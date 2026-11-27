"""derive.py: a new film from an old one by a plan. Reused scenes keep their pages (byte for byte), the files
they load and, when the narration is unchanged, their voice; changed narration is marked for re-voicing; a
finished render is carried only when the scene's picture cannot have changed, so render.py re-renders only new
and changed scenes. Run: python3 -m pytest tests/test_derive.py -q (from the skill directory)."""
import json
import os

from mv_fixtures import load_sb, make_project, need_browser, save_sb, write_page, write_voice

import derive  # noqa: E402
import film  # noqa: E402
import render as R  # noqa: E402


def read(p, *rel):
    return open(os.path.join(p, *rel), "rb").read()


def plan_file(tmp_path, plan, name="plan.json"):
    path = str(tmp_path / name)
    json.dump(plan, open(path, "w"))
    return path


PLAN = {"storyboard": {"title": "Synthetic, longer"},
        "scenes": [{"reuse": "S02"},
                   {"reuse": "S01", "narration": "alpha line changed now"},
                   {"title": "New", "narration": "golf brand new line", "visual": "a new card", "shot_type": "statement"},
                   {"reuse": "S03", "visual": "same picture, better note"}]}


def fake_old_build(project, stale=()):
    """a build/ as render.py leaves it: one mp4 per scene and the fingerprint render.py computes"""
    sb = load_sb(project)
    b = os.path.join(project, "build")
    os.makedirs(b, exist_ok=True)
    for s in sb["scenes"]:
        _d, _t, fp, _r = derive.render_state(project, sb, s["id"])
        open(os.path.join(b, f"{s['id']}.mp4"), "wb").write(b"mp4 of " + s["id"].encode())
        open(os.path.join(b, f"{s['id']}.fp"), "w").write(("stale" if s["id"] in stale else fp) + "\n")


def finish_new(new):
    """what the director does after derive: tts.py for the changed and new scenes, the new page, film.py"""
    sb = load_sb(new)
    for s in sb["scenes"]:
        if s["duration"] is None:
            write_voice(new, s["id"], s["narration"], seed="new voice")
            s["duration"] = 2.0
    save_sb(new, sb)
    write_page(new, "S03", "GOLF", "golf")
    assert film.main(["--project", new]) == 0


def test_derive_copies_reused_scenes_and_marks_revoice(tmp_path, capsys):
    old = make_project(str(tmp_path / "old"))
    new = str(tmp_path / "new")
    assert derive.main(["--from", old, "--to", new, "--plan", plan_file(tmp_path, PLAN)]) == 0
    out = capsys.readouterr().out
    # pages byte for byte, under their new ids
    assert read(new, "scenes", "S01.html") == read(old, "scenes", "S02.html")
    assert read(new, "scenes", "S02.html") == read(old, "scenes", "S01.html")
    assert read(new, "scenes", "S04.html") == read(old, "scenes", "S03.html")
    assert not os.path.exists(os.path.join(new, "scenes", "S03.html"))          # new: to be written
    # the files the pages load, and the film-level files
    assert read(new, "assets", "dot.svg") == read(old, "assets", "dot.svg")
    assert not os.path.exists(os.path.join(new, "assets", "unused.svg"))
    for rel in (("scenes", "shared.css"), ("film.json",), ("design.md",)):
        assert read(new, *rel) == read(old, *rel)
    # voice kept for identical narration, not for changed narration
    for ext in (".mp3", ".words.json", ".captions.json"):
        assert read(new, "audio", "voice", "S01" + ext) == read(old, "audio", "voice", "S02" + ext)
        assert read(new, "audio", "voice", "S04" + ext) == read(old, "audio", "voice", "S03" + ext)
        assert not os.path.exists(os.path.join(new, "audio", "voice", "S02" + ext))
    assert json.load(open(os.path.join(new, "audio", "voice", "S01.tts.json")))["scene"] == "S01"
    sb, osb = load_sb(new), load_sb(old)
    assert [s["id"] for s in sb["scenes"]] == ["S01", "S02", "S03", "S04"]
    assert sb["title"] == "Synthetic, longer" and sb["style"] == osb["style"]
    assert sb["scenes"][0]["duration"] == osb["scenes"][1]["duration"]
    assert sb["scenes"][1]["duration"] is None and sb["scenes"][1]["narration"] == "alpha line changed now"
    assert sb["scenes"][2]["duration"] is None and sb["scenes"][2]["transition"] == "cut"
    assert sb["scenes"][3]["visual"] == "same picture, better note"
    rec = json.load(open(os.path.join(new, "derive.json")))
    assert rec["from"] == os.path.abspath(old)
    assert [(r["id"], r["from"], r["status"]) for r in rec["scenes"]] == [
        ("S01", "S02", "reused"), ("S02", "S01", "revoice"), ("S03", None, "new"), ("S04", "S03", "reused")]
    assert [d["id"] for d in rec["dropped"]] == ["S04"]
    assert "re-voice (narration changed): S02" in out and "write (new scenes): S03" in out
    assert "dropped from the old film" in out and "S04 Delta" in out and "--derived-from" in out
    # the old project is untouched
    assert load_sb(old) == osb


