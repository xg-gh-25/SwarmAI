"""tts.py engines: edge-tts and Amazon Polly give the same outputs under the same rules.

Pure tests (always run): Polly speech marks -> word boundaries, engine resolution, voice choice, SSML, cache key.
Integration tests (need ffmpeg): a two-scene zh storyboard voiced through tts.run with a fake edge_tts module and
with a real boto3 Polly client whose responses come from botocore's Stubber (no network, but every request is
checked against Polly's API model); words.json / captions.json shape and the duration rule must match.
Run: python3 -m pytest tests/test_tts_engines.py -q   (from the skill directory)"""
import argparse
import asyncio
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import types

import pytest

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import tts  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


# ---------- pure ----------

def test_polly_marks_become_word_boundaries():
    marks = [
        {"time": 6, "type": "sentence", "start": 0, "end": 30, "value": "大家好，今天讲数据库。"},
        {"time": 100, "type": "word", "start": 0, "end": 6, "value": "大家"},
        {"time": 450, "type": "word", "start": 6, "end": 9, "value": "好"},
        {"time": 800, "type": "word", "start": 12, "end": 18, "value": "<lang>今天</lang>"},
        {"time": 900, "type": "word", "start": 18, "end": 18, "value": "  "},
        {"time": 1250, "type": "word", "start": 18, "end": 27, "value": "数据库"},
    ]
    b = tts.polly_boundaries(marks, 2.0)
    assert b == [
        {"text": "大家", "start": 0.1, "end": 0.45},
        {"text": "好", "start": 0.45, "end": 0.8},
        {"text": "今天", "start": 0.8, "end": 1.25},
        {"text": "数据库", "start": 1.25, "end": 2.0},
    ]
    assert tts.polly_boundaries([{"time": 3000, "type": "word", "value": "x"}], 1.0) == [{"text": "x", "start": 3.0, "end": 3.0}]


def test_engine_resolution():
    never = lambda: pytest.fail("probe must not run")   # noqa: E731
    assert tts.resolve_engine("polly", {"name": "zh-CN-YunxiNeural"}, never)[0] == "polly"
    assert tts.resolve_engine("edge", {"name": "Zhiyu"}, never)[0] == "edge"
    assert tts.resolve_engine(None, {"engine": "polly", "name": "Zhiyu"}, never)[0] == "polly"
    assert tts.resolve_engine(None, {"name": "zh-CN-YunxiNeural"}, never)[0] == "edge"   # existing projects stay on edge
    assert tts.resolve_engine(None, {"name": "Matthew"}, never)[0] == "polly"
    assert tts.resolve_engine(None, {}, lambda: (True, "ok"))[0] == "polly"
    assert tts.resolve_engine(None, {}, lambda: (False, "denied")) == ("edge", "auto: denied")
    assert tts.resolve_engine("auto", {"name": "zh-CN-YunxiNeural"}, lambda: (True, "ok"))[0] == "polly"
    with pytest.raises(tts.UsageError):
        tts.resolve_engine(None, {"engine": "azure"}, never)


def test_voice_choice_follows_the_engine():
    assert tts.pick_voice("edge", {"name": "zh-CN-XiaoxiaoNeural"}, "zh-CN", None) == ("zh-CN-XiaoxiaoNeural", False)
    assert tts.pick_voice("polly", {"name": "zh-CN-YunxiNeural"}, "zh-CN", "polly") == ("Zhiyu", True)
    assert tts.pick_voice("polly", {}, "en-US", None) == ("Matthew", True)
    assert tts.pick_voice("polly", {}, "en-GB", None) == ("Amy", True)
    assert tts.pick_voice("edge", {"name": "Zhiyu"}, "zh-CN", "edge") == ("zh-CN-YunxiNeural", True)
    # first voicing of a contract storyboard (edge voice name) with --engine auto when Polly works
    assert tts.pick_voice("polly", {"name": "zh-CN-YunxiNeural"}, "zh-CN", "auto") == ("Zhiyu", True)
    with pytest.raises(tts.UsageError):   # the storyboard contradicts itself
        tts.pick_voice("polly", {"engine": "polly", "name": "zh-CN-YunxiNeural"}, "zh-CN", None)
    with pytest.raises(tts.UsageError):   # no default Polly voice for this language
        tts.pick_voice("polly", {}, "fr-FR", None)


