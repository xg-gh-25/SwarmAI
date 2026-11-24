"""CDF's extractors write fewer, truer TIMELINE events and DDD entries, by the extraction
standard and the declaration path (not by a review layer, a per-entry check, a cap or a
budget).

Questions:
- the closest recorded events a new event is shown: the earlier event of each real
  duplicate pair (2026-11-20..24, as fixture events among unrelated ones) is among them,
  both doors (the session review, a run's report) rank one event pool, and a month
  summary is no event to declare against;
- a later step of an event already recorded is declared ``adds`` to it: the declaration
  survives the contract and the pipeline resolves the event to the earlier one (not
  written), unless it also finishes a gap (then it is written, so the gap closes);
- the review prompt, the event rule and the run's DELIVER card carry the event standard;
  the knowledge standard carries the DDD unit standard;
- a unit whose scope names an existing project goes to that project's DDD, never the
  General Brain: written as the project's name plus a part, or when the source declared a
  project that does not exist.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_cdf_v2_e2e import cdf_env  # noqa: F401  (fixture)

# ── the real duplicate pairs, each as (earlier event, later restatements...) ─────────

PAIRS = [
    [("First pipeline run whose TEST was decided by a Feature Map replay",
      "A pipeline run's TEST stage was decided by replaying the Feature Map walks in the "
      "sandbox instead of the agent's own test list.", "TEST verdicts come from the user's side."),
     ("Feature Map TEST replay loop completed end-to-end in a real pipeline run for the first time",
      "The Feature Map replay ran as the TEST stage of a real pipeline run and its verdict "
      "gated delivery.", "The replay loop is proven on a real run."),
     ("First real pipeline run through Feature Map TEST replay found a blocking lifecycle bug",
      "Running a real pipeline run through the Feature Map TEST replay found a lifecycle bug "
      "that blocked the run until it was fixed.", "The replay catches what unit tests missed.")],
    [("Inbox core landed, with all three channels checked on live accounts",
      "The unified Inbox core landed; Slack, mail and GitHub were each checked on the "
      "person's live accounts.", "One place for the person's messages."),
     ("Inbox ran end to end on the live machine for the first time",
      "The Inbox pulled, triaged and drafted replies on the live machine.", "Inbox works live."),
     ("Unified Inbox passed its full end-to-end checklist",
      "Every item of the Inbox end-to-end checklist passed on the live accounts.", "Inbox verified."),
     ("Inbox E2E completed and the legacy pipelines it replaced were removed",
      "After the Inbox end-to-end check passed, the signal pipelines it replaced were deleted.",
      "One path for incoming messages.")],
    [("Legacy Morning Reflect, signal chain, DailyBriefs and Community page retired in favour of Inbox",
      "Morning Reflect, the signal digest chain, DailyBriefs and the Community page were "
      "removed; Inbox covers what they did.", "Less to maintain."),
     ("Legacy signal chain, channel-monitor fallback script and Community page removed in favour of Inbox",
      "The signal chain, the channel-monitor fallback script and the Community page were "
      "deleted now that Inbox replaces them.", "Inbox is the only path.")],
    [("Hive outage after Amazon turned on VPC BPA, fixed with a subnet-level exclusion",
      "Hives lost outbound access when VPC Block Public Access was enabled on the account; a "
      "subnet-level BPA exclusion restored them.", "Hives were unreachable until the exclusion."),
     ("Provisioner now creates a dedicated BPA-excluded subnet for each Hive",
      "The Hive provisioner creates its own subnet with a BPA exclusion instead of using the "
      "default subnet.", "New Hives survive VPC BPA."),
     ("Both EC2 Hives moved to the dedicated BPA-excluded subnet; manual default-subnet exclusion removed",
      "The two running Hives were moved onto the dedicated subnet and the manual exclusion on "
      "the default subnet was removed.", "No hand-made exclusion is left.")],
    [("Swarm Out dashboard redesign approved and Hive settings moved out of App Settings",
      "The person approved the Swarm Out dashboard redesign; Hive settings now live in the "
      "Swarm Out tab, not App Settings.", "One place for Hive work."),
     ("Swarm Out dashboard redesign (v6) shipped and running",
      "The redesigned Swarm Out dashboard (v6) shipped and runs in the app.", "The new dashboard is live.")],
]

# unrelated events of the same weeks and the same product: what the earlier event has to
# stand out from
BACKGROUND = [
    ("Pipeline profile is fixed at EVALUATE", "A run's profile can no longer change after EVALUATE.", "Runs plan once."),
    ("Review experts read the diff, not the whole repo", "Each expert reads the change and its callers.", "Reviews are shorter."),
    ("Hive dispatch refuses push and PR on unattended tasks", "An unattended Hive task cannot push or open a PR.", "The person reviews first."),
    ("Month summary lane writes its summary with content", "The summary lane stores the month summary text.", "Summaries are injected."),
    ("Settings tabs merged into two", "The settings screen has two tabs.", "Fewer places to look."),
    ("Chat tabs keep their own message store", "Each chat tab writes through its own MessageStore.", "No cross-tab bleed."),
    ("Brain Hub shows every DDD's six sections", "Brain Hub projects each Brain's sections read-only.", "One window on all Brains."),
    ("Eval quick check runs on a temporary workspace", "The daily quick check no longer touches the real workspace.", "No eval writes to user data."),
    ("Backend daemon restart now needs the person's approval", "Restarting the daemon is asked first.", "No surprise restarts."),
    ("Recall reaches older archive shards through cognition find", "cognition find reads every archive shard.", "Older entries are findable."),
    ("Code graph impact lists callers across files", "The impact command lists resolved callers.", "Contract migrations find every reader."),
    ("Pollinate video stage moved to its own worker", "Video rendering runs in a separate worker.", "Chat stays responsive."),
    ("Jobs scheduler skips a run while the machine sleeps", "A job due during sleep runs once on wake.", "No burst of late runs."),
    ("Skills lint refuses owner-named paths", "The skills linter fails on a path naming one user.", "Shipped skills hold for everyone."),
    ("ToDo packets carry their own context", "A ToDo dragged into a tab starts without a briefing.", "Deferred work restarts cleanly."),
    ("Expert review capped at two rounds", "A HIGH left after round two goes to the person.", "Review loops end."),
    ("Canvas opens a terminal for long commands", "A long command surfaces in a Canvas terminal.", "The person sees what runs."),
    ("Need-You channel deduplicates repeated alerts", "The attention surface shows one alert per cause.", "Fewer interruptions."),
    ("Session healing respawns a broken session invisibly", "A crashed session is restarted with its context.", "No lost turn."),
    ("Mutation check restores the file after every run", "scripts.mutation_check always puts the code back.", "No reverted fix left behind."),
]


def _block(n: int, title: str, what: str, impact: str) -> str:
    return (f"### 2026-11-{10 + n % 14:02d} {title}\n- what: {what}\n- impact: {impact}\n"
            f"<!-- cand:cand-{n:024d}.unit | event_key:k{n} | source:S7 | event_kind:event"
            f" | project_hint:SwarmAI -->")


_SUMMARY = ("### 2026-11 key events\n- what: Inbox landed and replaced the signal chain; the "
            "Feature Map TEST replay ran in a real pipeline run; Hives moved to a BPA-excluded "
            "subnet after the VPC BPA outage; the Swarm Out dashboard redesign shipped.\n"
            "<!-- cand:cand-summary00000000000000000.unit | event_key:ks | source:summary"
            " | event_kind:month_summary -->")


def _timeline(ws: Path, *, summary: bool = False) -> dict[int, str]:
    """TIMELINE with the background events and the earlier event of each pair; returns
    each pair's earlier event title by pair index."""
    blocks, firsts = [], {}
    for n, (title, what, impact) in enumerate(BACKGROUND):
        blocks.append(_block(n, title, what, impact))
    for p, pair in enumerate(PAIRS):
        title, what, impact = pair[0]
        blocks.append(_block(100 + p, title, what, impact))
        firsts[p] = title
    if summary:
        blocks.append(_SUMMARY)
    (ws / ".context").mkdir(parents=True, exist_ok=True)
    (ws / ".context" / "TIMELINE.md").write_text(
        "# Timeline\n\n## 2026-11\n\n" + "\n\n".join(blocks) + "\n", encoding="utf-8")
    return firsts


