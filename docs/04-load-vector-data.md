# 04 · Load the paper data into Qdrant

> Goal: copy the **snapshot bundle** (produced by the separate ingest repo) onto
> the VM and restore it into Qdrant. After this, the library is searchable.

This is the "copy Qdrant to the VM" step.

---

## What you're loading

The ingest repo produces a single bundle file, e.g.:

```
colleague_materials_v2_qdrant_20260601.tar.gz
```

Inside it are **four snapshot files** (one per collection) plus a `RESTORE.md`:

```
materials_v2__<...>.snapshot            ← text chunks
materials_v2_figures__<...>.snapshot    ← figures
materials_v2_tables__<...>.snapshot     ← tables
materials_v2_summaries__<...>.snapshot  ← paper summaries
```

The part of each filename **before `__`** is the collection it restores into.

---

## Step 1 — Copy the bundle to the VM

On **your own computer** (where the bundle is), copy it up. See
[01 · Transfer a file](01-linux-basics.md#6-transfer-a-file-to-the-vm-youll-need-this-in-step-04):

```bash
scp "C:\path\to\colleague_materials_v2_qdrant_20260601.tar.gz" msei@10.12.0.5:~/
```

---

## Step 2 — Unpack it on the VM

Back on the **VM**:

```bash
cd ~
mkdir -p snapshots
tar -xzf colleague_materials_v2_qdrant_20260601.tar.gz -C snapshots
ls -lh snapshots
```

You should see the four `.snapshot` files and `RESTORE.md`.

---

## Step 3 — Restore the snapshots into Qdrant

Use the helper script included in this repo. It uploads every `.snapshot` in a
folder to the matching collection:

```bash
cd ~/MSEI_mcp_server
bash scripts/restore-snapshot.sh ~/snapshots
```

You'll see one block per collection ending in `"result": true`. Each upload can
take a while for a large corpus — let it finish.

<details>
<summary>What the script runs under the hood (if you prefer to do it by hand)</summary>

For each file it calls Qdrant's snapshot-upload API:

```bash
curl -sS -X POST \
  "http://localhost:6333/collections/materials_v2/snapshots/upload?priority=snapshot" \
  -H 'Content-Type: multipart/form-data' \
  -F "snapshot=@/home/msei/snapshots/materials_v2__SNAPSHOTNAME.snapshot"
```

The collection name (`materials_v2`) is taken from the filename before `__`.
</details>

---

## Step 4 — Verify the data is there

```bash
curl -s http://localhost:6333/collections | jq
```

You should now see your four collections listed. Check the point counts (how many
items are in each):

```bash
for c in materials_v2 materials_v2_figures materials_v2_tables materials_v2_summaries; do
  printf "%-28s " "$c"
  curl -s "http://localhost:6333/collections/$c" | jq '.result.points_count'
done
```

Non-zero numbers = success. The library is loaded. ✅

---

## Step 5 — Make sure `.env` points at this data

The server serves whichever collection **prefix** is named in `.env`. Open it:

```bash
nano .env
```

Make sure this line matches the collection names you just restored (the part
before `__`, without the `_figures`/`_tables`/`_summaries` suffix):

```
COLLECTION_PREFIX=materials_v2
```

Save (`Ctrl+O`, `Enter`) and exit (`Ctrl+X`).

> If your snapshots were named `materials_v2_external_2026_05_28__...`, then set
> `COLLECTION_PREFIX=materials_v2_external_2026_05_28`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `404` or `Collection not found` during restore | Normal — the upload **creates** the collection. If it persists, check the filename has a `__` separating collection name from snapshot name. |
| `points_count` is 0 after restore | The snapshot was empty, or you restored the wrong file. Re-check the bundle. |
| Restore is very slow | Large corpora take time; the snapshot is being indexed. Watch progress with `docker compose logs -f qdrant`. |
| `jq: command not found` | `sudo apt install -y jq` (just makes JSON readable; not required). |
| Out of disk space | Check with `df -h`. Snapshots need roughly 2× the final data size while restoring. |

---

✅ Papers are loaded. Now give the server its Google key so it can search them.

⬅️ Back: [03 · Start Qdrant](03-start-qdrant.md)  ·  ➡️ Next: [05 · Google API key](05-google-api-key.md)
