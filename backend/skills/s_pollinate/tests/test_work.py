"""work.py without a browser: init, adopt, list, open, the guard as a function, URL checks.

Covers design section 11 item 2 (c) for open, and the adopt rule: adopt adds
ids and keeps every other byte.
"""
from __future__ import annotations

import json
import re
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_common import (  # noqa: E402
    DECK5, adopt_small, copy_fixture, deck_text, env, meta, replace_in_slide, run,
    set_deck_text, version, work, work_render,
)

__all__ = ["env"]


def test_init_creates_work_and_registry(env, capsys):
    d = env["tmp"] / "new"
    code, out = run(["init", "--dir", str(d), "--kind", "deck", "--title", "My deck"], capsys)
    assert code == 0
    m = meta(d)
    assert m["schema"] == 1 and m["kind"] == "deck" and m["src"] == "src/deck.html"
    assert m["size"] == {"w": 1920, "h": 1080} and m["current"] is None and m["versions"] == []
    assert (d / "src" / "deck.html").is_file()
    assert (d / ".gitignore").read_text().splitlines()[:2] == ["out/", "review/"]
    reg = json.loads((env["tmp"] / "ws" / ".pollinate" / "works.json").read_text())
    assert reg["schema"] == 1
    assert [w["path"] for w in reg["works"]] == [str(d.resolve())]
    assert reg["works"][0]["kind"] == "deck" and reg["works"][0]["title"] == "My deck"
    code, _ = run(["init", "--dir", str(d), "--kind", "deck", "--title", "again"], capsys)
    assert code == 2


def test_list_prints_registry(env, capsys):
    run(["init", "--dir", str(env["tmp"] / "a"), "--kind", "poster", "--title", "A"], capsys)
    code, out = run(["list"], capsys)
    assert code == 0
    assert out["works"][0]["kind"] == "poster" and out["works"][0]["exists"] is True


def test_adopt_only_adds_ids(env, capsys):
    """Design 11 / task item 4: adopt keeps every byte outside the added attributes."""
    d = copy_fixture(env["tmp"] / "deck")
    original = (DECK5 / "src" / "deck.html").read_bytes()
    assert b"\r\n" in original and b"data-slide-id" not in original
    code, out = run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "960x540"], capsys)
    assert code == 0
    after = (d / "src" / "deck.html").read_bytes()
    ids = out["slides"]
    assert len(ids) == 5 and len(set(ids)) == 5
    assert all(re.fullmatch(r"s-[0-9a-f]{4,8}", i) for i in ids)
    stripped = re.sub(rb' data-slide-id="s-[0-9a-f]{4,8}"', b"", after)
    assert stripped == original
    # the nested section is part of slide 3 and gets no id of its own
    assert after.count(b"data-slide-id") == 5
    assert b"<SECTION data-slide-id=" in after
    v1 = version(d, "v001")
    assert v1["by"] == "user" and meta(d)["current"] == "v001"
    snap = d / ".versions" / "v001" / "src"
    assert (snap / "deck.html").read_bytes() == after
    assert (snap / "shared.css").is_file() and (snap / "assets" / "photo.png").is_file()
    assert not (snap / "deck.html").stat().st_mode & stat.S_IWUSR


def test_adopt_keeps_existing_ids(env, capsys):
    d = env["tmp"] / "deck"
    (d / "src").mkdir(parents=True)
    text = '<body>\n<section data-slide-id="cover">a</section>\n<section>b</section>\n</body>\n'
    (d / "src" / "deck.html").write_text(text)
    code, out = run(["adopt", "--dir", str(d), "--kind", "deck"], capsys)
    assert code == 0
    assert out["slides"][0] == "cover" and len(out["ids_added"]) == 1


