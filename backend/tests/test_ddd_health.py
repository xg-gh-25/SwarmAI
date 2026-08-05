"""Tests for DDD 5-Dimensional Health Scoring (T3).

Validates per-section health scoring across 5 dimensions:
D1 Staleness, D2 Completeness, D3 Usage, D4 Decay, D5 Contradiction (placeholder).

Each dimension produces a score 0-100. Composite = weighted average.
Trust levels: Full(80+), High(60-79), Moderate(40-59), Low(0-39).
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path



class TestStalenessScore:
    """D1: Staleness — days since last section update."""

    def test_fresh_section_scores_100(self):
        from core.ddd_health import score_staleness

        assert score_staleness(days_since_update=0) == 100

    def test_14_day_old_section_scores_58(self):
        from core.ddd_health import score_staleness

        assert score_staleness(days_since_update=14) == 58

    def test_34_day_old_section_scores_0(self):
        from core.ddd_health import score_staleness

        # 34 * 3 = 102 → max(0, 100 - 102) = 0
        assert score_staleness(days_since_update=34) == 0

    def test_never_floors_at_0(self):
        from core.ddd_health import score_staleness

        assert score_staleness(days_since_update=100) == 0


class TestCompletenessScore:
    """D2: Completeness — word count + placeholder detection."""

    def test_rich_content_no_placeholders_scores_100(self):
        from core.ddd_health import score_completeness

        content = "This is a well-written section with plenty of detail. " * 10
        assert score_completeness(content) == 100

    def test_placeholder_reduces_score(self):
        from core.ddd_health import score_completeness

        # 50+ words to avoid short penalty, but with 2 placeholders
        content = (" ".join(["word"] * 55) + " TODO: add more detail. Also TBD.")
        score = score_completeness(content)
        assert score < 100
        # 2 placeholders × 20 = 40 reduction → 60
        assert score == 60

    def test_short_content_penalized(self):
        from core.ddd_health import score_completeness

        content = "Short."  # < 50 words
        score = score_completeness(content)
        assert score <= 70  # -30 for short

    def test_empty_content_scores_0(self):
        from core.ddd_health import score_completeness

        assert score_completeness("") == 0


class TestUsageScore:
    """D3: Usage — changelog entry count per section in last 30 days."""

    def test_zero_entries_scores_0(self):
        from core.ddd_health import score_usage

        assert score_usage(changelog_entries_30d=0) == 0

    def test_7_entries_scores_100(self):
        from core.ddd_health import score_usage

        assert score_usage(changelog_entries_30d=7) == 100

    def test_3_entries_scores_45(self):
        from core.ddd_health import score_usage

        assert score_usage(changelog_entries_30d=3) == 45

    def test_caps_at_100(self):
        from core.ddd_health import score_usage

        assert score_usage(changelog_entries_30d=20) == 100


class TestDecayScore:
    """D4: Decay — score direction since last measurement."""

    def test_no_history_returns_50(self):
        from core.ddd_health import score_decay

        assert score_decay(current_composite=70, last_composite=None) == 50

    def test_improving_scores_above_50(self):
        from core.ddd_health import score_decay

        # current=80, last=70 → delta=10 → 50 + 10*5 = 100 (clamped)
        assert score_decay(current_composite=80, last_composite=70) == 100

    def test_declining_scores_below_50(self):
        from core.ddd_health import score_decay

        # current=60, last=80 → delta=-20 → 50 + (-20)*5 = -50 → clamped to 0
        assert score_decay(current_composite=60, last_composite=80) == 0

    def test_stable_scores_50(self):
        from core.ddd_health import score_decay

        assert score_decay(current_composite=70, last_composite=70) == 50


class TestCompositeAndTrust:
    """Composite scoring and trust level derivation."""

    def test_composite_weighted_average(self):
        from core.ddd_health import compute_composite

        scores = {
            "staleness": 100,
            "completeness": 100,
            "usage": 100,
            "decay": 50,
            "contradiction": 50,
        }
        composite = compute_composite(scores)
        # 100*0.25 + 100*0.20 + 100*0.25 + 50*0.15 + 50*0.15 = 25+20+25+7.5+7.5 = 85
        assert composite == 85

    def test_trust_full(self):
        from core.ddd_health import derive_trust_level

        assert derive_trust_level(85) == "full"

    def test_trust_high(self):
        from core.ddd_health import derive_trust_level

        assert derive_trust_level(70) == "high"

    def test_trust_moderate(self):
        from core.ddd_health import derive_trust_level

        assert derive_trust_level(50) == "moderate"

    def test_trust_low(self):
        from core.ddd_health import derive_trust_level

        assert derive_trust_level(30) == "low"

    def test_all_zero_scores_low(self):
        from core.ddd_health import compute_composite, derive_trust_level

        scores = {
            "staleness": 0,
            "completeness": 0,
            "usage": 0,
            "decay": 0,
            "contradiction": 0,
        }
        composite = compute_composite(scores)
        assert composite == 0
        assert derive_trust_level(composite) == "low"


class TestComputeSectionHealth:
    """Integration test: full scoring for a project."""

    def test_scores_all_sections_in_project(self):
        from core.ddd_health import compute_section_health

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create a DDD doc with 2 sections (Architecture rich, Conventions sparse)
            tech = project_dir / "TECH.md"
            tech.write_text(
                "# Tech\n\n"
                "## Architecture\n\n"
                + ("Detailed architecture description with many words to pass completeness check. " * 10) + "\n\n"
                "## Conventions\n\n"
                "Short.\n"
            )

            # Create a changelog with entries for Architecture
            changelog = project_dir / ".artifacts" / "ddd-changelog.jsonl"
            changelog.parent.mkdir(parents=True)
            entries = [
                {"target_doc": "TECH.md", "target_section": "Architecture",
                 "timestamp": datetime.now(timezone.utc).isoformat()},
                {"target_doc": "TECH.md", "target_section": "Architecture",
                 "timestamp": datetime.now(timezone.utc).isoformat()},
            ]
            changelog.write_text(
                "\n".join(json.dumps(e) for e in entries) + "\n"
            )

            result = compute_section_health(project_dir)

            # Should have TECH.md with 2 sections scored
            assert "TECH.md" in result["docs"]
            tech_sections = result["docs"]["TECH.md"]["sections"]
            assert "Architecture" in tech_sections
            assert "Conventions" in tech_sections

            # Architecture: good completeness + 2 usage entries
            arch = tech_sections["Architecture"]
            assert 0 <= arch["composite"] <= 100
            assert arch["trust"] in ("full", "high", "moderate", "low")
            assert arch["usage"] == 30  # 2 entries * 15 = 30

            # Conventions: short content → lower completeness
            conv = tech_sections["Conventions"]
            assert conv["completeness"] < arch["completeness"]


class TestPersistFlag:
    """#5 fix (run_e90535ea): compute_section_health writes section_health.json for
    the scheduled decay snapshot — but READ paths (engine-metrics GET, ddd-health
    CLI) must NOT write on every hit (read-handler-writes anti-pattern + sync-write
    latency). The write is gated on persist= (default True = back-compat)."""

    def _mk_project(self, tmpdir):
        project_dir = Path(tmpdir)
        (project_dir / "TECH.md").write_text(
            "# Tech\n\n## Architecture\n\n" + ("word " * 60) + "\n"
        )
        (project_dir / ".artifacts").mkdir(parents=True)
        return project_dir

    def test_persist_false_writes_no_file(self):
        """persist=False → identical scores computed, section_health.json NOT written."""
        from core.ddd_health import compute_section_health
        with tempfile.TemporaryDirectory() as tmpdir:
            pd = self._mk_project(tmpdir)
            sh = pd / ".artifacts" / "section_health.json"
            assert not sh.exists()
            result = compute_section_health(pd, persist=False)
            assert "TECH.md" in result["docs"]          # scores still computed
            assert not sh.exists(), "read path must NOT write section_health.json"

    def test_persist_true_default_writes(self):
        """Default (persist=True) still writes atomically — scheduled snapshot path."""
        from core.ddd_health import compute_section_health
        with tempfile.TemporaryDirectory() as tmpdir:
            pd = self._mk_project(tmpdir)
            sh = pd / ".artifacts" / "section_health.json"
            compute_section_health(pd)  # default
            assert sh.exists(), "default must write the snapshot (back-compat)"

    def test_persist_false_and_true_scores_identical(self):
        """persist only gates the WRITE — the returned scores are byte-identical."""
        from core.ddd_health import compute_section_health
        with tempfile.TemporaryDirectory() as tmpdir:
            pd = self._mk_project(tmpdir)
            r_no = compute_section_health(pd, persist=False)
            r_yes = compute_section_health(pd, persist=True)
            # computed_at differs by design; compare the scored docs only
            assert r_no["docs"] == r_yes["docs"]
