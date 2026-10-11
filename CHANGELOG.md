# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Release notes are generated automatically by [release-drafter](https://github.com/release-drafter/release-drafter)
based on merged pull requests; this file mirrors the published releases.

## [Unreleased]

<!-- Notable changes are collected here until the next release is cut. -->

## [0.4.0] - 2026-10-11

### Changed
- OIDC bearer-token validation and Qdrant token minting use PyJWT instead of
  python-jose (#62). python-jose, ecdsa, rsa, pyasn1 and six leave the
  dependency set; PyJWT becomes a direct dependency (`pyjwt[crypto]>=2.9`).
- A token without an `exp` or an `aud` claim is now rejected. Both were accepted
  before; tokens issued by Keycloak always carry them. `iat` is still not
  checked, so clock skew against the identity provider does not reject fresh
  tokens.
- PyJWT warns when `QDRANT_JWT_SECRET` is shorter than 32 bytes. The placeholder
  in the `.env.example` files is shorter; use a longer secret.
- Dependency updates: PyJWT 2.15.0, cryptography 50.0.0, mcp 1.28.1,
  joserfc 1.6.8, h2 4.4.1, anyio 4.14.2, urllib3 2.8.0, pydantic-settings 2.14.2,
  and the GitHub Actions group.
- The `THIRD_PARTY_LICENSES.md` refresh opens a pull request instead of pushing
  to `main`, which the branch ruleset rejects (#70).

### Fixed
- Publishing to the MCP registry no longer fails with "OCI packages must not
  have 'version' field" (#48).

### Added
- Tests for OIDC token validation and Qdrant token minting, using an in-process
  JWKS instead of an identity provider (#69).
- A project context and architecture overview for contributors (#50);
  `mypy --strict` passes and is checked in CI (#51).

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

[Unreleased]: https://github.com/Fidonis/qdrant-mcp-rbac/compare/v0.4.0...main
[0.4.0]: https://github.com/Fidonis/qdrant-mcp-rbac/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Fidonis/qdrant-mcp-rbac/compare/v0.2.0...v0.3.0
