# 07 · Connect OpenCode (the user side)

> Goal: on a **user's own computer**, point OpenCode at the server and run a real
> search. Do this once yourself to confirm everything works end-to-end, then hand
> the short version to the group.

Nothing here runs on the VM — this is all on each user's laptop/desktop.

---

## What each user needs

- **OpenCode** installed — <https://opencode.ai> (install instructions there).
- An **NHR@FAU API token** (from the NHR portal) — the LLM the assistant uses.
- The **server address** from the admin: `http://<VM-IP>:8080/mcp`
  (this deployment: `http://10.76.33.35:8080/mcp`).
- A **username** to identify themselves in the logs (e.g. their initials).

---

## Step 1 — Set your NHR@FAU API token

OpenCode reads the token from an environment variable, so it never sits in the
config file. In **PowerShell** (Windows):

```powershell
# current session — THE QUOTES ARE REQUIRED:
$env:NHR_API_TOKEN="PASTE_YOUR_TOKEN_HERE"
echo $env:NHR_API_TOKEN                        # verify it's set
setx NHR_API_TOKEN "PASTE_YOUR_TOKEN_HERE"     # permanent (new terminals)
```

> ⚠️ Use the quotes. `$env:NHR_API_TOKEN=...` (no quotes) fails. `setx` only
> affects **future** terminals — **restart OpenCode** afterwards.
> 🔒 Never put the token in the config file, a screenshot, or chat. If it ever
> leaks, **regenerate it in the NHR portal** and set the new one.
> macOS/Linux: `export NHR_API_TOKEN="..."` in `~/.bashrc` / `~/.zshrc`.

---

## Step 2 — Edit your OpenCode config

Open the config (create it if missing):
- **Windows:** `%USERPROFILE%\.config\opencode\opencode.json`
- **macOS/Linux:** `~/.config/opencode/opencode.json`

Replace the **whole file** with this (also in
[`../client-config/opencode.example.json`](../client-config/opencode.example.json)).
Change the **`url`** to your VM and **`X-User`** to your name:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "nhr-fau/gpt-oss-120b",
  "provider": {
    "nhr-fau": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "NHR@FAU",
      "options": {
        "baseURL": "https://hub.nhr.fau.de/api/llmgw/v1",
        "apiKey": "{env:NHR_API_TOKEN}"
      },
      "models": {
        "gpt-oss-120b": { "name": "gpt-oss-120b" },
        "Kimi-K2.6": { "name": "Kimi-K2.6" },
        "DeepSeek-V4-Flash": { "name": "DeepSeek-V4-Flash" },
        "Mistral-Medium-3.5-128B": { "name": "Mistral-Medium-3.5-128B" },
        "gemma-4-E4B-it": { "name": "gemma-4-E4B-it" }
      }
    }
  },
  "mcp": {
    "msei-papers": {
      "type": "remote",
      "url": "http://10.76.33.35:8080/mcp",
      "enabled": true,
      "headers": { "X-User": "anna.k" }
    }
  }
}
```

- `model` — default model written `provider-id/model-id` (`nhr-fau/gpt-oss-120b`).
- `apiKey: "{env:NHR_API_TOKEN}"` — pulls the token you set in Step 1.
- `mcp.msei-papers.url` — the VM address from the admin, ending in `/mcp`.
- `headers.X-User` — how you show up in the server's audit log (honour-system
  label, not a password).

> **It must be one object** — a single `{ }` with `model`, `provider`, and `mcp`
> as siblings (commas between, no stray `}`). An `EndOfFileExpected` error means a
> brace closed the file too early.

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

> 1. Install OpenCode.
> 2. Set your NHR token (PowerShell): `$env:NHR_API_TOKEN="..."` then
>    `setx NHR_API_TOKEN "..."` (mind the quotes; restart OpenCode after).
> 3. Open `%USERPROFILE%\.config\opencode\opencode.json` and paste the ready file
>    [`client-config/opencode.example.json`](../client-config/opencode.example.json);
>    set your `X-User` name and the VM `url`.
> 4. Restart OpenCode. Ask it to "call corpus_stats on msei-papers".

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `EndOfFileExpected` / JSON error on start | A brace closed the file too early. It must be **one** object — `model`, `provider`, `mcp` as siblings, commas between, no stray `}`. Validate at jsonlint.com. |
| Model errors / `401 Unauthorized` | `NHR_API_TOKEN` isn't set in this session, or you didn't restart OpenCode after `setx`. Re-check `echo $env:NHR_API_TOKEN`. |
| Tools don't appear | Check the `url` ends in `/mcp` and the VM IP is right. Open `http://<VM-IP>:8080/health` in a browser. |
| `/health` hangs in the browser | Network/firewall: your machine isn't on the allowed subnet (admin → [step 06](06-run-mcp-server.md#3-lock-down-the-network-important)). |
| Connects but every search errors | Server-side key/proxy issue — admin checks `docker compose logs mcp`. |

---

✅ A user can now search the whole library from their own machine with their own
model. That's the system working end-to-end.

⬅️ Back: [06 · Run the MCP server](06-run-mcp-server.md)  ·  ➡️ Next: [08 · Update / add a database](08-update-add-database.md)
