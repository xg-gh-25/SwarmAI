"""Unit tests for the audio scripts (tts.py, mix.py, bgm.py, sfx.py): pure functions, plus one small ffmpeg mix.
Run: python3 -m pytest tests/test_audio.py -q   (from the skill directory)"""
import os
import shutil
import subprocess
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import bgm  # noqa: E402
import mix  # noqa: E402
import sfx  # noqa: E402
import tts  # noqa: E402


def words_for(text, chunks, step=0.5):
    """fake word boundaries: consecutive chunks of text, `step` s each"""
    out, t = [], 0.1
    for c in chunks:
        out.append({"text": c, "start": round(t, 3), "end": round(t + step * 0.9, 3)})
        t += step
    spoken, s2o = tts.apply_pronounce(text, {})
    return tts.map_words(text, spoken, s2o, out)


# ---------- captions ----------

def test_caption_breaks_at_punctuation():
    text = "大家好，今天讲向量数据库。它很快！"
    w = words_for(text, ["大家", "好", "今天", "讲", "向量", "数据库", "它", "很快"])
    caps = tts.captions_from_words(text, w, 5.0, 16)
    assert [c["text"] for c in caps] == ["大家好", "今天讲向量数据库", "它很快"]


def test_caption_keeps_short_enumeration_on_one_line():
    text = "它声明类型、范围和去处。"
    w = words_for(text, ["它", "声明", "类型", "范围", "和", "去处"])
    caps = tts.captions_from_words(text, w, 4.0, 16)
    assert [c["text"] for c in caps] == ["它声明类型、范围和去处"]


def test_caption_breaks_long_line_at_enumeration_comma():
    text = "云原生数据库与向量检索引擎、范围。"
    w = words_for(text, ["云原生", "数据库", "与", "向量", "检索", "引擎", "范围"])
    caps = tts.captions_from_words(text, w, 4.0, 16)
    assert [c["text"] for c in caps] == ["云原生数据库与向量检索引擎", "范围"]


def test_caption_line_lasts_until_next_and_last_until_duration():
    text = "大家好，今天讲向量。"
    w = words_for(text, ["大家", "好", "今天", "讲", "向量"])
    caps = tts.captions_from_words(text, w, 3.3, 16)
    assert caps[0]["end"] == caps[1]["start"] == w[2]["start"]
    assert caps[-1]["end"] == 3.3


def test_caption_breaks_at_16_landscape_and_12_portrait():
    text = "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸子丑寅卯"   # 24 chars, no punctuation
    chunks = [text[i:i + 2] for i in range(0, 24, 2)]
    wide = tts.captions_from_words(text, words_for(text, chunks), 9.0, tts.max_caption_width(1920, 1080))
    tall = tts.captions_from_words(text, words_for(text, chunks), 9.0, tts.max_caption_width(1080, 1920))
    assert [len(c["text"]) for c in wide] == [16, 8]
    assert [len(c["text"]) for c in tall] == [12, 12]
    assert tts.max_caption_width(1080, 1080) == 16


def test_caption_width_counts_latin_as_half():
    assert tts.text_width("向量") == 2 and tts.text_width("abcd") == 2 and tts.text_width("SQL 查") == 3.0
    text = "Vector search turns every sentence into numbers"
    w = words_for(text, text.split())
    caps = tts.captions_from_words(text, w, 5.0, 12)   # 24 Latin characters per line
    assert all(tts.text_width(c["text"]) <= 12 for c in caps)
    assert " ".join(c["text"] for c in caps) == text


def test_caption_strips_edge_punctuation_and_quotes():
    text = "Type \"cheap flights\" now."
    caps = tts.captions_from_words(text, words_for(text, ["Type", "cheap", "flights", "now"]), 3.0, 16)
    assert caps == [{"text": 'Type "cheap flights" now', "start": 0.1, "end": 3.0}]


def test_strip_markup():
    s = tts.strip_markup("讲清楚 **向量数据库** 到底<b>是</b>什么 (laughs)<#0.5#>。处理/(chu3)(li3)  Hello  [docs](http://x)")
    assert s == "讲清楚向量数据库到底是什么。处理 Hello docs"


