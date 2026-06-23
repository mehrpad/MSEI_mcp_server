# 00 · Overview — what we are building and why

> **Who this guide is for:** someone who has **never used Linux** before and has
> been asked to set up the publication search system for the group. Every step is
> spelled out. You can copy-paste every command. If you can read and type
> carefully, you can do this.

---

## What is this thing?

Our research group has a large library of scientific publications that has been turned
into a **searchable database** (a "vector database"). This lets an AI assistant
find the *right* passages, figures, and tables from thousands of publications in
seconds, and answer questions with real citations.

You are setting up the **server** that makes this library available to everyone
in the group. Each person uses a tool called **OpenCode** on their own computer,
with their **own local AI model**, and connects to your server to search the
publications.

```
  ~100 people, each on their own PC                  ONE shared Linux server (the "VM")
  ┌─────────────────────────────┐                    ┌──────────────────────────────────┐
  │  OpenCode + their local LLM  │  ── network ──▶    │  MCP server  ──▶  Qdrant (publications) │
  └─────────────────────────────┘                    │       │                           │
                                                      │       └──▶ Google embedding API   │
                                                      └──────────────────────────────────┘
```

Three pieces live on the server:

| Piece | Plain-English job | Runs as |
|-------|-------------------|---------|
| **Qdrant** | Stores the publications as searchable vectors. The "library". | Docker container |
| **MCP server** | The translator. Receives a question from OpenCode, turns it into a search, asks Qdrant, returns results. | Docker container |
| **Google embedding API** | Turns text into numbers ("vectors") so it can be searched by meaning. Lives at Google; the server calls it over the internet. | External service (needs an API key) |

**You do NOT install any AI model on the server.** The heavy AI work happens on
each user's own computer (their local LLM) and at Google (embeddings). Your
server just stores publications and searches them — so it can be a modest machine.

---

## The two repositories — don't mix them up

| Repo | What it does | Where it runs |
|------|--------------|---------------|
| **The ingest repo** (separate) | Turns PDF publications into Qdrant data and produces **snapshot** files. | On a powerful machine / your laptop, run by an admin. |
| **This repo** (`MSEI_mcp_server`) | Runs the server that the group searches every day. | On the **VM**. |

In this guide you will **copy the snapshots produced by the ingest repo onto the
VM** and load them into Qdrant. You will not run any PDF processing on the VM.

---

## What you need before you start (checklist)

- [ ] A **Linux VM** on the group's private network, and login details for it
      (an IP address like `10.12.0.5`, a username, and a password or SSH key).
- [ ] Permission to run `sudo` on that VM (to install software). Ask whoever
      gave you the VM.
- [ ] The VM is allowed to reach the **internet** for Google's API
      (specifically `generativelanguage.googleapis.com`). ⚠️ This is the most
      common blocker — confirm it with whoever runs the firewall.
      If the VM is fully air-gapped, see the note in [05](05-google-api-key.md).
- [ ] A **Google account** to create a free/paid Gemini API key.
- [ ] The **snapshot bundle** from the ingest repo (a file like
      `colleague_xxx_qdrant_20260601.tar.gz`).
- [ ] The **subnet** your group's computers are on (e.g. `10.12.0.0/16`), so we
      can let only them reach the server.

Don't have all of these yet? You can still do steps 01–03; you'll need the rest
by step 04.

---

## The order to follow (the "happy path")

Do these in order. Each page ends by linking to the next.

1. [01 · Linux basics](01-linux-basics.md) — connect to the VM and find your way around.
2. [02 · Install Docker](02-install-docker.md) — the engine that runs everything.
3. [03 · Start Qdrant](03-start-qdrant.md) — bring up the empty database.
4. [04 · Load the publication data](04-load-vector-data.md) — copy snapshots to the VM and restore them.
5. [05 · Google API key](05-google-api-key.md) — create and activate the embedding key.
6. [06 · Run the MCP server](06-run-mcp-server.md) — start the server and lock down the firewall.
7. [07 · Connect OpenCode](07-connect-opencode.md) — set up a user's computer and run a test search.
8. [08 · Update / add a database](08-update-add-database.md) — day-2 changes to the corpus.
9. [09 · Operations & troubleshooting](09-operations-troubleshooting.md) — backups, restarts, fixing problems.

> **In a hurry and already know Linux + Docker?** Jump to
> [06 · Run the MCP server](06-run-mcp-server.md) after putting your snapshot in
> place — the whole stack is one `docker compose up -d`.

---

## Mini-glossary (keep this handy)

- **VM (Virtual Machine):** the Linux server you connect to. Think "a computer in
  the building you reach over the network".
- **SSH:** the secure way to type commands on the VM from your own computer.
- **Docker:** software that runs apps in isolated boxes called **containers**, so
  you don't have to install messy dependencies by hand.
- **Container:** one running app in its Docker box (we have two: `qdrant`, `mcp`).
- **Qdrant:** the vector database that stores the publications.
- **Vector / embedding:** a list of numbers representing the *meaning* of text, so
  search can match by meaning, not just keywords.
- **MCP (Model Context Protocol):** the standard way AI tools like OpenCode talk to
  external tools. Our server speaks MCP.
- **OpenCode:** the program each user runs on their own computer.
- **Snapshot:** a single file containing a full backup of one Qdrant collection.
- **Collection:** one "table" inside Qdrant. Our corpus is 4 collections (text,
  figures, tables, summaries).

➡️ Next: [01 · Linux basics](01-linux-basics.md)
