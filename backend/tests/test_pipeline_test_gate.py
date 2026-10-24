"""Tests for create_pipeline_test_gate (design docs/design/pipeline-speed-systematic-fix.md
§4-B3) and the run-amend gate. (direct_commit_gate and its tests were removed by design
2026-10-24-swarmai-usable-by-anyone §3.4.)

Methodology: each gate is driven through its real async PreToolUse callable. The
active-run state (contract C6) is real run.json files written under a temp
workspace (SWARM_WORKSPACE via monkeypatch; conftest strips SWARM_* vars). Fail-open paths are forced by pointing at a
non-repo or by monkeypatching a helper to fail.

Invariants:
  • C6: a run is "this session's" only if status is active / reflecting AND session_id equals
    the hook's app session id AND it was active within _RUN_ACTIVE_WINDOW_SECONDS;
    paused / other-session / unowned (no session_id) / idle runs are not, and an
    unowned run changes nothing for any session (no global fail-open).
  • pipeline_test_gate: inactive → approve everything. Active → deny each family
    (pytest, vitest, tsc, eslint, vite build, npm build/test/lint/typecheck/check,
    python -c / stdin code running pytest, coverage run, make test*, bash -o/--long
    -c wrappers, node entry points, pnpm / yarn / bun test|run|exec|dlx / bunx,
    the `pnpm exec|dlx -c` / `yarn dlx -c` shell mode (string classified like bash -c),
    git worktree add (a copy of the repo is not a hit: it runs no test),
    kill/pkill on pytest, python -X faulthandler); approve read-only ps/pgrep listings, pytest
    --version/--help, pytest words that are only quoted / grep patterns / commit /
    echo text, pnpm|yarn|bun install / dev, and the pipeline_test.py runner. A
    leading cd / pushd across && / ; moves the directory a bare make resolves its
    default goal in (real tmp Makefiles); an unknown directory (cd $X, cd -, ||, |,
    a closed subshell) never denies a bare make but still denies explicit test
    targets. The deny text
    carries the runner command and the run-update --status abandoned escape, filled
    with the real project and run id, and calls the force env operator-only.
"""
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from core import security_hooks as sh

REPO = sh._SOURCE_REPO_ROOT
SID = "app-session-1"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Temp workspace for run.json files; force switches cleared."""
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE", str(root))
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(sh, "_ACTIVE_RUN_GLOB_ROOT", str(root))
    for k in ("SWARM_GATE_FORCE",):
        monkeypatch.delenv(k, raising=False)
    return root


_NO_SID = object()


def _seed(ws, project="SwarmAI", run_id="run_abc123", status="active",
          session_id=SID, age_hours=0.0):
    """A real Pipeline v1 run (protected contract + signed events) owned by
    *session_id*: the gates read the SIGNED owner/status, never run.json's own fields
    (B-class re-check). *status* "paused" is a recorded user stop; an owner of None /
    "" / _NO_SID is a run.json with no contract (not a run). *age_hours* backdates its
    newest signed event. Returns the run's run.json."""
    from core.run_protocol import engine as engine_mod
    d = ws / "Projects" / project / ".artifacts" / "runs" / run_id
    if session_id in (None, "", _NO_SID):
        d.mkdir(parents=True, exist_ok=True)
        payload = {"id": run_id, "project": project, "status": status}
        if session_id is not _NO_SID:
            payload["session_id"] = session_id
        (d / "run.json").write_text(json.dumps(payload))
        return d / "run.json"
    data = Path(os.environ["SWARM_DATA_DIR"])
    real_now = engine_mod._now
    if age_hours:
        from datetime import datetime, timedelta, timezone
        then = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
        engine_mod._now = lambda: then
    try:
        engine = engine_mod.RunEngine.create(
            data_dir=data, workspace=ws, project=project, profile="direct",
            requirement="seeded", session_id=session_id, repos={}, mode="shared",
            run_id=run_id)
        if status == "paused":
            engine.record_stop("user_stop", reason="seeded")
        elif status in ("abandoned", "completed"):
            engine.abandon(reason="seeded")      # a terminal run
    finally:
        engine_mod._now = real_now
    return engine.run_dir / "run.json"


def _call(gate, command, cwd=REPO, tool_name="Bash", timeout=None):
    ti = {"command": command} if timeout is None else {"command": command, "timeout": timeout}
    return asyncio.run(gate({"tool_name": tool_name, "tool_input": ti, "cwd": cwd}, None, None))


def _is_deny(res):
    return res.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _reason(res):
    return res["hookSpecificOutput"]["permissionDecisionReason"]


