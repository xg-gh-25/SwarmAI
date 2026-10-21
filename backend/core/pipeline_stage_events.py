"""Backend-observed pipeline stage rows for the chat stream.

Pipeline stage progress used to reach the chat only as text the model chose to
write (``## ✦ ⑤ BUILD``). That is agent discipline, and it failed: whole runs
wrote no stage line at all, so the user saw a folded run of tool calls and could
not tell which stage was running. The agent never skips one thing, though: every
stage transition is written to run.json by an ``artifact_cli.py`` call that names
the run. The streaming orchestrator watches those calls and, when one returns,
asks this module what changed.

The module is pure and fail-safe (it never raises):

- ``parse_watched_run_id(command)`` — the cheap pre-filter. A Bash command is
  watched when it invokes ``artifact_cli`` with a ``--run-id``. Any subcommand
  qualifies (run-update, publish, advance, stage-complete …), because detection keys
  on what run.json says afterwards, not on which subcommand ran. A ``$VAR`` run id
  is resolved only from an assignment in the SAME command (every Bash tool call is
  a fresh shell, so that is the only place its value can come from); otherwise the
  call is not watched — nothing is guessed.
- ``read_stage_state(run_id)`` — reads run.json (the authority) into
  ``{"profile", "stages": {stage: last record}}``.
- ``new_stage_blocks(run_id, before, after, at)`` — one ``pipeline_stage`` content
  block per stage that STARTED between the two states (``stage_timing.<stage>.
  started_at`` appeared: status ``in_progress``, ``at`` = that start) and one per
  stage whose status became terminal (completed / skipped), completions first, each
  group in the profile's stage order. The frontend opens a stage's row at its start
  block and updates that same row in place at its completion block, so a row owns
  the work from its start to the next row's start.

Block shape (camelCase: the frontend converts only tool_use_id / is_error)::

    {"type": "pipeline_stage", "runId", "stage", "status", "ordinal", "position",
     "total", "profile", "next", "nextOrdinal", "gate", "summary", "at"}

``ordinal`` is the stage's slot in the pipeline order (Gate 1 holds 4 and the
adversarial gate 8, so build is 5 and deliver 9); the chat no longer prints it as a
circled number, but the frontend still uses it. Gates have no stage record, so
each gate gets its own row from where its verdict is recorded: Gate 0 from the
evaluation artifact (after evaluate's row), Gate 1 from the build / goal_cycle
record, Gate 2 from the deliver (else goal_cycle) artifact (before deliver's row).
A gate that did not run has no row. ``summary`` is the
stage record's own summary — model-written text — so it is collapsed to one line,
capped at ``SUMMARY_MAX`` characters and rendered as plain text by the frontend.

These blocks are persisted like any assistant content but are NOT conversation:
context_injector treats them as non-conversational, and every other content reader
selects text/tool blocks by type, so a backend-observed row never re-enters a model
prompt as something the assistant said.

The run's plan as the chat shows it (a checklist of every planned stage and gate) is
also the backend's: ``checklist(profile)`` places the gates of
``pipeline_profiles.PROFILE_GATES`` next to the stages they guard, ``run_block``
carries it, and ``new_stage_blocks`` emits a refreshed ``pipeline_run`` block when the
run's profile is first set or changes (a run is created before EVALUATE picks it).

@exports parse_watched_run_id, read_stage_state, new_stage_blocks, current_stage,
         attach_stage_docs, summary_block, SUMMARY_BLOCK_TYPE,
         checklist, run_block, STAGE_ORDINALS, SUMMARY_MAX, BLOCK_TYPE
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from .pipeline_profiles import FINISHED_STATUSES, get_profile_stages, profile_gates
from .project_registry import get_swarmws

logger = logging.getLogger(__name__)

BLOCK_TYPE = "pipeline_stage"

# The circled numbers of the pipeline progress display (INSTRUCTIONS.md "Complete
# Stage Reference"). ④ (Gate 1) and ⑧ (adversarial, run inside deliver) are gates;
# normally no stage record carries them, but older run.json files do record
# gate1 / adversarial / adversarial_review as stages, so those map to their gate
# slot. goal_cycle replaces build/review/test and takes build's slot.
STAGE_ORDINALS: dict[str, int] = {
    "evaluate": 1, "gate0": 1, "think": 2, "plan": 3, "gate1": 4, "gate2": 8, "build": 5, "review": 6,
    "test": 7, "adversarial": 8, "adversarial_review": 8, "deliver": 9,
    "reflect": 10, "goal_cycle": 5,
}

# Terminal statuses, normalized: "complete" and "done" are legacy spellings found in
# real run.json records and are shown (and compared) as "completed"
# (pipeline_profiles.FINISHED_STATUSES, the one set every reader shares).
_TERMINAL: dict[str, str] = {**{s: "completed" for s in FINISHED_STATUSES}, "skipped": "skipped"}
SUMMARY_MAX = 160

_RUN_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
# --run-id <id> | --run-id=<id> | --run-id '<id>' | --run-id "<id>"; the value may
# also sit inside a quoted flag bundle (P="--project X --run-id run_x").
_RUN_ID_ARG_RE = re.compile(r"--run-id(?:=|\s+)(['\"]?)([^\s'\";&|)]+)\1(?=[\s'\";&|)]|$)")
_VAR_REF_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def parse_watched_run_id(command: object) -> Optional[str]:
    """Return the literal run id of an ``artifact_cli`` invocation, else None.

    A pre-filter only: it decides whether a call is worth one run.json read when it
    returns. It never proves anything changed — ``new_stage_blocks`` decides that
    from run.json itself.
    """
    if not isinstance(command, str) or "artifact_cli" not in command:
        return None
    # The --run-id belonging to the LAST artifact_cli invocation wins (an id quoted
    # earlier — in a heredoc, an echo — is not the call that runs); one placed before
    # it (P="--run-id run_x"; … artifact_cli … $P) is the fallback.
    last_cli = command.rfind("artifact_cli")
    matches = list(_RUN_ID_ARG_RE.finditer(command))
    ordered = [m for m in matches if m.start() > last_cli] + [
        m for m in reversed(matches) if m.start() < last_cli]
    for m in ordered:
        value = m.group(2)
        ref = _VAR_REF_RE.fullmatch(value)
        if ref:
            # the value Bash uses is the LAST assignment before the reference
            assigns = list(re.finditer(
                r"(?:^|[\s;&(])(?:export\s+)?" + re.escape(ref.group(1))
                + r"=(['\"]?)([^\s'\";&|)]+)\1(?=[\s;&|)]|$)",
                command[: m.start()],
            ))
            value = assigns[-1].group(2) if assigns else ""
        if _RUN_ID_RE.fullmatch(value):
            return value
    return None


# ``run-create`` names no run in its command — artifact_cli mints the id and prints
# it (``{"pipeline_id": "run_…", …}``). Its call is watched with this sentinel and
# the id is read from the call's own output, so the run's alignment shows the
# moment the run exists, not after EVALUATE's first ``--run-id`` call.
RUN_CREATE_WATCH = "<run-create>"
_RUN_CREATE_RE = re.compile(r"artifact_cli(?:\.py)?['\"]?\s+(?:[^;&|\n]*\s)?run-create(?=[\s'\";&|)]|$)")
_PIPELINE_ID_RE = re.compile(r'"pipeline_id"\s*:\s*"([^"]*)"')


def is_run_create(command: object) -> bool:
    """True when the LAST artifact_cli invocation in ``command`` is ``run-create``."""
    if not isinstance(command, str) or "artifact_cli" not in command:
        return False
    return bool(_RUN_CREATE_RE.search(command[command.rfind("artifact_cli"):]))


def _result_text(content: object) -> str:
    """A tool_result's content as text (a string, or a list of text blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return ""


