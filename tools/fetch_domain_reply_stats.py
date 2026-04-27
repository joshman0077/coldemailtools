#!/usr/bin/env python3
"""
Fetch Domain Reply Stats

Pulls actual campaign sending performance per domain from Instantly API v2.
Ignores warmup data entirely. Only counts real campaign emails and replies.

Fixes applied:
  - Mailbox counts from accounts.json (current accounts only)
  - Uses 'eaccount' field for reply attribution (avoids parsing bugs)
  - Auto-reply correction using campaign-level real/auto ratio
  - Only reports domains with current mailboxes (no junk entries)

Output: .tmp/domain_reply_stats.csv — sorted by reply rate ascending (worst first)

Usage:
    python tools/fetch_domain_reply_stats.py
"""

import csv
import json
import sys
import time
import logging
from pathlib import Path
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from instantly_client import get, get_paginated, ensure_tmp_dir

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("instantly")


def load_current_accounts() -> dict:
    """Load accounts.json and return {email: account_data} for current accounts."""
    path = ensure_tmp_dir() / "accounts.json"
    if not path.exists():
        raise FileNotFoundError(
            "accounts.json not found. Run fetch_instantly_accounts.py first."
        )
    with open(path, "r", encoding="utf-8") as f:
        accounts = json.load(f)

    account_map = {}
    for acct in accounts:
        email = acct.get("email", "").lower()
        if email and "@" in email:
            account_map[email] = acct
    return account_map


def get_auto_reply_ratio() -> float:
    """Calculate the real reply ratio from campaign analytics.

    Returns the fraction of replies that are real (not auto/OOO).
    Falls back to 1.0 if campaign data is unavailable.
    """
    path = ensure_tmp_dir() / "campaigns.json"
    if not path.exists():
        logger.warning("campaigns.json not found — cannot correct for auto-replies")
        return 1.0

    with open(path, "r", encoding="utf-8") as f:
        campaigns = json.load(f)

    analytics = campaigns.get("analytics", [])
    total_real = sum((a.get("reply_count", 0) or 0) for a in analytics)
    total_auto = sum((a.get("reply_count_automatic", 0) or 0) for a in analytics)
    total = total_real + total_auto

    if total == 0:
        return 1.0

    ratio = total_real / total
    logger.info(f"Auto-reply correction: {total_real} real / {total_auto} auto = {ratio:.1%} real ratio")
    return ratio


def fetch_daily_account_analytics() -> list:
    """Fetch per-account daily sending stats (sent + bounced).
    Uses cached data if available to avoid burning rate limit."""
    cache_path = ensure_tmp_dir() / "daily_analytics.json"

    if cache_path.exists():
        logger.info("Loading cached daily analytics...")
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"  Loaded {len(data)} cached records")
        return data

    logger.info("Fetching daily account analytics from API...")
    data = get("/accounts/analytics/daily")
    if isinstance(data, list):
        result = data
    elif isinstance(data, dict):
        result = data.get("items", data.get("data", []))
    else:
        result = []

    logger.info(f"  Got {len(result)} daily records")

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f, default=str)
    logger.info(f"  Cached to {cache_path}")

    return result


def fetch_all_emails_for_replies(valid_accounts: set) -> dict:
    """Paginate through all emails and count replies per receiving account.

    Uses 'eaccount' field for reliable account attribution.
    Only counts replies to accounts in valid_accounts.

    Returns: {account_email: reply_count}
    """
    cache_path = ensure_tmp_dir() / "reply_counts.json"

    if cache_path.exists():
        logger.info("Loading cached reply counts...")
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        logger.info(f"  Loaded {len(cached)} accounts with replies, {sum(cached.values())} total replies")
        return cached

    logger.info("Fetching all emails to count replies per account...")
    reply_counts = defaultdict(int)
    total_processed = 0
    total_replies = 0
    skipped = 0
    cursor = None

    while True:
        params = {"limit": 100}
        if cursor:
            params["starting_after"] = cursor

        data = get("/emails", params=params)

        if isinstance(data, dict):
            items = data.get("items", [])
            cursor = data.get("next_starting_after")
        elif isinstance(data, list):
            items = data
            cursor = None
        else:
            break

        if not items:
            break

        for email in items:
            total_processed += 1
            ue_type = email.get("ue_type")

            if ue_type == 2:  # Reply
                # Use 'eaccount' — the sending account that received this reply
                acct = (email.get("eaccount") or "").lower()
                if acct and acct in valid_accounts:
                    reply_counts[acct] += 1
                    total_replies += 1
                else:
                    skipped += 1

        if total_processed % 5000 == 0:
            logger.info(f"  Processed {total_processed} emails, {total_replies} replies found...")

        if not cursor or len(items) < 100:
            break

        time.sleep(0.5)  # Pace requests to avoid rate limits

    logger.info(f"  Done: {total_processed} emails processed, {total_replies} replies found, {skipped} skipped (unknown accounts)")

    result = dict(reply_counts)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f)
    logger.info(f"  Cached to {cache_path}")

    return result


def extract_domain(email: str) -> str:
    return email.split("@")[1].lower() if "@" in email else "unknown"


