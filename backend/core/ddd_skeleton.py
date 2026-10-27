"""The DDD skeleton — the one template every new brain's four docs start from.

Design docs/design/2026-10-27-new-brain-initial-ddd.md §5.1: one source,
``backend/templates/ddd-skeleton/{PRODUCT,TECH,IMPROVEMENT,PROJECT}.md``. Each holds
only a title, the section headings and ``<!-- -->`` guidance — no placeholder text
that could pass for content. The section set is the set CDF writes into plus the
user-only live-state sections of PROJECT.md.

Readers: provisioning (``swarm_workspace_manager.provision_project_ddd``), the P6
completeness gate (structure + "still the skeleton" checks) and
``artifact_cli ddd-skeleton-align`` (adds the missing sections to an existing brain).
SwarmAI's own default project is seeded from ``templates/swarmai-seed/`` instead —
that is SwarmAI's product content, never a template for other brains.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from core.project_registry import DDD_CANONICAL_DOCS as DOCS

SKELETON_DIR = Path(__file__).resolve().parent.parent / "templates" / "ddd-skeleton"
# Sections only the user writes (live state): never required to hold an entry.
USER_ONLY_SECTIONS: dict[str, tuple[str, ...]] = {"PROJECT.md": ("Current Focus", "Blocked By")}

_H2_RE = re.compile(r"^## (?!#)(.+?)\s*$")


@lru_cache(maxsize=1)
def load_skeleton() -> dict[str, str]:
    """``{filename: template text}`` (with the ``{project_name}`` placeholder).
    Raises OSError when the bundled skeleton is missing (a broken install)."""
    return {name: (SKELETON_DIR / name).read_text(encoding="utf-8") for name in DOCS}


def render_skeleton(filename: str, project_name: str) -> str:
    """The skeleton of *filename* for *project_name* — what a fresh brain gets."""
    return load_skeleton()[filename].replace("{project_name}", project_name)


def normalize_heading(text: str) -> str:
    """Heading text compared by words, case-insensitively."""
    return " ".join(text.split()).casefold()


_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def headings(text: str) -> list[str]:
    """The ``## `` headings of a markdown text, in order (``###`` and anything inside
    a fenced code block excluded)."""
    out = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _H2_RE.match(line)
        if m:
            out.append(m.group(1).strip())
    return out


def starts_with_section(heading_or_title: str, section: str) -> bool:
    """True when *heading_or_title* begins with *section* as whole words
    ("Key Subsystems (shape)" yes, "Key Subsystemsless" no)."""
    a, key = normalize_heading(heading_or_title), normalize_heading(section)
    return a == key or (a.startswith(key) and not a[len(key)].isalnum())


def skeleton_sections(filename: str) -> list[str]:
    """The sections the skeleton gives *filename*, in order."""
    return headings(load_skeleton()[filename])


def missing_sections(filename: str, text: str) -> list[str]:
    """Skeleton sections absent from *text* (headings compared by ``normalize_heading``)."""
    have = {normalize_heading(h) for h in headings(text)}
    return [s for s in skeleton_sections(filename) if normalize_heading(s) not in have]


def near_matches(filename: str, text: str) -> dict[str, str]:
    """``{missing skeleton section: existing heading}`` where an existing heading starts
    with the section name (e.g. ``Key Subsystems (architecture shape)`` for ``Key
    Subsystems``) — reported, never auto-merged, so a long section never gets an empty
    twin beside it."""
    have = headings(text)
    out: dict[str, str] = {}
    for section in missing_sections(filename, text):
        for h in have:
            if starts_with_section(h, section):
                out[section] = h
                break
    return out


def is_skeleton(filename: str, text: str, project_name: str) -> bool:
    """True when *text* is the untouched skeleton (ignoring trailing whitespace)."""
    return text.rstrip() == render_skeleton(filename, project_name).rstrip()


def align(filename: str, text: str) -> tuple[list[str], dict[str, str], str]:
    """``(added, near, new_text)``: append every missing section that has no near match
    as ``## <section>`` at the end of *text*. Existing bytes are never changed."""
    near = near_matches(filename, text)
    added = [s for s in missing_sections(filename, text) if s not in near]
    if not added:
        return [], near, text
    nl = "\r\n" if "\r\n" in text else "\n"   # keep the file's own line ending
    tail = nl.join(f"## {s}{nl}" for s in added)
    if not text.strip():
        return added, near, tail
    head = text if text.endswith(("\n", "\r\n")) else text + nl
    return added, near, head + nl + tail


def align_project(project_dir: str | Path, *, apply: bool = False) -> dict:
    """Report (and with *apply*, add) the skeleton sections an existing brain lacks.

    Per doc: ``missing`` sections; ``rename`` — headings that only resemble a missing
    section ({section: existing heading}), never given an empty twin: rename them;
    ``to_add`` — sections a bare ``## <section>`` heading would add; ``added`` — those
    actually appended (only with *apply*). ``unresolved`` is True when any doc is
    missing or any rename remains. Existing bytes, line endings included, are never
    changed; a missing doc is reported, never created (that is provisioning's job).
    """
    from core.ddd_paths import ddd_path
    project_dir = Path(project_dir)
    docs: dict[str, dict] = {}
    for doc in DOCS:
        path = ddd_path(project_dir, doc)
        if not path.exists():
            docs[doc] = {"exists": False}
            continue
        with open(path, encoding="utf-8", newline="") as fh:  # keep \r\n as is
            text = fh.read()
        to_add, near, new_text = align(doc, text)
        if apply and to_add:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(new_text)
        docs[doc] = {"exists": True, "missing": missing_sections(doc, text), "rename": near,
                     "to_add": to_add, "added": to_add if apply else []}
    unresolved = any(not v["exists"] or v.get("rename") for v in docs.values())
    return {"project": project_dir.name, "applied": apply, "unresolved": unresolved, "docs": docs}