def run_id_from_create_output(content: object) -> Optional[str]:
    """The run id a ``run-create`` call printed, else None. The LAST ``pipeline_id``
    wins (the CLI prints its result line last). A pre-filter only: the id still has
    to name a real run.json before anything is shown (read_stage_state)."""
    ids = _PIPELINE_ID_RE.findall(_result_text(content))
    return ids[-1] if ids and _RUN_ID_RE.fullmatch(ids[-1]) else None


def resolve_watch(watch: Optional[str], content: object) -> Optional[str]:
    """The run id a watched call refers to: its ``--run-id``, or for ``run-create``
    the id it printed."""
    if watch == RUN_CREATE_WATCH:
        return run_id_from_create_output(content)
    return watch


def read_stage_state(run_id: object) -> Optional[dict]:
    """Read run.json for ``run_id`` into ``{"profile": str|None, "stages": {...}}``.

    ``stages`` maps a stage name to its LAST record (artifact_cli replaces the last
    record for a stage, and that is the one the validator reads). Returns None when
    the id is malformed or the file is missing / unreadable / not a JSON object.
    Same lookup as ``ui_actions.read_run_status``: the run directory name is the id,
    under any project.
    """
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        return None
    try:
        ws = Path(str(get_swarmws())).resolve()
        for hit in (ws / "Projects").glob("*/.artifacts/runs/" + run_id + "/run.json"):
            data = json.loads(hit.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            stages: dict[str, dict] = {}
            for rec in data.get("stages") or []:
                if isinstance(rec, dict) and isinstance(rec.get("stage"), str):
                    stages[rec["stage"]] = rec
            profile = data.get("profile")
            req = data.get("requirement")
            timing = data.get("stage_timing") if isinstance(data.get("stage_timing"), dict) else {}
            commits = data.get("commits") if isinstance(data.get("commits"), list) else []
            return {"profile": profile if isinstance(profile, str) else None,
                    "stages": stages,
                    "timing": {k: v for k, v in timing.items()
                               if isinstance(k, str) and isinstance(v, dict)},
                    "status": data.get("status") if isinstance(data.get("status"), str) else None,
                    "requirement": req if isinstance(req, str) else None,
                    # An isolated run's change lands in main at deliver stage-complete,
                    # AFTER its deliver record is written (and the landing can still be
                    # refused): the attention card waits for `landed` (attention_due).
                    "isolated": data.get("worktree_mode") == "isolated",
                    "landed": any(isinstance(c, dict) and c.get("landed") for c in commits),
                    "landed_shas": [str(c.get("sha")) for c in commits if isinstance(c, dict)
                                    and c.get("landed") and isinstance(c.get("sha"), str)],
                    "run_dir": str(hit.parent)}
        return None
    except Exception:  # noqa: BLE001 — a display aid must never break the stream
        logger.debug("read_stage_state: unreadable run.json for %r", run_id, exc_info=True)
        return None


def _status(state: Optional[dict], stage: str) -> Optional[str]:
    """The stage's status in ``state``, normalized via _TERMINAL when terminal."""
    if not state:
        return None
    rec = (state.get("stages") or {}).get(stage)
    raw = rec.get("status") if isinstance(rec, dict) else None
    return _TERMINAL.get(raw, raw) if isinstance(raw, str) else raw


def _summary(rec: dict) -> Optional[str]:
    raw = rec.get("summary")
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.split())
    if not text:
        return None
    return text if len(text) <= SUMMARY_MAX else text[: SUMMARY_MAX - 1] + "…"


