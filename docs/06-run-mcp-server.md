# 06 · Run the MCP server (and lock down the network)

> Goal: start the MCP server, confirm it's healthy, and make sure **only your
> group's computers** can reach it.

All commands run **on the VM**, in `~/MSEI_mcp_server`.

---

## 1. Start the whole stack

You've already got Qdrant running with data. Now bring up everything (Qdrant stays
up; the MCP server starts and connects to it):

```bash
cd ~/MSEI_mcp_server
docker compose up -d
```

The first time, this **builds** the MCP server image (installs Python packages) —
that takes a few minutes. Afterwards it's instant.

Check both containers are up:

```bash
docker compose ps
```

Expected: `msei-qdrant` and `msei-mcp`, both **running**, `msei-mcp` showing
**(healthy)** after ~20 seconds.

---

## 2. Confirm the server is healthy

```bash
curl http://localhost:8080/health
```

Expected:

```json
{"status": "ok", "server": "paperRAG-v2", "prefix": "materials_v2"}
```

Watch the live logs for a few seconds (press `Ctrl+C` to stop watching — it does
**not** stop the server):

```bash
docker compose logs -f mcp
```

You should see a line like:

```
paperRAG-v2 starting | transport=streamable-http | qdrant=http://qdrant:6333 | prefix=materials_v2 | model=gemini-embedding-2-preview
```

> **The address users will connect to** is
> `http://<VM-IP>:8080/mcp` — for example `http://10.12.0.5:8080/mcp`.
> (Find the VM IP with `hostname -I`.) We test it from a real client in
> [step 07](07-connect-opencode.md).

---

## 3. Lock down the network (important)

Right now the server port (8080) may be reachable by anything that can route to
the VM. On a private faculty network that's often acceptable, but it's good
practice to allow **only your group's subnet**.

### ⚠️ Docker + `ufw` gotcha — read this

Docker publishes ports by editing the firewall rules directly, which means the
popular `ufw` firewall **does not** filter Docker-published ports the way you'd
expect. The reliable way to restrict a Docker-published port is the special
**`DOCKER-USER`** rule below.

### Allow only your subnet to reach port 8080

Replace `10.12.0.0/16` with **your group's subnet** (ask if unsure):

```bash
# Allow your subnet, drop everyone else, for the MCP port.
sudo iptables -I DOCKER-USER -p tcp --dport 8080 -s 10.12.0.0/16 -j RETURN
sudo iptables -I DOCKER-USER -p tcp --dport 8080 -j DROP
```

> Order matters: the `RETURN` (allow) rule is inserted first so it sits **above**
> the `DROP`. `-I` inserts at the top, so run them in the order shown.

Make the rules survive a reboot:

```bash
sudo apt install -y iptables-persistent
sudo netfilter-persistent save
```

Also protect SSH and general traffic with `ufw` (this part `ufw` handles fine):

```bash
sudo ufw allow OpenSSH
sudo ufw --force enable
```

### Verify the lockdown

From a computer **on your subnet**, this should connect:

```bash
curl http://10.12.0.5:8080/health
```

From a machine **outside** the subnet, the same command should hang/refuse.

---

## 4. (Optional) See who is searching

Every request is logged with the client **IP** and the **username** they declare
(the `X-User` header set in OpenCode — see step 07). By default these lines go to
the container log:

```bash
docker compose logs mcp | grep '"user"'
```

Example line:

```json
{"ts": "2026-06-23T14:05:00", "ip": "10.12.0.42", "user": "anna.k", "method": "POST", "path": "/mcp"}
```

To keep a **permanent** audit file instead, set `AUDIT_LOG=/data/audit.log` in
`.env` and restart (`docker compose up -d mcp`); the file persists in the
`mcp_data` volume.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `msei-mcp` keeps restarting | `docker compose logs mcp`. Usually a missing/invalid `GEMINI_API_KEY` in `.env` — fix it, then `docker compose up -d mcp`. |
| `/health` works but searches fail | Key or internet problem — re-check [step 05](05-google-api-key.md). |
| Health says `prefix` you didn't expect | `COLLECTION_PREFIX` in `.env` doesn't match your data. Fix and restart. |
| Users can't connect but `localhost` works | Firewall/subnet. Re-check step 3 and the VM IP. |
| Changed `.env` but nothing changed | You must restart: `docker compose up -d mcp`. |

---

✅ The server is live and protected. Now connect a user.

⬅️ Back: [05 · Google API key](05-google-api-key.md)  ·  ➡️ Next: [07 · Connect OpenCode](07-connect-opencode.md)
