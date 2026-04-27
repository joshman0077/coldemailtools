#!/usr/bin/env python3
"""
Score Domain Health

Reads fetched data from .tmp/ (accounts, warmup, campaigns),
computes a composite health score per domain, classifies them,
and generates recommendations.

Requires: .tmp/accounts.json, .tmp/warmup.json, .tmp/campaigns.json
(Run the three fetch tools first)

Usage:
    python tools/score_domain_health.py
"""

import json
import sys
import logging
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from instantly_client import ensure_tmp_dir

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("instantly")

# --- Scoring weights (adjusted for available data) ---
WEIGHTS = {
    "warmup_health": 0.35,      # stat_warmup_score from account
    "inbox_placement": 0.40,    # landed_inbox vs landed_spam from warmup daily data
    "account_status": 0.25,     # account status (-3, -1 = bad, 1 = good)
}

# --- Thresholds ---
HEALTHY_THRESHOLD = 75
AT_RISK_THRESHOLD = 50


def load_json(filename: str):
    path = ensure_tmp_dir() / filename
    if not path.exists():
        logger.warning(f"  {filename} not found, skipping this data source")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def score_inbox_placement_rate(rate: float) -> float:
    """Score inbox placement rate (0-100%)."""
    if rate > 95:
        return 100
    elif rate > 90:
        return 90
    elif rate > 80:
        return 70
    elif rate > 70:
        return 50
    elif rate > 50:
        return 30
    else:
        return 10


def score_account_status(statuses: list) -> float:
    """Score based on account statuses. Status 1=active, -1/-3=issues."""
    if not statuses:
        return 50
    active = sum(1 for s in statuses if s == 1)
    total = len(statuses)
    active_pct = active / total
    if active_pct == 1.0:
        return 100
    elif active_pct >= 0.8:
        return 70
    elif active_pct >= 0.5:
        return 40
    else:
        return 10


def classify(score: float) -> str:
    if score >= HEALTHY_THRESHOLD:
        return "Healthy"
    elif score >= AT_RISK_THRESHOLD:
        return "At Risk"
    else:
        return "Unhealthy"


def generate_recommendation(classification: str, metrics: dict) -> str:
    if classification == "Healthy":
        return "No action needed. Keep in campaigns."

    issues = []

    warmup = metrics.get("warmup_score")
    if warmup is not None and warmup < 90:
        issues.append(f"warmup score declining ({warmup:.0f})")

    inbox_pct = metrics.get("inbox_placement_pct")
    if inbox_pct is not None and inbox_pct < 90:
        issues.append(f"poor inbox placement ({inbox_pct:.1f}%)")

    bad_accounts = metrics.get("bad_account_count", 0)
    if bad_accounts > 0:
        issues.append(f"{bad_accounts} mailbox(es) with errors")

    issue_str = ", ".join(issues) if issues else "underperforming across metrics"

    if classification == "Unhealthy":
        return f"REMOVE from campaigns. Enable warmup-only. Issues: {issue_str}"
    else:
        return f"MONITOR closely. Consider reducing send volume. Issues: {issue_str}"


def parse_warmup_by_email(warmup_data: list) -> dict:
    """Parse warmup batches into per-email aggregated stats.

    Warmup API returns: [{email_date_data: {email: {date: {sent, landed_inbox, landed_spam, received}}}}]
    """
    email_stats = defaultdict(lambda: {"sent": 0, "landed_inbox": 0, "landed_spam": 0, "received": 0, "days": 0})

    if not warmup_data:
        return {}

    for batch in warmup_data:
        if not isinstance(batch, dict):
            continue
        edd = batch.get("email_date_data", {})
        for email, dates in edd.items():
            if not isinstance(dates, dict):
                continue
            for date, day_stats in dates.items():
                if not isinstance(day_stats, dict):
                    continue
                email_stats[email]["sent"] += day_stats.get("sent", 0) or 0
                email_stats[email]["landed_inbox"] += day_stats.get("landed_inbox", 0) or 0
                email_stats[email]["landed_spam"] += day_stats.get("landed_spam", 0) or 0
                email_stats[email]["received"] += day_stats.get("received", 0) or 0
                email_stats[email]["days"] += 1

    return dict(email_stats)


