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
            # An explicit command on the user's behalf: a journaled governed edit, so
            # the CDF baseline follows it (nothing writes a DDD doc around it).
            from core.ingestion.governed_edit import apply_user_edit, revision_of
            apply_user_edit(path, new_text.encode("utf-8"), expected_revision=revision_of(path))
        docs[doc] = {"exists": True, "missing": missing_sections(doc, text), "rename": near,
                     "to_add": to_add, "added": to_add if apply else []}
    unresolved = any(not v["exists"] or v.get("rename") for v in docs.values())
    return {"project": project_dir.name, "applied": apply, "unresolved": unresolved, "docs": docs}


# ── what may be written where ────────────────────────────────────────────────
#
# The reverse of CDF's routing matrix (contracts._DDD_MATRIX: topic × type →
# (doc, section)): for each section, the entry types CDF may route into it. A writer
# that bypasses CDF (the New Brain bootstrap, design 2026-10-27 §5.5) checks against
# this table so everything it writes is where CDF would have put it. One table: CDF
# is asked to derive its matrix check from it (design §5.11 C12).
# IMPROVEMENT § What to Watch For holds risks and gaps (no evidence yet, written as
# guideline): WATCH_TYPES. PROJECT § Open Items holds entries of any type the user or a
# session records. PROJECT's Current Focus / Blocked By are live state, written by the
# user, so no writer targets them (USER_ONLY_SECTIONS).
_ALL_TYPES = frozenset({"guideline", "pitfall", "decision", "model", "process",
                        "principle", "correction"})
SECTION_TYPES: dict[tuple[str, str], frozenset[str]] = {
    ("PRODUCT.md", "Vision"): frozenset({"principle"}),
    ("PRODUCT.md", "What Makes It Different"): frozenset({"model"}),
    ("PRODUCT.md", "Strategic Priorities"): frozenset({"decision"}),
    ("PRODUCT.md", "Success Criteria"): frozenset({"guideline", "process"}),
    ("PRODUCT.md", "Non-Goals"): frozenset({"pitfall", "correction"}),
    ("TECH.md", "Architecture"): frozenset({"principle", "decision"}),
    ("TECH.md", "Key Subsystems"): frozenset({"model"}),
    ("TECH.md", "Conventions"): frozenset({"guideline", "process"}),
    ("TECH.md", "Runtime Traps"): frozenset({"pitfall", "correction"}),
    ("IMPROVEMENT.md", "What Worked"): _ALL_TYPES - {"pitfall", "correction"},
    ("IMPROVEMENT.md", "What Failed"): frozenset({"pitfall", "correction"}),
    ("PROJECT.md", "Recent Decisions"): _ALL_TYPES,
}
GAP_SECTION = ("IMPROVEMENT.md", "What to Watch For")
WATCH_TYPES = frozenset({"guideline", "pitfall"})
# Where a new Brain's first entries for each section come from, and what does not belong
# there: the extraction standard the entry contract carries per section
# (core/brain_bootstrap/apply.entry_contract), read while writing, never checked.
SECTION_GUIDE: dict[tuple[str, str], dict[str, str]] = {
    ("PRODUCT.md", "Vision"): {
        "take_from": "what the material says the project is for: the README's positioning, the package description"},
    ("PRODUCT.md", "What Makes It Different"): {
        "take_from": 'comparisons with alternatives the material itself makes ("unlike X, ...")'},
    ("PRODUCT.md", "Strategic Priorities"): {
        "take_from": "what the material calls a priority, a goal or the order of what comes first",
        "not": "a design choice kept for compatibility (Architecture or Recent Decisions) or a rule for "
               "changes (Conventions); none stated: a gap"},
    ("PRODUCT.md", "Success Criteria"): {
        "take_from": "what the material says counts as success: a target, a measure, a definition of done "
                     "for the project",
        "not": "a test, review or install step for contributors or users (Conventions); none stated: a gap"},
    ("PRODUCT.md", "Non-Goals"): {
        "take_from": "what the material says is out of scope or not to be used for",
        "not": "anything the material does not say is out of scope; none stated: a gap"},
    ("TECH.md", "Architecture"): {
        "take_from": "ARCHITECTURE and design documents; the entry points"},
    ("TECH.md", "Key Subsystems"): {
        "take_from": "one per code_shape.graph_clusters cluster that matters: responsibility, entry point, "
                     "what it depends on; sources = the cluster's files (never a graph id). When "
                     "graph_clusters.skipped says the graph was too large to cluster: one per module in "
                     "code_shape.structure.modules that matters (leave out test modules), what it depends "
                     "on from structure.module_edges, the repo's own architecture or key-files index for "
                     "the responsibility; sources = files from the module's files (a directory is not a source)"},
    ("TECH.md", "Conventions"): {
        "take_from": "CONTRIBUTING and the conventions configs; one process per dev command (how = the "
                     "command, sources = the CI, Makefile or package.json that declares it)"},
    ("TECH.md", "Runtime Traps"): {
        "take_from": "each history.hotspots file not marked test, with its commit subjects: the trap it "
                     "shows; security and bugfix CHANGELOG entries; SECURITY.md",
        "not": "the hot-spot list itself or which files change most (it changes with every commit)"},
    ("IMPROVEMENT.md", "What Worked"): {
        "take_from": "usually empty at birth"},
    ("IMPROVEMENT.md", "What Failed"): {
        "take_from": "reverts and repeated fixes, citing the commits' files"},
    ("IMPROVEMENT.md", "What to Watch For"): {
        "take_from": "gaps only: what has no evidence (untested entry points, a document that disagrees "
                     "with the code, an empty PRODUCT or TECH section)"},
    ("PROJECT.md", "Recent Decisions"): {
        "take_from": "accepted ADRs and design-document decisions, with why and rejected_alternatives",
        "not": "a rejected or proposed ADR presented as decided"},
}
GUIDE_EXAMPLES: dict[str, list[str]] = {
    "good": ['[pitfall] Changing the default digest invalidates every issued token. Applies when '
             'upgrading or configuring the digest (a trigger, a consequence, a source)'],
    "bad": ["[model] Uses Python (a fact any file listing shows)",
            "[guideline] The README explains usage (a restatement)",
            "a version number, a count, \"currently working on ...\", anything no source supports"],
}
# The type an entry written with no [type] is given in each section: the one the
# section's skeleton comment names first (ddd_format reads an untyped entry by it).
_DEFAULT_TYPE: dict[tuple[str, str], str] = {
    ("PRODUCT.md", "Vision"): "principle", ("PRODUCT.md", "What Makes It Different"): "model",
    ("PRODUCT.md", "Strategic Priorities"): "decision", ("PRODUCT.md", "Success Criteria"): "guideline",
    ("PRODUCT.md", "Non-Goals"): "pitfall",
    ("TECH.md", "Architecture"): "decision", ("TECH.md", "Key Subsystems"): "model",
    ("TECH.md", "Conventions"): "guideline", ("TECH.md", "Runtime Traps"): "pitfall",
    ("IMPROVEMENT.md", "What Worked"): "guideline", ("IMPROVEMENT.md", "What Failed"): "pitfall",
    ("IMPROVEMENT.md", "What to Watch For"): "guideline",
    ("PROJECT.md", "Recent Decisions"): "decision", ("PROJECT.md", "Open Items"): "guideline",
}


