#!/usr/bin/env python3
"""RFHC Mid-Week Report Generator — independent from weekly pipeline.

Generates RFHC weekly report using natural Mon-Sun weeks.
Runs on Wednesday (preview) and Thursday (send).

Usage:
  # Generate report (preview to fuxin + qiuyac)
  python3 midweek_generator.py --preview

  # Generate + send to kenshen
  python3 midweek_generator.py --send

  # Generate only (no email)
  python3 midweek_generator.py
"""

import argparse
import base64
import json
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

import boto3

LAMBDA_ARN = "arn:aws:lambda:us-east-1:537431199900:function:send_raw_email_poc"
LAMBDA_REGION = "us-east-1"
TEMPLATE_ID = "CMHK-Weekly-CDER-Usage-Report/EMAIL/en"
OUTPUT_DIR = "/home/ubuntu/.openclaw/workspace/output/weekly-revenue-report/midweek"

PROXY_URL = "https://thuedsrtpc.execute-api.cn-northwest-1.amazonaws.com.cn/prod/"
PROXY_KEY = "zV76dHel_s61cwljvPkFnNtCh7nhFS0XhImzteuVRfw"

# Recipients
KENSHEN = {"alias": "kenshen", "first_name": "Ken", "email": "kenshen@amazon.com"}
PREVIEW_RECIPIENTS = ["fuxin@amazon.com", "qiuyac@amazon.com"]


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


def _get_week_label(weeks):
    """Get week number and date range for subject line."""
    from datetime import datetime
    cw_start = datetime.strptime(weeks["cw_start"], "%Y-%m-%d")
    cw_end = datetime.strptime(weeks["cw_end"], "%Y-%m-%d")
    wk_num = cw_start.isocalendar()[1]
    date_range = f"{cw_start.strftime('%b')} {cw_start.day}-{cw_end.day}"
    return wk_num, date_range


def send_email(to_addresses, subject, html_body, attachments=None):
    """Send email via cross-account Lambda."""
    client = boto3.client("lambda", region_name=LAMBDA_REGION)
    payload = {
        "template_id": TEMPLATE_ID,
        "to_addresses": to_addresses,
        "render_parameters": {"subject": subject, "body": html_body},
    }
    if attachments:
        payload["attachments"] = attachments
    resp = client.invoke(
        FunctionName=LAMBDA_ARN,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload),
    )
    return json.loads(resp["Payload"].read())


def send_preview(html_path, weeks):
    """Send preview to fuxin + qiuyac."""
    wk_num, date_range = _get_week_label(weeks)
    subject = f"[Mid-Week][Preview] RFHC Weekly Revenue Report — W{wk_num} ({date_range})"

    with open(html_path, "r") as f:
        content = f.read()

    body = f"""<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; color: #333;">
<p>RFHC Mid-Week Report preview — please review.</p>
<p style="color:#666;font-size:13px;">CW: {weeks['cw_start']} ~ {weeks['cw_end']} (Mon-Sun) | {weeks['cw_days']} days data</p>
<p>Best,<br>DataRetriever 🐕</p>
</body></html>"""

    attachments = [{
        "file_name": f"RFHC_MidWeek_Report_W{wk_num}.html",
        "file_content_base64": base64.b64encode(content.encode()).decode(),
        "file_type": "text/html",
    }]

    result = send_email(PREVIEW_RECIPIENTS, subject, body, attachments)
    print(f"  Preview sent to {PREVIEW_RECIPIENTS}: {result.get('messageId', 'N/A')}")
    return result


def send_to_kenshen(html_path, weeks):
    """Send to kenshen — GM attachment mode."""
    wk_num, date_range = _get_week_label(weeks)
    subject = f"[Mid-Week] RFHC Weekly Revenue Report — W{wk_num} ({date_range})"

    with open(html_path, "r") as f:
        content = f.read()

    body = f"""<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; color: #333;">
<p>Hi {KENSHEN['first_name']},</p>
<p>Your mid-week RFHC Revenue Report is ready.<br>
Please open the attached HTML file to view the full interactive report.</p>
<p>Best,<br>DataRetriever 🐕</p>
</body></html>"""

    attachments = [{
        "file_name": f"RFHC_MidWeek_Report_W{wk_num}.html",
        "file_content_base64": base64.b64encode(content.encode()).decode(),
        "file_type": "text/html",
    }]

    result = send_email([KENSHEN["email"]], subject, body, attachments)
    print(f"  Sent to {KENSHEN['email']}: {result.get('messageId', 'N/A')}")
    return result


def main():
    parser = argparse.ArgumentParser(description="RFHC Mid-Week Report")
    parser.add_argument("--preview", action="store_true",
                        help="Send preview to fuxin + qiuyac")
    parser.add_argument("--send", action="store_true",
                        help="Send to kenshen (production)")
    args = parser.parse_args()

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

    if args.preview:
        print("\n📧 Sending preview...")
        send_preview(out_path, weeks)

    if args.send:
        print("\n📧 Sending to kenshen...")
        send_to_kenshen(out_path, weeks)

    if not args.preview and not args.send:
        print("\n💡 Use --preview or --send to email the report.")

    print(f"\nOutput: {out_path}")


if __name__ == "__main__":
    main()