def main():
    # Step 0: Load current accounts from accounts.json
    account_map = load_current_accounts()
    valid_accounts = set(account_map.keys())
    logger.info(f"Current accounts loaded: {len(valid_accounts)}")

    # Build domain -> mailbox count from accounts.json
    domain_mailboxes = defaultdict(set)
    for email in valid_accounts:
        domain = extract_domain(email)
        domain_mailboxes[domain].add(email)

    logger.info(f"Domains from accounts: {len(domain_mailboxes)}")

    # Step 1: Get sent + bounced per account from daily analytics
    daily_data = fetch_daily_account_analytics()

    account_sent = defaultdict(int)
    account_bounced = defaultdict(int)
    for row in daily_data:
        acct = row.get("email_account", "").lower()
        # Only count accounts that currently exist
        if acct and acct in valid_accounts:
            account_sent[acct] += row.get("sent", 0) or 0
            account_bounced[acct] += row.get("bounced", 0) or 0

    active_senders = {a for a in account_sent if account_sent[a] > 0}
    logger.info(f"Current accounts with sending data: {len(active_senders)}")
    logger.info(f"Total sent: {sum(account_sent.values())}, bounced: {sum(account_bounced.values())}")

    # Step 2: Get reply counts per account (filtered to current accounts)
    reply_counts = fetch_all_emails_for_replies(valid_accounts)
    logger.info(f"Accounts with replies: {len(reply_counts)}")
    logger.info(f"Total raw replies: {sum(reply_counts.values())}")

    # Step 3: Get auto-reply correction ratio
    real_ratio = get_auto_reply_ratio()

    # Step 4: Aggregate by domain (only domains with current mailboxes)
    results = []
    for domain, mailboxes in domain_mailboxes.items():
        sent = sum(account_sent.get(acct, 0) for acct in mailboxes)
        bounced = sum(account_bounced.get(acct, 0) for acct in mailboxes)
        raw_replies = sum(reply_counts.get(acct, 0) for acct in mailboxes)
        real_replies = round(raw_replies * real_ratio)

        reply_rate = (real_replies / sent * 100) if sent > 0 else 0
        bounce_rate = (bounced / sent * 100) if sent > 0 else 0

        results.append({
            "domain": domain,
            "mailboxes": len(mailboxes),
            "total_sent": sent,
            "raw_replies": raw_replies,
            "real_replies": real_replies,
            "total_bounced": bounced,
            "reply_rate_pct": round(reply_rate, 2),
            "bounce_rate_pct": round(bounce_rate, 2),
        })

    # Sort by reply rate ascending (worst first)
    results.sort(key=lambda x: x["reply_rate_pct"])

    # Step 5: Output
    tmp = ensure_tmp_dir()
    csv_path = tmp / "domain_reply_stats.csv"
    columns = ["domain", "mailboxes", "total_sent", "raw_replies", "real_replies", "total_bounced", "reply_rate_pct", "bounce_rate_pct"]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(results)

    # Print summary
    total_sent = sum(r["total_sent"] for r in results)
    total_raw = sum(r["raw_replies"] for r in results)
    total_real = sum(r["real_replies"] for r in results)
    total_bounced = sum(r["total_bounced"] for r in results)
    overall_rate = (total_real / total_sent * 100) if total_sent > 0 else 0

    # Separate domains with/without send data
    sending_domains = [r for r in results if r["total_sent"] > 0]
    idle_domains = [r for r in results if r["total_sent"] == 0]

    print(f"\n{'=' * 66}")
    print(f"  DOMAIN REPLY STATS - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  (warmup excluded, auto-replies corrected at {real_ratio:.0%} real ratio)")
    print(f"{'=' * 66}")
    print(f"\n  Domains:           {len(results)} ({len(sending_domains)} sending, {len(idle_domains)} idle)")
    print(f"  Total sent:        {total_sent:,}")
    print(f"  Raw replies:       {total_raw:,} (includes auto/OOO)")
    print(f"  Real replies:      {total_real:,} (estimated)")
    print(f"  Total bounced:     {total_bounced:,}")
    print(f"  Overall reply:     {overall_rate:.2f}%")

    if sending_domains:
        print(f"\n  {'-' * 62}")
        print(f"  {'DOMAIN':<28} {'MBX':>3} {'SENT':>7} {'REAL':>5} {'RATE':>7} {'BNCE':>6}")
        print(f"  {'-' * 62}")
        for r in sending_domains:
            print(f"  {r['domain']:<28} {r['mailboxes']:>3} {r['total_sent']:>7,} {r['real_replies']:>5} {r['reply_rate_pct']:>6.2f}% {r['bounce_rate_pct']:>5.1f}%")

    if idle_domains:
        print(f"\n  {'-' * 40}")
        print(f"  IDLE DOMAINS (no campaign sends)")
        print(f"  {'-' * 40}")
        for r in idle_domains:
            print(f"  {r['domain']:<28} {r['mailboxes']:>3} mailboxes")

    print(f"\n  Report saved to: {csv_path}")


if __name__ == "__main__":
    main()
