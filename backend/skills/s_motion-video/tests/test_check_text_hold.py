"""Tests for check.py's key-text hold checks: the exact scene-time check (runtime reveals from the registered
timeline) and the storyboard-time warning for late cue words.
Run: python3 -m pytest tests/test_check_text_hold.py -q   (from the skill directory)
The browser tests need python playwright with Chromium and skip without it (MV_REQUIRE_BROWSER=1: fail instead)."""
import json
import os
import sys

import pytest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import check  # noqa: E402
import render  # noqa: E402

KEY = "达标率 85%"          # 3 CJK + 2 other ('%' is punctuation): 3/4.5 + 2/15 + 1.5 = 2.30 s
KEY_NEED = 3 / 4.5 + 2 / 15 + 1.5


def collect():
    items = []
    return items, lambda level, where, msg: items.append({"level": level, "where": where, "msg": msg})


def reveal_tween(at, text=KEY, el="#key"):
    return {"start": at - 0.4, "duration": 0.4, "target": el, "count": 1, "reveals": [{"el": el, "text": text, "at": at}]}


# ---------- the rule's numbers

def test_required_hold_formula():
    need, cjk, other = check.required_hold(KEY)
    assert (cjk, other) == (3, 2) and need == pytest.approx(KEY_NEED)
    need, cjk, other = check.required_hold("Cloud bill -40%")
    assert (cjk, other) == (0, 11) and need == pytest.approx(11 / 15 + 1.5)
    assert check.required_hold("")[0] == pytest.approx(check.HOLD_BASE_S)
    assert (check.HOLD_CJK_PER_S, check.HOLD_OTHER_PER_S, check.HOLD_BASE_S, check.LAST_LAND_S, check.CUE_LAND_S) == \
        (4.5, 15.0, 1.5, 1.2, 0.5)


def test_contract_states_the_numbers():
    text = open(os.path.join(check.SKILL_DIR, "references", "project-contract.md"), encoding="utf-8").read()
    bullet = text[text.index("  - key text hold"):]
    rule = bullet[:bullet.find("\n  - ", 1) if "\n  - " in bullet else len(bullet)]
    for n in ("4.5", "/ 15", "+ 1.5", "1.2 s", "0.5 s"):
        assert n in rule


# ---------- scene time (synthetic runtime records)

def test_late_text_block_fails_with_hold_numbers():
    items, add = collect()
    check.text_hold_checks("S04", [reveal_tween(6.7)], 7.2, add)
    assert len(items) == 1 and items[0]["level"] == "fail" and items[0]["where"] == "S04"
    msg = items[0]["msg"]
    assert "'达标率 85%'" in msg and "6.70s" in msg and "holds 0.50s" in msg and "(7.20s)" in msg
    assert f"needs {KEY_NEED:.2f}s" in msg and "3 CJK / 4.5 + 2 other / 15 + 1.5 s" in msg
    assert "last 1.2 s" in msg


def test_early_text_block_passes():
    items, add = collect()
    check.text_hold_checks("S04", [reveal_tween(7.2 - KEY_NEED - 0.01)], 7.2, add)
    assert items == []


def test_short_hold_outside_last_window_fails_without_late_landing():
    items, add = collect()
    check.text_hold_checks("S04", [reveal_tween(5.6)], 7.2, add)   # hold 1.6 < 2.30, lands before the last 1.2 s
    assert len(items) == 1 and "holds 1.60s" in items[0]["msg"] and "last 1.2 s" not in items[0]["msg"]


def test_long_text_is_truncated_and_records_without_reveals_are_ignored():
    items, add = collect()
    long = "A very long key statement that keeps going on and on"
    check.text_hold_checks("S01", [{"start": 0, "duration": 4, "target": "#camera", "count": 1},
                                   reveal_tween(3.0, long, "#t")], 4.0, add)
    assert len(items) == 1 and "'A very long key stateme…'" in items[0]["msg"] and "(#t)" in items[0]["msg"]


def test_still_checks_runs_the_hold_check_and_main_exits_1(tmp_path, monkeypatch):
    proj = make_project(tmp_path, scene_html(3.1))
    fake = {"S01": {"tweens": [reveal_tween(3.5)], "errors": [], "missing": [], "blocked": [], "console": [], "stills": []}}
    monkeypatch.setattr(check.R, "preview_scenes", lambda *a, **k: fake)
    assert check.main(["--project", str(proj), "--scene", "S01"]) == 1
    rep = json.load(open(proj / "review" / "check-S01.json", encoding="utf-8"))
    assert any("key text" in x["msg"] and "holds 0.50s" in x["msg"] for x in rep["stills"])


