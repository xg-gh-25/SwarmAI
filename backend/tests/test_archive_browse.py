"""Tests for archive_browse — list + search the .context/*-archive* cold layer (Run B).

Invariants:
- MEMORY shards (bullet `- [type] **title**`) and EVOLUTION shards (`### Fxxx` blocks
  with `<!-- size-evicted from X -->` provenance) each parse with the RIGHT parser.
- Empty-but-valid when no shards exist (never crash).
- Search is ARCHIVE-ONLY: filtered to the requested family's source_file prefix, so
  active files + the other family + Knowledge/ library docs never leak in.
"""
from pathlib import Path

from core.archive_browse import list_archive_entries, search_archive


# ── list: MEMORY shard (native bullet format) ──────────────────────────────
def test_list_memory_shard_parses_bullets(tmp_path: Path):
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "MEMORY-archive-2026-03.md").write_text(
        "# Memory Archive — 2026-03\n\n"
        "- [pitfall] **stale subprocess reused** — resolved 3/17. (2026-03-15)\n"
        "  <!-- ref:0 | last:2026-03-15 | decay:archived -->\n\n"
        "- [guideline] **tab switch content loss** — resolved 3/15. (2026-03-14)\n"
        "  <!-- ref:0 | last:2026-03-14 | decay:archived -->\n",
        encoding="utf-8",
    )
    out = list_archive_entries(tmp_path, "memory")
    assert out["source"] == "memory"
    assert out["total"] == 2
    titles = [e["title"] for e in out["entries"]]
    assert any("stale subprocess" in t for t in titles)
    types = {e["type"] for e in out["entries"]}
    assert "pitfall" in types and "guideline" in types
    assert out["shards"] == ["MEMORY-archive-2026-03.md"]


# ── list: EVOLUTION shard (### block + provenance comment) ──────────────────
def test_list_evolution_shard_parses_blocks_and_provenance(tmp_path: Path):
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "EVOLUTION-archive-2026-08.md").write_text(
        "# EVOLUTION Archive — size-evicted entries\n\n"
        "<!-- size-evicted from Failed Evolutions -->\n"
        "### F001 | 2026-04-12\n"
        "- **Attempt**: Pytest safety hook\n"
        "- **Lesson**: Pre-Implementation Checkpoint\n\n"
        "<!-- size-evicted from Corrections Captured -->\n"
        "### C049 | 2026-08-11 [Bias A]\n"
        "- **Pattern**: improve-before-justify\n",
        encoding="utf-8",
    )
    out = list_archive_entries(tmp_path, "evolution")
    assert out["total"] == 2
    f001 = next(e for e in out["entries"] if e["title"].startswith("F001"))
    assert f001["date"] == "2026-04-12"
    assert f001["type"] == "failed-evolution"
    assert f001["archived_from"] == "Failed Evolutions"  # provenance from the comment
    c049 = next(e for e in out["entries"] if e["title"].startswith("C049"))
    assert c049["date"] == "2026-08-11"
    assert c049["type"] == "correction"
    assert c049["archived_from"] == "Corrections Captured"


def test_evolution_parser_not_used_on_memory_and_vice_versa(tmp_path: Path):
    # A MEMORY shard fed to the evolution family (wrong glob) yields its OWN shards
    # list — the two families are keyed by distinct globs, never cross-read.
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "MEMORY-archive-2026-03.md").write_text("- [pitfall] **x** — y\n", encoding="utf-8")
    evo = list_archive_entries(tmp_path, "evolution")
    assert evo["total"] == 0 and evo["shards"] == []  # no EVOLUTION-archive* shard


# ── empty-but-valid ─────────────────────────────────────────────────────────
def test_list_empty_when_no_shards(tmp_path: Path):
    (tmp_path / ".context").mkdir()
    for src in ("memory", "evolution"):
        out = list_archive_entries(tmp_path, src)
        assert out == {"entries": [], "total": 0, "shards": [], "source": src}


def test_list_no_context_dir_is_empty_not_crash(tmp_path: Path):
    out = list_archive_entries(tmp_path, "memory")
    assert out["total"] == 0 and out["shards"] == []


# ── search: blank query short-circuits (no DB touch) ────────────────────────
def test_search_blank_query_returns_empty(tmp_path: Path):
    out = search_archive("", "memory")
    assert out == {"results": [], "q": "", "source": "memory"}
    out2 = search_archive("   ", "evolution")
    assert out2["results"] == []


