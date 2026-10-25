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
            raise ReflectError(f"lesson {index} has wrong fields")
        if "repo" in raw and raw["repo"] not in (repos or set()):
            raise ReflectError(f"lesson {index} repo is not one of this run's repos")
        lesson_id, lesson_type = raw["id"], raw["type"]
        if not isinstance(lesson_id, str) or not re.fullmatch(r"L[1-9][0-9]*", lesson_id) \
                or lesson_id in ids or lesson_type not in TYPES:
            raise ReflectError(f"lesson {index} has invalid id/type")
        ids.add(lesson_id)
        statement = raw["statement"]
        if not isinstance(statement, str) or not statement.strip() or len(statement) > 400 \
                or _VOLATILE.search(statement) or _MULTILINE.search(statement):
            raise ReflectError(f"lesson {lesson_id} statement is volatile or invalid")
        tell = raw["tell"]
        if not isinstance(tell, str) or not tell.strip() or len(tell) > 400 \
                or _MULTILINE.search(tell):
            raise ReflectError(f"lesson {lesson_id} tell must be one line of text")
        evidence = raw["evidence"]
        if not isinstance(evidence, list) or not evidence or not set(evidence) <= event_ids:
            raise ReflectError(f"lesson {lesson_id} evidence is not from this run")
        relation = raw["relation"]
        if not isinstance(relation, Mapping) or set(relation) != {"kind", "entry"} \
                or relation.get("kind") not in RELATIONS:
            raise ReflectError(f"lesson {lesson_id} relation is invalid")
        available = list(candidates.get(lesson_id, ()))
        if available and relation["kind"] == "new":
            raise ReflectError(f"lesson {lesson_id} must relate to a dedup candidate")
        if relation["kind"] != "new" and relation.get("entry") not in available:
            raise ReflectError(f"lesson {lesson_id} relation target is not a candidate")
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
