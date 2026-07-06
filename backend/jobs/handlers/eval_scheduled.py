"""eval_scheduled — scheduled full eval run + drift-vs-baseline alert (run_5edf2cc0 C6, gap G7; renamed run_95d9acbc).

Runs the FULL golden set (programmatic + LLM judge + behavior-tier via
include_behavior=True — Bedrock cost is fine on this cadence, never gates) and
compares the overall score against the previous run.
Cron fires every Monday 12:30 ICT, but a 14-day gate (_should_run_biweekly, own
timestamp file — NOT JobState.last_run, which a skip would reset → deadlock) makes
a REAL run happen only once per 2 WEEKS (run_6980cb35). Behavior-tier cases spawn
real headless agents (slow/costly); their failures are segregated from the
deterministic hard alert (non-deterministic — verify before alarm).
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

# Every-two-weeks gate (run_6980cb35). The cron fires EVERY Monday but this job
# should only actually run once per 14 days. We use our OWN timestamp file —
# NOT JobState.last_run — because the executor rewrites last_run on EVERY result
# incl. a skip (executor.py:2111), so gating on last_run would let each weekly
# skip reset the clock → permanent deadlock (Gate-1 F). This file is written
# ONLY when a REAL run happens, so a skip never advances the clock.
_BIWEEKLY_STATE = Path.home() / ".swarm-ai" / "SwarmWS" / ".context" / ".eval-biweekly-last-run.json"
_BIWEEKLY_INTERVAL_DAYS = 14


def _should_run_biweekly(last_run, now, interval_days: int = _BIWEEKLY_INTERVAL_DAYS) -> bool:
    """True if a real biweekly run is due. FAIL-OPEN: unknown/unparseable → run
    (never silently never-run). last_run may be a datetime, an ISO string, or None."""
    from datetime import datetime, timezone
    if last_run is None:
        return True
    if isinstance(last_run, str):
        try:
            last_run = datetime.fromisoformat(last_run)
        except (ValueError, TypeError):
            return True  # fail-open on garbage
    try:
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - last_run).total_seconds() >= interval_days * 86400
    except (AttributeError, TypeError):
        return True  # fail-open


def _read_biweekly_last_run():
    try:
        import json
        return json.loads(_BIWEEKLY_STATE.read_text()).get("last_run")
    except Exception:
        return None  # → fail-open (run)


def _write_biweekly_last_run(now) -> None:
    try:
        import json
        _BIWEEKLY_STATE.parent.mkdir(parents=True, exist_ok=True)
        _BIWEEKLY_STATE.write_text(json.dumps({"last_run": now.isoformat()}))
    except Exception as e:
        logger.warning("eval-biweekly last-run write failed: %s", e)


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
    # include_behavior=True: this biweekly sweep is the intended full-coverage
    # run — it spawns real behavior-tier agents (run_6980cb35). The raw run_eval
    # default stays False (safe); only this caller + CLI --include-behavior opt in.
    result = run_eval(gs, "scheduled", None, root, verify_teeth=True, include_behavior=True)
    write_run(result, root)
    # Refresh the daemon's in-memory EvalService singleton so the GUI
    # (/api/eval/health + /history) reflects this run — write_run only touches
    # disk (Gate-1 D: the deleted CLI job did this via curl /api/eval/reload).
    try:
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:18321/api/eval/reload",
                               data=b"", timeout=5).read()
    except Exception as e:
        logger.warning("eval-scheduled GUI reload failed (non-fatal): %s", e)
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

    # Every-two-weeks gate (run_6980cb35, Gate-1 F): the cron fires every Monday
    # but a REAL run happens only once per 14 days. Gate on our OWN timestamp file
    # (_BIWEEKLY_STATE), NOT JobState.last_run — the executor rewrites last_run on
    # every result incl. a skip, so gating on it would deadlock (a weekly skip
    # keeps resetting the clock). FAIL-OPEN (no/garbage timestamp → run).
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if not _should_run_biweekly(_read_biweekly_last_run(), now):
        logger.info("eval-scheduled: within %dd of last run — skipping (biweekly gate)",
                    _BIWEEKLY_INTERVAL_DAYS)
        return {"status": "skipped", "reason": "biweekly gate: <14d since last run",
                "notified": False}

    try:
        out = run(root)
    except Exception as e:
        logger.error("eval-scheduled run crashed: %s", e)
        return {"status": "error", "reason": f"{type(e).__name__}: {e}"}

    # A real run happened → advance the biweekly clock (ONLY here, never on the
    # skip path above, so a skip can't reset it — Gate-1 F deadlock fix).
    _write_biweekly_last_run(now)

    this = out.get("this") or {}
    baseline = out.get("baseline") or {}
    bvt = this.get("bvt") or {}
    base_score = baseline.get("overall_score")

    # Behavior-tier is FULLY SEGREGATED from the deterministic hard alert
    # (run_6980cb35 decision B + Gate-2 HIGH×2): behavior cases spawn real agents
    # and are non-deterministic — a flaky spawn must NOT fire the deterministic
    # drift OR coverage-collapse alarm. It is NOT enough to keep behavior out of
    # reasons[]: behavior pass/fail feeds overall_score (→ drift) and behavior
    # spawn-errors feed cases_error (→ coverage). So we recompute BOTH the drift
    # score AND the coverage error/scored counts over DETERMINISTIC cases only
    # (programmatic + llm), from this["cases"] (eval_method rides there — Gate-1 E).
    cases = this.get("cases") or []
    behavior_failed = [c.get("id") for c in cases
                       if c.get("eval_method") == "behavior"
                       and c.get("status") in ("failed", "error")]
    det_cases = [c for c in cases if c.get("eval_method") != "behavior"]
    det_scored = sum(1 for c in det_cases if c.get("status") in ("passed", "failed"))
    det_error = sum(1 for c in det_cases if c.get("status") == "error")
    det_passed = sum(1 for c in det_cases if c.get("status") == "passed")
    # Deterministic-only score for the DRIFT axis. Fall back to the run-wide
    # overall_score only when the per-case list is unavailable (legacy run) — in
    # that case behavior can't be separated and we accept the old behaviour.
    if det_scored > 0:
        score = round(det_passed / det_scored * 100, 1)
    else:
        score = this.get("overall_score")

    bvt_red = bool(bvt) and not bvt.get("green", False)
    drift = (base_score is not None and score is not None
             and (base_score - score) > _DRIFT_TOLERANCE)

    reasons = []
    if bvt_red:
        reasons.append(f"BVT RED: {bvt.get('passed')}/{bvt.get('total')} "
                       f"(failed={bvt.get('failed')}, error={bvt.get('error')})")
    if drift:
        reasons.append(f"score drift: {base_score}% → {score}% (>{_DRIFT_TOLERANCE} drop)")

    # Coverage collapse — judge infra (Bedrock/creds) degraded. DETERMINISTIC-ONLY
    # (behavior spawn-errors are non-deterministic infra flakes, not judge-infra
    # collapse — Gate-2 HIGH). Fall back to run-wide counts only for a legacy run
    # with no per-case eval_method. intended>0 guard prevents ZeroDivisionError.
    if det_cases:
        scored, n_error = det_scored, det_error
    else:
        scored = this.get("scored_count")
        n_error = this.get("cases_error", 0) or 0
        if scored is None:  # legacy run without scored_count — derive from pass+fail
            scored = (this.get("cases_passed", 0) or 0) + (this.get("cases_failed", 0) or 0)
    intended = scored + n_error
    if intended > 0 and (n_error / intended) >= _COVERAGE_ALERT_THRESHOLD:
        coverage = scored / intended
        reasons.append(f"🔴 judge infra degraded: {n_error}/{intended} cases errored "
                       f"(coverage {coverage:.0%})")

    behavior_note = ""
    if behavior_failed:
        behavior_note = (f"⚠️ behavior (non-deterministic) {len(behavior_failed)} failed — "
                         f"verify before alarm: {', '.join(behavior_failed[:5])}")

    fingerprint = "|".join(reasons)  # "" when clean — behavior_note excluded by design
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
        "behavior_note": behavior_note,  # non-deterministic layer, segregated from hard alert
        "behavior_failed": behavior_failed,
        "notified": notified,
    }


def handle_eval_scheduled(job, state) -> dict:
    """Job-system entry point. `job`/`state` accepted for the executor contract."""
    dry = bool(getattr(job, "config", {}).get("dry_run", False)) if job else False
    return run_eval_scheduled(dry_run=dry)
