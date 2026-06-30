"""eval_scheduled — scheduled full eval run + drift-vs-baseline alert (run_5edf2cc0 C6, gap G7; renamed run_95d9acbc).

Runs the FULL golden set (programmatic + LLM judge — Bedrock cost is fine on
this cadence, never gates) and compares the overall score against the previous run.
Runs 12:30 ICT Mondays (lunch — weekly), NOT nightly — the name was fixed to reflect reality.
(Weekly cadence: behavior-tier cases now spawn real headless agents, slow/costly.)
Alerts Slack on:
  - BVT RED (a gate-eligible regression — the spine broke), OR
  - capability DRIFT below baseline beyond a tolerance band.

Mirrors session_health_probe's notify discipline: dedup via an alert-state file
(no alarm storm), test seams for the runner + notifier. The gate
(ci_eval_gate, local prod.sh release) is the HARD stop; this job is the
continuous-monitoring eye that catches model/dependency drift (AWS Eval-First:
"baseline is a drifting quantity, retest continuously").
"""
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Score may dip this much run-to-run without alerting (LLM-judge noise band).
_DRIFT_TOLERANCE = 5.0

# Coverage-collapse alarm: fraction of intended-scorable cases that errored.
# When error/(scored+error) >= this, the judge infra (Bedrock/creds) is degraded
# — distinct from a quality regression (low pass_rate) or a spine break (BVT).
# 2026-06-28: 90/146 errored (0.62) was SILENT because the 56 deterministic
# survivors scored 100 → no drift, no bvt_red. 0.20 ignores 偶发 single-case
# Bedrock blips (1/146 = 0.7%) while catching a real cohort collapse.
_COVERAGE_ALERT_THRESHOLD = 0.20

_ALERT_STATE = Path.home() / ".swarm-ai" / "SwarmWS" / ".context" / ".eval-scheduled-alert.json"


def _read_alert_state() -> str:
    try:
        import json
        return json.loads(_ALERT_STATE.read_text()).get("fingerprint", "")
    except Exception:
        return ""


def _write_alert_state(fingerprint: str) -> None:
    try:
        import json
        _ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
        _ALERT_STATE.write_text(json.dumps({"fingerprint": fingerprint}))
    except Exception as e:
        logger.warning("eval-scheduled alert-state write failed: %s", e)


def _default_notifier(**kwargs) -> dict:
    from skills.s_notify.notify import send_notification
    return send_notification(**kwargs)


def _default_runner(root: Path) -> dict:
    """Run the full eval and return (this_report, baseline_report)."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.eval_runner import (
        load_golden_set, _golden_set_path, run_eval, write_run,
        _load_history as load_history,
    )
    gs = load_golden_set(_golden_set_path(root))
    history = load_history(root)  # prior runs, newest last
    baseline = history[-1] if history else None
    result = run_eval(gs, "scheduled", None, root, verify_teeth=True)  # full run incl LLM + canary teeth
    write_run(result, root)
    return {"this": result, "baseline": baseline}


def run_eval_scheduled(
    dry_run: bool = False,
    *,
    notifier: Optional[Callable[..., dict]] = None,
    runner: Optional[Callable[[Path], dict]] = None,
    root: Optional[Path] = None,
) -> dict:
    """Run scheduled eval; alert Slack on BVT-red or score-drift. Returns a result dict.

    dry_run: never send a notification (still returns the verdict).
    notifier / runner / root: injected test seams.
    """
    root = root or (Path.home() / ".swarm-ai" / "SwarmWS")
    run = runner or _default_runner
    try:
        out = run(root)
    except Exception as e:
        logger.error("eval-scheduled run crashed: %s", e)
        return {"status": "error", "reason": f"{type(e).__name__}: {e}"}

    this = out.get("this") or {}
    baseline = out.get("baseline") or {}
    bvt = this.get("bvt") or {}
    score = this.get("overall_score")
    base_score = baseline.get("overall_score")

    bvt_red = bool(bvt) and not bvt.get("green", False)
    drift = (base_score is not None and score is not None
             and (base_score - score) > _DRIFT_TOLERANCE)

    reasons = []
    if bvt_red:
        reasons.append(f"BVT RED: {bvt.get('passed')}/{bvt.get('total')} "
                       f"(failed={bvt.get('failed')}, error={bvt.get('error')})")
    if drift:
        reasons.append(f"score drift: {base_score}% → {score}% (>{_DRIFT_TOLERANCE} drop)")

    # Coverage collapse — judge infra (Bedrock/creds) degraded. SEPARATE axis from
    # drift/bvt (P6: infra-failure ≠ agent-quality-regression). Formula mirrors
    # eval_service.py:583-587 verbatim (scored-None fallback + intended>0 guard) so
    # a fully-skipped run can't ZeroDivisionError. (run_f7a3acd7, gap from 2026-06-28)
    scored = this.get("scored_count")
    n_error = this.get("cases_error", 0) or 0
    if scored is None:  # legacy run without scored_count — derive from pass+fail
        scored = (this.get("cases_passed", 0) or 0) + (this.get("cases_failed", 0) or 0)
    intended = scored + n_error
    if intended > 0 and (n_error / intended) >= _COVERAGE_ALERT_THRESHOLD:
        coverage = scored / intended
        reasons.append(f"🔴 judge infra degraded: {n_error}/{intended} cases errored "
                       f"(coverage {coverage:.0%})")

    fingerprint = "|".join(reasons)  # "" when clean
    notified = False
    if not dry_run:
        prev = _read_alert_state()
        if reasons and fingerprint != prev:
            send = notifier or _default_notifier
            try:
                send(message="\n".join(f"- {r}" for r in reasons),
                     title="🔴 SwarmAI scheduled eval — regression/drift",
                     channels=["slack"])
                notified = True
            except Exception as e:
                logger.error("eval-scheduled notify failed: %s", e)
        elif not reasons and prev:
            send = notifier or _default_notifier
            try:
                send(message=f"✅ scheduled eval recovered (score {score}%)",
                     title="SwarmAI scheduled eval", channels=["slack"])
                notified = True
            except Exception as e:
                logger.error("eval-scheduled recovery notify failed: %s", e)
        _write_alert_state(fingerprint)

    return {
        "status": "regression" if reasons else "healthy",
        "overall_score": score,
        "baseline_score": base_score,
        "bvt_green": bvt.get("green"),
        "reasons": reasons,
        "notified": notified,
    }


def handle_eval_scheduled(job, state) -> dict:
    """Job-system entry point. `job`/`state` accepted for the executor contract."""
    dry = bool(getattr(job, "config", {}).get("dry_run", False)) if job else False
    return run_eval_scheduled(dry_run=dry)
