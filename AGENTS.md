# qdrant-mcp-rbac — project context

This document provides structural and architectural context for contributors and automated tooling working in this repository.

---

## What this project does

qdrant-mcp-rbac is a FastMCP server that sits in front of a Qdrant vector database and enforces **OIDC-based authentication** and **role-based access control (RBAC)**. Incoming OIDC bearer tokens are validated, the caller's roles are resolved against a runtime-managed ACL, and a scoped Qdrant JWT is derived — all before any tool call reaches Qdrant.

The ACL itself is stored as points inside a dedicated Qdrant collection (`_rbac_acl`), not in config files, so grants can be managed at runtime via MCP admin tools without restarting the server.

---

## Repository layout

```
qdrant-mcp-rbac/
├── src/                    # FastMCP server (uv project, Python 3.11+)
│   ├── main.py             # Entry point; uvicorn startup and logging config
│   ├── config.py           # Pydantic Settings; all env-var configuration
│   ├── auth/
│   │   ├── models.py       # Pydantic models: OIDCClaims, AclEntry, DocPolicy, QdrantToken
│   │   ├── oidc.py         # OIDCValidator: JWKS caching, token validation
│   │   ├── acl.py          # AclResolver: role→grant mapping with TTL cache
│   │   ├── jwt_builder.py  # QdrantJWTBuilder: derive scoped Qdrant JWTs
│   │   └── doc_filter.py   # Document-level policy filter building and merging
│   ├── mcp_app/
│   │   ├── server.py       # FastMCP instance; Starlette app assembly
│   │   ├── tools.py        # MCP tool definitions (data tools + admin tools)
│   │   └── middleware.py   # ASGI auth middleware: validate token, derive JWT
│   ├── qdrant/
│   │   ├── client.py       # Per-request JWT-scoped async client factory
│   │   ├── collections.py  # Async wrappers for Qdrant collection operations
│   │   └── meta.py         # Read/write helpers for _collection_meta
│   └── pyproject.toml      # uv project config; ruff, mypy, pytest settings
├── tests/                  # pytest suite (sibling to src/)
│   ├── test_doc_filter.py  # Unit tests for document-level filter building
│   ├── test_facet.py       # Faceting (document inventory) tests
│   └── test_scroll.py      # Scroll-with-filter tests
├── demo/
│   ├── bootstrap/          # uv project: vectorise Markdown files into Qdrant
│   ├── client/             # uv project: OIDC-authenticated demo MCP client
│   └── data/               # Seed Markdown files (one per demo role)
├── docker/
│   ├── Dockerfile          # Multi-stage build: uv sync → slim runtime image
│   ├── docker-compose.yml  # mcp-server + qdrant services
│   └── .env.example        # All required env vars with placeholder values
└── .github/                # Workflows, issue templates, PR template
```

The repository separates the production server (`src/`), the test suite (`tests/`), and demo material (`demo/`). Each sub-project that uses uv has its own `pyproject.toml` and `.venv` — there is **no** virtual environment at the repository root.

---

## Architecture

### Request flow

```
MCP client
  │  Authorization: Bearer <oidc_token>
  ▼
OIDCAuthMiddleware  (mcp_app/middleware.py)
  │  validates signature, expiry, aud, iss
  │  caches JWKS with configurable TTL
  ▼
QdrantJWTBuilder.build()  (auth/jwt_builder.py)
  │  admin role?  → global-manage Qdrant JWT (ACL bypassed)
  │  otherwise    → AclResolver.get_mapping()
  │                 maps OIDC roles → collection grants
  │                 merges multi-role grants per collection
  ▼
MCP tool call  (mcp_app/tools.py)
  │  receives QdrantToken via request state
  ▼
qdrant_client(token=...)  (qdrant/client.py)
  │  per-request, JWT-scoped, closed after the call
  ▼
Qdrant
```

### System collections (auto-created, do not modify manually)

| Collection | Purpose |
|---|---|
| `_rbac_acl` | Role → collection grants; readable only with the service API key |
| `_collection_meta` | Embedding model name per user collection |

### Multi-role grant merging

When a user holds multiple roles, per-collection grants are merged:

- **Access level**: most-permissive wins (`rw` beats `ro`)
- **Document policies**: merged with allow-beats-deny semantics (see `auth/doc_filter.py`)

---

## Engineering conventions

### Branches

| Prefix | Use |
|---|---|
| `feat/<short>` | New user-facing feature |
| `fix/<short>` | Bug fix |
| `docs/<short>` | Documentation only |
| `refactor/<short>` | Refactoring without behaviour change |
| `test/<short>` | Test additions or fixes |
| `ci/<short>` | CI/CD configuration |
| `chore/<short>` | Maintenance |

Never push directly to `main`; always open a pull request.