def _gate(rec: dict) -> Optional[dict]:
    verdict = rec.get("gate1_verdict")
    if isinstance(verdict, str) and verdict.strip():
        return {"name": "GATE 1", "verdict": verdict.strip()[:40]}
    return None


def _gate1_recorded(state: Optional[dict], stage: str) -> Optional[str]:
    rec = ((state or {}).get("stages") or {}).get(stage)
    v = rec.get("gate1_verdict") if isinstance(rec, dict) else None
    return v.strip() if isinstance(v, str) and v.strip() else None


_LEGACY_GATE2_STAGES = ("adversarial", "adversarial_review")


def _gate0_row_verdict(state: dict, rec: dict) -> Optional[str]:
    """Gate 0's verdict: the evaluation artifact's ``understanding.skeptic_verdict``,
    else the legacy keys on the evaluate record. None when Gate 0 did not run."""
    art = _artifact(state.get("run_dir"), rec)
    und = art.get("understanding") if isinstance(art.get("understanding"), dict) else {}
    for verdict in (und.get("skeptic_verdict"), _gate0_verdict(rec)):
        if isinstance(verdict, str) and verdict.strip():
            return verdict.strip()[:40]
    return None


def _gate2_row_verdict(state: dict) -> Optional[str]:
    """Gate 2's verdict as "<fixed>/<total> fixed" ("PASS" with no findings), from the
    deliver artifact's ``adversarial_review``, else the goal_cycle artifact's. None
    when neither carries one (the gate did not run) — or when a legacy adversarial
    stage record exists, because that record already has its own row."""
    stages = state.get("stages") or {}
    if any(name in stages for name in _LEGACY_GATE2_STAGES):
        return None
    for name in ("deliver", "goal_cycle"):
        adv = _artifact(state.get("run_dir"), stages.get(name)).get("adversarial_review")
        if not isinstance(adv, dict):
            continue
        total, fixed = adv.get("findings_total"), adv.get("findings_fixed")
        if isinstance(total, int) and isinstance(fixed, int):
            return "PASS" if total == 0 else f"{fixed}/{total} fixed"
        return "done"
    return None


def _timing(state: Optional[dict], stage: str, key: str) -> Optional[str]:
    t = ((state or {}).get("timing") or {}).get(stage)
    v = t.get(key) if isinstance(t, dict) else None
    return v if isinstance(v, str) and v else None


def _started_at(state: Optional[dict], stage: str) -> Optional[str]:
    return _timing(state, stage, "started_at")


def _window_open(state: Optional[dict], stage: str) -> bool:
    """The stage has a started window with no end. artifact_cli's stage-start re-opens
    a COMPLETED stage by writing a new started_at and dropping completed_at (keeping
    first_started_at), while the stage record stays "completed" — so a terminal record
    is open again when its window says so. A terminal record whose timing never had an
    end (older runs) is not open."""
    if not _started_at(state, stage) or _timing(state, stage, "completed_at"):
        return False
    if _status(state, stage) in ("completed", "skipped"):
        return bool(_timing(state, stage, "first_started_at"))
    return True


def current_stage(state: Optional[dict]) -> Optional[str]:
    """The stage now in progress: the latest-started one whose window is open
    (``_window_open``: started, not ended — a re-opened completed stage counts). None
    when no stage is running."""
    best: Optional[tuple[str, str]] = None
    for name in ((state or {}).get("timing") or {}):
        start = _started_at(state, name)
        if not start or not _window_open(state, name):
            continue
        # artifact_cli writes every timestamp with one UTC format, so ISO strings
        # order as times.
        if best is None or start > best[0]:
            best = (start, name)
    return best[1] if best else None


