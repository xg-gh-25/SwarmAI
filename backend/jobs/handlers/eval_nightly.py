"""eval_nightly — nightly full eval run + drift-vs-baseline alert (run_5edf2cc0 C6, gap G7).

Runs the FULL golden set (programmatic + LLM judge — Bedrock cost is fine
nightly, never gates) and compares the overall score against the previous run.
Alerts Slack on:
  - BVT RED (a gate-eligible regression — the spine broke), OR
  - capability DRIFT below baseline beyond a tolerance band.

Mirrors session_health_probe's notify discipline: dedup via an alert-state file
(no nightly alarm storm), test seams for the runner + notifier. The gate
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

_ALERT_STATE = Path.home() / ".swarm-ai" / "SwarmWS" / ".context" / ".eval-nightly-alert.json"


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
        logger.warning("eval-nightly alert-state write failed: %s", e)


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
    result = run_eval(gs, "nightly", None, root, verify_teeth=True)  # full run incl LLM + canary teeth
    write_run(result, root)
    return {"this": result, "baseline": baseline}


def run_eval_nightly(
    dry_run: bool = False,
    *,
    notifier: Optional[Callable[..., dict]] = None,
    runner: Optional[Callable[[Path], dict]] = None,
    root: Optional[Path] = None,
) -> dict:
    """Run nightly eval; alert Slack on BVT-red or score-drift. Returns a result dict.

    dry_run: never send a notification (still returns the verdict).
    notifier / runner / root: injected test seams.
    """
    root = root or (Path.home() / ".swarm-ai" / "SwarmWS")
    run = runner or _default_runner
    try:
        out = run(root)
    except Exception as e:
        logger.error("eval-nightly run crashed: %s", e)
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

    fingerprint = "|".join(reasons)  # "" when clean
    notified = False
    if not dry_run:
        prev = _read_alert_state()
        if reasons and fingerprint != prev:
            send = notifier or _default_notifier
            try:
                send(message="\n".join(f"- {r}" for r in reasons),
                     title="🔴 SwarmAI nightly eval — regression/drift",
                     channels=["slack"])
                notified = True
            except Exception as e:
                logger.error("eval-nightly notify failed: %s", e)
        elif not reasons and prev:
            send = notifier or _default_notifier
            try:
                send(message=f"✅ nightly eval recovered (score {score}%)",
                     title="SwarmAI nightly eval", channels=["slack"])
                notified = True
            except Exception as e:
                logger.error("eval-nightly recovery notify failed: %s", e)
        _write_alert_state(fingerprint)

    return {
        "status": "regression" if reasons else "healthy",
        "overall_score": score,
        "baseline_score": base_score,
        "bvt_green": bvt.get("green"),
        "reasons": reasons,
        "notified": notified,
    }


def handle_eval_nightly(job, state) -> dict:
    """Job-system entry point. `job`/`state` accepted for the executor contract."""
    dry = bool(getattr(job, "config", {}).get("dry_run", False)) if job else False
    return run_eval_nightly(dry_run=dry)
