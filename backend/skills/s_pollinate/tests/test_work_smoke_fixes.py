"""Fixes found by cutting a real adopted deck (a person's 31-slide v10 HTML deck).

- ``_carry_over_unchanged``: a revision is judged on what it made worse, not on
  issues the person's deck already had;
- ``_python_argv``: the packaged SwarmAI program runs another script through ``py``;
- ``_stays_blank``: the JS-off check looks again before calling a slide blank;
- deck runtimes that hide inactive slides with visibility/opacity still capture, and
  deck.html stays readable with JavaScript off.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_common import env, meta, need_browser, run, work, work_render  # noqa: E402

__all__ = ["env"]

SHARED = work.SHARED


def _f(slide, check="font_floor", level="fail"):
    return {"slide": slide, "check": check, "level": level, "msg": "m", "where": ""}


def _base(tmp: Path, findings, hashes) -> tuple:
    """A base version with these findings in its checks.json; returns (base, its counts)."""
    snap = tmp / ".versions" / "v001"
    snap.mkdir(parents=True)
    (snap / "checks.json").write_text(json.dumps({"ok": True, "findings": findings}))
    base = {"id": "v001", "hashes": hashes}
    return base, work._base_counts(tmp, base)


def _state(hashes):
    return {"slides": [k for k in hashes if k != SHARED], "hashes": hashes}


def test_unchanged_slide_keeps_its_findings_as_warnings(tmp_path):
    base, before = _base(tmp_path, [], {SHARED: "x", "a": "1", "b": "2"})
    found = [_f("a"), _f("b"), _f("", "pdf_pages")]
    work._carry_over_unchanged(found, base, _state({SHARED: "x", "a": "1", "b": "9"}), before)
    assert [f["level"] for f in found] == ["warn", "fail", "fail"]
    assert found[0]["carried"] is True and found[0]["msg"].startswith("(unchanged slide")


def test_changed_slide_that_did_not_get_worse_is_carried(tmp_path):
    base, before = _base(tmp_path, [_f("a"), {**_f("a", "fill"), "level": "warn", "carried": True}],
                 {SHARED: "x", "a": "1"})
    found = [_f("a"), _f("a", "fill")]
    work._carry_over_unchanged(found, base, _state({SHARED: "x", "a": "2"}), before)
    assert [f["level"] for f in found] == ["warn", "warn"]
    assert found[0]["msg"].startswith("(already so before this version)")


def test_a_check_that_gained_findings_on_a_slide_fails_them_all(tmp_path):
    base, before = _base(tmp_path, [_f("a")], {SHARED: "x", "a": "1"})
    found = [_f("a"), _f("a"), _f("a", "fill")]
    work._carry_over_unchanged(found, base, _state({SHARED: "x", "a": "2"}), before)
    assert [f["level"] for f in found] == ["fail", "fail", "fail"]


def test_shared_change_drops_the_unchanged_rule_but_keeps_the_count_rule(tmp_path):
    base, before = _base(tmp_path, [_f("b")], {SHARED: "x", "a": "1", "b": "2"})
    found = [_f("a"), _f("b")]
    work._carry_over_unchanged(found, base, _state({SHARED: "y", "a": "1", "b": "2"}), before)
    assert [f["level"] for f in found] == ["fail", "warn"]


def test_no_base_carries_nothing(tmp_path):
    found = [_f("a")]
    work._carry_over_unchanged(found, None, _state({SHARED: "x", "a": "1"}), {})
    assert found[0]["level"] == "fail"


def test_a_check_that_could_not_judge_is_never_carried(tmp_path):
    base, before = _base(tmp_path, [_f("a", "craft")], {SHARED: "x", "a": "1"})
    found = [_f("a", "craft")]
    work._carry_over_unchanged(found, base, _state({SHARED: "x", "a": "1"}), before)
    assert found[0]["level"] == "fail"


def test_a_version_the_person_made_has_no_counts(tmp_path):
    (tmp_path / ".versions" / "v001").mkdir(parents=True)
    assert work._base_counts(tmp_path, {"id": "v001"}) is None


def test_python_argv_uses_py_in_the_packaged_program(monkeypatch):
    monkeypatch.setattr(work.sys, "executable", "/app/python-backend")
    monkeypatch.setattr(work.sys, "frozen", True, raising=False)
    assert work._python_argv() == ["/app/python-backend", "py"]
    monkeypatch.delattr(work.sys, "frozen")
    assert work._python_argv() == ["/app/python-backend"]


class _Shots:
    def __init__(self, blanks):
        self.blanks = list(blanks)
        self.calls = 0

    def screenshot(self, sel, i):
        self.calls += 1
        return b"blank" if self.blanks.pop(0) else b"content"


def test_js_off_check_looks_again_before_calling_a_slide_blank(monkeypatch):
    monkeypatch.setattr(work_render, "_is_blank", lambda png: png == b"blank")
    monkeypatch.setattr(work_render.time, "sleep", lambda s: None)
    late = _Shots([True, False, False, False])
    assert work_render._stays_blank(late, "section", 0) is False and late.calls == 2
    empty = _Shots([True] * work_render._BLANK_LOOKS)
    assert work_render._stays_blank(empty, "section", 0) is True
    assert empty.calls == work_render._BLANK_LOOKS


_STAGE = ('<html><head><style>section{width:320px;height:180px;background:#fff;color:#000;'
          'font-size:40px}section:not([data-deck-active]){visibility:hidden;opacity:0}'
          '.reveal{opacity:0}section[data-deck-active] .reveal{opacity:1}</style></head>'
          '<body><deck-stage><section><h2 class="reveal">One</h2></section>'
          '<section><h2 class="reveal">Two</h2></section>'
          '<section><h2 class="reveal">Three</h2></section></deck-stage>'
          '<script>document.querySelector("section").setAttribute("data-deck-active","")</script>'
          '</body></html>')


def test_deck_runtime_hiding_inactive_slides_still_captures_every_slide(env, capsys):
    """A deck-stage style runtime shows only the active slide, and its content enters only on
    the active one (``[data-deck-active] .reveal``); every slide must still render with its
    content, and deck.html must show all of them with JavaScript off."""
    need_browser()
    from io import BytesIO
    from PIL import Image
    d = env["tmp"] / "stage"
    (d / "src").mkdir(parents=True)
    (d / "src" / "deck.html").write_text(_STAGE)
    run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "320x180"], capsys)
    run(["open", "--dir", str(d), "--scope", ""], capsys)
    code, out = run(["cut", "--dir", str(d), "--formats", "html,png"], capsys)
    assert code == 0, out
    pngs = sorted((d / "out" / "slides").glob("*.png"))
    assert len(pngs) == 3
    for p in pngs:
        lo, hi = Image.open(p).convert("L").getextrema()
        assert hi - lo > 40, f"{p.name} is blank"
    # the export does not pre-set the active mark: presenting keeps its entrance effects
    import re
    assert not re.search(r"<section[^>]*data-deck-active", (d / "out" / "deck.html").read_text())


_OVER = ('<html><head><style>section{{width:320px;height:180px;position:relative;background:#fff}}'
         'p{{position:absolute;left:{x}px;top:10px;white-space:nowrap;margin:0}}</style></head><body>'
         '<section><p>{text}</p></section></body></html>')


def _adopt_over(env, capsys, name):
    d = env["tmp"] / name
    (d / "src").mkdir(parents=True)
    (d / "src" / "deck.html").write_text(_OVER.format(x=300, text="This line runs off the right edge"))
    run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "320x180"], capsys)
    sid = meta(d)["slides"][0]
    run(["open", "--dir", str(d), "--scope", sid], capsys)
    return d, sid


def test_first_edit_after_adopt_is_judged_against_the_adopted_slide(env, capsys, monkeypatch):
    """The adopted slide already ran off the edge; editing its text keeps that as a warning."""
    need_browser()
    monkeypatch.setattr(work, "_load_craft", lambda: None)
    d, sid = _adopt_over(env, capsys, "over-kept")
    p = d / "src" / "deck.html"
    p.write_text(p.read_text().replace("right edge", "right edge, still"))
    code, out = run(["cut", "--dir", str(d), "--formats", "png"], capsys)
    assert code == 0, out
    checks = json.loads((d / ".versions" / "v002" / "checks.json").read_text())
    assert [(f["check"], f["level"], f.get("carried")) for f in checks["findings"]] == [("overflow", "warn", True)]


def test_first_edit_after_adopt_that_adds_a_defect_fails(env, capsys, monkeypatch):
    need_browser()
    monkeypatch.setattr(work, "_load_craft", lambda: None)
    d = env["tmp"] / "over-new"
    (d / "src").mkdir(parents=True)
    (d / "src" / "deck.html").write_text(_OVER.format(x=10, text="Fits"))
    run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "320x180"], capsys)
    run(["open", "--dir", str(d), "--scope", meta(d)["slides"][0]], capsys)
    p = d / "src" / "deck.html"
    p.write_text(p.read_text().replace("left:10px", "left:300px").replace("Fits", "This line runs off the edge"))
    code, out = run(["cut", "--dir", str(d), "--formats", "png"], capsys)
    assert code == 1
    assert [f["check"] for f in out["failed_checks"]] == ["overflow"]
