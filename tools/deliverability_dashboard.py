#!/usr/bin/env python3
"""
Deliverability Dashboard — Understory Agency

Flask web app for auditing Instantly workspace domain health.
Enter an API key to audit all mailboxes and get clear recommendations:
  Burned    — replace these domains immediately
  At Risk   — approaching burned, reduce volume
  Watch     — monitor closely
  Healthy   — keep sending, these are working

Scoring signals (transcript-derived):
  Warmup score     < 98 per mailbox flags it; ≥20% of a domain = pull whole domain
  Inbox placement  < 90% = Watch, < 80% = At Risk, < 60% = Burned
  Bounce rate      > 0.5% = Watch, > 1% = At Risk, > 2% = Burned
  Account errors   any disconnected mailboxes on a domain

Usage:
    python tools/deliverability_dashboard.py
    Open: http://localhost:8080
"""

import os
import sys
import time
import sqlite3
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, request, render_template_string, redirect, url_for, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dlvry-understory-2026")

# ── Client Registry (SQLite) ──────────────────────────────────────────────────

_DB_PATH = Path(os.environ.get(
    "DB_PATH",
    Path(__file__).resolve().parent.parent / ".tmp" / "clients.db"
))

def _db():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS clients (
        name     TEXT PRIMARY KEY,
        api_key  TEXT NOT NULL,
        added_at TEXT DEFAULT (datetime('now')),
        used_at  TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()
    return conn

def list_clients():
    try:
        with _db() as c:
            return [r[0] for r in c.execute(
                "SELECT name FROM clients ORDER BY used_at DESC, name ASC"
            ).fetchall()]
    except Exception:
        return []

def get_client_key(name):
    try:
        with _db() as c:
            row = c.execute("SELECT api_key FROM clients WHERE name=?", (name,)).fetchone()
            return row[0] if row else None
    except Exception:
        return None

def save_client(name, api_key):
    try:
        with _db() as c:
            c.execute(
                "INSERT OR REPLACE INTO clients (name, api_key, used_at) VALUES (?,?,datetime('now'))",
                (name, api_key)
            )
    except Exception:
        pass

def touch_client(name):
    try:
        with _db() as c:
            c.execute("UPDATE clients SET used_at=datetime('now') WHERE name=?", (name,))
    except Exception:
        pass

logging.basicConfig(level=logging.WARNING)

# ── Constants ────────────────────────────────────────────────────────────────
BASE_URL            = "https://api.instantly.ai/api/v2"
WARMUP_THRESHOLD    = 98       # < 98 per mailbox = flagged
DOMAIN_BURNED_PCT   = 0.20     # ≥ 20% bad mailboxes → Burned (10/50 rule)
DOMAIN_ATRISK_PCT   = 0.10     # ≥ 10% → At Risk
BOUNCE_BURNED       = 2.0
BOUNCE_ATRISK       = 1.0
BOUNCE_WATCH        = 0.5
INBOX_BURNED        = 60.0
INBOX_ATRISK        = 80.0
INBOX_WATCH         = 90.0
MIN_SEND_THRESHOLD  = 20       # minimum sends before scoring bounce
REPLY_ATRISK        = 0.3      # < 0.3% reply rate (incl. OOO) = At Risk
REPLY_WATCH         = 1.0      # < 1.0% = Watch
MIN_REPLY_SENDS     = 50       # minimum sends before scoring reply rate
BATCH_SIZE          = 50

STATUS_ORDER = {"Burned": 0, "At Risk": 1, "Watch": 2, "Healthy": 3, "No Data": 4}
STATUS_LABEL = {
    "Burned":  "Replace Now",
    "At Risk": "Reduce Volume",
    "Watch":   "Monitor",
    "Healthy": "Keep Sending",
    "No Data": "Insufficient Data",
}


# ── API Layer ─────────────────────────────────────────────────────────────────

def _headers(key):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _get(key, endpoint, params=None, retries=5):
    url = f"{BASE_URL}{endpoint}"
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=_headers(key), params=params, timeout=60)
            if r.status_code == 401:
                raise ValueError("Invalid API key — check your Instantly v2 API key.")
            if r.status_code == 429:
                time.sleep(min(5 * (attempt + 1), 30))
                continue
            r.raise_for_status()
            return r.json()
        except ValueError:
            raise
        except requests.exceptions.HTTPError:
            if attempt < retries - 1 and r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"API error {r.status_code}: {r.text[:200]}")
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Request failed: {e}")
    raise RuntimeError(f"Max retries exceeded: {endpoint}")


def _post(key, endpoint, body, retries=5):
    url = f"{BASE_URL}{endpoint}"
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=_headers(key), json=body, timeout=60)
            if r.status_code == 429:
                time.sleep(min(5 * (attempt + 1), 30))
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError:
            if attempt < retries - 1 and r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"API error {r.status_code}: {r.text[:200]}")
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Request failed: {e}")
    raise RuntimeError(f"Max retries exceeded: {endpoint}")


def _paginate(key, endpoint, params=None, limit=100):
    items_all, params, cursor = [], params or {}, None
    while True:
        p = {**params, "limit": limit}
        if cursor:
            p["starting_after"] = cursor
        data = _get(key, endpoint, params=p)
        if isinstance(data, list):
            items, cursor = data, None
        elif isinstance(data, dict):
            items = data.get("items", data.get("data", []))
            cursor = data.get("next_starting_after")
        else:
            break
        if not items:
            break
        items_all.extend(items)
        if not cursor or len(items) < limit:
            break
        time.sleep(0.1)
    return items_all


# ── Data Fetching ─────────────────────────────────────────────────────────────

def fetch_accounts(key):
    accounts = _paginate(key, "/accounts")
    for a in accounts:
        email = a.get("email", "")
        a["domain"] = email.split("@")[1].lower() if "@" in email else "unknown"
    return accounts


def fetch_warmup_analytics(key, emails):
    """Returns {email: {inbox, spam, sent, received}}"""
    stats = {}
    for i in range(0, len(emails), BATCH_SIZE):
        batch = emails[i:i + BATCH_SIZE]
        try:
            data = _post(key, "/accounts/warmup-analytics", {"emails": batch})
            entries = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for email, dates in entry.get("email_date_data", {}).items():
                    if email not in stats:
                        stats[email] = {"inbox": 0, "spam": 0, "sent": 0, "received": 0}
                    for day in (dates.values() if isinstance(dates, dict) else []):
                        if not isinstance(day, dict):
                            continue
                        stats[email]["inbox"]    += day.get("landed_inbox", 0) or 0
                        stats[email]["spam"]     += day.get("landed_spam",  0) or 0
                        stats[email]["sent"]     += day.get("sent",         0) or 0
                        stats[email]["received"] += day.get("received",     0) or 0
        except Exception:
            pass
        time.sleep(0.3)
    return stats


