"""Keep the checked outcome of each native expert review, and count it per expert.

A review expert's finding is a lead: the agent checks it against the code and decides
whether it is real or a false positive (AGENT.md R2, s_expert-review §3). That decision
is the label a later replay of the experts needs (does a changed prompt or model still
find the real ones, and stop raising the false ones?), and it is otherwise lost when the
session ends. A pipeline run already keeps it in its event log; this keeps it for a
native review.

One review is one JSON line in ``<app data>/state/expert_verdicts/verdicts.jsonl``:
the checkout's top level and its base commit, the change reviewed (its patch, stored
once by content hash under ``patches/``), and for every expert called its prompt file's
hash and each finding with the agent's verdict. An expert that found nothing is recorded
too: if a defect turns up later in that change, that is a miss.

- ``snapshot`` — store the change before the experts read it and print its id. The
  findings are fixed before they are recorded, so the code the experts saw exists only
  until then; a record names it by this id (``patch_at: review``). A record without one
  takes the code as it is when it is recorded (``patch_at: record``), fixes included.
- ``record`` — validate one review document and append it. Nothing is written when any
  part is invalid.
- ``stats`` — per expert and prompt version: reviews, findings, real, false positives,
  unverified, and the share of checked findings that were real.

Usage (from backend/, or anywhere through the backend binary):
    "$SWARM_BACKEND_BIN" py -m scripts.expert_verdicts snapshot --repo <checkout> [--base <ref>]
    "$SWARM_BACKEND_BIN" py -m scripts.expert_verdicts record --file review.json
    "$SWARM_BACKEND_BIN" py -m scripts.expert_verdicts record --file -   # stdin
    "$SWARM_BACKEND_BIN" py -m scripts.expert_verdicts stats [--json]

The review document (``model`` and ``goal`` are optional; without ``snapshot`` it names
``repo`` and optionally ``base``, default HEAD)::

    {"snapshot": "<id the snapshot command printed>", "goal": "<one sentence>",
     "model": "<the model the experts ran on>",
     "experts": [{"expert": "correctness", "prompt_file": "/abs/path/correctness.md",
                  "findings": [{"severity": "HIGH", "file": "src/a.py", "line": 12,
                                "claim": "<what breaks>", "verdict": "real",
                                "evidence": "<test command, or the path:line that shows it>"}]}]}

``severity`` is CRITICAL, HIGH, MEDIUM (or MED) or LOW. ``verdict`` is ``real`` (it
breaks as claimed), ``false_positive`` (the code shows it does not) or ``unverified``
(not checked). A real or false-positive verdict needs its evidence; an unverified one
does not count in the real share.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = 1
VERDICTS = ("real", "false_positive", "unverified")
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
_MAX_TEXT = 4000
_MAX_FINDINGS = 200
_GIT_TIMEOUT = 60
_MAX_UNTRACKED_BYTES = 1_000_000
_MAX_UNTRACKED_FILES = 200
_MAX_PATCH_BYTES = 5_000_000
# git's plain patch format, whatever the user's color / prefix / external-diff settings
_DIFF_FLAGS = ("--binary", "--no-color", "--no-ext-diff", "--no-textconv", "--no-relative",
               "--src-prefix=a/", "--dst-prefix=b/")
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_SNAPSHOT_ID = re.compile(r"[0-9a-f]{16}")
# the expert prompts grade with MED; it is MEDIUM here
_SEVERITY_ALIAS = {"MED": "MEDIUM"}


class ReviewInvalid(ValueError):
    """The review document cannot be recorded; the message says which part and why."""


def store_dir() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "state" / "expert_verdicts"


def _text(value: Any, where: str, *, required: bool = True) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ReviewInvalid(f"{where} is required")
        return ""
    if not isinstance(value, str):
        raise ReviewInvalid(f"{where} must be text")
    return value.strip()[:_MAX_TEXT]


def _finding(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ReviewInvalid(f"{where} must be an object")
    severity = _text(raw.get("severity"), f"{where}.severity").upper()
    severity = _SEVERITY_ALIAS.get(severity, severity)
    if severity not in SEVERITIES:
        raise ReviewInvalid(f"{where}.severity must be one of {', '.join(SEVERITIES)}")
    verdict = _text(raw.get("verdict"), f"{where}.verdict")
    if verdict not in VERDICTS:
        raise ReviewInvalid(f"{where}.verdict must be one of {', '.join(VERDICTS)}")
    line = raw.get("line")
    if line is not None and (isinstance(line, bool) or not isinstance(line, int) or line < 0):
        raise ReviewInvalid(f"{where}.line must be a line number")
    evidence = _text(raw.get("evidence"), f"{where}.evidence", required=verdict != "unverified")
    return {"severity": severity, "file": _text(raw.get("file"), f"{where}.file", required=False),
            "line": line, "claim": _text(raw.get("claim"), f"{where}.claim"),
            "verdict": verdict, "evidence": evidence}


def _expert(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ReviewInvalid(f"{where} must be an object")
    name = _text(raw.get("expert"), f"{where}.expert")
    prompt_file = Path(_text(raw.get("prompt_file"), f"{where}.prompt_file")).expanduser()
    try:
        prompt = prompt_file.read_bytes()
    except OSError as exc:
        raise ReviewInvalid(f"{where}.prompt_file cannot be read: {exc.strerror or exc}") from exc
    findings = raw.get("findings", [])
    if not isinstance(findings, list) or len(findings) > _MAX_FINDINGS:
        raise ReviewInvalid(f"{where}.findings must be a list of at most {_MAX_FINDINGS}")
    return {"expert": name, "prompt_file": prompt_file.name,
            "prompt_sha": hashlib.sha256(prompt).hexdigest(),
            "findings": [_finding(f, f"{where}.findings[{i}]") for i, f in enumerate(findings)]}


def _git(repo: Path, *args: str) -> "subprocess.CompletedProcess[bytes]":
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              timeout=_GIT_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ReviewInvalid(f"git {args[0]} failed in {repo}: {exc}") from exc


def _change(repo: Path, base: str | None) -> dict[str, Any]:
    """The change in *repo* against *base* (default HEAD): ``{repo, base, patch, skipped}``.

    ``repo`` is the checkout's top level, so the tracked and the untracked parts of the
    patch share one path base. The patch is the tracked diff plus each untracked file as
    a new-file diff (a native change is often uncommitted), in git's plain format whatever
    the user's diff settings, so it applies again on a replay. Untracked files past the
    caps are named in ``skipped``. A directory that is not a git checkout gives
    ``base: None`` and an empty patch; a checkout with no commit yet gives ``base: None``
    and a patch of every file, diffed against the empty tree."""
    top = _git(repo, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return {"repo": str(repo), "base": None, "patch": b"", "skipped": []}
    root = Path(top.stdout.decode().strip()).resolve()
    head = _git(root, "rev-parse", "--verify", "--quiet", f"{base or 'HEAD'}^{{commit}}")
    if head.returncode == 0:
        commit: str | None = head.stdout.decode().strip()
        against = commit
    elif base is None and _git(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode != 0:
        commit, against = None, _EMPTY_TREE  # no commit yet: everything is new
    else:
        raise ReviewInvalid(f"base {base or 'HEAD'!r} is not a commit in {root}")
    patch = _git(root, "diff", *_DIFF_FLAGS, against).stdout
    skipped: list[str] = []
    included = 0
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z").stdout
    for name in sorted(n for n in untracked.decode(errors="replace").split("\0") if n):
        try:
            size = (root / name).stat().st_size
        except OSError:
            continue
        if (size > _MAX_UNTRACKED_BYTES or len(patch) + size > _MAX_PATCH_BYTES
                or included >= _MAX_UNTRACKED_FILES):
            skipped.append(name)  # stray large or unignored output, not the reviewed change
            continue
        included += 1
        # --no-index exits 1 when the files differ, which they always do here
        patch += _git(root, "diff", *_DIFF_FLAGS, "--no-index", "--", "/dev/null", name).stdout
    return {"repo": str(root), "base": commit, "patch": patch, "skipped": skipped}


def _store_patch(root: Path, patch: bytes) -> str | None:
    """Store *patch* once under its content hash; returns the hash, None for no patch."""
    if not patch:
        return None
    sha = hashlib.sha256(patch).hexdigest()
    target = root / "patches" / f"{sha}.patch"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(patch)
        tmp.replace(target)
    return sha


def snapshot(repo: str, base: str | None = None) -> dict[str, Any]:
    """Store the change the experts are about to review; returns ``{snapshot, repo, base,
    patch_sha, skipped}``. Taken before any finding is fixed, so the record names the code
    the experts saw, not the fixed code."""
    path = Path(repo).expanduser()
    if not path.is_dir():
        raise ReviewInvalid(f"repo {path} is not a directory")
    ch = _change(path.resolve(), base)
    root = store_dir()
    patch_sha = _store_patch(root, ch["patch"])
    snap_id = hashlib.sha256(f"{ch['repo']}\0{ch['base']}\0{patch_sha}".encode()).hexdigest()[:16]
    snap = {"snapshot": snap_id, "repo": ch["repo"], "base": ch["base"], "patch_sha": patch_sha,
            "skipped": ch["skipped"], "at": int(time.time())}
    target = root / "snapshots" / f"{snap_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snap, sort_keys=True), encoding="utf-8")
    return snap


def _load_snapshot(snap_id: str) -> dict[str, Any]:
    if not _SNAPSHOT_ID.fullmatch(snap_id):
        raise ReviewInvalid("snapshot must be the id the snapshot command printed")
    try:
        snap = json.loads((store_dir() / "snapshots" / f"{snap_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReviewInvalid(f"snapshot {snap_id} cannot be read: {exc}") from exc
    if not isinstance(snap, dict) or not isinstance(snap.get("repo"), str) \
            or not all(k in snap for k in ("base", "patch_sha")):
        raise ReviewInvalid(f"snapshot {snap_id} is damaged: take a new one")
    sha = snap["patch_sha"]
    if sha is not None and not (store_dir() / "patches" / f"{sha}.patch").is_file():
        raise ReviewInvalid(f"snapshot {snap_id} names a patch that is gone: take a new one")
    return snap


def prepare(doc: Any) -> tuple[dict[str, Any], bytes]:
    """The record to append and the patch to store (empty when a snapshot holds it), or
    ReviewInvalid."""
    if not isinstance(doc, dict):
        raise ReviewInvalid("the review must be a JSON object")
    experts = doc.get("experts")
    if not isinstance(experts, list) or not experts:
        raise ReviewInvalid("experts must list every expert called, at least one")
    rows = [_expert(e, f"experts[{i}]") for i, e in enumerate(experts)]
    snap_id = _text(doc.get("snapshot"), "snapshot", required=False)
    if snap_id:
        snap = _load_snapshot(snap_id)
        change = {"repo": snap["repo"], "base": snap["base"], "patch_sha": snap["patch_sha"],
                  "skipped": snap.get("skipped") or [], "patch_at": "review"}
        patch = b""
    else:
        repo = Path(_text(doc.get("repo"), "repo")).expanduser()
        if not repo.is_dir():
            raise ReviewInvalid(f"repo {repo} is not a directory")
        ch = _change(repo.resolve(), _text(doc.get("base"), "base", required=False) or None)
        patch = ch["patch"]
        # taken now, after the fixes: a replay must not read it as the reviewed code
        change = {"repo": ch["repo"], "base": ch["base"],
                  "patch_sha": hashlib.sha256(patch).hexdigest() if patch else None,
                  "skipped": ch["skipped"], "patch_at": "record"}
    record = {"schema": SCHEMA, "review_id": uuid.uuid4().hex, "at": int(time.time()), **change,
              "goal": _text(doc.get("goal"), "goal", required=False),
              "model": _text(doc.get("model"), "model", required=False), "experts": rows}
    return record, patch


def record(doc: Any) -> dict[str, Any]:
    """Validate *doc* and append it; returns the stored record."""
    from utils.file_lock import flock_exclusive, flock_unlock
    rec, patch = prepare(doc)
    root = store_dir()
    root.mkdir(parents=True, exist_ok=True)
    _store_patch(root, patch)
    with open(root / ".lock", "w") as lock:
        flock_exclusive(lock)
        try:
            with open(root / "verdicts.jsonl", "a", encoding="utf-8") as out:
                out.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        finally:
            flock_unlock(lock)
    return rec


def read_reviews(path: Path | None = None) -> list[dict[str, Any]]:
    """Every recorded review; a line that does not parse is skipped."""
    path = path or store_dir() / "verdicts.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get("experts"), list):
            out.append(value)
    return out


def stats(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (expert, prompt version), most reviews first. ``real_share`` is real /
    (real + false_positive), None when nothing was checked."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for rev in reviews:
        for e in rev["experts"]:
            if not isinstance(e, dict):
                continue
            key = (str(e.get("expert")), str(e.get("prompt_sha"))[:12])
            row = rows.setdefault(key, {"expert": key[0], "prompt": key[1], "reviews": 0,
                                        "findings": 0, **{v: 0 for v in VERDICTS}})
            row["reviews"] += 1
            findings = e.get("findings")
            for f in findings if isinstance(findings, list) else []:
                if isinstance(f, dict) and f.get("verdict") in VERDICTS:
                    row["findings"] += 1
                    row[f["verdict"]] += 1
    for row in rows.values():
        checked = row["real"] + row["false_positive"]
        row["real_share"] = round(row["real"] / checked, 3) if checked else None
    return sorted(rows.values(), key=lambda r: (-r["reviews"], r["expert"], r["prompt"]))


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    snp = sub.add_parser("snapshot", help="store the change the experts are about to review")
    snp.add_argument("--repo", required=True)
    snp.add_argument("--base", default=None, help="the commit the change starts from (default HEAD)")
    rec = sub.add_parser("record", help="append one checked review")
    rec.add_argument("--file", required=True, help="the review JSON file, or - for stdin")
    st = sub.add_parser("stats", help="count verdicts per expert and prompt version")
    st.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "snapshot":
        try:
            snap = snapshot(args.repo, args.base)
        except ReviewInvalid as exc:
            print(f"no snapshot: {exc}", file=sys.stderr)
            return 2
        note = f"; {len(snap['skipped'])} untracked file(s) left out" if snap["skipped"] else ""
        print(f"snapshot {snap['snapshot']}" + ("" if snap["base"] or snap["patch_sha"]
                                                else " (not a git checkout: no patch)") + note)
        return 0
    if args.cmd == "record":
        try:
            raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
            doc = json.loads(raw)
        except (OSError, ValueError) as exc:
            print(f"cannot read the review: {exc}", file=sys.stderr)
            return 2
        try:
            stored = record(doc)
        except ReviewInvalid as exc:
            print(f"not recorded: {exc}", file=sys.stderr)
            return 2
        n = sum(len(e["findings"]) for e in stored["experts"])
        note = ("" if stored["patch_at"] == "review" else
                " (no snapshot: the patch is the code as it is now, after any fix)")
        print(f"recorded review {stored['review_id']}: {len(stored['experts'])} expert(s), "
              f"{n} finding(s)" + note)
        return 0
    rows = stats(read_reviews())
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print("no native review recorded yet")
        return 0
    print("expert          prompt        reviews findings real false_pos unverified real_share")
    for r in rows:
        share = "-" if r["real_share"] is None else f"{r['real_share']:.0%}"
        print(f"{r['expert']:<15} {r['prompt']:<13} {r['reviews']:>7} {r['findings']:>8} "
              f"{r['real']:>4} {r['false_positive']:>9} {r['unverified']:>10} {share:>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
