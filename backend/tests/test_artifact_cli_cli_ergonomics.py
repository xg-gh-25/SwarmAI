"""Tests for Run A — artifact_cli.py CLI ergonomics (G1/G2/G3).

Three independent frictions, all in artifact_cli.py:
- G2 (_read_json_arg): run-update --stage-json now supports @file, like publish --data.
  A single shared helper parses the raw-string source (@path / @- / inline) at BOTH
  call sites; json.loads STAYS at each call site (preserving run_update's asymmetric
  exception semantics — the deliver-ack precheck swallows, the main parse raises).
- G3 (advance --quiet): advance emits a compact {project,pipeline_state,advanced}
  payload (dropping the large next_stage_doc/next_action) when --quiet; a blocked
  advance returns an explicit {advanced:false,reason}.
- G1 (schema template prominence): cmd_schema surfaces `template` first + a `_hint`
  key so a consumer does not miss the full nested shape by reading only required/depth.

These are CLI-tooling bugfixes; the helper extraction is a contract migration (R27 —
all consumers of --data/--stage-json converted).
"""

import json
import sys
from pathlib import Path

import pytest

_scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
import scripts.artifact_cli as cli  # noqa: E402


# ─────────────────────────── G2: _read_json_arg ───────────────────────────
class TestReadJsonArg:
    """AC2 — single shared helper parses @path / @- / inline → raw string."""

    def test_at_path_reads_file(self, tmp_path):
        f = tmp_path / "payload.json"
        f.write_text('{"stage":"test","note":"中文内容"}', encoding="utf-8")
        got = cli._read_json_arg(f"@{f}", "--stage-json")
        assert got == '{"stage":"test","note":"中文内容"}'

    def test_at_dash_reads_stdin(self, monkeypatch):
        import io
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"from":"stdin"}'))
        got = cli._read_json_arg("@-", "--data")
        assert got == '{"from":"stdin"}'

    def test_inline_returned_verbatim(self):
        raw = '{"stage":"build","inline":true}'
        assert cli._read_json_arg(raw, "--stage-json") == raw

    def test_inline_not_starting_with_at_is_untouched(self):
        # A JSON value never starts with '@', so a non-@ string is pure inline.
        assert cli._read_json_arg('{"a":1}', "--data") == '{"a":1}'

    def test_unreadable_file_exits_with_argname_in_message(self, capsys, tmp_path):
        missing = tmp_path / "does_not_exist.json"
        with pytest.raises(SystemExit) as exc:
            cli._read_json_arg(f"@{missing}", "--stage-json")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        # error message is parameterized by argname (NOT hardcoded "--data")
        assert "--stage-json" in err
        assert "--data" not in err

    def test_helper_does_not_json_parse(self, tmp_path):
        # Helper returns the RAW string even if it is invalid JSON — json.loads
        # stays at the call site. This is what preserves the asymmetric exception
        # semantics in cmd_run_update (precheck swallows, main parse raises).
        f = tmp_path / "bad.json"
        f.write_text("not valid json {", encoding="utf-8")
        got = cli._read_json_arg(f"@{f}", "--data")
        assert got == "not valid json {"  # no JSONDecodeError raised here


# ─────────────────────── G2: run-update --stage-json @file ─────────────────
class TestRunUpdateStageJsonAtFile:
    """AC1 — run-update --stage-json @file reads a file, equivalent to inline."""

    def _run(self, project, run_id, stage_json_arg, monkeypatch):
        class _Args:
            pass
        a = _Args()
        a.project = project
        a.run_id = run_id
        a.stage_json = stage_json_arg
        a.status = None
        a.profile = None
        a.ddd_checksums = None
        a.files_touched = None
        a.add_required_expert = None
        return a

    def test_at_file_equivalent_to_inline(self, tmp_path, monkeypatch):
        # Rather than spin a whole run, assert at the parse layer: the stage_record
        # obtained from @file equals the one from the inline string. We do this by
        # reading via the helper (the exact path cmd_run_update now takes).
        payload = '{"stage":"think","status":"completed","stage_doc_consumed":true,"note":"活体"}'
        f = tmp_path / "sj.json"
        f.write_text(payload, encoding="utf-8")

        from_inline = json.loads(cli._read_json_arg(payload, "--stage-json"))
        from_file = json.loads(cli._read_json_arg(f"@{f}", "--stage-json"))
        assert from_inline == from_file
        assert from_file["note"] == "活体"