def test_adopt_in_place_tracks_only_referenced_files(env, capsys):
    """An adopted folder may hold other files (older decks); only used files are shared."""
    d = env["tmp"] / "assets"
    d.mkdir()
    (d / "deck-v13.html").write_text(
        '<link rel="stylesheet" href="style.css"><section>one</section>', encoding="utf-8")
    (d / "style.css").write_text("section{background:url(bg.png)}", encoding="utf-8")
    (d / "bg.png").write_bytes(b"png")
    (d / "deck-v12.html").write_text("<section>old</section>", encoding="utf-8")
    code, out = run(["adopt", "--dir", str(d), "--kind", "deck", "--src", "deck-v13.html"], capsys)
    assert code == 0
    shared = version(d, "v001")["hashes"]["_shared"]
    assert set(shared) == {"deck-v13.html", "style.css", "bg.png"}
    assert not (d / ".versions" / "v001" / "src" / "deck-v12.html").exists()


def test_open_absorbs_user_edit(env, capsys):
    """Design 11 item 2 (c): an edit made without open is stored first as a user version."""
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    replace_in_slide(d, ids[2], "Charlie", "Charlie by hand")
    code, out = run(["open", "--dir", str(d), "--scope", ids[1]], capsys)
    assert code == 0
    assert out["user_version"] == "v001-user" and out["changed_by_user"] == [ids[2]]
    m = meta(d)
    assert m["current"] == "v001-user" and m["open"]["base"] == "v001-user"
    uv = version(d, "v001-user")
    assert uv["found_on_disk"] is True
    assert uv["by"] == "user" and uv["hashes"][ids[2]] != version(d, "v001")["hashes"][ids[2]]
    snap = (d / ".versions" / "v001-user" / "src" / "deck.html").read_text()
    assert "Charlie by hand" in snap


def test_open_without_change_makes_no_user_version(env, capsys):
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    code, out = run(["open", "--dir", str(d), "--scope", ids[0], "--note", "title"], capsys)
    assert code == 0 and out["user_version"] is None
    assert meta(d)["open"]["base"] == "v001"


def test_second_open_keeps_base(env, capsys):
    """Work in progress between open and cut is the agent's, never a user version."""
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    run(["open", "--dir", str(d), "--scope", ids[0]], capsys)
    replace_in_slide(d, ids[0], "Alpha", "Alpha 2")
    code, out = run(["open", "--dir", str(d), "--scope", f"{ids[0]},{ids[1]}"], capsys)
    assert code == 0 and out["user_version"] is None
    m = meta(d)
    assert m["open"]["base"] == "v001" and m["open"]["scope"] == [ids[0], ids[1]]
    assert [v["id"] for v in m["versions"]] == ["v001"]


def test_open_rejects_unknown_ids(env, capsys):
    d = env["tmp"] / "deck"
    adopt_small(d, capsys)
    code, _ = run(["open", "--dir", str(d), "--scope", "s-nope"], capsys)
    assert code == 2


def test_cut_without_open_refused(env, capsys):
    d = env["tmp"] / "deck"
    adopt_small(d, capsys)
    code, _ = run(["cut", "--dir", str(d)], capsys)
    assert code == 2
    assert [v["id"] for v in meta(d)["versions"]] == ["v001"]


def _state(d: Path):
    m = meta(d)
    root, rel, _ = work.src_layout(d, m)
    return work.compute_state(m, work.collect_files(d, m), rel)


def test_guard_shared_needs_flag(env, capsys):
    """13.4: a shared file change is a violation unless the open record has shared=True (open --shared)."""
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    base = version(d, "v001")
    (d / "src" / "shared.css").write_text("section{background:red}", encoding="utf-8")
    state = _state(d)
    g = work.guard(base, {}, state, {"scope": [ids[0]], "note": "new title"})
    assert not g["ok"]
    assert g["violations"] == [{"slide": "_shared", "kind": "modified",
                                "detail": "shared file changed: shared.css"}]
    assert work.guard(base, {}, state, {"scope": [], "shared": True})["ok"]
    (d / "src" / "shared.css").unlink()
    g = work.guard(base, {}, _state(d), {"scope": []})
    assert g["violations"][0]["kind"] == "deleted" and "shared.css" in g["violations"][0]["detail"]


