"""Real end-to-end rig for Pipeline v1 (fixing spec §2, group G).

A run is driven ONLY through the shipped surfaces:

- ``scripts/pipeline_v1.py`` as a subprocess, every
  mutating command first passed through the real PreToolUse ``run_owner_gate`` (which
  may hand it the session id) and then executed by ``bash -c``;
- hook-side events through the real hook functions: the v1 handout tracker
  (PreToolUse Agent), the SubagentStop capture hook, and ``pipeline_human`` (a
  person's question-card pick, Stop, closed tab).

Nothing here appends an event directly; ``events()`` only reads the log.

Key symbols: ``Rig`` (``v1``, ``pt``, ``bash``, ``spawn_reviewer``, ``answer_card``,
``events``, ``state``), ``documents(profile)``, ``BASE_FILES``.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
PV1 = BACKEND / "scripts" / "pipeline_v1.py"
PROJECT = "P"
SESSION = "aaaaaaaa-0000-4000-8000-000000000001"
SWARM = "\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>"

BASE_FILES = {
    ".gitignore": "__pycache__/\n.pytest_cache/\n",
    "backend/pyproject.toml": "[tool.pytest.ini_options]\n",
    "backend/core/__init__.py": "",
    "backend/tests/__init__.py": "",
    "backend/core/mod.py": "def add(a, b):\n    return a + b\n",
    "backend/core/other.py": "def other():\n    return 1\n",
    "backend/tests/test_x.py": (
        "from core.mod import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"),
    "backend/tests/test_other.py": (
        "from core.other import other\n\n\ndef test_other():\n    assert other() == 1\n"),
    "validations.json": json.dumps({"schema": 1, "entries": [{
        "id": "docs", "paths": ["docs/**"], "cwd": ".",
        "argv": ["python3", "-c", "pass"], "call_type": "lint"}]}),
}

TEST_REF = "backend/tests/test_x.py::test_add"


def _ac(identifier: str = "R1.1") -> dict:
    return {"id": identifier, "pattern": "event", "condition": "add is called",
            "response": "it returns the sum", "negative": False, "universal": False}


def documents(profile: str) -> dict[str, Any]:
    """Valid EVALUATE / PLAN artifacts for *profile* (the change edits core/mod.py)."""
    requirements: dict[str, Any] = {
        "requirements": [{"id": "R1", "story": "As a caller", "acceptance": [_ac()]}],
        "analysis": [{"kind": "missing_edge", "refs": ["R1.1"], "note": "none",
                      "resolution": "rewritten"}],
        "clarifications": [{"qid": "Q1", "question": "Which add?", "answer": "core.mod",
                            "answered_by": "evidence"}],
        "understanding": {"claim": "swap the operands", "readings": ["one"],
                          "evidence": ["backend/core/mod.py"], "root_cause": None},
        "prior_knowledge": []}
    if profile == "bugfix":
        requirements["bugfix"] = {"current": [_ac("C1")], "expected": [_ac("E1")],
                                  "unchanged": [_ac("U1")]}
    task = {"id": "T1", "file": "backend/core/mod.py", "symbol": "add",
            "change": "swap the operands", "requirements": ["R1.1"], "depends_on": [],
            "creates_test_files": []}
    exits = [{"id": "X1", "kind": "test", "ref": TEST_REF, "covers": ["R1.1"]}]
    lite = {"tasks": [task], "exit_criteria": exits,
            "no_contract_change_reason": "internal function"}
    if profile in {"direct", "trivial"}:
        design = lite
    else:
        design = {
            "alternatives": [{"what": "swap"}], "chosen": "swap",
            "design": {"user_workflow": "call add", "architecture": "module",
                       "flow": "call -> validate; invalid -> error; valid -> sum",
                       "states": [{"name": "ready", "created_by": "import",
                                   "removed_by": "exit"}]},
            "properties": [{"id": "P1", "statement": "sum is returned",
                            "validates": ["R1.1"], "test": TEST_REF, "kind": "example"}],
            "failure_modes": [{"id": "F1", "scenario": "bad input", "expected": "error",
                               "test": TEST_REF}],
            "tasks": [task], "exit_criteria": list(exits),
            "consumers": [{"id": "K1", "contract": "add", "pattern": "add",
                           "paths": ["backend/core/mod.py"],
                           "readers": ["backend/tests/test_x.py"]}],
            "security_boundaries": []}
        if profile == "bugfix":
            design["exit_criteria"].append(
                {"id": "X2", "kind": "test", "ref": TEST_REF, "covers": ["U1"]})
    if profile == "direct":
        return {}            # direct writes no documents (execution plan v2 N1)
    return {"evaluate": requirements, "plan": design}


REQUIREMENT = "Swap the operands of core.mod.add."


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "-c", "user.name=t",
         "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, timeout=60, check=True).stdout.strip()


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class Rig:
    """One temporary data dir + workspace + git repo, and one run driven through it."""

    def __init__(self, tmp_path: Path, monkeypatch, profile: str = "direct",
                 extra: dict[str, str] | None = None, with_repo: bool = True):
        self.root, self.profile = tmp_path, profile
        self.data, self.ws = tmp_path / "data", tmp_path / "ws"
        (self.ws / "Projects" / PROJECT).mkdir(parents=True)
        self.repo = tmp_path / "repo" if with_repo else None
        if self.repo is not None:
            write(self.repo, {**BASE_FILES, **(extra or {})})
            _git(self.repo, "init", "-q", "-b", "main")
            _git(self.repo, "add", "-A")
            _git(self.repo, "commit", "-q", "-m", "init")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("SWARM_", "SWARMAI_"))}
        env.update(
            SWARM_DATA_DIR=str(self.data), SWARM_WORKSPACE=str(self.ws),
            SWARM_WORKTREE_ROOT=str(tmp_path / "wts"),
            SWARM_WORKTREE_LEDGER=str(tmp_path / "ledger"),
            SWARM_TEST_CACHE_DIR=str(tmp_path / "cache"),
            SWARM_TEST_PYTHON=sys.executable, SWARM_TEST_MAX_WORKERS="1",
            SWARM_APP_SESSION_ID=SESSION)
        self.env = env
        for key in ("SWARM_DATA_DIR", "SWARM_WORKSPACE", "SWARM_WORKTREE_ROOT",
                    "SWARM_WORKTREE_LEDGER", "SWARM_APP_SESSION_ID"):
            monkeypatch.setenv(key, env[key])
        # this process plays the daemon (hooks, a person's answers); pipeline commands
        # run in their own CLI process (S6-3). The default is isolated (conftest turns
        # worktrees off for every other test).
        monkeypatch.setenv("SWARM_WORKTREE_MODE", env.get("SWARM_WORKTREE_MODE") or "on")
        monkeypatch.setenv("SWARM_TEST_CACHE_DIR", env["SWARM_TEST_CACHE_DIR"])
        from core import runtime_hooks
        monkeypatch.setattr(runtime_hooks, "_PIPELINE_AUDIT_DIR",
                            tmp_path / "data" / "state" / "pipeline_agent_audit")
        from core import pipeline_human
        monkeypatch.setattr(pipeline_human, "_workspace", lambda: self.ws)
        self.ctx: dict[str, Any] = {"sdk_session_id": SESSION}
        self.run_id: str | None = None
        self.worktree: Path | None = None

    def close(self) -> None:
        """Nothing to stop: no daemon service is involved since S6-3."""

    # ── commands ────────────────────────────────────────────────────────────
    def _exec(self, argv: list[str], extra_env: dict[str, str] | None = None,
              timeout: float = 150) -> tuple[dict, int]:
        env = {**self.env, **(extra_env or {})}
        # the daemon (this process) reads the mode a test set on r.env
        os.environ["SWARM_WORKTREE_MODE"] = env.get("SWARM_WORKTREE_MODE") or "on"
        proc = subprocess.run(argv, env=env, capture_output=True, text=True,
                              timeout=timeout, cwd=str(self.root))
        lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
        try:
            out = json.loads(lines[-1]) if lines else {}
        except json.JSONDecodeError:
            out = {"raw": proc.stdout[-2000:]}
        if proc.returncode not in (0, 1, 2) or (not lines and proc.stderr):
            out.setdefault("crash", proc.stderr[-3000:])
        out.setdefault("_rc", proc.returncode)
        return out, proc.returncode

    def _run_args(self) -> list[str]:
        assert self.run_id, "create the run first"
        return ["--project", PROJECT, "--run-id", self.run_id]

    def v1(self, command: str, *args: str) -> dict:
        """One pipeline_v1.py call. Mutations go through the real run_owner_gate."""
        argv = [str(PV1), command, *(self._run_args() if command != "create" else []), *args]
        out = self.bash(" ".join([shlex.quote(sys.executable), *map(shlex.quote, argv)]))
        if command == "create" and out.get("pipeline_id"):
            self.run_id = out["pipeline_id"]
        # every response names where the run's code is (r6 §2.3: no stage-start)
        if command != "status" and isinstance(out.get("repos"), dict) and out["repos"]:
            self.worktree = Path(next(iter(out["repos"].values())))
        return out

    def bash(self, command: str, extra_env: dict[str, str] | None = None,
             session: str = SESSION) -> dict:
        """What a Bash tool call does in a SwarmAI session: the run_owner_gate check
        phase, then its after-approval phase (records a person's approval), then
        ``bash -c``."""
        from core import run_owner_gate
        tool_input = {"command": command}
        for phase in ("check", "approved"):
            decision = run_owner_gate._decide(session, tool_input["command"], tool_input,
                                              phase=phase)
            out = (decision or {}).get("hookSpecificOutput") or {}
            if out.get("permissionDecision") == "deny":
                return {"ok": False, "denied": out.get("permissionDecisionReason", "")}
            if out.get("updatedInput"):
                tool_input = dict(out["updatedInput"])
        result, _ = self._exec(["bash", "-c", tool_input["command"]], extra_env=extra_env)
        return result

    # ── hook-side producers (real hook functions) ───────────────────────────
    def spawn_reviewer(self, prompt: str, answer: str, tool_use_id: str) -> str | None:
        """An Agent spawn of *prompt* that ends with *answer*: the v1 handout tracker
        (PreToolUse) binds it, the SubagentStop audit hook records the answer.
        Returns the bound prompt, or None when the tracker did not bind it."""
        from core import runtime_hooks
        tracker = runtime_hooks.create_v1_review_handout_tracker(self.ctx)
        decided = asyncio.run(tracker(
            {"tool_name": "Agent", "tool_input": {"prompt": prompt}}, tool_use_id, None))
        bound = ((decided or {}).get("hookSpecificOutput") or {}).get(
            "updatedInput", {}).get("prompt")
        if not bound:
            return None
        transcript = self.root / "transcripts" / f"{tool_use_id}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("\n".join(json.dumps(r) for r in (
            {"type": "user", "isSidechain": True,
             "message": {"role": "user", "content": bound}},
            {"type": "assistant", "isSidechain": True,
             "message": {"role": "assistant", "content": [{"type": "text", "text": answer}],
                         "usage": {"input_tokens": 10, "output_tokens": 5}}},
        )) + "\n", encoding="utf-8")
        stop = runtime_hooks.create_agent_tool_audit_hook(self.ctx)
        asyncio.run(stop({"agent_type": "pipeline-expert",
                          "agent_transcript_path": str(transcript),
                          "last_assistant_message": answer,
                          "cwd": str(self.root)}, tool_use_id, None))
        return bound

    def answer_card(self, label: str) -> list[dict]:
        """A person picks *label* on the run's pending AskUserQuestion card."""
        from core import pipeline_human
        payload = self.v1("status")["decision"]["ask_user_question"]
        question = payload["questions"][0]["question"]
        return pipeline_human.record_card_answer(SESSION, {question: label})

    def stop_session(self) -> list[str]:
        from core import pipeline_human
        return pipeline_human.on_user_stop(SESSION)

    # ── reads ───────────────────────────────────────────────────────────────
    def engine(self):
        """The run's engine as the daemon loads it (reads only; tests never append)."""
        from core import pipeline_human
        return pipeline_human.load_engine(self.run_id)

    def events(self) -> list[dict]:
        path = self.data / "state" / "pipeline_agent_audit" / "runs" / str(self.run_id) \
            / "events.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def kinds(self) -> list[str]:
        return [event["kind"] for event in self.events()]

    def state(self) -> dict:
        out = self.v1("status")
        assert out.get("ok") is True, out
        return out["state"]

    def edit(self, files: dict[str, str]) -> None:
        assert self.worktree is not None, "no worktree yet: the run is not in a code stage"
        write(self.worktree, files)

    def artifact(self, name: str, value: dict) -> str:
        path = self.root / "artifacts" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    def commit_on_main(self, files: dict[str, str], message: str = "main moves") -> str:
        assert self.repo is not None
        write(self.repo, files)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", message)
        return _git(self.repo, "rev-parse", "HEAD")

    # ── the happy path, one stage at a time ─────────────────────────────────
    CHANGE = {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"}
    _edited = False

    def create(self, *args: str, requirement: str | None = REQUIREMENT) -> dict:
        repo_args = ["--repo", f"repo={self.repo}"] if self.repo is not None else []
        req = ["--requirement", requirement] if requirement is not None else []
        out = self.v1("create", "--project", PROJECT, "--profile", self.profile,
                      *repo_args, *req, *args)
        assert out.get("ok") is True, out
        return out

    def gate0(self, verdict: str = "SUPPORTED", n: int = 1) -> dict:
        docs = documents(self.profile)
        out = self.v1("gate0-start", "--artifact", self.artifact("requirements", docs["evaluate"]))
        assert out.get("ok") is True, out
        if out.get("required"):
            bound = self.spawn_reviewer(out["handout"]["prompt"],
                                        f"VERDICT: {verdict}\nEvidence: backend/core/mod.py",
                                        f"gate0-{n}")
            assert bound, "the handout tracker did not bind the Gate-0 spawn"
        return out

    def report(self, result: str = "pass") -> dict:
        """The agent's validation report for this run's repo (S6-1)."""
        cmd = {"command": "python -m pytest backend/tests/test_x.py", "cwd": ".",
               "result": result}
        if result != "pass":
            cmd["reason"] = "the test failed"
        return {"reports": [{"repo": "repo", "commands": [cmd],
                             "tests_changed": ["backend/tests/test_x.py"],
                             "rationale": "test_x fails before the change"}]}

    def tested_tree(self, stage: str = "test") -> str | None:
        """The code tree of this run's latest *stage* validation report (S6-1)."""
        from core.run_protocol.validation_report import latest
        row = latest(self.events(), stage).get("repo")
        return row["payload"]["code_tree"] if row else None

    def report_and_complete(self, result: str = "pass") -> dict:
        return self.v1("stage-complete", "--artifact",
                       self.artifact("report", self.report(result)))

    def complete_stage(self) -> dict:
        """Do the normal work of the current stage and complete it."""
        stage = self.state()["stage"]
        docs = documents(self.profile)
        if stage == "evaluate":
            if self.profile == "bugfix":
                self.gate0()
            return self.v1("stage-complete", "--artifact",
                           self.artifact("requirements", docs["evaluate"]))
        if stage == "think":
            return self.v1("stage-complete")
        if stage == "plan":
            if self.repo is not None:
                self.edit(self.CHANGE)
            return self.v1("stage-complete", "--artifact", self.artifact("design", docs["plan"]))
        if stage in {"build", "goal_cycle"}:
            from core.run_protocol.lifecycle import PROFILE_STAGES
            if self.repo is not None and "plan" not in PROFILE_STAGES[self.profile] \
                    and not self._edited:
                self.edit(self.CHANGE)          # no PLAN: BUILD is the first code stage
                self._edited = True
            return self.report_and_complete()
        if stage == "review":
            started = self.v1("review-start")
            assert started.get("ok") is True, started
            for i, handout in enumerate(started.get("handouts") or []):
                self.spawn_reviewer(handout["prompt"], "NO FINDINGS", f"review-{i}")
            return self.v1("stage-complete")
        if stage == "test":
            return self.report_and_complete()
        if stage == "deliver":
            self.v1("reflect-prepare", "--artifact", self.artifact("reflect", {"lessons": []}))
            if self.repo is None:
                return self.v1("stage-complete")
            out = self.v1("land", "--message", "fix: swap the operands" + SWARM)
            step = out.get("next") or {}
            if "report-tests" in str(step.get("command")):   # main moved: test again (S6-1)
                self.v1("report-tests", "--artifact", self.artifact("report", self.report()))
            return out
        if stage == "reflect":
            # the daemon's background REFLECT (D1), as the hook schedules it
            from core.run_protocol import reflect_job
            reflect_job.process_run(self.engine())
            return self.v1("status")
        raise AssertionError(f"no happy step for stage {stage!r}")

    def drive(self, until: str | None = None, limit: int = 12) -> dict:
        """Complete stages until *until* is the current stage (or the run ends).
        Passing *until* without stopping there (the run skipped it) is an error."""
        from core.run_protocol.lifecycle import PROFILE_STAGES
        order = PROFILE_STAGES[self.profile]
        for _ in range(limit):
            state = self.state()
            if until is not None and state["stage"] == until and state["status"] == "active":
                return state
            if until is not None and state["stage"] in order \
                    and order.index(state["stage"]) > order.index(until):
                raise AssertionError(f"the run skipped {until}: {state['stages']}")
            if state["status"] in {"completed", "abandoned", "failed"}:
                if until is not None:
                    raise AssertionError(f"the run ended before {until}: {state}")
                return state
            self.complete_stage()
        raise AssertionError(f"run did not progress: {self.state()}")
