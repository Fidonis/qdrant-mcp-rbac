# qdrant-rbac

A FastMCP server that exposes a Qdrant vector database over **streamable HTTP** with
**OIDC-based authentication** (Keycloak-compatible) and **role-based access control**
mapped onto Qdrant's native JWT access rules.

Role grants are **stored as data inside a dedicated Qdrant collection**, not in
configuration files — admins manage them at runtime via MCP tools.

```
┌──────────┐   OIDC Bearer   ┌────────────────┐   Qdrant JWT    ┌──────────┐
│ MCP host │ ──────────────▶ │ qdrant-rbac    │ ──────────────▶ │  Qdrant  │
│ (client) │                 │  (FastMCP)     │                 │          │
└──────────┘                 └────────────────┘                 │  ┌─────┐ │
                                     │                          │  │_rbac│ │
                                     ▼                          │  │_acl │ │
                                 Keycloak                       │  └─────┘ │
                                 (JWKS, OIDC discovery)         └──────────┘
```

## How it works

1. The MCP client sends a request with an OIDC access token (`Authorization: Bearer …`).
2. The `OIDCAuthMiddleware` validates the token against the OIDC provider's JWKS
   (signature, expiry, audience, issuer).
3. The `JWTBuilder` derives a Qdrant JWT for the user:
   - If the user is in the **break-glass admin role** (`RBAC_ADMIN_ROLE`),
     a **global-manage** token is minted directly.
   - Otherwise, the `AclResolver` returns the cached `role -> [grants]`
     mapping built from the **ACL collection** (`RBAC_ACL_COLLECTION`,
     default `_rbac_acl`).
4. The Qdrant JWT is attached to the request scope.
5. Each MCP tool checks the user's effective access for the targeted collection
   and forwards the request to Qdrant using the derived JWT.

### ACL collection schema

Each grant is one point in `_rbac_acl`. The point id is a deterministic
`uuid5(role, collection)`, so re-granting is idempotent. Payload:

```json
{ "role": "data-scientist", "collection": "embeddings_v2", "access": "rw" }
```

`access` is one of:

- `r` – read-only
- `rw` – read & write
- `m` – manage (global; `collection` is informational, convention: `"*"`)

The collection is auto-created on first read by the server using a self-minted
service token.

## Layout

The repo separates the production server (`src/`) from demo material (`demo/`).
`src/` and each project under `demo/` is its own uv project with its own
`pyproject.toml` and `.venv` — there is **no virtual environment in the
repo root**.

```
src/                       # uv project: the FastMCP server
  pyproject.toml
  uv.lock
  .venv/                   # created by `uv sync` (gitignored)
  .env                     # local config (gitignored)
  .env.example             # template for `.env`
  main.py                  # Entry point (uvicorn)
  config.py                # Settings (pydantic-settings, .env)
  auth/
    models.py              # OIDCClaims, CollectionAccess, AclEntry, QdrantToken
    oidc.py                # JWKS-based OIDC token validator
    acl.py                 # AclResolver: TTL-cached read of the ACL collection
    jwt_builder.py         # Builds Qdrant JWTs from claims + ACL mapping
  qdrant/
    client.py              # AsyncQdrantClient factory (per-request, JWT-scoped)
    collections.py         # Thin async wrappers around Qdrant collection operations
    meta.py                # Read/write helpers for the _collection_meta system collection
  mcp_app/                 # named `mcp_app` to avoid shadowing the installed `mcp` SDK
    server.py              # FastMCP instance + Starlette app wiring
    tools.py               # MCP tools: data tools + ACL admin tools
    middleware.py          # ASGI auth middleware: OIDC → Qdrant JWT

demo/                      # everything for the end-to-end demo
  README.md                # Demo quickstart (order of operations)
  bootstrap/               # uv project: vectorize Markdown → Qdrant collections
    pyproject.toml
    uv.lock
    .venv/
    vectorize.py
  client/                  # uv project: OIDC + LLM CLI client
    pyproject.toml
    uv.lock
    .venv/
    client.py
    agent.py
    oidc.py
    config.py
  data/                    # demo seed data (one Markdown file per role)
    finance.md
    it.md
    sales.md
  docker/                  # demo infrastructure
    docker-compose.yml     # Qdrant (+ optional Keycloak)
```

## Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) installed
- A reachable OIDC provider (e.g. Keycloak)
- A reachable Qdrant instance with JWT RBAC enabled

### Install

```bash
cd src
uv sync
```

This creates `src/.venv` with all dependencies. The repo root has no
`.venv` of its own.

### Configure

Copy the example env file and adjust values:

```bash
cp src/.env.example src/.env
```

The server reads `src/.env` (resolved relative to `config.py`), so keep
it inside `src/` regardless of the working directory you run from.

Important variables:

