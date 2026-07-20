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
def _make_skill(skills_dir: Path, name: str, *, body: str = "", script: str | None = None) -> None:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} skill\n---\n\n# {name}\n{body}\n", encoding="utf-8")
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
            "native_skills": ["s_ddd-manager", "s_ai-ready-repo"],
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
    secret_script = 'API_KEY = "AKIAIOSFODNN7EXAMPLE1"\n' if plant_secret else "print('ok')\n"
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
        assert pk._is_enablement("s_ai-ready-repo") is True
        assert pk._is_enablement("s_fx-report") is False


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
            "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE1\n", encoding="utf-8")
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)

    def test_private_key_in_pem_file_aborts(self, tmp_path):
        # C1: .pem is not a "content" suffix but must be scanned.
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        (ddd / "skills" / "s_fx-report" / "key.pem").write_text(
            "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n", encoding="utf-8")
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)

    def test_unquoted_secret_assignment_aborts(self, tmp_path):
        # H1: an UNQUOTED secret= assignment (the dominant shell/.env form) must match.
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        (ddd / "skills" / "s_fx-report" / "setup.sh").write_text(
            "#!/usr/bin/env bash\nexport API_KEY=supersecretvalue123\n", encoding="utf-8")
        with pytest.raises(pk.PackagingError, match="content-safety"):
            pk.package_ddd(ddd, tmp_path / "out", publish=True)

    def test_non_utf8_file_does_not_hide_secret(self, tmp_path):
        # C3: a non-UTF-8 byte must not silently skip the file — the readable text
        # (still containing the secret) is scanned via errors="replace".
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external")
        p = ddd / "skills" / "s_fx-report" / "notes.txt"
        p.write_bytes(b"\xff\xfe garbage byte then AKIAIOSFODNN7EXAMPLE1 secret\n")
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
    def test_unclassified_excluded_and_warned(self, tmp_path):
        ddd = build_fixture_ddd(tmp_path, targets=["open-plugin"], visibility="external",
                                add_unclassified_skill=True)
        [res] = pk.package_ddd(ddd, tmp_path / "out")
        assert "s_fx-orphan" in res.skills_excluded
        assert not (res.out_dir / "skills" / "s_fx-orphan").exists()
        assert any("s_fx-orphan" in w and "unclassified" in w for w in res.warnings)


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
