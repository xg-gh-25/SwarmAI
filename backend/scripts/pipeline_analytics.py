#!/usr/bin/env python3
"""Pipeline Meta-Intelligence — Cross-run analytics and intelligence generation.

Reads METRICS.json files across all projects to produce:
1. pipeline_intelligence.json — machine-readable adaptation data (consumed by EVALUATE)
2. Markdown health report (optional, for human consumption)

6 analysis dimensions:
- Profile accuracy (which profiles succeed for which requirement shapes?)
- Abandon patterns (what shapes/stages tend to get abandoned?)
- Stage efficiency (timing, tokens, retry rates)
- Adversarial value (what does adversarial catch that review misses?)
- Estimation accuracy (budget estimates vs actual)
- Goal performance (cycle velocity, stuck rate)

Usage:
    python pipeline_analytics.py --output <path>            # write intelligence JSON
    python pipeline_analytics.py --output <path> --report   # also write markdown report
    python pipeline_analytics.py --project SwarmAI          # single project only

Public symbols:
- ``main``  — CLI entry point
- ``analyze_all_runs`` — core analysis function (testable)
"""

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# RP30 scan-recency window: aggregate only runs touched within this many days.
# Wide (90d) so EVALUATE/BUILD's consumed intelligence stays populated; this is
# the SCAN bound, not the weekly-report window.
ANALYSIS_WINDOW_DAYS = 90

# Anti-C044 completeness gate: a dimension-cell computed from fewer than this many
# samples is rendered "insufficient data (n=X)" instead of a confident number.
# Absolute per-cell threshold (the eligible-runs denominator for a coverage RATIO
# is ambiguous per-dimension — profile_accuracy is per-profile, adversarial_value
# is per-telemetry-field, abandon_patterns already self-gates at n>=3). Matches
# the existing abandon_patterns idiom (analyze_abandon_patterns: count >= 3).
INSUFFICIENT_N = 3


