"""One entry boundary and one truncation rule for every recall leg
(design docs/design/2026-10-26-run-knowledge.md §3.0, step 0).

What is tested:
- R1: recall splits DDD / MEMORY / pipeline-lessons entries with the platform's one
  entry reader (``ddd_entry_lifecycle._scan_entry``). A bullet no longer swallows the
  ``###`` subsection, table or paragraph after it; its sub-bullets and metadata line
  stay with it; a superseded entry is still filtered.
- R2: ``recall_filter.clip`` cuts at a sentence end (else a line end, else a space)
  and names the full length and the source; never mid-word.
- R3: the domain budget goes by rank; a hit that does not fit whole is clipped, not
  skipped, and the next one is never placed ahead of it.
- Structure: recall code has no ``^- `` split regex and no ``_truncate``.
"""
from pathlib import Path

from core import recall_filter
from core.recall_multi import _ddd_entry_hits, _memory_outside_prompt

BACKEND = Path(__file__).resolve().parents[1]

DOC = """# TECH

## Runtime Traps

- **Lock order**: take _spawn_lock before _env_lock, always.
  - refines: also on the warm-reuse path.
  <!-- ref:0 | last:none | decay:active -->

### Ladder appendix

The quarterly zebra ladder survey lists every xylophone migration.

| zebra | xylophone |
|---|---|
| a | b |

- **Old rule**: retired zebra wording.
  <!-- ref:0 | last:none | decay:active | superseded_by:Lock order -->
"""


def test_bullet_does_not_swallow_the_subsection_after_it():
    hits = _ddd_entry_hits("zebra xylophone ladder", {"TECH.md": DOC}, top_n=5)
    assert all("Lock order" not in h["content"] for h in hits), hits


def test_entry_keeps_sub_bullets_and_metadata():
    hits = _ddd_entry_hits("spawn_lock env_lock warm-reuse", {"TECH.md": DOC}, top_n=5)
    assert hits and hits[0]["content"].startswith("- **Lock order**")
    assert "refines: also on the warm-reuse path." in hits[0]["content"]
    assert "Ladder appendix" not in hits[0]["content"]
    assert "|" not in hits[0]["content"].replace("<!-- ref:0 | last:none | decay:active -->", "")


def test_superseded_entry_still_filtered():
    hits = _ddd_entry_hits("retired zebra wording", {"TECH.md": DOC}, top_n=5)
    assert all("Old rule" not in h["content"] for h in hits), hits


def test_memory_outside_prompt_entries_end_at_their_boundary():
    content = ("## Pitfalls\n- **A**: first rule.\n\nA loose paragraph about zebras.\n"
               "- **B**: second rule.\n")
    out = _memory_outside_prompt(content, prompt_text="")
    assert "- **A**: first rule." in out and "- **B**: second rule." in out
    assert "loose paragraph" not in out


def test_clip_cuts_at_sentence_end_and_names_the_source():
    text = ("The first rule is short. " * 6) + "A trailing clause that would not fit in"
    out = recall_filter.clip(text, 120, "TECH.md#Runtime Traps")
    assert len(out) <= 120
    head = out.split(" …（", 1)[0]
    assert head.endswith("."), out
    assert f"全文 {len(text)} 字符" in out and "TECH.md#Runtime Traps" in out


def test_clip_falls_back_to_line_then_space_never_mid_word():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi " * 4
    out = recall_filter.clip(text, 80, "x.md")
    head = out.split(" …（", 1)[0]
    assert text.startswith(head) and text[len(head)] == " ", out


def test_clip_cuts_chinese_without_spaces_at_a_clause_mark():
    text = "修复自身过完整流程，跑了一百多个测试，结论写进报告" * 8
    out = recall_filter.clip(text, 100, "TECH.md#S")
    head = out.split(" …（", 1)[0]
    assert head.endswith("，"), out


def test_clip_leaves_short_text_alone():
    assert recall_filter.clip("short.", 100, "x.md") == "short."


def test_budget_clips_by_rank_and_never_skips():
    long = "A long rule sentence. " * 80                     # ~1760 chars
    hits = [{"doc": "TECH.md", "section": "S1", "content": "First rule."},
            {"doc": "TECH.md", "section": "S2", "content": long},
            {"doc": "TECH.md", "section": "S3", "content": "Third rule."}]
    out = recall_filter._budget_one("ddd", hits, {"ddd": 1000})
    assert [h["section"] for h in out][:2] == ["S1", "S2"], out
    assert "全文" in out[1]["content"] and "TECH.md#S2" in out[1]["content"]
    assert out[1]["content"].split(" …（", 1)[0].endswith(".")


def test_no_second_entry_splitter_or_truncate_in_recall_code():
    for rel in ("core/recall_multi.py", "core/recall_filter.py", "core/repo_guidance.py"):
        src = (BACKEND / rel).read_text(encoding="utf-8")
        assert '(?m)^(?=- )' not in src, rel
        assert "def _truncate" not in src and "_truncate(" not in src, rel
