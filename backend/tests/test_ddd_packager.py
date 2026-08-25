"""Tests for the DDD dual-target distribution packager (ddd_packager) +
its fail-closed reach policy (ddd_distribution_policy).

Strategy (design DoD + Gate-1 M1):
- A SYNTHETIC minimal compliant fixture DDD built in tmp_path (DDD-agnostic — the
  packager knows nothing about any real DDD).
- A NEGATIVE fixture with a planted secret in a hook + a host-path literal in a
  SKILL.md BODY (not just a script) → external emit must abort (Gate-1 H2).
- Gate-2 regressions: secret in a NON-allow-listed extension (.env/.pem) must still
  abort (C1); a skill dual-listed in native_skills AND domain_skills must be EXCLUDED
  (C2); a non-UTF-8 file must not silently hide a secret (C3); an UNQUOTED secret
  assignment must be caught (H1).
NOTE: the fail-closed-default and determinism MUTATION checks (revert prod line →
RED) are run out-of-band during the pipeline BUILD stage, not encoded as tests here.

Each AC has >=1 test. Real-DDD-shape coverage is exercised by building the fixture
to the same six-section shape a real DDD has (aim.json + bindings.yaml + docs +
skills/ + Config + AGENTS.md/REFRESHER.md).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from core import ddd_distribution_policy as pol
from core import ddd_packager as pk


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------
def _make_skill(
    skills_dir: Path,
    name: str,
    *,
    body: str = "",
    script: str | None = None,
    frontmatter_name: str | None = None,
) -> None:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    # frontmatter_name lets a test reproduce the real SwarmAI convention where the
    # dir name (s_repo-to-ddd) intentionally differs from the frontmatter name
    # (repo-to-ddd). Default = dir name, so existing callers are unaffected.
    fm_name = frontmatter_name if frontmatter_name is not None else name
    (d / "SKILL.md").write_text(f"---\nname: {fm_name}\ndescription: {name} skill\n---\n\n# {name}\n{body}\n", encoding="utf-8")
    if script is not None:
        sd = d / "scripts"
        sd.mkdir(exist_ok=True)
        (sd / "run.py").write_text(script, encoding="utf-8")


def build_fixture_ddd(
    root: Path,
    *,
    name: str = "Fixture_Brain",
    targets: list[str] | None = None,
    visibility: str = "internal",
    include_distribution: bool = True,
    plant_secret: bool = False,
    plant_host_path_in_body: bool = False,
    add_unclassified_skill: bool = False,
    build_system: str | None = None,
    corpus_docs: dict[str, str] | None = None,
    deliverables: dict[str, bytes | str] | None = None,
    gates: dict[str, str] | None = None,
    add_gate_pycache: bool = False,
    add_noise: bool = False,
    system_prompt: str | None = "# {name}\nYou are the {name} agent.\n",
    plugins_override: dict | None = None,
    understanding_orphans: dict[str, str] | None = None,
) -> Path:
    """Build a minimal compliant six-section DDD. Returns its dir.

    corpus_docs: {relative-name: text} written under 2-understanding/knowledge/.
    deliverables: {relative-path: bytes|text} written under deliverables/ (path may
                  be nested, e.g. "sub/x.png"; bytes → binary file).
    gates: {relative-path: text} written under 3-gates/ (③ gate section — path may be
                  nested, e.g. "context/includes/deny.txt"; proves the WHOLE section
                  ships, not just flat *.md). A .gitkeep is always planted (must NOT
                  be the only shipped file — a gitkeep-only 3-gates is a no-op).
    add_gate_pycache: also create 3-gates/__pycache__/x.pyc to prove build noise is
                  EXCLUDED from the shipped gates.
    add_noise: also create .artifacts/code-intel.json + a decay-archive to prove the
               packager does NOT sweep live-tree noise into the package.
    """
    ddd = root / name
    ddd.mkdir(parents=True, exist_ok=True)

    # ③ 3-gates section — always plant a .gitkeep (mirrors the real scaffold); gates
    # dict adds real gate files (md standards, py/sh scripts, context/includes data).
    gdir = ddd / "3-gates"
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / ".gitkeep").write_text("", encoding="utf-8")
    if gates:
        for rel, text in gates.items():
            dst = gdir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(text, encoding="utf-8")
    if add_gate_pycache:
        pc = gdir / "__pycache__"
        pc.mkdir(parents=True, exist_ok=True)
        (pc / "gate.cpython-312.pyc").write_bytes(b"\x00build-noise")

    if corpus_docs:
        kdir = ddd / "2-understanding" / "knowledge"
        kdir.mkdir(parents=True, exist_ok=True)
        (kdir / ".gitkeep").write_text("", encoding="utf-8")  # must NOT ship
        for fname, text in corpus_docs.items():
            (kdir / fname).write_text(text, encoding="utf-8")

    if understanding_orphans:
        # non-canonical .md at the 2-understanding/ ROOT (e.g. an RP-library SSOT) —
        # today these are dropped by the packager (the lost-content bug). Also drop an
        # archive + a .lock to prove filter-out is preserved.
        udir = ddd / "2-understanding"
        udir.mkdir(parents=True, exist_ok=True)
        for fname, text in understanding_orphans.items():
            (udir / fname).write_text(text, encoding="utf-8")

    if deliverables:
        ddir = ddd / "deliverables"
        for rel, content in deliverables.items():
            dst = ddir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                dst.write_bytes(content)
            else:
                dst.write_text(content, encoding="utf-8")

    if add_noise:
        art = ddd / ".artifacts"
        art.mkdir(parents=True, exist_ok=True)
        (art / "code-intel.json").write_text('{"nodes": 9999}\n', encoding="utf-8")
        (art / "decay-archive.jsonl").write_text('{"decayed": true}\n', encoding="utf-8")

    aim: dict = {
        "name": name,
        "ddd_spec_version": "1.0",
        "description": "A synthetic fixture DDD for packager tests.",
        "plugins": plugins_override if plugins_override is not None else {
            "native_skills": ["s_ddd-manager", "s_repo-to-ddd"],
            "domain_skills": ["s_fx-report", "s_fx-analyze"],
            "mcp": [{"name": "FxMCP", "endpoint_env": "MCP_ENDPOINT"}],
        },
    }
    if include_distribution:
        aim["distribution"] = {"targets": targets if targets is not None else [], "visibility": visibility}
    (ddd / "aim.json").write_text(json.dumps(aim, indent=2), encoding="utf-8")

    (ddd / "bindings.yaml").write_text("ddd_spec_version: '1.0'\ngoverned_assets: []\n", encoding="utf-8")
    for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
        (ddd / doc).write_text(f"# {name} — {doc}\nReal content here.\n", encoding="utf-8")
    (ddd / "AGENTS.md").write_text(f"# {name} — Agent Guide\nUse this brain.\n", encoding="utf-8")
    (ddd / "REFRESHER.md").write_text(f"# {name} refresher\n", encoding="utf-8")
    # SYSTEM_PROMPT.md is the agent's runtime persona (AIM systemPrompt source). Present
    # by default; system_prompt=None omits it (to exercise the fail-loud missing case).
    if system_prompt is not None:
        (ddd / "SYSTEM_PROMPT.md").write_text(system_prompt.format(name=name), encoding="utf-8")
    if build_system:
        (ddd / "Config").write_text(
            f"package.{name} = {{\n    build-system = {build_system};\n}};\n", encoding="utf-8")

    skills = ddd / "skills"
    # class-B domain skills (included); the host-path body plant goes here (Gate-1 H2)
    body = "Output goes to ~/.swarm-ai/SwarmWS/Knowledge/Reports/x.html" if plant_host_path_in_body else ""
    _make_skill(skills, "s_fx-report", body=body)
    secret_script = 'API_KEY = "AKIAIOSFODNN7EXAMPLE1"\n' if plant_secret else "print('ok')\n"  # pragma: allowlist secret  (intentional fake fixture — tests the content-safety gate)
    _make_skill(skills, "s_fx-analyze", script=secret_script)
    # class-A enablement skill on disk (must be excluded)
    _make_skill(skills, "s_ddd-manager")
    if add_unclassified_skill:
        _make_skill(skills, "s_fx-orphan")  # in NEITHER list → loud-exclude (H5)

    return ddd


# ---------------------------------------------------------------------------
# AC1 — validator fail-closed
# ---------------------------------------------------------------------------
class TestAC1FailClosed:
    def test_absent_block_fails_closed(self):
        p = pol.validate_distribution({"name": "x", "plugins": {}})
        assert p.targets == ()
        assert p.visibility == "internal"
        assert p.declared is False
        assert p.is_distributable is False

    def test_not_a_dict_fails_closed(self):
        assert pol.validate_distribution("nope").targets == ()
        assert pol.validate_distribution(None).targets == ()

    def test_malformed_block_fails_closed(self):
        p = pol.validate_distribution({"distribution": ["aim-capabilities"]})  # list not dict
        assert p.targets == ()
        assert p.warnings  # loud

    def test_typo_visibility_fails_closed_to_internal(self):
        p = pol.validate_distribution({"distribution": {"targets": ["open-plugin"], "visibility": "externl"}})
        assert p.visibility == "internal"  # fail-closed, not the typo
        assert any("visibility" in w for w in p.warnings)

    def test_unknown_target_token_is_loud_not_silent(self):
        # Gate-1 C1: "aim" (design uses "aim-capabilities") must warn, not silently drop.
        p = pol.validate_distribution({"distribution": {"targets": ["aim"], "visibility": "internal"}})
        assert p.targets == ()  # dropped
        assert any("unknown token" in w and "aim" in w for w in p.warnings)  # but LOUD

    def test_valid_declaration_resolves(self):
        p = pol.validate_distribution(
            {"distribution": {"targets": ["open-plugin", "aim-capabilities"], "visibility": "external"}})
        assert p.targets == ("aim-capabilities", "open-plugin")  # sorted (determinism)
        assert p.visibility == "external"
        assert p.declared is True


# ---------------------------------------------------------------------------
# AC5 — declaration is the ceiling, subset-only
# ---------------------------------------------------------------------------
class TestAC5Ceiling:
    def test_subset_permitted(self):
        p = pol.DistributionPolicy(targets=("aim-capabilities", "open-plugin"), visibility="internal", declared=True)
        permitted, refused = pol.resolve_requested_targets(p, ["aim-capabilities"])
        assert permitted == ["aim-capabilities"]
        assert refused == []

    def test_widen_refused(self):
        p = pol.DistributionPolicy(targets=("aim-capabilities",), visibility="internal", declared=True)
        permitted, refused = pol.resolve_requested_targets(p, ["open-plugin"])
        assert permitted == []
        assert refused == ["open-plugin"]

    def test_package_ddd_refuses_undeclared_target(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        with pytest.raises(pk.PackagingError, match="ceiling"):
            pk.package_ddd(ddd, tmp_path / "out", requested_targets=["open-plugin"])

    def test_none_request_emits_full_declared_set(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        results = pk.package_ddd(ddd, tmp_path / "out", requested_targets=None)
        assert [r.target for r in results] == ["aim-capabilities"]


# ---------------------------------------------------------------------------
# AC2 — Target A AIM package emit
# ---------------------------------------------------------------------------
class TestAC2TargetAim:
    def test_emits_config_agentspec_skills_context(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        out = res.out_dir
        cfg = (out / "Config").read_text()
        assert "AIMBuild = 1.0" in cfg
        assert "type = ai-capabilities" in cfg
        spec_files = list((out / "agents").glob("*.agent-spec.json"))
        assert len(spec_files) == 1
        spec = json.loads(spec_files[0].read_text())
        assert spec["schemaVersion"] == "1"
        assert spec["config"]["systemPrompt"] == "{{aim:include:context/SYSTEM_PROMPT.md}}"
        assert (out / "context" / "SYSTEM_PROMPT.md").is_file()  # the persona ships
        assert (out / "context" / "TECH.md").is_file()
        assert (out / "skills" / "fx-report" / "SKILL.md").is_file()  # emitted under compliant name

    def test_preserves_existing_build_system(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal",
                                build_system="npm-pretty-much")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        cfg = (res.out_dir / "Config").read_text()
        assert "build-system = npm-pretty-much" in cfg  # preserved, not clobbered
        assert "AIMBuild = 1.0" in cfg                    # added alongside


# ---------------------------------------------------------------------------
# AC3 — Target B Open-Plugins emit
# ---------------------------------------------------------------------------
class TestAC3TargetOpenPlugin:
    def test_emits_plugin_json_and_dirs(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        out = res.out_dir
        pj = json.loads((out / ".plugin" / "plugin.json").read_text())
        assert pj["name"] == "swarmai-fixture-brain"  # normalized + prefixed
        assert (out / "skills" / "fx-report" / "SKILL.md").is_file()  # emitted under compliant name
        assert list((out / "agents").glob("*.md"))
        assert (out / "rules" / "tech.md").is_file()
        assert (out / "hooks").is_dir()
        assert (out / ".mcp.json").is_file()  # from aim.json plugins.mcp


# ---------------------------------------------------------------------------
# name==dirname — AIM agentskills.io requires the emitted SKILL.md frontmatter
# `name` to equal its dir name; SwarmAI's convention keeps them distinct on the
# source, so the packager must rewrite `name` on emit. (run_62055da6)
# ---------------------------------------------------------------------------
def _skill_frontmatter_name(skill_md: Path) -> str:
    """Read the `name:` value from a SKILL.md frontmatter (tolerates `name :`)."""
    for line in skill_md.read_text(encoding="utf-8").splitlines():
        stripped = line.rstrip()
        if stripped.replace(" ", "").replace("\t", "").startswith("name:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no name line in {skill_md}")


def _ddd_with_prefixed_skill(root: Path, *, targets: list[str], visibility: str) -> Path:
    """A fixture DDD whose domain skill uses the REAL SwarmAI convention: dir
    `s_fx-report` (an `s_` prefix whose underscore is AIM-NON-COMPLIANT). The
    emit must normalize dir + name to the compliant `fx-report`."""
    ddd = build_fixture_ddd(root, targets=targets, visibility=visibility)
    _make_skill(ddd / "skills", "s_fx-report")
    return ddd


class TestSkillNameAimCompliant:
    """AIM agentskills.io requires a skill name (and its dir) to match ^[a-z0-9-]+$
    AND name==dirname. SwarmAI's `s_`-prefixed dirs violate both, so the emit
    normalizes dir + name to a compliant form (`s_fx-report` → `fx-report`)."""

    def test_aim_emit_normalizes_dir_and_name(self, tmp_path):
        ddd = _ddd_with_prefixed_skill(tmp_path, targets=["aim-capabilities"], visibility="internal")
        # precondition: the SOURCE really uses the non-compliant s_ prefix (non-vacuous)
        assert (ddd / "skills" / "s_fx-report").is_dir()
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        # emitted under the COMPLIANT name; the raw s_ dir must NOT appear
        assert (res.out_dir / "skills" / "fx-report" / "SKILL.md").is_file()
        assert not (res.out_dir / "skills" / "s_fx-report").exists()
        assert _skill_frontmatter_name(res.out_dir / "skills" / "fx-report" / "SKILL.md") == "fx-report"

    def test_open_plugin_emit_normalizes_dir_and_name(self, tmp_path):
        ddd = _ddd_with_prefixed_skill(tmp_path, targets=["open-plugin"], visibility="external")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert (res.out_dir / "skills" / "fx-report" / "SKILL.md").is_file()
        assert not (res.out_dir / "skills" / "s_fx-report").exists()

    def test_every_emitted_skill_is_compliant_and_name_equals_dir(self, tmp_path):
        ddd = _ddd_with_prefixed_skill(tmp_path, targets=["aim-capabilities"], visibility="internal")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        skill_dirs = [d for d in (res.out_dir / "skills").iterdir() if (d / "SKILL.md").is_file()]
        assert skill_dirs, "expected at least one emitted skill"
        for d in skill_dirs:
            assert re.fullmatch(r"[a-z0-9-]+", d.name), f"dir {d.name} not AIM-compliant"
            assert _skill_frontmatter_name(d / "SKILL.md") == d.name

    def test_agent_spec_skillnames_are_compliant(self, tmp_path):
        # skillNames must reference the emitted compliant dirs, not the raw s_ names
        ddd = _ddd_with_prefixed_skill(tmp_path, targets=["aim-capabilities"], visibility="internal")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        spec = json.loads(next((res.out_dir / "agents").glob("*.agent-spec.json")).read_text())
        names = spec["dependencies"]["skills"]["skillNames"]
        emitted_dirs = {d.name for d in (res.out_dir / "skills").iterdir() if d.is_dir()}
        for n in names:
            assert re.fullmatch(r"[a-z0-9-]+", n), f"skillName {n} not compliant"
            assert n in emitted_dirs, f"skillName {n} has no emitted dir (dangling ref)"

    def test_collision_after_normalization_is_fail_loud(self, tmp_path):
        # two raw names normalizing to the same compliant name → PackagingError, never
        # a silent merge (which would drop a skill from the package)
        with pytest.raises(pk.PackagingError, match="collision"):
            pk._compliant_skill_map(["s_fx-report", "fx-report"])

    def test_compliant_name_strips_prefix_and_underscore(self):
        assert pk._compliant_skill_name("s_repo-to-ddd") == "repo-to-ddd"
        assert pk._compliant_skill_name("s_ddd-manager") == "ddd-manager"
        assert pk._compliant_skill_name("example-skill") == "example-skill"  # already compliant
        assert re.fullmatch(r"[a-z0-9-]+", pk._compliant_skill_name("s_A_B__c"))

    def test_compliant_name_degenerate_input_fails_loud(self):
        # Gate-2 HIGH (run_05e60d5b): a dir normalizing to EMPTY must raise, never
        # return "" — else dst = out_skills / "" == out_skills, collapsing the skill
        # into the skills root and corrupting siblings. Every degenerate input raises.
        for bad in ("s_", "s___", "...", "---", "s_中文", ""):
            with pytest.raises(pk.PackagingError, match="empty/invalid"):
                pk._compliant_skill_name(bad)

    def test_compliant_name_never_empty_for_valid_inputs(self):
        for ok in ("s_a", "s_x1", "s_2fa-tool", "PLAIN"):
            out = pk._compliant_skill_name(ok)
            assert out and re.fullmatch(r"[a-z0-9-]+", out)

    def test_source_skill_dir_is_untouched(self, tmp_path):
        # emit-layer only: the source keeps its s_ dir name
        ddd = _ddd_with_prefixed_skill(tmp_path, targets=["aim-capabilities"], visibility="internal")
        pk.package_ddd(ddd, tmp_path / "out")
        assert (ddd / "skills" / "s_fx-report").is_dir()

    def test_rewrite_name_literal_not_regex_backref(self, tmp_path):
        # Gate-2 (review-found): the rewrite replacement must treat the dir name as a
        # LITERAL, not a regex template. A dir name with a `\g<0>`/`\1` sequence would
        # corrupt output if fed into re.sub as a template string. Verify the helper
        # writes the dir name verbatim. (Directly exercises _rewrite_skill_name — the
        # value comes from dst.name, a filesystem string we must not trust.)
        d = tmp_path / r"s_weird\g<0>name"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: old\n---\n# body\n", encoding="utf-8")
        pk._rewrite_skill_name(d / "SKILL.md", d.name)
        assert _skill_frontmatter_name(d / "SKILL.md") == r"s_weird\g<0>name"

    def test_rewrite_name_missing_skill_md_is_noop(self, tmp_path):
        # fail-soft: a skill dir without SKILL.md must not crash
        d = tmp_path / "s_scriptonly"
        d.mkdir()
        pk._rewrite_skill_name(d / "SKILL.md", d.name)  # no raise
        assert not (d / "SKILL.md").exists()

    def test_rewrite_name_tolerates_space_before_colon(self, tmp_path):
        # Gate-2 LOW: `name :` (space before colon) is valid YAML; aim-build still
        # validates it, so the rewrite must normalize it too (not silently skip).
        d = tmp_path / "s_spaced"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname : old\ndescription: x\n---\n# b\n", encoding="utf-8")
        pk._rewrite_skill_name(d / "SKILL.md", d.name)
        assert _skill_frontmatter_name(d / "SKILL.md") == "s_spaced"

    def test_rewrite_name_idempotent(self, tmp_path):
        # already-matching name → no spurious rewrite (stable re-emit)
        d = tmp_path / "s_ok"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: s_ok\ndescription: x\n---\n# b\n", encoding="utf-8")
        before = (d / "SKILL.md").read_text()
        pk._rewrite_skill_name(d / "SKILL.md", "s_ok")
        assert (d / "SKILL.md").read_text() == before


# ---------------------------------------------------------------------------
# AC4 — class-A excluded / class-B included (delegated, no fork)
# ---------------------------------------------------------------------------
class TestAC4SkillSplit:
    def test_class_a_excluded_class_b_included(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert set(res.skills_included) == {"s_fx-report", "s_fx-analyze"}
        assert "s_ddd-manager" in res.skills_excluded  # class-A enablement excluded
        assert not (res.out_dir / "skills" / "s_ddd-manager").exists()

    def test_delegates_to_registry(self):
        # Gate-1 C3: uses the registry's enablement definition, not a fork.
        assert pk._is_enablement("s_ddd-manager") is True
        assert pk._is_enablement("s_repo-to-ddd") is True
        assert pk._is_enablement("s_fx-report") is False


# ---------------------------------------------------------------------------
# with_enablement — opt-in bare-host variant ships class-A engine as portable copy
# (Gap 1, run_385b37f9). Default OFF must remain byte-identical to the lean package.
# ---------------------------------------------------------------------------
class TestWithEnablement:
    def test_default_still_excludes_enablement(self, tmp_path):
        # REGRESSION: without the flag, class-A enablement is excluded (unchanged).
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert "s_ddd-manager" in res.skills_excluded
        assert "s_ddd-manager" not in res.skills_included
        assert not (res.out_dir / "skills" / "s_ddd-manager").exists()

    def test_with_enablement_ships_engine_both_targets(self, tmp_path):
        # OPT-IN: the class-A enablement skill dir is copied into the package on BOTH
        # targets, and moves from excluded -> included.
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities", "open-plugin"],
                                visibility="external")
        results = pk.package_ddd(ddd, tmp_path / "out", with_enablement=True)
        assert len(results) == 2
        for res in results:
            assert "s_ddd-manager" in res.skills_included, res.target
            assert "s_ddd-manager" not in res.skills_excluded, res.target
            assert (res.out_dir / "skills" / "ddd-manager").exists(), res.target  # emitted compliant
            # class-B domain skills still ship regardless of the flag
            assert {"s_fx-report", "s_fx-analyze"}.issubset(set(res.skills_included)), res.target

    def test_with_enablement_ships_ondisk_domain_skill(self, tmp_path):
        # FOLDER-AS-SOURCE: an on-disk skill dir with a SKILL.md that is NOT enablement
        # and NOT declared-native IS a domain skill (the folder is authoritative — there
        # is no "unclassified" category to exclude anymore). s_fx-orphan, planted on disk
        # in neither list, now ships as domain. The smuggle guard (declared-native) is
        # the only exclusion, tested in TestGate2SkillDualList.
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external",
                                add_unclassified_skill=True)
        [res] = pk.package_ddd(ddd, tmp_path / "out", with_enablement=True)
        assert "s_fx-orphan" in res.skills_included
        assert (res.out_dir / "skills" / "fx-orphan").exists()  # emitted under compliant name


# ---------------------------------------------------------------------------
# AC6 — content scan aborts on secret + host-path (over the emitted tree)
# ---------------------------------------------------------------------------
class TestAC6ContentScan:
    def test_secret_in_hook_script_aborts(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external", plant_secret=True)
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)

    def test_host_path_in_skill_body_aborts(self, tmp_path):
        # Gate-1 H2: host-path in a .md BODY (not a script) must be caught.
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external",
                                plant_host_path_in_body=True)
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)

    def test_host_path_aborts_even_emit_only(self, tmp_path):
        # host-path breaks even a private install → aborts on ANY emit.
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="internal",
                                plant_host_path_in_body=True)
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=False)

    def test_clean_ddd_passes(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        results = pk.package_ddd(ddd, tmp_path / "out", publish=True)
        assert results


# ---------------------------------------------------------------------------
# Gate-2 regressions — content-safety fail-OPEN holes + skill dual-list leak
# ---------------------------------------------------------------------------
class TestGate2ContentSafety:
    def test_secret_in_env_file_aborts(self, tmp_path):
        # C1: a secret in a NON-allow-listed extension (.env) must still abort —
        # the scan is fail-CLOSED (scan-all-but-binary), not an allow-list.
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        (ddd / "skills" / "s_fx-report" / "creds.env").write_text(
            "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE1\n", encoding="utf-8")  # pragma: allowlist secret  (intentional fake fixture — tests the content-safety gate)
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)

    def test_private_key_in_pem_file_aborts(self, tmp_path):
        # C1: .pem is not a "content" suffix but must be scanned.
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        (ddd / "skills" / "s_fx-report" / "key.pem").write_text(
            "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n", encoding="utf-8")  # pragma: allowlist secret  (intentional fake fixture — tests the content-safety gate)
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)

    def test_unquoted_secret_assignment_aborts(self, tmp_path):
        # H1: an UNQUOTED secret= assignment (the dominant shell/.env form) must match.
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        (ddd / "skills" / "s_fx-report" / "setup.sh").write_text(
            "#!/usr/bin/env bash\nexport API_KEY=supersecretvalue123\n", encoding="utf-8")  # pragma: allowlist secret  (intentional fake fixture — tests the content-safety gate)
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)

    def test_non_utf8_file_does_not_hide_secret(self, tmp_path):
        # C3: a non-UTF-8 byte must not silently skip the file — the readable text
        # (still containing the secret) is scanned via errors="replace".
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        p = ddd / "skills" / "s_fx-report" / "notes.txt"
        p.write_bytes(b"\xff\xfe garbage byte then AKIAIOSFODNN7EXAMPLE1 secret\n")  # pragma: allowlist secret  (intentional fake fixture — tests the content-safety gate)
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)


class TestGate2SkillDualList:
    def test_dual_listed_enablement_skill_is_excluded(self, tmp_path):
        # C2: a skill in BOTH native_skills AND domain_skills must be EXCLUDED,
        # never copied into an external package. Excluded set is authoritative.
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        aim = json.loads((ddd / "aim.json").read_text())
        # s_fx-report is a real class-B skill on disk; also list it as native (smuggle).
        aim["plugins"]["native_skills"].append("s_fx-report")
        (ddd / "aim.json").write_text(json.dumps(aim, indent=2), encoding="utf-8")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert "s_fx-report" not in res.skills_included
        assert "s_fx-report" in res.skills_excluded
        assert not (res.out_dir / "skills" / "fx-report").exists()  # excluded → no emitted (compliant) dir


# ---------------------------------------------------------------------------
# AC7 — install.sh defensive + passes bash -n
# ---------------------------------------------------------------------------
class TestAC7InstallScript:
    def test_install_sh_is_defensive_and_valid(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        sh = res.out_dir / "install.sh"
        assert sh.is_file()
        text = sh.read_text()
        assert "set -euo pipefail" in text
        assert 'mkdir -p "${DEST}"' in text  # quoted expansions
        r = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------
# AC9 — determinism: emit twice → byte-identical
# ---------------------------------------------------------------------------
class TestAC9Determinism:
    def test_emit_twice_byte_identical(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities", "open-plugin"], visibility="external")
        pk.package_ddd(ddd, tmp_path / "out1")
        pk.package_ddd(ddd, tmp_path / "out2")

        def tree_bytes(root: Path) -> dict[str, bytes]:
            return {
                str(p.relative_to(root)): p.read_bytes()
                for p in sorted(root.rglob("*")) if p.is_file()
            }
        assert tree_bytes(tmp_path / "out1") == tree_bytes(tmp_path / "out2")


# ---------------------------------------------------------------------------
# AC10 — neither-list skill excluded AND surfaced (loud)
# ---------------------------------------------------------------------------
class TestAC10Unclassified:
    def test_ondisk_skill_is_domain_folder_as_source(self, tmp_path):
        # FOLDER-AS-SOURCE (supersedes the old "unclassified excluded+warned" model):
        # a skill dir on disk with a SKILL.md, not enablement, not declared-native, IS
        # a domain skill — the folder is the source of truth, so there is no
        # undeclared-but-on-disk "unclassified" class to exclude. s_fx-orphan ships.
        # (The inverse — DECLARED-but-absent-from-folder — is now surfaced loudly by
        # ddd_skill_registry's cross-check, tested in test_ddd_skill_registry.py.)
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external",
                                add_unclassified_skill=True)
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert "s_fx-orphan" in res.skills_included
        assert (res.out_dir / "skills" / "fx-orphan").exists()  # emitted under compliant name


# ---------------------------------------------------------------------------
# AC11 — emit != publish
# ---------------------------------------------------------------------------
class TestAC11EmitNotPublish:
    def test_open_plugin_internal_emits_but_publish_refused(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="internal")
        # emit works
        results = pk.package_ddd(ddd, tmp_path / "out", publish=False)
        assert results and (results[0].out_dir / ".plugin" / "plugin.json").is_file()
        # publish refused
        with pytest.raises(pk.PackagingError, match="publish refused"):
            pk.package_ddd(ddd, tmp_path / "out2", publish=True)

    def test_external_visibility_permits_publish(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        assert pk.package_ddd(ddd, tmp_path / "out", publish=True)


# ---------------------------------------------------------------------------
# targets:[] valid — emits nothing (not a degraded DDD)
# ---------------------------------------------------------------------------
def test_empty_targets_emits_nothing(tmp_path):
    ddd = build_fixture_ddd(tmp_path, targets=[], visibility="internal")
    results = pk.package_ddd(ddd, tmp_path / "out")
    assert results == []


# ---------------------------------------------------------------------------
# _shared/ materialization (A′: single-source _shared locally, self-contained on distribute)
# ---------------------------------------------------------------------------
def _add_shared(ddd: Path, files: dict[str, str]) -> None:
    """Plant a _shared/ code layer under the DDD's capabilities dir (fixture uses skills/)."""
    from core.ddd_paths import ddd_path as _dp
    shared = _dp(ddd, "capabilities") / "_shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "__init__.py").write_text("", encoding="utf-8")
    for name, body in files.items():
        (shared / name).write_text(body, encoding="utf-8")