# ─────────────────────────── G3: advance --quiet ──────────────────────────
class TestAdvanceQuiet:
    """AC3/AC4 — advance --quiet compact payload; blocked advance → explicit reason."""

    class _StubReg:
        """Minimal stub: advance_pipeline succeeds, or raises ValueError if told to."""
        def __init__(self, raise_value_error=False):
            self._raise = raise_value_error

        def advance_pipeline(self, project, state):
            if self._raise:
                raise ValueError(f"illegal transition to {state}")

    def _args(self, quiet, state="think"):
        class _A:
            pass
        a = _A()
        a.project = "SwarmAI"
        a.state = state
        a.run_id = None
        a.quiet = quiet
        return a

    def test_quiet_payload_is_compact(self, capsys, monkeypatch):
        # Skip the pre-advance validation gate by passing run_id=None (no validation).
        reg = self._StubReg()
        cli.cmd_advance(self._args(quiet=True), reg)
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["advanced"] is True
        assert payload["pipeline_state"] == "think"
        # the large fields are DROPPED under --quiet
        assert "next_stage_doc" not in payload
        assert "next_action" not in payload

    def test_non_quiet_payload_keeps_continuation_fields(self, capsys):
        reg = self._StubReg()
        cli.cmd_advance(self._args(quiet=False), reg)
        payload = json.loads(capsys.readouterr().out.strip())
        # continuation contract preserved when NOT quiet
        assert "next_stage_doc" in payload
        assert "next_action" in payload

    def test_blocked_advance_returns_explicit_reason(self, capsys):
        reg = self._StubReg(raise_value_error=True)
        with pytest.raises(SystemExit) as exc:
            cli.cmd_advance(self._args(quiet=True, state="deliver"), reg)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        payload = json.loads(err)
        assert payload["advanced"] is False
        assert "reason" in payload
        assert "illegal transition" in payload["reason"]


# ─────────────────────────── G1: schema template prominence ────────────────
class TestSchemaTemplateProminence:
    """AC5 — template surfaced first + _hint; existing keys preserved (additive)."""

    def _args(self, stage="test"):
        class _A:
            pass
        a = _A()
        a.stage = stage
        return a

    def test_template_is_first_non_stage_key(self, capsys):
        cli.cmd_schema(self._args("test"), None)
        parsed = json.loads(capsys.readouterr().out.strip(), object_pairs_hook=list)
        # parsed is a list of (key, value) preserving JSON key order
        keys = [k for k, _ in parsed]
        assert keys[0] == "stage"
        assert keys[1] == "template", f"template must be first after stage, got {keys[:3]}"

    def test_hint_key_present(self, capsys):
        cli.cmd_schema(self._args("test"), None)
        parsed = json.loads(capsys.readouterr().out.strip())
        assert "_hint" in parsed
        assert "template" in parsed["_hint"].lower()

    def test_existing_keys_preserved_additive(self, capsys):
        cli.cmd_schema(self._args("test"), None)
        parsed = json.loads(capsys.readouterr().out.strip())
        # nothing removed — required/recommended/depth/template all still readable
        assert "required" in parsed
        assert "depth" in parsed
        assert "template" in parsed
        assert parsed["stage"] == "test"

    def test_template_still_carries_nested_shape(self, capsys):
        cli.cmd_schema(self._args("test"), None)
        parsed = json.loads(capsys.readouterr().out.strip())
        # the whole point: template exposes layers.ac_driven:{run,pass} nested shape
        ac_driven = parsed["template"]["layers"]["ac_driven"]
        assert "run" in ac_driven and "pass" in ac_driven
