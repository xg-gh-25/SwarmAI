"""Acceptance matrix (design 2026-10-24-swarmai-usable-by-anyone §5.2, §7 "验收"): a
pipeline run on each sample repo goes from create to land while the CLI cannot reach any
daemon. Sample repos here: Node (``node --test``) and a repo with no tests at all.

Methodology: the real ``"$SWARM_BACKEND_BIN" pipeline-v1`` program and the real hooks
(``tests/pipeline_v1_rig.py``), on a repo with no Python in it (``package.json``,
``src/price.js``, a ``node --test`` test). Every CLI process starts with a
``sitecustomize`` that makes each socket connect fail, so a command that needed the
daemon (or any network service) would fail. The validation reports are real: the rig
runs ``node --test`` in the run's worktree and reports what it returned.

Covered for the Node repo, ``direct`` and ``full``:
- the run lands: one commit on main whose parent is the base, with the change, the
  test and the run's REPORT, and the main checkout ends clean;
- the BUILD test went red before the change and green after (reported, not rerun);
- the REPORT names the reported command, and push is ready (isolated, tests bound);
- the repo has no ``.py`` file and declares no ``pipeline.toml``.

Covered for the repo with no tests (docs only), ``direct`` and ``full``: the reports say
``not_run`` with a reason, the full DESIGN_SPEC has a ``manual`` exit and no test
references, and the run still lands with the REPORT naming the unrun command.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.pipeline_v1_rig import Rig, _git


pytestmark = pytest.mark.timeout(300)
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

PRICE = "src/price.js"
TEST = "test/price.test.js"
TEST_REF = f"{TEST}::total takes a percentage discount off the sum"

BASE = {
    ".gitignore": "node_modules/\n",
    "package.json": '{"name": "shop", "version": "1.0.0", "type": "module", '
                    '"scripts": {"test": "node --test"}}\n',
    PRICE: "export function total(items) {\n"
           "  return items.reduce((sum, item) => sum + item.price, 0);\n}\n",
    TEST: 'import { test } from "node:test";\n'
          'import assert from "node:assert/strict";\n'
          'import { total } from "../src/price.js";\n\n'
          'test("total adds prices", () => {\n'
          "  assert.equal(total([{ price: 2 }, { price: 3 }]), 5);\n});\n",
    ".swarmai/review.md": (
        "# shop review notes\n\n| # | Pattern | Trigger | Verify | Example |\n"
        "|---|---|---|---|---|\n| SH1 | **pricing** | money maths `match: glob:src/*` | "
        "rounding and defaults are tested | a discount of 0 |\n"),
}
NEW_TEST = (BASE[TEST] + '\ntest("total takes a percentage discount off the sum", () => {\n'
            "  assert.equal(total([{ price: 40 }, { price: 60 }], 25), 75);\n"
            "  assert.equal(total([{ price: 10 }]), 10);\n});\n")
NEW_PRICE = ("export function total(items, discountPercent = 0) {\n"
             "  const sum = items.reduce((acc, item) => acc + item.price, 0);\n"
             "  return sum * (1 - discountPercent / 100);\n}\n")

class NodeRig(Rig):
    """The rig on a Node repo: its files, documents and real ``node --test`` reports."""

    BASE_FILES = BASE
    REQUIREMENT = "Give total() a discount argument: a percentage taken off the sum."
    CHANGE: dict[str, str] = {}         # written in BUILD, test first (complete_stage)

    def __init__(self, tmp_path: Path, monkeypatch, profile: str):
        super().__init__(tmp_path, monkeypatch, profile=profile)
        self.go_offline()
        self.red_green: tuple[int, int] | None = None

    def node_test(self) -> int:
        assert self.worktree is not None
        return subprocess.run(["node", "--test"], cwd=self.worktree, capture_output=True,
                              text=True, timeout=120).returncode

    def documents(self) -> dict[str, Any]:
        import json
        text = json.dumps(super().documents())
        for old, new in (("backend/tests/test_x.py::test_add", TEST_REF),
                         ("backend/tests/test_x.py", TEST), ("backend/core/mod.py", PRICE),
                         ("core.mod", "src/price.js"), ("swap the operands",
                                                        "take a percentage discount")):
            text = text.replace(old, new)
        return json.loads(text)

    def report(self, result: str = "pass") -> dict:
        passed = self.node_test() == 0
        command = {"command": "node --test", "cwd": ".", "result": "pass" if passed else "fail"}
        if not passed:
            command["reason"] = "node --test failed"
        return {"reports": [{"repo": "repo", "commands": [command], "tests_changed": [TEST],
                             "rationale": "the discount test failed before the change"}]}

    def complete_stage(self) -> dict:
        if self.state()["stage"] == "build" and self.red_green is None:
            self.edit({TEST: NEW_TEST})
            red = self.node_test()
            self.edit({PRICE: NEW_PRICE})
            self.red_green = (red, self.node_test())
        return super().complete_stage()


@needs_node
@pytest.mark.parametrize("profile", ["direct", "full"])
def test_a_node_run_lands_with_no_daemon(tmp_path, monkeypatch, profile):
    r = NodeRig(tmp_path, monkeypatch, profile)
    try:
        assert not list(r.repo.rglob("*.py")) and not (r.repo / "pipeline.toml").exists()
        r.assert_offline()
        base = _git(r.repo, "rev-parse", "main")
        r.create()
        state = r.drive()
        assert state["landing"] == "landed", state
        assert r.red_green == (1, 0), r.red_green   # red before the change, green after
        head = _git(r.repo, "rev-parse", "main")
        assert _git(r.repo, "rev-parse", "main^") == base
        changed = set(_git(r.repo, "diff", "--name-only", base, head).splitlines())
        report = f".aidlc/deliverables/{r.run_id}/REPORT.md"
        assert changed == {PRICE, TEST, report}, changed
        assert _git(r.repo, "show", f"{head}:{PRICE}") + "\n" == NEW_PRICE
        assert _git(r.repo, "status", "--porcelain") == ""
        text = _git(r.repo, "show", f"{head}:{report}")
        assert "node --test → pass" in text, text
        # standalone T1: the landing's own command ended the run and cleaned up
        assert r.state()["status"] == "completed", r.state()
        assert r.worktree is not None and not r.worktree.exists()
        assert _git(r.repo, "branch", "--list", "run/*") == ""
        kinds = [e["kind"] for e in r.events()]
        if profile == "full":            # the full path really ran: its documents and review
            assert {"stage_complete", "handout", "answer"} <= set(kinds), sorted(set(kinds))
        push = r.engine().push_status()
        assert push["push_ready"] is True, push
    finally:
        r.close()


# ── a repo with no tests ──────────────────────────────────────────────────────
GUIDE = "docs/guide.md"
NEW_GUIDE = "Install with make.\nUninstall with make uninstall.\n"
UNRUN = "(no test command)"


class NoTestRig(Rig):
    """A docs-only repo: nothing to run, so every report is ``not_run`` with a reason."""

    BASE_FILES = {"README.md": "# notes\n", GUIDE: "Install with make.\n"}
    REQUIREMENT = "Document the uninstall step in docs/guide.md."
    CHANGE = {GUIDE: NEW_GUIDE}

    def __init__(self, tmp_path: Path, monkeypatch, profile: str):
        super().__init__(tmp_path, monkeypatch, profile=profile)
        self.go_offline()

    def documents(self) -> dict[str, Any]:
        import json
        docs = super().documents()
        if not docs:
            return docs
        text = json.dumps(docs)
        for old in ("backend/tests/test_x.py::test_add", "backend/tests/test_x.py",
                    "backend/core/mod.py"):
            text = text.replace(old, GUIDE)
        docs = json.loads(text)
        plan = docs["plan"]
        # nothing can be run: the exit is checked by reading, and no property or failure
        # mode is claimed (each would need a test reference)
        plan["exit_criteria"] = [{"id": "X1", "kind": "manual", "covers": ["R1.1"],
                                  "ref": "read docs/guide.md: it names make uninstall"}]
        plan["properties"], plan["failure_modes"] = [], []
        plan["consumers"][0]["readers"] = ["README.md"]
        return docs

    def report(self, result: str = "pass") -> dict:
        return {"reports": [{"repo": "repo", "commands": [{
            "command": UNRUN, "cwd": ".", "result": "not_run",
            "reason": "the repo has no tests; the change is documentation"}],
            "tests_changed": [], "rationale": "docs only: read the changed section"}]}


@pytest.mark.parametrize("profile", ["direct", "full"])
def test_a_repo_with_no_tests_lands_with_no_daemon(tmp_path, monkeypatch, profile):
    r = NoTestRig(tmp_path, monkeypatch, profile)
    try:
        r.assert_offline()
        base = _git(r.repo, "rev-parse", "main")
        r.create()
        state = r.drive()
        assert state["landing"] == "landed", state
        head = _git(r.repo, "rev-parse", "main")
        assert _git(r.repo, "rev-parse", "main^") == base
        report = f".aidlc/deliverables/{r.run_id}/REPORT.md"
        changed = set(_git(r.repo, "diff", "--name-only", base, head).splitlines())
        assert changed == {GUIDE, report}, changed
        assert _git(r.repo, "show", f"{head}:{GUIDE}") + "\n" == NEW_GUIDE
        assert _git(r.repo, "status", "--porcelain") == ""
        assert r.state()["status"] == "completed", r.state()
        assert r.worktree is not None and not r.worktree.exists()
        text = _git(r.repo, "show", f"{head}:{report}")
        # the REPORT says what was not run and why (Markdown escapes the underscore),
        # and Quality does not call an unverified change verified
        assert f"{UNRUN} → not\\_run（the repo has no tests" in text, text
        assert "0 通过，0 失败，1 未运行" in text, text
        assert "| Quality | WARN | 0/1 verification" in text, text
    finally:
        r.close()
