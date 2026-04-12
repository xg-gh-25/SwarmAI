#!/usr/bin/env python3
"""
Weekly Report Generator — CLI entry point.

Supports two scopes:
  --scope gcr          GCR Overall (CEO-level): breakdown by sh_l3
  --scope "ISV & SUP"  Per-BU (GM-level): breakdown by sh_l4
  --scope all          Generate gcr + all 13 sh_l3 BUs

Templates:
  --template summary   2-card single-page layout
  --template detailed  3-tab interactive layout (⚡Usage → 💰Revenue → 📋Overall)

Usage:
  python generator.py --scope gcr
  python generator.py --scope gcr --template detailed
  python generator.py --scope "ISV & SUP"
  python generator.py --scope all
  python generator.py --scope all --publish   # also upload to S3 + write OpenSearch metadata
"""

import argparse
import os
import sys
import time
from datetime import datetime

# Force unbuffered stdout for real-time progress
sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, "/home/ubuntu/.openclaw/workspace/infra/data-proxy")
from client import DataProxy

from data import (
    find_anchor_and_weeks,
    fetch_all_bu_names,
    fetch_gcr_data,
    fetch_detailed_data,
    fetch_summary_data,
    compute_6_weeks,
    fetch_6w_segment_data_split,
    fetch_6w_account_data_split,
    fetch_total_movers,
)
from utils import safe_name
from templates.summary import generate_summary_html
from templates.detailed import generate_detailed_html
from templates.ceo_lite import generate_ceo_lite_html
from metadata import extract_report_metadata, embed_metadata_in_html

PROXY_URL = "https://thuedsrtpc.execute-api.cn-northwest-1.amazonaws.com.cn/prod/"
PROXY_KEY = "zV76dHel_s61cwljvPkFnNtCh7nhFS0XhImzteuVRfw"
OUTPUT_DIR = "/home/ubuntu/.openclaw/workspace/output/weekly-revenue-report/latest"

S3_REPORT_PREFIX = "reports/weekly-revenue-report"


def _upload_to_s3(proxy, local_path, s3_key):
    """Upload a report HTML file to S3 via presigned PUT URL."""
    proxy.upload_report_via_presigned_url(local_path, s3_key)
    return s3_key


def _publish_metadata(proxy, metadata, weeks, scope_label, scope_is_gcr, s3_key):
    """Write report metadata to OpenSearch via Data Proxy."""
    cw_end = datetime.strptime(weeks["cw_end"], "%Y-%m-%d")
    iso_year, week_num, _ = cw_end.isocalendar()
    year = str(iso_year)

    hierarchy_level = "gcr" if scope_is_gcr else "l3"
    hierarchy_name = "GCR" if scope_is_gcr else scope_label

    proxy.os_publish_report_metadata(
        cycle_year=year,
        cycle_week=str(week_num),
        hierarchy_level=hierarchy_level,
        hierarchy_name=hierarchy_name,
        s3_key=s3_key,
        report_metadata=metadata,
        operator="dataretriever",
    )


def _publish_report(proxy, path, fname, metadata, weeks, scope_label, scope_is_gcr):
    """Upload HTML to S3 and write metadata to OpenSearch.

    Args:
        proxy: DataProxy instance
        path: local file path of the generated HTML
        fname: filename (e.g. "rfhc.html")
        metadata: report metadata dict (from extract_report_metadata)
        weeks: anchor/week dict
        scope_label: "GCR" or BU name
        scope_is_gcr: True if GCR overall
    """
    cw_end = datetime.strptime(weeks["cw_end"], "%Y-%m-%d")
    iso_year, week_num, _ = cw_end.isocalendar()
    year = str(iso_year)

    s3_key = f"{S3_REPORT_PREFIX}/{year}-W{week_num}/{fname}"

    _upload_to_s3(proxy, path, s3_key)
    _publish_metadata(proxy, metadata, weeks, scope_label, scope_is_gcr, s3_key)

    print(f"  📤 Published to S3 + OpenSearch ({s3_key})")


