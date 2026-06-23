# 08 · Update, add, or replace a vector database

> Goal: change what the server searches — load a newer version of the corpus, add
> a second corpus, or swap between them — safely, with a way to roll back.

Remember the split: **new data is always produced by the separate ingest repo** as
a snapshot bundle. This page is about getting that bundle live on the VM.

---

## Key idea: a "database" = a collection **prefix**

The server serves the four collections under one prefix (set by
`COLLECTION_PREFIX` in `.env`):

```
<prefix>            <prefix>_figures   <prefix>_tables   <prefix>_summaries
```

So you can keep **several** databases side by side in the same Qdrant, each under
its own prefix, and switch which one the server uses by changing one line and
restarting. This makes updates safe: load the new one, test it, switch, and keep
the old one as a fallback.

See what's currently loaded any time:

```bash
curl -s http://localhost:6333/collections | jq '.result.collections[].name'
```

or, from OpenCode, ask the assistant to call **`list_databases`**.

---

## Scenario A — Update to a newer corpus (recommended: prefix swap)

The ingest repo gives you a fresh bundle (more publications, better metadata). Load it
under a **new** prefix so the current one keeps working until you're happy.

1. **Copy & unpack** the new bundle (as in [step 04](04-load-vector-data.md)).
   Assume its collections are named `materials_v3*`.

   ```bash
   scp materials_v3_bundle.tar.gz msei@10.12.0.5:~/
   mkdir -p ~/snap_v3 && tar -xzf ~/materials_v3_bundle.tar.gz -C ~/snap_v3
   ```

2. **Restore** it (creates the `materials_v3*` collections alongside the old ones):

   ```bash
   cd ~/MSEI_mcp_server
   bash scripts/restore-snapshot.sh ~/snap_v3
   ```

3. **Verify** the new data:

   ```bash
   for c in materials_v3 materials_v3_figures materials_v3_tables materials_v3_summaries; do
     printf "%-30s " "$c"; curl -s "http://localhost:6333/collections/$c" | jq '.result.points_count'
   done
   ```

4. **Switch** the server to it (updates `.env` and restarts the MCP container):

   ```bash
   bash scripts/switch-database.sh materials_v3
   curl http://localhost:8080/health     # "prefix": "materials_v3"
   ```

5. **Test** from OpenCode. Happy? Done. Unhappy? Roll back instantly:

   ```bash
   bash scripts/switch-database.sh materials_v2
   ```

6. Once confident, free space by deleting the old one (Scenario D).

> Users do **not** need to change anything — they keep using the same URL. Only the
> data behind it changed.

---

## Scenario B — Add a second, separate corpus

Maybe you want a "main" library and a separate "external collaborators" set. Just
restore each under its own prefix; they coexist. Switch between them with
`switch-database.sh <prefix>` whenever you want the server to serve a different one.

```bash
bash scripts/restore-snapshot.sh ~/snap_collab     # e.g. materials_v2_collab*
bash scripts/switch-database.sh materials_v2_collab # serve it
bash scripts/switch-database.sh materials_v2        # back to the main one
```

### Serve both corpora at the same time (second MCP server)

One MCP server serves **one** prefix. To offer **two** libraries simultaneously,
this repo ships an optional second server (a Docker Compose *profile*) on its own
port — same Qdrant, different prefix.

1. In `.env`, set the second corpus and its port (uncomment them):

   ```
   COLLECTION_PREFIX_2=materials_v2_external
   HOST_PORT_2=8081
   ```

2. Start it (the `--profile second` flag is what turns it on):

   ```bash
   docker compose --profile second up -d
   ```

3. Now you have two endpoints:
   - main corpus → `http://<VM-IP>:8080/mcp`
   - second corpus → `http://<VM-IP>:8081/mcp`

   Point each group of users at the URL they need (same OpenCode config as
   [step 07](07-connect-opencode.md), just a different port).

4. Open the second port to your subnet too — repeat the firewall rule from
   [step 06 §3](06-run-mcp-server.md#3-lock-down-the-network-important) with
   `--dport 8081`.

Stop just the second server with `docker compose stop mcp-secondary`.

> The two share one Qdrant, so make sure **both** prefixes are restored and that
> both corpora were embedded with the same `EMBED_MODEL`.

---

## Scenario C — Replace the current corpus in place

If you don't need a fallback and just want to overwrite:

```bash
# delete the old collections, then restore the new bundle under the same names
for c in materials_v2 materials_v2_figures materials_v2_tables materials_v2_summaries; do
  curl -s -X DELETE "http://localhost:6333/collections/$c"; echo
done
bash scripts/restore-snapshot.sh ~/snap_new_same_names
docker compose restart mcp
```

⚠️ This has a short window with no data. Prefer **Scenario A** for anything users
depend on.

---

## Scenario D — Delete a collection / database (free disk space)

```bash
# Delete one collection:
curl -X DELETE http://localhost:6333/collections/materials_v2_figures

# Delete a whole old database (all four):
for c in materials_v2 materials_v2_figures materials_v2_tables materials_v2_summaries; do
  curl -s -X DELETE "http://localhost:6333/collections/$c"; echo
done
```

Check free space before/after with `df -h`.

> **Never delete the prefix the server is currently using** (check
> `curl localhost:8080/health`). Switch away first.

---

## A note on the embedding model

Every database on this server must have been embedded with the **same model**
(`EMBED_MODEL`, default `gemini-embedding-2-preview`). If a future corpus uses a
different embedding model or vector size, search will silently return nonsense.
The ingest repo controls this — keep `EMBED_MODEL` in `.env` matched to it.

---

⬅️ Back: [07 · Connect OpenCode](07-connect-opencode.md)  ·  ➡️ Next: [09 · Operations & troubleshooting](09-operations-troubleshooting.md)