| Variable | Purpose |
|---|---|
| `OIDC_ISSUER_URL` | Keycloak realm URL, e.g. `https://kc.example.com/realms/myrealm` |
| `OIDC_AUDIENCE` | Expected `aud` claim, e.g. `mcp-server` |
| `QDRANT_URL` | Qdrant HTTP endpoint |
| `QDRANT_JWT_SECRET` | Signing secret — **must equal Qdrant's `service.api_key`** |
| `MCP_HOST` / `MCP_PORT` / `MCP_PATH` | Listen address and path (default `0.0.0.0:8000/mcp`) |
| `EMBEDDING_API_URL` | OpenAI-compatible embeddings base URL (default `http://localhost:11434/v1`) |
| `EMBEDDING_API_KEY` | Optional bearer token for the embeddings endpoint |
| `EMBEDDING_META_COLLECTION` | Collection storing per-collection embedding metadata (default `_collection_meta`) |
| `RBAC_ADMIN_ROLE` | OIDC role granting global manage (default `qdrant-admin`) |
| `RBAC_ACL_COLLECTION` | Qdrant collection storing grants (default `_rbac_acl`) |
| `RBAC_ACL_CACHE_TTL` | In-memory ACL cache TTL (default 60 s) |
| `RBAC_SERVICE_TOKEN_TTL` | TTL of server-minted service tokens for ACL reads (default 300 s) |

### Run

```bash
cd src
uv run python main.py
```

### Run with Docker

To run the server itself in a container instead of on the host, use the
compose file in `docker/`:

```bash
cp docker/.env.example docker/.env   # then edit secrets and OIDC_ISSUER_URL
docker compose -f docker/docker-compose.yml up -d
```

This builds the image from `docker/Dockerfile` and starts the `mcp-server`
container (published on `:8000`, with a `/health` health-check) alongside
Qdrant. Keycloak is **not** included — point `OIDC_ISSUER_URL` in `docker/.env`
at an external OIDC provider. Every variable is documented in
`docker/.env.example`.

## Bootstrapping access

1. Assign one user the `qdrant-admin` realm role in Keycloak.
2. Start the server.
3. As that user, call the `grant_access` tool to provision other roles:

```jsonc
// MCP tool call
{
  "tool": "grant_access",
  "args": { "role": "data-scientist", "collection": "embeddings_v2", "access": "rw" }
}
```

4. Other users with the `data-scientist` role now get an `rw` JWT scoped to
   `embeddings_v2`.

## Tools

### Data tools

| Tool | Required access |
|---|---|
| `list_collections` | (any) — returns only collections the caller can read |
| `get_collection_info` | `r` on the target collection |
| `search_collection` | `r` on the target collection |
| `search_collection_by_text` | `r` on the target collection |
| `upsert_points` | `rw` on the target collection |
| `delete_points` | `rw` on the target collection |

`search_collection_by_text` accepts a natural-language query, embeds it via
the configured OpenAI-compatible endpoint using the model recorded at
bootstrap time, and runs the resulting vector search.

`upsert_points` and `delete_points` refuse to operate on system collections
(`_rbac_acl`, `_collection_meta`) — use the admin tools or bootstrap instead.

### ACL admin tools (require global manage)

| Tool | Effect |
|---|---|
| `list_acl(role?)` | Return all grants, optionally filtered by role |
| `grant_access(role, collection, access)` | Idempotently upsert one grant |
| `revoke_access(role, collection)` | Remove a grant |
| `refresh_acl()` | Force the in-memory cache to reload |

Mutations invalidate the local cache immediately; out-of-band edits to the ACL
collection (e.g. via the Qdrant API directly) become visible after at most
`RBAC_ACL_CACHE_TTL` seconds.

## Local development with docker-compose

The compose file lives in `demo/docker/`, but it reads variable values
(e.g. `QDRANT_JWT_SECRET`) from `src/.env`. Run it from the repo root
and point Compose at both files explicitly:

```bash
docker compose --env-file src/.env -f demo/docker/docker-compose.yml up -d
```

This starts:

- Qdrant on `:6333` (with JWT RBAC enabled, signing key from `QDRANT_JWT_SECRET`)
- Keycloak on `:8080` (admin/admin in dev mode)

Create a realm, a confidential client with audience `mcp-server`, the
`qdrant-admin` role, and any per-team roles you plan to grant access to.
Assign roles to test users.

## Qdrant JWT setup

`qdrant-rbac` signs JWTs with `QDRANT_JWT_SECRET` (HS256). Qdrant must be
configured to accept JWTs and use the same secret as its API key:

```yaml
# qdrant config.yaml
service:
  api_key: <same value as QDRANT_JWT_SECRET>
  jwt_rbac: true
```

The derived JWT structure for a non-admin user is:

```json
{
  "exp": 1735689600,
  "access": [
    { "collection": "col_a", "access": "r" },
    { "collection": "col_b", "access": "rw" }
  ]
}
```

For admins (or any grant with `access: "m"`):

```json
{ "exp": 1735689600, "access": "m" }
```

## Calling from an MCP client

Pass the OIDC access token in the `Authorization` header:

```bash
TOKEN=$(curl -s -X POST \
  -d "client_id=mcp-cli" -d "username=alice" -d "password=…" \
  -d "grant_type=password" \
  https://kc.example.com/realms/myrealm/protocol/openid-connect/token | jq -r .access_token)

curl -H "Authorization: Bearer $TOKEN" \
     -H "Accept: text/event-stream" \
     http://localhost:8000/mcp
```
