"""Tests for create_pipeline_test_gate and create_direct_commit_gate (design
docs/design/pipeline-speed-systematic-fix.md §4-B3 and §4-G1).

Methodology: each gate is driven through its real async PreToolUse callable. The
active-run state (contract C6) is real run.json files written under a temp
workspace (SWARM_WORKSPACE via monkeypatch; conftest strips SWARM_* vars). The
direct-commit gate runs against real temp git repos (HOME pointed at the temp dir,
so no global git config or hooks apply). Fail-open paths are forced by pointing at a
non-repo or by monkeypatching a helper to fail.

Invariants:
  • C6: a run is "this session's" only if status == "running" AND session_id equals
    the hook's app session id AND it was active within _RUN_ACTIVE_WINDOW_SECONDS;
    paused / other-session / unowned (no session_id) / idle runs are not, and an
    unowned run changes nothing for any session (no global fail-open).
  • pipeline_test_gate: inactive → approve everything. Active → deny each family
    (pytest, vitest, tsc, eslint, vite build, npm build/test/lint/typecheck/check,
    python -c / stdin code running pytest, coverage run, make test*, bash -o/--long
    -c wrappers, node entry points, pnpm / yarn / bun test|run|exec|dlx / bunx,
    the `pnpm exec|dlx -c` / `yarn dlx -c` shell mode (string classified like bash -c),
    git archive, git worktree add, rsync / cp -r of the repo or backend/,
    ps/pgrep/kill/pkill on pytest, python -X faulthandler); approve pytest
    --version/--help, pytest words that are only quoted / grep patterns / commit /
    echo text, pnpm|yarn|bun install / dev, and the pipeline_test.py runner. A
    leading cd / pushd across && / ; moves the directory a bare make resolves its
    default goal in (real tmp Makefiles); an unknown directory (cd $X, cd -, ||, |,
    a closed subshell) never denies a bare make but still denies explicit test
    targets. The deny text
    carries the runner command and the run-update --status abandoned escape, filled
    with the real project and run id, and calls the force env operator-only.
  • direct_commit_gate: only for the SwarmAI source repo; no own run + >20 changed
    source lines (docs and lockfiles excluded) → deny with run-create --profile
    direct for the repo's project; unowned / other-session runs do not exempt;
    docs-only, lockfile-only, ≤20 lines, other repos, session in a run, git errors
    and force envs → approve.
"""
import asyncio
import json
import os
import subprocess
import time

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
    monkeypatch.setattr(sh, "_ACTIVE_RUN_GLOB_ROOT", str(root))
    for k in ("SWARM_TEST_GATE_FORCE", "SWARM_DIRECT_GATE_FORCE", "SWARM_ADVERSARIAL_GATE_FORCE"):
        monkeypatch.delenv(k, raising=False)
    return root


_NO_SID = object()


def _seed(ws, project="SwarmAI", run_id="run_abc123", status="running",
          session_id=SID, age_hours=0.0):
    """Write Projects/<project>/.artifacts/runs/<run_id>/run.json (C1 fields)."""
    d = ws / "Projects" / project / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    payload = {"id": run_id, "project": project, "status": status}
    if session_id is not _NO_SID:
        payload["session_id"] = session_id
    f = d / "run.json"
    f.write_text(json.dumps(payload))
    if age_hours:
        t = time.time() - age_hours * 3600
        os.utime(f, (t, t))
    return f


def _call(gate, command, cwd=REPO, tool_name="Bash"):
    return asyncio.run(gate(
        {"tool_name": tool_name, "tool_input": {"command": command}, "cwd": cwd}, None, None))


def _is_deny(res):
    return res.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _reason(res):
    return res["hookSpecificOutput"]["permissionDecisionReason"]


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

    def test_missing_id_and_project_fall_back_to_directory_names(self, ws):
        d = ws / "Projects" / "Proj" / ".artifacts" / "runs" / "run_dir"
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"status": "running", "session_id": SID}))
        assert sh._session_run_state(SID) == {"project": "Proj", "id": "run_dir"}


