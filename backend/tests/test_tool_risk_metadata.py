"""Tool risk: metadata can only raise risk; a user-local override can set it
(design 2026-10-24-swarmai-usable-by-anyone §3.4, external_approval_gate row).

What is tested:
- a tool's own metadata (``requires_approval`` / MCP ``openWorldHint`` + not read-only,
  ``destructiveHint``) raises an MCP tool the verb taxonomy calls READ or WRITE_LOCAL
  to EXTERNAL; a metadata claim of read-only never lowers an EXTERNAL verb.
- the user-local override file (``<app data>/tool_risk_overrides.json``) sets a tool's
  class (the user's own decision, so it may lower too); a malformed file or an unknown
  class value is ignored; Bash is never overridable (dangerous_command_gate owns it).
- external_approval_gate consults the override (an overridden-to-external MCP tool is
  sent for approval; an overridden-to-read one is not).
"""
import asyncio
import json

import pytest

from core import tool_risk
from core.tool_risk import RiskClass, classify


@pytest.mark.parametrize("meta", [
    {"requires_approval": True},
    {"openWorldHint": True},
    {"destructiveHint": True},
])
def test_metadata_raises_a_local_or_read_verb_to_external(meta):
    assert classify("mcp__crm__create_record") is RiskClass.WRITE_LOCAL
    assert classify("mcp__crm__create_record", metadata=meta) is RiskClass.EXTERNAL
    assert classify("mcp__crm__search_records", metadata=meta) is RiskClass.EXTERNAL


def test_openworld_read_only_is_not_raised():
    meta = {"openWorldHint": True, "readOnlyHint": True}
    assert classify("mcp__web__search_pages", metadata=meta) is RiskClass.READ


def test_metadata_never_lowers_risk():
    meta = {"readOnlyHint": True, "requires_approval": False}
    assert classify("mcp__slack__post_message", metadata=meta) is RiskClass.EXTERNAL


def test_attribute_style_metadata_still_works():
    class M:
        requires_approval = True
    assert classify("mcp__x__create_thing", metadata=M()) is RiskClass.EXTERNAL


@pytest.fixture
def overrides(tmp_path, monkeypatch):
    path = tmp_path / "tool_risk_overrides.json"
    monkeypatch.setattr(tool_risk, "_overrides_path", lambda: path)
    tool_risk._OVERRIDE_CACHE.clear()
    return path


def test_user_override_sets_the_class(overrides):
    overrides.write_text(json.dumps({"mcp__crm__create_record": "external",
                                     "mcp__slack__post_message": "read"}))
    ov = tool_risk.user_overrides
    assert classify("mcp__crm__create_record", overrides=ov) is RiskClass.EXTERNAL
    assert classify("mcp__slack__post_message", overrides=ov) is RiskClass.READ


def test_bad_override_file_or_value_is_ignored(overrides):
    overrides.write_text("{not json")
    assert classify("mcp__slack__post_message", overrides=tool_risk.user_overrides) \
        is RiskClass.EXTERNAL
    tool_risk._OVERRIDE_CACHE.clear()
    overrides.write_text(json.dumps({"mcp__slack__post_message": "harmless"}))
    assert classify("mcp__slack__post_message", overrides=tool_risk.user_overrides) \
        is RiskClass.EXTERNAL


def test_bash_is_never_overridden(overrides):
    overrides.write_text(json.dumps({"Bash": "read"}))
    assert classify("Bash", overrides=tool_risk.user_overrides) is RiskClass.EXEC


def test_external_approval_gate_consults_the_override(overrides, monkeypatch):
    from core import security_hooks
    asked = []

    class PM:
        def __getattr__(self, name):
            def _f(*a, **k):
                asked.append(name)
                raise RuntimeError("stop here")
            return _f

    gate = security_hooks.create_external_approval_gate({"sdk_session_id": "s"}, "k", PM())
    call = {"tool_name": "mcp__crm__create_record", "tool_input": {}}
    out = asyncio.run(gate(call, "tu1", None))
    assert out.get("decision") == "approve" and not asked     # WRITE_LOCAL: no prompt
    overrides.write_text(json.dumps({"mcp__crm__create_record": "external"}))
    tool_risk._OVERRIDE_CACHE.clear()
    try:
        asyncio.run(gate(call, "tu2", None))
    except Exception:
        pass
    assert asked, "an overridden-to-external tool must reach the approval flow"
