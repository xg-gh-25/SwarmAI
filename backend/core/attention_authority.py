"""AttentionAuthority — the single READ-AGGREGATION layer behind the unified
"Need You" channel.

Design: Knowledge/Designs/2026-08-08-unified-need-you-channel-design.md

WHY this is a read-aggregation layer and NOT a new store/table:
    The sources each own their lifecycle correctly (escalation has resolve,
    a governance proposal has a decision, a run has resume). A central "attention" table
    would force every source to dual-write → state drift — the exact lesson from
    2026-08-03-unified-review-trigger ("git is already the authority, don't build
    a new table"). So this module OWNS no state: it READS the sources' existing
    entry points and NORMALIZES them into one shape.

The single output is `collect()` → `AttentionResult` (items + counts), consumed by:
    - GET /api/attention → frontend needs-you overlay
    - the same endpoint → agent SENSE ("show me / handle Need You")

Two tiers (design §2 — "does the user NOT-acting break something?"):
    - BLOCKING : work is STOPPED, nothing moves without the user
                 (L2 escalation · paused pipeline `pause_kind=="decision"` · circuit-broken job)
    - REVIEW   : work self-advances (L1 timeout auto-accepts); the user's input
                 is confirm-or-override (governance · L1 consult)

Brain attribution (design §4 Gate-1 correction): every item carries the brain
(project) it belongs to, EXCEPT governance — governance proposals are
workspace-global OS-level rule/gate changes with NO project, so `brain=None`
(rendered in an "OS-level" group; excluded from any per-brain count).

Actions: NONE here. There is no /act endpoint. Each item carries a `dispatch`
payload; the action is "inject into a chat tab" via the EXISTING onItemClick
mechanism (design principle 3 — reuse, do not build a new dispatch channel).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional
import logging
import re

from config import get_app_data_dir

logger = logging.getLogger(__name__)

# Circuit-breaker threshold — a job enters BLOCKING only when it has failed this
# many times in a row (matches jobs.scheduler check_circuit_breaker at
# _FAILURE_ALERT_THRESHOLD=3). This is a FILTER TIGHTENING vs the old frontend
# behaviour (`consecutive_failures > 0`): transient one-off failures self-heal on
# the next scheduled run and are NOT the user's problem — only a broken streak is.
_JOB_CIRCUIT_BREAKER_THRESHOLD = 3

# R3 闭环 dispatch: a fresh result of a DDD-owned job surfaces as REVIEW for this many
# hours, then ages out (the window IS the state — no read-marker file, keeps collect()
# read-only). 20h covers the daily-scan cadence gap without perpetual re-surfacing.
_DIGEST_FRESH_HOURS = 20

# Tiers
TIER_BLOCKING = "blocking"
TIER_REVIEW = "review"

# ── Plain-language writing (design welcome-screen-v2.md "Needs you rows") ──────
# Everything an item carries is read by the USER first: `title` says what is
# needed of them, `summary` says which thing it is, `dispatch.message` is written
# in the user's voice (they may send it as-is) with the facts a person can read,
# and the ids an agent needs go on one closing "References" line.

_SUMMARY_MAX = 160
_FACT_MAX = 2000


def _one_line(text: str, limit: int = _SUMMARY_MAX) -> str:
    """Collapse whitespace; cut at `limit` with an ellipsis."""
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _prompt(ask: str, facts: list[tuple[str, str]] = (), refs: list[str] = ()) -> str:
    """The user-voice dispatch message: the ask, the readable facts, then one
    References line for the ids. Empty facts are dropped, never shown blank."""
    parts = [ask.strip()]
    lines = [f"{label}:{value}" if value.startswith("\n") else f"{label}: {value}"
             for label, value in facts if value]
    if lines:
        parts.append("\n".join(lines))
    refs = [r for r in refs if r]
    if refs:
        parts.append("References: " + " · ".join(refs))
    return "\n\n".join(parts)


@dataclass
class AttentionItem:
    """One normalized "needs you" item, source-agnostic.

    `id` carries a source prefix (e.g. "escalation:esc_abc") so it is globally
    unique across the sources. `brain` is the owning project, or None for
    OS-level (governance). `dispatch.message` is what gets injected into a chat
    tab when the user clicks the item (reusing onItemClick).
    """

    id: str
    source: str          # escalation | paused_run | governance | job | …
    tier: str            # blocking | review
    brain: Optional[str]  # project name, or None for OS-level (governance)
    title: str           # plain: what is needed of the user
    detail: str = ""
    dispatch: dict[str, Any] = field(default_factory=dict)
    summary: str = ""    # plain, one line: which thing this is

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AttentionResult:
    items: list[AttentionItem] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: {TIER_BLOCKING: 0, TIER_REVIEW: 0})

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [it.to_dict() for it in self.items],
            "counts": dict(self.counts),
        }


# ── Per-source collectors ────────────────────────────────────────────────────
# Each collector is fail-soft: on ANY error it logs + returns [] so one broken
# source never blanks the whole channel (the channel's value is completeness, so
# a partial channel with a logged gap beats a crashed one).


def _collect_escalations(workspace_root: Path) -> list[AttentionItem]:
    """L2 BLOCK → BLOCKING ; L1 CONSULT → REVIEW. (L0 INFORM is auto-resolved on
    creation, never open, so it never appears here.)"""
    items: list[AttentionItem] = []
    try:
        from core.escalation import get_open_escalations, Level  # local import: avoid cycle
        from routers.ddd_brain import _list_project_dirs

        for pdir in _list_project_dirs():
            project = pdir.name
            try:
                for esc in get_open_escalations(workspace_root, project):
                    lvl = int(getattr(esc, "level", Level.BLOCK))
                    if lvl == int(Level.BLOCK):
                        tier = TIER_BLOCKING
                    elif lvl == int(Level.CONSULT):
                        tier = TIER_REVIEW
                    else:
                        continue  # INFORM (0) — not actionable, skip
                    options = "; ".join(
                        o.label + (" (recommended)" if getattr(o, "is_recommendation", False) else "")
                        for o in (getattr(esc, "options", None) or []) if getattr(o, "label", "")
                    )
                    blocked = tier == TIER_BLOCKING
                    items.append(AttentionItem(
                        id=f"escalation:{esc.id}",
                        source="escalation",
                        tier=tier,
                        brain=project,
                        title=("Answer a question SwarmAI is stuck on" if blocked
                               else "SwarmAI would like your opinion"),
                        summary=_one_line(esc.title),
                        detail=esc.situation or "",
                        dispatch={
                            "message": _prompt(
                                (f"SwarmAI {'is stuck and needs my answer' if blocked else 'asks for my opinion'} "
                                 f"on a question in the {project} brain. Explain in plain words what it is "
                                 "asking, what the options mean and what you recommend, then wait for my decision."),
                                [("Question", esc.title),
                                 ("What happened", _one_line(esc.situation or "", _FACT_MAX)),
                                 ("Options", options),
                                 ("SwarmAI's pick", getattr(esc, "recommendation", None) or "")],
                                [f"escalation {esc.id}"],
                            ),
                            "context": {"kind": "escalation", "project": project,
                                        "escalation_id": esc.id, "level": lvl},
                        },
                    ))
            except Exception as exc:  # per-project isolation
                logger.warning("attention: escalation read failed for %s: %s", project, exc)
    except Exception as exc:
        logger.warning("attention: escalation source failed: %s", exc)
    return items


def _collect_governance() -> list[AttentionItem]:
    """Pending governance proposals → REVIEW, brain=None (OS-level).

    A proposal is an EVOLUTION.md Governance Candidates row at 3× evidence whose
    Checked cell says what it was checked against (core/evolution/governance_miner).
    Governance is NOT per-project: it changes SOUL/AGENT rules for the whole OS, so it
    has no project field → brain=None, excluded from per-brain counts and rendered in
    the overlay's OS-level group. The verdict is given on the C&M dashboard; the item's
    message asks the agent to explain the proposal so the person can decide.
    """
    items: list[AttentionItem] = []
    try:
        from core.eval_service import get_eval_service

        for gp in get_eval_service().get_pending_governance().get("proposals", []):
            gid = str(gp.get("id") or gp.get("gc_id") or "")
            # producer fields are untrusted: only a string is shown (a list or dict
            # would str() to a Python repr)
            rule, checked, explicit = (v.strip() if isinstance(v, str) else ""
                                       for v in (gp.get("proposed_rule"), gp.get("checked"),
                                                 gp.get("title")))
            occ = gp.get("occurrence_count")
            seen = (f"Seen {occ} times" if isinstance(occ, int) and not isinstance(occ, bool)
                    else "Seen repeatedly")
            title = explicit or rule or f"Rule proposal {gid}"
            examples = [_one_line(e) for e in (gp.get("evidence") or [])
                        if isinstance(e, str) and e.strip()][:3]
            items.append(AttentionItem(
                id=f"governance:{gid}",
                source="governance",
                tier=TIER_REVIEW,
                brain=None,  # OS-level — no owning brain
                title=title[:120],
                summary=f"{seen} · SwarmAI suggests a new rule",
                detail=(checked or rule)[:400],
                dispatch={
                    "message": _prompt(
                        "SwarmAI suggests a new rule for how it works. Explain the problem it "
                        "prevents in simple words, say how it differs from the rules already "
                        "in place, and let me decide.",
                        [("Proposed rule", rule), ("Checked against", checked),
                         ("Evidence", "".join(f"\n- {e}" for e in examples))],
                        [f"proposal {gid} (EVOLUTION.md Governance Candidates)"],
                    ),
                    "context": {"kind": "governance", "proposal_id": gid},
                },
            ))
    except Exception as exc:
        logger.warning("attention: governance source failed: %s", exc)
    return items


#: what each v1 state needs: (title, the ask in the user's voice). The run's own
#: requirement, reason and ids are added as facts / references by _prompt.
_NEEDS_YOU_TEXT = {
    "decision": ("Answer a question a pipeline run is waiting on",
                 "A pipeline run is waiting for my decision. Find the question it is waiting on, "
                 "explain it and the options in plain words, and wait for my answer. The question "
                 "card belongs to the run's own chat session."),
    "stopped": ("Resume or drop a stopped pipeline run",
                "This pipeline run was stopped before it finished. In plain words, tell me what it "
                "already did, what is left, and whether I should resume it or drop it."),
    "worktree": ("A pipeline run could not start its working copy",
                 "This pipeline run could not create its working copy, so it cannot go on. Explain "
                 "simply why, what I need to fix, and how to resume it after."),
    "idle": ("A pipeline run is active but nobody is driving it",
             "This pipeline run is still open but no chat session is driving it. Tell me where "
             "it is, and whether I should continue it in its own chat or cancel it."),
    "abandoned": ("A pipeline run was dropped before it landed",
                  "This pipeline run was dropped without landing its change. Tell me in plain words "
                  "what it was doing and why it stopped; a new run is the way to redo it."),
}


def _collect_paused_runs(pipeline_runs: Optional[list[dict]] = None) -> list[AttentionItem]:
    """v1 runs that need a person (``pipeline_metrics.needs_you``: a pending decision,
    a stopped run, a worktree that could not be made, an active run no session drives,
    an abandoned run) → BLOCKING. Reuses the pipelines router's loader + classifier."""
    items: list[AttentionItem] = []
    try:
        from core import pipeline_metrics as pm
        from routers.pipelines import _load_pipeline_runs, _to_response

        raws = pipeline_runs if pipeline_runs is not None else _load_pipeline_runs()
        for raw in raws:
            try:
                resp = _to_response(raw)
                reason = raw.get("reason") if isinstance(raw.get("reason"), str) else None
                landed = raw.get("landed_as") if isinstance(raw.get("landed_as"), dict) else None
                if not pm.needs_you(resp.status.value, resp.pause_kind, reason, landed):
                    continue
                project = raw.get("_project", raw.get("project", "unknown"))
                run_id = resp.id
                requirement = resp.requirement or ""
                owner = raw.get("session_id") if isinstance(raw.get("session_id"), str) else None
                state = resp.pause_kind or resp.status.value
                title, ask = _NEEDS_YOU_TEXT.get(state, _NEEDS_YOU_TEXT["abandoned"])
                if state == "stopped" and reason == "user_stop":
                    title = "Resume or drop a pipeline run you stopped"
                items.append(AttentionItem(
                    id=f"paused_run:{run_id}",
                    source="paused_run",
                    tier=TIER_BLOCKING,
                    brain=project,
                    title=title,
                    summary=_one_line(requirement) or run_id,
                    detail=reason or "",
                    dispatch={
                        "message": _prompt(
                            ask,
                            [("What it was for", _one_line(requirement, _FACT_MAX)),
                             ("Brain", project),
                             ("Reason recorded", reason or "")],
                            [f"run {run_id}", f"its chat session {owner}" if owner else ""],
                        ),
                        "context": {"kind": "paused_run", "project": project, "run_id": run_id,
                                    "owner_session_id": owner},
                    },
                ))
            except Exception as exc:
                logger.warning("attention: paused-run coerce failed: %s", exc)
    except Exception as exc:
        logger.warning("attention: paused-run source failed: %s", exc)
    return items


