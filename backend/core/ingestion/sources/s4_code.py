"""S4 code-change source for commits not landed by a pipeline run.

The adapter accepts complete commit records, excludes run-owned SHAs before the LLM
call, and emits taint-preserving Cognition or Event units through api_v2 only.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Iterable

from core.ingestion import api_v2

from . import common
from .extract_llm import EXTRACTOR_VERSION

_ALLOWED = ("cognition", "event")




def landed_commit_shas(workspace: Any) -> set[str]:
    """Read landed commit SHAs from immutable run audit projections."""
    from pathlib import Path
    shas: set[str] = set()
    root = Path(workspace) / "Projects"
    for path in root.glob("*/.artifacts/runs/*/run_audit.json") if root.is_dir() else ():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            shas.update(str(sha) for sha in
                        ((value.get("details") or {}).get("commits") or {}).values()
                        if sha)
        except (OSError, ValueError, AttributeError):
            continue
    return shas


def _commit_sha(row: dict[str, Any]) -> str:
    return str(row.get("sha") or row.get("commit") or "")


def _record_empty(commits: list[str], project_hint: str | None) -> None:
    attempt = api_v2.ExtractionAttempt(
        source="S4", evidence_range={"commits": commits, "project": project_hint},
        extractor_version=EXTRACTOR_VERSION,
        attempt_id=f"attempt:{uuid.uuid4().hex}")
    api_v2.record_extraction_attempt(attempt, status="no_unit")


def extract_commits(commits: Iterable[dict[str, Any]], *,
                    workspace: Any | None = None,
                    run_landed_commits: set[str] | None = None,
                    project_hint: str | None = None,
                    project_snapshot: dict[str, Any] | None = None,
                    llm: Any | None = None,
                    root_ids: tuple[str, ...] | None = None) -> list[Any]:
    """Extract one scheduled batch after deterministically removing run commits."""
    rows = [dict(row) for row in commits]
    owned = run_landed_commits if run_landed_commits is not None else landed_commit_shas(workspace)
    selected = [row for row in rows
                if _commit_sha(row) and _commit_sha(row) not in owned]
    shas = [_commit_sha(row) for row in selected]
    if not selected:
        _record_empty([_commit_sha(row) for row in rows], project_hint)
        return []
    anchors = tuple(f"commit:{sha}" for sha in shas)
    commit_root_ids: list[str] = []
    for row, sha in zip(selected, shas):
        repository = str(row.get("repository") or row.get("repo") or "").strip()
        if not repository:
            raise ValueError(f"S4 commit {sha} is missing repository identity")
        record = str(row.get("record") or row.get("content") or "")
        commit_root_ids.append(api_v2.register_root(
            "commit", f"repo:{repository}:{sha}", record, "external"
        ))
    read_roots = tuple(dict.fromkeys((*commit_root_ids, *(root_ids or ()))))
    snapshot = dict(project_snapshot or {})
    if project_hint:
        snapshot.setdefault("project", project_hint)
    return common.extract_and_enqueue(
        "S4", ",".join(shas), json.dumps(selected, ensure_ascii=False, default=str), anchors,
        evidence_range={"commits": shas, "project": project_hint},
        read_root_ids=read_roots,
        project_snapshot=snapshot, extraction_pass="main",
        allowed_kinds=_ALLOWED, llm=llm)


def enqueue_refine_unit(unit: dict[str, Any], *, source_ref: str,
                        project: str, read_root_ids: tuple[str, ...] = ()) -> Any:
    """Validate and enqueue one deterministic DDD refinement as S4."""
    from core.ingestion.contracts_v2 import SOURCE_UNIT
    typed = SOURCE_UNIT.validate_python(unit)
    attempt = api_v2.ExtractionAttempt(
        source="S4", evidence_range={"ddd_refine": source_ref, "project": project},
        extractor_version=EXTRACTOR_VERSION,
        attempt_id=f"attempt:{uuid.uuid4().hex}")
    try:
        outcome = api_v2.enqueue_unit(
            "S4", source_ref, typed, session_id=None, run_id=None,
            read_root_ids=read_root_ids, project_snapshot={"project": project},
            attempt_id=attempt.attempt_id, extraction_pass="main")
        api_v2.record_extraction_attempt(attempt, status="ok", units_emitted=1)
        return outcome
    except Exception as exc:
        api_v2.record_extraction_attempt(
            attempt, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
