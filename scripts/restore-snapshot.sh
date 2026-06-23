#!/usr/bin/env bash
# Restore Qdrant snapshot files into the local Qdrant instance.
#
# Usage:
#   bash scripts/restore-snapshot.sh <dir-with-.snapshot-files> [QDRANT_URL]
#
# Each file should be named "<collection>__<snapshotname>.snapshot" (the naming
# the ingest repo's snapshot bundler produces). The collection name is taken from
# the part before "__"; the collection is created automatically on upload.
set -euo pipefail

DIR="${1:-}"
QDRANT_URL="${2:-http://localhost:6333}"

if [[ -z "$DIR" || ! -d "$DIR" ]]; then
  echo "Usage: bash scripts/restore-snapshot.sh <dir-with-.snapshot-files> [QDRANT_URL]" >&2
  exit 1
fi

shopt -s nullglob
files=("$DIR"/*.snapshot)
if (( ${#files[@]} == 0 )); then
  echo "No .snapshot files found in: $DIR" >&2
  exit 1
fi

echo "Restoring ${#files[@]} snapshot(s) into ${QDRANT_URL}"
for f in "${files[@]}"; do
  base="$(basename "$f")"
  coll="${base%%__*}"
  if [[ "$coll" == "$base" ]]; then
    echo "!! Skipping '$base' — name has no '__', so the target collection is unknown." >&2
    echo "   Restore it by hand with the collection name you want." >&2
    continue
  fi
  echo "==> ${coll}  (from ${base})"
  curl -sS -X POST \
    "${QDRANT_URL%/}/collections/${coll}/snapshots/upload?priority=snapshot" \
    -H 'Content-Type: multipart/form-data' \
    -F "snapshot=@${f}"
  echo
done

echo
echo "Done. Current collections:"
curl -s "${QDRANT_URL%/}/collections" | (jq -r '.result.collections[].name' 2>/dev/null || cat)
