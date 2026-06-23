# 11 · Behind a proxy (FAU / RRZE network)

> Do this **only if the VM has no direct internet** — e.g. on the FAU/RRZE
> network, where `curl https://get.docker.com` times out. Symptoms: downloads
> hang for minutes, then `Failed to connect ... Timeout was reached`.
>
> If your VM reaches the internet directly, skip this page.

A proxy has to be set in **five** independent places — each program looks in a
different spot. Miss one and "it works for `curl` but Docker can't pull images",
or "images pull but the server can't reach Google". This page sets all five.

**FAU/RRZE proxy:** `http://proxy.rrze.uni-erlangen.de:80`
(source: RRZE. If your VLAN uses a different proxy, substitute it everywhere
below — it's used as `$PROXY`.)

All commands run **on the VM** as `root` (no `sudo` needed if you're root).

---

## Step 0 — Confirm the proxy works from this VM

Before configuring anything, prove the proxy reaches both Docker and Google
(fast-fail, 15 s max):

```bash
curl -x http://proxy.rrze.uni-erlangen.de:80 -sS -m 15 -o /dev/null -w "docker  -> HTTP %{http_code}\n" https://get.docker.com
curl -x http://proxy.rrze.uni-erlangen.de:80 -sS -m 15 -o /dev/null -w "google  -> HTTP %{http_code}\n" https://generativelanguage.googleapis.com
```

✓ Two real HTTP codes (e.g. `200`, `301`, `404`) = the proxy works **and** Google
is reachable through it (so the default Google-embedding design will work).
✗ `000` / `Failed to connect` = wrong proxy host/port — get the right one from
RRZE-IT or a working machine's proxy settings, then substitute it below.

---

## Step 1 — Shell proxy (for `curl`, `git`, this session + future logins)

```bash
# This session, right now:
export http_proxy="http://proxy.rrze.uni-erlangen.de:80"
export https_proxy="http://proxy.rrze.uni-erlangen.de:80"
export HTTP_PROXY="$http_proxy" HTTPS_PROXY="$https_proxy"
export no_proxy="localhost,127.0.0.1,::1,qdrant,.rrze.uni-erlangen.de,.uni-erlangen.de,.fau.de,10.0.0.0/8"
export NO_PROXY="$no_proxy"

# Persist for every future login:
cat >> /etc/environment <<'EOF'
http_proxy="http://proxy.rrze.uni-erlangen.de:80"
https_proxy="http://proxy.rrze.uni-erlangen.de:80"
HTTP_PROXY="http://proxy.rrze.uni-erlangen.de:80"
HTTPS_PROXY="http://proxy.rrze.uni-erlangen.de:80"
no_proxy="localhost,127.0.0.1,::1,qdrant,.rrze.uni-erlangen.de,.uni-erlangen.de,.fau.de,10.0.0.0/8"
NO_PROXY="localhost,127.0.0.1,::1,qdrant,.rrze.uni-erlangen.de,.uni-erlangen.de,.fau.de,10.0.0.0/8"
EOF
```

> ⚠️ **`no_proxy` must include `localhost` and `127.0.0.1`.** Otherwise
> `curl http://localhost:8080/health` and `curl http://localhost:6333` would be
> sent *to the proxy* and fail. It also includes `qdrant` for the same reason
> inside containers.

---

## Step 2 — `apt` proxy (to install packages)

```bash
cat > /etc/apt/apt.conf.d/95proxy <<'EOF'
Acquire::http::Proxy "http://proxy.rrze.uni-erlangen.de:80";
Acquire::https::Proxy "http://proxy.rrze.uni-erlangen.de:80";
EOF

apt-get update     # should now succeed
```

---

## Step 3 — Install Docker (now that the proxy is set)

The installer uses `curl` + `apt`, both of which now go through the proxy:

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
docker compose version
```

---

## Step 4 — Docker **daemon** proxy (so `docker pull` can fetch images)

The Docker daemon does **not** read your shell variables — it needs its own
drop-in, then a restart:

```bash
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/http-proxy.conf <<'EOF'
[Service]
Environment="HTTP_PROXY=http://proxy.rrze.uni-erlangen.de:80"
Environment="HTTPS_PROXY=http://proxy.rrze.uni-erlangen.de:80"
Environment="NO_PROXY=localhost,127.0.0.1,::1,qdrant"
EOF

systemctl daemon-reload
systemctl restart docker

# Verify the daemon can now pull through the proxy:
docker run --rm hello-world
```

---

## Step 5 — Docker **build/run** proxy (image build + container runtime)

Two more spots, both important for *this* project:

**5a. Build time** — the MCP image runs `pip install`, which needs the proxy.
Configure the Docker CLI once (root's Docker config):

```bash
mkdir -p /root/.docker
cat > /root/.docker/config.json <<'EOF'
{
  "proxies": {
    "default": {
      "httpProxy":  "http://proxy.rrze.uni-erlangen.de:80",
      "httpsProxy": "http://proxy.rrze.uni-erlangen.de:80",
      "noProxy":    "localhost,127.0.0.1,::1,qdrant"
    }
  }
}
EOF
```

**5b. Runtime** — the MCP **server** must reach Google's embedding API at runtime,
*through the proxy*, while still reaching Qdrant **directly**. Add these to your
`.env` (they're injected into the container automatically; `.env` is per-VM and
git-ignored, so the proxy never ends up in the repo):

```bash
cd ~/MSEI_mcp_server
cat >> .env <<'EOF'

# Proxy (FAU/RRZE) — lets the MCP server reach Google; NO_PROXY keeps Qdrant direct
HTTP_PROXY=http://proxy.rrze.uni-erlangen.de:80
HTTPS_PROXY=http://proxy.rrze.uni-erlangen.de:80
NO_PROXY=localhost,127.0.0.1,::1,qdrant
EOF
```

> Why `NO_PROXY=qdrant`? Inside Docker the server talks to the database at
> `http://qdrant:6333`. Without excluding it, those calls would be sent to the
> internet proxy and fail. With it, only the Google calls use the proxy.

---

## Verify the whole chain

```bash
# host shell:
curl -sS -m 15 -o /dev/null -w "shell  -> HTTP %{http_code}\n" https://github.com
# apt:
apt-get update >/dev/null 2>&1 && echo "apt    -> OK"
# docker pull (daemon proxy):
docker run --rm hello-world >/dev/null 2>&1 && echo "docker -> OK"
```

All three good → you're fully proxied. Continue the normal setup at
[03 · Start Qdrant](03-start-qdrant.md) (or [INSTALL.md](../INSTALL.md) Phase 5).

When the MCP server is later running, a successful search in OpenCode confirms the
**runtime** proxy (Step 5b) — that's the server reaching Google through the proxy.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `curl localhost:8080/health` hangs | `localhost` not in `no_proxy` | Re-check Step 1; re-`source /etc/environment` or re-login. |
| Shell works, `docker pull` fails | Missing daemon drop-in | Step 4, then `systemctl restart docker`. |
| Pull works, image **build** fails on `pip install` | Missing CLI config | Step 5a. |
| Server runs, `/health` OK, but every search errors with a connection timeout | Container can't reach Google | Step 5b in `.env`, then `docker compose up -d mcp`. |
| Server can't reach **Qdrant** after adding proxy | `qdrant` not in `NO_PROXY` | Add `qdrant` to `NO_PROXY` in `.env` (and Step 5a), restart. |
| Everything still blocked | Wrong proxy for this VLAN | Re-run Step 0 with the proxy RRZE-IT gave you. |

---

## Removing the proxy (if the VM later gets direct internet)

Undo: delete `/etc/apt/apt.conf.d/95proxy`,
`/etc/systemd/system/docker.service.d/http-proxy.conf` (then
`systemctl daemon-reload && systemctl restart docker`),
`/root/.docker/config.json`, the proxy lines in `/etc/environment` and `.env`,
then log out/in.

---

⬅️ Back: [00 · Overview](00-overview.md)  ·  ➡️ Continue: [03 · Start Qdrant](03-start-qdrant.md)
