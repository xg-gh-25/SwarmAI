"""A Brain's term map (core.term_map, a "## Term map" section and the two-script
glossary headings of its CONTEXT.md) expands a Chinese query with English terms at every
Brain recall door: find, the push-recall ddd legs and the chat first-message DDD leg. A
Brain without a map scores as before; the probe script reports before and after."""
import json
from types import SimpleNamespace

import pytest

from core import addressing, memory_index, term_map


# ── R2: the term map ──────────────────────────────────────────────────────────

CONTEXT = """# Demo — Domain Language

## Core Concepts

**Session（会话）**
One conversation in one tab.

**标签页 / Tab**
A UI container.

**Memory (MEMORY.md)**
No Chinese name: not a mapping.

## Term map

- 派发, 分派 → dispatch
- 超时 -> timeout, time out
- **不一致** => inconsistent
- 一致 = consistent
- 库 → library
- code → source
- 缓存，快取 → `cache`
- not a mapping line
* 备份、存档 → backup; archive

```
- 围栏 → fenced
```

## Other

- 错误 → error
"""


def test_parse_reads_the_term_map_section_and_two_script_glossary_headings():
    m = term_map.parse_term_map(CONTEXT)
    assert m["会话"] == ("Session",)
    assert m["标签页"] == ("Tab",)
    assert m["派发"] == m["分派"] == ("dispatch",)
    assert m["超时"] == ("timeout", "time out")
    assert m["不一致"] == ("inconsistent",) and m["一致"] == ("consistent",)
    assert m["缓存"] == m["快取"] == ("cache",)
    assert m["备份"] == m["存档"] == ("backup", "archive")
    # a one-character key, a Latin key, fenced code and other sections' bullets are not mappings
    assert not {"库", "code", "围栏", "错误"} & set(m)
    assert all(v for v in m.values())


def test_expansion_appends_mapped_english_longest_key_first():
    m = term_map.parse_term_map(CONTEXT)
    assert term_map.expand_query("派发超时为什么不一致", m) == \
        "派发超时为什么不一致 inconsistent dispatch timeout time out"
    assert "consistent" not in term_map.mapped_terms("不一致", m)
    assert term_map.mapped_terms("保持一致", m) == ["consistent"]


def test_expansion_is_idempotent_and_skips_terms_already_asked():
    m = term_map.parse_term_map(CONTEXT)
    once = term_map.expand_query("会话超时", m)
    assert term_map.expand_query(once, m) == once
    assert term_map.mapped_terms("会话 timeout 超时", m) == ["Session", "time out"]


def test_no_cjk_or_no_map_leaves_the_query_unchanged():
    m = term_map.parse_term_map(CONTEXT)
    assert term_map.expand_query("dispatch 库 timeout", {}) == "dispatch 库 timeout"
    assert term_map.expand_query("session timeout", m) == "session timeout"
    assert term_map.expand_query("", m) == ""


def test_load_is_cached_per_file_and_rereads_a_changed_file(tmp_path):
    term_map._reset_cache()
    assert term_map.load_term_map(tmp_path) == {}            # no CONTEXT.md
    assert term_map.load_term_map(None) == {}
    f = tmp_path / "CONTEXT.md"
    f.write_text("## Term map\n- 派发 → dispatch\n", encoding="utf-8")
    assert term_map.load_term_map(tmp_path) == {"派发": ("dispatch",)}
    f.write_text("## Term map\n- 派发 → dispatch, send out\n", encoding="utf-8")
    assert term_map.load_term_map(tmp_path) == {"派发": ("dispatch", "send out")}
    f.write_text("## Term map\n" + "- 派发 → dispatch\n" * 40000, encoding="utf-8")
    assert term_map.load_term_map(tmp_path) == {}            # over MAX_FILE_BYTES: not read
    assert term_map.expand_for_brain(tmp_path, "派发") == "派发"