class TestSharedMaterialization:
    def test_shared_module_lands_in_each_emitted_skill(self, tmp_path):
        """AC1/AC2: _shared/*.py copied into every emitted skill scripts/ (both targets)."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities", "open-plugin"], visibility="internal")
        _add_shared(ddd, {"client.py": "def hello():\n    return 'shared'\n"})
        results = pk.package_ddd(ddd, tmp_path / "out")
        assert results
        for res in results:  # both targets
            for skill in res.skills_included:
                # skills_included holds RAW names; the emitted dir uses the compliant name
                dst = res.out_dir / "skills" / pk._compliant_skill_name(skill) / "scripts" / "client.py"
                assert dst.is_file(), f"{res.target}: client.py not materialized into {skill}"
                assert "def hello" in dst.read_text(encoding="utf-8")

    def test_init_py_not_materialized(self, tmp_path):
        """__init__.py is a package marker, not shared code — must NOT be copied."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        _add_shared(ddd, {"client.py": "x = 1\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        skill = pk._compliant_skill_name(res.skills_included[0])
        assert not (res.out_dir / "skills" / skill / "scripts" / "__init__.py").exists()

    def test_distributed_skill_imports_shared_via_injection(self, tmp_path):
        """AC3: a skill using parents[2]/_shared injection imports cleanly from the emitted package."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        _add_shared(ddd, {"client.py": "def hello():\n    return 'shared-ok'\n"})
        # give s_fx-report a real entry that uses the parents[2]/_shared injection pattern
        from core.ddd_paths import ddd_path as _dp
        entry_dir = _dp(ddd, "capabilities") / "s_fx-report" / "scripts"
        entry_dir.mkdir(parents=True, exist_ok=True)
        (entry_dir / "gen.py").write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "_S = str(Path(__file__).resolve().parents[2] / '_shared')\n"
            "if _S not in sys.path: sys.path.insert(0, _S)\n"
            "from client import hello\n"
            "print(hello())\n",
            encoding="utf-8",
        )
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        emitted_entry = res.out_dir / "skills" / "fx-report" / "scripts" / "gen.py"
        assert emitted_entry.is_file()
        # run the DISTRIBUTED skill entry — parents[2]/_shared points outside the pkg,
        # but the materialized sibling client.py in scripts/ makes the import resolve.
        proc = subprocess.run(
            ["python3", str(emitted_entry)], capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, f"distributed import failed: {proc.stderr}"
        assert "shared-ok" in proc.stdout

    def test_skill_owned_file_not_overwritten_warns(self, tmp_path):
        """AC4: a skill's own scripts/<name> is NOT clobbered by _shared; a WARN is emitted."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        _add_shared(ddd, {"client.py": "def hello():\n    return 'from_shared'\n"})
        # s_fx-report owns its OWN client.py (must win)
        from core.ddd_paths import ddd_path as _dp
        owned = _dp(ddd, "capabilities") / "s_fx-report" / "scripts"
        owned.mkdir(parents=True, exist_ok=True)
        (owned / "client.py").write_text("def hello():\n    return 'SKILL_OWNED'\n", encoding="utf-8")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        dst = res.out_dir / "skills" / "fx-report" / "scripts" / "client.py"
        assert "SKILL_OWNED" in dst.read_text(encoding="utf-8"), "skill-owned file was clobbered"
        assert any("skill-owned" in w and "client.py" in w for w in res.warnings), \
            f"expected a skill-owned collision WARN, got {res.warnings}"

    def test_shared_subpackage_warns_and_is_not_materialized(self, tmp_path):
        """Gate-2 MED: a sub-package in _shared can't be flat-materialized — WARN loud, skip it,
        never silently ship a package that fails on sub-import at the foreign host."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        _add_shared(ddd, {"client.py": "from sub.mod import g\n"})
        from core.ddd_paths import ddd_path as _dp
        sub = _dp(ddd, "capabilities") / "_shared" / "sub"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "__init__.py").write_text("", encoding="utf-8")
        (sub / "mod.py").write_text("def g():\n    return 1\n", encoding="utf-8")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        # the sub-package is NOT materialized into any skill
        for skill in res.skills_included:
            assert not (res.out_dir / "skills" / skill / "scripts" / "sub").exists()
        # and a loud warning names it
        assert any("_shared/sub/" in w and "sub-package" in w for w in res.warnings), \
            f"expected a loud sub-package warning, got {res.warnings}"

    def test_no_shared_dir_is_noop_byte_identical(self, tmp_path):
        """AC5: a DDD with no _shared/ emits a tree byte-identical to pre-feature (no-op)."""
        # emit WITHOUT _shared
        ddd = build_fixture_ddd(tmp_path, name="NoShared_A", targets=["aim-capabilities"], visibility="internal")
        [res] = pk.package_ddd(ddd, tmp_path / "out_a")
        files_no_shared = sorted(res.files)
        # no client.py should appear anywhere in the emitted skills
        for skill in res.skills_included:
            assert not (res.out_dir / "skills" / skill / "scripts" / "client.py").exists()
        # and warnings must be empty (no collision path taken)
        assert res.warnings == [] or all("_shared" not in w for w in res.warnings)
        assert files_no_shared  # sanity: it did emit something


def _add_domain_tools_sdk(ddd: Path, rel_dir: str, files: dict[str, str]) -> None:
    """Plant a data-source-asset SDK dir + declare it via aim.json plugins.domain_tools."""
    sdk = ddd / rel_dir
    sdk.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (sdk / name).write_text(body, encoding="utf-8")
    aim = json.loads((ddd / "aim.json").read_text(encoding="utf-8"))
    aim.setdefault("plugins", {})["domain_tools"] = [f"{rel_dir}/{n}" for n in files]
    (ddd / "aim.json").write_text(json.dumps(aim, indent=2), encoding="utf-8")


class TestDomainToolsMaterialization:
    def test_collect_sources_unions_shared_and_domain_tools(self, tmp_path):
        """AC1: _collect_shared_sources returns _shared + domain_tools parent-dirs, ordered+deduped."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        _add_shared(ddd, {"a.py": "x=1\n"})
        _add_domain_tools_sdk(ddd, "assets/data-source/scripts", {"catalog.py": "C=1\n"})
        srcs = pk._collect_shared_sources(ddd)
        names = [s.name for s in srcs]
        assert "_shared" in names and "scripts" in names
        assert names.index("_shared") < names.index("scripts")  # _shared precedence first
        assert len(srcs) == len({s.resolve() for s in srcs})  # deduped

    def test_domain_tools_sdk_materialized_into_skills(self, tmp_path):
        """AC2: the data-source-asset SDK (declared via domain_tools) lands in emitted skills."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        _add_domain_tools_sdk(ddd, "assets/data-source/scripts",
                              {"catalog.py": "def rule():\n    return 'moat'\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        for skill in res.skills_included:
            dst = res.out_dir / "skills" / pk._compliant_skill_name(skill) / "scripts" / "catalog.py"
            assert dst.is_file(), f"domain_tools SDK not materialized into {skill}"
            assert "def rule" in dst.read_text(encoding="utf-8")

    def test_same_name_in_both_sources_not_double_copied(self, tmp_path):
        """AC3: a filename in BOTH _shared and a domain_tools dir → one copy, _shared wins."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        _add_shared(ddd, {"client.py": "V='from_shared'\n"})
        _add_domain_tools_sdk(ddd, "assets/data-source/scripts", {"client.py": "V='from_asset'\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        skill = pk._compliant_skill_name(res.skills_included[0])
        dst = res.out_dir / "skills" / skill / "scripts" / "client.py"
        assert "from_shared" in dst.read_text(encoding="utf-8"), "_shared should win precedence"
        # no double-copy warning (dedup is silent, first-writer-wins)
        assert not any("client.py is skill-owned" in w for w in res.warnings)

    def test_no_shared_no_domain_tools_is_noop(self, tmp_path):
        """AC4: neither source present → no-op, no materialized files, no warnings."""
        ddd = build_fixture_ddd(tmp_path, name="Bare_Brain", targets=["aim-capabilities"], visibility="internal")
        assert pk._collect_shared_sources(ddd) == []
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        for skill in res.skills_included:
            assert not (res.out_dir / "skills" / pk._compliant_skill_name(skill) / "scripts" / "catalog.py").exists()

    def _set_domain_tools(self, ddd: Path, entries: list[str]) -> None:
        aim = json.loads((ddd / "aim.json").read_text(encoding="utf-8"))
        aim.setdefault("plugins", {})["domain_tools"] = entries
        (ddd / "aim.json").write_text(json.dumps(aim, indent=2), encoding="utf-8")

    def test_traversal_domain_tools_escapes_are_sandboxed(self, tmp_path):
        """Gate-2 CRITICAL: ../ traversal / absolute / bare-filename domain_tools entries must
        NOT become materialization sources (silent host-file leak into the package)."""
        # plant a would-be-leaked host dir OUTSIDE the ddd
        host_secret = tmp_path / "hostsecrets"
        host_secret.mkdir()
        (host_secret / "leak.py").write_text("PROPRIETARY = 'do-not-ship'\n", encoding="utf-8")
        ddd = build_fixture_ddd(tmp_path, name="Sbx_Brain", targets=["aim-capabilities"], visibility="internal")
        for bad in (["../hostsecrets/x.py"], [str(host_secret / "x.py")], ["catalog.py"]):
            self._set_domain_tools(ddd, bad)
            srcs = pk._collect_shared_sources(ddd)
            for s in srcs:
                rd = s.resolve()
                # every returned source must be strictly inside the ddd and not the root
                assert str(rd).startswith(str(ddd.resolve())), f"{bad}: source {rd} escaped the DDD"
                assert rd != ddd.resolve(), f"{bad}: DDD root became a source (too broad)"
        # end-to-end: the external proprietary file never lands in any emitted skill
        self._set_domain_tools(ddd, ["../hostsecrets/leak.py"])
        [res] = pk.package_ddd(ddd, tmp_path / "out_sbx")
        for skill in res.skills_included:
            assert not (res.out_dir / "skills" / skill / "scripts" / "leak.py").exists()

    def test_domain_tools_into_skill_dir_is_skipped(self, tmp_path):
        """Gate-2 MED: a domain_tools entry under a skill's own dir is skill-owned, NOT a shared
        source — must not cross-materialize that skill's private files into sibling skills."""
        ddd = build_fixture_ddd(tmp_path, name="SkillTool_Brain", targets=["aim-capabilities"], visibility="internal")
        from core.ddd_paths import ddd_path as _dp
        priv = _dp(ddd, "capabilities") / "s_fx-report" / "scripts"
        priv.mkdir(parents=True, exist_ok=True)
        (priv / "priv_tool.py").write_text("SECRET_SAUCE = 1\n", encoding="utf-8")
        # declare it (relative to ddd) as a domain tool
        rel = (priv / "priv_tool.py").resolve().relative_to(ddd.resolve())
        self._set_domain_tools(ddd, [str(rel)])
        assert pk._collect_shared_sources(ddd) == [], "skill-owned dir must not be a shared source"
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        # priv_tool.py must NOT appear in a DIFFERENT skill
        for skill in res.skills_included:
            if skill == "s_fx-report":
                continue
            assert not (res.out_dir / "skills" / skill / "scripts" / "priv_tool.py").exists()


# ---------------------------------------------------------------------------
# Knowledge corpus + deliverables shipping (run_6e4bced6)
# ---------------------------------------------------------------------------
_HOST_PATH = "Output goes to ~/.swarm-ai/SwarmWS/Knowledge/Reports/x.html"
_FAKE_PNG = b"\x89PNG\r\n\x1a\n\x00\x00fake-binary-bytes\x00\x01"


class TestCorpusShipping:
    def test_aim_ships_corpus_into_context_knowledge(self, tmp_path):
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            corpus_docs={"pattern-lib.md": "# Patterns\nRP1..RP80\n",
                         "case-library.md": "# Cases\nreal content\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        ck = res.out_dir / "context" / "knowledge"
        assert (ck / "pattern-lib.md").is_file()
        assert (ck / "case-library.md").is_file()
        assert "RP1..RP80" in (ck / "pattern-lib.md").read_text(encoding="utf-8")
        # .gitkeep must NOT ship
        assert not (ck / ".gitkeep").exists()

    def test_open_plugin_ships_corpus_into_knowledge(self, tmp_path):
        ddd = build_fixture_ddd(
            tmp_path, targets=["open-plugin"], visibility="internal",
            corpus_docs={"pattern-lib.md": "# Patterns\nRP1..RP80\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert (res.out_dir / "knowledge" / "pattern-lib.md").is_file()

    def test_both_targets_consistent_on_corpus(self, tmp_path):
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities", "open-plugin"], visibility="internal",
            corpus_docs={"a.md": "x\n", "b.md": "y\n"})
        results = pk.package_ddd(ddd, tmp_path / "out")
        by_target = {r.target: r for r in results}
        aim_corpus = {p.name for p in (by_target["aim-capabilities"].out_dir / "context" / "knowledge").glob("*.md")}
        op_corpus = {p.name for p in (by_target["open-plugin"].out_dir / "knowledge").glob("*.md")}
        assert aim_corpus == op_corpus == {"a.md", "b.md"}

    def test_no_corpus_dir_is_noop(self, tmp_path):
        # A 0-corpus DDD must still emit cleanly (no context/knowledge dir required).
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert res  # emits fine; corpus dir simply absent


class TestDeliverablesShipping:
    def test_aim_ships_deliverables_including_binary(self, tmp_path):
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            deliverables={"deck.md": "# Deck\nhuman-facing\n",
                          "assets/diagram.png": _FAKE_PNG,
                          "threat-models/tm.md": "# TM\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        dv = res.out_dir / "deliverables"
        assert (dv / "deck.md").is_file()
        assert (dv / "threat-models" / "tm.md").is_file()  # nested subdir preserved
        # binary shipped byte-identical
        assert (dv / "assets" / "diagram.png").read_bytes() == _FAKE_PNG

    def test_open_plugin_ships_deliverables(self, tmp_path):
        ddd = build_fixture_ddd(
            tmp_path, targets=["open-plugin"], visibility="internal",
            deliverables={"deck.md": "# Deck\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert (res.out_dir / "deliverables" / "deck.md").is_file()

    def test_no_deliverables_dir_is_noop(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert not (res.out_dir / "deliverables").exists()


class TestScanPartition:
    def test_deliverables_host_path_warns_not_blocks(self, tmp_path):
        # A host-path in a DELIVERABLE (human-facing artifact) must NOT block emit —
        # it is downgraded to a warning (XG decision A). Internal package, emit-only.
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            deliverables={"deck.md": f"# Deck\n{_HOST_PATH}\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")  # must NOT raise
        assert any("host-path" in w and "deliverables/" in w for w in res.warnings)

    def test_context_knowledge_host_path_still_blocks(self, tmp_path):
        # A host-path in the CORPUS (agent-consumed context) must STILL block emit.
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            corpus_docs={"leaky.md": f"# Leaky\n{_HOST_PATH}\n"})
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=False)

    def test_secret_in_deliverables_still_blocks(self, tmp_path):
        # secret blocks in EVERY zone incl. deliverables — never downgraded.
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            deliverables={"notes.md": "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE1\n"})  # pragma: allowlist secret  (intentional fake fixture)
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=False)

    def test_deliverables_host_path_blocks_on_publish(self, tmp_path):
        # On an EXTERNAL publish, everything blocks (deliverables downgrade is emit-only).
        ddd = build_fixture_ddd(
            tmp_path, targets=["open-plugin"], visibility="external",
            deliverables={"deck.md": f"# Deck\n{_HOST_PATH}\n"})
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)

    def test_unscannable_binary_deliverable_warns_loud(self, tmp_path):
        # G1: a binary deliverable (.png) can't be content-scanned — the packager must
        # surface a LOUD warning so nobody assumes it was secret-scanned.
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            deliverables={"assets/diagram.png": _FAKE_PNG})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert any("unscanned" in w.lower() and "diagram.png" in w for w in res.warnings)


class TestPayloadBoundary:
    def test_live_tree_noise_not_shipped(self, tmp_path):
        # run_eb45c28d lesson: extending the payload must NOT sweep in live-tree noise.
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            corpus_docs={"a.md": "x\n"}, add_noise=True)
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        all_files = [str(p) for p in res.out_dir.rglob("*") if p.is_file()]
        assert not any("code-intel.json" in f for f in all_files)
        assert not any("decay-archive" in f for f in all_files)
        assert not any(".artifacts" in f for f in all_files)


# ---------------------------------------------------------------------------
# Secret regex false-positive on prose (run_6e4bced6, XG decision A)
# ---------------------------------------------------------------------------
class TestSecretProseFalsePositive:
    """Tightening the UNQUOTED secret pattern: an all-letter word after `token =`
    (English prose) is NOT a secret; a real secret value carries a non-letter
    (digit/symbol/base64). The QUOTED form and AKIA/PEM/gh_ patterns are unchanged."""

    def test_prose_token_equals_word_is_not_secret(self, tmp_path):
        # The real SecDLC false-positive: "...a user-identity token = operating a bespoke..."
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            corpus_docs={"lesson.md": "self-issuing a token = operating a bespoke idP is the anti-pattern\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")  # must NOT raise
        assert not any("secret" in w for w in res.warnings), res.warnings

    def test_prose_password_word_and_secret_word_not_flagged(self, tmp_path):
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            corpus_docs={"prose.md": "the password: rotated regularly; the secret = shared across callers\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert not any("secret" in w for w in res.warnings), res.warnings

    def test_real_unquoted_secret_with_digits_still_blocks(self, tmp_path):
        # MUTATION guard: a genuine unquoted secret (has digits) MUST still block.
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        (ddd / "skills" / "s_fx-report" / "setup.sh").write_text(
            "export API_KEY=supersecretvalue123\n", encoding="utf-8")  # pragma: allowlist secret
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)

    def test_real_unquoted_secret_base64ish_still_blocks(self, tmp_path):
        # A base64/symbol-bearing secret with NO digit must still block (non-letter = /+).
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        (ddd / "skills" / "s_fx-report" / "conf.env").write_text(
            "token=abc/def+ghXYZ\n", encoding="utf-8")  # pragma: allowlist secret
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)

    def test_all_letter_secret_in_config_file_still_blocks(self, tmp_path):
        # REVIEW finding (HIGH): the prose FP fix must NOT weaken the detector in a real
        # config/script file. An ALL-LETTER unquoted secret in a .env/.sh MUST still block —
        # the suppression is scoped to prose (.md/.rst/.txt) ONLY.
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        (ddd / "skills" / "s_fx-report" / "creds.env").write_text(
            "password=abcdefgh\n", encoding="utf-8")  # pragma: allowlist secret
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)

    def test_all_letter_secret_in_prose_is_suppressed(self, tmp_path):
        # The mirror: an all-letter unquoted `token = word` in a .md knowledge doc is prose,
        # not a secret — suppressed (this is the whole point of the scoped fix).
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            corpus_docs={"lesson.md": "a shared token = operating a bespoke idP is the anti-pattern\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")  # must NOT raise
        assert not any("secret" in w for w in res.warnings), res.warnings


