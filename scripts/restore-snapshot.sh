#!/usr/bin/env bash
# Restore Qdrant snapshot files into the local Qdrant instance.
#
# Usage:
#   bash scripts/restore-snapshot.sh <dir-with-.snapshot-files> [QDRANT_URL]
#
# Works out the target collection from each filename, in two supported formats:
#   * Qdrant native:  <collection>-<id>-<YYYY-MM-DD-HH-MM-SS>.snapshot
#                     e.g. materials_v2-2819031290988516-2026-05-27-12-40-06.snapshot
#   * bundler:        <collection>__<snapshotname>.snapshot
# The collection is created automatically on upload.
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
  if [[ "$base" == *"__"* ]]; then
    # bundler format: <collection>__<snapshotname>.snapshot
    coll="${base%%__*}"
  else
    # Qdrant native format: <collection>-<id>-<YYYY-MM-DD-HH-MM-SS>.snapshot
    coll="$(printf '%s' "$base" | sed -E 's/-[0-9]+-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}\.snapshot$//')"
  fi
  if [[ -z "$coll" || "$coll" == "$base" ]]; then
    echo "!! Could not determine the collection name from '$base'." >&2
    echo "   Restore it by hand with the target name, e.g.:" >&2
    echo "   curl -X POST ${QDRANT_URL%/}/collections/<NAME>/snapshots/upload -F snapshot=@'$f'" >&2
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