def test_polly_ssml_escapes_text_and_reads_latin_as_english():
    s = tts.polly_ssml("用 SQL & <b>向量</b>", "+0%", "zh-CN")
    assert s == '<speak>用 <lang xml:lang="en-US">SQL</lang> &amp; &lt;b&gt;向量&lt;/b&gt;</speak>'   # one letter stays as is
    assert tts.polly_ssml("Hello & bye", "+10%", "en-US") == '<speak><prosody rate="110%">Hello &amp; bye</prosody></speak>'
    assert 'rate="20%"' in tts.polly_ssml("x", "-95%", "en-US")
    assert 'rate="200%"' in tts.polly_ssml("x", "+150%", "en-US")


def test_cache_key_includes_engine():
    edge = tts.tts_hash("a", "a", "v", "+0%", "edge")
    polly = tts.tts_hash("a", "a", "v", "+0%", "polly")
    assert edge != polly
    assert tts.tts_hash("a", "a", "v", "+0%") == edge


def test_edge_cache_key_unchanged():
    """An edge project voiced before engines existed keeps its cache (no re-voicing because the code changed)."""
    legacy = hashlib.sha256(json.dumps([tts.CACHE_VERSION, "文本", "文本", "zh-CN-YunxiNeural", "+0%"],
                                       ensure_ascii=False).encode()).hexdigest()[:16]
    assert tts.tts_hash("文本", "文本", "zh-CN-YunxiNeural", "+0%", "edge") == legacy


# ---------- missing dependencies: exit 2, one line ----------

def _main(monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["tts.py"] + argv)
    monkeypatch.setattr(tts, "tool", lambda name: name)
    with pytest.raises(SystemExit) as e:
        tts.main()
    return e.value.code, capsys.readouterr().err


def _write_storyboard(d, voice, narrations=("大家好，今天讲 SQL 数据库。", "检索快、成本低。")):
    sb = {"title": "t", "lang": "zh-CN", "width": 1920, "height": 1080, "fps": 30, "style": "exec-brief",
          "method": "principle", "mode": "explainer", "voice": voice, "bgm": None, "captions": True,
          "scenes": [{"id": f"S0{i + 1}", "title": "x", "narration": n, "visual": "x", "shot_type": "statement",
                      "duration": None, "transition": "cut"} for i, n in enumerate(narrations)]}
    with open(os.path.join(d, "storyboard.json"), "w", encoding="utf-8") as f:
        json.dump(sb, f, ensure_ascii=False)
    with open(os.path.join(d, "pronounce.json"), "w", encoding="utf-8") as f:
        json.dump({"SQL": "sequel"}, f)
    return sb


def test_missing_dependency_exits_2_with_one_line(tmp_path, monkeypatch, capsys):
    _write_storyboard(str(tmp_path), {"name": "zh-CN-YunxiNeural"})
    monkeypatch.setitem(sys.modules, "edge_tts", None)          # import edge_tts -> ImportError
    code, err = _main(monkeypatch, capsys, ["--project", str(tmp_path)])
    assert code == 2 and err.count("\n") == 1 and "edge-tts" in err and "--engine polly" in err

    monkeypatch.setitem(sys.modules, "boto3", None)             # import boto3 -> ImportError
    code, err = _main(monkeypatch, capsys, ["--project", str(tmp_path), "--engine", "polly"])
    assert code == 2 and err.count("\n") == 1 and "boto3" in err and "SWARM_BACKEND_BIN" in err


