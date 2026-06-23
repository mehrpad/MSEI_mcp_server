# 07 · Connect OpenCode (the user side)

> Goal: on a **user's own computer**, point OpenCode at the server and run a real
> search. Do this once yourself to confirm everything works end-to-end, then hand
> the short version to the group.

Nothing here runs on the VM — this is all on each user's laptop/desktop.

---

## What each user needs

- **OpenCode** installed — <https://opencode.ai> (install instructions there).
- **A local LLM** already working in OpenCode (e.g. via Ollama or LM Studio). The
  group already uses this; if not, see OpenCode's model docs.
- The **server address** from the admin: `http://<VM-IP>:8080/mcp`
  (e.g. `http://10.12.0.5:8080/mcp`).
- A **username** to identify themselves in the logs (e.g. their initials).

---

## Step 1 — Open the OpenCode config file

OpenCode reads a JSON config. Use either:

- **Per-project:** a file named `opencode.json` in the folder they work in, **or**
- **Global (recommended for everyone):** `opencode.json` in the OpenCode config
  directory:
  - macOS/Linux: `~/.config/opencode/opencode.json`
  - Windows: `%USERPROFILE%\.config\opencode\opencode.json`

If the file doesn't exist, create it.

---

## Step 2 — Add the paper server

Paste this, replacing the **URL** with your VM address and the **X-User** value
with the person's name/initials:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "msei-papers": {
      "type": "remote",
      "url": "http://10.12.0.5:8080/mcp",
      "enabled": true,
      "headers": {
        "X-User": "anna.k"
      }
    }
  }
}
```

- `type: "remote"` — it's a server over the network (not a local program).
- `url` — the VM address from the admin, ending in `/mcp`.
- `headers.X-User` — how this person shows up in the server's audit log. It's an
  honour-system label (not a password), so just use something recognisable.

> Already have other things in `opencode.json`? Only add the `"msei-papers"` block
> **inside** your existing `"mcp": { ... }` section. Keep the JSON valid (commas
> between entries, no trailing comma).

Save the file.

---

## Step 3 — Restart OpenCode and check the tools appeared

Close and reopen OpenCode (or start a new session). The server's tools should now
be available. Ask OpenCode something like:

> *"Use the msei-papers tools. What's in the corpus? Call corpus_stats."*

If it reports paper/chunk/figure/table counts, you're connected. 🎉

You can also ask it to list what's available:

> *"List the available paper databases."* → it calls `list_databases`.

---

## Step 4 — Run a real search

Try a domain question, e.g.:

> *"Search the papers for the effect of rhenium on creep resistance in
> nickel-base superalloys. Give me the top passages with their DOIs."*

OpenCode will call `search_text` (and maybe `evidence_pack`) and answer with
quotes and citations pulled from the library.

A few of the most useful tools the assistant can call:

| Tool | Use it for |
|------|-----------|
| `search_text` | Main semantic search over paper passages. |
| `evidence_pack` | Gather text + tables + figures for a research question at once. |
| `search_papers` | Find whole papers by topic. |
| `get_paper` / `get_paper_chunks` | Read everything about one paper (by DOI). |
| `search_tables` / `search_figures` | Find specific data tables or figures. |
| `corpus_stats` / `list_keywords` | Understand what's in the library and how to filter. |

(The server exposes **24 tools** in total — citation-graph queries, composition
search, image similarity, and more.)

---

## The short version to give the group

> 1. Install OpenCode and make sure your local model works.
> 2. Open `~/.config/opencode/opencode.json` (create it if missing).
> 3. Paste the `msei-papers` block from the admin, set your `X-User` name.
> 4. Restart OpenCode. Ask it to "call corpus_stats on msei-papers".
> A ready-to-edit file is in this repo at
> [`client-config/opencode.example.json`](../client-config/opencode.example.json).

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Tools don't appear | Check the URL ends in `/mcp` and the VM IP is correct. From the user's machine: `curl http://10.12.0.5:8080/health`. |
| `curl` to /health hangs | Network/firewall: the user's machine isn't on the allowed subnet (admin → [step 06](06-run-mcp-server.md#3-lock-down-the-network-important)). |
| Connects but every search errors | Server-side key/internet issue — admin checks `docker compose logs mcp`. |
| JSON error on OpenCode start | The `opencode.json` is malformed. Validate it (e.g. paste into jsonlint.com). |

---

✅ A user can now search the whole library from their own machine with their own
model. That's the system working end-to-end.

⬅️ Back: [06 · Run the MCP server](06-run-mcp-server.md)  ·  ➡️ Next: [08 · Update / add a database](08-update-add-database.md)
