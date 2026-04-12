#!/usr/bin/env python3
"""Send CMHK Weekly Usage & Revenue Report via cross-account Lambda (537431199900).

v3 — 2026-03-31: Two send modes based on report type.
  - CEO (GCR Overall) → HTML inline in email body (no attachment).
    Uses ceo_lite template — single-page, all inline styles, email-safe.
  - GM (per-BU) → HTML attachment (3-tab interactive, open in browser).
  - Each recipient gets ONE email. If a recipient has both CEO + GM reports,
    the CEO HTML is inlined in the body and GM reports are attached.

v2 — 2026-03-27: Rewritten for per-recipient merged sends.

Sender: DataRetriever <dataretriever@amazonaws.cn>
"""

import base64
import json
import os
import sys
import time
from datetime import datetime, timedelta

import boto3

LAMBDA_ARN = "arn:aws:lambda:us-east-1:537431199900:function:send_raw_email_poc"
LAMBDA_REGION = "us-east-1"
TEMPLATE_ID = "CMHK-Weekly-CDER-Usage-Report/EMAIL/en"
OUTPUT_DIR = "/home/ubuntu/.openclaw/workspace/output/weekly-revenue-report/latest"
ALERT_RECIPIENTS = ["fuxin@amazon.com"]

# ── Recipient Configuration ─────────────────────────────────────────────
# Each recipient: alias, first_name, email, list of (bu_name, html_filename)
# CEO report (gcr_detailed.html) is treated as a special BU entry.
RECIPIENTS = [
    {
        "alias": "arobchu",
        "first_name": "Rob",
        "email": "arobchu@amazon.com",
        "reports": [
            ("GCR", "gcr_detailed.html"),
        ],
    },
    {
        "alias": "kenshen",
        "first_name": "Ken",
        "email": "kenshen@amazon.com",
        "reports": [
            ("RFHC", "rfhc.html"),
        ],
    },
    {
        "alias": "zhangaz",
        "first_name": "Alfonso",
        "email": "zhangaz@amazon.com",
        "reports": [
            ("ISV & SUP", "isv_and_sup.html"),
        ],
    },
    {
        "alias": "mzji",
        "first_name": "Ken",
        "email": "mzji@amazon.com",
        "reports": [
            ("MEAGS", "meags.html"),
            ("DNBP", "dnbp.html"),
        ],
    },
    {
        "alias": "tiafeng",
        "first_name": "Feng",
        "email": "tiafeng@amazon.com",
        "reports": [
            ("AUTO & MFG", "auto_and_mfg.html"),
        ],
    },
    {
        "alias": "akchan",
        "first_name": "Andy",
        "email": "akchan@amazon.com",
        "reports": [
            ("HK", "hk.html"),
        ],
    },
    {
        "alias": "danffer",
        "first_name": "Danffer",
        "email": "danffer@amazon.com",
        "reports": [
            ("IND GFD", "ind_gfd.html"),
            ("NWCD", "nwcd.html"),
            ("SMB", "smb.html"),
        ],
    },
    {
        "alias": "chrisso",
        "first_name": "Chris",
        "email": "chrisso@amazon.com",
        "reports": [
            ("PARTNER", "partner.html"),
            ("HK", "hk.html"),
        ],
    },
    {
        "alias": "gufan",
        "first_name": "Fan",
        "email": "gufan@amazon.com",
        "reports": [
            ("STRATEGIC", "strategic.html"),
        ],
    },
    {
        "alias": "ligxi",
        "first_name": "Coleman",
        "email": "ligxi@amazon.com",
        "reports": [
            ("FSI-DNB", "fsi-dnb.html"),
        ],
    },
]


