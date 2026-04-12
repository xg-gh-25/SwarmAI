#!/usr/bin/env python3
"""GTM Report Generator -- renders reports from pre-computed data JSON.

Pure template rendering: no data fetching. Takes a JSON data file and
generates Rob/CEO summary HTML, GM detailed HTML, or working-level Excel.

Usage:
    python generator.py --data /tmp/analysis.json --template all --output /tmp/report/
"""
import argparse
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))

# Import templates from sibling skill
sys.path.insert(0, os.path.join(SKILL_DIR, '..', 's_industry-gtm-analysis'))
from templates.rob_summary import render_rob_summary
from templates.gm_detailed import render_gm_detailed
from templates.excel_builder import build_excel


def parse_args():
    """Parse CLI arguments."""
    p = argparse.ArgumentParser(description="GTM Report Generator — render from pre-computed data")
    p.add_argument("--data", required=True, help="Path to JSON data file")
    p.add_argument("--template", default="all", choices=["rob", "gm", "excel", "all"],
                   help="Template to render (default: all)")
    p.add_argument("--product", default="agentcore", help="AWS product name (default: agentcore)")
    p.add_argument("--output", default="/tmp/gtm-report/", help="Output directory")
    return p.parse_args()


def load_data(path: str) -> dict:
    """
    Read JSON data file.

    Args:
        path: path to the JSON file

    Returns:
        Parsed dict from JSON.

    Raises:
        FileNotFoundError: if the file doesn't exist
        json.JSONDecodeError: if the file isn't valid JSON
    """
    with open(path) as f:
        return json.load(f)


def load_product_knowledge(product: str) -> list[dict]:
    """
    Read product knowledge from knowledge directory.

    Looks in this skill's knowledge dir first, then falls back
    to s_industry-gtm-analysis/knowledge.

    Args:
        product: product name (e.g., "agentcore")

    Returns:
        List of component dicts parsed from the product markdown file.
    """
    # Try local knowledge dir first
    local_path = os.path.join(SKILL_DIR, "knowledge", f"{product}.md")
    # Fall back to sibling skill
    sibling_path = os.path.join(SKILL_DIR, "..", "s_industry-gtm-analysis", "knowledge", f"{product}.md")

    knowledge_path = local_path if os.path.exists(local_path) else sibling_path

    if not os.path.exists(knowledge_path):
        return []

    with open(knowledge_path) as f:
        content = f.read()

    components = []
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


def generate(data: dict, template: str = "all", product: str = "agentcore",
             output_dir: str = "/tmp/gtm-report/") -> dict:
    """
    Generate reports from pre-computed data.

    Args:
        data: pre-computed analysis data dict
        template: which template to render ("rob", "gm", "excel", "all")
        product: AWS product name
        output_dir: where to write output files

    Returns:
        Dict mapping template names to output file paths.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Inject product knowledge if not already present
    if not data.get("product_components"):
        data["product_components"] = load_product_knowledge(product)

    # Override product name if specified
    if product:
        data["product"] = product

    outputs = {}

    if template in ("rob", "all"):
        rob_path = os.path.join(output_dir, "rob_summary.html")
        html = render_rob_summary(data)
        with open(rob_path, "w", encoding="utf-8") as f:
            f.write(html)
        outputs["rob"] = rob_path

    if template in ("gm", "all"):
        gm_path = os.path.join(output_dir, "gm_detailed.html")
        html = render_gm_detailed(data)
        with open(gm_path, "w", encoding="utf-8") as f:
            f.write(html)
        outputs["gm"] = gm_path

    if template in ("excel", "all"):
        # Excel requires accounts list; check if data has it
        accounts = data.get("accounts", [])
        if accounts:
            excel_path = os.path.join(output_dir, "gtm_analysis.xlsx")
            categories = data.get("categories", {})
            penetration = data.get("bedrock_penetration", {})
            build_excel(accounts, excel_path, product=product,
                        categories=categories, bedrock_penetration=penetration)
            outputs["excel"] = excel_path

    return outputs


def main():
    args = parse_args()
    data = load_data(args.data)
    outputs = generate(data, template=args.template, product=args.product,
                       output_dir=args.output)
    for name, path in outputs.items():
        print(f"{name}: {path}")
    if outputs:
        print(f"\nDone. {len(outputs)} report(s) generated in {args.output}")
    else:
        print("No reports generated. Check data format and template selection.")


if __name__ == "__main__":
    main()