def new_stage_blocks(
    run_id: str, before: Optional[dict], after: Optional[dict], at: str,
) -> list[dict[str, Any]]:
    """Blocks for every stage that is terminal in ``after`` with a different status
    in ``before`` (a stage already completed before the call emits nothing, so a
    re-recorded stage never repeats). Ordered by the profile's stage list; stages
    outside the profile follow in record order, without a position.

    Gate 1 (④) runs between plan and build but has no stage record of its own: its
    verdict is written onto the build (or goal_cycle) record, usually before that
    stage finishes. So a newly recorded ``gate1_verdict`` becomes its own ④ row, and
    on a profile that runs Gate 1 (``PROFILE_GATES``) plan's ``next`` is ④ — the live line says GATE 1 while the
    skeptic runs, then the gate row hands over to BUILD. Gate 0 and Gate 2 rows come
    with the stage that records them (see ``_gate0_row_verdict`` /
    ``_gate2_row_verdict``), so they appear exactly once, when that stage finishes."""
    if not after:
        return []
    stages_after: dict[str, dict] = after.get("stages") or {}
    profile = after.get("profile")
    order = list(get_profile_stages(profile))
    names = [s for s in order if s in stages_after]
    names += [s for s in stages_after if s not in order]

    def _next_after(name: str) -> Optional[str]:
        # The next stage still to run: skip stages already finished (a resumed run can
        # finish review after test was recorded), so "in progress" never names a done one.
        if name not in order:
            return None
        i = order.index(name) + 1
        return next((s for s in order[i:]
                     if _status(after, s) not in ("completed", "skipped")), None)

    runs_gate1 = "gate1" in profile_gates(profile)
    blocks: list[dict[str, Any]] = []
    # The run's plan changed (EVALUATE set the profile, or a replan changed it): the
    # turn's pipeline_run block was written without it, so write it again.
    if after.get("profile") and before is not None and before.get("profile") != after.get("profile"):
        rb = run_block(run_id, after, at)
        if rb:
            blocks.append(rb)

    def _gate_row(stage: str, name: str, verdict: str, nxt: Optional[str]) -> dict:
        return {"type": BLOCK_TYPE, "runId": run_id, "stage": stage,
                "status": "completed", "ordinal": STAGE_ORDINALS.get(stage),
                "position": None, "total": len(order), "profile": profile,
                "next": nxt, "nextOrdinal": STAGE_ORDINALS.get(nxt) if nxt else None,
                "gate": {"name": name, "verdict": verdict}, "summary": None, "at": at}
    for name in names:
        rec = stages_after[name]
        verdict = _gate1_recorded(after, name)
        if verdict and verdict != _gate1_recorded(before, name):
            done = _status(after, name) in ("completed", "skipped")
            gate_next = _next_after(name) if done else name
            blocks.append({
                "type": BLOCK_TYPE, "runId": run_id, "stage": "gate1",
                "status": "completed", "ordinal": STAGE_ORDINALS["gate1"],
                "position": None, "total": len(order), "profile": profile,
                "next": gate_next,
                "nextOrdinal": STAGE_ORDINALS.get(gate_next) if gate_next else None,
                "gate": _gate(rec), "summary": None, "at": at,
            })
        status = _status(after, name)
        reclosed = (status in ("completed", "skipped") and _window_open(before, name)
                    and not _window_open(after, name))
        if status not in ("completed", "skipped") or (_status(before, name) == status and not reclosed):
            continue
        position = order.index(name) + 1 if name in order else None
        nxt = _next_after(name)
        first_close = _status(before, name) != status  # a re-closed stage: no second gate row
        if name == "deliver" and status == "completed" and first_close:
            gate2 = _gate2_row_verdict(after)
            if gate2:
                blocks.append(_gate_row("gate2", "GATE 2", gate2, "deliver"))
        if (name == "plan" and runs_gate1 and nxt in ("build", "goal_cycle")
                and not _gate1_recorded(after, nxt)):
            nxt = "gate1"
        blocks.append({
            "type": BLOCK_TYPE,
            "runId": run_id,
            "stage": name,
            "status": status,
            "ordinal": STAGE_ORDINALS.get(name),
            "position": position,
            "total": len(order),
            "profile": profile,
            "next": nxt,
            "nextOrdinal": STAGE_ORDINALS.get(nxt) if nxt else None,
            # the Gate-1 verdict is shown on its own ④ row, not repeated here
            "gate": None,
            "summary": _summary(rec),
            "at": at,
        })
        if name == "evaluate" and status == "completed" and first_close:
            gate0 = _gate0_row_verdict(after, rec)
            if gate0:
                blocks.append(_gate_row("gate0", "GATE 0", gate0, nxt))
    # Stages that started in this call (after every completion: "complete X, start Y"
    # reads X done, then Y running). A stage that also finished in this call has its
    # completed row already; a start that was known before is not repeated.
    timing_order = [s for s in order if s in (after.get("timing") or {})]
    timing_order += [s for s in (after.get("timing") or {}) if s not in order]
    for name in timing_order:
        start = _started_at(after, name)
        if not start or start == _started_at(before, name):
            continue
        if not _window_open(after, name):
            continue  # started and finished in this call: its completed row says it
        blocks.append({
            "type": BLOCK_TYPE, "runId": run_id, "stage": name, "status": "in_progress",
            "ordinal": STAGE_ORDINALS.get(name),
            "position": order.index(name) + 1 if name in order else None,
            "total": len(order), "profile": profile, "next": None, "nextOrdinal": None,
            "gate": None, "summary": None, "at": start,
        })
    return blocks


