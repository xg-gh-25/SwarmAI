#!/usr/bin/env python3
"""WTF gate scorer for TEST stage — determines if fixes are getting too risky.

Accepts CLI args for individual risk factors. Produces a deterministic
halt/pass decision.

Usage:
    python scripts/wtf_gate.py --files-touched 4 --fix-count 12
    python scripts/wtf_gate.py --files-touched 5 --fix-count 2 --unrelated-module true

Output (JSON):
    {
        "score": 3,
        "breakdown": ["+2: fix touches > 3 files"],
        "halt": false,
        "threshold": 5
    }
"""
import argparse
import json
import sys


def _parse_bool(value: str) -> bool:
    """Parse string to bool for CLI args."""
    return value.lower() in ("true", "1", "yes")


def calculate_wtf(
    files_touched: int = 0,
    fix_count: int = 0,
    unrelated_module: bool = False,
    api_contract_changed: bool = False,
    previous_fix_broke: bool = False,
) -> dict:
    """Calculate WTF gate score from risk factors."""
    score = 0
    breakdown = []

    # +2: fix touches > 3 files
    if files_touched > 3:
        score += 2
        breakdown.append(f"+2: fix touches {files_touched} files (> 3)")

    # +3: fix modifies unrelated module
    if unrelated_module:
        score += 3
        breakdown.append("+3: fix modifies unrelated module")

    # +2: fix changes API contract
    if api_contract_changed:
        score += 2
        breakdown.append("+2: fix changes API contract")

    # +1: fix_count > 10
    if fix_count > 10:
        score += 1
        breakdown.append(f"+1: fix count = {fix_count} (> 10)")

    # +3: previous fix broke something
    if previous_fix_broke:
        score += 3
        breakdown.append("+3: previous fix broke something")

    threshold = 5
    return {
        "score": score,
        "breakdown": breakdown,
        "halt": score >= threshold,
        "threshold": threshold,
    }


def main():
    parser = argparse.ArgumentParser(description="WTF gate scoring")
    parser.add_argument("--files-touched", type=int, default=0)
    parser.add_argument("--fix-count", type=int, default=0)
    parser.add_argument("--unrelated-module", type=_parse_bool, default=False)
    parser.add_argument("--api-contract-changed", type=_parse_bool, default=False)
    parser.add_argument("--previous-fix-broke", type=_parse_bool, default=False)
    args = parser.parse_args()

    result = calculate_wtf(
        files_touched=args.files_touched,
        fix_count=args.fix_count,
        unrelated_module=args.unrelated_module,
        api_contract_changed=args.api_contract_changed,
        previous_fix_broke=args.previous_fix_broke,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
