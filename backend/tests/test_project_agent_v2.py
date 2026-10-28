"""Project Agent schema v2 (design docs/design/2026-10-28-project-agent-flow.md §4).

Questions these tests answer: does a v2 definition with modes parse into the shape
Brain Hub reads (with the v1 keys filled from the default mode); does a mode that
WIDENS the shared part (placement, reserved action, gate) fail; are the names a
definition uses checked against the Brain on disk (skills, gates, repos); is
branch-pr refused for an internal or non-GitHub repo; does a v1 file read as one
mode; does the revision move with the definition, a used skill and a gate, and not
with byte-compiled caches; does the P6 gate use the same validator.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from core.project_agent import (gates_digest, load_project_agent, normalize, notes,
                                revision, validate)

V2 = {
    "schema": 2,
    "status": "draft",
    "identity": {"summary": "Signing library", "source": "PRODUCT.md"},
    "intake": {"handles": ["fix a signer bug"], "repos": ["lib"]},
    "watches": [{"area": "security", "why": "constant-time compare", "source": "IMPROVEMENT.md"}],
    "gates": {"enforce": ["no_git_push"]},
    "reserved_for_human": ["push", "open_pr", "publish_release", "send_external_message"],
    "placements": ["chat-tab", "hive"],
    "landing": {"approval": "human"},
    "modes": [
        {"id": "code-change", "when": "change the library code", "profile": "bugfix",
         "profile_rules": [{"when": "a public API change", "profile": "full"}],
         "placements": ["chat-tab", "hive"], "repos": ["lib"],
         "setup": [{"command": "uv sync", "source": "ci.yml"}],
         "validations": [{"id": "tests", "command": "pytest -q", "source": "ci.yml"}],
         "skills": ["s_lib-release"], "delivery": "bundle"},
        {"id": "research", "when": "a question with no code change", "profile": "research"},
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
    assert pa["defaultProfile"] == "bugfix"
    assert pa["profileRules"] == [{"when": "a public API change", "profile": "full"}]
    assert pa["validations"][0]["command"] == "pytest -q"
    assert pa["reviewFocus"][0]["area"] == "security"
    research = pa["modes"][1]
    assert research["placements"] == ["chat-tab", "hive"]            # inherited
    assert research["reservedForHuman"] == V2["reserved_for_human"]  # inherited
    assert research["gates"] == ["no_git_push"] and research["delivery"] == "bundle"
    assert pa["revision"] and len(pa["revision"]) == 64


def test_a_mode_only_narrows(tmp_path):
    joined = " | ".join(validate(_with_mode(placements=["chat-tab", "local-worker"])))
    assert "['local-worker'] are not in the shared placements" in joined
    joined = " | ".join(validate(_with_mode(reserved_for_human=["push"])))
    assert "drops ['open_pr', 'publish_release', 'send_external_message']" in joined
    joined = " | ".join(validate(_with_mode(gates={"enforce": []})))
    assert "gates.enforce drops ['no_git_push']" in joined
    # adding is fine
    assert validate(_with_mode(placements=["chat-tab"], gates={"enforce": ["no_git_push", "x"]})) == []


def test_mode_shape_problems():
    d = yaml.safe_load(yaml.safe_dump(V2))
    d["modes"][1]["id"] = "code-change"
    d["modes"][0]["when"] = " "
    d["default_mode"] = "nope"
    d["modes"][0]["delivery"] = "push-main"
    d["reserved_for_human"].append("deploy")
    joined = " | ".join(validate(d))
    for needle in ("is used twice", "when is empty", "default_mode 'nope'",
                   "delivery 'push-main'", "unknown actions ['deploy']"):
        assert needle in joined
    assert "pr_remote" in " | ".join(validate(_with_mode(delivery="branch-pr", pr_remote="upstream")))
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


def test_branch_pr_needs_a_github_repo_that_is_not_internal(tmp_path):
    d = yaml.safe_load(yaml.safe_dump(V2))
    d["intake"]["repos"] = ["lib", "inner"]
    d["modes"][0].update(delivery="branch-pr", repos=["inner"])
    b = _brain(tmp_path, d)
    assert "repo inner is internal; only delivery bundle is allowed" in " | ".join(validate(d, b))
    d["modes"][0]["repos"] = ["lib"]
    assert validate(d, b) == []


def test_a_desktop_only_skill_in_a_hive_mode_is_a_note_not_a_problem(tmp_path):
    b = _brain(tmp_path, skill_platform="desktop")
    assert validate(V2, b) == []
    assert notes(V2, b) == ["modes[code-change]: skill s_lib-release is desktop-only and is not available on a Hive"]
    assert notes(_with_mode(placements=["chat-tab"]), b) == []


def test_v1_reads_as_one_mode_and_keeps_its_own_key_names():
    v1 = {"default_profile": "bugfix", "profile_rules": [{"when": "docs", "profile": "direct"}],
          "placements": ["chat-tab"], "validations": [{"id": "u", "command": "pytest"}]}
    n = normalize(v1)
    assert n["schema"] == 2 and n["default_mode"] == "default"
    assert n["modes"][0]["profile"] == "bugfix" and n["modes"][0]["validations"][0]["command"] == "pytest"
    assert validate(v1) == []
    probs = validate({**v1, "default_profile": "turbo"})
    assert probs == ["default_profile 'turbo' is not a pipeline profile"]


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
    assert status == mod.STATUS_PENDING and "2 modes, default code-change" in detail
    (b / "project-agent.yaml").write_text(yaml.safe_dump(_with_mode(placements=["local-worker"])))
    status, detail = mod._check_project_agent(b)[1:]
    assert status == mod.STATUS_FAIL and "a mode only narrows" in detail


def test_source_check_is_stored_per_revision(tmp_path):
    from core.project_agent import check_sources, source_check_current
    repo = tmp_path / "repo"
    repo.mkdir()
    b = _brain(tmp_path)
    (b / "bindings.yaml").write_text(BINDINGS.replace(
        'clone: "https://github.com/acme/lib.git"',
        f'clone: "https://github.com/acme/lib.git"\n    worktree: {repo}'))
    res = check_sources(b)
    assert res["ok"] is False and len(res["missing"]) == 2      # setup + validations both cite ci.yml
    (repo / "ci.yml").write_text("on: push\n")
    assert check_sources(b)["ok"] is True
    assert source_check_current(b)["ok"] is True
    (b / "project-agent.yaml").write_text((b / "project-agent.yaml").read_text() + "\n# edited\n")
    assert source_check_current(b) is None                      # stale after an edit