def _collect_jobs() -> list[AttentionItem]:
    """Broken jobs → BLOCKING. Two classes, both gated on a >=threshold STREAK:

    1. Circuit-broken (consecutive_failures) — a real job bug that stopped it.
    2. Persistently auth-failing (consecutive_auth_failures) — auth failures
       self-heal + auto-retry (so a blip does NOT surface), but a PERSISTENT one
       (revoked IdC, wrong region) would otherwise retry invisibly forever with
       no user-facing trace. Past the threshold it MUST reach the user.

    Filter tightening vs the old frontend (>0): only a BROKEN STREAK is the
    user's problem; transient failures self-heal next scheduled run. Jobs are
    not per-project → brain=None (OS-level infra).
    """
    items: list[AttentionItem] = []
    try:
        from jobs.scheduler import load_jobs, load_state

        state = load_state()
        jobs_by_id = {j.id: j for j in load_jobs()}
        for job_id, js in (state.jobs or {}).items():
            job = jobs_by_id.get(job_id)
            name = getattr(job, "name", None) or job_id
            fails = js.consecutive_failures or 0
            auth_fails = getattr(js, "consecutive_auth_failures", 0) or 0

            if fails >= _JOB_CIRCUIT_BREAKER_THRESHOLD:
                items.append(AttentionItem(
                    id=f"job:{job_id}",
                    source="job",
                    tier=TIER_BLOCKING,
                    brain=None,  # OS-level infra
                    title="A scheduled job keeps failing and was paused",
                    summary=f"{name} · failed {fails} runs in a row",
                    detail=(js.last_error or "")[:400],
                    dispatch={
                        "message": _prompt(
                            (f"The scheduled job \u201c{name}\u201d failed {fails} runs in a row, so "
                             "SwarmAI stopped running it. Find out why, explain it to me simply, and "
                             "suggest a fix. Don't restart it until I agree."),
                            [("Last recorded error (raw)", _one_line(js.last_error or "", 400))],
                            [f"job {job_id}"],
                        ),
                        "context": {"kind": "job", "job_id": job_id,
                                    "consecutive_failures": fails},
                    },
                ))
            elif auth_fails >= _JOB_CIRCUIT_BREAKER_THRESHOLD:
                # Persistent auth failure — not a job bug, an auth problem the user
                # must fix (re-login / refresh SSO IdC). Distinct id + wording so it
                # doesn't read as a code failure.
                items.append(AttentionItem(
                    id=f"job_auth:{job_id}",
                    source="job",
                    tier=TIER_BLOCKING,
                    brain=None,  # OS-level infra
                    title="A scheduled job can't sign in",
                    summary=f"{name} · sign-in failed {auth_fails} times in a row",
                    detail=(js.last_error or "")[:400],
                    dispatch={
                        "message": _prompt(
                            (f"The scheduled job \u201c{name}\u201d failed to sign in {auth_fails} "
                             "times in a row, so it can't run. Tell me simply what I need to do to fix "
                             "the sign-in (for example log in again or refresh SSO). It retries by "
                             "itself once sign-in works."),
                            [("Last recorded error (raw)", _one_line(js.last_error or "", 400))],
                            [f"job {job_id}"],
                        ),
                        "context": {"kind": "job_auth", "job_id": job_id,
                                    "consecutive_auth_failures": auth_fails},
                    },
                ))
    except Exception as exc:
        logger.warning("attention: job source failed: %s", exc)
    return items


