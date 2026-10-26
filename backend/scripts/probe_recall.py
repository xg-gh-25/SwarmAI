#!/usr/bin/env python3
"""probe_recall — product eval probes for recall.

Contract: scripts/_probe_kit.py. Runs in the isolated workspace / data dir the
eval runner gives a product case. Subcommands:
  provenance  injected recall is always labelled as retrieved ([RECALLED]) before
              its content, on a cold spawn (system prompt) and on a warm reuse
              (prefixed to the user message)
  privacy     only a desktop tab or the owner's private chat gets recall, and a
              file excluded for the session cannot be read through recall
  ddd_scope   project-doc recall stays inside the active project, and a question
              that names two projects resolves to none
  archive     an archived memory shard is findable through library recall
  ranking     the right section ranks near the top: a project-doc section for an
              English question, and memory sections for Chinese questions
"""
from __future__ import annotations

import asyncio
from unittest import mock

from scripts._probe_kit import run, verdict

BODY = "RECALLED_BODY_7f3"


def _unit():
    u = mock.MagicMock()
    u._recall_injected = False
    u.is_channel_session = False
    u._recall_keyword_misses = 0
    u._recall_followups = 0
    u._recall_query_block = None
    return u


def _options():
    o = mock.MagicMock()
    o.system_prompt = "## Base"
    return o


def _inject(unit, options, *, ctx=None, warm=False):
    from core import session_router as sr

    with mock.patch("core.session_router._unified_recall_body", return_value=(None, None)), \
         mock.patch("core.session_router._recall_for_query", return_value=BODY):
        asyncio.run(sr._maybe_inject_recall(
            user_message="How does the evolution pipeline work?", options=options, unit=unit,
            channel_context=ctx, should_mutate_system_prompt=not warm))


# ── provenance ────────────────────────────────────────────────────────────────

def _provenance_holds() -> tuple[bool, str]:
    from core.injected_blocks import RECALL_HEADER

    label = RECALL_HEADER + "\n> **[RECALLED]**"
    cold_u, cold_o = _unit(), _options()
    _inject(cold_u, cold_o)
    sp = cold_o.system_prompt
    if BODY not in sp or label not in sp or sp.index(label) > sp.index(BODY):
        return False, "cold spawn: recall content without the [RECALLED] label before it"
    warm_u, warm_o = _unit(), _options()
    _inject(warm_u, warm_o, warm=True)
    block = warm_u._recall_query_block or ""
    if warm_o.system_prompt != "## Base":
        return False, "warm reuse wrote the system prompt (the reused process ignores it)"
    if BODY not in block or label not in block or block.index(label) > block.index(BODY):
        return False, "warm reuse: recall block missing or unlabelled"
    return True, ""


def provenance(negative: bool) -> int:
    def broken():
        with mock.patch("core.injected_blocks.RECALL_PROVENANCE", ""):
            return _provenance_holds()
    return verdict("RCL_PROVENANCE", negative, _provenance_holds, broken)


# ── privacy ───────────────────────────────────────────────────────────────────

def _ctx(chat_type, owner=True):
    return {"channel_type": "slack", "is_owner": owner, "chat_type": chat_type,
            "is_group": chat_type in ("group", "channel", "mpim")}


def _privacy_holds() -> tuple[bool, str]:
    from core.context_recall import recall_context

    for ctx, should in ((None, True), (_ctx("im"), True), (_ctx("mpim"), False),
                        (_ctx("group"), False), (_ctx("im", owner=False), False)):
        u, o = _unit(), _options()
        _inject(u, o, ctx=ctx)
        got = BODY in o.system_prompt
        if got != should:
            return False, f"recall {'missing' if should else 'injected'} for {ctx}"
    memory = "## Secrets\n\nSENTINEL_PRIVATE_MEMORY_7f3 owner only.\n"
    r = recall_context("memory.md", "secrets owner", memory_content=memory,
                       policy_excluded_files=frozenset({"MEMORY.md"}))
    if getattr(r, "allowed", True) or "SENTINEL_PRIVATE_MEMORY_7f3" in str(getattr(r, "content", r)):
        return False, "an excluded file was readable through recall"
    return True, ""


def privacy(negative: bool) -> int:
    def broken():  # the channel rule switched off: every chat recalls
        with mock.patch("core.session_router._channel_recall_allowed", return_value=True):
            return _privacy_holds()
    return verdict("RCL_PRIVACY", negative, _privacy_holds, broken)


# ── ddd_scope ─────────────────────────────────────────────────────────────────

def _seed_projects():
    from config import get_swarmws
    from core.ddd_paths import ddd_path

    for name, word in (("Alpha", "ALPHA_ONLY_SENTINEL_7f3"), ("Beta", "BETA_ONLY_SENTINEL_7f3")):
        base = get_swarmws() / "Projects" / name
        tech = ddd_path(base, "TECH.md")
        tech.parent.mkdir(parents=True, exist_ok=True)
        tech.write_text(f"# {name} TECH\n\n## Storage engine of {word}\n\n{name} stores its storage "
                        f"engine notes here: storage engine compaction.\n", encoding="utf-8")


def _render(result) -> str:
    from core.recall_multi import render_bucketed_recall
    return render_bucketed_recall(result)