def _is_note(res):
    """G1 (pipeline-gate-expert-simplification): a direct test command is approved with
    a note naming the runner, never denied."""
    return res.get("decision") == "approve" and "pipeline_test.py" in (res.get("additionalContext") or "")


def _note(res):
    return res["additionalContext"]


# ── C6 active-run detection ──────────────────────────────────────────────────
class TestSessionRunState:
    def test_running_run_of_this_session_matches(self, ws):
        _seed(ws, project="SwarmAI", run_id="run_abc123")
        assert sh._session_run_state(SID) == {"project": "SwarmAI", "id": "run_abc123"}

    def test_other_session_does_not_match(self, ws):
        _seed(ws, session_id="someone-else")
        assert sh._session_run_state(SID) is None

    def test_paused_run_does_not_match(self, ws):
        _seed(ws, status="paused")
        assert sh._session_run_state(SID) is None

    def test_unowned_running_run_is_not_this_sessions_run(self, ws):
        # H1: a run without session_id is nobody's run — no global flag, no match.
        _seed(ws, session_id=_NO_SID)
        _seed(ws, run_id="run_null", session_id=None)
        assert sh._session_run_state(SID) is None

    def test_unowned_run_does_not_hide_own_run(self, ws):
        _seed(ws, run_id="run_legacy", session_id=_NO_SID)
        _seed(ws, run_id="run_mine")
        assert sh._session_run_state(SID)["id"] == "run_mine"

    def test_empty_session_id_never_matches(self, ws):
        _seed(ws, session_id="")
        assert sh._session_run_state("") is None

    def test_run_idle_beyond_active_window_is_ignored(self, ws):
        # M3: an abandoned run left at status=running stops gating after the window.
        hours = sh._RUN_ACTIVE_WINDOW_SECONDS / 3600
        _seed(ws, age_hours=hours + 0.5)
        assert sh._session_run_state(SID) is None

    def test_run_inside_active_window_matches(self, ws):
        hours = sh._RUN_ACTIVE_WINDOW_SECONDS / 3600
        _seed(ws, age_hours=hours - 0.5)
        assert sh._session_run_state(SID)["id"] == "run_abc123"

    def test_recent_updated_at_counts_as_activity(self, ws):
        from datetime import datetime, timezone
        f = _seed(ws)
        data = json.loads(f.read_text())
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        f.write_text(json.dumps(data))
        assert sh._run_activity_time(str(f), data) >= os.path.getmtime(f) - 1
        assert sh._session_run_state(SID)["id"] == "run_abc123"

    def test_corrupt_run_json_is_skipped(self, ws):
        bad = ws / "Projects" / "P" / ".artifacts" / "runs" / "run_bad"
        bad.mkdir(parents=True)
        (bad / "run.json").write_text("{ nope")
        _seed(ws, run_id="run_ok")
        assert sh._session_run_state(SID)["id"] == "run_ok"

    def test_id_and_project_are_the_run_directorys_never_run_json(self, ws):
        f = _seed(ws, project="Proj", run_id="run_dir")
        f.write_text(json.dumps({**json.loads(f.read_text()), "id": "run_x", "project": "X"}))
        assert sh._session_run_state(SID) == {"project": "Proj", "id": "run_dir"}

    def test_a_forged_run_json_is_not_this_sessions_run(self, ws):
        """B-class re-check: status / owner of a run.json with no contract do not
        make it a run (a forged one lifted the run gates)."""
        d = ws / "Projects" / "Proj" / ".artifacts" / "runs" / "run_forged"
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"status": "active", "session_id": SID}))
        assert sh._session_run_state(SID) is None


