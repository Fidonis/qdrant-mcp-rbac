# demo

End-to-end demo material for `qdrant-rbac`. Everything in this folder
exists to exercise the server in `src/`; it is **not** part of the
shipped product.

```
demo/
├── data/        # Markdown seed files, one per role (finance / it / sales)
├── bootstrap/   # uv project: vectorize data/ into per-role Qdrant collections
└── client/      # uv project: OIDC-authenticated MCP client with an LLM agent
```

`bootstrap/` and `client/` are independent uv projects, each with its own
`pyproject.toml`, `uv.lock` and `.venv`.

## Quickstart (from the repo root)

1. **Infrastructure** — start Qdrant. Compose reads `QDRANT_JWT_SECRET`
   from `src/.env`, so configure `src/.env` first (see top-level `README.md`).

   ```bash
   docker compose --env-file src/.env -f docker/docker-compose.yml up -d qdrant
   ```

2. **Seed data** — vectorize the Markdown files in `demo/data/`.

   ```bash
   cd demo/bootstrap
   uv sync
   cp .env.example .env   # default DATA_DIR=../data resolves to demo/data
   uv run python vectorize.py
   ```

3. **MCP server** — start `qdrant-rbac` (unchanged path).

   ```bash
   cd src
   uv sync
   uv run python main.py
   ```

4. **Client** — log in via OIDC and chat with the server.

   ```bash
   cd demo/client
   uv sync
   cp .env.example .env   # edit OIDC + LLM creds
   uv run python client.py
   ```

The full walkthrough (Keycloak setup, ACL grants, per-user RBAC tests,
VS Code debugging) lives in [`../DEMO.md`](../DEMO.md).