def fetch_daily_analytics(key, days=None):
    """Returns {email: {sent, bounced, replies, replies_auto}}, optionally filtered to last N days."""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=int(days))).isoformat() if days else None

    result = defaultdict(lambda: {"sent": 0, "bounced": 0, "replies": 0, "replies_auto": 0})
    try:
        data = _get(key, "/accounts/analytics/daily")
        rows = data if isinstance(data, list) else data.get("items", data.get("data", []))
        for row in rows:
            if cutoff and (row.get("date") or "") < cutoff:
                continue
            acct = (row.get("email_account") or row.get("email") or "").lower()
            if acct:
                result[acct]["sent"]         += row.get("sent",                0) or 0
                result[acct]["bounced"]       += row.get("bounced",             0) or 0
                result[acct]["replies"]       += row.get("replies",             0) or 0
                result[acct]["replies_auto"]  += row.get("replies_automatic",   0) or 0
    except Exception:
        pass
    return dict(result)


def fetch_workspace_stats(key):
    """Aggregates all-campaigns totals for the header stats bar."""
    try:
        data = _get(key, "/campaigns/analytics")
        campaigns = data if isinstance(data, list) else data.get("items", data.get("data", []))
        if not campaigns:
            return {}
        return {
            "total_sent": sum(c.get("emails_sent_count", 0) or 0 for c in campaigns),
            "reply_count": sum(
                max(0, (c.get("reply_count", 0) or 0) - (c.get("reply_count_automatic", 0) or 0))
                for c in campaigns
            ),
            "total_bounced": sum(c.get("bounced_count", 0) or 0 for c in campaigns),
            "contacted_count": sum(c.get("contacted_count", 0) or 0 for c in campaigns),
        }
    except Exception:
        return {}


# ── Scoring ───────────────────────────────────────────────────────────────────

def classify_domain(m):
    issues, votes = [], []

    # — Warmup signal (primary: per-mailbox threshold) —
    bad_pct   = m.get("bad_warmup_pct", 0)
    bad_count = m.get("bad_warmup_count", 0)
    if bad_pct >= DOMAIN_BURNED_PCT:
        issues.append(f"{bad_pct*100:.0f}% of mailboxes below {WARMUP_THRESHOLD} warmup")
        votes.append("Burned")
    elif bad_pct >= DOMAIN_ATRISK_PCT:
        issues.append(f"{bad_count} mailbox(es) below {WARMUP_THRESHOLD} warmup")
        votes.append("At Risk")
    elif bad_count > 0:
        issues.append(f"{bad_count} mailbox(es) slightly below {WARMUP_THRESHOLD}")
        votes.append("Watch")

    # — Inbox placement signal —
    inbox = m.get("inbox_placement_pct")
    if inbox is not None:
        if inbox < INBOX_BURNED:
            issues.append(f"Inbox placement critical ({inbox:.0f}%)")
            votes.append("Burned")
        elif inbox < INBOX_ATRISK:
            issues.append(f"Inbox placement low ({inbox:.0f}%)")
            votes.append("At Risk")
        elif inbox < INBOX_WATCH:
            issues.append(f"Inbox placement declining ({inbox:.0f}%)")
            votes.append("Watch")

    # — Bounce rate signal —
    bounce = m.get("bounce_rate_pct")
    if bounce is not None and m.get("total_sent", 0) >= MIN_SEND_THRESHOLD:
        if bounce > BOUNCE_BURNED:
            issues.append(f"Bounce rate {bounce:.1f}% — domain problem")
            votes.append("Burned")
        elif bounce > BOUNCE_ATRISK:
            issues.append(f"Bounce rate elevated ({bounce:.1f}%)")
            votes.append("At Risk")
        elif bounce > BOUNCE_WATCH:
            issues.append(f"Bounce rate rising ({bounce:.1f}%)")
            votes.append("Watch")

    # — Reply rate signal (manual + OOO auto-replies) —
    reply = m.get("reply_rate_pct")
    if reply is not None:
        if reply < REPLY_ATRISK:
            issues.append(f"Reply rate very low ({reply:.2f}%) — likely in spam")
            votes.append("At Risk")
        elif reply < REPLY_WATCH:
            issues.append(f"Reply rate low ({reply:.2f}%)")
            votes.append("Watch")

    # — Account errors signal —
    error_pct   = m.get("error_pct", 0)
    error_count = m.get("error_count", 0)
    if error_pct > 0.5:
        issues.append(f"{error_pct*100:.0f}% of mailboxes disconnected")
        votes.append("Burned")
    elif error_count > 0:
        issues.append(f"{error_count} mailbox(es) disconnected")
        votes.append("Watch")

    if not votes:
        status = "Healthy" if m.get("warmup_avg") is not None else "No Data"
    else:
        status = min(votes, key=lambda s: STATUS_ORDER[s])

    return status, issues


def build_domain_metrics(accounts, warmup_stats, daily_stats):
    by_domain = defaultdict(list)
    for a in accounts:
        by_domain[a["domain"]].append(a)

    domains = []
    for domain, accts in sorted(by_domain.items()):
        # Warmup scores from account stat
        warmup_scores = []
        for a in accts:
            ws = a.get("stat_warmup_score")
            if ws is not None:
                try:
                    warmup_scores.append(float(ws))
                except (ValueError, TypeError):
                    pass

        bad_warmup  = [s for s in warmup_scores if s < WARMUP_THRESHOLD]
        warmup_avg  = round(sum(warmup_scores) / len(warmup_scores), 1) if warmup_scores else None

        # Inbox placement from warmup analytics
        d_inbox = d_spam = 0
        for a in accts:
            ws = warmup_stats.get(a.get("email", ""), {})
            d_inbox += ws.get("inbox", 0)
            d_spam  += ws.get("spam",  0)
        total_warmup_delivered = d_inbox + d_spam
        inbox_pct = round(d_inbox / total_warmup_delivered * 100, 1) if total_warmup_delivered > 0 else None

        # Send / bounce / reply from daily analytics
        d_sent = d_bounced = d_replies = d_replies_auto = 0
        for a in accts:
            ds = daily_stats.get(a.get("email", "").lower(), {})
            d_sent         += ds.get("sent",         0)
            d_bounced      += ds.get("bounced",      0)
            d_replies      += ds.get("replies",      0)
            d_replies_auto += ds.get("replies_auto", 0)
        bounce_rate   = round(d_bounced / d_sent * 100, 2) if d_sent > 0 else None
        total_replies = d_replies + d_replies_auto
        reply_rate    = round(total_replies / d_sent * 100, 2) if d_sent >= MIN_REPLY_SENDS else None

        # Account statuses
        statuses    = [a.get("status", 0) for a in accts]
        active_cnt  = sum(1 for s in statuses if s == 1)
        error_cnt   = sum(1 for s in statuses if isinstance(s, int) and s < 0)

        m = {
            "domain":             domain,
            "mailbox_count":      len(accts),
            "warmup_avg":         warmup_avg,
            "bad_warmup_count":   len(bad_warmup),
            "bad_warmup_pct":     len(bad_warmup) / len(warmup_scores) if warmup_scores else 0,
            "inbox_placement_pct": inbox_pct,
            "total_sent":         d_sent,
            "total_bounced":      d_bounced,
            "bounce_rate_pct":    bounce_rate,
            "total_replies":      total_replies,
            "total_replies_auto": d_replies_auto,
            "reply_rate_pct":     reply_rate,
            "active_count":       active_cnt,
            "error_count":        error_cnt,
            "error_pct":          error_cnt / len(accts) if accts else 0,
        }
        m["status"], m["issues"] = classify_domain(m)
        domains.append(m)

    domains.sort(key=lambda d: (STATUS_ORDER.get(d["status"], 99), d["domain"]))
    return domains