def _later():
    for p, pair in enumerate(PAIRS):
        for title, what, impact in pair[1:]:
            yield p, title, what, impact


def _event_unit(title, what, impact, **over):
    from core.ingestion.contracts_v2 import EventUnit
    return EventUnit(**{"when": "2026-11-23", "title": title, "what": what, "impact": impact,
                        "evidence": ["event:input:x"], "scope": "project:SwarmAI", **over})


# 1 ── the closest recorded events a new event is shown

@pytest.mark.parametrize("p,title,what,impact", list(_later()))
def test_the_closest_events_hold_the_earlier_event_of_each_duplicate(tmp_path, p, title, what, impact):
    from core.ingestion import relations_v2
    firsts = _timeline(tmp_path, summary=True)
    unit = _event_unit(title, what, impact)
    shown = relations_v2.closest_event_entries(relations_v2.unit_text(unit), tmp_path)
    assert any(firsts[p] in e.head for e in shown), [e.head for e in shown]
    # the run report's list (pipeline-v1 events) is the same ranking, as refs
    refs = relations_v2.closest_events(relations_v2.unit_text(unit), tmp_path)
    assert refs == [e.shown() for e in shown]


@pytest.mark.parametrize("p,title,what,impact", list(_later()))
def test_the_session_review_shows_the_earlier_event_and_no_month_summary(cdf_env, p, title, what, impact):
    from core.ingestion.sources import s7_review
    firsts = _timeline(cdf_env["workspace"], summary=True)
    unit = _event_unit(title, what, impact, scope="project:P")
    (shown,) = s7_review._candidates([unit], cdf_env["workspace"], "P", [])
    heads = [e.head for e in shown]
    assert any(firsts[p] in h for h in heads), heads
    assert not any("month_summary" in e.text for e in shown), heads