def _ddd_holds(project_for_beta: str) -> tuple[bool, str]:
    from core.recall_multi import detect_active_project, recall_all

    # DDD hits name the doc and section (the agent reads the file), so the check
    # is on which project's section came back.
    result = recall_all("storage engine compaction", project=project_for_beta, domains=("ddd",))
    text = " ".join(str(h.get("section", "")) for h in result.buckets.get("ddd", []))
    if "BETA_ONLY_SENTINEL_7f3" not in text:
        return False, "Beta's own doc was not recalled for Beta"
    if "ALPHA_ONLY_SENTINEL_7f3" in text:
        return False, "another project's doc leaked into Beta's recall"
    project, _why = detect_active_project(query="compare Alpha and Beta storage",
                                          candidates=["Alpha", "Beta"])
    if project is not None:
        return False, f"a question naming two projects resolved to {project!r}"
    return True, ""


def ddd_scope(negative: bool) -> int:
    _seed_projects()
    return verdict("RCL_DDD_SCOPE", negative, lambda: _ddd_holds("Beta"),
                   lambda: _ddd_holds("Alpha"))


# ── archive ───────────────────────────────────────────────────────────────────

def _archive_holds(index_archive: bool) -> tuple[bool, str]:
    from config import get_swarmws
    from core.knowledge_store import KnowledgeStore, sync_knowledge_index
    from core.recall_multi import recall_all
    from core.vec_db import open_vec_db

    ws = get_swarmws()
    (ws / "Knowledge" / "Notes").mkdir(parents=True, exist_ok=True)
    (ws / "Knowledge" / "Notes" / "n.md").write_text("# Note\n\nan ordinary note\n", encoding="utf-8")
    (ws / ".context").mkdir(parents=True, exist_ok=True)
    (ws / ".context" / "MEMORY.md").write_text(
        "## Live\n\n- LIVE_MEMORY_SENTINEL_7f3 quasar lighthouse\n", encoding="utf-8")
    shard = ws / ".context" / "MEMORY-archive-2026-01.md"
    if index_archive:
        shard.write_text("## Decisions\n\n- [decision] ARCHIVED_SENTINEL_7f3 quasar lighthouse "
                         "decommissioned. (2026-01-05)\n", encoding="utf-8")
    with open_vec_db() as conn:
        store = KnowledgeStore(conn)
        store.ensure_tables()
        sync_knowledge_index(store, ws / "Knowledge")
    text = _render(recall_all("quasar lighthouse", domains=("library",)))
    if "ARCHIVED_SENTINEL_7f3" not in text:
        return False, "an archived memory is not reachable through library recall"
    if "LIVE_MEMORY_SENTINEL_7f3" in text:
        return False, "the live MEMORY.md was indexed (it is injected in full, not recalled)"
    return True, ""


def archive(negative: bool) -> int:
    return verdict("RCL_ARCHIVE", negative, lambda: _archive_holds(True),
                   lambda: _archive_holds(False))


# ── ranking ───────────────────────────────────────────────────────────────────

_TECH = """# Demo — Tech

## Storage
Tickets are stored in Postgres with a nightly vacuum; attachments live in object storage.

## Architecture
The autonomous pipeline runs in stages: evaluate the requirement, build the change,
review it, test it, then deliver it. Each stage hands a report to the next.

## Deploy
Releases go out through a blue-green deploy behind the load balancer.
"""
_PRODUCT = """# Demo — Product

## Strategic Positioning
A dashboard for support teams; the pipeline of tickets is shown by age.
"""
_MEMORY = """# Memory

## Decisions
- [decision] **用 pnpm 管理依赖** 不混用包管理器。

## Open Threads
- 多个标签页同时运行时的 session 资源仲裁：每个 tab 必须隔离，不能抢占别的标签页的会话。

## Pitfalls
- [pitfall] **restore 超时导致死锁窗口** 恢复会话时等待超时，锁没有释放，修复方法是缩短窗口并在超时后释放锁。

## Guidelines
- [guideline] **提交前先跑受影响的测试** 不跑全量。
"""
_QUERIES = (("ddd", "how does the autonomous pipeline work — its stages", ("TECH.md", "Architecture")),
            ("memory", "多个标签页之间怎么做资源仲裁", "Open Threads"),
            ("memory", "恢复超时导致的死锁怎么修", "Pitfalls"))


def _ranking_holds(k: int = 2) -> tuple[bool, str]:
    from scripts.recall_suite import _rank_of_gold_context_files, _rank_of_gold_ddd

    for domain, query, gold in _QUERIES:
        rank = (_rank_of_gold_ddd(query, {"TECH.md": _TECH, "PRODUCT.md": _PRODUCT}, gold[0], gold[1], k)
                if domain == "ddd" else _rank_of_gold_context_files(query, _MEMORY, gold, k))
        if not rank:
            return False, f"{query!r}: {gold} is not in the top {k}"
    return True, ""


def ranking(negative: bool) -> int:
    import re

    from core import memory_index

    def ascii_only(text):  # a tokenizer that has lost its Chinese handling
        return re.findall(r"[a-z0-9]+", (text or "").lower())

    def broken():
        with mock.patch.object(memory_index, "_bm25_tokenize", ascii_only), \
                mock.patch.object(memory_index, "_bm25_tokenize_doc", ascii_only):
            return _ranking_holds()
    return verdict("RCL_RANKING", negative, _ranking_holds, broken)


if __name__ == "__main__":
    raise SystemExit(run({"provenance": provenance, "privacy": privacy,
                          "ddd_scope": ddd_scope, "archive": archive, "ranking": ranking}))
