#!/usr/bin/env python3
"""Sync docs/discussions/ mirror to live GitHub Discussions (number-keyed, edit-aware).

The docs/discussions/ tree is a DeepWiki-facing mirror of the repo's GitHub
Discussions. It was hand-maintained and drifted (stale index counts, missing
files) until this script. Identity is the **discussion number** (the `NN-` file
prefix), NOT the slug — so the sync can never create a duplicate file for a
discussion that already has a mirror under a differently-slugged name.

Approach A1 (edit-aware):
  - ADD       — a live discussion with no mirror file → create `NN-<slug>.md`.
  - UPDATE    — mirror exists AND live body was edited since the mirror's
                `updated:` date (or title/body differ) → rewrite body, preserving
                any editorial `>` blockquote header lines.
  - UNCHANGED — otherwise.
  - Index     — README.md discussion table is always rebuilt (sorted by number);
                the hand-curated Themes section is preserved verbatim.

Idempotency: the frontmatter `updated:` field is derived from the live
`lastEditedAt || createdAt` timestamp (a STABLE value), never `today()` — so a
second `--write` immediately after the first reports zero changes.

Modes:
  --check   fetch live, compute drift, print summary, exit 1 if any drift, WRITE NOTHING.
  --write   apply ADD/UPDATE + rebuild index.

Fetching uses the `gh` CLI (must be authenticated). `fetch_discussions` is an
injectable seam so tests drive the real path-resolution / content-generation /
classification logic without network.

Usage:
    python scripts/sync_discussions.py --check
    python scripts/sync_discussions.py --write
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

OWNER = "xg-gh-25"
REPO = "SwarmAI"
DOCS_DIR = Path("docs/discussions")
README = "README.md"
BASE_URL = f"https://github.com/{OWNER}/{REPO}/discussions"

# Categories seen in the live repo (for the index Category column).
CATEGORY_ORDER = ("Announcements", "General", "Ideas", "Q&A", "Show and tell")


@dataclass
class Discussion:
    """A live GitHub discussion (the fields the mirror needs)."""

    number: int
    title: str
    body: str
    created_at: str  # ISO8601, e.g. 2026-06-29T05:37:26Z
    last_edited_at: str | None  # ISO8601 or None (never body-edited)
    category: str

    @property
    def created_date(self) -> str:
        return self.created_at[:10]

    @property
    def updated_date(self) -> str:
        """Stable 'updated' date: last body edit, else creation. Never today()."""
        src = self.last_edited_at or self.created_at
        return src[:10]

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.number}"


# --------------------------------------------------------------------------- #
# Slug generation (only for genuinely-new numbers — never re-slugs existing)  #
# --------------------------------------------------------------------------- #

def slugify(title: str, max_len: int = 60) -> str:
    """Generate a filename slug from a title.

    Lowercase; keep word chars (incl. CJK via \\w with re.UNICODE) and spaces;
    collapse runs of non-word chars to a single hyphen; trim to max_len on a
    hyphen boundary so we never cut a multibyte char mid-sequence.
    """
    s = title.lower().strip()
    # Replace any run of chars that are NOT word-chars into a hyphen.
    s = re.sub(r"[^\w\s-]", " ", s, flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s, flags=re.UNICODE)
    s = s.strip("-")
    if len(s) > max_len:
        s = s[:max_len].rsplit("-", 1)[0].rstrip("-") or s[:max_len].rstrip("-")
    return s


def new_filename(disc: Discussion) -> str:
    # Existing mirror files zero-pad numbers <10 (e.g. 02-, 06-). Match that so
    # new single-digit numbers don't collide with a differently-formatted name.
    return f"{disc.number:02d}-{slugify(disc.title)}.md"


# --------------------------------------------------------------------------- #
# Mirror file indexing + content generation                                   #
# --------------------------------------------------------------------------- #

_NUM_PREFIX = re.compile(r"^(\d+)-.*\.md$")


def index_mirror_files(docs_dir: Path) -> dict[int, Path]:
    """Map discussion number → existing mirror file path (by NN- prefix)."""
    out: dict[int, Path] = {}
    for p in sorted(docs_dir.iterdir()):
        if not p.is_file():
            continue
        m = _NUM_PREFIX.match(p.name)
        if m:
            num = int(m.group(1))
            if num in out:
                # Two mirror files for the same discussion number (e.g. 2-x.md
                # AND 02-y.md) — ambiguous identity. Fail loud rather than
                # silently overwrite one. [Gate-2 LOW fix]
                raise ValueError(
                    f"Duplicate mirror files for discussion #{num}: "
                    f"{out[num].name} and {p.name} — remove one."
                )
            out[num] = p
    return out


def parse_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML frontmatter — mirrors lint_doc_frontmatter.parse_frontmatter."""
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    fields: dict[str, str] = {}
    for line in content[3:end].strip().split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields


