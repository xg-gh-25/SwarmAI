"""CDF §12 deterministic MEMORY/EVOLUTION injection selection."""
from __future__ import annotations

import hashlib
import inspect
import json
from datetime import datetime, timezone

from core.context_directory_loader import ContextDirectoryLoader
from core.ingestion.store import connect_rw
from core.ingestion.units import parse_units
from core.memory_injection import select_context_file


def _memory(entries: list[tuple[str, str, str]]) -> str:
    sections: dict[str, list[str]] = {}
    for section, title, body in entries:
        sections.setdefault(section, []).append(
            f"- [{'principle' if section == 'Principles' else 'decision'}] **{title}** {body}\n"
            "  <!-- ref:0 | last:2026-10-20 | decay:active | source:manual -->"
        )
    return "# Memory\n\n" + "\n\n".join(
        f"## {section}\n" + "\n".join(items) for section, items in sections.items()
    ) + "\n"


def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "app-data"))
    return connect_rw()


def test_selector_signature_has_no_query_input():
    names = set(inspect.signature(select_context_file).parameters)
    assert not names & {"query", "user_message", "prompt", "prompt_text", "input"}


def test_recompute_only_on_hash_or_utc_day_and_preserves_file(tmp_path, monkeypatch):
    conn = _isolated_store(tmp_path, monkeypatch)
    path = tmp_path / "SwarmWS/.context/MEMORY.md"
    path.parent.mkdir(parents=True)
    path.write_text(_memory([("Principles", "meta", "always keep"),
                             ("Decisions", "choice", "x" * 180)]), encoding="utf-8")
    before = path.read_bytes()
    day1 = datetime(2026, 10, 27, 23, 59, tzinfo=timezone.utc)
    first = select_context_file(path, budget=180, estimate=len, now=day1, conn=conn)
    again = select_context_file(path, budget=180, estimate=len, now=day1, conn=conn)
    day2 = select_context_file(path, budget=180, estimate=len,
                               now=datetime(2026, 10, 28, tzinfo=timezone.utc), conn=conn)
    assert first.recomputed and not again.recomputed and day2.recomputed
    assert first.text == again.text
    assert conn.execute("SELECT COUNT(*) FROM injection_selection").fetchone()[0] == 2
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    changed = select_context_file(path, budget=180, estimate=len,
                                  now=datetime(2026, 10, 28, tzinfo=timezone.utc), conn=conn)
    assert changed.recomputed
    assert conn.execute("SELECT COUNT(*) FROM injection_selection").fetchone()[0] == 3
    assert before == path.read_bytes()[:-1]
    row = conn.execute("SELECT file_hash,score_snapshot_version,computed_at FROM injection_selection "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert row[1] and row[2].startswith("2026-10-28")


def test_over_budget_keeps_meta_whole_ranks_lower_layers_and_outputs_file_order(tmp_path, monkeypatch):
    conn = _isolated_store(tmp_path, monkeypatch)
    path = tmp_path / "SwarmWS/.context/MEMORY.md"
    path.parent.mkdir(parents=True)
    path.write_text(_memory([
        ("Principles", "meta", "M" * 80),
        ("Decisions", "earlier", "E" * 105),
        ("Decisions", "later", "L" * 105),
        ("Guidelines", "filler", "F" * 500),
    ]), encoding="utf-8")
    units = parse_units(path, root=path.parent.parent)
    by_title = {unit.title: unit for unit in units}
    for root_id, title, trust, observed, weight in (
        ("r-earlier", "earlier", "measured", "2026-10-27T00:00:00+00:00", 0.8),
        ("r-later", "later", "user", "2026-10-28T00:00:00+00:00", 1.0),
    ):
        conn.execute("INSERT INTO evidence_roots VALUES(?,?,?,?,?,?,?,?)",
                     (root_id, "user_message", root_id, root_id, trust, "[]", observed, observed))
        conn.execute("INSERT INTO claim_support VALUES(?,?,?,?,?,?,?)",
                     (by_title[title].anchor, root_id, "source", weight, "support", 0, observed))
    conn.commit()
    # Room for meta plus both decisions, but not the operational filler.
    selected = select_context_file(path, budget=500, estimate=len,
                                   now=datetime(2026, 10, 28, tzinfo=timezone.utc), conn=conn)
    assert "meta" in selected.text and selected.text.count("M") >= 80
    assert "earlier" in selected.text and "later" in selected.text
    assert selected.text.index("earlier") < selected.text.index("later")
    assert "filler" not in selected.text
    assert selected.used_tokens <= 500
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_ties_use_anchor_order_and_never_cut_a_unit(tmp_path, monkeypatch):
    conn = _isolated_store(tmp_path, monkeypatch)
    path = tmp_path / "SwarmWS/.context/MEMORY.md"
    path.parent.mkdir(parents=True)
    path.write_text(_memory([
        ("Decisions", "alpha", "A" * 150),
        ("Decisions", "beta", "B" * 150),
        ("Guidelines", "force-over", "Z" * 500),
    ]), encoding="utf-8")
    units = parse_units(path, root=path.parent.parent)
    expected = min((unit for unit in units if unit.title in {"alpha", "beta"}),
                   key=lambda unit: unit.anchor)
    result = select_context_file(path, budget=220, estimate=len,
                                 now=datetime(2026, 10, 28, tzinfo=timezone.utc), conn=conn)
    assert expected.title in result.text
    other = "beta" if expected.title == "alpha" else "alpha"
    assert other not in result.text
    assert ("A" * 150 in result.text) or ("B" * 150 in result.text)
    assert "A" * 75 not in result.text.replace("A" * 150, "")
    assert "B" * 75 not in result.text.replace("B" * 150, "")


def test_superseded_unit_is_dropped_only_when_selection_is_needed(tmp_path, monkeypatch):
    conn = _isolated_store(tmp_path, monkeypatch)
    path = tmp_path / "SwarmWS/.context/MEMORY.md"
    path.parent.mkdir(parents=True)
    path.write_text(_memory([
        ("Decisions", "old", "O" * 100),
        ("Decisions", "new", "N" * 100),
        ("Guidelines", "force-over", "F" * 500),
    ]), encoding="utf-8")
    units = {unit.title: unit for unit in parse_units(path, root=path.parent.parent)}
    conn.execute("INSERT INTO ingestion_relations VALUES(?,?,?,?,?)",
                 (units["new"].anchor, "supersedes", units["old"].anchor,
                  "2026-10-28T00:00:00+00:00", 0))
    conn.commit()
    result = select_context_file(path, budget=250, estimate=len,
                                 now=datetime(2026, 10, 28, tzinfo=timezone.utc), conn=conn)
    assert "old" not in result.text and units["old"].anchor not in result.anchors
    assert "new" in result.text


def test_evolution_meta_layer_goes_first_and_inside_the_budget(tmp_path, monkeypatch):
    """Meta-cognition is admitted first but no longer unbudgeted: kept whole it was
    already over the EVOLUTION budget and grew with every CDF correction
    (2026-10-29-cdf-launch §2 C2)."""
    conn = _isolated_store(tmp_path, monkeypatch)
    ctx = tmp_path / "SwarmWS/.context"
    ctx.mkdir(parents=True)
    evolution = ctx / "EVOLUTION.md"
    evolution.write_text(
        "# Evolution\n\n## Corrections Captured\n### C001 First\n" + "C" * 90 +
        "\n### C002 Second\n" + "D" * 90 +
        "\n\n## Capabilities Built\n### K001 Tool\n" + "T" * 500 + "\n",
        encoding="utf-8",
    )
    when = datetime(2026, 10, 28, tzinfo=timezone.utc)
    evo = select_context_file(evolution, budget=260, estimate=len, now=when, conn=conn)
    assert "C001" in evo.text and "C002" in evo.text and "K001" not in evo.text
    evolution.write_text(evolution.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # new hash
    tight = select_context_file(evolution, budget=150, estimate=len, now=when, conn=conn)
    assert len(tight.text) <= 150 and ("C001" in tight.text) != ("C002" in tight.text)
    conn.close()


def test_loader_specs_use_design_budgets():
    from core.context_directory_loader import CONTEXT_FILES
    specs = {item.filename: item for item in CONTEXT_FILES}
    assert (specs["MEMORY.md"].selector, specs["MEMORY.md"].inject_budget_tokens) == ("cdf", 24_000)
    assert (specs["EVOLUTION.md"].selector, specs["EVOLUTION.md"].inject_budget_tokens) == ("cdf", 8_000)


def test_score_prefers_stronger_newer_evidence_when_only_one_unit_fits(tmp_path, monkeypatch):
    conn = _isolated_store(tmp_path, monkeypatch)
    path = tmp_path / "SwarmWS/.context/MEMORY.md"
    path.parent.mkdir(parents=True)
    path.write_text(_memory([
        ("Decisions", "weak old", "W" * 150),
        ("Decisions", "strong new", "S" * 150),
        ("Guidelines", "force-over", "F" * 500),
    ]), encoding="utf-8")
    units = {unit.title: unit for unit in parse_units(path, root=path.parent.parent)}
    facts = (
        ("weak", units["weak old"].anchor, "external", "web_document", "2026-10-01T00:00:00+00:00", 0.3),
        ("strong", units["strong new"].anchor, "user", "user_message", "2026-10-28T00:00:00+00:00", 1.0),
    )
    for root_id, anchor, trust, kind, observed, weight in facts:
        conn.execute("INSERT INTO evidence_roots VALUES(?,?,?,?,?,?,?,?)",
                     (root_id, kind, root_id, root_id, trust, "[]", observed, observed))
        conn.execute("INSERT INTO claim_support VALUES(?,?,?,?,?,?,?)",
                     (anchor, root_id, "source", weight, "support", 0, observed))
    conn.commit()
    result = select_context_file(path, budget=225, estimate=len,
                                 now=datetime(2026, 10, 28, tzinfo=timezone.utc), conn=conn)
    assert "strong new" in result.text and "weak old" not in result.text