# ── Alignment and needs-you blocks (chat display v7.1) ───────────────────────
#
# Two more backend-written blocks, both persisted and both non-conversational
# (context_injector _TOOL_ONLY_TYPES):
#   pipeline_run       — once per run per TURN, the first time a call names it
#                        (normally ``run-create`` itself, via RUN_CREATE_WATCH),
#                        and again when the run's profile is set or changes
#                        (new_stage_blocks): the run's requirement, its planned
#                        stages, its checklist (stages + gates) and the stage in
#                        progress (``current``).
#   pipeline_attention — with the DELIVER row: only what needs a human judgement,
#                        from STRUCTURED fields (never the model's prose): questions
#                        the pipeline answered itself, assumed requirements, gate
#                        verdicts that are not a pass, and a push_ready of False.

RUN_BLOCK_TYPE = "pipeline_run"
ATTENTION_BLOCK_TYPE = "pipeline_attention"
REQUIREMENT_MAX = 400
ITEM_TEXT_MAX = 200
MAX_ATTENTION_ITEMS = 8

# A verdict that needs no one: PASS / SUPPORTED / PROCEED / GO / SKIPPED, or a
# BLOCK that was resolved. Verdicts are free text in real run.json files, so the
# match is on the leading word.
_OK_VERDICT_RE = re.compile(r"^\s*(PASS|SUPPORTED|PROCEED|GO|SKIP)", re.I)
_OPEN_REQ_STATUSES = {"assumed", "needs-clarification"}


def _clip(value: object, limit: int = ITEM_TEXT_MAX) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def checklist(profile: Optional[str]) -> list[dict[str, Any]]:
    """The run's plan as the chat lists it: every stage of the profile and every gate
    it runs, in order — gate0 after evaluate, gate1 before build / goal_cycle, gate2
    before deliver (where their rows land, see new_stage_blocks). [] while the run has
    no profile yet (EVALUATE has not picked it)."""
    if not profile:
        return []
    gates = profile_gates(profile)
    out: list[dict[str, Any]] = []
    for stage in get_profile_stages(profile):
        if stage in ("build", "goal_cycle") and "gate1" in gates:
            out.append({"stage": "gate1", "gate": True})
        if stage == "deliver" and "gate2" in gates:
            out.append({"stage": "gate2", "gate": True})
        out.append({"stage": stage, "gate": False})
        if stage == "evaluate" and "gate0" in gates:
            out.append({"stage": "gate0", "gate": True})
    return out


def run_block(run_id: str, state: Optional[dict], at: str) -> Optional[dict[str, Any]]:
    """The run's alignment block, or None when run.json says nothing to show."""
    if not state:
        return None
    requirement = _clip(state.get("requirement"), REQUIREMENT_MAX)
    if not requirement:
        return None
    profile = state.get("profile")
    stages = list(get_profile_stages(profile)) if profile else []
    # `current`: the stage in progress when the block is written. A resumed run is
    # announced again in a new session mid-run; without this the chat would show its
    # FIRST stage as running.
    return {"type": RUN_BLOCK_TYPE, "runId": run_id, "profile": profile,
            "stages": stages, "checklist": checklist(profile), "requirement": requirement,
            "current": current_stage(state), "at": at}


