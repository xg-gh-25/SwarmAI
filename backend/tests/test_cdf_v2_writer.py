"""CDF v2 final-format writer and parser tests."""
from __future__ import annotations

from pathlib import Path

from core.ingestion.units import parse_entries, parse_units
from core.ingestion.writer import (
    _append_timeline, _compose_intended, _render_ddd, render_unit,
)


def decision(store: str, path: str, section: str | None = None, **extra):
    return {
        "unit_id": "candidate.unit", "decision": "write", "action": "add", "reason": "new",
        "destination": {"store": store, "relative_path": path, "section": section, **extra},
        "version_vector": {"event_key": "event-key"},
    }


def event(title: str, kind: str = "event", *, tainted: bool = False):
    return {
        "kind": "event", "event_kind": kind, "when": "2026-10-27",
        "title": title, "what": f"{title} happened.", "impact": "It changed the product.",
        "outcome": "The change landed.", "follow_up": "Observe the result.",
        "source": "S3", "project_hint": "Widget", "cited_external": tainted,
    }


def test_timeline_month_summary_and_late_addenda_are_append_only(tmp_path: Path) -> None:
    content = "# Timeline\n"
    first = render_unit(decision("TIMELINE", ".context/TIMELINE.md"), "event.1", event("First"))
    content = _append_timeline(content, event("First"), first)
    summary_group = event("October summary", "month_summary")
    summary = render_unit(decision("TIMELINE", ".context/TIMELINE.md"), "event.2", summary_group)
    content = _append_timeline(content, summary_group, summary)
    late_group = event("Late event")
    late = render_unit(decision("TIMELINE", ".context/TIMELINE.md"), "event.3", late_group)
    content = _append_timeline(content, late_group, late)
    supplement_group = event("October supplement", "month_supplement")
    supplement = render_unit(decision("TIMELINE", ".context/TIMELINE.md"), "event.4", supplement_group)
    content = _append_timeline(content, supplement_group, supplement)

    assert content.index("First") < content.index("October summary") < content.index("Late event")
    assert content.index("Late event") < content.index("October supplement")
    path = tmp_path / "TIMELINE.md"
    path.write_text(content, encoding="utf-8")
    units = parse_units(path, root=tmp_path)
    assert [unit.identity for unit in units] == [
        "cand:event.1", "cand:event.2", "cand:event.3", "cand:event.4"]
    assert [unit.entry_type for unit in units] == ["event"] * 4
    assert all(unit.section == "2026-10" for unit in units)


def test_tainted_timeline_event_is_last_and_not_injected() -> None:
    trusted = event("Trusted")
    content = _append_timeline("# Timeline\n", trusted,
                               render_unit(decision("TIMELINE", ".context/TIMELINE.md"),
                                           "event.trusted", trusted))
    tainted = event("External", tainted=True)
    content = _append_timeline(content, tainted,
                               render_unit(decision("TIMELINE", ".context/TIMELINE.md"),
                                           "event.external", tainted))
    assert content.rfind("\n## Not injected") > content.rfind("\n## 2026-10")
    assert "taint:external" in content


def test_common_ddd_initializes_section_and_gap_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "Knowledge/Learned/repos/widget.md"
    cognition = {"kind": "cognition", "type": "guideline", "title": "One source",
                 "what": "One fact has one owner.", "how": "All readers use that owner.",
                 "why": "Competing definitions drift.", "source": "S3",
                 "project_hint": "Widget", "cited_external": True}
    rendered = render_unit(
        decision("COMMON_DDD", "Knowledge/Learned/repos/widget.md", "Conventions",
                 project="Widget"), "cog.1", cognition)
    content = _compose_intended("", target, "COMMON_DDD", "Conventions", cognition,
                                rendered).decode()
    assert "source: n/a (multi-unit)" in content
    assert "ingestion_candidate: n/a (multi-unit)" in content
    assert "## Conventions" in content and "## Open Gaps" in content
    assert "**One source** One fact has one owner. **How:** All readers use that owner." in content
    assert "project_hint:\"Widget\"" in content and "taint:external" in content
    # the project hint rides in the identity marker, so the metadata comment still
    # parses and the taint is read back (it was lost when project_hint sat in it)
    [entry] = parse_entries(content)
    assert entry.taint == "external" and entry.source == "S3"

    gap = {"kind": "gap", "gap_kind": "risk", "title": "Missing guard",
           "what": "The guard is absent.", "why": "Invalid data can pass.",
           "proposed_next": "Add a deterministic guard.", "source": "S3",
           "project_hint": "Widget"}
    gap_block = render_unit(
        decision("COMMON_DDD", "Knowledge/Learned/repos/widget.md", "Open Gaps",
                 project="Widget"), "gap.1", gap)
    content = _compose_intended(content, target, "COMMON_DDD", "Open Gaps", gap,
                                gap_block).decode()
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    units = parse_units(target, root=tmp_path)
    assert {unit.identity for unit in units} == {"cand:cog.1", "cand:gap.1"}
    parsed_gap = next(unit for unit in units if unit.identity == "cand:gap.1")
    # a gap is written as a guideline through the one renderer (new-brain §5.5)
    assert parsed_gap.entry_type == "guideline" and parsed_gap.taint == ""
    assert "**How:** Add a deterministic guard." in parsed_gap.text
    assert "gap kind: risk" in parsed_gap.text


