"""S2 windowing (core/ingestion/sources/s2_session.py ``windows``).

What is tested:
- Every window serializes within the limit; windows keep the original order and lose
  nothing (the items come back in order, fragments reassemble byte-for-byte).
- An item larger than one window is cut into numbered fragments that rebuild it.
- A small session is one window.
"""
from __future__ import annotations

import json
from typing import Any

from core.ingestion.sources import s2_session


def _items(n: int, size: int) -> list[dict[str, Any]]:
    return [{"id": f"m{i}", "role": "assistant", "content": f"{i}:" + "x" * size} for i in range(n)]


def _rebuild(chunks: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out, pending = [], []
    for chunk in chunks:
        for item in chunk:
            if "json_fragment" in item:
                pending.append(item["json_fragment"])
                if item["part"] == item["parts"]:
                    out.append(json.loads("".join(pending)))
                    pending = []
            else:
                out.append(item)
    return out


def test_windows_fit_keep_order_and_lose_nothing() -> None:
    items = _items(40, 900)
    chunks = s2_session.windows(items, "id", limit=5_000)
    assert len(chunks) > 1
    assert all(len(json.dumps(c, ensure_ascii=False)) <= 5_000 for c in chunks)
    assert _rebuild(chunks) == items


def test_oversized_item_is_split_into_fragments() -> None:
    items = _items(2, 100) + [{"id": "big", "content": "中" * 12_000}] + _items(1, 100)
    chunks = s2_session.windows(items, "id", limit=5_000)
    parts = [c[0] for c in chunks if "json_fragment" in c[0]]
    assert parts and all(p["part_of"] == "big" for p in parts)
    assert [p["part"] for p in parts] == list(range(1, parts[0]["parts"] + 1))
    assert all(len(json.dumps(c, ensure_ascii=False)) <= 5_000 for c in chunks)
    assert _rebuild(chunks) == items


def test_small_session_is_one_window() -> None:
    assert s2_session.windows(_items(3, 50), "id") == [_items(3, 50)]


def test_overlap_repeats_the_tail_and_never_stands_alone() -> None:
    items = _items(40, 900)
    chunks = s2_session.windows(items, "id", limit=5_000, overlap=0.25)
    assert all(len(json.dumps(c, ensure_ascii=False)) <= 5_000 for c in chunks)
    for previous, chunk in zip(chunks, chunks[1:]):
        carried = [item for item in chunk if item in previous]
        assert carried and chunk[:len(carried)] == previous[-len(carried):]
        assert len(chunk) > len(carried)
    seen = []
    for chunk in chunks:
        seen += [item for item in chunk if item not in seen]
    assert seen == items
    assert s2_session.windows(items, "id", limit=5_000) == s2_session.windows(
        items, "id", limit=5_000, overlap=0.0)
