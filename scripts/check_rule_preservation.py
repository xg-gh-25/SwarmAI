#!/usr/bin/env python3
"""Rule-preservation checker for the pipeline stage-doc split (design §4-C3).

The pipeline docs (``INSTRUCTIONS.md`` + ``stages/*.md``) are being split into
exec cards, ``stages/ref/**`` and ``stages/history/**``. This tool guarantees
no enforceable rule is lost in the split:

* ``extract`` reads the OLD docs from a git ref (``git show``), pulls every
  rule-bearing block (``[GATE…]``, ``[MUST]``, ``BLOCKING``, ``⛔``) and writes
  a manifest with stable IDs ``R-<source>-<nnn>`` + content hashes.
  ``--only SRC --merge`` re-extracts just those sources into an existing
  manifest, so the split can go in batches (each source extracted at the ref
  that still holds its old text).
* ``check`` scans the NEW tree on disk and asserts every manifest ID appears
  as a tag ``<!-- R-<source>-<nnn> -->`` in a runtime-read doc (INSTRUCTIONS,
  ``stages/*.md``, ``stages/ref/**``). ``stages/history/**`` does not count.
* ``index`` prints a markdown index: rule ID -> file it lives in now.

Tag syntax: an HTML comment whose body starts with one or more IDs, e.g.
``<!-- R-build-007 -->`` or ``<!-- R-build-007 R-build-008 -->``. It renders
invisibly (cards stay readable for humans), survives every markdown renderer
including inside GFM table cells, is still visible to the agent reading the
raw file, and is trivially greppable (``grep -rn 'R-build-007'``).

Public symbols:
    DEFAULT_MANIFEST, SKILL_DIR, ID_RE
    repo_root(start)            -> Path of the git work tree
    split_blocks(text)          -> list[Block] (paragraph/list/quote/table/heading)
    find_markers(raw)           -> list[(kind, shape)]
    normalize(raw)              -> canonical rule text
    extract_rules(text, source, file) -> list[dict] rules (IDs assigned)
    extract(repo, ref, only)    -> manifest dict (optionally only some sources)
    merge_sources(base, part)   -> base with part's sources replaced
    write_manifest(path, manifest, force)
    scan_tags(repo, also=())    -> {"live": {...}, "history": {...}}
    check(manifest, scan)       -> result dict (``ok`` flag)
    render_index(manifest, scan) -> markdown str
    main(argv)                  -> exit code (0 ok, 1 check failed, 2 usage/env error)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

SKILL_DIR = "backend/skills/s_autonomous-pipeline"
DEFAULT_MANIFEST = f"{SKILL_DIR}/stages/rules.json"
MANIFEST_VERSION = 1
GIT_TIMEOUT_S = 30

# Rule ID: R-<source>-<nnn>; source = stage stem (build, goal_cycle, ...) or "instr".
ID_RE = re.compile(r"\bR-[a-z][a-z0-9_]*-\d{3,}\b")
_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)

# Marker patterns (case-sensitive on purpose: lowercase "blocking" is prose,
# e.g. "non-blocking", "_blocking" identifiers, "Blocking findings" headers).
_GATE_RE = re.compile(r"`?\[GATE(?:[·.:\-]([A-Za-z_+]+))?\]`?")
_MUST_RE = re.compile(r"`?\[MUST\]`?")
_BLOCKING_RE = re.compile(r"(?:\*\*|__)?\(?\bBLOCKING\b\)?:?(?:\*\*|__)?")
_STOP_RE = re.compile(r"⛔")
# Shapes seen in the docs but deliberately NOT treated as rule markers;
# counted only so the extract report shows what was left out.
_EXCLUDED_RES = {
    "blocking (lower/title case)": re.compile(r"\b(?:blocking|Blocking)\b"),
    "BLOCK (bare uppercase)": re.compile(r"\bBLOCKS?\b"),
    "MANDATORY": re.compile(r"\bMANDATORY\b"),
    "[GUIDE]": re.compile(r"\[GUIDE\]"),
}

_FENCE_RE = re.compile(r"^\s*(?:>\s?)*\s*(?:`{3,}|~{3,})")
_QUOTE_RE = re.compile(r"^\s*((?:>\s?)+)")
_HEADING_RE = re.compile(r"^#{1,6}\s")
_TABLE_RE = re.compile(r"^\s*\|")
_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


class ToolError(Exception):
    """Usage / environment error (exit code 2)."""


def _git(repo: Path, *args: str) -> str:
    """Run ``git <args>`` list-form in *repo*; raise ToolError on failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"git {args[0]} failed: {exc.__class__.__name__}") from exc
    if proc.returncode != 0:
        raise ToolError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def repo_root(start: Path | None = None) -> Path:
    """Return the git work-tree root containing *start* (default: this script)."""
    base = Path(start) if start else Path(__file__).resolve().parent
    return Path(_git(base, "rev-parse", "--show-toplevel").strip())


