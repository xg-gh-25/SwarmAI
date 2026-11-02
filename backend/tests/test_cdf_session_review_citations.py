"""The review cites the ids the record showed (design 2026-10-29-cdf-session-review): a
message id it wrote with an anchor's prefix, or cut to its first characters, is the
same item. Measured 2026-11-01: 12 of 13 invalid review units cited a shown message id
as ``event:input:<message id>`` or by its first 8 characters, and each was refused."""
from __future__ import annotations


def _answer(*evidence):
    return {"cognition_units": [{"title": "t", "evidence": list(evidence)}]}


ALIASES = {"2880c7b1-9d25-4d17-b49d-0ab382cd332f": ["event:input:aaaa"],
           "fb2bfaa6-1111-2222-3333-444455556666": ["event:input:bbbb"],
           "fb2bfaa7-0000-0000-0000-000000000000": ["event:input:cccc"],
           "75f01afc-9999-8888-7777-666655554444": ["event:input:dddd"]}


def _ev(answer):
    return answer["cognition_units"][0]["evidence"]


def test_a_shown_message_id_written_with_an_anchor_prefix_is_its_item():
    from core.ingestion.sources.s7_review import _cited
    assert _ev(_cited(_answer("event:input:2880c7b1-9d25-4d17-b49d-0ab382cd332f"), ALIASES)) == ["event:input:aaaa"]


def test_a_shown_id_cut_to_its_first_eight_characters_is_its_item_when_unique():
    from core.ingestion.sources.s7_review import _cited
    assert _ev(_cited(_answer("event:input:75f01afc"), ALIASES)) == ["event:input:dddd"]
    assert _ev(_cited(_answer("75f01afc"), ALIASES)) == ["event:input:dddd"]


def test_an_ambiguous_or_unknown_id_is_left_as_written():
    from core.ingestion.sources.s7_review import _cited
    assert _ev(_cited(_answer("event:input:fb2bfaa"), ALIASES)) == ["event:input:fb2bfaa"]   # 7 chars
    assert _ev(_cited(_answer("fb2bfaa6-1111-2222-3333-444455556666"), ALIASES)) == ["event:input:bbbb"]
    assert _ev(_cited(_answer("event:input:deadbeef"), ALIASES)) == ["event:input:deadbeef"]


def test_two_shown_ids_sharing_a_prefix_are_not_guessed():
    from core.ingestion.sources.s7_review import _cited
    aliases = {**ALIASES, "75f01afc-0000-0000-0000-000000000001": ["event:input:eeee"]}
    assert _ev(_cited(_answer("75f01afc"), aliases)) == ["75f01afc"]


def test_a_short_prefix_is_not_taken_even_when_unique():
    from core.ingestion.sources.s7_review import _cited
    assert _ev(_cited(_answer("75f01af"), ALIASES)) == ["75f01af"]          # 7 characters
