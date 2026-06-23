#!/usr/bin/env bash
# Switch the MCP server to a different vector database (collection prefix).
#
# Usage:
#   bash scripts/switch-database.sh <collection_prefix>
#
# Updates COLLECTION_PREFIX in .env and restarts the MCP container. The old
# collections stay in Qdrant, so you can switch back instantly for a rollback.
set -euo pipefail

PREFIX="${1:-}"
if [[ -z "$PREFIX" ]]; then
  echo "Usage: bash scripts/switch-database.sh <collection_prefix>" >&2
  exit 1
fi

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "No .env in $(pwd). Create it first (cp .env.example .env)." >&2
  exit 1
fi

if grep -q '^COLLECTION_PREFIX=' .env; then
  sed -i "s|^COLLECTION_PREFIX=.*|COLLECTION_PREFIX=${PREFIX}|" .env
else
  printf '\nCOLLECTION_PREFIX=%s\n' "$PREFIX" >> .env
fi

echo "Set COLLECTION_PREFIX=${PREFIX}. Restarting the MCP server..."
docker compose up -d mcp

echo "Waiting for the server to come back..."
sleep 3
echo -n "Health: "
curl -s http://localhost:8080/health || echo "(no response yet — check 'docker compose logs mcp')"
echo
