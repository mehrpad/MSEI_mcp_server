# 10 · Optional: API-token authentication

> **Advanced / optional.** By default the server is **open** on the private
> network: it trusts anyone who can reach it and identifies them by IP + the
> `X-User` name they declare. That's the right setup for a trusted faculty LAN.
>
> Turn this on if you want **real per-user accountability** — a token each person
> must present, so usernames can't simply be made up, and access can be revoked.

This is fully built in and **disabled until you set `MCP_AUTH_TOKENS`**. Nothing
changes for existing users until you do.

---

## How it works

- You create one **token** per user and map it to their name.
- Each user puts their token in OpenCode as an `Authorization: Bearer <token>`
  header.
- The server checks every request: no/invalid token → `401 Unauthorized`; valid
  token → the request proceeds and is logged under that user's **mapped** name
  (not a self-declared one).
- `/health` stays open (so monitoring keeps working).

---

## Step 1 — Generate tokens

On the VM (or anywhere), make one random token per user:

```bash
openssl rand -hex 24
```

Run it once per person. Keep a list mapping token → person, e.g.:

```
anna.k  → 2f9a3c...e1
ben.r   → 7c3b88...90
```

---

## Step 2 — Enable it in `.env`

Edit `.env` and set `MCP_AUTH_TOKENS` to comma-separated `token=username` pairs:

```
MCP_AUTH_TOKENS=2f9a3c...e1=anna.k,7c3b88...90=ben.r
```

Restart the server:

```bash
docker compose up -d mcp
curl http://localhost:8080/health      # "auth": "token"
```

The startup log now shows `auth=token (N keys)`.

> Using the **second corpus** server too? It reads the same `.env`, so the same
> tokens work there. Restart it with `docker compose --profile second up -d`.

---

## Step 3 — Each user adds their token to OpenCode

In their `opencode.json`, add an `Authorization` header alongside (or instead of)
`X-User`:

```json
{
  "mcp": {
    "msei-publications": {
      "type": "remote",
      "url": "http://10.12.0.5:8080/mcp",
      "enabled": true,
      "headers": {
        "Authorization": "Bearer 2f9a3c...e1"
      }
    }
  }
}
```

That's the only change on the user side. (With tokens on, the audit log uses the
name mapped to the token, so `X-User` becomes optional.)

---

## Verify

```bash
# No token → rejected:
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8080/mcp
# → 401

# Valid token → accepted (you'll get an MCP protocol response, not 401):
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8080/mcp \
  -H "Authorization: Bearer 2f9a3c...e1"
# → 200 (or a normal MCP error, but NOT 401)
```

The audit log shows the mapped user and flags rejected attempts:

```json
{"ts":"...","ip":"10.12.0.42","user":"anna.k","path":"/mcp","auth":"token"}
{"ts":"...","ip":"10.12.0.99","user":"?","path":"/mcp","auth":"denied"}
```

---

## Managing tokens

- **Add a user:** append `,newtoken=name` to `MCP_AUTH_TOKENS`, `docker compose up -d mcp`.
- **Revoke a user:** remove their pair, restart. Their token stops working immediately.
- **Rotate:** generate a new token, swap it in, restart, give the user the new one.

---

## Important security note

Bearer tokens are sent in the request. Over plain `http://` on a trusted private
LAN that's acceptable. If this server is ever reachable over an **untrusted**
network, put it behind **TLS** (e.g. a Caddy reverse proxy terminating HTTPS) so
tokens aren't sent in the clear. Ask and this can be added to the compose stack.

---

## Turning it back off

Comment out / remove `MCP_AUTH_TOKENS` in `.env`, then `docker compose up -d mcp`.
The server returns to open (IP + `X-User`) mode.

---

⬅️ Back: [09 · Operations](09-operations-troubleshooting.md)  ·  🏠 [Overview](00-overview.md)