def _artifact(run_dir: Optional[str], record: Optional[dict]) -> dict:
    """A stage record's own artifact (``<type>-<date>-<artifact_id>.json`` in the
    run directory), unwrapped from its ``{"data": …}`` envelope; {} when absent."""
    art_id = (record or {}).get("artifact_id")
    if not run_dir or not isinstance(art_id, str) or not _RUN_ID_RE.fullmatch(art_id):
        return {}
    try:
        for hit in Path(run_dir).glob(f"*-{art_id}.json"):
            data = json.loads(hit.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data["data"] if isinstance(data.get("data"), dict) else data
    except Exception:  # noqa: BLE001 — a display aid must never break the stream
        logger.debug("pipeline attention: unreadable artifact %r", art_id, exc_info=True)
    return {}


def _gate0_verdict(evaluate: dict) -> object:
    """Gate 0's verdict — the key varies across real run.json files."""
    if evaluate.get("gate") == "gate0" and evaluate.get("gate_verdict"):
        return evaluate.get("gate_verdict")
    return evaluate.get("gate0_verdict") or evaluate.get("gate0") or evaluate.get("gate_result")


def _gate1_verdict(stages: dict) -> object:
    """Gate 1's verdict: on the build (or goal_cycle; older runs: plan) record."""
    for name in ("build", "goal_cycle", "plan"):
        rec = stages.get(name) or {}
        legacy = rec.get("gate1")
        verdict = rec.get("gate1_verdict") or (legacy.get("verdict") if isinstance(legacy, dict) else None)
        if verdict:
            return verdict
    return None


def _gate_items(stages: dict, deliver_art: dict) -> list[dict]:
    items: list[dict] = []
    ev = stages.get("evaluate") or {}
    for name, verdict, note in (
        ("GATE 0", _gate0_verdict(ev), ev.get("gate0_note") or ev.get("gate_note")),
        ("GATE 1", _gate1_verdict(stages), None),
    ):
        v = _clip(verdict, 40)
        if v and not _OK_VERDICT_RE.match(v) and "RESOLVED" not in v.upper():
            items.append({"kind": "gate", "name": name, "verdict": v, "detail": _clip(note)})
    # Gate 2 is the deliver artifact's adversarial review: open findings need a look.
    adv = deliver_art.get("adversarial_review") if isinstance(deliver_art.get("adversarial_review"), dict) else {}
    total, fixed = adv.get("findings_total"), adv.get("findings_fixed")
    if isinstance(total, int) and isinstance(fixed, int) and total > fixed:
        items.append({"kind": "gate", "name": "GATE 2", "verdict": f"{total - fixed}/{total} open", "detail": None})
    return items


def _clar_answer(c: dict) -> Optional[str]:
    ans = c.get("answer")
    if isinstance(ans, str):
        return _clip(re.sub(r"^[A-Z]\.\s+", "", ans.strip()))
    return None


def attention_due(before: Optional[dict], after: Optional[dict], deliver_row_ended: bool) -> bool:
    """When the attention card is emitted: with the DELIVER row for a shared run; for an
    isolated run only once its change has LANDED in main (``landed`` turns true). Its
    deliver record is written before the landing, and the landing can still be refused
    (changed since review, main moved) — a card then would read as the end of a run
    that is still re-reviewing."""
    if not after:
        return False
    if after.get("isolated"):
        return bool(after.get("landed")) and not (before or {}).get("landed")
    return deliver_row_ended


# push_ready reasons are written for the model (validator hints, pipeline mechanics).
# The card says what is actually missing, in one plain sentence each; the raw text
# stays in REPORT §10. Pipeline mechanics ("DEGRADED … not a stop", "landing is
# decided by the test evidence", "degraded after N refused attempts") say nothing new.
_REASON_NOISE_RE = re.compile(
    r"completed DEGRADED:|not a stop|landing is decided by the test evidence"
    r"|completed degraded after .* refused attempts", re.I)
#: (pattern, code, sentence). The code lets the chat say it in the UI language
#: (chat.turn.attention.reason.<code>); the sentence is the English fallback.
_PLAIN_REASONS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"design\.states is empty", re.I), "design_states",
     "The design does not say which state the change adds or touches"),
    (re.compile(r"smoke_tests=0|SMOKE step skipped", re.I), "no_smoke",
     "No smoke test ran the changed code with real objects"),
    (re.compile(r"continued test\(s\) passed without the earlier tests", re.I), "continued_tests",
     "Some tests passed only in a continued test call, never in one run with the rest of their file"),
    (re.compile(r"gate\(s\) passed on a recorded reason, not verified", re.I), "gate_on_reason",
     "A review step was accepted on a written reason, not on a checked result"),
    (re.compile(r"Gate-2 .*(?:did not converge|not converged)|review_not_converged", re.I), "review_not_converged",
     "Review did not converge in 3 rounds; the open findings are listed"),
    (re.compile(r"\btests?: \d+ failed|failed test", re.I), "tests_failed", "Tests failed"),
    (re.compile(r"unresolved (?:HIGH|MED|CRITICAL)|unresolved blocking finding|^gate2: \S+ open:", re.I),
     "finding_open", "A review finding is still open"),
    (re.compile(r"test evidence predates later edits|tests ran on another tree", re.I),
     "tests_not_final", "The tests did not run on the final code"),
    (re.compile(r"tests: only \S+ evidence|only tdd", re.I), "tests_scope_partial",
     "The tests did not cover everything the change touches"),
    # a finding's record lacks a field (Fix by class / one record per finding): paperwork,
    # not a missing review — matched before the review pattern below, which also says
    # "adversarial_review"
    (re.compile(r"^Fix by class:|findings\[\] must carry", re.I), "records_incomplete",
     "Some delivery record fields are empty"),
    # a review that did not run (by decision or not): what the push decision needs to know
    (re.compile(r"no Gate-2 review|Gate-2 expert\(s\) have not reviewed|adversarial_review|"
                r"meta_review|requires sub-agent to be actually spawned|Red Team required|"
                r"differs from the tree recorded when the Gate-2", re.I), "not_reviewed",
     "Not reviewed by the Gate-2 experts"),
    (re.compile(r"no exit contract", re.I), "no_exit_contract", "No exit contract (the plan was skipped)"),
    (re.compile(r"no runner (?:evidence|report)", re.I), "no_test_run", "No test run was recorded for the code change"),
    (re.compile(r"no test passed", re.I), "no_test_selected", "No test was selected for the change"),
    (re.compile(r"not tested \(the test runner", re.I), "repo_not_tested",
     "A changed repo is not covered by the test runner"),
    # the rest of the delivery record's paperwork: one line, the details are in REPORT §10
    (re.compile(r"Missing required field|completion_audit|ac_verification|Schema note|"
                r"Design spec:|plan\.design|Depth:", re.I), "records_incomplete",
     "Some delivery record fields are empty"),
]
_STAGE_PREFIX_RE = re.compile(r"^(?:unverified:\s*)?(?:(evaluate|think|plan|build|review|test|deliver|reflect|goal_cycle):\s*)?", re.I)