def test_derive_refuses_bad_input(tmp_path, capsys):
    old = make_project(str(tmp_path / "old"))
    new = str(tmp_path / "new")
    cases = [
        {"scenes": [{"reuse": "S09"}]},                                   # unknown old scene
        {"scenes": "S01"},                                                # malformed
        {"scenes": [{"reuse": "S01", "id": "S07"}]},                      # ids are assigned
        {"scenes": [{"title": "x", "narration": "y"}]},                   # a new scene without visual, shot_type
        {"scenes": [{"reuse": "S01"}], "copy": ["../secret.txt"]},        # outside the project
    ]
    for k, plan in enumerate(cases):
        assert derive.main(["--from", old, "--to", new, "--plan", plan_file(tmp_path, plan, f"p{k}.json")]) == 2, plan
        assert not os.path.exists(new)
    ok = plan_file(tmp_path, {"scenes": [{"reuse": "S01"}]}, "ok.json")
    assert derive.main(["--from", str(tmp_path / "missing"), "--to", new, "--plan", ok]) == 2
    os.makedirs(new)
    open(os.path.join(new, "mine.txt"), "w").write("keep")
    assert derive.main(["--from", old, "--to", new, "--plan", ok]) == 2
    assert os.listdir(new) == ["mine.txt"] and open(os.path.join(new, "mine.txt")).read() == "keep"
    assert derive.main(["--from", old, "--to", os.path.join(old, "inner"), "--plan", ok]) == 2
    assert derive.main(["--carry", "--to", str(tmp_path / "old")]) == 2                 # not a derived project
    assert not [n for n in os.listdir(tmp_path) if ".derive-" in n]                      # no temp folders left
    capsys.readouterr()


def test_carry_writes_render_fingerprint_only_for_identical_inputs(tmp_path, capsys):
    old = make_project(str(tmp_path / "old"))
    fake_old_build(old, stale=("S03",))
    plan = {"scenes": [{"reuse": "S02"}, {"reuse": "S01", "narration": "alpha line changed now"},
                       {"title": "New", "narration": "golf brand new line", "visual": "v", "shot_type": "statement"},
                       {"reuse": "S03"}, {"reuse": "S04", "transition": "fade"}, {"reuse": "S01", "narration": "alpha line number 1"}]}
    new = str(tmp_path / "new")
    assert derive.main(["--from", old, "--to", new, "--plan", plan_file(tmp_path, plan)]) == 0
    rec = json.load(open(os.path.join(new, "derive.json")))
    # new and re-voiced scenes have no duration yet, so there is no film data and nothing is carried
    assert all("not carried" in r.get("render", "") for r in rec["scenes"] if r["from"])
    assert "no scenes/film-data.js" in next(r for r in rec["scenes"] if r["id"] == "S01")["render"]
    assert not os.path.exists(os.path.join(new, "build"))
    finish_new(new)
    assert derive.main(["--carry", "--to", new]) == 0
    rec = {r["id"]: r for r in json.load(open(os.path.join(new, "derive.json")))["scenes"]}
    sb = load_sb(new)
    b = os.path.join(new, "build")
    # S01 (old S02) and S06 (old S01, same narration) are carried with render.py's own fingerprint
    for nid, oid in (("S01", "S02"), ("S06", "S01")):
        assert rec[nid]["render"] == f"carried build/{oid}.mp4"
        assert read(new, "build", f"{nid}.mp4") == read(old, "build", f"{oid}.mp4")
        assert open(os.path.join(b, f"{nid}.fp")).read().strip() == derive.render_state(new, sb, nid)[2]
    # re-voiced (S02), stale old render (S04 from old S03), fade tail added (S05 from old S04): not carried
    assert rec["S02"]["render"].startswith("re-render")
    assert "not current" in rec["S04"]["render"]
    assert rec["S05"]["render"].startswith("re-render") or "not current" in rec["S05"]["render"]
    for nid in ("S02", "S03", "S04", "S05"):
        assert not os.path.exists(os.path.join(b, f"{nid}.fp"))
    assert derive.main(["--carry", "--to", new]) == 0                        # idempotent
    rec = {r["id"]: r for r in json.load(open(os.path.join(new, "derive.json")))["scenes"]}
    assert rec["S01"]["render"] == "render current"
    capsys.readouterr()