def test_an_unchanged_map_is_parsed_once_and_a_same_size_edit_is_seen(tmp_path, monkeypatch):
    import os
    term_map._reset_cache()
    f = tmp_path / "CONTEXT.md"
    f.write_text("## Term map\n- 派发 → dispatch\n", encoding="utf-8")
    calls = {"n": 0}
    real = term_map.parse_term_map

    def counting(text):
        calls["n"] += 1
        return real(text)
    monkeypatch.setattr(term_map, "parse_term_map", counting)
    for _ in range(50):
        term_map.expand_for_brain(tmp_path, "派发")
    assert calls["n"] == 1                                    # one stat per turn after the first
    st = f.stat()
    f.write_text("## Term map\n- 派发 → transmit\n", encoding="utf-8")   # same size
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))          # mtime put back
    assert f.stat().st_size == st.st_size
    assert term_map.load_term_map(tmp_path) == {"派发": ("transmit",)}


def test_mentions_matches_whole_words_only():
    assert term_map.mentions("the dispatch timed out", "dispatch")
    assert term_map.mentions("会话session state", "session")
    assert term_map.mentions("a time-out here", "time out")
    assert not term_map.mentions("read about the output of a time-based job", "time out")
    assert not term_map.mentions("sessions", "session")


def test_expansion_never_writes(tmp_path):
    term_map._reset_cache()
    f = tmp_path / "CONTEXT.md"
    f.write_text(CONTEXT, encoding="utf-8")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*")}
    term_map.expand_for_brain(tmp_path, "派发超时")
    assert {p: p.read_bytes() for p in tmp_path.rglob("*")} == before


# ── the Brain doors ───────────────────────────────────────────────────────────

TECH = """# TECH

## Architecture

- [model] **Dispatch timeout on the worker** A dispatched task that exceeds its timeout is reported as failed.
- [model] **Session isolation per tab** Every tab owns its session.
- [pitfall] **Prompt cache needs a byte-stable prefix** The system prompt must not change between turns.
""" + "\n".join(f"- [pitfall] **Filler {i}** — wombat note number {i}." for i in range(30)) + "\n"

MAP = "## Term map\n- 派发 → dispatch\n- 超时 → timeout\n- 会话 → session\n- 标签页 → tab\n"


