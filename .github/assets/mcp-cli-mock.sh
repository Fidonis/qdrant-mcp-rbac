#!/usr/bin/env bash
# Mock qdrant-mcp-rbac client for demo recording purposes only.
# Shows what the same MCP tool call returns when invoked by two
# different OIDC identities with different role grants and doc-policies.

set -e

# --- arg parsing ---
USER=""
TOOL=""
COLLECTION=""
QUERY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) USER="$2"; shift 2 ;;
    call) TOOL="$2"; shift 2 ;;
    collection=*) COLLECTION="${1#collection=}"; shift ;;
    query=*) QUERY="${1#query=}"; shift ;;
    *) shift ;;
  esac
done

# subtle ANSI helpers
DIM='\033[2m'
GREEN='\033[32m'
BLUE='\033[34m'
RESET='\033[0m'

echo -e "${DIM}→ exchanging OIDC token at https://kc.example.com/realms/myrealm${RESET}"
sleep 0.4
echo -e "${DIM}→ POST /mcp tools/call ${TOOL}${RESET}"
sleep 0.5

case "$USER" in
  alice@acme.com)
    cat <<JSON
{
  "count": 3,
  "results": [
    { "id": 142, "source": "sales/Q3-acme-revenue.pdf",   "score": 0.91 },
    { "id": 198, "source": "sales/Q3-board-summary.pdf",  "score": 0.87 },
    { "id": 207, "source": "public/quarterly-newsletter.md", "score": 0.74 }
  ]
}
JSON
    ;;
  bob@vendor.com)
    cat <<JSON
{
  "count": 1,
  "results": [
    { "id": 207, "source": "public/quarterly-newsletter.md", "score": 0.74 }
  ]
}
JSON
    ;;
esac
