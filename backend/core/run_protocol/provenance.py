"""Register Pipeline run artifacts as CDF v2 evidence roots.

- ``sync_run_evidence`` idempotently scans one verified run log at landing/REFLECT.
- ``run_roots`` returns stable evidence anchors available to the S3 extractor.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping

from core.ingestion.api_v2 import register_root
from core.ingestion.store import connect_ro

logger = logging.getLogger(__name__)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _value(store: Any, value: Any) -> Any:
    if isinstance(value, Mapping) and set(value) == {"blob"}:
        return store.read_blob(str(value["blob"])).decode("utf-8")
    return value


def _context_root(run_id: str) -> str:
    return register_root(
        "tool_text", f"{run_id}:repo/tool context", "repo/tool context", "external")


def _selected_option(store: Any, decisions: Mapping[str, Mapping[str, Any]],
                     request_id: str, option_id: str) -> str | None:
    event = decisions.get(request_id)
    if event is None:
        return None
    raw = _value(store, event.get("payload", {}).get("decision_json"))
    try:
        document = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(document, Mapping):
        return None
    for option in document.get("options") or ():
        if isinstance(option, Mapping) and str(option.get("id")) == option_id:
            return _canonical(dict(option))
    return None


def sync_run_evidence(store: Any, run_dir: str | Path | None = None) -> dict[str, str]:
    """Idempotently register every supported evidence artifact currently in a run."""
    run_id = str(store.paths.run_id)
    events = store.read_events()
    roots: dict[str, str] = {}
    decisions = {
        str(event.get("payload", {}).get("request_id")): event for event in events
        if event.get("kind") == "decision"
    }
    answers = {
        str(event.get("event_id")): event for event in events if event.get("kind") == "answer"
    }
    finding_status: dict[str, str] = {}
    for event in events:
        payload = event.get("payload") or {}
        if event.get("kind") == "finding_status":
            finding_status[str(event.get("ckey"))] = str(payload.get("status"))

    for event in events:
        kind = event.get("kind")
        payload = event.get("payload") or {}
        event_id = str(event.get("event_id") or "")
        if kind == "finding_occ":
            finding_id = str(event.get("ckey"))
            text = _canonical({
                "claim": _value(store, payload.get("claim")),
                "fix": _value(store, payload.get("fix")),
                "file": payload.get("file"), "line": payload.get("line"),
                "severity": payload.get("severity"), "kind": payload.get("finding_kind"),
            })
            root = register_root(
                "run_finding", f"{run_id}:{finding_id}", text, "external",
                observed_at=event.get("at"))
            roots[f"finding:{run_id}:{finding_id}"] = root
            answer = answers.get(str(payload.get("answer_event_id")))
            verdict = finding_status.get(finding_id)
            if verdict is None and answer is not None:
                verdict = str(answer.get("payload", {}).get("verdict") or "OPEN")
            verdict = verdict or "OPEN"
            verdict_root = register_root(
                "measured_field", f"{run_id}:{finding_id}:verdict", verdict, "measured",
                observed_at=event.get("at"))
            roots[f"run_event:{run_id}:{finding_id}:verdict"] = verdict_root
        elif kind == "human_answer":
            answer = {"request_id": payload.get("request_id"), "option_id": payload.get("option_id")}
            root = register_root(
                "human_answer", f"{run_id}:{event_id}", _canonical(answer), "user",
                observed_at=event.get("at"))
            roots[f"run_event:{run_id}:{event_id}"] = root
            option = _selected_option(
                store, decisions, str(payload.get("request_id")), str(payload.get("option_id")))
            if option is not None:
                option_root = register_root(
                    "agent_document",
                    f"{run_id}:decision-option:{payload.get('request_id')}:{payload.get('option_id')}",
                    option, "external", parent_root_ids=(_context_root(run_id),),
                    observed_at=event.get("at"))
                roots[f"doc:{option_root}"] = option_root
        elif kind == "validation_report":
            measured = {
                key: payload.get(key) for key in
                ("stage", "repo", "code_tree", "n_pass", "n_fail", "n_not_run")
            }
            root = register_root(
                "run_validation", f"{run_id}:{event_id}", _canonical(measured), "measured",
                observed_at=event.get("at"))
            roots[f"run_event:{run_id}:{event_id}"] = root
        elif kind in {"land", "partial_land"}:
            commits = payload.get("commits") if kind == "land" else payload.get("landed")
            commits = commits if isinstance(commits, Mapping) else {}
            if not commits and payload.get("commit"):
                commits = {"primary": payload["commit"]}
            for repo, sha in sorted(commits.items()):
                if not isinstance(sha, str) or not sha:
                    continue
                root = register_root(
                    "commit", f"{run_id}:{sha}", sha, "external",
                    observed_at=event.get("at"))
                roots[f"commit:{sha}"] = root

    if run_dir is not None:
        parent = _context_root(run_id)
        directory = Path(run_dir)
        for name in ("REQUIREMENTS.md", "DESIGN_SPEC.md"):
            path = directory / name
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            root = register_root(
                "agent_document", f"{run_id}:doc:{name}", content, "external",
                parent_root_ids=(parent,))
            roots[f"doc:{root}"] = root
    return roots


def run_roots(run_id: str) -> dict[str, str]:
    """Return every registered S3 anchor for ``run_id`` from the provenance store."""
    prefix = f"{run_id}:"
    try:
        conn = connect_ro()
    except (FileNotFoundError, OSError):
        return {}
    try:
        rows = list(conn.execute(
            "SELECT root_id,root_kind,locator FROM evidence_roots WHERE locator LIKE ?",
            (prefix + "%",)))
    finally:
        conn.close()
    out: dict[str, str] = {}
    for root_id, kind, locator in rows:
        suffix = str(locator)[len(prefix):]
        if kind == "run_finding":
            out[f"finding:{run_id}:{suffix}"] = str(root_id)
        elif kind in {"run_validation", "human_answer"}:
            out[f"run_event:{run_id}:{suffix}"] = str(root_id)
        elif kind == "commit":
            out[f"commit:{suffix}"] = str(root_id)
        elif kind == "measured_field" and suffix.endswith(":verdict"):
            out[f"run_event:{run_id}:{suffix}"] = str(root_id)
        elif kind == "agent_document":
            out[f"doc:{root_id}"] = str(root_id)
    return out
