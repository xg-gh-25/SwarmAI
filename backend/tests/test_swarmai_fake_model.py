"""SwarmAI's Feature Map model stand-in (.swarmai/features/fixtures/model/server.py).

Design: docs/design/2026-11-20-feature-map-fake-model.md §8. The wire format is checked with
botocore's own event-stream parser (the one the AWS SDKs use), the script choice with a table
of request histories, the routes against a real server on a loopback port.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from botocore.eventstream import ChecksumMismatch, EventStreamBuffer

SERVER = Path(__file__).resolve().parents[2] / ".swarmai/features/fixtures/model/server.py"
_spec = importlib.util.spec_from_file_location("fm_fake_model", SERVER)
fake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake)

TOOLS = [{"name": "Bash", "input_schema": {"type": "object"}}]


def _decode(stream: bytes) -> list[dict]:
    buf = EventStreamBuffer()
    buf.add_data(stream)
    out = []
    for msg in buf:
        assert msg.headers[":event-type"] == "chunk" and msg.headers[":message-type"] == "event"
        out.append(json.loads(base64.b64decode(json.loads(msg.payload)["bytes"])))
    return out


def test_frames_decode_with_botocore_and_a_broken_crc_is_refused():
    events = [{"type": "message_start", "n": 1}, {"type": "content_block_delta", "t": "héllo"}]
    stream = b"".join(fake.frame(e) for e in events)
    assert _decode(stream) == events
    broken = bytearray(fake.frame(events[0]))
    broken[-1] ^= 0xFF
    with pytest.raises(ChecksumMismatch):
        _decode(bytes(broken))


def test_a_text_reply_and_a_tool_reply_are_whole_turns():
    text = [e for e, _ in fake.reply_events("m", {"text": "Hello there", "chunk": 4}, "")]
    assert [e["type"] for e in text][0] == "message_start" and text[-1]["type"] == "message_stop"
    assert "".join(e["delta"]["text"] for e in text if e["type"] == "content_block_delta") == "Hello there"
    assert next(e for e in text if e["type"] == "message_delta")["delta"]["stop_reason"] == "end_turn"
    tool = [e for e, _ in fake.reply_events("m", {"tool_use": {"name": "Bash", "input": {"command": "echo x"}}}, "")]
    start = next(e for e in tool if e["type"] == "content_block_start")["content_block"]
    assert start["type"] == "tool_use" and start["name"] == "Bash" and start["id"].startswith("toolu_")
    args = "".join(e["delta"]["partial_json"] for e in tool if e["type"] == "content_block_delta")
    assert json.loads(args) == {"command": "echo x"}
    assert next(e for e in tool if e["type"] == "message_delta")["delta"]["stop_reason"] == "tool_use"
    # every reply has fresh ids
    ids = {next(e for e, _ in fake.reply_events("m", {"text": "a"}, ""))["message"]["id"] for _ in range(3)}
    assert len(ids) == 3


def _user(text):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _assistant():
    return {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]}


def _result(output):
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": output}]}


SYSTEM = {"role": "system", "content": "reminder"}


@pytest.mark.parametrize("doc,expected", [
    # no marker: default
    ({"tools": TOOLS, "messages": [_user("hi")]}, (None, 0, "")),
    # a marker in the last user message, a trailing system message (the CLI's own)
    ({"tools": TOOLS, "messages": [_user("go [fm:tool-bash]"), SYSTEM]}, ("tool-bash", 0, "")),
    # an older marked message is superseded by a newer one
    ({"tools": TOOLS, "messages": [_user("[fm:hello]"), _assistant(), _result("x"), _user("[fm:slow]")]},
     ("slow", 0, "")),
    # after one tool round: step 1, with the tool's output
    ({"tools": TOOLS, "messages": [_user("[fm:tool-bash]"), _assistant(), _result("feature-map-marker"), SYSTEM]},
     ("tool-bash", 1, "feature-map-marker")),
    # after two rounds: step 2, the latest output
    ({"tools": TOOLS, "messages": [_user("[fm:two]"), _assistant(), _result("one"), _assistant(), _result("two")]},
     ("two", 2, "two")),
    # a marker printed by a tool does not restart the script
    ({"tools": TOOLS, "messages": [_user("[fm:tool-bash]"), _assistant(), _result("cat: [fm:tool-bash]")]},
     ("tool-bash", 1, "cat: [fm:tool-bash]")),
    # recalled context first, typed text last: the last marker wins
    ({"tools": TOOLS, "messages": [_user("<recall>earlier [fm:slow-a]</recall>\nnow [fm:slow-b]")]},
     ("slow-b", 0, "")),
    # a request without tools (a probe, a summary that resends history): default
    ({"messages": [_user("[fm:tool-bash]")]}, (None, 0, "")),
    # plain string content
    ({"tools": TOOLS, "messages": [{"role": "user", "content": "x [fm:hello]"}]}, ("hello", 0, "")),
])
def test_choose(doc, expected):
    assert fake.choose(doc) == expected


def test_steps_end_with_the_default_and_a_missing_script_says_so(tmp_path):
    (tmp_path / "one.yaml").write_text("steps:\n  - text: first\n")
    assert fake.load_step(tmp_path, "one", 0) == ({"text": "first"}, "one:0")
    step, chosen = fake.load_step(tmp_path, "one", 1)
    assert chosen == "one:end" and step == {"text": fake.DEFAULT_TEXT}, "past the end: the turn ends"
    assert fake.load_step(tmp_path, "nope", 0)[1] == "nope:missing"
    assert fake.load_step(tmp_path, None, 0)[1] == "default"


@pytest.fixture()
def server(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "hello.yaml").write_text("steps:\n  - text: Hello from the script.\n")
    (scripts / "bad.yaml").write_text(
        "steps:\n  - error: {status: 400, type: ValidationException, message: Scripted refusal.}\n")
    fake.Handler.scripts = scripts
    fake.Handler.log_path = tmp_path / "model.jsonl"
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), fake.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", tmp_path / "model.jsonl"
    httpd.shutdown()
    httpd.server_close()


def _post(url, doc):
    req = urllib.request.Request(url, data=json.dumps(doc).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    return urllib.request.urlopen(req, timeout=10)


def test_routes(server):
    base, log = server
    with _post(f"{base}/model/m/invoke-with-response-stream",
               {"tools": TOOLS, "messages": [_user("[fm:hello]")]}) as r:
        assert r.headers["content-type"] == "application/vnd.amazon.eventstream"
        events = _decode(r.read())
    assert "".join(e["delta"].get("text", "") for e in events
                   if e["type"] == "content_block_delta") == "Hello from the script."
    with _post(f"{base}/model/m/invoke", {"messages": [_user("probe")]}) as r:
        assert json.loads(r.read())["content"][0]["type"] == "text"
    with _post(f"{base}/model/m/converse", {"messages": []}) as r:
        assert json.loads(r.read())["output"]["message"]["role"] == "assistant"
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(f"{base}/model/m/invoke-with-response-stream", {"tools": TOOLS, "messages": [_user("[fm:bad]")]})
    assert err.value.code == 400 and err.value.headers["x-amzn-ErrorType"] == "ValidationException"
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(f"{base}/foundation-models", {})
    assert err.value.code == 404
    with urllib.request.urlopen(f"{base}/ping", timeout=5) as r:
        assert json.loads(r.read()) == {"ok": True}
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["chosen"] for r in rows if "chosen" in r][:3] == ["hello:0", "invoke", "converse"]
    assert all("body" not in r for r in rows), "the log never holds request bodies"
