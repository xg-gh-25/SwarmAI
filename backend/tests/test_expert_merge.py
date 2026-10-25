"""S6-4 expert merge, 12 -> 9 (design 2026-10-24-swarmai-usable-by-anyone.md §3.5.1).

What is tested:
- the expert set is exactly the nine merged experts; red-team, spec-compliance,
  code-quality and ux-test are gone, ``ux`` exists;
- ``reviewers()`` never returns red-team: a >=50-line risky change gets correctness and
  security only, and a required retired id maps to the expert that took its job;
- security has a "High-risk change" section worded as confirming protections hold, with
  no attack / forge instruction (a review brief worded that way can make the model
  decline and return nothing);
- the generated catalog lists every remaining expert and none of the deleted ones;
- ``spawn_as`` is gone from every frontmatter and from the expert model and API.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.expert_manager import ExpertManager
from core.run_protocol.review import reviewers

BACKEND = Path(__file__).resolve().parents[1]
EXPERTS = BACKEND / "experts"
MERGED = {"correctness", "security", "concurrency", "state-machine", "api-contract",
          "integration", "performance", "operational", "ux"}
RETIRED = {"red-team", "spec-compliance", "code-quality", "ux-test"}


def _ids(rows):
    return [row["id"] for row in rows]


def test_the_expert_set_is_the_nine_merged_experts():
    assert set(ExpertManager(EXPERTS).scan_experts()) == MERGED


def test_reviewers_never_dispatch_red_team():
    risky = [{"path": "backend/core/security_hooks.py", "insertions": 80, "deletions": 0}]
    assert _ids(reviewers(profile="full", changed=risky)) == ["correctness", "security"]
    for retired in sorted(RETIRED):
        got = _ids(reviewers(profile="full", changed=[], required=[retired]))
        assert "red-team" not in got and retired not in got, (retired, got)
    assert _ids(reviewers(profile="full", changed=[], required=["red-team"])) == ["security"]


def test_security_has_a_high_risk_section_that_confirms_protections():
    text = (EXPERTS / "code" / "security.md").read_text(encoding="utf-8")
    match = re.search(r"^## High-risk change\b(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, "security.md lacks a '## High-risk change' section"
    section = match.group(1).lower()
    assert "confirm" in section and "unchecked" in section
    for word in ("attack", "exploit", "forge", "break in", "red team", "red-team"):
        assert word not in section, word


def test_catalog_lists_exactly_the_remaining_experts():
    text = (BACKEND / "skills" / "s_expert-review" / "CATALOG.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `([a-z-]+)` \|", text, re.M))
    assert listed == MERGED


def test_no_spawn_as_anywhere():
    for path in EXPERTS.rglob("*.md"):
        head = path.read_text(encoding="utf-8").split("\n---", 1)[0]
        assert "spawn_as" not in head, path
    from core.expert_manager import ExpertInfo
    from schemas.expert import ExpertResponse
    assert "spawn_as" not in ExpertInfo.__dataclass_fields__
    assert "spawn_as" not in ExpertResponse.model_fields


def test_only_a_large_risky_change_is_marked_high_risk():
    risky = [{"path": "backend/core/security_hooks.py", "insertions": 80, "deletions": 0}]
    small = [{"path": "backend/auth.py", "insertions": 15, "deletions": 0}]
    rows = {row["id"]: row for row in reviewers(profile="full", changed=risky)}
    assert rows["security"].get("high_risk") is True and "high_risk" not in rows["correctness"]
    rows = {row["id"]: row for row in reviewers(profile="direct", changed=small)}
    assert "high_risk" not in rows["security"]


def test_review_start_tells_security_a_change_is_high_risk(tmp_path, monkeypatch):
    """End to end: a 60-line change to an auth module hands out correctness and security
    only, and only the security handout starts with the high-risk notice."""
    from tests.pipeline_v1_rig import Rig
    body = "".join(f"def check_{i}(user):\n    return user is not None\n\n" for i in range(20))
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    try:
        r.CHANGE = {"backend/core/auth.py": body}
        r.create()
        r.drive(until="review")
        started = r.v1("review-start")
        assert started.get("ok") is True, started
        prompts = {h["event"]["payload"]["expert_id"]: h["prompt"] for h in started["handouts"]}
        assert set(prompts) == {"correctness", "security"}, set(prompts)
        assert "HIGH-RISK CHANGE" in prompts["security"]
        assert "HIGH-RISK CHANGE" not in prompts["correctness"]
    finally:
        r.close()


def test_a_retired_expert_id_reads_its_successors_prompt():
    """A run started before the merge keeps red-team in its events and findings, so
    its R2 handout asks for red-team's prompt; that must be security's, never the
    deleted file (FileNotFoundError mid review-address, after events were written)."""
    from core.run_protocol.review import expert_prompt
    assert expert_prompt("red-team") == expert_prompt("security")
    for old in ("spec-compliance", "code-quality", "ux-test"):
        assert expert_prompt(old) == expert_prompt("correctness")
    assert expert_prompt("ux") != expert_prompt("correctness")


def test_engine_reads_every_expert_prompt_through_expert_prompt():
    """One place resolves an id to a prompt file: no engine call site opens
    experts/code/<id>.md itself."""
    engine = (Path(__file__).resolve().parents[1] / "core" / "run_protocol" / "engine.py")
    text = engine.read_text(encoding="utf-8")
    assert '"experts" / "code"' not in text
    assert text.count("review.expert_prompt(") == 3
