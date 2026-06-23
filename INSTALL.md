# INSTALL — one-shot setup walkthrough (zero → working search)

This is the **single, line-by-line** runbook: from a blank Linux VM to a working
first search in OpenCode. Follow it top to bottom. Every command is copy-paste.

> Prefer per-topic detail or troubleshooting? The numbered guide in
> [`docs/`](docs/00-overview.md) covers each step in depth. This file is the
> condensed linear version.

---

## Fill these in once (you'll paste them below)

Write down your real values and substitute them wherever you see the placeholder:

| Placeholder | Means | Example |
|-------------|-------|---------|
| `VM_USER` | your login name on the VM | `msei` |
| `VM_IP` | the VM's address on your network | `10.12.0.5` |
| `SUBNET` | your group's network range (ask IT) | `10.12.0.0/16` |
| `BUNDLE` | the snapshot file from the **ingest repo** | `colleague_materials_v2_qdrant_20260601.tar.gz` |
| `GEMINI_KEY` | your Google Gemini API key | `AIzaSy...` |

**Before you start, make sure you have:** SSH access to the VM with `sudo`
rights · the `BUNDLE` file on your own computer · a Google account · confirmation
that the VM can reach the internet (we test this in Phase 6).

---

## Phase 1 — Connect to the VM

Run on **your own computer** (PowerShell on Windows, Terminal on macOS/Linux):

```bash
ssh VM_USER@VM_IP
```

Type `yes` if asked about the fingerprint, then your password. You're in when the
prompt looks like `VM_USER@...:~$`.

✓ **Checkpoint:** you see a `$` prompt.

---

## Phase 2 — Update the system and install basics

Everything from here runs **on the VM**.

```bash
sudo apt update
sudo apt -y upgrade
sudo apt install -y curl git jq ca-certificates
```

✓ **Checkpoint:**

```bash
curl --version | head -1
git --version
jq --version
```

Each prints a version line.

---

## Phase 3 — Install Docker + Compose

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

Let your user run Docker without `sudo`, then apply it by reconnecting:

```bash
sudo usermod -aG docker $USER
exit
```

Reconnect:

```bash
ssh VM_USER@VM_IP
```

Make Docker start on boot, and verify:

```bash
sudo systemctl enable docker
docker run --rm hello-world
docker compose version
```

✓ **Checkpoint:** you see `Hello from Docker!` and a `Docker Compose version v2.x`.

---

## Phase 4 — (Optional) Install Python 3

> **You can skip this.** The MCP server runs inside Docker with its own Python.
> Install Python on the VM **only** if you also want to run Python admin/ingest
> tooling directly here.

```bash
sudo apt install -y python3 python3-pip python3-venv
python3 --version
```

✓ **Checkpoint:** prints `Python 3.10`+ (any 3.10+ is fine).

---

## Phase 5 — Get the project onto the VM