# ── pipeline_test_gate ───────────────────────────────────────────────────────
NOTED_COMMANDS = [
    # direct test / build
    "pytest tests/test_x.py",
    "cd backend && .venv/bin/python -m pytest tests/test_x.py -q",
    "perl -e 'alarm 90; exec @ARGV' python -m pytest tests/test_a.py > /tmp/t.txt 2>&1",
    "PYTHONPATH=. timeout 60 python3.12 -m pytest -x",
    "echo hi; pytest -q",
    "bash -c 'pytest tests/test_x.py'",
    "npx vitest run src/a.test.ts",
    "cd desktop && ./node_modules/.bin/vitest run",
    "cd desktop && npx tsc --noEmit",
    "tsc -p desktop",
    "npx eslint src",
    "npx vite build",
    "npm run build",
    "npm run build:all",
    "npm --prefix desktop run build",
    "npm test -- --run",
    "npm run test",
    # M1: python inline / stdin code that runs pytest
    "perl -e 'alarm 90; exec @ARGV' .venv/bin/python -c "
    "\"import pytest,sys; sys.exit(pytest.main(['tests/test_x.py']))\"",
    "python3 -uc 'import pytest; pytest.main()'",
    "python -c 'import subprocess; subprocess.run([\"npx\", \"vitest\", \"run\"])'",
    # M1: coverage wrappers
    "coverage run -m pytest tests/test_x.py",
    "perl -e 'alarm 90; exec @ARGV' .venv/bin/python -m coverage run -m pytest tests/test_x.py",
    "coverage run --source backend --branch -m pytest",
    ".venv/bin/coverage run .venv/bin/pytest tests/test_x.py",
    # M1: make test targets
    "make test",
    "make -C backend test",
    "perl -e 'alarm 90; exec @ARGV' make test-file F=tests/test_x.py",
    "make -j4 -C backend test-all PROFILE=dev",
    "make --directory=backend test-lf",
    # residual 1: a leading cd / pushd moves make's directory (backend/Makefile → test)
    "cd backend && make",
    "cd backend; make",
    "cd backend; make test",
    "pushd backend && make",
    "cd ./backend/../backend && make -j4",
    "cd desktop && cd ../backend && make",
    "cd backend && bash -c 'make'",
    "(cd backend && make)",
    "cd $UNKNOWN && make test",
    "cd backend || make check",
    # residual 2: pnpm / yarn / bun
    "pnpm test",
    "pnpm t",
    "pnpm run test",
    "pnpm run lint",
    "pnpm run typecheck",
    "pnpm run check",
    "pnpm run build:all",
    "pnpm --filter web run test:run",
    "pnpm -C desktop lint",
    "pnpm -r test",
    "pnpm recursive run build",
    "pnpm vitest run",
    "pnpm vite build",
    "pnpm exec vitest run src/a.test.ts",
    "pnpm exec eslint src",
    "pnpm --dir desktop exec tsc --noEmit",
    "pnpm dlx vitest",
    "pnpx vitest run",
    "yarn test",
    "yarn lint",
    "yarn vitest",
    "yarn run typecheck",
    "yarn run build",
    "yarn --cwd desktop test",
    "yarn exec tsc",
    "yarn dlx eslint src",
    "yarn workspace web test",
    "yarn workspaces foreach -A run lint",
    "bun test",
    "bun run test",
    "bun run lint",
    "bun run --bun vitest",
    "bun build ./src/index.ts",
    "bunx vitest",
    "bunx eslint src",
    "bunx tsc --noEmit",
    "bun x vitest run",
    "cd ../desktop && perl -e 'alarm 90; exec @ARGV' pnpm test",
    # shell mode: `pnpm exec|dlx -c` / `yarn dlx -c` strings are classified like bash -c
    "pnpm dlx -c 'vitest run'",
    "pnpm exec -c 'tsc --noEmit && echo ok'",
    "pnpm exec --shell-mode 'eslint src'",
    "pnpm --filter web dlx -p vitest -c 'vitest run src/a.test.ts'",
    "yarn dlx -c 'pytest -q'",
    "cd desktop && pnpm dlx -c 'npm run build'",
    # M1: shell -c behind -o / --long options
    "bash -o pipefail -c \"perl -e 'alarm 90; exec @ARGV' .venv/bin/python -m pytest tests/test_x.py\"",
    "bash --norc -c 'pytest tests/test_x.py'",
    "sh -e -c 'pytest -q'",
    "bash -eo pipefail -c 'pytest -q'",
    "bash +o histexpand --noprofile -c 'npx vitest run'",
    # M1: npm lint / typecheck scripts and node entry points
    "cd ../desktop && npm run lint",
    "npm run lint:contract",
    "npm run typecheck",
    "npm run test:run",
    "npm run check",
    "node node_modules/.bin/vitest run src/a.test.ts",
    "node ./node_modules/eslint/bin/eslint.js src",
    "node node_modules/typescript/bin/tsc --noEmit",
    "cd ../desktop && npx eslint src",
    # a registered worktree outside the run
    "git -C . worktree add /tmp/base HEAD",
    # manual hang triage that acts on a test process
    "pkill -f pytest",
    "kill $(pgrep -f pytest)",
    "python -X faulthandler -c 'import x'",
    "python3 -Xfaulthandler scripts/foo.py",
]

