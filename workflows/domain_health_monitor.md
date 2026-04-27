# Domain Health Monitor — Instantly

## Quick Start: Web Dashboard

For a live, interactive view — just run:
```
python tools/deliverability_dashboard.py
```
Then open **http://localhost:8080** and enter any Instantly v2 API key.
The dashboard shows all domains in the workspace with health status, warmup scores,
inbox placement, and bounce rates. No setup required — data fetches live from the API.

Run the bat file for a one-click launch: `tools/run_deliverability_dashboard.bat`

---

## Objective

Analyze all email domains in Instantly, score their health, classify them as Healthy/At Risk/Unhealthy, and generate a CSV report with specific recommendations for each domain.

## When to Run

- **Recommended cadence:** Weekly (every Monday)
- **Also run after:** Adding new domains, noticing deliverability drops, or before launching new campaigns

## Prerequisites

- `INSTANTLY_API_KEY` set in `.env` (v2 key with scopes: `accounts:read`, `campaigns:read`, `analytics:read`)
- Python 3.8+ with `requests`, `python-dotenv` installed

## Tool Execution Sequence

Run these tools in order:

### Step 1: Fetch accounts
```
python tools/fetch_instantly_accounts.py
```
Pulls all email accounts, extracts domains. Output: `.tmp/accounts.json`

### Step 2: Fetch warmup analytics
```
python tools/fetch_instantly_warmup.py
```
Gets warmup health scores, inbox/spam placement per account. Output: `.tmp/warmup.json`

### Step 3: Fetch campaign data
```
python tools/fetch_instantly_campaigns.py
```
Gets campaign analytics and account-campaign mappings. Output: `.tmp/campaigns.json`

### Step 4: Score domains
```
python tools/score_domain_health.py
```
Computes composite health score per domain, classifies them. Output: `.tmp/domain_scores.json`

### Step 5: Generate report
```
python tools/generate_domain_report.py
```
Outputs CSV report with all metrics and recommendations. Output: `.tmp/domain_health_report.csv`

## Health Scoring Model

| Metric | Weight | Source |
|--------|--------|--------|
| Warmup Health Score | 25% | Instantly's native 0-100 score |
| Inbox Placement Rate | 30% | landed_inbox / (inbox + spam) |
| Blacklist Count | 20% | From inbox placement tests |
| SpamAssassin Score | 10% | From inbox placement tests |
| Reply Rate | 15% | Campaign analytics (relative to account avg) |

Missing metrics are excluded and weights are redistributed proportionally.

## Classifications

| Classification | Score Range | Action |
|---------------|-------------|--------|
| **Healthy** | ≥ 75 | No action. Keep in campaigns. |
| **At Risk** | 50–74 | Monitor weekly. Consider reducing send volume. |
| **Unhealthy** | < 50 | Remove from campaigns. Enable warmup-only mode. Recheck in 2 weeks. |
| **Insufficient Data** | N/A | New domain or no warmup data yet. Wait for data before acting. |

## How to Act on Results

### For Unhealthy domains:
1. Note which campaigns they're in (from the `active_campaigns` column)
2. Remove domain's mailboxes from those campaigns in Instantly
3. Enable warmup-only mode for those mailboxes
4. Re-run this workflow in 2 weeks to check recovery

### For At Risk domains:
1. Reduce daily sending limits for these mailboxes
2. Check if they're on any blacklists (see `blacklist_count` column)
3. If blacklisted, pause sending entirely and enable warmup
4. Re-run next week to track trend

### For Healthy domains:
1. These are your workhorses — keep them active
2. If launching a new campaign, prefer these domains

## Edge Cases

- **New domains** (< 3 days of warmup data): Shown as "Insufficient Data." Don't act on them yet.
- **Domains not in any campaign**: Still scored on warmup health. Useful for knowing which domains are ready to deploy.
- **Single-mailbox domains**: Scored normally but less reliable. Consider the `mailbox_count` column.
- **Blacklist/SpamAssassin data unavailable**: These require inbox placement tests to have been run in Instantly. If missing, the score is computed from available metrics only. The `data_completeness` column shows how many metrics were available.

## Learned Constraints

_(Update this section as you discover API quirks, rate limits, or edge cases)_

- Rate limits are shared across the entire Instantly workspace
- Warmup analytics endpoint accepts batches of 50 emails max
- Campaign analytics may return aggregate data instead of per-campaign when no campaign ID is specified
