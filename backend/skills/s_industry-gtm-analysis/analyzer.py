#!/usr/bin/env python3
"""
Industry GTM Analysis — CLI entry point.
Orchestrates: load accounts → query revenue → merge → generate reports.

Usage:
    python analyzer.py --bu "AUTO & MFG" --product agentcore --accounts /tmp/accounts.json --output /tmp/gtm/
"""
import argparse
import json
import os
import sys

# Add skill dir to path
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

from data import query_revenue, merge_accounts_with_revenue
from templates.rob_summary import render_rob_summary
from templates.gm_detailed import render_gm_detailed
from templates.excel_builder import build_excel


def parse_args():
    p = argparse.ArgumentParser(description="Industry GTM Analysis Report Generator")
    p.add_argument("--bu", required=True, help="sh_l3 BU name (e.g., 'AUTO & MFG')")
    p.add_argument("--product", required=True, help="AWS product (e.g., agentcore, bedrock)")
    p.add_argument("--tshirt", default="L", choices=["L", "XL", "XXL"], help="Min T-shirt size")
    p.add_argument("--accounts", required=True, help="Path to Sentral accounts JSON")
    p.add_argument("--template", default="all", choices=["rob", "gm", "excel", "all"])
    p.add_argument("--output", default="/tmp/gtm-output/", help="Output directory")
    return p.parse_args()


def run_analysis(bu_name: str, product: str, accounts_path: str,
                 tshirt_min: str = "L", template: str = "all",
                 output_dir: str = "/tmp/gtm-output/") -> dict:
    """
    Run the full GTM analysis pipeline.

    Returns dict with paths to generated files.
    """
    os.makedirs(output_dir, exist_ok=True)

    # 1. Load accounts from Sentral export
    with open(accounts_path) as f:
        accounts = json.load(f)
    print(f"Loaded {len(accounts)} accounts from {accounts_path}")

    # 2. Query revenue from Athena
    print(f"Querying revenue for BU: {bu_name}...")
    revenue = query_revenue(bu_name, tshirt_min)
    print(f"Got revenue for {len(revenue)} accounts")

    # Save raw revenue
    rev_path = os.path.join(output_dir, "revenue_data.json")
    with open(rev_path, "w") as f:
        json.dump(revenue, f, indent=2)

    # 3. Merge accounts with revenue
    accounts = merge_accounts_with_revenue(accounts, revenue)

    # Filter by tshirt
    size_order = {"XXL": 3, "XL": 2, "L": 1}
    min_ord = size_order.get(tshirt_min, 1)
    accounts = [a for a in accounts if size_order.get(a.get("size", ""), 0) >= min_ord]
    print(f"After T-shirt filter ({tshirt_min}+): {len(accounts)} accounts")

    # 4. Compute summary stats
    total_ttm = sum(float(a.get("ttm", 0)) for a in accounts)
    total_genai = sum(float(a.get("genai", 0)) for a in accounts)
    total_bedrock = sum(float(a.get("bedrock", 0)) for a in accounts)

    top_accounts = sorted(accounts, key=lambda x: -float(x.get("ttm", 0)))
    bedrock_accounts = sorted(
        [a for a in accounts if float(a.get("bedrock", 0)) > 0],
        key=lambda x: -float(x.get("bedrock", 0))
    )

    summary_data = {
        "bu_name": bu_name,
        "product": product,
        "total_accounts": len(accounts),
        "total_ttm": total_ttm,
        "total_genai": total_genai,
        "total_bedrock": total_bedrock,
        "auto_count": sum(1 for a in accounts if a.get("industry") == "AUTO"),
        "mfg_count": sum(1 for a in accounts if a.get("industry") != "AUTO"),
        "top_accounts": [
            {"name": a.get("short", a.get("name", "")), "ttm": float(a.get("ttm", 0)),
             "genai": float(a.get("genai", 0)), "bedrock": float(a.get("bedrock", 0))}
            for a in top_accounts[:20]
        ],
        "bedrock_accounts": [
            {"name": a.get("short", a.get("name", "")), "bedrock": float(a.get("bedrock", 0)),
             "genai": float(a.get("genai", 0))}
            for a in bedrock_accounts[:15]
        ],
        "categories": {},
        "plays": [],
        "scenarios": [],
        "competitive": [],
    }

    outputs = {"revenue": rev_path}

    # 5. Generate reports
    if template in ("rob", "all"):
        rob_path = os.path.join(output_dir, "rob_summary.html")
        html = render_rob_summary(summary_data)
        with open(rob_path, "w") as f:
            f.write(html)
        outputs["rob"] = rob_path
        print(f"Rob summary: {rob_path}")

    if template in ("gm", "all"):
        gm_path = os.path.join(output_dir, "gm_detailed.html")
        html = render_gm_detailed(summary_data)
        with open(gm_path, "w") as f:
            f.write(html)
        outputs["gm"] = gm_path
        print(f"GM detailed: {gm_path}")

    if template in ("excel", "all"):
        excel_path = os.path.join(output_dir, "gtm_analysis.xlsx")
        result = build_excel(accounts, excel_path, product=product)
        outputs["excel"] = excel_path
        print(f"Excel: {excel_path} ({result['total']} accounts: AUTO {result['auto']} + MFG {result['mfg']})")

    print(f"\n✅ Done. Outputs in {output_dir}")
    return outputs


def main():
    args = parse_args()
    run_analysis(
        bu_name=args.bu,
        product=args.product,
        accounts_path=args.accounts,
        tshirt_min=args.tshirt,
        template=args.template,
        output_dir=args.output,
    )


if __name__ == "__main__":
    main()
