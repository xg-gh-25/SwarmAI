"""Tests for the self-loops health check engine (s_loops-health).

Every test runs against a throwaway workspace built in tmp_path; no test reads or
writes the real ~/.swarm-ai/SwarmWS. The engine resolves its paths into module
constants at import time, so the ``workspace`` fixture repoints those constants
(WORKSPACE, CONTEXT_DIR, KNOWLEDGE_DIR, PROJECTS_DIR, HISTORY_FILE) per test.
This matters because ``run(auto_fix=True)`` commits, pushes, rewrites MEMORY.md,
archives DailyActivity and edits run.json files in whatever workspace it points at.

Verified:
- Report schema, dimension set (incl. the P0 ``meta`` probe), check ids per dimension
- Probe anchors match the live 7-type MEMORY / EVOLUTION structure (P0 passes)
- auto_fix leaves MEMORY.md with every MEMORY_SECTIONS header, and touches only
  the temp workspace
- Markdown report format
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_loops-health" / "scripts"))
import loops_health_check as lhc  # noqa: E402
from loops_health_check import MEMORY_SECTIONS, SelfLoopsHealthEngine  # noqa: E402

CONTEXT_FILES = [
    "SWARMAI.md", "IDENTITY.md", "SOUL.md", "AGENT.md", "USER.md",
    "STEERING.md", "TOOLS.md", "MEMORY.md", "EVOLUTION.md", "KNOWLEDGE.md",
]


def _memory_md() -> str:
    body = {
        "## Decisions": "- [DEC01] Keep memory self-owned | 2026-10-20",
        "## Guidelines": "- [GL01] Use locked_write for MEMORY.md | 2026-10-20",
        "## Pitfalls": "- [PIT01] Soft-interrupted CLI is poisoned | 2026-10-20",
        "## Corrections": "- [COR01] Ask when genuinely ambiguous | 2026-10-20",
    }
    return "\n\n".join(f"{h}\n{body.get(h, '')}" for h in MEMORY_SECTIONS) + "\n"


EVOLUTION_MD = """# Evolution

## Corrections Captured

### C056 | 2026-10-20 [Scope creep from a review finding]
- fix recorded

### CLASS B: Symptom-Level Fix [1 entries]
- **C042** (06-27): example

## Competence Learned
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "SwarmWS"
    ctx = ws / ".context"
    ctx.mkdir(parents=True)
    for name in CONTEXT_FILES:
        (ctx / name).write_text(f"# {name}\n", encoding="utf-8")
    (ctx / "MEMORY.md").write_text(_memory_md(), encoding="utf-8")
    (ctx / "EVOLUTION.md").write_text(EVOLUTION_MD, encoding="utf-8")
    (ws / "Knowledge" / "DailyActivity").mkdir(parents=True)
    (ws / "Projects").mkdir()
    _git(ws, "init", "-q")
    _git(ws, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(ws, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    monkeypatch.setattr(lhc, "WORKSPACE", ws)
    monkeypatch.setattr(lhc, "CONTEXT_DIR", ctx)
    monkeypatch.setattr(lhc, "KNOWLEDGE_DIR", ws / "Knowledge")
    monkeypatch.setattr(lhc, "PROJECTS_DIR", ws / "Projects")
    monkeypatch.setattr(lhc, "HISTORY_FILE", ws / ".loops-health-history.json")
    return ws


@pytest.fixture
def run_health_check(workspace):
    engine = SelfLoopsHealthEngine()
    engine.run(auto_fix=False)
    return engine


@pytest.fixture
def health_data(run_health_check):
    return json.loads(run_health_check.to_json())


def _ids(data, dimension):
    return {f["id"] for f in data["findings"] if f["dimension"] == dimension}


class TestHealthCheckSchema:
    def test_has_required_fields(self, health_data):
        for key in ("overall_score", "scores", "findings", "found", "fixed"):
            assert key in health_data
        assert isinstance(health_data["overall_score"], int)
        assert 0 <= health_data["overall_score"] <= 100

    def test_dimensions_present(self, health_data):
        expected = {"meta", "context", "memory", "knowledge", "evolution",
                    "coherence", "brain_safety", "infrastructure"}
        actual = set(health_data["scores"].keys())
        assert actual.issubset(expected)
        assert {"context", "memory", "knowledge", "evolution"}.issubset(actual)

    def test_check_count(self, health_data):
        # 29 dimension checks (C3 and K1 removed 2026-08-14) + the P0 probe.
        assert len(health_data["findings"]) == 25  # M2, M3 retired with the distillation hook; E1, E2 with the evolution cycle; M4 with Open Threads

    def test_finding_schema(self, health_data):
        for f in health_data["findings"]:
            for key in ("id", "name", "dimension", "status"):
                assert key in f
            assert f["status"] in ("pass", "warn", "fail", "n/a")

    def test_score_is_min_of_dimensions(self, health_data):
        scores = health_data["scores"]
        if scores:
            assert health_data["overall_score"] == min(scores.values())


class TestDimensionChecks:
    def test_probe_anchors_match_live_structure(self, health_data):
        (p0,) = [f for f in health_data["findings"] if f["id"] == "P0"]
        assert p0["status"] == "pass", p0["detail"]

    def test_context_checks(self, health_data):
        assert _ids(health_data, "context") == {"C1", "C2", "C4"}

    def test_memory_checks(self, health_data):
        assert _ids(health_data, "memory") == {"M1", "M5", "M6"}

    def test_evolution_checks(self, health_data):
        assert _ids(health_data, "evolution") == {"E3", "E4"}

    def test_brain_safety_checks(self, health_data):
        assert _ids(health_data, "brain_safety") == {"B1", "B2", "B3", "B4"}

    def test_infrastructure_checks(self, health_data):
        assert _ids(health_data, "infrastructure") == {"I1", "I2", "I3", "I4", "I5", "I6"}


class TestAutoFix:
    def test_auto_fix_doesnt_crash(self, workspace):
        engine = SelfLoopsHealthEngine()
        engine.run(auto_fix=True)
        assert hasattr(engine.report, "fixes_applied")

    def test_memory_not_corrupted_after_autofix(self, workspace):
        SelfLoopsHealthEngine().run(auto_fix=True)
        mem = (workspace / ".context" / "MEMORY.md").read_text(encoding="utf-8")
        for header in MEMORY_SECTIONS:
            assert header in mem

    def test_auto_fix_stays_in_temp_workspace(self, workspace, tmp_path):
        engine = SelfLoopsHealthEngine()
        engine.run(auto_fix=True)
        assert lhc.WORKSPACE == workspace
        assert str(lhc.CONTEXT_DIR).startswith(str(tmp_path))
        assert (workspace / ".loops-health-history.json").exists()


class TestMarkdownOutput:
    def test_markdown_has_frontmatter(self, run_health_check):
        output = run_health_check.to_markdown()
        assert output.startswith("---\n")
        assert "job_id: loops-health" in output
        assert "score:" in output
        assert "found:" in output

    def test_markdown_has_summary_table(self, run_health_check):
        output = run_health_check.to_markdown()
        assert "## Summary: Found" in output
        assert "| Dimension |" in output
