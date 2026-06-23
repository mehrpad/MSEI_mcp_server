# 02 · Install Docker

> Goal: install Docker (the engine that runs Qdrant and the MCP server) and prove
> it works. ~10 minutes.

All commands here run **on the VM** (you should see the `msei@...:~$` prompt). If
you're not logged in, see [01](01-linux-basics.md#2-connect-with-ssh).

This guide assumes **Ubuntu** (the most common server Linux). If `cat /etc/os-release`
shows Debian, the same commands work. For other distributions, see
<https://docs.docker.com/engine/install/>.

---

## 1. Check whether Docker is already there

Someone may have installed it for you:

```bash
docker --version
```

- If you see something like `Docker version 27.x.x` → **skip to step 4** (verify).
- If you see `command not found` → continue with step 2.

---

## 2. Install Docker (the official one-line script)

Docker provides an official installer script. Run these two lines:

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

This downloads and installs Docker Engine **and** the Compose plugin. It prints a
lot of text and takes a couple of minutes. When it finishes you're back at the `$`
prompt.

> **No `curl`?** Install it first: `sudo apt update && sudo apt install -y curl`,
> then re-run the two lines above.

---

## 3. Let your user run Docker without `sudo` every time

By default Docker needs `sudo`. Add yourself to the `docker` group so you don't
have to:

```bash
sudo usermod -aG docker $USER
```

⚠️ **This only takes effect after you log out and back in.** So:

```bash
exit
```

Then SSH back in:

```bash
ssh msei@10.12.0.5
```

---

## 4. Verify Docker works

```bash
docker run --rm hello-world
```

This downloads a tiny test image and runs it. Success looks like:

```
Hello from Docker!
This message shows that your installation appears to be working correctly.
```

Also check Compose (note: it's `docker compose`, two words, not `docker-compose`):

```bash
docker compose version
```

You should see `Docker Compose version v2.x.x`.

---

## 5. Make sure Docker starts automatically after a reboot

You want the server to come back on its own if the VM restarts:

```bash
sudo systemctl enable docker
```

(Our containers are set to `restart: unless-stopped`, so they'll also restart
themselves — see the compose file.)

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `permission denied while trying to connect to the Docker daemon socket` | You didn't log out/in after step 3. Run `exit`, SSH back in, retry. |
| `docker: command not found` after install | Re-run step 2; check for errors in the script output. |
| `Cannot connect to the Docker daemon` | Start it: `sudo systemctl start docker`. |
| Behind a corporate proxy, downloads fail | Ask IT for the proxy address and see <https://docs.docker.com/engine/cli/proxy/>. |

---

✅ Docker is installed and working. Next we bring up the database.

⬅️ Back: [01 · Linux basics](01-linux-basics.md)  ·  ➡️ Next: [03 · Start Qdrant](03-start-qdrant.md)
