"""Project Agent schema v2 (design docs/design/2026-10-28-project-agent-flow.md §4).

Questions these tests answer: does a v2 definition with modes parse into the shape
Brain Hub reads (with the v1 keys filled from the default mode); does a mode that
drops a shared gate fail; are the names a definition uses checked against the Brain on
disk (skills, gates, repos); does a v1 file read as one mode; does the revision move
with the definition, a used skill and a gate, and not with byte-compiled caches; does
the P6 gate use the same validator; is a command whose source is not in the repository
found (design 2026-11-01-project-agent-simple).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from core.project_agent import (RESERVED_ACTIONS, gates_digest, load_project_agent, normalize,
                                revision, source_problems, validate)

V2 = {
    "schema": 2,
    "status": "draft",
    "identity": {"summary": "Signing library", "source": "PRODUCT.md"},
    "intake": {"handles": ["fix a signer bug"], "repos": ["lib"]},
    "watches": [{"area": "security", "why": "constant-time compare", "source": "IMPROVEMENT.md"}],
    "gates": {"enforce": ["no_git_push"]},
    "reserved_for_human": ["push", "open_pr", "publish_release", "send_external_message"],
    "placements": ["chat-tab", "hive"],
    "modes": [
        {"id": "code-change", "when": "change the library code",
         "placements": ["chat-tab", "hive"], "repos": ["lib"],
         "setup": [{"command": "uv sync", "source": "ci.yml"}],
         "validations": [{"id": "tests", "command": "pytest -q", "source": "ci.yml"}],
         "skills": ["s_lib-release"], "delivery": "bundle"},
        {"id": "research", "when": "a question with no code change"},
    ],
    "default_mode": "code-change",
}

BINDINGS = """\
bindings:
  - repo: lib
    kind: external
    clone: "https://github.com/acme/lib.git"
    delivery_contract: {remote_kind: github-pr, branch: main, review_path: github-pr, auto_send: manual-push}
  - repo: inner
    kind: internal
    clone: "ssh://git.amazon.com/pkg/Inner"
    delivery_contract: {remote_kind: code-amazon-cr, branch: mainline, review_path: cr, auto_send: manual-push}
