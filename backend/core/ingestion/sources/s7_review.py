"""S7 after-session review in a fork of the same session (2026-10-29-cdf-session-review.md).

- ``signals`` / ``qualifies``: what happened after the watermark, read from the message
  store (user messages, tool calls, files written, a rule or correction), and whether
  that is enough to review. Nothing is counted on the chat path.
- ``build_prompt``: the after-action review, the three filters, situation-judgment-
  reason-evidence, the seven types and the per-kind rules (from contracts_v2), examples
  per task type, the flagged user messages and the evidence index.
- ``review_session``: registers the read set, resumes a fork of the session through the
  injectable ``runner`` (default: the SDK, no tools, structured output), validates the
  units against SOURCE_UNIT and enqueues them as S7. ``unresumable`` tells the job to
  run the S2 main pass instead.
- ``sampled``: the deterministic 10% of sessions that also get an S2 main pass, to
  measure what the review missed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Callable

from core.ingestion import api_v2, contracts_v2
from core.ingestion.llm_usage import attribute

from . import extract_llm, s2_session

logger = logging.getLogger(__name__)

MAX_UNITS = 15  # units one review may enqueue (cost bound, design 2026-10-29-cdf-launch §3 P2)
REVIEW_VERSION = "cdf-review-r16.15-v4"
ALLOWED_KINDS = ("cognition", "gap", "reference")   # events come from S3/S4 (§4)
MIN_USER_MESSAGES = 3
MIN_TOOL_CALLS = 10
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
CODE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".kt",
                           ".swift", ".c", ".cc", ".cpp", ".h", ".rb", ".sh", ".sql", ".css"})
SAMPLE_PERCENT = 10
INDEX_USER_LIMIT = 200      # evidence index caps: what falls past is left off, not guessed
INDEX_MEASURED_LIMIT = 100
REVIEW_TIMEOUT_S = 300
# The review runs on Sonnet whatever model the session used (user 2026-10-29): it reads
# the whole session context, so an Opus review would cost several times more. Same model
# key as the CDF extractor and cards (BedrockJudge).
REVIEW_MODEL_KEY = "claude-sonnet-4-6"

Runner = Callable[[str, str, str, dict], dict]   # (sdk_session_id, cwd, prompt, schema) -> answer


class Unresumable(Exception):
    """The session cannot be resumed (no sdk id, no CLI transcript, resume failed)."""


def _key(item: dict[str, Any]) -> str:
    return f"{item.get('created_at') or ''}|{item.get('id') or ''}"


def after_watermark(transcript: list[dict[str, Any]], watermark: str | None) -> list[dict[str, Any]]:
    return [item for item in transcript if not watermark or _key(item) > watermark]


def _blocks(item: dict[str, Any]) -> list[dict[str, Any]]:
    content = item.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    return "\n".join(str(b.get("text") or "") for b in _blocks(item) if b.get("type") == "text")


def _rule_patterns():
    from core.runtime_hooks import (
        _CORRECTION_PATTERNS_EN, _CORRECTION_PATTERNS_META, _HIGH_SIGNAL_PATTERNS)
    return (_CORRECTION_PATTERNS_EN, _CORRECTION_PATTERNS_META, _HIGH_SIGNAL_PATTERNS)


def signals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic signals over *rows* (the transcript after the watermark)."""
    from core.injected_blocks import strip_injected_blocks
    patterns = _rule_patterns()
    users, rule_ids, files, tools = 0, [], set(), 0
    for item in rows:
        if item.get("role") == "user":
            users += 1
            typed = strip_injected_blocks(_text(item))
            if typed and any(p.search(typed) for p in patterns):
                rule_ids.append(str(item.get("id")))
        for block in _blocks(item):
            if block.get("type") != "tool_use":
                continue
            tools += 1
            if block.get("name") in WRITE_TOOLS:
                data = block.get("input") or {}
                path = data.get("file_path") or data.get("notebook_path") or data.get("path")
                if path:
                    files.add(str(path))
    return {"user_messages": users, "tool_calls": tools, "files_written": sorted(files),
            "rule_messages": rule_ids}


def qualifies(sig: dict[str, Any]) -> bool:
    return (sig["user_messages"] >= MIN_USER_MESSAGES or sig["tool_calls"] >= MIN_TOOL_CALLS
            or bool(sig["files_written"]) or bool(sig["rule_messages"]))


def session_type(sig: dict[str, Any]) -> str:
    """code / files / conversation, for reporting the miss rate per type (§12)."""
    if any(Path(p).suffix.lower() in CODE_SUFFIXES for p in sig["files_written"]):
        return "code"
    return "files" if sig["files_written"] else "conversation"


def sampled(session_id: str) -> bool:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100 < SAMPLE_PERCENT


