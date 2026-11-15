"""The bigrams of one Chinese phrase share one term's weight in BM25
(memory_index._bm25_query_weights): 1/n each for an n-bigram phrase, 1 for an English
word or identifier, so a query with no CJK scores exactly as before."""
import math

import pytest

from core import memory_index
from core.cjk_index import expand_cjk, expand_cjk_runs


# ── the old scorer, written out: every query term weight 1 ────────────────────

def _reference_scores(query: str, docs: dict[str, str]) -> dict[str, float]:
    q = set(memory_index._bm25_tokenize(query))
    toks = {k: memory_index._bm25_tokenize(t) for k, t in docs.items()}
    n = len(docs)
    avgdl = sum(len(t) for t in toks.values()) / n
    out = {}
    for k, ts in toks.items():
        s = 0.0
        for term in q:
            f = ts.count(term)
            if not f:
                continue
            df = sum(1 for x in toks.values() if term in x)
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            s += idf * (f * (memory_index.BM25_K1 + 1.0)) / (
                f + memory_index.BM25_K1 * (1.0 - memory_index.BM25_B + memory_index.BM25_B * len(ts) / avgdl))
        if s > 0:
            out[k] = s
    return out


DOCS = {
    "a": "dispatch timeout on the Hive worker, 派发超时",
    "b": "session tab isolation 会话隔离 for every tab",
    "c": "the prompt cache stays byte-stable across sessions",
    "d": "保持一致 the frontend mirrors backend state",
}


# ── R1: phrase weighting ──────────────────────────────────────────────────────

def test_the_bigrams_of_one_phrase_share_one_terms_weight():
    w = memory_index._bm25_query_weights("派发超时")
    assert w == pytest.approx({"派发": 1 / 3, "发超": 1 / 3, "超时": 1 / 3})


def test_words_identifiers_lone_chars_and_embedded_ascii_keep_weight_one():
    w = memory_index._bm25_query_weights("dispatch session_router 用goal-pipeline跑")
    assert w == {"dispatch": 1.0, "session_router": 1.0, "用": 1.0, "goal-pipeline": 1.0, "跑": 1.0}


def test_a_token_reached_twice_takes_its_larger_weight_and_repeats_add_up_in_a_phrase():
    assert memory_index._bm25_query_weights("配置 配置文件")["配置"] == 1.0
    assert memory_index._bm25_query_weights("哈哈哈") == {"哈哈": 1.0}


def test_weight_terms_are_exactly_the_tokenizers_terms():
    for q in ("派发超时 不一致 dispatch", "用goal-pipeline跑 설정파일", "the session", "會話 タブ"):
        assert set(memory_index._bm25_query_weights(q)) == set(memory_index._bm25_tokenize(q))


def test_expand_cjk_runs_is_expand_cjk_with_run_boundaries():
    for tok in ("配置文件", "用goal-pipeline跑", "a", "___配置", "설정파일", "x-y"):
        flat = [t for run in expand_cjk_runs(tok) for t in run]
        assert flat == expand_cjk(tok).split()
    assert expand_cjk_runs("用goal-pipeline跑配置") == [["用"], ["goal-pipeline"], ["跑配", "配置"]]


def test_an_english_query_scores_exactly_as_before():
    for q in ("dispatch timeout", "session tab", "prompt cache byte-stable sessions"):
        assert memory_index._bm25_scores(q, DOCS) == _reference_scores(q, DOCS)


def test_a_whole_phrase_found_scores_as_one_word_found():
    docs = {"zh": "派发超时", "en": "dispatch alpha beta", "other": "unrelated words here"}
    got = memory_index._bm25_scores("派发超时 dispatch", docs)
    assert got["zh"] == pytest.approx(got["en"])
    old = _reference_scores("派发超时 dispatch", docs)
    assert old["zh"] == pytest.approx(3 * old["en"])          # what the weighting removes


def test_two_query_concepts_beat_one_long_phrase():
    docs = {"two": "dispatch 会话 retry notes", "phrase": "会话超时 notes here",
            "x": "nothing relevant at all", "y": "still nothing relevant"}
    got = memory_index._bm25_scores("会话超时 dispatch", docs)
    assert got["two"] > got["phrase"]
    old = _reference_scores("会话超时 dispatch", docs)
    assert old["phrase"] > old["two"]


def test_an_unspaced_clause_is_one_phrase_next_to_an_identifier():
    """The clause's bigrams share one weight; the identifier keeps its own, so the
    entry naming it ranks first (the measured choice, see _bm25_query_weights)."""
    q = "session_router 里派发任务为什么超时"
    w = memory_index._bm25_query_weights(q)
    assert w["session_router"] == 1.0
    assert w["派发"] == pytest.approx(1 / 9)
    docs = {"ident": "session_router routes each turn", "clause": "派发任务 超时 notes",
            "x": "nothing relevant here", "y": "still nothing relevant"}
    got = memory_index._bm25_scores(q, docs)
    assert got["ident"] > got["clause"]