def test_bad_storyboard_values_exit_2(tmp_path, monkeypatch, capsys):
    sb = _write_storyboard(str(tmp_path), {"name": "zh-CN-YunxiNeural"})
    sb["scenes"][0]["id"] = "../../outside"
    with open(os.path.join(tmp_path, "storyboard.json"), "w", encoding="utf-8") as f:
        json.dump(sb, f, ensure_ascii=False)
    code, err = _main(monkeypatch, capsys, ["--project", str(tmp_path)])
    assert code == 2 and "scene ids" in err and err.count("\n") == 1
    _write_storyboard(str(tmp_path), {"name": "zh-CN-YunxiNeural", "rate": "fast"})
    code, err = _main(monkeypatch, capsys, ["--project", str(tmp_path)])
    assert code == 2 and "voice.rate" in err


@pytest.mark.skipif(os.path.isfile("/opt/homebrew/bin/ffprobe"), reason="tts.py also looks in /opt/homebrew/bin")
def test_missing_ffprobe_exits_2_with_one_line(tmp_path):
    _write_storyboard(str(tmp_path), {"name": "zh-CN-YunxiNeural"})
    empty = tmp_path / "bin"
    empty.mkdir()
    r = subprocess.run([sys.executable, os.path.join(SKILL, "scripts", "tts.py"), "--project", str(tmp_path)],
                       capture_output=True, text=True, timeout=60, env={**os.environ, "PATH": str(empty)})
    assert r.returncode == 2, r.stderr
    assert r.stderr.count("\n") == 1 and "ffprobe" in r.stderr and "winget" in r.stderr and "apt" in r.stderr


def test_auto_falls_back_to_edge_when_polly_denied(monkeypatch):
    from botocore.stub import Stubber
    client = _polly_client()
    stub = Stubber(client)
    for _ in range(2):
        stub.add_client_error("synthesize_speech", service_error_code="AccessDeniedException",
                              service_message="not authorized to perform: polly:SynthesizeSpeech", http_status_code=400)
    stub.activate()
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(tts, "polly_client", lambda region=None: client)
    ok, why = tts.polly_probe("zh-CN")
    assert not ok and "AccessDeniedException" in why
    engine, reason = tts.resolve_engine(None, {}, lambda: tts.polly_probe("zh-CN"))
    assert engine == "edge" and reason.startswith("auto:") and "AccessDeniedException" in reason
    monkeypatch.delenv("AWS_ACCESS_KEY_ID")
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", os.devnull)
    monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", raising=False)
    monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", raising=False)
    monkeypatch.delenv("AWS_WEB_IDENTITY_TOKEN_FILE", raising=False)
    assert tts.polly_probe("zh-CN") == (False, "no AWS credentials found")


# ---------- both engines through tts.run (ffmpeg) ----------

# The same spoken words and start times for both engines: spoken text is "大家好，今天讲 sequel 数据库。" (pronounce.json).
TIMED = {
    "S01": [("大家", 0.10), ("好", 0.40), ("今天", 0.70), ("讲", 0.95), ("sequel", 1.10), ("数据库", 1.40)],
    "S02": [("检索", 0.10), ("快", 0.40), ("成本", 0.70), ("低", 1.00)],
}


def _mp3(path, speech=1.6, silence=0.9):
    subprocess.run([shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=frequency=330:duration={speech}", "-af", f"apad=pad_dur={silence}",
                    "-c:a", "libmp3lame", "-b:a", "48k", str(path)], check=True, timeout=60)
    return open(path, "rb").read()


def _ns(project, engine=None):
    return argparse.Namespace(project=str(project), scene=None, force=False, engine=engine)


def _fake_edge(audio, spoken_to_sid):
    class Communicate:
        def __init__(self, text, voice, rate="+0%", boundary="WordBoundary"):
            self.sid, self.boundary = spoken_to_sid[text], boundary

        async def stream(self):
            yield {"type": "audio", "data": audio}
            for w, st in TIMED[self.sid]:
                yield {"type": "WordBoundary", "offset": int(st * 1e7), "duration": int(0.25 * 1e7), "text": w}
    return types.SimpleNamespace(Communicate=Communicate, __version__="test")


