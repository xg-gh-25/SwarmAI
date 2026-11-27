"""The shared film layer: scripts/film.py writes scenes/film-data.js from storyboard.json + film.json, and
runtime/film-layer.js draws plate, mask, vignette, kicker, progress bar and camera push from it.
Run: python3 -m pytest tests/test_film_layer.py -q   (from the skill directory). The browser tests skip without
python playwright + Chromium + ffmpeg."""
import json
import os
import sys
import re
import shutil

import pytest

from mv_fixtures import SKILL, load_sb, need_browser, save_sb, scene, storyboard, write_voice

import check  # noqa: E402
import film  # noqa: E402
import render  # noqa: E402

STYLES = os.path.join(SKILL, "references", "styles")


def film_project(tmp_path, cfg=None):
    p = str(tmp_path / "film")
    os.makedirs(os.path.join(p, "scenes"))
    sb = storyboard([
        scene("S01", "Opening", "", 3.0, shot="title-card"),
        scene("S02", "01 Costs", "", 2.0, shot="chapter-card"),
        scene("S03", "Cloud bill", "the cloud bill fell", 4.01),
        scene("S04", "Growth", "", 2.0, shot="chapter-card"),
        scene("S05", "Revenue", "revenue rose", 3.0),
        scene("S06", "Thanks", "", 2.5, shot="logo-endcard"),
    ], fps=30, width=1920, height=1080)
    save_sb(p, sb)
    json.dump(cfg if cfg is not None else {"kicker": True, "progress": True}, open(os.path.join(p, "film.json"), "w"))
    return p


def test_film_data_timing_chapters_and_kickers(tmp_path):
    p = film_project(tmp_path)
    assert film.main(["--project", p]) == 0
    d = film.read_data(p)
    f, sc = d["film"], d["scenes"]
    assert f["total"] == 16.5                       # 3 + 2 + 4.0 (4.01 s = 120 frames) + 2 + 3 + 2.5
    assert f["starts"] == {"S01": 0.0, "S02": 3.0, "S03": 5.0, "S04": 9.0, "S05": 11.0, "S06": 14.0}
    assert f["marks"] == [3.0, 9.0]
    assert sc["S03"]["kicker"] == {"no": "01", "name": "Costs", "section": "Cloud bill"}
    assert sc["S05"]["kicker"] == {"no": "02", "name": "Growth", "section": "Revenue"}   # no number in the title: ordinal
    assert sc["S01"]["kicker"] is None and sc["S02"]["kicker"] is None and sc["S06"]["kicker"] is None
    assert sc["S02"]["section"] == "Cloud bill" and sc["S02"]["chapter"] == {"no": "01", "name": "Costs"}
    assert sc["S03"]["next"] == "Growth" and sc["S06"]["next"] is None
    assert sc["S03"]["progress"] == {"from": round(5 / 16.5, 6), "to": round(9 / 16.5, 6),
                                     "marks": [round(3 / 16.5, 6), round(9 / 16.5, 6)]}
    assert sc["S02"]["progress"]["to"] == sc["S03"]["progress"]["from"]     # neighbours meet
    assert sc["S01"]["progress"] is None and sc["S06"]["progress"] is None   # title and end cards
    assert sc["S03"]["duration"] == 4.0
    lay = d["layer"]
    assert lay["style"] == "exec-brief" and lay["classes"]["kicker"] == "eb-kicker"
    assert lay["tokens"]["accent"] == "--eb-accent"
    assert open(os.path.join(p, "scenes", "film-data.js"), encoding="utf-8").read().startswith("/* scenes/film-data.js")


def test_chapter_number_only_when_clearly_marked():
    assert film.chapter_of("01 拐点", 5) == {"no": "01", "name": "拐点"}
    assert film.chapter_of("2. Growth", 5) == {"no": "2", "name": "Growth"}
    assert film.chapter_of("IV: Results", 4) == {"no": "IV", "name": "Results"}
    assert film.chapter_of("3 lessons we learned", 4) == {"no": "04", "name": "3 lessons we learned"}
    assert film.chapter_of("CI pipelines", 2) == {"no": "02", "name": "CI pipelines"}
    assert film.chapter_of("I see", 1) == {"no": "01", "name": "I see"}
    assert film.chapter_of("3、拐点", 1) == {"no": "3", "name": "拐点"}
    assert film.chapter_of("01 - Intro", 1) == {"no": "01", "name": "Intro"}
    assert film.chapter_of("01 — 拐点", 1) == {"no": "01", "name": "拐点"}
    assert film.chapter_of("01 -Intro", 1) == {"no": "01", "name": "Intro"}
    assert film.chapter_of("01 -5 degrees", 1) == {"no": "01", "name": "-5 degrees"}   # a sign stays with the number
    assert film.chapter_of("01 .5 percent", 1) == {"no": "01", "name": ".5 percent"}
    assert film.chapter_of("2 - Growth", 2) == {"no": "2", "name": "Growth"}
    assert film.chapter_of("IV : Results", 4) == {"no": "IV", "name": "Results"}
    for title in ("3.5 billion users", "2.0 launch", "10:30 standup", "CI-pipelines", "V-Day", "01.5 percent"):
        assert film.chapter_of(title, 7) == {"no": "07", "name": title}, title
    for title, k in (("C. elegans study", 3), ("V. cholerae", 2), ("IV: Results", 2)):   # not this chapter's numeral
        assert film.chapter_of(title, k) == {"no": f"{k:02d}", "name": title}, title


