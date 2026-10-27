"""A project's repo documents, indexed in place for recall.

Design docs/design/2026-10-27-new-brain-initial-ddd.md §5.3. The documents a repo
carries (README, design records, ADRs, CONTRIBUTING, API contracts) are chunked into the
shared knowledge index under ``bind:<project>/<repo>/<path>`` — nothing is copied into
the brain, so the index cannot drift from the repo: each sync re-reads the repo at its
current HEAD, writes only changed chunks and drops the keys of files that are gone.

What gets indexed is the survey's document rule (``docs.is_doc`` / ``docs.api_format``)
over the repo's tracked files (at most ``docs._DOC_CAP``), read through
``repo_read.safe_read_text`` (inside the repo only, secret files never opened, nothing
larger than 2 MB), each scanned on its own by MemoryGuard first: secrets are redacted,
and a document that reads like a prompt injection is not indexed at all (it stays
listed in the survey, flagged, for a person to open).

Readers: recall's ``library`` domain, which returns these chunks only to a session of
this project and renders them in an untrusted block (``recall_multi._recall_library``,
``KnowledgeStore.fts5_search(bind_project=)``).

Writers that call this: ``ddd_bindings.bind_project`` (after a bind), the code-intel
reindex job (when a repo's HEAD moved) and ``artifact_cli survey``.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import docs as _docs
from . import repo_read as rr

logger = logging.getLogger(__name__)


def indexable(rel: str) -> bool:
    """A tracked file that is indexed in place: a document or an API contract."""
    return _docs.is_doc(rel) or _docs.api_format(rel) is not None


def _items(root: Path, files: list[str], stats: dict):
    from core.memory_guard import MemoryGuard
    guard = MemoryGuard()
    seen = 0
    for rel in files:
        if not indexable(rel):
            continue
        seen += 1
        if seen > _docs._DOC_CAP:
            stats["capped"] = True
            logger.warning("doc_index: more than %d documents; the rest are not indexed",
                           _docs._DOC_CAP)
            return
        text = rr.safe_read_text(root, rel)
        if not text:
            continue
        scan = guard.scan(text)
        if scan.rejected:
            stats["flagged"].append(rel)
            continue
        yield rel, scan.sanitized_content


def index_repo_docs(project: str, repo: str, root: str | Path,
                    files: list[str] | None = None) -> dict:
    """Delta-sync one repo's documents under ``bind:<project>/<repo>/``.

    Returns ``{"status": "indexed" | "unavailable", "written", "files", "removed"}``;
    ``unavailable`` when the knowledge database cannot be opened (nothing changed).
    """
    from core.knowledge_store import KnowledgeStore, bind_key_prefix, index_text_items
    from core.vec_db import open_vec_db

    root = Path(root).resolve()
    prefix = bind_key_prefix(project, repo)
    files = rr.list_files(root) if files is None else files
    extra: dict = {"flagged": [], "capped": False}
    with open_vec_db() as conn:
        if conn is None:
            return {"status": "unavailable", "written": 0, "files": 0, "removed": 0}
        store = KnowledgeStore(conn)
        store.ensure_tables()
        stats = index_text_items(store, prefix, _items(root, files, extra))
    logger.info("doc_index %s/%s: %d files, %d chunks written, %d removed, %d flagged",
                project, repo, stats["files"], stats["written"], stats["removed"],
                len(extra["flagged"]))
    return {"status": "indexed", **stats, **extra}


def prune_unowned(project: str, keep_repos: set[str]) -> int:
    """Remove the ``bind:<project>/<repo>/`` keys of repos the project no longer owns.
    Returns the number of files removed."""
    from core.knowledge_store import KnowledgeStore, bind_key_prefix
    from core.vec_db import open_vec_db

    prefix = bind_key_prefix(project)
    keep = {bind_key_prefix(project, r) for r in keep_repos}
    removed = 0
    with open_vec_db() as conn:
        if conn is None:
            return 0
        store = KnowledgeStore(conn)
        store.ensure_tables()
        for key in store.get_indexed_files():
            if key.startswith(prefix) and not any(key.startswith(k) for k in keep):
                store.remove_file_entries(key)
                removed += 1
    return removed


def sync_project(project_dir: str | Path, *, survey: bool = True) -> dict:
    """Index every repo *project_dir* owns, prune repos it no longer owns, and (with
    *survey*) rewrite its survey.json. The one entry point for "learn this project's
    repos" (bind, ``artifact_cli survey``).

    Each repo is handled on its own: one that fails is reported and the others still
    run. Returns ``{"repos": {name: index result | {"error"}}, "pruned", "survey"}``.
    """
    from .survey import owned_repos, survey_project

    project_dir = Path(project_dir)
    project = project_dir.name
    out: dict = {"repos": {}, "pruned": 0, "survey": None}
    repos = owned_repos(project_dir)
    for r in repos:
        try:
            out["repos"][r["name"]] = index_repo_docs(project, r["name"], r["root"])
        except Exception as exc:  # noqa: BLE001 — one repo must not stop the others
            logger.warning("doc_index %s/%s failed: %s: %s", project, r["name"],
                           type(exc).__name__, exc)
            out["repos"][r["name"]] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    try:
        out["pruned"] = prune_unowned(project, {r["name"] for r in repos})
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_index %s: prune failed: %s", project, exc)
    if survey:
        try:
            out["survey"] = str(Path(project_dir) / ".artifacts" / "bootstrap" / "survey.json")
            survey_project(project_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("survey %s failed: %s: %s", project, type(exc).__name__, exc)
            out["survey"] = None
            out["survey_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return out


def sync_repo_after_head_move(project_dir: str | Path, repo_root: str | Path,
                              changed_files: list[str] | None = None) -> dict | None:
    """After the reindex job processed a repo whose HEAD moved: re-index its documents
    and re-rank them in survey.json (identity, docs, api_contracts only — the rest of
    the survey is refreshed by the next full ``survey``). None when *repo_root* is not
    a repo this project owns.

    *changed_files* (the commit range's ``git diff --name-only``, deletions included)
    lets a commit that touched no document skip the work: ``{"skipped": ...}``. None
    (a full rebuild, range unknown) always syncs. The survey's ``identity.head`` is
    therefore the HEAD of the last document change, not of the last commit.
    """
    from .survey import owned_repos, refresh_repo_docs

    if changed_files is not None and not any(indexable(f) for f in changed_files):
        return {"skipped": "no document changed"}

    project_dir = Path(project_dir)
    try:
        root = str(Path(repo_root).resolve())
    except OSError:
        return None
    match = next((r for r in owned_repos(project_dir) if r["root"] == root), None)
    if match is None:
        return None
    files = rr.list_files(Path(root))
    result = index_repo_docs(project_dir.name, match["name"], root, files=files)
    result["survey_refreshed"] = refresh_repo_docs(project_dir, match["name"], root, files=files)
    return result