# ── pipeline_test_gate ───────────────────────────────────────────────────────
DENIED_COMMANDS = [
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
    "python - <<'EOF'\nimport pytest\npytest.main(['tests/test_x.py'])\nEOF",
    "echo 'import pytest; pytest.main()' | python3",
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
    # code-copy baselines
    "git archive HEAD -o /tmp/base.tar",
    "git -C . worktree add /tmp/base HEAD",
    "rsync -a backend/ /tmp/base",
    "cp -r backend /tmp/base",
    "cp -R . /tmp/base",
    "cp -a " + str(REPO) + " /tmp/base",
    # manual hang triage
    "ps aux | grep pytest",
    "pgrep -f pytest",
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
    "cp backend/core/x.py /tmp/x.py",
    "ps aux | grep node",
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
    @pytest.mark.parametrize("command", DENIED_COMMANDS)
    def test_denies_each_family(self, ws, command):
        _seed(ws)
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}), command)
        assert _is_deny(res), command
        assert "pipeline_test.py" in _reason(res)

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

    def test_runner_plus_raw_pytest_is_denied(self, ws):
        _seed(ws)
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}),
                    RUNNER + " --mode verify && pytest tests/test_x.py")
        assert _is_deny(res)

    def test_bare_make_uses_the_makefile_default_goal(self, ws):
        # backend/Makefile's first target is `test`, so a bare `make` there runs tests.
        _seed(ws)
        gate = sh.create_pipeline_test_gate({"sdk_session_id": SID})
        assert _is_deny(_call(gate, "make", cwd=os.path.join(REPO, "backend")))
        assert _is_deny(_call(gate, "make -C backend", cwd=REPO))

    def test_bare_make_with_non_test_default_goal_is_approved(self, ws, tmp_path):
        _seed(ws)
        (tmp_path / "Makefile").write_text(".PHONY: all\nall:\n\techo hi\ntest:\n\tpytest\n")
        gate = sh.create_pipeline_test_gate({"sdk_session_id": SID})
        assert _call(gate, "make", cwd=str(tmp_path)) == {"decision": "approve"}
        assert _is_deny(_call(gate, "make test", cwd=str(tmp_path)))

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
            assert _is_deny(_call(gate, cmd, cwd=here)), cmd
        for cmd in ["make", "cd other && make", "cd sub/../other && make",
                    "(cd sub) && make", "cd sub | make", "cd sub || make",
                    "cd $D && make", "pushd -n sub && make",
                    "(cd sub && echo) ; make", "cd sub && make all"]:
            assert _call(gate, cmd, cwd=here) == {"decision": "approve"}, cmd
        # an explicit gated target is denied wherever make runs, known dir or not
        for cmd in ["cd other && make test", "cd $D && make test", "cd sub || make check"]:
            assert _is_deny(_call(gate, cmd, cwd=here)), cmd

    def test_force_env_approves(self, ws, monkeypatch):
        _seed(ws)
        monkeypatch.setenv("SWARM_TEST_GATE_FORCE", "1")
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}), "pytest tests/test_x.py")
        assert res == {"decision": "approve"}

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


class TestPipelineTestGateDenyText:
    def _deny(self, ws, command, cwd=REPO):
        _seed(ws)
        res = _call(sh.create_pipeline_test_gate({"sdk_session_id": SID}), command, cwd=cwd)
        assert _is_deny(res), command
        return _reason(res)

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
        reason = _reason(res)
        assert "--project 'Agent DLC' --run-id run_9f --mode verify" in reason
        assert ("artifact_cli.py run-update --project 'Agent DLC' --run-id run_9f "
                "--status abandoned") in reason

    def test_deny_names_the_abandon_escape(self, ws):
        reason = self._deny(ws, "pytest -q")
        assert ("python backend/scripts/artifact_cli.py run-update --project SwarmAI "
                "--run-id run_abc123 --status abandoned") in reason

    def test_force_env_is_described_as_operator_only(self, ws):
        # L1: the model must not be told it can set the bypass itself.
        reason = self._deny(ws, "pytest -q")
        assert "SWARM_TEST_GATE_FORCE=1 bypasses" not in reason
        assert "operator" in reason
        assert "backend/daemon environment" in reason


# ── direct_commit_gate ───────────────────────────────────────────────────────
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


def _direct(command, cwd, sid=SID):
    return _call(sh.create_direct_commit_gate({"sdk_session_id": sid}), command, cwd=str(cwd))