def test_setup_hands_out_elements_never_the_film_entry():
    """FL.setup returns the elements it made; the scene's entry is read only through FL.scene, which derive.py
    detects in the page (a returned entry would let a page depend on its neighbours unseen)."""
    js = open(os.path.join(SKILL, "runtime", "film-layer.js"), encoding="utf-8").read()
    body = js[js.index("function setup(tl, o)"):js.index("window.FL = {")]
    assert not re.search(r"\bH\s*(?:\.\s*scene|\[\s*[\"']scene)", body)
    assert re.search(r"return H;", body)


def test_plate_image_symlinked_outside_the_project_is_refused(tmp_path):
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"private")
    p = film_project(tmp_path, {"plate": {"image": "assets/plate.jpg"}})
    os.makedirs(os.path.join(p, "assets"))
    try:
        os.symlink(str(outside), os.path.join(p, "assets", "plate.jpg"))
    except (OSError, NotImplementedError):
        pytest.skip("cannot make symlinks here")
    assert film.main(["--project", p]) == 2


def test_duration_change_updates_progress_after_rerun(tmp_path, capsys):
    p = film_project(tmp_path)
    assert film.main(["--project", p]) == 0
    assert film.main(["--project", p, "--check"]) == 0
    sb = load_sb(p)
    sb["scenes"][2]["duration"] = 5.0          # tts.py rewrote S03
    save_sb(p, sb)
    capsys.readouterr()
    assert film.main(["--project", p, "--check"]) == 1
    assert "film.py" in capsys.readouterr().err
    assert film.main(["--project", p]) == 0
    d = film.read_data(p)
    assert d["film"]["total"] == 17.5 and d["film"]["starts"]["S04"] == 10.0 and d["film"]["marks"] == [3.0, 10.0]
    assert d["scenes"]["S05"]["progress"]["from"] == round(12 / 17.5, 6)
    assert d["scenes"]["S03"]["duration"] == 5.0
    assert film.main(["--project", p, "--check"]) == 0
    os.remove(os.path.join(p, "scenes", "film-data.js"))
    assert film.main(["--project", p, "--check"]) == 1


def test_bad_inputs_exit_2_and_name_the_problem(tmp_path, capsys):
    p = film_project(tmp_path)
    sb = load_sb(p)
    sb["scenes"][2]["duration"] = None
    save_sb(p, sb)
    assert film.main(["--project", p]) == 2
    assert "S03" in capsys.readouterr().err
    p2 = film_project(tmp_path / "b", {"kickr": True})
    assert film.main(["--project", p2]) == 2
    assert "kickr" in capsys.readouterr().err
    p3 = film_project(tmp_path / "c", {"tokens": {"accent": "#ff0000"}})
    assert film.main(["--project", p3]) == 2              # the layer never brings its own colours
    p4 = film_project(tmp_path / "d", {"plate": {"image": "../outside.jpg"}})
    assert film.main(["--project", p4]) == 2
    p5 = film_project(tmp_path / "e", {"plate": {"image": "assets/missing.jpg"}})
    assert film.main(["--project", p5]) == 2
    assert not os.path.exists(os.path.join(p, "scenes", "film-data.js"))
    assert film.main(["--project", str(tmp_path / "nowhere")]) == 2


def test_plate_images_per_scene_carry_their_hash(tmp_path):
    p = film_project(tmp_path, {"plate": {"image": "assets/a.jpg", "images": {"S03": "assets/b.jpg"}}})
    os.makedirs(os.path.join(p, "assets"))
    open(os.path.join(p, "assets", "a.jpg"), "wb").write(b"a")
    open(os.path.join(p, "assets", "b.jpg"), "wb").write(b"b")
    film.write(p)
    sc = film.read_data(p)["scenes"]
    assert sc["S03"]["plate_image"]["src"] == "/project/assets/b.jpg"
    assert sc["S05"]["plate_image"]["src"] == "/project/assets/a.jpg"
    before = sc["S03"]["plate_image"]["sha"]
    open(os.path.join(p, "assets", "b.jpg"), "wb").write(b"b2")       # a new photo changes the data (and the fingerprint)
    assert not film.is_current(p)
    film.write(p)
    assert film.read_data(p)["scenes"]["S03"]["plate_image"]["sha"] != before


