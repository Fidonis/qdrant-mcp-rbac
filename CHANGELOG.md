# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Release notes are generated automatically by [release-drafter](https://github.com/release-drafter/release-drafter)
based on merged pull requests; this file mirrors the published releases.

## [Unreleased]

<!-- Updated automatically by release-drafter as PRs are merged to `main`. -->

## [0.3.0] - 2026-06-16

### Added
- `scroll_collection` — pages through raw points with a `next_offset` cursor;
  supports optional `query_filter` and `limit`
- `list_documents` — deduplicated document inventory via Qdrant faceting on the
  `source` payload field; returns one entry per document with chunk count
- Bootstrap (`vectorize.py`) creates keyword payload index on `source` at
  ingest time; server self-heals a missing index on first `list_documents` call

### Fixed
- Agent hallucination of document names (#10): system prompt now directs the
  LLM to use `list_documents` for inventory questions and forbids fabricating
  document names

[Unreleased]: https://github.com/Fidonis/qdrant-mcp-rbac/compare/HEAD...main
[0.3.0]: https://github.com/Fidonis/qdrant-mcp-rbac/compare/v0.2.0...v0.3.0
