# MSEI paper-RAG server

A shared, searchable library of the group's scientific papers — served to ~100
users who each run **OpenCode** with their **own local LLM**. The server turns
questions into semantic searches over a **Qdrant** vector database and answers
with real passages, tables, figures, and citations, over the **Model Context
Protocol (MCP)**.

```
  ~100 people, each on their own PC                  ONE shared Linux VM (private network)
  ┌─────────────────────────────┐                    ┌──────────────────────────────────┐
  │  OpenCode + their local LLM  │  ─ MCP /HTTP ─▶    │  MCP server  ──▶  Qdrant (papers) │
  └─────────────────────────────┘   :8080/mcp         │       │                           │
                                                      │       └──▶ Google embedding API   │
                                                      └──────────────────────────────────┘
```

- **No AI model runs on the server.** Each user's machine runs the LLM; Google's
  API does the embeddings (one server-side key). The VM just stores and searches.
- **One shared corpus**, read-only to users, loaded by an admin from snapshots.
- **Users are identified** by IP + a self-declared `X-User` header (audit-logged).
  The VM is reached only from the group's subnet (firewall).

> **New here?**
> - **One-shot linear runbook (zero → working search):** [`INSTALL.md`](INSTALL.md)
> - **Per-topic guide, with troubleshooting:** [`docs/00-overview.md`](docs/00-overview.md)
>
> Both are written for someone who has never used Linux, step by step.

---

## Two repositories

| Repo | Job | Runs on |
|------|-----|---------|
| **ingest repo** (separate) | PDFs → Qdrant → **snapshot bundles** | admin machine |
| **this repo** | serves the corpus to OpenCode every day | the **VM** |

You load the snapshots produced by the ingest repo onto the VM — no PDF
processing happens here.

---

## Quick start (for admins who know Linux + Docker)

```bash
# on the VM
git clone https://github.com/mehrpad/MSEI_mcp_server.git && cd MSEI_mcp_server
cp .env.example .env && nano .env          # set GEMINI_API_KEY

docker compose up -d qdrant                # 1) start the database
bash scripts/restore-snapshot.sh ~/snapshots   # 2) load papers (snapshot bundle)
docker compose up -d                       # 3) start the MCP server

curl http://localhost:8080/health          # -> {"status":"ok", ...}
bash scripts/healthcheck.sh                # stack overview
```

Then point OpenCode at `http://<VM-IP>:8080/mcp` (see
[`client-config/`](client-config/)) and restrict port 8080 to your subnet
([`docs/06`](docs/06-run-mcp-server.md#3-lock-down-the-network-important)).

The full, beginner-proof version is the numbered guide in [`docs/`](docs/).

---

## What the server exposes

A FastMCP server (Streamable HTTP) with **24 tools** over the corpus's four
collections (text chunks, figures, tables, paper summaries): semantic search,
multi-collection evidence packs, keyword/metadata filtering, citation-graph
queries, composition/property search, image similarity, facets, and corpus stats.
Embeddings use Google `gemini-embedding-2-preview` (3072-dim, cosine). It also
adds `list_databases`, a `/health` endpoint, and per-request IP + username audit
logging.

Switching to a different corpus is one line (`COLLECTION_PREFIX`) plus a restart;
a second corpus can run side-by-side on another port (`docker compose --profile
second up -d`) — see [`docs/08`](docs/08-update-add-database.md). Optional per-user
**API-token auth** is built in and off by default —
see [`docs/10`](docs/10-api-token-auth.md).

---

## Repository layout

```
MSEI_mcp_server/
├── README.md                  ← you are here
├── INSTALL.md                 ← one-shot, line-by-line setup walkthrough
├── docker-compose.yml         ← Qdrant + MCP server
├── .env.example               ← copy to .env, set your key
├── mcp_server/
│   ├── server.py              ← the MCP server (24 tools)
│   ├── requirements.txt
│   └── Dockerfile
├── scripts/
│   ├── restore-snapshot.sh    ← load a snapshot bundle into Qdrant
│   ├── switch-database.sh     ← change which corpus the server serves
│   └── healthcheck.sh         ← quick status of the whole stack
├── client-config/
│   ├── opencode.example.json  ← what each user pastes into OpenCode
│   └── README.md              ← the user-facing short guide
└── docs/                      ← 00–10, the step-by-step setup guide
    ├── 00-overview.md            01-linux-basics.md       02-install-docker.md
    ├── 03-start-qdrant.md        04-load-vector-data.md   05-google-api-key.md
    ├── 06-run-mcp-server.md      07-connect-opencode.md   08-update-add-database.md
    ├── 09-operations-troubleshooting.md
    └── 10-api-token-auth.md      (optional: per-user tokens)
```

---

## Configuration (`.env`)

| Variable | Default | Meaning |
|----------|---------|---------|
| `GEMINI_API_KEY` | — | **Required.** Google embedding key (server-side only). |
| `COLLECTION_PREFIX` | `materials_v2` | Which corpus (collection prefix) to serve. |
| `EMBED_MODEL` | `gemini-embedding-2-preview` | Must match what the data was ingested with. |
| `HOST_PORT` | `8080` | Port the server is published on. |
| `MCP_AUTH_TOKENS` | _(unset)_ | Optional per-user API tokens (`token=name,…`). Empty = open (IP + `X-User`). See [docs/10](docs/10-api-token-auth.md). |
| `COLLECTION_PREFIX_2` | _(unset)_ | Second corpus for the optional `--profile second` server ([docs/08](docs/08-update-add-database.md)). |
| `HOST_PORT_2` | `8081` | Port for the optional second-corpus server. |
| `AUDIT_LOG` | _(unset)_ | If set (e.g. `/data/audit.log`), keep a permanent audit file. |
| `QDRANT_API_KEY` | _(unset)_ | Only if you protect/expose Qdrant. |
| `LOG_LEVEL` | `INFO` | `DEBUG` for more detail. |

---

## Credits

The MCP server is built on the `paperRAG-v2` server from
[AgentAugmentedAutonomousAcademic](https://github.com/peterfelfer/AgentAugmentedAutonomousAcademic),
hardened here for multi-user OpenCode deployment (Streamable HTTP transport,
environment-based config, collection-prefix switching, health checks, and IP +
username audit logging).
