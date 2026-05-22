"""LLM agent loop bridging litellm <-> the qdrant-rbac MCP server.

The MCP transport is FastMCP's streamable-HTTP client, configured with an
``Authorization: Bearer <token>`` header so the qdrant-rbac middleware sees
the same Keycloak access token the user logged in with.

The LLM is configurable through litellm: any model id understood by litellm
(``gpt-4o-mini``, ``anthropic/claude-3-5-sonnet-latest``, ``ollama/llama3``,
``azure/<deployment>``, ...) works without code changes.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import litellm
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.types import Tool as McpTool

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "You are an assistant connected to the qdrant-rbac MCP server. "
    "The server enforces per-collection role-based access control: tools may "
    "refuse with 'forbidden' errors when the logged-in user lacks the required "
    "grant. Use the available tools to inspect collections, run vector searches, "
    "and (for admins) manage ACL grants. Be concise. When a tool error mentions "
    "'forbidden' or 'no_mapped_roles', tell the user which role/grant is "
    "missing instead of retrying blindly. "
    "IMPORTANT: When the user asks to search a collection with a text or natural-language "
    "query, always use 'search_collection_by_text' (which accepts a 'query' string). "
    "Only use 'search_collection' when you already have a raw float vector."
)


@dataclass
class LlmConfig:
    model: str
    api_base: str = ""
    api_key: str = ""
    temperature: float = 0.2
    max_iterations: int = 8
    system_prompt: str = ""


def _mcp_tool_to_openai(tool: McpTool) -> dict[str, Any]:
    """Convert an MCP tool definition to OpenAI/litellm tool-call format."""
    schema: dict[str, Any] = dict(tool.inputSchema or {"type": "object", "properties": {}})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": schema,
        },
    }


def _stringify_tool_result(result: Any) -> str:
    """Render an MCP CallToolResult into a string the LLM can read back."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        try:
            return json.dumps(structured, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            pass

    pieces: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            pieces.append(text)
        else:
            pieces.append(repr(block))
    if pieces:
        return "\n".join(pieces)

    return json.dumps(result, default=str)


class McpLlmAgent:
    """One agent instance per session — owns the MCP client and chat history."""

    def __init__(
        self,
        *,
        mcp_url: str,
        bearer_token: str,
        llm: LlmConfig,
    ) -> None:
        self._transport = StreamableHttpTransport(
            url=mcp_url,
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
        self._client: Client | None = None
        self._llm = llm
        self._tools_openai: list[dict[str, Any]] = []
        self._tool_names: set[str] = set()
        self._messages: list[dict[str, Any]] = []

    async def __aenter__(self) -> McpLlmAgent:
        self._client = Client(self._transport)
        await self._client.__aenter__()
        await self._refresh_tools()
        system = self._llm.system_prompt.strip() or DEFAULT_SYSTEM_PROMPT
        self._messages = [{"role": "system", "content": system}]
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc, tb)
            self._client = None

    async def _refresh_tools(self) -> None:
        assert self._client is not None
        tools = await self._client.list_tools()
        self._tools_openai = [_mcp_tool_to_openai(t) for t in tools]
        self._tool_names = {t.name for t in tools}
        logger.info("Discovered %d MCP tools: %s", len(tools), sorted(self._tool_names))

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tool_names)

    async def chat(self, user_message: str) -> str:
        """Run one user-turn through the LLM, executing tool-calls as needed."""
        if self._client is None:
            raise RuntimeError("McpLlmAgent must be entered via 'async with' first")

        self._messages.append({"role": "user", "content": user_message})

        for _iteration in range(self._llm.max_iterations):
            kwargs: dict[str, Any] = {
                "model": self._llm.model,
                "messages": self._messages,
                "temperature": self._llm.temperature,
            }
            if self._tools_openai:
                kwargs["tools"] = self._tools_openai
                kwargs["tool_choice"] = "auto"
            if self._llm.api_base:
                kwargs["api_base"] = self._llm.api_base
            if self._llm.api_key:
                kwargs["api_key"] = self._llm.api_key

            response = await litellm.acompletion(**kwargs)
            choice = response.choices[0]
            msg = choice.message
            tool_calls = getattr(msg, "tool_calls", None) or []

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
            }
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ]
            self._messages.append(assistant_msg)

            if not tool_calls:
                return msg.content or ""

            for tc in tool_calls:
                name = tc.function.name
                raw_args = tc.function.arguments or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError as exc:
                    tool_output = f"error: invalid JSON arguments: {exc}: {raw_args!r}"
                else:
                    if name not in self._tool_names:
                        tool_output = f"error: unknown tool '{name}'"
                    else:
                        tool_output = await self._invoke_tool(name, args)

                self._messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": name,
                        "content": tool_output,
                    }
                )

        return (
            "[agent stopped: reached max_iterations="
            f"{self._llm.max_iterations} without a final answer]"
        )

    async def _invoke_tool(self, name: str, args: dict[str, Any]) -> str:
        assert self._client is not None
        try:
            result = await self._client.call_tool(name, args)
        except Exception as exc:  # noqa: BLE001 - surface the raw error to the LLM
            logger.info("Tool %s raised: %s", name, exc)
            return f"error: {type(exc).__name__}: {exc}"
        return _stringify_tool_result(result)