def plain_reason_items(raw: object, limit: int = 4) -> list[tuple[Optional[str], str]]:
    """``push_ready_reason`` ("; "-joined, model-facing) as at most *limit* plain,
    de-duplicated ``(code, sentence)`` pairs for the user: one per CAUSE, so the same
    validator error repeated by the deliver record, the commit and the degraded
    stage says it once. An unrecognised reason keeps its first clause (before ``—``
    / ``(``), prefixed with its stage, and has no code."""
    out: list[tuple[Optional[str], str]] = []
    for part in str(raw or "").split("; "):
        part = " ".join(part.split())
        if not part or _REASON_NOISE_RE.search(part):
            continue
        m = _STAGE_PREFIX_RE.match(part)
        body = re.sub(r"^(?:\w+ )?completed with \d+ open gate error\(s\):\s*", "", part[m.end():], flags=re.I)
        hit = next(((code, plain) for rx, code, plain in _PLAIN_REASONS if rx.search(body)), None)
        if hit is None:
            stage = (m.group(1) or "").capitalize()
            rest = re.split(r"\s+—\s+|\s+\(", body, maxsplit=1)[0].strip(" .:;")
            if not rest:
                continue
            hit = (None, _clip(f"{stage}: {rest}" if stage else rest, 140) or "")
        if hit[1] and all(hit[1] != t for _c, t in out):
            out.append(hit)
    return out[:limit]


def plain_reasons(raw: object, limit: int = 4) -> list[str]:
    """The sentences of :func:`plain_reason_items`."""
    return [t for _c, t in plain_reason_items(raw, limit)]


def attention_block(run_id: str, state: Optional[dict], at: str) -> Optional[dict[str, Any]]:
    """What in this run needs a human judgement, from structured fields only; None
    when nothing does. Fail-safe: an unreadable artifact just contributes nothing."""
    if not state:
        return None
    stages: dict = state.get("stages") or {}
    run_dir = state.get("run_dir")
    evaluation = _artifact(run_dir, stages.get("evaluate"))
    deliver_art = _artifact(run_dir, stages.get("deliver"))
    items: list[dict] = []
    for c in evaluation.get("clarifications") or []:
        if isinstance(c, dict) and str(c.get("answered_by") or "").strip() == "self":
            items.append({"kind": "self_answered", "id": _clip(c.get("qid"), 20),
                          "question": _clip(c.get("question")), "answer": _clar_answer(c),
                          "reason": _clip(c.get("resolution"))})
    for r in evaluation.get("requirements") or []:
        if not isinstance(r, dict):
            continue
        source = r.get("source") if isinstance(r.get("source"), dict) else {}
        if source.get("kind") == "assumption" or str(r.get("status") or "").strip() in _OPEN_REQ_STATUSES:
            items.append({"kind": "assumption", "id": _clip(r.get("id"), 20), "statement": _clip(r.get("statement"))})
    items += _gate_items(stages, deliver_art)
    deliver = stages.get("deliver") or {}
    if deliver.get("push_ready") is False:
        pairs = plain_reason_items(deliver.get("push_ready_reason"))
        items.append({"kind": "not_push_ready", "reasons": [t for _c, t in pairs],
                      "reasonCodes": [c for c, _t in pairs], "detail": "Details: REPORT §10"})
    if not items:
        return None
    return {"type": ATTENTION_BLOCK_TYPE, "runId": run_id,
            "items": items[:MAX_ATTENTION_ITEMS], "more": max(0, len(items) - MAX_ATTENTION_ITEMS), "at": at}


# ── Run documents (core/run_docs.py): per-stage products + the completion summary ──

SUMMARY_BLOCK_TYPE = "pipeline_summary"
#: run.json statuses that end a run: each gets the summary card, however it ended.
#: Same set as artifact_cli._TERMINAL_STATUSES ('complete' = the old 'completed').
RUN_END_STATUSES = ("completed", "complete", "abandoned", "failed", "cancelled")
#: the documents a stage row carries (DELIVER also writes FIXING_SPEC and REPORT).
_ROW_DOCS = {"deliver": ("REVIEW.md", "FIXING_SPEC.md", "REPORT.md")}