def _polly_client():
    import boto3
    return boto3.Session(aws_access_key_id="AKIDEXAMPLE", aws_secret_access_key="secret",
                         region_name="us-east-1").client("polly")


def _stub_polly(client, audio, scenes):
    from botocore.response import StreamingBody
    from botocore.stub import Stubber
    stub = Stubber(client)
    for sid, spoken in scenes:
        common = {"Engine": "neural", "VoiceId": "Zhiyu", "TextType": "ssml", "Text": tts.polly_ssml(spoken, "+0%", "zh-CN")}
        stub.add_response("synthesize_speech",
                          {"AudioStream": StreamingBody(io.BytesIO(audio), len(audio)), "ContentType": "audio/mpeg",
                           "RequestCharacters": len(spoken)},
                          {**common, "OutputFormat": "mp3", "SampleRate": "24000"})
        marks = "\n".join(json.dumps({"time": int(st * 1000), "type": "word", "start": 0, "end": 0, "value": w},
                                     ensure_ascii=False) for w, st in TIMED[sid]).encode()
        stub.add_response("synthesize_speech",
                          {"AudioStream": StreamingBody(io.BytesIO(marks), len(marks)), "ContentType": "application/x-json-stream",
                           "RequestCharacters": len(spoken)},
                          {**common, "OutputFormat": "json", "SpeechMarkTypes": ["word"]})
    stub.activate()
    return stub


def _outputs(project):
    vdir = os.path.join(project, "audio", "voice")
    sb = json.load(open(os.path.join(project, "storyboard.json"), encoding="utf-8"))
    out = {}
    for s in sb["scenes"]:
        sid = s["id"]
        out[sid] = {"words": json.load(open(os.path.join(vdir, sid + ".words.json"), encoding="utf-8")),
                    "captions": json.load(open(os.path.join(vdir, sid + ".captions.json"), encoding="utf-8")),
                    "meta": json.load(open(os.path.join(vdir, sid + ".tts.json"), encoding="utf-8")),
                    "duration": s["duration"], "mp3": os.path.join(vdir, sid + ".mp3")}
    return sb, out


def _shape(rows):
    return [sorted(r) for r in rows], all(isinstance(r["text"], str) and isinstance(r["start"], float)
                                         and isinstance(r["end"], float) and r["end"] >= r["start"] for r in rows)


