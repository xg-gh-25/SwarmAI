"""Forced-execution tests for R1.5a — the session<->run "key system" for D7.

Every test DRIVES the real code (the real hook closure),
never a mock of the subject. Mutation-proof points are called out: reverting the
production line makes the named test go RED.

Covered:
  * create_pipeline_run_mapping_hook — parses `pipeline_v1.py create` stdout, sets
    _active_pipeline_run_id in ctx, persists the session->run map keyed by the
    APP-LEVEL session_id (NOT an env value — the identity correctness AC7).
  * _extract_pipeline_id — dict / str / list / wrapped-stdout shapes.
  (expert_id extraction moved to test_expert_id_contract.py in R1.5b-i.)
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from core import runtime_hooks as rh  # noqa: E402



def _run_hook(hook, input_data):
    return asyncio.run(hook(input_data, "tuid", None))


# ---- _extract_pipeline_id ---------------------------------------------------

@pytest.mark.parametrize("resp,expected", [
    ({"pipeline_id": "run_abc"}, "run_abc"),
    ('log line\n{"pipeline_id": "run_xyz", "project": "P"}', "run_xyz"),
    ({"stdout": '{"pipeline_id": "run_s"}'}, "run_s"),
    ([{"text": '{"pipeline_id": "run_blk"}'}], "run_blk"),
    ("no id here", None),
    (None, None),
    ({}, None),
])
def test_extract_pipeline_id_shapes(resp, expected):
    assert rh._extract_pipeline_id(resp) == expected


def test_extract_pipeline_id_takes_last_object():
    """If stdout echoes an old id then prints the real result, take the LAST."""
    text = '{"pipeline_id": "run_old"}\n...\n{"pipeline_id": "run_new"}'
    assert rh._extract_pipeline_id(text) == "run_new"


# ---- the rendezvous hook ----------------------------------------------------

def test_hook_sets_active_run_and_persists_map(tmp_path, monkeypatch):
    """AC1/AC2/AC7: a create tool_response sets ctx['_active_pipeline_run_id']
    AND writes a session->run map keyed by the ctx (app-level) session_id."""
    monkeypatch.setattr(rh, "_PIPELINE_AUDIT_DIR", tmp_path)
    ctx = {"sdk_session_id": "app-sess-123"}
    hook = rh.create_pipeline_run_mapping_hook(ctx)
    _run_hook(hook, {
        "tool_name": "Bash",
        "tool_input": {"command": "python backend/scripts/pipeline_v1.py create --project P --requirement x"},
        "tool_response": {"pipeline_id": "run_deadbeef"},
    })
    # ctx half (revives the dead consumer)
    assert ctx["_active_pipeline_run_id"] == "run_deadbeef"
    # disk half, keyed by the APP-LEVEL session id (AC7 — not any env value)
    mp = tmp_path / "session_app-sess-123_run.map"
    assert mp.exists(), "mapping file not written under the app-level session id"
    data = json.loads(mp.read_text())
    assert data["run_id"] == "run_deadbeef"
    assert data["session_id"] == "app-sess-123"


def test_hook_ignores_non_run_create_bash(tmp_path, monkeypatch):
    """A normal Bash command does nothing — no map, no ctx mutation."""
    monkeypatch.setattr(rh, "_PIPELINE_AUDIT_DIR", tmp_path)
    ctx = {"sdk_session_id": "s"}
    hook = rh.create_pipeline_run_mapping_hook(ctx)
    _run_hook(hook, {"tool_name": "Bash",
                     "tool_input": {"command": "ls -la"},
                     "tool_response": {"pipeline_id": "run_x"}})
    assert "_active_pipeline_run_id" not in ctx
    assert not list(tmp_path.glob("*.map"))


def test_hook_ignores_failed_run_create(tmp_path, monkeypatch):
    """a create that printed no pipeline_id (failure) → no mapping."""
    monkeypatch.setattr(rh, "_PIPELINE_AUDIT_DIR", tmp_path)
    ctx = {"sdk_session_id": "s"}
    hook = rh.create_pipeline_run_mapping_hook(ctx)
    _run_hook(hook, {"tool_name": "Bash",
                     "tool_input": {"command": "python backend/scripts/pipeline_v1.py create --project P"},
                     "tool_response": "Error: boom"})
    assert "_active_pipeline_run_id" not in ctx
    assert not list(tmp_path.glob("*.map"))


def test_hook_sets_ctx_even_without_session(tmp_path, monkeypatch):
    """No session_id → ctx half still done (so marker#1 can write), disk half skipped."""
    monkeypatch.setattr(rh, "_PIPELINE_AUDIT_DIR", tmp_path)
    ctx = {"sdk_session_id": ""}
    hook = rh.create_pipeline_run_mapping_hook(ctx)
    _run_hook(hook, {"tool_name": "Bash",
                     "tool_input": {"command": "pipeline_v1.py create --project P"},
                     "tool_response": {"pipeline_id": "run_q"}})
    assert ctx["_active_pipeline_run_id"] == "run_q"
    assert not list(tmp_path.glob("*.map"))


def test_mapping_key_is_app_session_not_env(tmp_path, monkeypatch):
    """MUTATION-PROOF of AC7: the map is keyed by ctx['sdk_session_id'] — if an env
    value were used instead, this (which sets a DIFFERENT env) would reveal it."""
    monkeypatch.setattr(rh, "_PIPELINE_AUDIT_DIR", tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "env-sess-DIFFERENT")
    ctx = {"sdk_session_id": "app-sess-CORRECT"}
    hook = rh.create_pipeline_run_mapping_hook(ctx)
    _run_hook(hook, {"tool_name": "Bash",
                     "tool_input": {"command": "pipeline_v1.py create --project P"},
                     "tool_response": {"pipeline_id": "run_z"}})
    assert (tmp_path / "session_app-sess-CORRECT_run.map").exists()
    assert not (tmp_path / "session_env-sess-DIFFERENT_run.map").exists(), (
        "mapping was keyed by the env session id — WRONG namespace (D7 would never "
        "find it); it must key on the app-level ctx['sdk_session_id']"
    )


# NOTE: `_expert_id_from_agent_type` was REMOVED in R1.5b-i (方案 Z) — the expert_id
# is no longer derived from the SDK agent_type (which is `general-purpose` for real
# spawns). It is now extracted from the spawn-prompt's `EXPERT-ID:` anchor line; see
# test_expert_id_contract.py for the inject↔extract contract tests.