class TestDirectCommitGateDenies:
    def test_staged_source_change_over_20_lines_is_denied(self, ws, repo):
        (ws / "Projects" / "Repo").mkdir(parents=True)  # project matching the repo name
        (repo / "a.py").write_text("base = 0\n" + _lines(21))
        _git(repo, "add", "a.py")
        res = _direct("git commit -m fix", repo)
        assert _is_deny(res)
        reason = _reason(res)
        assert "21 source lines" in reason
        assert ('python backend/scripts/artifact_cli.py run-create --project Repo '
                '--profile direct --requirement "<one line>"') in reason
        assert "--project SwarmAI" not in reason

    def test_unmapped_repo_gets_a_project_placeholder(self, ws, repo):
        (repo / "a.py").write_text("base = 0\n" + _lines(21))
        _git(repo, "add", "a.py")
        reason = _reason(_direct("git commit -m fix", repo))
        assert "run-create --project '<project>' --profile direct" in reason

    def test_force_env_is_described_as_operator_only(self, ws, repo):
        # L1
        (repo / "a.py").write_text("base = 0\n" + _lines(21))
        _git(repo, "add", "a.py")
        reason = _reason(_direct("git commit -m fix", repo))
        assert "SWARM_DIRECT_GATE_FORCE=1 for that one command" not in reason
        assert "operator" in reason and "backend/daemon environment" in reason

    def test_unowned_running_run_does_not_exempt(self, ws, repo):
        # H1: a run without session_id must not switch the gate off for anyone.
        _seed(ws, run_id="run_legacy", session_id=_NO_SID)
        (repo / "a.py").write_text("base = 0\n" + _lines(40))
        _git(repo, "add", "a.py")
        assert _is_deny(_direct("git commit -m fix", repo))

    def test_lockfiles_do_not_count_but_source_still_does(self, ws, repo):
        (repo / "a.py").write_text("base = 0\n" + _lines(21))
        (repo / "package-lock.json").write_text(_lines(200, "k"))
        _git(repo, "add", "-A")
        reason = _reason(_direct("git commit -m fix", repo))
        assert "21 source lines" in reason

    def test_dash_a_form_counts_unstaged_changes(self, ws, repo):
        (repo / "a.py").write_text("base = 0\n" + _lines(30))
        assert _is_deny(_direct("git commit -am fix", repo))

    def test_pathspec_form_counts_unstaged_changes(self, ws, repo):
        (repo / "a.py").write_text("base = 0\n" + _lines(30))
        assert _is_deny(_direct("git commit -m fix -- a.py", repo))

    def test_lines_sum_across_files(self, ws, repo):
        (repo / "a.py").write_text("base = 0\n" + _lines(11))
        (repo / "b.py").write_text(_lines(10, "y"))
        _git(repo, "add", "a.py", "b.py")
        assert _is_deny(_direct("git commit -m fix", repo))  # 11 + 10 = 21

    def test_paused_run_of_this_session_does_not_count_as_active(self, ws, repo):
        _seed(ws, status="paused")
        (repo / "a.py").write_text("base = 0\n" + _lines(25))
        _git(repo, "add", "a.py")
        assert _is_deny(_direct("git commit -m fix", repo))