APPROVED_COMMANDS = [
    'grep -rn "pytest" backend/core',
    "rg -n 'npm run build' docs",
    'echo "pytest tests"',
    'git commit -m "fix pytest gate; run vitest after npm run build"',
    "ls backend/tests | grep test_pytest",
    "cp -r /tmp/a /tmp/b",
    # copies run no test: the test run on a copy is what is gated
    "git archive HEAD -o /tmp/base.tar",
    "rsync -a backend/ /tmp/base",
    "cp -r backend /tmp/base",
    "cp -a " + str(REPO) + " /tmp/base",
    # an edit script that only WRITES text naming the runner (pathlib / text modules)
    "python3 -I - <<'EOF'\nfrom pathlib import Path\nimport re, json\n"
    "p = Path('x.md'); p.write_text(p.read_text().replace('pytest -q', 'pytest -x'))\nEOF",
    "python3 -c \"import pathlib; pathlib.Path('a.md').write_text('run pytest later')\"",
    "cp backend/core/x.py /tmp/x.py",
    "ps aux | grep node",
    # a read-only listing of test processes changes nothing
    "ps aux | grep pytest",
    "pgrep -f pytest",
    'ps -Ao pid,ppid,pcpu,etime,args | grep -E "pytest|pipeline_test" | grep -v grep',
    "kill 12345",
    "npm install",
    "npm run dev",
    "git worktree list",
    "git status",
    "python backend/scripts/artifact_cli.py stage-start --stage build",
    # L4: info-only pytest and quoted mentions
    "pytest --version",
    "pytest --help",
    ".venv/bin/python -m pytest --version",
    'kill $PID; echo "pytest finished"',
    "kill 12345; echo pytest done",
    # inline / stdin python that does not run tests
    "python -c 'print(1)'",
    "python --version",
    "echo 'print(1)' | python3",
    "python - <<'EOF'\nprint('hello')\nEOF",
    # G1: stdin code is not classified (it was almost always an edit script)
    "python - <<'EOF'\nimport pytest\npytest.main(['tests/test_x.py'])\nEOF",
    "echo 'import pytest; pytest.main()' | python3",
    "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['pytest', 'tests/x.py'])\nEOF",
    # shells, make and npm targets that are not tests
    "bash -o pipefail -c 'git status | cat'",
    "make help",
    "make -C backend dev",
    "npm run dev",
    "npm ci",
    "node scripts/build-icons.js",
    "node -e 'console.log(1)'",
    # residual 1: bare make in an unknown / non-test directory is not a hit
    "cd $UNKNOWN && make",
    "cd - && make",
    "popd && make",
    "cd backend || make",
    "cd backend | make",
    "(cd backend); make",
    "cd backend && make help",
    "cd backend && ls",
    # residual 2: installs, dev servers and other scripts
    "pnpm install",
    "pnpm i",
    "pnpm add -D vitest",
    "pnpm dev",
    "pnpm run dev",
    "pnpm exec prettier --check .",
    "yarn",
    "yarn install",
    "yarn add eslint",
    "yarn dev",
    "yarn workspace web dev",
    "bun install",
    "bun add vitest",
    "bun run dev",
    "bun index.ts",
    "bunx prettier --write .",
    # shell mode whose string runs no test / build
    "pnpm dlx -c 'echo hi'",
    "pnpm exec -c 'prettier --check . | cat'",
    "yarn dlx -c 'ls -la'",
]

RUNNER = ("perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python "
          "backend/scripts/pipeline_test.py --project SwarmAI --run-id run_abc123")


