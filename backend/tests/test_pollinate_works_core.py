"""core/pollinate_works: the overlay's rows, one work's view and the eval, read from files.

Each test writes a small workspace by hand (registry, work.json, .versions/*/checks.json and
guard.json, out/ files) and checks the numbers against a count made by hand.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core import pollinate_works as pw

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def _work(root: Path, rel: str, *, kind="deck", title="T", slides=("s-1", "s-2", "s-3"), current="v001",
          versions=(), open_=None, size=(1920, 1080), files=(), checks=None, guards=None, brief=None,
          extra=None) -> Path:
    d = root / rel
    meta = {"schema": 1, "kind": kind, "title": title, "style": "clean",
            "size": {"w": size[0], "h": size[1]} if size else None, "src": "src/deck.html",
            "slides": list(slides), "current": current, "open": open_, "versions": list(versions)}
    meta.update(extra or {})
    _write(d / "work.json", meta)
    for f in files:
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_bytes(b"x" * 10)
    for vid, findings in (checks or {}).items():
        _write(d / ".versions" / vid / "checks.json", {"ok": True, "findings": findings})
    for vid, g in (guards or {}).items():
        _write(d / ".versions" / vid / "guard.json", g)
    if brief is not None:
        (d / "brief.md").write_text(brief, encoding="utf-8")
    return d


def _registry(ws: Path, dirs, updated=None) -> None:
    _write(ws / ".pollinate" / "works.json",
           {"schema": 1, "works": [{"path": str(d), **({"updated": updated} if updated else {})} for d in dirs]})


def _v(vid, by="agent", status="ok", at="2026-11-25T10:00:00Z", scope=(), base=None, **kw):
    return {"id": vid, "by": by, "status": status, "at": at, "scope": list(scope), "base": base, **kw}


@pytest.fixture
def ws(tmp_path):
    return tmp_path.resolve()


def test_work_rows(ws):
    deck = _work(ws, "Projects/AIDLC/assets/deck", title="AIDLC",
                 versions=[_v("v001", by="user", note="adopted"), _v("v002", base="v001", scope=["s-2"], session="S1"),
                           _v("v002-user", by="user", status="ok", found_on_disk=True),
                           _v("v003", status="failed", base="v002-user", scope=["s-3"])],
                 current="v002-user",
                 open_={"scope": ["s-3"], "remove": [], "add": 0, "note": "fix title", "at": "2026-11-26T08:00:00Z",
                        "base": "v002-user", "session": "S2"},
                 files=["out/deck.html", "out/slides/01-s-1.png"])
    film = ws / "Knowledge" / "Pollinate" / "film"
    _write(film / "work.json", {"schema": 1, "kind": "video", "title": "Edge", "style": "anatomy",
                                "size": {"w": 1920, "h": 1080}, "src": None, "slides": [], "current": None,
                                "open": None, "versions": []})
    _write(film / "storyboard.json", {"scenes": [{"id": "S01", "duration": 10.5}, {"id": "S02", "duration": 20}]})
    (film / "out").mkdir()
    (film / "out" / "edge.mp4").write_bytes(b"mp4")
    (film / "review").mkdir()
    (film / "review" / "S01-a.jpg").write_bytes(b"jpg")
    odd = _work(ws, "Knowledge/Pollinate/odd", kind="poster", size=None, slides="nope",
                versions=["bad", {"id": "v001", "status": "weird", "scope": "x"}], open_="not a dict",
                files=["out/poster.png"])
    _registry(ws, [deck, film, odd])
    rows = {r["title"]: r for r in pw.read_works(ws)["works"]}
    d = rows["AIDLC"]
    assert d["brain"] == "AIDLC" and d["slide_count"] == 3 and (d["width"], d["height"]) == (1920, 1080)
    assert d["open"] == {"scope": ["s-3"], "remove": [], "add": 0, "note": "fix title",
                         "at": "2026-11-26T08:00:00Z", "base": "v002-user", "session": "S2"}
    assert [(v["id"], v["by"], v["status"]) for v in d["versions"]] == [
        ("v001", "user", "ok"), ("v002", "agent", "ok"), ("v002-user", "user", "ok"), ("v003", "agent", "failed")]
    assert d["versions"][1]["session"] == "S1" and d["versions"][2]["found_on_disk"] is True
    assert d["latest"] == "v003" and d["status"] == "failed"
    assert d["thumbnail"] == "Projects/AIDLC/assets/deck/out/slides/01-s-1.png"
    v = rows["Edge"]
    assert v["kind"] == "video" and v["brain"] == "General" and v["main_output"] == "Knowledge/Pollinate/film/out/edge.mp4"
    assert v["thumbnail"] == "Knowledge/Pollinate/film/review/S01-a.jpg"
    assert v["duration_s"] == 30.5 and v["scene_count"] == 2 and v["slide_count"] is None
    o = rows["T"]   # every field of the odd work is broken: nulls and empties, still listed
    assert o["width"] is None and o["open"] is None and o["slide_count"] is None
    assert [(x["id"], x["status"], x["scope"]) for x in o["versions"]] == [("v001", None, [])]
    assert o["main_output"] == "Knowledge/Pollinate/odd/out/poster.png"


def test_work_view(ws, tmp_path_factory):
    deck = _work(ws, "Projects/AIDLC/assets/deck",
                 versions=[_v("v001", by="user"), _v("v002", base="v001", scope=["s-2"]),
                           _v("v003", status="failed", base="v002", scope=["s-3"], session="S9")],
                 current="v002", open_={"scope": ["s-3"], "base": "v002"},
                 files=["out/deck.html", "out/deck.pdf", "out/slides/01-s-1.png", "out/slides/03-s-3.png"],
                 checks={"v003": [{"slide": "s-3", "check": "nowrap", "level": "fail", "msg": "wraps"},
                                  {"slide": "s-1", "check": "font_floor", "level": "warn", "carried": True}]},
                 guards={"v003": {"ok": False, "violations": [{"slide": "s-1", "kind": "modified", "detail": "body"}]}},
                 brief="Who: leadership\nThesis: one line\n")
    _registry(ws, [deck])
    view = pw.work_view(ws, "Projects/AIDLC/assets/deck")
    assert view["brief"].startswith("Who: leadership")
    assert [(f["check"], f["level"], f["carried"]) for f in view["findings"]] == [
        ("nowrap", "fail", False), ("font_floor", "warn", True)]
    assert view["guard"] == {"ok": False, "violations": [{"slide": "s-1", "kind": "modified", "detail": "body"}]}
    assert [(v["id"], v["guard_ok"], v["fail_counts"]) for v in view["versions"]] == [
        ("v001", None, {}), ("v002", None, {}), ("v003", False, {"nowrap": 1})]
    assert [(s["n"], s["id"], s["changed"], s["image"]) for s in view["slides"]] == [
        (1, "s-1", False, "Projects/AIDLC/assets/deck/out/slides/01-s-1.png"),
        (2, "s-2", False, None),
        (3, "s-3", True, "Projects/AIDLC/assets/deck/out/slides/03-s-3.png")]
    assert {o["name"] for o in view["outputs"]} == {"deck.html", "deck.pdf", "slides/"}
    assert [o["files"] for o in view["outputs"] if o["name"] == "slides/"] == [2]
    # the newest version's session → open_session; open_canvas first
    assert view["session"] == "S9"
    assert [(a["key"], a["primary"]) for a in view["actions"]] == [("open_canvas", True), ("open_session", False)]
    # not registered → None, including tricks that resolve to the registered folder
    for bad in ("Projects/AIDLC/assets/other", "Projects/AIDLC/assets/deck/../deck", str(deck), "", "../etc"):
        assert pw.work_view(ws, bad) is None, bad
    # a work outside the workspace: no Canvas, continue in chat
    out = _work(tmp_path_factory.mktemp("desk"), "deck", versions=[_v("v001", by="user")], files=["out/deck.html"])
    _registry(ws, [deck, out])
    v2 = pw.work_view(ws, str(out.resolve()))
    assert [a["key"] for a in v2["actions"]] == ["continue_in_chat"]


def test_work_view_never_reads_through_a_symlink_out(ws, tmp_path_factory):
    secret = tmp_path_factory.mktemp("secret")
    (secret / "brief.md").write_text("TOP SECRET", encoding="utf-8")
    deck = _work(ws, "Projects/P/assets/deck", versions=[_v("v001", by="user")])
    (deck / "brief.md").symlink_to(secret / "brief.md")
    (deck / "out").symlink_to(secret, target_is_directory=True)
    _registry(ws, [deck])
    view = pw.work_view(ws, "Projects/P/assets/deck")
    assert view["brief"] is None and view["outputs"] == [] and view["work"]["main_output"] is None


def test_eval_summary(ws):
    cur, prev = "2026-11-25T10:00:00Z", "2026-11-05T10:00:00Z"
    a = _work(ws, "Projects/A/assets/a", title="A",
              versions=[_v("v001", by="user", at="2026-10-01T00:00:00Z"),
                        # cur revision 1: first cut passed
                        _v("v002", base="v001", at=cur),
                        # cur revision 2: failed, then passed (guard blocked the first)
                        _v("v003", status="failed", base="v002", at=cur),
                        _v("v004", base="v002", at=cur),
                        _v("v004-user", by="user", found_on_disk=True, at=cur),
                        # prev revision: failed and never passed
                        ],
              checks={"v003": [{"check": "nowrap", "level": "fail"}, {"check": "nowrap", "level": "fail"},
                               {"check": "density", "level": "warn"}]},
              guards={"v003": {"ok": False, "violations": [{"slide": "s-9", "kind": "modified", "detail": "page 9 changed"}]},
                      "v004": {"ok": True, "violations": []}})
    b = _work(ws, "Projects/B/assets/b", title="B",
              versions=[_v("v001", by="user", at="2026-10-01T00:00:00Z"),
                        _v("v002", status="failed", base="v001", at=prev),
                        _v("v001-user", by="user", found_on_disk=True, at=prev)],
              checks={"v002": "{ corrupt"})
    film = ws / "film"
    _write(film / "work.json", {"schema": 1, "kind": "video", "title": "F", "versions": [_v("v001", at=cur)]})
    _registry(ws, [a, b, film])
    e = pw.eval_summary(ws, now=NOW, days=15)
    assert e["rate"] == {"cur": {"passed": 1, "revisions": 2, "pct": 50}, "prev": {"passed": 0, "revisions": 1, "pct": 0}}
    assert e["works_changed"] == {"cur": 1, "prev": 1}
    assert e["versions_median"] == {"cur": 3, "prev": 1}
    assert e["person_versions"] == {"cur": 1, "prev": 1}
    assert e["guard_blocks"] == {"cur": 1, "prev": 0}
    assert e["check_hits"] == [{"check": "nowrap", "count": 2, "top_title": "A", "top_count": 2}]
    assert [(g["title"], g["version"], g["violations"]) for g in e["guard_list"]] == [("A", "v003", ["page 9 changed"])]
    assert len(e["daily"]) == 30 and e["daily"][-1]["date"] == "2026-11-26"
    day = {x["date"]: x for x in e["daily"]}
    assert (day["2026-11-25"]["first_pass"], day["2026-11-25"]["later"]) == (1, 1)
    assert day["2026-11-05"]["not_passed"] == 1


def test_eval_with_no_works_is_empty(ws):
    e = pw.eval_summary(ws, now=NOW)
    assert e["rate"] == {"cur": None, "prev": None} and e["check_hits"] == [] and e["guard_list"] == []
    assert e["versions_median"] == {"cur": None, "prev": None}


# ── review fixes: bounded reads, scene ids, day windows, per-work hits ──

def test_work_json_symlinked_out_or_a_fifo_is_skipped_not_read(ws, tmp_path_factory):
    import os
    outside = tmp_path_factory.mktemp("elsewhere")
    _write(outside / "work.json", {"schema": 1, "kind": "deck", "title": "Elsewhere", "versions": []})
    linked = ws / "Projects" / "P" / "assets" / "linked"
    linked.mkdir(parents=True)
    (linked / "work.json").symlink_to(outside / "work.json")
    fifo = ws / "Projects" / "P" / "assets" / "fifo"
    fifo.mkdir(parents=True)
    os.mkfifo(fifo / "work.json")          # opening it blocking would hang the request
    good = _work(ws, "Projects/P/assets/good", versions=[_v("v001", by="user")])
    _registry(ws, [linked, fifo, good])
    body = pw.read_works(ws)
    assert [r["title"] for r in body["works"]] == ["T"] and body["skipped"] == 2


def test_read_bounded_refuses_devices_and_cuts_when_asked(tmp_path):
    with pytest.raises(ValueError):
        pw._read_bounded(Path("/dev/zero"), 1024)
    big = tmp_path / "brief.md"
    big.write_bytes(b"a" * 5000)
    assert len(pw._read_bounded(big, 100, truncate=True)) == 100
    with pytest.raises(ValueError):
        pw._read_bounded(big, 100)


def test_storyboard_scene_ids_cannot_walk_out(ws):
    film = ws / "Knowledge" / "Pollinate" / "film"
    _write(film / "work.json", {"schema": 1, "kind": "video", "title": "F", "versions": []})
    _write(film / "storyboard.json", {"scenes": [{"id": "../../**/x", "duration": 5}, {"id": "S01", "duration": 3}]})
    (film / "out").mkdir()
    (film / "out" / "S01-a.jpg").write_bytes(b"jpg")
    (film / "out" / "f.mp4").write_bytes(b"mp4")
    _registry(ws, [film])
    row = pw.read_works(ws)["works"][0]
    assert row["scene_count"] == 1 and row["duration_s"] == 3
    view = pw.work_view(ws, "Knowledge/Pollinate/film")
    assert [(s["id"], s["image"]) for s in view["scenes"]] == [("S01", "Knowledge/Pollinate/film/out/S01-a.jpg")]


def test_eval_windows_are_the_days_the_bars_show(ws):
    # NOW is 2026-11-26 12:00 UTC: cur = 11-12..11-26, prev = 10-28..11-11, bars = 10-28..11-26
    w = _work(ws, "Projects/A/assets/a", title="A",
              versions=[_v("v001", by="user", at="2026-10-01T00:00:00Z"),
                        _v("v002", base="v001", at="2026-10-28T00:30:00Z"),     # first bar day → prev
                        _v("v003", base="v002", at="2026-10-27T23:30:00Z"),     # the day before → nowhere
                        _v("v004", base="v003", at="2026-11-12T00:10:00Z")])    # first cur day → cur
    _registry(ws, [w])
    e = pw.eval_summary(ws, now=NOW, days=15)
    assert e["daily"][0]["date"] == "2026-10-28" and e["daily"][15]["date"] == "2026-11-12"
    assert e["rate"]["prev"]["revisions"] == 1 and e["rate"]["cur"]["revisions"] == 1
    assert sum(d["first_pass"] for d in e["daily"]) == 2      # every counted revision has its bar


def test_check_hits_count_each_work_apart_even_with_one_title(ws):
    common = dict(title="Same", checks={"v002": [{"check": "fill", "level": "fail"}] * 2})
    a = _work(ws, "Projects/A/assets/a", versions=[_v("v001", by="user"), _v("v002", status="failed", base="v001")], **common)
    b = _work(ws, "Projects/B/assets/b", versions=[_v("v001", by="user"), _v("v002", status="failed", base="v001")], **common)
    _registry(ws, [a, b])
    e = pw.eval_summary(ws, now=NOW)
    assert e["check_hits"] == [{"check": "fill", "count": 4, "top_title": "Same", "top_count": 2}]


def test_eval_days_are_the_persons_local_days(ws):
    # 07:00 on 11-26 in UTC+8 is 23:00 on 11-25 UTC: it counts on the person's 11-26
    w = _work(ws, "Projects/A/assets/a", title="A",
              versions=[_v("v001", by="user", at="2026-10-01T00:00:00Z"), _v("v002", base="v001", at="2026-11-25T23:00:00Z")])
    _registry(ws, [w])
    local = {d["date"]: d for d in pw.eval_summary(ws, now=NOW, tz_minutes=480)["daily"]}
    utc = {d["date"]: d for d in pw.eval_summary(ws, now=NOW, tz_minutes=0)["daily"]}
    assert local["2026-11-26"]["first_pass"] == 1 and local["2026-11-25"]["first_pass"] == 0
    assert utc["2026-11-25"]["first_pass"] == 1


def test_brief_is_read_up_to_its_cap(ws):
    deck = _work(ws, "Projects/P/assets/d", versions=[_v("v001", by="user")], brief="x" * (pw.MAX_BRIEF_BYTES * 2))
    _registry(ws, [deck])
    assert len(pw.work_view(ws, "Projects/P/assets/d")["brief"]) == pw.MAX_BRIEF_BYTES


def test_eval_window_edge_follows_the_local_day(ws):
    # 2026-11-11T20:00Z is 11-12 04:00 in UTC+8: the first day of the current window there,
    # the last day of the previous one in UTC
    w = _work(ws, "Projects/A/assets/a", title="A",
              versions=[_v("v001", by="user", at="2026-10-01T00:00:00Z"), _v("v002", base="v001", at="2026-11-11T20:00:00Z")])
    _registry(ws, [w])
    local = pw.eval_summary(ws, now=NOW, tz_minutes=480)["rate"]
    utc = pw.eval_summary(ws, now=NOW, tz_minutes=0)["rate"]
    assert local["cur"]["revisions"] == 1 and local["prev"] is None
    assert utc["prev"]["revisions"] == 1 and utc["cur"] is None


def test_work_view_reads_the_brief_through_the_bounded_reader(ws, monkeypatch):
    deck = _work(ws, "Projects/P/assets/d", versions=[_v("v001", by="user")], brief="who: x")
    _registry(ws, [deck])
    calls = []
    real = pw._read_bounded
    monkeypatch.setattr(pw, "_read_bounded", lambda p, limit, **kw: calls.append((p.name, limit, kw)) or real(p, limit, **kw))
    assert pw.work_view(ws, "Projects/P/assets/d")["brief"] == "who: x"
    assert ("brief.md", pw.MAX_BRIEF_BYTES, {"truncate": True}) in calls