# ── evidence: read set and index ─────────────────────────────────────────────

def measured_from_ref(ref: str, message: dict[str, Any]) -> tuple[str, Any] | None:
    """(field path, value) of the measured root registered under *ref*, read back from
    the transcript *message* it came from. The store keeps only the hash, so this is the
    one way to get the value (ref: ``s2:<message id>#<block>:<type>#field:<path>``)."""
    from core import session_inputs
    try:
        head, path = str(ref).split("#field:", 1)
        _message_id, block = head[len(s2_session._S2_REF):].rsplit("#", 1)
        content = s2_session._blocks(message)[int(block.split(":", 1)[0])][1]
        value = json.loads(content) if isinstance(content, str) else content
        fields = dict(session_inputs._measured_fields(value))
    except (KeyError, ValueError, IndexError, TypeError):
        return None
    return (path, fields[path]) if path in fields else None


def message_id_of(ref: str) -> str:
    return str(ref)[len(s2_session._S2_REF):].split("#", 1)[0]


def _measured(session_id: str, rows: list[dict[str, Any]],
              event_ids: tuple[str, ...]) -> list[tuple[str, str, Any]]:
    """(anchor, field path, value) for the measured roots registered for *rows*. The
    store keeps only hashes, so the value is read back from the transcript block."""
    import sqlite3
    from core import session_inputs
    if not event_ids:
        return []
    marks = ",".join("?" * len(event_ids))
    with sqlite3.connect(str(session_inputs._db_path()), timeout=5.0) as conn:
        found = conn.execute(
            f"SELECT event_id, ref FROM session_inputs WHERE session_id=? AND kind='measured_field' "
            f"AND event_id IN ({marks})", (session_id, *event_ids)).fetchall()
    by_id = {str(item.get("id")): item for item in rows}
    out = []
    for event_id, ref in found:
        message = by_id.get(message_id_of(ref))
        found_value = measured_from_ref(ref, message) if message else None
        if found_value:
            out.append((f"event:{event_id}", *found_value))
    return out[:INDEX_MEASURED_LIMIT]