def extract_editorial_header(content: str) -> list[str]:
    """Return the editorial `>` blockquote lines that sit between the
    `<!-- GitHub Discussion #N -->` comment and the body, if any.

    Legacy mirror files carry a curated cross-reference line, e.g.:
        <!-- GitHub Discussion #86 ... -->
        > 🌐 English | 中文版 → #87 · Series: #84 WRITE Path

    We preserve these on UPDATE so the sync never destroys curated cross-refs.
    """
    lines = content.splitlines()
    # find the discussion comment marker
    idx = next((i for i, l in enumerate(lines) if l.startswith("<!-- GitHub Discussion #")), None)
    if idx is None:
        return []
    # The editorial header is GLUED to the comment marker: the blockquote line(s)
    # that immediately follow it with NO intervening blank line. A blank line
    # separates the header from the body — so the FIRST blank line ends the header
    # zone, and a body that leads with a blockquote (even one containing `#N`,
    # e.g. "> Related: #99") is separated by that blank line and never captured.
    # This positional rule is robust; a content regex on `#N` is NOT (a body
    # cross-ref line looks identical to an editorial one). [Gate-2 CRITICAL fix]
    header: list[str] = []
    for l in lines[idx + 1:]:
        if l.strip() == "":
            break                 # first blank line ends the header zone
        if l.startswith(">"):
            header.append(l)
        else:
            break                 # first non-blockquote line ends the header zone
    return header


def strip_created_date(content: str) -> str | None:
    """Preserve the original `created:` from an existing mirror if present."""
    fm = parse_frontmatter(content)
    return fm.get("created")


def render_file(disc: Discussion, existing: str | None) -> str:
    """Render the full mirror-file content for a discussion.

    On UPDATE (existing given): preserve the original `created:` date and any
    editorial `>` header lines. Title/updated/body come from live.
    """
    created = disc.created_date
    editorial: list[str] = []
    if existing is not None:
        created = strip_created_date(existing) or created
        editorial = extract_editorial_header(existing)

    title_escaped = disc.title.replace('"', '\\"')
    parts = [
        "---",
        f'title: "{title_escaped}"',
        f"created: {created}",
        f"updated: {disc.updated_date}",
        "status: published",
        "---",
        f"<!-- GitHub Discussion #{disc.number}: {disc.url} -->",
    ]
    if editorial:
        parts.extend(editorial)
    header = "\n".join(parts)
    body = (disc.body or "").rstrip("\n")  # null body (empty discussion) → empty, not crash [Gate-2 HIGH]
    return f"{header}\n\n{body}\n"


# --------------------------------------------------------------------------- #
# Classification                                                              #
# --------------------------------------------------------------------------- #

@dataclass
class Change:
    number: int
    kind: str  # "add" | "update" | "unchanged" | "legacy_skip"
    path: Path
    reason: str
    content: str  # desired content (empty for legacy_skip)


def classify(disc: Discussion, existing_files: dict[int, Path], docs_dir: Path) -> Change:
    """Decide ADD / UPDATE / UNCHANGED / LEGACY_SKIP for one live discussion.

    Design (A1 edit-aware, narrowed after live probe): the mirror bodies are
    EDITORIALIZED (legacy `# H1 + > 📎` header, recent `> 🌐` cross-refs) and are
    NOT verbatim copies of live bodies — so raw content-comparison always shows
    spurious drift. We therefore drive UPDATE off the STABLE edit date, never raw
    content:

      - number not mirrored          → ADD (create frontmatter file from live)
      - mirror file has NO frontmatter (legacy format) → LEGACY_SKIP (never
        auto-reformat; a format migration is a separate opt-in, not this tool's
        job — this bounds blast radius to the drift class that actually occurs)
      - live edited AFTER mirror `updated:` → UPDATE (genuine body edit)
      - otherwise                    → UNCHANGED
    """
    if disc.number not in existing_files:
        path = docs_dir / new_filename(disc)
        return Change(disc.number, "add", path, "no mirror file", render_file(disc, None))

    path = existing_files[disc.number]
    existing = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(existing)

    if not fm:
        # Legacy no-frontmatter file — leave it exactly as is.
        return Change(disc.number, "legacy_skip", path, "legacy format (no frontmatter)", "")

    live_upd = disc.updated_date
    mirror_upd = fm.get("updated", "")
    if live_upd > mirror_upd:
        desired = render_file(disc, existing)
        return Change(disc.number, "update", path,
                      f"live edited {live_upd} > mirror {mirror_upd or '(none)'}", desired)
    return Change(disc.number, "unchanged", path, "", existing)


# --------------------------------------------------------------------------- #
# README index rebuild                                                        #
# --------------------------------------------------------------------------- #

_INDEX_HEADER = "| # | Title | Category | Date |"


def build_index_table(discs: list[Discussion], paths: dict[int, Path]) -> str:
    """Build the markdown index table (sorted by number)."""
    rows = [_INDEX_HEADER, "|---|-------|----------|------|"]
    for d in sorted(discs, key=lambda x: x.number):
        fname = paths[d.number].name
        title = d.title.replace("|", "\\|")
        rows.append(f"| {d.number} | [{title}]({fname}) | {d.category} | {d.created_date} |")
    return "\n".join(rows)


