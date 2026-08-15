"""Property-based tests for project CRUD operations.

Tests the ``SwarmWorkspaceManager`` project lifecycle methods using
Hypothesis to verify universal correctness properties across randomised
valid inputs.

Key properties verified:

- ``test_project_creation_produces_complete_scaffold``
    — Property 1: Every valid project name produces a complete directory
      scaffold with all template items and correct ``.project.json`` defaults.
- ``test_project_crud_round_trip``
    — Property 5: For any set of created projects, create→get(id) returns
      matching metadata, create→list includes it, get_by_name returns the
      same, and delete→get raises ValueError.

**Feature: swarmws-projects**
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import given, strategies as st

from core.swarm_workspace_manager import SwarmWorkspaceManager
from core.project_schema_migrations import CURRENT_SCHEMA_VERSION
from tests.helpers import PROPERTY_SETTINGS



# ---------------------------------------------------------------------------
# Hypothesis settings
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

valid_project_names = st.from_regex(
    r"[a-zA-Z0-9][a-zA-Z0-9 _.\-]{0,99}", fullmatch=True
).filter(lambda n: n.strip() == n)

# ---------------------------------------------------------------------------
# Expected template items
# ---------------------------------------------------------------------------

EXPECTED_FILES = {".project.json"}
EXPECTED_DIRS: set[str] = set()

REQUIRED_METADATA_FIELDS = {
    "id", "name", "description", "created_at", "updated_at",
    "status", "tags", "priority", "schema_version", "version",
    "update_history",
}


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestProjectCreationScaffold:
    """Property 1: Project creation produces complete metadata and template.

    # Feature: swarmws-projects, Property 1: Project creation produces complete metadata and template

    *For any* valid project name, ``create_project()`` should produce a
    directory containing all Standard Project Template items and a
    ``.project.json`` with all required fields, ``version=1``,
    ``schema_version="1.0.0"``, and exactly one ``created`` history entry.

    **Validates: Requirements 4.2, 4.3, 5.1, 5.5, 18.1, 27.1, 27.2, 27.3, 31.3, 32.1**
    """

    @given(name=valid_project_names)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_project_creation_produces_complete_scaffold(
        self,
        tmp_path: Path,
        name: str,
    ):
        """Every valid project name produces a complete scaffold with correct defaults.

        **Validates: Requirements 4.2, 4.3, 5.1, 5.5, 18.1, 27.1, 27.2, 27.3, 31.3, 32.1**
        """
        # Use a unique workspace dir per Hypothesis example to avoid collisions
        workspace_dir = tmp_path / str(uuid4())
        workspace_dir.mkdir(parents=True, exist_ok=True)
        projects_dir = workspace_dir / "Projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        manager = SwarmWorkspaceManager()
        result = await manager.create_project(
            project_name=name,
            workspace_path=str(workspace_dir),
        )

        project_dir = projects_dir / name

        # --- Verify directory exists ---
        assert project_dir.exists(), f"Project directory '{name}' was not created"
        assert project_dir.is_dir(), f"Project path '{name}' is not a directory"

        # --- Verify all expected files exist ---
        for expected_file in EXPECTED_FILES:
            file_path = project_dir / expected_file
            assert file_path.exists(), (
                f"Expected file '{expected_file}' missing from project scaffold"
            )
            assert file_path.is_file(), (
                f"Expected '{expected_file}' to be a file, not a directory"
            )

        # --- Verify all expected directories exist ---
        for expected_dir in EXPECTED_DIRS:
            dir_path = project_dir / expected_dir
            assert dir_path.exists(), (
                f"Expected directory '{expected_dir}' missing from project scaffold"
            )
            assert dir_path.is_dir(), (
                f"Expected '{expected_dir}' to be a directory, not a file"
            )

        # --- Verify .project.json content ---
        metadata_path = project_dir / ".project.json"
        raw = metadata_path.read_text()
        metadata = json.loads(raw)

        # All required fields present
        for field in REQUIRED_METADATA_FIELDS:
            assert field in metadata, (
                f"Required field '{field}' missing from .project.json"
            )

        # Field value checks
        assert metadata["name"] == name
        assert metadata["description"] == ""
        assert metadata["status"] == "active"
        assert metadata["tags"] == []
        assert metadata["priority"] is None
        assert metadata["version"] == 1
        assert metadata["schema_version"] == CURRENT_SCHEMA_VERSION
        # DDD six-section spec version stamp (§3.7 anti-drift): a provisioned project
        # must record which DDD spec version it was scaffolded under, so propagated
        # DDDs are version-traceable. Sourced from the DDD_SPEC_VERSION module constant.
        from core.swarm_workspace_manager import DDD_SPEC_VERSION
        assert metadata["ddd_spec_version"] == DDD_SPEC_VERSION

        # UUID is a non-empty string
        assert isinstance(metadata["id"], str) and len(metadata["id"]) > 0

        # Timestamps are non-empty strings
        assert isinstance(metadata["created_at"], str) and len(metadata["created_at"]) > 0
        assert isinstance(metadata["updated_at"], str) and len(metadata["updated_at"]) > 0

        # --- Verify update_history ---
        history = metadata["update_history"]
        assert isinstance(history, list)
        assert len(history) == 1, (
            f"Expected exactly 1 history entry, got {len(history)}"
        )

        entry = history[0]
        assert entry["version"] == 1
        assert entry["action"] == "created"
        assert entry["changes"] == {}
        assert entry["source"] == "user"
        assert isinstance(entry["timestamp"], str) and len(entry["timestamp"]) > 0

        # --- Verify return value matches file content ---
        assert result["id"] == metadata["id"]
        assert result["name"] == metadata["name"]
        assert result["version"] == metadata["version"]
        assert result["schema_version"] == metadata["schema_version"]



class TestProjectCRUDRoundTrip:
    """Property 5: Project CRUD Round-Trip.

    # Feature: swarmws-projects, Property 5: Project create-then-read round trip

    *For any* valid project name, the full CRUD lifecycle should be
    consistent: create→get(id) returns matching metadata,
    create→list includes the project, get_by_name returns the same
    metadata, and delete→get(id) raises ValueError.

    **Validates: Requirements 4.6, 18.3, 18.4, 18.6, 18.9, 31.6**
    """

    @given(name=valid_project_names)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_project_crud_round_trip(
        self,
        tmp_path: Path,
        name: str,
    ):
        """Create→get→list→get_by_name→delete→get round trip is consistent.

        **Validates: Requirements 4.6, 18.3, 18.4, 18.6, 18.9, 31.6**
        """
        # Use a unique workspace dir per Hypothesis example to avoid collisions
        workspace_dir = tmp_path / str(uuid4())
        workspace_dir.mkdir(parents=True, exist_ok=True)
        projects_dir = workspace_dir / "Projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        ws = str(workspace_dir)
        manager = SwarmWorkspaceManager()

        # ── CREATE ───────────────────────────────────────────────────
        created = await manager.create_project(
            project_name=name,
            workspace_path=ws,
        )
        project_id = created["id"]

        # ── GET by id ────────────────────────────────────────────────
        fetched = await manager.get_project(project_id, workspace_path=ws)

        assert fetched["id"] == project_id
        assert fetched["name"] == name
        assert fetched["version"] == created["version"]
        assert fetched["schema_version"] == created["schema_version"]
        assert fetched["status"] == created["status"]
        assert fetched["tags"] == created["tags"]
        assert fetched["description"] == created["description"]
        assert fetched["priority"] == created["priority"]
        assert fetched["created_at"] == created["created_at"]

        # ── LIST includes the project ────────────────────────────────
        all_projects = await manager.list_projects(workspace_path=ws)
        listed_ids = [p["id"] for p in all_projects]
        assert project_id in listed_ids, (
            f"Created project {project_id} not found in list_projects"
        )

        # Find the matching entry and verify metadata consistency
        listed = next(p for p in all_projects if p["id"] == project_id)
        assert listed["name"] == name
        assert listed["version"] == created["version"]
        assert listed["schema_version"] == created["schema_version"]

        # ── GET by name ──────────────────────────────────────────────
        by_name = await manager.get_project_by_name(name, workspace_path=ws)

        assert by_name["id"] == project_id
        assert by_name["name"] == name
        assert by_name["version"] == created["version"]
        assert by_name["created_at"] == created["created_at"]

        # ── DELETE ───────────────────────────────────────────────────
        deleted = await manager.delete_project(project_id, workspace_path=ws)
        assert deleted is True

        # ── GET after delete raises ValueError ───────────────────────
        with pytest.raises(ValueError):
            await manager.get_project(project_id, workspace_path=ws)


class TestDeleteProjectPreserves:
    """run_a456640f (STEERING #20 + SOUL safety 'trash > rm'): a user-initiated
    project delete must PRESERVE the DDD (move to a recoverable .trash/), NEVER
    hard ``rmtree`` an irreplaceable knowledge tree. It still returns True and
    the project disappears from listings — but the bytes survive, recoverable."""

    @pytest.mark.asyncio
    async def test_delete_moves_to_trash_not_rmtree(self, tmp_path: Path):
        ws = tmp_path / "ws"
        (ws / "Projects").mkdir(parents=True)
        manager = SwarmWorkspaceManager()
        created = await manager.create_project(project_name="Precious", workspace_path=str(ws))
        pid = created["id"]
        pdir = ws / "Projects" / "Precious"
        # drop a unique sentinel so we can prove the bytes survived
        (pdir / "PRODUCT.md").write_text("DO_NOT_LOSE_THIS_KNOWLEDGE")

        deleted = await manager.delete_project(pid, workspace_path=str(ws))
        assert deleted is True

        # GONE from the live tree + listings
        assert not pdir.exists(), "project dir removed from the live Projects/ tree"
        with pytest.raises(ValueError):
            await manager.get_project(pid, workspace_path=str(ws))

        # PRESERVED in .trash — the knowledge is recoverable, not destroyed
        trash_root = ws / "Projects" / ".trash"
        survivors = list(trash_root.glob("Precious*/PRODUCT.md")) if trash_root.exists() else []
        assert survivors, "deleted DDD must be preserved under Projects/.trash/, not rmtree'd"
        assert survivors[0].read_text() == "DO_NOT_LOSE_THIS_KNOWLEDGE", "bytes preserved intact"

    @pytest.mark.asyncio
    async def test_default_project_still_cannot_be_deleted(self, tmp_path: Path):
        """The preserve-refactor must not weaken the existing default-project guard."""
        manager = SwarmWorkspaceManager()
        with pytest.raises(ValueError):
            await manager.delete_project("swarmai-default", workspace_path=str(tmp_path / "ws"))


# ---------------------------------------------------------------------------
# Six-section canonical DDD structure (DDD-agent-brain spec §3.6) — physical
# scaffold. Fixed project name (NOT Hypothesis) to keep the path-assertion
# oracle unambiguous (Gate-0 E: a generated name equal to a section dir would
# make "is there a skills/ dir" ambiguous).
# ---------------------------------------------------------------------------

# SSOT (skeptic Gate-1): the expected skeleton is DERIVED from the scaffold
# source of truth (SECTION_SCAFFOLD.keys() = ①⑥ files; SECTION_DIRS = ③④ dirs),
# NOT a hand-maintained literal that drifts. ② (4 docs + Knowledge/) is covered
# by the scaffold test above; ⑤ bindings is provisioned by BIND, not CREATE.
from core.swarm_workspace_manager import (
    SECTION_SCAFFOLD, SECTION_DIRS, DDD_NATIVE_SKILLS, INTERNAL_DDD_SKILLS,
)

# The 5 default DDD-native skills — SSOT from the code constant, so the test can
# never drift from what provisioning actually copies (D3/D6).
EXPECTED_NATIVE_SKILLS = set(DDD_NATIVE_SKILLS)


class TestSixSectionScaffold:
    """The canonical six-section DDD structure is physically materialized at
    CREATE (option A, XG decision 2026-07-12; refined same day to remove the
    over-build): ① identity manifests + ⑥ marker as FILES, ③④ as empty section
    DIRS (with .gitkeep, no prose README — AGENTS.md is the single README).
    NO agents/ or agent-sops/ (those are AIM-export-form, D1). Content accretes."""

    @pytest.mark.asyncio
    async def test_create_scaffolds_six_section_skeleton(self, tmp_path: Path):
        ws = tmp_path / "ws"
        (ws / "Projects").mkdir(parents=True)
        manager = SwarmWorkspaceManager()
        await manager.create_project(project_name="SixSecProj", workspace_path=str(ws))
        pdir = ws / "Projects" / "SixSecProj"

        # D1/D6 SSOT: every SECTION_SCAFFOLD file exists + non-empty.
        for rel in SECTION_SCAFFOLD:
            p = pdir / rel
            assert p.exists(), f"six-section scaffold missing: {rel}"
            assert p.is_file(), f"{rel} should be a file"
            assert p.read_text(encoding="utf-8").strip(), f"{rel} must not be empty"

        # D1: ③④ section DIRS exist (via .gitkeep) — empty but present.
        for reldir in SECTION_DIRS:
            d = pdir / reldir
            assert d.is_dir(), f"section dir missing: {reldir}"
            assert (d / ".gitkeep").exists(), f"{reldir} missing .gitkeep marker"

        # D1: agents/ and agent-sops/ must NOT be scaffolded (AIM-export-form only).
        assert not (pdir / "agents").exists(), "agents/ is AIM-export-form, not SwarmWS-native (D1)"
        assert not (pdir / "agent-sops").exists(), "agent-sops/ is AIM-export-form, not SwarmWS-native (D1)"

        # D2: exactly ONE README (AGENTS.md) — no per-section README stubs.
        readmes = [p for p in pdir.rglob("README.md")]
        assert readmes == [], f"per-section READMEs must not exist (D2 single AGENTS.md): {readmes}"
        assert (pdir / "AGENTS.md").exists(), "the single unified README AGENTS.md must exist"

        # D2: the unified AGENTS.md documents all six sections + the accretion rule.
        agents_md = (pdir / "AGENTS.md").read_text(encoding="utf-8")
        agents_lower = agents_md.lower()
        for marker in ("①", "②", "③", "④", "⑤", "⑥"):
            assert marker in agents_md, f"AGENTS.md must document section {marker}"
        assert "accrete" in agents_lower, "AGENTS.md must state the accretion rule (purpose/what-belongs)"

        # {project_name} templating actually applied (not a literal placeholder).
        assert "{project_name}" not in agents_md, "template placeholder left unfilled"
        assert "SixSecProj" in agents_md, "project name not templated into AGENTS.md"

        # ① aim.json VALID JSON after templating (Gate-2 CRITICAL history) + D3:
        # declares exactly the 5 default DDD-native skills.
        aim = json.loads((pdir / "aim.json").read_text(encoding="utf-8"))
        assert aim["name"] == "SixSecProj"
        assert aim["ddd_spec_version"] == "1.0"
        declared = set(aim["plugins"]["native_skills"])
        assert declared == EXPECTED_NATIVE_SKILLS, (
            f"aim.json must declare the 5 native skills (D3), got: {declared}"
        )

        # ★ THE CORE FIX: the 5 native skills must PHYSICALLY EXIST in ④
        # 4-capabilities/ — not merely be declared in aim.json. "declared a name"
        # != "skill exists". This is what makes the DDD self-養成 after `aim`
        # export to Kiro/Claude Code. (Numbered tree, redesign 2026-07-21: was skills/.)
        for skill in DDD_NATIVE_SKILLS:
            skill_md = pdir / "4-capabilities" / skill / "SKILL.md"
            assert skill_md.exists(), (
                f"DDD-native skill '{skill}' must be COPIED into 4-capabilities/ at create "
                f"(declared in aim.json but missing on disk = the bug this fixes)"
            )
            body = skill_md.read_text(encoding="utf-8")
            assert body.strip(), f"{skill}/SKILL.md must have real content"
            assert body.startswith("---"), f"{skill}/SKILL.md must have frontmatter"

        # A NON-internal project must NOT get the internal toolchain skills.
        for skill in INTERNAL_DDD_SKILLS:
            assert not (pdir / "4-capabilities" / skill).exists(), (
                f"non-internal DDD must NOT carry internal skill '{skill}'"
            )

    @pytest.mark.asyncio
    async def test_internal_ddd_gets_internal_skills_and_gate(self, tmp_path: Path):
        """An internal DDD (Brazil/CRUX-bound) gets the 3 internal toolchain skills
        + the no_git_push gate COPIED IN, on top of the 5 native skills (D5)."""
        ws = tmp_path / "ws"
        (ws / "Projects").mkdir(parents=True)
        pdir = ws / "Projects" / "IntProj"
        pdir.mkdir()
        manager = SwarmWorkspaceManager()
        await manager.migrate_project_to_six_section(
            "IntProj", workspace_path=str(ws), internal=True)

        # 5 native + 3 internal skills all physically present (④ 4-capabilities/)
        for skill in DDD_NATIVE_SKILLS + INTERNAL_DDD_SKILLS:
            assert (pdir / "4-capabilities" / skill / "SKILL.md").exists(), (
                f"internal DDD must carry skill '{skill}'"
            )
        # the no_git_push gate + its test copied in (③ 3-gates/ moat seed)
        assert (pdir / "3-gates" / "no_git_push.py").exists(), \
            "internal DDD must get the no_git_push gate"
        assert (pdir / "3-gates" / "test_no_git_push.py").exists(), \
            "the gate must ship with its knockout test"

    @pytest.mark.asyncio
    async def test_migrate_moves_old_layout_into_numbered_tree(self, tmp_path: Path):
        """migrate_project_to_six_section physically RELOCATES an existing OLD-layout
        DDD into the numbered tree: 4 docs → 2-understanding/, Knowledge/ →
        2-understanding/knowledge/, gates/ → 3-gates/, skills/ → 4-capabilities/.
        Human content is preserved byte-for-byte; AGENTS.md stays at root."""
        ws = tmp_path / "ws"
        pdir = ws / "Projects" / "LegacyDDD"
        pdir.mkdir(parents=True)
        # An OLD-layout DDD: docs at root, bare section dirs with real content.
        (pdir / "TECH.md").write_text("# legacy tech\nHUMAN_MARKER_T", encoding="utf-8")
        (pdir / "PRODUCT.md").write_text("# legacy product\nHUMAN_MARKER_P", encoding="utf-8")
        (pdir / "IMPROVEMENT.md").write_text("# imp", encoding="utf-8")
        (pdir / "PROJECT.md").write_text("# proj", encoding="utf-8")
        (pdir / "Knowledge").mkdir()
        (pdir / "Knowledge" / "note.md").write_text("HUMAN_MARKER_K", encoding="utf-8")
        (pdir / "gates").mkdir()
        (pdir / "gates" / "my_gate.py").write_text("HUMAN_MARKER_G", encoding="utf-8")
        (pdir / "skills").mkdir()
        (pdir / "skills" / "s_custom").mkdir()
        (pdir / "skills" / "s_custom" / "SKILL.md").write_text("HUMAN_MARKER_S", encoding="utf-8")

        manager = SwarmWorkspaceManager()
        await manager.migrate_project_to_six_section("LegacyDDD", workspace_path=str(ws))

        # Docs relocated into 2-understanding/, content preserved, root copies gone.
        assert (pdir / "2-understanding" / "TECH.md").read_text(encoding="utf-8").endswith("HUMAN_MARKER_T")
        assert not (pdir / "TECH.md").exists(), "root TECH.md must be moved, not left behind"
        assert (pdir / "2-understanding" / "PRODUCT.md").read_text(encoding="utf-8").endswith("HUMAN_MARKER_P")

        # Knowledge corpus relocated under ②, content preserved.
        assert (pdir / "2-understanding" / "knowledge" / "note.md").read_text(encoding="utf-8") == "HUMAN_MARKER_K"
        assert not (pdir / "Knowledge").exists(), "old per-DDD Knowledge/ must be moved"

        # gates/ → 3-gates/, skills/ → 4-capabilities/, human content preserved.
        assert (pdir / "3-gates" / "my_gate.py").read_text(encoding="utf-8") == "HUMAN_MARKER_G"
        assert not (pdir / "gates").exists(), "old gates/ must be moved"
        assert (pdir / "4-capabilities" / "s_custom" / "SKILL.md").read_text(encoding="utf-8") == "HUMAN_MARKER_S"
        assert not (pdir / "skills").exists(), "old skills/ must be moved"

        # ① AGENTS.md provisioned at root; re-run is idempotent (no crash, no dup).
        assert (pdir / "AGENTS.md").is_file()
        await manager.migrate_project_to_six_section("LegacyDDD", workspace_path=str(ws))
        assert (pdir / "2-understanding" / "TECH.md").read_text(encoding="utf-8").endswith("HUMAN_MARKER_T")

    @pytest.mark.asyncio
    async def test_provision_is_idempotent(self, tmp_path: Path):
        """Re-provisioning writes nothing new (AC3) — never clobbers hand-authored content."""
        ws = tmp_path / "ws"
        (ws / "Projects").mkdir(parents=True)
        manager = SwarmWorkspaceManager()
        await manager.create_project(project_name="IdemProj", workspace_path=str(ws))
        # second provision pass over the now-populated project → zero new files
        created_second = await manager.provision_project_ddd("IdemProj", workspace_path=str(ws))
        assert created_second == [], f"re-provision must be a no-op, got: {created_second}"

    @pytest.mark.asyncio
    async def test_migrate_prune_never_deletes_human_content(self, tmp_path: Path):
        """Gate-2 CRITICAL regression (2026-07-12): _prune_legacy_scaffold must
        content-gate — delete the SHIPPED legacy README stub, KEEP a human-edited
        one; prune an agents/ holding only .gitkeep, KEEP one holding a real file."""
        ws = tmp_path / "ws"
        (ws / "Projects").mkdir(parents=True)
        pdir = ws / "Projects" / "MigTest"
        pdir.mkdir()
        # (a) HUMAN-authored gates/README — diverges from stub → MUST survive
        (pdir / "gates").mkdir()
        (pdir / "gates" / "README.md").write_text(
            "# My custom gate notes\nIMPORTANT human docs, do not delete!", encoding="utf-8")
        # (b) LEGACY skills/README stub (carries the old markers) → MUST be pruned
        (pdir / "skills").mkdir()
        (pdir / "skills" / "README.md").write_text(
            "# ④ Capabilities — skills\n\nSection ④. Content ACCRETES as bound.", encoding="utf-8")
        # (c) agents/ with ONLY .gitkeep → MUST be pruned (no half-state)
        (pdir / "agents").mkdir()
        (pdir / "agents" / ".gitkeep").write_text("", encoding="utf-8")
        # (d) agent-sops/ with a REAL sop → MUST be kept for human review
        (pdir / "agent-sops").mkdir()
        (pdir / "agent-sops" / "deploy.sop.md").write_text("real human SOP", encoding="utf-8")

        manager = SwarmWorkspaceManager()
        await manager.migrate_project_to_six_section("MigTest", workspace_path=str(ws))

        # Human gates/README survives — pruned FIRST (content diverges from stub →
        # kept), THEN relocated into the numbered ③ dir. Data preserved, new path.
        assert (pdir / "3-gates" / "README.md").exists(), \
            "human-authored gates/README.md must survive + relocate to 3-gates/ (data-loss guard)"
        assert "IMPORTANT human docs" in (pdir / "3-gates" / "README.md").read_text(encoding="utf-8"), \
            "human content must be byte-preserved through prune+relocate"
        # Legacy skills/README stub is pruned BEFORE relocate → never lands in 4-capabilities/.
        assert not (pdir / "skills" / "README.md").exists(), \
            "legacy skills/README stub must be pruned"
        assert not (pdir / "4-capabilities" / "README.md").exists(), \
            "legacy stub must not be carried into 4-capabilities/ by relocate"
        assert not (pdir / "agents").exists(), \
            "agents/ with only .gitkeep must be pruned (no D1-violating half-state)"
        assert (pdir / "agent-sops" / "deploy.sop.md").exists(), \
            "agent-sops/ with a real file must be kept for human review"

    @pytest.mark.asyncio
    async def test_refresher_marks_bind_activation(self, tmp_path: Path):
        """⑥ REFRESHER.md is a shape-neutral marker: it must state it activates on
        BIND and is a no-op for a no-repo project (Gate-0/Gate-1 F reconciliation —
        the skeleton is concrete but the semantics honor GOVERN-a-physical-repo)."""
        ws = tmp_path / "ws"
        (ws / "Projects").mkdir(parents=True)
        manager = SwarmWorkspaceManager()
        await manager.create_project(project_name="RefProj", workspace_path=str(ws))
        body = (ws / "Projects" / "RefProj" / "REFRESHER.md").read_text(encoding="utf-8").lower()
        assert "bind" in body, "REFRESHER must explain it activates when a repo is bound (⑤)"
        assert ("no-op" in body or "no repo" in body or "not-yet-built" in body), (
            "REFRESHER must state it's a no-op without a bound repo"
        )

    @pytest.mark.asyncio
    async def test_create_scaffolds_numbered_self_explaining_tree(self, tmp_path: Path):
        """CREATE materializes the NUMBERED six-section tree so the file listing
        reads ①→⑥ (redesign 2026-07-21). The 4 canonical docs live under
        2-understanding/; sections are 3-gates/ 4-capabilities/ (not bare
        gates/ skills/); knowledge corpus is 2-understanding/knowledge/."""
        ws = tmp_path / "ws"
        (ws / "Projects").mkdir(parents=True)
        manager = SwarmWorkspaceManager()
        await manager.create_project(project_name="NumTree", workspace_path=str(ws))
        pdir = ws / "Projects" / "NumTree"

        # ② the 4 canonical docs live UNDER 2-understanding/, not at root.
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            assert (pdir / "2-understanding" / doc).is_file(), (
                f"{doc} must be provisioned under 2-understanding/ (② numbered)"
            )
            assert not (pdir / doc).exists(), (
                f"{doc} must NOT remain at project root (moved into 2-understanding/)"
            )

        # ② knowledge corpus dir is nested under Understanding.
        assert (pdir / "2-understanding" / "knowledge").is_dir(), (
            "recall corpus must be 2-understanding/knowledge/"
        )

        # ③④ numbered section dirs exist; the OLD bare names must NOT.
        assert (pdir / "3-gates").is_dir(), "③ must be 3-gates/"
        assert (pdir / "4-capabilities").is_dir(), "④ must be 4-capabilities/"
        assert not (pdir / "gates").exists(), "old bare gates/ must not be scaffolded"
        assert not (pdir / "skills").exists(), "old bare skills/ must not be scaffolded"
        assert not (pdir / "Knowledge").exists(), "old bare per-DDD Knowledge/ must not be scaffolded"

        # ① AGENTS.md STAYS at root (the external door-plate, H4).
        assert (pdir / "AGENTS.md").is_file(), "① AGENTS.md stays at project root"

        # ④ native skills physically land under 4-capabilities/ (not skills/).
        for skill in DDD_NATIVE_SKILLS:
            assert (pdir / "4-capabilities" / skill / "SKILL.md").is_file(), (
                f"native skill {skill} must be copied into 4-capabilities/"
            )


class TestDddNativeSkills:
    """D4: the DDD-native skill TEMPLATES (the official maintained source at
    backend/templates/ddd-skills/) are DECOUPLED (no data.db / artifact_cli
    hard-dependency) and RETAIN the moat (Gate-2 adversarial + 养成 ladder).
    These are the templates copied INTO each DDD's skills/ at provision — NOT
    SwarmAI-native skills (those live in backend/skills/ and are never touched)."""

    SKILLS_DIR = Path(__file__).resolve().parent.parent / "templates" / "ddd-skills"

    @pytest.mark.parametrize("skill", ["s_ddd-pipeline", "s_ddd-pollinate"])
    def test_native_skill_exists(self, skill: str):
        d = self.SKILLS_DIR / skill
        assert (d / "SKILL.md").exists(), f"{skill}/SKILL.md must exist"
        assert (d / "INSTRUCTIONS.md").exists() or (d / "SKILL.md").read_text(encoding="utf-8").strip(), (
            f"{skill} must ship real content"
        )

    def test_ddd_pipeline_is_decoupled_and_retains_moat(self):
        """The whole point of s_ddd-pipeline: portable (no SwarmAI backend) yet
        keeps the adversarial moat. A copy that still imports data.db/artifact_cli
        would NOT be portable — that failure must be caught."""
        d = self.SKILLS_DIR / "s_ddd-pipeline"
        text = (d / "SKILL.md").read_text(encoding="utf-8") + (d / "INSTRUCTIONS.md").read_text(encoding="utf-8")
        low = text.lower()
        # Decoupled: it may NAME data.db/artifact_cli to say "no longer uses them",
        # but must NOT declare a runtime dependency. Assert it uses file-state instead.
        assert ".artifacts" in low and "run.json" in low, "must use file-based .artifacts/ state"
        assert "no data.db" in low or "no `data.db`" in low, "must state it drops the data.db coupling"
        # Moat retained (non-negotiable):
        assert "adversarial" in low, "must retain Gate-2 adversarial-before-commit"
        assert "养成" in text or "ladder" in low, "must retain the 养成 ladder"
        assert ("forbidden" in low or "blocking" in low or "never skip" in low), (
            "the moat must be BLOCKING, not optional"
        )

    def test_ddd_pollinate_is_message_first_and_portable(self):
        d = self.SKILLS_DIR / "s_ddd-pollinate"
        low = (d / "SKILL.md").read_text(encoding="utf-8").lower()
        assert "message" in low and "audience" in low, "must retain message-first/audience principle"
        assert "product.md" in low, "must source value from the DDD's own ② PRODUCT.md (portable)"

    def test_ddd_pollinate_shared_files_track_the_source(self):
        """EVERY html-deck shared/ runtime file is a copy of the s_pollinate
        source-of-truth — they carry no DDD-specific adaptation and MUST stay
        byte-identical. export-pdf.sh silently drifted a full generation behind
        (base64+page.pdf() vs screenshot+pdf-lib) and nothing caught it for months
        (run_ff9db326). This guard is DIRECTORY-DRIVEN (not a hardcoded file list) so a
        NEW shared file is auto-covered, and a file present in one tree but not the
        other is also caught — both are drift."""
        src = (Path(__file__).resolve().parent.parent / "skills" / "s_pollinate"
               / "templates" / "html-deck" / "shared")
        dst = self.SKILLS_DIR / "s_ddd-pollinate" / "templates" / "html-deck" / "shared"
        assert src.is_dir(), f"source shared dir missing: {src}"
        assert dst.is_dir(), f"ddd-pollinate shared dir missing: {dst}"
        # Union of both trees' files → catches presence-mismatch, not just content drift.
        src_files = {p.name for p in src.iterdir() if p.is_file()}
        dst_files = {p.name for p in dst.iterdir() if p.is_file()}
        assert src_files == dst_files, (
            "shared/ file SET diverges between the two trees — "
            f"only in source: {sorted(src_files - dst_files)}; "
            f"only in ddd-pollinate: {sorted(dst_files - src_files)}. "
            f"Re-sync the directory: rsync -a '{src}/' '{dst}/'"
        )
        assert src_files, "source shared/ is empty — path wrong?"
        for name in sorted(src_files):
            s, b = src / name, dst / name
            assert s.read_bytes() == b.read_bytes(), (
                f"shared/{name} drifted from the s_pollinate source-of-truth — "
                f"re-sync: cp '{s}' '{b}' (these files carry no DDD-specific adaptation)"
            )

    # ─── Verbatim-copy drift guard — manifest owned by scripts/ddd_verbatim_sync.py ───
    # UNLIKE shared/ (100% verbatim, dir-driven above), the scripts/ + engine/ dirs are
    # MIXED — some files are verbatim copies (must stay byte-identical to a backend/
    # source), others are DELIBERATE portability forks (_ddd_paths.py, artifact_cli.py,
    # ai_ready_helpers.py, …) whose whole point is to differ. "verbatim vs adapted" is a
    # human judgment, so the pairs live in a CURATED manifest. That manifest is the
    # SINGLE SOURCE OF TRUTH in scripts/ddd_verbatim_sync.py — these tests IMPORT it
    # (a duplicated manifest would itself drift, the very failure this mechanism prevents).
    # Re-check / discover / repair from the CLI:
    #   python scripts/ddd_verbatim_sync.py verify | discover | sync [--dry-run]

    def _sync_mod(self):
        from scripts import ddd_verbatim_sync
        return ddd_verbatim_sync

    def test_ddd_verbatim_copies_are_in_sync(self):
        """Every verbatim-copied ddd-skill file MUST stay byte-identical to its
        backend/ source-of-truth (generalizes the shared/ drift guard, run_ff9db326,
        to the scripts/ + engine/ copies across ALL ddd-skills). Delegates to the
        script's verify() so test and CLI can never disagree."""
        mod = self._sync_mod()
        rc = mod.verify()
        assert rc == 0, (
            f"{len(mod.VERBATIM_PAIRS)} verbatim pairs — one or more drifted from (or are "
            "missing vs) their source-of-truth (see stdout). "
            "Repair: python scripts/ddd_verbatim_sync.py sync"
        )

    def test_ddd_verbatim_manifest_is_complete(self):
        """COMPLETENESS: no ddd-skill file may be byte-identical to a backend/ source
        WITHOUT being in the manifest — else a new verbatim copy could be added and
        silently drift, unwatched (the exact failure the manifest prevents). Delegates
        to the script's discover()."""
        mod = self._sync_mod()
        rc = mod.discover()
        assert rc == 0, (
            "ddd-skill file(s) are byte-identical to a backend/ source but NOT in the "
            "manifest (see stdout). Add them to VERBATIM_PAIRS (track the source) or "
            "_KNOWN_NON_VERBATIM (coincidental) in scripts/ddd_verbatim_sync.py."
        )