def _collect_db_recovery() -> list[AttentionItem]:
    """A boot-time DB-recovery marker (run_a456640f, option B) → a BLOCKING item.

    When a suspected-corrupt ``data.db`` was isolated (preserved) + a fresh store
    re-seeded at boot, ``_init_db_bounded`` drops a marker in the app data dir.
    Its presence means the daemon is running on a FRESH store while the user's
    prior data sits preserved in a ``.corrupt-<ts>`` file, awaiting a
    recover-vs-discard decision. Surfacing it here is what makes option B
    NON-SILENT — the decision reaches the user IN their chat at the next session
    open (STEERING #20 'reach the human'), not just a passive log line.

    The same marker also carries non-DB stores isolated by AppConfigManager
    (an unparseable ``config.json`` / ``secrets.json``); those get their own
    store-specific title + recovery steps, chosen from the isolated file name.

    OS-level (brain=None, like governance). Fail-soft: any error → [].
    """
    items: list[AttentionItem] = []
    try:
        from pathlib import Path as _Path
        from core.data_safety import read_recovery_marker
        app_dir = get_app_data_dir()
        marker = read_recovery_marker(app_dir)
        if not marker:
            return items
        # ONE item per STILL-PENDING isolate (adversarial MED: a 2nd corruption
        # accrues into the marker's `pending` list, so multiple isolated stores are
        # each surfaced, never orphaned). SELF-LIMITING (not a nag loop): an entry
        # surfaces WHILE its decision is pending and drops WHEN resolved — RECOVER
        # moves the isolated file back to data.db, DISCARD deletes it, so a recorded
        # isolated path that no longer exists == the user decided. Keyed on observable
        # state, not agent discipline. This reader never writes: resolved entries are
        # pruned by write_recovery_marker on its next write (collect() is read-only).
        pending = marker.get("pending") or [marker]  # legacy flat marker → single entry
        seen_ids: set[str] = set()  # one item per store even if the marker repeats it
        for entry in pending:
            isolated = entry.get("isolated_path", "")
            reason = entry.get("reason", "A corrupt database was isolated at boot.")
            if not isolated or isolated == "<already-absent>" or not _Path(isolated).exists():
                continue  # resolved (or nothing to recover) → don't surface
            store_name = _Path(isolated).name.split(".corrupt-")[0]
            if ".corrupt-" not in _Path(isolated).name and not store_name.startswith("data.db"):
                # Could-not-isolate entry (AppConfigManager): the marker points at
                # the ORIGINAL file, left in place. Resolved once it parses again
                # (fixed by hand) — its mere existence is not "pending".
                try:
                    import json as _json
                    _text = _Path(isolated).read_text(encoding="utf-8").strip()
                    # Mirrors the readers: an empty secrets.json is a valid (empty)
                    # store for set_secret; config.json must be a JSON object.
                    if (not _text and store_name == "secrets.json") or isinstance(_json.loads(_text), dict):
                        continue
                except Exception:  # noqa: BLE001 — still unreadable → keep surfacing
                    logger.debug("_collect_db_recovery: suppressed exception on a fail-safe path", exc_info=True)
                    pass
                if f"db_recovery:{isolated}" in seen_ids:
                    continue
                seen_ids.add(f"db_recovery:{isolated}")
                consequence = (
                    "saving API keys is refused" if store_name == "secrets.json"
                    else "settings changes are refused and the app is running on default "
                         "settings in memory"
                )
                items.append(AttentionItem(
                    id=f"db_recovery:{isolated}",
                    source="db_recovery",
                    tier=TIER_BLOCKING,
                    brain=None,
                    title=f"{store_name} is unreadable and could not be preserved — fix or move it",
                    summary=f"The file is still at {isolated}",
                    detail=reason,
                    dispatch={
                        "message": (
                            f"{store_name} at {isolated} could not be parsed and could not be "
                            f"moved aside automatically, so it was left untouched.\n\n"
                            f"{reason}\n\nUntil it is fixed, {consequence}. Fix the JSON in "
                            f"place, or move the file elsewhere — the next change re-checks it "
                            f"(or restart the app)."
                        ),
                        "context": {"kind": "db_recovery", "isolated_path": isolated},
                    },
                ))
                continue
            if not store_name.startswith("data.db"):
                # A non-DB store (config.json / secrets.json via AppConfigManager)
                # shares the marker but NOT the DB recovery steps: there is no
                # connection pool or -wal/-shm, and the fix is usually a one-line
                # JSON repair of the preserved file.
                items.append(AttentionItem(
                    id=f"db_recovery:{isolated}",
                    source="db_recovery",
                    tier=TIER_BLOCKING,
                    brain=None,
                    title=f"{store_name} was unreadable and has been preserved — recover or discard?",
                    summary=f"Your previous file is kept at {isolated}",
                    detail=reason,
                    dispatch={
                        "message": (
                            f"{store_name} could not be parsed, so it was isolated instead "
                            f"of being overwritten. A fresh {store_name} is in use now; your "
                            f"previous file is preserved (not deleted) at: {isolated}\n\n"
                            f"{reason}\n\n"
                            "Decide: RECOVER or DISCARD.\n"
                            f"RECOVER = fix the JSON in the preserved file, then MOVE it back "
                            f"over {store_name} (moving it clears this notice) and restart the "
                            "app so it is re-read (merge by hand if you have changed settings "
                            "since).\n"
                            "DISCARD = delete the preserved file once you are sure nothing in "
                            "it is needed."
                        ),
                        "context": {"kind": "db_recovery", "isolated_path": isolated},
                    },
                ))
                continue
            items.append(AttentionItem(
                id=f"db_recovery:{isolated}",
                source="db_recovery",
                tier=TIER_BLOCKING,
                brain=None,
                title="Database recovery pending — recover or discard your prior data?",
                summary=f"Your previous data is kept at {isolated}",
                detail=reason,
                dispatch={
                    "message": (
                        "A suspected-corrupt data.db was isolated at boot to PRESERVE it "
                        "(the app may be running on a fresh store). Your prior data is "
                        f"preserved (not deleted) at: {isolated}\n\n{reason}\n\n"
                        "Decide: RECOVER or DISCARD.\n"
                        "⚠️ RECOVER SAFELY — do NOT `mv` the isolated file over data.db while "
                        "the daemon is running (it holds an open connection pool on data.db; "
                        "an in-place swap under the open pool causes torn reads / re-corruption). "
                        "Instead: STOP the daemon first, then replace data.db (and remove any "
                        "stale -wal/-shm), then restart — or copy the isolated db to a staging "
                        "path and restart the daemon to adopt it.\n"
                        "DISCARD = delete the isolated file. Do NOT delete it until you decide."
                    ),
                    "context": {"kind": "db_recovery", "isolated_path": isolated},
                },
            ))
    except Exception as exc:
        logger.warning("attention: db_recovery source failed: %s", exc)
    return items