def _get_workspace() -> Path:
    """Resolve workspace root."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import get_app_data_dir
    ws = os.environ.get("SWARM_WORKSPACE", str(get_app_data_dir() / "SwarmWS"))
    return Path(ws).expanduser().resolve()


def _load_all_metrics(
    workspace: Path,
    project_filter: str | None = None,
    max_age_days: float | None = ANALYSIS_WINDOW_DAYS,
) -> list[dict]:
    """Load METRICS.json files across projects, bounded to a recency window.

    RP30 (no-op-path scaling): the scan cost grows with ALL runs ever, but the
    aggregate only needs recent history — so skip run dirs whose mtime is older
    than ``max_age_days`` (default 90). The bound is on FILE MTIME, not a schema
    field: METRICS.json carry ``generated_at`` but NONE carry ``created_at`` (as
    of Run C), and mtime is universal (works for the run.json fallback path too). Pass
    ``max_age_days=None`` to disable the bound (full-history analysis).

    NOTE: this is the SCAN bound, deliberately kept wide (90d) so the JSON that
    EVALUATE/BUILD consume stays populated; it is NOT the weekly-report window.
    """
    projects_dir = workspace / "Projects"
    if not projects_dir.is_dir():
        return []

    cutoff = None if max_age_days is None else (time.time() - max_age_days * 86400)

    metrics = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        if project_filter and project_dir.name != project_filter:
            continue

        runs_dir = project_dir / ".artifacts" / "runs"
        if not runs_dir.is_dir():
            continue

        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            # RP30 recency bound — skip runs older than the window. Use the
            # METRICS.json mtime when present (the artifact we actually read),
            # else the run dir mtime. Fail-OPEN (include) if mtime is unreadable.
            if cutoff is not None:
                try:
                    _mf = run_dir / "METRICS.json"
                    _mtime = (_mf if _mf.exists() else run_dir).stat().st_mtime
                    if _mtime < cutoff:
                        continue
                except OSError:
                    pass  # unreadable mtime → include (fail-open, don't lose data)

            # Try METRICS.json first
            metrics_file = run_dir / "METRICS.json"
            if metrics_file.exists():
                try:
                    m = json.loads(metrics_file.read_text(encoding="utf-8"))
                    m.setdefault("project", project_dir.name)
                    m.setdefault("run_id", run_dir.name)
                    # LIFECYCLE-TRUTH OVERLAY (Gap-2, F1/F2): METRICS.json is
                    # written at completion and goes STALE if the run is later
                    # superseded (its status becomes 'abandoned' + gains an
                    # abandon_reason that METRICS.json never sees). We overlay
                    # ONLY the lifecycle fields the completion-rate calc needs —
                    # `lifecycle_status` + `abandon_reason` — from the sibling
                    # run.json, and DELIBERATELY do NOT touch `status`, because
                    # the four telemetry dimensions (stage_efficiency /
                    # adversarial / estimation / goal) filter on `status ==
                    # 'completed'` and a completed-then-superseded run's
                    # telemetry is still valid (F2: 12 real runs completed then
                    # got superseded — dropping their telemetry would corrupt
                    # those dimensions). The rate functions read
                    # `lifecycle_status`; every other consumer keeps reading the
                    # at-completion `status`.
                    _rf = run_dir / "run.json"
                    if _rf.exists():
                        try:
                            _r = json.loads(_rf.read_text(encoding="utf-8"))
                            m["lifecycle_status"] = _r.get("status")
                            m["abandon_reason"] = _r.get("abandon_reason")
                        except (json.JSONDecodeError, OSError):
                            pass
                    metrics.append(m)
                    continue
                except (json.JSONDecodeError, OSError):
                    pass

            # Fallback: extract minimal metrics from run.json
            run_file = run_dir / "run.json"
            if run_file.exists():
                try:
                    r = json.loads(run_file.read_text(encoding="utf-8"))
                    m = _extract_minimal_metrics(r, project_dir.name)
                    if m:
                        metrics.append(m)
                except (json.JSONDecodeError, OSError):
                    pass

    return metrics


def _extract_minimal_metrics(run_state: dict, project: str) -> dict | None:
    """Extract minimal metrics from run.json (for legacy runs without METRICS.json)."""
    status = run_state.get("status")
    if not status:
        return None

    created = run_state.get("created_at", "")
    completed = run_state.get("completed_at", "")
    duration = None
    if created and completed:
        try:
            t0 = datetime.fromisoformat(created)
            t1 = datetime.fromisoformat(completed)
            duration = round((t1 - t0).total_seconds() / 60, 1)
        except (ValueError, TypeError):
            pass

    stages = run_state.get("stages", [])
    total_tokens = sum(s.get("token_cost", 0) for s in stages)
    stage_tokens = {s.get("stage", "?"): s.get("token_cost", 0) for s in stages if s.get("token_cost", 0) > 0}

    return {
        "run_id": run_state.get("id", "unknown"),
        "project": project,
        "profile": run_state.get("profile"),
        "status": status,
        # lifecycle_status mirrors status here (run.json is the source of both),
        # but the rate functions read lifecycle_status uniformly so the METRICS
        # path and this fallback path expose the SAME field name (Gap-2).
        "lifecycle_status": status,
        "abandon_reason": run_state.get("abandon_reason"),
        "stages_completed": sum(1 for s in stages if s.get("status") in ("completed", "done")),
        "stages_total": len(stages),
        "duration_minutes": duration,
        "total_tokens": total_tokens,
        "stage_tokens": stage_tokens,
        "requirement": run_state.get("requirement", ""),
        "created_at": created,
        "telemetry": "legacy",
    }


def _safe_avg(lst: list) -> float:
    return round(sum(lst) / len(lst), 1) if lst else 0.0


def _safe_median(lst: list) -> float:
    if not lst:
        return 0.0
    s = sorted(lst)
    n = len(s)
    return round(s[n // 2], 1)


def _safe_pct(count: int, total: int) -> float:
    return round(count * 100 / total, 1) if total > 0 else 0.0


# ─── Replaced-duplicate exclusion (Gap-2) ──────────────────────────────
#
# A run whose work was re-done by a successor is marked abandoned with
# abandon_reason='superseded_by_<successor_id>' (or the no-id literal
# 'superseded_by_completed_run'). It is NOT a failure — it is a replaced
# duplicate of one unit of work. Counting it as abandoned depresses the
# completion rate and double-counts one work unit as (1 completed successor +
# 1 abandoned original), which systematically misleads any judge reading the
# rate. These helpers identify such runs so the rate functions can exclude
# them from BOTH numerator and denominator. Source of truth: run.json
# lifecycle_status + abandon_reason (NOT the stale METRICS.json status).

_SUPERSEDED_PREFIX = "superseded_by_"
# Explicit "a completed run finished this" signal that carries no successor id
# to verify (proactive_intelligence.py emits it on the completed-but-no-id
# branch). It IS a confirmed-completed supersede by construction.
_SUPERSEDED_NO_ID = "superseded_by_completed_run"
_CHAIN_HOP_CAP = 5


def _lifecycle_status(m: dict) -> str:
    """Authoritative current status for rate bucketing — prefers the run.json
    overlay (lifecycle_status), falls back to status for older dicts."""
    return m.get("lifecycle_status") or m.get("status") or ""


def _resolve_run(project: str, run_id: str, workspace: Path,
                 cache: dict) -> dict | None:
    """Resolve a run's {status, abandon_reason} — from cache, then disk run.json.

    Used to reach a successor that fell outside the recency window / metrics
    list. Same-project by construction (supersede only ever references a
    same-project successor). Returns None if unresolvable (fail-safe: caller
    must then NOT exclude)."""
    key = (project, run_id)
    if key in cache:
        return cache[key]
    result = None
    # Defence-in-depth: run_id is sliced from an abandon_reason string. Today it
    # is only ever a trusted internal run id, but reject any path-escaping value
    # (traversal / separators) before interpolating it into a filesystem path.
    if run_id != Path(run_id).name or run_id in ("", ".", ".."):
        cache[key] = None
        return None
    try:
        rf = workspace / "Projects" / project / ".artifacts" / "runs" / run_id / "run.json"
        if rf.is_file():
            r = json.loads(rf.read_text(encoding="utf-8"))
            result = {"status": r.get("status"),
                      "abandon_reason": r.get("abandon_reason")}
    except (json.JSONDecodeError, OSError):
        result = None
    cache[key] = result
    return result


def _is_replaced_duplicate(m: dict, in_list: dict, workspace: Path,
                           disk_cache: dict) -> bool:
    """True iff this run is an abandoned original whose work was re-done by a
    CONFIRMED-completed successor (directly or through a supersede chain).

    Fail-safe (F3/F4): the chain-walk stops at the FIRST non-superseded
    terminal and excludes ONLY when that terminal is 'completed'. A successor
    that is abandoned-crash / failed / running / unresolvable / missing →
    NOT a replaced duplicate (stays in the denominator). Cycle-guarded and
    hop-capped so a malformed chain can't loop or scan unboundedly.

    `in_list` maps (project, run_id) -> {status, abandon_reason} for the
    already-loaded metrics (cheap hit); disk is the fallback for out-of-window
    successors.
    """
    if _lifecycle_status(m) != "abandoned":
        return False
    reason = m.get("abandon_reason") or ""
    if not reason.startswith(_SUPERSEDED_PREFIX):
        return False
    # No-id explicit signal: a completed run demonstrably finished this. Exclude.
    if reason == _SUPERSEDED_NO_ID:
        return True

    project = m.get("project", "")
    visited: set = set()
    cur_reason = reason
    for _ in range(_CHAIN_HOP_CAP):
        succ_id = cur_reason[len(_SUPERSEDED_PREFIX):].strip()
        if not succ_id or succ_id in visited:
            return False  # malformed / cycle → fail-safe keep
        visited.add(succ_id)
        rec = in_list.get((project, succ_id)) \
            or _resolve_run(project, succ_id, workspace, disk_cache)
        if not rec:
            return False  # unresolvable successor → fail-safe keep
        s = rec.get("status")
        if s == "completed":
            return True  # confirmed completed terminal → replaced duplicate
        # Continue the walk ONLY if the successor was itself superseded;
        # any other terminal (abandoned-crash, failed, running, paused) → keep.
        r2 = rec.get("abandon_reason") or ""
        if s == "abandoned" and r2.startswith(_SUPERSEDED_PREFIX):
            if r2 == _SUPERSEDED_NO_ID:
                return True
            cur_reason = r2
            continue
        return False
    return False  # hop cap exhausted → fail-safe keep


def _replaced_duplicate_ids(metrics: list[dict], workspace: Path) -> set:
    """Set of (project, run_id) for every replaced-duplicate in the corpus.
    Computed once, reused by all rate functions (single source of truth — no
    per-function drift)."""
    in_list = {
        (m.get("project", ""), m.get("run_id", "")): {
            "status": _lifecycle_status(m),
            "abandon_reason": m.get("abandon_reason"),
        }
        for m in metrics
    }
    disk_cache: dict = {}
    return {
        (m.get("project", ""), m.get("run_id", ""))
        for m in metrics
        if _is_replaced_duplicate(m, in_list, workspace, disk_cache)
    }


# ─── Analysis Dimensions ───────────────────────────────────────────────


def analyze_profile_accuracy(metrics: list[dict], replaced: set | None = None) -> dict:
    """Dimension 1: Which profiles succeed for which requirement shapes?

    `replaced` = (project, run_id) of replaced-duplicate runs (superseded by a
    completed successor) — excluded from ALL buckets so the rate reflects real
    work units, not reruns (Gap-2). Uses lifecycle_status (run.json truth), not
    the stale METRICS.json status.
    """
    replaced = replaced or set()
    profile_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "completed": 0, "abandoned": 0, "failed": 0})

    for m in metrics:
        if (m.get("project", ""), m.get("run_id", "")) in replaced:
            continue  # replaced duplicate — neither success nor failure
        profile = str(m.get("profile", "None"))
        profile_stats[profile]["total"] += 1
        status = _lifecycle_status(m)
        if status == "completed":
            profile_stats[profile]["completed"] += 1
        elif status == "abandoned":
            profile_stats[profile]["abandoned"] += 1
        elif status == "failed":
            profile_stats[profile]["failed"] += 1

    # Calculate success rates
    profile_success: dict[str, dict] = {}
    for profile, stats in profile_stats.items():
        total = stats["total"]
        profile_success[profile] = {
            "total_runs": total,
            "completion_rate": _safe_pct(stats["completed"], total),
            "abandon_rate": _safe_pct(stats["abandoned"], total),
            "failure_rate": _safe_pct(stats["failed"], total),
        }

    # Identify multi-file requirements that used full but might benefit from goal
    full_runs = [m for m in metrics
                 if m.get("profile") == "full"
                 and (m.get("project", ""), m.get("run_id", "")) not in replaced]
    multi_file_full = [m for m in full_runs if m.get("build", {}).get("files_changed", 0) > 3]
    multi_file_success = sum(1 for m in multi_file_full if _lifecycle_status(m) == "completed")

    return {
        "profile_success_rates": profile_success,
        "recommendation": {
            "multi_file_full_runs": len(multi_file_full),
            "multi_file_full_success_rate": _safe_pct(multi_file_success, len(multi_file_full)),
            "suggest_goal_for_multi_file": len(multi_file_full) > 5 and _safe_pct(multi_file_success, len(multi_file_full)) < 85,
        },
    }


def analyze_abandon_patterns(metrics: list[dict], replaced: set | None = None) -> dict:
    """Dimension 2: What shapes/stages tend to get abandoned?

    Excludes replaced-duplicates (superseded-by-completed) so abandon_rate
    counts only GENUINE failures (crash / OOM / orphan), not reruns (Gap-2).
    """
    replaced = replaced or set()
    live = [m for m in metrics
            if (m.get("project", ""), m.get("run_id", "")) not in replaced]
    abandoned = [m for m in live if _lifecycle_status(m) == "abandoned"]
    total = len(live)

    if not abandoned:
        return {"total_abandoned": 0, "abandon_rate": 0, "hotspots": [],
                "high_risk_shapes": [], "replaced_duplicates": len(replaced)}

    # Last stage distribution
    last_stage_counts: Counter = Counter()
    for m in abandoned:
        # Determine last completed stage
        stages_completed = m.get("stages_completed", 0)
        stage_tokens = m.get("stage_tokens", {})
        last_stage = "init"
        if stage_tokens:
            # The last stage with tokens is approximately where it stopped
            for stage in ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"]:
                if stage in stage_tokens and stage_tokens[stage] > 0:
                    last_stage = stage
        elif isinstance(m.get("abandon_context"), dict):
            last_stage = m["abandon_context"].get("last_stage", "unknown")
        last_stage_counts[last_stage] += 1

    # Profile abandon distribution
    profile_abandon: Counter = Counter(str(m.get("profile", "None")) for m in abandoned)

    # Identify high-risk shapes (patterns that frequently abandon)
    high_risk_shapes = []
    for profile, count in profile_abandon.most_common():
        total_profile = sum(1 for m in live if str(m.get("profile", "None")) == profile)
        rate = _safe_pct(count, total_profile)
        if rate > 20 and count >= 3:
            high_risk_shapes.append({
                "profile": profile,
                "abandon_count": count,
                "total_runs": total_profile,
                "abandon_rate": rate,
            })

    return {
        "total_abandoned": len(abandoned),
        "abandon_rate": _safe_pct(len(abandoned), total),
        "hotspots": [{"stage": s, "count": c} for s, c in last_stage_counts.most_common(5)],
        "profile_abandon": dict(profile_abandon),
        "high_risk_shapes": high_risk_shapes,
        "replaced_duplicates": len(replaced),
    }


def analyze_stage_efficiency(metrics: list[dict]) -> dict:
    """Dimension 3: Stage timing, tokens, retry rates."""
    completed = [m for m in metrics if m.get("status") == "completed"]
    if not completed:
        return {"stages": {}}

    stage_data: dict[str, dict[str, list]] = defaultdict(lambda: {"tokens": [], "durations": []})

    for m in completed:
        for stage, tokens in m.get("stage_tokens", {}).items():
            if isinstance(tokens, (int, float)) and tokens > 0:
                stage_data[stage]["tokens"].append(tokens)

        # Stage timing (from extended telemetry)
        for stage, timing in m.get("stage_timing", {}).items():
            if isinstance(timing, dict) and timing.get("wall_minutes"):
                stage_data[stage]["durations"].append(timing["wall_minutes"])

    result = {}
    for stage in ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect", "goal_cycle"]:
        if stage in stage_data:
            data = stage_data[stage]
            result[stage] = {
                "avg_tokens": _safe_avg(data["tokens"]),
                "median_tokens": _safe_median(data["tokens"]),
                "sample_count": len(data["tokens"]),
            }
            if data["durations"]:
                result[stage]["avg_minutes"] = _safe_avg(data["durations"])
                result[stage]["median_minutes"] = _safe_median(data["durations"])

    return {"stages": result}


def analyze_adversarial_value(metrics: list[dict]) -> dict:
    """Dimension 4: What does adversarial catch that review misses?"""
    completed = [m for m in metrics if m.get("status") == "completed"]
    if not completed:
        return {"runs_analyzed": 0}

    total_review = 0
    total_adversarial = 0
    total_adversarial_high = 0
    runs_with_adversarial = 0
    rp_violations: Counter = Counter()

    for m in completed:
        catches = m.get("catches", {})
        rf = catches.get("review_findings", 0)
        af = catches.get("adversarial_findings", 0)
        ah = catches.get("adversarial_high", 0)

        if isinstance(rf, (int, float)):
            total_review += rf
        if isinstance(af, (int, float)):
            total_adversarial += af
            if af > 0:
                runs_with_adversarial += 1
        if isinstance(ah, (int, float)):
            total_adversarial_high += ah

        # Track RP violations from extended telemetry
        for rp in m.get("adversarial_patterns", {}).get("rp_violations", []):
            rp_violations[rp] += 1

    n = len(completed)
    gap_ratio = 0.0
    if total_adversarial > 0:
        overlap = min(total_review, total_adversarial) * 0.2  # rough estimate
        unique_to_adversarial = total_adversarial - overlap
        gap_ratio = round(unique_to_adversarial / max(total_adversarial, 1), 2)

    # Chronic violations (top patterns that keep appearing)
    chronic = [{"pattern": rp, "count": c} for rp, c in rp_violations.most_common(10) if c >= 2]

    return {
        "runs_analyzed": n,
        "review_findings_total": total_review,
        "adversarial_findings_total": total_adversarial,
        "adversarial_high_total": total_adversarial_high,
        "adversarial_hit_rate": _safe_pct(runs_with_adversarial, n),
        "review_adversarial_gap_ratio": gap_ratio,
        "chronic_violations": chronic,
        "build_injection_recommendations": [v["pattern"] for v in chronic[:5]],
    }


def analyze_estimation_accuracy(metrics: list[dict]) -> dict:
    """Dimension 5: Budget estimates vs actual consumption."""
    completed = [m for m in metrics if m.get("status") == "completed" and m.get("total_tokens", 0) > 0]
    if not completed:
        return {"sample_count": 0}

    # Token estimation accuracy by profile
    profile_tokens: dict[str, list[int]] = defaultdict(list)
    for m in completed:
        profile = str(m.get("profile", "full"))
        profile_tokens[profile].append(m["total_tokens"])

    calibration = {}
    for profile, tokens_list in profile_tokens.items():
        calibration[profile] = {
            "avg_tokens": int(_safe_avg(tokens_list)),
            "median_tokens": int(_safe_median(tokens_list)),
            "p90_tokens": int(sorted(tokens_list)[int(len(tokens_list) * 0.9)]) if len(tokens_list) >= 5 else int(_safe_avg(tokens_list)),
            "sample_count": len(tokens_list),
        }

    # Duration calibration
    profile_durations: dict[str, list[float]] = defaultdict(list)
    for m in completed:
        d = m.get("duration_minutes")
        if d and isinstance(d, (int, float)) and d < 120:  # Filter outliers
            profile_durations[str(m.get("profile", "full"))].append(d)

    duration_calibration = {}
    for profile, durations in profile_durations.items():
        duration_calibration[profile] = {
            "avg_minutes": _safe_avg(durations),
            "median_minutes": _safe_median(durations),
            "sample_count": len(durations),
        }

    # Per-stage calibration (for budget.stage_estimates)
    stage_calibration: dict[str, list[int]] = defaultdict(list)
    for m in completed:
        for stage, tokens in m.get("stage_tokens", {}).items():
            if isinstance(tokens, (int, float)) and tokens > 0:
                stage_calibration[stage].append(int(tokens))

    stage_estimates = {}
    for stage, tokens_list in stage_calibration.items():
        stage_estimates[stage] = int(_safe_avg(tokens_list))

    return {
        "sample_count": len(completed),
        "calibration_by_profile": calibration,
        "duration_by_profile": duration_calibration,
        "stage_estimates": stage_estimates,
    }


def analyze_goal_performance(metrics: list[dict], replaced: set | None = None) -> dict:
    """Dimension 6: Goal profile specific performance metrics.

    Excludes replaced-duplicates and uses lifecycle_status for the completion
    rate — same Gap-2 correction as profile_accuracy/abandon_patterns, since
    this dimension also renders a completion_rate in the report.
    """
    replaced = replaced or set()
    goal_runs = [m for m in metrics
                 if m.get("profile") == "goal"
                 and (m.get("project", ""), m.get("run_id", "")) not in replaced]
    if not goal_runs:
        return {"total_goal_runs": 0, "message": "No goal runs yet"}

    completed = [m for m in goal_runs if _lifecycle_status(m) == "completed"]
    return {
        "total_goal_runs": len(goal_runs),
        "completed": len(completed),
        "completion_rate": _safe_pct(len(completed), len(goal_runs)),
        "avg_tokens": _safe_avg([m.get("total_tokens", 0) for m in completed if m.get("total_tokens")]),
        "avg_duration": _safe_avg([m.get("duration_minutes", 0) for m in completed if m.get("duration_minutes")]),
    }


# ─── Main Analysis ─────────────────────────────────────────────────────


def analyze_all_runs(workspace: Path, project_filter: str | None = None) -> dict:
    """Run all 6 analysis dimensions and produce pipeline intelligence."""
    metrics = _load_all_metrics(workspace, project_filter)
    if not metrics:
        return {"error": "No metrics data found", "runs_analyzed": 0}

    # Gap-2: identify replaced-duplicate runs ONCE (single source of truth) so
    # every rate function excludes the SAME set — no per-function drift.
    replaced = _replaced_duplicate_ids(metrics, workspace)

    intelligence = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs_analyzed": len(metrics),
        # Bridge the two numbers a reader would otherwise be unable to reconcile
        # (F5): runs_analyzed counts every run on disk; the rate denominators
        # below drop these replaced duplicates (reruns, neither success nor
        # failure). rate_denominator = runs_analyzed - replaced_duplicates.
        "replaced_duplicates": len(replaced),
        "rate_denominator": len(metrics) - len(replaced),
        "projects": list(set(m.get("project", "?") for m in metrics)),
        "telemetry_coverage": {
            "full": sum(1 for m in metrics if m.get("telemetry") != "legacy"),
            "legacy": sum(1 for m in metrics if m.get("telemetry") == "legacy"),
        },
        "dimensions": {
            "profile_accuracy": analyze_profile_accuracy(metrics, replaced),
            "abandon_patterns": analyze_abandon_patterns(metrics, replaced),
            "stage_efficiency": analyze_stage_efficiency(metrics),
            "adversarial_value": analyze_adversarial_value(metrics),
            "estimation_accuracy": analyze_estimation_accuracy(metrics),
            "goal_performance": analyze_goal_performance(metrics, replaced),
        },
    }

    return intelligence


def _insufficient(n: int) -> bool:
    """Anti-C044: True if a dimension-cell has too few samples to report a
    confident number (absolute per-cell threshold — see INSUFFICIENT_N)."""
    return (n or 0) < INSUFFICIENT_N


def generate_report(intelligence: dict) -> str:
    """Generate markdown health report from intelligence data.

    Anti-C044 honesty: any dimension-cell whose sample count n<INSUFFICIENT_N is
    rendered "insufficient data (n=X)" rather than a confident number — a green
    metric over thin data misleads (the exact failure the completeness gate exists
    to prevent). The gate is per-cell absolute-n, not a coverage ratio.
    """
    lines = [
        "# Pipeline Health Report",
        f"",
        f"Generated: {intelligence.get('generated_at', 'unknown')[:16]}",
        f"Runs analyzed: {intelligence.get('runs_analyzed', 0)}"
        + (f" ({intelligence['replaced_duplicates']} replaced duplicates excluded from rates; "
           f"rate denominator {intelligence.get('rate_denominator', 0)})"
           if intelligence.get('replaced_duplicates') else ""),
        f"Projects: {', '.join(intelligence.get('projects', []))}",
        "",
        "---",
        "",
    ]

    dims = intelligence.get("dimensions", {})

    # Profile Accuracy
    pa = dims.get("profile_accuracy", {})
    lines.append("## Profile Success Rates\n")
    lines.append("| Profile | Runs | Completion | Abandon | Fail |")
    lines.append("|---------|------|-----------|---------|------|")
    for profile, stats in pa.get("profile_success_rates", {}).items():
        _n = stats.get("total_runs", 0)
        if _insufficient(_n):
            lines.append(f"| {profile} | {_n} | insufficient data (n={_n}) | — | — |")
        else:
            lines.append(f"| {profile} | {_n} | {stats['completion_rate']}% | {stats['abandon_rate']}% | {stats['failure_rate']}% |")
    lines.append("")

    # Abandon Patterns
    ap = dims.get("abandon_patterns", {})
    lines.append(f"## Abandon Patterns (total: {ap.get('total_abandoned', 0)}, rate: {ap.get('abandon_rate', 0)}%)\n")
    if ap.get("hotspots"):
        lines.append("**Abandon hotspot stages:**")
        for h in ap["hotspots"]:
            lines.append(f"- {h['stage']}: {h['count']} times")
    if ap.get("high_risk_shapes"):
        lines.append("\n**High-risk shapes (>20% abandon rate):**")
        for s in ap["high_risk_shapes"]:
            lines.append(f"- profile={s['profile']}: {s['abandon_count']}/{s['total_runs']} abandoned ({s['abandon_rate']}%)")
    lines.append("")

    # Stage Efficiency
    se = dims.get("stage_efficiency", {})
    lines.append("## Stage Efficiency\n")
    lines.append("| Stage | Avg Tokens | Median | Samples |")
    lines.append("|-------|-----------|--------|---------|")
    for stage, data in se.get("stages", {}).items():
        _n = data.get("sample_count", 0)
        if _insufficient(_n):
            lines.append(f"| {stage} | insufficient data (n={_n}) | — | {_n} |")
        else:
            lines.append(f"| {stage} | {data['avg_tokens']:.0f} | {data['median_tokens']:.0f} | {_n} |")
    lines.append("")

    # Adversarial Value
    av = dims.get("adversarial_value", {})
    lines.append("## Adversarial Review Value\n")
    lines.append(f"- Review findings: {av.get('review_findings_total', 0)}")
    lines.append(f"- Adversarial findings: {av.get('adversarial_findings_total', 0)} ({av.get('adversarial_high_total', 0)} HIGH)")
    lines.append(f"- Adversarial hit rate: {av.get('adversarial_hit_rate', 0)}%")
    lines.append(f"- Review→Adversarial gap: {av.get('review_adversarial_gap_ratio', 0)}")
    if av.get("chronic_violations"):
        lines.append("\n**Chronic RP violations (inject in BUILD):**")
        for v in av["chronic_violations"]:
            lines.append(f"- {v['pattern']}: {v['count']} occurrences")
    lines.append("")

    # Estimation
    ea = dims.get("estimation_accuracy", {})
    lines.append("## Budget Calibration\n")
    lines.append("| Profile | Avg Tokens | Median | P90 | Samples |")
    lines.append("|---------|-----------|--------|-----|---------|")
    for profile, cal in ea.get("calibration_by_profile", {}).items():
        _n = cal.get("sample_count", 0)
        if _insufficient(_n):
            lines.append(f"| {profile} | insufficient data (n={_n}) | — | — | {_n} |")
        else:
            lines.append(f"| {profile} | {cal['avg_tokens']:,} | {cal['median_tokens']:,} | {cal['p90_tokens']:,} | {_n} |")
    lines.append("")

    if ea.get("stage_estimates"):
        lines.append("**Stage estimates (for budget allocation):**")
        for stage, tokens in ea.get("stage_estimates", {}).items():
            lines.append(f"- {stage}: {tokens:,} tokens")
    lines.append("")

    # Goal Performance
    gp = dims.get("goal_performance", {})
    lines.append(f"## Goal Profile ({gp.get('total_goal_runs', 0)} runs)\n")
    if gp.get("total_goal_runs", 0) > 0:
        lines.append(f"- Completion rate: {gp.get('completion_rate', 0)}%")
        lines.append(f"- Avg tokens: {gp.get('avg_tokens', 0):,.0f}")
        lines.append(f"- Avg duration: {gp.get('avg_duration', 0):.1f} min")
    else:
        lines.append("_No goal runs yet — monitoring._")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Pipeline Meta-Intelligence — cross-run analytics")
    parser.add_argument("--output", default=None, help="Path to write pipeline_intelligence.json")
    parser.add_argument("--report", action="store_true", help="Also generate markdown report (next to --output)")
    parser.add_argument("--report-path", default=None, help="Write the markdown report DIRECTLY to this path (implies --report; no cp needed)")
    parser.add_argument("--project", default=None, help="Filter to single project")
    parser.add_argument("--workspace", default=None, help="Override workspace path")

    args = parser.parse_args()

    if args.workspace:
        workspace = Path(args.workspace).expanduser().resolve()
    else:
        workspace = _get_workspace()

    intelligence = analyze_all_runs(workspace, args.project)

    # Default output location
    output_path = args.output or str(workspace / "pipeline_intelligence.json")
    Path(output_path).write_text(json.dumps(intelligence, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "output": output_path, "runs_analyzed": intelligence.get("runs_analyzed", 0)}))

    if args.report or args.report_path:
        report = generate_report(intelligence)
        # --report-path lets a caller (the weekly job) write the markdown DIRECTLY
        # to its final home (Knowledge/Reports/pipeline-weekly.md) — no fragile cp
        # + filename-guess (RP34 shell-var / RP39 detached-reexec class avoided).
        report_path = args.report_path or str(Path(output_path).parent / "pipeline-health-report.md")
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(report, encoding="utf-8")
        print(json.dumps({"report": report_path}))


if __name__ == "__main__":
    main()
