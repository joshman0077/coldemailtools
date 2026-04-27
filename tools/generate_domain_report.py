#!/usr/bin/env python3
"""
Generate Domain Health Report

Reads .tmp/domain_scores.json and outputs a CSV report
with health classifications and recommendations.

Requires: .tmp/domain_scores.json (run score_domain_health.py first)

Usage:
    python tools/generate_domain_report.py
"""

import csv
import json
import sys
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from instantly_client import ensure_tmp_dir

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("instantly")

CSV_COLUMNS = [
    "domain",
    "mailbox_count",
    "composite_score",
    "classification",
    "warmup_score",
    "inbox_placement_pct",
    "warmup_sent",
    "warmup_received",
    "active_account_count",
    "bad_account_count",
    "data_completeness",
    "recommendation",
]


def load_scores() -> list:
    path = ensure_tmp_dir() / "domain_scores.json"
    if not path.exists():
        raise FileNotFoundError(
            "domain_scores.json not found. Run score_domain_health.py first."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def format_value(value) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.1f}"
    if isinstance(value, list):
        return str(len(value))
    return str(value)


def write_csv(scores: list, output_path: Path):
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)

        for domain in scores:
            row = [format_value(domain.get(col)) for col in CSV_COLUMNS]
            writer.writerow(row)


def print_summary(scores: list):
    classifications = defaultdict(int)
    for d in scores:
        classifications[d.get("classification", "Unknown")] += 1

    total = len(scores)
    healthy = classifications.get("Healthy", 0)
    at_risk = classifications.get("At Risk", 0)
    unhealthy = classifications.get("Unhealthy", 0)
    insufficient = classifications.get("Insufficient Data", 0)

    print(f"\n{'=' * 50}")
    print(f"  DOMAIN HEALTH REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 50}")
    print(f"\n  Total domains: {total}")
    if total:
        print(f"  Healthy (>=75):      {healthy:>4}  ({healthy / total * 100:.0f}%)")
        print(f"  At Risk (50-74):     {at_risk:>4}  ({at_risk / total * 100:.0f}%)")
        print(f"  Unhealthy (<50):     {unhealthy:>4}  ({unhealthy / total * 100:.0f}%)")
        if insufficient:
            print(f"  Insufficient Data:   {insufficient:>4}  ({insufficient / total * 100:.0f}%)")

    # Unhealthy domains detail
    unhealthy_domains = [d for d in scores if d.get("classification") == "Unhealthy"]
    if unhealthy_domains:
        print(f"\n  {'-' * 46}")
        print(f"  UNHEALTHY DOMAINS (action required)")
        print(f"  {'-' * 46}")
        for d in unhealthy_domains:
            score = d.get("composite_score", "?")
            inbox = d.get("inbox_placement_pct")
            inbox_str = f"Inbox: {inbox:.0f}%" if inbox is not None else ""
            bad = d.get("bad_account_count", 0)
            bad_str = f"Errors: {bad}" if bad > 0 else ""
            parts = [p for p in [inbox_str, bad_str] if p]
            detail = " | ".join(parts) if parts else "see report"
            print(f"  {d['domain']:<30} Score: {score:<6} {detail}")

    # At-risk domains
    at_risk_domains = [d for d in scores if d.get("classification") == "At Risk"]
    if at_risk_domains:
        print(f"\n  {'-' * 46}")
        print(f"  AT RISK DOMAINS (monitor closely)")
        print(f"  {'-' * 46}")
        for d in at_risk_domains[:15]:
            score = d.get("composite_score", "?")
            print(f"  {d['domain']:<30} Score: {score}")
        if len(at_risk_domains) > 15:
            print(f"  ... and {len(at_risk_domains) - 15} more (see CSV)")

    print(f"\n{'=' * 50}")


def main():
    scores = load_scores()

    tmp = ensure_tmp_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    output_path = tmp / f"domain_health_report_{timestamp}.csv"
    latest_path = tmp / "domain_health_report.csv"

    # Write timestamped and latest versions
    write_csv(scores, output_path)
    write_csv(scores, latest_path)

    print_summary(scores)
    print(f"\n  Report saved to: {output_path}")
    print(f"  Latest copy at:  {latest_path}")


if __name__ == "__main__":
    main()