class TestDirectCommitGateApproves:
    def _big_staged_change(self, repo):
        (repo / "a.py").write_text("base = 0\n" + _lines(40))
        _git(repo, "add", "a.py")

    def test_docs_only_change_is_approved(self, ws, repo):
        (repo / "README.md").write_text(_lines(50))
        for rel in ("docs/x.txt", "Knowledge/k.py", ".kiro/steering/s.txt", ".context/c.json"):
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_lines(30))
        _git(repo, "add", "-A")
        assert _direct("git commit -m docs", repo) == {"decision": "approve"}

    def test_exactly_20_source_lines_is_approved(self, ws, repo):
        (repo / "a.py").write_text("base = 0\n" + _lines(20))
        _git(repo, "add", "a.py")
        assert _direct("git commit -m small", repo) == {"decision": "approve"}

    def test_unstaged_changes_outside_the_commit_are_not_counted(self, ws, repo):
        (repo / "b.py").write_text(_lines(3, "y"))
        _git(repo, "add", "b.py")
        (repo / "a.py").write_text("base = 0\n" + _lines(40))  # unstaged, not -a
        assert _direct("git commit -m small", repo) == {"decision": "approve"}

    def test_session_in_a_running_run_is_approved(self, ws, repo):
        _seed(ws)
        self._big_staged_change(repo)
        assert _direct("git commit -m fix", repo) == {"decision": "approve"}

    def test_commit_in_an_unrelated_repo_is_approved(self, ws, repo, tmp_path):
        # M2: the gate is scoped to the SwarmAI source repo.
        other = _make_repo(tmp_path / "other")
        (other / "a.py").write_text("base = 0\n" + _lines(40))
        _git(other, "add", "a.py")
        assert _direct("git commit -m fix", other) == {"decision": "approve"}

    def test_lockfile_only_change_is_approved(self, ws, repo):
        for rel in ("package-lock.json", "uv.lock", "desktop/pnpm-lock.yaml", "yarn.lock"):
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_lines(60, "k"))
        _git(repo, "add", "-A")
        assert _direct("git commit -m 'chore: bump deps'", repo) == {"decision": "approve"}

    def test_other_sessions_run_does_not_exempt(self, ws, repo):
        _seed(ws, session_id="someone-else")
        self._big_staged_change(repo)
        assert _is_deny(_direct("git commit -m fix", repo))

    def test_not_a_repo_fails_open(self, ws, tmp_path):
        d = tmp_path / "plain"
        d.mkdir()
        assert _direct("git commit -m fix", d) == {"decision": "approve"}

    def test_git_numstat_error_fails_open(self, ws, repo, monkeypatch):
        self._big_staged_change(repo)
        monkeypatch.setattr(sh, "_numstat_lines", lambda root, *extra: None)
        assert _direct("git commit -m fix", repo) == {"decision": "approve"}

    def test_helper_exception_fails_open(self, ws, repo, monkeypatch):
        self._big_staged_change(repo)

        def _boom(cwd, command):
            raise RuntimeError("git exploded")
        monkeypatch.setattr(sh, "_pending_source_line_count", _boom)
        assert _direct("git commit -m fix", repo) == {"decision": "approve"}

    @pytest.mark.parametrize("env", ["SWARM_DIRECT_GATE_FORCE", "SWARM_ADVERSARIAL_GATE_FORCE"])
    def test_force_envs_approve(self, ws, repo, monkeypatch, env):
        self._big_staged_change(repo)
        monkeypatch.setenv(env, "1")
        assert _direct("git commit -m fix", repo) == {"decision": "approve"}

    def test_non_commit_commands_are_approved(self, ws, repo):
        self._big_staged_change(repo)
        for command in ("git status", "git diff --cached", 'echo "git commit"'):
            assert _direct(command, repo) == {"decision": "approve"}, command

    def test_missing_session_id_fails_open(self, ws, repo):
        self._big_staged_change(repo)
        assert _direct("git commit -m fix", repo, sid="") == {"decision": "approve"}


# ── registration ─────────────────────────────────────────────────────────────
def test_build_hooks_registers_both_gates_before_the_hitl_gate(monkeypatch):
    """L3: pipeline_test_gate precedes pytest_command_guard, and both new gates
    precede dangerous_command_gate (no approve-then-deny). No existing gate is lost."""
    from core import hook_builder
    from core.permission_manager import permission_manager

    recorded = []
    original = hook_builder.HookRegistry.register

    def _spy(self, event, hook, name, matcher=None, no_timeout=False):
        recorded.append((event, name, matcher))
        return original(self, event, hook, name, matcher=matcher, no_timeout=no_timeout)

    monkeypatch.setattr(hook_builder.HookRegistry, "register", _spy)
    asyncio.run(hook_builder.build_hooks(
        agent_config={"id": "default"}, enable_skills=False, enable_mcp=False,
        resume_session_id="test-sess", session_context={"sdk_session_id": "test-sess"},
        permission_manager=permission_manager,
    ))
    names = [n for _e, n, _m in recorded]
    for existing in ("pre_tool_logger", "background_command_guard", "pytest_command_guard",
                     "bash_syntax_guard", "inclusive_term_guard", "design_lint_guard",
                     "image_read_dedup_guard", "dangerous_command_gate",
                     "adversarial_commit_gate", "active_run_commit_gate",
                     "ask_question_gate", "external_approval_gate", "governance_file_gate"):
        assert existing in names, existing
    assert names.index("pipeline_test_gate") < names.index("pytest_command_guard")
    assert names.index("pipeline_test_gate") < names.index("dangerous_command_gate")
    assert names.index("direct_commit_gate") < names.index("dangerous_command_gate")
    # run_amend_gate denies an amend before dangerous_command_gate could prompt for it
    assert names.index("run_amend_gate") < names.index("dangerous_command_gate")
    assert names.count("run_amend_gate") == 1
    assert names.count("pipeline_test_gate") == names.count("direct_commit_gate") == 1
    # session_link_gate sits right before pipeline_test_gate (test_session_link_gate.py)
    assert names.index("session_link_gate") == names.index("pipeline_test_gate") - 1
    by_name = {n: (e, m) for e, n, m in recorded}
    assert by_name["pipeline_test_gate"] == ("PreToolUse", "Bash")
    assert by_name["direct_commit_gate"] == ("PreToolUse", "Bash")


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
        assert "deliver title" in reason and "run-commit" in reason

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
