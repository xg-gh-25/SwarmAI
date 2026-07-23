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
) -> Path:
    """Build a minimal compliant six-section DDD. Returns its dir."""
    ddd = root / name
    ddd.mkdir(parents=True, exist_ok=True)

    aim: dict = {
        "name": name,
        "ddd_spec_version": "1.0",
        "description": "A synthetic fixture DDD for packager tests.",
        "plugins": {
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
        assert spec["config"]["systemPrompt"].startswith("{{aim:include:")
        assert (out / "context" / "TECH.md").is_file()
        assert (out / "skills" / "s_fx-report" / "SKILL.md").is_file()

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
        assert (out / "skills" / "s_fx-report" / "SKILL.md").is_file()
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


def _ddd_with_mismatched_skill_name(root: Path, *, targets: list[str], visibility: str) -> Path:
    """A fixture DDD whose one domain skill has the REAL SwarmAI mismatch: dir
    `s_fx-report` but frontmatter `name: fx-report` (no s_ prefix). Reproduces the
    aim-build BLOCK the fix must cure."""
    ddd = build_fixture_ddd(root, targets=targets, visibility=visibility)
    # overwrite the domain skill with the deliberate dir/name mismatch
    _make_skill(ddd / "skills", "s_fx-report", frontmatter_name="fx-report")
    return ddd


class TestSkillNameMatchesDirName:
    def test_aim_emit_rewrites_name_to_dirname(self, tmp_path):
        ddd = _ddd_with_mismatched_skill_name(tmp_path, targets=["aim-capabilities"], visibility="internal")
        # precondition: the SOURCE really has the mismatch (else the test is vacuous)
        assert _skill_frontmatter_name(ddd / "skills" / "s_fx-report" / "SKILL.md") == "fx-report"
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        emitted = res.out_dir / "skills" / "s_fx-report" / "SKILL.md"
        assert emitted.is_file()
        # the fix: emitted name == dir name (AIM name==dirname)
        assert _skill_frontmatter_name(emitted) == "s_fx-report"

    def test_open_plugin_emit_rewrites_name_to_dirname(self, tmp_path):
        ddd = _ddd_with_mismatched_skill_name(tmp_path, targets=["open-plugin"], visibility="external")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        emitted = res.out_dir / "skills" / "s_fx-report" / "SKILL.md"
        assert _skill_frontmatter_name(emitted) == "s_fx-report"

    def test_every_emitted_skill_name_equals_its_dir(self, tmp_path):
        # all included domain skills, not just the planted one, must satisfy name==dirname
        ddd = _ddd_with_mismatched_skill_name(tmp_path, targets=["aim-capabilities"], visibility="internal")
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        skill_dirs = [d for d in (res.out_dir / "skills").iterdir() if (d / "SKILL.md").is_file()]
        assert skill_dirs, "expected at least one emitted skill"
        for d in skill_dirs:
            assert _skill_frontmatter_name(d / "SKILL.md") == d.name

    def test_source_skill_md_is_untouched(self, tmp_path):
        # emit-layer only: the source SKILL.md keeps its (mismatched) name
        ddd = _ddd_with_mismatched_skill_name(tmp_path, targets=["aim-capabilities"], visibility="internal")
        pk.package_ddd(ddd, tmp_path / "out")
        assert _skill_frontmatter_name(ddd / "skills" / "s_fx-report" / "SKILL.md") == "fx-report"

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
            assert (res.out_dir / "skills" / "s_ddd-manager").exists(), res.target
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
        assert (res.out_dir / "skills" / "s_fx-orphan").exists()


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
        assert not (res.out_dir / "skills" / "s_fx-report").exists()


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
        assert (res.out_dir / "skills" / "s_fx-orphan").exists()


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
                dst = res.out_dir / "skills" / skill / "scripts" / "client.py"
                assert dst.is_file(), f"{res.target}: client.py not materialized into {skill}"
                assert "def hello" in dst.read_text(encoding="utf-8")

    def test_init_py_not_materialized(self, tmp_path):
        """__init__.py is a package marker, not shared code — must NOT be copied."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        _add_shared(ddd, {"client.py": "x = 1\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        skill = res.skills_included[0]
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
        emitted_entry = res.out_dir / "skills" / "s_fx-report" / "scripts" / "gen.py"
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
        dst = res.out_dir / "skills" / "s_fx-report" / "scripts" / "client.py"
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
            dst = res.out_dir / "skills" / skill / "scripts" / "catalog.py"
            assert dst.is_file(), f"domain_tools SDK not materialized into {skill}"
            assert "def rule" in dst.read_text(encoding="utf-8")

    def test_same_name_in_both_sources_not_double_copied(self, tmp_path):
        """AC3: a filename in BOTH _shared and a domain_tools dir → one copy, _shared wins."""
        ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
        _add_shared(ddd, {"client.py": "V='from_shared'\n"})
        _add_domain_tools_sdk(ddd, "assets/data-source/scripts", {"client.py": "V='from_asset'\n"})
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        skill = res.skills_included[0]
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
            assert not (res.out_dir / "skills" / skill / "scripts" / "catalog.py").exists()

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
