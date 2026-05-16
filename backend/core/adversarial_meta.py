"""Adversarial Review Meta-Monitoring — detects degraded review quality.

Tracks per-run adversarial findings count. When 3+ consecutive runs with >50
changed lines have 0 findings, emits a degradation warning. This signals that
the adversarial review prompt may need rotation or the review is rubber-stamping.

Public symbols:
    - check_adversarial_health  — main function
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Configuration
CONSECUTIVE_THRESHOLD = 3  # Number of consecutive 0-finding runs to trigger warning
MIN_CHANGED_LINES = 50  # Only count runs with significant changes


def check_adversarial_health(artifacts_dir: Path) -> dict:
    """Analyze recent pipeline runs for adversarial review degradation.

    Reads run.json + deliver artifacts from .artifacts/runs/. Tracks findings
    count per run. Warns when the review appears to be rubber-stamping.

    Args:
        artifacts_dir: Path to .artifacts/ directory (contains runs/ subdir)

    Returns:
        Dict with:
            - runs_analyzed: int
            - stats: list of {run_id, findings_total, files_changed}
            - degradation_warning: bool
            - consecutive_zero_count: int
    """
    runs_dir = artifacts_dir / "runs"
    if not runs_dir.is_dir():
        return {
            "runs_analyzed": 0,
            "stats": [],
            "degradation_warning": False,
            "consecutive_zero_count": 0,
        }

    stats: list[dict] = []

    # Scan all completed runs with deliver stage
    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue

        run_json = run_dir / "run.json"
        if not run_json.exists():
            continue

        try:
            run_data = json.loads(run_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        if run_data.get("status") != "completed":
            continue

        # Find deliver stage
        stages = run_data.get("stages", [])
        deliver_stage = None
        for stage in stages:
            if isinstance(stage, dict) and stage.get("stage") == "deliver":
                deliver_stage = stage
                break

        if not deliver_stage:
            continue

        # Try to read deliver artifact for adversarial data
        art_id = deliver_stage.get("artifact_id", "")
        findings_total = 0
        files_changed = 0

        # Fix F3: skip artifact lookup when art_id is empty
        if art_id:
            for art_path in [
                artifacts_dir / f"{art_id}.json",
                run_dir / f"{art_id}.json",
            ]:
                if art_path.exists():
                    try:
                        art_data = json.loads(art_path.read_text(encoding="utf-8"))
                        ar = art_data.get("adversarial_review", {})
                        findings_total = ar.get("findings_total", 0)
                        # Fix F1: files_changed can be list (paths) or int
                        fc = art_data.get("files_changed", 0)
                        files_changed = len(fc) if isinstance(fc, list) else (
                            fc if isinstance(fc, int) else 0
                        )
                        break
                    except (json.JSONDecodeError, OSError):
                        pass

        # Also check build artifact for files_changed if deliver didn't have it
        if files_changed == 0:
            for stage in stages:
                if isinstance(stage, dict) and stage.get("stage") == "build":
                    build_art_id = stage.get("artifact_id", "")
                    if build_art_id:
                        for art_path in [
                            artifacts_dir / f"{build_art_id}.json",
                            run_dir / f"{build_art_id}.json",
                        ]:
                            if art_path.exists():
                                try:
                                    build_data = json.loads(art_path.read_text(encoding="utf-8"))
                                    fc = build_data.get("files_changed", 0)
                                    files_changed = len(fc) if isinstance(fc, list) else (
                                        fc if isinstance(fc, int) else 0
                                    )
                                except (json.JSONDecodeError, OSError):
                                    pass
                    break

        stats.append({
            "run_id": run_data.get("id", run_dir.name),
            "findings_total": findings_total,
            "files_changed": files_changed,
        })

    # Detect degradation: 3+ trailing consecutive runs with >50 lines and 0 findings
    # Fix F2: use trailing count (current streak at end), not historical max
    consecutive_zero = 0

    for entry in stats:
        if entry["files_changed"] > MIN_CHANGED_LINES and entry["findings_total"] == 0:
            consecutive_zero += 1
        else:
            consecutive_zero = 0

    # consecutive_zero now holds the trailing streak length
    degradation_warning = consecutive_zero >= CONSECUTIVE_THRESHOLD

    if degradation_warning:
        logger.warning(
            "adversarial_meta: DEGRADATION WARNING — %d consecutive runs "
            "with >%d changed lines had 0 adversarial findings. "
            "Review prompt may need rotation.",
            consecutive_zero,
            MIN_CHANGED_LINES,
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
