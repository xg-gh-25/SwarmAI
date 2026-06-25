"""Fold the unbounded auto-appended churn in a DDD doc's section.

One-shot, reproducible curation (NOT hand-editing — O028). Isolates a single
`## <section>` span, keeps all curated (hand-written) bullets plus the most
recent N auto-appended `[auto ...]` bullets, and MOVES the rest to an archive
file (archive = move, never delete — M0 run_94fd5597 lesson). Every other
section is preserved byte-identical (the span regex is bounded by the next
`## ` header).

Public API:
    fold_section(content, section, *, keep_recent, today) -> FoldResult
    FoldResult(new_content, archived_bullets, curated_kept, auto_kept)

CLI:
    python -m skills.s_github_community.scripts.fold_patterns \
        --doc <path> [--section "Patterns Discovered"] [--keep-recent 20] [--apply]
    (default is dry-run; --apply writes <doc>.<date>.bak then doc + archive)
"""

import argparse
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

# Bullet = a "- " line plus any wrapped continuation lines (until next "- " or "#").
_BULLET_RE = re.compile(r"^- .+(?:\n(?!- |#).*)*", re.M)
# Auto-appended churn entries start "- [auto YYYY-MM-DD]".
_AUTO_RE = re.compile(r"^- \[auto ", re.M)
_AUTO_DATE_RE = re.compile(r"^- \[auto (\d{4}-\d{2}-\d{2})\]")

DEFAULT_KEEP_RECENT = 20


@dataclass
class FoldResult:
    new_content: str
    archived_bullets: list[str] = field(default_factory=list)
    curated_kept: int = 0
    auto_kept: int = 0

    @property
    def archived_count(self) -> int:
        return len(self.archived_bullets)


def _is_auto(bullet: str) -> bool:
    return bool(_AUTO_RE.match(bullet.strip() if bullet[:1] != "-" else bullet)) or \
        bullet.lstrip().startswith("- [auto ")


def fold_section(
    content: str,
    section: str,
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT,
    today: date | None = None,
) -> FoldResult:
    """Return content with `section`'s auto-churn folded.

    Keeps ALL curated bullets + the most-recent `keep_recent` auto bullets.
    The rest of the auto bullets are returned in `archived_bullets` for the
    caller to persist to an archive file. Only the named section is rewritten;
    everything else in `content` is byte-identical.
    """
    today = today or datetime.now(timezone.utc).date()

    # Isolate the section span: from "## <section>" up to the next "## " or EOF.
    span_re = re.compile(
        r"(^## " + re.escape(section) + r"\b.*?)(?=^## |\Z)",
        re.M | re.S,
    )
    m = span_re.search(content)
    if not m:
        return FoldResult(new_content=content)  # section absent — no-op

    span = m.group(1)

    # Split span into: header block (everything before the first bullet) + bullets.
    first_bullet = _BULLET_RE.search(span)
    if not first_bullet:
        return FoldResult(new_content=content)  # no bullets — nothing to fold
    header_block = span[: first_bullet.start()]
    bullets = _BULLET_RE.findall(span)

    curated = [b for b in bullets if not b.lstrip().startswith("- [auto ")]
    auto = [b for b in bullets if b.lstrip().startswith("- [auto ")]

    # Recency: auto bullets are appended in chronological order, so the tail is
    # newest. Sort by embedded [auto DATE] when present (stable), else preserve
    # document order; keep the last `keep_recent`.
    def _auto_date(b: str) -> str:
        dm = _AUTO_DATE_RE.match(b.lstrip())
        return dm.group(1) if dm else ""

    auto_sorted = sorted(auto, key=_auto_date)  # stable; undated sort first (oldest)
    if keep_recent > 0:
        auto_kept = auto_sorted[-keep_recent:] if len(auto_sorted) > keep_recent else auto_sorted
    else:
        auto_kept = []
    kept_set = set(map(id, auto_kept))
    archived = [b for b in auto_sorted if id(b) not in kept_set]

    # Rebuild the span: header + curated (doc order) + kept auto + pointer line.
    parts = [header_block.rstrip("\n"), ""]
    parts.extend(b.rstrip("\n") for b in curated)
    if auto_kept:
        parts.append("")
        parts.extend(b.rstrip("\n") for b in auto_kept)
    if archived:
        parts.append("")
        parts.append(
            f"- _[{today.isoformat()}] {len(archived)} older auto-logged "
            f"engagement patterns folded to IMPROVEMENT-archive.md_"
        )
    new_span = "\n".join(parts) + "\n\n"

    new_content = content[: m.start(1)] + new_span + content[m.end(1):]
    return FoldResult(
        new_content=new_content,
        archived_bullets=archived,
        curated_kept=len(curated),
        auto_kept=len(auto_kept),
    )


def _append_archive(archive_path: Path, bullets: list[str], section: str, today: date) -> None:
    block = [b.rstrip("\n") for b in bullets]
    body = "\n".join(block) + "\n"
    if archive_path.exists():
        existing = archive_path.read_text(encoding="utf-8")
        if not existing.endswith("\n"):
            existing += "\n"
        archive_path.write_text(
            existing + f"\n## {section} — folded {today.isoformat()}\n\n" + body,
            encoding="utf-8",
        )
    else:
        header = (
            "# Archived Knowledge Entries\n\n"
            "_Auto-logged churn folded from IMPROVEMENT.md by fold_patterns. "
            "Moved, not deleted — full history preserved here._\n\n"
        )
        archive_path.write_text(
            header + f"## {section} — folded {today.isoformat()}\n\n" + body,
            encoding="utf-8",
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Fold auto-churn in a DDD doc section.")
    ap.add_argument("--doc", required=True)
    ap.add_argument("--section", default="Patterns Discovered")
    ap.add_argument("--keep-recent", type=int, default=DEFAULT_KEEP_RECENT)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    args = ap.parse_args()

    doc = Path(args.doc)
    content = doc.read_text(encoding="utf-8")
    today = datetime.now(timezone.utc).date()
    result = fold_section(content, args.section, keep_recent=args.keep_recent, today=today)

    before = len(content)
    after = len(result.new_content)
    print(f"section:        {args.section}")
    print(f"curated kept:   {result.curated_kept}")
    print(f"auto kept:      {result.auto_kept}")
    print(f"archived:       {result.archived_count}")
    print(f"chars:          {before} -> {after} (~{before//4} -> {after//4} tok)")
    print(f"mode:           {'APPLY' if args.apply else 'DRY-RUN'}")

    if args.apply and result.new_content != content:
        bak = doc.with_name(f"{doc.name}.{today.isoformat()}.bak")
        if not bak.exists():
            bak.write_text(content, encoding="utf-8")
        if result.archived_bullets:
            _append_archive(
                doc.with_name("IMPROVEMENT-archive.md"),
                result.archived_bullets, args.section, today,
            )
        doc.write_text(result.new_content, encoding="utf-8")
        print(f"wrote:          {doc.name} + {bak.name} + IMPROVEMENT-archive.md")


if __name__ == "__main__":
    main()
