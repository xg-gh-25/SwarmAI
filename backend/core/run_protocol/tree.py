"""Code-tree and delivery-tree identity for Pipeline v1 (v13 §A3.7).

Public symbols:
- ``build_code_trees`` — ask an injected tree writer for each repo's code tree.
- ``build_delivery_trees`` — add the renderer-owned REPORT to each code tree.
- ``check_lg3`` — prove delivery differs from code by exactly that REPORT.
- ``TreeError`` — malformed inputs or tree-writer output.

The module performs no git or filesystem I/O. Cycle 4 injects the real temporary-index
writer; cycle-1 tests inject deterministic fakes.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from core.run_protocol.identifiers import IdentifierError, validate_run_id

TreeWriter = Callable[[str, tuple[str, ...], Mapping[str, bytes]], str]
TreeDiffer = Callable[[str, str, str], list[dict[str, Any]]]
ReservedLister = Callable[[str, str], list[str]]
REPORT_TEMPLATE = ".aidlc/deliverables/{run_id}/REPORT.md"


class TreeError(ValueError):
    """Tree inputs or an injected tree operation violate the protocol."""


def report_path(run_id: str) -> str:
    """Return the only renderer-owned path allowed in a delivery tree."""
    try:
        run_id = validate_run_id(run_id)
    except IdentifierError as exc:
        raise TreeError(str(exc)) from exc
    return REPORT_TEMPLATE.format(run_id=run_id)


def _repos(repos: Mapping[str, str]) -> list[tuple[str, str]]:
    if not isinstance(repos, Mapping) or not repos:
        raise TreeError("repos must be a non-empty mapping")
    out = []
    for name, root in sorted(repos.items()):
        if not isinstance(name, str) or not name:
            raise TreeError("repo name must be non-empty")
        if not isinstance(root, str) or not root:
            raise TreeError(f"repo {name!r} root must be non-empty")
        out.append((name, root))
    return out


def _tree(value: Any, repo: str) -> str:
    if not isinstance(value, str) or len(value) != 40 or any(
        c not in "0123456789abcdef" for c in value
    ):
        raise TreeError(f"repo {repo!r} returned an invalid git tree id")
    return value


def build_code_trees(
    repos: Mapping[str, str], run_id: str, write_tree: TreeWriter,
    list_reserved: ReservedLister,
) -> dict[str, str]:
    """Build every repo's code tree, excluding this run's delivery path.

    ``write_tree(root, excludes, overlays)`` must behave like a temporary git index:
    add the complete worktree, exclude every listed repo-relative path, overlay the
    supplied bytes, then return ``git write-tree``. No caller may substitute a diff or
    the tracked-file set because untracked, non-ignored files belong to code_tree.
    """
    reserved = report_path(run_id).rsplit("/", 1)[0] + "/"
    items = {name: list_reserved(root, reserved) for name, root in _repos(repos)}
    dirty = {name: paths for name, paths in items.items() if paths}
    if dirty:
        raise TreeError(f"reserved delivery directory is not empty: {dirty}")
    excluded = (reserved,)
    return {
        name: _tree(write_tree(root, excluded, {}), name)
        for name, root in _repos(repos)
    }


def build_delivery_trees(
    repos: Mapping[str, str],
    run_id: str,
    code_trees: Mapping[str, str],
    report: bytes,
    report_sha: str,
    write_tree: TreeWriter,
    list_reserved: ReservedLister,
) -> dict[str, dict[str, str]]:
    """Build delivery trees by overlaying exactly the renderer's REPORT bytes.

    Returns per repo ``{code_tree, delivery_tree, report_path, report_sha}``; these are
    the values bound into ``land_intent`` and authorization. ``report_sha`` is supplied
    by the render event and must match the bytes before any tree writer runs.
    """
    import hashlib

    if not isinstance(report, bytes):
        raise TreeError("report must be bytes")
    actual = hashlib.sha256(report).hexdigest()
    if actual != report_sha:
        raise TreeError("report bytes do not match render event sha")
    expected_names = {name for name, _ in _repos(repos)}
    if set(code_trees) != expected_names:
        raise TreeError("code_trees must contain exactly the repos being delivered")
    path = report_path(run_id)
    reserved = path.rsplit("/", 1)[0] + "/"
    dirty = {name: list_reserved(root, reserved) for name, root in _repos(repos)}
    dirty = {name: paths for name, paths in dirty.items() if paths}
    if dirty:
        raise TreeError(f"reserved delivery directory is not empty: {dirty}")
    excluded = (reserved,)
    out: dict[str, dict[str, str]] = {}
    for name, root in _repos(repos):
        code = _tree(code_trees[name], name)
        delivery = _tree(write_tree(root, excluded, {path: report}), name)
        out[name] = {
            "code_tree": code, "delivery_tree": delivery,
            "report_path": path, "report_sha": report_sha,
        }
    return out


def check_lg3(
    *,
    repo: str,
    run_id: str,
    binding: Mapping[str, str],
    rendered_report_sha: str,
    diff_trees: TreeDiffer,
) -> tuple[bool, str]:
    """Check LG-3: delivery tree adds exactly one correctly rendered REPORT.

    ``diff_trees(repo, code_tree, delivery_tree)`` returns entries shaped as
    ``{status, path, blob_sha256?}``. The real adapter in cycle 4 obtains status/path
    from git and hashes the report blob. Renames, deletes, mode-only edits, another
    file, or a report whose bytes do not match the render event all fail.
    """
    required = {"code_tree", "delivery_tree", "report_path", "report_sha"}
    if not isinstance(binding, Mapping) or not required <= set(binding):
        return False, "LG-3: incomplete tree binding"
    try:
        code = _tree(binding["code_tree"], repo)
        delivery = _tree(binding["delivery_tree"], repo)
        expected_path = report_path(run_id)
    except TreeError as exc:
        return False, f"LG-3: {exc}"
    if binding["report_path"] != expected_path:
        return False, "LG-3: report path is not this run's reserved REPORT.md"
    if binding["report_sha"] != rendered_report_sha:
        return False, "LG-3: report sha differs from the render event"
    try:
        changes = diff_trees(repo, code, delivery)
    except Exception as exc:  # adapter failure is a failed guard, never a pass
        return False, f"LG-3: tree diff failed ({type(exc).__name__})"
    if not isinstance(changes, list) or len(changes) != 1:
        return False, f"LG-3: expected one REPORT change, got {len(changes) if isinstance(changes, list) else 'invalid'}"
    change = changes[0]
    if not isinstance(change, Mapping):
        return False, "LG-3: invalid tree-diff entry"
    if change.get("status") != "A" or change.get("path") != expected_path:
        return False, "LG-3: delivery differs by something other than one added REPORT"
    if change.get("blob_sha256") != rendered_report_sha:
        return False, "LG-3: REPORT blob differs from the render event"
    return True, "LG-3: delivery tree adds exactly the rendered REPORT"


def check_all_lg3(
    *,
    repos: Mapping[str, str],
    run_id: str,
    bindings: Mapping[str, Mapping[str, str]],
    rendered_report_sha: str,
    diff_trees: TreeDiffer,
) -> tuple[bool, dict[str, str]]:
    """Evaluate LG-3 independently for every repo in a multi-repo delivery."""
    names = {name for name, _ in _repos(repos)}
    if set(bindings) != names:
        return False, {"*": "LG-3: bindings must contain exactly every delivered repo"}
    details = {
        name: check_lg3(
            repo=root, run_id=run_id, binding=bindings[name],
            rendered_report_sha=rendered_report_sha, diff_trees=diff_trees,
        )
        for name, root in _repos(repos)
    }
    return all(ok for ok, _ in details.values()), {
        name: reason for name, (_, reason) in details.items()
    }
