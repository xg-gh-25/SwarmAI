"""Where a task runs (this machine or a Hive) is judged at intake (AGENT R1).

The rule is text the agent reads every turn, and its skill (s_swarm-out) must trigger on
the person accepting the agent's proposal and say the same conditions. These assertions
pin the content the agent relies on: a lean-to-Hive list, a stay-local list, the
proposal-then-accept step, the never-without-their-word floor, and with no Hive a
proposal made once for long work, never per task.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parents[1]


def _r1() -> str:
    text = (BACKEND / "context" / "AGENT.md").read_text(encoding="utf-8")
    match = re.search(r"\*\*R1\..*?(?=\n\*\*R2\.)", text, re.S)
    assert match, "AGENT.md has no R1"
    return " ".join(match.group(0).split())


def _skill() -> tuple[dict, str]:
    text = (BACKEND / "skills" / "s_swarm-out" / "SKILL.md").read_text(encoding="utf-8")
    _, front, body = text.split("---", 2)
    return yaml.safe_load(front), " ".join(body.split())


SHARED = ["long", "full or bugfix pipeline run", "independent parts", "busy",
          "interactive back-and-forth", "small change", "internal or private resources",
          "this machine's own data, build or daemon", "propose it in one line"]


def test_agent_r1_and_swarm_out_say_where_work_runs():
    r1 = _r1()
    for phrase in SHARED:
        assert phrase in r1, phrase
    assert "Never dispatch without their word" in r1
    assert "with no Hive, everything runs locally, and I propose one once when the work is long" in r1
    assert "only once they accept or ask for it" in r1

    front, body = _skill()
    assert "accepts your Hive proposal" in front["description"]
    section = body.split("## Proposing a Hive", 1)[1].split("## When to ask", 1)[0]
    for phrase in SHARED:
        assert phrase in section, phrase
    assert "dispatch only once they accept" in section
    assert "No Hive: run it here, and propose a Hive once when the work is long, never per task" in section
