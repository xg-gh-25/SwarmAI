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
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _get_workspace() -> Path:
    """Resolve workspace root."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import get_app_data_dir
    ws = os.environ.get("SWARM_WORKSPACE", str(get_app_data_dir() / "SwarmWS"))
    return Path(ws).expanduser().resolve()


def _load_all_metrics(workspace: Path, project_filter: str | None = None) -> list[dict]:
    """Load all METRICS.json files across projects."""
    projects_dir = workspace / "Projects"
    if not projects_dir.is_dir():
        return []

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

            # Try METRICS.json first
            metrics_file = run_dir / "METRICS.json"
            if metrics_file.exists():
                try:
                    m = json.loads(metrics_file.read_text(encoding="utf-8"))
                    m.setdefault("project", project_dir.name)
                    m.setdefault("run_id", run_dir.name)
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


# ─── Analysis Dimensions ───────────────────────────────────────────────


def analyze_profile_accuracy(metrics: list[dict]) -> dict:
    """Dimension 1: Which profiles succeed for which requirement shapes?"""
    profile_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "completed": 0, "abandoned": 0, "failed": 0})

    for m in metrics:
        profile = str(m.get("profile", "None"))
        profile_stats[profile]["total"] += 1
        status = m.get("status", "")
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
    full_runs = [m for m in metrics if m.get("profile") == "full"]
    multi_file_full = [m for m in full_runs if m.get("build", {}).get("files_changed", 0) > 3]
    multi_file_success = sum(1 for m in multi_file_full if m.get("status") == "completed")

    return {
        "profile_success_rates": profile_success,
        "recommendation": {
            "multi_file_full_runs": len(multi_file_full),
            "multi_file_full_success_rate": _safe_pct(multi_file_success, len(multi_file_full)),
            "suggest_goal_for_multi_file": len(multi_file_full) > 5 and _safe_pct(multi_file_success, len(multi_file_full)) < 85,
        },
    }


def analyze_abandon_patterns(metrics: list[dict]) -> dict:
    """Dimension 2: What shapes/stages tend to get abandoned?"""
    abandoned = [m for m in metrics if m.get("status") == "abandoned"]
    total = len(metrics)

    if not abandoned:
        return {"total_abandoned": 0, "abandon_rate": 0, "hotspots": [], "high_risk_shapes": []}

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
        total_profile = sum(1 for m in metrics if str(m.get("profile", "None")) == profile)
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


def analyze_goal_performance(metrics: list[dict]) -> dict:
    """Dimension 6: Goal profile specific performance metrics."""
    goal_runs = [m for m in metrics if m.get("profile") == "goal"]
    if not goal_runs:
        return {"total_goal_runs": 0, "message": "No goal runs yet"}

    completed = [m for m in goal_runs if m.get("status") == "completed"]
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

    intelligence = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs_analyzed": len(metrics),
        "projects": list(set(m.get("project", "?") for m in metrics)),
        "telemetry_coverage": {
            "full": sum(1 for m in metrics if m.get("telemetry") != "legacy"),
            "legacy": sum(1 for m in metrics if m.get("telemetry") == "legacy"),
        },
        "dimensions": {
            "profile_accuracy": analyze_profile_accuracy(metrics),
            "abandon_patterns": analyze_abandon_patterns(metrics),
            "stage_efficiency": analyze_stage_efficiency(metrics),
            "adversarial_value": analyze_adversarial_value(metrics),
            "estimation_accuracy": analyze_estimation_accuracy(metrics),
            "goal_performance": analyze_goal_performance(metrics),
        },
    }

    return intelligence


def generate_report(intelligence: dict) -> str:
    """Generate markdown health report from intelligence data."""
    lines = [
        "# Pipeline Health Report",
        f"",
        f"Generated: {intelligence.get('generated_at', 'unknown')[:16]}",
        f"Runs analyzed: {intelligence.get('runs_analyzed', 0)}",
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
        lines.append(f"| {profile} | {stats['total_runs']} | {stats['completion_rate']}% | {stats['abandon_rate']}% | {stats['failure_rate']}% |")
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
        lines.append(f"| {stage} | {data['avg_tokens']:.0f} | {data['median_tokens']:.0f} | {data['sample_count']} |")
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
        lines.append(f"| {profile} | {cal['avg_tokens']:,} | {cal['median_tokens']:,} | {cal['p90_tokens']:,} | {cal['sample_count']} |")
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
    parser.add_argument("--report", action="store_true", help="Also generate markdown report")
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

    if args.report:
        report = generate_report(intelligence)
        report_path = str(Path(output_path).parent / "pipeline-health-report.md")
        Path(report_path).write_text(report, encoding="utf-8")
        print(json.dumps({"report": report_path}))


if __name__ == "__main__":
    main()
