"""Tests for Code Change Feed (DDD Cultivation Channel 1).

Tests the heuristic-based detection of architecture-impacting git commits
and generation of CultivationProposal targeting TECH.md.
"""


from hooks.code_change_feed import CodeChangeFeed, ArchChange


class TestParseNameStatus:
    """Test git --name-status output parsing."""

    def test_parses_added_file(self):
        feed = CodeChangeFeed()
        result = feed._parse_name_status(["A\tbackend/core/new_module.py"])
        assert result == [("A", "backend/core/new_module.py")]

    def test_parses_modified_file(self):
        feed = CodeChangeFeed()
        result = feed._parse_name_status(["M\tbackend/core/session_unit.py"])
        assert result == [("M", "backend/core/session_unit.py")]

    def test_parses_renamed_file(self):
        feed = CodeChangeFeed()
        result = feed._parse_name_status(["R100\told/path.py\tnew/path.py"])
        assert result == [("R", "new/path.py")]

    def test_skips_empty_lines(self):
        feed = CodeChangeFeed()
        result = feed._parse_name_status(["", "A\tfile.py", ""])
        assert result == [("A", "file.py")]

    def test_handles_multiple_entries(self):
        feed = CodeChangeFeed()
        lines = [
            "A\tbackend/core/entity_extractor.py",
            "M\tbackend/core/swarm_workspace_manager.py",
            "D\tbackend/old_module.py",
        ]
        result = feed._parse_name_status(lines)
        assert len(result) == 3
        assert result[0] == ("A", "backend/core/entity_extractor.py")
        assert result[2] == ("D", "backend/old_module.py")


class TestDetectArchChanges:
    """Test heuristic architecture detection."""

    def test_new_python_module_detected(self):
        feed = CodeChangeFeed()
        changes = feed._detect_arch_changes([("A", "backend/core/new_thing.py")])
        assert len(changes) == 1
        assert changes[0].change_type == "new_module"
        assert changes[0].confidence == 0.9
        assert changes[0].target_section == "Key Subsystems"

    def test_new_router_detected_as_endpoint(self):
        feed = CodeChangeFeed()
        changes = feed._detect_arch_changes([("A", "backend/routers/new_router.py")])
        assert len(changes) == 1
        assert changes[0].change_type == "api_endpoint"
        assert changes[0].confidence == 0.8

    def test_rename_detected(self):
        feed = CodeChangeFeed()
        changes = feed._detect_arch_changes([("R", "backend/core/renamed.py")])
        assert len(changes) == 1
        assert changes[0].change_type == "rename"
        assert changes[0].confidence == 0.8
        assert changes[0].target_section == "Architecture"

    def test_init_py_change_detected(self):
        feed = CodeChangeFeed()
        changes = feed._detect_arch_changes([
            ("M", "backend/core/__init__.py"),
            ("A", "backend/core/new_thing.py"),  # Need >1 dir or new file
        ])
        init_change = [c for c in changes if c.change_type == "import_change"]
        assert len(init_change) == 1
        assert init_change[0].confidence == 0.7

    def test_skips_test_files(self):
        """AC2: test-only changes produce no proposals."""
        feed = CodeChangeFeed()
        changes = feed._detect_arch_changes([
            ("A", "tests/test_new_thing.py"),
            ("M", "tests/test_existing.py"),
        ])
        assert changes == []

    def test_skips_context_files(self):
        feed = CodeChangeFeed()
        changes = feed._detect_arch_changes([
            ("M", ".context/MEMORY.md"),
            ("M", "Knowledge/DailyActivity/2026-05-16.md"),
        ])
        assert changes == []

    def test_skips_small_same_dir_edits(self):
        """AC2: small same-directory changes don't trigger."""
        feed = CodeChangeFeed()
        # 2 modifications in same dir, no new files
        changes = feed._detect_arch_changes([
            ("M", "backend/core/session_unit.py"),
            ("M", "backend/core/session_utils.py"),
        ])
        assert changes == []

    def test_skips_non_code_files(self):
        feed = CodeChangeFeed()
        changes = feed._detect_arch_changes([
            ("A", "backend/config.json"),
            ("M", "README.md"),
        ])
        assert changes == []


class TestGenerateProposals:
    """Admission root-fix (run_97519f7c): _generate_proposals no longer writes a
    CultivationProposal for an arch change. 'A new module `foo.py` exists' is a GIT
    FACT, not knowledge, and never needed a human decision (R30#4). The arch signal
    is captured in the code_intel graph via _reindex_changed_files, NOT the review
    queue. These tests pin the NEW contract: 0 proposals written."""

    def test_new_module_writes_NO_proposal(self, tmp_path):
        """A new module produces 0 review-queue proposal (was the #1 noise source)."""
        project_dir = tmp_path / "Projects" / "SwarmAI"
        project_dir.mkdir(parents=True)

        feed = CodeChangeFeed()
        arch_changes = [
            ArchChange(
                change_type="new_module",
                path="backend/core/entity_extractor.py",
                confidence=0.9,
                target_section="Key Subsystems",
            )
        ]

        feed._generate_proposals(
            arch_changes, "e3700b09", "feat: Entity Index", str(tmp_path)
        )

        proposals_dir = project_dir / ".artifacts" / "proposals"
        # either the dir was never created, or it holds zero proposals
        assert not proposals_dir.exists() or list(proposals_dir.glob("*.json")) == []

    def test_multiple_arch_changes_write_NO_proposal(self, tmp_path):
        """Even several high-confidence arch changes write nothing to the queue."""
        project_dir = tmp_path / "Projects" / "SwarmAI"
        project_dir.mkdir(parents=True)

        feed = CodeChangeFeed()
        arch_changes = [
            ArchChange("new_module", "backend/core/new.py", 0.9, "Key Subsystems"),
            ArchChange("api_endpoint", "backend/routers/new.py", 0.8, "Key Subsystems"),
            ArchChange("rename", "backend/core/old.py", 0.8, "Architecture"),
        ]
        feed._generate_proposals(
            arch_changes, "abcdef12", "feat: new thing", str(tmp_path)
        )
        proposals_dir = project_dir / ".artifacts" / "proposals"
        assert not proposals_dir.exists() or list(proposals_dir.glob("*.json")) == []
