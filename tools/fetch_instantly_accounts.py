#!/usr/bin/env python3
"""
Fetch Instantly Accounts

Pulls all email accounts from Instantly API v2, extracts domain info,
and saves to .tmp/accounts.json.

Usage:
    python tools/fetch_instantly_accounts.py
"""

import json
import sys
import logging
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from instantly_client import get_paginated, ensure_tmp_dir

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("instantly")


def fetch_accounts() -> list:
    logger.info("Fetching all email accounts from Instantly...")
    accounts = get_paginated("/accounts")
    logger.info(f"  Retrieved {len(accounts)} accounts")
    return accounts


def extract_domain(email: str) -> str:
    return email.split("@")[1].lower() if "@" in email else "unknown"


def enrich_with_domains(accounts: list) -> list:
    for acct in accounts:
        email = acct.get("email", "")
        acct["domain"] = extract_domain(email)
    return accounts


def summarize(accounts: list):
    domains = defaultdict(list)
    for acct in accounts:
        domains[acct["domain"]].append(acct["email"])

    status_counts = defaultdict(int)
    for acct in accounts:
        status = acct.get("status", "unknown")
        status_counts[status] += 1

    warmup_enabled = sum(1 for a in accounts if a.get("warmup_enabled") or a.get("warmup", {}).get("enabled"))
    warmup_disabled = len(accounts) - warmup_enabled

    print(f"\n=== Instantly Accounts ===")
    print(f"Total accounts:  {len(accounts)}")
    print(f"Total domains:   {len(domains)}")
    print(f"Warmup enabled:  {warmup_enabled}")
    print(f"Warmup disabled: {warmup_disabled}")
    print(f"\nStatus breakdown:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
    print(f"\nTop 10 domains by mailbox count:")
    for domain, emails in sorted(domains.items(), key=lambda x: -len(x[1]))[:10]:
        print(f"  {domain}: {len(emails)} mailboxes")


def main():
    accounts = fetch_accounts()
    accounts = enrich_with_domains(accounts)

    tmp = ensure_tmp_dir()
    output_path = tmp / "accounts.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2, default=str)

    summarize(accounts)
    print(f"\nSaved to {output_path}")


if __name__ == "__main__":
    main()
