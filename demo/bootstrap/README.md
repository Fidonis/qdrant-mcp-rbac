# bootstrap

Standalone helper that vectorizes Markdown demo data into per-role Qdrant
collections. Each `*.md` file under `demo/data/` becomes one collection — the
file's stem (e.g. `finance.md` → `finance`) is interpreted as the **role** name
in the project's RBAC concept and used as the collection name.

The script is intended for local demo / bootstrap use; it is independent of
the FastMCP server in `src/`.

## What it does

- Walks `demo/data/` recursively for `*.md` files.
- Splits each file into ~500-token chunks with 50-token overlap.
- Embeds each chunk by calling an OpenAI-compatible HTTP embeddings endpoint
  (default: `http://localhost:11434/v1`, model `all-MiniLM-L6-v2`).
- Recreates the role's collection (idempotent re-runs) and upserts all chunks.
- Stores per point: `{ source, chunk_index, text }`.
- Writes a metadata record for each ingested collection to the shared
  `_collection_meta` system collection so the MCP server can embed queries
  with the same model at search time.

## Prerequisites

- Python 3.11+
- A reachable Qdrant instance (e.g. `docker compose -f demo/docker/docker-compose.yml up qdrant`
  from the project root).
- A reachable OpenAI-compatible embeddings endpoint, e.g.
  [Ollama](https://ollama.com/) (`ollama serve`) with an embedding model pulled:
  `ollama pull all-minilm:l6-v2` (or any model the endpoint serves).
- `demo/data/` populated with one Markdown file per role, e.g.

  ```
  demo/data/
    finance.md
    it.md
    sales.md
  ```

## Install

`demo/bootstrap/` is its own self-contained uv project. Install it from
inside the directory — it gets its own `.venv`, separate from the
FastMCP server in `src/`. The repo root has no virtual environment.

```bash
cd demo/bootstrap
uv sync
```

## Run

```bash
# Defaults: QDRANT_URL=http://localhost:6333, model=all-MiniLM-L6-v2
cd demo/bootstrap
uv run python vectorize.py
```

To override defaults, copy the env example and edit it:

```bash
cp .env.example .env
```

The script auto-loads `demo/bootstrap/.env` on startup (resolved relative
to `vectorize.py`, regardless of the current working directory).
Variables already set in the shell take precedence.

### Configuration (environment variables)

| Variable                   | Default                      | Description |
|----------------------------|------------------------------|-------------|
| `QDRANT_URL`               | `http://localhost:6333`      | Qdrant HTTP endpoint. |
| `QDRANT_API_KEY`           | _(unset)_                    | Optional API key / JWT for Qdrant. |
| `EMBEDDING_API_URL`        | `http://localhost:11434/v1`  | OpenAI-compatible embeddings base URL (must include the `/v1` prefix). |
| `EMBEDDING_API_KEY`        | _(unset)_                    | Optional bearer token for the embeddings endpoint. Leave empty for unauthenticated local providers. |
| `EMBEDDING_MODEL`          | `all-MiniLM-L6-v2`           | Model name passed to the endpoint. Any model the endpoint serves works; for non-English data try `paraphrase-multilingual-MiniLM-L12-v2`. |
| `EMBEDDING_META_COLLECTION`| `_collection_meta`           | Qdrant collection that stores which model produced each collection's vectors. Read by the MCP server at query time. |
| `EMBEDDING_BATCH`          | `32`                         | Number of texts per `/embeddings` request. |
| `DATA_DIR`                 | `demo/data`                  | Source directory for Markdown files (default resolves to the sibling `data/` folder). |
| `CHUNK_TOKENS`             | `500`                        | Approx. tokens per chunk (whitespace-tokenized). |
| `CHUNK_OVERLAP`            | `50`                         | Token overlap between adjacent chunks. |
| `LOG_LEVEL`                | `INFO`                       | Python logging level. |

## Idempotency

Each run drops and recreates every target collection before upserting, so
running the script repeatedly leaves Qdrant in the same final state.
