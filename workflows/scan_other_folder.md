# Workflow: Scan Other Folder for Positive Replies

## Objective
Recover positive prospect replies that landed in Instantly's "Other" folder instead of the primary inbox. This happens when: (a) the lead was deleted from the system before they replied, or (b) they replied from a different email address. The recovered leads are created in Instantly with Interested status, which triggers downstream Slack notifications to the client.

## When to run
- Daily at 6:00 AM via scheduled routine (set up with `/schedule` skill)
- On-demand via `/scan-other-folder <workspace>` slash command

## Tool
**`tools/scan_other_folder.py`**

```bash
python tools/scan_other_folder.py --workspace=expensify   # single workspace
python tools/scan_other_folder.py --all                    # all configured workspaces
```

## How it works

1. **Incremental fetch** — Reads last-run timestamp from `.tmp/state_{workspace}_other_folder.json`. Fetches Other-folder received emails newest-first, stopping when it hits emails older than that timestamp. First run defaults to 30 days ago.

2. **Filter warmup** — Drops any email whose body contains `a3h7onmg` (Instantly's warmup service identifier). ~98% of Other-folder emails are warmup.

3. **Filter to outreach replies** — Keeps only emails where:
   - Body contains the workspace keyword (e.g., "expensify"), OR
   - Subject matches an outreach pattern ("Re: ... savings for / reaching out / expense / ...")

4. **Classify sentiment**:
   - `exclude:ooo` — Out of office, no longer with company, email inactive
   - `exclude:unsubscribe` — Unsubscribe/opt-out requests
   - `exclude:negative` — Not interested, wrong person, no need
   - `exclude:auto-reply` — Automated responses
   - `positive` — Contains genuine interest signals (see POSITIVE_RE in script)
   - `neutral` — Real reply but no clear positive/negative signal

5. **Campaign matching** — Looks up which campaign the sending account (`eaccount`) belongs to. Falls back to `{WORKSPACE_UPPER}_RECOVERED_LEADS_CAMPAIGN_ID` if no match.

6. **Lead creation** — `POST /leads` with campaign assignment and `lt_interest_status: 1`. If the lead already exists (409), updates interest status via `PATCH /leads/{id}`.

7. **State save** — Writes newest processed email timestamp to state file.

## Adding a new workspace

1. Get the Instantly v2 API key for the workspace (Settings → API → Generate Key)
2. Add to `.env`:
   ```
   {WORKSPACE_UPPER}_INSTANTLY_API_KEY=<key>
   {WORKSPACE_UPPER}_RECOVERED_LEADS_CAMPAIGN_ID=<fallback-campaign-uuid>
   ```
3. Add the workspace to `WORKSPACES` dict in `tools/scan_other_folder.py`:
   ```python
   "newclient": "NEWCLIENT_INSTANTLY_API_KEY",
   ```
4. Test: `python tools/scan_other_folder.py --workspace=newclient`

## Configured workspaces

| Workspace | Env var |
|-----------|---------|
| expensify | EXPENSIFY_INSTANTLY_API_KEY |
| ais | AIS_INSTANTLY_API_KEY |
| patientnow | PATIENTNOW_INSTANTLY_API_KEY |
| growthx | GROWTHX_INSTANTLY_API_KEY |
| sharegate | SHAREGATE_INSTANTLY_API_KEY |
| hapily | HAPILY_INSTANTLY_API_KEY |
| trackstreet | TRACKSTREET_INSTANTLY_API_KEY |
| warespace | WARESPACE_INSTANTLY_API_KEY |
| operatus | OPERATUS_INSTANTLY_API_KEY |
| prelude | PRELUDE_INSTANTLY_API_KEY |
| understory | UNDERSTORY_INSTANTLY_API_KEY |

## Troubleshooting

**`SKIP: EXPENSIFY_INSTANTLY_API_KEY not set`** — Add the API key to `.env`

**`⚠ no campaign match — set EXPENSIFY_RECOVERED_LEADS_CAMPAIGN_ID`** — The sending account wasn't found in any campaign map. Get the fallback campaign UUID from Instantly (campaign URL contains the UUID) and add to `.env`.

**`✗ <email>: 404`** — The `/leads` endpoint returned 404. The lead lookup for existing leads may need a different search parameter. Check the Instantly API docs for the correct search endpoint for this workspace.

**To reset and re-scan from scratch** — Delete `.tmp/state_{workspace}_other_folder.json` and re-run. Will scan the last 30 days.

**To extend the backlog window** — Edit `load_state()` in the script: change `timedelta(days=30)` to a larger value for the initial default.

## Schedule setup

Run `/schedule` and create a daily routine:
- Name: "Other Folder Scan — All Workspaces"  
- Time: 6:00 AM daily
- Prompt: `/scan-other-folder all`
