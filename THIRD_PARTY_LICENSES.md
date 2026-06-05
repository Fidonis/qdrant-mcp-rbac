# Third-party licenses

This file is regenerated automatically by
[`.github/workflows/license-check.yml`](.github/workflows/license-check.yml)
on every push to `main`. The list below is the placeholder seeded when
the workflow was introduced and lists the **direct runtime dependencies**
declared in [`src/pyproject.toml`](src/pyproject.toml). The CI will
replace it with the full transitive set as soon as it next runs on
`main`.

| Package | Declared license | Project URL |
|---|---|---|
| `fastmcp` | Apache-2.0 | https://github.com/jlowin/fastmcp |
| `qdrant-client` | Apache-2.0 | https://github.com/qdrant/qdrant-client |
| `python-jose[cryptography]` | MIT | https://github.com/mpdavis/python-jose |
| `httpx` | BSD-3-Clause | https://github.com/encode/httpx |
| `pydantic` | MIT | https://github.com/pydantic/pydantic |
| `pydantic-settings` | MIT | https://github.com/pydantic/pydantic-settings |
| `uvicorn[standard]` | BSD-3-Clause | https://github.com/encode/uvicorn |
| `starlette` | BSD-3-Clause | https://github.com/encode/starlette |

All licenses listed above are MIT-compatible permissive licenses. The
CI workflow fails the build if any dependency under a copyleft license
(GPL / LGPL / AGPL / OSL / EUPL / SSPL) is introduced.

Development-only dependencies (pytest, ruff, mypy …) and the demo
clients are not bundled with the server and are out of scope of this
listing.
