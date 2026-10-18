"""Tests for scripts/pipeline_run_profile.py — per-run speed profile from CLI transcripts.

Methodology: build a synthetic SwarmWS run dir and synthetic CLI transcripts
(JSONL records shaped like the real CLI's) in tmp_path, point SWARM_WORKSPACE /
CLAUDE_CONFIG_DIR at them, and assert on the computed metrics.

Key properties:
- only transcripts that issued a pipeline WRITE command for the run are used
  (a session that merely reads the run, a sub-agent file, or an out-of-window
  file is not)
- model vs tool time follows the documented gap/span rule; sidechain records
  and duplicate uuids are ignored; records outside the run window are ignored
- mechanical jobs, test repeats, CLI-source reads and gate denials are counted
- --write-metrics merges under `speed_profile` without clobbering other keys
"""
import json
import os

import pytest

from scripts import pipeline_run_profile as prof

T0 = "2026-10-17T10:00:00+00:00"
T_END = "2026-10-17T11:00:00+00:00"


def _at(sec: int) -> str:
    return f"2026-10-17T10:{sec // 60:02d}:{sec % 60:02d}+00:00"


def _use(uid, t, mid, name, inp, usage=None, sidechain=False, rec_uuid=None):
    msg = {"id": mid, "content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]}
    if usage:
        msg["usage"] = usage
    return {"type": "assistant", "timestamp": _at(t), "uuid": rec_uuid or f"a-{uid}",
            "isSidechain": sidechain, "message": msg}


def _res(uid, t, text="ok", error=False):
    return {"type": "user", "timestamp": _at(t), "uuid": f"r-{uid}",
            "message": {"content": [{"type": "tool_result", "tool_use_id": uid,
                                     "content": text, "is_error": error}]}}


def _write_jsonl(path, records, mtime=None):
    """Write a transcript and pin its mtime (default: the run's end) so the
    selection's mtime filter does not depend on the machine's clock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    t = prof._ts(T_END) if mtime is None else mtime
    os.utime(path, (t, t))
    return str(path)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A workspace with one run (run_abc) and an empty CLI config dir."""
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    run_dir = tmp_path / "ws" / "Projects" / "P" / ".artifacts" / "runs" / "run_abc"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "id": "run_abc", "project": "P", "profile": "bugfix", "status": "completed",
        "created_at": T0, "completed_at": T_END, "stages": []}), encoding="utf-8")
    return tmp_path, run_dir


class TestClassification:
    def test_categories(self):
        c = prof.classify_command
        assert c("Agent", {}) == "agent"
        assert c("Edit", {}) == "edit"
        assert c("Read", {"file_path": "x.py"}) == "read"
        assert c("Bash", {"command": "python backend/scripts/artifact_cli.py publish --x"}) == "pipeline-bookkeeping"
        assert c("Bash", {"command": "perl -e 'alarm 90; exec @ARGV' python -m pytest tests/t.py"}) == "test-build"
        assert c("Bash", {"command": "python3 - <<'EOF'\nprint(1)\nEOF"}) == "edit"
        assert c("Bash", {"command": "git status --short"}) == "git"
        assert c("Bash", {"command": "ls -la"}) == "read-probe"

    def test_mechanical_jobs(self):
        assert "pytest" in prof.mechanical_jobs("python -m pytest tests/t.py")
        assert "read_cli_source" in prof.mechanical_jobs("sed -n 1,40p backend/scripts/artifact_cli.py")
        assert "hang_triage" in prof.mechanical_jobs("sleep 60; cat out.txt")
        assert "change_scope" in prof.mechanical_jobs("git diff --name-only HEAD")
        # a git diff feeding a commit is a commit, not scoping
        assert "change_scope" not in prof.mechanical_jobs("git diff --cached && git commit -m x")
        assert prof.mechanical_jobs("ls -la") == []


