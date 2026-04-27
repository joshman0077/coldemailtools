#!/usr/bin/env python3
"""
Fetch Instantly Warmup Analytics

Pulls warmup analytics for all accounts from Instantly API v2
and saves to .tmp/warmup.json.

Requires: .tmp/accounts.json (run fetch_instantly_accounts.py first)

Usage:
    python tools/fetch_instantly_warmup.py
"""

import json
import sys
import time
import logging
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from instantly_client import post, ensure_tmp_dir

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("instantly")

BATCH_SIZE = 50  # Emails per API call to avoid timeouts


def load_accounts() -> list:
    accounts_path = ensure_tmp_dir() / "accounts.json"
    if not accounts_path.exists():
        raise FileNotFoundError(
            "accounts.json not found. Run fetch_instantly_accounts.py first."
        )
    with open(accounts_path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_warmup_analytics(emails: list) -> list:
    """Fetch warmup analytics in batches."""
    all_analytics = []

    for i in range(0, len(emails), BATCH_SIZE):
        batch = emails[i:i + BATCH_SIZE]
        logger.info(f"  Fetching warmup analytics batch {i // BATCH_SIZE + 1} ({len(batch)} accounts)...")

        try:
            data = post("/accounts/warmup-analytics", json_body={"emails": batch})

            if isinstance(data, list):
                all_analytics.extend(data)
            elif isinstance(data, dict):
                items = data.get("data", data.get("items", []))
                if items:
                    all_analytics.extend(items)
                else:
                    # Single-object response — might be keyed by email
                    all_analytics.append(data)
        except Exception as e:
            logger.warning(f"  Batch failed: {e}")

        time.sleep(0.5)

    return all_analytics


def summarize(analytics: list, accounts: list):
    # Build domain mapping from accounts
    domain_map = {}
    for acct in accounts:
        domain_map[acct.get("email", "")] = acct.get("domain", "unknown")

    # Aggregate by domain
    domain_stats = defaultdict(lambda: {
        "health_scores": [],
        "total_sent": 0,
        "total_inbox": 0,
        "total_spam": 0,
        "mailbox_count": 0,
    })

    for entry in analytics:
        email = entry.get("email", "")
        domain = domain_map.get(email, email.split("@")[1] if "@" in email else "unknown")

        stats = domain_stats[domain]
        stats["mailbox_count"] += 1

        health = entry.get("health_score")
        if health is not None:
            try:
                stats["health_scores"].append(float(health))
            except (ValueError, TypeError):
                pass

        stats["total_sent"] += entry.get("sent", 0) or 0
        stats["total_inbox"] += entry.get("landed_inbox", 0) or 0
        stats["total_spam"] += entry.get("landed_spam", 0) or 0

    # Print summary
    all_scores = []
    for stats in domain_stats.values():
        all_scores.extend(stats["health_scores"])

    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0

    print(f"\n=== Warmup Analytics ===")
    print(f"Accounts with data: {len(analytics)}")
    print(f"Domains analyzed:   {len(domain_stats)}")
    print(f"Avg health score:   {avg_score:.1f}")

    if domain_stats:
        # Worst domains by avg health score
        ranked = []
        for domain, stats in domain_stats.items():
            if stats["health_scores"]:
                avg = sum(stats["health_scores"]) / len(stats["health_scores"])
                ranked.append((domain, avg, stats["mailbox_count"]))

        ranked.sort(key=lambda x: x[1])

        print(f"\nBottom 5 domains (lowest health):")
        for domain, score, count in ranked[:5]:
            print(f"  {domain}: {score:.1f} ({count} mailboxes)")

        print(f"\nTop 5 domains (highest health):")
        for domain, score, count in ranked[-5:]:
            print(f"  {domain}: {score:.1f} ({count} mailboxes)")


def main():
    accounts = load_accounts()
    emails = [a["email"] for a in accounts if a.get("email")]

    logger.info(f"Fetching warmup analytics for {len(emails)} accounts...")
    analytics = fetch_warmup_analytics(emails)

    tmp = ensure_tmp_dir()
    output_path = tmp / "warmup.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(analytics, f, indent=2, default=str)

    summarize(analytics, accounts)
    print(f"\nSaved to {output_path}")


if __name__ == "__main__":
    main()
