"""Tests for scripts/craft_checks.py (Pollinate v2 craft checks).

* Fixture tests: tests/fixtures/craft/good.json has no findings; each
  bad_<check>.json changes good.json in one way and must trigger exactly its
  own check (design section 11, item 3). bad_reference.json is compared with
  good.json as the reference.
* Config, canvas scaling, reference parts and the command line exit codes.
* A Playwright test runs PROBE_JS on tests/fixtures/craft/probe.html and checks
  the result against the section 13.3 shape. It is skipped when Playwright or
  its Chromium is not installed.
"""
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "scripts" / "craft_checks.py"
FIX = Path(__file__).resolve().parent / "fixtures" / "craft"

sys.path.insert(0, str(SCRIPT.parent))
import craft_checks as cc  # noqa: E402

BAD = ["font_floor", "fill", "align", "nowrap", "density",
       "overflow", "overlap", "safe_area", "image", "reference"]


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def checks_of(findings):
    return {f["check"] for f in findings}


# ---------------------------------------------------------------- fixtures

def test_good_fixture_has_no_findings():
    good = load("good.json")
    assert cc.evaluate(good, {"w": 1920, "h": 1080}) == []
    assert cc.evaluate(good, {"w": 1920, "h": 1080}, reference=good) == []


def test_every_check_has_a_bad_fixture():
    assert set(BAD) == set(cc.CHECKS)
    for check in BAD:
        assert (FIX / f"bad_{check}.json").is_file(), check


@pytest.mark.parametrize("check", BAD)
def test_bad_fixture_triggers_only_its_check(check):
    measure = load(f"bad_{check}.json")
    reference = load("good.json") if check == "reference" else None
    findings = cc.evaluate(measure, {"w": 1920, "h": 1080}, reference=reference)
    assert findings, f"bad_{check}.json gave no findings"
    assert checks_of(findings) == {check}, findings
    for f in findings:
        assert set(f) == {"slide", "check", "level", "msg", "where"}
        assert f["slide"] == measure["slide"]
        assert f["level"] == "fail"
        assert f["msg"] and isinstance(f["where"], str)


def test_measure_list_and_slides_wrapper():
    slides = [load("good.json"), load("bad_overlap.json"), load("bad_fill.json")]
    as_list = cc.evaluate(slides, None)
    as_obj = cc.evaluate({"slides": slides}, None)
    assert as_list == as_obj
    assert checks_of(as_list) == {"overlap", "fill"}
    assert {f["slide"] for f in as_list} == {slides[1]["slide"], slides[2]["slide"]}


# ------------------------------------------------------------ thresholds

def test_config_overrides_and_unknown_key():
    dense = load("bad_density.json")
    assert cc.evaluate(dense, None, {"density_cap": 5000}) == []
    warn = cc.evaluate(dense, None, {"levels": {"density": "warn"}})
    assert [f["level"] for f in warn] == ["warn"]
    with pytest.raises(ValueError, match="unknown config key: density_max"):
        cc.evaluate(dense, None, {"density_max": 10})
    with pytest.raises(ValueError, match="levels.fill"):
        cc.evaluate(dense, None, {"levels": {"fill": "block"}})
    with pytest.raises(ValueError, match="fill_min must be a number"):
        cc.evaluate(dense, None, {"fill_min": "a lot"})
    with pytest.raises(ValueError, match="font_canvas_ratio.body must be a number"):
        cc.evaluate(dense, None, {"font_canvas_ratio": {"body": None}})
    # DEFAULTS is never changed by an override
    assert cc.DEFAULTS["density_cap"] == 400


def test_font_floor_scales_with_canvas():
    good = load("good.json")
    # The same 30 px body text is fine on 1920x1080 but too small on a 4K canvas.
    assert cc.evaluate(good, {"w": 1920, "h": 1080}) == []
    big = copy.deepcopy(good)
    big["w"], big["h"] = 3840, 2160
    floors = [f for f in cc.evaluate(big, None) if f["check"] == "font_floor"]
    # title/subtitle/body floor 0.022 x 2160 = 47.5 px, label 0.0167 x 2160 = 36.1 px:
    # every text of good.json but the 72 px title is below its 4K floor
    assert sorted(f["where"] for f in floors) == sorted(
        t["text"] for t in good["texts"] if t["role"] != "title")
    assert all("47.5px" in f["msg"] or "36.1px" in f["msg"] for f in floors)
    # The floor also follows the largest title on the slide.
    huge_title = copy.deepcopy(good)
    huge_title["texts"][0]["font_px"] = 200
    hits = [f for f in cc.evaluate(huge_title, None) if f["check"] == "font_floor"]
    assert hits and all("largest title" in f["msg"] for f in hits)


