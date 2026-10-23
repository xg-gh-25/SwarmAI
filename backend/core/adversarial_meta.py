"""Adversarial Review Meta-Monitoring — detects degraded review quality.

Reads each completed Pipeline v1 run's ``run_audit.json`` (built by
``core.run_protocol.audit`` from the signed events): ``details.experts`` (one row per
review-expert dispatch) and ``details.findings``. When the 3 latest completed runs that
dispatched review experts all closed with 0 findings, it emits a degradation warning:
the review may be rubber-stamping and its prompt may need rotation. A run v1 sent no
expert (a small change) is not counted. A run with no audit (the retired pipeline) is
skipped.

Public symbols:
    - check_adversarial_health  — main function
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Number of consecutive reviewed runs with 0 findings that triggers the warning
CONSECUTIVE_THRESHOLD = 3


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def check_adversarial_health(artifacts_dir: Path) -> dict:
    """Analyze completed v1 runs under ``artifacts_dir/runs`` for review degradation.

    Returns:
        Dict with:
            - runs_analyzed: int (completed runs that dispatched review experts)
            - stats: list of {run_id, findings_total, experts}, oldest first
            - degradation_warning: bool
            - consecutive_zero_count: int (trailing streak)
    """
    runs_dir = artifacts_dir / "runs"
    if not runs_dir.is_dir():
        return {
            "runs_analyzed": 0,
            "stats": [],
            "degradation_warning": False,
            "consecutive_zero_count": 0,
        }

    rows: list[tuple[str, dict]] = []
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        run_data = _read(run_dir / "run.json")
        audit = _read(run_dir / "run_audit.json")
        if not isinstance(run_data, dict) or not isinstance(audit, dict):
            continue
        if run_data.get("status") != "completed":
            continue
        details = audit.get("details") if isinstance(audit.get("details"), dict) else {}
        experts = details.get("experts") if isinstance(details.get("experts"), list) else []
        findings = details.get("findings") if isinstance(details.get("findings"), list) else []
        if not experts:
            continue  # v1 reviewed nothing (a small change): 0 findings is expected
        rows.append((str(run_data.get("created_at") or ""), {
            "run_id": run_data.get("id", run_dir.name),
            "findings_total": len(findings),
            "experts": len(experts),
        }))
    stats = [row for _created, row in sorted(rows, key=lambda r: (r[0], r[1]["run_id"]))]

    # Detect degradation: the trailing streak of reviewed runs with 0 findings
    consecutive_zero = 0

    for entry in stats:
        if entry["findings_total"] == 0:
            consecutive_zero += 1
        else:
            consecutive_zero = 0

    # consecutive_zero now holds the trailing streak length
    degradation_warning = consecutive_zero >= CONSECUTIVE_THRESHOLD

    if degradation_warning:
        logger.warning(
            "adversarial_meta: DEGRADATION WARNING — %d consecutive reviewed runs "
            "had 0 adversarial findings. Review prompt may need rotation.",
            consecutive_zero,
        )

    result = {
        "runs_analyzed": len(stats),
        "stats": stats,
        "degradation_warning": degradation_warning,
        "consecutive_zero_count": consecutive_zero,
    }

    # Persist stats
    try:
        stats_file = artifacts_dir / "adversarial_stats.json"
        stats_file.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("adversarial_meta: failed to persist stats: %s", exc)

    return result
