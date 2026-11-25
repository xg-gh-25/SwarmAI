"""The revision guard through real cuts (design section 11 item 2).

(a) an in-scope change passes and the other slides keep their hashes;
(b) an out-of-scope modify, delete and move each fail and name the slide;
(c) the person's edit made without open is stored first, and the agent's
    change is compared against it (so reverting the person's edit fails).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_common import (  # noqa: E402
    adopt_small, deck_text, env, meta, need_browser, replace_in_slide, run, section_of,
    set_deck_text, version, work,
)

__all__ = ["env"]


@pytest.fixture
def deck(env, capsys):
    need_browser()
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    return d, ids


def _cut(d, capsys):
    return run(["cut", "--dir", str(d), "--formats", "png"], capsys)


def test_in_scope_change_passes(deck, capsys):
    d, ids = deck
    run(["open", "--dir", str(d), "--scope", ids[1], "--note", "new wording"], capsys)
    replace_in_slide(d, ids[1], "Bravo", "Bravo, rewritten")
    code, out = _cut(d, capsys)
    assert code == 0, out
    assert out["status"] == "ok" and out["current"] == "v002"
    m = meta(d)
    assert m["open"] is None and m["current"] == "v002"
    v1, v2 = version(d, "v001")["hashes"], version(d, "v002")["hashes"]
    assert v2[ids[1]] != v1[ids[1]]
    assert all(v2[i] == v1[i] for i in ids if i != ids[1])
    assert v2["_shared"] == v1["_shared"]
    assert sorted(p.name for p in (d / "out" / "slides").iterdir()) == [
        f"{n:02d}-{i}.png" for n, i in enumerate(ids, 1)]
    assert (d / "review" / "sheet.jpg").is_file()
    snap = d / ".versions" / "v002"
    assert {p.name for p in snap.iterdir()} == {"src", "checks.json", "guard.json", "measure.json"}


def test_in_scope_delete_passes(deck, capsys):
    d, ids = deck
    run(["open", "--dir", str(d), "--scope", "", "--remove", ids[3]], capsys)
    text = deck_text(d)
    set_deck_text(d, text.replace(section_of(text, ids[3]), ""))
    code, out = _cut(d, capsys)
    assert code == 0, out
    assert meta(d)["slides"] == ids[:3]


def _assert_failed(d, out, code, sid, kind):
    assert code == 1
    assert out["status"] == "failed" and out["current"] == "v001"
    assert [(v["slide"], v["kind"]) for v in out["guard"]["violations"]] == [(sid, kind)]
    m = meta(d)
    assert m["current"] == "v001" and m["open"] is not None
    assert version(d, "v002")["status"] == "failed"
    assert (d / "review" / "failed-v002" / "slides").is_dir()
    assert not (d / "out" / "slides").exists()


def test_out_of_scope_modify_fails(deck, capsys):
    d, ids = deck
    run(["open", "--dir", str(d), "--scope", ids[1]], capsys)
    replace_in_slide(d, ids[2], "Charlie", "Charlie changed")
    code, out = _cut(d, capsys)
    _assert_failed(d, out, code, ids[2], "modified")
    assert "Charlie changed" in out["guard"]["violations"][0]["detail"]


def test_out_of_scope_delete_fails(deck, capsys):
    d, ids = deck
    run(["open", "--dir", str(d), "--scope", ids[1]], capsys)
    text = deck_text(d)
    set_deck_text(d, text.replace(section_of(text, ids[2]), ""))
    code, out = _cut(d, capsys)
    _assert_failed(d, out, code, ids[2], "deleted")


def test_out_of_scope_move_fails(deck, capsys):
    d, ids = deck
    run(["open", "--dir", str(d), "--scope", ids[1]], capsys)
    text = deck_text(d)
    last = section_of(text, ids[3])
    text = text.replace(last, "")
    second = section_of(text, ids[1])
    set_deck_text(d, text.replace(second, last + second))
    code, out = _cut(d, capsys)
    _assert_failed(d, out, code, ids[3], "moved")
    assert "was page 4" in out["guard"]["violations"][0]["detail"]


def test_fixed_cut_after_failure_passes(deck, capsys):
    """After a failed cut the open stays; fixing the source and cutting again works."""
    d, ids = deck
    run(["open", "--dir", str(d), "--scope", ids[1]], capsys)
    replace_in_slide(d, ids[2], "Charlie", "Charlie changed")
    assert _cut(d, capsys)[0] == 1
    replace_in_slide(d, ids[2], "Charlie changed", "Charlie")
    code, out = _cut(d, capsys)
    assert code == 0 and out["version"] == "v003" and out["current"] == "v003"
    assert not list((d / "review").glob("failed-*"))


def test_user_edit_is_base_and_cannot_be_reverted(deck, capsys):
    d, ids = deck
    # the person edits slide 3 on disk, without open
    replace_in_slide(d, ids[2], "Charlie", "Charlie by the person")
    code, out = run(["open", "--dir", str(d), "--scope", ids[1]], capsys)
    assert out["user_version"] == "v001-user"
    # the agent changes only slide 2: passes, and the person's edit is kept
    replace_in_slide(d, ids[1], "Bravo", "Bravo by the agent")
    code, out = _cut(d, capsys)
    assert code == 0, out
    assert version(d, "v002")["base"] == "v001-user"
    assert version(d, "v002")["hashes"][ids[2]] == version(d, "v001-user")["hashes"][ids[2]]
    assert "Charlie by the person" in deck_text(d)


def test_reverting_user_edit_fails(deck, capsys):
    d, ids = deck
    replace_in_slide(d, ids[2], "Charlie", "Charlie by the person")
    run(["open", "--dir", str(d), "--scope", ids[1]], capsys)
    # the agent writes slide 3 back from its old copy: that is "changed back", out of scope
    replace_in_slide(d, ids[2], "Charlie by the person", "Charlie")
    code, out = _cut(d, capsys)
    assert code == 1
    assert [(v["slide"], v["kind"]) for v in out["guard"]["violations"]] == [(ids[2], "modified")]
    assert meta(d)["current"] == "v001-user"


def test_first_cut_after_init_needs_no_open(env, capsys):
    need_browser()
    d = env["tmp"] / "fresh"
    run(["init", "--dir", str(d), "--kind", "deck", "--title", "Fresh", "--size", "320x180"], capsys)
    text = deck_text(d).replace("<body>\n", "<body>\n<section><h2>Hello</h2></section>\n")
    set_deck_text(d, text)
    (d / "src" / "shared.css").write_text("body{margin:0}section{width:320px;height:180px}")
    code, out = _cut(d, capsys)
    assert code == 0, out
    assert len(out["ids_added"]) == 1 and meta(d)["slides"] == out["ids_added"]
    assert work.parse_slides(deck_text(d))[0]["id"] == out["ids_added"][0]


def test_adopted_folder_in_scope_image_swap_cuts(env, capsys):
    """Real cut in an adopted folder: an in-scope image swap passes (old image still on disk)."""
    need_browser()
    d = env["tmp"] / "assets"
    d.mkdir()
    (d / "deck.html").write_text(
        '<style>section{width:320px;height:180px}</style>'
        '<section><img src="a.png"></section>\n<section>two</section>\n')
    from PIL import Image
    Image.new("RGB", (8, 8), (200, 0, 0)).save(d / "a.png")
    Image.new("RGB", (8, 8), (0, 0, 200)).save(d / "b.png")
    code, out = run(["adopt", "--dir", str(d), "--kind", "deck", "--src", "deck.html",
                      "--size", "320x180"], capsys)
    sid = out["slides"][0]
    run(["open", "--dir", str(d), "--scope", sid], capsys)
    (d / "deck.html").write_text((d / "deck.html").read_text().replace('src="a.png"', 'src="b.png"'))
    code, out = _cut(d, capsys)
    assert code == 0, out