@pytest.fixture
def brain(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("SWARMWS", str(tmp_path))
    root = tmp_path / "Projects" / "P"
    (root / "2-understanding").mkdir(parents=True)
    (root / "2-understanding" / "TECH.md").write_text(TECH, encoding="utf-8")
    term_map._reset_cache()
    memory_index._reset_doc_token_cache()
    return root


def _titles(units) -> list[str]:
    return [u.text for u in units]


def test_find_expands_a_chinese_query_with_the_brains_map(brain):
    assert not any("Dispatch timeout" in t for t in _titles(addressing.find("P", "派发超时").units))
    (brain / "CONTEXT.md").write_text(MAP, encoding="utf-8")
    assert "Dispatch timeout" in addressing.find("P", "派发超时").units[0].text


def test_find_without_a_map_ranks_english_as_before(brain):
    before = _titles(addressing.find("P", "dispatch timeout").units)
    (brain / "CONTEXT.md").write_text(MAP, encoding="utf-8")
    assert _titles(addressing.find("P", "dispatch timeout").units) == before


def test_push_recall_ddd_legs_expand_with_the_map(brain):
    from core.recall_multi import _recall_ddd, recall_ddd
    hits, _ = _recall_ddd("会话标签页", "P", 3)
    assert not any("Session isolation" in str(h.get("content") or "") for h in hits)
    (brain / "CONTEXT.md").write_text(MAP, encoding="utf-8")
    hits, _ = _recall_ddd("会话标签页", "P", 3)
    assert any("Session isolation" in str(h.get("content") or "") for h in hits)
    got = recall_ddd("会话标签页", "P", "design")
    assert any("Session isolation" in str(h.get("content") or "") for h in got)


def test_chat_ddd_leg_recalls_and_passes_coverage_through_the_map(brain, monkeypatch):
    from core import session_router as sr
    monkeypatch.setattr(sr, "_record_ddd_inject", lambda *a: None)
    monkeypatch.setattr("core.code_intel.local_roots.hive_mode", lambda: True)

    def inject() -> str:
        got: list[str] = []
        sr._inject_ddd_for_active_project(SimpleNamespace(system_prompt=""), "派发任务为什么超时",
                                          ("P", "signal1_project_path"), deliver=got.append)
        return "\n".join(got)

    assert "Dispatch timeout" not in inject()
    (brain / "CONTEXT.md").write_text(MAP, encoding="utf-8")
    assert "Dispatch timeout" in inject()


def test_chat_coverage_counts_a_mapped_keyword_once_and_needs_as_many_as_before(brain, monkeypatch):
    from core import session_router as sr
    monkeypatch.setattr(sr, "_record_ddd_inject", lambda *a: None)
    monkeypatch.setattr("core.code_intel.local_roots.hive_mode", lambda: True)
    (brain / "CONTEXT.md").write_text("## Term map\n- 派发 → dispatch\n- 超时 → timeout, time out\n",
                                      encoding="utf-8")

    def inject(message: str, text: str) -> str:
        hit = {"doc": "TECH.md", "section": "Architecture", "content": text}
        monkeypatch.setattr("core.recall_multi.recall_all",
                            lambda *a, **k: SimpleNamespace(buckets={"ddd": [hit]}))
        got: list[str] = []
        sr._inject_ddd_for_active_project(SimpleNamespace(system_prompt=""), message,
                                          ("P", "signal1_project_path"), deliver=got.append)
        return "\n".join(got)

    # two keywords (派发, 隔离), one mapped: an entry holding only "dispatch" covers one of two
    assert "MARK1" not in inject("派发和隔离", "- [model] **MARK1** every dispatch is kept alone.")
    assert "MARK4" in inject("派发和隔离", "- [model] **MARK4** every dispatch 隔离 alone.")
    # "time out" is not found inside "output of a time-based job"
    assert "MARK2" not in inject("任务超时", "- [model] **MARK2** the output of a time-based job.")
    assert "MARK3" in inject("任务超时", "- [model] **MARK3** a task can time out.")


def test_chat_section_hit_points_at_the_entry_the_map_matches(brain, monkeypatch):
    from core import session_router as sr
    monkeypatch.setattr(sr, "_record_ddd_inject", lambda *a: None)
    monkeypatch.setattr("core.code_intel.local_roots.hive_mode", lambda: True)
    monkeypatch.setattr("core.recall_multi.recall_all", lambda *a, **k: SimpleNamespace(
        buckets={"ddd": [{"doc": "TECH.md", "section": "Architecture"}]}))
    (brain / "CONTEXT.md").write_text(MAP, encoding="utf-8")
    got: list[str] = []
    sr._inject_ddd_for_active_project(SimpleNamespace(system_prompt=""), "派发任务为什么超时",
                                      ("P", "signal1_project_path"), deliver=got.append)
    assert "Dispatch timeout" in "\n".join(got)


# ── R3: the probe ─────────────────────────────────────────────────────────────

PAIRS = [
    {"id": "q1", "title": "Dispatch timeout on the worker", "en": "dispatch timeout worker",
     "zh": "派发任务超时"},
    {"id": "q2", "title": "Session isolation per tab", "en": "session isolation tab",
     "zh": "会话和标签页隔离"},
]


def test_probe_reports_before_and_after_and_no_english_regression(brain, tmp_path):
    from scripts import cjk_recall_probe as probe
    mapfile = tmp_path / "map.md"
    mapfile.write_text(MAP, encoding="utf-8")
    rows = probe.run_probe("P", PAIRS, top=8, term_map_file=mapfile)
    assert [r["base"]["zh"] for r in rows] == [None, None]
    assert all(r["mapped"]["zh"] == 1 for r in rows)
    assert all(r["mapped"]["en"] == r["base"]["en"] == 1 for r in rows)
    assert probe.regressions(rows) == []
    out = tmp_path / "out.json"
    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps({"pairs": PAIRS}), encoding="utf-8")
    assert probe.main(["--project", "P", "--pairs", str(pairs), "--term-map", str(mapfile),
                       "--json", str(out)]) == 0
    assert json.loads(out.read_text())["rows"][0]["mapped"]["zh"] == 1


def test_probe_flags_a_rank_that_drops_in_either_language():
    rows = [{"id": "x", "set": "s", "base": {"zh": 2, "en": 1}, "weighted": {"zh": 5, "en": 1},
             "mapped": {"zh": 1, "en": 4}}]
    from scripts import cjk_recall_probe as probe
    assert probe.regressions(rows) == ["x: zh rank 2 -> 5 (weighted)", "x: en rank 1 -> 4 (mapped)"]