# ---------- duration rounding ----------

@pytest.mark.parametrize("voice,fps,expect", [
    (6.792, 30, 7.2),        # 7.192 -> 216 frames
    (3.0, 30, 3.4),          # exactly 102 frames: not bumped to 103
    (0.8, 30, 2.0),          # minimum scene
    (5.496, 30, 5.9),
    (3.72, 30, 4.133),       # 4.12 -> 124 frames = 4.1333
    (1.234, 25, 2.0),
    (4.01, 60, 4.417),       # 4.41 -> 265 frames
])
def test_scene_duration_rounds_up_to_whole_frame(voice, fps, expect):
    d = tts.scene_duration(voice, fps)
    assert d == expect
    assert d >= max(voice + 0.4, 2.0) - 1e-3
    assert abs(d * fps - round(d * fps)) < 0.05          # render's round(d*fps) gives the intended frame count
    assert round(d * fps) == int(np.ceil(max(voice + 0.4, 2.0) * fps - 1e-6))


# ---------- pronunciation mapping ----------

def test_apply_pronounce_longest_first_and_ascii_word_boundaries():
    spoken, s2o = tts.apply_pronounce("SQL 和 SQLite 不同", {"SQL": "S Q L", "SQ": "x"})
    assert spoken == "S Q L 和 SQLite 不同"
    assert len(s2o) == len(spoken) and s2o[0] == s2o[4] == (0, 3)
    assert s2o[len("S Q L ")] == (4, 5)


def test_pronounce_keeps_original_text_and_timings():
    text = "A GPU makes it fast."
    spoken, s2o = tts.apply_pronounce(text, {"GPU": "G P U"})
    bounds = [{"text": t, "start": s, "end": s + 0.2} for t, s in
              [("A", 0.1), ("G", 0.4), ("P", 0.6), ("U", 0.8), ("makes", 1.1), ("it", 1.4), ("fast", 1.6)]]
    w = tts.map_words(text, spoken, s2o, bounds)
    assert [x["text"] for x in w] == ["A", "GPU", "makes", "it", "fast"]
    gpu = w[1]
    assert gpu["start"] == 0.4 and gpu["end"] == pytest.approx(1.0)
    caps = tts.captions_from_words(text, w, 2.0, 16)
    assert caps[0]["text"] == "A GPU makes it fast"


def test_pronounce_replacement_with_different_word_count_zh():
    text = "用三分钟讲向量"
    spoken, s2o = tts.apply_pronounce(text, {"三分钟": "三分種"})
    w = tts.map_words(text, spoken, s2o, [{"text": "用", "start": 0.1, "end": 0.2}, {"text": "三分種", "start": 0.2, "end": 0.8},
                                         {"text": "讲", "start": 0.8, "end": 0.9}, {"text": "向量", "start": 0.9, "end": 1.3}])
    assert [x["text"] for x in w] == ["用", "三分钟", "讲", "向量"]


def test_locate_words_survives_normalized_tokens():
    spoken = "2026年增长了 30%"
    w = tts.locate_words(spoken, [{"text": "二零二六", "start": 0, "end": 1}, {"text": "年", "start": 1, "end": 1.2},
                                  {"text": "增长", "start": 1.2, "end": 1.5}])
    assert w[-1]["b"] == spoken.index("增长")


def test_sentence_fallback_splits_by_characters():
    out = tts.split_sentences([{"text": "大家好，今天讲向量。", "start": 0.0, "end": 8.0}])
    assert [x["text"] for x in out] == list("大家好今天讲向量")
    assert out[0]["start"] == 0.0 and out[-1]["end"] == 8.0
    assert all(abs((x["end"] - x["start"]) - 1.0) < 1e-6 for x in out)
    en = tts.split_sentences([{"text": "It is fast!", "start": 1.0, "end": 2.0}])
    assert [x["text"] for x in en] == ["It", "is", "fast"] and en[-1]["end"] == 2.0


def test_length_check():
    assert tts.length_check(21, 20)[0] == "ok"
    assert tts.length_check(22.5, 20)[0] == "warn"
    assert tts.length_check(24.5, 20)[0] == "fail"
    assert tts.length_check(30, None)[0] == "ok"