# ---------- storyboard time

def words(pairs):
    return [{"text": t, "start": s, "end": round(s + 0.3, 3)} for t, s in pairs]


def make_storyboard(tmp_path, visual, word_list, duration=6.0, scenes_html=False):
    proj = tmp_path / "p"
    (proj / "audio" / "voice").mkdir(parents=True)
    sb = base_storyboard(duration, visual, captions=True)
    (proj / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    if word_list is not None:
        (proj / "audio" / "voice" / "S01.words.json").write_text(json.dumps(word_list, ensure_ascii=False), encoding="utf-8")
    return proj


def test_parse_cues_handles_quotes_separators_and_apostrophes():
    assert check.parse_cues("big '85' + '达标' <- 85") == [(["85", "达标"], "85")]
    assert check.parse_cues("title 'Cloud bill' <- cloud bill; bar grows <- \"doubled\"") == \
        [(["Cloud bill"], "cloud bill"), ([], "doubled")]
    assert check.parse_cues("the bar's label 'Q3' <- quarter then fades") == [(["Q3"], "quarter")]
    assert check.parse_cues("'A' <- alpha 'B' <- beta") == [(["A"], "alpha"), (["B"], "beta")]
    assert check.parse_cues("标题「向量数据库」<- 向量；数字 “3 倍” <- 三倍") == [(["向量数据库"], "向量"), (["3 倍"], "三倍")]
    assert check.parse_cues("'x' <- ") == [(["x"], "")]
    assert check.parse_cues("no cue here") == []


def test_storyboard_late_cue_warns(tmp_path, capsys):
    w = words([("今天", 0.2), ("我们", 0.8), ("看", 1.4), ("达标", 3.6), ("情况", 5.0)])
    proj = make_storyboard(tmp_path, "big '85' + '达标' <- 达标; heading '今天' <- 今天", w, duration=6.0)
    assert check.main(["--project", str(proj), "--storyboard"]) == 0          # warnings only
    rep = json.load(open(proj / "review" / "check-storyboard.json", encoding="utf-8"))
    warns = [x for x in rep["storyboard"] if x["level"] == "warn" and "cue '达标'" in x["msg"]]
    need = check.required_hold("85 达标")[0]
    assert len(warns) == 1 and warns[0]["where"] == "S01"
    # spoken at 3.6 s: 2.4 s would be left from the word, but the label lands about 4.1 s and holds 1.9 s < 2.08 s
    msg = warns[0]["msg"]
    assert "spoken at 3.60s" in msg and "lands about 4.10s" in msg and "holds 1.90s" in msg and f"needs {need:.2f}s" in msg
    assert "last 1.2 s" not in msg
    assert not any("cue '今天'" in x["msg"] for x in rep["storyboard"])        # early cue: no warning
    assert rep["cues"]["checked"] == 2 and rep["cues"]["late"] == 1
    assert "WARN" in capsys.readouterr().out


def test_storyboard_unparsed_cues_are_counted(tmp_path, capsys):
    w = words([("hello", 0.2), ("world", 0.8)])
    proj = make_storyboard(tmp_path, "bar grows <- world; 'Title' <- ; 'X' <- never; 'Hello' <- hello", w)
    assert check.main(["--project", str(proj), "--storyboard"]) == 0
    rep = json.load(open(proj / "review" / "check-storyboard.json", encoding="utf-8"))
    assert rep["cues"] == {"checked": 1, "late": 0, "no_label": 1, "unparsed": 1, "not_spoken": 1, "no_timings": 0}
    out = capsys.readouterr().out
    assert "INFO" in out and "1 checked, 0 late; 3 skipped" in out


def test_storyboard_scene_without_word_timings_is_counted(tmp_path):
    proj = make_storyboard(tmp_path, "'Hello' <- hello", None)
    assert check.main(["--project", str(proj), "--storyboard"]) == 0
    rep = json.load(open(proj / "review" / "check-storyboard.json", encoding="utf-8"))
    assert rep["cues"]["no_timings"] == 1 and rep["cues"]["checked"] == 0


def test_storyboard_mode_skips_missing_scene_files(tmp_path):
    proj = make_storyboard(tmp_path, "'Hello' <- hello", words([("hello", 0.2)]))
    assert check.main(["--project", str(proj), "--storyboard"]) == 0
    rep = json.load(open(proj / "review" / "check-storyboard.json", encoding="utf-8"))
    assert not any("does not exist" in x["msg"] for x in rep["storyboard"])
    assert rep["static"] == [] and rep["stills"] == []
    assert check.main(["--project", str(proj), "--no-render"]) == 1          # the scene mode still requires the pages


# ---------- browser: the runtime measures reveals exactly from the timeline

DUR = 4.0


def base_storyboard(duration, visual="key text <- 达标", captions=True):
    return {"title": "Hold test", "lang": "zh-CN", "width": 1920, "height": 1080, "fps": 30, "style": "exec-brief",
            "method": "principle", "mode": "explainer", "voice": {"name": "zh-CN-YunxiNeural"}, "bgm": None,
            "captions": captions,
            "scenes": [{"id": "S01", "title": "测试", "narration": "", "visual": visual, "shot_type": "statement",
                        "duration": duration, "transition": "cut"}]}


def scene_html(key_at):
    """A scene with: a static heading (frame 0, emphasised at 3 s), a ghost text (ends at 0.3 opacity), a card whose container fades in
    with a counter inside, three staggered list items, and the key text fading in at key_at (lands key_at + 0.4)."""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<script src="/skill/runtime/gsap.min.js"></script>
<script src="/skill/runtime/scene-runtime.js"></script>
<style>
  body {{ background: #0f172a; color: #fff; font-family: sans-serif; }}
  #bg {{ position: absolute; left: 0; top: 0; width: 50%; height: 100%; background: linear-gradient(#334155, #e2e8f0); }}
  .t {{ position: absolute; font-size: 60px; white-space: nowrap; }}
</style></head><body>
<div id="stage"><div id="camera">
  <div id="bg"></div>
  <div class="t" id="static" style="left:1000px;top:120px">Static heading</div>
  <div class="t" id="ghost" style="left:1000px;top:220px">Ghost words</div>
  <div class="t" id="card" style="left:1000px;top:320px"><span id="num">0</span> days</div>
  <div class="t" style="left:1000px;top:420px"><span id="i1">Rightsizing</span> <span id="i2">Spot</span> <span id="i3">Tiers</span></div>
  <div class="t" id="key" style="left:1000px;top:560px">{KEY}</div>
</div></div>
<script>
  const tl = gsap.timeline({{ paused: true, defaults: {{ ease: "power3.out" }} }});
  tl.fromTo("#camera", {{ scale: 1 }}, {{ scale: 1.03, duration: MV.duration, ease: "none" }}, 0);
  tl.fromTo("#ghost", {{ opacity: 0 }}, {{ opacity: 0.3, duration: 0.5 }}, 0.5);
  tl.fromTo("#card", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.5 }}, 0.5);
  const c = {{ v: 0 }}, num = document.getElementById("num");
  tl.fromTo(c, {{ v: 0 }}, {{ v: 90, duration: 0.6, onUpdate: () => {{ num.textContent = Math.round(c.v); }} }}, 0.5);
  tl.fromTo(["#i1", "#i2", "#i3"], {{ autoAlpha: 0 }}, {{ autoAlpha: 1, duration: 0.5, stagger: 0.2 }}, 1.0);
  tl.fromTo("#key", {{ opacity: 0, y: 30 }}, {{ opacity: 1, y: 0, duration: 0.4 }}, {key_at});
  // an emphasis on text that is already readable is not a reveal
  tl.fromTo("#static", {{ opacity: 0.6 }}, {{ opacity: 1, duration: 0.4, immediateRender: false }}, 3.0);
  MV.register(tl);
</script></body></html>"""


def make_project(tmp_path, html, duration=DUR):
    proj = tmp_path / "film"
    (proj / "scenes").mkdir(parents=True)
    (proj / "audio" / "voice").mkdir(parents=True)
    (proj / "storyboard.json").write_text(json.dumps(base_storyboard(duration), ensure_ascii=False), encoding="utf-8")
    (proj / "audio" / "voice" / "S01.captions.json").write_text(
        json.dumps([{"text": "字幕不算关键文字", "start": 0.0, "end": duration}], ensure_ascii=False), encoding="utf-8")
    (proj / "scenes" / "S01.html").write_text(html, encoding="utf-8")
    return proj


def preview_or_skip(proj):
    """the scene's preview result; skips without playwright/Chromium unless MV_REQUIRE_BROWSER=1 (then it fails)"""
    required = os.environ.get("MV_REQUIRE_BROWSER") == "1"
    try:
        import playwright  # noqa: F401
        return render.preview_scenes(str(proj), ["S01"], 1, probe=True, log=lambda m: None)["S01"]
    except (ImportError, render.UsageError) as e:   # playwright or Chromium missing or not launchable here
        if required:
            pytest.fail(f"MV_REQUIRE_BROWSER=1 but the browser is unavailable: {e}")
        pytest.skip(str(e))


def test_browser_reveals_exclude_caption_ghost_and_static(tmp_path):
    res = preview_or_skip(make_project(tmp_path, scene_html(0.6)))
    assert res["errors"] == []
    got = {rv["el"]: rv for tw in res["tweens"] for rv in tw.get("reveals", [])}
    assert set(got) == {"#card", "#i1", "#i2", "#i3", "#key"}           # no #static, #ghost or caption
    assert {k: v["at"] for k, v in got.items()} == {"#card": 1.0, "#i1": 1.5, "#i2": 1.7, "#i3": 1.9, "#key": 1.0}
    assert got["#card"]["text"] == "90 days" and got["#key"]["text"] == KEY   # final counter text, no nested block
    assert all("字幕" not in v["text"] for v in got.values())


def test_browser_late_key_text_fails_with_hold_numbers(tmp_path, capsys):
    proj = make_project(tmp_path, scene_html(3.1))                         # lands at 3.5 s, 0.5 s before the end
    preview_or_skip(proj)
    assert check.main(["--project", str(proj), "--scene", "S01"]) == 1
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "key text" in ln)
    assert line.startswith("FAIL") and "'达标率 85%' (#key)" in line and "readable from 3.50s" in line
    assert "holds 0.50s" in line and f"needs {KEY_NEED:.2f}s" in line and "last 1.2 s" in line


