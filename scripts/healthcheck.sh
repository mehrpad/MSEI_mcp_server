#!/usr/bin/env bash
# Quick health overview of the whole stack. Run from the project root.
#
# Usage:
#   bash scripts/healthcheck.sh
set -euo pipefail

QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
MCP_URL="${MCP_URL:-http://localhost:8080}"

echo "== Containers =="
docker compose ps 2>/dev/null || echo "(run from the project root)"
echo

echo "== MCP /health =="
curl -s "${MCP_URL%/}/health" || echo "(no response — is the mcp container up?)"
echo; echo

echo "== Qdrant collections (name : points) =="
names="$(curl -s "${QDRANT_URL%/}/collections" | jq -r '.result.collections[].name' 2>/dev/null || true)"
if [[ -z "$names" ]]; then
  echo "(no collections, or Qdrant not reachable on ${QDRANT_URL})"
else
  while read -r c; do
    [[ -z "$c" ]] && continue
    n="$(curl -s "${QDRANT_URL%/}/collections/${c}" | jq -r '.result.points_count' 2>/dev/null || echo '?')"
    printf "  %-34s %s\n" "$c" "$n"
  done <<< "$names"
fi