def test_render_ddd_unit_preserves_title_and_round_trips_external_taint() -> None:
    title = "Explicit title " + "A" * 240
    group = {
        "kind": "cognition",
        "type": "model",
        "title": title,
        "what": "The model stays complete.",
        "how": "The writer uses one renderer.",
        "why": "Parallel render paths drift.",
        "source": "S7",
        "cited_external": True,
    }
    direct = _render_ddd("ddd.1", group)
    routed = render_unit(
        decision("DDD", "Projects/P/2-understanding/TECH.md", "Key Subsystems"),
        "ddd.1",
        group,
    )
    assert routed == direct
    assert title in routed
    assert "taint:external" in routed
    [entry] = parse_entries(f"# Tech\n\n## Key Subsystems\n\n{routed}\n")
    assert entry.title == title
    assert entry.taint == "external"


def test_link_multi_unit_file_round_trips_complete_entries(tmp_path: Path) -> None:
    target = tmp_path / "Knowledge/Library/Links/example.com.md"
    link = {"kind": "reference", "reference_kind": "link", "title": "Reference",
            "summary": "This link documents the stable interface.",
            "body": "Read it when implementing the interface.",
            "source_url": "https://example.com/interface", "source": "S6",
            "retrieved_at": "2026-10-27T00:00:00Z", "content_hash": "abc"}
    block = render_unit(decision("KNOWLEDGE", "Knowledge/Library/Links/example.com.md"),
                        "link.1", link)
    content = _compose_intended("", target, "KNOWLEDGE", "", link, block).decode()
    assert "source: n/a (multi-unit)" in content
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    [unit] = parse_units(target, root=tmp_path)
    assert unit.identity == "cand:link.1"
    assert unit.entry_type == "link"
    assert "https://example.com/interface" in unit.text
    assert "This link documents the stable interface." in unit.text


def test_reference_frontmatter_is_complete_and_title_is_not_clipped() -> None:
    title = "A" * 120
    reference = {"kind": "reference", "reference_kind": "analysis", "title": title,
                 "summary": "This analysis explains the complete behavior.",
                 "body": "Full body.", "ontology_types": ["model"], "source": "S2",
                 "project_hint": "Widget", "cited_external": True,
                 "source_url": "https://example.com/analysis",
                 "retrieved_at": "2026-10-27T00:00:00Z", "content_hash": "sha"}
    text = render_unit(decision("KNOWLEDGE", "Knowledge/Notes/"), "ref.1", reference)
    for field in ("title:", "summary:", "reference_kind:", "ontology_types:", "source:",
                  "ingestion_candidate:", "project_hint:", "taint:", "created:",
                  "source_url:", "retrieved_at:", "content_hash:"):
        assert field in text
    assert title in text


