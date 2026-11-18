#!/usr/bin/env python3
"""Search and read the pinned Modern Web Guidance guides (offline).

The guides are GoogleChrome/modern-web-guidance's, copied verbatim into
``data/modern-web-guidance/guides/`` at the commit ``PROVENANCE.json`` names. A guide's
id is its file name without ``.md``.

    web_guidance.py search "<2-5 word capability>"   ranked ids, titles and summaries (JSON)
    web_guidance.py retrieve <id>[,<id>...]           whole guides, separated by a rule line
    web_guidance.py list                              every id, by category (JSON)

Search is BM25 (this skill's ``core.BM25``) over each guide's id, title and headings,
weighted three times, and its whole text. A result names the guide whole (id, title,
category, length) and gives its first paragraph; ``retrieve`` prints the guide uncut.

The guides point to each other with the upstream command
``npx -y modern-web-guidance@latest retrieve "<id>"``. Every text this script prints
names this script instead (``web_guidance.py retrieve "<id>"``), so following a
cross-reference never fetches and runs the npm package.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

GUIDES = Path(__file__).resolve().parent.parent / "data" / "modern-web-guidance" / "guides"
TOP = 8
_UPSTREAM_CMD = re.compile(r"npx\s+(?:-y\s+)?modern-web-guidance@[\w.\-]+\s+(retrieve|search|list)\b")


def _local(text: str) -> str:
    """*text* with the upstream npx command replaced by this script."""
    return _UPSTREAM_CMD.sub(r"web_guidance.py \1", text)


def _bm25():
    # The sibling core.py, loaded by path: a plain ``import core`` could resolve to the
    # backend's own ``core`` package when this runs inside the backend binary.
    spec = importlib.util.spec_from_file_location(
        "_frontend_design_core", Path(__file__).resolve().parent / "core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BM25()


def _summary(lines: list[str]) -> str:
    """The first prose paragraph after the title (not a heading, list, table or code)."""
    para: list[str] = []
    fence = False
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("```"):
            fence = not fence
            if para:
                break
            continue
        if fence:
            continue
        if not s:
            if para:
                break
            continue
        if s.startswith(("#", "-", "*", "|", ">", "1.")) or re.match(r"\d+\.\s", s):
            if para:
                break
            continue
        para.append(s)
    if not para:   # a guide that opens with a section: its first heading names it
        para = [next((l.lstrip("# ").strip() for l in lines[1:] if l.startswith("#")), "")]
    return " ".join(para)


def load() -> list[dict]:
    guides = []
    for path in sorted(GUIDES.rglob("*.md")):
        text = _local(path.read_text(encoding="utf-8"))
        lines = text.splitlines()
        title = lines[0].lstrip("# ").strip() if lines else path.stem
        headings = " ".join(l.lstrip("# ").strip() for l in lines if l.startswith("#"))
        gid = path.stem
        guides.append({
            "id": gid, "title": title, "category": path.parent.name,
            "chars": len(text), "summary": _summary(lines), "text": text,
            "index": " ".join([gid.replace("-", " "), title, headings] * 3 + [text]),
        })
    return guides


def search(query: str, top: int = TOP) -> list[dict]:
    guides = load()
    bm25 = _bm25()
    bm25.fit([g["index"] for g in guides])
    ranked = [(i, s) for i, s in bm25.score(query) if s > 0][:top]
    return [{k: guides[i][k] for k in ("id", "title", "category", "chars", "summary")}
            for i, _s in ranked]


def retrieve(ids: list[str]) -> tuple[str, list[str]]:
    by_id = {g["id"]: g for g in load()}
    found = [by_id[i]["text"].rstrip() for i in ids if i in by_id]
    return "\n\n---\n\n".join(found), [i for i in ids if i not in by_id]


def listing() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for g in load():
        out.setdefault(g["category"], []).append(g["id"])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search the pinned Modern Web Guidance guides")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--top", type=int, default=TOP)
    r = sub.add_parser("retrieve")
    r.add_argument("ids", help="one id, or several separated by commas")
    sub.add_parser("list")
    args = parser.parse_args(argv)
    if args.cmd == "search":
        print(json.dumps(search(args.query, args.top), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "list":
        print(json.dumps(listing(), indent=1))
        return 0
    text, missing = retrieve([i.strip() for i in args.ids.split(",") if i.strip()])
    if text:
        print(text)
    if missing:
        print(f"no such guide: {', '.join(missing)} (see: web_guidance.py list)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
