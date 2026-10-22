"""Tests for first-message recall quality: keyword extraction and core/recall_filter.py.

Properties:
- keyword extraction splits CJK from ASCII, cuts Chinese function words, and
  yields nothing for turn-control messages ("continue", "继续");
- the relevance gate keeps hits covering enough query terms, drops the rest;
- a single plain word recalls nothing; a single code-like term still recalls;
- code symbols survive only for code-like terms;
- a hit already present in the system prompt is dropped;
- each domain is held to its character budget.
"""

from core.recall_filter import DOMAIN_CHAR_CAPS, filter_recall
from core.recall_multi import BucketedRecall
from core.session_router import _extract_query_keywords


def test_keywords_split_cjk_and_ascii():
    assert _extract_query_keywords(
        "你正好E2E的看下我们现在的recall机制有什么问题吗") == "E2E recall 机制"
    assert _extract_query_keywords("的Memory很大") == "Memory"
    assert _extract_query_keywords("context_health_hook 的 transcript 同步为什么慢") == \
        "context_health_hook transcript 同步"


def test_turn_control_messages_yield_no_keywords():
    for q in ("continue", "继续", "你继续啊", "好的"):
        assert _extract_query_keywords(q) == "", q


def _result(query, **buckets):
    return BucketedRecall(query=query, buckets=buckets,
                          hit_layers={k: "fts" for k in buckets})


def test_relevance_gate_needs_enough_terms():
    r = _result("recall 机制 分词", library=[
        {"source": "a.md", "heading": "x", "content": "recall 机制 的设计说明"},
        {"source": "b.md", "heading": "y", "content": "只提到 recall 一次"},
    ])
    kept = filter_recall(r).buckets["library"]
    assert [h["source"] for h in kept] == ["a.md"]


def test_single_plain_word_recalls_nothing_but_code_term_does():
    hit = {"source": "a.md", "content": "commit the change; commit again"}
    assert filter_recall(_result("commit", library=[hit])).buckets["library"] == []
    code = {"source": "b.md", "content": "session_router owns the slots"}
    assert filter_recall(_result("session_router", library=[code])).buckets["library"]


def test_code_symbols_only_for_code_like_terms():
    sym = [{"id": "core.session_router.build", "name": "build"}]
    assert filter_recall(_result("build 重启", codeintel=sym)).buckets["codeintel"] == []
    assert filter_recall(_result("session_router build", codeintel=sym)).buckets["codeintel"]


def test_hit_already_in_prompt_is_dropped():
    body = "## COE Registry\n- [pitfall] the recall leg re-injected sections " * 3
    prompt = "base prompt\n" + body + "\nmore"
    r = _result("recall leg sections", context_files=[{"section": "COE", "content": body}],
                library=[{"source": "n.md", "content": "a recall leg note about sections"}])
    out = filter_recall(r, prompt)
    assert out.buckets["context_files"] == []
    assert out.buckets["library"]


def test_domain_budget():
    hits = [{"source": f"{i}.md", "content": "recall budget " + "x" * 2000} for i in range(8)]
    kept = filter_recall(_result("recall budget", library=hits)).buckets["library"]
    assert sum(len(h["content"]) for h in kept) <= DOMAIN_CHAR_CAPS["library"] + 10
    assert kept and len(kept) < 8