def _collect_job_digests(workspace_root: Path) -> list[AttentionItem]:
    """A fresh, SUCCESSFUL result of a job some DDD owns → a REVIEW item for that
    DDD whose dispatch.message tells the agent to learn from it and s_persist the
    useful bits into that brain (R3 闭环: job → 🔔 → click → result in chat → sediment).

    Which jobs qualify comes only from the DDD job registry (each DDD's bindings.yaml
    `kind: job`, indexed in .context/ddd_job_registry.json); no job-name pattern and
    no project name (usable-by-anyone P7; this was GitHub_Community-only). Distinct
    from _collect_jobs (circuit-BROKEN jobs as BLOCKING).

    Time-windowed + read-only (design constraint: collect() never writes disk): a
    digest surfaces for _DIGEST_FRESH_HOURS then ages out on its own — no marker
    file, no perpetual noise. Only the NEWEST digest per job_id surfaces (no
    stacking). Fail-soft: any error → [] (never blank the whole channel)."""
    items: list[AttentionItem] = []
    try:
        from datetime import datetime, timezone, timedelta
        from core.daily_activity_writer import parse_frontmatter

        from core import ddd_job_registry
        owner_of = {r["job_id"]: r["owner_ddd"]
                    for r in ddd_job_registry.read_manifest(workspace_root)}
        jr_dir = workspace_root / "Knowledge" / "JobResults"
        if not owner_of or not jr_dir.is_dir():
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=_DIGEST_FRESH_HOURS)
        # newest fresh, successful result per DDD-owned job_id
        newest: dict[str, tuple[datetime, Path, dict]] = {}
        for p in jr_dir.glob("*.md"):
            try:
                # Read only the HEAD, not the whole file. Frontmatter lives in the
                # first few hundred bytes, but this used to read_text() the ENTIRE
                # file (299 JobResult .md files, ~549KB total) just to slice [:600] —
                # reading megabytes to look at kilobytes, on every attention collect().
                # open+read(600) touches only what's needed. errors="replace" keeps
                # the fail-soft contract (a bad byte must not abort the digest scan).
                with open(p, "r", encoding="utf-8", errors="replace") as _fh:
                    head = _fh.read(600)
                fm, _ = parse_frontmatter(head)
            except Exception:
                logger.debug("_collect_job_digests: suppressed exception on a fail-safe path", exc_info=True)
                continue
            if not fm:
                continue
            job_id = str(fm.get("job_id", ""))
            if job_id not in owner_of:
                continue
            if str(fm.get("status", "")).lower() != "success":
                continue
            run_at_raw = str(fm.get("run_at", ""))
            try:
                run_at = datetime.fromisoformat(run_at_raw.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            if run_at < cutoff:
                continue  # aged out of the freshness window
            prev = newest.get(job_id)
            if prev is None or run_at > prev[0]:
                newest[job_id] = (run_at, p, fm)

        for job_id, (run_at, path, fm) in newest.items():
            job_name = str(fm.get("job_name", job_id))
            owner = owner_of[job_id]
            items.append(AttentionItem(
                id=f"job_digest:{path.name}",
                source="job_digest",
                tier=TIER_REVIEW,  # a fresh digest is review-worthy, never blocking
                brain=owner,
                title=f"A new {owner} digest is ready to learn from",
                summary=job_name,
                detail=f"A fresh result from {job_name}, a job of {owner}.",
                dispatch={
                    "message": _prompt(
                        (f"A new result from \u201c{job_name}\u201d ({owner}) is ready. Read it, tell me "
                         "in a few plain bullets what is worth keeping, and save those to the "
                         f"{owner} brain with s_persist. Skip the routine items."),
                        [],
                        [f"digest file {path}"],
                    ),
                    "context": {"kind": "job_digest", "job_id": job_id,
                                "path": str(path)},
                },
            ))
    except Exception as exc:
        logger.warning("attention: job_digest source failed: %s", exc)
    return items


# A local repository a Brain started reading surfaces for this long, then ages out (the
# window is the state: collect() stays read-only).
_LOCAL_ROOT_FRESH_HOURS = 20


def _collect_local_roots() -> list[AttentionItem]:
    """A local repository declared in the last ``_LOCAL_ROOT_FRESH_HOURS`` → a REVIEW
    item for its Brain: it is read at once with no Confirm (design
    2026-10-29-code-intel-auto D1), so the person is told, once, and can take it
    back from the Brain Hub card. Read-only; fail-soft."""
    items: list[AttentionItem] = []
    try:
        import json as _json
        from datetime import datetime, timedelta, timezone
        from core.code_intel.local_roots import CONFIRMED, local_mode, registry_path
        if not local_mode():
            return []
        path = registry_path()
        if not path.is_file():
            return []
        data = _json.loads(path.read_text(encoding="utf-8"))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_LOCAL_ROOT_FRESH_HOURS)
        for brain, rows in (data.get("projects") or {}).items():
            for r in rows if isinstance(rows, list) else []:
                if not isinstance(r, dict) or r.get("state") != CONFIRMED:
                    continue
                at_raw = r.get("confirmed_at") or r.get("added_at") or ""
                try:
                    at = datetime.fromisoformat(str(at_raw))
                except ValueError:
                    continue
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                if at < cutoff:
                    continue
                # the path comes from disk and goes into a message sent in the person's
                # voice: one line, capped, quoted as data
                root = re.sub(r"[\x00-\x1f\x7f]", " ", str(r.get("path") or ""))[:300]
                name = Path(root).name or root
                items.append(AttentionItem(
                    id=f"local_root:{brain}:{root}",
                    source="local_root",
                    tier=TIER_REVIEW,
                    brain=brain,
                    title=f"{brain} now reads the repository {name}",
                    summary=root,
                    detail=(f"{root} is read into {brain}'s code graph. To stop, remove it "
                            "on the Brain Hub card (Local repositories)."),
                    dispatch={
                        "message": _prompt(
                            (f"The Brain {brain} now reads the local repository `{name}`. Tell "
                             "me in two lines what it is and what reading it gives this Brain; "
                             "I can remove it in Brain Hub if it should not be read."),
                            [("path", f"`{root}`"), ("added by", str(r.get("source") or "agent"))],
                        ),
                        "context": {"kind": "local_root", "brain": brain, "path": root},
                    },
                ))
    except Exception as exc:
        logger.warning("attention: local_root source failed: %s", exc)
    return items


