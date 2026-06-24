# 12 · Remote access (from home / off the office LAN)

> Goal: reach the publication server from outside the office — over the FAU VPN,
> or through an SSH tunnel — and connect OpenCode / Claude Code from there.

Two things must be true to connect from anywhere:
1. **Network can route to the VM** (you can reach `10.76.33.35` at all), and
2. **The VM firewall lets you in** on `:8080` (open mode allows everyone;
   restricted mode needs *your* subnet — see [06 §3](06-run-mcp-server.md)).

---

## Step 1 — On the FAU VPN, test what you can reach

Connect the **FAU VPN**, then in **PowerShell** on your home PC:

```powershell
Test-NetConnection 10.76.33.35 -Port 22      # SSH
Test-NetConnection 10.76.33.35 -Port 8080    # the MCP server
```

Read the `TcpTestSucceeded` line for each, and follow the matching case below.

---

## Case 1 — Port 8080 succeeds → just connect

The VPN routes to the VM **and** the firewall lets you in (you're in *open mode*,
or your VPN subnet is allowed). Use the VM address directly, exactly like on the
office LAN:
- OpenCode / Claude Code `url` = `http://10.76.33.35:8080/mcp`.

Nothing else to do.

## Case 2 — Port 22 succeeds but 8080 doesn't → SSH tunnel

The VPN routes to the VM, but the firewall is blocking your VPN IP. Easiest fix
that needs **no server change** — tunnel the port over SSH. Leave this running in
a terminal:

```powershell
ssh -N -L 8080:127.0.0.1:8080 root@10.76.33.35
```

Then point the client at the **tunnel** instead of the VM IP:
- `url` = `http://127.0.0.1:8080/mcp`

The traffic rides your SSH session and arrives on the VM as `localhost`, which
bypasses the firewall entirely. (If local port 8080 is busy, use
`-L 9090:127.0.0.1:8080` and `http://127.0.0.1:9090/mcp`.)

> Prefer not to tunnel per person? The admin can instead **open the port** or
> **allow the VPN's IP range** — see "For the admin" below.

## Case 3 — Neither succeeds → the VPN doesn't reach the VM

The VPN doesn't route to the VM's subnet (`10.76.33.x`). Options:
- Ask **RRZE / institute IT** to route (or allow) the VPN to the VM's subnet.
- **Jump through a machine** that *is* on the office subnet and reachable on the
  VPN (e.g. your office PC, if it's left on and runs SSH):
  ```powershell
  ssh -N -L 8080:10.76.33.35:8080 you@10.131.233.150
  ```
  The hop to the VM then comes from the allowed office subnet.

---

## For the admin — make home access easy for everyone

Per-person SSH tunnels are fine for one or two people, clumsy for a whole group.
Two cleaner options on the VM:

- **Open mode** — if the university network already isolates the VM, drop the IP
  restriction so anything that can route to it connects
  ([06 §3 Option A](06-run-mcp-server.md)).
- **Allow the VPN range + turn on token auth** — find the FAU VPN's IP range (on
  VPN, `ipconfig` shows your VPN adapter IP; ask RRZE for the pool), allow it in
  `DOCKER-USER`, and enable per-user **token auth** so off-campus access is gated
  by a token rather than IP ([docs/10](10-api-token-auth.md)). This is the
  recommended posture once the server is reachable from beyond the office LAN.

---

## You do **not** need to keep SSH open

The server runs as background Docker services and stays up **24/7**, independent
of any SSH session, and restarts automatically after a reboot. SSH (or a tunnel)
is only needed while you're actively using or administering it — closing your SSH
window does not stop the server for anyone.

---

⬅️ Back: [07 · Connect OpenCode](07-connect-opencode.md)  ·  🏠 [Overview](00-overview.md)