def test_hash_changes_with_voice_rate_and_pronunciation():
    h = tts.tts_hash("a", "a", "v", "+0%")
    assert h == tts.tts_hash("a", "a", "v", "+0%")
    assert len({h, tts.tts_hash("a", "b", "v", "+0%"), tts.tts_hash("a", "a", "w", "+0%"), tts.tts_hash("a", "a", "v", "+5%")}) == 4


# ---------- mix placement ----------

SCENES = [
    {"id": "S01", "narration": "x", "duration": 5.9, "sfx": [{"name": "whoosh", "at": 0.2}]},
    {"id": "S02", "narration": "y", "duration": 10.7, "sfx": [{"name": "pop", "at": 1.0}, {"name": "ding", "at": 3.5, "volume": 0.9}],
     "transition": "fade"},
    {"id": "S03", "narration": "", "duration": 4.133, "sfx": []},
]


def test_timeline_accumulates_frames_not_seconds():
    tl, total = mix.timeline(SCENES, 30)
    assert [(sid, n) for sid, _, n in tl] == [("S01", 177), ("S02", 321), ("S03", 124)]
    assert tl[1][1] == pytest.approx(177 / 30) and tl[2][1] == pytest.approx(498 / 30)
    assert total == pytest.approx(622 / 30)


def test_placements_voice_at_scene_start_sfx_at_start_plus_at():
    voices, fx, total = mix.placements(SCENES, 30, lambda sid: None if sid == "S03" else f"{sid}.mp3", lambda n: f"{n}.wav")
    assert voices == [("S01.mp3", 0.0), ("S02.mp3", round(177 / 30, 6))]
    assert fx[0] == ("whoosh.wav", 0.2, 0.22)
    assert fx[1] == ("pop.wav", round(177 / 30 + 1.0, 6), 0.22)
    assert fx[2] == ("ding.wav", round(177 / 30 + 3.5, 6), 0.35)   # volume capped at 0.35
    assert total == pytest.approx(622 / 30)


def test_placement_errors():
    with pytest.raises(mix.MixError):
        mix.timeline([{"id": "S01", "duration": None}], 30)
    with pytest.raises(mix.MixError):
        mix.placements([{"id": "S01", "duration": 2.0, "sfx": [{"name": "pop", "at": 2.5}]}], 30, lambda s: None, lambda n: n)


def test_narration_gaps_exclude_scenes_without_narration():
    spans = [("S01", 0.0, 5.9), ("S02", 5.9, 16.6), ("S03", 16.6, 20.733)]
    silences = [(5.2, 6.1), (9.0, 11.0), (15.9, 20.733)]
    gaps = mix.narration_gaps(silences, spans, {"S01", "S02"})
    assert gaps == [{"from": 9.0, "to": 11.0, "seconds": 2.0, "scenes": ["S02"]}]   # 15.9-16.6 is only 0.7 s once S03 is removed
    gaps2 = mix.narration_gaps(silences, spans, {"S01", "S02", "S03"})
    assert [g["from"] for g in gaps2] == [9.0, 15.9]


def test_parse_silences_and_ebur128():
    err = "silence_start: 2.01\nsilence_end: 4.0 | silence_duration: 1.99\nsilence_start: 8.5\n"
    assert mix.parse_silences(err, 10.0) == [(2.01, 4.0), (8.5, 10.0)]
    summary = ("[Parsed_ebur128_0] t: 1 M: -20.0 S: -21.0 I: -30.0 LUFS LRA: 0.0 LU\nSummary:\n  Integrated loudness:\n"
               "    I:         -14.2 LUFS\n    Threshold: -24.3 LUFS\n  Loudness range:\n    LRA:         1.5 LU\n"
               "  True peak:\n    Peak:        -1.4 dBFS\n")
    assert mix.parse_ebur128(summary) == {"lufs": -14.2, "true_peak": -1.4, "lra": 1.5}