def _session_title(session_id: str) -> "tuple[bool, str]":
    """(whether the app session still exists, its title). Read only; a missing or
    unreadable database reads as existing. Whether a run is its dispatching agent's is
    ``session_confirmed``'s rule, not this one's."""
    import sqlite3
    try:
        db_path = Path(get_app_data_dir()) / "data.db"
        if not db_path.exists():
            return True, ""
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        try:
            row = conn.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return True, ""
    return (row is not None), (str(row[0] or "") if row else "")


def session_confirmed(session_id: str) -> bool:
    """The session table says this session exists. Unlike ``_session_title``, a missing
    or unreadable database is not a yes: a run is the dispatching agent's (out of Needs
    You) only while its session is known to exist (design
    2026-11-02-hive-run-decision-record §1)."""
    import sqlite3
    try:
        db_path = Path(get_app_data_dir()) / "data.db"
        if not db_path.exists():
            return False
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        try:
            return conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is not None
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return False


def _host_wait_items(runs: list, asks: list) -> "tuple[list[AttentionItem], list]":
    """Split the open waits of runs an agent dispatched (design 2026-10-31-hive-approvals
    §2.3): one item each for those the person must answer, naming the session that
    answers for the run (a class the agent may not answer, one it escalated, one whose
    session is gone); the agent's own are left to it however long they wait: the session
    that dispatched the run is woken to answer them (core/swarm_out_wake; design
    2026-11-02-hive-run-decision-record: nothing is handed to the person by a timer).
    Returns (items, the waits of runs no agent dispatched)."""
    from core import approval_class, swarm_out_wait_card
    # a run's Hive session id names its waits (as core/swarm_out_wait_card.card joins them)
    by_session = {r.get("session_id"): r for r in runs if r.get("session_id")}
    items, plain = [], []
    titles: dict[str, tuple[bool, str]] = {}
    for a in asks:
        row = by_session.get(a.get("session_id"))
        owner = (row or {}).get("dispatched_from")
        if not row or not owner:
            plain.append(a)
            continue
        dispatch_id, request_id = str(row.get("dispatch_id") or ""), str(a.get("request_id") or "")
        cls = a.get("approval_class")
        if owner not in titles:
            # whether it exists: the one rule every "the agent's or the person's" split
            # reads (``session_confirmed``); the title only names it
            titles[owner] = (session_confirmed(owner), _session_title(owner)[1])
        alive, owner_title = titles[owner]
        esc = swarm_out_wait_card.escalation(dispatch_id, request_id)
        kind = str(a.get("kind") or "question")
        if esc:
            title, why = f"A Hive {kind} the agent handed to you", str(esc.get("why") or "")
        elif cls not in approval_class.HOST_CLASSES:
            title = f"A Hive {kind} only you can answer"
            why = (f"It is about {cls}." if cls and cls != "unknown"
                   else "Its Hive does not say what it is about.")
        elif not alive:
            title, why = f"A Hive {kind} whose session is gone", "The session that dispatched the run no longer exists."
        else:
            continue                       # the dispatching agent's to answer
        run_title = " ".join(str(row.get("title") or "").split())[:80]
        session_name = owner_title or owner[:8]
        items.append(AttentionItem(
            id=f"swarm_out:wait:{dispatch_id}:{request_id}", source="swarm_out", tier=TIER_BLOCKING,
            brain=None, title=title, summary=f"「{run_title}」 · from session 「{session_name}」",
            detail=why,
            # the agent's escalation reason is shown (detail), never put in the message a
            # click sends in the person's voice: it may echo what the Hive wrote
            dispatch={"message": _prompt("Open Swarm Out: " + title.lower() + ".",
                                         [("Run", run_title),
                                          ("Why", "the agent's reason is shown in Swarm Out" if esc else why)],
                                         [f"run {dispatch_id}", f"its chat session {owner}"]),
                      "context": {"kind": "swarm_out", "focus": dispatch_id,
                                  "owner_session_id": owner}}))
    return items, plain