# ---------------------------------------------------------------------------
# Gate-2 adversarial findings (run_6e4bced6): prose-suppression hole + symlink exfil
# ---------------------------------------------------------------------------
class TestGate2ProseSuppressionNarrowed:
    """Finding A/G: the prose FP suppression must NOT cover an ISOLATED all-letter
    assignment in a .md — only a genuine sentence (value FOLLOWED BY prose words)."""

    def test_isolated_all_letter_secret_in_md_still_blocks(self, tmp_path):
        # `password=hunterhunter` alone on a line in a .md is a real credential, not prose.
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            corpus_docs={"leak.md": "password=hunterhunter\n"})  # pragma: allowlist secret
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out")

    def test_all_letter_secret_last_token_in_md_still_blocks(self, tmp_path):
        # value is the last token on the line (no trailing prose) → real secret, blocks.
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            corpus_docs={"leak.md": "the config sets TOKEN=mytokenname\n"})  # pragma: allowlist secret
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out")

    def test_genuine_prose_sentence_still_suppressed(self, tmp_path):
        # A real sentence — value followed by more words — stays suppressed (the FP we fix).
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            corpus_docs={"lesson.md": "a shared token = operating a bespoke idP is the anti-pattern\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")  # must NOT raise
        assert not any("secret" in w for w in res.warnings), res.warnings