@dataclass
class Block:
    """One markdown unit: heading, table row, list item, quote or paragraph."""

    kind: str
    start: int  # 1-based first line
    end: int  # 1-based last line
    lines: list[str] = field(default_factory=list)

    @property
    def raw(self) -> str:
        return "\n".join(self.lines)


def split_blocks(text: str) -> list[Block]:
    """Split markdown into rule-sized blocks, in document order.

    Fence delimiter lines only act as block boundaries; fenced content is
    parsed like prose. The real docs contain malformed fences (an unclosed
    fence in complete.md, mis-nested fences in deliver.md) that would hide
    genuine rules from a strict CommonMark parser, and no well-formed code
    block at HEAD carries a marker, so scanning everything is the safe choice.
    """
    blocks: list[Block] = []
    cur: Block | None = None

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            blocks.append(cur)
        cur = None

    for n, line in enumerate(text.splitlines(), 1):
        if _FENCE_RE.match(line) or _HR_RE.match(line):
            flush()
            # Malformed closers carry prose ("```(Mid-run edits are ...") — keep
            # the line as its own block so a marker on it is never dropped.
            if find_markers(line):
                blocks.append(Block("fence-line", n, n, [line]))
            continue
        qm = _QUOTE_RE.match(line)
        quoted = qm is not None
        body = line[qm.end():] if qm else line
        prefix = "quote-" if quoted else ""
        if not body.strip():
            flush()
            continue
        if _HEADING_RE.match(body) or _TABLE_RE.match(body):
            flush()
            kind = "heading" if _HEADING_RE.match(body) else "table-row"
            blocks.append(Block(prefix + kind, n, n, [body]))
            continue
        if _ITEM_RE.match(body):
            flush()
            cur = Block(prefix + "list-item", n, n, [body])
            continue
        if cur is not None and cur.kind.startswith("quote-") == quoted:
            cur.lines.append(body)  # continuation (incl. lazy list continuation)
            cur.end = n
            continue
        flush()
        cur = Block(prefix + "paragraph", n, n, [body])
    flush()
    return blocks


def find_markers(raw: str) -> list[tuple[str, str]]:
    """Return ``(kind, shape)`` for every rule marker in *raw*, in order.

    kind  = canonical marker (``GATE``, ``GATE·cli``, ``MUST``, ``BLOCKING``, ``⛔``)
    shape = the literal spelling used (e.g. ``\\`[MUST]\\```, ``**BLOCKING:**``)
    """
    found: list[tuple[int, str, str]] = []
    for m in _GATE_RE.finditer(raw):
        kind = f"GATE·{m.group(1)}" if m.group(1) else "GATE"
        found.append((m.start(), kind, m.group(0)))
    for m in _MUST_RE.finditer(raw):
        found.append((m.start(), "MUST", m.group(0)))
    for m in _BLOCKING_RE.finditer(raw):
        found.append((m.start(), "BLOCKING", m.group(0)))
    for m in _STOP_RE.finditer(raw):
        found.append((m.start(), "⛔", m.group(0)))
    found.sort()
    return [(k, s) for _, k, s in found]