class TestTranscriptSelection:
    def test_only_writers_inside_window(self, ws):
        tmp, _ = ws
        proj = tmp / "claude" / "projects" / "-ws"
        owner = _write_jsonl(proj / "owner.jsonl", [
            _use("u1", 10, "m1", "Bash",
                 {"command": "python artifact_cli.py run-update --project P --run-id run_abc --stage-json x"}),
        ])
        # reads the run only (run-get) — must not be selected
        _write_jsonl(proj / "reader.jsonl", [
            _use("u2", 10, "m2", "Bash", {"command": "python artifact_cli.py run-get --project P --run-id run_abc"}),
        ])
        # sub-agent transcripts that write — excluded (counted as parent's Agent
        # time); each exclusion rule is exercised on its own
        _write_jsonl(proj / "s" / "subagents" / "x.jsonl", [
            _use("u3", 10, "m3", "Bash", {"command": "artifact_cli.py publish --run-id run_abc"}),
        ])
        _write_jsonl(proj / "agent-y.jsonl", [
            _use("u6", 10, "m6", "Bash", {"command": "artifact_cli.py publish --run-id run_abc"}),
        ])
        # a writer whose file was last modified before the run started
        _write_jsonl(proj / "stale.jsonl", [
            _use("u7", 10, "m7", "Bash", {"command": "artifact_cli.py publish --run-id run_abc"}),
        ], mtime=prof._ts(T0) - 60)
        # a similar run id must not match by prefix
        _write_jsonl(proj / "other.jsonl", [
            _use("u4", 10, "m4", "Bash", {"command": "artifact_cli.py publish --run-id run_abcd"}),
        ])
        lo, hi = prof._ts(T0), prof._ts(T_END)
        assert prof.find_run_transcripts("run_abc", lo, hi) == [owner]

    def test_write_outside_window_is_ignored(self, ws):
        tmp, _ = ws
        late = {"type": "assistant", "timestamp": "2026-10-17T12:00:00+00:00", "uuid": "z",
                "message": {"id": "mz", "content": [{"type": "tool_use", "id": "uz", "name": "Bash",
                            "input": {"command": "artifact_cli.py advance --run-id run_abc"}}]}}
        _write_jsonl(tmp / "claude" / "projects" / "-ws" / "late.jsonl", [late])
        assert prof.find_run_transcripts("run_abc", prof._ts(T0), prof._ts(T_END)) == []


PYTEST = "perl -e 'alarm 90; exec @ARGV' python -m pytest tests/test_a.py -q > /tmp/t.txt 2>&1"


