"""Tests for the 2026-10-26 recall precision/coverage changes (design
``docs/design/2026-10-26-recall-precision-and-coverage.md``).

Each test pins one rule and was mutation-checked (reverting the rule turns it red):
- D1 library rank slots: at most 3 ordinary hits, memory archives get their own
  2 slots and budget, fetched feeds (Signals, DailyBriefs) are never injected;
- D1 session age: a past session older than 7 days needs 3 matched terms;
- D3 project names match whole (no "agent" -> Agent-DLC), next to CJK too, and
  a session's bound repo (signal-2) names the project;
- D3 the DDD leg runs on a later message once the project is known, and a warm
  turn's DDD block reaches the query stash next to the recall block;
- D4 live-MEMORY recall scores only entries outside the prompt, so a relevant
  dormant entry surfaces even when in-prompt sections outrank it.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import core.recall_filter as rf
from core.recall_filter import filter_recall
from core.recall_multi import BucketedRecall

_Q = "pipeline worktree isolation"


def _lib(src: str, n: int) -> dict:
    return {"source": src, "heading": f"h{n}",
            "content": f"pipeline worktree isolation note {n} " + "x" * 80, "score": 1.0}


def test_library_keeps_three_ordinary_plus_two_archive_hits():
    hits = [_lib(f"Notes/n{i}.md", i) for i in range(5)] + \
           [_lib(f".context/Archives/MEMORY-archive-2026-0{i}.md", 10 + i) for i in range(3)]
    out = filter_recall(BucketedRecall(query=_Q, buckets={"library": hits}))
    srcs = [h["source"] for h in out.buckets["library"]]
    assert srcs == ["Notes/n0.md", "Notes/n1.md", "Notes/n2.md",
                    ".context/Archives/MEMORY-archive-2026-00.md",
                    ".context/Archives/MEMORY-archive-2026-01.md"]


def test_archive_hits_have_their_own_budget():
    big = [{**_lib(f"Notes/n{i}.md", i), "content": "pipeline worktree isolation " + "y" * 2000}
           for i in range(3)]
    arch = _lib(".context/Archives/MEMORY-archive-2026-07.md", 9)
    out = filter_recall(BucketedRecall(query=_Q, buckets={"library": big + [arch]}))
    got = [h for h in out.buckets["library"] if h["source"] == arch["source"]]
    # Shared with the ordinary hits it would get the ~100 chars they leave over.
    assert got and len(got[0]["content"]) == len(arch["content"])


def test_fetched_feeds_are_never_injected():
    hits = [_lib("Signals/2026-10-01-hn.md", 1), _lib("Archives/Signals/x.md", 2),
            _lib("DailyBriefs/2026-10-02.md", 3), _lib("Designs/d.md", 4)]
    out = filter_recall(BucketedRecall(query=_Q, buckets={"library": hits}))
    assert [h["source"] for h in out.buckets["library"]] == ["Designs/d.md"]


def test_old_session_needs_three_terms(monkeypatch):
    monkeypatch.setattr(rf, "_today", lambda: "2026-10-26")
    two = "**{d} · session abc**\n- User: pipeline worktree " + "z" * 60
    three = two + " isolation"
    hits = [{"text": two.format(d="2026-10-01"), "date": "2026-10-01"},
            {"text": three.format(d="2026-10-01"), "date": "2026-10-01"},
            {"text": two.format(d="2026-10-24"), "date": "2026-10-24"}]
    out = filter_recall(BucketedRecall(query=_Q, buckets={"session": hits}))
    assert [(h["date"], "isolation" in h["text"]) for h in out.buckets["session"]] == \
        [("2026-10-01", True), ("2026-10-24", False)]


def test_project_names_match_whole_and_next_to_cjk():
    from core.recall_multi import detect_active_project
    cands = ["Agent-DLC", "ai_ready_repo", "SwarmAI"]
    assert detect_active_project(query="参考其它 agent 的做法", candidates=cands)[0] is None
    assert detect_active_project(query="local repo 和 worktree", candidates=cands)[0] is None
    assert detect_active_project(query="看下我们的agent-dlc进展", candidates=cands)[0] == "Agent-DLC"
    assert detect_active_project(query="SwarmAI看下这个", candidates=cands)[0] == "SwarmAI"


def test_bound_repo_names_the_project():
    from core.recall_multi import detect_active_project
    assert detect_active_project(query="继续", candidates=["SwarmAI", "AIDLC"],
                                 session_bound_project="SwarmAI") == \
        ("SwarmAI", "signal2_bound_repo")


def test_repo_guidance_reports_the_last_bound_project(tmp_path):
    """A tool call in a bound repo makes it the session's project; a later call
    in another bound repo replaces it (the LAST one the session worked in)."""
    import os
    from core import repo_guidance as rg
    roots = {}
    for name in ("swarm", "aidlc"):
        r = tmp_path / name
        (r / ".git").mkdir(parents=True)
        (r / "a.py").write_text("x")
        roots[name] = os.path.realpath(r)
    bound = {roots["swarm"]: "SwarmAI", roots["aidlc"]: "AIDLC"}
    rg.forget("s-bound")
    try:
        with patch.object(rg, "_bound_projects", return_value=bound), \
                patch.object(rg, "_excluded", return_value=False), \
                patch.object(rg, "_lessons", return_value=None), \
                patch.object(rg, "_ddd_docs", return_value=[]):
            rg._judge("s-bound", {"tool_name": "Read",
                                  "tool_input": {"file_path": roots["swarm"] + "/a.py"}})
            assert rg.session_bound_project("s-bound") == "SwarmAI"
            rg._judge("s-bound", {"tool_name": "Read",
                                  "tool_input": {"file_path": roots["aidlc"] + "/a.py"}})
            assert rg.session_bound_project("s-bound") == "AIDLC"
        assert rg.session_bound_project("nobody") is None
    finally:
        rg.forget("s-bound")


def _unit(**kw):
    u = MagicMock()
    u.is_channel_session = False
    u._recall_injected = False
    u._ddd_injected = False
    u._recall_keyword_misses = 0
    u._active_project = None
    u._recall_query_block = None
    u._sdk_session_id = "sdk-1"
    for k, v in kw.items():
        setattr(u, k, v)
    return u


async def test_ddd_runs_on_a_later_message_once_the_project_is_known():
    import core.session_router as sr
    calls = []

    def _fake_ddd(options, msg, project, deliver=None):
        calls.append(project)
        deliver("\n\n## Project DDD — [DDD:SwarmAI]\nsection list")

    u, o = _unit(), MagicMock()
    o.system_prompt = "BASE"
    bound = {"v": None}
    with patch("core.repo_guidance.session_bound_project", lambda sid: bound["v"]), \
            patch("core.recall_multi.list_project_names", return_value=["SwarmAI"]), \
            patch.object(sr, "_inject_ddd_for_active_project", side_effect=_fake_ddd), \
            patch.object(sr, "_unified_recall_body", return_value=("", None)):
        await sr._maybe_inject_recall("帮我看下这个 pipeline worktree 问题", o, u,
                                      should_mutate_system_prompt=True)
        assert calls == [] and u._ddd_injected is False      # no project yet: retry later
        bound["v"] = "SwarmAI"                              # the agent touched the repo
        u._recall_query_block = None
        await sr._maybe_inject_recall("继续修 worktree 隔离", o, u,
                                      should_mutate_system_prompt=False)
    assert calls == [("SwarmAI", "signal2_bound_repo")]
    assert u._ddd_injected is True
    # A warm turn discards system_prompt: the block must be in the query stash.
    assert "[DDD:SwarmAI]" in (u._recall_query_block or "")
    assert "[DDD:" not in o.system_prompt


def test_warm_turn_keeps_both_ddd_and_recall_blocks():
    import core.session_router as sr
    u, o = _unit(), MagicMock()
    o.system_prompt = "BASE"
    sr._deliver_block(o, u, "\n\n## Project DDD — [DDD:X]", False)
    sr._deliver_block(o, u, "\n\n## Recalled Knowledge\nbody", False)
    assert "[DDD:X]" in u._recall_query_block and "Recalled Knowledge" in u._recall_query_block
    assert o.system_prompt == "BASE"


_MEMORY = "".join(
    f"## Active {i}\n- pipeline worktree isolation rule number {i}, kept active and "
    f"already in the prompt; pipeline worktree isolation pipeline worktree.\n"
    for i in range(4)
) + ("## Old\n- [D1] the worktree isolation decision from March, gone dormant.\n"
     "  <!-- ref:0 | last:2026-03-01 | decay:dormant -->\n")


def test_live_memory_recall_scores_only_entries_outside_the_prompt(tmp_path):
    from core.context_directory_loader import _filter_dormant_entries
    from core.recall_multi import recall_all
    ws = tmp_path / "SwarmWS"
    (ws / ".context").mkdir(parents=True)
    (ws / ".context" / "MEMORY.md").write_text(_MEMORY, encoding="utf-8")
    prompt = _filter_dormant_entries(_MEMORY)
    assert "decision from March" not in prompt
    with patch("core.project_registry.get_swarmws", return_value=ws):
        res = recall_all(_Q, domains=("context_files",), prompt_text=prompt)
        allin = recall_all(_Q, domains=("context_files",), prompt_text=_MEMORY)
        cli = recall_all(_Q, domains=("context_files",))
    out = filter_recall(res, prompt)
    assert "decision from March" in out.buckets["context_files"][0]["content"]
    assert allin.buckets["context_files"] == [] and allin.hit_layers["context_files"] == "in_prompt"
    assert cli.buckets["context_files"]           # no prompt_text: whole-file, as before


def test_code_hits_match_the_symbol_name_not_a_project_name(monkeypatch):
    import core.recall_multi as rm
    monkeypatch.setattr(rm, "list_project_names", lambda: ["SwarmAI"])
    hits = [{"name": "_Parser", "id": "a", "file_path": "skills/s_autonomous-pipeline/x.py"},
            {"name": "_find_swarmai_repo", "id": "b", "file_path": "core/x.py"},
            {"name": "build", "id": "c", "file_path": "core/session_router.py"}]
    out = filter_recall(BucketedRecall(
        query="SwarmAI s_autonomous-pipeline session_router", buckets={"codeintel": hits}))
    assert [h["name"] for h in out.buckets["codeintel"]] == ["build"]   # via its file
