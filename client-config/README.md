# Client setup — OpenCode (give this to each user)

This connects your OpenCode to the group's shared paper library. ~2 minutes.

## 1. Prerequisites
- OpenCode installed — <https://opencode.ai>
- Your local model already working in OpenCode
- From the admin: the server URL (e.g. `http://10.12.0.5:8080/mcp`)

## 2. Edit your OpenCode config
Open (create if missing):
- macOS/Linux: `~/.config/opencode/opencode.json`
- Windows: `%USERPROFILE%\.config\opencode\opencode.json`

## 3. Add the paper server
Copy [`opencode.example.json`](opencode.example.json) into it, then change:
- `url` → the address your admin gave you (must end in `/mcp`)
- `X-User` → your name/initials (this is how you appear in the server log)

If your config already has an `"mcp"` section, just add the `"msei-papers"` entry
inside it — don't create a second `"mcp"` block.

## 4. Restart OpenCode and test
Ask: *"Call corpus_stats on msei-papers."* You should get paper/figure/table
counts. Then try a real question:
*"Search the papers for fatigue crack growth in titanium alloys — give DOIs."*

## Trouble?
- Tools missing → check the URL ends in `/mcp`; run `curl <url-without-/mcp>/health`.
- It hangs → you may not be on the allowed network; tell your admin.

Full walkthrough: [`../docs/07-connect-opencode.md`](../docs/07-connect-opencode.md)