def _run_records():
    """Three main-thread responses plus noise the profiler must ignore."""
    two_calls = _use("u2", 100, "m2", "Read",
                     {"file_path": "/r/backend/scripts/artifact_cli.py"}, usage={"input_tokens": 5000})
    two_calls["message"]["content"].append(
        {"type": "tool_use", "id": "u3", "name": "Bash", "input": {"command": PYTEST}})
    first = _use("u1", 60, "m1", "Bash", {"command": PYTEST},
                 usage={"input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 40})
    return [
        first,
        dict(first),  # same uuid re-logged → deduped
        _res("u1", 90),
        two_calls,
        _res("u2", 105),
        _use("u9", 110, "m9", "Bash", {"command": "pytest x"}, sidechain=True),  # sub-agent noise
        _res("u3", 120),
        _use("u4", 130, "m3", "Bash", {"command": "python -m pytest tests/test_a.py | tail -5"}),
        _res("u4", 131, "PreToolUse:Bash hook error: pytest piped into tail/head is denied (R9)", error=True),
        {**_use("u8", 0, "m8", "Bash", {"command": "ls"}), "timestamp": "2026-10-17T12:00:00+00:00"},
    ]


class TestProfileRun:
    def test_metrics(self, ws):
        tmp, run_dir = ws
        path = _write_jsonl(tmp / "claude" / "projects" / "-ws" / "t.jsonl", _run_records())
        run_state = json.loads((run_dir / "run.json").read_text())
        p = prof.profile_run(run_state, [path])

        assert p["wall_minutes"] == 60.0
        assert p["api_responses"] == 3
        assert p["tool_calls"] == 4
        assert p["single_call_response_ratio"] == round(2 / 3, 3)
        # model gaps 60 + 10 + 10 s; tool spans 30 + 20 + 1 s
        assert p["model_minutes"] == round(80 / 60, 1)
        assert p["tool_minutes"] == round(51 / 60, 1)
        assert p["tool_errors"] == 1
        assert p["tests"]["calls"] == 3
        assert p["tests"]["max_repeat"] == {"target": "tests/test_a.py", "runs": 3}
        assert p["cli_source_reads"] == 1
        assert p["gate_denials"]["total"] == 1
        assert p["exact_duplicate_calls"] == 1  # u1 and u3 ran the identical command
        assert p["mechanical"]["total_calls"] == 3
        assert p["mechanical"]["bash_calls"] == 3
        assert p["context_tokens"]["peak"] == 5000
        assert p["output_tokens"] == 40

    def test_open_run_uses_updated_at(self, ws):
        _, run_dir = ws
        state = {"id": "run_abc", "created_at": T0, "completed_at": None,
                 "updated_at": "2026-10-17T10:30:00+00:00"}
        assert prof.run_window(state)[1] - prof.run_window(state)[0] == 1800
        assert prof.profile_run(state, [])["tool_calls"] == 0


class TestCli:
    def test_write_metrics_merges(self, ws, capsys):
        tmp, run_dir = ws
        (run_dir / "METRICS.json").write_text(json.dumps({"total_tokens": 7}), encoding="utf-8")
        _write_jsonl(tmp / "claude" / "projects" / "-ws" / "t.jsonl", _run_records() + [
            _use("u5", 140, "m5", "Bash",
                 {"command": "python artifact_cli.py advance --project P --state test --run-id run_abc"}),
        ])
        assert prof.main(["--run-id", "run_abc", "--project", "P", "--json", "--write-metrics"]) == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["transcripts"] == ["t.jsonl"]
        metrics = json.loads((run_dir / "METRICS.json").read_text())
        assert metrics["total_tokens"] == 7
        assert metrics["speed_profile"]["tool_calls"] == 5

    @pytest.mark.parametrize("run_id", ["../etc", "run_missing"])
    def test_bad_or_missing_run(self, ws, run_id):
        assert prof.main(["--run-id", run_id, "--project", "P"]) == 1


class TestReviewFindings:
    """Regressions for the adversarial-review findings on the first version."""

    @pytest.mark.parametrize("cmd,owns", [
        ("artifact_cli.py publish --run-id run_abc --stage x", True),
        ('artifact_cli.py run-update --run-id "run_abc" --stage-json @f', True),
        ("artifact_cli.py advance --run-id=run_abc --state test", True),
        ("artifact_cli.py publish --run-id run_abc-x", False),            # hyphen suffix
        ("artifact_cli.py run-get --run-id run_abc && artifact_cli.py publish --run-id run_b", False),
        ("artifact_cli.py run-get --run-id run_abc", False),              # read only
    ])
    def test_command_writes_run(self, cmd, owns):
        assert prof.command_writes_run(cmd, "run_abc") is owns

    def test_missing_result_does_not_double_count_and_streaming_is_model_time(self):
        uses = {
            "a": ("Bash", {"command": "ls"}, 10.0, "m1"),   # result never came back
            "b": ("Bash", {"command": "ls"}, 20.0, "m2"),
            "c": ("Edit", {}, 25.0, "m2"),                  # later block streamed 5 s
        }
        results = {"b": (40.0, False, 0, ""), "c": (30.0, False, 0, "")}
        responses = {"m1": {"uses": ["a"], "usage": None}, "m2": {"uses": ["b", "c"], "usage": None}}
        model_s, tool_s, gaps, _ = prof._time_split(uses, results, responses, 0.0)
        assert gaps == [10.0, 10.0]          # m2's gap starts at m1's last tool_use
        assert model_s == 10.0 + 10.0 + 5.0  # + the 20→25 s streaming inside m2
        assert tool_s == 15.0                # 25 → 40 s

    def test_cross_file_duplicates_count_once(self, ws):
        tmp, run_dir = ws
        recs = _run_records()
        a = _write_jsonl(tmp / "claude" / "projects" / "-ws" / "a.jsonl", recs)
        b = _write_jsonl(tmp / "claude" / "projects" / "-ws" / "b.jsonl", recs)  # resumed copy
        state = json.loads((run_dir / "run.json").read_text())
        single = prof.profile_run(state, [a])
        assert prof.profile_run(state, [a, b]) == {**single, "transcripts": ["a.jsonl", "b.jsonl"]}
        assert prof.profile_run(state, [a, a])["single_call_response_ratio"] == round(2 / 3, 3)

    def test_malformed_records_do_not_crash(self, ws):
        tmp, run_dir = ws
        bad = [
            {"type": "assistant", "timestamp": None, "uuid": "b1", "message": {"id": "x"}},
            {"type": "assistant", "timestamp": "not-a-date", "uuid": "b2",
             "message": {"content": "artifact_cli.py publish --run-id run_abc"}},
            {"type": "assistant", "timestamp": _at(5), "uuid": "b3",
             "message": "artifact_cli.py publish --run-id run_abc"},
            {"type": "assistant", "timestamp": _at(6), "uuid": "b4",
             "message": {"id": "m", "content": [{"type": "tool_use", "name": "Bash", "input": "run_abc"}]}},
        ]
        path = _write_jsonl(tmp / "claude" / "projects" / "-ws" / "bad.jsonl", bad + _run_records())
        assert prof.find_run_transcripts("run_abc", prof._ts(T0), prof._ts(T_END)) == []
        p = prof.profile_run(json.loads((run_dir / "run.json").read_text()), [path])
        assert p["tool_calls"] == 4

    def test_write_metrics_refuses_unreadable_file(self, ws):
        _, run_dir = ws
        metrics = run_dir / "METRICS.json"
        metrics.write_text('{"total_tokens": 7,', encoding="utf-8")  # half-written
        with pytest.raises(ValueError):
            prof.write_metrics(run_dir, {"x": 1})
        assert metrics.read_text(encoding="utf-8") == '{"total_tokens": 7,'
        assert not list(run_dir.glob(".METRICS.json.*.tmp"))


# ── sub-agent section: per-spawn cost vs its stated cap, by stage, vs the plan ──

def _sub(owner_path, name, start, end, prompt, tool_uses, description="d", shape="foreground"):
    """A sub-agent transcript beside its owner (<owner stem>/subagents/agent-*.jsonl)."""
    stem = owner_path[:-len(".jsonl")]
    recs = [{"type": "user", "timestamp": _at(start), "uuid": f"{name}-u", "isSidechain": True,
             "message": {"role": "user", "content": prompt}}]
    for i in range(tool_uses):
        recs.append(_use(f"{name}-t{i}", start + 1, f"{name}-m{i}", "Read", {"file_path": "/x"},
                         sidechain=True))
    recs.append({"type": "assistant", "timestamp": _at(end), "uuid": f"{name}-end", "isSidechain": True,
                 "message": {"id": f"{name}-mz", "content": [{"type": "text", "text": "done"}]}})
    path = os.path.join(stem, "subagents", f"agent-{name}.jsonl")
    _write_jsonl(__import__("pathlib").Path(path), recs)
    with open(path[:-len(".jsonl")] + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump({"description": description, "requestShape": shape}, fh)
    return path


GATE2 = "EXPERT-ID: {eid}\nAdversarially review this changeset.\nSCOPE BUDGET: At most 14\ntool calls."


class TestSubagents:
    def _state(self, run_dir):
        state = json.loads((run_dir / "run.json").read_text())
        state["stage_timing"] = {
            "evaluate": {"started_at": _at(0), "completed_at": _at(300)},
            "review": {"started_at": _at(600), "completed_at": _at(1200)},
            "deliver": {"started_at": _at(1800), "completed_at": _at(3000)},
        }
        state["dispatch_plan"] = {"review": ["spec-compliance", "code-quality"],
                                  "deliver": ["correctness", "security"]}
        return state

    def test_per_spawn_stage_cap_repeats_and_unplanned(self, ws):
        tmp, run_dir = ws
        owner = _write_jsonl(tmp / "claude" / "projects" / "-ws" / "t.jsonl", _run_records())
        _sub(owner, "g0", 10, 40, "You are a skeptic.\nat most 8 tool calls", 3, "Gate-0 skeptic",
             shape="background")
        _sub(owner, "rsec", 700, 900, GATE2.format(eid="security"), 6, "REVIEW security")
        _sub(owner, "dsec", 1900, 2100, GATE2.format(eid="security"), 18, "Gate-2 security")
        _sub(owner, "dcor", 1900, 2000, GATE2.format(eid="correctness"), 5, "Gate-2 correctness")
        _sub(owner, "gap", 1500, 1550, "no anchor here", 2, "between stages")
        p = prof.profile_run(self._state(run_dir), [owner])
        s = p["subagents"]
        assert s["count"] == 5
        by = {x["description"]: x for x in s["spawns"]}
        assert by["Gate-0 skeptic"]["stage"] == "evaluate" and by["Gate-0 skeptic"]["tool_cap"] == 8
        assert by["Gate-2 security"] == {**by["Gate-2 security"], "stage": "deliver", "expert_id": "security",
                                         "seconds": 200.0, "tool_calls": 18, "tool_cap": 14, "over_cap": True}
        assert by["Gate-2 correctness"]["over_cap"] is False
        assert by["between stages"]["stage"] == "unattributed" and by["between stages"]["expert_id"] is None
        assert s["over_cap"] == 1
        assert s["background"] == 1 and by["Gate-0 skeptic"]["background"] is True
        assert s["repeated_experts"] == {"security": ["review", "deliver"]}
        # REVIEW security was not in dispatch_plan.review; gate / unattributed spawns are not flagged
        assert [(u["stage"], u["expert_id"]) for u in s["unplanned"]] == [("review", "security")]
        # sub-agent time is its own section — the parent totals are unchanged
        assert p["tool_calls"] == 4

    def test_no_subagents_and_no_stage_timing(self, ws):
        tmp, run_dir = ws
        owner = _write_jsonl(tmp / "claude" / "projects" / "-ws" / "t.jsonl", _run_records())
        state = json.loads((run_dir / "run.json").read_text())
        assert prof.profile_run(state, [owner])["subagents"]["count"] == 0
        _sub(owner, "x", 100, 160, GATE2.format(eid="security"), 2)
        sp = prof.profile_run(state, [owner])["subagents"]["spawns"][0]
        assert sp["stage"] == "unattributed" and sp["seconds"] == 60.0

    def test_subagent_outside_window_is_ignored(self, ws):
        tmp, run_dir = ws
        owner = _write_jsonl(tmp / "claude" / "projects" / "-ws" / "t.jsonl", _run_records())
        stem = owner[:-len(".jsonl")]
        late = [{"type": "user", "timestamp": "2026-10-17T12:00:00+00:00", "uuid": "l",
                 "isSidechain": True, "message": {"role": "user", "content": "x"}}]
        _write_jsonl(__import__("pathlib").Path(stem) / "subagents" / "agent-late.jsonl", late)
        state = json.loads((run_dir / "run.json").read_text())
        assert prof.profile_run(state, [owner])["subagents"]["count"] == 0

    def test_render_includes_subagent_section(self, ws):
        tmp, run_dir = ws
        owner = _write_jsonl(tmp / "claude" / "projects" / "-ws" / "t.jsonl", _run_records())
        _sub(owner, "dsec", 1900, 2100, GATE2.format(eid="security"), 18, "Gate-2 security")
        text = prof._render(prof.profile_run(self._state(run_dir), [owner]))
        assert "sub-agents" in text and "Gate-2 security" in text and "over cap" in text


# ── post-refactor runs: stage-start/stage-complete, $R ids, recorded transcripts,
#    runner test executions (run_045b96b2) ─────────────────────────────────────
class TestPostRefactorRuns:
    @pytest.mark.parametrize("cmd,owns", [
        ("artifact_cli.py stage-complete --project P --run-id run_abc --stage plan --data @f", True),
        ("artifact_cli.py stage-start --project P --run-id run_abc --stage build", True),
        ("R=run_abc; python artifact_cli.py stage-complete --project P --run-id $R --stage x", True),
        ('R=run_abc; python artifact_cli.py run-update --run-id "$R" --profile bugfix', True),
        ("RUN='run_abc' && artifact_cli.py stage-start --run-id ${RUN} --stage test", True),
        ("R=run_abcd; artifact_cli.py stage-complete --run-id $R --stage x", False),   # other run
        ("R=run_other; echo run_abc; artifact_cli.py stage-complete --run-id $R", False),
        ("R=run_abc; artifact_cli.py run-get --run-id $R", False),                    # read only
        ("artifact_cli.py stage-complete --run-id $R --stage x", False),              # R unassigned
    ])
    def test_command_writes_run_new_verbs_and_shell_variable(self, cmd, owns):
        assert prof.command_writes_run(cmd, "run_abc") is owns

    def test_recorded_transcript_ids_are_used(self, ws):
        tmp, _ = ws
        sid = "11111111-2222-3333-4444-555555555555"
        # no pipeline write for the run in this file: only the recorded id selects it
        rec = _write_jsonl(tmp / "claude" / "projects" / "-ws" / f"{sid}.jsonl", [
            _use("u1", 10, "m1", "Bash", {"command": "ls"})])
        lo, hi = prof._ts(T0), prof._ts(T_END)
        assert prof.find_run_transcripts("run_abc", lo, hi) == []
        assert prof.find_run_transcripts("run_abc", lo, hi, recorded_ids=[sid, "../x"]) == [rec]

    def test_cli_uses_recorded_ids(self, ws, capsys):
        tmp, run_dir = ws
        sid = "11111111-2222-3333-4444-555555555555"
        _write_jsonl(tmp / "claude" / "projects" / "-ws" / f"{sid}.jsonl", [
            _use("u1", 10, "m1", "Bash", {"command": "ls"})])
        state = json.loads((run_dir / "run.json").read_text())
        state["transcripts"] = [sid]
        (run_dir / "run.json").write_text(json.dumps(state), encoding="utf-8")
        assert prof.main(["--run-id", "run_abc", "--project", "P", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["tool_calls"] == 1

    def test_runner_is_a_test_command(self):
        cmd = ("R=run_abc; perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python "
               "backend/scripts/pipeline_test.py --project P --run-id $R --mode tdd "
               "--files backend/tests/test_a.py > /tmp/o.json")
        assert prof.classify_command("Bash", {"command": cmd}) == "test-build"
        # reading or grepping the runner's source is not a test run
        for read in ("grep -n _PLUGIN_SRC backend/scripts/pipeline_test.py | head",
                     "sed -n 1,80p backend/scripts/pipeline_test.py"):
            assert prof.classify_command("Bash", {"command": read}) != "test-build"
            assert not prof._TEST_RE.search(read)

    def _evidence(self, run_dir):
        t = run_dir / "evidence" / "tests"
        t.mkdir(parents=True)
        def ev(name, **kw):
            (t / name).write_text(json.dumps({"schema": 1, "run_id": "run_abc", **kw}), encoding="utf-8")
        ev("tdd-20261017T100100.json", mode="tdd", duration_s=30)          # pre-index run
        ev("verify-20261017T100500.json", mode="verify", duration_s=200)   # pre-index run
        ev("verify-20261017T100600.json", mode="verify", duration_s=200,   # pre-index overlay
           rechecked_from="2026-10-17T10:05:00Z")
        ev("tdd-20261017T101000.json", mode="tdd", duration_s=12)          # indexed run
        ev("regression-20261017T101500.json", mode="regression", duration_s=9,
           rechecked_from=None)                                            # null key: still a run
        ev("latest-verify.json", mode="verify", duration_s=200)            # pointer, not a run
        (t / "tdd_index.json").write_text("{}", encoding="utf-8")
        (t / "run_index.json").write_text(json.dumps([
            {"kind": "run", "mode": "tdd", "evidence": "tdd-20261017T101000.json", "cached": False},
            {"kind": "run", "mode": "tdd", "evidence": "tdd-20261017T101100.json", "cached": True},  # pruned file
            {"kind": "run", "mode": "regression", "evidence": "regression-20261017T102000.json", "cached": False},
            {"kind": "overlay", "mode": "regression", "evidence": "regression-20261017T102100.json"},
        ]), encoding="utf-8")

    def test_runner_executions_from_index_plus_unindexed_files(self, ws):
        _, run_dir = ws
        self._evidence(run_dir)
        state = json.loads((run_dir / "run.json").read_text())
        runner = prof.profile_run(state, [], run_dir=run_dir)["tests"]["runner"]
        assert runner["executions"] == 6          # 3 indexed runs + 3 unindexed run files
        assert runner["cached"] == 1
        assert runner["by_mode"] == {"tdd": 3, "regression": 2, "verify": 1}
        assert runner["sources"] == {"run_index": 3, "evidence_files": 3}

    def test_runner_without_evidence(self, ws):
        _, run_dir = ws
        state = json.loads((run_dir / "run.json").read_text())
        assert prof.profile_run(state, [], run_dir=run_dir)["tests"]["runner"]["executions"] == 0
        assert prof.profile_run(state, [])["tests"]["runner"]["executions"] == 0

    def test_runner_commands_count_their_tool_time(self, ws):
        tmp, run_dir = ws
        self._evidence(run_dir)
        cmd = ("perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py "
               "--project P --run-id run_abc --mode tdd --files backend/tests/test_a.py")
        path = _write_jsonl(tmp / "claude" / "projects" / "-ws" / "t.jsonl", [
            _use("u1", 60, "m1", "Bash", {"command": cmd}), _res("u1", 120)])
        state = json.loads((run_dir / "run.json").read_text())
        tests = prof.profile_run(state, [path], run_dir=run_dir)["tests"]
        assert tests["by_kind"] == {"pipeline_test": 1}
        assert tests["exec_min"] == 1.0
        assert tests["calls"] == 6                 # runner executions, not the 1 command
        assert tests["sources"]["calls"] == "direct test commands + runner evidence"
        # no evidence: the runner commands seen are counted, and the output says so
        bare = prof.profile_run(state, [path])["tests"]
        assert bare["calls"] == 1
        assert bare["sources"]["calls"] == "direct test commands + runner commands in the transcript"
