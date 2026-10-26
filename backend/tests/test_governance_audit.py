"""Default-model-changed → prompt-audit hint (docs/design/2026-10-26-evolution-loop-admission.md §3.5)."""

from __future__ import annotations

import asyncio
import json
import unittest.mock as mock

from core import governance_audit as ga


def test_first_start_writes_baseline_and_no_hint(tmp_path):
    assert ga.audit_hint(tmp_path, "m1") is None             # no state: no hint, and the read writes nothing
    assert not (tmp_path / ".context" / ".governance_audit.json").exists()
    assert ga.record_baseline(tmp_path, "m1") is True
    state = json.loads((tmp_path / ".context" / ".governance_audit.json").read_text())
    assert state == {"baseline_model": "m1"}                  # never recorded as audited
    assert ga.audit_hint(tmp_path, "m1") is None
    assert ga.record_baseline(tmp_path, "m2") is False        # baseline is written once


def test_changed_default_model_hints_until_audited(tmp_path):
    ga.record_baseline(tmp_path, "m1")
    hint = ga.audit_hint(tmp_path, "m2")
    assert hint and "m2" in hint and "m1" in hint and "s_context-hygiene" in hint
    ga.record_audit(tmp_path, "m2")
    assert ga.audit_hint(tmp_path, "m2") is None
    assert "m2" in ga.audit_hint(tmp_path, "m3")              # compares against the audited model now


def test_cli_mark(tmp_path, capsys):
    ga.record_baseline(tmp_path, "m1")
    assert ga._main(["mark", "--model", "m2", "--workspace", str(tmp_path)]) == 0
    assert ga.audit_hint(tmp_path, "m2") is None


def test_briefing_uses_default_model_not_the_tab_override(tmp_path):
    """The hint reaches the system prompt, and a per-tab model override does not
    change the comparison (it uses config.default_model)."""
    from tests.test_prompt_builder_properties import _make_builder

    ga.record_baseline(tmp_path, "older-model")             # builder's default is claude-sonnet-4-6
    for agent_config in ({}, {"model": "older-model"}):
        builder = _make_builder()
        with mock.patch("core.proactive_intelligence.build_session_briefing", return_value=None):
            asyncio.run(builder.build_system_prompt(agent_config=agent_config, working_directory=str(tmp_path)))
        full = (agent_config.get("_system_prompt_metadata") or {}).get("full_text", "")
        assert "Default model changed to claude-sonnet-4-6" in full, agent_config