def test_sync_knowledge_file_chunks_multi_unit_files_per_unit(
    tmp_path: Path, monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    data = tmp_path / "data"
    target = workspace / "Knowledge/Library/Links/example.com.md"
    target.parent.mkdir(parents=True)
    data.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE", str(workspace))
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    first = {"kind": "reference", "reference_kind": "link", "title": "One",
             "summary": "The first link explains one complete topic.", "body": "Read one.",
             "source_url": "https://example.com/one", "source": "S6"}
    second = {**first, "title": "Two", "summary": "The second link explains another topic.",
              "source_url": "https://example.com/two"}
    content = _compose_intended("", target, "KNOWLEDGE", "", first,
                                render_unit(decision("KNOWLEDGE", "Knowledge/Library/Links/example.com.md"),
                                            "link.one", first)).decode()
    content = _compose_intended(content, target, "KNOWLEDGE", "", second,
                                render_unit(decision("KNOWLEDGE", "Knowledge/Library/Links/example.com.md"),
                                            "link.two", second)).decode()
    target.write_text(content, encoding="utf-8")
    from core.knowledge_store import sync_knowledge_file
    stats = sync_knowledge_file(target)
    assert stats["chunks_added"] == 2
    import sqlite3
    conn = sqlite3.connect(data / "data.db")
    rows = conn.execute("SELECT content FROM knowledge_chunks ORDER BY chunk_index").fetchall()
    conn.close()
    assert len(rows) == 2
    assert "cand:link.one" in rows[0][0] and "cand:link.two" in rows[1][0]


def test_knowledge_recall_shape_returns_whole_section_parent_and_summary(
    tmp_path: Path, monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "Knowledge/Notes/reference.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        '---\ntitle: "Reference"\nsummary: "A complete document summary."\n'
        'reference_kind: note\n---\n\n# Reference\n\n## Parent\n\n### Child\n'
        'First sentence.\nSecond sentence.\n\n### Other\nNot part of the hit.\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SWARM_WORKSPACE", str(workspace))
    from core.recall_multi import _knowledge_hit_shape
    shaped = _knowledge_hit_shape("Notes/reference.md", "Child", "partial")
    assert shaped["summary"] == "A complete document summary."
    assert shaped["parent_heading"] == "Parent"
    assert shaped["content"].startswith("## Parent\n\n### Child")
    assert "Second sentence." in shaped["content"]
    assert "Not part of the hit." not in shaped["content"]


def test_baseline_trust_reads_the_entry_metadata_taint(tmp_path: Path, monkeypatch) -> None:
    """C4 (new-brain §5.11): a lifecycle entry's baseline trust comes from the one
    metadata parser, EntryMetadata.taint; an untainted entry is user-trust."""
    from core.ddd_entry_lifecycle import render_ddd_unit
    from core.ingestion import writer

    doc = tmp_path / "Projects/P/2-understanding/TECH.md"
    doc.parent.mkdir(parents=True)
    tainted = render_ddd_unit({"type": "pitfall", "title": "Repo claim", "what": "Read from a repo."},
                              date_str="2026-10-27", source="bootstrap", taint="external")
    clean = render_ddd_unit({"type": "guideline", "title": "User claim", "what": "Said by the user."},
                            date_str="2026-10-27", source="manual")
    doc.write_text(f"# Tech\n\n## Runtime Traps\n\n{tainted}\n{clean}\n", encoding="utf-8")
    monkeypatch.setattr(writer, "_workspace", lambda: tmp_path)
    trust = {row["identity"]: row["trust"] for row in writer._manifest_entry(doc)["units"]}
    units = {u.title: u.identity for u in parse_units(doc, root=tmp_path)}
    assert trust[units["Repo claim"]] == "external"
    assert trust[units["User claim"]] == "user"


def test_a_general_brain_library_file_is_indexed_and_recalled_one_entry_at_a_time(
    tmp_path: Path, monkeypatch,
) -> None:
    """The General Brain writes Knowledge/Library/<subject>.md (route_general). Both index
    writers and the recall hit shape must treat it as a multi-entry file: chunked by
    heading, one hit is a whole section of many entries."""
    workspace = tmp_path / "workspace"
    data = tmp_path / "data"
    target = workspace / "Knowledge/Library/general-knowledge.md"
    target.parent.mkdir(parents=True)
    data.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE", str(workspace))
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    target.write_text(
        "# General knowledge\n\n## Pitfalls\n\n"
        "- [pitfall] **One** First trap. <!-- cand:c-one.unit -->\n\n"
        "- [pitfall] **Two** Second trap. <!-- cand:c-two.unit -->\n", encoding="utf-8")
    import sqlite3
    from core.knowledge_store import sync_knowledge_file, sync_knowledge_index

    def rows():
        conn = sqlite3.connect(data / "data.db")
        try:
            return [r[0] for r in conn.execute(
                "SELECT content FROM knowledge_chunks WHERE source_file='Library/general-knowledge.md' "
                "ORDER BY chunk_index")]
        finally:
            conn.close()

    sync_knowledge_file(target)
    one_file = rows()
    assert [r for r in one_file if "cand:" in r] == [r for r in one_file if r.count("cand:") == 1]
    assert sum("cand:c-one.unit" in r for r in one_file) == 1 and sum("cand:c-two.unit" in r for r in one_file) == 1
    target.write_text(target.read_text(encoding="utf-8") + "\n- [pitfall] **Three** Third. <!-- cand:c-three.unit -->\n",
                      encoding="utf-8")
    from core.knowledge_store import KnowledgeStore
    from core.vec_db import open_vec_db
    with open_vec_db(data / "data.db") as conn:
        sync_knowledge_index(KnowledgeStore(conn), workspace / "Knowledge")
    whole = rows()
    assert all(r.count("cand:") <= 1 for r in whole)          # the bulk sync agrees with the file sync
    assert sum("cand:c-three.unit" in r for r in whole) == 1
    from core.recall_multi import _knowledge_hit_shape
    hit = next(r for r in whole if "cand:c-two.unit" in r)
    shaped = _knowledge_hit_shape("Library/general-knowledge.md", "Pitfalls", hit)
    assert shaped["content"] == hit                            # the entry, not the whole section