def test_an_events_query_reads_its_impact_and_outcome():
    from core.ingestion import relations_v2
    unit = _event_unit("Hives moved", "Both moved.", "No hand-made exclusion is left.",
                       outcome="The default subnet carries no BPA exclusion.")
    text = relations_v2.unit_text(unit)
    assert "No hand-made exclusion" in text and "default subnet carries" in text


# 2 ── a later step of a recorded event is adds to it, and resolves to it

def test_an_events_adds_declaration_survives_the_contract():
    from core.ingestion.sources import s7_review
    unit = _event_unit("Swarm Out dashboard redesign (v6) shipped and running", "Shipped.", "Live.")
    ref = "0123abcd | ### 2026-11-20 Swarm Out dashboard redesign approved"
    (out,) = s7_review.apply_relations([unit], {"relations": [
        {"unit": 1, "relation": "adds", "target": ref}]})
    assert out.adds == ref and out.same is None


def _ref_of(ws: Path, title: str) -> tuple[str, str]:
    from core.ingestion import relations_v2
    entry = next(e for e in relations_v2.timeline_entries(ws) if title in e.head)
    return entry.shown(), entry.anchor


@pytest.mark.parametrize("p,title,what,impact", list(_later()))
def test_a_later_step_declared_adds_resolves_to_the_earlier_event(cdf_env, p, title, what, impact):
    from core.ingestion.api import connect_rw
    from core.ingestion.pipeline_v2 import run_unit_v2
    from tests.test_cdf_refactor import _row
    connect_rw().close()
    firsts = _timeline(cdf_env["workspace"])
    ref, anchor = _ref_of(cdf_env["workspace"], firsts[p])
    unit = _event_unit(title, what, impact, adds=ref).model_dump(mode="json")
    decision = run_unit_v2(_row(unit, source="S7", tainted=False, session_project="P"),
                           cdf_env["workspace"])
    assert (decision.decision, decision.reason) == ("reject", "already_exists")
    assert decision.version_vector["same_anchor"] == anchor


def test_a_later_step_that_finishes_a_gap_is_still_written(cdf_env):
    """Only a written event closes a gap (overrides.apply, gaps.close_declared read
    state='written'): a later step that finishes one is written, linked to the earlier."""
    from core.ingestion.api import connect_rw
    from core.ingestion.pipeline_v2 import run_unit_v2
    from tests.test_cdf_extraction_at_task_end import _GAPS, _cand, _pool_ref
    from tests.test_cdf_refactor import _row
    firsts = _timeline(cdf_env["workspace"])
    (cdf_env["ddd"] / "IMPROVEMENT.md").write_text(_GAPS, encoding="utf-8")
    conn = connect_rw()
    try:
        _cand(conn, "cand-eeeeeeeeeeeeeeeeeeeeeeee", "sess-2", kind="gap")
    finally:
        conn.close()
    gap_ref, gap_anchor = _pool_ref(cdf_env, "design doc not yet written")
    ref, anchor = _ref_of(cdf_env["workspace"], firsts[4])
    unit = _event_unit("Swarm Out dashboard redesign (v6) shipped and running", "Shipped.",
                       "Live.", scope="project:P", adds=ref, overrides=[gap_ref]).model_dump(mode="json")
    decision = run_unit_v2(_row(unit, source="S7", tainted=False, session_project="P",
                                source_ref="sess-1"), cdf_env["workspace"])
    assert (decision.decision, decision.reason) == ("write", "adds")
    assert decision.version_vector["override_closes"] == [gap_anchor]
    assert {"predicate": "derived_from", "object_anchor": anchor} in decision.version_vector["relation_hints"]