def _generate_one(proxy, scope_label, scope_is_gcr, template, weeks):
    """Generate a single report and return (html, filename, metadata).

    Args:
        scope_label: display name ("GCR" or BU name like "ISV & SUP")
        scope_is_gcr: True if GCR overall, False if per-BU
        template: "summary" or "detailed"
        weeks: anchor/week dict from find_anchor_and_weeks
    """
    if scope_is_gcr and template == "summary":
        data = fetch_gcr_data(proxy, weeks)
        html = generate_summary_html(data, weeks)
        return html, "gcr.html", None

    elif template == "detailed":
        # Detailed template (both GCR and per-BU)
        bu_name_for_query = None if scope_is_gcr else scope_label
        display_name = "GCR Overall" if scope_is_gcr else scope_label
        seg_label = "BU (sh_l3)" if scope_is_gcr else "Unit (sh_l4)"

        data = fetch_detailed_data(proxy, bu_name_for_query, weeks, scope_is_gcr=scope_is_gcr)

        # Compute 6-week windows
        six_weeks = compute_6_weeks(weeks["cw_end"])
        print(f"  6W: {six_weeks[0]['label']} ({six_weeks[0]['start']}) ~ "
              f"{six_weeks[-1]['label']} ({six_weeks[-1]['end']})")

        # Fetch segment 6W trend
        seg_6w = fetch_6w_segment_data_split(proxy, six_weeks,
                                             scope_is_gcr=scope_is_gcr,
                                             bu_name=bu_name_for_query)

        # Collect all account names (top10 + all movers)
        all_names = set()
        for r in data["acct_rows_by_rev"]:
            all_names.add(r["name"])
        for r in data["acct_rows_by_usg"]:
            all_names.add(r["name"])
        for cat in data["movers"].values():
            all_names |= set(m["name"] for m in cat["accel"])
            all_names |= set(m["name"] for m in cat["decel"])

        # Fetch total movers (no genai_flag filter)
        mover_bu = None if scope_is_gcr else scope_label
        print("  Fetching Total Usage movers...")
        tu_a, tu_d = fetch_total_movers(proxy, mover_bu, weeks, "usage")
        print("  Fetching Total Revenue movers...")
        tr_a, tr_d = fetch_total_movers(proxy, mover_bu, weeks, "revenue")
        total_movers_data = {
            "total_usg": {"accel": tu_a, "decel": tu_d},
            "total_rev": {"accel": tr_a, "decel": tr_d},
        }
        # Add total mover names
        for cat in total_movers_data.values():
            all_names |= set(m["name"] for m in cat["accel"])
            all_names |= set(m["name"] for m in cat["decel"])

        # Fetch account 6W trend for ALL relevant accounts
        acct_6w = fetch_6w_account_data_split(proxy, six_weeks, list(all_names),
                                              scope_is_gcr=scope_is_gcr,
                                              bu_name=bu_name_for_query)
        print(f"  Account 6W: {len(all_names)} accounts")

        # Use CEO Lite template for GCR (single-page, email-safe)
        gen_fn = generate_ceo_lite_html if scope_is_gcr else generate_detailed_html
        template_name = "ceo_lite" if scope_is_gcr else "detailed"
        html = gen_fn(display_name, data, weeks, six_weeks,
                      seg_6w, acct_6w, total_movers_data,
                      segment_label=seg_label)

        # Extract metadata and embed in HTML
        metadata = extract_report_metadata(
            data, weeks, six_weeks, scope_label, scope_is_gcr,
            template_name, total_movers_data)
        html = embed_metadata_in_html(html, metadata)

        if scope_is_gcr:
            return html, "gcr_detailed.html", metadata
        else:
            sn = safe_name(scope_label)
            return html, f"{sn}.html", metadata

    else:
        data = fetch_summary_data(proxy, scope_label, weeks)
        html = generate_summary_html(data, weeks,
                                     title_override=f"{scope_label} — Weekly Revenue & Usage Report")
        sn = safe_name(scope_label)
        return html, f"{sn}_summary.html", None


