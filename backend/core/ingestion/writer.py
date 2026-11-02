"""Journaled CDF corpus writer, recovery, and baseline management.

- ``apply_unit`` materializes one judged unit through an immutable write plan.
- ``recover`` resumes plans from postconditions and abandons stale plans.
- ``ensure_baseline`` and ``detect_out_of_band`` preserve baseline sovereignty.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from config import get_app_data_dir, get_swarmws
from core.ddd_entry_lifecycle import format_memory_entry
from utils.file_lock import md_lock

from .contracts import UnitDecision
from .contracts_v2 import ROOT_TRUST_ORDER, SYSTEM_APPENDIX_HEADING, find_system_appendix
from .materializer import get_materializer, iter_governed_files
from .units import parse_units

_BASELINE_VERSION = 1
_SLUG = re.compile(r"[^a-z0-9]+")
_H2 = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_H3 = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
_SYSTEM_APPENDIX_STORES = frozenset({"STEERING", "USER", "TOOLS"})
_SYSTEM_APPENDIX_DEFAULT_SECTIONS = {
    "STEERING": "Standing Rules",
    "USER": "Preferences",
    "TOOLS": "Environment Notes",
}

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path_hash(path: Path) -> str:
    try:
        return _hash(path.read_bytes())
    except FileNotFoundError:
        return ""


def _row_value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return getattr(row, name, default)


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _decision_value(decision: UnitDecision | Mapping[str, Any], name: str, default: Any = None) -> Any:
    if isinstance(decision, Mapping):
        return decision.get(name, default)
    return getattr(decision, name, default)


def _model_value(model: Any, name: str, default: Any = None) -> Any:
    if model is None:
        return default
    if isinstance(model, Mapping):
        return model.get(name, default)
    return getattr(model, name, default)


def _workspace() -> Path:
    return Path(get_swarmws()).resolve(strict=False)


def _relative(path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(_workspace()).as_posix()
    except ValueError:
        return path.resolve(strict=False).as_posix()


def _absolute(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else _workspace() / path


def _target_path(decision: UnitDecision | Mapping[str, Any], unit_id: str, group: dict[str, Any]) -> Path:
    destination = _decision_value(decision, "destination") or _decision_value(decision, "card2")
    relative = str(_model_value(destination, "relative_path", ""))
    target = _workspace() / relative
    store = str(_model_value(destination, "store", ""))
    if store not in {"KNOWLEDGE", "COMMON_DDD"} or target.suffix == ".md":
        return target
    title = str(group.get("title") or group.get("text") or unit_id)
    slug = _SLUG.sub("-", title.lower()).strip("-") or "reference"
    safe_id = _SLUG.sub("-", unit_id.lower()).strip("-")
    return target / f"{slug}-{safe_id}.md"


def _section_bounds(content: str, section: str) -> tuple[int, int]:
    heading = re.compile(rf"^##\s+{re.escape(section)}\s*$", re.MULTILINE)
    match = heading.search(content)
    if match is None:
        raise ValueError(f"missing_section:{section}")
    next_heading = re.search(r"^##\s+", content[match.end():], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(content)
    return match.end(), end


# a closing footer: a ``---`` rule and text with no heading up to the end of the file
_FOOTER = re.compile(r"\n---[ \t]*\n(?:(?!^## ).)*\Z", re.MULTILINE | re.DOTALL)


def _append_section(content: str, section: str | None, block: str) -> str:
    if not section:  # STEERING / USER / TOOLS route with no section: append at end of file
        return f"{content.rstrip(chr(10))}\n\n{block.strip()}\n" if content.strip() else f"{block.strip()}\n"
    _, end = _section_bounds(content, section)
    footer = _FOOTER.search(content)
    if footer and footer.start() < end:      # the last section: the entry goes before the footer
        end = footer.start() + 1
    before = content[:end].rstrip("\n")
    after = content[end:].lstrip("\n")
    joined = f"{before}\n\n{block.strip()}\n"
    return joined + (f"\n{after}" if after else "")


def _append_system_appendix(content: str, store: str, section: str | None, block: str) -> str:
    """Append one extracted unit under the store's appendix-only subheading. An existing
    appendix under the legacy heading is the appendix (no second one is started)."""
    subsection = section or _SYSTEM_APPENDIX_DEFAULT_SECTIONS[store]
    appendix = find_system_appendix(content)
    if appendix is None:
        prefix = content.rstrip("\n")
        separator = "\n\n" if prefix else ""
        return (
            f"{prefix}{separator}{SYSTEM_APPENDIX_HEADING}\n\n"
            f"### {subsection}\n\n{block.strip()}\n"
        )

    next_h2 = _H2.search(content, appendix.end())
    appendix_end = next_h2.start() if next_h2 else len(content)
    appendix_body = content[appendix.end():appendix_end]
    subsection_match = re.search(
        rf"^###\s+{re.escape(subsection)}\s*$", appendix_body, re.MULTILINE
    )
    if subsection_match is None:
        before = content[:appendix_end].rstrip("\n")
        after = content[appendix_end:].lstrip("\n")
        added = f"### {subsection}\n\n{block.strip()}\n"
        return f"{before}\n\n{added}" + (f"\n{after}" if after else "")

    subsection_start = appendix.end() + subsection_match.end()
    next_h3 = _H3.search(content, subsection_start, appendix_end)
    insert_at = next_h3.start() if next_h3 else appendix_end
    before = content[:insert_at].rstrip("\n")
    after = content[insert_at:].lstrip("\n")
    return f"{before}\n\n{block.strip()}\n" + (f"\n{after}" if after else "")


def _multi_unit_frontmatter(title: str, summary: str) -> str:
    return "\n".join([
        "---", f"title: {_meta_value(title)}", f"summary: {_meta_value(summary)}",
        "reference_kind: learned", "ontology_types: []",
        "source: n/a (multi-unit)", "ingestion_candidate: n/a (multi-unit)",
        f"created: {_now().date().isoformat()}", "---", "",
    ])


def _ensure_common_ddd(content: str, target: Path, section: str | None) -> str:
    if content.strip():
        value = content
    else:
        title = target.stem.replace("-", " ").title()
        value = (_multi_unit_frontmatter(title, f"This document holds reusable cognition for {title}.")
                 + f"# {title}\n")
    headings = {" ".join(match.group(1).split()) for match in _H2.finditer(value)}
    required = [name for name in (section, "Open Gaps") if name]
    for name in required:
        if " ".join(name.split()) not in headings:
            value = value.rstrip() + f"\n\n## {name}\n"
            headings.add(" ".join(name.split()))
    return value


def _ensure_link_file(content: str, target: Path) -> str:
    if content.strip():
        return content
    host = target.stem
    return (_multi_unit_frontmatter(
        f"Links from {host}", f"This document indexes retained links from {host}.")
            + f"# Links from {host}\n")


def _timeline_section(group: Mapping[str, Any]) -> str:
    """The TIMELINE section an event goes under: its month, or "Not injected" when it
    cites external content (the plan's anchor names the same section)."""
    if group.get("cited_external"):
        return "Not injected"
    return str(group.get("when") or _now().date().isoformat())[:7]


def _append_timeline(content: str, group: dict[str, Any], block: str) -> str:
    value = content if content.strip() else "# Timeline\n"
    section = _timeline_section(group)
    pattern = re.compile(rf"^##\s+{re.escape(section)}\s*$", re.MULTILINE)
    match = pattern.search(value)
    if match is None:
        if section == "Not injected":
            return value.rstrip() + f"\n\n## Not injected\n\n{block.strip()}\n"
        not_injected = re.search(r"^##\s+Not injected\s*$", value, re.MULTILINE)
        insert_at = not_injected.start() if not_injected else len(value)
        before = value[:insert_at].rstrip()
        after = value[insert_at:].lstrip()
        addition = f"## {section}\n\n{block.strip()}"
        return before + "\n\n" + addition + "\n" + ("\n" + after if after else "")
    next_heading = re.search(r"^##\s+", value[match.end():], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(value)
    before = value[:end].rstrip()
    after = value[end:].lstrip()
    return before + f"\n\n{block.strip()}\n" + ("\n" + after if after else "")


def product_sections(store: str) -> frozenset[str]:
    """The sections the product defines for an every-turn store by the unit's type
    (MEMORY: ``MEMORY_TYPE_TO_SECTION``; EVOLUTION: corrections and competence), so the
    writer adds a missing one itself; a DDD document's structure is the person's and a
    missing section there waits for them (``pipeline_v2._section_exists``)."""
    if store == "MEMORY":
        from core.ddd_entry_lifecycle import MEMORY_TYPE_TO_SECTION
        return frozenset(MEMORY_TYPE_TO_SECTION.values())
    if store == "EVOLUTION":
        return frozenset({"Corrections Captured", "Competence Learned"})
    return frozenset()


def _ensure_product_section(content: str, store: str, section: str | None) -> str:
    """*content* with ``## <section>`` added when it is one of the store's product
    sections and missing: before a closing ``---`` footer at the end, else at the end."""
    if not section or section not in product_sections(store):
        return content
    if re.search(rf"^##\s+{re.escape(section)}\s*$", content, re.MULTILINE):
        return content
    footer = _FOOTER.search(content)
    head, tail = (content[:footer.start()], content[footer.start():]) if footer else (content, "")
    return f"{head.rstrip(chr(10))}\n\n## {section}\n{tail if tail else ''}"


def _compose_intended(content: str, target: Path, store: str, section: str,
                      group: dict[str, Any], block: str) -> bytes:
    content = _ensure_product_section(content, store, section or None)
    if store == "TIMELINE":
        return _append_timeline(content, group, block).encode("utf-8")
    if store == "COMMON_DDD":
        seeded = _ensure_common_ddd(content, target, section or None)
        return _append_section(seeded, section or None, block).encode("utf-8")
    if store == "KNOWLEDGE" and target.parent.name == "Links":
        seeded = _ensure_link_file(content, target)
        return (seeded.rstrip() + "\n\n" + block.strip() + "\n").encode("utf-8")
    if store == "KNOWLEDGE":
        return (block.rstrip() + "\n").encode("utf-8")
    if store in _SYSTEM_APPENDIX_STORES:
        return _append_system_appendix(content, store, section or None, block).encode("utf-8")
    return _append_section(content, section or None, block).encode("utf-8")


def _title_and_body(group: dict[str, Any], answer: Any) -> tuple[str, str]:
    text = str(group.get("text") or group.get("what") or group.get("body") or "").strip()
    title = str(group.get("title") or "").strip()
    if not title:
        claims = list(_model_value(answer, "claims", []) or [])
        title = str(claims[0] if claims else "Untitled")
    # whitespace and a leading list marker only: stripping "#" or "." from the ends
    # changed meaning ("Use C#" -> "Use C", ".NET pinning" -> "NET pinning")
    title = re.sub(r"^(?:[-*+]\s+)+", "", _one_line(title)).strip() or "Untitled"
    return title, text


def _evolution_prefix(group: Mapping[str, Any], answer: Any) -> str:
    """C for a correction, K for the rest: from the unit's own type (the extractor
    declares it), or a legacy card 1 answer's agent_kind."""
    correction = (str(group.get("type") or "") == "correction"
                  or str(_model_value(answer, "agent_kind", "")) == "correction")
    return "C" if correction else "K"


def _one_line(value: Any) -> str:
    """*value* on one line: an entry in a per-turn file is one line, and a field with a
    line break ended the entry early or started a new heading in MEMORY.md."""
    # every line break a reader may honour (\r, \u2028, ... — str.splitlines' set)
    return " ".join(part.strip() for part in str(value or "").splitlines() if part.strip())


def _meta_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _cognition_body(group: dict[str, Any], fallback: str) -> str:
    """The entry text as a reader reads it, labelled as in DDD entries (the field names
    "what: ...; applicability: ..." were written into MEMORY / EVOLUTION verbatim)."""
    fields = [("", group.get("what")), ("**How:** ", group.get("how")),
              ("**Why:** ", group.get("why")), ("**Applies when:** ", group.get("applicability")),
              ("**Rejected:** ", group.get("rejected_alternatives"))]
    values = [f"{label}{value}" for label, value in fields if value not in (None, "")]
    return " ".join(values) if values else fallback


def _ddd_entry(group: Mapping[str, Any], title: str, fallback_body: str,
               answer: Any, effective: Any) -> dict[str, Any]:
    """The unit's fields in the shape ``ddd_entry_lifecycle.render_ddd_unit`` takes.

    Fields are copied whole, never shortened. A gap is written as a guideline (gap is
    not an ontology type, new-brain design §5.5): ``proposed_next`` is its ``how`` and
    its ``gap_kind`` its ``applicability``. Card 3b's merged text, when present,
    replaces the body (it already folds the old entry's claims in).
    """
    if str(group.get("kind") or "cognition") == "gap":
        entry: dict[str, Any] = {
            "type": "gap", "title": title, "what": group.get("what") or fallback_body,
            "why": group.get("why"), "how": group.get("proposed_next"),
            "applicability": f"gap kind: {group.get('gap_kind') or 'unknown'}"}
    else:
        entry = {
            "type": str(group.get("type") or _model_value(
                answer, "entry_type", group.get("type_hint") or "guideline")),
            "title": title, "what": group.get("what") or fallback_body,
            "how": group.get("how"), "why": group.get("why"),
            "applicability": group.get("applicability"),
            "rejected": group.get("rejected_alternatives")}
    if effective:
        entry = {"type": entry["type"], "title": title, "what": str(effective).strip()}
    return {k: v for k, v in entry.items() if v not in (None, "")}


def _render_ddd(unit_id: str, group: Mapping[str, Any], *, answer: Any = None,
                effective: Any = None, project_hint: Any = None) -> str:
    """One DDD / common-DDD unit through THE renderer (new-brain design §5.11 C3).

    ``core.ddd_entry_lifecycle.render_ddd_unit`` writes the entry and its metadata
    line, ``taint:external`` included, so ``parse_entries`` reads back exactly what was
    written. The writer adds only its own identity marker, ``<!-- cand:<id> -->``, at
    the end of the entry line (plan recovery finds the unit by it), and in a common-DDD
    file the unit's ``project_hint`` inside that same marker. Nothing is added to the
    metadata comment: a field outside its fixed order (``_META_RE``) would make the
    whole comment unreadable, ``taint`` included.
    """
    from core.ddd_entry_lifecycle import render_ddd_unit

    title, fallback_body = _title_and_body(dict(group), answer)
    # render_ddd_unit refuses "|" and "**" in a title (they end the bold title or a
    # table cell); refused, the unit was re-judged and refused again forever
    title = title.replace("**", "").replace("|", "/")
    source = re.sub(r"\W", "_", str(group.get("source") or "ingestion")) or "ingestion"
    rendered = render_ddd_unit(
        _ddd_entry(group, title, fallback_body, answer, effective),
        date_str=_now().date().isoformat(), source=source,
        taint="external" if bool(group.get("cited_external")) else None)
    lead, _, meta = rendered.partition("\n")
    marker = f"cand:{unit_id}"
    if project_hint:
        marker += f" | project_hint:{_meta_value(project_hint)}"
    return f"{lead} <!-- {marker} -->\n{meta}"


# the text fields of an entry (one line in a governed file); a reference is a document
_ENTRY_TEXT_FIELDS = frozenset({"title", "what", "how", "why", "applicability", "rejected_alternatives",
                                "impact", "outcome", "proposed_next", "text"})


def _defang(text: str) -> str:
    """An HTML comment marker inside an entry would open or close the metadata comment
    of its line (and cut the text after it for the prompt): written as entities, it reads
    the same and is no marker (design 2026-10-30-cdf-agent-decides D6)."""
    return text.replace("<!--", "&lt;!--").replace("-->", "--&gt;")


def _render_block(decision: UnitDecision | Mapping[str, Any], unit_id: str,
                  group: dict[str, Any], *, sequence: int = 1) -> str:
    destination = _decision_value(decision, "destination") or _decision_value(decision, "card2")
    store = str(_model_value(destination, "store", ""))
    if str(group.get("kind") or ("reference" if store == "KNOWLEDGE" else "cognition")) != "reference":
        group = {k: _defang(v) if k in _ENTRY_TEXT_FIELDS and isinstance(v, str) else v
                 for k, v in group.items()}
    card3b = _decision_value(decision, "card3b")
    answer = _model_value(card3b, "card1") or _decision_value(decision, "card1")
    effective = _model_value(card3b, "effective_unit")
    title, fallback_body = _title_and_body(group, answer)
    kind = str(group.get("kind") or ("reference" if store == "KNOWLEDGE" else "cognition"))
    source = str(group.get("source") or "ingestion")
    project_hint = group.get("project_hint") or _model_value(destination, "project")
    marked = bool(group.get("cited_external"))
    today = _now().date().isoformat()

    if kind == "event" or store == "TIMELINE":
        event_key = str(group.get("event_key") or
                        _decision_value(decision, "version_vector", {}).get("event_key", ""))
        # each field on its own line: a line break inside one would start a heading,
        # i.e. a forged event, in a file read every turn
        lines = [f"### {_one_line(group.get('when', today))} {_one_line(title)}",
                 f"- what: {_one_line(group.get('what', fallback_body))}"]
        for name in ("impact", "outcome"):
            if group.get(name) is not None:
                lines.append(f"- {name}: {_one_line(group[name])}")
        meta = [f"cand:{unit_id}", f"event_key:{event_key}", f"source:{source}",
                f"event_kind:{group.get('event_kind', 'event')}"]
        if project_hint:
            meta.append(f"project_hint:{_meta_value(project_hint)}")
        if marked:
            meta.append("taint:external")
        lines.append("<!-- " + " | ".join(meta) + " -->")
        return "\n".join(lines)

    if kind == "reference":
        reference_kind = str(group.get("reference_kind") or
                             _model_value(answer, "reference_kind", "note"))
        if reference_kind == "link":
            meta = [f"cand:{unit_id}", f"source:{source}",
                    f"summary:{_meta_value(group.get('summary', ''))}"]
            for name in ("retrieved_at", "content_hash"):
                if group.get(name) is not None:
                    meta.append(f"{name}:{_meta_value(group[name])}")
            if project_hint:
                meta.append(f"project_hint:{_meta_value(project_hint)}")
            meta.append("taint:external")
            return (f"- [link] **{title}** <{group.get('source_url', '')}>\n"
                    f"  <!-- {' | '.join(meta)} -->")
        frontmatter = ["---", f"title: {_meta_value(title)}",
                       f"summary: {_meta_value(group.get('summary', ''))}",
                       f"reference_kind: {reference_kind}",
                       f"ontology_types: {_meta_value(group.get('ontology_types', []))}",
                       f"source: {source}", f"ingestion_candidate: {unit_id}"]
        if project_hint:
            frontmatter.append(f"project_hint: {_meta_value(project_hint)}")
        if marked:
            frontmatter.append("taint: external")
        frontmatter.append(f"created: {today}")
        for name in ("source_url", "retrieved_at", "content_hash"):
            if group.get(name) is not None:
                frontmatter.append(f"{name}: {_meta_value(group[name])}")
        body = str(effective or group.get("body") or fallback_body)
        return "\n".join([*frontmatter, "---", "", f"# {title}", "", body,
                          "", f"<!-- cand:{unit_id} -->"])

    if store in {"DDD", "COMMON_DDD"}:
        return _render_ddd(unit_id, group, answer=answer, effective=effective,
                           project_hint=project_hint if store == "COMMON_DDD" else None)

    entry_type = str(group.get("type") or _model_value(answer, "entry_type",
                                                        group.get("type_hint") or "guideline"))
    if kind == "gap":
        entry_type = f"gap:{group.get('gap_kind') or 'unknown'}"
        fields = [("", group.get("what")), ("**Why:** ", group.get("why")),
                  ("**Next:** ", group.get("proposed_next"))]
        body = " ".join(f"{label}{value}" for label, value in fields if value not in (None, ""))
    else:
        body = str(effective or _cognition_body(group, fallback_body)).strip()
    body = _one_line(body)
    marker = f"<!-- cand:{unit_id} -->"

    if store == "EVOLUTION":
        prefix = _evolution_prefix(group, answer)
        label = "Correction" if prefix == "C" else "Competence"
        return f"### {prefix}{sequence:03d} | {today}\n- **{label}**: **{_one_line(title)}** {body}\n{marker}"
    if store == "MEMORY":
        lead, meta = format_memory_entry(entry_type, title, body, today, source=source)
        return f"{lead} {marker}\n{meta}"
    # The metadata comment holds only _META_RE's fields, in order (source, taint);
    # anything else made the whole comment unreadable, so the entry's own marker
    # carries project_hint / rerouted_from, as in a common-DDD file.
    from core.ddd_entry_lifecycle import meta_comment
    meta = meta_comment(source=source, taint="external" if marked else "")
    cand = [f"cand:{unit_id}"]
    if project_hint:
        cand.append(f"project_hint:{_meta_value(project_hint)}")
    rerouted = _model_value(destination, "rerouted_from")
    if rerouted:
        cand.append(f"rerouted_from:{rerouted}")
    line = f"- [{entry_type}] **{title}** {body} <!-- {' | '.join(cand)} -->"
    return f"{line}\n{meta}" if meta else line


def render_unit(decision: UnitDecision | Mapping[str, Any], unit_id: str,
                group: Mapping[str, Any]) -> str:
    """Public deterministic renderer for one complete source unit."""
    return _render_block(decision, unit_id, dict(group))


def _next_evolution_id(content: str, section: str, prefix: str,
                       withdrawn: list[str] | None = None) -> int:
    """One past the highest number of *prefix* in the section, counting the entries
    withdrawn from it too: a withdrawal deletes the entry, and its number must not be
    given to the next one (other files cite a correction by its number, e.g. "(C052)")."""
    _, end = _section_bounds(content, section)
    start, _ = _section_bounds(content, section)
    values = [int(item) for item in re.findall(rf"^###\s+{prefix}(\d+)\b", content[start:end], re.MULTILINE)]
    for text in withdrawn or ():
        m = re.match(rf"\s*###\s+{prefix}(\d+)\b", text)
        if m:
            values.append(int(m.group(1)))
    return max(values, default=0) + 1


def _withdrawn_evolution_texts() -> list[str]:
    """The original texts of the entries withdrawn from EVOLUTION.md (write journal)."""
    from .store import connect_ro
    try:
        conn = connect_ro()
    except (OSError, sqlite3.Error):
        return []
    try:
        rows = conn.execute("SELECT anchors_absent FROM ingestion_writes WHERE op_kind='user_redact' "
                            "AND step='done' AND target_path='.context/EVOLUTION.md'").fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out: list[str] = []
    for (absent,) in rows:
        try:
            out += [t for t in json.loads(absent or "[]") if isinstance(t, str)]
        except (TypeError, ValueError):
            continue
    return out


def _build_plan(decision: UnitDecision | Mapping[str, Any], unit_row: Any) -> dict[str, Any]:
    unit_id = str(_row_value(unit_row, "unit_id") or _decision_value(decision, "unit_id"))
    group = dict(_json_value(_row_value(unit_row, "claim_group"), {}))
    envelope = dict(_json_value(_row_value(unit_row, "envelope"), {}))
    group.setdefault("source", _row_value(unit_row, "source", "ingestion"))
    group.setdefault("project_hint", _row_value(unit_row, "project"))
    vector = _decision_value(decision, "version_vector", {}) or {}
    if vector.get("event_key"):
        group.setdefault("event_key", vector["event_key"])
    target = _target_path(decision, unit_id, group)
    target.parent.mkdir(parents=True, exist_ok=True)
    original = target.read_bytes() if target.exists() else b""
    destination = _decision_value(decision, "destination") or _decision_value(decision, "card2")
    store = str(_model_value(destination, "store", ""))
    rerouted = bool(_model_value(destination, "rerouted_from"))
    non_injected_timeline = store == "TIMELINE" and not bool(
        _model_value(destination, "injectable", True))
    # what the entry rests on (pipeline_v2.unit_basis): only "external" is marked in the
    # file; the baseline registers the entry with this trust (cdf-fixing-spec C1). A
    # decision judged before the basis was recorded is external by its marks, else
    # inferred: without a recorded basis it is never the user's (I4).
    basis = str(vector.get("basis") or "")
    if basis not in ROOT_TRUST_ORDER:
        legacy = bool(envelope.get("cited_external", envelope.get("tainted")) or rerouted
                      or non_injected_timeline or vector.get("inferred"))
        basis = "external" if legacy else "inferred"
    group["cited_external"] = basis == "external"
    section = str(_model_value(destination, "section", "") or "")
    content = original.decode("utf-8") if original else ""
    archive_path: Path | None = None       # an add archives nothing (v1's replace is retired)
    sequence = 1
    if store == "EVOLUTION":
        prefix = _evolution_prefix(group, _decision_value(decision, "card1"))
        sequence = _next_evolution_id(content, section, prefix, _withdrawn_evolution_texts())
    block = _render_block(decision, unit_id, group, sequence=sequence)
    intended = _compose_intended(content, target, store, section, group, block)
    # the section the entry is written under: an event's is chosen when it is placed (its
    # month), not declared — an anchor without it names no entry and its relations were
    # skipped as "anchor not found"
    placed = _timeline_section(group) if store == "TIMELINE" else section
    new_anchor = f"{_relative(target)}#{placed}#cand:{unit_id}"
    relations = []
    for hint in vector.get("relation_hints", []):
        predicate = hint.get("predicate")
        object_anchor = hint.get("object_anchor")
        if predicate in {"supersedes", "derived_from"} and object_anchor:
            relations.append({"subject_anchor": new_anchor, "predicate": predicate,
                              "object_anchor": object_anchor})
    return {
        "op_id": f"ingestion:{unit_id}", "unit_id": unit_id, "target": target, "basis": basis,
        "target_path": _relative(target), "before_hash": _hash(original) if original else "",
        "intended_bytes": intended, "after_hash": _hash(intended),
        "archive_path": _relative(archive_path) if archive_path else None,
        "archive_hash": _hash(original) if archive_path else None,
        "anchors_present": [f"cand:{unit_id}"],
        "anchors_absent": [],
        "relations": relations,
    }


def _current_epoch(conn: sqlite3.Connection, unit_row: Any = None) -> int:
    value = _row_value(unit_row, "owner_epoch")
    if value is not None:
        return int(value)
    row = conn.execute("SELECT epoch FROM ingestion_owner WHERE id=1").fetchone()
    return int(row[0]) if row else 0


def _insert_plan(conn: sqlite3.Connection, plan: dict[str, Any], epoch: int) -> None:
    now = _iso()
    # op_id is per unit: a retry after user_race / stale_plan must get a new plan, not
    # load the abandoned one back (which skipped every step and reported success)
    conn.execute("DELETE FROM ingestion_writes WHERE op_id=? AND step='abandoned'", (plan["op_id"],))
    conn.execute(
        """INSERT OR IGNORE INTO ingestion_writes
        (op_id,op_kind,unit_id,target_path,before_hash,intended_bytes,after_hash,
         archive_path,archive_hash,anchors_present,anchors_absent,relations,
         step,step_owner,owner_epoch,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,? ,?,?,?,?)""",
        (plan["op_id"], "ingestion_unit", plan["unit_id"], plan["target_path"],
         plan["before_hash"], plan["intended_bytes"], plan["after_hash"],
         plan["archive_path"], plan["archive_hash"], json.dumps(plan["anchors_present"]),
         json.dumps(plan["anchors_absent"]), json.dumps(plan["relations"]),
         "planned", None, epoch, now, now),
    )
    if plan.get("basis") in ROOT_TRUST_ORDER:
        conn.execute(
            "INSERT INTO unit_basis(unit_id,basis,recorded_at) VALUES(?,?,?) "
            "ON CONFLICT(unit_id) DO UPDATE SET basis=excluded.basis,recorded_at=excluded.recorded_at",
            (plan["unit_id"], plan["basis"], now))
    conn.execute(
        "UPDATE ingestion_units SET state='writing',owner_epoch=?,updated_at=? "
        "WHERE unit_id=? AND state='judged'",
        (epoch, now, plan["unit_id"]),
    )
    conn.commit()


def _claim_step(conn: sqlite3.Connection, op_id: str, step: str, epoch: int) -> bool:
    owner = f"owner:{os.getpid()}:{epoch}"
    cursor = conn.execute(
        "UPDATE ingestion_writes SET step_owner=:owner,updated_at=:updated_at "
        "WHERE op_id=:op_id AND step=:step AND owner_epoch=:epoch",
        {"owner": owner, "updated_at": _iso(), "op_id": op_id, "step": step, "epoch": epoch},
    )
    conn.commit()
    return cursor.rowcount == 1


# Once a plan is done or abandoned nothing reads its intended bytes again (recover takes
# only unfinished plans; the flow views drop the column): they are released then. Kept,
# every write held a whole copy of its target file — 419 MB in two days.
_SETTLED = ("done", "abandoned")


def _advance(conn: sqlite3.Connection, op_id: str, current: str, following: str, epoch: int) -> bool:
    release = ",intended_bytes=NULL" if following in _SETTLED else ""
    cursor = conn.execute(
        f"UPDATE ingestion_writes SET step=?,updated_at=?{release} "
        "WHERE op_id=? AND step=? AND owner_epoch=?",
        (following, _iso(), op_id, current, epoch),
    )
    conn.commit()
    return cursor.rowcount == 1


def _event(conn: sqlite3.Connection, kind: str, detail: dict[str, Any]) -> None:
    conn.execute("INSERT INTO ingestion_events(at,kind,detail) VALUES (?,?,?)",
                 (_iso(), kind, json.dumps(detail, ensure_ascii=False, sort_keys=True)))


DEFER_LIMIT = 5   # a unit whose writes keep racing is poisoned after this many attempts


def _defer(conn: sqlite3.Connection, plan: Mapping[str, Any], reason: str, *,
           event_kind: str | None = "user_race") -> None:
    """Abandon *plan*; its unit (if still writing) is deferred with a backoff and one
    more attempt counted, poisoned after DEFER_LIMIT -- without them a target the user
    keeps editing was re-judged and re-planned in a tight loop."""
    conn.execute("UPDATE ingestion_writes SET step='abandoned',updated_at=?,intended_bytes=NULL WHERE op_id=?",
                 (_iso(), plan["op_id"]))
    if plan.get("unit_id"):
        row = conn.execute("SELECT attempts FROM ingestion_units WHERE unit_id=?",
                           (plan["unit_id"],)).fetchone()
        attempts = int(row[0] or 0) + 1 if row else 1
        poison = attempts >= DEFER_LIMIT
        next_at = None if poison else _iso(_now() + timedelta(seconds=min(3600, 60 * 2 ** (attempts - 1))))
        conn.execute(
            "UPDATE ingestion_units SET state=?,reason=?,error_class=?,attempts=?,next_attempt_at=?,"
            "updated_at=? WHERE unit_id=? AND state='writing'",
            ("failed_poison" if poison else "deferred", "retries_exhausted" if poison else reason,
             reason, attempts, next_at, _iso(), plan["unit_id"]),
        )
    if event_kind is not None:
        _event(conn, event_kind, {"op_id": plan["op_id"], "target_path": plan["target_path"], "reason": reason})
    conn.commit()


def _load_plan(row: Any) -> dict[str, Any]:
    columns = ("op_id", "op_kind", "unit_id", "target_path", "before_hash", "intended_bytes",
               "after_hash", "archive_path", "archive_hash", "anchors_present", "anchors_absent",
               "relations", "baseline_patch", "tree_manifest", "step", "owner_epoch")
    if isinstance(row, sqlite3.Row):
        raw = {name: row[name] for name in columns}
    elif isinstance(row, Mapping):
        raw = dict(row)
    else:
        raw = dict(zip(columns, row))
    for key in ("anchors_present", "anchors_absent", "relations", "baseline_patch", "tree_manifest"):
        raw[key] = _json_value(raw.get(key), [] if key in {"anchors_present", "anchors_absent", "relations"} else None)
    return raw


def _target_postcondition(plan: Mapping[str, Any]) -> bool:
    target = _absolute(str(plan["target_path"]))
    # absent and empty are different states: read as b"" both, a new empty file was
    # "already written" (never created) and a delete of an empty file "already done"
    if target.exists() != (plan.get("intended_bytes") is not None):
        return False
    data = target.read_bytes() if target.exists() else b""
    if _hash(data) != plan.get("after_hash"):
        return False
    text = data.decode("utf-8", errors="replace")
    return all(anchor in text for anchor in plan.get("anchors_present", [])) and not any(
        anchor in text for anchor in plan.get("anchors_absent", [])
    )


def _archive_postcondition(plan: Mapping[str, Any]) -> bool:
    if not plan.get("archive_path"):
        return True
    path = _absolute(str(plan["archive_path"]))
    return path.is_file() and _path_hash(path) == plan.get("archive_hash")


def _relations_postcondition(conn: sqlite3.Connection, plan: Mapping[str, Any]) -> bool:
    for relation in plan.get("relations", []):
        row = conn.execute(
            "SELECT 1 FROM ingestion_relations WHERE subject_anchor=? AND predicate=? AND object_anchor=?",
            (relation["subject_anchor"], relation["predicate"], relation["object_anchor"]),
        ).fetchone()
        if row is None:
            return False
    return True


def _would_create_cycle(conn: sqlite3.Connection, subject: str, object_anchor: str) -> bool:
    """Return true when adding ``subject supersedes object`` would close a cycle."""
    current = object_anchor
    seen: set[str] = set()
    while current not in seen:
        if current == subject:
            return True
        seen.add(current)
        row = conn.execute(
            "SELECT object_anchor FROM ingestion_relations "
            "WHERE subject_anchor=? AND predicate='supersedes' AND inactive=0",
            (current,),
        ).fetchone()
        if row is None:
            return False
        current = str(row[0])
    return True


def _anchor_exists(anchor: str) -> bool:
    try:
        path_value, _section, _identity = anchor.rsplit("#", 2)
        path = _absolute(path_value)
        if path.is_file() and any(unit.anchor == anchor for unit in parse_units(path, root=_workspace())):
            return True
        archive = path.parent / ".archive"
        if archive.is_dir():
            return any(unit.identity == _identity
                       for item in archive.iterdir() if item.is_file()
                       for unit in parse_units(item, root=_workspace()))
        return False
    except (OSError, UnicodeError, ValueError):
        return False


def _active_relation_head(conn: sqlite3.Connection, anchor: str) -> str:
    current = anchor
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        rows = conn.execute(
            "SELECT subject_anchor FROM ingestion_relations "
            "WHERE object_anchor=? AND predicate='supersedes' AND inactive=0",
            (current,),
        ).fetchall()
        if not rows:
            return current
        if len(rows) != 1:
            raise ValueError("incompatible_hits")
        current = str(rows[0][0])
    raise ValueError("incompatible_hits")


def _write_relations(conn: sqlite3.Connection, plan: Mapping[str, Any]) -> None:
    """Record *plan*'s relations. This step runs after the entry is on disk, so a
    relation that cannot be recorded (unknown predicate, missing anchor, a second head,
    a cycle) is skipped with an event: raising here left the plan at target_applied and
    stopped the worker at every start."""
    for relation in plan.get("relations", []):
        predicate = relation.get("predicate")
        subject = str(relation.get("subject_anchor") or "")
        object_anchor = str(relation.get("object_anchor") or "")
        try:
            if predicate not in {"supersedes", "derived_from"}:
                raise ValueError("unknown predicate")
            if not _anchor_exists(subject) or not _anchor_exists(object_anchor):
                raise ValueError("anchor not found")
            if predicate == "supersedes":
                object_anchor = _active_relation_head(conn, object_anchor)
                if _would_create_cycle(conn, subject, object_anchor):
                    raise ValueError("cycle")
        except ValueError as exc:
            _event(conn, "relation_skipped", {"op_id": plan.get("op_id"), "relation": dict(relation),
                                              "reason": str(exc)})
            continue
        conn.execute(
            "INSERT OR IGNORE INTO ingestion_relations(subject_anchor,predicate,object_anchor,created,inactive) "
            "VALUES (?,?,?,?,0)",
            (subject, predicate, object_anchor, _iso()),
        )


def _execute_plan(conn: sqlite3.Connection, plan: dict[str, Any]) -> str:
    epoch = int(plan.get("owner_epoch") or 0)
    target = _absolute(str(plan["target_path"]))
    materializer = get_materializer()
    sequence = (("planned", "archive_applied"), ("archive_applied", "target_applied"),
                ("target_applied", "relations_applied"), ("relations_applied", "baseline_applied"),
                ("baseline_applied", "done"))
    for current, following in sequence:
        if plan["step"] != current:
            continue
        lock_target = target if target.suffix else target.parent / ".cdf-tree"
        lock_target.parent.mkdir(parents=True, exist_ok=True)
        with md_lock(lock_target, blocking=True):
            if not _claim_step(conn, str(plan["op_id"]), current, epoch):
                return "fenced"
            if current == "planned":
                if not _archive_postcondition(plan) and plan.get("archive_path"):
                    if _path_hash(target) != plan.get("before_hash"):
                        _defer(conn, plan, "user_race")
                        return "deferred"
                    materializer.archive(target, op_id=str(plan["op_id"]))
                    if not _archive_postcondition(plan):
                        raise OSError("archive postcondition failed")
            elif current == "archive_applied":
                if not _target_postcondition(plan):
                    if _path_hash(target) != plan.get("before_hash"):
                        _defer(conn, plan, "user_race")
                        return "deferred"
                    intended = plan.get("intended_bytes")
                    if intended is None:
                        materializer.unlink(target, op_id=str(plan["op_id"]))
                    else:
                        materializer.write_bytes(target, bytes(intended), op_id=str(plan["op_id"]))
                    if not _target_postcondition(plan):
                        _defer(conn, plan, "user_race")
                        return "deferred"
                    if str(plan["target_path"]).startswith("Knowledge/"):
                        from core.knowledge_store import sync_knowledge_file
                        sync_knowledge_file(target)
            elif current == "target_applied" and not _relations_postcondition(conn, plan):
                _write_relations(conn, plan)
                conn.commit()
            elif current == "relations_applied" and plan.get("baseline_patch"):
                _apply_baseline_patch(plan["baseline_patch"], conn=conn)
            if not _advance(conn, str(plan["op_id"]), current, following, epoch):
                return "fenced"
            plan["step"] = following
    if plan["step"] == "done" and plan.get("unit_id"):
        now = _iso()
        conn.execute(
            "UPDATE ingestion_units SET state='written',updated_at=? WHERE unit_id=? AND state='writing' AND owner_epoch=?",
            (now, plan["unit_id"], epoch),
        )
        conn.commit()
    return str(plan["step"])


def apply_unit(decision: UnitDecision | Mapping[str, Any], unit_row: Any,
               conn: sqlite3.Connection) -> str:
    """Plan, commit, and materialize one judged unit; return the final plan step."""
    enriched = {key: _row_value(unit_row, key) for key in (
        "unit_id", "candidate_id", "claim_group", "owner_epoch", "source", "envelope", "project"
    )}
    candidate_id = enriched.get("candidate_id")
    if candidate_id and (not enriched.get("source") or not enriched.get("envelope")):
        inbox = conn.execute(
            "SELECT source,envelope FROM ingestion_inbox WHERE id=?", (candidate_id,)
        ).fetchone()
        if inbox is not None:
            enriched["source"], enriched["envelope"] = inbox[0], inbox[1]
    unit_id = str(enriched.get("unit_id") or _decision_value(decision, "unit_id"))
    group = dict(_json_value(enriched.get("claim_group"), {}))
    target = _target_path(decision, unit_id, group)
    target.parent.mkdir(parents=True, exist_ok=True)
    with md_lock(target, blocking=True):
        plan = _build_plan(decision, enriched)
        epoch = _current_epoch(conn, unit_row)
        _insert_plan(conn, plan, epoch)
    row = conn.execute("SELECT * FROM ingestion_writes WHERE op_id=?", (plan["op_id"],)).fetchone()
    return _execute_plan(conn, _load_plan(row))


def recover(conn: sqlite3.Connection, *, requeue_grace: timedelta | None = None) -> dict[str, int]:
    """Resume journal plans from postconditions, each on its own (a plan that raises
    before its entry is in the file is abandoned; after, it is kept and finished by the
    next recovery), and requeue judged or writing units that have no live plan.

    With *requeue_grace* a unit is requeued only when it has not moved for that long:
    the daily pass runs while the run lane may be between its claim and its plan (at
    startup the run lane has not started yet, so every such unit is stranded)."""
    counts = {"recovered": 0, "abandoned": 0, "requeued": 0}
    rows = conn.execute("SELECT * FROM ingestion_writes WHERE step NOT IN ('done','abandoned') ORDER BY created_at").fetchall()
    for row in rows:
        plan = _load_plan(row)
        try:
            _recover_plan(conn, plan, counts)
        except Exception as exc:  # one bad plan never stops the others
            if conn.in_transaction:
                conn.rollback()
            current = conn.execute("SELECT step FROM ingestion_writes WHERE op_id=?",
                                   (plan["op_id"],)).fetchone()
            if current is not None and current[0] not in ("planned", "archive_applied"):
                # its entry is in the file: kept, and finished by the next recovery
                logger.warning("CDF recovery of plan %s failed at %s, retried next time: %s: %s",
                               plan.get("op_id"), current[0], type(exc).__name__, exc)
                continue
            logger.warning("CDF recovery abandoned plan %s: %s: %s", plan.get("op_id"),
                           type(exc).__name__, exc)
            _defer(conn, plan, "stale_plan", event_kind="recovery_failed")
            counts["abandoned"] += 1
    _finish_recovery(conn, counts, requeue_grace)
    return counts


def _recover_plan(conn: sqlite3.Connection, plan: dict[str, Any], counts: dict[str, int]) -> None:
    """Resume one journal plan from its postconditions."""
    if plan.get("op_kind") == "user_tree_move":
        from .governed_edit import recover_tree_plan

        result = recover_tree_plan(conn, plan)
        counts["recovered" if result == "done" else "abandoned"] += 1
        return
    if plan.get("op_kind") == "user_redact":
        from .governed_edit import recover_redact_plan

        result = recover_redact_plan(conn, plan)
        counts["recovered" if result == "done" else "abandoned"] += 1
        return
    target = _absolute(str(plan["target_path"]))
    step = str(plan["step"])
    if step == "planned" and _archive_postcondition(plan):
        _advance(conn, str(plan["op_id"]), "planned", "archive_applied", int(plan.get("owner_epoch") or 0))
        plan["step"] = "archive_applied"
    if plan["step"] == "archive_applied" and _target_postcondition(plan):
        _advance(conn, str(plan["op_id"]), "archive_applied", "target_applied", int(plan.get("owner_epoch") or 0))
        plan["step"] = "target_applied"
    # From target_applied on the entry is in the file and the steps left (relations,
    # baseline) touch only the database: they are finished whatever the file holds now.
    # Abandoning there let the retry judge the unit again and append its entry a
    # second time (to the same file or, after a new judgment, another).
    if plan["step"] in ("planned", "archive_applied"):
        current_hash = _path_hash(target)
        expected_hashes = {str(plan.get("before_hash") or ""), str(plan.get("after_hash") or "")}
        if current_hash not in expected_hashes:
            _defer(conn, plan, "stale_plan", event_kind=None)
            counts["abandoned"] += 1
            return
    result = _execute_plan(conn, plan)
    counts["recovered" if result == "done" else "abandoned"] += 1


def _finish_recovery(conn: sqlite3.Connection, counts: dict[str, int],
                     requeue_grace: timedelta | None = None) -> None:
    conn.execute(
        """UPDATE ingestion_units SET state='written',updated_at=?
        WHERE state='writing' AND EXISTS (
          SELECT 1 FROM ingestion_writes w
          WHERE w.unit_id=ingestion_units.unit_id AND w.step='done'
        )""",
        (_iso(),),
    )
    # judged, or writing whose plan was never inserted (the writer raised, or the
    # process died, between the worker's claim and the plan), or whose last plan was
    # abandoned: judge again
    settled = _iso(_now() - requeue_grace) if requeue_grace else _iso(_now() + timedelta(days=1))
    missing = conn.execute(
        "SELECT unit_id,state FROM ingestion_units u WHERE state IN ('judged','writing') "
        "AND updated_at<? AND NOT EXISTS (SELECT 1 FROM ingestion_writes w "
        "WHERE w.unit_id=u.unit_id AND w.step<>'abandoned')", (settled,)
    ).fetchall()
    for row in missing:
        conn.execute("UPDATE ingestion_units SET state='queued',updated_at=? WHERE unit_id=? AND state=?",
                     (_iso(), row[0], row[1]))
        counts["requeued"] += 1
    conn.commit()


def baseline_path() -> Path:
    """Return the versioned baseline manifest path in app state."""
    return Path(get_app_data_dir()) / "state" / "baseline.json"


def _atomic_state_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _installed_project(path: Path) -> bool:
    """Return whether a governed DDD file belongs to an installed package."""
    try:
        relative = path.resolve(strict=False).relative_to(_workspace())
    except ValueError:
        return False
    if len(relative.parts) < 3 or relative.parts[0] != "Projects":
        return False
    marker = _workspace() / "Projects" / relative.parts[1] / ".swarmai" / "installed-package.json"
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return bool(isinstance(value, dict) and value.get("name") and value.get("version"))


def _unit_external(unit: Any) -> bool:
    """Whether a baseline unit declares ``taint: external`` (new-brain §5.11 C4).

    A lifecycle entry's taint is read by the one metadata parser
    (``EntryMetadata.taint``, carried on the unit). The comment scan stays as a
    fail-closed fallback: a comment that declares ``taint: external`` but breaks the
    metadata contract's order is not metadata to the parser, and trust may only ever
    err toward external. Other units (TIMELINE / EVOLUTION blocks, link bullets,
    documents) carry it in frontmatter or a metadata comment.
    """
    if getattr(unit, "taint", None) == "external":
        return True
    return _unit_declares_external(unit.text)


def _unit_declares_external(text: str) -> bool:
    """Read ``taint: external`` only from frontmatter or HTML metadata comments."""
    stripped = text.lstrip()
    if stripped.startswith("---"):
        closing = stripped.find("\n---", 3)
        if closing >= 0 and re.search(
            r"^taint\s*:\s*external\s*$", stripped[:closing], re.MULTILINE
        ):
            return True
    return any(
        re.search(r"(?:^|\|)\s*taint\s*:\s*external\s*(?:\||$)", comment)
        for comment in re.findall(r"<!--(.*?)-->", text, re.DOTALL)
    )


def _recorded_bases(units, conn: sqlite3.Connection | None) -> dict[str, str] | None:
    """The recorded basis (``unit_basis``) of each CDF entry among *units*, by unit id;
    None when the store cannot be read."""
    ids = [unit.identity[5:] for unit in units if str(unit.identity).startswith("cand:")]
    if not ids:
        return {}
    owned = conn is None
    try:
        if owned:
            from .store import connect_ro
            conn = connect_ro()
        marks = ",".join("?" for _ in ids)
        return {str(r[0]): str(r[1]) for r in conn.execute(
            f"SELECT unit_id,basis FROM unit_basis WHERE unit_id IN ({marks})", ids)}
    except (OSError, sqlite3.Error):
        return None
    finally:
        if owned and conn is not None:
            conn.close()


def _unit_trust(unit: Any, bases: dict[str, str] | None) -> str:
    """The trust a baseline unit is registered with (cdf-fixing-spec C1). A declared
    ``taint: external`` is external. An entry CDF wrote has the trust of what it rests on
    (its recorded basis); one with no recorded basis (written before bases were recorded,
    or the store unreadable) is inferred: an entry is the user's only when its basis says
    so (design 2026-10-30-cdf-agent-decides §2 K3, I4). Everything a person wrote is the
    user's."""
    if _unit_external(unit):
        return "external"
    identity = str(unit.identity)
    if not identity.startswith("cand:"):
        return "user"
    if bases is None:
        return "inferred"
    return bases.get(identity[5:], "inferred")


def _manifest_entry(path: Path, *, trust: str | None = None,
                    conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    data = path.read_bytes()
    installed_trust = "external" if _installed_project(path) else None
    units = parse_units(path, root=_workspace())
    bases = {} if trust or installed_trust else _recorded_bases(units, conn)
    return {
        "file_hash": _hash(data),
        "units": [{"section": unit.section, "identity": unit.identity,
                   "byte_hash": unit.byte_hash,
                   "trust": trust or installed_trust or _unit_trust(unit, bases)}
                  for unit in units],
    }


def _new_manifest() -> dict[str, Any]:
    return {"version": _BASELINE_VERSION, "updated_at": _iso(),
            "files": {_relative(path): _manifest_entry(path) for path in iter_governed_files()}}


def _write_baseline(manifest: dict[str, Any]) -> None:
    path = baseline_path()
    if path.is_file():
        _atomic_state_write(path.with_suffix(".json.bak"), path.read_bytes())
    manifest["version"] = _BASELINE_VERSION
    manifest["updated_at"] = _iso()
    _atomic_state_write(path, json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")


def _read_baseline(*, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    path = baseline_path()
    if not path.exists():
        manifest = _new_manifest()
        _write_baseline(manifest)
        return manifest
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("version") != _BASELINE_VERSION or not isinstance(value.get("files"), dict):
            raise ValueError("unsupported baseline")
        return value
    except (OSError, ValueError, json.JSONDecodeError):
        backup = path.with_suffix(".json.bak")
        if not backup.is_file():
            raise
        value = json.loads(backup.read_text(encoding="utf-8"))
        _atomic_state_write(path, backup.read_bytes())
        if conn is not None:
            _event(conn, "writer_unknown", {"reason": "baseline_restored_from_backup"})
            conn.commit()
        return value


def _register_baseline_roots(manifest: Mapping[str, Any],
                             conn: sqlite3.Connection | None) -> None:
    from .api import connect_rw
    from .provenance import register_corpus_root

    owned = conn is None
    connection = conn or connect_rw()
    try:
        for path_value, entry in manifest.get("files", {}).items():
            for unit in entry.get("units", []):
                locator = (
                    f"{path_value}#{unit.get('section', '')}#"
                    f"{unit.get('identity', unit.get('byte_hash', ''))}"
                )
                register_corpus_root(
                    locator, str(unit["byte_hash"]),
                    str(unit.get("trust") or "user"), conn=connection,
                )
    finally:
        if owned:
            connection.close()


def ensure_baseline(*, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Load or first-run generate the baseline and register its evidence roots."""
    path = baseline_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with md_lock(path, blocking=True):
        manifest = _read_baseline(conn=conn)
        _register_baseline_roots(manifest, conn)
        return manifest


def _apply_baseline_patch_unlocked(patch: Mapping[str, Any], *, conn: sqlite3.Connection | None = None) -> None:
    manifest = _read_baseline(conn=conn)
    files = manifest["files"]
    path_value = str(patch["target_path"])
    target = _absolute(path_value)
    surviving_anchors: set[str] = set()
    if patch.get("delete") or not target.exists():
        files.pop(path_value, None)
    else:
        entry = _manifest_entry(target, trust=patch.get("trust"), conn=conn)
        surviving_anchors = {
            f"{path_value}#{unit.get('section', '')}#{unit.get('identity', unit.get('byte_hash', ''))}"
            for unit in entry.get("units", [])
        }
        for old_path, old_entry in list(files.items()):
            if old_path != path_value and old_entry.get("file_hash") == entry["file_hash"]:
                files.pop(old_path)
        files[path_value] = entry
    for anchor in patch.get("invalidate_anchors", []):
        if conn is not None and anchor not in surviving_anchors:
            conn.execute(
                "UPDATE ingestion_relations SET inactive=1 WHERE subject_anchor=? OR object_anchor=?",
                (anchor, anchor),
            )
    _write_baseline(manifest)
    if conn is not None:
        conn.commit()


def _apply_baseline_patch(patch: Mapping[str, Any], *, conn: sqlite3.Connection | None = None) -> None:
    """Apply one baseline revision without losing concurrent edits to other files."""
    path = baseline_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with md_lock(path, blocking=True):
        _apply_baseline_patch_unlocked(patch, conn=conn)


def detect_out_of_band(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Observe unknown corpus changes without ever revising the baseline."""
    baseline = ensure_baseline(conn=conn)
    known_files = baseline["files"]
    current = {_relative(path): _manifest_entry(path) for path in iter_governed_files()}
    known_candidates = {row[0] for row in conn.execute(
        "SELECT unit_id FROM ingestion_writes WHERE op_kind='ingestion_unit' AND step='done' AND unit_id IS NOT NULL"
    )}
    findings: list[dict[str, Any]] = []
    for path_value in sorted(set(known_files) | set(current)):
        old = known_files.get(path_value, {"units": []})
        new = current.get(path_value, {"units": []})
        baseline_hashes = {unit["byte_hash"] for unit in old.get("units", [])}
        unknown = [unit for unit in new.get("units", [])
                   if unit["byte_hash"] not in baseline_hashes
                   and not (str(unit.get("identity", "")).startswith("cand:")
                            and str(unit["identity"])[5:] in known_candidates)]
        missing = [unit for unit in old.get("units", [])
                   if unit["byte_hash"] not in {item["byte_hash"] for item in new.get("units", [])}]
        if unknown or missing:
            detail = {"target_path": path_value, "unknown": unknown, "missing": missing}
            _event(conn, "writer_unknown", detail)
            findings.append(detail)
    conn.commit()
    return findings
