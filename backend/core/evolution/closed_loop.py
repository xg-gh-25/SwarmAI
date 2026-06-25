"""Closed-loop audit — the feedback edge ⑥→① of the self-evolution circuit.

Self-evolution closed-loop design §5 + §6e. Two PURE functions (no I/O) so the
audit is testable on synthetic state and can never silently pass on a dead loop:

- audit_recurrence: the Goodhart guard. A falling correction count is only real
  evolution if it falls because of FEWER MISTAKES (known-class recurrence
  dropped after a gate), not because we LOGGED LESS (total capture collapsed).
  These are opposite requirements; conflating them is how "evolution" becomes a
  metric you can game by capturing less.

- loop_closed_meta_test: injects a synthetic class-tagged lesson and walks it
  around the circuit (inject → clean → resident → replay), returning a verdict
  that NAMES the exact broken link. Each link is a CALLABLE supplied by the
  caller, so the meta-test asserts real behavioral transitions — not the mere
  existence of a file (the "100/100 on a dead loop" theater the design condemns).
"""

from __future__ import annotations

from typing import Callable

# Mirror correction_tracker's RED threshold: post_gate/post_rule >= this means
# the class recurred past its structural fix (the fix did not hold).
_RECURRENCE_RED = 2

# A capture-total drop steeper than this fraction, with no matching improvement
# in known-class recurrence, signals "logged less" rather than "fewer mistakes".
_CAPTURE_COLLAPSE_RATIO = 0.5

# The circuit links, in flow order. loop_closed_meta_test walks them in sequence.
_LINK_ORDER = ("inject", "clean", "resident", "replay")


def audit_recurrence(tracker_state: dict, capture_stats: dict) -> dict:
    """Distinguish genuine evolution from Goodhart capture-deflation.

    Args:
        tracker_state: {class_name: entry} from CorrectionClassTracker. Each
            entry has count, post_gate_count, post_rule_count, active_gate,
            resolved. Resolved classes are excluded (already closed out).
        capture_stats: {"total_this_period": int, "total_prev_period": int} —
            total corrections captured this vs prior period. Falling capture
            with persistent recurrence = logged-less (bad), not fewer-mistakes.

    Returns:
        {"healthy": bool, "reason_class": str, "detail": str}
        reason_class ∈ {fewer_mistakes, logged_less, gate_failed, recurring,
                        no_activity}.
    """
    # Filter None entries: the wiring builds tracker_state from
    # {name: get_class(name)}, and get_class returns None when a raw class_names()
    # key doesn't survive canonicalization. A None value here would crash
    # `.get()` and turn the whole Phase-3d audit into a permanent silent no-op
    # (caught by the caller's try/except). Skip None + resolved entries. (adv #2)
    active = {
        name: e for name, e in (tracker_state or {}).items()
        if e and not e.get("resolved")
    }

    prev = capture_stats.get("total_prev_period", 0) or 0
    cur = capture_stats.get("total_this_period", 0) or 0
    capture_collapsed = prev > 0 and cur < prev * _CAPTURE_COLLAPSE_RATIO

    # "Recurrence persists" = some active class is still recurring post-fix.
    any_recurring = any(
        (e.get("post_gate_count", 0) or 0) >= 1 or (e.get("post_rule_count", 0) or 0) >= _RECURRENCE_RED
        for e in active.values()
    )

    # Goodhart guard FIRST: if capture collapsed, the recurrence counts become
    # untrustworthy (you can't measure mistakes you stopped logging). A capture
    # collapse alongside persistent recurrence is the logged-less failure — named
    # BEFORE gate_failed because the collapsed denominator is the root distortion.
    # gate_failed / recurring are only trustworthy verdicts when capture is stable.
    if capture_collapsed and any_recurring:
        return {
            "healthy": False,
            "reason_class": "logged_less",
            "detail": f"capture fell {prev}->{cur} (>{int((1-_CAPTURE_COLLAPSE_RATIO)*100)}% drop) "
                      f"while a known class still recurs — fewer corrections from "
                      f"logging less, not fewer mistakes (Goodhart)",
        }

    # Capture trustworthy: a class recurring past its deployed gate = gate FAILED.
    for name, e in active.items():
        post_gate = e.get("post_gate_count", 0) or 0
        if e.get("active_gate") and post_gate >= _RECURRENCE_RED:
            return {
                "healthy": False,
                "reason_class": "gate_failed",
                "detail": f"{name}: {post_gate} recurrences since gate "
                          f"{e.get('active_gate')} — gate did not hold, escalate",
            }

    if any_recurring:
        return {
            "healthy": False,
            "reason_class": "recurring",
            "detail": "a known correction class is still recurring (no gate yet, "
                      "or recurrence past rule) — not yet converged",
        }

    if not active:
        return {"healthy": True, "reason_class": "no_activity",
                "detail": "no unresolved correction classes"}

    # Healthy: known classes are gated/quiet AND no class is recurring. The
    # detail must NOT claim "capture is stable" when it collapsed without
    # recurrence (adv #4) — that's a contradictory log line. Branch the wording
    # on the actual capture state.
    if capture_collapsed:
        detail = (
            f"known classes are gated/quiet and no class recurs, but capture fell "
            f"{prev}->{cur} — plausibly fewer mistakes; watch next period in case "
            f"it is logging less"
        )
    else:
        detail = (
            "known classes are gated/quiet and capture is stable — "
            "the falling correction count reflects fewer mistakes"
        )
    return {"healthy": True, "reason_class": "fewer_mistakes", "detail": detail}


def loop_closed_meta_test(
    inject: Callable[[dict], bool],
    clean: Callable[[dict], bool],
    resident: Callable[[dict], bool],
    replay: Callable[[dict], bool],
    lesson: dict | None = None,
) -> dict:
    """Walk a synthetic class-tagged lesson around the circuit; name the break.

    Injects a synthetic lesson, then verifies it survives each downstream link
    via the supplied callables (behavior, not existence). Short-circuits at the
    FIRST failing link and reports its name — a bare False would hide WHERE the
    circuit is open, which is the whole diagnostic value.

    Each link callable receives the synthetic lesson and returns True (link
    intact) or False (link broken). An exception in a link is treated as broken
    (a crashing link is a broken link).

    Args:
        inject:   lesson enters the system (e.g. written to a store).
        clean:    lesson SURVIVES the clean/decay pass (not wrongly pruned).
        resident: lesson is present in a resident/injected store.
        replay:   a triggering scenario reflects the lesson (gate/action).
        lesson:   optional synthetic lesson payload; a default is supplied.

    Returns:
        {"closed": bool, "broken_link": str | None, "detail": str}
    """
    synthetic = lesson or {
        "id": "SYNTH_META",
        "class_name": "META_TEST",
        "title": "synthetic class-tagged lesson for loop-closed meta-test",
    }

    links = {"inject": inject, "clean": clean, "resident": resident, "replay": replay}
    for link_name in _LINK_ORDER:
        fn = links[link_name]
        try:
            ok = bool(fn(synthetic))
        except Exception as exc:  # a crashing link is a broken link
            return {
                "closed": False,
                "broken_link": link_name,
                "detail": f"link '{link_name}' raised: {type(exc).__name__}: {exc}",
            }
        if not ok:
            return {
                "closed": False,
                "broken_link": link_name,
                "detail": f"synthetic lesson did not survive link '{link_name}'",
            }

    return {
        "closed": True,
        "broken_link": None,
        "detail": "synthetic lesson flowed inject→clean→resident→replay — circuit closed",
    }
