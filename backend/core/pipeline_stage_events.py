"""Backend-observed Pipeline v1 stage rows for the chat stream.

The streaming orchestrator watches the Bash calls that name a v1 run (a pipeline CLI
call with ``--run-id``; ``create`` by its printed id)
and, when one returns, asks this module what changed in the run's projection
(``run.json``, written by ``RunEngine.write_projection`` from the signed events). Only
a run with a protected v1 contract is read (retirement R3: no old-pipeline shapes).

The module is fail-safe (it never raises):

- ``parse_watched_run_id(command)`` — the cheap pre-filter: the literal ``--run-id`` of
  the last pipeline CLI call (a ``$VAR`` only from an assignment in the same command).
- ``read_stage_state(run_id)`` — the projection as ``{"profile", "stages": {stage:
  {"stage", "status"}}, "status", "current_stage", ...}``.
- ``new_stage_blocks(run_id, before, after, at)`` — one ``pipeline_stage`` block per
  stage that closed (completed / degraded) and per stage that opened between the two
  states, completions first, in the profile's order.
- ``run_block`` / ``checklist`` — the run's alignment (requirement, planned stages).
- ``attention_due`` / ``attention_block`` — the needs-you card with the DELIVER row.
- ``attach_stage_docs`` / ``summary_block`` — each closed stage's documents, and the
  run's execution summary once it ends.

Block shape (camelCase)::

    {"type": "pipeline_stage", "runId", "stage", "status", "ordinal", "position",
     "total", "profile", "next", "nextOrdinal", "gate", "summary", "at"}

These blocks are persisted like assistant content but are NOT conversation
(context_injector treats them as non-conversational).

@exports parse_watched_run_id, is_run_create, resolve_watch, read_stage_state,
         new_stage_blocks, current_stage, run_block, checklist, attention_due,
         attention_block, attach_stage_docs, summary_block, doc_excerpt,
         BLOCK_TYPE, RUN_BLOCK_TYPE, ATTENTION_BLOCK_TYPE, SUMMARY_BLOCK_TYPE,
         RUN_END_STATUSES, RUN_CREATE_WATCH, STAGE_ORDINALS, SUMMARY_MAX
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from .project_registry import get_swarmws
from .run_protocol.lifecycle import PROFILE_STAGES

logger = logging.getLogger(__name__)

BLOCK_TYPE = "pipeline_stage"
RUN_BLOCK_TYPE = "pipeline_run"
ATTENTION_BLOCK_TYPE = "pipeline_attention"
SUMMARY_BLOCK_TYPE = "pipeline_summary"

#: The stage's slot in the pipeline order (the frontend still reads it).
STAGE_ORDINALS: dict[str, int] = {
    "evaluate": 1, "think": 2, "plan": 3, "build": 5, "review": 6,
    "test": 7, "deliver": 9, "reflect": 10,
}
#: v1 stage statuses: a closed stage is completed or degraded (exhausted checks)
_CLOSED = {"completed": "completed", "degraded": "completed"}
_OPEN = "open"
#: projection statuses that end a run: each gets the summary card
RUN_END_STATUSES = ("completed", "abandoned", "failed")
SUMMARY_MAX = 160
REQUIREMENT_MAX = 400
ITEM_TEXT_MAX = 200
MAX_ATTENTION_ITEMS = 8

def _valid_run_id(value: object) -> bool:
    """The protocol's one run-id rule (``identifiers.validate_run_id``)."""
    from .run_protocol.identifiers import IdentifierError, validate_run_id
    try:
        validate_run_id(value)
        return True
    except IdentifierError:
        return False
_RUN_ID_ARG_RE = re.compile(r"--run-id(?:=|\s+)(['\"]?)([^\s'\";&|)]+)\1(?=[\s'\";&|)]|$)")
_VAR_REF_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")