class TestPipelineTestGateActive:
    @pytest.mark.parametrize("command", NOTED_COMMANDS)
    def test_notes_each_family(self, ws, command):
        _seed(ws)
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}), command)
        assert _is_note(res), command
        assert "not count as evidence" in _note(res)

    @pytest.mark.parametrize("command", APPROVED_COMMANDS)
    def test_approves_non_test_commands_and_mentions(self, ws, command):
        _seed(ws)
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}), command)
        assert res == {"decision": "approve"}, command

    @pytest.mark.parametrize("command", [
        RUNNER + " --mode regression",
        RUNNER + " --mode tdd --files backend/tests/test_pytest_command_guard.py desktop/src/a.test.ts",
        "cd /tmp && " + RUNNER + " --mode verify",
    ])
    def test_runner_command_is_approved(self, ws, command):
        _seed(ws)
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}), command)
        assert res == {"decision": "approve"}

    def test_runner_plus_raw_pytest_gets_the_note(self, ws):
        _seed(ws)
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}),
                    RUNNER + " --mode verify && pytest tests/test_x.py")
        assert _is_note(res)

    def test_bare_make_uses_the_makefile_default_goal(self, ws):
        # backend/Makefile's first target is `test`, so a bare `make` there runs tests.
        _seed(ws)
        gate = sh.create_pipeline_test_gate({"sdk_session_id": SID})
        assert _is_note(_call(gate, "make", cwd=os.path.join(REPO, "backend")))
        assert _is_note(_call(gate, "make -C backend", cwd=REPO))

    def test_bare_make_with_non_test_default_goal_is_approved(self, ws, tmp_path):
        _seed(ws)
        (tmp_path / "Makefile").write_text(".PHONY: all\nall:\n\techo hi\ntest:\n\tpytest\n")
        gate = sh.create_pipeline_test_gate({"sdk_session_id": SID})
        assert _call(gate, "make", cwd=str(tmp_path)) == {"decision": "approve"}
        assert _is_note(_call(gate, "make test", cwd=str(tmp_path)))

    def test_cd_chain_resolves_the_makefile_directory(self, ws, tmp_path, monkeypatch):
        # sub/ defaults to `check` (gated), other/ to `all` (not gated); the hook cwd
        # (tmp_path) has no Makefile, so only cd tracking can decide.
        _seed(ws)
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "Makefile").write_text("check:\n\tpytest\nall:\n\techo\n")
        (tmp_path / "other").mkdir()
        (tmp_path / "other" / "Makefile").write_text("all:\n\techo hi\ntest:\n\tpytest\n")
        monkeypatch.setenv("HOME", str(tmp_path))
        gate = sh.create_pipeline_test_gate({"sdk_session_id": SID})
        here = str(tmp_path)
        for cmd in ["cd sub && make", "cd sub; make", "pushd sub && make",
                    "cd other && cd ../sub && make", "cd " + here + "/sub && make",
                    "cd ~/sub && make", "cd other; (cd ../sub && make)",
                    "cd sub && bash -c 'make -j2'"]:
            assert _is_note(_call(gate, cmd, cwd=here)), cmd
        for cmd in ["make", "cd other && make", "cd sub/../other && make",
                    "(cd sub) && make", "cd sub | make", "cd sub || make",
                    "cd $D && make", "pushd -n sub && make",
                    "(cd sub && echo) ; make", "cd sub && make all"]:
            assert _call(gate, cmd, cwd=here) == {"decision": "approve"}, cmd
        # an explicit gated target is denied wherever make runs, known dir or not
        for cmd in ["cd other && make test", "cd $D && make test", "cd sub || make check"]:
            assert _is_note(_call(gate, cmd, cwd=here)), cmd

    def test_non_bash_tool_is_approved(self, ws):
        _seed(ws)
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}), "pytest", tool_name="Read")
        assert res == {"decision": "approve"}


class TestPipelineTestGateInactive:
    @pytest.mark.parametrize("seed_kwargs", [
        None,                              # no run at all
        {"session_id": "someone-else"},    # another session's run
        {"status": "paused"},              # paused, not running
        {"session_id": _NO_SID},           # old run without session_id
        {"status": "completed"},           # terminal
        {"status": "abandoned"},           # closed via run-update --status abandoned
        {"age_hours": 4},                  # running but idle beyond the active window
    ])
    def test_inactive_session_approves_everything(self, ws, seed_kwargs):
        if seed_kwargs is not None:
            _seed(ws, **seed_kwargs)
        gate = sh.create_pipeline_test_gate({"sdk_session_id": SID})
        for command in ("pytest tests/test_x.py", "npm run build", "pkill -f pytest"):
            assert _call(gate, command) == {"decision": "approve"}, (seed_kwargs, command)

    def test_missing_session_id_in_context_approves(self, ws):
        _seed(ws)
        gate = sh.create_pipeline_test_gate({})
        assert _call(gate, "pytest tests/test_x.py") == {"decision": "approve"}

    def test_scan_failure_fails_open(self, ws, monkeypatch):
        _seed(ws)

        def _boom(_sid):
            raise RuntimeError("scan broke")
        monkeypatch.setattr(sh, "_session_run_state", _boom)
        gate = sh.create_pipeline_test_gate({"sdk_session_id": SID})
        assert _call(gate, "pytest tests/test_x.py") == {"decision": "approve"}


