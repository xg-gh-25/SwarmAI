"""The facts a pipeline run's TEST is decided from, and the REPORT's Feature checks lines.

The decision never rests on the agent's report row (Brain-check rows are the agent's own
and never required). At TEST the engine computes, per repo with a feature folder:

- the **reached** features, with the same ``reach.affected`` the CLI uses, from the
  run's own changed paths;
- for each, the ledger lines this run's replay wrote **at the run's current code tree**
  (``run`` = run id, ``code_tree`` = the engine's tree for the worktree, which the CLI
  records from the same engine function);
- ``pre_existing``: a walk that failed at head and also failed in the run's base replay
  (``trigger: base``);
- ``missing``: a reached feature with no line at the current tree (not replayed, or
  replayed before the last edit): listed, never blocking;
- the **changed expectations**: lines of the run's diff of the feature folder that add,
  remove or change an ``expect(`` call or a ``reach`` / ``expect`` field — computed from
  the diff, never reported by the agent.

The engine records this as one ``feature_checks`` event; check FM-1 reads it. With
``blocking`` on (the ``feature_checks_block`` setting when it was recorded) TEST stays red
for a failed reached walk, for a reached feature not replayed on this tree (a missing
replay is the agent's to run; an environment problem is a ``not_run`` line, never a
missing one), and for a feature folder this run made invalid. Off, all of it is listed.

What the gate guards against is a missed or mistaken replay. The ledger is a local file:
an agent with a shell could write lines into it as it could edit any local record, so
the gate does not stand against a hostile agent.

A feature folder in the Brain home is not in the run's diff: its files modified since
the run was created (``brain_folder_changes``) count as changed folder files, for reach
and for the changed expectations (listed by file, as no diff is kept for them).

Exports: facts, failures, unreplayed, brain_folder_changes, report_lines, evidence_rows.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.feature_map import files, ledger, reach

# a spec's assertion, or a feature entry's reach / expect field (block or inline style)
_EXPECT_LINE = re.compile(r"\bexpect\s*\(|\b(expect|reach)\s*:")
MAX_EXPECTATION_LINES = 60
MAX_BRAIN_CHANGES = 200


def brain_folder_changes(folder: Path, since: float | None) -> list[str]:
    """Folder-relative paths of the files under a Brain-home feature folder modified at or
    after *since* (the run's creation, epoch seconds); [] without *since*."""
    if since is None:
        return []
    out = []
    folder = Path(folder)
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d != "node_modules" and not d.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                if path.stat().st_mtime >= since:
                    out.append(path.relative_to(folder).as_posix())
            except OSError:
                continue
    return sorted(out)[:MAX_BRAIN_CHANGES]


def _changed_expectations(root: Path, base: str, tree: str, folder_rel: str) -> list[dict]:
    try:
        diff = subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=off", "diff",
                               "--unified=0", "--no-renames", base, tree, "--", folder_rel],
                              capture_output=True, text=True, check=True, timeout=120).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    out, current = [], ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else current
        elif line.startswith("--- a/"):
            current = line[6:]
        elif line[:1] in "+-" and not line.startswith(("+++", "---")):
            if _EXPECT_LINE.search(line[1:]):
                out.append({"file": current, "change": line[:1],
                            "line": line[1:].strip()[:240]})
        if len(out) >= MAX_EXPECTATION_LINES:
            break
    return out


def facts(*, project_dir: Path | None, project: str, repo: str, root: Path, base: str,
          code_tree: str, run_id: str, changed: Sequence[str], blocking: bool,
          since: float | None = None) -> dict | None:
    """The ``feature_checks`` payload for one repo, or None when it has no feature folder.
    *since*: the run's creation (epoch seconds), for a Brain-home folder's own changes."""
    folder, mode = files.location(project_dir, repo, Path(root))
    if folder is None:
        return None
    payload: dict[str, Any] = {"repo": repo, "mode": mode, "blocking": bool(blocking),
                               "code_tree": code_tree, "reached": [], "rows": [],
                               "missing": [], "unplaced": [], "changed_expectations": [],
                               "error": "", "error_blocks": False}
    folder_rel = (folder.resolve().relative_to(Path(root).resolve()).as_posix()
                  if mode == "repo" else None)
    brain_changes = brain_folder_changes(folder, since) if mode == "brain" else []
    try:
        fmap = files.load_folder(folder)
    except files.FeatureMapError as exc:
        payload["error"] = str(exc)[:600]
        # this run made it invalid (it changed the folder): blocking; else listed
        payload["error_blocks"] = bool(brain_changes) or bool(folder_rel) and any(
            p == folder_rel or p.startswith(folder_rel + "/") for p in changed)
        return payload
    aff = reach.affected(fmap, changed, folder_rel=folder_rel, folder_changes=brain_changes)
    payload["reached"] = list(aff["features"])
    payload["unplaced"] = list(aff["unplaced"])[:50]
    rows = ledger.read(project, repo)
    mine = [r for r in rows if r.get("run") == run_id]
    for fid in aff["features"]:
        feature = fmap.feature(fid)
        h = files.content_hash(fmap, feature)
        at_tree = [r for r in mine if r.get("feature") == fid and r.get("code_tree") == code_tree
                   and r.get("trigger") in ("replay", "verify")]
        if not at_tree:
            keys = {r.get("key") for r in rows if r.get("feature") == fid and r.get("hash") == h}
            quarantined = any(ledger.quarantined(rows, key, h) for key in keys if key)
            payload["missing"].append({"feature": fid, "quarantined": quarantined})
            continue
        latest: dict[str, dict] = {}
        for r in at_tree:
            latest[str(r.get("key"))] = r
        base_fail = {str(r.get("key")) for r in mine if r.get("feature") == fid
                     and r.get("trigger") == "base" and r.get("result") == "fail"}
        for key, r in sorted(latest.items()):
            payload["rows"].append({
                "feature": fid, "walk": r.get("walk"), "project": r.get("project"),
                "result": r.get("result"), "reason": r.get("reason") or "",
                "error": (r.get("error") or "")[:400],
                "pre_existing": r.get("result") == "fail" and key in base_fail,
                "quarantined": ledger.quarantined(rows, key, h),
                "evidence": list(r.get("evidence_files") or [])[:6]})
    if folder_rel:
        payload["changed_expectations"] = _changed_expectations(Path(root), base, code_tree, folder_rel)
    elif brain_changes:
        payload["changed_expectations"] = [
            {"file": rel, "change": "~",
             "line": "changed during this run (a Brain-home feature folder keeps no diff)"}
            for rel in brain_changes[:MAX_EXPECTATION_LINES]]
    return payload