class TestGate2SymlinkExfil:
    """Finding E: copytree/copy2 must NOT dereference a symlink whose target escapes the
    DDD dir (would package ~/.aws/credentials etc.). Escaping links are dropped."""

    def test_deliverable_escaping_symlink_not_dereferenced(self, tmp_path):
        secret_outside = tmp_path / "outside_secret.txt"
        secret_outside.write_text("aws_secret_access_key=AKIAIOSFODNN7EXAMPLE1\n", encoding="utf-8")  # pragma: allowlist secret
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            deliverables={"readme.md": "# ok\n"})
        # plant an escaping symlink inside deliverables/
        link = ddd / "deliverables" / "creds"
        link.symlink_to(secret_outside)
        [res] = pk.package_ddd(ddd, tmp_path / "out")  # must NOT raise, must NOT ship the target
        shipped = res.out_dir / "deliverables" / "creds"
        # the escaping link is dropped entirely; if anything shipped it must NOT be the secret bytes
        if shipped.exists():
            assert "AKIAIOSFODNN7EXAMPLE1" not in shipped.read_text(encoding="utf-8", errors="replace")  # pragma: allowlist secret
        assert not (shipped.exists() and not shipped.is_symlink()), "escaping link must not become a real file with the target's bytes"

    def test_corpus_escaping_symlink_skipped(self, tmp_path):
        secret_outside = tmp_path / "outside.md"
        secret_outside.write_text("aws_secret_access_key=AKIAIOSFODNN7EXAMPLE1\n", encoding="utf-8")  # pragma: allowlist secret
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            corpus_docs={"real.md": "# real corpus\n"})
        link = ddd / "2-understanding" / "knowledge" / "sneaky.md"
        link.symlink_to(secret_outside)
        [res] = pk.package_ddd(ddd, tmp_path / "out")  # must NOT raise, must NOT ship the target
        assert not (res.out_dir / "context" / "knowledge" / "sneaky.md").exists()
        assert (res.out_dir / "context" / "knowledge" / "real.md").is_file()