def _collect_swarm_out() -> list[AttentionItem]:
    """What Swarm Out waits on the person for (design 2026-10-31-swarm-out-run-review §3.7,
    2026-10-31-hive-approvals §2.3): the open waits of runs an agent dispatched that are
    the person's (one BLOCKING item each, naming the session that answers for the run),
    the open questions of any other run (one BLOCKING summary item), each landed result
    the dispatching agent handed to the person (``ask-person``, one BLOCKING item with the
    agent's question, judgment and reason), and, for runs no live session answers for,
    the runs to accept or decide (REVIEW) and the runs whose records go within days
    (REVIEW). A run whose dispatching session is alive is never here before it landed:
    reviewing, landing, re-running, discarding it is that session's (design
    2026-11-02-hive-run-decision-record §1), and it is woken for it. Read from the runs
    snapshot the refresh loop keeps (core/swarm_out.snapshot); never refreshes it: this
    authority holds no state. A click opens the Swarm Out tab (``context.kind``
    swarm_out). Read-only; fail-soft."""
    items: list[AttentionItem] = []
    try:
        from core import swarm_out
        snap = swarm_out.snapshot()
        if not snap:
            return []
        runs = [r for r in snap.get("runs") or [] if isinstance(r, dict)]
        # a discarded or expired run's waits are no one's to answer (its Hive may keep a
        # pipeline decision open after the chat stopped)
        closed = {r.get("session_id") for r in runs
                  if r.get("stage") in ("discarded", "expired") and r.get("session_id")}
        asks = [a for a in snap.get("asks") or [] if isinstance(a, dict) and not a.get("refused")
                and a.get("session_id") not in closed]
        # a run a live session dispatched is that session's until it lands and the
        # session hands it over (design 2026-11-02-hive-run-decision-record §1)
        alive: dict[str, bool] = {}

        def owned(r: dict) -> bool:
            owner = r.get("dispatched_from")
            if not owner:
                return False
            if owner not in alive:
                alive[owner] = session_confirmed(owner)
            return alive[owner]
        # an answer the Hive did not use: the Host cannot answer again; the person
        # answers on the Hive's own page (the run's "open session")
        refused = [r for r in runs if r.get("stage") == "needs_answer"
                   and r.get("state_reason") == "answer_refused" and not owned(r)]
        try:
            host_items, asks = _host_wait_items(runs, asks)
            items += host_items
        except Exception as exc:   # the split failed: every wait stays the person's
            logger.warning("attention: swarm_out wait split failed: %s", exc)
        decide = [r for r in runs if r.get("stage") in ("awaiting", "stopped") and not owned(r)
                  and not _open_ask(r)]
        soon = [r for r in runs if r.get("cleanup_soon") and not owned(r)]
        for r in runs:
            a = _open_ask(r)
            if a:
                items.append(_ask_item(r, a))

        def item(key: str, tier: str, n: int, title: str, detail: str) -> AttentionItem:
            return AttentionItem(
                id=f"swarm_out:{key}", source="swarm_out", tier=tier, brain=None,
                title=title, summary=f"{n} in Swarm Out", detail=detail,
                dispatch={"message": _prompt("Open Swarm Out: " + title.lower() + "."),
                          "context": {"kind": "swarm_out", "focus": key}})
        if asks:
            n = len(asks)
            items.append(item("questions", TIER_BLOCKING, n,
                              f"{n} Hive question{'s' if n != 1 else ''} waiting for you",
                              "An agent on a Hive is waiting for your answer."))
        if refused:
            n = len(refused)
            items.append(item("refused", TIER_BLOCKING, n,
                              f"{n} Hive wait{'s' if n != 1 else ''} did not take your answer",
                              "Answer it on the Hive's own page: open the run's session from Swarm Out."))
        if decide:
            n = len(decide)
            items.append(item("decide", TIER_REVIEW, n,
                              f"{n} Swarm Out run{'s' if n != 1 else ''} to accept or decide",
                              "Accept, re-run with a correction, or discard."))
        if soon:
            n = len(soon)
            items.append(item("cleanup", TIER_REVIEW, n,
                              f"{n} Swarm Out run{'s' if n != 1 else ''} cleared within 3 days",
                              "After that, their commits can no longer land."))
    except Exception as exc:
        logger.warning("attention: swarm_out source failed: %s", exc)
    return items


def _open_ask(r: dict) -> Optional[dict]:
    a = (r.get("funnel") or {}).get("ask") if isinstance(r.get("funnel"), dict) else None
    return a if isinstance(a, dict) and a.get("state") == "open" else None


def _ask_item(r: dict, a: dict) -> AttentionItem:
    """A landed result the dispatching agent handed to the person: what they decide,
    where it landed, the agent's judgment and why it could not decide (all Host-written:
    the agent's own words, never the Hive's)."""
    dispatch_id = str(r.get("dispatch_id") or "")
    landed = a.get("landed") if isinstance(a.get("landed"), dict) else {}
    where = ", ".join([str(c)[:12] for c in landed.get("commits") or []] + list(landed.get("files") or [])[:3])
    run_title = " ".join(str(r.get("title") or "").split())[:80]
    detail = (f"{a.get('decide') or ''}\nLanded: {where or 'on this host'}\n"
              f"Agent's judgment: {a.get('judgment') or ''}\nWhy it could not decide: {a.get('why') or ''}")
    return AttentionItem(
        id=f"swarm_out:ask:{dispatch_id}", source="swarm_out", tier=TIER_BLOCKING, brain=None,
        title="Keep or revert what an agent landed", summary=f"「{run_title}」", detail=detail[:1500],
        dispatch={"message": _prompt("Open Swarm Out: keep or revert what an agent landed.",
                                     [("Run", run_title)], [f"run {dispatch_id}"]),
                  "context": {"kind": "swarm_out", "focus": dispatch_id}})


