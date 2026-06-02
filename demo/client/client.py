"""Interactive CLI client.

Flow:
  1. Read settings from .env
  2. Get an OIDC access token from Keycloak
  3. Connect to the qdrant-rbac MCP server with that token
  4. Spin up an LLM agent (configurable via LLM_MODEL) and start a chat loop

Run from inside ``demo/client/``:

    uv run python client.py
"""
from __future__ import annotations

import asyncio
import logging
import sys

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from agent import LlmConfig, McpLlmAgent
from config import Settings, get_settings
from oidc import OIDCError, TokenBundle, fetch_token, refresh_access_token

console = Console()


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
    )
    # litellm is chatty at INFO; keep it at WARNING unless the user asked for DEBUG.
    if level.upper() != "DEBUG":
        logging.getLogger("LiteLLM").setLevel(logging.WARNING)
        logging.getLogger("litellm").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)


async def _login(settings: Settings) -> TokenBundle:
    console.print(
        f"[cyan]Logging in to[/] {settings.oidc_issuer_url} "
        f"as [bold]{settings.oidc_username or settings.oidc_client_id}[/] "
        f"(grant={settings.oidc_grant_type})"
    )
    bundle = await fetch_token(
        issuer_url=settings.oidc_issuer_url,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        grant_type=settings.oidc_grant_type,
        username=settings.oidc_username,
        password=settings.oidc_password,
        extra_scopes=settings.oidc_scopes,
    )
    console.print(
        f"[green]OK[/] - access token acquired "
        f"(expires in {bundle.expires_in}s)"
    )
    return bundle


def _make_token_refresher(settings: Settings, bundle: TokenBundle):
    """Return an async callable that refreshes the access token.

    Tries the refresh-token grant first; falls back to a full re-login
    (password / client_credentials) if no refresh token is available.
    """
    async def _refresher() -> TokenBundle:
        nonlocal bundle
        if bundle.refresh_token:
            try:
                bundle = await refresh_access_token(
                    issuer_url=settings.oidc_issuer_url,
                    client_id=settings.oidc_client_id,
                    client_secret=settings.oidc_client_secret,
                    refresh_token=bundle.refresh_token,
                )
                return bundle
            except OIDCError as exc:
                logging.getLogger(__name__).warning(
                    "Refresh-token grant failed (%s), falling back to re-login", exc
                )
        bundle = await fetch_token(
            issuer_url=settings.oidc_issuer_url,
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret,
            grant_type=settings.oidc_grant_type,
            username=settings.oidc_username,
            password=settings.oidc_password,
            extra_scopes=settings.oidc_scopes,
        )
        return bundle

    return _refresher


async def _run_chat(settings: Settings, bundle: TokenBundle) -> None:
    llm_cfg = LlmConfig(
        model=settings.llm_model,
        api_base=settings.llm_api_base,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        max_iterations=settings.llm_max_iterations,
        system_prompt=settings.llm_system_prompt,
    )

    console.print(
        Panel.fit(
            f"[bold]MCP[/]: {settings.mcp_server_url}\n"
            f"[bold]LLM[/]: {settings.llm_model}"
            + (f"  [dim](api_base={settings.llm_api_base})[/]" if settings.llm_api_base else ""),
            title="qdrant-rbac demo client",
            border_style="cyan",
        )
    )

    async with McpLlmAgent(
        mcp_url=settings.mcp_server_url,
        token_bundle=bundle,
        llm=llm_cfg,
        token_refresher=_make_token_refresher(settings, bundle),
    ) as agent:
        console.print(
            f"[dim]Connected. {len(agent.tool_names)} tools available: "
            f"{', '.join(agent.tool_names)}[/]"
        )
        console.print("[dim]Type 'exit' or Ctrl-D to quit.[/]\n")

        while True:
            try:
                user_input = Prompt.ask("[bold green]you[/]")
            except (EOFError, KeyboardInterrupt):
                console.print()
                return
            text = user_input.strip()
            if not text:
                continue
            if text.lower() in {"exit", "quit", ":q"}:
                return
            try:
                reply = await agent.chat(text)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]Agent error:[/] {exc}")
                continue
            console.print(Panel(reply or "[dim](empty)[/]", title="assistant",
                                border_style="magenta"))


async def _async_main() -> int:
    settings = get_settings()
    _configure_logging(settings.log_level)

    try:
        bundle = await _login(settings)
    except OIDCError as exc:
        console.print(f"[red]OIDC login failed:[/] {exc}")
        return 2

    try:
        await _run_chat(settings, bundle)
    except KeyboardInterrupt:
        console.print()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            console.print(
                "[red]Forbidden:[/] the MCP server rejected the request "
                "(HTTP 403). Your account is authenticated but lacks the "
                "required role/grant for this server."
            )
            return 3
        raise
    return 0


def main() -> None:
    sys.exit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
