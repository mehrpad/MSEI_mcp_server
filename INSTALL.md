# INSTALL — one-shot setup walkthrough (zero → working search)

The **single, line-by-line** runbook: from a blank Linux VM to a working first
search in OpenCode. Follow it top to bottom. Every command is copy-paste.

This version is **proxy-aware** — it assumes the VM may have **no direct
internet** (the case on the FAU/RRZE network) and sets the proxy up *before*
anything tries to reach the internet, which is the order that actually works.

> Per-topic detail / troubleshooting lives in the numbered guide
> [`docs/`](docs/00-overview.md). Proxy deep-dive: [`docs/11`](docs/11-proxy-setup.md).

---

## Fill these in once

Substitute these wherever they appear below:

| Placeholder | Means | This deployment (FAU example) |
|-------------|-------|-------------------------------|
| `VM_USER` | your login on the VM | `root` |
| `VM_IP` | the VM's address | `10.76.33.35` |
| `SUBNET` | your group's network range | `10.76.33.32/28` |
| `PROXY` | the HTTP proxy (if behind one) | `http://proxy.rrze.uni-erlangen.de:80` |
| `BUNDLE` | snapshot file from the **ingest repo** | `colleague_materials_v2_qdrant_*.tar.gz` |
| `GEMINI_KEY` | your Google **AI Studio** key | `AIzaSy...` |

**Before you start:** SSH access with `sudo`/root · the `BUNDLE` on your own PC ·
a Google account · your group's `SUBNET`.

> **`sudo` note:** if your prompt ends with `#` you're **root** — omit every
> `sudo` below and skip the `usermod` step.

---

## Phase 1 — Connect to the VM

On **your own computer** (PowerShell on Windows, Terminal on macOS/Linux):

```bash
ssh VM_USER@VM_IP
```

Everything after this runs **on the VM**.

✓ You see a prompt like `VM_USER@host:~#`.

**Quick network sanity check** (these need no internet, just confirm the VM is on
the network — usually already configured by IT):

```bash
ip route | grep default          # → a default gateway exists
getent hosts github.com          # → resolves to an IP (DNS works)
```

If DNS doesn't resolve or there's no gateway, fix the netplan first
([docs/01 & 11 cover this]) — but on a normal FAU VM both already work.

---

## Phase 2 — Proxy: do this FIRST if the VM has no direct internet

**Test direct internet (fast-fail, 8 s):**

```bash
curl -sS -m 8 -o /dev/null -w "%{http_code}\n" https://get.docker.com || echo BLOCKED
```

- Prints `200`/`301` → you have direct internet. **Skip to Phase 3.**
- Hangs / `BLOCKED` / timeout → you're behind a proxy (normal on FAU/RRZE).
  **Do the rest of this phase now.**

**Confirm the proxy reaches both Docker and Google:**

```bash
curl -x http://proxy.rrze.uni-erlangen.de:80 -sS -m 15 -o /dev/null -w "docker -> %{http_code}\n" https://get.docker.com
curl -x http://proxy.rrze.uni-erlangen.de:80 -sS -m 15 -o /dev/null -w "google -> %{http_code}\n" https://generativelanguage.googleapis.com
```