def test_scene_slice_holds_only_what_decides_the_picture(tmp_path):
    p = film_project(tmp_path, {"kicker": True, "progress": False})
    a = film.build_data(p)
    sb = load_sb(p)
    sb["scenes"][4]["duration"] = 6.0          # a later scene got longer: the film total changes
    save_sb(p, sb)
    b = film.build_data(p)
    assert a["film"]["total"] != b["film"]["total"]
    assert film.scene_slice(a, "S03") == film.scene_slice(b, "S03")          # no progress bar: S03 unchanged
    sb["scenes"][3]["title"] = "Growth plan"   # the next scene's title changes only scenes that read FL.scene
    save_sb(p, sb)
    c = film.build_data(p)
    assert film.scene_slice(a, "S03", reads_entry=False) == film.scene_slice(c, "S03", reads_entry=False)
    assert film.scene_slice(a, "S03") != film.scene_slice(c, "S03")
    p2 = film_project(tmp_path / "prog", {"progress": True})
    d1 = film.build_data(p2)
    sb2 = load_sb(p2)
    sb2["scenes"][4]["duration"] = 6.0
    save_sb(p2, sb2)
    assert film.scene_slice(d1, "S03", False) != film.scene_slice(film.build_data(p2), "S03", False)   # bar moved


def css_defines(css_text):
    return set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", css_text))


def test_style_map_names_only_existing_classes_and_tokens():
    table = json.load(open(os.path.join(SKILL, "runtime", "film-styles.json"), encoding="utf-8"))["styles"]
    index = open(os.path.join(STYLES, "INDEX.md"), encoding="utf-8").read()
    ported = re.findall(r"^\| ([a-z0-9-]+) \|.*\| yes \|$", index, re.M)
    assert ported and sorted(ported) == sorted(table), "every ported style has a film-layer entry, and only those"
    for style, entry in table.items():
        css = open(os.path.join(STYLES, f"{style}.css"), encoding="utf-8").read()
        defined = css_defines(css)
        assert set(entry["tokens"]) <= set(film.TOKENS)
        for k, var in entry["tokens"].items():
            assert var in defined, f"{style}: token {k} -> {var} is not defined in {style}.css"
        for part, cls in entry["classes"].items():
            assert part in film.PARTS
            for side in film.MASKS:
                for c in cls.replace("{mask}", side).split():
                    assert re.search(r"\." + re.escape(c) + r"\b", css), f"{style}: class .{c} not in {style}.css"


def layer_css():
    js = open(os.path.join(SKILL, "runtime", "film-layer.js"), encoding="utf-8").read()
    block = js[js.index("var CSS = ["):js.index('].join("\\n")')]
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', block))


def test_layer_css_brings_no_colour_or_font_of_its_own():
    css = layer_css()
    assert ":where(.fl-kicker)" in css and ":where(.fl-progress-fill)" in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "no literal hex colours"
    # rgba() only as the fallback of a --fl-* variable (a neutral black shade, never a palette colour)
    for m in re.finditer(r"rgba?\(", css):
        assert re.search(r"var\(--fl-[a-z-]+,\s*$", css[:m.start()]), css[max(0, m.start() - 40):m.start() + 20]
        assert re.match(r"rgba\(0,0,0,", css[m.start():]), "only neutral black shades as fallbacks"
    for m in re.finditer(r"(?:^|[;{])\s*(color|background|font-family)\s*:\s*([^;}]+)", css):
        assert m.group(2).strip().startswith(("var(--fl-", "linear-gradient", "radial-gradient")), m.group(0)
    assert not re.search(r"font\s*:", css), "no font shorthand: the font comes from --fl-font (the style's token)"


def test_layer_and_template_pass_the_static_scene_rules(tmp_path):
    rt = os.path.join(SKILL, "runtime", "film-layer.js")
    assert [x for x in check.scan_file(rt, "runtime/film-layer.js", False) if x["level"] == "fail"] == []
    tp = os.path.join(SKILL, "templates", "scene.html")
    items = check.scan_file(tp, "templates/scene.html", True)
    assert [x for x in items if x["level"] == "fail"] == []
    page = open(tp, encoding="utf-8").read()
    assert "/skill/runtime/film-layer.js" in page and "/project/scenes/film-data.js" in page
    assert page.index("gsap.timeline(") < page.index("FL.setup(tl") < page.index("tl.fromTo(\"#title\"")