# ── search: archive-only family filter (the privacy/scope crux) ─────────────
def test_search_filters_to_requested_family_prefix(monkeypatch):
    """FTS5 returns rows across all indexed files (archives + Knowledge/ + …); the
    search MUST keep only the requested family's .context/Archives/<FAM>-archive
    prefix — excluding the other family, Knowledge/ library docs, and any active file."""
    # Fake KnowledgeStore.fts5_search returning a mix of sources. search_archive does
    # `from core.knowledge_store import KnowledgeStore` INSIDE the fn, so patch the
    # name on its SOURCE module (that's what the fresh `from` import binds).
    class _FakeStore:
        def __init__(self, conn):
            pass

        def fts5_search(self, query, limit=20):
            return [
                {"source_file": ".context/Archives/MEMORY-archive-2026-03.md", "heading": "mem hit", "content": "credential proxy note"},
                {"source_file": ".context/Archives/EVOLUTION-archive-2026-08.md", "heading": "evo hit", "content": "credential proxy lesson"},
                {"source_file": "Knowledge/Library/some-doc.md", "heading": "lib hit", "content": "credential proxy doc"},
            ]

    import core.knowledge_store as ks_mod
    monkeypatch.setattr(ks_mod, "KnowledgeStore", _FakeStore, raising=False)
    # open_vec_db is likewise imported inside the fn — patch its source module.
    import core.vec_db as vec_db
    from contextlib import contextmanager

    @contextmanager
    def _fake_open():
        yield object()  # non-None conn

    monkeypatch.setattr(vec_db, "open_vec_db", _fake_open, raising=False)

    mem = search_archive("credential proxy", "memory")
    assert len(mem["results"]) == 1
    assert mem["results"][0]["shard"] == "MEMORY-archive-2026-03.md"

    evo = search_archive("credential proxy", "evolution")
    assert len(evo["results"]) == 1
    assert evo["results"][0]["shard"] == "EVOLUTION-archive-2026-08.md"
    # neither returned the Knowledge/ library doc
    for res in (mem, evo):
        assert all("Knowledge/" not in r["source_file"] for r in res["results"])


# ── Run C: list_archive_files — FILE-level summaries (not per-entry) ────────
def test_list_archive_files_memory_returns_file_summaries(tmp_path: Path):
    from core.archive_browse import list_archive_files
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "MEMORY-archive-2026-03.md").write_text(
        "# Memory Archive — 2026-03\n\n"
        "- [pitfall] **a** — x\n"
        "- [guideline] **b** — y\n",
        encoding="utf-8",
    )
    (ctx / "MEMORY-archive-2026-07.md").write_text(
        "# Memory Archive — 2026-07\n\n"
        "- [decision] **c** — z\n",
        encoding="utf-8",
    )
    out = list_archive_files(tmp_path, "memory")
    assert out["source"] == "memory"
    assert out["total_files"] == 2
    # files carry name/bytes/period/entry_count — NOT a flat entries[] content list
    assert "entries" not in out
    files = {f["name"]: f for f in out["files"]}
    assert files["MEMORY-archive-2026-03.md"]["entry_count"] == 2
    assert files["MEMORY-archive-2026-07.md"]["entry_count"] == 1
    assert files["MEMORY-archive-2026-03.md"]["period"] == "2026-03"
    assert files["MEMORY-archive-2026-03.md"]["bytes"] > 0


def test_list_archive_files_evolution_legacy_period(tmp_path: Path):
    from core.archive_browse import list_archive_files
    ctx = tmp_path / ".context"
    ctx.mkdir()
    # legacy undated shard + a dated one
    (ctx / "EVOLUTION-archive.md").write_text(
        "<!-- size-evicted from Corrections -->\n### C001 | 2026-04-01\n- x\n",
        encoding="utf-8",
    )
    (ctx / "EVOLUTION-archive-2026-08.md").write_text(
        "<!-- size-evicted from Corrections -->\n### C049 | 2026-08-11\n- y\n",
        encoding="utf-8",
    )
    out = list_archive_files(tmp_path, "evolution")
    files = {f["name"]: f for f in out["files"]}
    assert files["EVOLUTION-archive.md"]["period"] == "legacy"   # undated → legacy
    assert files["EVOLUTION-archive-2026-08.md"]["period"] == "2026-08"
    assert files["EVOLUTION-archive.md"]["entry_count"] == 1


def test_list_archive_files_empty_when_no_shards(tmp_path: Path):
    from core.archive_browse import list_archive_files
    (tmp_path / ".context").mkdir()
    out = list_archive_files(tmp_path, "memory")
    assert out == {"files": [], "total_files": 0, "source": "memory"}
