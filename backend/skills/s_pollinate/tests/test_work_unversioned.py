"""Edits made with no revision open (the agent skipped ``open``).

Before this, such edits could be rendered into out/ and shown, skipping the guard, and the
next ``open`` stored them as the person's version. Now ``render`` refuses them, and
``open --mine`` keeps them as the agent's own edits only when they fit the declared scope.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_common import (  # noqa: E402
    adopt_small, env, meta, need_browser, replace_in_slide, run, version, work,
)

__all__ = ["env"]


@pytest.fixture
def stub_render(monkeypatch):
    """Record render calls instead of starting a browser."""
    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        return {"outputs": {}, "findings": [], "probe": {}, "measure": []}

    monkeypatch.setattr(work.work_render, "render", fake)
    return calls


@pytest.fixture
def deck(env, capsys):
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    return d, ids


def test_render_refuses_edits_with_no_revision_open(deck, capsys, stub_render):
    d, ids = deck
    replace_in_slide(d, ids[2], "Charlie", "Charlie edited")
    capsys.readouterr()
    assert work.main(["render", "--dir", str(d)]) == 2
    err = capsys.readouterr().err
    assert ids[2] in err and "no revision is open" in err and "--mine" in err
    assert stub_render == []                       # nothing was rendered into out/
    assert meta(d)["current"] == "v001" and meta(d)["open"] is None


def test_render_of_unchanged_source_still_works(deck, capsys, stub_render):
    d, _ = deck
    assert work.main(["render", "--dir", str(d), "--formats", "png"]) == 0
    assert len(stub_render) == 1


def test_render_while_a_revision_is_open_still_works(deck, capsys, stub_render):
    d, ids = deck
    run(["open", "--dir", str(d), "--scope", ids[1]], capsys)
    replace_in_slide(d, ids[1], "Bravo", "Bravo edited")
    assert work.main(["render", "--dir", str(d), "--formats", "png"]) == 0
    assert len(stub_render) == 1


def test_open_mine_keeps_in_scope_edits_as_the_agents(deck, capsys):
    d, ids = deck
    replace_in_slide(d, ids[1], "Bravo", "Bravo edited")
    code, out = run(["open", "--dir", str(d), "--scope", ids[1], "--mine"], capsys)
    assert code == 0, out
    assert out["user_version"] is None
    assert out["changed_by_agent"] == [ids[1]] and out["changed_by_user"] == []
    m = meta(d)
    assert [v["id"] for v in m["versions"]] == ["v001"]   # no -user version
    assert m["current"] == "v001" and m["open"]["base"] == "v001"


def test_open_mine_refuses_edits_outside_the_scope(deck, capsys):
    """A wrong --mine must not turn the person's edit into something the guard makes you undo."""
    d, ids = deck
    replace_in_slide(d, ids[1], "Bravo", "Bravo edited")
    replace_in_slide(d, ids[3], "Delta", "Delta by the person")
    capsys.readouterr()
    assert work.main(["open", "--dir", str(d), "--scope", ids[1], "--mine"]) == 2
    err = capsys.readouterr().err
    assert ids[3] in err and "open without --mine" in err
    m = meta(d)
    assert m["open"] is None and [v["id"] for v in m["versions"]] == ["v001"]
    assert "Delta by the person" in (d / "src" / "deck.html").read_text(encoding="utf-8")


def test_open_without_mine_still_stores_the_persons_version(deck, capsys):
    d, ids = deck
    replace_in_slide(d, ids[3], "Delta", "Delta by the person")
    code, out = run(["open", "--dir", str(d), "--scope", ids[1]], capsys)
    assert code == 0 and out["user_version"] == "v001-user"
    assert out["changed_by_user"] == [ids[3]] and out["changed_by_agent"] == []


def test_open_mine_then_cut_versions_the_edit_as_the_agents(deck, capsys):
    need_browser()
    d, ids = deck
    replace_in_slide(d, ids[1], "Bravo", "Bravo edited")
    assert run(["open", "--dir", str(d), "--scope", ids[1], "--mine"], capsys)[0] == 0
    code, out = run(["cut", "--dir", str(d), "--formats", "png"], capsys)
    assert code == 0, out
    v2 = version(d, "v002")
    assert v2["by"] == "agent" and v2["base"] == "v001"
    assert v2["hashes"][ids[1]] != version(d, "v001")["hashes"][ids[1]]


def test_open_mine_accepts_a_slide_the_agent_already_deleted(deck, capsys):
    from test_work_common import deck_text, section_of, set_deck_text
    d, ids = deck
    text = deck_text(d)
    set_deck_text(d, text.replace(section_of(text, ids[2]), ""))
    capsys.readouterr()
    assert work.main(["render", "--dir", str(d)]) == 2
    assert f"--remove {ids[2]}" in capsys.readouterr().err   # the hint names the command that works
    code, out = run(["open", "--dir", str(d), "--scope", "", "--remove", ids[2], "--mine"], capsys)
    assert code == 0, out
    assert out["changed_by_agent"] == [ids[2]] and out["user_version"] is None


def test_render_hint_adds_shared_for_a_shared_file(deck, capsys):
    d, ids = deck
    css = d / "src" / "shared.css"
    css.write_text(css.read_text(encoding="utf-8") + "h2{color:red}\n", encoding="utf-8")
    capsys.readouterr()
    assert work.main(["render", "--dir", str(d)]) == 2
    assert "--shared --mine" in capsys.readouterr().err


def test_open_on_an_open_revision_reports_both_change_lists(deck, capsys):
    d, ids = deck
    run(["open", "--dir", str(d), "--scope", ids[1]], capsys)
    code, out = run(["open", "--dir", str(d), "--scope", ids[1], "--mine"], capsys)
    assert code == 0 and out["changed_by_agent"] == [] and out["changed_by_user"] == []
