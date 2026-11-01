"""Tests for core.project_agent and its two readers (Brain Hub detail, P6 gate).

Design docs/design/2026-11-01-project-agent-simple.md. What is tested: a valid
definition parses into the shape the Brain Hub shows; the keys a definition no longer
has (status, placements, delivery, reserved actions, a pipeline profile) are not read
and are no problem, whatever their value; the actions kept for the person are the
product's whatever a file says; a missing file is None; a malformed file never raises;
⑤ Delivery lists project-agent.yaml next to bindings.yaml; the P6 gate passes any valid
definition (nothing is confirmed); the Hub home word is ready / invalid / None.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from core.project_agent import RESERVED_ACTIONS, load_project_agent, project_agent_status, validate

GOOD = """\
schema: 1
status: draft
summary: Card payments API; most changes are small fixes behind the service layer.
default_profile: bugfix
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


def test_valid_definition_parses(tmp_path):
    (tmp_path / "project-agent.yaml").write_text(GOOD)
    pa = load_project_agent(tmp_path)
    assert pa is not None and pa["problems"] == []
    assert pa["reviewFocus"][0]["source"] == "TECH.md"
    assert pa["validations"][0]["command"] == "pytest -q"
    for gone in ("status", "placements", "confirmation", "form", "reviewedBy", "sourceCheck"):
        assert gone not in pa
    assert all("profile" not in m and "placements" not in m and "delivery" not in m for m in pa["modes"])


def test_keys_no_longer_read_are_no_problem_whatever_their_value():
    assert validate({"schema": 2, "status": "done", "placements": ["cloud"],
                     "reserved_for_human": ["nonsense"],
                     "modes": [{"id": "a", "when": "w", "delivery": "teleport", "pr_remote": "x",
                                "placements": [], "reserved_for_human": [], "profile": "turbo"}],
                     "default_mode": "a"}) == []


def test_the_actions_kept_for_the_person_are_the_products(tmp_path):
    (tmp_path / "project-agent.yaml").write_text(
        "schema: 2\nreserved_for_human: []\nmodes:\n- id: a\n  when: w\n  reserved_for_human: [push]\n"
        "default_mode: a\n")
    pa = load_project_agent(tmp_path)
    assert pa["reservedForHuman"] == sorted(RESERVED_ACTIONS)
    assert pa["modes"][0]["reservedForHuman"] == sorted(RESERVED_ACTIONS)


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


def test_p6_gate_passes_any_valid_definition(tmp_path):
    mod = _verify_mod()
    name, status, _ = mod._check_project_agent(tmp_path)
    assert (name, status) == ("⑤ Project Agent", mod.STATUS_FAIL)       # every Brain has one
    (tmp_path / "project-agent.yaml").write_text(GOOD)
    assert mod._check_project_agent(tmp_path)[1] == mod.STATUS_PASS      # a draft is no longer waiting
    (tmp_path / "project-agent.yaml").write_text(GOOD.replace("placements: [chat-tab]", "placements: [cloud]"))
    assert mod._check_project_agent(tmp_path)[1] == mod.STATUS_PASS      # placements is not read
    (tmp_path / "project-agent.yaml").write_text("schema: 2\nmodes: []\n")
    assert mod._check_project_agent(tmp_path)[1] == mod.STATUS_FAIL


def test_status_for_the_hub_home(tmp_path):
    """ready, invalid, or None: nothing waits for a confirmation."""
    from routers import ddd_brain as m
    assert project_agent_status(tmp_path) is None
    f = tmp_path / "project-agent.yaml"
    f.write_text(GOOD)
    assert project_agent_status(tmp_path) == "ready"
    assert m._brain_summary(tmp_path)["projectAgentStatus"] == "ready"
    f.write_text("schema: 2\nmodes: []\n")
    assert project_agent_status(tmp_path) == "invalid"
    f.write_text("- not\n- a mapping\n")
    assert project_agent_status(tmp_path) == "invalid"
