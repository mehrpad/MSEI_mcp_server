# 03 · Start Qdrant (the database)

> Goal: get this project onto the VM and bring up the empty Qdrant database.
> Next step (04) fills it with papers.

All commands run **on the VM**.

---

## 1. Put this project on the VM

You need the `MSEI_mcp_server` folder (this repository) on the VM. Pick **one**
of these.

### Option A — clone from git (if it's in a git server)

```bash
cd ~
git clone https://github.com/mehrpad/MSEI_mcp_server.git
cd MSEI_mcp_server
```

(No `git`? `sudo apt update && sudo apt install -y git`, then retry.)

> **The repo is private.** The clone will ask for a GitHub username + a
> **Personal Access Token** (not your password) — create one at
> <https://github.com/settings/tokens>. If that's a hassle, just use **Option B**
> below (`scp`), which needs no GitHub access at all.

### Option B — copy it from your computer

If the project is only on your own machine, copy the whole folder up with `scp`
(run this **on your computer**, not the VM):

```bash
scp -r "E:\MSEI_mcp_server" msei@10.12.0.5:~/MSEI_mcp_server
```

Then on the VM:

```bash
cd ~/MSEI_mcp_server
```

Confirm you're in the right place — `ls` should show `docker-compose.yml`:

```bash
ls
```

---

## 2. Create your configuration file

The project ships a template called `.env.example`. Make your own copy called
`.env`:

```bash
cp .env.example .env
```

We'll fill in the Google API key in [step 05](05-google-api-key.md). For now the
defaults are fine — Qdrant itself needs no key.

---

## 3. Start Qdrant

Bring up **only** the database for now (we start the MCP server later, after the
data and key are in place):

```bash
docker compose up -d qdrant
```

- `up` = start it. `-d` = in the background. The first run downloads the Qdrant
  image (~100 MB), so give it a minute.

Check it's running:

```bash
docker compose ps
```

You should see `msei-qdrant` with state **running (healthy)** or **Up**.

---

## 4. Verify Qdrant answers

```bash
curl http://localhost:6333/healthz
```

Expected output:

```
healthz check passed
```

And list collections (there are none yet — that's correct):

```bash
curl http://localhost:6333/collections
```

You'll get `{"result":{"collections":[]}, ...}`. Empty list = Qdrant is up and
waiting for data. 🎉

> **Why only `localhost`?** Qdrant is published on `127.0.0.1:6333`, reachable
> **only from the VM itself** — never from the network. That's deliberate: users
> talk to the MCP server, never directly to the database. You (the admin) use
> `localhost:6333` for loading and checking data.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `docker compose: command not found` | Finish [step 02](02-install-docker.md). It's `docker compose` (two words). |
| `curl: command not found` | `sudo apt install -y curl jq`. |
| `Connection refused` on port 6333 | Give it a few seconds; check `docker compose logs qdrant`. |
| `no configuration file provided` | You're in the wrong folder. `cd ~/MSEI_mcp_server`. |

---

✅ Qdrant is running but empty. Let's load the papers.

⬅️ Back: [02 · Install Docker](02-install-docker.md)  ·  ➡️ Next: [04 · Load the paper data](04-load-vector-data.md)
