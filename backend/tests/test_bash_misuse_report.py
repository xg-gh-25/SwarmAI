"""Tests for backend/scripts/bash_misuse_report.py.

Methodology: synthetic Claude Code transcripts (one JSONL line per message, the
SDK's tool_use / tool_result shape) are written to a temp dir and scanned. Checks:
- each gate is recognised by its own deny text (classify_gate), an unknown hook
  error is ``other_hook``, an ordinary failing command is no gate;
- a session with 3+ denials by one gate counts in ``repeat_sessions``;
- a foreground call the CLI moved to the background counts, an explicit
  run_in_background call does not;
- ``inline_state_reads`` / ``run_json_writes`` count python that loads (and writes)
  run.json; ``run-get --brief`` does not count;
- entries outside [--since, --until) are not counted;
- ``--save`` writes counts without samples;
- (review 196b28b3) a timestamp-less ``api-request-blob`` copy of earlier blocks is
  not counted again; the window compares instants, not text; every gate pattern
  matches a deny text that really exists in backend/core.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import bash_misuse_report as bmr  # noqa: E402

TS = "2026-10-20T10:00:00.000Z"


def _use(i, cmd, bg=False, ts=TS):
    inp = {"command": cmd, **({"run_in_background": True} if bg else {})}
    return {"type": "assistant", "timestamp": ts,
            "message": {"content": [{"type": "tool_use", "id": i, "name": "Bash", "input": inp}]}}


def _res(i, text, err=False, ts=TS):
    return {"type": "user", "timestamp": ts, "message": {"content": [
        {"type": "tool_result", "tool_use_id": i, "content": text, "is_error": err}]}}


def _write(tmp_path, name, lines):
    p = tmp_path / "proj" / f"{name}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def _scan(tmp_path, **kw):
    return bmr.scan(tmp_path, datetime(2026, 10, 1, tzinfo=timezone.utc), **kw)


def test_classify_gate():
    assert bmr.classify_gate("PreToolUse:Bash hook error: DENY (run owner gate): x") == "run_owner_gate"
    assert bmr.classify_gate("PreToolUse:Bash hook error: something new") == "other_hook"
    assert bmr.classify_gate("Exit code 1\nTraceback ...") is None


def test_counts(tmp_path):
    deny = "PreToolUse:Bash hook error: DENY (run owner gate): run it on its own"
    lines = []
    for n in range(3):
        lines += [_use(f"o{n}", "cd x && artifact_cli.py run-commit"), _res(f"o{n}", deny, err=True)]
    lines += [_use("b1", "perl -e 'alarm 100; exec @ARGV' pytest x"),
              _res("b1", "Command running in background with ID: abc. Output is being written to: /t")]
    lines += [_use("b2", "npm run dev", bg=True),
              _res("b2", "Command running in background with ID: def.")]
    lines += [_use("r1", "python3 -c \"import json;d=json.load(open('/w/.artifacts/runs/run_1234abcd/run.json'));print(d['status'])\""),
              _res("r1", "running")]
    lines += [_use("w1", "python3 - <<'EOF'\nimport json\np='/w/.artifacts/runs/run_1234abcd/run.json'\n"
                         "d=json.load(open(p)); d['status']='x'; json.dump(d, open(p,'w'))\nEOF"),
              _res("w1", "")]
    lines += [_use("g1", "python backend/scripts/artifact_cli.py run-get --project P --run-id run_1 --brief"),
              _res("g1", "{}")]
    lines += [_use("e1", "ls /nope"), _res("e1", "Exit code 1\nls: /nope: No such file", err=True)]
    _write(tmp_path, "s1", lines)
    r = _scan(tmp_path)
    assert r["bash"] == {"calls": 9, "errors": 4, "error_rate": 0.4444}
    assert r["gates"] == {"run_owner_gate": {"denials": 3, "repeat_sessions": 1}}
    assert r["auto_backgrounded"] == 1
    assert r["inline_state_reads"] == 2
    assert r["run_json_writes"] == 1


def test_window_and_save(tmp_path):
    _write(tmp_path, "s1", [_use("a", "ls", ts="2026-10-20T10:00:00Z"), _res("a", "ok", ts="2026-10-20T10:00:00Z"),
                            _use("b", "ls", ts="2026-10-22T10:00:00Z"), _res("b", "ok", ts="2026-10-22T10:00:00Z")])
    r = bmr.scan(tmp_path, datetime(2026, 10, 19, tzinfo=timezone.utc), 0,
                 datetime(2026, 10, 21, tzinfo=timezone.utc))
    assert r["bash"]["calls"] == 1
    out = tmp_path / "m" / "base.json"
    assert bmr.main(["--root", str(tmp_path), "--since", "2026-10-01", "--samples", "2", "--save", str(out)]) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert "samples" not in saved and saved["bash"]["calls"] == 2


def test_request_blob_copies_are_not_counted(tmp_path):
    pair = [_use("a", "ls"), _res("a", "PreToolUse:Bash hook error: has a SYNTAX ERROR", err=True)]
    blob = {"type": "api-request-blob", "message": {"content": [
        pair[0]["message"]["content"][0], pair[1]["message"]["content"][0]]}}
    _write(tmp_path, "s1", pair + [blob, blob])
    r = _scan(tmp_path)
    assert r["bash"]["calls"] == 1 and r["bash"]["errors"] == 1
    assert r["gates"]["bash_syntax_guard"]["denials"] == 1


def test_window_compares_instants(tmp_path):
    ts = "2026-10-20T10:00:00+08:00"   # 02:00Z
    _write(tmp_path, "s1", [_use("a", "ls", ts=ts), _res("a", "ok", ts=ts)])
    r = bmr.scan(tmp_path, datetime(2026, 10, 20, 2, 30, tzinfo=timezone.utc))
    assert r["bash"]["calls"] == 0 and r["transcripts"] == 0


def test_more_inline_read_forms(tmp_path):
    a = "python3 -c \"import json,pathlib; json.loads(pathlib.Path('/x/.artifacts/runs/run_deadbeef/run.json').read_text())\""
    b = "python3 <<'PY'\nimport json\nfrom pathlib import Path\njson.loads(Path('/x/evidence/r.json').read_text())\nPY"
    c = "python3 - <<'PY'\nimport json\nfrom pathlib import Path\np = Path(R) / 'run.json'\nd = json.loads(p.read_text()); p.write_text(json.dumps(d))\nPY"
    _write(tmp_path, "s1", [_use("a", a), _res("a", ""), _use("b", b), _res("b", ""),
                            _use("c", c), _res("c", "")])
    r = _scan(tmp_path)
    assert r["inline_state_reads"] == 3 and r["run_json_writes"] == 1


def test_every_gate_pattern_matches_a_real_deny_text():
    core = Path(__file__).resolve().parent.parent / "core"
    source = "".join(f.read_text(encoding="utf-8") for f in core.glob("*.py"))
    samples = {
        "run_owner_gate": "DENY (run owner gate)",
        "worktree_write_gate": "DENY (worktree gate)",
        "audit_marker_write_gate": "this writes, moves or deletes files in",
        "session_link_gate": "this command removes or overrides",
        "run_amend_gate": "DENY (git commit --amend)",
        "active_run_commit_gate": "git commit → DENY: this commit touches file(s) owned",
        "bash_syntax_guard": "has a SYNTAX ERROR",
        "background_command_guard": "Background execution is disabled for this command",
        "dangerous_command_gate": "Matches dangerous command pattern",
        "expert_budget_gate": "Tool budget used (",
    }
    assert set(samples) == {n for n, _ in bmr.GATE_PATTERNS}
    for gate, text in samples.items():
        assert text in source, f"{gate}: deny text no longer in backend/core: {text!r}"
        probe = text.replace("Tool budget used (", "Tool budget used (24 calls)")
        assert bmr.classify_gate(probe) == gate, gate
