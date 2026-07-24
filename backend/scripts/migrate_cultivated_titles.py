"""One-shot migration: give existing PLAIN cultivated DDD bullets a bold title.

Background (run_3e43c7ee): cultivation's apply_to_ddd historically wrote raw-prose
reflect-lessons VERBATIM as ``- {content} (date, run, label)`` with NO ``**bold**``
title. The lifecycle engine's _ENTRY_RE only parses ``- [type]? **Title** …``, so
these bullets were structurally INVISIBLE to parse/decay/reclaim/retire — they silted
forever (measured ~1600 across all DDD docs). The writer is now fixed
(_normalize_cultivated_bullet); this migration re-titles the EXISTING backlog using the
SAME helper, so title logic lives in ONE place.

INSERT-ONLY + FAIL-LOUD. For every bullet it rewrites, it asserts BOTH invariants and
REFUSES (skips + reports) any bullet that would violate them — it never ships a lossy
or dedup-breaking edit:
  1. TRUE-LOSSLESS   — the original content is recoverable byte-for-byte from the output.
  2. SIGNATURE-INVARIANT — content_signature(before) == content_signature(after), so the
     doc-wide dedup chokepoint (run_e9cb7e2a) is not re-opened.

Scope gate: ONLY plain bullets carrying a trailing ``(YYYY-MM-DD, …)`` / ``(run_xxx …)``
attribution (the shape cultivation writes) AND lacking a leading ``**`` are touched.
Hand-curated emoji-prose / Open-Threads bullets (no attribution) are SKIPPED — the same
distinction the writer relies on.

Recovery = git (the workspace auto-commits); NO .bak (STEERING #2 / run_a6482355).

Usage (from the swarmai repo root):
    backend/.venv/bin/python backend/scripts/migrate_cultivated_titles.py            # dry-run (default)
    backend/.venv/bin/python backend/scripts/migrate_cultivated_titles.py --apply    # write
    backend/.venv/bin/python backend/scripts/migrate_cultivated_titles.py --project SwarmAI  # scope to one project
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ddd_cultivation import (
    _ALREADY_TITLED_RE,
    _normalize_cultivated_bullet,
    content_signature,
)
from core.ddd_entry_lifecycle import classify_entry_type, _ENTRY_RE

WORKSPACE = Path.home() / ".swarm-ai" / "SwarmWS"
PROJECTS_DIR = WORKSPACE / "Projects"

# A bullet line (may have leading whitespace for continuation-safety we don't touch).
_BULLET_RE = re.compile(r"^- (.*)$")
# Trailing cultivation attribution: (YYYY-MM-DD, …) or (run_xxx …) at end of line.
_ATTRIBUTION_RE = re.compile(r"\((?:\d{4}-\d{2}-\d{2}|run_[0-9a-f]+)[^)]*\)\s*$")
# (Already-titled gate _ALREADY_TITLED_RE imported from ddd_cultivation — single source.)


def _text_after_type_and_markers(s: str) -> str:
    """Return `s` with a single leading ``[type] `` (if any) and the two leftmost
    ``**`` markers (if any) removed. Applied to BOTH the original content and the
    normalized output, this reduces both to their bare text so losslessness can be
    checked tag-agnostically: the normalizer legitimately CONSUMES a leading
    ``[type] `` from the content (else it would double-prefix), so comparing bare
    text is the correct lossless invariant — the type itself is preserved in the
    emitted prefix, verified separately by the signature check."""
    t = re.sub(r"^\[\w+\] ", "", s, count=1)
    return t.replace("**", "", 2)


def _migrate_line(line: str) -> "tuple[str, str] | None":
    """If `line` is a plain attributed cultivated bullet, return (new_line, title).
    Return None if the line should be left untouched (not a bullet / not attributed /
    already titled / degenerate). Raises AssertionError on an invariant violation
    (fail-loud — the caller reports it and does NOT write)."""
    bm = _BULLET_RE.match(line)
    if not bm:
        return None
    body = bm.group(1)
    # Scope gate: must carry the cultivation attribution AND lack a bold title.
    if not _ATTRIBUTION_RE.search(body):
        return None
    if _ALREADY_TITLED_RE.match(body):
        return None
    # Split content from its trailing attribution; normalize ONLY the content.
    am = _ATTRIBUTION_RE.search(body)
    content = body[: am.start()].rstrip()
    attribution = body[am.start():]
    if not content:
        return None
    entry_type = classify_entry_type(content)
    normalized = _normalize_cultivated_bullet(content, entry_type)
    if normalized is None or normalized == content:
        return None  # degenerate or already-titled → leave as-is

    # ---- FAIL-LOUD invariant asserts (per bullet) ----
    # 1. TRUE-LOSSLESS: the bare text (type-tag + ** markers removed) must be
    # identical before and after — no words dropped, truncated, or re-separated.
    assert _text_after_type_and_markers(normalized) == _text_after_type_and_markers(content), (
        f"LOSSY migration would drop content:\n  before={content!r}\n  after ={normalized!r}"
    )
    new_body = f"{normalized} {attribution}".rstrip()
    new_line = f"- {new_body}"
    # 2. SIGNATURE-INVARIANT: dedup chokepoint must see the same signature.
    assert content_signature(line) == content_signature(new_line), (
        f"SIGNATURE DRIFT (would re-open dedup hole):\n  before-sig={content_signature(line)!r}"
        f"\n  after -sig={content_signature(new_line)!r}"
    )
    # 3. PARSE: the result must actually be _ENTRY_RE-parseable with a real title.
    m = _ENTRY_RE.match(new_line)
    assert m is not None and m.group(2).strip(), f"result not parseable: {new_line!r}"
    return new_line, m.group(2)


def migrate_doc(path: Path, apply: bool) -> dict:
    """Migrate one DDD doc. Returns per-doc counts. Idempotent."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out_lines = []
    migrated = skipped_bad = 0
    for raw in lines:
        line = raw.rstrip("\n")
        newline_suffix = raw[len(line):]  # preserve original EOL
        try:
            result = _migrate_line(line)
        except AssertionError as e:
            # Fail-loud: never write a violating edit — keep the original line.
            print(f"  ⚠️  SKIP (invariant violation) in {path.name}: {e}", file=sys.stderr)
            skipped_bad += 1
            out_lines.append(raw)
            continue
        if result is None:
            out_lines.append(raw)
        else:
            new_line, _title = result
            out_lines.append(new_line + newline_suffix)
            migrated += 1
    if apply and migrated:
        path.write_text("".join(out_lines), encoding="utf-8")
    return {"migrated": migrated, "skipped_bad": skipped_bad}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--project", default=None, help="scope to one project (default: all)")
    args = ap.parse_args()

    base = PROJECTS_DIR / args.project if args.project else PROJECTS_DIR
    if not base.exists():
        print(f"no such path: {base}", file=sys.stderr)
        sys.exit(1)

    docs = [
        p for p in base.rglob("*.md")
        if ".artifacts" not in p.parts and not p.name.endswith("-archive.md")
    ]
    total_migrated = total_bad = docs_touched = 0
    for doc in sorted(docs):
        counts = migrate_doc(doc, apply=args.apply)
        if counts["migrated"] or counts["skipped_bad"]:
            docs_touched += 1
            total_migrated += counts["migrated"]
            total_bad += counts["skipped_bad"]
            rel = doc.relative_to(PROJECTS_DIR)
            print(f"  {rel}: {counts['migrated']} titled"
                  + (f", {counts['skipped_bad']} skipped-bad" if counts["skipped_bad"] else ""))

    mode = "APPLIED" if args.apply else "DRY-RUN (no writes — pass --apply to persist)"
    print(f"\n{mode}: {total_migrated} bullets titled across {docs_touched} docs"
          + (f"; {total_bad} skipped on invariant violations" if total_bad else ""))
    if not args.apply and total_migrated:
        print("Re-run with --apply to write. Recovery = git (no .bak).")


if __name__ == "__main__":
    main()
