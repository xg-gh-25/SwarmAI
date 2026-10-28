"""parse_memory_sections never drops a section whose name collides with another.

The parser strips a " — suffix" from each ``## `` heading, so ``## What Worked`` and
``## What Worked — Major Deliveries`` both become "What Worked". Keying a dict by that
name used to keep only the LAST body: in SwarmAI's own IMPROVEMENT.md the first, largest
section (about 38% of the file) was invisible to every DDD recall. Bodies under the same
name are now joined in file order.
"""
from core import memory_index


def test_colliding_section_names_keep_both_bodies_in_order():
    text = (
        "# Doc\n\n"
        "## What Worked\n\n- first body entry\n\n"
        "## Other\n\n- other entry\n\n"
        "## What Worked — Major Deliveries\n\n- second body entry\n"
    )
    secs = memory_index.parse_memory_sections(text)
    assert set(secs) == {"What Worked", "Other"}
    body = secs["What Worked"]
    assert "first body entry" in body and "second body entry" in body
    assert body.index("first body entry") < body.index("second body entry")


def test_no_text_under_a_heading_is_lost():
    parts = [f"## Sec{i % 3} — v{i}\n\n- entry number {i}\n" for i in range(9)]
    secs = memory_index.parse_memory_sections("\n".join(parts))
    joined = "\n".join(secs.values())
    for i in range(9):
        assert f"entry number {i}" in joined


def test_distinct_names_unchanged():
    secs = memory_index.parse_memory_sections("## A\n\n- a\n\n## B\n\n- b\n")
    assert secs == {"A": "- a", "B": "- b"}
