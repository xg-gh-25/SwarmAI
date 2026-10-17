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
  block per stage whose status became terminal (completed / skipped) between the
  two states, in the profile's stage order.

Block shape (camelCase: the frontend converts only tool_use_id / is_error)::

    {"type": "pipeline_stage", "runId", "stage", "status", "ordinal", "position",
     "total", "profile", "next", "nextOrdinal", "gate", "summary", "at"}

``ordinal`` is the circled number the pipeline's progress display uses (①–⑩: Gate 1
holds ④ and the adversarial gate ⑧, so build is ⑤ and deliver ⑨). ``summary`` is the
stage record's own summary — model-written text — so it is collapsed to one line,
capped at ``SUMMARY_MAX`` characters and rendered as plain text by the frontend.

These blocks are persisted like any assistant content but are NOT conversation:
context_injector treats them as non-conversational, and every other content reader
selects text/tool blocks by type, so a backend-observed row never re-enters a model
prompt as something the assistant said.

@exports parse_watched_run_id, read_stage_state, new_stage_blocks,
         STAGE_ORDINALS, SUMMARY_MAX, BLOCK_TYPE
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from .pipeline_profiles import get_profile_stages, is_relaxed_profile
from .project_registry import get_swarmws

logger = logging.getLogger(__name__)

BLOCK_TYPE = "pipeline_stage"

# The circled numbers of the pipeline progress display (INSTRUCTIONS.md "Complete
# Stage Reference"). ④ (Gate 1) and ⑧ (adversarial, run inside deliver) are gates;
# normally no stage record carries them, but older run.json files do record
# gate1 / adversarial / adversarial_review as stages, so those map to their gate
# slot. goal_cycle replaces build/review/test and takes build's slot.
STAGE_ORDINALS: dict[str, int] = {
    "evaluate": 1, "think": 2, "plan": 3, "gate1": 4, "build": 5, "review": 6,
    "test": 7, "adversarial": 8, "adversarial_review": 8, "deliver": 9,
    "reflect": 10, "goal_cycle": 5,
}

# Terminal statuses, normalized: "complete" is a legacy spelling found in real
# run.json records and is shown (and compared) as "completed".
_TERMINAL: dict[str, str] = {"completed": "completed", "complete": "completed",
                             "skipped": "skipped"}
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
            return {"profile": profile if isinstance(profile, str) else None,
                    "stages": stages}
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
        return {"name": "GATE 1", "verdict": verdict.strip()[:16]}
    return None


def _gate1_recorded(state: Optional[dict], stage: str) -> Optional[str]:
    rec = ((state or {}).get("stages") or {}).get(stage)
    v = rec.get("gate1_verdict") if isinstance(rec, dict) else None
    return v.strip() if isinstance(v, str) and v.strip() else None


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
    on a strict profile plan's ``next`` is ④ — the live line says GATE 1 while the
    skeptic runs, then the gate row hands over to BUILD."""
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

    strict = not is_relaxed_profile(profile)
    blocks: list[dict[str, Any]] = []
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
        if status not in ("completed", "skipped") or _status(before, name) == status:
            continue
        position = order.index(name) + 1 if name in order else None
        nxt = _next_after(name)
        if (name == "plan" and strict and nxt in ("build", "goal_cycle")
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
    return blocks
