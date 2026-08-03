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
    """Test CultivationProposal generation."""

    def test_generates_proposal_for_new_module(self, tmp_path):
        """AC1: new module → proposal in .proposals/"""
        # Setup project directory
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

        # Verify proposal was written
        proposals_dir = project_dir / ".artifacts" / "proposals"
        assert proposals_dir.exists()
        proposal_files = list(proposals_dir.glob("*.json"))
        assert len(proposal_files) >= 1

    def test_evidence_includes_commit_hash(self, tmp_path):
        """AC3: source_run_id contains commit SHA."""
        project_dir = tmp_path / "Projects" / "SwarmAI"
        project_dir.mkdir(parents=True)

        feed = CodeChangeFeed()
        arch_changes = [
            ArchChange("new_module", "backend/core/new.py", 0.9, "Key Subsystems")
        ]

        feed._generate_proposals(
            arch_changes, "abcdef12", "feat: new thing", str(tmp_path)
        )

        # Read the proposal
        proposals_dir = project_dir / ".artifacts" / "proposals"
        proposal_files = list(proposals_dir.glob("*.json"))
        assert len(proposal_files) >= 1

        import json
        data = json.loads(proposal_files[0].read_text())
        assert "commit:abcdef12" in data.get("source_run_id", "")

    def test_low_confidence_changes_filtered(self, tmp_path):
        """AC4: confidence < 0.6 → no proposal."""
        project_dir = tmp_path / "Projects" / "SwarmAI"
        project_dir.mkdir(parents=True)

        feed = CodeChangeFeed()
        arch_changes = [
            ArchChange("minor", "backend/util.py", 0.5, "Architecture")
        ]

        feed._generate_proposals(
            arch_changes, "abc123", "chore: minor", str(tmp_path)
        )

        proposals_dir = project_dir / ".artifacts" / "proposals"
        if proposals_dir.exists():
            assert list(proposals_dir.glob("*.json")) == []