def failures(payload: Mapping[str, Any]) -> list[dict]:
    """The rows that keep TEST red: failed at the current tree, not red at base too,
    not quarantined."""
    return [r for r in payload.get("rows") or [] if r.get("result") == "fail"
            and not r.get("pre_existing") and not r.get("quarantined")]


def unreplayed(payload: Mapping[str, Any]) -> list[dict]:
    """The reached features with no line at the current tree that keep TEST red: not
    replayed since the last edit (a quarantined one is listed only)."""
    return [m for m in payload.get("missing") or [] if not m.get("quarantined")]


def report_lines(facts_list: Sequence[Mapping[str, Any]]) -> list[str]:
    """The REPORT's Feature checks section, from the latest facts of each repo (the
    ``feature_checks`` events' records, read by the engine). Every word that came from a
    spec, a page, the ledger or the diff is untrusted: it goes through the REPORT's own
    escaper (``audit._text``, K4 r1), so it brings no link, image, HTML or heading."""
    from core.run_protocol.audit import _text

    def _lead(value: Any, limit: int) -> str:
        """An untrusted value that opens a list item: a leading ``#`` would make the
        item a heading (``_text`` leaves ``#`` alone), so it is escaped too."""
        out = _text(value, limit)
        return "\\" + out if out.startswith("#") else out
    latest = {str(p.get("repo")): p for p in facts_list}
    if not latest:
        return []
    lines = ["## Feature checks", ""]
    for repo, p in sorted(latest.items()):
        mode = "" if p.get("blocking", True) else " (blocking off: listed only)"
        lines.append(f"**{_text(repo, 120)}**{mode}:")      # never a line-leading heading
        if p.get("error"):
            lines.append(f"- the feature checks could not be decided: {_text(p['error'], 600)}")
        rows = p.get("rows") or []
        for r in rows:
            mark = {"pass": "✓", "fail": "✗", "flaky": "~", "not_run": "–"}.get(r.get("result"), "?")
            extra = (" (red at the run's base too)" if r.get("pre_existing") else "") + \
                    (" (quarantined)" if r.get("quarantined") else "")
            why = f": {_text(r['reason'], 200)}" if r.get("reason") else (
                f": {_text(r['error'].splitlines()[0], 200)}" if r.get("error") else "")
            lines.append(f"- {mark} {_text(r.get('feature'), 120)} · {_text(r.get('walk'), 200)} "
                         f"[{_text(r.get('project'), 40)}] {_text(r.get('result'), 20)}{extra}{why}")
            for path in r.get("evidence") or []:
                lines.append(f"  - {_lead(path, 400)}")
        for m in p.get("missing") or []:
            lines.append(f"- – {_text(m['feature'], 120)}: feature check not run on this tree"
                         + (" (quarantined)" if m.get("quarantined") else ""))
        if not rows and not p.get("missing"):
            lines.append("- no feature is reached by this change")
        if p.get("unplaced"):
            lines.append("- changed paths under no target: "
                         + ", ".join(_text(u, 200) for u in p["unplaced"][:10]))
        if p.get("changed_expectations"):
            lines.append("- expected results changed by this run:")
            lines += [f"  - {_lead(c['file'], 200)}: {_text(c['change'], 2)} {_text(c['line'], 240)}"
                      for c in p["changed_expectations"][:20]]
        lines.append("")
    return lines


def evidence_rows(payload: Mapping[str, Any]) -> list[dict]:
    """``[{feature, walk, project, result, files: [...]}]`` for the run card."""
    return [{"feature": r.get("feature"), "walk": r.get("walk"), "project": r.get("project"),
             "result": r.get("result"), "pre_existing": bool(r.get("pre_existing")),
             "files": list(r.get("evidence") or [])} for r in payload.get("rows") or []]
