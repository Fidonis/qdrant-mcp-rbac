"""Vectorize Markdown demo data into per-role Qdrant collections.

For each ``*.md`` file found recursively under ``data/``, the file's stem is
treated as the **role** (matching the project's RBAC role concept) and is
also used as the **collection name** in Qdrant. The file content is split
into overlapping chunks, embedded via an OpenAI-compatible embeddings
endpoint, and upserted into the role's collection.

After ingesting a collection, a record is written to the shared
``_collection_meta`` system collection identifying which embedding model
produced its vectors. The MCP server reads this meta entry at query time
so it uses the same model to embed user queries.

Re-runs are idempotent: existing collections are recreated before ingestion.

Configuration via environment variables:

    QDRANT_URL                Qdrant HTTP endpoint (default: http://localhost:6333)
    QDRANT_API_KEY            Optional API key / JWT for Qdrant
    EMBEDDING_API_URL         OpenAI-compatible embeddings base URL
                              (default: http://localhost:11434/v1)
    EMBEDDING_API_KEY         Optional bearer token for the endpoint
    EMBEDDING_MODEL           Model name passed to the endpoint
                              (default: all-MiniLM-L6-v2)
    EMBEDDING_META_COLLECTION Meta collection name (default: _collection_meta)
    DATA_DIR                  Source directory (default: ./data next to this script)
    CHUNK_TOKENS              Approx. tokens per chunk (default: 500)
    CHUNK_OVERLAP             Token overlap between chunks (default: 50)
    EMBEDDING_BATCH           Texts per embeddings request (default: 32)
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

logger = logging.getLogger("vectorize")

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")
DEFAULT_DATA_DIR = SCRIPT_DIR.parent / "data"
DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_API_URL = "http://localhost:11434/v1"
DEFAULT_META_COLLECTION = "_collection_meta"
DEFAULT_CHUNK_TOKENS = 500
DEFAULT_CHUNK_OVERLAP = 50
DEFAULT_BATCH = 32
ACL_TAGS_FILENAME = "acl_tags.yml"

# Must match src/qdrant/meta.py — bootstrap and the MCP server agree on the
# point id derived from a collection name so meta upserts are idempotent.
_META_NAMESPACE = uuid.UUID("9e3a5c2f-8b7d-4f1e-a6b3-2d8c9e4f1a02")
_META_VECTOR: list[float] = [0.0]


@dataclass(frozen=True)
class IngestResult:
    role: str
    collection: str
    source: Path
    chunk_count: int


@dataclass(frozen=True)
class EmbedderConfig:
    api_url: str
    api_key: str
    model: str
    batch: int


def chunk_text(text: str, *, chunk_tokens: int, overlap: int) -> list[str]:
    """Split ``text`` into roughly ``chunk_tokens``-sized chunks with overlap.

    Tokens are approximated by whitespace-separated words, which is good
    enough for selecting chunk boundaries in Markdown demo data.
    """
    if chunk_tokens <= 0:
        raise ValueError("chunk_tokens must be positive")
    if overlap < 0 or overlap >= chunk_tokens:
        raise ValueError("overlap must be in [0, chunk_tokens)")

    words = text.split()
    if not words:
        return []

    step = chunk_tokens - overlap
    chunks: list[str] = []
    for start in range(0, len(words), step):
        window = words[start : start + chunk_tokens]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + chunk_tokens >= len(words):
            break
    return chunks


def collection_name_for(stem: str) -> str:
    """Derive the Qdrant collection name from a file stem.

    Takes the prefix up to (but not including) the first hyphen so that
    documents sharing a prefix land in the same collection:

        finance-2025 → finance
        finance-2026 → finance
        it           → it
        sales        → sales
        sales-vp     → sales
    """
    return stem.split("-")[0]


def discover_markdown_files(root: Path) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Data directory not found: {root}")
    files = sorted(p for p in root.rglob("*.md") if p.is_file())
    return files


def load_acl_tags(data_root: Path) -> dict[str, list[str]]:
    """Load the optional ``acl_tags.yml`` sidecar.

    The sidecar maps each Markdown source path (relative to ``data_root``,
    POSIX-style) to a list of tag strings. Documents listed here get an
    ``acl_tags`` payload field at ingest time; documents not listed remain
    untagged and stay fully visible under default-allow doc policies.

    The file is optional — when missing, an empty mapping is returned.
    """
    sidecar = data_root / ACL_TAGS_FILENAME
    if not sidecar.is_file():
        return {}
    try:
        raw = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SystemExit(f"Invalid YAML in {sidecar}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"{sidecar} must contain a mapping at top level")
    result: dict[str, list[str]] = {}
    for source, tags in raw.items():
        if not isinstance(source, str) or not source:
            raise SystemExit(f"{sidecar}: keys must be non-empty strings, got {source!r}")
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise SystemExit(
                f"{sidecar}: value for {source!r} must be a list of strings"
            )
        result[source] = list(tags)
    return result


def embed_batch(
    http: httpx.Client, *, cfg: EmbedderConfig, texts: list[str]
) -> list[list[float]]:
    """Call the OpenAI-compatible /embeddings endpoint for a batch of texts."""
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    response = http.post(
        f"{cfg.api_url.rstrip('/')}/embeddings",
        json={"model": cfg.model, "input": texts},
        headers=headers,
    )
    response.raise_for_status()
    data = response.json()
    items = data.get("data") or []
    if len(items) != len(texts):
        raise RuntimeError(
            f"embedding endpoint returned {len(items)} vectors for {len(texts)} inputs"
        )
    return [[float(x) for x in item["embedding"]] for item in items]


def embed_chunks(
    http: httpx.Client, *, cfg: EmbedderConfig, chunks: list[str]
) -> list[list[float]]:
    """Embed all chunks in batches of ``cfg.batch``."""
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), cfg.batch):
        batch = chunks[start : start + cfg.batch]
        vectors.extend(embed_batch(http, cfg=cfg, texts=batch))
    return vectors


def ensure_collection(
    client: QdrantClient, *, name: str, vector_size: int
) -> None:
    """(Re)create ``name`` so the run is idempotent."""
    if client.collection_exists(collection_name=name):
        client.delete_collection(collection_name=name)
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )


def ensure_meta_collection(client: QdrantClient, *, name: str) -> None:
    """Idempotently create the shared meta collection."""
    if not client.collection_exists(collection_name=name):
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=1, distance=Distance.COSINE),
        )


def upsert_meta_entry(
    client: QdrantClient,
    *,
    meta_collection: str,
    collection_name: str,
    embedding_model: str,
    vector_dimension: int,
) -> None:
    """Write or refresh the meta record for ``collection_name``."""
    point_id = str(uuid.uuid5(_META_NAMESPACE, collection_name))
    client.upsert(
        collection_name=meta_collection,
        points=[
            PointStruct(
                id=point_id,
                vector=_META_VECTOR,
                payload={
                    "collection": collection_name,
                    "embedding_model": embedding_model,
                    "vector_dimension": vector_dimension,
                },
            )
        ],
        wait=True,
    )


def ingest_file(
    *,
    client: QdrantClient,
    http: httpx.Client,
    embed_cfg: EmbedderConfig,
    meta_collection: str,
    md_path: Path,
    data_root: Path,
    collection: str,
    chunk_tokens: int,
    overlap: int,
    recreate_collection: bool = True,
    acl_tags: list[str] | None = None,
) -> IngestResult:
    """Ingest one Markdown file into ``collection``.

    When ``recreate_collection`` is ``True`` (default), the collection is
    wiped and recreated before upserting — this is the right behaviour for
    the first file in a collection group. Subsequent files in the same
    collection must pass ``recreate_collection=False`` so their chunks are
    appended rather than replacing the previous ones.
    """
    relative_source = md_path.relative_to(data_root).as_posix()

    text = md_path.read_text(encoding="utf-8")
    chunks = chunk_text(text, chunk_tokens=chunk_tokens, overlap=overlap)

    if not chunks:
        logger.warning("No content to vectorize in %s — collection left empty", md_path)
        # Still probe the endpoint once to determine the vector dimension so
        # the collection (if new) gets the right schema.
        probe = embed_batch(http, cfg=embed_cfg, texts=["probe"])
        vector_size = len(probe[0])
        if recreate_collection:
            ensure_collection(client, name=collection, vector_size=vector_size)
        upsert_meta_entry(
            client,
            meta_collection=meta_collection,
            collection_name=collection,
            embedding_model=embed_cfg.model,
            vector_dimension=vector_size,
        )
        return IngestResult(role=collection, collection=collection, source=md_path, chunk_count=0)

    embeddings = embed_chunks(http, cfg=embed_cfg, chunks=chunks)
    vector_size = len(embeddings[0])
    if recreate_collection:
        ensure_collection(client, name=collection, vector_size=vector_size)

    # Point IDs are scoped to the source file path so chunks from
    # different files within the same collection never collide.
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"qdrant-rbac/{relative_source}")

    def _payload(idx: int, chunk: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "source": relative_source,
            "chunk_index": idx,
            "text": chunk,
        }
        if acl_tags:
            payload["acl_tags"] = list(acl_tags)
        return payload

    points = [
        PointStruct(
            id=str(uuid.uuid5(namespace, f"{relative_source}#{idx}")),
            vector=vector,
            payload=_payload(idx, chunk),
        )
        for idx, (chunk, vector) in enumerate(zip(chunks, embeddings, strict=True))
    ]

    client.upsert(collection_name=collection, points=points, wait=True)
    upsert_meta_entry(
        client,
        meta_collection=meta_collection,
        collection_name=collection,
        embedding_model=embed_cfg.model,
        vector_dimension=vector_size,
    )

    return IngestResult(
        role=collection, collection=collection, source=md_path, chunk_count=len(points)
    )


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid integer for {name}: {raw!r}") from exc


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )

    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_api_key = os.getenv("QDRANT_API_KEY") or None

    embed_cfg = EmbedderConfig(
        api_url=os.getenv("EMBEDDING_API_URL", DEFAULT_API_URL),
        api_key=os.getenv("EMBEDDING_API_KEY", ""),
        model=os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL),
        batch=_env_int("EMBEDDING_BATCH", DEFAULT_BATCH),
    )
    meta_collection = os.getenv("EMBEDDING_META_COLLECTION", DEFAULT_META_COLLECTION)

    data_dir = Path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR))).resolve()
    chunk_tokens = _env_int("CHUNK_TOKENS", DEFAULT_CHUNK_TOKENS)
    overlap = _env_int("CHUNK_OVERLAP", DEFAULT_CHUNK_OVERLAP)

    logger.info(
        "Using embeddings endpoint %s with model %s",
        embed_cfg.api_url,
        embed_cfg.model,
    )

    md_files = discover_markdown_files(data_dir)
    if not md_files:
        logger.warning("No Markdown files found under %s", data_dir)
        return 0
    logger.info("Found %d Markdown file(s) under %s", len(md_files), data_dir)

    client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    ensure_meta_collection(client, name=meta_collection)

    acl_tags_map = load_acl_tags(data_dir)
    if acl_tags_map:
        logger.info(
            "Loaded acl_tags for %d source file(s) from %s",
            len(acl_tags_map),
            data_dir / ACL_TAGS_FILENAME,
        )

    # Track which collections have been (re)created so subsequent files in the
    # same group are appended rather than overwriting the previous chunks.
    seen_collections: set[str] = set()

    results: list[IngestResult] = []
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=5.0)) as http:
        for md_path in md_files:
            relative_source = md_path.relative_to(data_dir).as_posix()
            coll = collection_name_for(md_path.stem)
            recreate = coll not in seen_collections
            seen_collections.add(coll)
            result = ingest_file(
                client=client,
                http=http,
                embed_cfg=embed_cfg,
                meta_collection=meta_collection,
                md_path=md_path,
                data_root=data_dir,
                collection=coll,
                chunk_tokens=chunk_tokens,
                overlap=overlap,
                recreate_collection=recreate,
                acl_tags=acl_tags_map.get(relative_source),
            )
            results.append(result)
            logger.info(
                "Ingested collection=%s chunks=%d source=%s%s",
                result.collection,
                result.chunk_count,
                result.source.relative_to(data_dir).as_posix(),
                " (collection created)" if recreate else " (appended)",
            )

    print("\nSummary")
    print("-------")
    print(f"{'Source file':<32} {'Chunks':>8}  Collection")
    for r in results:
        print(f"{r.source.name:<32} {r.chunk_count:>8}  {r.collection}")
    print(f"\nTotal collections created/refreshed: {len(seen_collections)}")
    print(f"Total source files ingested:          {len(results)}")
    print(f"Meta collection: {meta_collection}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