def _get_week_info() -> tuple:
    """Derive week number and date range from the latest report HTML.

    Falls back to computing from current date if parsing fails.
    Returns: (week_num: int, date_range_str: str)
      e.g. (11, "Mar 13-19")
    """
    # Try to parse from gcr_detailed.html header
    gcr_path = os.path.join(OUTPUT_DIR, "gcr_detailed.html")
    if os.path.exists(gcr_path):
        import re
        with open(gcr_path, "r") as f:
            head = f.read(5000)
        # Look for pattern like "Mar 13 – Mar 19, 2026" (en-dash U+2013, or &ndash;, or hyphen)
        m = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2})\s*(?:[–\u2013\-]|&ndash;)\s*[A-Z][a-z]{2}\s+(\d{1,2}),?\s*(\d{4})", head)
        if m:
            from datetime import date
            month_map = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                         "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
            mon = month_map.get(m.group(1), 1)
            day_start = int(m.group(2))
            day_end = int(m.group(3))
            year = int(m.group(4))
            d = date(year, mon, day_start)
            week_num = d.isocalendar()[1]
            date_range = f"{m.group(1)} {day_start}-{day_end}"
            return week_num, date_range

    # Fallback: last week
    today = datetime.utcnow().date()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    week_num = last_monday.isocalendar()[1]
    date_range = f"{last_monday.strftime('%b')} {last_monday.day}-{last_sunday.day}"
    return week_num, date_range


def _invoke_lambda(payload: dict) -> dict:
    """Invoke the send_raw_email_poc Lambda and return parsed response."""
    client = boto3.client("lambda", region_name=LAMBDA_REGION)
    resp = client.invoke(
        FunctionName=LAMBDA_ARN,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload),
    )
    return json.loads(resp["Payload"].read())


def send_email(to_addresses: list, subject: str, html_body: str,
               cc_addresses: list = None, attachments: list = None) -> dict:
    """Send a single email via Lambda."""
    payload = {
        "template_id": TEMPLATE_ID,
        "to_addresses": to_addresses,
        "render_parameters": {
            "subject": subject,
            "body": html_body,
        },
    }
    if cc_addresses:
        payload["cc_addresses"] = cc_addresses
    if attachments:
        payload["attachments"] = attachments
    return _invoke_lambda(payload)


def _build_body(first_name: str, ceo_html: str = None, has_attachments: bool = False) -> str:
    """Build email body.

    CEO mode (ceo_html provided, no attachments): pure report content, no greeting/signature.
    GM mode (attachments): greeting + attachment instruction + signature.
    Mixed mode (both): report content + attachment note.
    """
    if ceo_html and not has_attachments:
        # CEO-only: pure content, no fluff
        return f"""<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; color: #333; line-height: 1.6;">
{ceo_html}
</body></html>"""

    # GM mode or mixed mode: with greeting
    parts = []
    parts.append(f"""<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; color: #333; line-height: 1.6;">
<p>Hi {first_name},</p>
<p>Your weekly Usage &amp; Revenue Report is ready.</p>""")

    if ceo_html:
        parts.append(f"""<hr style="border:none;border-top:1px solid #e8eaed;margin:20px 0;">
{ceo_html}""")

    if has_attachments:
        parts.append("""<hr style="border:none;border-top:1px solid #e8eaed;margin:20px 0;">
<p style="color:#666;font-size:13px;">📎 Your BU-level detailed report is attached. Please open the HTML file in a browser for the full interactive view.</p>""")

    parts.append("""<p style="margin-top:20px;">Best,<br>DataRetriever 🐕</p>
</body></html>""")
    return "\n".join(parts)


def _build_attachments(reports: list, week_num: int) -> list:
    """Build attachment list from report definitions.

    Note: GCR (CEO) reports are EXCLUDED — they go inline in the body.
    Only GM-level BU reports become attachments.

    Args:
        reports: list of (bu_name, html_filename) tuples
        week_num: calendar week number for filename
    Returns:
        list of attachment dicts for Lambda payload
    """
    attachments = []
    for bu_name, html_file in reports:
        # Skip CEO report — it's inlined in the body
        if bu_name == "GCR":
            continue

        html_path = os.path.join(OUTPUT_DIR, html_file)
        if not os.path.exists(html_path):
            print(f"⚠️  Missing report file: {html_path}")
            continue
        with open(html_path, "r") as f:
            content = f.read()

        safe_bu = bu_name.replace(" & ", "_and_").replace(" ", "_").replace("-", "_")
        att_name = f"{safe_bu}_Weekly_Report_W{week_num}.html"

        attachments.append({
            "file_name": att_name,
            "file_content_base64": base64.b64encode(content.encode()).decode(),
            "file_type": "text/html",
        })
    return attachments


