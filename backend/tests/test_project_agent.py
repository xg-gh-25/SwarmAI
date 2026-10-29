"""Tests for core.project_agent and its two readers (Brain Hub detail, P6 gate).

What is tested: a valid definition parses into the camelCase shape the Brain Hub
shows; a profile or placement the pipeline does not support is reported, never
silently accepted; a missing file is None / PENDING (old Brains are not broken);
a malformed file never raises; ⑤ Delivery lists project-agent.yaml next to
bindings.yaml; the P6 gate FAILs an unsupported profile.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from core.project_agent import PROFILES, load_project_agent, validate
from core.run_protocol.lifecycle import PROFILE_STAGES

GOOD = """\
schema: 1
status: draft
summary: Card payments API; most changes are small fixes behind the service layer.
default_profile: bugfix
profile_rules:
  - when: config-only or docs-only change
    profile: direct
  - when: a new public API endpoint
    profile: full
review_focus:
  - area: concurrency
    why: refunds settle asynchronously through the ledger worker
    source: TECH.md
validations:
  - id: unit
    command: pytest -q
    source: .github/workflows/ci.yml
placements: [chat-tab]
landing: {approval: human, via: github-pr}
sources: [TECH.md, bindings.yaml]
"""


def _verify_mod():
    p = Path(__file__).resolve().parents[1] / "skills/s_project-manager/scripts/verify_ddd_complete.py"
    spec = importlib.util.spec_from_file_location("verify_ddd_complete_pa", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_profiles_are_the_pipeline_profiles():
    assert PROFILES == frozenset(PROFILE_STAGES)


def test_valid_definition_parses(tmp_path):
    (tmp_path / "project-agent.yaml").write_text(GOOD)
    pa = load_project_agent(tmp_path)
    assert pa is not None and pa["problems"] == []
    assert pa["defaultProfile"] == "bugfix"
    assert pa["profileRules"][1] == {"when": "a new public API endpoint", "profile": "full"}
    assert pa["reviewFocus"][0]["source"] == "TECH.md"
    assert pa["validations"][0]["command"] == "pytest -q"
    assert pa["placements"] == ["chat-tab"]


def test_unsupported_values_are_reported():
    probs = validate({"default_profile": "turbo", "profile_rules": [{"when": "", "profile": "x"}],
                      "placements": ["cloud"], "status": "done"})
    joined = " | ".join(probs)
    for needle in ("default_profile", "profile_rules[0].profile", "profile_rules[0].when", "placements", "status"):
        assert needle in joined


def test_missing_is_none_and_malformed_never_raises(tmp_path):
    assert load_project_agent(tmp_path) is None
    (tmp_path / "project-agent.yaml").write_text("default_profile: [unclosed\n")
    assert load_project_agent(tmp_path)["problems"] == ["file could not be parsed"]
    (tmp_path / "project-agent.yaml").write_text("- a list\n")
    assert load_project_agent(tmp_path)["problems"] == ["not a mapping"]


def test_delivery_section_lists_the_project_agent(tmp_path):
    from routers.ddd_brain import _section_members
    (tmp_path / "bindings.yaml").write_text("bindings: []\n")
    (tmp_path / "project-agent.yaml").write_text(GOOD)
    assert _section_members(tmp_path, "delivery") == ["bindings.yaml", "project-agent.yaml"]


def test_p6_gate(tmp_path):
    mod = _verify_mod()
    name, status, _ = mod._check_project_agent(tmp_path)
    assert (name, status) == ("⑤ Project Agent", mod.STATUS_FAIL)       # every Brain has one
    (tmp_path / "project-agent.yaml").write_text(GOOD)
    assert mod._check_project_agent(tmp_path)[1] == mod.STATUS_PENDING   # a draft waits for the person
    (tmp_path / "project-agent.yaml").write_text(GOOD.replace("status: draft", "status: reviewed"))
    assert mod._check_project_agent(tmp_path)[1] == mod.STATUS_PASS
    (tmp_path / "project-agent.yaml").write_text(GOOD.replace("default_profile: bugfix", "default_profile: turbo"))
    assert mod._check_project_agent(tmp_path)[1] == mod.STATUS_FAIL


def test_status_for_the_hub_home(tmp_path):
    """The gallery's projectAgentStatus: draft waits for the person, reviewed does
    not, a broken file is invalid, no file is None."""
    from core.project_agent import project_agent_status
    from routers import ddd_brain as m

    assert project_agent_status(tmp_path) is None
    f = tmp_path / "project-agent.yaml"
    f.write_text(GOOD)
    assert project_agent_status(tmp_path) == "draft"
    assert m._brain_summary(tmp_path)["projectAgentStatus"] == "draft"
    f.write_text(GOOD.replace("status: draft", "status: reviewed"))
    assert project_agent_status(tmp_path) == "reviewed"
    f.write_text(GOOD.replace("default_profile: bugfix", "default_profile: yolo"))
    assert project_agent_status(tmp_path) == "invalid"
    f.write_text("- not\n- a mapping\n")
    assert project_agent_status(tmp_path) == "invalid"
