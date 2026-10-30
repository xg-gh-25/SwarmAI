"""The CDF unit model (core/ingestion/units): offsets never define identity, the active
head follows only supersedes and fails closed, the layer table is closed."""

from __future__ import annotations

from pathlib import Path

from core.ingestion.units import Supersedes, active_head, layer_for, parse_units


def test_parse_units_identity_survives_offset_shift(tmp_path: Path):
    path = tmp_path / "MEMORY.md"
    path.write_text("## Guidelines\n\n- [guideline] **Stable** body <!-- cand:c.1 -->\n", encoding="utf-8")
    first = parse_units(path, root=tmp_path)[0]
    path.write_text("# Heading\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    second = parse_units(path, root=tmp_path)[0]
    assert first.identity == second.identity == "cand:c.1"
    assert first.byte_start != second.byte_start
    assert first.byte_hash == second.byte_hash


def test_active_head_only_follows_supersedes_and_fails_closed():
    edges = [Supersedes("new", "old"), Supersedes("newest", "new")]
    assert active_head("old", edges, existing_anchors={"old", "new", "newest"}) == "newest"
    assert active_head("old", edges + [Supersedes("old", "newest")]) is None
    assert active_head("missing", edges, existing_anchors={"old", "new", "newest"}) is None


def test_layer_for_is_closed():
    assert layer_for("Guidelines", "guideline") == "operational"
    assert layer_for("Architecture", "principle") == "domain"
    assert layer_for("What Makes It Different", "model") == "domain"
    assert layer_for("Key Subsystems", "model") == "domain"
    try:
        layer_for("Guidelines", "model")
    except ValueError as exc:
        assert str(exc) == "invalid_layer_combination"
    else:
        raise AssertionError("invalid pair was accepted")