def test_pages_reading_their_film_entry_are_not_carried_when_a_neighbour_changes(tmp_path, capsys):
    """a page that reads FL.scene, or loads a project script, may show the next section's title"""
    old = make_project(str(tmp_path / "old"), n=5)
    s01 = os.path.join(old, "scenes", "S01.html")
    page = open(s01).read()
    open(s01, "w").write(page.replace("  MV.register(tl);", "  const nextTitle = FL.scene.next;\n  MV.register(tl);"))
    open(os.path.join(old, "scenes", "extra.js"), "w").write("window.EXTRA = 1;\n")
    s03 = os.path.join(old, "scenes", "S03.html")
    page = open(s03).read()
    open(s03, "w").write(page.replace("</head>", '<script src="extra.js"></script>\n</head>'))
    s02 = os.path.join(old, "scenes", "S02.html")
    page = open(s02).read()
    open(s02, "w").write(page.replace("  MV.register(tl);", "  const { scene } = FL;\n  MV.register(tl);"))
    s04 = os.path.join(old, "scenes", "S04.html")
    page = open(s04).read()
    open(s04, "w").write(page.replace("FL.setup(tl, {});", "const L = FL.setup(tl, {});"))
    film.write(old)
    fake_old_build(old)
    plan = {"scenes": [{"reuse": "S01"}, {"reuse": "S02", "title": "Bravo renamed"},
                       {"reuse": "S03"}, {"reuse": "S04", "title": "Delta renamed"}, {"reuse": "S05"}]}
    new = str(tmp_path / "new")
    assert derive.main(["--from", old, "--to", new, "--plan", plan_file(tmp_path, plan)]) == 0
    rec = {r["id"]: r for r in json.load(open(os.path.join(new, "derive.json")))["scenes"]}
    assert rec["S01"]["render"].startswith("re-render")          # reads FL.scene; its next title changed
    assert rec["S02"]["render"].startswith("re-render")          # destructures FL; its own title changed
    assert rec["S03"]["render"].startswith("re-render")          # loads a project script; its next title changed
    assert rec["S04"]["render"] == "carried build/S04.mp4"       # FL.setup's handle carries no entry: same picture
    assert rec["S05"]["render"] == "carried build/S05.mp4"
    # the same S02 page with its own title kept, now followed by a renamed scene: it reads its whole entry
    plan2 = {"scenes": [{"reuse": "S02"}, {"reuse": "S03", "title": "Charlie renamed"}]}
    new2 = str(tmp_path / "new2")
    assert derive.main(["--from", old, "--to", new2, "--plan", plan_file(tmp_path, plan2, "plan2.json")]) == 0
    rec2 = {r["id"]: r for r in json.load(open(os.path.join(new2, "derive.json")))["scenes"]}
    assert rec2["S01"]["render"].startswith("re-render")
    assert read(new, "scenes", "extra.js") == read(old, "scenes", "extra.js")
    capsys.readouterr()


