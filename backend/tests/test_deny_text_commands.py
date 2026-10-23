"""Every command a gate's deny text tells the agent to run must exist (fixing spec §7, E).

Methodology: collect the deny / warning texts that name a pipeline command, then
- no text names a retired ``artifact_cli.py run-*`` command (main() refuses them);
- every ``pipeline_v1.py <sub> ...`` in a text parses with the CLI's own argparse
  parser after its ``<placeholders>`` are filled in (a real parse, not a regex check).
"""
from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

RETIRED = re.compile(r"artifact_cli(?:\.py)?\s+(run-create|run-update|run-commit|run-diff|"
                     r"run-checkpoint|run-resume|run-report|run-observe)\b")


def _texts(monkeypatch=None) -> dict[str, str]:
    from core import security_hooks, worktree_write_gate
    import pipeline_test
    texts = {
        "active_run_commit": security_hooks.active_run_commit_deny_text(
            ["backend/core/x.py"], ["run_abc"]),
        "stash": worktree_write_gate.STASH_DENY,
    }
    source = (BACKEND / "core" / "security_hooks.py").read_text(encoding="utf-8")
    start = source.index("def create_session_link_gate")
    texts["session_link"] = source[start:start + 6000]
    return texts


def _commands(text: str) -> list[list[str]]:
    out = []
    for match in re.finditer(r"pipeline_v1\.py ([^`\n\"]+?)(?:`|\"\)|$|, whose|\. )", text):
        line = match.group(1)
        line = (line.replace("<project>", "P").replace("<run_id>", "run_x")
                .replace("'<message>'", "m").replace("'<the request>'", "r")
                .replace("<name>=<absolute repo path>", "repo=/tmp/repo")
                .replace("{project}", "P").replace("{run}", "run_x"))
        if "--" in line:            # a runnable command, not a prose mention of one
            out.append(shlex.split(line))
    return out


@pytest.mark.parametrize("name", ["active_run_commit", "stash", "session_link"])
def test_no_deny_text_names_a_retired_command(name, monkeypatch):
    text = _texts(monkeypatch)[name]
    assert text
    assert not RETIRED.search(text), RETIRED.search(text).group(0)


def test_every_named_pipeline_v1_command_parses(monkeypatch):
    import pipeline_v1
    parser = pipeline_v1.parser()
    seen = 0
    for name, text in _texts(monkeypatch).items():
        for argv in _commands(text):
            parser.parse_args(argv)          # SystemExit = a command that does not exist
            seen += 1
    assert seen >= 1


def test_experts_the_pipeline_does_not_dispatch_say_so():
    """E: the v1 roster is correctness / security / red-team; every other code expert
    file says it is not dispatched, and the agent type names no retired gate."""
    from core import pipeline_expert_agent
    roster = {"correctness", "security", "red-team"}
    for path in sorted((BACKEND / "experts" / "code").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        assert (path.stem in roster) != ("Pipeline v1: not dispatched" in text), path.name
    description = pipeline_expert_agent.agent_definitions()[
        pipeline_expert_agent.PIPELINE_EXPERT].description
    for retired in ("Gate-1", "Gate-2", "meta-review", "root-cause"):
        assert retired not in description