def test_guard_frame_change_is_shared(env, capsys):
    """A head style edit changes every slide; it is caught as a change outside the slides."""
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    base = version(d, "v001")
    set_deck_text(d, deck_text(d).replace("<title>Small</title>", "<title>Small</title><style>h2{color:red}</style>"))
    g = work.guard(base, {}, _state(d), {"scope": ids})
    assert [v["slide"] for v in g["violations"]] == ["_shared"]
    assert "outside its slides" in g["violations"][0]["detail"]


def test_guard_insert_is_not_a_move(env, capsys):
    """Adding a slide shifts positions; that alone moves nothing."""
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    base = version(d, "v001")
    text = deck_text(d)
    first = text.index("<section")
    set_deck_text(d, text[:first] + '<section data-slide-id="s-0new">\n  <h2>New</h2>\n</section>\n' + text[first:])
    g = work.guard(base, {}, _state(d), {"scope": [], "add": 1})
    assert g["ok"] and g["added"] == ["s-0new"] and g["notes"] == []
    g = work.guard(base, {}, _state(d), {"scope": [], "add": 0})
    assert g["ok"] and g["notes"]


def test_guard_swap_names_moved_slide(env, capsys):
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    base = version(d, "v001")
    text = deck_text(d)
    a, b = work.parse_slides(text)[1], work.parse_slides(text)[2]
    swapped = text[:a["start"]] + b["raw"] + text[a["end"]:b["start"]] + a["raw"] + text[b["end"]:]
    set_deck_text(d, swapped)
    g = work.guard(base, {}, _state(d), {"scope": []})
    assert not g["ok"] and len(g["violations"]) == 1
    v = g["violations"][0]
    assert v["kind"] == "moved" and v["slide"] in (ids[1], ids[2]) and "page" in v["detail"]
    # the same swap is fine when one of the two slides is in scope
    assert work.guard(base, {}, _state(d), {"scope": [ids[2]]})["ok"]


def test_change_detail_names_text_and_image():
    old = '<section><h1>Old title</h1><img src="a.png"></section>'
    assert work.change_detail(old, old.replace("Old", "New")) == "text changed: 'Old title' -> 'New title'"
    assert work.change_detail(old, old.replace('<img src="a.png">', "")) == "image removed: a.png"


@pytest.mark.parametrize("url,ips,ok", [
    ("https://fonts.googleapis.com/css2?family=Inter", ["142.250.1.1"], True),
    ("http://fonts.googleapis.com/css2", ["142.250.1.1"], False),
    ("https://internal.example/css", ["10.0.0.5"], False),
    ("https://meta.example/css", ["169.254.169.254"], False),
    ("https://loop.example/css", ["::1"], False),
    ("https://mixed.example/css", ["142.250.1.1", "192.168.1.2"], False),
    ("https://" + "name:" + "word" + "@fonts.example/css", ["142.250.1.1"], False),  # user info in URL
    ("file:///etc/passwd", [], False),
])
def test_font_url_validation(monkeypatch, url, ips, ok):
    monkeypatch.setattr(work_render, "_resolve", lambda host: ips)
    if ok:
        work_render.validate_url(url)
    else:
        with pytest.raises(work_render.FetchError):
            work_render.validate_url(url)


