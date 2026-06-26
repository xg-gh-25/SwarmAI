#!/usr/bin/env python3
"""stamp_golden_cases.py — one-shot migration: stamp gate-eligible golden cases
with their content-bound validated_by_4gate hash (run_5edf2cc0 C3, gap G1).

WHY a script and not the validator: validate_case computes the stamp but has no
write path (Gate-1 BLOCK-B). The only sanctioned writer is
EvalService.update_case → _persist_golden_set (holds the file locks, strips
_origin, keeps the public/private split). This script walks every gate-eligible
non-draft case and writes its stamp through that path, so compute_bvt (which
requires a matching stamp) keeps a NON-EMPTY green set after the C2 tightening.

Legacy cases are stamped as-is (grandfathered for gate_teeth — they predate the
negative_command requirement). Idempotent: a case already carrying the correct
stamp is skipped. Run with --dry-run to preview.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.eval_service import EvalService  # noqa: E402
from scripts.golden_case_validator import (  # noqa: E402
    compute_case_stamp,
    _is_gate_eligible,
)


def stamp_all(svc: EvalService, dry_run: bool = False) -> dict:
    """Stamp every gate-eligible non-draft case. Returns a summary dict.

    Reads svc._cases (the RAW on-disk case set), NOT get_golden_set() — the latter
    is an allowlisted projection that injects read-time fields (last_result) and
    drops others, so a stamp computed over it would never match the full case that
    compute_bvt/load_golden_set sees on disk. The stamp must be over the raw body."""
    cases = svc._cases
    stamped, skipped, already = [], [], []
    for c in cases:
        if not _is_gate_eligible(c) or c.get("tier") == "draft":
            skipped.append(c.get("id"))
            continue
        want = compute_case_stamp(c)
        if c.get("validated_by_4gate") == want:
            already.append(c.get("id"))
            continue
        stamped.append(c.get("id"))
        if not dry_run:
            svc.update_case(c["id"], {"validated_by_4gate": want})
    return {"stamped": stamped, "already": already, "skipped_non_eligible": len(skipped),
            "dry_run": dry_run}


def main() -> int:
    ap = argparse.ArgumentParser(description="Stamp gate-eligible golden cases.")
    ap.add_argument("--dry-run", action="store_true", help="preview, do not write")
    args = ap.parse_args()
    svc = EvalService()
    summary = stamp_all(svc, dry_run=args.dry_run)
    print(f"{'DRY-RUN ' if args.dry_run else ''}stamped={len(summary['stamped'])} "
          f"already={len(summary['already'])} non-eligible-skipped={summary['skipped_non_eligible']}")
    if summary["stamped"]:
        print("  newly stamped:", ", ".join(summary["stamped"][:20]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