### PR titles — Conventional Commits

Format: `<type>[(<scope>)][!]: <subject>`

- Subject: lowercase, imperative mood, no trailing period
- `!` suffix marks a breaking change (triggers a major version bump)
- CI enforces this format on every PR via `amannn/action-semantic-pull-request`

Examples: `feat: add revoke_access tool`, `fix(auth): reject expired tokens`, `docs: clarify ACL schema`

### Merge strategy

All PRs are **squash-merged**. The PR title becomes the single commit message on `main`.

Development commits on feature branches may be informal (`wip`, `tmp`, etc.) — they are squashed away.

---

## Code style and local checks

Run all checks locally before pushing:

```bash
# YAML — from the repository root
yamllint .

# Python — from src/ (repeat for demo/client/ and demo/bootstrap/ if changed)
cd src
uv run ruff check .
uv run mypy .
uv run pytest -q
```

- **Python linting**: ruff with rule sets E, F, I, B, UP, N, RET, SIM, ASYNC
- **Type checking**: mypy in strict mode (`--strict`); all public functions must carry explicit type annotations, including `-> None` return types
- **Import style**: absolute imports throughout (`from auth.oidc import OIDCValidator`), not relative; ruff's I rule set enforces ordering
- **YAML**: yamllint with the project `.yamllint` config
- **Python version**: 3.11 minimum

---

## Testing

Tests live in `tests/` (sibling to `src/`). Run from `src/`:

```bash
cd src
uv run pytest -q
```

| File | What it covers |
|---|---|
| `tests/test_doc_filter.py` | Document-level filter building and policy merging |
| `tests/test_facet.py` | Faceting (document inventory via Qdrant facets) |
| `tests/test_scroll.py` | Point scrolling with payload filters |

Integration tests that hit Qdrant require `QDRANT_URL` to point at a running instance.

---

## Key implementation patterns

### Per-request Qdrant client

Never instantiate `QdrantClient` directly in tools. Use the async context manager from `qdrant/client.py`:

```python
async with qdrant_client(token=state.qdrant_token) as client:
    result = await client.search(...)
```

Each request gets a fresh, JWT-scoped client that is closed at the end of the tool call.

### ACL cache invalidation

`AclResolver` caches the role → grant mapping with a TTL (`ACL_CACHE_TTL_SECONDS`). Any write to `_rbac_acl` (grant, revoke) must call `AclResolver.invalidate()` immediately after to force a fresh load on the next request. This is already done in the built-in admin tools; follow the same pattern if adding new admin operations.

### UUID5 point IDs in `_rbac_acl`

Points use `uuid5(NAMESPACE_DNS, f"{role}::{collection}")` as their ID. This makes every grant upsert idempotent: applying the same (role, collection) pair twice updates the existing point rather than creating a duplicate.

### Document-level policies

A grant may carry a `doc_policy` (`auth/models.py`) that injects a Qdrant payload filter into every retrieval for that grant. Filter construction and multi-grant merging happen in `auth/doc_filter.py`. Read the existing tests in `tests/test_doc_filter.py` before modifying the merging logic.

---

## Configuration reference

All settings are loaded via Pydantic Settings in `config.py`. See `docker/.env.example` for the full list with descriptions. The essential variables:

| Variable | Purpose |
|---|---|
| `QDRANT_URL` | URL of the Qdrant instance |
| `QDRANT_API_KEY` | Service-level API key (used for `_rbac_acl` operations) |
| `QDRANT_JWT_SECRET` | Secret used to sign scoped Qdrant JWTs |
| `OIDC_ISSUER` | OIDC issuer URL; discovery at `{issuer}/.well-known/openid-configuration` |
| `OIDC_AUDIENCE` | Expected `aud` claim value in incoming tokens |
| `RBAC_ADMIN_ROLE` | Role that bypasses ACL entirely (default: `qdrant-admin`) |
| `JWKS_CACHE_TTL_SECONDS` | How long JWKS keys are cached (default: 300) |
| `ACL_CACHE_TTL_SECONDS` | How long the role → grant mapping is cached (default: 60) |

---

## Security boundaries

- **Never log** token values, derived Qdrant JWTs, or `QDRANT_JWT_SECRET`.
- **Keep `_rbac_acl` server-side only.** The Qdrant service API key must never be exposed to MCP clients; only the derived, scoped JWTs reach callers.
- **JWKS cache TTL** should be short enough to pick up provider key rotation within an acceptable window. The default of 300 s is a reasonable starting point; lower it if your OIDC provider rotates keys frequently.
- **Copyleft dependencies are not accepted.** The CI license-check workflow rejects GPL, LGPL, AGPL, EUPL, and similar licences. See `CONTRIBUTING.md` for the full list.
