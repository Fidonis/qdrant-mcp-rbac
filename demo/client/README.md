# client

Interactive demo client for the `qdrant-mcp-rbac` MCP server.

```
┌──────────────┐  OIDC password   ┌──────────┐
│ demo/client  │ ───────────────▶ │ Keycloak │
│ (this app)   │ ◀──── token ──── │          │
└──────────────┘                  └──────────┘
       │
       │  Bearer <access_token>
       ▼
┌──────────────┐    Qdrant JWT    ┌──────────┐
│ qdrant-mcp-rbac  │ ───────────────▶ │  Qdrant  │
└──────────────┘                  └──────────┘
       ▲
       │  tool_calls (litellm)
┌──────────────┐
│  LLM (any    │
│  litellm     │
│  provider)   │
└──────────────┘
```

The client:

1. Logs in to Keycloak (Resource-Owner Password or `client_credentials` grant).
2. Opens a streamable-HTTP MCP session against `qdrant-mcp-rbac` and forwards the
   Keycloak access token as `Authorization: Bearer …` — the server's
   `OIDCAuthMiddleware` validates it and derives the per-user Qdrant JWT.
3. Lists the MCP tools and gives them to a litellm-driven LLM as
   OpenAI-style tool definitions.
4. Runs a REPL: each user message is sent to the LLM, tool calls are executed
   against the MCP server, results are fed back into the conversation until
   the LLM produces a plain answer.

## Setup

```bash
cd demo/client
uv sync
cp .env.example .env
# edit .env
uv run python client.py
```

## Configuration

All knobs live in `.env`. Important groups:

| Variable | Notes |
|---|---|
| `OIDC_ISSUER_URL` | Same realm URL the MCP server uses |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | Keycloak client; secret optional for public clients |
| `OIDC_GRANT_TYPE` | `password` (default) or `client_credentials` |
| `OIDC_USERNAME` / `OIDC_PASSWORD` | Required for password grant |
| `MCP_SERVER_URL` | e.g. `http://localhost:8000/mcp` |
| `LLM_MODEL` | Any litellm model id (see below) |
| `LLM_API_BASE` | Override for OpenAI-compatible endpoints (Ollama, vLLM, …) |
| `LLM_API_KEY` | Optional; otherwise litellm uses the provider's default env var |
| `LLM_TEMPERATURE` | Sampling temperature, default `0.2` |
| `LLM_MAX_ITERATIONS` | Maximum tool-call rounds per user turn, default `8` |
| `LLM_SYSTEM_PROMPT` | Override the default system prompt |

### LLM examples

```env
# OpenAI
LLM_MODEL=gpt-4o-mini
# (and OPENAI_API_KEY=sk-... in your shell or .env)

# Anthropic
LLM_MODEL=anthropic/claude-3-5-sonnet-latest
# (and ANTHROPIC_API_KEY=sk-ant-... )

# Ollama (local)
LLM_MODEL=ollama/llama3.1
LLM_API_BASE=http://localhost:11434

# Azure OpenAI
LLM_MODEL=azure/<deployment-name>
# (AZURE_API_KEY, AZURE_API_BASE, AZURE_API_VERSION in env)
```

litellm picks up provider-specific env vars automatically, so `LLM_API_KEY`
is only needed when you want to override them.

## Keycloak prerequisites

For the `password` grant the realm client must have **Direct Access Grants**
enabled. The token's `aud` claim must include the value the MCP server
expects (`OIDC_AUDIENCE` on the server side). Two common ways to ensure that:

- Make the *client_id* itself the audience and configure the MCP server with
  `OIDC_AUDIENCE=<client_id>`.
- Add an *Audience* mapper to the client that hard-codes the required
  audience (e.g. `mcp-server`).

The user must also hold either the break-glass admin role
(`RBAC_ADMIN_ROLE`, default `qdrant-admin`) or a role that has at least one
grant in the ACL collection — otherwise the MCP server returns
`403 no_mapped_roles`.

## Tips

- Set `LOG_LEVEL=DEBUG` to see every HTTP request, MCP frame, and litellm
  call. At `INFO` (default) the third-party loggers are quieted to keep
  the chat readable.
- The agent stops after `LLM_MAX_ITERATIONS` tool rounds in a single turn.
  Bump it if your model legitimately needs more steps; lower it to fail
  fast against runaway tool loops.