@needs_ffmpeg
def test_both_engines_write_same_shapes(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "LANES", 1)   # Stubber answers in order: one scene at a time
    audio = _mp3(tmp_path / "voice.mp3")
    spoken = {"S01": "大家好，今天讲 sequel 数据库。", "S02": "检索快、成本低。"}

    edge_p = tmp_path / "edge"
    edge_p.mkdir()
    _write_storyboard(str(edge_p), {"name": "zh-CN-YunxiNeural", "rate": "+0%"})
    monkeypatch.setitem(sys.modules, "edge_tts", _fake_edge(audio, {v: k for k, v in spoken.items()}))
    assert asyncio.run(tts.run(_ns(edge_p))) == 0

    polly_p = tmp_path / "polly"
    polly_p.mkdir()
    _write_storyboard(str(polly_p), {"name": "zh-CN-YunxiNeural", "rate": "+0%"})
    client = _polly_client()
    stub = _stub_polly(client, audio, list(spoken.items()))
    monkeypatch.setattr(tts, "polly_client", lambda region=None: client)
    assert asyncio.run(tts.run(_ns(polly_p, "polly"))) == 0
    stub.assert_no_pending_responses()

    sb_e, e = _outputs(str(edge_p))
    sb_p, p = _outputs(str(polly_p))
    for sid in ("S01", "S02"):
        for kind in ("words", "captions"):
            keys_e, ok_e = _shape(e[sid][kind])
            keys_p, ok_p = _shape(p[sid][kind])
            assert ok_e and ok_p and keys_e == keys_p and all(k == ["end", "start", "text"] for k in keys_e)
            assert [r["text"] for r in e[sid][kind]] == [r["text"] for r in p[sid][kind]]
            assert [r["start"] for r in e[sid][kind]] == [r["start"] for r in p[sid][kind]]
        # original spelling comes back through pronounce.json on both engines
        assert "SQL" in [w["text"] for w in e["S01"]["words"]] and "SQL" in [w["text"] for w in p["S01"]["words"]]
        for o in (e[sid], p[sid]):
            voice_s = tts.probe_seconds(o["mp3"])
            assert o["duration"] == tts.scene_duration(voice_s, 30)
            assert o["duration"] >= voice_s + tts.PAD_AFTER_VOICE - 1e-6 and o["duration"] >= tts.MIN_SCENE
            assert abs(round(o["duration"] * 30) / 30 - o["duration"]) < 0.0005   # a whole frame (3 decimals)
        assert e[sid]["meta"]["engine"] == "edge" and p[sid]["meta"]["engine"] == "polly"
        assert e[sid]["meta"]["hash"] != p[sid]["meta"]["hash"]
    # Polly's last word ends at the end of speech (the trimmed tail is not speech)
    last = p["S01"]["words"][-1]
    assert last["end"] == pytest.approx(tts.probe_seconds(p["S01"]["mp3"]) - tts.TAIL_KEEP, abs=0.002)
    assert sb_e["voice"]["name"] == "zh-CN-YunxiNeural" and sb_p["voice"]["name"] == "Zhiyu"


@needs_ffmpeg
def test_polly_two_scene_zh_storyboard_stays_on_polly(tmp_path, monkeypatch, capsys):
    """--engine polly once; the next run without a flag reuses the Polly cache (no request: the Stubber has none left)."""
    monkeypatch.setattr(tts, "LANES", 1)
    audio = _mp3(tmp_path / "voice.mp3")
    spoken = [("S01", "大家好，今天讲 sequel 数据库。"), ("S02", "检索快、成本低。")]
    proj = tmp_path / "p"
    proj.mkdir()
    _write_storyboard(str(proj), {"name": "zh-CN-YunxiNeural"})
    client = _polly_client()
    stub = _stub_polly(client, audio, spoken)
    monkeypatch.setattr(tts, "polly_client", lambda region=None: client)
    assert asyncio.run(tts.run(_ns(proj, "polly"))) == 0
    stub.assert_no_pending_responses()
    for sid, _ in spoken:
        for ext in (".mp3", ".words.json", ".captions.json"):
            assert os.path.isfile(os.path.join(proj, "audio", "voice", sid + ext))
    capsys.readouterr()
    assert asyncio.run(tts.run(_ns(proj))) == 0
    out = capsys.readouterr().out
    assert "engine: polly" in out and out.count(": reused,") == 2


@needs_ffmpeg
def test_null_voice_gets_the_chosen_voice_written_back(tmp_path, monkeypatch):
    audio = _mp3(tmp_path / "voice.mp3")
    spoken = {"S01": "大家好，今天讲 sequel 数据库。", "S02": "检索快、成本低。"}
    proj = tmp_path / "p"
    proj.mkdir()
    _write_storyboard(str(proj), None)
    monkeypatch.setattr(tts, "polly_probe", lambda lang: (False, "no AWS credentials found"))
    monkeypatch.setitem(sys.modules, "edge_tts", _fake_edge(audio, {v: k for k, v in spoken.items()}))
    assert asyncio.run(tts.run(_ns(proj))) == 0
    sb = json.load(open(os.path.join(proj, "storyboard.json"), encoding="utf-8"))
    assert sb["voice"] == {"name": "zh-CN-YunxiNeural"} and all(s["duration"] for s in sb["scenes"])
