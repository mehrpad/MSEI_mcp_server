# 09 · Operations & troubleshooting

> Goal: the day-to-day admin tasks — start/stop, logs, backups, updates — and a
> single place to look when something breaks.

All commands run **on the VM**, in `~/MSEI_mcp_server`.

---

## Everyday commands (cheat sheet)

| Task | Command |
|------|---------|
| See what's running | `docker compose ps` |
| Live logs (server) | `docker compose logs -f mcp` |
| Live logs (database) | `docker compose logs -f qdrant` |
| Restart just the server | `docker compose up -d mcp` |
| Restart everything | `docker compose restart` |
| Stop everything (keep data) | `docker compose down` |
| Start everything again | `docker compose up -d` |
| Is the server healthy? | `curl http://localhost:8080/health` |
| How big is the corpus? | ask OpenCode for `corpus_stats`, or curl Qdrant |
| Free disk space | `df -h` |

> `docker compose down` stops the containers but **keeps your data** (it lives in
> named volumes). Your papers are safe across restarts and reboots.

---

## Back up the data (do this regularly)

Your corpus is precious. Two ways to back it up:

### Option 1 — back up the whole Qdrant storage volume (simplest)

```bash
docker run --rm \
  -v msei-paperrag_qdrant_storage:/data:ro \
  -v "$PWD":/backup \
  busybox tar czf /backup/qdrant-backup-$(date +%F).tar.gz -C /data .
```

This writes `qdrant-backup-YYYY-MM-DD.tar.gz` into the project folder. Copy it
somewhere safe (off the VM) with `scp` from your computer:

```bash
scp msei@10.12.0.5:~/MSEI_mcp_server/qdrant-backup-*.tar.gz .
```

> The volume name is `<project>_<volume>` = `msei-paperrag_qdrant_storage`.
> Confirm with `docker volume ls`.

### Option 2 — per-collection snapshots (portable, same format as the ingest repo)

```bash
# Create a snapshot of one collection and download it:
NAME=$(curl -s -X POST http://localhost:6333/collections/materials_v2/snapshots | jq -r .result.name)
curl -s "http://localhost:6333/collections/materials_v2/snapshots/$NAME" -o "materials_v2__$NAME"
```

These `.snapshot` files restore exactly like the bundles in
[step 04](04-load-vector-data.md).

**To restore a backup:** stop the stack, replace the volume contents, or use
`scripts/restore-snapshot.sh` for snapshot files. For volume restore, ask for help
so you don't overwrite live data by accident.

---

## Update the server code (after a fix or new feature)

```bash
cd ~/MSEI_mcp_server
git pull                      # if you cloned it; otherwise re-copy the files
docker compose up -d --build mcp
```

`--build` rebuilds the image with the new code. Qdrant and your data are
untouched.

---

## Read the audit log (who searched what)

```bash
# From container logs:
docker compose logs mcp | grep '"user"' | tail -50

# If you enabled AUDIT_LOG=/data/audit.log in .env, the permanent file is in the
# mcp_data volume:
docker compose exec mcp cat /data/audit.log | tail -50
```

Each line is JSON: timestamp, client IP, the `X-User` they declared, method, path.

---

## Make it survive reboots

Already handled: Docker is enabled on boot ([step 02](02-install-docker.md)) and
both containers use `restart: unless-stopped`. After a VM reboot, wait a minute,
then `docker compose ps` to confirm both are back. If not: `docker compose up -d`.

---

## Problem → fix table

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `msei-mcp` restart-loops | Bad/missing `GEMINI_API_KEY` | `docker compose logs mcp`; fix `.env`; `docker compose up -d mcp`. |
| `/health` OK but all searches error | No internet to Google, or quota hit | Test [step 05 §4](05-google-api-key.md#step-4--confirm-the-vm-can-reach-google); check logs for `RESOURCE_EXHAUSTED`. |
| Searches return nothing/garbage | Wrong `COLLECTION_PREFIX`, or data embedded with a different model | `curl localhost:8080/health` shows the prefix; confirm collections exist; confirm `EMBED_MODEL` matches ingestion. |
| Users can't connect | Firewall/subnet or wrong URL | From a user machine: `curl http://<VM-IP>:8080/health`. Re-check [step 06 §3](06-run-mcp-server.md#3-lock-down-the-network-important). |
| `no space left on device` | Disk full (snapshots, old DBs, logs) | `df -h`; delete old databases ([step 08 §D](08-update-add-database.md#scenario-d--delete-a-collection--database-free-disk-space)); remove old snapshot files. |
| Qdrant won't start | Corrupted/locked storage, or port in use | `docker compose logs qdrant`; ensure nothing else uses 6333. |
| Restore fails: `unknown variant `rocks_db`` | Snapshot is RocksDB-format; Qdrant **v1.17+ removed RocksDB** | Use `qdrant/qdrant:v1.16.0` (this repo is pinned to it). To re-fix after a bad attempt: `docker compose down -v && docker compose up -d qdrant`, then restore again. |
| Everything is slow | Many concurrent searches, or a huge corpus on a small VM | Check load with `docker stats`; consider more vCPU/RAM. |
| Container says "unhealthy" | App not answering /health yet, or crashed | Give it 30s; if it persists, read the logs. |

When asking for help, always include the output of:

```bash
docker compose ps
docker compose logs --tail 50 mcp
```

---

## Start completely fresh (factory reset)

⚠️ **Deletes all loaded data.** You'd re-load from snapshots afterward.

```bash
docker compose down -v      # the -v also deletes the data volumes
docker compose up -d        # fresh, empty stack
```

---

## Appendix: offline embeddings

The default design calls Google's embedding API over the internet (one key on the
server). If the VM **cannot** reach the internet and the firewall can't be opened,
the alternative is a **local CPU embedding model** running on the VM (e.g. a
`sentence-transformers` / `fastembed` model). This requires:

1. Changing the embedding function in `mcp_server/server.py` (the `embed_text`
   method) to call the local model instead of Gemini.
2. Re-ingesting the corpus **with the same local model** in the ingest repo, so
   query vectors and stored vectors live in the same space.

This is a deliberate change to both repos — coordinate it rather than flipping a
single switch. Flag it and it can be designed in.

---

⬅️ Back: [08 · Update / add a database](08-update-add-database.md)  ·  🏠 [Overview](00-overview.md)