def test_browser_early_key_text_passes(tmp_path, capsys):
    proj = make_project(tmp_path, scene_html(0.6))
    preview_or_skip(proj)
    assert check.main(["--project", str(proj), "--scene", "S01"]) == 0
    assert "key text" not in capsys.readouterr().out


def test_browser_ancestor_reveal_decides_when_text_is_readable(tmp_path):
    html = scene_html(0.6).replace('<div class="t" id="key"', '<div id="wrap"><div class="t" id="key"').replace(
        f"{KEY}</div>", f"{KEY}</div></div>").replace(
        "MV.register(tl);", 'tl.fromTo("#wrap", { opacity: 0 }, { opacity: 1, duration: 0.5 }, 2.0);\n  MV.register(tl);')
    res = preview_or_skip(make_project(tmp_path, html))
    got = {rv["el"]: rv for tw in res["tweens"] for rv in tw.get("reveals", [])}
    # #key's own fade (lands 1.0 s) happens while the wrapper is still hidden, so it reveals nothing; the wrapper's
    # fade (lands 2.5 s) is what makes the key text readable
    assert got["#wrap"]["at"] == 2.5 and got["#wrap"]["text"] == KEY and "#key" not in got


def test_browser_nested_block_text_is_not_counted_twice(tmp_path):
    html = scene_html(2.0).replace('<div class="t" id="key"', '<div id="wrap"><div class="t" style="left:1000px;top:660px">Label</div><div class="t" id="key"').replace(
        f"{KEY}</div>", f"{KEY}</div></div>").replace(
        "MV.register(tl);", 'tl.fromTo("#wrap", { opacity: 0 }, { opacity: 1, duration: 0.4 }, 0.2);\n  MV.register(tl);')
    res = preview_or_skip(make_project(tmp_path, html))
    got = {rv["el"]: rv for tw in res["tweens"] for rv in tw.get("reveals", [])}
    assert got["#wrap"]["at"] == 0.6 and got["#wrap"]["text"] == "Label"      # the key text is its own block
    assert got["#key"]["at"] == 2.4 and got["#key"]["text"] == KEY