#: The documents a closed stage wrote (``RunEngine``: REQUIREMENTS at evaluate,
#: DESIGN_SPEC at plan, REVIEW / FIXING_SPEC / REPORT by DELIVER's end).
STAGE_DOCS: dict[str, tuple[str, ...]] = {
    "evaluate": ("REQUIREMENTS.md",), "plan": ("DESIGN_SPEC.md",),
    "deliver": ("REVIEW.md", "FIXING_SPEC.md", "REPORT.md"),
}
DOC_PURPOSES = {
    "REQUIREMENTS.md": "Requirements · each requirement and its acceptance check",
    "DESIGN_SPEC.md": "Design · the approach, its properties and failure modes",
    "REVIEW.md": "Review · each expert and each finding",
    "FIXING_SPEC.md": "Fixing spec · the fix and break test of each finding",
    "REPORT.md": "Report · what this run did, its evidence and conclusion",
}


def _last_cli(command: str) -> int:
    from core import pipeline_command
    return pipeline_command.last_call_offset(command)


def parse_watched_run_id(command: object) -> Optional[str]:
    """The literal run id of a pipeline CLI invocation, else None. A pre-filter only:
    ``new_stage_blocks`` decides from the projection whether anything changed."""
    if not isinstance(command, str):
        return None
    last = _last_cli(command)
    if last < 0:
        return None
    matches = list(_RUN_ID_ARG_RE.finditer(command))
    ordered = [m for m in matches if m.start() > last] + [
        m for m in reversed(matches) if m.start() < last]
    for m in ordered:
        value = m.group(2)
        ref = _VAR_REF_RE.fullmatch(value)
        if ref:
            # the value Bash uses is the LAST assignment before the reference
            assigns = list(re.finditer(
                r"(?:^|[\s;&(])(?:export\s+)?" + re.escape(ref.group(1))
                + r"=(['\"]?)([^\s'\";&|)]+)\1(?=[\s;&|)]|$)", command[: m.start()]))
            value = assigns[-1].group(2) if assigns else ""
        if _valid_run_id(value):
            return value
    return None


# ``pipeline_v1.py create`` names no run: the CLI prints the id it minted
RUN_CREATE_WATCH = "<run-create>"
_PIPELINE_ID_RE = re.compile(r'"pipeline_id"\s*:\s*"([^"]*)"')


def is_run_create(command: object) -> bool:
    """True when the last pipeline CLI invocation creates a run."""
    from core import pipeline_command
    return pipeline_command.is_create(command)


def _result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(t for t in (
            item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            for item in content) if isinstance(t, str))
    return ""


def run_id_from_create_output(content: object) -> Optional[str]:
    """The run id a ``create`` call printed (the last ``pipeline_id``), else None."""
    ids = _PIPELINE_ID_RE.findall(_result_text(content))
    return ids[-1] if ids and _valid_run_id(ids[-1]) else None


def resolve_watch(watch: Optional[str], content: object) -> Optional[str]:
    """The run id a watched call refers to: its ``--run-id``, or the id ``create`` printed."""
    if watch == RUN_CREATE_WATCH:
        return run_id_from_create_output(content)
    return watch


def _text(value: object) -> Optional[str]:
    return value if isinstance(value, str) else None


def read_stage_state(run_id: object) -> Optional[dict]:
    """The v1 run's projection as the stream reads it, or None (malformed id, no v1
    contract, no projection). The run directory is the contract run's own; the
    projection is display data only, never authority."""
    if not _valid_run_id(run_id):
        return None
    try:
        from config import get_app_data_dir
        from .run_protocol.contract import read_contract
        contract = read_contract(get_app_data_dir(), run_id)
        if not isinstance(contract, dict):
            return None
        project = contract.get("project")
        if not isinstance(project, str) or "/" in project or project in ("", ".", ".."):
            return None
        run_dir = Path(str(get_swarmws())).resolve() / "Projects" / project / ".artifacts" \
            / "runs" / run_id
        data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        raw = data.get("stages") if isinstance(data.get("stages"), dict) else {}
        stages = {name: {"stage": name, "status": status} for name, status in raw.items()
                  if isinstance(name, str) and isinstance(status, str)}
        commits = [c for c in data.get("commits") or [] if isinstance(c, dict)]
        status = {"active": "running", "reflecting": "running",
                  "awaiting_human": "paused"}.get(data.get("status"), data.get("status"))
        return {"profile": contract.get("profile"), "stages": stages,
                "status": status if isinstance(status, str) else None,
                "current_stage": _text(data.get("stage")),
                "updated_at": _text(data.get("updated_at")),
                # what the run was created to do: the contract's words only (R3 review)
                "requirement": _text(contract.get("requirement")),
                # an isolated run's change is on main only once it LANDED
                "isolated": contract.get("mode") == "isolated",
                "landing": _text(data.get("landing")),
                "reason": _text(data.get("reason")),
                "landed": any(c.get("landed") for c in commits),
                "landed_shas": [str(c["sha"]) for c in commits
                                if c.get("landed") and isinstance(c.get("sha"), str)],
                # every repo's landed commit (a run can land several repos)
                "landed_commits": [{"repo": str(c.get("repo") or ""), "sha": str(c["sha"])}
                                   for c in commits
                                   if c.get("landed") and isinstance(c.get("sha"), str)],
                "audit_card": _text(data.get("audit_card")),
                "audit_rows": _audit_rows(data.get("audit_rows")),
                "run_dir": str(run_dir)}
    except Exception:  # noqa: BLE001 — a display aid must never break the stream
        logger.debug("read_stage_state: unreadable v1 run %r", run_id, exc_info=True)
        return None


