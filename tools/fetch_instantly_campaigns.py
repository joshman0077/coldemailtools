#!/usr/bin/env python3
"""
Fetch Instantly Campaign Data

Pulls campaign analytics and account-campaign mappings from Instantly API v2.
Saves to .tmp/campaigns.json.

Usage:
    python tools/fetch_instantly_campaigns.py
"""

import json
import sys
import time
import logging
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from instantly_client import get, get_paginated, ensure_tmp_dir

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("instantly")


def fetch_campaigns() -> list:
    logger.info("Fetching all campaigns...")
    campaigns = get_paginated("/campaigns")
    logger.info(f"  Retrieved {len(campaigns)} campaigns")
    return campaigns


def fetch_campaign_analytics() -> dict:
    logger.info("Fetching campaign analytics (all campaigns)...")
    try:
        data = get("/campaigns/analytics")
        return data
    except Exception as e:
        logger.warning(f"  Could not fetch campaign analytics: {e}")
        return {}


def fetch_campaign_analytics_for(campaign_id: str) -> dict:
    """Fetch analytics for a single campaign."""
    try:
        data = get("/campaigns/analytics", params={"id": campaign_id})
        return data
    except Exception as e:
        logger.warning(f"  Analytics failed for campaign {campaign_id}: {e}")
        return {}


def fetch_account_campaign_mapping() -> list:
    logger.info("Fetching account-campaign mappings...")
    try:
        data = get_paginated("/account-campaign-mapping")
        logger.info(f"  Retrieved {len(data)} mappings")
        return data
    except Exception as e:
        logger.warning(f"  Could not fetch mappings: {e}")
        return []


def build_output(campaigns: list, analytics: dict, mappings: list) -> dict:
    """Combine campaigns, analytics, and mappings into a single structure."""

    # Build mapping: account email → list of campaign IDs
    account_to_campaigns = defaultdict(list)
    campaign_to_accounts = defaultdict(list)

    for mapping in mappings:
        email = mapping.get("email", mapping.get("account_email", ""))
        cid = mapping.get("campaign_id", mapping.get("id", ""))
        if email and cid:
            account_to_campaigns[email].append(str(cid))
            campaign_to_accounts[str(cid)].append(email)

    return {
        "campaigns": campaigns,
        "analytics": analytics,
        "account_to_campaigns": dict(account_to_campaigns),
        "campaign_to_accounts": dict(campaign_to_accounts),
    }


def summarize(output: dict):
    campaigns = output["campaigns"]
    a2c = output["account_to_campaigns"]

    active = [c for c in campaigns if c.get("status") in ("active", "ACTIVE", 1, True)]

    print(f"\n=== Campaign Data ===")
    print(f"Total campaigns:   {len(campaigns)}")
    print(f"Active campaigns:  {len(active)}")
    print(f"Accounts mapped:   {len(a2c)}")

    # Accounts used in most campaigns
    if a2c:
        top_accounts = sorted(a2c.items(), key=lambda x: -len(x[1]))[:5]
        print(f"\nAccounts in most campaigns:")
        for email, cids in top_accounts:
            print(f"  {email}: {len(cids)} campaigns")

    # Analytics summary
    analytics = output.get("analytics", {})
    if isinstance(analytics, dict):
        for key in ("total_sent", "total_opened", "total_replied", "total_bounced"):
            val = analytics.get(key)
            if val is not None:
                print(f"  {key}: {val}")


def main():
    campaigns = fetch_campaigns()
    analytics = fetch_campaign_analytics()
    mappings = fetch_account_campaign_mapping()

    output = build_output(campaigns, analytics, mappings)

    tmp = ensure_tmp_dir()
    output_path = tmp / "campaigns.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)

    summarize(output)
    print(f"\nSaved to {output_path}")


if __name__ == "__main__":
    main()
