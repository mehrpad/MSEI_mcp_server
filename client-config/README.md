# Client setup — OpenCode (give this to each user)

Connects your OpenCode to the group's shared publication library and the NHR@FAU
models. ~5 minutes.

## 1. Install OpenCode (it's a terminal app)

OpenCode runs in a **terminal** — there is no separate "software" vs "terminal"
version. You install it once with a command, then start it by typing `opencode`.
Configuring it (the token + `opencode.json`) is identical however you launch it.

**Install — pick one** (Windows users: `npm` is easiest if you have Node.js):

```powershell
npm install -g opencode-ai          # anywhere Node.js is installed
scoop install opencode              # or, Windows: Scoop
choco install opencode              # or, Windows: Chocolatey
curl -fsSL https://opencode.ai/install | bash   # macOS / Linux / WSL
```

- **Check it:** `opencode --version`
- **Start it:** open a terminal in the folder you want to work in, type `opencode`
- Inside OpenCode, `/mcp` lists connected servers (you'll see `msei-publications`
  after step 3).

**You also need:**
- Your **NHR@FAU API token** (from the NHR portal).
- From the admin: the server URL (e.g. `http://10.76.33.35:8080/mcp`).

## 2. Set your NHR API token (PowerShell on Windows)

The token is read from an environment variable, never stored in the config file.

```powershell
# current session — THE QUOTES ARE REQUIRED:
$env:NHR_API_TOKEN="PASTE_YOUR_TOKEN_HERE"
echo $env:NHR_API_TOKEN                       # verify
setx NHR_API_TOKEN "PASTE_YOUR_TOKEN_HERE"    # make permanent (new terminals)
```

> ⚠️ Use the quotes — `$env:NHR_API_TOKEN=...` without quotes fails. After
> `setx`, **restart OpenCode**.
> 🔒 Never paste the token into the config, a screenshot, or chat. If it leaks,
> **regenerate it in the NHR portal**.
> macOS/Linux: `export NHR_API_TOKEN="..."` in `~/.bashrc` / `~/.zshrc`.

## 3. Edit your OpenCode config

Open (create if missing):
- Windows: `%USERPROFILE%\.config\opencode\opencode.json`
- macOS/Linux: `~/.config/opencode/opencode.json`

Replace its **whole contents** with [`opencode.example.json`](opencode.example.json),
then change:
- `url` → the address your admin gave you (must end in `/mcp`)
- `X-User` → your name/initials (how you appear in the server log)

> It must be **one** object: `{` at the top, `}` at the bottom, with `model`,
> `provider`, and `mcp` as siblings (commas between them). An `EndOfFileExpected`
> error means a brace closed the file too early.

## 4. Restart OpenCode and test
Ask: *"Call corpus_stats on msei-publications."* → publication/summary counts. Then a real
question: *"Search the publications for fatigue crack growth in titanium alloys — give DOIs."*

## Trouble?
- `EndOfFileExpected` / JSON error → a brace is in the wrong place (see the note in step 3).
- Tools missing → check the URL ends in `/mcp`; open `http://<vm-ip>:8080/health` in a browser.
- Model errors / 401 → the `NHR_API_TOKEN` isn't set in this session, or OpenCode wasn't restarted after `setx`.
- It hangs → you may not be on the allowed network; tell your admin.

Full walkthrough: [`../docs/07-connect-opencode.md`](../docs/07-connect-opencode.md)