def test_an_event_without_a_relation_is_still_written(cdf_env):
    from core.ingestion.api import connect_rw
    from core.ingestion.pipeline_v2 import run_unit_v2
    from tests.test_cdf_refactor import _row
    connect_rw().close()
    _timeline(cdf_env["workspace"])
    unit = _event_unit("Hive billing alarms decided", "Each Hive gets a cost alarm.", "Spend is visible.")
    decision = run_unit_v2(_row(unit.model_dump(mode="json"), source="S7", tainted=False,
                                session_project="P"), cdf_env["workspace"])
    assert (decision.decision, decision.reason) == ("write", "new")


# 3 ── the standard the extractors read

def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_event_standard_reaches_every_event_door():
    from core.ingestion import contracts_v2
    from core.ingestion.sources import s7_review
    rule = contracts_v2.EVENT_RULE
    step = contracts_v2.LATER_STEP
    assert step in rule
    for words in ("designed", "approved", "landed", "verified", "adds", "not a new event"):
        assert words in step, words
    # a door shown no recorded events (extract_llm) is not told to drop a later step
    assert step.startswith("When the recorded events are shown")
    # what the code does with it: the earlier event stays the record (pipeline_v2), no
    # promise that the later step is added to it
    assert "stays the one record of that work" in step
    for words in ("publishing", "a minor edit", "an admin action"):
        assert words in rule, words
    assert "a month" in rule
    review = _flat(s7_review.build_prompt(s7_review.ALLOWED_KINDS, {}, "", ""))
    assert _flat(rule) in review
    relations = _flat(s7_review.relation_prompt([], []))
    assert _flat(contracts_v2.LATER_STEP) in relations
    # the general "overrides rather than adds" rule leaves another conversation's event to
    # the event rule (an override of it is refused: overrides.check_session_own)
    assert "(other than an event another conversation recorded, below)" in relations
    deliver = (Path(__file__).resolve().parents[1] / "skills" / "s_autonomous-pipeline"
               / "stages" / "deliver.md").read_text(encoding="utf-8")
    assert '"adds": "<ref>"' in deliver


def test_the_ddd_unit_standard_reaches_both_doors():
    from core import knowledge_standard as ks
    from core.brain_bootstrap import apply as ap
    from core.ingestion.sources import s7_review
    sentence = next(s for s in ks.WRITE_WHEN if s.startswith("A project's knowledge"))
    for words in ("one unit per durable lesson or decision", "changes a future judgment",
                  "one incident", "one module's wiring", "a construction step", "git"):
        assert words in sentence, words
    assert _flat(sentence) in _flat(s7_review.build_prompt(s7_review.ALLOWED_KINDS, {}, "", ""))
    assert sentence in ap.entry_contract()["knowledge_standard"]


# 4 ── an explicit project scope reaches that project's DDD

def _ws(tmp_path: Path) -> Path:
    from tests.test_cdf_refactor import _ws as base
    ws = base(tmp_path)
    und = ws / "Projects" / "SwarmAI" / "2-understanding"
    und.mkdir(parents=True)
    for name, body in (("TECH.md", "## Runtime Traps\n\n## Conventions\n"),
                       ("IMPROVEMENT.md", "## What to Watch For\n\n## What Worked\n\n## What Failed\n"),
                       ("PROJECT.md", "## Recent Decisions\n\n## Open Items\n")):
        (und / name).write_text(f"# {name}\n\n{body}", encoding="utf-8")
    return ws


def _lesson(scope: str) -> dict:
    return {"kind": "cognition", "type": "pitfall", "scope": scope, "topic": "lesson",
            "title": "Inbox drafts wait for a click", "what": "A drafted reply is sent only on the person's click.",
            "applicability": "When changing Inbox send.", "evidence": ["event:e1"]}


@pytest.mark.parametrize("scope", ["project:SwarmAI", "project:swarmai", "project:SwarmAI/Inbox",
                                   "project:SwarmAI (CDF)", "project:SwarmAI: Hive",
                                   "project:SwarmAI - Swarm Out", "project:SwarmAI — Brain Hub",
                                   "project:Projects/SwarmAI"])