# ---------------------------------------------------------------- in the browser

TEMPLATE_NARRATION = "Cloud bill down forty percent: rightsizing, spot capacity, storage tiers, in one quarter."


def template_project(tmp_path):
    p = str(tmp_path / "fresh")
    os.makedirs(os.path.join(p, "scenes"))
    shutil.copyfile(os.path.join(SKILL, "templates", "scene.html"), os.path.join(p, "scenes", "S02.html"))
    sb = storyboard([scene("S01", "01 Costs", "", 2.0, shot="chapter-card"),
                     scene("S02", "Cloud bill", TEMPLATE_NARRATION, 7.0)], fps=30, width=1920, height=1080)
    save_sb(p, sb)
    write_voice(p, "S02", TEMPLATE_NARRATION.replace(",", "").replace(":", "").replace(".", ""))
    json.dump({"kicker": True, "progress": True, "vignette": True}, open(os.path.join(p, "film.json"), "w"))
    return p


def test_fresh_project_from_template_passes_check_and_preview(tmp_path):
    need_browser()
    p = template_project(tmp_path)
    assert film.main(["--project", p]) == 0
    assert check.main(["--project", p, "--scene", "S02"]) == 0
    rep = json.load(open(os.path.join(p, "review", "check-S02.json"), encoding="utf-8"))
    assert rep["ok"] and rep["fails"] == 0
    assert render.main(["--project", p, "--scene", "S02", "--preview"]) == 0
    for tag in "abc":
        assert os.path.getsize(os.path.join(p, "review", f"S02-{tag}.jpg")) > 1000
    # the layer drew its parts and the progress fill runs from this scene's start to its end
    res = render.preview_scenes(p, "S02", probe=True, log=lambda m: None)["S02"]
    tw = [t for t in res["tweens"] if t["target"].startswith("div.fl-progress-fill")]
    assert len(tw) == 1 and tw[0]["start"] == 0 and abs(tw[0]["duration"] - 7.0) < 0.05


def test_stale_film_data_fails_check(tmp_path):
    need_browser()
    p = template_project(tmp_path)
    assert film.main(["--project", p]) == 0
    sb = load_sb(p)
    sb["scenes"][1]["duration"] = 8.0          # tts.py rewrote the duration; film.py was not re-run
    save_sb(p, sb)
    assert check.main(["--project", p, "--scene", "S02"]) == 1
    rep = open(os.path.join(p, "review", "check-S02.json"), encoding="utf-8").read()
    assert "film-data.js is stale" in rep and "film.py" in rep
    assert film.main(["--project", p]) == 0
    assert check.main(["--project", p, "--scene", "S02"]) == 0



def test_film_layer_hold_numbers_match_check_py():
    """FL.at's key text hold uses the numbers check.py fails a scene on (project-contract.md, key text hold)"""
    import re
    sys.path.insert(0, os.path.join(SKILL, "scripts"))
    import check
    js = open(os.path.join(SKILL, "runtime", "film-layer.js"), encoding="utf-8").read()
    got = {k: float(v) for k, v in re.findall(r"(HOLD_CJK_PER_S|HOLD_OTHER_PER_S|HOLD_BASE_S) = ([0-9.]+)", js)}
    assert got == {"HOLD_CJK_PER_S": check.HOLD_CJK_PER_S, "HOLD_OTHER_PER_S": check.HOLD_OTHER_PER_S,
                   "HOLD_BASE_S": check.HOLD_BASE_S}


def test_template_keeps_late_stat_text_readable(tmp_path):
    """the word that reveals the stat is spoken 0.8 s before the end: FL.at with the stat's text pulls it early
    enough for check.py's key text hold; the same page without the text argument fails"""
    need_browser()
    p = template_project(tmp_path)
    wpath = os.path.join(p, "audio", "voice", "S02.words.json")
    words = json.load(open(wpath, encoding="utf-8"))
    for w in words:                       # only the stat's word moves late; the scene keeps its 7 s
        if w["text"].lower().startswith("quarter"):
            w["start"], w["end"] = 6.2, 6.6
    json.dump(words, open(wpath, "w"))
    assert film.main(["--project", p]) == 0
    assert check.main(["--project", p, "--scene", "S02"]) == 0
    page = os.path.join(p, "scenes", "S02.html")
    html = open(page, encoding="utf-8").read()
    assert 'FL.at("quarter", 0.84, "90 days")' in html
    open(page, "w", encoding="utf-8").write(html.replace('FL.at("quarter", 0.84, "90 days")', 'FL.at("quarter", 0.84)'))
    assert check.main(["--project", p, "--scene", "S02"]) == 1
    assert "key text" in open(os.path.join(p, "review", "check-S02.json"), encoding="utf-8").read()