def rebuild_readme(readme_content: str, table: str) -> str:
    """Replace the index table in README, preserving everything else (incl. Themes).

    The table is the block starting at the `| # | Title ...` header line through
    the last consecutive table row. Everything before and after is preserved.
    """
    lines = readme_content.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == _INDEX_HEADER), None)
    if start is None:
        raise ValueError("README index header not found — cannot rebuild table")
    # table = header + separator + all following '|' rows, bounded at the FIRST
    # blank line. Bounding on blank (not "first non-| line") means an ADJACENT
    # markdown table — e.g. the hand-curated `| Theme | Key Articles |` Themes
    # table — can never be consumed even if its separator formatting changes.
    # [Gate-2 MEDIUM fix: preserve Themes verbatim structurally, not incidentally]
    end = start + 1
    while end < len(lines) and lines[end].strip() and lines[end].lstrip().startswith("|"):
        end += 1
    new_lines = lines[:start] + table.splitlines() + lines[end:]
    return "\n".join(new_lines) + ("\n" if readme_content.endswith("\n") else "")


# --------------------------------------------------------------------------- #
# Fetch (injectable seam)                                                     #
# --------------------------------------------------------------------------- #

def fetch_discussions() -> list[Discussion]:
    """Fetch all live discussions via the gh CLI. Raises on failure."""
    query = """
    { repository(owner: "%s", name: "%s") {
        discussions(first: 100, orderBy: {field: CREATED_AT, direction: ASC}) {
          nodes { number title body createdAt lastEditedAt category { name } }
    } } }
    """ % (OWNER, REPO)
    proc = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh api graphql failed: {proc.stderr.strip()}")
    nodes = json.loads(proc.stdout)["data"]["repository"]["discussions"]["nodes"]
    return [
        Discussion(
            number=n["number"], title=n["title"], body=n["body"],
            created_at=n["createdAt"], last_edited_at=n.get("lastEditedAt"),
            category=n["category"]["name"],
        )
        for n in nodes
    ]


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #

def compute_sync(discs: list[Discussion], docs_dir: Path):
    """Compute all changes + the desired README. Pure (no writes).

    Returns (changes, existing_files_incl_new, readme_old, readme_new_or_None).
    """
    existing = index_mirror_files(docs_dir)
    changes = [classify(d, existing, docs_dir) for d in discs]

    # path map for the index: existing files reused, new files get their new path
    paths = dict(existing)
    for c in changes:
        paths[c.number] = c.path

    table = build_index_table(discs, paths)
    readme_path = docs_dir / README
    readme_old = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    readme_new = rebuild_readme(readme_old, table) if readme_old else None
    return changes, paths, readme_old, readme_new


def run(mode: str, docs_dir: Path = DOCS_DIR, fetcher=fetch_discussions) -> int:
    """Execute the sync. mode in {'check','write'}. Returns exit code."""
    discs = fetcher()
    changes, _paths, readme_old, readme_new = compute_sync(discs, docs_dir)

    adds = [c for c in changes if c.kind == "add"]
    updates = [c for c in changes if c.kind == "update"]
    legacy = [c for c in changes if c.kind == "legacy_skip"]
    index_drift = readme_new is not None and readme_new != readme_old

    print(f"Discussions: {len(discs)} live")
    print(f"  add:         {len(adds)}")
    print(f"  update:      {len(updates)}")
    print(f"  unchanged:   {len(changes) - len(adds) - len(updates) - len(legacy)}")
    print(f"  legacy-skip: {len(legacy)} (no frontmatter — not auto-reformatted)")
    print(f"  index:       {'DRIFT' if index_drift else 'in sync'}")
    for c in adds + updates:
        print(f"    [{c.kind:6}] #{c.number} {c.path.name} — {c.reason}")

    # legacy_skip does NOT count as drift — those files are intentionally left as-is.
    has_drift = bool(adds or updates or index_drift)

    if mode == "check":
        if has_drift:
            print("\nDRIFT DETECTED — run with --write to sync.")
            return 1
        print("\nMirror in sync.")
        return 0

    # write mode
    written = 0
    for c in adds + updates:
        c.path.write_text(c.content, encoding="utf-8")
        written += 1
    if index_drift:
        (docs_dir / README).write_text(readme_new, encoding="utf-8")
        print("  README index rebuilt.")
    print(f"\nWrote {written} file(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync docs/discussions/ mirror to live GitHub Discussions.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="report drift, exit 1 if any, write nothing")
    g.add_argument("--write", action="store_true", help="apply the sync")
    args = ap.parse_args(argv)
    mode = "check" if args.check else "write"
    try:
        return run(mode)
    except Exception as e:  # noqa: BLE001 — top-level CLI guard, surface reason
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
