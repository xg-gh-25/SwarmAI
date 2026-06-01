#!/usr/bin/env python3
"""Forecast Report Generator — CLI entry point.

Fetches forecast data from API + baseline tables, renders 4-tab HTML report.
Outputs .insights_data.json companion for LLM Step 2.

Unified template for all levels (UT1):
  CMHK (CEO):   --scope CMHK       → overall CORE/GenAI split
  GM (L3):      --scope RFHC       → BU-specific forecast

Usage:
  python generator.py --cycle latest --scope CMHK
  python generator.py --cycle fcst_2026_04 --scope RFHC
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.stdout.reconfigure(line_buffering=True)

# Project path resolution (single source of truth)
import sys as _sys_proj
from pathlib import Path as _PathProj
_SHARED_DIR = str(_PathProj(__file__).resolve().parents[2] / "_shared")
if _SHARED_DIR not in _sys_proj.path:
    _sys_proj.path.insert(0, _SHARED_DIR)
from project_paths import get_output_dir, CMHK_PROJECT

from client import DataProxyClient
from data import fetch_all_data
from templates.forecast_report import render

# BU → GM owner mapping (from USER.md org chart)
BU_OWNERS = {
    "STRATEGIC": ("gufan", "Fan Gu"),
    "FSI-DNB": ("kenshen", "Ken Shen"),
    "MEAGS": ("mzji", "Ken Li"),
    "RFHC": ("kenshen", "Ken Shen"),
    "ISV & SUP": ("zhangaz", "Alfonso Zhang"),
    "AUTO & MFG": ("tiafeng", "Feng Tian"),
    "SMB": ("danffer", "Danffer Ni"),
    "HK": ("akchan", "AK Chan"),
    "DNBP": ("danffer", "Danffer Ni"),
    "IND GFD": ("danffer", "Danffer Ni"),
    "PARTNER": ("chrisso", "Chris So"),
    "NWCD": ("danffer", "Danffer Ni"),
    "IND SS": ("danffer", "Danffer Ni"),
}


def _sf(val, default=0.0):
    if val is None or val == "" or val == "null":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def main():
    parser = argparse.ArgumentParser(description="Generate CMHK Forecast Report")
    parser.add_argument("--cycle", default="latest",
                        help="Forecast cycle ID (e.g. fcst_2026_04) or 'latest'")
    parser.add_argument("--scope", default="CMHK",
                        help="Scope: CMHK (all) or BU name (e.g. RFHC)")
    parser.add_argument("--output", default=None,
                        help="Output directory (default: Projects/CMHK_SalesIntel/outputs/)")
    parser.add_argument("--alias", default="fuxin",
                        help="Athena RLS alias (default: fuxin)")
    args = parser.parse_args()

    os.environ.setdefault("DATAPROXY_DEFAULT_ALIAS", args.alias)

    print(f"Generating Forecast Report — Cycle: {args.cycle}, Scope: {args.scope}")
    print("=" * 60)

    client = DataProxyClient()

    # ── Stage 1/4: Find cycle + Fetch baseline (BLOCKING) ──
    print("\n  [Stage 1/4] Finding forecast cycle + baseline...")
    from data import find_latest_cycle, fetch_forecast_baseline, fetch_forecast_view
    try:
        if args.cycle == "latest":
            cycle = find_latest_cycle(client)
            if not cycle:
                raise RuntimeError("No published forecast cycle found")
            cycle_id = cycle["cycleId"]
            print(f"  Latest cycle: {cycle_id} (month {cycle['month']})")
        else:
            cycle_id = args.cycle
        hierarchy_id = "GCR" if args.scope == "CMHK" else f"GCR/{args.scope}"
        baseline = fetch_forecast_baseline(client, cycle_id, hierarchy_id)
    except Exception as e:
        print(f"\n  [Stage 1/4] ❌ FATAL: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  Cycle: {cycle_id} | Target: {baseline.get('fyTargetRev', 'N/A')} | Gap: {baseline.get('fcstGap', 'N/A')}")

    # ── Stage 2/4: Fetch forecast view (BLOCKING — core rendering data) ──
    print("\n  [Stage 2/4] Fetching forecast view (CORE + GenAI monthly)...")
    try:
        view = fetch_forecast_view(client, cycle_id, hierarchy_id)
    except Exception as e:
        print(f"\n  [Stage 2/4] ❌ FATAL: {e}", file=sys.stderr)
        sys.exit(1)
    core_monthly = view.get("core", {}).get("monthlyData", []) if isinstance(view.get("core"), dict) else []
    genai_monthly = view.get("genai", {}).get("monthlyData", []) if isinstance(view.get("genai"), dict) else []
    print(f"  Core months: {len(core_monthly)} | GenAI months: {len(genai_monthly)}")

    # ── Stage 3/4: Opportunities + Risks + GenAI Bedrock Split (optional) ──
    print("\n  [Stage 3/4] Opportunities + Risks + GenAI Bedrock Split...")
    opportunities = []
    risks = []
    genai_bedrock_split = {"bedrock": [], "non_bedrock": []}
    try:
        from data import fetch_top_opportunities, fetch_top_risks, fetch_genai_bedrock_split
        opportunities = fetch_top_opportunities(client, cycle_id)
        print(f"  [Stage 3/4] Opportunities: {len(opportunities)}")
    except Exception as e:
        print(f"  [Stage 3/4] ⚠️ Opportunities failed: {e} — continuing without")
    try:
        risks = fetch_top_risks(client, cycle_id)
        print(f"  [Stage 3/4] Risks: {len(risks)}")
    except Exception as e:
        print(f"  [Stage 3/4] ⚠️ Risks failed: {e} — continuing without")
    try:
        genai_bedrock_split = fetch_genai_bedrock_split(client, cycle_id, hierarchy_id)
        br_count = len(genai_bedrock_split.get("bedrock", []))
        nbr_count = len(genai_bedrock_split.get("non_bedrock", []))
        print(f"  [Stage 3/4] GenAI Bedrock split: {br_count} Bedrock months, {nbr_count} Non-Bedrock months")
    except Exception as e:
        print(f"  [Stage 3/4] ⚠️ GenAI Bedrock split failed: {e} — continuing without")

    # ── Stage 4/4: Render + insights_data ──
    print(f"\n  [Stage 4/4] Rendering...")
    data = {
        "cycle_id": cycle_id,
        "baseline": baseline,
        "view": view,
        "opportunities": opportunities,
        "risks": risks,
        "genai_bedrock_split": genai_bedrock_split,
    }
    html = render(data, insights=None)

    # ── Build insights_data (compact summary for LLM Step 2) ──
    insights_data = {
        "scope": args.scope,
        "period": "forecast",
        "cycle_id": cycle_id,
        "baseline": {
            "fy_target_m": round(_sf(baseline.get("fyTargetRev")) / 1e6, 2) if baseline.get("fyTargetRev") else None,
            "ytd_m": round(_sf(baseline.get("ytdFbr")) / 1e6, 2) if baseline.get("ytdFbr") else None,
            "roy_m": round(_sf(baseline.get("royRevenue")) / 1e6, 2) if baseline.get("royRevenue") else None,
            "gap_m": round(_sf(baseline.get("fcstGap")) / 1e6, 2) if baseline.get("fcstGap") else None,
            "roy_risk_m": round(_sf(baseline.get("royRisk")) / 1e6, 2) if baseline.get("royRisk") else None,
            "roy_opp_m": round(_sf(baseline.get("royOpp")) / 1e6, 2) if baseline.get("royOpp") else None,
        },
        "monthly_trajectory": {
            "core": [
                {"month": m.get("month", ""), "fbr_m": round(_sf(m.get("fbr")) / 1e6, 2),
                 "fcst_m": round(_sf(m.get("fcst")) / 1e6, 2),
                 "target_m": round(_sf(m.get("target")) / 1e6, 2)}
                for m in core_monthly[:12]
            ],
            "genai": [
                {"month": m.get("month", ""), "fbr_m": round(_sf(m.get("fbr")) / 1e6, 2),
                 "fcst_m": round(_sf(m.get("fcst")) / 1e6, 2),
                 "target_m": round(_sf(m.get("target")) / 1e6, 2)}
                for m in genai_monthly[:12]
            ],
        },
        "top_opportunities": [
            {"name": o.get("opportunity_name", "")[:60],
             "account": o.get("account_name", ""),
             "owner": o.get("territory_owner", ""),
             "prr_m": round(_sf(o.get("total_prr")) / 1e6, 2),
             "stage": o.get("stage", "")}
            for o in opportunities[:10]
        ],
        "top_risks": [
            {"name": r.get("risk_name", "")[:60],
             "account": r.get("account_name", ""),
             "owner": r.get("territory_owner", ""),
             "impact_m": round(_sf(r.get("total_impact")) / 1e6, 2),
             "status": r.get("risk_status", "")}
            for r in risks[:10]
        ],
        "genai_bedrock_split": {
            "bedrock": [
                {"month": e.get("month", ""), "revenue_m": round(e.get("revenue", 0) / 1e6, 2)}
                for e in genai_bedrock_split.get("bedrock", [])
            ],
            "non_bedrock": [
                {"month": e.get("month", ""), "revenue_m": round(e.get("revenue", 0) / 1e6, 2)}
                for e in genai_bedrock_split.get("non_bedrock", [])
            ],
        },
        "bu_owners": BU_OWNERS,
    }

    # ── Save outputs ──
    output_dir = args.output or str(get_output_dir())
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    safe_scope = re.sub(r"[^\w\-]", "_", args.scope.lower())[:30]
    cycle_short = data.get("cycle_id", "unknown").replace("fcst_", "")
    filename = f"{timestamp}-forecast-{safe_scope}-{cycle_short}.html"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    # Write insights_data JSON companion file
    json_path = filepath.replace(".html", ".insights_data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(insights_data, f, ensure_ascii=False, indent=2)

    print(f"\n  Saved: {filepath}")
    print(f"  Data:  {json_path} (for Step 2 insights generation)")
    print(f"  Open:  open '{filepath}'")


if __name__ == "__main__":
    main()
