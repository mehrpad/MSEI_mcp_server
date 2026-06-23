# 05 · Google API key (for embeddings)

> Goal: create a Google Gemini API key, activate it, and put it in `.env`. The
> server uses it to turn search queries into vectors.

**One key for the whole server.** Users never see it or need their own. It lives
only in the `.env` file on the VM.

---

## Why this is needed

When someone searches *"creep behaviour of nickel superalloys"*, the server sends
that text to Google's embedding model, which returns a vector (a list of numbers).
Qdrant then finds the paper passages whose vectors are closest. No key → no
embeddings → no search.

---

## Step 1 — Create the key

1. Go to **<https://aistudio.google.com/apikey>** in a browser (on any computer).
2. Sign in with a Google account.
3. Click **"Create API key"**.
4. Copy the key. It looks like `AIzaSy...` (about 39 characters).

> **Free vs paid:** Google offers a free tier with rate limits, and a paid tier
> for higher volume. For ~100 users doing occasional searches the cost is small
> (embeddings are cheap — a search embeds only the query text). You can start
> free and enable billing later if you hit limits. Check current pricing at
> <https://ai.google.dev/pricing>.

---

## Step 2 — Put the key in `.env`

On the **VM**, in the project folder:

```bash
cd ~/MSEI_mcp_server
nano .env
```

Find the line:

```
GEMINI_API_KEY=PASTE-YOUR-KEY-HERE
```

Replace `PASTE-YOUR-KEY-HERE` with your real key (no quotes, no spaces):

```
GEMINI_API_KEY=AIzaSyAbC123...your-real-key...
```

Save (`Ctrl+O`, `Enter`) and exit (`Ctrl+X`).

> The `.env` file is already git-ignored, so the secret won't be committed. Keep
> it private — anyone with this key can spend your quota.

---

## Step 3 — (Recommended) lock the key down

In the Google Cloud / AI Studio console you can harden the key so a leak can't be
abused:

- **Restrict the API:** allow only the *Generative Language API*.
- **Set a quota cap:** a daily request limit so a bug or misuse can't run up a
  bill.
- **(Optional) IP restriction:** allow the key to be used only from the VM's
  public IP, if your VM has a stable one.

These are optional but good practice for a shared server.

---

## Step 4 — Confirm the VM can reach Google

The VM must be able to reach Google's API over the internet. Test it:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://generativelanguage.googleapis.com
```

- A number like `404` or `200` → **good** (you reached Google; 404 just means that
  bare URL has no page).
- It hangs or says `Could not resolve host` → the VM has **no internet egress** to
  Google. See the box below.

> ### ⚠️ If the VM is air-gapped (no internet)
> The Google embedding API will not work without outbound internet. Two options:
> 1. **Ask the firewall admin** to allow HTTPS to
>    `generativelanguage.googleapis.com` (and `*.googleapis.com`). This is the
>    simplest fix.
> 2. **Switch to a local embedding model** that runs on the VM's CPU (no
>    internet). This is a code change in the embedding function and the data must
>    be re-ingested with the same local model. If you need this path, flag it —
>    it changes the ingest repo too. See
>    [09 · Operations](09-operations-troubleshooting.md#appendix-offline-embeddings).

---

## Troubleshooting

| Symptom (you'll see it after step 06) | Fix |
|---------|-----|
| Tool returns `No Google API key found` | The key isn't in `.env`, or you didn't restart the server after editing. Re-check `.env`, then `docker compose up -d mcp`. |
| `PERMISSION_DENIED` / `API key not valid` | The key is wrong or the Generative Language API isn't enabled for it. Recreate it in step 1. |
| `RESOURCE_EXHAUSTED` / `429` | You hit the rate limit/quota. Wait, or enable billing / raise the cap. |

---

✅ The server now has everything it needs. Time to start it.

⬅️ Back: [04 · Load the paper data](04-load-vector-data.md)  ·  ➡️ Next: [06 · Run the MCP server](06-run-mcp-server.md)