def _load_ceo_html(reports: list) -> str:
    """Load the CEO lite HTML if this recipient has a GCR report.

    Returns the raw HTML string or None.
    """
    for bu_name, html_file in reports:
        if bu_name == "GCR":
            html_path = os.path.join(OUTPUT_DIR, html_file)
            if os.path.exists(html_path):
                with open(html_path, "r") as f:
                    return f.read()
            else:
                print(f"⚠️  Missing CEO report: {html_path}")
    return None


def send_all(dry_run: bool = False) -> dict:
    """Send reports to all recipients.

    Args:
        dry_run: if True, print what would be sent without actually sending.
    Returns:
        dict of {email: {status, message_id?, error?}}
    """
    week_num, date_range = _get_week_info()
    subject = f"CMHK Weekly Usage & Revenue Report — W{week_num} ({date_range})"
    print(f"📊 Week: W{week_num} ({date_range})")
    print(f"📧 Subject: {subject}")
    print(f"📁 Output dir: {OUTPUT_DIR}")
    print(f"👥 Recipients: {len(RECIPIENTS)}")
    print()

    results = {}
    for rcpt in RECIPIENTS:
        alias = rcpt["alias"]
        email = rcpt["email"]
        first_name = rcpt["first_name"]
        reports = rcpt["reports"]
        bu_names = [r[0] for r in reports]

        # CEO lite → inline in body; GM reports → attachments
        ceo_html = _load_ceo_html(reports)
        attachments = _build_attachments(reports, week_num)

        body = _build_body(first_name, ceo_html=ceo_html, has_attachments=bool(attachments))

        # Must have either CEO inline or attachments
        if not ceo_html and not attachments:
            print(f"⚠️  {alias}: No report files found, skipping")
            results[email] = {"status": "skipped", "reason": "no files"}
            continue

        mode_desc = []
        if ceo_html:
            mode_desc.append("CEO inline")
        if attachments:
            mode_desc.append(f"{len(attachments)} attachment(s)")
        print(f"📨 {alias} ({email}): {', '.join(bu_names)} [{', '.join(mode_desc)}]")

        if dry_run:
            results[email] = {"status": "dry_run", "bu": bu_names}
            continue

        try:
            result = send_email(
                to_addresses=[email],
                subject=subject,
                html_body=body,
                attachments=attachments,
            )
            if result.get("statusCode") == 200:
                msg_id = result.get("messageId", "")
                results[email] = {"status": "success", "message_id": msg_id, "bu": bu_names}
                print(f"  ✅ Sent → {msg_id}")
            else:
                results[email] = {"status": "error", "error": result.get("message", str(result)), "bu": bu_names}
                print(f"  ❌ Failed: {result}")
        except Exception as e:
            results[email] = {"status": "error", "error": str(e), "bu": bu_names}
            print(f"  ❌ Failed: {e}")

    return results


def send_with_retry(dry_run: bool = False) -> int:
    """Send all reports with one retry for failures."""
    print("=" * 60)
    print("CMHK Weekly Usage & Revenue Report — Sender v2")
    print("=" * 60)
    print()

    # Attempt 1
    print("=== Attempt 1 ===")
    results = send_all(dry_run=dry_run)

    if dry_run:
        print("\n🏁 Dry run complete.")
        print(json.dumps(results, indent=2))
        return 0

    failed = {k: v for k, v in results.items() if v["status"] != "success"}

    if not failed:
        print(f"\n✅ All {len(results)} emails sent successfully!")
        _send_summary(results)
        return 0

    # Attempt 2 — retry failed after 30s
    print(f"\n⏳ {len(failed)} recipient(s) failed. Retrying in 30 seconds...")
    time.sleep(30)

    print("\n=== Attempt 2 (retry) ===")
    week_num, date_range = _get_week_info()
    subject = f"CMHK Weekly Usage & Revenue Report — W{week_num} ({date_range})"

    still_failed = {}
    for email in failed:
        # Find the recipient config
        rcpt = next((r for r in RECIPIENTS if r["email"] == email), None)
        if not rcpt:
            continue
        body = _build_body(rcpt["first_name"],
                           ceo_html=_load_ceo_html(rcpt["reports"]),
                           has_attachments=bool(attachments))
        attachments = _build_attachments(rcpt["reports"], week_num)
        bu_names = [r[0] for r in rcpt["reports"]]

        print(f"📨 Retry {rcpt['alias']} ({email})...")

        try:
            result = send_email(
                to_addresses=[email],
                subject=subject,
                html_body=body,
                attachments=attachments,
            )
            if result.get("statusCode") == 200:
                results[email] = {"status": "success", "message_id": result.get("messageId", ""), "attempt": 2, "bu": bu_names}
                print(f"  ✅ Retry sent → {result.get('messageId', '')}")
            else:
                still_failed[email] = {"status": "error", "error": str(result), "bu": bu_names}
                results[email] = still_failed[email]
                print(f"  ❌ Retry failed: {result}")
        except Exception as e:
            still_failed[email] = {"status": "error", "error": str(e), "bu": bu_names}
            results[email] = still_failed[email]
            print(f"  ❌ Retry failed: {e}")

    if still_failed:
        print(f"\n🚨 {len(still_failed)} still failed. Sending alert to {ALERT_RECIPIENTS}...")
        _send_alert(still_failed)
        _send_summary(results)
        return 1

    print(f"\n✅ All emails sent (some on retry)!")
    _send_summary(results)
    return 0