def evidence(session_id: str, rows: list[dict[str, Any]],
             input_rows: list[dict[str, Any]]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """(index text, citable anchors, read-set root ids) for reviewing *rows*.

    Registers the agent and tool content of *rows* (s2_session.window_roots), so the
    read set is everything the fork can see after the watermark plus every user root of
    the session; only user messages and measured fields are citable (§16)."""
    owned = [r for r in input_rows if not str(r.get("ref") or "").startswith(s2_session._S2_REF)]
    user_events = s2_session._user_events_by_message(owned)
    kinds = {str(r["event_id"]): r.get("kind") for r in owned}
    registered: list[str] = []
    for chunk in s2_session.windows(rows, "id"):
        for ids in s2_session.window_roots(session_id, chunk):
            registered += ids
    lines, anchors = [], []
    for item in rows:
        if item.get("role") != "user" or len(anchors) >= INDEX_USER_LIMIT:
            continue
        ids = user_events.get(str(item.get("id")), [])
        primary = [e for e in ids if kinds.get(e) == "user_message"] or ids
        if primary:
            head = " ".join(_text(item).split())[:120]
            lines.append(f"- event:{primary[0]} (user message): {head}")
            anchors.append(f"event:{primary[0]}")
    for anchor, path, value in _measured(session_id, rows, tuple(dict.fromkeys(registered))):
        lines.append(f"- {anchor} (measured {path}): {json.dumps(value, ensure_ascii=False)}")
        anchors.append(anchor)
    user_ids = tuple(str(r["event_id"]) for r in owned)
    read = s2_session._root_ids(tuple(dict.fromkeys(user_ids + tuple(registered))))
    return "\n".join(lines), tuple(dict.fromkeys(anchors)), read


# ── prompt ────────────────────────────────────────────────────────────────────

_TYPE_QUESTIONS = {
    "correction": "what was done wrong before, and who noticed?",
    "pitfall": "under what condition does it go wrong, and how do you recognise it?",
    "decision": "which option was chosen, which were rejected, and why?",
    "guideline": "which way of working proved itself and should be repeated?",
    "process": "which sequence of steps matters in its order?",
    "model": "why is the system the way it is (the mechanism)?",
    "principle": "do not write one from a single session.",
}

_EXAMPLES = """- research: which sources were reliable and which not, and why; the conclusions and
  what they rest on; what is still unresolved (gap). A finished report is a reference.
- documents and decks: what the user's feedback on structure, length and tone showed;
  which writing was accepted and which was changed. The final version is a reference.
- mockups and design: what each round changed and why; directions rejected and why.
- brainstorming: ideas chosen and dropped, and why (decision); open questions (gap);
  ideas worth bringing back later (reference).
- code: root cause, fix and rejected alternatives, only when not obvious."""


def build_prompt(allowed: tuple[str, ...], sig: dict[str, Any], index: str) -> str:
    types = "\n".join(f"- {name}: {_TYPE_QUESTIONS[name]}" for name in contracts_v2.TYPES)
    rules = "\n".join(f"- {kind}: " + "; ".join(contracts_v2.UNIT_RULES[kind])
                      for kind in allowed if contracts_v2.UNIT_RULES.get(kind))
    flagged = ""
    if sig["rule_messages"]:
        flagged = ("\nThe user stated a rule or corrected you in these messages (ids from this "
                   f"conversation): {', '.join(sig['rule_messages'])}. Write each lasting one as a "
                   "unit that a reader outside this conversation understands.\n")
    return f"""This conversation is over for now. Review the work since your last review and
record what is worth keeping, by calling {extract_llm.TOOL_NAME}. Write nothing else.

1. After-action review. Ask: what was intended? what actually happened? why did the two
   differ (a pitfall or a correction)? what to do next time (a guideline or a process)?
   Record the trade-offs you made as decisions and what is left open as gaps.
2. Keep a unit only if all three hold: it is useful in another task; a capable person who
   was not here would go wrong or waste time without it; you can point to its evidence.
3. Write each unit as situation (applicability: a recognisable trigger, not "when
   developing"), judgment (what; how for a process), reason (why; for a decision also
   rejected_alternatives) and evidence.
4. The seven types, each recognised by one question:
{types}
5. Ask about yourself as well (scope "agent"): where was your judgment wrong this time, why
   did the user correct you, which of your ways of working are reliable and which are not.
6. Per kind of work:
{_EXAMPLES}
{flagged}
Allowed kinds: {", ".join(allowed)}. Rules the validator enforces:
{rules}
- scope is {contracts_v2.SCOPE_RULE}.

Do not record operational trivia, unverified guesses, restatements of memory you were given,
task-local state, or a log of what you did. Recording nothing is a valid answer.
{extract_llm.QUOTE_RULE}{extract_llm.UNIT_SIZE_RULE}
Evidence: cite only anchors from this index (user messages and measured results since your
last review). Cite every one the unit rests on.
{index or "(no citable evidence: record nothing)"}
"""


# ── the fork ─────────────────────────────────────────────────────────────────

def _cwd_of(sdk_session_id: str) -> str:
    from core.leak_transcript import find_transcript
    path = find_transcript(sdk_session_id)
    if path is None:
        raise Unresumable("no CLI transcript")
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                cwd = json.loads(line).get("cwd")
            except ValueError:
                continue
            if cwd:
                return str(cwd)
    raise Unresumable("CLI transcript has no cwd")


def _drop_fork(fork_id: str | None, original_id: str) -> None:
    """Remove the fork's own CLI transcript: one review must not leave a conversation
    file behind (the original is never touched)."""
    from core.leak_transcript import find_transcript
    if not fork_id or fork_id == original_id:
        return
    path = find_transcript(fork_id)
    if path is not None:
        try:
            path.unlink()
        except OSError:
            logger.warning("CDF review: could not remove fork transcript %s", path)


def review_options(sdk_session_id: str, cwd: str, schema: dict) -> Any:
    """The SDK options of a review: a fork of the session, no tools, structured output."""
    from claude_agent_sdk import ClaudeAgentOptions
    from jobs.bedrock import get_model_id
    from jobs.executor import _build_cli_env, _get_aws_credentials, _resolve_claude_cli
    return ClaudeAgentOptions(
        resume=sdk_session_id, fork_session=True, tools=[], cwd=cwd, max_turns=3,
        model=get_model_id(REVIEW_MODEL_KEY),
        output_format={"type": "json_schema", "schema": schema}, strict_mcp_config=True,
        setting_sources=[], env=_build_cli_env(_get_aws_credentials()),
        cli_path=_resolve_claude_cli())


def sdk_runner(sdk_session_id: str, cwd: str, prompt: str, schema: dict) -> dict:
    """Resume a fork of the session with no tools and structured output (§6.2)."""
    from claude_agent_sdk import ResultMessage, query
    from core.ingestion import llm_usage
    options = review_options(sdk_session_id, cwd, schema)

    async def run() -> dict:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                usage = message.usage or {}
                llm_usage.record({"input_tokens": int(usage.get("input_tokens") or 0)
                                  + int(usage.get("cache_read_input_tokens") or 0)
                                  + int(usage.get("cache_creation_input_tokens") or 0),
                                  "output_tokens": int(usage.get("output_tokens") or 0)})
                errors = " ".join(message.errors or [])
                if message.is_error:
                    detail = f"{errors} {message.result or ''}"
                    # gone, or larger than the review model's window: S2 reads it instead
                    if "No conversation found" in detail or "too long" in detail.lower():
                        raise Unresumable(detail.strip()[:300])
                    raise RuntimeError(f"review failed: {message.subtype} {errors}"[:300])
                _drop_fork(message.session_id, sdk_session_id)
                if not isinstance(message.structured_output, dict):
                    raise extract_llm.UntypedOutput("reply missing: no structured output")
                return message.structured_output
        raise RuntimeError("review ended without a result")

    return asyncio.run(asyncio.wait_for(run(), REVIEW_TIMEOUT_S))


def current_sdk_session_id(session_id: str) -> str | None:
    """The live unit's SDK session id, else the one persisted for a later resume."""
    from core import session_registry
    router = getattr(session_registry, "session_router", None)
    if router is None:
        return None
    unit = router.get_unit(session_id)
    return (getattr(unit, "_sdk_session_id", None) if unit is not None
            else router.persisted_sdk_session_id(session_id))


# ── one review ───────────────────────────────────────────────────────────────

def review_session(session_id: str, *, sdk_session_id: str | None, watermark: str | None,
                   force: bool = False, project_snapshot: dict[str, Any] | None = None,
                   runner: Runner | None = None) -> dict[str, Any]:
    """Review the session after *watermark*. Returns ``{status, reviewed_to, units, ...}``;
    status is ``reviewed``, ``nothing_new``, ``below_threshold`` or ``unresumable``."""
    transcript = s2_session._transcript_sync(session_id)
    rows = after_watermark(transcript, watermark)
    if not rows:
        return {"status": "nothing_new", "reviewed_to": watermark, "units": 0}
    sig = signals(rows)
    reviewed_to = _key(rows[-1])
    if not force and not qualifies(sig):
        return {"status": "below_threshold", "reviewed_to": watermark, "units": 0, "signals": sig}
    if not sdk_session_id:
        return {"status": "unresumable", "reviewed_to": watermark, "units": 0, "reason": "no sdk id"}
    try:
        cwd = _cwd_of(sdk_session_id)
    except Unresumable as exc:
        return {"status": "unresumable", "reviewed_to": watermark, "units": 0, "reason": str(exc)}
    index, anchors, read = evidence(session_id, rows, s2_session._input_rows(session_id))
    prompt = build_prompt(ALLOWED_KINDS, sig, index)
    schema = extract_llm.unit_schema(ALLOWED_KINDS)
    attempt = api_v2.ExtractionAttempt(
        source="S7", attempt_id=f"attempt:{uuid.uuid4().hex}", extractor_version=REVIEW_VERSION,
        evidence_range={"session_id": session_id, "pass": "review", "from": watermark,
                        "to": reviewed_to, "session_type": session_type(sig),
                        "sampled": sampled(session_id), "forced": force,
                        "signals": {k: (len(v) if isinstance(v, list) else v) for k, v in sig.items()}})
    try:
        with attribute("S7"):
            answer = (runner or sdk_runner)(sdk_session_id, cwd, prompt, schema)
    except Unresumable as exc:
        return {"status": "unresumable", "reviewed_to": watermark, "units": 0, "reason": str(exc)}
    except Exception as exc:
        api_v2.record_extraction_attempt(attempt, status="failed", error=f"{type(exc).__name__}: {exc}"[:500])
        raise
    raw_units = extract_llm.units_from_answer(answer, ALLOWED_KINDS)
    result = extract_llm._validate(raw_units, ALLOWED_KINDS)
    # every unit costs 2-5 judge calls: one review contributes at most MAX_UNITS, the
    # rest are recorded as invalid (dropped, never silently)
    for unit in result.units[MAX_UNITS:]:
        result.invalid_units.append({"raw": unit.model_dump(mode="json"), "reason": "over_unit_cap"})
    result.units = result.units[:MAX_UNITS]
    bad = [u for u in result.units if not set(u.evidence) <= set(anchors)]
    for unit in bad:
        result.invalid_units.append({"raw": unit.model_dump(mode="json"),
                                     "reason": "evidence_not_in_index"})
    units = [u for u in result.units if u not in bad]
    for unit in units:
        api_v2.enqueue_unit("S7", session_id, unit, session_id=session_id, run_id=None,
                            read_root_ids=read,
                            project_snapshot=project_snapshot, attempt_id=attempt.attempt_id,
                            extraction_pass="main")
    api_v2.record_extraction_attempt(attempt, status="ok" if units else "no_unit",
                                     units_emitted=len(units), invalid_units=result.invalid_units)
    return {"status": "reviewed", "reviewed_to": reviewed_to, "units": len(units),
            "invalid": len(result.invalid_units), "session_type": session_type(sig)}