# ── HTML Templates ────────────────────────────────────────────────────────────

_GREEN   = "#02E481"
_DARK    = "#071018"
_BG      = "#0a0e13"
_CARD    = "#171719"
_CARD2   = "#1c1f24"
_BORDER  = "#2a2e36"
_WHITE   = "#ffffff"
_SUB     = "#94a3b8"
_MUTED   = "#4b5563"
_RED     = "#ef4444"
_ORANGE  = "#f97316"
_AMBER   = "#f59e0b"

LOGIN_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deliverability Audit — Understory</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Inter',sans-serif;
       background:{_BG};color:{_WHITE};min-height:100vh;
       display:flex;align-items:center;justify-content:center;padding:24px}}
  .wrap{{width:100%;max-width:480px}}
  .logo{{display:flex;align-items:center;gap:10px;margin-bottom:28px;justify-content:center}}
  .logo-mark{{width:36px;height:36px;background:{_GREEN};border-radius:8px;
              display:flex;align-items:center;justify-content:center;
              font-size:18px;font-weight:900;color:{_DARK}}}
  .logo-text{{font-size:18px;font-weight:700}}
  .logo-text span{{color:{_GREEN}}}
  .card{{background:{_CARD};border:1px solid {_BORDER};border-radius:14px;padding:36px}}
  h1{{font-size:22px;font-weight:700;margin-bottom:8px;text-align:center}}
  .sub{{font-size:14px;color:{_SUB};text-align:center;margin-bottom:28px;line-height:1.5}}
  label{{display:block;font-size:12px;font-weight:600;text-transform:uppercase;
         letter-spacing:.6px;color:{_SUB};margin-bottom:7px}}
  input[type=password],input[type=text],select{{
    width:100%;padding:12px 14px;background:{_BG};border:1px solid {_BORDER};
    border-radius:8px;color:{_WHITE};font-size:14px;outline:none;
    transition:border-color .2s,box-shadow .2s;appearance:none;-webkit-appearance:none}}
  input[type=password],input[type=text]{{font-family:monospace}}
  select{{background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%234b5563' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
          background-repeat:no-repeat;background-position:right 14px center;padding-right:36px;cursor:pointer}}
  select option{{background:{_CARD}}}
  input:focus,select:focus{{border-color:{_GREEN};box-shadow:0 0 0 3px rgba(2,228,129,0.1)}}
  .hint{{font-size:12px;color:{_MUTED};margin-top:7px;line-height:1.5}}
  .hint a{{color:{_GREEN};text-decoration:none}}
  .btn{{width:100%;margin-top:20px;padding:13px;background:{_GREEN};
        border:none;border-radius:8px;color:{_DARK};font-size:15px;font-weight:700;
        cursor:pointer;transition:all .2s;box-shadow:0 0 20px rgba(2,228,129,0.2)}}
  .btn:hover{{background:#00c96e;box-shadow:0 0 30px rgba(2,228,129,0.35)}}
  .btn:disabled{{opacity:.4;cursor:not-allowed;box-shadow:none}}
  .btn-ghost{{width:100%;margin-top:12px;padding:11px;background:none;
              border:1px solid {_BORDER};border-radius:8px;color:{_SUB};
              font-size:14px;font-weight:600;cursor:pointer;transition:all .2s}}
  .btn-ghost:hover{{border-color:{_GREEN};color:{_GREEN}}}
  .error{{background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);
          border-radius:8px;padding:12px 14px;font-size:13px;color:#fca5a5;margin-bottom:20px}}
  .loading{{display:none;text-align:center;margin-top:16px;font-size:13px;color:{_MUTED}}}
  .dot{{display:inline-block;width:6px;height:6px;border-radius:50%;
        background:{_GREEN};margin:0 2px;animation:pulse 1.4s infinite}}
  .dot:nth-child(2){{animation-delay:.2s}}
  .dot:nth-child(3){{animation-delay:.4s}}
  @keyframes pulse{{0%,100%{{opacity:.3}}50%{{opacity:1}}}}
  .divider{{border:none;border-top:1px solid {_BORDER};margin:24px 0}}
  .section-label{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;
                  color:{_MUTED};margin-bottom:14px}}
  .new-form{{margin-top:4px}}
  .field{{margin-bottom:16px}}
  .client-list{{display:flex;flex-direction:column;gap:8px;margin-bottom:4px}}
  .client-row{{display:flex;align-items:center;gap:10px;padding:11px 14px;
               background:{_BG};border:1px solid {_BORDER};border-radius:8px;cursor:pointer;
               transition:all .15s;text-decoration:none}}
  .client-row:hover{{border-color:{_GREEN};background:rgba(2,228,129,0.04)}}
  .client-row-name{{flex:1;font-size:14px;font-weight:600;color:{_WHITE}}}
  .client-row-arrow{{color:{_MUTED};font-size:16px}}
  .features{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
  .feature{{background:{_BG};border:1px solid {_BORDER};border-radius:8px;
            padding:10px 12px;font-size:12px;color:{_SUB}}}
  .feature strong{{display:block;color:{_WHITE};font-size:12px;margin-bottom:2px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">
    <div class="logo-mark">U</div>
    <div class="logo-text">Understory <span>Deliverability</span></div>
  </div>
  <div class="card">
    <h1>Domain Health Audit</h1>

    {{% if error %}}<div class="error">{{{{ error }}}}</div>{{% endif %}}

    {{% if clients %}}
    <!-- ── Saved workspaces ── -->
    <p class="sub">Select a workspace to audit, or add a new client.</p>
    <div class="section-label">Saved Workspaces</div>
    <div class="client-list">
      {{% for c in clients %}}
      <form method="POST" action="/connect" style="margin:0" onsubmit="go(this)">
        <input type="hidden" name="action" value="select">
        <input type="hidden" name="client_name" value="{{{{ c }}}}">
        <button class="client-row" type="submit" style="width:100%;text-align:left">
          <span class="client-row-name">{{{{ c }}}}</span>
          <span class="client-row-arrow">›</span>
        </button>
      </form>
      {{% endfor %}}
    </div>

    <hr class="divider">
    <div id="toggleWrap">
      <button class="btn-ghost" onclick="showNew()" type="button">+ Add New Client</button>
    </div>
    <div id="newWrap" style="display:none">
      <div class="section-label">New Client</div>
      <form method="POST" action="/connect" class="new-form" onsubmit="go(this)">
        <input type="hidden" name="action" value="new">
        <div class="field">
          <label>Client Name</label>
          <input type="text" name="new_name" placeholder="e.g. acme-corp" required autocomplete="off">
        </div>
        <div class="field">
          <label>Instantly API Key (v2)</label>
          <input type="password" name="new_key" placeholder="Paste API key…" required autocomplete="off">
          <div class="hint">
            <a href="https://app.instantly.ai/app/settings/api" target="_blank">Instantly → Settings → API</a>
            — needs <code>accounts:read</code> + <code>analytics:read</code>
          </div>
        </div>
        <button class="btn" type="submit">Save &amp; Connect →</button>
        <div class="loading"><span class="dot"></span><span class="dot"></span><span class="dot"></span>&nbsp;Fetching domain data…</div>
      </form>
    </div>

    {{% else %}}
    <!-- ── First-time setup ── -->
    <p class="sub">Add your first workspace to get started.</p>
    <form method="POST" action="/connect" onsubmit="go(this)">
      <input type="hidden" name="action" value="new">
      <div class="field">
        <label>Client / Workspace Name</label>
        <input type="text" name="new_name" placeholder="e.g. acme-corp" required autocomplete="off">
      </div>
      <div class="field">
        <label>Instantly API Key (v2)</label>
        <input type="password" name="new_key" placeholder="Paste your v2 API key…" required autocomplete="off">
        <div class="hint">
          Get yours at <a href="https://app.instantly.ai/app/settings/api" target="_blank">Instantly → Settings → API</a>
          — needs <code>accounts:read</code> + <code>analytics:read</code>
        </div>
      </div>
      <button class="btn" type="submit">Save &amp; Connect →</button>
      <div class="loading"><span class="dot"></span><span class="dot"></span><span class="dot"></span>&nbsp;Fetching domain data — may take 15–60s for large workspaces</div>
    </form>
    <hr class="divider">
    <div class="features">
      <div class="feature"><strong>Warmup Score</strong>Threshold: ≥ 98 per mailbox</div>
      <div class="feature"><strong>Inbox Placement</strong>Threshold: ≥ 90%</div>
      <div class="feature"><strong>Bounce Rate</strong>Threshold: ≤ 2%</div>
      <div class="feature"><strong>Burned Domains</strong>≥ 20% bad mailboxes</div>
    </div>
    {{% endif %}}
  </div>
</div>
<script>
function showNew(){{
  document.getElementById('toggleWrap').style.display='none';
  document.getElementById('newWrap').style.display='block';
  document.querySelector('#newWrap input[name=new_name]').focus();
}}
function go(form){{
  const btn = form.querySelector('button[type=submit]');
  if(btn){{btn.disabled=true; btn.textContent='Connecting…';}}
  const ld = form.querySelector('.loading');
  if(ld) ld.style.display='block';
}}
</script>
</body>
</html>"""


DASHBOARD_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deliverability Audit — Understory</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Inter',sans-serif;
        background:{_BG};color:{_WHITE};font-size:14px;min-height:100vh}}

  /* ── Header ── */
  .hdr{{background:{_CARD};border-bottom:1px solid {_BORDER};height:58px;
        padding:0 28px;display:flex;align-items:center;justify-content:space-between;
        position:sticky;top:0;z-index:20}}
  .logo{{display:flex;align-items:center;gap:10px}}
  .logo-mark{{width:30px;height:30px;background:{_GREEN};border-radius:7px;
              display:flex;align-items:center;justify-content:center;
              font-size:15px;font-weight:900;color:{_DARK}}}
  .logo-name{{font-size:15px;font-weight:700}}
  .logo-name span{{color:{_GREEN}}}
  .hdr-right{{display:flex;align-items:center;gap:12px}}
  .timestamp{{font-size:12px;color:{_MUTED}}}
  .btn-refresh{{background:transparent;border:1px solid {_BORDER};border-radius:7px;
                color:{_SUB};padding:6px 14px;font-size:13px;cursor:pointer;
                transition:all .2s}}
  .btn-refresh:hover{{border-color:{_GREEN};color:{_GREEN}}}
  .btn-dc{{background:none;border:none;color:{_MUTED};font-size:12px;
           cursor:pointer;padding:4px 8px}}
  .btn-dc:hover{{color:{_RED}}}

  /* ── Status cards ── */
  .cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;padding:24px 28px 0}}
  .card{{background:{_CARD};border:1px solid {_BORDER};border-radius:12px;
         padding:20px 22px;position:relative;overflow:hidden}}
  .card-glow{{box-shadow:0 0 30px rgba(2,228,129,0.12)}}
  .card::after{{content:'';position:absolute;top:0;left:0;right:0;height:3px;border-radius:12px 12px 0 0}}
  .card-burned::after{{background:{_RED}}}
  .card-risk::after{{background:{_ORANGE}}}
  .card-watch::after{{background:{_AMBER}}}
  .card-healthy::after{{background:{_GREEN}}}
  .card-label{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;
               color:{_MUTED};margin-bottom:10px}}
  .card-value{{font-size:32px;font-weight:800;line-height:1}}
  .c-burned{{color:{_RED}}}
  .c-risk{{color:{_ORANGE}}}
  .c-watch{{color:{_AMBER}}}
  .c-healthy{{color:{_GREEN}}}
  .c-white{{color:{_WHITE}}}
  .card-sub{{font-size:12px;color:{_MUTED};margin-top:6px}}
  .card-action{{font-size:11px;font-weight:700;text-transform:uppercase;
                letter-spacing:.5px;margin-top:8px}}
  .ca-burned{{color:{_RED}}}
  .ca-risk{{color:{_ORANGE}}}
  .ca-watch{{color:{_AMBER}}}
  .ca-healthy{{color:{_GREEN}}}

  /* ── Workspace stats bar ── */
  .ws-bar{{margin:20px 28px 0;background:{_CARD};border:1px solid {_BORDER};
           border-radius:12px;padding:16px 24px;display:flex;gap:0;overflow:hidden}}
  .ws-stat{{flex:1;padding:0 20px;position:relative}}
  .ws-stat+.ws-stat::before{{content:'';position:absolute;left:0;top:15%;
                              height:70%;width:1px;background:{_BORDER}}}
  .ws-stat:first-child{{padding-left:0}}
  .ws-label{{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;
             color:{_MUTED};margin-bottom:4px}}
  .ws-value{{font-size:20px;font-weight:700;color:{_WHITE}}}
  .ws-sub{{font-size:11px;color:{_MUTED};margin-top:2px}}
  .ws-rate-good{{color:{_GREEN}}}
  .ws-rate-warn{{color:{_AMBER}}}
  .ws-rate-bad{{color:{_RED}}}

  /* ── Signals legend ── */
  .legend{{margin:16px 28px 0;display:flex;gap:8px;flex-wrap:wrap}}
  .leg{{background:{_CARD};border:1px solid {_BORDER};border-radius:6px;
        padding:5px 12px;font-size:11px;color:{_SUB};white-space:nowrap}}
  .leg strong{{color:{_WHITE};font-weight:600}}

  /* ── Table ── */
  .tbl-wrap{{margin:16px 28px 32px;background:{_CARD};border:1px solid {_BORDER};
             border-radius:12px;overflow:hidden}}
  .tbl-top{{padding:16px 20px 14px;display:flex;align-items:center;
            justify-content:space-between;border-bottom:1px solid {_BORDER}}}
  .tbl-title{{font-size:14px;font-weight:700;color:{_WHITE}}}
  .tbl-right{{display:flex;align-items:center;gap:10px}}
  .filters{{display:flex;gap:6px}}
  .fb{{background:none;border:1px solid {_BORDER};border-radius:6px;
       color:{_MUTED};padding:5px 12px;font-size:12px;cursor:pointer;transition:all .15s}}
  .fb:hover{{background:{_CARD2};color:{_WHITE}}}
  .fb.active{{background:{_CARD2};color:{_WHITE};border-color:#404754}}
  .fb.fb-burned.active{{border-color:rgba(239,68,68,.4);color:{_RED}}}
  .fb.fb-risk.active{{border-color:rgba(249,115,22,.4);color:{_ORANGE}}}
  .fb.fb-watch.active{{border-color:rgba(245,158,11,.4);color:{_AMBER}}}
  .fb.fb-healthy.active{{border-color:rgba(2,228,129,.4);color:{_GREEN}}}
  .search-wrap{{position:relative}}
  .search-wrap input{{background:{_BG};border:1px solid {_BORDER};border-radius:7px;
                      color:{_WHITE};padding:7px 12px 7px 30px;font-size:13px;
                      width:200px;outline:none;transition:border-color .2s}}
  .search-wrap input:focus{{border-color:{_GREEN}}}
  .si{{position:absolute;left:9px;top:50%;transform:translateY(-50%);
       color:{_MUTED};font-size:12px;pointer-events:none}}
  table{{width:100%;border-collapse:collapse}}
  th{{text-align:left;padding:10px 16px;font-size:11px;font-weight:700;
      text-transform:uppercase;letter-spacing:.6px;color:{_MUTED};
      background:#111519;border-bottom:1px solid {_BORDER};cursor:pointer;
      white-space:nowrap;user-select:none}}
  th:hover{{color:{_SUB}}}
  th.sorted{{color:{_GREEN}}}
  td{{padding:12px 16px;border-bottom:1px solid rgba(42,46,54,.6);vertical-align:middle}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:rgba(255,255,255,.02)}}
  .domain-name{{font-family:'SF Mono',SFMono-Regular,Consolas,monospace;
                font-size:13px;font-weight:500;color:{_WHITE}}}
  .mailbox-count{{font-size:12px;color:{_MUTED};margin-top:2px}}
  .na{{color:#2a2e36}}

  /* Warmup bar */
  .bar-row{{display:flex;align-items:center;gap:8px}}
  .bar{{height:4px;border-radius:2px;background:#1e2329;width:50px;flex-shrink:0}}
  .bar-fill{{height:100%;border-radius:2px}}

  /* Status badges */
  .badge{{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;
          border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.3px;
          white-space:nowrap}}
  .b-burned {{background:rgba(239,68,68,.12); color:{_RED};   border:1px solid rgba(239,68,68,.2)}}
  .b-risk   {{background:rgba(249,115,22,.12);color:{_ORANGE};border:1px solid rgba(249,115,22,.2)}}
  .b-watch  {{background:rgba(245,158,11,.12);color:{_AMBER}; border:1px solid rgba(245,158,11,.2)}}
  .b-healthy{{background:rgba(2,228,129,.1);  color:{_GREEN}; border:1px solid rgba(2,228,129,.2)}}
  .b-nodata {{background:{_CARD2};color:{_MUTED};border:1px solid {_BORDER}}}

  /* Action chips */
  .action-chip{{display:inline-block;padding:3px 9px;border-radius:5px;
                font-size:11px;font-weight:700;letter-spacing:.4px;white-space:nowrap}}
  .ac-burned {{background:rgba(239,68,68,.15); color:{_RED}}}
  .ac-risk   {{background:rgba(249,115,22,.15);color:{_ORANGE}}}
  .ac-watch  {{background:rgba(245,158,11,.15);color:{_AMBER}}}
  .ac-healthy{{background:rgba(2,228,129,.12); color:{_GREEN}}}
  .ac-nodata {{background:{_CARD2};            color:{_MUTED}}}

  .issues{{font-size:12px;color:{_MUTED};line-height:1.5}}
  .no-results{{padding:40px;text-align:center;color:{_MUTED}}}

  /* ── Period pills ── */
  .period-pills{{display:flex;gap:4px;align-items:center}}
  .pill{{padding:5px 11px;border-radius:6px;font-size:12px;font-weight:600;
         border:1px solid {_BORDER};color:{_MUTED};text-decoration:none;
         transition:all .15s;cursor:pointer}}
  .pill:hover{{background:{_CARD2};color:{_WHITE}}}
  .pill-active{{background:{_CARD2};border-color:{_GREEN};color:{_GREEN} !important}}
  .period-divider{{width:1px;height:18px;background:{_BORDER};margin:0 4px}}

  /* ── Domain tabs ── */
  .tab-bar{{display:flex;gap:0;border-bottom:1px solid {_BORDER};padding:0 20px}}
  .tab{{background:none;border:none;border-bottom:2px solid transparent;
        color:{_MUTED};padding:12px 16px;font-size:13px;font-weight:600;
        cursor:pointer;transition:all .15s;margin-bottom:-1px;white-space:nowrap}}
  .tab:hover{{color:{_WHITE}}}
  .tab.active{{color:{_WHITE};border-bottom-color:{_WHITE}}}
  .tab.tab-burned.active{{color:{_RED};border-bottom-color:{_RED}}}
  .tab.tab-risk.active{{color:{_ORANGE};border-bottom-color:{_ORANGE}}}
  .tab.tab-watch.active{{color:{_AMBER};border-bottom-color:{_AMBER}}}
  .tab.tab-healthy.active{{color:{_GREEN};border-bottom-color:{_GREEN}}}
  .tab-count{{display:inline-block;padding:1px 6px;border-radius:10px;
              font-size:10px;font-weight:700;margin-left:5px;
              background:rgba(255,255,255,.08);color:{_MUTED}}}
  .tab-burned .tab-count{{background:rgba(239,68,68,.15);color:{_RED}}}
  .tab-risk   .tab-count{{background:rgba(249,115,22,.15);color:{_ORANGE}}}
  .tab-watch  .tab-count{{background:rgba(245,158,11,.15);color:{_AMBER}}}
  .tab-healthy .tab-count{{background:rgba(2,228,129,.12);color:{_GREEN}}}

  /* Empty state */
  .empty{{padding:60px;text-align:center}}
  .empty-icon{{font-size:40px;margin-bottom:12px}}
  .empty-text{{color:{_MUTED};font-size:14px}}

  /* Scrollbar */
  ::-webkit-scrollbar{{width:6px;height:6px}}
  ::-webkit-scrollbar-track{{background:{_BG}}}
  ::-webkit-scrollbar-thumb{{background:{_BORDER};border-radius:3px}}
</style>
</head>
<body>

<!-- Header -->
<div class="hdr">
  <div class="logo">
    <div class="logo-mark">U</div>
    <div class="logo-name">Understory <span>Deliverability</span></div>
  </div>
  <div class="hdr-right">
    {{% if client_name %}}<span style="font-size:13px;font-weight:600;color:{_SUB}">{{{{ client_name }}}}</span><div class="period-divider"></div>{{% endif %}}
    <div class="period-pills">
      <span style="font-size:11px;color:{_MUTED};font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-right:4px">Period</span>
      <a href="/dashboard?period=7"   class="pill {{{{ 'pill-active' if period=='7'    else '' }}}}">7d</a>
      <a href="/dashboard?period=14"  class="pill {{{{ 'pill-active' if period=='14'   else '' }}}}">14d</a>
      <a href="/dashboard?period=30"  class="pill {{{{ 'pill-active' if period=='30'   else '' }}}}">30d</a>
      <a href="/dashboard?period=all" class="pill {{{{ 'pill-active' if period=='all'  else '' }}}}">All</a>
    </div>
    <div class="period-divider"></div>
    <span class="timestamp">Audited {{{{ fetched_at }}}}</span>
    <form method="POST" action="/dashboard" style="display:inline">
      <input type="hidden" name="api_key" value="{{{{ api_key }}}}">
      <input type="hidden" name="period"  value="{{{{ period }}}}">
      <button class="btn-refresh" type="submit">&#8635; Refresh</button>
    </form>
    <form method="POST" action="/disconnect" style="display:inline">
      <button class="btn-dc" type="submit">Disconnect</button>
    </form>
  </div>
</div>

<!-- Summary cards -->
<div class="cards">
  <div class="card card-burned">
    <div class="card-label">Burned</div>
    <div class="card-value c-burned">{{{{ burned }}}}</div>
    <div class="card-sub">{{{{ burned }}}} domain{{{{ 's' if burned != 1 else '' }}}} to replace</div>
    <div class="card-action ca-burned">{{{{ 'REPLACE NOW' if burned > 0 else 'NONE' }}}}</div>
  </div>
  <div class="card card-risk">
    <div class="card-label">At Risk</div>
    <div class="card-value c-risk">{{{{ atrisk }}}}</div>
    <div class="card-sub">Reduce send volume</div>
    <div class="card-action ca-risk">{{{{ 'REDUCE VOLUME' if atrisk > 0 else 'NONE' }}}}</div>
  </div>
  <div class="card card-watch">
    <div class="card-label">Watch</div>
    <div class="card-value c-watch">{{{{ watch }}}}</div>
    <div class="card-sub">Monitor weekly</div>
    <div class="card-action ca-watch">{{{{ 'MONITOR' if watch > 0 else 'NONE' }}}}</div>
  </div>
  <div class="card card-healthy card-glow">
    <div class="card-label">Healthy</div>
    <div class="card-value c-healthy">{{{{ healthy }}}}</div>
    <div class="card-sub">{{{{ total_mailboxes }}}} total mailboxes</div>
    <div class="card-action ca-healthy">KEEP SENDING</div>
  </div>
</div>

<!-- Workspace stats bar -->
{{% if ws %}}<div class="ws-bar">
  <div class="ws-stat">
    <div class="ws-label">Emails Sent</div>
    <div class="ws-value">{{{{ ws.total_sent | fmt_num }}}}</div>
    <div class="ws-sub">all campaigns</div>
  </div>
  <div class="ws-stat">
    <div class="ws-label">Real Replies</div>
    <div class="ws-value">{{{{ ws.reply_count | fmt_num }}}}</div>
    <div class="ws-sub">auto-replies excluded</div>
  </div>
  <div class="ws-stat">
    <div class="ws-label">Reply Rate</div>
    {{%- set rr = (ws.reply_count / ws.total_sent * 100) if ws.total_sent else None %}}
    <div class="ws-value {{{{ 'ws-rate-good' if rr and rr >= 2 else 'ws-rate-warn' if rr and rr >= 1 else 'ws-rate-bad' if rr else '' }}}}">
      {{{{ '%.2f'|format(rr) + '%' if rr is not none else 'N/A' }}}}
    </div>
    <div class="ws-sub">baseline ≥ 1%</div>
  </div>
  <div class="ws-stat">
    <div class="ws-label">Bounces</div>
    <div class="ws-value">{{{{ ws.total_bounced | fmt_num }}}}</div>
    <div class="ws-sub">all campaigns</div>
  </div>
  <div class="ws-stat">
    <div class="ws-label">Bounce Rate</div>
    {{%- set br = (ws.total_bounced / ws.total_sent * 100) if ws.total_sent else None %}}
    <div class="ws-value {{{{ 'ws-rate-good' if br and br <= 0.5 else 'ws-rate-warn' if br and br <= 2 else 'ws-rate-bad' if br else '' }}}}">
      {{{{ '%.2f'|format(br) + '%' if br is not none else 'N/A' }}}}
    </div>
    <div class="ws-sub">threshold ≤ 2%</div>
  </div>
  <div class="ws-stat">
    <div class="ws-label">Data Window</div>
    <div class="ws-value" style="font-size:15px">{{{{ period_label }}}}</div>
    <div class="ws-sub">bounce &amp; reply rates</div>
  </div>
</div>{{% endif %}}

<!-- Thresholds legend -->
<div class="legend">
  <div class="leg">Warmup: <strong>&lt; {WARMUP_THRESHOLD}</strong> = flagged mailbox</div>
  <div class="leg">Burned: <strong>≥ {int(DOMAIN_BURNED_PCT*100)}%</strong> mailboxes below threshold</div>
  <div class="leg">Inbox placement: <strong>&lt; {int(INBOX_WATCH)}%</strong> = Watch · <strong>&lt; {int(INBOX_ATRISK)}%</strong> = At Risk · <strong>&lt; {int(INBOX_BURNED)}%</strong> = Burned</div>
  <div class="leg">Bounce rate: <strong>&gt; {BOUNCE_WATCH}%</strong> = Watch · <strong>&gt; {BOUNCE_ATRISK}%</strong> = At Risk · <strong>&gt; {BOUNCE_BURNED}%</strong> = Burned</div>
  <div class="leg">Reply rate (manual + OOO): <strong>&lt; {REPLY_WATCH}%</strong> = Watch · <strong>&lt; {REPLY_ATRISK}%</strong> = At Risk &mdash; OOO auto-replies count as positive inbox signal</div>
</div>

<!-- Domain table -->
<div class="tbl-wrap">
  <div class="tab-bar">
    <button class="tab active"        onclick="setFilter('all',this)">All <span class="tab-count">{{{{ total }}}}</span></button>
    <button class="tab tab-burned"    onclick="setFilter('Burned',this)">Replace Now <span class="tab-count">{{{{ burned }}}}</span></button>
    <button class="tab tab-risk"      onclick="setFilter('At Risk',this)">At Risk <span class="tab-count">{{{{ atrisk }}}}</span></button>
    <button class="tab tab-watch"     onclick="setFilter('Watch',this)">Watch <span class="tab-count">{{{{ watch }}}}</span></button>
    <button class="tab tab-healthy"   onclick="setFilter('Healthy',this)">Healthy <span class="tab-count">{{{{ healthy }}}}</span></button>
    <div style="flex:1"></div>
    <div class="search-wrap" style="margin:8px 0">
      <span class="si">&#128269;</span>
      <input type="text" id="search" placeholder="Search domains…" oninput="applyFilters()">
    </div>
  </div>
  <table id="tbl">
    <thead>
      <tr>
        <th onclick="sortBy(0)">Domain</th>
        <th onclick="sortBy(1)">Mailboxes</th>
        <th onclick="sortBy(2)">Warmup Avg</th>
        <th onclick="sortBy(3)">Bad Mailboxes</th>
        <th onclick="sortBy(4)">Inbox Placement</th>
        <th onclick="sortBy(5)">Bounce Rate</th>
        <th onclick="sortBy(6)">Reply Rate</th>
        <th onclick="sortBy(7)" class="sorted">Status</th>
        <th onclick="sortBy(8)">Recommendation</th>
        <th>Issues</th>
      </tr>
    </thead>
    <tbody>
    {{% for d in domains %}}
    <tr data-status="{{{{ d.status }}}}">
      <td>
        <div class="domain-name">{{{{ d.domain }}}}</div>
        <div class="mailbox-count">{{{{ d.mailbox_count }}}} mailbox{{{{ 'es' if d.mailbox_count != 1 else '' }}}}</div>
      </td>
      <td>{{{{ d.mailbox_count }}}}</td>
      <td>
        {{% if d.warmup_avg is not none %}}
          {{%- set wc = '#02E481' if d.warmup_avg >= 98 else '#f59e0b' if d.warmup_avg >= 90 else '#ef4444' %}}
          <div class="bar-row">
            <span style="color:{{{{ wc }}}};font-weight:600">{{{{ d.warmup_avg }}}}</span>
            <div class="bar"><div class="bar-fill" style="width:{{{{ d.warmup_avg }}}}%;background:{{{{ wc }}}}"></div></div>
          </div>
        {{% else %}}<span class="na">&mdash;</span>
        {{% endif %}}
      </td>
      <td>
        {{% if d.bad_warmup_count > 0 %}}
          <span style="color:{_RED};font-weight:600">{{{{ d.bad_warmup_count }}}} / {{{{ d.mailbox_count }}}}</span>
        {{% else %}}
          <span style="color:{_GREEN}">0 / {{{{ d.mailbox_count }}}}</span>
        {{% endif %}}
      </td>
      <td>
        {{% if d.inbox_placement_pct is not none %}}
          {{%- set ic = '#02E481' if d.inbox_placement_pct >= 90 else '#f59e0b' if d.inbox_placement_pct >= 80 else '#f97316' if d.inbox_placement_pct >= 60 else '#ef4444' %}}
          <span style="color:{{{{ ic }}}};font-weight:600">{{{{ d.inbox_placement_pct }}}}%</span>
        {{% else %}}<span class="na">&mdash;</span>
        {{% endif %}}
      </td>
      <td>
        {{% if d.bounce_rate_pct is not none and d.total_sent >= {MIN_SEND_THRESHOLD} %}}
          {{%- set bc = '#02E481' if d.bounce_rate_pct <= 0.5 else '#f59e0b' if d.bounce_rate_pct <= 1 else '#f97316' if d.bounce_rate_pct <= 2 else '#ef4444' %}}
          <span style="color:{{{{ bc }}}};font-weight:600">{{{{ d.bounce_rate_pct }}}}%</span>
        {{% else %}}<span class="na">&mdash;</span>
        {{% endif %}}
      </td>
      <td>
        {{% if d.reply_rate_pct is not none %}}
          {{%- set rc = '#02E481' if d.reply_rate_pct >= {REPLY_WATCH} else '#f59e0b' if d.reply_rate_pct >= {REPLY_ATRISK} else '#ef4444' %}}
          <span style="color:{{{{ rc }}}};font-weight:600">{{{{ d.reply_rate_pct }}}}%</span>
          {{% if d.total_replies_auto > 0 %}}
            <div style="font-size:11px;color:#64748b;margin-top:2px">{{{{ d.total_replies_auto }}}} OOO</div>
          {{% endif %}}
        {{% else %}}<span class="na">&mdash;</span>
        {{% endif %}}
      </td>
      <td>
        {{% if d.status == 'Burned' %}}<span class="badge b-burned">&#9679; Burned</span>
        {{% elif d.status == 'At Risk' %}}<span class="badge b-risk">&#9679; At Risk</span>
        {{% elif d.status == 'Watch' %}}<span class="badge b-watch">&#9679; Watch</span>
        {{% elif d.status == 'Healthy' %}}<span class="badge b-healthy">&#9679; Healthy</span>
        {{% else %}}<span class="badge b-nodata">&#9679; No Data</span>
        {{% endif %}}
      </td>
      <td>
        {{% if d.status == 'Burned' %}}<span class="action-chip ac-burned">Replace Now</span>
        {{% elif d.status == 'At Risk' %}}<span class="action-chip ac-risk">Reduce Volume</span>
        {{% elif d.status == 'Watch' %}}<span class="action-chip ac-watch">Monitor</span>
        {{% elif d.status == 'Healthy' %}}<span class="action-chip ac-healthy">Keep Sending</span>
        {{% else %}}<span class="action-chip ac-nodata">Await Data</span>
        {{% endif %}}
      </td>
      <td>
        {{% if d.issues %}}
          <div class="issues">{{{{ d.issues | join(' &middot; ') }}}}</div>
        {{% else %}}<span class="na">&mdash;</span>
        {{% endif %}}
      </td>
    </tr>
    {{% endfor %}}
    </tbody>
  </table>
  <div class="no-results" id="noResults" style="display:none">
    No domains match the current filter.
  </div>
</div>

<script>
let activeFilter = 'all';

function setFilter(f, btn) {{
  activeFilter = f;
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  applyFilters();
}}

function applyFilters() {{
  const q = document.getElementById('search').value.toLowerCase();
  let vis = 0;
  document.querySelectorAll('#tbl tbody tr').forEach(row => {{
    const st = row.dataset.status;
    const dm = row.querySelector('.domain-name').textContent.toLowerCase();
    const show = (activeFilter === 'all' || st === activeFilter) && dm.includes(q);
    row.style.display = show ? '' : 'none';
    if (show) vis++;
  }});
  document.getElementById('noResults').style.display = vis ? 'none' : 'block';
}}

let sortState = {{}};
function sortBy(col) {{
  const tbl = document.getElementById('tbl');
  tbl.querySelectorAll('th').forEach((th, i) => th.classList.toggle('sorted', i === col));
  sortState[col] = sortState[col] === 'asc' ? 'desc' : 'asc';
  const asc = sortState[col] === 'asc';
  const rows = Array.from(tbl.querySelectorAll('tbody tr'));
  rows.sort((a, b) => {{
    const av = a.cells[col]?.textContent.trim().replace('%','') || '';
    const bv = b.cells[col]?.textContent.trim().replace('%','') || '';
    const an = parseFloat(av), bn = parseFloat(bv);
    const cmp = (!isNaN(an) && !isNaN(bn)) ? an - bn : av.localeCompare(bv);
    return asc ? cmp : -cmp;
  }});
  const tbody = tbl.querySelector('tbody');
  rows.forEach(r => tbody.appendChild(r));
}}
</script>
</body>
</html>"""


# ── Jinja filters ─────────────────────────────────────────────────────────────

@app.template_filter("fmt_num")
def fmt_num(v):
    if v is None or v == "N/A":
        return "N/A"
    try:
        return f"{int(v):,}"
    except (ValueError, TypeError):
        return str(v)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    if session.get("api_key"):
        return redirect(url_for("dashboard_get"))
    return render_template_string(LOGIN_HTML, clients=list_clients(), error=None)


@app.route("/connect", methods=["POST"])
def connect():
    action = request.form.get("action", "new")

    if action == "select":
        name = request.form.get("client_name", "").strip()
        key  = get_client_key(name)
        if not key:
            return render_template_string(LOGIN_HTML, clients=list_clients(),
                                          error=f"Client '{name}' not found.")
        touch_client(name)
        session["api_key"]      = key
        session["client_name"]  = name
        session["period"]       = "30"
        return redirect(url_for("dashboard_get"))

    # action == "new"
    name = request.form.get("new_name", "").strip()
    key  = request.form.get("new_key",  "").strip()
    if not name or not key:
        return render_template_string(LOGIN_HTML, clients=list_clients(),
                                      error="Both a client name and API key are required.")
    save_client(name, key)
    session["api_key"]      = key
    session["client_name"]  = name
    session["period"]       = "30"
    return redirect(url_for("dashboard_get"))


@app.route("/dashboard", methods=["GET"])
def dashboard_get():
    key = session.get("api_key")
    if not key:
        return redirect(url_for("index"))
    period = request.args.get("period", session.get("period", "30"))
    session["period"] = period
    return _render(key, period)


@app.route("/dashboard", methods=["POST"])
def dashboard_post():
    key = request.form.get("api_key", "").strip()
    period = request.form.get("period", "30")
    if not key:
        return render_template_string(LOGIN_HTML, clients=list_clients(), error="API key is required.")
    session["api_key"] = key
    session["period"]  = period
    return _render(key, period)


@app.route("/disconnect", methods=["POST"])
def disconnect():
    session.clear()
    return redirect(url_for("index"))


def _render(key, period="30"):
    try:
        accounts = fetch_accounts(key)
    except ValueError as e:
        session.clear()
        return render_template_string(LOGIN_HTML, error=str(e))
    except Exception as e:
        session.clear()
        return render_template_string(LOGIN_HTML, error=f"Connection failed: {e}")

    days         = None if period == "all" else int(period)
    emails       = [a["email"] for a in accounts if a.get("email")]
    warmup_stats = fetch_warmup_analytics(key, emails)
    daily_stats  = fetch_daily_analytics(key, days=days)
    ws           = fetch_workspace_stats(key)

    domains = build_domain_metrics(accounts, warmup_stats, daily_stats)

    counts = defaultdict(int)
    for d in domains:
        counts[d["status"]] += 1

    period_label = "All time" if period == "all" else f"Last {period} days"

    return render_template_string(
        DASHBOARD_HTML,
        domains=domains,
        total=len(domains),
        total_mailboxes=len(accounts),
        burned=counts["Burned"],
        atrisk=counts["At Risk"],
        watch=counts["Watch"],
        healthy=counts["Healthy"],
        ws=ws if ws.get("total_sent") else None,
        api_key=key,
        fetched_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        period=period,
        period_label=period_label,
        client_name=session.get("client_name", ""),
    )


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    print("\n  Understory Deliverability Dashboard")
    print(f"  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