def test_files_symlinked_outside_the_old_project_are_not_copied(tmp_path, capsys):
    old = make_project(str(tmp_path / "old"))
    secret = tmp_path / "secret.svg"
    secret.write_text("<svg>private</svg>")
    dot = os.path.join(old, "assets", "dot.svg")
    words = os.path.join(old, "audio", "voice", "S01.words.json")
    os.remove(dot)
    os.remove(words)
    try:
        os.symlink(str(secret), dot)
        os.symlink(str(secret), words)
        os.symlink(str(secret), os.path.join(old, "assets", "link.svg"))
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("cannot make symlinks here")
    new = str(tmp_path / "new")
    plan = {"scenes": [{"reuse": "S01"}], "copy": ["assets/link.svg"]}
    assert derive.main(["--from", old, "--to", new, "--plan", plan_file(tmp_path, plan, "copy.json")]) == 2
    assert not os.path.exists(new)
    assert derive.main(["--from", old, "--to", new, "--plan", plan_file(tmp_path, {"scenes": [{"reuse": "S01"}]})]) == 0
    assert not os.path.exists(os.path.join(new, "assets", "dot.svg"))
    assert not os.path.exists(os.path.join(new, "audio", "voice", "S01.words.json"))
    assert os.path.exists(os.path.join(new, "audio", "voice", "S01.mp3"))
    page = os.path.join(old, "scenes", "S02.html")
    os.remove(page)
    os.symlink(str(secret), page)                               # a scene page that is a link to an outside file
    new3 = str(tmp_path / "new3")
    assert derive.main(["--from", old, "--to", new3, "--plan", plan_file(tmp_path, {"scenes": [{"reuse": "S02"}]}, "p3.json")]) == 0
    assert not os.path.exists(os.path.join(new3, "scenes", "S01.html"))
    rec = json.load(open(os.path.join(new3, "derive.json")))["scenes"][0]
    assert rec["status"] == "new" and "of its own" in rec["note"]
    capsys.readouterr()


def test_progress_bar_change_blocks_carry(tmp_path, capsys):
    """with the film-wide progress bar on, a reused scene whose place in the film changed has another picture"""
    old = make_project(str(tmp_path / "old"), film={"progress": True})
    fake_old_build(old)
    plan = {"scenes": [{"reuse": "S01"}, {"reuse": "S03"}, {"reuse": "S02"}]}      # S04 dropped: the total changes
    new = str(tmp_path / "new")
    assert derive.main(["--from", old, "--to", new, "--plan", plan_file(tmp_path, plan)]) == 0
    rec = {r["id"]: r for r in json.load(open(os.path.join(new, "derive.json")))["scenes"]}
    assert all(r["render"].startswith("re-render") for r in rec.values())
    capsys.readouterr()


def test_inputs_digest_tracks_what_render_fingerprints(tmp_path):
    """derive's digest changes whenever render.py's fingerprint would, except for the scene id"""
    p = make_project(str(tmp_path / "p"), n=2)

    def both(sid="S01"):
        sb = load_sb(p)
        d, t, fp, roots = derive.render_state(p, sb, sid)
        return derive.inputs_digest(p, roots, d, t, film.read_data(p)), fp

    base = both()
    open(os.path.join(p, "scenes", "S01.html"), "a").write("<!-- edit -->\n")
    edited = both()
    assert edited[0] != base[0] and edited[1] != base[1]
    open(os.path.join(p, "assets", "dot.svg"), "a").write("<!-- new art -->\n")
    art = both()
    assert art[0] != edited[0] and art[1] != edited[1]
    open(os.path.join(p, "scenes", "shared.css"), "a").write(".x { opacity: .5; }\n")
    css = both()
    assert css[0] != art[0] and css[1] != art[1]
    write_voice(p, "S01", "alpha line number one again")
    voice = both()
    assert voice[0] != css[0] and voice[1] != css[1]
    # the same page and voice under another id: same digest, different fingerprint
    open(os.path.join(p, "scenes", "S02.html"), "wb").write(read(p, "scenes", "S01.html"))
    for ext in (".words.json", ".captions.json"):
        open(os.path.join(p, "audio", "voice", "S02" + ext), "wb").write(read(p, "audio", "voice", "S01" + ext))
    sb = load_sb(p)
    sb["scenes"][1]["narration"] = sb["scenes"][0]["narration"]
    save_sb(p, sb)
    film.write(p)
    one, two = both("S01"), both("S02")
    assert one[0] == two[0] and one[1] != two[1]


def test_render_after_derive_renders_only_new_and_changed(tmp_path):
    need_browser()
    old = make_project(str(tmp_path / "old"))
    assert R.main(["--project", old]) == 0
    new = str(tmp_path / "new")
    assert derive.main(["--from", old, "--to", new, "--plan", plan_file(tmp_path, PLAN)]) == 0
    finish_new(new)
    assert derive.main(["--carry", "--to", new]) == 0
    log = []
    R.render_scenes(new, log=log.append)
    skipped = sorted(m.split(":")[0] for m in log if "up to date (fingerprint match), skipped" in m)
    rendered = sorted(m.split(":")[0] for m in log if " frames, load " in m)
    assert skipped == ["S01", "S04"] and rendered == ["S02", "S03"]
    assert read(new, "build", "S01.mp4") == read(old, "build", "S02.mp4")
    assert R.render_scenes(new, log=lambda m: None) == []                          # everything current now