def test_subtitle_and_title_have_a_font_floor():
    m = load("good.json")
    m["texts"][1]["font_px"] = 14   # the subtitle
    hits = cc.evaluate(m, None)
    assert checks_of(hits) == {"font_floor"}
    assert [f["where"] for f in hits] == ["Revenue grew in every region"]
    m = load("good.json")
    m["texts"][0]["font_px"] = 20   # a title below the canvas floor
    assert [f["where"] for f in cc.evaluate(m, None)
            if f["check"] == "font_floor"][:1] == ["Quarterly results"]


def cover_slide():
    """A centred cover: a title and a subtitle, nothing else."""
    return {"slide": "s-c0e1", "w": 1920, "h": 1080, "layout": "cover",
            "texts": [{"text": "Pollinate v2", "role": "title", "font_px": 96, "lines": 1,
                       "nowrap": False, "box": [660, 420, 600, 112],
                       "container": [0, 0, 1920, 1080], "chars": 11},
                      {"text": "A content engine", "role": "subtitle", "font_px": 44,
                       "lines": 1, "nowrap": False, "box": [760, 560, 400, 52],
                       "container": [0, 0, 1920, 1080], "chars": 14}],
            "blocks": [], "images": []}


def test_cover_slide_is_not_too_empty():
    assert cc.evaluate(cover_slide(), None) == []
    # the same slide without the declaration is judged as a content slide
    undeclared = dict(cover_slide(), layout=None)
    hits = cc.evaluate(undeclared, None)
    assert checks_of(hits) == {"fill"} and "data-layout" in hits[0]["msg"]
    # compared with a deck that has only content slides, a cover gets no
    # type-scale or margin drift: it has no counterpart there
    assert cc.evaluate(cover_slide(), None, reference=load("good.json")) == []
    # but compared with another cover, its drift is reported
    ref = cover_slide()
    ref["texts"][0]["font_px"] = 140
    drift = cc.evaluate(cover_slide(), None, reference=ref)
    assert checks_of(drift) == {"reference"} and drift[0]["where"] == "title"
    # a slide with nothing on it is still too empty
    empty = dict(cover_slide(), texts=[])
    assert checks_of(cc.evaluate(empty, None)) == {"fill"}


def test_sparse_content_slides_still_fail_fill():
    """No body text is not a cover: these slides were not declared sparse."""
    corner = {"slide": "s-c0e2", "w": 1920, "h": 1080,
              "texts": [{"text": "Results", "role": "title", "font_px": 48, "lines": 1,
                         "nowrap": False, "box": [100, 60, 300, 56],
                         "container": [0, 0, 1920, 1080], "chars": 7},
                        {"text": "Up 4 percent", "role": "subtitle", "font_px": 28, "lines": 1,
                         "nowrap": False, "box": [100, 130, 200, 34],
                         "container": [0, 0, 1920, 1080], "chars": 10}],
              "blocks": [], "images": []}
    assert checks_of(cc.evaluate(corner, None)) == {"fill"}
    chart = copy.deepcopy(corner)
    chart["texts"][1].update(role="caption", font_px=24, box=[100, 400, 300, 30])
    chart["images"] = [{"src": "chart.png", "box": [100, 180, 300, 180], "loaded": True}]
    assert checks_of(cc.evaluate(chart, None)) == {"fill"}
    # a layout the config does not list is not sparse
    assert checks_of(cc.evaluate(dict(corner, layout="two-column"), None)) == {"fill"}
    # a chart slide (content image, no body text) is a content slide for the
    # reference: compared with good.json it gets the content profile's drift
    ref_hits = [f for f in cc.evaluate(chart, None, reference=load("good.json"))
                if f["check"] == "reference"]
    assert {f["where"] for f in ref_hits} >= {"title", "side margin"}
    assert cc.evaluate(dict(corner, layout="Section"), None) == []
    assert cc.evaluate(dict(corner, layout="chapter"), None) == []   # a design-system name
    # config names are compared without case too
    assert cc.evaluate(dict(corner, layout="splash"), None, {"sparse_layouts": ["Splash"]}) == []


def test_empty_measure_is_an_error(tmp_path):
    for measure in ([], {"slides": []}):
        with pytest.raises(ValueError, match="no slides"):
            cc.evaluate(measure, None)
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"slides": []}))
    r = run_cli("--measure", str(empty))
    assert r.returncode == 2 and "no slides" in r.stderr


