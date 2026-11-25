"""A docs mount indexes an HTML or SVG file as the text it shows (library_mounts._markup_text).

A slide deck written as HTML is mostly CSS and script; indexed as source, its chunks
were filled with styles and recall's relevance check never found the slides' words.

Properties:
  1. text nodes and the title are kept; tags, attributes, comments, style, script and
     the head are not; entities are decoded; h2/h3 become markdown headings
  2. a deck that renders its slides from script keeps the slides' text (markup in the
     script's string literals, JSON-escaped or not); code between unrelated quotes,
     CSS strings and a minified bundle are not read as markup
  3. a deck of framed slides (iframe srcdoc) keeps the frames' text
  4. a base64 run pasted as text is dropped; a long run of Chinese is kept
  5. a page that shows no text gives "" (nothing indexed)
  6. index_docs_mount stores the shown text of an .html file, not its source
"""

from __future__ import annotations

import contextlib
import json
import sqlite3

from core.library_mounts import LibraryMounts, _markup_text


def test_shown_text_only():
    page = """<!DOCTYPE html><html><head><title>Deck &amp; Notes</title>
    <style>.slide{color:red}</style><meta name="x" content="hidden words"></head>
    <body><!-- a comment --><div class="slide"><h2>First slide</h2><p>Hello &mdash; world</p>
    <script>var secretWord = 1;</script><h3>Sub point</h3><p>Detail <b>bold</b> here</p></div></body></html>"""
    text = _markup_text(page)
    assert text.startswith("# Deck & Notes\n")
    assert "## First slide" in text and "### Sub point" in text
    assert "Hello — world" in text and "Detail bold here" in text
    for gone in ("color:red", "secretWord", "hidden words", "a comment", "<", "class="):
        assert gone not in text


def test_slides_rendered_from_script():
    slides = [{"title": "One", "style": ".x { color: red; background: blue; }",
               "body": '\n  <div class="slide"><h2>Revenue plan</h2><p>Grow 30% in CMHK</p></div>'},
              {"title": "Two", "body": "<section><p>Second slide words</p></section>"}]
    page = f"""<html><body><div id="app"></div><script>
    const SLIDES = {json.dumps(slides)};
    const tpl = `<p>From a template literal</p>`;
    function f(a){{return a+"x</p>"+b+"<div"}}
    document.title = 'not markup at all, just a sentence';
    </script></body></html>"""
    text = _markup_text(page)
    assert "## Revenue plan" in text and "Grow 30% in CMHK" in text
    assert "Second slide words" in text and "From a template literal" in text
    for gone in ("color: red", "return a", "not markup at all"):
        assert gone not in text


def test_minified_code_between_quotes_is_not_markup():
    bundle = 'var a="x";' + 'function q(e){return e}' * 200 + 'var b="<div>' + "y" * 30 + '";'
    page = f"<html><body><p>Visible</p><script>{bundle}</script></body></html>"
    text = _markup_text(page)
    assert "function q" not in text and "Visible" in text


def test_framed_slides():
    page = '<html><body><iframe srcdoc="&lt;h2&gt;Framed title&lt;/h2&gt;&lt;p&gt;Inside the frame&lt;/p&gt;"></iframe></body></html>'
    text = _markup_text(page)
    assert "## Framed title" in text and "Inside the frame" in text


def test_encoded_runs_are_dropped_and_chinese_kept():
    zh = "这是一段很长的中文正文" * 30
    page = f"<html><body><p>{'QUJD' * 100}</p><p>{zh}</p></body></html>"
    text = _markup_text(page)
    assert "QUJD" not in text and zh in text


def test_svg_labels():
    svg = '<svg><style>.t{fill:#000}</style><path d="M0 0L10 10"/><text x="1">Tension Matrix</text><text><tspan>T1 · No-AI</tspan></text></svg>'
    text = _markup_text(svg)
    assert "Tension Matrix" in text and "T1 · No-AI" in text
    assert "M0 0" not in text and "fill" not in text


def test_a_page_with_no_text_gives_nothing():
    assert _markup_text("<html><head><style>a{b:c}</style></head><body><div></div></body></html>") == ""


def test_index_docs_mount_stores_the_shown_text(tmp_path, monkeypatch):
    import core.library_mounts as lm
    import core.vec_db as vec_db
    monkeypatch.setattr(lm, "_mounts_dir", lambda: tmp_path / "mounts")
    vec = sqlite3.connect(":memory:")
    monkeypatch.setattr(vec_db, "open_vec_db", lambda *a, **k: contextlib.nullcontext(vec))
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    store = LibraryMounts(conn); store.ensure_table()
    d = tmp_path / "decks"; d.mkdir()
    (d / "deck.html").write_text("<html><head><style>.s{color:red}</style></head>"
                                 "<body><h2>Plan</h2><p>Ship the agent</p></body></html>")
    mid = store.add_mount(scope="S", path=str(d), kind="docs")
    assert lm.index_docs_mount(store, mid)["status"] == "indexed"
    body = "\n".join(r[0] for r in vec.execute("SELECT content FROM knowledge_chunks"))
    assert "Ship the agent" in body and "color:red" not in body and "<" not in body


def test_code_with_a_tag_between_unrelated_quotes_is_not_markup():
    script = 'var a="k"; el.innerHTML = s.replace(/<br \\/>/g, "") + t; var c="z";'
    text = _markup_text(f"<html><body><p>Visible</p><script>{script}</script></body></html>")
    assert "innerHTML" not in text and "Visible" in text


def test_a_mount_indexed_before_markup_text_is_redone(tmp_path, monkeypatch):
    import core.code_intel.build as build
    import core.library_mounts as lm
    calls: list = []
    monkeypatch.setattr(build, "spawn_child", lambda args: calls.append(args))
    monkeypatch.setattr(lm, "_mounts_dir", lambda: tmp_path / "mounts")
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    store = LibraryMounts(conn); store.ensure_table()
    d = tmp_path / "decks"; d.mkdir(); (d / "a.md").write_text("# a\ntext\n")
    mid = store.add_mount(scope="S", path=str(d), kind="docs")
    store.mark_synced(mid, index_version=2)          # HTML indexed as its source
    lm.refresh_all_mounts(store, reindex=True)
    assert calls == [["-m", "core.library_mounts", "index-docs", mid]]