def test_graph_has_the_contract_filters():
    inputs, filters, lab, vsd = mix.graph(20.0, [("v.mp3", 0.0)], [("s.wav", 1.0, 0.22)], "bgm.wav", 0.32)
    g = ";".join(filters)
    assert "-stream_loop" in inputs and inputs[inputs.index("-stream_loop") + 1] == "-1"
    assert "sidechaincompress=threshold=0.03:ratio=3:attack=40:release=700" in g
    assert "afade=t=in:d=1" in g and "afade=t=out:st=18.5000:d=1.5" in g
    assert g.count("normalize=0") == 3 and "adelay=1000|1000" in g and "end_sample=960000" in g
    assert lab == "[mix]" and vsd == "[vsd]"
    assert "limit=0.8414" in mix.limiter_chain(3.0) and "aresample=192000" in mix.limiter_chain(3.0)
    assert mix.loudness_gain(-40.0) == 20.0 and mix.loudness_gain(-20.0) == 6.0


# ---------- bgm / sfx ----------

def test_bgm_lift_plan():
    scenes = [{"id": f"S{i:02d}", "duration": 8, **({"hero": True} if i == 5 else {})} for i in range(1, 9)]
    assert bgm.plan_lift(scenes, 64.0)[:2] == (32.0, "S05")
    for s in scenes:
        s.pop("hero", None)
    at, sid, _ = bgm.plan_lift(scenes, 64.0)
    assert sid == "S06" and at == 40.0   # scene start nearest 58 % (37.1 s) within 40-75 %
    assert bgm.plan_lift(scenes[:3], 24.0)[0] is None
    bar, n_bars, intro, lift_bar, final_at = bgm.plan_structure(64.0, 75, 32.0)
    assert lift_bar * bar == pytest.approx(32.0) and final_at <= 64.0 - 3.2


def test_bgm_compose_deterministic_and_exact_length():
    a, ma = bgm.compose(8, "warm", seed=1)
    b, _ = bgm.compose(8, "warm", seed=1)
    c, _ = bgm.compose(8, "warm", seed=2)
    assert a.shape == (8 * bgm.SR, 2) and np.array_equal(a, b) and not np.array_equal(a, c)
    assert ma["sections"][-1]["end"] == 8 and set(bgm.MOODS) == {"calm", "tense", "hopeful", "bright", "tech", "warm", "cinematic"}


def test_sfx_recipes_and_budget():
    for name in sfx.RECIPES:
        x = sfx.render(name)
        assert 0.79 <= np.abs(x).max() <= 0.8 + 1e-9 and len(x) < sfx.SR
    names, problems = sfx.used_names([{"id": "S01", "sfx": [{"name": "pop", "at": 0}, {"name": "pop", "at": 1}, {"name": "ding", "at": 2}]}])
    assert names == ["pop", "ding"] and problems and "max 2" in problems[0]


# ---------- one real mix (ffmpeg) ----------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_mix_audio_hits_target_and_exact_length(tmp_path):
    ff = shutil.which("ffmpeg")

    def lavfi(name, src, *extra):
        p = str(tmp_path / name)
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", src, *extra, "-ar", "48000", "-ac", "2", p], check=True)
        return p
    voice = lavfi("voice.wav", "sine=frequency=440:duration=2", "-af", "volume=6dB")
    music = lavfi("bgm.wav", "anoisesrc=color=pink:amplitude=0.5:duration=4:seed=1")
    pop = lavfi("sfx.wav", "sine=frequency=1000:duration=0.3")
    out = str(tmp_path / "mix.wav")
    total = 301 / 30
    rep = mix.mix_audio(out, total, [(voice, 0.0), (voice, 4.0)], [(pop, 1.0, 0.22)], music, 0.32)
    m = mix.measure(out)
    assert abs(m["lufs"] - mix.TARGET_LUFS) <= 1.0, (m, rep)
    assert m["true_peak"] <= -0.8, m
    with wave.open(out) as w:
        assert w.getframerate() == 48000 and w.getnchannels() == 2 and w.getsampwidth() == 2
        assert w.getnframes() == round(total * 48000)
    assert any(abs(a - 2.0) < 0.2 and abs(b - 4.0) < 0.2 for a, b in rep["silences"]), rep["silences"]