def _audit_rows(raw: Any) -> Optional[list[dict[str, str]]]:
    """The run audit's five card rows (run_protocol.audit.card_rows), or None."""
    if not isinstance(raw, list):
        return None
    rows = [{"dim": _clip(r.get("dim"), 40) or "", "verdict": _clip(r.get("verdict"), 20) or "",
             "detail": _clip(r.get("detail"), 300) or ""}
            for r in raw[:10] if isinstance(r, dict)]
    return rows or None


def _status(state: Optional[dict], stage: str) -> Optional[str]:
    rec = ((state or {}).get("stages") or {}).get(stage)
    raw = rec.get("status") if isinstance(rec, dict) else None
    return _CLOSED.get(raw, raw) if isinstance(raw, str) else None


def current_stage(state: Optional[dict]) -> Optional[str]:
    """The stage now open, else None."""
    current = (state or {}).get("current_stage")
    return current if isinstance(current, str) and _status(state, current) == _OPEN else None


def _order(profile: object) -> list[str]:
    return list(PROFILE_STAGES.get(profile, ())) if isinstance(profile, str) else []


def checklist(profile: Optional[str]) -> list[dict[str, Any]]:
    """The run's plan as the chat lists it: every stage of the profile, in order (v1
    has no separate gate rows: REVIEW is a stage)."""
    return [{"stage": stage, "gate": False} for stage in _order(profile)]


def _clip(value: object, limit: int = ITEM_TEXT_MAX) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def run_block(run_id: str, state: Optional[dict], at: str) -> Optional[dict[str, Any]]:
    """The run's alignment block, or None when there is no requirement to show."""
    if not state:
        return None
    requirement = _clip(state.get("requirement"), REQUIREMENT_MAX)
    if not requirement:
        return None
    profile = state.get("profile")
    return {"type": RUN_BLOCK_TYPE, "runId": run_id, "profile": profile,
            "stages": _order(profile), "checklist": checklist(profile),
            "requirement": requirement, "current": current_stage(state), "at": at}


def _row(run_id: str, after: dict, name: str, status: str, nxt: Optional[str],
         at: str) -> dict[str, Any]:
    order = _order(after.get("profile"))
    return {"type": BLOCK_TYPE, "runId": run_id, "stage": name, "status": status,
            "ordinal": STAGE_ORDINALS.get(name),
            "position": order.index(name) + 1 if name in order else None,
            "total": len(order), "profile": after.get("profile"), "next": nxt,
            "nextOrdinal": STAGE_ORDINALS.get(nxt) if nxt else None,
            "gate": None, "summary": None, "at": at}


def new_stage_blocks(run_id: str, before: Optional[dict], after: Optional[dict],
                     at: str) -> list[dict[str, Any]]:
    """A completed row per stage that closed between the two states, then an
    in-progress row per stage that opened; each group in the profile's order. A stage
    already in that status before emits nothing, so a row never repeats."""
    if not after:
        return []
    order = _order(after.get("profile"))
    names = [s for s in order if s in (after.get("stages") or {})]
    blocks: list[dict[str, Any]] = []
    for name in names:
        status = _status(after, name)
        if status != "completed" or _status(before, name) == status:
            continue
        i = order.index(name) + 1
        nxt = next((s for s in order[i:] if _status(after, s) != "completed"), None)
        blocks.append(_row(run_id, after, name, status, nxt, at))
    for name in names:
        if _status(after, name) == _OPEN and _status(before, name) != _OPEN:
            blocks.append(_row(run_id, after, name, "in_progress", None,
                               after.get("updated_at") or at))
    return blocks