def allowed_types(doc: str, section: str) -> frozenset[str] | None:
    """The entry types that belong in skeleton section *section* of *doc*; None for a
    user-only section (anything the user writes)."""
    if section in USER_ONLY_SECTIONS.get(doc, ()):
        return None
    if (doc, section) == GAP_SECTION:
        return WATCH_TYPES
    if (doc, section) == ("PROJECT.md", "Open Items"):
        return _ALL_TYPES
    return SECTION_TYPES[(doc, section)]


def default_type(doc: str, section: str) -> str:
    return _DEFAULT_TYPE[(doc, section)]


def home_section(doc: str, etype: str) -> str:
    """The section of *doc* CDF routes an entry of type *etype* to (its matrix,
    ``core.ingestion.contracts._DDD_MATRIX``): the first skeleton section whose
    SECTION_TYPES hold the type."""
    for s in skeleton_sections(doc):
        if etype in SECTION_TYPES.get((doc, s), ()):
            return s
    raise KeyError(f"no section of {doc} takes [{etype}]")


def locate_section(section: str) -> tuple[str, str] | None:
    """``(doc, canonical section name)`` for a skeleton section name (compared by
    ``normalize_heading``), or None. Section names are unique across the four docs."""
    key = normalize_heading(section)
    for doc in DOCS:
        for s in skeleton_sections(doc):
            if normalize_heading(s) == key:
                return doc, s
    return None


_TYPED_ENTRY_RE = re.compile(r"^- \[(\w+)\] \*\*(.+?)\*\*")


def typed_entries(text: str) -> list:
    """Entries whose bullet carries an explicit, known ``[type]`` (an untyped
    ``- **Bold** …`` bullet is prose, not an entry)."""
    from core.ddd_entry_lifecycle import VALID_TYPES, parse_entries
    out = []
    for e in parse_entries(text):
        m = _TYPED_ENTRY_RE.match(e.raw_text.split("\n", 1)[0])
        if m and m.group(1) in VALID_TYPES:
            out.append(e)
    return out


#: the docs whose every section needs an entry or a gap: what the Brain is and how it is
#: built; IMPROVEMENT holds the gaps, PROJECT is the person's live state
GAP_DOCS = ("PRODUCT.md", "TECH.md")


def unexplained_sections(doc: str, doc_text: str, improvement_text: str) -> list[str]:
    """Sections of *doc* (one of ``GAP_DOCS``) that hold no typed entry and have no gap
    — a What to Watch For entry whose title starts with the section name as whole words
    ("Vision: no evidence yet"). The one rule shared by the P6 gate and the bootstrap
    writer: a Brain with no material is complete once every such section is a gap, so
    nobody is asked. A gap is an ordinary guideline: if decay archives it, its section
    is unexplained again."""
    watch = [e.title for e in typed_entries(improvement_text)
             if normalize_heading(e.section) == normalize_heading(GAP_SECTION[1])]
    filled = {normalize_heading(e.section) for e in typed_entries(doc_text)}
    return [s for s in skeleton_sections(doc)
            if normalize_heading(s) not in filled
            and not any(starts_with_section(t, s) for t in watch)]