"""


def _brain(tmp_path: Path, doc: dict | None = None, skill_platform: str | None = None) -> Path:
    b = tmp_path / "Brain"
    (b / "4-capabilities" / "s_lib-release").mkdir(parents=True)
    head = f"---\nname: s_lib-release\n" + (f"platform: {skill_platform}\n" if skill_platform else "") + "---\n"
    (b / "4-capabilities" / "s_lib-release" / "SKILL.md").write_text(head + "release it\n")
    (b / "3-gates").mkdir()
    (b / "3-gates" / "no_git_push.py").write_text("import sys\nsys.exit(0)\n")
    (b / "3-gates" / "test_no_git_push.py").write_text("def test(): pass\n")
    (b / "bindings.yaml").write_text(BINDINGS)
    (b / "project-agent.yaml").write_text(yaml.safe_dump(doc or V2, allow_unicode=True))
    return b


def _with_mode(**changes) -> dict:
    d = yaml.safe_load(yaml.safe_dump(V2))
    d["modes"][0].update(changes)
    return d


def test_v2_parses_with_v1_keys_from_the_default_mode(tmp_path):
    pa = load_project_agent(_brain(tmp_path))
    assert pa["problems"] == [] and pa["schema"] == 2
    assert [m["id"] for m in pa["modes"]] == ["code-change", "research"]
    assert "defaultProfile" not in pa and all("profile" not in m for m in pa["modes"])
    assert pa["validations"][0]["command"] == "pytest -q"
    assert pa["reviewFocus"][0]["area"] == "security"
    research = pa["modes"][1]
    assert research["reservedForHuman"] == sorted(RESERVED_ACTIONS)  # the product's
    assert research["gates"] == ["no_git_push"]                      # inherited
    assert pa["revision"] and len(pa["revision"]) == 64


def test_a_mode_only_adds_gates(tmp_path):
    joined = " | ".join(validate(_with_mode(gates={"enforce": []})))
    assert "gates.enforce drops ['no_git_push']" in joined
    assert validate(_with_mode(gates={"enforce": ["no_git_push", "x"]})) == []


def test_mode_shape_problems():
    d = yaml.safe_load(yaml.safe_dump(V2))
    d["modes"][1]["id"] = "code-change"
    d["modes"][0]["when"] = " "
    d["default_mode"] = "nope"
    joined = " | ".join(validate(d))
    for needle in ("is used twice", "when is empty", "default_mode 'nope'"):
        assert needle in joined
    assert "not in intake.repos" in " | ".join(validate(_with_mode(repos=["other"])))


def test_names_are_checked_against_the_brain(tmp_path):
    b = _brain(tmp_path, _with_mode(skills=["s_lib-release", "s_missing"], repos=["lib", "ghost"]))
    joined = " | ".join(validate(yaml.safe_load((b / "project-agent.yaml").read_text()), b))
    assert "skills that do not exist: ['s_missing']" in joined
    assert "repos that do not exist: ['ghost']" in joined or "not in intake.repos" in joined
    d = yaml.safe_load(yaml.safe_dump(V2))
    d["gates"]["enforce"] = ["no_git_push", "test_no_git_push"]   # a test file is not a gate
    assert "gates that do not exist: ['test_no_git_push']" in " | ".join(validate(d, b))
    assert validate(V2, b) == []


def test_v1_reads_as_one_mode_and_keeps_its_own_key_names():
    v1 = {"default_profile": "bugfix", "profile_rules": [{"when": "docs", "profile": "direct"}],
          "placements": ["chat-tab"], "validations": [{"id": "u", "command": "pytest"}]}
    n = normalize(v1)
    assert n["schema"] == 2 and n["default_mode"] == "default"
    assert "profile" not in n["modes"][0] and n["modes"][0]["validations"][0]["command"] == "pytest"
    assert validate(v1) == []
    assert validate({**v1, "default_profile": "turbo"}) == []      # not read any more


def test_revision_follows_definition_skill_and_gate_not_caches(tmp_path):
    b = _brain(tmp_path)
    r0 = revision(b)
    g0 = gates_digest(b)
    (b / "3-gates" / "__pycache__").mkdir()
    (b / "3-gates" / "__pycache__" / "no_git_push.cpython-312.pyc").write_bytes(b"\x00")
    assert revision(b) == r0 and gates_digest(b) == g0
    (b / "4-capabilities" / "s_lib-release" / "run.sh").write_text("echo\n")
    r1 = revision(b)
    assert r1 != r0
    (b / "3-gates" / "no_git_push.py").write_text("import sys\nsys.exit(2)\n")
    assert gates_digest(b) != g0 and revision(b) != r1
    r2 = revision(b)
    (b / "project-agent.yaml").write_text((b / "project-agent.yaml").read_text() + "\n# note\n")
    assert revision(b) != r2
    assert revision(tmp_path / "none") is None


def test_p6_uses_the_same_validator(tmp_path):
    p = Path(__file__).resolve().parents[1] / "skills/s_project-manager/scripts/verify_ddd_complete.py"
    spec = importlib.util.spec_from_file_location("verify_ddd_complete_v2", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    b = _brain(tmp_path)
    name, status, detail = mod._check_project_agent(b)
    assert status == mod.STATUS_PASS and "2 modes, default code-change" in detail
    (b / "project-agent.yaml").write_text(yaml.safe_dump(_with_mode(gates={"enforce": []})))
    status, detail = mod._check_project_agent(b)[1:]
    assert status == mod.STATUS_FAIL and "a mode only adds" in detail


def test_a_command_whose_source_is_not_in_the_repository_is_found(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    b = _brain(tmp_path)
    (b / "bindings.yaml").write_text(BINDINGS.replace(
        'clone: "https://github.com/acme/lib.git"',
        f'clone: "https://github.com/acme/lib.git"\n    worktree: {repo}'))
    assert len(source_problems(b)) == 2                          # setup + validations both cite ci.yml
    assert len(source_problems(b, "code-change")) == 2
    assert source_problems(b, "research") == []                  # another mode's stale row does not refuse it
    (repo / "ci.yml").write_text("on: push\n")
    assert source_problems(b) == []
    assert not list(b.glob(".project-agent-check*"))             # nothing is kept: it is a check