def attention_due(before: Optional[dict], after: Optional[dict], deliver_row_ended: bool) -> bool:
    """With the DELIVER row for a shared run; for an isolated run only once its change
    LANDED (``landed`` turns true): the landing can still be refused after DELIVER."""
    if not after:
        return False
    if after.get("isolated"):
        return bool(after.get("landed")) and not (before or {}).get("landed")
    return deliver_row_ended


def attention_block(run_id: str, state: Optional[dict], at: str) -> Optional[dict[str, Any]]:
    """What in this run needs a person: a change that did not land (it is not on main).
    None when nothing does; open audit checks are not shown here (XG 2026-10-26: no
    push verdict, they are one line in the REPORT's Quality cell)."""
    if not state:
        return None
    items: list[dict] = []
    if state.get("landing") == "not_landed":
        reason = _clip(state.get("reason"), 120) or "not landed"
        items.append({"kind": "not_landed", "reasons": [reason],
                      "reasonCodes": [None], "detail": "Details: REPORT.md"})
    if not items:
        return None
    return {"type": ATTENTION_BLOCK_TYPE, "runId": run_id,
            "items": items[:MAX_ATTENTION_ITEMS],
            "more": max(0, len(items) - MAX_ATTENTION_ITEMS), "at": at}


def doc_excerpt(path: Any, max_chars: int = 500) -> Optional[str]:
    """The first section of a run document (after its title): what a chat card shows
    before the reader opens it. None when unreadable or empty."""
    try:
        text = Path(str(path)).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, TypeError):
        return None
    return _excerpt_of(text, max_chars)


def _excerpt_of(text: str, max_chars: int = 500) -> Optional[str]:
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), None)
    if start is None:
        body = [ln for ln in lines if ln.strip() and not ln.startswith("# ")]
    else:
        end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")),
                   len(lines))
        body = [ln for ln in lines[start:end] if ln.strip()]
    out = "\n".join(body).strip()
    if not out:
        return None
    return out if len(out) <= max_chars else out[: max_chars - 1].rstrip() + "…"


_DOC_READ_CAP = 256 * 1024