def main():
    parser = argparse.ArgumentParser(description="Weekly Revenue & Usage Report Generator")
    parser.add_argument("--scope", required=True,
                        help='Report scope: "gcr" for GCR overall, a BU name (e.g. "ISV & SUP"), '
                             'or "all" for gcr + all BUs')
    parser.add_argument("--template", choices=["summary", "detailed"], default=None,
                        help='Template: "summary" (2 cards, table only) or "detailed" '
                             '(3-tab interactive). Default: summary for gcr, detailed for BU')
    parser.add_argument("--publish", action="store_true", default=False,
                        help='After generating, upload HTML to S3 and write metadata to OpenSearch')
    args = parser.parse_args()

    proxy = DataProxy(url=PROXY_URL, api_key=PROXY_KEY, timeout=120)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Finding data anchor...")
    weeks = find_anchor_and_weeks(proxy)
    print(f"  CW: {weeks['cw_start']} ~ {weeks['cw_end']} ({weeks['cw_days']} days)")
    print(f"  PW: {weeks['pw_start']} ~ {weeks['pw_end']} ({weeks['pw_days']} days)")
    print()

    scope = args.scope.strip()

    # Resolve default template
    template = args.template
    if template is None:
        template = "summary" if scope.lower() == "gcr" else "detailed"

    if scope.lower() == "all":
        bu_list = fetch_all_bu_names(proxy, weeks)
        total_count = 1 + len(bu_list)
        results = []
        total_start = time.time()

        # 1. GCR
        t0 = time.time()
        print(f"[1/{total_count}] Generating gcr ...")
        html, fname, metadata = _generate_one(proxy, "GCR", True, template, weeks)
        path = os.path.join(OUTPUT_DIR, fname)
        with open(path, "w") as f:
            f.write(html)
        elapsed = time.time() - t0
        print(f"[1/{total_count}] {fname} ✅ ({elapsed:.0f}s)")
        results.append(("gcr", path))
        if args.publish and metadata:
            _publish_report(proxy, path, fname, metadata, weeks, "GCR", True)

        # 2. Each BU
        for i, bu in enumerate(bu_list, 2):
            sn = safe_name(bu)
            t0 = time.time()
            print(f"\n[{i}/{total_count}] Generating {sn} ...")
            try:
                html, fname, metadata = _generate_one(proxy, bu, False, template, weeks)
                path = os.path.join(OUTPUT_DIR, fname)
                with open(path, "w") as f:
                    f.write(html)
                elapsed = time.time() - t0
                print(f"[{i}/{total_count}] {fname} ✅ ({elapsed:.0f}s)")
                results.append((bu, path))
                if args.publish and metadata:
                    _publish_report(proxy, path, fname, metadata, weeks, bu, False)
            except Exception as e:
                elapsed = time.time() - t0
                print(f"[{i}/{total_count}] {sn} ❌ Error: {e} ({elapsed:.0f}s)")
                results.append((bu, None))

        # Summary
        total_elapsed = time.time() - total_start
        ok = sum(1 for _, p in results if p)
        fail = sum(1 for _, p in results if not p)
        print(f"\n{'='*50}")
        print(f"Done! {ok} succeeded, {fail} failed out of {total_count} ({total_elapsed:.0f}s total)")
        print(f"Template: {template}")
        print(f"Output: {OUTPUT_DIR}")
        for name, path in results:
            status = "✅" if path else "❌"
            print(f"  {status} {name}")

    elif scope.lower() == "gcr":
        t0 = time.time()
        print(f"Generating GCR overall report (template={template})...")
        html, fname, metadata = _generate_one(proxy, "GCR", True, template, weeks)
        path = os.path.join(OUTPUT_DIR, fname)
        with open(path, "w") as f:
            f.write(html)
        elapsed = time.time() - t0
        print(f"\n[1/1] {fname} ✅ ({elapsed:.0f}s)")
        print(f"Output: {path}")
        if args.publish and metadata:
            _publish_report(proxy, path, fname, metadata, weeks, "GCR", True)

    else:
        bu_name = scope
        t0 = time.time()
        print(f"Generating report for BU: {bu_name} (template={template})...")
        html, fname, metadata = _generate_one(proxy, bu_name, False, template, weeks)
        path = os.path.join(OUTPUT_DIR, fname)
        with open(path, "w") as f:
            f.write(html)
        elapsed = time.time() - t0
        print(f"\n[1/1] {fname} ✅ ({elapsed:.0f}s)")
        print(f"Output: {path}")
        if args.publish and metadata:
            _publish_report(proxy, path, fname, metadata, weeks, bu_name, False)


if __name__ == "__main__":
    main()
