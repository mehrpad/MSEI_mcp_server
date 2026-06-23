# 04 · Load the paper data into Qdrant

> Goal: copy the **snapshot bundle** (produced by the separate ingest repo) onto
> the VM and restore it into Qdrant. After this, the library is searchable.

This is the "copy Qdrant to the VM" step.

---

## What you're loading

You get one `.snapshot` file **per collection**. A full corpus has up to four
(`materials_v2` text, `_figures`, `_tables`, `_summaries`), but you may have fewer
— e.g. just text + summaries. That's fine; the server uses whatever is present.

Filenames come in one of two shapes — both are handled automatically:

```
# Qdrant native (most common):  <collection>-<id>-<date>.snapshot
materials_v2-2819031290988516-2026-05-27-12-40-06.snapshot            ← collection: materials_v2
materials_v2_summaries-2819031290988516-2026-05-27-12-41-41.snapshot  ← collection: materials_v2_summaries

# bundler (.tar.gz from the ingest repo):  <collection>__<name>.snapshot
materials_v2__<...>.snapshot                                          ← collection: materials_v2
```

In both, the **collection name is the leading part** of the filename — that's the
collection each file restores into. The restore script figures this out for you.

---

## Step 1 — Copy the snapshots to the VM

Make a folder on the **VM**:

```bash
mkdir -p ~/snapshots
```

Then copy from **your computer**. For **loose `.snapshot` files** (use your real
filenames):

```bash
scp "C:\path\to\materials_v2-2819031290988516-2026-05-27-12-40-06.snapshot"           msei@10.12.0.5:~/snapshots/
scp "C:\path\to\materials_v2_summaries-2819031290988516-2026-05-27-12-41-41.snapshot" msei@10.12.0.5:~/snapshots/
```

Or for a **`.tar.gz` bundle** — copy it, then unpack on the VM:

```bash
scp "C:\path\to\bundle.tar.gz" msei@10.12.0.5:~/
# then, on the VM:
tar -xzf ~/bundle.tar.gz -C ~/snapshots
```

Confirm on the **VM**:

```bash
ls -lh ~/snapshots
```

---

## Step 3 — Restore the snapshots into Qdrant

Use the helper script included in this repo. It uploads every `.snapshot` in a
folder to the matching collection:

```bash
cd ~/MSEI_mcp_server
bash scripts/restore-snapshot.sh ~/snapshots
```

You'll see one block per collection ending in `"result": true`.

> ⚠️ **A multi-GB snapshot takes minutes and looks frozen while it loads.** Let
> each one finish — **do not press Ctrl-C**, or that collection ends up empty and
> you'll have to start it over. To watch progress, open a second SSH session and
> run `docker compose logs -f qdrant`.

<details>
<summary>What the script runs under the hood (if you prefer to do it by hand)</summary>

For each file it calls Qdrant's snapshot-upload API, naming the target collection
in the URL:

```bash
curl -sS -X POST \
  "http://localhost:6333/collections/materials_v2/snapshots/upload?priority=snapshot" \
  -H 'Content-Type: multipart/form-data' \
  -F "snapshot=@$HOME/snapshots/materials_v2-2819031290988516-2026-05-27-12-40-06.snapshot"
```

The collection name (`materials_v2`) is the leading part of the filename. Doing it
by hand like this is the foolproof fallback if a filename is unusual — just put
the right collection name in the URL.
</details>

---

## Step 4 — Verify the data is there

```bash
curl -s http://localhost:6333/collections | jq
```

You'll see the collections you restored. Check the point counts — this loop lists
**whatever exists**, so it works whether you have two collections or four:

```bash
for c in $(curl -s http://localhost:6333/collections | jq -r '.result.collections[].name'); do
  printf "%-30s " "$c"
  curl -s "http://localhost:6333/collections/$c" | jq '.result.points_count'
done
```

Non-zero numbers = success. The library is loaded. ✅

> **Fewer than four collections?** Totally fine. With just `materials_v2` and
> `materials_v2_summaries` you get full text + paper-summary search; the
> figure/table tools simply return nothing.

---

## Step 5 — Make sure `.env` points at this data

The server serves whichever collection **prefix** is named in `.env`. Open it:

```bash
nano .env
```

Make sure this line matches the collections you just restored — the base name
without the `_figures`/`_tables`/`_summaries` suffix (for the example files,
that's `materials_v2`):

```
COLLECTION_PREFIX=materials_v2
```

Save (`Ctrl+O`, `Enter`) and exit (`Ctrl+X`).

> If your snapshots were for a different corpus, e.g.
> `materials_v2_external-<id>-<date>.snapshot`, set
> `COLLECTION_PREFIX=materials_v2_external`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| **`unknown variant `rocks_db`, expected `gridstore` or `mmap`** | Qdrant version mismatch: your snapshot is RocksDB-format and Qdrant **v1.17+ removed RocksDB**. Pin the image to `qdrant/qdrant:v1.16.0` in `docker-compose.yml`, then `docker compose down -v && docker compose up -d qdrant` and restore again. (This repo is already pinned to v1.16.0.) |
| `404` or `Collection not found` during restore | Usually fine — the upload **creates** the collection. If it persists, check the collection name is the leading part of the filename. |
| `points_count` is 0 after restore | The snapshot was empty, or you restored the wrong file. Re-check the file. |
| Restore is very slow | Large corpora take time; the snapshot is being indexed. Watch progress with `docker compose logs -f qdrant`. |
| `jq: command not found` | `sudo apt install -y jq` (just makes JSON readable; not required). |
| Out of disk space | Check with `df -h`. Snapshots need roughly 2× the final data size while restoring. |

---

✅ Papers are loaded. Now give the server its Google key so it can search them.

⬅️ Back: [03 · Start Qdrant](03-start-qdrant.md)  ·  ➡️ Next: [05 · Google API key](05-google-api-key.md)
