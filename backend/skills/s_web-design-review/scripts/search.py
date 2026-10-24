# -*- coding: utf-8 -*-
"""
UX guideline search for the web-design-review skill (BM25 over data/ux-guidelines.csv).
Usage: python search.py "<query>" [--max-results 3] [--json]

This skill ships only the UX guidelines. The full style / color / typography search and the
design-system generator live in the frontend-design skill (s_frontend-design/scripts/search.py);
an earlier copy of that script here imported its design_system module, which this skill does
not have, so every call failed with ModuleNotFoundError.
"""

import argparse
import io
import json
import sys

from core import MAX_RESULTS, search

# Force UTF-8 for stdout/stderr to handle emojis on Windows (cp1252 default)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def format_output(result):
    """Format results for Claude consumption (token-optimized)"""
    if "error" in result:
        return f"Error: {result['error']}"
    output = [
        "## UX Guideline Search Results",
        f"**Query:** {result['query']}",
        f"**Source:** {result['file']} | **Found:** {result['count']} results\n",
    ]
    for i, row in enumerate(result['results'], 1):
        output.append(f"### Result {i}")
        for key, value in row.items():
            value_str = str(value)
            if len(value_str) > 300:
                value_str = value_str[:300] + "..."
            output.append(f"- **{key}:** {value_str}")
        output.append("")
    return "\n".join(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description="UX guideline search")
    parser.add_argument("query", help="Search query")
    # kept so the documented `--domain ux` calls still parse; ux is the only data this skill has
    parser.add_argument("--domain", "-d", choices=["ux"], default="ux", help=argparse.SUPPRESS)
    parser.add_argument("--max-results", "-n", type=int, default=MAX_RESULTS, help="Max results (default: 3)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args(argv)
    result = search(args.query, "ux", args.max_results)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_output(result))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