@pytest.mark.parametrize("session_project", [None, "Widget"])
def test_a_scope_naming_an_existing_project_goes_to_its_ddd(tmp_path, scope, session_project):
    from core.ingestion.pipeline_v2 import run_unit_v2
    from tests.test_cdf_refactor import _row
    decision = run_unit_v2(_row(_lesson(scope), session_project=session_project), _ws(tmp_path))
    d = decision.destination
    assert decision.decision == "write", decision.reason
    assert (d.store, d.project) == ("DDD", "SwarmAI"), d
    assert d.relative_path.startswith("Projects/SwarmAI/")


def test_a_declared_project_that_does_not_exist_does_not_hide_the_scope(tmp_path):
    from core.ingestion.pipeline_v2 import run_unit_v2
    from tests.test_cdf_refactor import _row
    decision = run_unit_v2(_row(_lesson("project:SwarmAI"), project_hint="Nope"), _ws(tmp_path))
    d = decision.destination
    assert (d.store, d.project) == ("DDD", "SwarmAI"), d


@pytest.mark.parametrize("scope,slug", [("project:CNTech tenets", "cntech-tenets"),
                                        ("project:SwarmAIish", "swarmaiish"),
                                        ("project:Swarm Out", "swarm-out")])
def test_a_scope_naming_no_project_still_goes_to_the_general_brain(tmp_path, scope, slug):
    from core.ingestion.pipeline_v2 import run_unit_v2
    from tests.test_cdf_refactor import _row
    decision = run_unit_v2(_row(_lesson(scope)), _ws(tmp_path))
    d = decision.destination
    assert (d.store, d.relative_path) == ("COMMON_DDD", f"Knowledge/Library/{slug}.md"), d


def test_a_hive_lesson_scoped_to_a_part_of_its_brain_is_not_refused(tmp_path):
    from core.ingestion.pipeline_v2 import run_unit_v2
    from tests.test_cdf_refactor import _row
    row = _row(_lesson("project:SwarmAI/Inbox"), source="S6", source_ref="hive:so_x",
               project_hint="SwarmAI")
    decision = run_unit_v2(row, _ws(tmp_path))
    assert decision.reason != "project_outside_brain"
    assert (decision.destination.store, decision.destination.project) == ("DDD", "SwarmAI")


# failure modes and the relations turn

def test_an_unresolved_adds_leaves_the_event_new(cdf_env):
    from core.ingestion.api import connect_rw
    from core.ingestion.pipeline_v2 import run_unit_v2
    from tests.test_cdf_refactor import _row
    connect_rw().close()
    _timeline(cdf_env["workspace"])
    unit = _event_unit("Swarm Out dashboard redesign (v6) shipped and running", "Shipped.", "Live.",
                       adds="0123abcd | ### 2026-11-20 An event nobody recorded").model_dump(mode="json")
    decision = run_unit_v2(_row(unit, source="S7", tainted=False, session_project="P"),
                           cdf_env["workspace"])
    assert (decision.decision, decision.reason) == ("write", "new")
    assert [r["relation"] for r in decision.version_vector["relation_refused"]] == ["adds"]


def test_an_event_cannot_be_same_and_adds():
    from core.ingestion.sources import s7_review
    unit = _event_unit("Swarm Out dashboard redesign (v6) shipped and running", "Shipped.", "Live.")
    ref = "0123abcd | ### 2026-11-20 Swarm Out dashboard redesign approved"
    with pytest.raises(ValueError):
        _event_unit("x", "y", "z", same=ref, adds=ref)
    (out,) = s7_review.apply_relations([unit.model_copy(update={"same": ref})], {"relations": [
        {"unit": 1, "relation": "adds", "target": ref}]})
    assert (out.same, out.adds) == (ref, None)        # the pair is refused; the unit stands


def test_a_declaration_the_unit_cannot_hold_is_logged(caplog):
    import logging
    from core.ingestion.contracts_v2 import SOURCE_UNIT
    from core.ingestion.sources import s7_review
    ref = "0123abcd | - [guideline] **Mockup first** Show a mockup"
    note = SOURCE_UNIT.validate_python({
        "kind": "reference", "reference_kind": "note", "title": "Notes", "summary": "s",
        "body": "b", "evidence": ["event:e"]})
    with caplog.at_level(logging.INFO, logger=s7_review.logger.name):
        (out,) = s7_review.apply_relations([note], {"relations": [
            {"unit": 1, "relation": "same", "target": ref, "repeats": ref, "overrides": [ref]}]})
    assert out == note
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "unit 1" in logged and "repeats" in logged