def test_bad_hex_color_is_skipped():
    m = load("good.json")
    m["colors"].append({"color": "#0x1234", "use": "text", "weight": 500})
    assert cc.evaluate(m, None, reference=load("good.json")) == []


def test_backdrop_image_is_ignored():
    good = load("good.json")
    # good.json already has a full-bleed backdrop; a declared one is ignored too.
    m = copy.deepcopy(good)
    m["images"].append({"src": "deco.png", "box": [100, 100, 900, 300], "loaded": True,
                        "role": "background"})
    assert cc.evaluate(m, None) == []


def test_align_ignores_deliberate_offsets():
    m = load("good.json")
    # 140 px or more off on every edge is a different layout, not a near miss.
    m["blocks"][3]["box"] = [460, 440, 600, 80]
    m["texts"][3]["box"] = [460, 440, 600, 80]
    assert "align" not in checks_of(cc.evaluate(m, None))


def test_reference_margin_and_palette_drift():
    good = load("good.json")
    shifted = copy.deepcopy(good)   # all content 50 px lower: the top margin drifts
    for item in shifted["texts"] + shifted["blocks"] + shifted["images"][:1]:
        item["box"][1] += 50
    margin = cc.evaluate(shifted, None, reference=good)
    assert checks_of(margin) == {"reference"}
    assert [f["where"] for f in margin] == ["top margin"]

    recolored = copy.deepcopy(good)
    recolored["colors"][0]["color"] = "#e5322d"
    palette = cc.evaluate(recolored, None, reference=good)
    assert checks_of(palette) == {"reference"}
    assert any("#e5322d" in f["msg"] for f in palette)


def test_invalid_measure_raises():
    with pytest.raises(ValueError):
        cc.evaluate({"slide": "s-1", "w": 1920, "h": 1080, "texts": [{"text": "x", "box": [1, 2]}]}, None)
    with pytest.raises(ValueError, match="canvas"):
        cc.evaluate({"slide": "s-1", "texts": []}, None)


# ------------------------------------------------------------ command line

def run_cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=60)


def test_cli_exit_codes():
    ok = run_cli("--measure", str(FIX / "good.json"))
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout) == {"ok": True, "fails": 0, "warns": 0, "findings": []}

    bad = run_cli("--measure", str(FIX / "bad_reference.json"),
                  "--reference", str(FIX / "good.json"))
    assert bad.returncode == 1, bad.stderr
    out = json.loads(bad.stdout)
    assert out["ok"] is False and out["fails"] == 1
    assert out["findings"][0]["check"] == "reference"


def test_cli_warn_only_exits_zero(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"levels": {"overlap": "warn"}}))
    r = run_cli("--measure", str(FIX / "bad_overlap.json"), "--config", str(cfg))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["warns"] == 1


def test_cli_usage_errors(tmp_path):
    missing = run_cli("--measure", str(tmp_path / "nope.json"))
    assert missing.returncode == 2 and "not found" in missing.stderr
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert run_cli("--measure", str(broken)).returncode == 2
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"no_such_key": 1}))
    r = run_cli("--measure", str(FIX / "good.json"), "--config", str(cfg))
    assert r.returncode == 2 and "unknown config key" in r.stderr
    assert run_cli("--measure", str(FIX / "good.json"), "--canvas", "big").returncode == 2
    wrong_type = tmp_path / "wrong.json"
    wrong_type.write_text(json.dumps({"slide": "s-1", "w": 1920, "h": 1080, "texts": 5}))
    r = run_cli("--measure", str(wrong_type))
    assert r.returncode == 2 and "invalid input" in r.stderr
    assert run_cli().returncode == 2  # argparse: --measure is required


# ------------------------------------------------------------ the probe

_HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


@pytest.fixture(scope="module")
def probe_page():
    if not _HAS_PLAYWRIGHT:
        pytest.skip("Playwright is not installed")
    from playwright.sync_api import sync_playwright
    try:
        manager = sync_playwright().start()
    except Exception as exc:  # the Playwright driver itself cannot start
        pytest.skip(f"Playwright does not start here: {str(exc).splitlines()[0]}")
    try:
        try:
            browser = manager.chromium.launch()
        except Exception as exc:  # Chromium or its system libraries missing
            pytest.skip(f"Chromium does not start here: {str(exc).splitlines()[0]}")
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto((FIX / "probe.html").as_uri())
        yield page
        browser.close()
    finally:
        manager.stop()


def assert_box(value):
    assert isinstance(value, list) and len(value) == 4
    assert all(isinstance(v, (int, float)) for v in value)


