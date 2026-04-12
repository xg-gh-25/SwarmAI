#!/usr/bin/env python3
"""
Industry GTM Analysis — CLI entry point.
Orchestrates: load accounts -> query revenue -> merge -> generate reports.

Usage:
    python analyzer.py --bu "AUTO & MFG" --product agentcore --accounts /tmp/accounts.json --output /tmp/gtm/
"""
import argparse
import json
import os
import re
import sys

# Add skill dir to path
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

from data import query_revenue, merge_accounts_with_revenue
from templates.rob_summary import render_rob_summary
from templates.gm_detailed import render_gm_detailed
from templates.excel_builder import build_excel

AUTO_CATS = {'汽车整车', '自动驾驶/智驾', '汽车零部件', '出行/两轮'}


def parse_args():
    p = argparse.ArgumentParser(description="Industry GTM Analysis Report Generator")
    p.add_argument("--bu", required=True, help="sh_l3 BU name (e.g., 'AUTO & MFG')")
    p.add_argument("--product", required=True, help="AWS product (e.g., agentcore, bedrock)")
    p.add_argument("--tshirt", default="L", choices=["L", "XL", "XXL"], help="Min T-shirt size")
    p.add_argument("--accounts", required=True, help="Path to Sentral accounts JSON")
    p.add_argument("--template", default="all", choices=["rob", "gm", "excel", "all"])
    p.add_argument("--output", default="/tmp/gtm-output/", help="Output directory")
    return p.parse_args()


def _load_product_knowledge(product: str) -> list[dict]:
    """Load product components from knowledge/{product}.md and parse the component table."""
    knowledge_path = os.path.join(SKILL_DIR, "knowledge", f"{product}.md")
    if not os.path.exists(knowledge_path):
        return []

    with open(knowledge_path) as f:
        content = f.read()

    components = []
    # Parse markdown table: | # | Component | Description | Key Use Case |
    in_table = False
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("| #"):
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table and line.startswith("|"):
            parts = [p.strip() for p in line.split("|")]
            # parts[0] is empty (before first |), parts[-1] is empty (after last |)
            parts = [p for p in parts if p]
            if len(parts) >= 4:
                components.append({
                    "num": parts[0],
                    "name": parts[1],
                    "desc": parts[2],
                    "use_case": parts[3],
                })
        elif in_table and not line.startswith("|"):
            in_table = False

    return components


def _compute_categories(accounts: list[dict]) -> dict:
    """
    Group accounts by category field. For each category, compute:
    - count, ttm, genai, bedrock sums
    - industry (AUTO or MFG)
    - xxl and xl account names
    """
    cats = {}
    for a in accounts:
        cat = a.get("category", "其他") or "其他"
        if cat not in cats:
            cats[cat] = {
                "count": 0, "ttm": 0.0, "genai": 0.0, "bedrock": 0.0,
                "industry": "AUTO" if cat in AUTO_CATS else "MFG",
                "xxl": [], "xl": [], "accounts": [],
            }
        c = cats[cat]
        c["count"] += 1
        c["ttm"] += float(a.get("ttm", 0))
        c["genai"] += float(a.get("genai", 0))
        c["bedrock"] += float(a.get("bedrock", 0))
        short = a.get("short", a.get("name", ""))
        c["accounts"].append(short)
        size = a.get("size", "")
        if size == "XXL":
            c["xxl"].append(short)
        elif size == "XL":
            c["xl"].append(short)

    return cats


def _compute_penetration(accounts: list[dict]) -> dict:
    """Compute bedrock and genai penetration stats."""
    total = len(accounts)
    with_bedrock = [a for a in accounts if float(a.get("bedrock", 0)) > 0]
    with_genai = [a for a in accounts if float(a.get("genai", 0)) > 0]

    bedrock_pct = (len(with_bedrock) / total * 100) if total > 0 else 0
    genai_pct = (len(with_genai) / total * 100) if total > 0 else 0

    return {
        "bedrock_penetration": {
            "total_with_bedrock": len(with_bedrock),
            "total_accounts": total,
            "pct": round(bedrock_pct, 1),
        },
        "genai_penetration": {
            "total_with_genai": len(with_genai),
            "total_accounts": total,
            "pct": round(genai_pct, 1),
        },
    }


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

    # 4a. Compute category distribution
    categories = _compute_categories(accounts)

    # 4b. Compute penetration stats
    penetration = _compute_penetration(accounts)

    # 4c. Load product knowledge components
    product_components = _load_product_knowledge(product)

    # 4d. Classify top 20 accounts into priority quadrants
    # Quick Win = has bedrock + high TTM (top-right)
    # Strategic = high TTM but no bedrock (top-left)
    # Seed = has bedrock but low TTM (bottom-right)
    # Monitor = low TTM + no bedrock (bottom-left)
    sorted_by_ttm = sorted(accounts, key=lambda x: -float(x.get("ttm", 0)))
    ttm_median = float(sorted_by_ttm[len(sorted_by_ttm) // 2].get("ttm", 0)) if sorted_by_ttm else 0

    quick_win, strategic, seed, monitor = [], [], [], []
    for a in sorted_by_ttm[:20]:
        has_bedrock = float(a.get("bedrock", 0)) > 0
        high_ttm = float(a.get("ttm", 0)) >= ttm_median
        entry = {
            "name": a.get("short", a.get("name", "")),
            "ttm": float(a.get("ttm", 0)),
            "bedrock": float(a.get("bedrock", 0)),
            "genai": float(a.get("genai", 0)),
            "category": a.get("category", ""),
        }
        if has_bedrock and high_ttm:
            quick_win.append(entry)
        elif high_ttm and not has_bedrock:
            strategic.append(entry)
        elif has_bedrock and not high_ttm:
            seed.append(entry)
        else:
            monitor.append(entry)

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
             "genai": float(a.get("genai", 0)), "bedrock": float(a.get("bedrock", 0)),
             "category": a.get("category", "")}
            for a in top_accounts[:20]
        ],
        "bedrock_accounts": [
            {"name": a.get("short", a.get("name", "")), "bedrock": float(a.get("bedrock", 0)),
             "genai": float(a.get("genai", 0)), "ttm": float(a.get("ttm", 0)),
             "category": a.get("category", "")}
            for a in bedrock_accounts[:15]
        ],
        "categories": categories,
        "bedrock_penetration": penetration["bedrock_penetration"],
        "genai_penetration": penetration["genai_penetration"],
        "product_components": product_components,
        "priority": {
            "quick_win": quick_win,
            "strategic": strategic,
            "seed": seed,
            "monitor": monitor,
        },
        # Keep backward compat
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
        result = build_excel(accounts, excel_path, product=product,
                             categories=categories,
                             bedrock_penetration=penetration["bedrock_penetration"])
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