def normalize(raw: str) -> str:
    """Canonical rule text: no tags/emphasis/backticks/list bullets, one-space runs."""
    text = _COMMENT_RE.sub(" ", raw)  # drop HTML comments (incl. future R-tags)
    lines = []
    for line in text.splitlines():
        line = _ITEM_RE.sub("", line, count=1)
        if _TABLE_RE.match(line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            line = " | ".join(cells)
        lines.append(line)
    text = " ".join(lines)
    text = re.sub(r"\*\*|__|`", "", text)
    text = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def extract_rules(text: str, source: str, file: str) -> list[dict]:
    """Extract rule blocks from one doc; IDs are ``R-<source>-<nnn>`` in doc order."""
    rules: list[dict] = []
    blocks = split_blocks(text)
    i = 0
    while i < len(blocks):
        block = blocks[i]
        i += 1
        markers = find_markers(block.raw)
        if not markers:
            continue
        # A label paragraph ("**Execution (BLOCKING):**") governs the list right
        # under it: absorb adjacent list items so the rule carries its body.
        if block.kind.endswith("paragraph") and normalize(block.raw).endswith(":"):
            # (one leading paragraph is allowed: "label:\n\nbody text.")
            block = Block(block.kind, block.start, block.end, list(block.lines))
            quoted = block.kind.startswith("quote-")
            first = True
            while i < len(blocks) and blocks[i].start <= block.end + 2:
                nxt = blocks[i]
                if nxt.kind.startswith("quote-") != quoted:
                    break
                if not (nxt.kind.endswith("list-item")
                        or (first and nxt.kind.endswith("paragraph"))):
                    break
                block.lines += nxt.lines
                block.end = nxt.end
                first = False
                i += 1
            markers = find_markers(block.raw)
        norm = normalize(block.raw)
        rules.append({
            "id": f"R-{source}-{len(rules) + 1:03d}",
            "source": source,
            "file": file,
            "line": block.start,
            "end_line": block.end,
            "block": block.kind,
            "markers": sorted({k for k, _ in markers}),
            "shapes": [s for _, s in markers],
            "text": norm,
            "hash": _hash(norm),
        })
    return rules


def _old_doc_paths(repo: Path, commit: str) -> list[tuple[str, str]]:
    """``[(source, repo-relative path)]``: INSTRUCTIONS first, then stages/*.md sorted."""
    instr = f"{SKILL_DIR}/INSTRUCTIONS.md"
    listing = _git(repo, "ls-tree", commit, f"{SKILL_DIR}/stages/")
    stages = []
    for row in listing.splitlines():
        meta, _, path = row.partition("\t")
        if meta.split()[1:2] == ["blob"] and path.endswith(".md"):
            stages.append((Path(path).stem, path))
    return [("instr", instr)] + sorted(stages)


def _count_excluded(text: str, counter: Counter) -> None:
    for label, rx in _EXCLUDED_RES.items():
        counter[label] += len(rx.findall(text))


def extract(repo: Path, ref: str = "HEAD", only: tuple[str, ...] = ()) -> dict:
    """Build the manifest from the docs as they exist at git *ref*.

    *only* limits extraction to those sources (``instr``, ``build``, …); an
    unknown source name is a usage error."""
    if not ref or ref.startswith("-"):
        raise ToolError(f"invalid ref: {ref!r}")
    commit = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    rules: list[dict] = []
    files: list[dict] = []
    excluded: Counter = Counter()
    docs = _old_doc_paths(repo, commit)
    unknown = sorted(set(only) - {s for s, _ in docs})
    if unknown:
        raise ToolError(f"unknown source(s) at {ref}: {unknown}")
    for source, path in docs:
        if only and source not in only:
            continue
        text = _git(repo, "show", f"{commit}:{path}")
        found = extract_rules(text, source, path)
        _count_excluded(text, excluded)
        files.append({"source": source, "file": path, "rules": len(found)})
        rules.extend(found)
    return {
        "version": MANIFEST_VERSION,
        "ref": ref,
        "commit": commit,
        "tag_syntax": "<!-- R-<source>-<nnn> -->",
        "files": files,
        "marker_counts": dict(sorted(Counter(m for r in rules for m in r["markers"]).items())),
        "shape_counts": dict(sorted(Counter(s for r in rules for s in r["shapes"]).items())),
        "excluded_shape_counts": dict(sorted(excluded.items())),
        "rules": rules,
    }


def merge_sources(base: dict, part: dict) -> dict:
    """Replace *part*'s sources in *base* (the doc split goes in batches: each
    batch re-extracts only its own sources, at the ref holding their OLD text).

    Every other source keeps its rules, IDs and extraction ref untouched. Each
    file entry records the ``ref``/``commit`` it was extracted at."""
    sources = {f["source"] for f in part["files"]}
    for f in part["files"]:
        f.setdefault("ref", part["ref"])
        f.setdefault("commit", part["commit"])
    kept_files = [f for f in base.get("files", []) if f["source"] not in sources]
    for f in kept_files:
        f.setdefault("ref", base.get("ref"))
        f.setdefault("commit", base.get("commit"))
    order: dict[str, int] = {}
    for f in [*base.get("files", []), *part["files"]]:
        order.setdefault(f["source"], len(order))  # a replaced source keeps its slot
    files = sorted(kept_files + part["files"], key=lambda f: order[f["source"]])
    rank = {f["source"]: i for i, f in enumerate(files)}
    rules = [r for r in base["rules"] if r["source"] not in sources] + part["rules"]
    rules.sort(key=lambda r: (rank[r["source"]], r["id"]))
    merged = dict(base)
    merged.update({
        "files": files,
        "rules": rules,
        "marker_counts": dict(sorted(Counter(m for r in rules for m in r["markers"]).items())),
        "shape_counts": dict(sorted(Counter(s for r in rules for s in r["shapes"]).items())),
    })
    return merged


def write_manifest(path: Path, manifest: dict, force: bool = False) -> None:
    """Write *manifest* as JSON; refuse to clobber an existing file unless *force*."""
    if path.exists() and not force:
        raise ToolError(f"manifest exists: {path} (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> dict:
    """Load + sanity-check a manifest (rules list, unique well-formed IDs)."""
    if not path.is_file():
        raise ToolError(f"manifest not found: {path} (run `extract` first)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolError(f"manifest is not valid JSON: {exc}") from exc
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        raise ToolError("manifest has no 'rules' list")
    ids = [r.get("id") for r in rules if isinstance(r, dict)]
    dupes = sorted(i for i, c in Counter(ids).items() if c > 1)
    bad = [i for i in ids if not isinstance(i, str) or not ID_RE.fullmatch(i)]
    if dupes or bad or len(ids) != len(rules):
        raise ToolError(f"manifest IDs invalid: dupes={dupes[:5]} malformed={bad[:5]}")
    return data


def _tags_in(text: str) -> list[tuple[str, int]]:
    """``[(id, line)]`` for every tag: an HTML comment whose body starts with an ID."""
    out = []
    for m in _COMMENT_RE.finditer(text):
        body = m.group(1).strip()
        if not ID_RE.match(body):
            continue
        line = text.count("\n", 0, m.start()) + 1
        out.extend((i, line) for i in ID_RE.findall(body))
    return out


def _live_files(skill: Path, also: tuple[str, ...]) -> list[Path]:
    stages = skill / "stages"
    files = {skill / "INSTRUCTIONS.md"} | set(stages.glob("*.md"))
    files |= set((stages / "ref").rglob("*.md"))
    for pattern in also:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ToolError(f"--also must be a glob relative to {SKILL_DIR}: {pattern!r}")
        files |= set(skill.glob(pattern))
    hist = stages / "history"
    return sorted(f for f in files if f.is_file() and hist not in f.parents)


def scan_tags(repo: Path, also: tuple[str, ...] = ()) -> dict:
    """Map rule ID -> locations, split into runtime-read ``live`` docs and ``history``."""
    skill = repo / SKILL_DIR
    groups = {
        "live": _live_files(skill, also),
        "history": sorted(f for f in (skill / "stages" / "history").rglob("*.md") if f.is_file()),
    }
    result: dict = {"files": {}}
    for name, paths in groups.items():
        where: dict[str, list[dict]] = {}
        for p in paths:
            rel = p.relative_to(repo).as_posix()
            for rid, line in _tags_in(p.read_text(encoding="utf-8")):
                where.setdefault(rid, []).append({"file": rel, "line": line})
        result[name] = where
        result["files"][name] = [p.relative_to(repo).as_posix() for p in paths]
    return result


def check(manifest: dict, scan: dict) -> dict:
    """Compare manifest IDs against tags found on disk.

    Errors: ``missing`` (no tag anywhere), ``history_only`` (tag only under
    stages/history/), ``unknown`` (tag ID not in manifest). Warning:
    ``multi_location`` (ID tagged at more than one live location).
    """
    rules = {r["id"]: r for r in manifest["rules"]}
    live, hist = scan["live"], scan["history"]
    missing, history_only, multi = [], [], []
    for rid, rule in rules.items():
        if rid in live:
            if len(live[rid]) > 1:
                multi.append({"id": rid, "locations": live[rid]})
            continue
        entry = {"id": rid, "file": rule.get("file"), "line": rule.get("line"),
                 "text": rule.get("text", "")}
        if rid in hist:
            history_only.append({**entry, "locations": hist[rid]})
        else:
            missing.append(entry)
    unknown = [{"id": rid, "locations": locs}
               for group in (live, hist) for rid, locs in sorted(group.items())
               if rid not in rules]
    return {
        "ok": not (missing or history_only or unknown),
        "total": len(rules),
        "preserved": len(rules) - len(missing) - len(history_only),
        "missing": missing,
        "history_only": history_only,
        "unknown": unknown,
        "multi_location": multi,
        "scanned": scan["files"],
    }


def _short(text: str, n: int = 90) -> str:
    text = text.replace("|", "\\|")
    return text if len(text) <= n else text[: n - 1] + "…"


def index_rows(manifest: dict, scan: dict) -> list[dict]:
    """One row per rule: where it lives now (live first, else history, else MISSING)."""
    rows = []
    for r in manifest["rules"]:
        locs = scan["live"].get(r["id"])
        status = "live"
        if not locs:
            locs = scan["history"].get(r["id"])
            status = "history-only" if locs else "missing"
        files = sorted({loc["file"] for loc in locs or []})
        rows.append({"id": r["id"], "markers": r.get("markers", []),
                     "status": status, "files": files, "text": r.get("text", "")})
    return rows


def render_index(manifest: dict, scan: dict) -> str:
    """Markdown index: rule ID -> file(s) it lives in now (the exec-card index)."""
    out = ["# Pipeline rule index", "",
           f"Manifest: {len(manifest['rules'])} rules extracted at "
           f"`{manifest.get('ref', '?')}` ({str(manifest.get('commit', ''))[:10]}).", "",
           "| Rule | Markers | Lives in | Rule text |", "|---|---|---|---|"]
    for row in index_rows(manifest, scan):
        if row["status"] == "missing":
            where = "**MISSING**"
        else:
            where = ", ".join(f"`{f}`" for f in row["files"])
            if row["status"] == "history-only":
                where += " (**history only**)"
        out.append(f"| {row['id']} | {' '.join(row['markers'])} | {where} | {_short(row['text'])} |")
    return "\n".join(out) + "\n"


def _summary(result: dict) -> dict:
    return {k: (len(v) if isinstance(v, list) else v)
            for k, v in result.items() if k != "scanned"}


def _print_check(result: dict) -> None:
    for kind, label in (("missing", "MISSING"), ("history_only", "HISTORY-ONLY")):
        for e in result[kind]:
            print(f"{label} {e['id']} (was {e['file']}:{e['line']}): {_short(e['text'], 160)}")
    for e in result["unknown"]:
        locs = ", ".join(f"{loc['file']}:{loc['line']}" for loc in e["locations"])
        print(f"UNKNOWN {e['id']} tagged at {locs} (not in manifest)")
    for e in result["multi_location"]:
        locs = ", ".join(f"{loc['file']}:{loc['line']}" for loc in e["locations"])
        print(f"WARN multi-location {e['id']}: {locs}")
    verdict = "PASS" if result["ok"] else "FAIL"
    print(f"{verdict}: {result['preserved']}/{result['total']} rules preserved; "
          f"missing={len(result['missing'])} history_only={len(result['history_only'])} "
          f"unknown={len(result['unknown'])} multi_location={len(result['multi_location'])}")
    print("SUMMARY " + json.dumps(_summary(result), separators=(",", ":")))


def _print_extract(manifest: dict, out: Path) -> None:
    print(f"extracted {len(manifest['rules'])} rules from {manifest['ref']} "
          f"({manifest['commit'][:10]}) -> {out}")
    for f in manifest["files"]:
        print(f"  {f['source']:<11} {f['rules']:>4}  {f['file']}")
    print("markers (rules carrying):", json.dumps(manifest["marker_counts"], ensure_ascii=False))
    print("shapes (occurrences):", json.dumps(manifest["shape_counts"], ensure_ascii=False))
    print("excluded (not rules):", json.dumps(manifest["excluded_shape_counts"], ensure_ascii=False))


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", help="repo root (default: git root of this script)")
    common.add_argument("--json", action="store_true", help="machine-readable JSON output")
    common.add_argument("--manifest", "--out", dest="manifest", default=DEFAULT_MANIFEST,
                        help=f"manifest path, relative to repo root (default {DEFAULT_MANIFEST})")
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract", parents=[common], help="build manifest from old docs at --ref")
    ex.add_argument("--ref", default="HEAD", help="git ref holding the OLD docs (default HEAD)")
    ex.add_argument("--force", action="store_true", help="overwrite an existing manifest")
    ex.add_argument("--only", action="append", default=[], metavar="SOURCE",
                    help="extract only this source (instr, build, …); repeatable")
    ex.add_argument("--merge", action="store_true",
                    help="replace only the extracted sources in the existing manifest "
                         "(batched split: other sources keep their rules and ref)")
    for name, desc in (("check", "assert every rule ID is tagged in card/ref docs"),
                       ("index", "print markdown index: rule ID -> current file")):
        sp = sub.add_parser(name, parents=[common], help=desc)
        sp.add_argument("--also", action="append", default=[], metavar="GLOB",
                        help=f"extra runtime-read docs, glob relative to {SKILL_DIR} "
                             "(e.g. data/gate-prompts/*.md); repeatable")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        repo = Path(args.repo).resolve() if args.repo else repo_root()
        repo = repo_root(repo)
        manifest_path = Path(args.manifest)
        if not manifest_path.is_absolute():
            manifest_path = repo / manifest_path
        if args.cmd == "extract":
            manifest = extract(repo, args.ref, tuple(args.only))
            if args.merge:
                if not manifest_path.is_file():
                    raise ToolError(f"--merge needs an existing manifest: {manifest_path}")
                manifest = merge_sources(load_manifest(manifest_path), manifest)
            write_manifest(manifest_path, manifest, force=args.force or args.merge)
            if args.json:
                print(json.dumps({k: v for k, v in manifest.items() if k != "rules"}
                                 | {"rules": len(manifest["rules"]),
                                    "manifest": str(manifest_path)}, ensure_ascii=False))
            else:
                _print_extract(manifest, manifest_path)
            return 0
        manifest = load_manifest(manifest_path)
        scan = scan_tags(repo, tuple(args.also))
        if args.cmd == "index":
            if args.json:
                print(json.dumps(index_rows(manifest, scan), ensure_ascii=False, indent=2))
            else:
                sys.stdout.write(render_index(manifest, scan))
            return 0
        result = check(manifest, scan)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_check(result)
        return 0 if result["ok"] else 1
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
