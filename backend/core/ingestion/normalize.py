"""Deterministic CDF candidate normalization.

- ``normalize_candidate`` emits indivisible claim-groups.
- ``normalize_batch`` marks exact normalized duplicates as merged.
- ``resolve_project_candidates`` applies the normative precedence rules.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from .contracts import Candidate, SourcePayload

_NORMALIZER_VERSION = "cdf-normalizer-v1"


@dataclass(frozen=True)
class ClaimGroup:
    """An atomic claim and all of its why/how/evidence context."""

    slot: str
    text: str
    title: str | None = None
    why: str | None = None
    how: str | None = None
    evidence: str | None = None
    type_hint: str | None = None
    project_candidates: tuple[str, ...] = ()
    merged_into: str | None = None

    @property
    def fingerprint(self) -> str:
        material = json.dumps(
            {"text": self.text, "title": self.title, "why": self.why, "how": self.how,
             "evidence": self.evidence, "type_hint": self.type_hint},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _strings(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, (list, tuple)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def resolve_project_candidates(candidate: Candidate) -> tuple[str, ...]:
    """Resolve projects without allowing the judge to invent one.

    Sources are evaluated in order. A later source is considered only when an
    earlier source yielded no project, matching design §7.
    """
    snapshot = candidate.project_snapshot or {}
    explicit = _strings(snapshot.get("explicit_project") or snapshot.get("project"))
    if explicit:
        return tuple(dict.fromkeys(explicit))

    source_repo = snapshot.get("source_repo") or snapshot.get("repo")
    repo_projects = snapshot.get("repo_projects") or snapshot.get("bindings") or {}
    if isinstance(repo_projects, dict) and isinstance(source_repo, str):
        bound = _strings(repo_projects.get(source_repo))
        if bound:
            return tuple(dict.fromkeys(bound))

    touched = _strings(snapshot.get("touched_repos") or snapshot.get("repos"))
    if len(touched) == 1 and isinstance(repo_projects, dict):
        bound = _strings(repo_projects.get(touched[0]))
        if bound:
            return tuple(dict.fromkeys(bound))
    return ()


def _semantic(value: str) -> str:
    """Normalize only surrounding whitespace; semantic bytes inside stay unchanged."""
    return value.strip()


def _group(slot: str, text: str, projects: tuple[str, ...], **fields: Any) -> ClaimGroup:
    return ClaimGroup(slot=slot, text=_semantic(text), project_candidates=projects,
                      **{key: _semantic(value) if isinstance(value, str) else value
                         for key, value in fields.items()})


def _payload_groups(payload: SourcePayload, projects: tuple[str, ...]) -> list[ClaimGroup]:
    kind = payload.kind
    if kind == "remember":
        return [_group("remember", payload.text, projects, evidence=payload.user_intent)]
    if kind == "correction":
        text = f"Wrong: {payload.wrong}\nRight: {payload.right}\nTrigger: {payload.trigger}"
        return [_group("correction", text, projects, why=payload.wrong, how=payload.right,
                       evidence=payload.trigger, type_hint="correction")]
    if kind == "distillation":
        return [_group(f"lesson-{hashlib.sha256(item.text.encode()).hexdigest()[:12]}", item.text,
                       projects, type_hint=item.type_hint) for item in payload.lessons]
    if kind == "daily_activity":
        return [_group(payload.activity_kind, payload.text, projects, why=payload.why,
                       type_hint="decision" if payload.activity_kind == "decision" else "correction")]
    if kind == "backflow":
        return [_group(f"message-{payload.message_id}", payload.body, projects, title=payload.title)]
    if kind == "extracted":
        return [_group("extracted", payload.text, projects, type_hint=payload.type_hint)]
    if kind == "evolution":
        text = f"Skill: {payload.skill}\nChange: {payload.change}\nResult: {payload.result}"
        return [_group("evolution", text, projects, how=payload.change, evidence=payload.result)]
    if kind == "reflect":
        return [_group(f"lesson-{hashlib.sha256(item.statement.encode()).hexdigest()[:12]}",
                       item.statement, projects, evidence=item.evidence, type_hint=item.kind,
                       why=item.relation_hint) for item in payload.lessons]
    if kind == "cultivation":
        p = payload.proposal
        return [_group("proposal", p.text, projects, why=p.why)]
    raise ValueError(f"unsupported payload kind: {kind}")


def normalize_candidate(candidate: Candidate) -> list[ClaimGroup]:
    """Parse one candidate into claim-groups without splitting attached context."""
    projects = resolve_project_candidates(candidate)
    return [group for group in _payload_groups(candidate.payload, projects) if group.text]


def normalize_batch(candidates: Iterable[Candidate]) -> dict[str, list[ClaimGroup]]:
    """Normalize a batch and mark later exact duplicates as merged."""
    result: dict[str, list[ClaimGroup]] = {}
    first_by_fingerprint: dict[str, str] = {}
    for candidate in candidates:
        groups: list[ClaimGroup] = []
        for group in normalize_candidate(candidate):
            unit_id = f"{candidate.id}.{group.slot}"
            prior = first_by_fingerprint.get(group.fingerprint)
            if prior is None:
                first_by_fingerprint[group.fingerprint] = unit_id
                groups.append(group)
            else:
                groups.append(ClaimGroup(**{**group.__dict__, "merged_into": prior}))
        result[candidate.id] = groups
    return result
