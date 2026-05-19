"""Tests for knowledge_graph — cross-entry relation layer.

Tests cover: loading/saving YAML, CRUD operations, querying by entity,
stale/expired filtering, and integration with get_stage_knowledge boost.
"""

import pytest
from datetime import date, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from core.knowledge_graph import (
    Relation,
    load_graph,
    save_graph,
    add_relation,
    expire_relation,
    touch_relation,
    query_relations,
    query_related_entries,
    VALID_PREDICATES,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_YAML = """\
version: 1
updated: "2026-05-19"

predicates:
  motivated_by: "A exists because of B"
  supersedes: "A replaces B"
  applies_to: "knowledge applies to module/file"

relations:
  - s: KD16
    p: motivated_by
    o: COE04
    c: "2026-04-15"
    u: "2026-05-19"

  - s: LL19
    p: applies_to
    o: "session_unit.py"
    c: "2026-05-02"
    u: "2026-05-02"

  - s: COE06
    p: applies_to
    o: "session_unit.py"
    c: "2026-03-15"
    u: "2026-04-12"

  - s: C019
    p: supersedes
    o: C005
    c: "2026-05-06"
    u: "2026-05-06"
    e: "2026-05-06"
"""


@pytest.fixture
def sample_yaml_file(tmp_path):
    f = tmp_path / ".knowledge-graph.yaml"
    f.write_text(SAMPLE_YAML, encoding="utf-8")
    return f


@pytest.fixture
def empty_yaml_file(tmp_path):
    f = tmp_path / ".knowledge-graph.yaml"
    return f  # Does not exist yet


# ── AC1: load_graph returns parsed relations ──────────────────────────────────

class TestLoadGraph:
    def test_loads_relations_from_yaml(self, sample_yaml_file):
        relations = load_graph(sample_yaml_file)
        assert len(relations) == 4

    def test_parses_fields_correctly(self, sample_yaml_file):
        relations = load_graph(sample_yaml_file)
        r = relations[0]
        assert r.subject == "KD16"
        assert r.predicate == "motivated_by"
        assert r.object == "COE04"
        assert r.created == date(2026, 4, 15)
        assert r.last_used == date(2026, 5, 19)
        assert r.expired is None

    def test_parses_expired_field(self, sample_yaml_file):
        relations = load_graph(sample_yaml_file)
        expired_rel = [r for r in relations if r.subject == "C019"][0]
        assert expired_rel.expired == date(2026, 5, 6)

    def test_returns_empty_for_missing_file(self, tmp_path):
        missing = tmp_path / "nonexistent.yaml"
        relations = load_graph(missing)
        assert relations == []

    def test_returns_empty_for_empty_file(self, tmp_path):
        f = tmp_path / ".knowledge-graph.yaml"
        f.write_text("", encoding="utf-8")
        relations = load_graph(f)
        assert relations == []


# ── AC7: CRUD operations ─────────────────────────────────────────────────────

class TestCRUD:
    def test_add_relation_creates_file(self, empty_yaml_file):
        add_relation(empty_yaml_file, "KD01", "extends", "COE01")
        relations = load_graph(empty_yaml_file)
        assert len(relations) == 1
        assert relations[0].subject == "KD01"
        assert relations[0].predicate == "extends"
        assert relations[0].object == "COE01"
        assert relations[0].created == date.today()
        assert relations[0].last_used == date.today()
        assert relations[0].expired is None

    def test_add_relation_appends(self, sample_yaml_file):
        add_relation(sample_yaml_file, "E007", "extends", "E002")
        relations = load_graph(sample_yaml_file)
        assert len(relations) == 5

    def test_expire_relation_sets_expired(self, sample_yaml_file):
        expire_relation(sample_yaml_file, "KD16", "motivated_by", "COE04")
        relations = load_graph(sample_yaml_file)
        kd16 = [r for r in relations if r.subject == "KD16" and r.predicate == "motivated_by"][0]
        assert kd16.expired == date.today()

    def test_touch_relation_updates_last_used(self, sample_yaml_file):
        touch_relation(sample_yaml_file, "LL19", "applies_to", "session_unit.py")
        relations = load_graph(sample_yaml_file)
        ll19 = [r for r in relations if r.subject == "LL19"][0]
        assert ll19.last_used == date.today()

    def test_save_roundtrip(self, tmp_path):
        f = tmp_path / "test.yaml"
        rels = [
            Relation("A", "extends", "B", date(2026, 1, 1), date(2026, 5, 1)),
            Relation("C", "applies_to", "D", date(2026, 2, 1), date(2026, 4, 1), date(2026, 5, 1)),
        ]
        save_graph(f, rels)
        loaded = load_graph(f)
        assert len(loaded) == 2
        assert loaded[0].subject == "A"
        assert loaded[1].expired == date(2026, 5, 1)


# ── AC2, AC4, AC5: query operations ──────────────────────────────────────────

class TestQuery:
    def test_query_relations_by_entity(self, sample_yaml_file):
        rels = load_graph(sample_yaml_file)
        results = query_relations(rels, "session_unit.py")
        # LL19 and COE06 both apply_to session_unit.py
        assert len(results) >= 2
        subjects = {r.subject for r in results}
        assert "LL19" in subjects
        assert "COE06" in subjects

    def test_query_excludes_expired(self, sample_yaml_file):
        rels = load_graph(sample_yaml_file)
        results = query_relations(rels, "C005")
        # C019 supersedes C005 but is expired
        subjects = {r.subject for r in results}
        assert "C019" not in subjects

    def test_query_related_entries_returns_titles(self, sample_yaml_file):
        rels = load_graph(sample_yaml_file)
        titles = query_related_entries(rels, ["session_unit.py"])
        assert "LL19" in titles
        assert "COE06" in titles

    def test_stale_relations_flagged(self, sample_yaml_file):
        rels = load_graph(sample_yaml_file)
        # COE06 last_used 2026-04-12 — if today is 2026-05-19 that's only 37 days
        # Make a relation that's >180 days old
        rels.append(Relation(
            "OLD_ENTRY", "applies_to", "ancient.py",
            date(2025, 6, 1), date(2025, 6, 1)
        ))
        results = query_relations(rels, "ancient.py", stale_threshold_days=180)
        assert len(results) == 1
        assert results[0].subject == "OLD_ENTRY"
        # Stale relations should still appear but be identifiable
        assert results[0].is_stale(threshold_days=180, today=date(2026, 5, 19))

    def test_stale_threshold_sorts_fresh_before_stale(self):
        """AC1: stale_threshold_days param sorts fresh before stale."""
        # Explicitly put stale relation FIRST to prove sorting reorders
        rels = [
            Relation("STALE", "applies_to", "target.py",
                     date(2025, 1, 1), date(2025, 1, 1)),  # old
            Relation("FRESH", "applies_to", "target.py",
                     date(2026, 5, 1), date(2026, 5, 18)),  # recent
        ]
        results = query_relations(rels, "target.py", stale_threshold_days=90)
        assert len(results) == 2
        # Fresh should be sorted before stale
        assert results[0].subject == "FRESH"
        assert results[1].subject == "STALE"


# ── AC2+AC3: Concurrent writes + locking ────────────────────────────────────

class TestConcurrency:
    def test_concurrent_add_no_data_loss(self, tmp_path):
        """AC2: two sequential writes both persist (simulates lock serialization)."""
        f = tmp_path / ".knowledge-graph.yaml"
        add_relation(f, "A", "extends", "B")
        add_relation(f, "C", "extends", "D")
        relations = load_graph(f)
        assert len(relations) == 2
        subjects = {r.subject for r in relations}
        assert "A" in subjects
        assert "C" in subjects

    def test_lock_contention_no_crash(self, tmp_path):
        """AC3: lock serializes access — second writer sees first writer's data."""
        import threading
        f = tmp_path / ".knowledge-graph.yaml"

        results = []

        def writer(subject):
            add_relation(f, subject, "extends", "TARGET")
            results.append(subject)

        # Run two writers in parallel threads — lock serializes them
        t1 = threading.Thread(target=writer, args=("FIRST",))
        t2 = threading.Thread(target=writer, args=("SECOND",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Both should succeed (serialized by lock)
        relations = load_graph(f)
        subjects = {r.subject for r in relations}
        assert "FIRST" in subjects
        assert "SECOND" in subjects
        assert len(relations) == 2


# ── AC3: get_stage_knowledge boost integration ───────────────────────────────

class TestStageKnowledgeBoost:
    def test_boost_via_title_match(self):
        """Boost works when relation subject matches entry title directly."""
        from core.ddd_entry_lifecycle import EntryMetadata, get_stage_knowledge

        entries = [
            EntryMetadata(
                title="Subprocess in async must use to_thread",
                entry_type="guideline", ref_count=2,
                last_referenced=date(2026, 5, 2), decay_state="active",
                section="What Worked",
                raw_text="- **Subprocess in async must use to_thread** — details",
            ),
            EntryMetadata(
                title="Unrelated lesson about CSS",
                entry_type="guideline", ref_count=2,
                last_referenced=date(2026, 5, 2), decay_state="active",
                section="What Worked",
                raw_text="- **Unrelated lesson about CSS** — flexbox stuff",
            ),
        ]

        # Relation uses exact title as subject
        relations = [
            Relation(
                "Subprocess in async must use to_thread",
                "applies_to", "session_unit.py",
                date(2026, 5, 2), date(2026, 5, 2),
            )
        ]

        result = get_stage_knowledge(
            entries, "build",
            context_entities=["session_unit.py"],
            relations=relations,
        )
        assert len(result) >= 1
        assert result[0].title == "Subprocess in async must use to_thread"

    def test_boost_via_raw_text_content_match(self):
        """Boost works via content match — entry raw_text mentions context_entity."""
        from core.ddd_entry_lifecycle import EntryMetadata, get_stage_knowledge

        # Entry mentions "session_unit.py" in raw_text but graph uses MEMORY ID
        entries = [
            EntryMetadata(
                title="Subprocess in async must use to_thread",
                entry_type="guideline", ref_count=2, decay_state="active",
                section="What Worked",
                raw_text="- **Subprocess in async...** — session_unit.py blocked (2026-05-02)",
            ),
            EntryMetadata(
                title="Unrelated lesson about CSS",
                entry_type="guideline", ref_count=5, decay_state="active",
                section="What Worked",
                raw_text="- **Unrelated lesson about CSS** — frontend (2026-04-01)",
            ),
        ]

        # Graph uses MEMORY IDs — LL19 applies_to session_unit.py
        relations = [
            Relation("LL19", "applies_to", "session_unit.py",
                     date(2026, 5, 2), date(2026, 5, 2)),
        ]

        result = get_stage_knowledge(
            entries, "build",
            context_entities=["session_unit.py"],
            relations=relations,
        )
        # Subprocess entry mentions session_unit.py in raw_text → boosted above CSS
        assert len(result) >= 2
        assert result[0].title == "Subprocess in async must use to_thread"

    def test_no_boost_without_relations(self):
        """Backward compat — no relations param means no boost."""
        from core.ddd_entry_lifecycle import EntryMetadata, get_stage_knowledge

        entries = [
            EntryMetadata(title="Low ref", entry_type="guideline", ref_count=1,
                         decay_state="active", section="What Worked"),
            EntryMetadata(title="High ref", entry_type="guideline", ref_count=5,
                         decay_state="active", section="What Worked"),
        ]

        result = get_stage_knowledge(entries, "build")
        assert result[0].title == "High ref"