**Option A — clone from GitHub** (the repo is **private**, so this asks for a
GitHub username + a Personal Access Token, not a password — create one at
<https://github.com/settings/tokens>):

```bash
cd ~
git clone https://github.com/mehrpad/MSEI_mcp_server.git
cd MSEI_mcp_server
```

**Option B — copy from your computer** (no GitHub needed). Run this **on your
computer**, then come back to the VM:

```bash
scp -r "E:\MSEI_mcp_server" VM_USER@VM_IP:~/MSEI_mcp_server
```

Then on the VM: `cd ~/MSEI_mcp_server`.

✓ **Checkpoint:**

```bash
ls
```

shows `docker-compose.yml`, `mcp_server`, `docs`, `scripts`.

---

## Phase 6 — Set up the Google API key

### 6a. Create the key
In a browser: go to <https://aistudio.google.com/apikey> → sign in → **Create API
key** → copy it (`GEMINI_KEY`).

### 6b. Check the VM can reach Google

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://generativelanguage.googleapis.com
```

✓ A number like `404` or `200` = **good** (you reached Google).
✗ Hangs or `Could not resolve host` = the VM has no internet to Google — fix the
firewall (see [docs/05](docs/05-google-api-key.md)) before continuing.

### 6c. Create your `.env` and paste the key

```bash
cp .env.example .env
nano .env
```

In nano, change this one line (replace with your real key):

```
GEMINI_API_KEY=GEMINI_KEY
```

Save and exit: `Ctrl+O`, `Enter`, `Ctrl+X`.

✓ **Checkpoint:**

```bash
grep GEMINI_API_KEY .env
```

shows your key (not the placeholder).

---

## Phase 7 — Start Qdrant (the empty database)

```bash
docker compose up -d qdrant
```

(First run downloads the Qdrant image — give it a minute.)

✓ **Checkpoint:**

```bash
curl http://localhost:6333/healthz
curl http://localhost:6333/collections
```

First prints `healthz check passed`; second shows an **empty** collection list —
correct, we load data next.

---

## Phase 8 — Transfer the vector database to the VM

The `BUNDLE` is on **your own computer**. Copy it up — run this **on your
computer**:

```bash
scp "C:\path\to\BUNDLE" VM_USER@VM_IP:~/
```

(macOS/Linux: `scp ~/Downloads/BUNDLE VM_USER@VM_IP:~/`)

Back on the **VM**, unpack it:

```bash
cd ~
mkdir -p snapshots
tar -xzf BUNDLE -C snapshots
ls -lh snapshots
```

✓ **Checkpoint:** you see four `.snapshot` files (text, figures, tables,
summaries) and a `RESTORE.md`.

---

## Phase 9 — Add the data to Qdrant (restore)

```bash
cd ~/MSEI_mcp_server
bash scripts/restore-snapshot.sh ~/snapshots
```

Each collection uploads and ends with `"result": true`. Large corpora take a
while — let it finish.

✓ **Checkpoint — confirm the data is in:**

```bash
for c in materials_v2 materials_v2_figures materials_v2_tables materials_v2_summaries; do
  printf "%-28s " "$c"; curl -s "http://localhost:6333/collections/$c" | jq '.result.points_count'
done
```

You should see **non-zero** numbers for each.

> If your snapshot collections are named with a different prefix (e.g.
> `materials_v2_external_*`), open `.env` and set `COLLECTION_PREFIX` to that
> prefix. Default is `materials_v2`.

---

## Phase 10 — Start the MCP server

```bash
docker compose up -d
```

(First run **builds** the server image — a few minutes.)

✓ **Checkpoint:**

```bash
docker compose ps
curl http://localhost:8080/health
```

Both containers are **running**; health returns
`{"status": "ok", "server": "paperRAG-v2", "prefix": "materials_v2"}`.

Find the address users will connect to:

```bash
hostname -I
```

The first address (e.g. `VM_IP`) → users connect to **`http://VM_IP:8080/mcp`**.

---

## Phase 11 — Lock the server to your subnet

Replace `SUBNET` with your real range. (Docker bypasses `ufw` for published
ports, so we use the `DOCKER-USER` rule — order matters, run as shown.)

```bash
sudo iptables -I DOCKER-USER -p tcp --dport 8080 -s SUBNET -j RETURN
sudo iptables -I DOCKER-USER -p tcp --dport 8080 -j DROP
sudo apt install -y iptables-persistent
sudo netfilter-persistent save
```

Protect SSH too:

```bash
sudo ufw allow OpenSSH
sudo ufw --force enable
```

✓ **Checkpoint:** from a computer **on your subnet**,
`curl http://VM_IP:8080/health` works; from outside, it does not.

---

## Phase 12 — First test with OpenCode

This part runs on a **user's own computer** (yours, for the test) — not the VM.

### 12a. Watch the server (optional, on the VM)
In your VM session, start streaming logs so you can see the test arrive:

```bash
docker compose logs -f mcp
```

(Leave this running; `Ctrl+C` later to stop watching.)

### 12b. Configure OpenCode (on your computer)
Make sure OpenCode is installed (<https://opencode.ai>) and your local model
works. Then open (create if missing):

- macOS/Linux: `~/.config/opencode/opencode.json`
- Windows: `%USERPROFILE%\.config\opencode\opencode.json`

Paste this, replacing `VM_IP` and the `X-User` name (there's a ready copy in
[`client-config/opencode.example.json`](client-config/opencode.example.json)):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "msei-papers": {
      "type": "remote",
      "url": "http://VM_IP:8080/mcp",
      "enabled": true,
      "headers": { "X-User": "your.name" }
    }
  }
}
```

Save the file.

### 12c. Run the first searches
Restart OpenCode, then ask:

> **"Call corpus_stats on msei-papers."**

You should get paper / chunk / figure / table counts. Then a real search:

> **"Search the papers for the effect of rhenium on creep resistance in
> nickel-base superalloys. Give the top passages with their DOIs."**

OpenCode returns quoted passages with citations from your library.

✓ **On the VM log window** you'll see the request arrive, e.g.:

```json
{"ts":"...","ip":"VM_IP_of_your_PC","user":"your.name","method":"POST","path":"/mcp","auth":"open"}
```

🎉 **That's the whole system working end to end.**

---

## You're done — quick reference

| Task | Command (on the VM, in `~/MSEI_mcp_server`) |
|------|---------------------------------------------|
| Status | `docker compose ps` |
| Health | `curl http://localhost:8080/health` |
| Logs | `docker compose logs -f mcp` |
| Stack overview | `bash scripts/healthcheck.sh` |
| Restart server | `docker compose up -d mcp` |
| Stop (keep data) | `docker compose down` |
| Start again | `docker compose up -d` |

**Next steps & deeper topics:**
- Update / add / swap a corpus → [docs/08](docs/08-update-add-database.md)
- Backups, troubleshooting, reboots → [docs/09](docs/09-operations-troubleshooting.md)
- Turn on per-user API tokens → [docs/10](docs/10-api-token-auth.md)
- Hand users the short version → [client-config/README.md](client-config/README.md)

---

### Fast path (for the next VM, if you already know the above)

```bash
ssh VM_USER@VM_IP
sudo apt update && sudo apt install -y curl git jq
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && exit
# reconnect:
ssh VM_USER@VM_IP
git clone https://github.com/mehrpad/MSEI_mcp_server.git && cd MSEI_mcp_server
cp .env.example .env && nano .env            # set GEMINI_API_KEY
docker compose up -d qdrant
# copy BUNDLE up (from your PC): scp BUNDLE VM_USER@VM_IP:~/
mkdir -p ~/snapshots && tar -xzf ~/BUNDLE -C ~/snapshots
bash scripts/restore-snapshot.sh ~/snapshots
docker compose up -d
curl http://localhost:8080/health
```