def _send_alert(error_details: dict):
    """Send failure alert to ALERT_RECIPIENTS."""
    alert_subject = "⚠️ ALERT: Failed to send CMHK Weekly Report"
    alert_body = f"""<html><body>
<h2>⚠️ Weekly Report Send Failure</h2>
<p>The CMHK Weekly Usage &amp; Revenue Report failed to send after 2 attempts.</p>
<h3>Error Details:</h3>
<pre>{json.dumps(error_details, indent=2)}</pre>
<p>Please investigate and resend manually if needed.</p>
<p>— DataRetriever 🐕</p>
</body></html>"""

    try:
        send_email(
            to_addresses=ALERT_RECIPIENTS,
            subject=alert_subject,
            html_body=alert_body,
        )
        print(f"🚨 Alert sent to {ALERT_RECIPIENTS}")
    except Exception as e:
        print(f"❌ Alert also failed: {e}")


SUMMARY_RECIPIENTS = ["fuxin@amazon.com", "qiuyac@amazon.com"]


def _send_summary(results: dict):
    """Send a summary email to fuxin + qiuyac after all sends complete."""
    week_num, date_range = _get_week_info()
    subject = f"[Weekly] Send Summary — W{week_num} ({date_range})"

    rows = ""
    for email, info in sorted(results.items()):
        bu = ", ".join(info.get("bu", []))
        status = "✅" if info.get("status") == "success" else "⚠️ " + info.get("status", "unknown")
        alias = email.split("@")[0]
        rows += (f"<tr><td style='padding:6px 12px;border-bottom:1px solid #eee;'>{alias}</td>"
                 f"<td style='padding:6px 12px;border-bottom:1px solid #eee;'>{bu}</td>"
                 f"<td style='padding:6px 12px;border-bottom:1px solid #eee;'>{status}</td></tr>\n")

    success_count = sum(1 for v in results.values() if v.get("status") == "success")
    total_count = len(results)

    body = f"""<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; color: #333; line-height: 1.6;">
<p>Hi Team,</p>
<p>Weekly reports for W{week_num} ({date_range}) have been sent. {success_count}/{total_count} succeeded.</p>
<table style="border-collapse:collapse;font-size:13px;margin:16px 0;">
<tr style="background:#f0f4f8;">
  <th style="padding:8px 12px;text-align:left;">Recipient</th>
  <th style="padding:8px 12px;text-align:left;">Report(s)</th>
  <th style="padding:8px 12px;text-align:left;">Status</th>
</tr>
{rows}</table>
<p style="margin-top:20px;">Best,<br>DataRetriever 🐕</p>
</body></html>"""

    try:
        result = send_email(to_addresses=SUMMARY_RECIPIENTS, subject=subject, html_body=body)
        print(f"📧 Summary → {SUMMARY_RECIPIENTS}: {result.get('messageId', 'N/A')}")
    except Exception as e:
        print(f"❌ Summary email failed: {e}")


def main():
    dry_run = "--dry-run" in sys.argv
    return send_with_retry(dry_run=dry_run)


if __name__ == "__main__":
    sys.exit(main())
