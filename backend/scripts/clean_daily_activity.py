"""One-shot cleanup of DailyActivity day files (Knowledge/DailyActivity/*.md).

Why: until 2026-10-21 every session appended a new entry on each checkpoint and
summary (one session wrote 118 entries in a day), checkpoints listed every repo
commit rather than the session's own, the Files line was parsed from command
fragments (`null`, `activate`, quoted code), and test sessions wrote into the
live workspace. All of it is indexed for recall. The writers are fixed; this
script cleans what they already wrote. It changes text only -- the files are
git-tracked in SwarmWS, so the previous state stays in history.

Per day file:
- one entry per session, merged from all of its entries: header and
  single-value fields from the latest summary, list sections as the ordered
  union of every entry's bullets (nothing distinct is lost);
- entries of the ``test-ses`` session are dropped;
- ``**Git activity:**`` lists are dropped (unattributed: they named other
  sessions' commits; ``git log`` is the record);
- ``**Files:**`` keeps only tokens that look like file names;
- non-session sections (Session Resume, COE Registry, ...) are kept as-is.

Usage::

    python scripts/clean_daily_activity.py [--dir PATH] [--apply]

Without ``--apply`` it reports what would change and writes nothing. Each file
is rewritten under the DailyActivity writer lock with an atomic rename.

Public: ``clean_text(text) -> str``, ``main()``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ENTRY_RE = re.compile(r"^## \d{1,2}:\d{2} \| (?P<sid>[^|]+?) \| (?P<title>.*)$")
_MARKER_RE = re.compile(r"^<!-- da:(?P<kind>[a-z]+):(?P<sid>[^ ]+) -->$")
_END_MARKER = "<!-- /da -->"
_FILE_TOKEN_RE = re.compile(r"^[\w@+-][\w.@+-]*\.[A-Za-z0-9]{1,10}$")
_TEST_SESSIONS = {"test-ses"}


def _split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[:end + 5], text[end + 5:]
    return "", text


def _blocks(body: str) -> list[dict]:
    """Split a body into blocks: session entries (with marker if any) and other text."""
    lines = body.split("\n")
    blocks: list[dict] = []
    cur: dict | None = None

    def start(kind: str, sid: str | None, title: str = "") -> dict:
        nonlocal cur
        cur = {"kind": kind, "sid": sid, "title": title, "lines": []}
        blocks.append(cur)
        return cur

    i = 0
    while i < len(lines):
        line = lines[i]
        m = _MARKER_RE.match(line)
        if m:
            b = start("entry", m["sid"][:8])
            b["marked"] = True
            b["lines"].append(line)
            i += 1
            while i < len(lines):
                b["lines"].append(lines[i])
                e = _ENTRY_RE.match(lines[i])
                if e and not b["title"]:
                    b["title"] = e["title"]
                if lines[i] == _END_MARKER:
                    i += 1
                    break
                i += 1
            cur = None
            continue
        e = _ENTRY_RE.match(line)
        if e:
            start("entry", e["sid"].strip(), e["title"])["lines"].append(line)
        elif line.startswith("## ") or cur is None:
            if line.startswith("## ") or cur is None:
                start("other", None)
            cur["lines"].append(line)
        else:
            cur["lines"].append(line)
        i += 1
    return blocks


def _is_checkpoint(block: dict) -> bool:
    return "Mid-session checkpoint" in block["title"]


_SECTION_RE = re.compile(r"^\*\*(?P<name>[^*]+):\*\*\s?(?P<rest>.*)$")


def _parse_entry(lines: list[str]) -> tuple[str, list[str], dict]:
    """(header line, section order, {name: {"inline": str, "items": [str]}})."""
    header = next((ln for ln in lines if _ENTRY_RE.match(ln)), "")
    order: list[str] = []
    secs: dict[str, dict] = {}
    cur: str | None = None
    for ln in lines:
        if ln == header or _MARKER_RE.match(ln) or ln == _END_MARKER:
            continue
        m = _SECTION_RE.match(ln)
        if m:
            cur = m["name"].strip()
            if cur not in secs:
                order.append(cur)
                secs[cur] = {"inline": "", "items": []}
            if m["rest"].strip():
                secs[cur]["inline"] = m["rest"].strip()
            continue
        if cur and ln.strip():
            secs[cur]["items"].append(ln.rstrip())
    return header, order, secs


def _file_tokens(inline: str) -> list[str]:
    return [t for t in re.findall(r"`([^`]*)`", inline) if _FILE_TOKEN_RE.match(t)]


def _merge_entries(bs: list[dict]) -> list[str]:
    """Merge every entry of one session into one, losing no distinct bullet.

    Header and single-value fields come from the latest summary (latest
    checkpoint if there is none); list sections are the ordered union of all
    entries' bullets; Files is the union of real file names; Git activity is
    dropped.
    """
    summaries = [b for b in bs if not _is_checkpoint(b)]
    base = (summaries or bs)[-1]
    parsed = [_parse_entry(b["lines"]) for b in bs]
    header, base_order, _ = _parse_entry(base["lines"])
    order = list(base_order)
    for _, o, _ in parsed:
        order += [n for n in o if n not in order]
    out = [header, ""]
    for name in order:
        if name == "Git activity":
            continue
        if name == "Files":
            files: list[str] = []
            for _, _, s in parsed:
                for t in _file_tokens(s.get("Files", {}).get("inline", "")):
                    if t not in files:
                        files.append(t)
            if files:
                out += ["**Files:** " + ", ".join(f"`{t}`" for t in files[:20]), ""]
            continue
        items: list[str] = []
        inline = ""
        for b, (_, _, s) in zip(bs, parsed):
            sec = s.get(name)
            if not sec:
                continue
            if sec["inline"] and (not inline or b is base or not _is_checkpoint(b)):
                inline = sec["inline"]
            for it in sec["items"]:
                if it not in items:
                    items.append(it)
        if not inline and not items:
            continue
        out.append(f"**{name}:**" + (f" {inline}" if inline else ""))
        out += items
        out.append("")
    marker = next((ln for ln in base["lines"] if _MARKER_RE.match(ln)), None)
    if marker:
        return [marker] + out[:-1] + [_END_MARKER, ""]
    return out


def clean_text(text: str) -> str:
    """Return the cleaned day file text (idempotent)."""
    front, body = _split_frontmatter(text)
    blocks = _blocks(body)
    by_sid: dict[str, list[dict]] = {}
    for b in blocks:
        if b["kind"] == "entry":
            by_sid.setdefault(b["sid"], []).append(b)
    # Each session's merged entry goes where its base (latest summary) was.
    place: dict[int, list[str]] = {}
    for sid, bs in by_sid.items():
        if sid in _TEST_SESSIONS:
            continue
        summaries = [b for b in bs if not _is_checkpoint(b)]
        place[id((summaries or bs)[-1])] = _merge_entries(bs)
    out_lines: list[str] = []
    for b in blocks:
        if b["kind"] == "entry":
            out_lines.extend(place.get(id(b), []))
        else:
            out_lines.extend(b["lines"])
    new_body = "\n".join(out_lines)
    new_body = re.sub(r"\n{3,}", "\n\n", new_body)
    return front + new_body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", type=Path,
                    default=Path.home() / ".swarm-ai" / "SwarmWS" / "Knowledge" / "DailyActivity")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.daily_activity_writer import _atomic_write, _day_lock

    files = sorted(args.dir.glob("*.md"))
    tot_before = tot_after = changed = 0
    for path in files:
        with _day_lock(path):
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
            new = clean_text(text)
            tot_before += len(text.encode("utf-8", "surrogateescape"))
            tot_after += len(new.encode("utf-8", "surrogateescape"))
            if new != text:
                changed += 1
                n_old = len(re.findall(r"^## \d", text, re.M))
                n_new = len(re.findall(r"^## \d", new, re.M))
                print(f"{path.name}: entries {n_old} -> {n_new}, "
                      f"{len(text) // 1024}K -> {len(new) // 1024}K")
                if args.apply:
                    _atomic_write(path, new)
    print(f"{changed}/{len(files)} files {'rewritten' if args.apply else 'would change'}; "
          f"{tot_before // 1024}K -> {tot_after // 1024}K")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
