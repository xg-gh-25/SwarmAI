"""Pipeline v1 reflection preparation and bounded background processing.

Public symbols:
- ``prepare`` — derive layer/destination and validate evidence/relation before landing.
- ``process`` — judge and route lessons with bounded attempts, always recording outcomes.
- ``ReflectError`` — malformed lesson or unsupported routing.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Mapping, Sequence

TYPES = frozenset({"principle", "correction", "decision", "model",
                   "guideline", "pitfall", "process"})
LAYERS = {"principle": "metacognition", "correction": "metacognition",
          "decision": "cognition", "model": "cognition",
          "guideline": "operation", "pitfall": "operation", "process": "operation"}
RELATIONS = frozenset({"new", "reinforces", "refines", "contradicts"})
# a lesson is one line of plain text: it is written into a DDD doc as one bullet, so a
# line break would let it write its own headings or entries (K3 review r1)
_MULTILINE = re.compile(r"[\r\n\u2028\u2029\x0b\x0c\x85]")
_VOLATILE = re.compile(r"\brun_[A-Za-z0-9_-]+\b|/(?:Users|home)/[^/]+/|\b(?:running|pending|done)\b", re.I)


_MAX_TEXT = 400


def _statement_problem(statement: Any) -> str | None:
    """Why a lesson statement is refused, with the rule it broke, or None."""
    if not isinstance(statement, str) or not statement.strip():
        return "it is empty"
    if len(statement) > _MAX_TEXT:
        return f"{len(statement)} characters, at most {_MAX_TEXT}"
    if _MULTILINE.search(statement):
        return "it has a line break (one line of text)"
    hit = _VOLATILE.search(statement)
    if hit:
        return (f"it names {hit.group(0)!r}: no run ids, home paths or status words "
                "(running / pending / done)")
    return None


class ReflectError(ValueError):
    """A lesson cannot safely enter background cultivation."""


class TransientWrite(RuntimeError):
    """The destination was busy (retried, then degraded)."""


# writer results (ddd_cultivation.apply_to_ddd): what counts as applied, what is retried
APPLIED_RESULTS = frozenset({"applied", "created_section", "duplicate"})
TRANSIENT_RESULTS = frozenset({"locked"})
# the destination does not exist: the lesson stays in the run's record (S6-4d)
KEPT_RESULTS = frozenset({"kept_in_run"})
# ... or went to the shared Library instead (knowledge-delivery-gaps §3.1)
LIBRARY_PREFIX = "library:"


def destination(lesson_type: str, scope: str) -> str:
    layer = LAYERS[lesson_type]
    if scope == "product":
        return "self-evolution-proposal"
    if scope.startswith("domain:") and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}",
                                                    scope[len("domain:"):]):
        section = ({("metacognition", "principle"): "PRODUCT:Design Philosophy",
                    ("metacognition", "correction"): "IMPROVEMENT:What Failed"}
                   .get((layer, lesson_type))
                   or {"cognition": "PROJECT:Recent Decisions" if lesson_type == "decision" else "TECH:Architecture",
                       "operation": "TECH:Conventions" if lesson_type == "guideline" else
                                    "IMPROVEMENT:What to Watch For" if lesson_type == "pitfall" else "TECH:Processes"}[layer])
        return f"{scope}:{section}"
    if scope == "instance":
        return "EVOLUTION" if lesson_type == "correction" else "MEMORY"
    raise ReflectError("scope must be product, domain:<project>, or instance")


def prepare(lessons: Sequence[Mapping[str, Any]], *, event_ids: set[str],
            candidates: Mapping[str, Sequence[str]],
            repos: "set[str] | None" = None) -> dict[str, Any]:
    """*repos*: the run's repo names; a lesson may name one in ``repo`` (where a lesson
    no project DDD takes is written: knowledge-delivery-gaps §3.1)."""
    if not isinstance(lessons, (list, tuple)):
        raise ReflectError("lessons must be a list")
    out = []
    ids: set[str] = set()
    for index, raw in enumerate(lessons):
        required = {"id", "type", "scope", "statement", "tell", "evidence", "relation"}
        if not isinstance(raw, Mapping) or set(raw) - {"repo"} != required:
            have = set(raw) if isinstance(raw, Mapping) else set()
            raise ReflectError(
                f"lesson {index} has wrong fields: missing {sorted(required - have)}, "
                f"unknown {sorted(have - required - {'repo'})} (fields: {sorted(required)}, "
                "optional repo)")
        if "repo" in raw and raw["repo"] not in (repos or set()):
            raise ReflectError(f"lesson {index} repo is not one of this run's repos")
        lesson_id, lesson_type = raw["id"], raw["type"]
        if not isinstance(lesson_id, str) or not re.fullmatch(r"L[1-9][0-9]*", lesson_id) \
                or lesson_id in ids or lesson_type not in TYPES:
            raise ReflectError(f"lesson {index} has invalid id/type: id {lesson_id!r} (L1, "
                               f"L2, ... each once), type {lesson_type!r} (one of "
                               f"{', '.join(sorted(TYPES))})")
        ids.add(lesson_id)
        statement = raw["statement"]
        why = _statement_problem(statement)
        if why:
            raise ReflectError(f"lesson {lesson_id} statement is volatile or invalid: {why}")
        tell = raw["tell"]
        if not isinstance(tell, str) or not tell.strip() or len(tell) > _MAX_TEXT \
                or _MULTILINE.search(tell):
            raise ReflectError(f"lesson {lesson_id} tell must be one line of text, at most "
                               f"{_MAX_TEXT} characters (it has "
                               f"{len(tell) if isinstance(tell, str) else 0})")
        evidence = raw["evidence"]
        if not isinstance(evidence, list) or not evidence or not set(evidence) <= event_ids:
            unknown = sorted(str(item) for item in evidence or () if item not in event_ids) \
                if isinstance(evidence, list) else []
            raise ReflectError(f"lesson {lesson_id} evidence is not from this run: give at "
                               "least one event id of this run (pipeline-v1 events lists "
                               f"them); not in this run: {unknown}")
        relation = raw["relation"]
        if not isinstance(relation, Mapping) or set(relation) != {"kind", "entry"} \
                or relation.get("kind") not in RELATIONS:
            raise ReflectError(f"lesson {lesson_id} relation is invalid")
        available = list(candidates.get(lesson_id, ()))
        # "new" stays allowed with candidates: they are the CLI's keyword recall, kept on
        # the lesson below, so the agent's judgment that they are unrelated is on record
        # (research-deliver #3; forcing a relation made every new lesson unwritable)
        if relation["kind"] != "new" and relation.get("entry") not in available:
            shown = "; ".join(available) if available else "none"
            raise ReflectError(f"lesson {lesson_id} relation target is not a candidate "
                               f"(candidates: {shown})")
        out.append({**dict(raw), "layer": LAYERS[lesson_type],
                    "destination": destination(lesson_type, raw["scope"]),
                    "candidates": available, "outcome": None})
    return {"schema": 1, "lessons": out}


def process(document: Mapping[str, Any], *,
            judge: Callable[[Mapping[str, Any]], str],
            writer: Callable[[Mapping[str, Any], str], str],
            max_attempts: int = 2, max_seconds: float = 60.0) -> dict[str, Any]:
    """Process lessons after landing; timeout/failure degrades but loses no outcome."""
    if not isinstance(document, Mapping) or document.get("schema") != 1 \
            or not isinstance(document.get("lessons"), list):
        raise ReflectError("prepared reflect document is required")
    if not isinstance(max_attempts, int) or max_attempts < 1 or max_seconds <= 0:
        raise ReflectError("background bounds must be positive")
    started = time.monotonic()
    rows = []
    degraded = False
    for lesson in document["lessons"]:
        outcome = None
        for attempt in range(1, max_attempts + 1):
            if time.monotonic() - started > max_seconds:
                break
            try:
                verdict = judge(lesson)
                if verdict in {"noise", "suspect"}:
                    outcome = {"status": verdict, "attempts": attempt}
                elif verdict == "keep":
                    result = str(writer(lesson, lesson["destination"]))
                    if result in TRANSIENT_RESULTS:
                        raise TransientWrite(result)
                    outcome = {"status": "applied" if result in APPLIED_RESULTS
                               or result.startswith("queued:") else
                               "library" if result.startswith(LIBRARY_PREFIX) else
                               "kept_in_run" if result in KEPT_RESULTS else "rejected",
                               "result": result, "attempts": attempt}
                else:
                    outcome = {"status": "judge_error", "detail": str(verdict), "attempts": attempt}
                break
            except Exception as exc:
                outcome = {"status": "retry", "detail": type(exc).__name__, "attempts": attempt}
        if outcome is None or outcome["status"] in {"retry", "judge_error"}:
            degraded = True
            outcome = outcome or {"status": "pending", "attempts": 0}
        rows.append({**dict(lesson), "outcome": outcome})
    return {"schema": 1, "lessons": rows,
            "reflect": "degraded" if degraded else "ok"}