def _doc_ref(run_dir: Optional[str], name: str) -> Optional[dict[str, str]]:
    """``{name, path, absolutePath}`` for a run document on disk (``path`` is
    workspace-relative, the way the Canvas lists it); None when absent."""
    if not run_dir:
        return None
    try:
        p = Path(run_dir) / name
        if not p.is_file():
            return None
        ws = Path(str(get_swarmws())).resolve()
        try:
            rel = str(p.resolve().relative_to(ws))
        except ValueError:
            rel = str(p)
        from .run_docs import doc_excerpt
        ref = {"name": name, "path": rel, "absolutePath": str(p.resolve())}
        ex = doc_excerpt(p)
        if ex:
            ref["excerpt"] = ex
        return ref
    except Exception:  # noqa: BLE001 — a display aid must never break the stream
        logger.debug("_doc_ref: suppressed exception on a fail-safe path", exc_info=True)
        return None


def attach_stage_docs(blocks: list[dict], state: Optional[dict]) -> list[dict[str, str]]:
    """Give each COMPLETED stage row the run documents its stage wrote (REQUIREMENTS /
    DESIGN_SPEC; DELIVER: REVIEW, FIXING_SPEC, REPORT) as ``docs`` refs with an excerpt
    (``doc`` keeps the first, for older readers), and return them all: the orchestrator
    surfaces each as a knowledge file (Canvas). Mutates ``blocks`` in place."""
    from .run_docs import doc_for_stage
    out: list[dict[str, str]] = []
    run_dir = (state or {}).get("run_dir")
    for b in blocks:
        if b.get("type") != BLOCK_TYPE or b.get("status") != "completed":
            continue
        stage = str(b.get("stage") or "")
        names = _ROW_DOCS.get(stage) or tuple(n for n in (doc_for_stage(stage),) if n)
        refs = [r for r in (_doc_ref(run_dir, n) for n in names) if r]
        if refs:
            b["docs"] = refs
            b["doc"] = refs[0]
            out.extend(refs)
    return out


def summary_block(run_id: str, state: Optional[dict], at: str) -> Optional[dict[str, Any]]:
    """The run's execution summary for the chat, written once when run.json reaches an
    end status (RUN_END_STATUSES, a direct run included): how it ended, whether it is
    ready to push, and its documents (each with a ref to open, or why it does not
    exist). Structured fields only."""
    if not state or state.get("status") not in RUN_END_STATUSES:
        return None
    from .run_docs import doc_status
    run_dir = state.get("run_dir")
    stages: dict = state.get("stages") or {}
    run_state = {"profile": state.get("profile"),
                 "stages": [dict(rec) for rec in stages.values() if isinstance(rec, dict)]}
    deliver = stages.get("deliver") or {}
    deliver_art = _artifact(run_dir, deliver)
    adv = deliver_art.get("adversarial_review") if isinstance(deliver_art.get("adversarial_review"), dict) else {}
    findings = [f for f in adv.get("findings") or [] if isinstance(f, dict)]
    experts = sorted({str(f.get("specialist")).strip().lower() for f in findings
                      if isinstance(f.get("specialist"), str) and f["specialist"].strip()})
    docs: list[dict[str, Any]] = []
    for d in doc_status(run_dir, state.get("profile"), run_state):
        ref = _doc_ref(run_dir, d["name"]) if d.get("exists") else None
        entry: dict[str, Any] = {"name": d["name"], "purpose": d.get("purpose"),
                                 "path": ref["path"] if ref else None,
                                 "absolutePath": ref["absolutePath"] if ref else None,
                                 "required": bool(d.get("required")),
                                 "reason": _clip(d.get("reason"), 120)}
        if d["name"] == "REVIEW.md" and findings:
            entry["stat"] = {"experts": len(experts), "findings": len(findings),
                             "settled": sum(1 for f in findings if f.get("resolved"))}
        docs.append(entry)
    push = deliver.get("push_ready")
    outcome = deliver_art.get("outcome") if isinstance(deliver_art.get("outcome"), dict) else {}
    before, now = _clip(outcome.get("problem_before"), 300), _clip(outcome.get("result_now"), 300)
    landed = state.get("landed_shas") or []
    return {"type": SUMMARY_BLOCK_TYPE, "runId": run_id, "profile": state.get("profile"),
            # the verdict line and its context (the attention card says what to check)
            "intent": _clip(state.get("requirement"), 200),
            "solved": {"before": before, "now": now} if before and now else None,
            "landedSha": str(landed[-1])[:12] if landed else None,
            "status": "completed" if state.get("status") == "complete" else state.get("status"),
            "pushReady": push if isinstance(push, bool) else None,
            "pushReason": _clip(deliver.get("push_ready_reason"), 200),
            # the same plain causes as the attention card, for a turn that has none
            "pushReasons": [{"code": c, "text": t} for c, t in
                            plain_reason_items(deliver.get("push_ready_reason"))] if push is False else [],
            "stagesDone": sum(1 for r in stages.values() if isinstance(r, dict)
                              and _TERMINAL.get(str(r.get("status") or "")) == "completed"),
            "docs": docs, "at": at}