NEEDS_ACCOUNT_TITLE = "Background cognition needs the SwarmAI AWS account"


def _needs_account(conn: Any) -> str:
    """The recorded credentials failure of background cognition when it is the latest
    word: the last finished extraction attempt failed with it, or, with no later
    success, a session job still carries it (``core.ingestion.llm.AWS_CREDENTIALS_REASON``).
    A later successful attempt means the credentials work again: empty, even while an
    old job that ran out of attempts keeps its error until its session is reopened."""
    import sqlite3
    from core.ingestion.llm import AWS_CREDENTIALS_REASON
    pattern = f"%{AWS_CREDENTIALS_REASON}%"
    found = ""
    try:
        last = conn.execute(
            "SELECT status,error FROM extraction_attempts WHERE status<>'running' "
            "ORDER BY COALESCE(finished_at, started_at) DESC, rowid DESC LIMIT 1").fetchone()
        if last is not None and last[0] == "failed" and AWS_CREDENTIALS_REASON in str(last[1] or ""):
            found = str(last[1])
        elif last is not None and last[0] != "failed":
            return ""  # the latest attempt worked: the credentials are fine now
    except sqlite3.Error:
        pass
    if not found:
        try:
            job = conn.execute(
                "SELECT error FROM ingestion_s2_jobs WHERE state IN ('queued','failed') "
                "AND error LIKE ? LIMIT 1", (pattern,)).fetchone()
            found = str(job[0]) if job is not None else ""
        except sqlite3.Error:
            pass
    return found


def _collect_ingestion() -> list[AttentionItem]:
    """Unresolved CDF anomalies, one item per kind; a missing DB is silent. A model call
    that failed for lack of AWS credentials is ONE item that opens Settings → SwarmAI
    AWS Account, and a poison of the same cause is not shown again as a generic one."""
    import json
    import sqlite3
    from contextlib import closing
    from core.ingestion.llm import AWS_CREDENTIALS_REASON

    titles = {
        "id_collision": "Cognitive Distillation found an ID collision",
        "writer_unknown": "A governed file changed outside Cognitive Distillation",
        "user_race": "A file changed while Cognitive Distillation was writing",
        "poison": "A Cognitive Distillation item repeatedly failed",
        "restored_orphan": "A restored file has no matching audit record",
        "missing_section": "Cognitive Distillation needs a target section",
        "stalled": "Cognitive Distillation has stopped moving",
    }
    prompts = {
        "id_collision": "Inspect the Cognitive Distillation ID collision and explain which candidate is trustworthy.",
        "writer_unknown": "Inspect the governed file change that bypassed Cognitive Distillation and tell me what changed.",
        "user_race": "Inspect the Cognitive Distillation user-race record and preserve the user's version.",
        "poison": "Inspect the repeatedly failing Cognitive Distillation item and fix the root cause; then run `\"$SWARM_BACKEND_BIN\" ingest requeue --reason <its reason>` so those units are processed again.",
        "restored_orphan": "Inspect the restored Cognitive Distillation orphan without changing the restored file.",
        "missing_section": "Add the missing governed-file section so Cognitive Distillation can continue.",
        "stalled": "Run `\"$SWARM_BACKEND_BIN\" ingest status`, find why Cognitive Distillation stopped moving and fix the cause.",
    }
    try:
        from core.ingestion.api import connect_ro
        with closing(connect_ro()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id,at,kind,detail FROM ingestion_events WHERE resolved=0 ORDER BY at DESC,id DESC"
            ).fetchall()
            from core.ingestion.status import stalls
            stalled = stalls(conn)
            account_failure = _needs_account(conn)
    except (OSError, sqlite3.Error):
        return []
    except Exception as exc:
        logger.warning("attention: ingestion source failed: %s", exc)
        return []

    items: list[AttentionItem] = []
    seen: set[str] = set()
    if account_failure:
        summary = _one_line(account_failure)
        items.append(AttentionItem(
            id="ingestion:needs_account", source="ingestion", tier=TIER_REVIEW, brain=None,
            title=NEEDS_ACCOUNT_TITLE, summary=summary,
            detail=_one_line("Background cognition (Cognitive Distillation, background jobs) "
                             "calls Bedrock with the SwarmAI AWS account, and no working "
                             "AWS credentials were found. " + account_failure, 400),
            dispatch={"message": _prompt("Open Settings → SwarmAI AWS Account and connect or "
                                         "sign in to the account, so background cognition "
                                         "can run again.", [("Last error", summary)]),
                      "context": {"kind": "aws_account", "settings_tab": "aws-account"}},
        ))
    if stalled:
        # computed from the store, not an event: it clears by itself once CDF moves
        summary = _one_line("; ".join(stalled))
        items.append(AttentionItem(
            id="ingestion:stalled", source="ingestion", tier=TIER_REVIEW, brain=None,
            title=titles["stalled"], summary=summary, detail=summary,
            dispatch={"message": _prompt(prompts["stalled"], [("What stopped", summary)],
                                          ["ingest status"]),
                      "context": {"kind": "stalled"}},
        ))
    for row in rows:
        kind = str(row["kind"])
        if kind not in titles or kind in seen:
            continue
        try:
            detail = json.loads(row["detail"])
        except (TypeError, ValueError):
            detail = {"detail": str(row["detail"] or "")}
        if kind == "poison" and AWS_CREDENTIALS_REASON in str(detail.get("reason") or ""):
            if account_failure:
                continue        # the same cause, already the one "needs the account" item
        seen.add(kind)
        summary = _one_line(str(detail.get("summary") or detail.get("reason") or kind))
        tier = TIER_BLOCKING if kind in {"user_race", "writer_unknown"} else TIER_REVIEW
        prompt = (str(detail.get("prompt") or "")
                  if kind == "missing_section" else prompts[kind])
        items.append(AttentionItem(
            id=f"ingestion:{kind}:{row['id']}", source="ingestion", tier=tier, brain=None,
            title=titles[kind], summary=summary, detail=_one_line(json.dumps(detail, ensure_ascii=False), 400),
            dispatch={"message": _prompt(prompt, [("Recorded detail", summary)],
                                          [f"ingestion event {row['id']}"]),
                      "context": {"kind": kind, "event_id": row["id"]}},
        ))
    return items


