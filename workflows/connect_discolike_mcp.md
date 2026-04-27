# Connect Discolike MCP

## Objective
Authenticate and connect the Discolike MCP server so its tools are available in Claude Code.

## When to use this
- First-time Discolike MCP setup
- Token expired and Discolike tools stop working ("needs auth" in `/mcp`)
- After a fresh Claude Code install

## Token lifetime — read this first

The Discolike access token lasts **~90 minutes**. The full flow is:

1. Run the auth script → browser opens → log in → token saved
2. Restart Claude Code **immediately** (within 90 min of step 1)
3. Tools are available for the session
4. When tools stop working again, just re-run Step 3 and reload the window

**Do not wait between running the script and restarting Claude Code.**

---

## Config files involved
- `.mcp.json` — project-level MCP server list (must include discolike)
- `C:/Users/joshu/.claude/.credentials.json` — OAuth tokens
- `C:/Users/joshu/.claude.json` — user/project-level MCP server config

## Step 1: Ensure discolike is in .mcp.json

`.mcp.json` must have this entry:
```json
"discolike": {
  "type": "http",
  "url": "https://api.discolike.com/v1/mcp"
}
```

If missing, add it. Do NOT include any `headers` or `Authorization` — the token is managed by OAuth, not hardcoded.

## Step 2: Clear stale OAuth entries (first-time or broken state only)

Only needed on first-time setup or if the credentials are corrupted. Stale entries block a fresh auth. Run this:

```python
import json

# Clear stale discolike OAuth tokens from credentials
path = 'C:/Users/joshu/.claude/.credentials.json'
with open(path) as f: creds = json.load(f)
creds['mcpOAuth'] = {k: v for k, v in creds.get('mcpOAuth', {}).items() if 'discolike' not in k}
with open(path, 'w') as f: json.dump(creds, f)

# Clear stale discolike entries from claude.json
path2 = 'C:/Users/joshu/.claude.json'
with open(path2) as f: data = json.load(f)
if 'mcpServers' in data:
    data['mcpServers'] = {k: v for k, v in data['mcpServers'].items() if 'discolike' not in k}
for proj in data.get('projects', {}).values():
    if 'mcpServers' in proj:
        proj['mcpServers'] = {k: v for k, v in proj['mcpServers'].items() if 'discolike' not in k}
with open(path2, 'w') as f: json.dump(data, f, indent=2)

print("Cleared.")
```

**Skip this step for routine token refresh** — only needed when the credential entry itself is broken (e.g., wrong clientId stored, first-time setup).

## Step 3: Run the OAuth auth script

Claude Code's VSCode extension cannot complete the OAuth browser flow automatically. Use the dedicated tool:

```
python "C:\Folders\Claude Code\Test\tools\discolike_auth.py"
```

This script:
1. Reads Claude Code's already-registered OAuth `clientId`/`clientSecret` from `.credentials.json` (so the token is issued for Claude Code's own client — required for Claude Code to accept it)
2. Opens a browser for you to log in to your Discolike account
3. Captures the callback and exchanges the code for a token
4. Saves the token to `.credentials.json` under the key `discolike|27c43afa13599f03`

## Step 4: Restart Claude Code immediately

After the script reports success, **restart Claude Code right away** — do not wait. The access token has a ~90-minute lifetime. If you restart more than ~90 minutes after running the script, the token will already be expired and Claude Code will show "needs auth" again. In that case, just re-run Step 3 and restart immediately.

**For routine refresh** (token expired mid-session): re-run Step 3, then reload the VSCode window (`Ctrl+Shift+P → Reload Window`) rather than doing a full restart.

## Step 5: Verify

Ask Claude to run `ToolSearch` for "discolike" — tools should appear.

---

## Key technical details (for debugging)

- MCP endpoint: `https://api.discolike.com/v1/mcp`
- OAuth registration endpoint: `https://auth.discolike.com/oauth/2.1/register`
- OAuth authorize endpoint: `https://auth.discolike.com/oauth/2.1/authorize`
- OAuth token endpoint: `https://auth.discolike.com/oauth/2.1/token`
- Auth server metadata: `https://auth.discolike.com/.well-known/oauth-authorization-server/oauth/2.1`
- Uses PKCE (S256), `state`, and `resource=https://api.discolike.com/v1/mcp` — all required
- Token stored in `.credentials.json` under `mcpOAuth["discolike|27c43afa13599f03"].accessToken`
- Credentials key hash `27c43afa13599f03` is fixed — Claude Code writes its `clientId`/`clientSecret` to this key on first discovery; the auth script reads them back and reuses them so the token is issued for Claude Code's client
- Token lifetime: ~90 minutes (Discolike server-side limit)
- The `/mcp` panel shows "needs auth" when: (a) no token exists, (b) token is expired, or (c) token was issued for a different OAuth client than the one stored in credentials

## Common errors and fixes

| Error | Fix |
|-------|-----|
| "needs auth" after running script | Token expired before restart — re-run Step 3 and restart **immediately** |
| "needs auth" persists after immediate restart | ClientId mismatch — run Step 2 to wipe the entry, then repeat Steps 3–4 so Claude Code re-registers a fresh client |
| `invalid_token` on API call | Token expired mid-session — re-run Step 3 and reload window |
| `already exists in local config` on `claude mcp add` | Run Step 2 to clear stale entries first |
| `Missing or invalid resource` in OAuth callback | Script is missing `resource` param — check `discolike_auth.py` |
| `Query deserialize error: missing field state` | Script is missing `state` param — check `discolike_auth.py` |
| Auth URL 404 | Wrong auth endpoint — correct one is `https://auth.discolike.com/oauth/2.1/authorize` |
| Discolike not showing in `/mcp` | Path case mismatch in `.claude.json` — use `.mcp.json` instead |
| Can't click Authenticate in VSCode `/mcp` UI | VSCode extension bug — use `tools/discolike_auth.py` instead |
| Script says "No existing client found" | Credentials entry was wiped — restart Claude Code once (it will register a new client), then re-run Step 3 |