class TestPipelineTestGateNoteText:
    def _deny(self, ws, command, cwd=REPO):
        _seed(ws)
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}), command, cwd=cwd)
        assert _is_note(res), command
        return _note(res)

    def test_pytest_with_files_suggests_tdd_with_those_files(self, ws):
        reason = self._deny(
            ws, "python -m pytest backend/tests/test_a.py::test_x backend/tests/test_b.py -q")
        assert (RUNNER + " --mode tdd --files backend/tests/test_a.py "
                "backend/tests/test_b.py") in reason

    def test_files_are_made_repo_relative_from_cwd(self, ws):
        reason = self._deny(ws, "pytest tests/test_a.py", cwd=os.path.join(REPO, "backend"))
        assert RUNNER + " --mode tdd --files backend/tests/test_a.py" in reason

    def test_at_most_five_files(self, ws):
        files = " ".join(f"backend/tests/test_{i}.py" for i in range(7))
        reason = self._deny(ws, "pytest " + files)
        assert "backend/tests/test_4.py" in reason
        assert "backend/tests/test_5.py" not in reason

    def test_vitest_files_also_suggest_tdd(self, ws):
        reason = self._deny(ws, "npx vitest run desktop/src/a.test.ts")
        assert RUNNER + " --mode tdd --files desktop/src/a.test.ts" in reason

    @pytest.mark.parametrize("command", ["npx tsc --noEmit", "pytest -q", "pkill -f pytest"])
    def test_without_files_suggests_verify(self, ws, command):
        reason = self._deny(ws, command)
        assert RUNNER + " --mode verify" in reason
        assert "--files" not in reason.split(RUNNER, 1)[1].split("\n", 1)[0]

    def test_real_project_and_run_id_are_filled_and_quoted(self, ws):
        _seed(ws, project="Agent DLC", run_id="run_9f")
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}), "pytest -q")
        assert "--project 'Agent DLC' --run-id run_9f --mode verify" in _note(res)


# ── git helpers (run_amend_gate tests) ─────────────────────────────────────
def _git(d, *args):
    # HOME is a sibling dir, NOT the repo: machine-level git tooling may write cache /
    # log files under $HOME, and `git add -A` would then stage them as source.
    home = d.parent / "home"
    home.mkdir(exist_ok=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(home), "PATH": os.environ.get("PATH", "")}
    subprocess.run(["git", "-C", str(d), *args], env=env, capture_output=True, check=True)


def _lines(n, tag="x"):
    return "".join(f"{tag}_{i} = {i}\n" for i in range(n))