def main():
    # --- Load all data ---
    accounts = load_json("accounts.json")
    warmup_raw = load_json("warmup.json")
    campaigns_data = load_json("campaigns.json")

    if not accounts:
        print("ERROR: accounts.json is required. Run fetch_instantly_accounts.py first.")
        sys.exit(1)

    # --- Parse warmup data into per-email stats ---
    warmup_by_email = parse_warmup_by_email(warmup_raw)
    logger.info(f"Warmup data available for {len(warmup_by_email)} accounts")

    # --- Group accounts by domain ---
    domain_accounts = defaultdict(list)
    for acct in accounts:
        domain_accounts[acct["domain"]].append(acct)

    # --- Score each domain ---
    scored_domains = []

    for domain, accts in domain_accounts.items():
        metrics = {
            "domain": domain,
            "mailbox_count": len(accts),
        }

        # --- Warmup health score (from stat_warmup_score on each account) ---
        warmup_scores = []
        for acct in accts:
            ws = acct.get("stat_warmup_score")
            if ws is not None:
                warmup_scores.append(float(ws))

        metrics["warmup_score"] = (sum(warmup_scores) / len(warmup_scores)) if warmup_scores else None

        # --- Inbox placement (from warmup daily data) ---
        total_inbox = 0
        total_spam = 0
        total_sent = 0
        total_received = 0
        total_days = 0

        for acct in accts:
            email = acct["email"]
            ws = warmup_by_email.get(email, {})
            total_inbox += ws.get("landed_inbox", 0)
            total_spam += ws.get("landed_spam", 0)
            total_sent += ws.get("sent", 0)
            total_received += ws.get("received", 0)
            total_days += ws.get("days", 0)

        total_delivered = total_inbox + total_spam
        metrics["inbox_placement_pct"] = (total_inbox / total_delivered * 100) if total_delivered > 0 else None
        metrics["warmup_sent"] = total_sent
        metrics["warmup_received"] = total_received
        metrics["warmup_days"] = total_days

        # --- Account status ---
        statuses = [acct.get("status", 0) for acct in accts]
        active_count = sum(1 for s in statuses if s == 1)
        bad_count = sum(1 for s in statuses if s < 0)
        metrics["active_account_count"] = active_count
        metrics["bad_account_count"] = bad_count

        # --- Compute composite score ---
        available_weights = {}
        weighted_sum = 0

        # Warmup health
        if metrics["warmup_score"] is not None:
            available_weights["warmup_health"] = WEIGHTS["warmup_health"]
            weighted_sum += metrics["warmup_score"] * WEIGHTS["warmup_health"]

        # Inbox placement
        if metrics["inbox_placement_pct"] is not None:
            ip_score = score_inbox_placement_rate(metrics["inbox_placement_pct"])
            available_weights["inbox_placement"] = WEIGHTS["inbox_placement"]
            weighted_sum += ip_score * WEIGHTS["inbox_placement"]

        # Account status
        status_score = score_account_status(statuses)
        available_weights["account_status"] = WEIGHTS["account_status"]
        weighted_sum += status_score * WEIGHTS["account_status"]

        # Normalize
        total_weight = sum(available_weights.values())
        if total_weight > 0:
            composite = weighted_sum / total_weight
        else:
            composite = None

        metrics["composite_score"] = round(composite, 1) if composite is not None else None
        metrics["classification"] = classify(composite) if composite is not None else "Insufficient Data"
        metrics["data_completeness"] = f"{len(available_weights)}/{len(WEIGHTS)} metrics"
        metrics["recommendation"] = generate_recommendation(metrics["classification"], metrics)

        scored_domains.append(metrics)

    # --- Sort by score ascending (worst first) ---
    scored_domains.sort(key=lambda x: x.get("composite_score") or 0)

    # --- Summary ---
    classifications = defaultdict(int)
    for d in scored_domains:
        classifications[d["classification"]] += 1

    print(f"\n=== Domain Health Scores ===")
    print(f"Total domains: {len(scored_domains)}")
    print(f"  Healthy (>={HEALTHY_THRESHOLD}):       {classifications.get('Healthy', 0)}")
    print(f"  At Risk ({AT_RISK_THRESHOLD}-{HEALTHY_THRESHOLD - 1}):       {classifications.get('At Risk', 0)}")
    print(f"  Unhealthy (<{AT_RISK_THRESHOLD}):      {classifications.get('Unhealthy', 0)}")
    print(f"  Insufficient Data:  {classifications.get('Insufficient Data', 0)}")

    unhealthy = [d for d in scored_domains if d["classification"] == "Unhealthy"]
    if unhealthy:
        print(f"\nUnhealthy domains:")
        for d in unhealthy:
            print(f"  {d['domain']}: Score {d['composite_score']} — {d['recommendation']}")

    at_risk = [d for d in scored_domains if d["classification"] == "At Risk"]
    if at_risk:
        print(f"\nAt Risk domains:")
        for d in at_risk:
            print(f"  {d['domain']}: Score {d['composite_score']} — {d['recommendation']}")

    # --- Save ---
    tmp = ensure_tmp_dir()
    output_path = tmp / "domain_scores.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scored_domains, f, indent=2, default=str)

    print(f"\nSaved to {output_path}")


if __name__ == "__main__":
    main()
