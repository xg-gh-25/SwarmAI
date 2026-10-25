"""Validate a retained App clean-room product-proof report.

Usage: python backend/scripts/check_app_product_proof.py <report.json>
Exit 0 means product-proven, 1 means valid but blocked, and 64 means invalid input.
The command never creates evidence; the clean-room runner must record the facts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import ValidationError

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.app_product_proof import ProductProofReport, evaluate_product_proof


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_app_product_proof.py <report.json>", file=sys.stderr)
        return 64
    try:
        raw = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        report = ProductProofReport.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"invalid product-proof report: {exc}", file=sys.stderr)
        return 64
    result = evaluate_product_proof(report)
    payload = result.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if result.evidence_state == "product-proven" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