def _make_repo(d):
    d.mkdir()
    _git(d, "init", "-q")
    (d / "a.py").write_text("base = 0\n")
    (d / "README.md").write_text("# readme\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "init")
    return d


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Temp git repo with one committed source file and one committed doc, standing
    in for the SwarmAI source repo (_SOURCE_REPO_ROOT points at it; M2 scope)."""
    d = _make_repo(tmp_path / "repo")
    monkeypatch.setattr(sh, "_SOURCE_REPO_ROOT", os.path.realpath(str(d)))
    return d



# ── registration ─────────────────────────────────────────────────────────────
def test_build_hooks_registers_both_gates_before_the_hitl_gate(monkeypatch):
    """L3: pipeline_test_gate and run_amend_gate precede dangerous_command_gate (no
    approve-then-deny). No existing gate is lost."""
    from core import hook_builder
    from core.permission_manager import permission_manager

    recorded = []
    original = hook_builder.HookRegistry.register

    def _spy(self, event, hook, name, matcher=None, no_timeout=False):
        # a compose_gates group stands for its members, in order
        for n in getattr(hook, "members", (name,)):
            recorded.append((event, n, matcher))
        return original(self, event, hook, name, matcher=matcher, no_timeout=no_timeout)

    monkeypatch.setattr(hook_builder.HookRegistry, "register", _spy)
    asyncio.run(hook_builder.build_hooks(
        agent_config={"id": "default"}, enable_skills=False, enable_mcp=False,
        resume_session_id="test-sess", session_context={"sdk_session_id": "test-sess"},
        permission_manager=permission_manager,
    ))
    names = [n for _e, n, _m in recorded]
    for existing in ("pre_tool_logger", "background_command_guard",
                     "bash_syntax_guard", "inclusive_term_guard", "design_lint_guard",
                     "image_read_dedup_guard", "dangerous_command_gate",
                     "active_run_commit_gate",
                     "ask_question_gate", "external_approval_gate", "governance_file_gate"):
        assert existing in names, existing
    assert names.index("pipeline_test_gate") < names.index("dangerous_command_gate")
    # run_amend_gate denies an amend before dangerous_command_gate could prompt for it
    assert names.index("run_amend_gate") < names.index("dangerous_command_gate")
    assert names.count("run_amend_gate") == 1
    assert names.count("pipeline_test_gate") == 1
    # session_link_gate sits right before pipeline_test_gate (test_session_link_gate.py)
    assert names.index("session_link_gate") == names.index("pipeline_test_gate") - 1
    by_name = {n: (e, m) for e, n, m in recorded}
    assert by_name["pipeline_test_gate"] == ("PreToolUse", "Bash")


# ── run_amend_gate ───────────────────────────────────────────────────────────
class TestRunAmendGate:
    """In an active run, `git commit --amend` is denied outright (no HITL prompt);
    outside a run, and for any other commit, the gate changes nothing."""

    def _gate(self, sid=SID):
        return sh.create_run_amend_gate({"sdk_session_id": sid})

    @pytest.mark.parametrize("cmd", [
        "git commit --amend -F /tmp/m.txt",
        "git commit --amend --no-edit",
        "cd /repo; test -z \"$(git diff --cached)\" && git -C /repo commit --amend -m x",
        "cat > /tmp/m.txt <<'EOF'\nfeat: x\nEOF\ngit commit --amend -F /tmp/m.txt 2>&1 | tail -3",
        "GIT_EDITOR=true git commit --amend",
        "git commit --amend=x 'unterminated",   # unparseable → fail closed
    ])
    def test_amend_in_active_run_is_denied_with_the_way_out(self, ws, cmd):
        _seed(ws, project="SwarmAI", run_id="run_amend1")
        res = _call(self._gate(), cmd)
        assert _is_deny(res)
        reason = _reason(res)
        assert "run_amend1" in reason
        assert "pipeline-v1 land --message" in reason and "run-commit" not in reason

    @pytest.mark.parametrize("cmd", [
        "git commit -m 'feat: x'",
        "git commit -m 'mention --amend in text'",
        "echo git commit --amend",
        "git log --format=%s -1",
        "python backend/scripts/artifact_cli.py run-commit --project SwarmAI --run-id r",
    ])
    def test_other_commands_pass_in_active_run(self, ws, cmd):
        _seed(ws)
        assert _call(self._gate(), cmd) == {"decision": "approve"}

    def test_no_run_other_session_or_no_session_pass(self, ws):
        cmd = "git commit --amend --no-edit"
        assert _call(self._gate(), cmd) == {"decision": "approve"}          # no run
        _seed(ws, session_id="someone-else")
        assert _call(self._gate(), cmd) == {"decision": "approve"}          # not ours
        _seed(ws, run_id="run_mine")
        assert _call(self._gate(sid=""), cmd) == {"decision": "approve"}    # no session id
        assert _call(self._gate(), cmd, tool_name="Write") == {"decision": "approve"}

    def test_scan_failure_fails_open(self, ws, monkeypatch):
        _seed(ws)

        def boom(_sid):
            raise OSError("glob failed")

        monkeypatch.setattr(sh, "_session_run_state", boom)
        assert _call(self._gate(), "git commit --amend") == {"decision": "approve"}

    def test_history_rewrite_classification_unchanged(self):
        # the shared tokenizer refactor keeps _is_history_rewrite's verdicts
        assert sh._is_history_rewrite("git rebase -i HEAD~2")
        assert sh._is_history_rewrite("ls && git commit --amend")
        assert sh._is_history_rewrite("git reset --hard HEAD~1")
        assert sh._is_history_rewrite("git rebase 'unterminated")   # unparseable → regex
        assert not sh._is_history_rewrite("git reset -q -- a.py")
        assert not sh._is_history_rewrite("git commit -m x")
        assert not sh._is_history_rewrite("")


def test_source_repo_root_survives_the_packaged_daemon(tmp_path, monkeypatch):
    """In the packaged daemon this module's own path is inside the bundle, never the repo:
    the root then comes from the newest v1 run's protected contract (its repo_roots), so
    the direct-commit gate keeps applying instead of failing open. A run.json field is
    never read for it."""
    import json as _json
    import os as _os
    import subprocess as _sp
    from core import security_hooks as sh
    repo = tmp_path / "swarmai"
    (repo / "backend" / "core").mkdir(parents=True)
    (repo / "backend" / "core" / "security_hooks.py").write_text("")
    _sp.run(["git", "init", "-q", str(repo)], check=True)
    ws, data = tmp_path / "ws", tmp_path / "data"
    rd = ws / "Projects" / "P" / ".artifacts" / "runs" / "run_aaaabbbb"
    rd.mkdir(parents=True)
    (rd / "run.json").write_text(_json.dumps({"id": "run_aaaabbbb"}))
    audit = data / "state" / "pipeline_agent_audit" / "runs" / "run_aaaabbbb"
    audit.mkdir(parents=True)
    (audit / "contract.json").write_text(_json.dumps({
        "run_id": "run_aaaabbbb", "protocol_version": 1, "repo_roots": {"swarmai": str(repo)}}))
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setattr(sh, "_SOURCE_REPO_ROOT", str(tmp_path / "bundle"))
    monkeypatch.setattr(sh, "_SOURCE_ROOT_CACHE", [0.0, None])
    # a bundle with no usable source_repo.json (e.g. CI-built): the contract fallback
    from utils import source_repo
    monkeypatch.setattr(source_repo, "root", lambda: None)
    assert sh._source_repo_root() == _os.path.realpath(str(repo))
    # a contract root that is not a SwarmAI source tree is not used
    (repo / "backend" / "core" / "security_hooks.py").unlink()
    monkeypatch.setattr(sh, "_SOURCE_ROOT_CACHE", [0.0, None])
    assert sh._source_repo_root() is None


# ── One runner call per Bash call (run_bd60daff 2026-10-19) ───────────────────

_PY = "/repo/backend/.venv/bin/python"
_RT = "/repo/backend/scripts/pipeline_test.py --project SwarmAI --run-id run_x --mode verify"
# The loop actually issued in run_bd60daff (shape kept, paths shortened).
_LOOP_SEEN = (
    "cd /wt && for i in 1 2 3 4 5; do perl -e 'alarm 110; exec @ARGV' " + _PY + " " + _RT
    + " > /tmp/v3.json 2>/tmp/v3.err; c=$(python3 -c \"import json;d=json.load(open("
    "'/tmp/v3.json'));print(d.get('continue'))\"); echo \"iter $i: $c\"; case \"$c\" in "
    "True*) ;; *) break;; esac; done"
)
# A single call followed by a python -c report whose quoted body has `for ... in`.
_SINGLE_WITH_PY_FOR = (
    "perl -e 'alarm 110; exec @ARGV' " + _PY + " " + _RT + " > /tmp/v2.json; echo rc=$?; "
    "python3 -c \"\nimport json;d=json.load(open('/tmp/v2.json'))\n"
    "for f in d.get('failures',[])[:40]: print(f.get('nodeid'))\n\""
)


class TestRunnerLoopKind:
    @pytest.mark.parametrize("command,kind", [
        (_LOOP_SEEN, "loop"),
        ("while true; do " + _PY + " " + _RT + "; done", "loop"),
        ("until false; do python3 " + _RT + " || break; done", "loop"),
        (_PY + " " + _RT + " && " + _PY + " " + _RT, "repeat"),
        (_PY + " " + _RT + "; python " + _RT, "repeat"),
    ])
    def test_flags_multiple_runner_calls(self, command, kind):
        assert sh._runner_loop_kind(command) == kind

    @pytest.mark.parametrize("command", [
        _SINGLE_WITH_PY_FOR,
        "perl -e 'alarm 110; exec @ARGV' " + _PY + " " + _RT,
        # reading the script is not running it
        "grep -n desktop_tsc /repo/backend/scripts/pipeline_test.py; sed -n 1,9p /repo/backend/scripts/pipeline_test.py",
        "for f in a b; do echo $f; done; grep -n x /repo/backend/scripts/pipeline_test.py",
        "for f in a b; do echo $f; done",
        "echo 'unterminated",
    ])
    def test_single_or_no_runner_call_is_fine(self, command):
        assert sh._runner_loop_kind(command) is None


class TestPipelineTestGateRunnerLoop:
    def test_loop_denied_in_an_active_run(self, ws):
        _seed(ws)
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}), _LOOP_SEEN, timeout=600000)
        assert _is_deny(res)
        why = _reason(res)
        assert "runner loop" in why and "SAME command" in why and "continue: true" in why

    def test_loop_denied_without_a_run(self, ws):
        """IO-free and run-independent: the 120 s cap holds for every caller."""
        res = _call(sh.create_pipeline_test_gate({}), _LOOP_SEEN)
        assert _is_deny(res) and "runner loop" in _reason(res)

    def test_repeat_denied(self, ws):
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}),
                    _PY + " " + _RT + " && " + _PY + " " + _RT)
        assert _is_deny(res) and "runner repeat" in _reason(res)

    def test_single_runner_call_still_approved(self, ws):
        _seed(ws)
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}), _SINGLE_WITH_PY_FOR)
        assert not _is_deny(res)
