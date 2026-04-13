#!/usr/bin/env python3
"""RFHC Mid-Week Report Generator — independent from weekly pipeline.

Generates RFHC weekly report using natural Mon-Sun weeks.

Usage:
  # Generate report
  python3 midweek_generator.py
"""

import os
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, "/home/ubuntu/.openclaw/workspace/infra/data-proxy")

from client import DataProxy
from data import (
    find_natural_weeks,
    compute_6_natural_weeks,
    fetch_detailed_data,
    fetch_6w_segment_data_split,
    fetch_6w_account_data_split,
    fetch_total_movers,
)
from templates.detailed import generate_detailed_html
from html_helpers import fmt_week_dates
from utils import safe_name

OUTPUT_DIR = "/home/ubuntu/.openclaw/workspace/output/weekly-revenue-report/midweek"

PROXY_URL = "https://thuedsrtpc.execute-api.cn-northwest-1.amazonaws.com.cn/prod/"
PROXY_KEY = os.environ.get("DATA_PROXY_KEY", "zV76dHel_s61cwljvPkFnNtCh7nhFS0XhImzteuVRfw")


def generate_rfhc_midweek(proxy, weeks):
    """Generate RFHC report using natural week windows."""
    print(f"Generating RFHC Mid-Week report...")
    print(f"  CW: {weeks['cw_start']} ~ {weeks['cw_end']} ({weeks['cw_days']} days)")
    print(f"  PW: {weeks['pw_start']} ~ {weeks['pw_end']} ({weeks['pw_days']} days)")

    # Fetch data (same as regular pipeline, just different week windows)
    data = fetch_detailed_data(proxy, "RFHC", weeks, scope_is_gcr=False)

    # 6W trend using natural weeks
    six_weeks = compute_6_natural_weeks(weeks["cw_end"])
    print(f"  6W: {six_weeks[0]['label']} ({six_weeks[0]['start']}) ~ {six_weeks[-1]['label']} ({six_weeks[-1]['end']})")

    seg_6w = fetch_6w_segment_data_split(
        proxy, six_weeks, bu_name="RFHC", scope_is_gcr=False
    )

    # Collect all account names (top10 + all movers) for 6W account trends
    all_acct_names = set()
    for r in data.get("acct_rows_by_rev", []):
        all_acct_names.add(r["name"])
    for r in data.get("acct_rows_by_usg", []):
        all_acct_names.add(r["name"])
    for cat in data.get("movers", {}).values():
        all_acct_names |= set(m["name"] for m in cat.get("accel", []))
        all_acct_names |= set(m["name"] for m in cat.get("decel", []))

    # Total movers
    print("  Fetching Total Usage movers...")
    tu_a, tu_d = fetch_total_movers(proxy, "RFHC", weeks, "usage")
    print("  Fetching Total Revenue movers...")
    tr_a, tr_d = fetch_total_movers(proxy, "RFHC", weeks, "revenue")
    total_movers_data = {
        "total_usg": {"accel": tu_a, "decel": tu_d},
        "total_rev": {"accel": tr_a, "decel": tr_d},
    }

    # Add total mover names to account set
    for cat in total_movers_data.values():
        all_acct_names |= set(m["name"] for m in cat["accel"])
        all_acct_names |= set(m["name"] for m in cat["decel"])

    # 6W account trends
    acct_6w = {}
    if all_acct_names:
        acct_6w = fetch_6w_account_data_split(
            proxy, six_weeks, list(all_acct_names),
            scope_is_gcr=False, bu_name="RFHC"
        )
    print(f"  Account 6W: {len(all_acct_names)} accounts")

    # Generate HTML
    html = generate_detailed_html(
        "RFHC", data, weeks, six_weeks,
        seg_6w, acct_6w, total_movers_data,
        segment_label="Unit (sh_l4)"
    )

    # Save
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "rfhc_midweek.html")
    with open(out_path, "w") as f:
        f.write(html)
    print(f"  Saved: {out_path}")

    return html, out_path


def main():
    print("=" * 60)
    print("RFHC Mid-Week Report Generator")
    print("=" * 60)

    proxy = DataProxy(url=PROXY_URL, api_key=PROXY_KEY, timeout=120)

    # Use natural Mon-Sun weeks
    print("\nFinding natural week windows...")
    weeks = find_natural_weeks(proxy)

    t0 = time.time()
    html, out_path = generate_rfhc_midweek(proxy, weeks)
    elapsed = time.time() - t0
    print(f"\n✅ Generated in {elapsed:.0f}s")
    print(f"\nOutput: {out_path}")


if __name__ == "__main__":
    main()