def _read_regular_no_follow(base: Path, directory: Path, name: str) -> Optional[str]:
    """*name* inside *directory* (which lies under *base*), read without following a
    link anywhere below *base*: every component from *base* down is opened with
    O_NOFOLLOW relative to its parent's fd, and the file is read from its own fd only
    when it is a regular file; else None. A swap of the run directory or any parent to
    a symlink after the caller's checks is refused by the opens themselves (re-audits
    536bb62b, 84b72ec1)."""
    import stat
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_DIRECTORY", 0)
    try:
        parts = directory.relative_to(base).parts
        dir_fd = os.open(str(base), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except (ValueError, OSError):
        return None
    try:
        for part in parts:
            nxt = os.open(part, flags, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = nxt
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError:
        return None
    finally:
        os.close(dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=True) as f:
            fd = -1
            data = f.read(_DOC_READ_CAP)
        return data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def _doc_ref(run_dir: Optional[str], name: str) -> Optional[dict[str, str]]:
    """``{name, path, absolutePath, excerpt?}`` for a run document on disk (``path``
    workspace-relative, as the Canvas lists it); None when absent. The run directory
    is agent-writable, so a document that is a symlink, or resolves outside the run
    directory or the workspace, is not a run document: its bytes are never read
    (R3 review)."""
    if not run_dir:
        return None
    try:
        ws = Path(str(get_swarmws())).resolve(strict=True)
        root = Path(run_dir).resolve(strict=True)
        p = Path(run_dir) / name
        if p.is_symlink() or not p.is_file():
            return None
        real = p.resolve(strict=True)
        if real.parent != root or ws not in root.parents:
            return None
        ref = {"name": name, "path": str(real.relative_to(ws)), "absolutePath": str(real)}
        # read through ONE fd opened without following a link, relative to the checked
        # run directory: a swap to a symlink after the checks above is refused by the
        # open itself (re-audit 536bb62b TOCTOU)
        text = _read_regular_no_follow(ws, root, name)
        if text is None:
            return None
        excerpt = _excerpt_of(text)
        if excerpt:
            ref["excerpt"] = excerpt
        return ref
    except Exception:  # noqa: BLE001 — a display aid must never break the stream
        logger.debug("_doc_ref: suppressed exception on a fail-safe path", exc_info=True)
        return None


def attach_stage_docs(blocks: list[dict], state: Optional[dict]) -> list[dict[str, str]]:
    """Give each COMPLETED stage row the run documents its stage wrote (``docs``;
    ``doc`` keeps the first) and return them all: the orchestrator surfaces each as a
    knowledge file. Mutates ``blocks`` in place."""
    out: list[dict[str, str]] = []
    run_dir = (state or {}).get("run_dir")
    for b in blocks:
        if b.get("type") != BLOCK_TYPE or b.get("status") != "completed":
            continue
        refs = [r for r in (_doc_ref(run_dir, n) for n in STAGE_DOCS.get(
            str(b.get("stage") or ""), ())) if r]
        if refs:
            b["docs"], b["doc"] = refs, refs[0]
            out.extend(refs)
    return out


#: the TEST commands a summary lists, and how much of each command
MAX_SUMMARY_TESTS = 60
MAX_SUMMARY_COMMAND = 500


def _test_commands(run_dir: Optional[str]) -> list[dict[str, str]]:
    """The commands of the run's TEST validation report, as its audit keeps them
    (``run_audit.json`` ``details.test_reports``, the record the REPORT's test section is
    rendered from): ``{repo, command, result}``, result one of pass / fail / not_run."""
    if not run_dir:
        return []
    try:
        path = Path(run_dir) / "run_audit.json"
        if path.stat().st_size > 4 * 1024 * 1024:
            return []
        reports = (json.loads(path.read_text(encoding="utf-8")).get("details") or {}).get("test_reports")
    except (OSError, ValueError, AttributeError):
        return []
    out: list[dict[str, str]] = []
    for repo, rows in sorted(reports.items()) if isinstance(reports, dict) else []:
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and isinstance(row.get("command"), str):
                result = row.get("result") if row.get("result") in (
                    "pass", "fail", "not_run", "pre_existing") else "other"
                out.append({"repo": str(repo)[:100], "command": row["command"][:MAX_SUMMARY_COMMAND],
                            "result": result})
    return out[:MAX_SUMMARY_TESTS]


def summary_block(run_id: str, state: Optional[dict], at: str) -> Optional[dict[str, Any]]:
    """The run's execution summary, written once when it ends: how it ended, whether it
    landed / is push-ready, its audit card, its documents, and the commands of its TEST
    report (``tests``: what the Swarm Out checks grid reads, the same record the REPORT
    shows)."""
    if not state or state.get("status") not in RUN_END_STATUSES:
        return None
    run_dir = state.get("run_dir")
    order = _order(state.get("profile"))
    docs: list[dict[str, Any]] = []
    for stage in order:
        for name in STAGE_DOCS.get(stage, ()):
            ref = _doc_ref(run_dir, name)
            docs.append({"name": name, "purpose": DOC_PURPOSES.get(name),
                         "path": ref["path"] if ref else None,
                         "absolutePath": ref["absolutePath"] if ref else None,
                         "required": name != "FIXING_SPEC.md",
                         "reason": None if ref else "not written by this run"})
    not_landed = state.get("landing") == "not_landed"
    landed = state.get("landed_shas") or []
    return {"type": SUMMARY_BLOCK_TYPE, "runId": run_id, "profile": state.get("profile"),
            "intent": _clip(state.get("requirement"), 200), "solved": None,
            "landedSha": str(landed[-1])[:12] if landed else None,
            "commits": [dict(c) for c in state.get("landed_commits") or []],
            "status": state.get("status"), "landing": state.get("landing"),
            "notLandedReason": _clip(state.get("reason"), 200) if not_landed else None,
            "stagesDone": sum(1 for s in order if _status(state, s) == "completed"),
            "docs": docs, "at": at, "auditCard": state.get("audit_card"),
            "audit": state.get("audit_rows"), "tests": _test_commands(run_dir)}