✓ `docker -> 200` and `google -> 404` are **both good** (404 just means you
reached Google and there's no page at the bare URL — the connection works).
✗ `000`/`Failed to connect` → wrong proxy for your VLAN; get the right one from IT.

**Set the proxy for your shell + apt** (this is what unblocks Docker install):

```bash
P=http://proxy.rrze.uni-erlangen.de:80
NP="localhost,127.0.0.1,::1,qdrant,.rrze.uni-erlangen.de,.uni-erlangen.de,.fau.de,10.0.0.0/8"

# this shell, now:
export http_proxy=$P https_proxy=$P HTTP_PROXY=$P HTTPS_PROXY=$P no_proxy=$NP NO_PROXY=$NP

# persist for future logins:
cat >> /etc/environment <<EOF
http_proxy="$P"
https_proxy="$P"
HTTP_PROXY="$P"
HTTPS_PROXY="$P"
no_proxy="$NP"
NO_PROXY="$NP"
EOF

# apt:
printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' "$P" "$P" > /etc/apt/apt.conf.d/95proxy
```

> ⚠️ `no_proxy`/`NO_PROXY` **must** include `localhost`, `127.0.0.1`, and
> `qdrant` — otherwise `curl localhost:8080/health` and the server→database
> calls would be sent to the internet proxy and fail.

---

## Phase 3 — Install system basics + Docker

```bash
apt-get update
apt-get install -y curl git jq ca-certificates
```

Install Docker (works now because the proxy is set):

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
```

*(Non-root only:* `sudo usermod -aG docker $USER`, then `exit` and SSH back in.*)*

✓ Verify:

```bash
docker --version
docker compose version
```

---

## Phase 4 — Point Docker at the proxy (skip if you have direct internet)

The Docker **daemon** and **builds** don't read your shell variables — they each
need their own proxy config, or `docker pull`/`docker build` will hang.

```bash
P=http://proxy.rrze.uni-erlangen.de:80

# daemon proxy (for pulling images):
mkdir -p /etc/systemd/system/docker.service.d
printf '[Service]\nEnvironment="HTTP_PROXY=%s"\nEnvironment="HTTPS_PROXY=%s"\nEnvironment="NO_PROXY=localhost,127.0.0.1,::1,qdrant"\n' "$P" "$P" \
  > /etc/systemd/system/docker.service.d/http-proxy.conf

# build proxy (for the image's pip install):
mkdir -p /root/.docker
printf '{"proxies":{"default":{"httpProxy":"%s","httpsProxy":"%s","noProxy":"localhost,127.0.0.1,::1,qdrant"}}}\n' "$P" "$P" \
  > /root/.docker/config.json

systemctl daemon-reload && systemctl restart docker
```

✓ Verify the daemon can pull through the proxy:

```bash
docker run --rm hello-world          # → "Hello from Docker!"
```

---

## Phase 5 — Get the project onto the VM

The repo is **private**, so a plain `git clone` on the VM will prompt for a
GitHub login. Pick the easiest path:

**Option A — `git clone`** (needs a GitHub **Personal Access Token** as the
password, from <https://github.com/settings/tokens> — *or* ask the repo owner to
make it public, then no login is needed):

```bash
cd ~
git clone https://github.com/mehrpad/MSEI_mcp_server.git
cd MSEI_mcp_server
```

**Option B — copy from your own PC** (no GitHub needed; the repo is already at
`E:\MSEI_mcp_server`). Run **on your PC**, then `cd ~/MSEI_mcp_server` on the VM:

```powershell
scp -r "E:\MSEI_mcp_server" VM_USER@VM_IP:~/MSEI_mcp_server
```

✓ `ls` shows `docker-compose.yml`, `mcp_server`, `docs`, `scripts`.

---

## Phase 6 — Configure `.env` (Google key + runtime proxy)

```bash
cd ~/MSEI_mcp_server
cp .env.example .env
nano .env
```

Set this one line (your AI Studio key — the **same** key does embeddings, no
separate key needed):

```
GEMINI_API_KEY=GEMINI_KEY
```

Save (`Ctrl+O`, `Enter`, `Ctrl+X`). Then, **if behind a proxy**, append the
runtime proxy so the running server can reach Google while keeping Qdrant direct:

```bash
cat >> .env <<'EOF'

HTTP_PROXY=http://proxy.rrze.uni-erlangen.de:80
HTTPS_PROXY=http://proxy.rrze.uni-erlangen.de:80
NO_PROXY=localhost,127.0.0.1,::1,qdrant
EOF
```

✓ Check the active lines (no leftover placeholder):

```bash
grep -v '^\s*#' .env | grep .
```

You should see your real `GEMINI_API_KEY`, `COLLECTION_PREFIX=materials_v2`,
`EMBED_MODEL=gemini-embedding-2-preview`, `HOST_PORT=8080`, and the 3 proxy lines.

---

## Phase 7 — Start Qdrant (the empty database)

```bash
docker compose up -d qdrant
sleep 5                                   # give Qdrant a moment to be ready
curl http://localhost:6333/healthz       # → "healthz check passed"
curl http://localhost:6333/collections   # → empty list (correct, no data yet)
```

> This repo **pins Qdrant to v1.16.0** on purpose: v1.17+ removed RocksDB and
> can't restore older RocksDB-format snapshots (`unknown variant rocks_db`).
> If the *first* health call says `Connection reset by peer`, Qdrant just wasn't
> up yet — wait a few seconds and retry.

---

## Phase 8 — Transfer the vector database to the VM

Snapshots come either as **loose `.snapshot` files** (Qdrant's native names look
like `materials_v2-<id>-<date>.snapshot`) or as one `.tar.gz` **bundle**. Make a
folder on the VM, then copy them in with `scp`.

On the **VM**:

```bash
mkdir -p ~/snapshots
```

On your **PC** — loose files (use YOUR real filenames):

```powershell
scp "C:\path\to\materials_v2-2819031290988516-2026-05-27-12-40-06.snapshot"           VM_USER@VM_IP:~/snapshots/
scp "C:\path\to\materials_v2_summaries-2819031290988516-2026-05-27-12-41-41.snapshot" VM_USER@VM_IP:~/snapshots/
```

…or a bundle: `scp "C:\path\to\BUNDLE.tar.gz" VM_USER@VM_IP:~/` then on the VM
`tar -xzf ~/BUNDLE.tar.gz -C ~/snapshots`.

On the **VM**, confirm they arrived:

```bash
ls -lh ~/snapshots
```

---

## Phase 9 — Restore the data + start the MCP server

> ⚠️ **Big snapshots take minutes and look frozen while loading.** Restore **one
> file at a time**, wait for each to print `{"result":true,...}`, and **do NOT
> press Ctrl-C** — interrupting leaves the collection empty and you start over.

Easiest — the script reads the collection name from each filename automatically:

```bash
cd ~/MSEI_mcp_server
bash scripts/restore-snapshot.sh ~/snapshots
```

Or restore each file yourself, one at a time. The `-w` line prints how long it
took, so you know it actually finished (vs. froze):

```bash
# big file first — several minutes is normal; leave it alone until it returns:
curl -sS -X POST "http://localhost:6333/collections/materials_v2/snapshots/upload?priority=snapshot" \
  -F "snapshot=@$HOME/snapshots/materials_v2-2819031290988516-2026-05-27-12-40-06.snapshot" \
  -w "\n-> HTTP %{http_code} in %{time_total}s\n"

# then the smaller one:
curl -sS -X POST "http://localhost:6333/collections/materials_v2_summaries/snapshots/upload?priority=snapshot" \
  -F "snapshot=@$HOME/snapshots/materials_v2_summaries-2819031290988516-2026-05-27-12-41-41.snapshot" \
  -w "\n-> HTTP %{http_code} in %{time_total}s\n"
```

> **See it working** (optional): in a *second* SSH session, run
> `docker compose -f ~/MSEI_mcp_server/docker-compose.yml logs -f qdrant`.

<details><summary>Huge file won't upload over HTTP? Recover from disk instead (no long upload)</summary>

```bash
docker exec msei-qdrant mkdir -p /qdrant/snapshots/materials_v2
docker cp ~/snapshots/materials_v2-2819031290988516-2026-05-27-12-40-06.snapshot msei-qdrant:/qdrant/snapshots/materials_v2/
curl -sS -X PUT "http://localhost:6333/collections/materials_v2/snapshots/recover" \
  -H 'Content-Type: application/json' \
  -d '{"location":"file:///qdrant/snapshots/materials_v2/materials_v2-2819031290988516-2026-05-27-12-40-06.snapshot","priority":"snapshot"}'
```
</details>

✓ Confirm what loaded — lists whatever collections exist, with point counts:

```bash
for c in $(curl -s http://localhost:6333/collections | jq -r '.result.collections[].name'); do
  printf "%-30s " "$c"; curl -s "http://localhost:6333/collections/$c" | jq '.result.points_count'
done
```

You should see `materials_v2` and `materials_v2_summaries` with non-zero counts.
**Only have those two (no figures/tables)? That's fine** — text search and
publication-summary search work fully; the figure/table tools simply return nothing.

Start the server (first run **builds** the image — a few minutes, through the
proxy):

```bash
docker compose up -d
docker compose ps
curl http://localhost:8080/health        # → {"status":"ok", ... "prefix":"materials_v2"}
```

---

## Phase 10 — Lock the server to your users' subnet

Restrict `:8080` to the subnet where your **OpenCode users' PCs** are — note that
is usually **NOT the VM's own subnet**. (Here the VM is `10.76.33.x` but users are
`10.131.233.x`, so we allow `10.131.233.0/24`.)

Docker bypasses `ufw` for published ports, so use the `DOCKER-USER` chain.
**Allow first, drop last** — that keeps the allowed subnet reachable the whole
time and puts `RETURN` above `DROP`:

```bash
# 1) allow your users' subnet (replace with YOUR real subnet; repeat per subnet):
iptables -I DOCKER-USER -p tcp --dport 8080 -s 10.131.233.0/24 -j RETURN

# 2) THEN drop everyone else:
iptables -A DOCKER-USER -p tcp --dport 8080 -j DROP

# 3) confirm RETURN is ABOVE DROP:
iptables -S DOCKER-USER

# 4) persist + protect SSH:
apt-get install -y iptables-persistent && netfilter-persistent save
ufw allow OpenSSH && ufw --force enable
```

> ⚠️ **Replace the subnet and add the `DROP` last.** A `DROP` with no `RETURN`
> above it blocks **everyone** (symptom: nobody can open `:8080/health`).
> **Locked out?** Run step 1 with your subnet — it restores access instantly.

✓ From a PC on an allowed subnet, `http://VM_IP:8080/health` works in a browser;
from outside, it doesn't.

---

## Phase 11 — First test with OpenCode

On a **user's PC** (not the VM). OpenCode's config needs two things: a **model
provider** (here the NHR@FAU gateway) and the **publication server** (`mcp`).

### 11a. Set your NHR@FAU API token (PowerShell, Windows)

The config reads the token from an environment variable so it never sits in the
file. Get your token from the NHR portal, then in **PowerShell**:

```powershell
# current session — THE QUOTES ARE REQUIRED:
$env:NHR_API_TOKEN="PASTE_YOUR_TOKEN_HERE"

# verify:
echo $env:NHR_API_TOKEN

# make it permanent (applies to NEW terminals / after restarting OpenCode):
setx NHR_API_TOKEN "PASTE_YOUR_TOKEN_HERE"
```

> ⚠️ **Use the quotes.** `$env:NHR_API_TOKEN="..."` works; `$env:NHR_API_TOKEN=...`
> (no quotes) fails. `setx` only affects **future** terminals, so **restart
> OpenCode** afterwards.
> 🔒 **Never put the token in the config file, a screenshot, or chat.** If it's
> ever exposed, **regenerate it in the NHR portal** and set the new one.
>
> macOS/Linux: `export NHR_API_TOKEN="..."` (add it to `~/.bashrc` / `~/.zshrc`).

### 11b. Configure OpenCode

Edit OpenCode's config — `%USERPROFILE%\.config\opencode\opencode.json` (Windows)
or `~/.config/opencode/opencode.json` (macOS/Linux). Replace the **whole file**
with this (also saved in
[`client-config/opencode.example.json`](client-config/opencode.example.json)):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "nhr-fau/Qwen/Qwen3.6-35B-A3B-FP8",
  "provider": {
    "nhr-fau": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "NHR@FAU",
      "options": {
        "baseURL": "https://hub.nhr.fau.de/api/llmgw/v1",
        "apiKey": "{env:NHR_API_TOKEN}"
      },
      "models": {
        "Qwen/Qwen3.6-35B-A3B-FP8": { "name": "Qwen3.6-35B (good for tools)" },
        "deepseek-ai/DeepSeek-V4-Flash": { "name": "DeepSeek-V4-Flash (good for tools)" },
        "RedHatAI/Mistral-Small-3.2-24B-Instruct-2506-FP8": { "name": "Mistral-Small-3.2-24B" },
        "google/gemma-4-E4B-it": { "name": "gemma-4-E4B-it" },
        "gpt-oss-120b": { "name": "gpt-oss-120b (tool-call id bug)" }
      }
    }
  },
  "mcp": {
    "msei-publications": {
      "type": "remote",
      "url": "http://10.76.33.35:8080/mcp",
      "enabled": true,
      "headers": { "X-User": "mehrpad" }
    }
  }
}
```

- `model` = the default model, written `provider-id/model-id`
  (`nhr-fau/Qwen/Qwen3.6-35B-A3B-FP8`). **Use the EXACT model IDs your NHR team can
  access** — they're namespaced (`google/gemma-4-E4B-it`, not `gemma-4-E4B-it`). If
  you pick one your team can't use, the gateway error lists the allowed IDs.
- **Pick a model that does tool-calling well** (Qwen3.6 / DeepSeek work; **avoid
  `gpt-oss-120b`** — it returns malformed tool-call ids → "Expected 'id' to be a
  string"). Don't pick embedding/OCR models (`*e5-large*`, `*vdr*`, `*OCR*`).
- `apiKey: "{env:NHR_API_TOKEN}"` pulls the token from the env var set in 11a.
- Change `"X-User": "mehrpad"` to each person's name (how they show up in the log).
- **One outer `{ }`** — `model`, `provider`, `mcp` are siblings (commas between, no
  stray braces). An `EndOfFileExpected` error means a brace closed the file early.

### 11c. Watch the server (optional, on the VM)

```bash
docker compose logs -f mcp        # leave running; Ctrl+C stops watching
```

### 11d. Test

Restart OpenCode, then ask:

> *"Call corpus_stats on msei-publications."* → counts (~351k chunks, ~31k summaries).
> *"Search the publications for rhenium's effect on creep in Ni-base superalloys; give DOIs."*

✓ On the VM log you'll see the request arrive with the user's IP + `X-User`. 🎉
**The whole system working end-to-end** — model via NHR@FAU, publication search via the
MCP server, embeddings via Google through the proxy.

---

## Quick reference

| Task | Command (on the VM, in `~/MSEI_mcp_server`) |
|------|---------------------------------------------|
| Status | `docker compose ps` |
| Health | `curl http://localhost:8080/health` |
| Logs | `docker compose logs -f mcp` |
| Stack overview | `bash scripts/healthcheck.sh` |
| Restart server | `docker compose up -d mcp` |
| Stop (keep data) | `docker compose down` |
| Start again | `docker compose up -d` |

**Next:** update/swap a corpus → [docs/08](docs/08-update-add-database.md) ·
backups & troubleshooting → [docs/09](docs/09-operations-troubleshooting.md) ·
per-user tokens → [docs/10](docs/10-api-token-auth.md) ·
proxy details → [docs/11](docs/11-proxy-setup.md).

---

## Where this differs from a "normal" (direct-internet) VM

If a future VM has direct internet, **skip Phases 2 and 4** entirely and drop the
proxy lines from `.env` in Phase 6 — everything else is identical.

The five places the proxy must be set (the thing that trips everyone up):
1. shell (`/etc/environment`) — Phase 2
2. apt (`/etc/apt/apt.conf.d/95proxy`) — Phase 2
3. Docker daemon (`docker.service.d/http-proxy.conf`) — Phase 4
4. Docker build (`/root/.docker/config.json`) — Phase 4
5. MCP container runtime (`.env`) — Phase 6