def assert_shape(m):
    """The section 13.3 measurement shape."""
    assert {"slide", "w", "h", "texts", "blocks", "images"} <= set(m)
    for t in m["texts"]:
        assert {"text", "role", "font_px", "lines", "nowrap", "box", "container", "chars"} <= set(t)
        assert t["role"] in {"title", "subtitle", "body", "label", "caption"}
        assert isinstance(t["text"], str) and t["text"]
        assert isinstance(t["font_px"], (int, float)) and t["font_px"] > 0
        assert isinstance(t["lines"], int) and t["lines"] >= 1
        assert isinstance(t["nowrap"], bool)
        assert isinstance(t["chars"], int) and t["chars"] > 0
        assert_box(t["box"])
        assert_box(t["container"])
    for b in m["blocks"]:
        assert set(b) >= {"box", "group"} and isinstance(b["group"], str)
        assert_box(b["box"])
    for im in m["images"]:
        assert {"src", "box", "loaded"} <= set(im) and isinstance(im["loaded"], bool)
        assert_box(im["box"])


def by_text(m, text):
    hits = [t for t in m["texts"] if t["text"] == text]
    assert len(hits) == 1, (text, [t["text"] for t in m["texts"]])
    return hits[0]


def test_probe_shape_and_roles(probe_page):
    m = probe_page.evaluate(cc.PROBE_JS, "s-1a2b")
    assert_shape(m)
    assert (m["slide"], m["w"], m["h"]) == ("s-1a2b", 1920, 1080)

    title = by_text(m, "Quarterly results")
    assert title["role"] == "title" and title["font_px"] == 72 and title["lines"] == 1
    assert abs(title["box"][0] - 160) < 2 and title["container"] == [0, 0, 1920, 1080]
    assert by_text(m, "Revenue grew in every region")["role"] == "subtitle"
    body = by_text(m, "Asia doubled its partner count this quarter.")  # inline <b> is merged
    assert body["role"] == "body" and body["font_px"] == 30
    assert by_text(m, "Europe led with strong demand for the new plan.")["role"] == "body"
    source = by_text(m, "Source: internal sales data")
    assert source["role"] == "label"              # data-role override
    keep = by_text(m, "Short line kept whole")
    assert keep["nowrap"] is True                 # data-nowrap override
    assert title["nowrap"] is False

    assert len(m["images"]) == 1
    img = m["images"][0]
    assert img["loaded"] is True and img["src"].startswith("data:image/svg+xml")
    assert [round(v) for v in img["box"]] == [1040, 330, 720, 540]
    groups = {b["group"] for b in m["blocks"]}
    assert {"root", "#content", "#left"} <= groups
    assert any(c["color"] == "#1a1a2e" and c["use"] == "text" for c in m["colors"])
    assert any(c["color"] == "#ffffff" and c["use"] == "fill" for c in m["colors"])

    # A well-made slide passes every check end to end.
    assert cc.evaluate(m, {"w": 1920, "h": 1080}) == []


def test_probe_scopes_to_slide_and_scale(probe_page):
    m1 = probe_page.evaluate(cc.PROBE_JS, "s-1a2b")
    m2 = probe_page.evaluate(cc.PROBE_JS, "s-3c4d")
    assert_shape(m2)
    texts1 = {t["text"] for t in m1["texts"]}
    texts2 = {t["text"] for t in m2["texts"]}
    assert not texts1 & texts2
    assert "Scaled title" not in texts1 | texts2

    wrapped = by_text(m2, "A title that is far too long for its narrow box")
    assert wrapped["role"] == "title" and wrapped["lines"] > 1
    assert by_text(m2, "A heading the author marks as body")["role"] == "body"  # h2 overridden
    assert by_text(m2, "Nowrap set in CSS")["nowrap"] is True
    assert by_text(m2, "Author turned nowrap off")["nowrap"] is False
    nowrap = [f for f in cc.evaluate(m2, None) if f["check"] == "nowrap"]
    assert [f["where"] for f in nowrap] == ["A title that is far too long for its narrow box"]

    # A slide scaled to half size by a deck stage is measured in canvas px.
    m3 = probe_page.evaluate(cc.PROBE_JS, "s-5e6f")
    assert (m3["w"], m3["h"]) == (1920, 1080)
    assert m3["layout"] == "cover" and m1["layout"] is None   # data-layout is read
    scaled = by_text(m3, "Scaled title")
    assert abs(scaled["box"][0] - 160) < 2 and scaled["font_px"] == 72

    with pytest.raises(Exception, match="no slide"):
        probe_page.evaluate(cc.PROBE_JS, "s-ffff")