def test_local_refs_outside_work_are_not_read(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("secret")
    findings = []
    out = work_render.Inliner(root, findings).html(
        '<img src="../secret.txt"><img src="file:///etc/hostname">', root)
    assert "secret" not in out and out.count('src="data:,"') == 2
    assert [f["check"] for f in findings] == ["local_file", "local_file"]


def test_bad_scope_after_user_edit_does_not_block_the_work(env, capsys):
    """A wrong id in open must not leave a user version on disk that blocks every later open."""
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    replace_in_slide(d, ids[2], "Charlie", "Charlie by hand")
    code, _ = run(["open", "--dir", str(d), "--scope", "s-typo"], capsys)
    assert code == 2
    assert not (d / ".versions" / "v001-user").exists()
    code, out = run(["open", "--dir", str(d), "--scope", ids[1]], capsys)
    assert code == 0 and out["user_version"] == "v001-user"


def test_version_ids_skip_snapshot_left_on_disk(env, capsys):
    """A snapshot folder left by a crash before work.json was saved is never reused."""
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    (d / ".versions" / "v001-user").mkdir()
    (d / ".versions" / "v002").mkdir()
    assert work.user_version_id(meta(d), d) == "v001-user2"
    assert work.next_version_id(meta(d), d) == "v003"


def test_adopted_folder_keeps_tracking_unused_files(env, capsys):
    """In an adopted folder, a slide that swaps its image does not 'delete' the old image."""
    d = env["tmp"] / "assets"
    d.mkdir()
    (d / "deck.html").write_text('<section><img src="a.png"></section>\n<section>two</section>\n')
    (d / "a.png").write_bytes(b"a")
    (d / "b.png").write_bytes(b"b")
    code, out = run(["adopt", "--dir", str(d), "--kind", "deck", "--src", "deck.html"], capsys)
    sid = out["slides"][0]
    base = version(d, "v001")
    text = (d / "deck.html").read_text().replace('src="a.png"', 'src="b.png"')
    (d / "deck.html").write_text(text)
    m = meta(d)
    _root, rel, _ = work.src_layout(d, m)

    def state():
        files = work.collect_files(d, m, keep=base["hashes"]["_shared"])
        return work.compute_state(m, files, rel)

    assert work.guard(base, {}, state(), {"scope": [sid]})["ok"]
    (d / "a.png").unlink()
    g = work.guard(base, {}, state(), {"scope": [sid]})
    assert [(v["slide"], v["kind"]) for v in g["violations"]] == [("_shared", "deleted")]


def test_shared_flag_allows_shared_changes(env, capsys):
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    code, out = run(["open", "--dir", str(d), "--scope", ids[0], "--shared"], capsys)
    assert code == 0 and out["open"]["shared"] is True
    (d / "src" / "shared.css").write_text("section{background:red}", encoding="utf-8")
    assert work.guard(version(d, "v001"), {}, _state(d), meta(d)["open"])["ok"]


def test_unexpected_error_exits_2(env, capsys, monkeypatch):
    def boom(args):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(work, "cmd_list", boom)
    assert work.main(["list"]) == 2
    assert "disk on fire" in capsys.readouterr().err


def test_render_old_version_out_must_not_be_in_the_work(env, capsys):
    d = env["tmp"] / "deck"
    adopt_small(d, capsys)
    capsys.readouterr()
    code = work.main(["render", "--dir", str(d), "--version", "v001", "--out", str(d / "src")])
    assert code == 2
    assert "must be outside the work folder" in capsys.readouterr().err
    assert (d / "src" / "deck.html").is_file()


def test_inlined_css_cannot_close_its_style(tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "x.css").write_text("a{color:red}</STYLE><script>alert(1)</script>")
    out = work_render.Inliner(root, []).html('<link rel="stylesheet" href="x.css">', root)
    assert "</STYLE><script>" not in out and out.count("</style>") == 1


def test_http_get_checks_redirects_and_size(monkeypatch):
    monkeypatch.setattr(work_render, "_resolve",
                        lambda host: ["10.1.2.3"] if host == "inside.example" else ["93.184.216.34"])
    handler = work_render._CheckedRedirect()
    req = work_render.urllib.request.Request("https://fonts.example/css")
    with pytest.raises(work_render.FetchError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://inside.example/secret")

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"x" * n

    class Opener:
        def open(self, req, timeout):
            return Resp()

    monkeypatch.setattr(work_render.urllib.request, "build_opener", lambda *h: Opener())
    with pytest.raises(work_render.FetchError, match="larger than"):
        work_render.http_get("https://fonts.example/big.woff2")


def test_symlink_out_of_src_and_bad_paths_are_not_read(env, capsys):
    """A symlink under src/ pointing elsewhere in the work is skipped; stored paths stay inside."""
    d = env["tmp"] / "deck"
    adopt_small(d, capsys)
    (d / "src" / "peek.json").symlink_to(d / "work.json")
    m = meta(d)
    assert "peek.json" not in work.collect_files(d, m)
    assert work._safe_join(d, "../outside.json") is None
    assert work._safe_join(d, "/etc/hostname") is None
    assert work._safe_join(d, "src/deck.html") == (d / "src" / "deck.html").resolve()


def test_second_open_without_shared_turns_it_off(env, capsys):
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    run(["open", "--dir", str(d), "--scope", ids[0], "--shared"], capsys)
    code, out = run(["open", "--dir", str(d), "--scope", ids[0]], capsys)
    assert code == 0 and out["open"]["shared"] is False
    (d / "src" / "shared.css").write_text("section{background:red}", encoding="utf-8")
    assert not work.guard(version(d, "v001"), {}, _state(d), meta(d)["open"])["ok"]


def test_paths_stored_in_work_json_stay_inside(env, capsys):
    """A reference or a version source in work.json that points outside is refused (exit 2)."""
    d = env["tmp"] / "deck"
    adopt_small(d, capsys)
    (env["tmp"] / "outside.json").write_text('{"slides": []}')
    m = meta(d)
    m["reference"] = "../outside.json"  # exists, but outside the work
    (d / "work.json").write_text(json.dumps(m))
    capsys.readouterr()
    run(["open", "--dir", str(d), "--scope", ""], capsys)
    assert work.main(["cut", "--dir", str(d), "--formats", "png"]) == 2
    assert "reference measure must be a file inside the work" in capsys.readouterr().err
    m = meta(d)
    m.pop("reference")
    m["versions"][0]["source"] = "../../work.json"
    (d / "work.json").write_text(json.dumps(m))
    capsys.readouterr()
    assert work.main(["render", "--dir", str(d), "--version", "v001", "--out", str(env["tmp"] / "o")]) == 2
    assert "names a source outside its snapshot" in capsys.readouterr().err
    assert work._base_raws(d, m["versions"][0]) == {}


def test_note_never_grants_shared_permission(env, capsys):
    """A note is free text: 'leave shared.css alone' must not allow changing shared.css."""
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    code, out = run(["open", "--dir", str(d), "--scope", ids[0],
                     "--note", "fix title, leave shared.css alone"], capsys)
    assert code == 0 and out["open"]["shared"] is False
    (d / "src" / "shared.css").write_text("section{background:red}", encoding="utf-8")
    assert not work.guard(version(d, "v001"), {}, _state(d), meta(d)["open"])["ok"]
    # an open record from before the flag existed (no 'shared' key) still reads its note
    old_record = {"scope": [ids[0]], "note": "restyle shared CSS"}
    assert work.guard(version(d, "v001"), {}, _state(d), old_record)["ok"]


def test_reference_that_is_not_json_is_a_usage_error(env, capsys):
    d = env["tmp"] / "deck"
    adopt_small(d, capsys)
    (d / "ref.json").write_text("{not json")
    m = meta(d)
    m["reference"] = "ref.json"
    (d / "work.json").write_text(json.dumps(m))
    run(["open", "--dir", str(d), "--scope", ""], capsys)
    capsys.readouterr()
    assert work.main(["cut", "--dir", str(d)]) == 2
    assert "not readable JSON" in capsys.readouterr().err