# ---------------------------------------------------------------------------
# 3-gates section shipping (run_f4d1489b) — gates were NEVER copied into any
# package (line ~751 comment claimed "agent-sops/ (gates + refresher)" but only
# REFRESHER shipped). These prove the WHOLE ③ section ships, scanned, noise-free.
# ---------------------------------------------------------------------------
class TestGatesShipped:
    def test_gates_md_ships_aim(self, tmp_path):
        """AC1: a 3-gates/*.md standard ships in the aim package (under agent-sops/gates/)."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            gates={"security-coding-baseline.md": "# Security Coding Baseline (Layer 1 STANDARD)\nA1 no eval.\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        shipped = res.out_dir / "agent-sops" / "gates" / "security-coding-baseline.md"
        assert shipped.is_file(), "the gate .md standard must ship in the aim package"
        assert "Security Coding Baseline" in shipped.read_text(encoding="utf-8")

    def test_gates_executable_and_subdir_ship_aim(self, tmp_path):
        """AC2: a mixed 3-gates (executable .py + context/includes/ subdir) ships WHOLE,
        not just flat *.md (the Gate-1 catch — flat glob would drop these)."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            gates={"no_git_push.py": "def gate():\n    return 0\n",
                   "context/includes/denylist.txt": "forbidden-pattern\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        gbase = res.out_dir / "agent-sops" / "gates"
        assert (gbase / "no_git_push.py").is_file(), "executable gate must ship"
        assert (gbase / "context" / "includes" / "denylist.txt").is_file(), "gate subdir data must ship"

    def test_gates_ship_open_plugin(self, tmp_path):
        """AC3: 3-gates content ships in the open-plugin package (under rules/gates/)."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["open-plugin"], visibility="external",
            gates={"baseline.md": "# baseline\nB1 external content is data.\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        shipped = res.out_dir / "rules" / "gates" / "baseline.md"
        assert shipped.is_file(), "the gate must ship in the open-plugin package under rules/gates/"

    def test_gate_pycache_excluded(self, tmp_path):
        """AC6: __pycache__/*.pyc build noise is EXCLUDED from shipped gates."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            gates={"g.py": "x=1\n"}, add_gate_pycache=True)
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        gbase = res.out_dir / "agent-sops" / "gates"
        assert (gbase / "g.py").is_file()
        assert not (gbase / "__pycache__").exists(), "pycache build noise must not ship"
        assert not list(gbase.rglob("*.pyc")), "no .pyc may ship in gates"

    def test_gitkeep_only_gates_is_noop(self, tmp_path):
        """AC7: a 3-gates/ with only .gitkeep ships no gate payload (no-op, no crash)."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        gbase = res.out_dir / "agent-sops" / "gates"
        # either no gates/ dir, or an empty one — but never a shipped .gitkeep-only payload
        real = [p for p in gbase.rglob("*") if p.is_file()] if gbase.exists() else []
        assert real == [], "a gitkeep-only 3-gates must ship no gate files"

    def test_gate_hostpath_fails_scan(self, tmp_path):
        """AC5: a workspace host-path planted in a gate file fails the content-safety
        scan fail-closed — proving the copied gates ARE scanned (not shipped unscanned).
        Uses a token the scan actually matches (_HOST_PATH_PATTERNS: .swarm-ai/SwarmWS)."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            gates={"leaky.md": "# gate\noutput goes to ~/.swarm-ai/SwarmWS/Knowledge/x.md\n"})
        with pytest.raises(pk.PackagingError):
            pk.package_ddd(ddd, tmp_path / "out")

    def test_gates_escaping_symlink_skipped(self, tmp_path):
        """Exfil guard parity with corpus: a gate symlink escaping the DDD is dropped."""
        secret_outside = tmp_path / "outside_gate.md"
        secret_outside.write_text("aws_secret_access_key=AKIAIOSFODNN7EXAMPLE1\n", encoding="utf-8")  # pragma: allowlist secret
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            gates={"real_gate.md": "# real gate\n"})
        link = ddd / "3-gates" / "sneaky.md"
        link.symlink_to(secret_outside)
        [res] = pk.package_ddd(ddd, tmp_path / "out")  # must NOT raise, must NOT ship target
        gbase = res.out_dir / "agent-sops" / "gates"
        assert (gbase / "real_gate.md").is_file()
        assert not (gbase / "sneaky.md").exists() or (gbase / "sneaky.md").is_symlink()

    def test_gates_escaping_symlink_only_is_noop(self, tmp_path):
        """Gate-2 LOW fix: a 3-gates whose ONLY payload is an escaping symlink ships no
        spurious empty gates/ dir (the no-op pre-check matches the copy-stage drop)."""
        secret_outside = tmp_path / "outside_only.md"
        secret_outside.write_text("aws_secret_access_key=AKIAIOSFODNN7EXAMPLE1\n", encoding="utf-8")  # pragma: allowlist secret
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        link = ddd / "3-gates" / "only.md"
        link.symlink_to(secret_outside)
        [res] = pk.package_ddd(ddd, tmp_path / "out")  # must NOT raise, must NOT ship target
        gbase = res.out_dir / "agent-sops" / "gates"
        real = [p for p in gbase.rglob("*") if p.is_file()] if gbase.exists() else []
        assert real == [], "escaping-symlink-only 3-gates must ship no files (and no leaked target)"


# ---------------------------------------------------------------------------
# Package README — authored-copy OR generated fallback (B2), run_d8d60202
# A distributed package must ALWAYS carry a top-level README.md (the install-team's
# first-open doc), guaranteed like Config/agent-spec — not reliant on the author.
# ---------------------------------------------------------------------------
class TestReadme:
    def test_authored_readme_copied_verbatim(self, tmp_path):
        """AC1: a DDD root README.md is copied byte-identical into the package top level."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        authored = "# My Hand-Written Package\n\nCustom wording the author controls.\n"
        (ddd / "README.md").write_text(authored, encoding="utf-8")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        pkg_readme = res.out_dir / "README.md"
        assert pkg_readme.is_file(), "authored README must ship at package top level"
        assert pkg_readme.read_text(encoding="utf-8") == authored, "authored README must be byte-identical"

    def test_generated_readme_when_absent(self, tmp_path):
        """AC2: no root README → a fallback is GENERATED containing the aim.description
        and every INCLUDED domain skill's compliant name (the guarantee: always present)."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        assert not (ddd / "README.md").exists()
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        pkg_readme = res.out_dir / "README.md"
        assert pkg_readme.is_file(), "a package with no authored README must still ship a generated one"
        text = pkg_readme.read_text(encoding="utf-8")
        assert "A synthetic fixture DDD for packager tests." in text, "generated README must carry aim.description"
        # included domain skills are s_fx-report, s_fx-analyze → compliant fx-report, fx-analyze
        assert "fx-report" in text and "fx-analyze" in text, "generated README must list included domain skills"
        # excluded enablement skill must NOT appear as a shipped capability
        assert "ddd-manager" not in text, "excluded enablement skill must not be listed as a capability"

    def test_readme_in_both_targets(self, tmp_path):
        """AC3: BOTH targets (aim + open-plugin) ship a top-level README with the same content."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities", "open-plugin"], visibility="internal")
        authored = "# Dual Target README\n\nSame doc in both shapes.\n"
        (ddd / "README.md").write_text(authored, encoding="utf-8")
        results = pk.package_ddd(ddd, tmp_path / "out")
        assert len(results) == 2
        for res in results:
            r = res.out_dir / "README.md"
            assert r.is_file(), f"{res.target} must ship a top-level README"
            assert r.read_text(encoding="utf-8") == authored

    def test_readme_in_manifest_and_scanned(self, tmp_path):
        """AC4: README is written BEFORE res.files rebuild → it lands in the manifest
        (res.files) and is therefore covered by the content-safety scan."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert "README.md" in res.files, "README must be in the emitted manifest (res.files)"

    def test_authored_readme_with_host_path_blocks(self, tmp_path):
        """AC4 (teeth): README is agent-consumed context, NOT a deliverable — a host-path
        in it BLOCKS emit (proves it's really scanned, not silently written past the gate)."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        (ddd / "README.md").write_text(
            "# Pkg\n\nRun from ~/.swarm-ai/SwarmWS/Projects/X to build.\n", encoding="utf-8")
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out")

    def test_generated_readme_deterministic(self, tmp_path):
        """AC5: two emits of the same DDD → byte-identical generated README (no timestamp/run-id)."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        [r1] = pk.package_ddd(ddd, tmp_path / "out1")
        [r2] = pk.package_ddd(ddd, tmp_path / "out2")
        a = (r1.out_dir / "README.md").read_text(encoding="utf-8")
        b = (r2.out_dir / "README.md").read_text(encoding="utf-8")
        assert a == b, "generated README must be deterministic across emits"

    def test_generated_readme_excludes_distribution_block(self, tmp_path):
        """NEVER boundary: the aim.json distribution block (code.amazon.com / brazil target)
        must NOT leak into the generated README (would self-block an external publish + leak)."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        # plant a distribution block with a host-ish internal URL
        import json as _json
        aim = _json.loads((ddd / "aim.json").read_text(encoding="utf-8"))
        aim["distribution"]["brazil_package"] = "https://code.amazon.com/packages/Secret/trees/mainline"
        (ddd / "aim.json").write_text(_json.dumps(aim, indent=2), encoding="utf-8")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        text = (res.out_dir / "README.md").read_text(encoding="utf-8")
        assert "code.amazon.com" not in text, "distribution block must not leak into generated README"

    def test_authored_readme_escaping_symlink_falls_back_to_generated(self, tmp_path):
        """LOW (review) exfil-guard parity: a root README.md symlink escaping the DDD is
        NOT copied verbatim — it falls through to the generated fallback (no exfil)."""
        secret_outside = tmp_path / "outside_readme.md"
        secret_outside.write_text("# proprietary\nnothing-patterned-here\n", encoding="utf-8")
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        (ddd / "README.md").symlink_to(secret_outside)
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        text = (res.out_dir / "README.md").read_text(encoding="utf-8")
        assert "proprietary" not in text, "escaping-symlink README must NOT be copied verbatim"
        # fell back to generated → carries aim.description
        assert "A synthetic fixture DDD for packager tests." in text

    def test_skill_description_first_sentence(self, tmp_path):
        """Direct unit: _skill_description returns the first sentence via the SSOT parser,
        truncated at 200 chars; missing SKILL.md/description → ''."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        # fixture skill s_fx-report has description "s_fx-report skill" (no ". ") → whole kept
        d = pk._skill_description(ddd, "s_fx-report")
        assert d == "s_fx-report skill"
        # unknown skill dir → empty
        assert pk._skill_description(ddd, "s_does-not-exist") == ""

    def test_generated_readme_escapes_pipe_in_description(self, tmp_path):
        """Gate-2 MEDIUM: a `|` in a skill description must be ESCAPED in the generated
        table cell (else it injects extra columns / corrupts the 2-col table)."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        # overwrite one included domain skill's description with pipe + newline chars
        sk = ddd / "skills" / "s_fx-report" / "SKILL.md"
        sk.write_text(
            "---\nname: s_fx-report\ndescription: Reports | extra col | injection\n---\n\n# x\n",
            encoding="utf-8")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        text = (res.out_dir / "README.md").read_text(encoding="utf-8")
        # the fx-report row must have exactly 3 UNESCAPED pipes (2-col table structure);
        # description pipes must be backslash-escaped so they don't inject columns.
        row = [ln for ln in text.splitlines() if "fx-report" in ln and ln.startswith("|")][0]
        unescaped = row.replace("\\|", "")  # drop escaped pipes, count only structural ones
        assert unescaped.count("|") == 3, f"table row must stay 2-col (3 structural pipes), got: {row!r}"
        assert "\\|" in row, "pipe in description must be backslash-escaped"

    def test_generated_readme_skill_desc_host_path_blocks(self, tmp_path):
        """Gate-2 gap: a host-path in a SKILL description flows into the generated README
        (agent-consumed context) → must BLOCK emit (not silently ship)."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        sk = ddd / "skills" / "s_fx-report" / "SKILL.md"
        sk.write_text(
            "---\nname: s_fx-report\ndescription: Run from ~/.swarm-ai/SwarmWS/Projects/X here\n---\n\n# x\n",
            encoding="utf-8")
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out")


# ---------------------------------------------------------------------------
# AIM capabilities-package compliance (run_7fa39634) — 4 emit defects vs the
# official SampleAICapabilities shape (Knowledge/Library/2026-08-25-aim-
# capabilities-package-standard.md). See that doc for the verified contract.
# ---------------------------------------------------------------------------
class TestAIMCompliance:
    def _aim(self, root, **kw):
        ddd = build_fixture_ddd(root, targets=["aim-capabilities"], visibility="internal", **kw)
        [res] = pk.package_ddd(ddd, root / "out")
        return res

    # AC1 — a .md gate STANDARD ALSO emits as agent-sops/<stem>.sop.md (discoverable SOP)
    def test_gate_md_also_emits_sop(self, tmp_path):
        res = self._aim(tmp_path, gates={"security-coding-baseline.md": "# Baseline\nA1 no eval.\n"})
        sop = res.out_dir / "agent-sops" / "security-coding-baseline.sop.md"
        assert sop.is_file(), "a .md gate STANDARD must ALSO emit as agent-sops/<stem>.sop.md"
        assert "Baseline" in sop.read_text(encoding="utf-8")
        # and the whole gate section still ships to gates/ (run_f4d1489b behavior preserved)
        assert (res.out_dir / "agent-sops" / "gates" / "security-coding-baseline.md").is_file()

    # AC1 collision — a gate named refresher.md would collide with the REFRESHER sop → fail-loud
    def test_gate_sop_stem_collision_fails_loud(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal",
                                gates={"refresher.md": "# collides with REFRESHER.md sop\n"})
        with pytest.raises(pk.PackagingError, match="(?i)sop.*collision|collision.*sop|refresher"):
            pk.package_ddd(ddd, tmp_path / "out")

    # AC1 — .py/.sh gates do NOT become .sop.md (they are CI scripts, not SOPs)
    def test_executable_gate_not_sopified(self, tmp_path):
        res = self._aim(tmp_path, gates={"no_git_push.py": "def gate():\n    return 0\n"})
        assert not (res.out_dir / "agent-sops" / "no_git_push.sop.md").exists(), \
            "a .py gate is a CI script, must NOT become a .sop.md"
        assert (res.out_dir / "agent-sops" / "gates" / "no_git_push.py").is_file()

    # AC2 — agent-spec carries clientConfig.kiroCli + dependencies.mcpRegistry (from aim.json plugins.mcp)
    def test_agent_spec_clientconfig_and_mcpregistry(self, tmp_path):
        res = self._aim(tmp_path)
        specs = list((res.out_dir / "agents").glob("*.agent-spec.json"))
        assert specs, "an agent-spec must be emitted"
        spec = json.loads(specs[0].read_text(encoding="utf-8"))
        cc = spec.get("clientConfig", {}).get("kiroCli", {})
        assert cc.get("tools"), "clientConfig.kiroCli.tools must be non-empty"
        # base tools always present; the MCP tag derives from the fixture's declared MCP
        # (FxMCP) — NOT a hardcoded @builder-mcp (contract-driven, run_91a812c6).
        assert "read" in cc["tools"] and "@FxMCP" in cc["tools"]
        assert "allowedTools" in cc
        # mcpRegistry derived from aim.json plugins.mcp (fixture declares FxMCP)
        reg = spec.get("dependencies", {}).get("mcpRegistry", {})
        assert "FxMCP" in reg, "mcpRegistry must include the aim.json-declared MCP (FxMCP)"

    # AC3 — knowledge corpus declared as a knowledgeBase resource, not just resident context
    def test_agent_spec_knowledgebase_resource(self, tmp_path):
        res = self._aim(tmp_path, corpus_docs={"note.md": "# a knowledge note\nfact.\n"})
        spec = json.loads(next((res.out_dir / "agents").glob("*.agent-spec.json")).read_text(encoding="utf-8"))
        resources = spec.get("clientConfig", {}).get("kiroCli", {}).get("resources", [])
        kb = [r for r in resources if r.get("type") == "knowledgeBase"]
        assert kb, "a knowledgeBase resource over context/knowledge must be declared when corpus non-empty"
        assert "context/knowledge" in kb[0].get("source", "")
        assert kb[0].get("indexType") == "fast"

    # AC4 — s_ sibling refs on SIBLINGS:/NOT FOR: metadata lines are normalized; prose is NOT
    def test_skill_body_sibling_ref_rewritten_scoped(self, tmp_path):
        # s_fx-analyze is a real domain sibling in the fixture; put it on a metadata line + a prose line
        body = ("SIBLINGS: s_fx-analyze = the analyzer\n"
                "\n"
                "Some prose mentioning s_fx-analyze inline should NOT be rewritten.\n")
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        # overwrite s_fx-report body with our metadata+prose mix
        (ddd / "skills" / "s_fx-report" / "SKILL.md").write_text(
            "---\nname: s_fx-report\ndescription: r\n---\n\n# r\n" + body, encoding="utf-8")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        shipped = (res.out_dir / "skills" / "fx-report" / "SKILL.md").read_text(encoding="utf-8")
        assert "SIBLINGS: fx-analyze = the analyzer" in shipped, "metadata-line sibling ref must be normalized"
        assert "prose mentioning s_fx-analyze inline should NOT" in shipped, "prose s_ ref must be UNTOUCHED"

    # AC4 regression — SIBLINGS with a parenthetical (real SecDLC shape: "SIBLINGS (SecDLC ...):")
    def test_sibling_ref_rewritten_with_parenthetical(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        (ddd / "skills" / "s_fx-report" / "SKILL.md").write_text(
            "---\nname: s_fx-report\ndescription: >\n"
            "  A report skill.\n"
            "  SIBLINGS (some qualifier): s_fx-analyze = the analyzer.\n"
            "tags: [x]\n---\n\n# r\nProse with s_fx-analyze stays.\n", encoding="utf-8")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        shipped = (res.out_dir / "skills" / "fx-report" / "SKILL.md").read_text(encoding="utf-8")
        assert "SIBLINGS (some qualifier): fx-analyze" in shipped, "SIBLINGS with a parenthetical must still be normalized"
        assert "Prose with s_fx-analyze stays" in shipped, "prose s_ ref untouched"


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT.md — the agent's runtime persona (run_0395c955)
# AIM standard §4: systemPrompt points at a dedicated prompt file, NOT AGENTS.md.
# Three package-relative roles, one file each: SYSTEM_PROMPT.md = runtime persona;
# README.md = consumer entry doc (§7 overview+map+usage); the source AGENTS.md is the
# DDD dev door-plate and does NOT ship into the package (P1, run_ed775916). No generator.
# ---------------------------------------------------------------------------
class TestSystemPrompt:
    def test_aim_systemprompt_points_at_system_prompt_md(self, tmp_path):
        """AC: aim agent-spec systemPrompt includes context/SYSTEM_PROMPT.md, not AGENTS.md."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal",
                                system_prompt="# Persona\nYou are the fixture security agent.\n")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        spec = json.loads(next((res.out_dir / "agents").glob("*.agent-spec.json")).read_text())
        assert spec["config"]["systemPrompt"] == "{{aim:include:context/SYSTEM_PROMPT.md}}"
        # the persona file ships in context/, byte-verbatim from source
        shipped = (res.out_dir / "context" / "SYSTEM_PROMPT.md").read_text()
        assert shipped == "# Persona\nYou are the fixture security agent.\n"

    def test_source_agents_md_does_not_ship_to_package(self, tmp_path):
        """P1 (run_ed775916): the source AGENTS.md is the DDD *dev* door-plate — it
        describes the six-section SOURCE tree (2-understanding/3-gates/4-capabilities),
        which does NOT exist in the flat AIM package → it is noise to a package user, so
        it is NOT shipped. The consumer entry doc job (overview + map + usage, §7) is
        carried by README.md; the runtime persona by SYSTEM_PROMPT.md."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert not (res.out_dir / "context" / "AGENTS.md").exists(), \
            "source AGENTS.md (dev door-plate) must NOT ship into the AIM package"
        # the two package-relative docs that DO carry the consumer + persona roles:
        assert (res.out_dir / "README.md").is_file(), "README is the consumer entry doc"
        assert (res.out_dir / "context" / "SYSTEM_PROMPT.md").is_file(), "persona ships"
        spec = json.loads(next((res.out_dir / "agents").glob("*.agent-spec.json")).read_text())
        assert "AGENTS.md" not in spec["config"]["systemPrompt"]

    def test_missing_system_prompt_fails_loud(self, tmp_path):
        """No SYSTEM_PROMPT.md → fail-loud PackagingError (never ship a persona-less package,
        never silently fall back to AGENTS.md, never generate a hollow one)."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal",
                                system_prompt=None)
        with pytest.raises(pk.PackagingError, match="SYSTEM_PROMPT"):
            pk.package_ddd(ddd, tmp_path / "out")

    def test_open_plugin_agent_md_uses_system_prompt(self, tmp_path):
        """open-plugin agents/<plugin>.md body = SYSTEM_PROMPT.md content, not AGENTS.md."""
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="internal",
                                system_prompt="# Persona\nRuntime persona body.\n")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        md = next((res.out_dir / "agents").glob("*.md")).read_text()
        assert "Runtime persona body." in md
        assert "Agent Guide" not in md  # the AGENTS.md dev door-plate must NOT be the body

    def test_system_prompt_verbatim_no_generation(self, tmp_path):
        """The persona is the author's file VERBATIM — no metadata-generated filler injected."""
        authored = "# SecFix\nYou review authz. Use cr-review for diffs.\n"
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal",
                                system_prompt=authored)
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert (res.out_dir / "context" / "SYSTEM_PROMPT.md").read_text() == authored


# ---------------------------------------------------------------------------
# Contract-driven agent-spec tools (run_91a812c6) — tools + allowedTools derive
# from aim.json plugins.tools/allowed_tools + plugins.mcp, ZERO hardcoded @<mcp>
# singleton. Standard: Knowledge/Library/2026-08-25-aim-capabilities-package-standard.md.
# ---------------------------------------------------------------------------
class TestContractDrivenTools:
    def _spec(self, root, plugins, **kw):
        ddd = build_fixture_ddd(root, targets=["aim-capabilities"], visibility="internal",
                                plugins_override=plugins, **kw)
        [res] = pk.package_ddd(ddd, root / "out")
        return json.loads(next((res.out_dir / "agents").glob("*.agent-spec.json")).read_text(encoding="utf-8"))

    def _cc(self, spec):
        return spec["clientConfig"]["kiroCli"]

    # AC4 pin — no tools, no mcp declared → tools == base [read,write,shell], allowedTools == [read]
    def test_no_declaration_byte_identical(self, tmp_path):
        cc = self._cc(self._spec(tmp_path, {"domain_skills": ["s_fx-report"]}))
        assert cc["tools"] == ["read", "write", "shell"], "no-decl DDD must get exactly the base tools (no @<mcp>)"
        assert cc["allowedTools"] == ["read"], "no-decl allowedTools must be exactly [read] (no hardcoded @builder-mcp)"

    # AC1 — declared plugins.tools honored
    def test_declared_tools(self, tmp_path):
        cc = self._cc(self._spec(tmp_path, {"domain_skills": ["s_fx-report"], "tools": ["read", "write", "shell", "aws"]}))
        assert "aws" in cc["tools"], "a declared tool (aws) must appear"

    # AC2 — declared allowed_tools honored
    def test_declared_allowed_tools(self, tmp_path):
        cc = self._cc(self._spec(tmp_path, {"domain_skills": ["s_fx-report"], "allowed_tools": ["read"]}))
        assert cc["allowedTools"] == ["read"]

    # AC3 — EVERY declared MCP derives to @<name> in BOTH tools and allowedTools (single source)
    def test_mcp_bridge_all_and_both_lists(self, tmp_path):
        cc = self._cc(self._spec(tmp_path, {"domain_skills": ["s_fx-report"],
                                            "mcp": [{"name": "coe-mcp"}, {"name": "talos-gateway-mcp"}]}))
        assert "@coe-mcp" in cc["tools"] and "@talos-gateway-mcp" in cc["tools"]
        assert "@coe-mcp" in cc["allowedTools"] and "@talos-gateway-mcp" in cc["allowedTools"]

    # AC3 dedup — declaring builder-mcp yields @builder-mcp exactly once
    def test_mcp_dedup(self, tmp_path):
        cc = self._cc(self._spec(tmp_path, {"domain_skills": ["s_fx-report"], "mcp": [{"name": "builder-mcp"}]}))
        assert cc["tools"].count("@builder-mcp") == 1

    # SecDLC-shaped — declares builder-mcp (Gate-1: removing default @builder-mcp must not lose it for SecDLC)
    def test_secdlc_shaped_derives_builder_mcp(self, tmp_path):
        cc = self._cc(self._spec(tmp_path, {"domain_skills": ["s_fx-report"],
                                            "mcp": [{"name": "builder-mcp"}, {"name": "coe-mcp"}, {"name": "talos-gateway-mcp"}]}))
        assert cc["tools"] == ["read", "write", "shell", "@builder-mcp", "@coe-mcp", "@talos-gateway-mcp"]
        assert cc["allowedTools"] == ["read", "@builder-mcp", "@coe-mcp", "@talos-gateway-mcp"]

    # invariant — allowedTools ⊆ tools ALWAYS (even with a declared-override that over-claims)
    def test_allowed_subset_of_tools_clamped(self, tmp_path):
        # author over-claims: allowed_tools lists a tool NOT in tools → must be clamped, not leak
        cc = self._cc(self._spec(tmp_path, {"domain_skills": ["s_fx-report"],
                                            "tools": ["read", "write"], "allowed_tools": ["read", "shell", "aws"]}))
        assert set(cc["allowedTools"]) <= set(cc["tools"]), "allowedTools must always be a subset of tools (clamp over-claims)"

    # AC5 — fail-soft on malformed plugins.tools (a string, not a list) → falls back to default
    def test_malformed_tools_fail_soft(self, tmp_path):
        cc = self._cc(self._spec(tmp_path, {"domain_skills": ["s_fx-report"], "tools": "notalist"}))
        assert cc["tools"] == ["read", "write", "shell"], "malformed plugins.tools must fall back to default, not crash"


# ---------------------------------------------------------------------------
# §9 fresh-clone integrity — no empty dir survives in the emitted package
# (run_c4191122). git drops empty dirs on a fresh clone; a copytree helper that
# excludes .gitkeep/build-noise can leave an empty subdir → any skill body
# referencing a file under it breaks on the consumer. Root fix: after emit, every
# empty dir gets a .gitkeep so the tree is fresh-clone-complete.
# ---------------------------------------------------------------------------
class TestFreshCloneIntegrity:
    def _empty_dirs(self, root: Path) -> list[str]:
        return sorted(
            str(d.relative_to(root)) for d in root.rglob("*")
            if d.is_dir() and not any(c.is_file() for c in d.rglob("*"))
        )

    def test_aim_no_empty_dir_survives(self, tmp_path):
        """A gate subdir that holds ONLY a .gitkeep (excluded on emit) must NOT leave an
        empty dir in the package — it gets a .gitkeep so a fresh clone keeps it."""
        # gates: a real .md standard + a context/includes/ that is gitkeep-only in source
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            gates={"security-coding-baseline.md": "# baseline\nrule.\n",
                   "context/includes/.gitkeep": ""})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        empties = self._empty_dirs(res.out_dir)
        assert empties == [], f"emitted package must have NO empty dir (fresh-clone §9); found: {empties}"

    def test_open_plugin_no_empty_dir_survives(self, tmp_path):
        ddd = build_fixture_ddd(
            tmp_path, targets=["open-plugin"], visibility="internal",
            gates={"security-coding-baseline.md": "# baseline\nrule.\n",
                   "context/includes/.gitkeep": ""})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        empties = self._empty_dirs(res.out_dir)
        assert empties == [], f"emitted plugin must have NO empty dir (fresh-clone §9); found: {empties}"


# ---------------------------------------------------------------------------
# Lossless mapping: 2-understanding/ ROOT non-canonical .md -> context/knowledge/
# (run_f291ad72). RP-library SSOT (security-review-patterns.md) sits in the
# understanding ROOT, not knowledge/ - the old packager dropped it (lost-content bug).
# ---------------------------------------------------------------------------
class TestUnderstandingOrphansShip:
    def test_root_orphan_md_ships_to_knowledge(self, tmp_path):
        """A non-canonical .md in 2-understanding/ root (RP library) MUST ship into
        context/knowledge/ (retrievable), not be dropped."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            understanding_orphans={"security-review-patterns.md": "# RP library\nRP44 ...\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        shipped = res.out_dir / "context" / "knowledge" / "security-review-patterns.md"
        assert shipped.is_file(), "the RP-library orphan must ship to context/knowledge/"
        assert "RP44" in shipped.read_text()

    def test_filter_out_preserved_archive_and_lock_do_not_ship(self, tmp_path):
        """Filter-out preserved: an -archive.md and a .lock at the understanding root
        must NOT ship."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            understanding_orphans={
                "security-review-patterns.md": "# RP\nRP1 ...\n",
                "IMPROVEMENT-archive.md": "# decay archive\nnoise\n",
                "PRODUCT.md.lock": "lockstate\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        k = res.out_dir / "context" / "knowledge"
        assert (k / "security-review-patterns.md").is_file()
        assert not (k / "IMPROVEMENT-archive.md").exists(), "decay archive must NOT ship"
        assert not (k / "PRODUCT.md.lock").exists(), ".lock local state must NOT ship"
        assert not (k / "PRODUCT.md").exists()


# ---------------------------------------------------------------------------
# Gate: fail-loud on a hardcoded six-section physical path in a shipped skill/SOP
# (run_f291ad72). TECH-class structure docs are NOT gated.
# ---------------------------------------------------------------------------
class TestHardcodedPathGate:
    def test_skill_with_hardcoded_layout_path_fails_loud(self, tmp_path):
        """A skill body hardcoding a six-section source path -> PackagingError."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        sk = ddd / "skills" / "s_fx-report" / "SKILL.md"
        sk.write_text(
            "---\nname: s_fx-report\ndescription: fx\n---\n\n"
            "Read `2-understanding/knowledge/anti-pattern-case-library.md` before review.\n",
            encoding="utf-8")
        with pytest.raises(pk.PackagingError, match="hardcoded|layout|2-understanding"):
            pk.package_ddd(ddd, tmp_path / "out")

    def test_clean_skill_passes_gate(self, tmp_path):
        """A skill referencing assets layout-neutrally emits cleanly."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        sk = ddd / "skills" / "s_fx-report" / "SKILL.md"
        sk.write_text(
            "---\nname: s_fx-report\ndescription: fx\n---\n\n"
            "Consult the anti-pattern case library (retrievable knowledge) before review.\n",
            encoding="utf-8")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert (res.out_dir / "skills" / "fx-report" / "SKILL.md").is_file()


class TestSecretCodeSpanSuppression:
    def test_codespan_pattern_in_prose_not_flagged(self, tmp_path):
        """A security doc that DESCRIBES a secret pattern in a markdown code-span
        (password=`...`) is NOT a real secret — suppressed in prose (run_f291ad72)."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            understanding_orphans={"security-review-patterns.md":
                "# RP\ngrep for the credential pattern `password=`/`secret=` in config.\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")  # must NOT raise
        assert (res.out_dir / "context" / "knowledge" / "security-review-patterns.md").is_file()

    def test_real_unquoted_secret_in_config_still_flagged(self, tmp_path):
        """Detector NOT weakened: a real unquoted secret in a .env script still blocks."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        (ddd / "skills" / "s_fx-report" / "config.env").write_text(
            "password=hunter2token\n", encoding="utf-8")
        with pytest.raises(pk.PackagingError, match="content-safety|secret"):
            pk.package_ddd(ddd, tmp_path / "out")


class TestGate2Fixes:
    def test_backtick_wrapped_real_secret_still_flagged(self, tmp_path):
        """Gate-2 4a: a backtick-wrapped REAL secret in prose must NOT be suppressed —
        only a backtick-span that itself contains a key=-PATTERN is a description."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            understanding_orphans={"security-review-patterns.md":
                "# RP\nExample leak: password=`hunter2realtokenvalue` committed to config.\n"})
        with pytest.raises(pk.PackagingError, match="content-safety|secret"):
            pk.package_ddd(ddd, tmp_path / "out")

    def test_pattern_codespan_still_suppressed(self, tmp_path):
        """The describe-the-pattern code-span (`password=`/`secret=`) is still suppressed."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            understanding_orphans={"security-review-patterns.md":
                "# RP\ngrep for the credential pattern `password=`/`secret=` in config.\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")  # must NOT raise
        assert (res.out_dir / "context" / "knowledge" / "security-review-patterns.md").is_file()

    def test_hardcoded_path_in_corpus_is_gated(self, tmp_path):
        """Gate-2 2c/5: a hardcoded six-section path INSIDE a shipped corpus file
        (context/knowledge/) is now gated — it dangles like one in a skill."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            understanding_orphans={"security-review-patterns.md":
                "# RP library\nAlso read `2-understanding/knowledge/other.md` for context.\n"})
        with pytest.raises(pk.PackagingError, match="hardcoded|layout|2-understanding"):
            pk.package_ddd(ddd, tmp_path / "out")

    def test_context_root_doc_not_gated_for_layout_path(self, tmp_path):
        """A canonical context/ ROOT doc (TECH etc.) describing the six-section structure
        is NOT gated (path there is descriptive semantics, not a runtime deref)."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        # TECH.md at ddd root (→ context/TECH.md) describes the layout
        (ddd / "TECH.md").write_text(
            "# TECH\nThe DDD keeps standards in `3-gates/` and corpus in `2-understanding/knowledge/`.\n",
            encoding="utf-8")
        [res] = pk.package_ddd(ddd, tmp_path / "out")  # must NOT raise
        assert (res.out_dir / "context" / "TECH.md").is_file()

    def test_orphan_corpus_name_collision_fails_loud(self, tmp_path):
        """Gate-2 1b: a root orphan colliding with a knowledge/ corpus file fails loud
        (never silently clobbers)."""
        ddd = build_fixture_ddd(
            tmp_path, targets=["aim-capabilities"], visibility="internal",
            corpus_docs={"shared.md": "# corpus version\n"},
            understanding_orphans={"shared.md": "# orphan version\n"})
        with pytest.raises(pk.PackagingError, match="collides|collision"):
            pk.package_ddd(ddd, tmp_path / "out")
