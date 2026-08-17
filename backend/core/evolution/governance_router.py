"""Evolution Pipeline v3 Phase 1 — governance router.

Takes a :class:`JudgmentClassification` and routes it by ``counter_state``
(the asymmetric-autonomy decision, design §7):

  - counter_state="counted"  (operational / low-risk)
        -> ``tracker.record(class)`` ONCE. Auto-counts toward the 3x threshold.
  - counter_state="pending_confirm"  (cognitive / CLASS_*)
        -> RECORD (idempotent by correction_ref, confidence>=0.6) AND append to
           the pending-confirm queue (flock-safe JSON) + build a SOUL Intake Gate
           brief. Two views: the counter feeds the autonomous escalate loop; the
           queue feeds the human dashboard.

CONTRACT CHANGE (run_448a4f7f, XG directive): the prior invariant —
"cognitive NEVER records; the counter is human-verified" — was deliberately
overridden. Recording a recurring mistake is an act of self-awareness
(cognition), not a permission item. The human mentor's oversight moves to the
constitution-WRITE step (git-tracked + surfaced in the growth report), NOT a gate
on whether the agent may COUNT its own mistakes. Misclassification is bounded not
by withholding the count but by precision teeth: correction_ref dedup +
confidence floor + the upstream is_cognitive_class axis guard.

Safety invariants (design §9, still hold):
  - This module NEVER writes to SOUL/AGENT/STEERING. It records the counter and
    persists the pending queue; constitution writes happen elsewhere, git-tracked
    and report-surfaced.
  - The human Intake Gate (eval_service.decide_governance) remains the accept path
    (register_rule/register_gate); it is now a mentor veto-via-revert over an
    autonomous proposal, not a blocking pre-count approval.

Key public symbols:
    route_classification — route one classification; returns Intake brief | None

Design: Knowledge/Designs/2026-06-22-evolution-pipeline-v3-governance-routing-design.md
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Budget reality (verified against SOUL/AGENT/STEERING 2026-06-27): caps are
# smoke-tests, not guillotines (P6 + AGENT Intake Gate). SOUL principles 7/12
# (room), AGENT rules <=25, STEERING 15/15 (full). A cognitive proposal still
# usually lands as an L1 rule (AGENT) or a gate — but a genuinely new axis MAY
# be promoted to a principle, and hitting a cap triggers the Intake Gate
# JUDGMENT (fold / replace-wallpaper / raise-cap), never a reflexive retire-one.
_BUDGET_NOTE = (
    "Principles 7/12, AGENT rules <=25, STEERING 15/15 (full). "
    "Caps are smoke-tests, not quotas: at a cap, run the Intake Gate JUDGMENT "
    "(fold same-source / replace wallpaper / raise cap) — never cut a "
    "load-bearing item just to hit a number."
)

# Confidence floor for autonomous cognitive recording (run_448a4f7f). A cognitive
# classification below this still PARKS for human review but does NOT auto-advance
# the counter — a low-confidence LLM guess must not mature toward a proposal on its
# own. Live pending items run 0.82-0.93, so 0.6 admits genuine signal and rejects
# malformed/uncertain guesses. The human can still confirm a parked low-conf item.
_COGNITIVE_RECORD_CONFIDENCE_FLOOR = 0.6


def _default_pending_path() -> Path:
    return Path.home() / ".swarm-ai" / "state" / "governance_pending.json"


def _flock(fd, exclusive=True):
    try:
        from utils.file_lock import flock_exclusive

        flock_exclusive(fd)
    except ImportError:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _funlock(fd):
    try:
        from utils.file_lock import flock_unlock

        flock_unlock(fd)
    except ImportError:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _append_pending(item: dict, pending_path: Path) -> None:
    """Append one item to the pending-confirm queue under an exclusive lock.

    Re-reads the queue under lock (parallel sessions may have appended), then
    writes atomically (tmp + replace). Same pattern as correction_tracker.
    """
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = pending_path.with_suffix(".json.lock")
    fd = open(lock_path, "w")
    try:
        _flock(fd)
        queue: list = []
        if pending_path.exists():
            try:
                loaded = json.loads(pending_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    queue = loaded
            except (json.JSONDecodeError, OSError):
                queue = []  # corrupt -> start fresh (item still gets appended)
        queue.append(item)
        tmp = tempfile.NamedTemporaryFile(
            mode="w", dir=pending_path.parent, suffix=".tmp", delete=False, encoding="utf-8"
        )
        try:
            json.dump(queue, tmp, indent=2, ensure_ascii=False)
            tmp.close()
            Path(tmp.name).replace(pending_path)
        except Exception:
            tmp.close()
            Path(tmp.name).unlink(missing_ok=True)
            raise
    finally:
        _funlock(fd)
        fd.close()


def _build_intake_brief(jc) -> dict:
    """Build the SOUL Intake Gate brief for a cognitive classification.

    Matches the 4-field protocol the human already expects:
    {classify, parent, conflict, budget}.
    """
    return {
        "classify": "Rule|Gate",  # cognitive almost never a new Principle (budget full)
        "parent": jc.parent_principle or "unknown",
        "conflict": f"check existing rules for {jc.class_name} before promote",
        "budget": _BUDGET_NOTE,
        "class_name": jc.class_name,
        "evidence": jc.evidence,
        "confidence": jc.confidence,
        "correction_ref": jc.correction_ref,
    }


def _default_corrections_path() -> Path:
    # Canonical corpus (jobs.paths.STATE_DIR) — NOT the stale .context/ copy.
    return Path.home() / ".swarm-ai" / "state" / "corrections.jsonl"


def _default_watermark_path() -> Path:
    return Path.home() / ".swarm-ai" / "state" / "judgment_classifier_watermark.json"


def _read_watermark(watermark_path: Path) -> float:
    """Read last_ts from the watermark file. Missing/corrupt -> 0.0."""
    try:
        if watermark_path.exists():
            data = json.loads(watermark_path.read_text(encoding="utf-8"))
            return float(data.get("last_ts", 0.0))
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    return 0.0


def classify_new_corrections(
    *,
    corrections_path: Path | None = None,
    watermark_path: Path | None = None,
    pending_path: Path | None = None,
    tracker=None,
    evolution_classes: list[str] | None = None,
    bedrock_client=None,
    max_records: int = 50,
) -> dict:
    """Classify + route only corrections NEWER than the watermark.

    This is the hook entry point. It gates BOTH tiers on a persisted watermark
    (last-processed ``ts``) so the append-only corrections.jsonl is never
    re-processed — killing the double-count + recurring-LLM-cost failure modes
    that Gate 1 caught.

    Degrade-to-log: any failure returns a partial summary; never raises.

    Returns a summary dict: {processed, operational, cognitive, skipped, watermark}.
    """
    from core.evolution.judgment_classifier import classify_correction

    corrections_path = corrections_path or _default_corrections_path()
    watermark_path = watermark_path or _default_watermark_path()
    summary = {"processed": 0, "operational": 0, "cognitive": 0, "skipped": 0, "watermark": 0.0}

    if tracker is None:
        from core.evolution.correction_tracker import CorrectionClassTracker

        tracker = CorrectionClassTracker()

    if not corrections_path.exists():
        return summary

    # Adversarial #1 (CRITICAL): the ENTIRE read-watermark / process / write-watermark
    # span runs under a single exclusive lock. This hook fires at session close across
    # concurrent sessions on the same shared state/ dir; without one lock spanning the
    # whole span, two runs interleave and the watermark write regresses (B writes 150
    # after A writes 200) -> re-processing + double-count, or permanent skips.
    # Adversarial #2 (HIGH): we also re-read the on-disk watermark UNDER the lock and
    # take max(...) before writing, so the watermark can never move backwards even if
    # the lock is somehow bypassed (defense in depth).
    watermark_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = watermark_path.with_suffix(".json.lock")
    lock_fd = open(lock_path, "w")
    try:
        _flock(lock_fd)

        last_ts = _read_watermark(watermark_path)

        new_records = []
        try:
            for line in corrections_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if float(rec.get("ts", 0.0)) > last_ts:
                    new_records.append(rec)
        except OSError as exc:
            logger.debug("classify_new_corrections read failed: %s", exc)
            summary["watermark"] = last_ts
            return summary

        new_records.sort(key=lambda r: float(r.get("ts", 0.0)))
        if len(new_records) > max_records:
            logger.info(
                "classify_new_corrections: capping %d new records to %d (rest next run)",
                len(new_records),
                max_records,
            )
            new_records = new_records[:max_records]

        max_seen = last_ts
        for rec in new_records:
            max_seen = max(max_seen, float(rec.get("ts", 0.0)))
            jc = classify_correction(
                rec, evolution_classes=evolution_classes, bedrock_client=bedrock_client
            )
            if jc is None:
                summary["skipped"] += 1
                continue
            route_classification(jc, tracker, pending_path=pending_path)
            summary["processed"] += 1
            if jc.axis == "cognitive":
                summary["cognitive"] += 1
            else:
                summary["operational"] += 1

            # A-pipeline DECOMMISSIONED (run_1bfd3cf9, option-D source governance):
            # this branch used to seed a tier=draft golden skeleton per cognitive
            # CLASS_A/B/C correction. Those skeletons were tautologies (rubric =
            # "PASS if the agent consults AGENT.md" — a competent agent trivially
            # passes) with NO negative_example, so under the new teeth gate
            # (eval_service.gate_by_knockout) they are 100% fail-closed — they can
            # never be admitted. Rather than feed the teeth gate guaranteed-reject
            # skeletons every session, we stop seeding from corrections entirely.
            #
            # The DURABLE signal is untouched: the correction is still recorded to
            # the tracker (route_classification above) and lives in corrections.jsonl.
            # Auto-growth of the eval suite now flows ONLY through the B pipeline
            # (session_harvest), which HAS an LLM boundary to generate a full case +
            # negative_example that the teeth gate can actually knockout-test.
            # (auto_seed_case itself is retained for its direct callers/tests but is
            # no longer wired to the correction hot-path.)
            _ = jc.counter_state  # (kept: routing/classification above is the live effect)

        # Monotonic write: never regress below what's already on disk.
        on_disk = _read_watermark(watermark_path)
        max_seen = max(max_seen, on_disk)
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", dir=watermark_path.parent, suffix=".tmp", delete=False, encoding="utf-8"
            )
            json.dump({"last_ts": max_seen}, tmp)
            tmp.close()
            Path(tmp.name).replace(watermark_path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("watermark write degraded: %s", exc)

        summary["watermark"] = max_seen
        return summary
    finally:
        _funlock(lock_fd)
        lock_fd.close()


def _default_proposals_path() -> Path:
    # Existing governance-proposal sink, read by proactive_intelligence -> briefing.
    return Path.home() / ".swarm-ai" / "SwarmWS" / ".context" / ".evolution_proposals.json"


def _append_proposal(proposal: dict, proposals_path: Path) -> None:
    """Append a governance proposal under an exclusive lock, kind-aware dedup.

    Identity = (source_class, proposal_kind) so a rule and a gate proposal for the
    same class coexist (never overwrite each other). Re-reads under lock.
    """
    proposals_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = proposals_path.with_suffix(".json.lock")
    fd = open(lock_path, "w")
    try:
        _flock(fd)
        existing: list = []
        if proposals_path.exists():
            try:
                loaded = json.loads(proposals_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    existing = loaded
            except (json.JSONDecodeError, OSError):
                existing = []
        # Dedup identity: gc_id (if present) OR (source_class, proposal_kind).
        # Mirrors the optimizer's identity so both writers converge — a rule and a
        # gate proposal for the same class coexist; same-identity replaces.
        gc_id = proposal.get("gc_id")
        src = proposal.get("source_class")
        kind = proposal.get("proposal_kind", "rule")
        # Stamp a stable id = the dedup identity itself (gc_id, or source:kind).
        # Derived-but-persisted: stable across reads (Gate-1 Phase-3 Check-1 — the
        # accept/reject API targets this id; the raw items had no unique key).
        proposal["id"] = gc_id or f"{src}:{kind}"
        existing = [
            p
            for p in existing
            if not (
                p.get("target") == "governance"
                and (
                    (gc_id and p.get("gc_id") == gc_id)
                    or (
                        src
                        and p.get("source_class") == src
                        and p.get("proposal_kind", "rule") == kind
                    )
                )
            )
        ]
        existing.append(proposal)
        tmp = tempfile.NamedTemporaryFile(
            mode="w", dir=proposals_path.parent, suffix=".tmp", delete=False, encoding="utf-8"
        )
        try:
            json.dump(existing, tmp, indent=2, ensure_ascii=False)
            tmp.close()
            Path(tmp.name).replace(proposals_path)
        except Exception:
            tmp.close()
            Path(tmp.name).unlink(missing_ok=True)
            raise
    finally:
        _funlock(fd)
        fd.close()


def remove_governance_proposal(proposal_id: str, proposals_path: Path | None = None) -> bool:
    """Remove ONE governance proposal by id, under the SHARED flock.

    The dashboard accept/reject path must mutate .evolution_proposals.json through
    the SAME lock escalate_class/_append_proposal use — otherwise an unlocked
    read-modify-write races the escalation writer and loses a write (adversarial
    HIGH, a regression of the Phase-2 HIGH-3 fix). Re-reads under the lock, removes
    exactly the first id match (not all — adversarial LOW), atomic tmp+replace.

    Non-governance rows (skill-optimization) and other governance rows are preserved.
    Returns True if a row was removed.
    """
    proposals_path = proposals_path or _default_proposals_path()
    if not proposals_path.exists():
        return False
    lock_path = proposals_path.with_suffix(".json.lock")
    fd = open(lock_path, "w")
    removed = False
    try:
        _flock(fd)
        try:
            rows = json.loads(proposals_path.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                rows = []
        except (json.JSONDecodeError, OSError):
            rows = []
        # Remove exactly the FIRST matching id (governance row), preserve the rest.
        out, dropped = [], False
        for r in rows:
            if (
                not dropped
                and isinstance(r, dict)
                and r.get("target") == "governance"
                and (r.get("id") or f"{r.get('source_class')}:{r.get('proposal_kind', 'rule')}") == proposal_id
            ):
                dropped = True
                removed = True
                continue
            out.append(r)
        if removed:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", dir=proposals_path.parent, suffix=".tmp", delete=False, encoding="utf-8"
            )
            try:
                json.dump(out, tmp, indent=2, ensure_ascii=False)
                tmp.close()
                Path(tmp.name).replace(proposals_path)
            except Exception:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                raise
    finally:
        _funlock(fd)
        fd.close()
    return removed


def escalate_class(
    class_name: str,
    tracker,
    proposals_path: Path | None = None,
) -> dict | None:
    """Run the escalation ladder for one correction class; write a proposal if due.

    Reads the class's tracker state, asks the ladder for a decision, and on
    kind in ("rule", "gate") writes a GovernanceProposal to the existing
    .evolution_proposals.json sink (kind-aware dedup). kind="none" -> no write.

    NEVER writes SOUL/AGENT/STEERING. Degrade-to-log on any failure.

    Returns the proposal dict written, or None.
    """
    from core.evolution.escalation_ladder import canonical_class_key, decide_escalation

    try:
        state = tracker.get_class(class_name)
        if not state:
            return None
        decision = decide_escalation(state, class_name=canonical_class_key(class_name))
        if decision.kind == "none" or not decision.proposal:
            return None
        proposals_path = proposals_path or _default_proposals_path()
        _append_proposal(decision.proposal, proposals_path)
        logger.info(
            "escalation: %s -> propose %s (%dx)",
            class_name,
            decision.kind,
            state.get("count", 0),
        )
        return decision.proposal
    except Exception as exc:  # noqa: BLE001 — degrade-to-log
        logger.debug("escalate_class degraded for %s: %s", class_name, exc)
        return None


def route_classification(jc, tracker, pending_path: Path | None = None) -> dict | None:
    """Route one classification by its counter_state.

    Args:
        jc: a JudgmentClassification, or None (degraded — safe no-op).
        tracker: a CorrectionClassTracker (record() called only for "counted").
        pending_path: override for the pending-confirm queue (tests inject tmp).

    Returns:
        The SOUL Intake Gate brief (dict) for cognitive classifications,
        or None for operational / None input.
    """
    if jc is None:
        return None

    pending_path = pending_path or _default_pending_path()

    # counter_state == "ignored": operator/transient NOISE. Neither count nor
    # park — a clean no-op. MUST short-circuit BEFORE the pending_confirm
    # fallthrough below, else an ignored record would be wrongly parked as a
    # cognitive pending-confirm item (the latent PARK bug, Gate-1 Check 5).
    if jc.counter_state == "ignored":
        return None

    if jc.counter_state == "counted":
        # Operational / low-risk: auto-count toward the 3x threshold.
        try:
            label = jc.class_name or jc.axis  # operational has no CLASS -> use axis
            tracker.record(label, evidence=(jc.evidence[0] if jc.evidence else ""))
        except Exception as exc:  # noqa: BLE001 — counting must never break routing
            logger.debug("router record() degraded: %s: %s", type(exc).__name__, exc)
        return None

    # counter_state == "pending_confirm": cognitive. RECORD (autonomous
    # self-awareness) AND park (human-visible Intake brief) — two views.
    #
    # CONTRACT CHANGE (run_448a4f7f, XG directive): the prior invariant was
    # "cognitive parks but NEVER records — the counter is human-verified." That
    # is deliberately overridden: recording a recurring mistake is cognition, not
    # a permission item. The human mentor's oversight moves to the constitution-
    # WRITE step (git-tracked + surfaced in the growth report), NOT a gate on
    # whether the agent may COUNT its own mistakes. So the live cognitive stream
    # now advances the counter, and the existing autonomous escalate_class loop
    # (evolution_maintenance_hook) proposes a structural fix on real recurrence
    # WITHOUT being asked.
    #
    # Precision teeth (Gate-1, so misclassification can't flood the counter):
    #   - correction_ref dedup: record() is idempotent by ref. The live pending
    #     queue parked ONE correction_ref 3x with 3 different evidence strings —
    #     text-dedup would triple-count; ref-dedup counts it once.
    #   - confidence floor (0.6): a low-confidence LLM guess parks for human
    #     review but does NOT auto-advance the autonomous counter.
    # The axis guard upstream (is_cognitive_class) already makes operational
    # noise structurally unable to reach this branch.
    if jc.confidence >= _COGNITIVE_RECORD_CONFIDENCE_FLOOR:
        try:
            tracker.record(
                jc.class_name,
                evidence=(jc.evidence[0] if jc.evidence else ""),
                correction_ref=jc.correction_ref,
            )
        except Exception as exc:  # noqa: BLE001 — counting must never break routing
            logger.debug("router cognitive record() degraded: %s: %s",
                         type(exc).__name__, exc)

    item = {
        "correction_ref": jc.correction_ref,
        "axis": jc.axis,
        "class_name": jc.class_name,
        "parent_principle": jc.parent_principle,
        "evidence": jc.evidence,
        "confidence": jc.confidence,
        "status": "pending_confirm",
    }
    try:
        _append_pending(item, pending_path)
    except Exception as exc:  # noqa: BLE001 — degrade-to-log
        logger.debug("router pending append degraded: %s: %s", type(exc).__name__, exc)
    return _build_intake_brief(jc)