def _collect_aws_account() -> list[AttentionItem]:
    """The SwarmAI AWS account (design 2026-10-31-swarmai-aws-account §7.1): a sign-in
    that expired or reaches another account is BLOCKING (chat, Hive and storage stop);
    fewer Hives than the person asked for is one REVIEW item until they say "later"
    (``hive_fleet.dismissed``), never more than once at a time. Reads the record and the
    last Hive status the account page or the reconcile loop computed; a click opens
    Settings → Swarm AWS Account (``context.kind`` aws_account). Read-only; fail-soft."""
    items: list[AttentionItem] = []
    try:
        from core import aws_account
        if aws_account.is_hive_mode():
            return []
        rec = aws_account.get_record()
        state = (rec or {}).get("state")
        acct = str((rec or {}).get("account_id") or "")
        ctx = {"kind": "aws_account", "settings_tab": "aws-account"}
        if rec is not None and state in (aws_account.NEEDS_LOGIN, aws_account.MISMATCH):
            fix = (aws_account.view().get("remediation") or {}).get("fix_text") or ""
            title = ("Sign in to the SwarmAI AWS account again" if state == aws_account.NEEDS_LOGIN
                     else "The SwarmAI AWS sign-in reaches another account")
            items.append(AttentionItem(
                id=f"aws_account:{state}", source="aws_account", tier=TIER_BLOCKING, brain=None,
                title=title, summary=acct, detail=_one_line(fix, 400),
                dispatch={"message": _prompt("Open Settings → Swarm AWS Account: " + title.lower() + "."),
                          "context": ctx}))
        from hive import fleet
        st = fleet.last_status() or {}
        if st.get("state") in ("short", "cannot_build") and not st.get("dismissed"):
            n = int(st.get("short") or 0)
            title = (f"{n} Hive{'s' if n != 1 else ''} short of what you asked for"
                     if st["state"] == "short" else "Hives cannot be created in this account")
            detail = (str((st.get("capability") or {}).get("detail") or "")
                      if st["state"] == "cannot_build" else
                      "Create them on the Swarm AWS Account page, or choose Later.")
            items.append(AttentionItem(
                id="aws_account:hive", source="aws_account", tier=TIER_REVIEW, brain=None,
                title=title, summary=acct, detail=_one_line(detail, 400),
                dispatch={"message": _prompt("Open Settings → Swarm AWS Account: " + title.lower() + "."),
                          "context": ctx}))
    except Exception as exc:
        logger.warning("attention: aws_account source failed: %s", exc)
    return items


# ── Aggregator ───────────────────────────────────────────────────────────────


def _collect_stale_todos() -> list[AttentionItem]:
    """Open ToDos nobody has touched for long (design 2026-10-31-continue-lookback-todo
    r3): they are no longer cancelled on a timer, so they are shown here, as one REVIEW
    item per kind: pending untouched for TODO_LIFECYCLE["stale_pending_days"], overdue
    untouched for ["stale_overdue_days"]. A click puts the list in a chat, where the
    person keeps, updates or cancels each. Read-only (the ToDo table is opened read
    only); fail-soft; a missing database is silent."""
    items: list[AttentionItem] = []
    try:
        import sqlite3
        from datetime import datetime, timedelta, timezone
        from schemas.todo import TODO_LIFECYCLE

        db_path = Path(get_app_data_dir()) / "data.db"
        if not db_path.exists():
            return []
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        try:
            for status, days in (("pending", TODO_LIFECYCLE["stale_pending_days"]),
                                 ("overdue", TODO_LIFECYCLE["stale_overdue_days"])):
                cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
                rows = conn.execute(
                    "SELECT id, title FROM todos WHERE status = ? AND review_state IS NULL "
                    "AND COALESCE(updated_at, created_at) < ? ORDER BY updated_at",
                    (status, cutoff)).fetchall()
                if not rows:
                    continue
                n = len(rows)
                what = "pending" if status == "pending" else "overdue"
                titles = "\n" + "\n".join(f"- {_one_line(t, 120)}" for _i, t in rows[:30])
                if n > 30:
                    titles += f"\n- … and {n - 30} more"
                items.append(AttentionItem(
                    id=f"todo:stale_{status}", source="todo", tier=TIER_REVIEW, brain=None,
                    title=f"{n} {what} ToDo{'s' if n != 1 else ''} untouched for {days}+ days",
                    summary=f"{n} in ToDo",
                    detail="Nothing closes them on a timer: keep, update or cancel each.",
                    dispatch={"message": _prompt(
                        f"Go through my {what} ToDos untouched for {days} days or more: for "
                        "each, tell me whether it still matters, then I decide to keep, "
                        "update or cancel it.",
                        [("ToDos", titles)],
                        [f"todo {i}" for i, _t in rows[:30]])}))
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("attention: stale ToDo source failed: %s", exc)
    return items


def collect(
    workspace_root: Path,
    *,
    brain: Optional[str] = None,
    pipeline_runs: Optional[list[dict]] = None,
) -> AttentionResult:
    """Aggregate all sources → normalized, tiered, brain-attributed items.

    Args:
        workspace_root: SwarmWS root (Projects/ lives under it).
        brain: if set, return ONLY items for that brain. Governance (brain=None)
               is EXCLUDED from a per-brain query — it is OS-level, not that
               brain's pending work (this is what fixes the brain-card badge:
               _pending_count passes brain=<name> and gets a truthful count that
               includes escalation but excludes OS-level governance).
        pipeline_runs: optional pre-loaded run dicts (lets a caller that already
               scanned runs avoid a second disk scan); None → load internally.

    Read-only. Never writes disk. Fail-soft per source.
    """
    items: list[AttentionItem] = []
    items += _collect_escalations(workspace_root)
    items += _collect_governance()
    items += _collect_paused_runs(pipeline_runs)
    items += _collect_jobs()
    items += _collect_job_digests(workspace_root)
    items += _collect_db_recovery()  # OS-level (brain=None), like governance
    items += _collect_ingestion()  # OS-level CDF anomalies; missing DB is silent
    items += _collect_local_roots()  # a repository a Brain started reading
    items += _collect_swarm_out()    # OS-level summary of what Swarm Out waits on
    items += _collect_stale_todos()  # open ToDos nobody touched for long
    items += _collect_aws_account()  # the SwarmAI account's sign-in, and the Hive count

    if brain is not None:
        # Per-brain query: only that brain's items. Governance (brain=None) is
        # OS-level and NEVER attributed to a single brain.
        items = [it for it in items if it.brain == brain]

    # Order: BLOCKING first (design §5 main axis), stable within tier.
    items.sort(key=lambda it: 0 if it.tier == TIER_BLOCKING else 1)

    counts = {
        TIER_BLOCKING: sum(1 for it in items if it.tier == TIER_BLOCKING),
        TIER_REVIEW: sum(1 for it in items if it.tier == TIER_REVIEW),
    }
    return AttentionResult(items=items, counts=counts)
