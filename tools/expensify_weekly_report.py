#!/usr/bin/env python3
"""
Expensify Instantly Weekly Email Report

Fetches daily analytics from the Expensify Instantly workspace,
aggregates into weekly buckets (Jan 1 onwards), and writes to Google Sheets.

Schedule: Every Monday at 7am EST via cron.
Usage: python tools/expensify_weekly_report.py
"""

import os
import requests
import gspread
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY         = os.getenv("EXPENSIFY_INSTANTLY_API_KEY")
SHEET_ID        = os.getenv("EXPENSIFY_REPORT_SHEET_ID")
SERVICE_ACCOUNT = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON",
                            str(Path(__file__).resolve().parent.parent / "service_account.json"))
BASE_URL        = "https://api.instantly.ai/api/v2"
YEAR_START      = date(2026, 1, 1)
SCOPES          = ["https://www.googleapis.com/auth/spreadsheets"]

# ── Instantly API ─────────────────────────────────────────────────────────────
def fetch_daily(start: date, end: date) -> list[dict]:
    resp = requests.get(
        f"{BASE_URL}/campaigns/analytics/daily",
        headers={"Authorization": f"Bearer {API_KEY}"},
        params={"start_date": start.isoformat(), "end_date": end.isoformat()},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


# ── Weekly bucketing ──────────────────────────────────────────────────────────
def week_number(d: date) -> int:
    """
    Week 1 = Jan 1-4 (partial first week)
    Week 2 = Jan 5-11, Week 3 = Jan 12-18, etc.
    """
    delta = (d - YEAR_START).days
    return 1 if delta < 4 else ((delta - 4) // 7) + 2


def week_date_range(w: int) -> str:
    if w == 1:
        return "Jan 01 - Jan 04"
    start = YEAR_START + timedelta(days=4 + (w - 2) * 7)
    end   = start + timedelta(days=6)
    return f"{start.strftime('%b %d')} - {end.strftime('%b %d')}"


def aggregate_weekly(daily: list[dict]) -> list[dict]:
    weeks = {}
    for row in daily:
        w = week_number(date.fromisoformat(row["date"]))
        if w not in weeks:
            weeks[w] = {"sent": 0, "human_replies": 0, "ooo_replies": 0, "positive_replies": 0}
        weeks[w]["sent"]             += row["sent"]
        weeks[w]["human_replies"]    += row["unique_replies"]
        weeks[w]["ooo_replies"]      += row["unique_replies_automatic"]
        weeks[w]["positive_replies"] += row["unique_opportunities"]

    result = []
    for w in sorted(weeks):
        d = weeks[w]
        s = d["sent"]
        r = d["human_replies"]
        a = d["ooo_replies"]
        o = d["positive_replies"]
        t = r + a
        result.append({
            "week":             f"Week {w}",
            "date_range":       week_date_range(w),
            "emails_sent":      s,
            "human_replies":    r,
            "ooo_replies":      a,
            "total_replies":    t,
            "positive_replies": o,
            "human_reply_rate": f"{r/s*100:.2f}%" if s else "N/A",
            "total_reply_rate": f"{t/s*100:.2f}%" if s else "N/A",
            "positive_rate":    f"{o/s*100:.2f}%" if s else "N/A",
        })
    return result


# ── Google Sheets ─────────────────────────────────────────────────────────────
HEADERS = [
    "Week", "Date Range", "Emails Sent", "Human Replies", "OOO / Auto-Replies",
    "Total Replies (incl. OOO)", "Positive Replies (Opps)",
    "Human Reply Rate", "Total Reply Rate (incl. OOO)", "Positive Reply Rate",
]


def write_to_sheet(weekly: list[dict]):
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT, scopes=SCOPES)
    gc    = gspread.authorize(creds)
    sh    = gc.open_by_key(SHEET_ID)

    try:
        ws = sh.worksheet("Weekly Report")
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet("Weekly Report", rows=200, cols=12)

    today = date.today()
    rows  = [
        [f"Expensify — Instantly Weekly Report  |  Updated: {today.strftime('%b %d, %Y')}"],
        [f"Period: {YEAR_START.strftime('%b %d, %Y')} – {today.strftime('%b %d, %Y')}"],
        [],
        HEADERS,
    ]

    total = {"emails_sent": 0, "human_replies": 0, "ooo_replies": 0,
             "total_replies": 0, "positive_replies": 0}

    for w in weekly:
        rows.append([
            w["week"], w["date_range"], w["emails_sent"], w["human_replies"],
            w["ooo_replies"], w["total_replies"], w["positive_replies"],
            w["human_reply_rate"], w["total_reply_rate"], w["positive_rate"],
        ])
        for k in total:
            total[k] += w[k]

    s = total["emails_sent"]
    r = total["human_replies"]
    t = total["total_replies"]
    o = total["positive_replies"]
    rows.append([
        "TOTAL",
        f"{YEAR_START.strftime('%b %d')} – {today.strftime('%b %d')}",
        s, r, total["ooo_replies"], t, o,
        f"{r/s*100:.2f}%" if s else "N/A",
        f"{t/s*100:.2f}%" if s else "N/A",
        f"{o/s*100:.2f}%" if s else "N/A",
    ])

    ws.update(rows, "A1")

    # Bold header row (row 4)
    ws.format("A4:J4", {"textFormat": {"bold": True}})
    # Bold TOTAL row
    total_row = len(rows)
    ws.format(f"A{total_row}:J{total_row}", {"textFormat": {"bold": True}})

    print(f"Sheet updated: {len(weekly)} weeks written.")
    print(f"Total sent: {s:,} | Replies: {r:,} | Total (incl OOO): {t:,} | Positive: {o:,}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not API_KEY:
        raise RuntimeError("EXPENSIFY_INSTANTLY_API_KEY not set in .env")
    if not SHEET_ID:
        raise RuntimeError("EXPENSIFY_REPORT_SHEET_ID not set in .env")
    if not Path(SERVICE_ACCOUNT).exists():
        raise RuntimeError(f"Service account JSON not found: {SERVICE_ACCOUNT}")

    today = date.today()
    print(f"Fetching data: {YEAR_START} to {today}")
    daily  = fetch_daily(YEAR_START, today)
    weekly = aggregate_weekly(daily)
    write_to_sheet(weekly)


if __name__ == "__main__":
    main()
