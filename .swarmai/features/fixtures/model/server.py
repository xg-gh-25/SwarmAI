"""A scripted stand-in for Bedrock, for walking SwarmAI's chat path in a Feature Map sandbox.

Design: docs/design/2026-11-20-feature-map-fake-model.md. The app reaches it through its own
code path: the backend in Bedrock API-key mode with a fake key, and
``ANTHROPIC_BEDROCK_BASE_URL`` / ``AWS_ENDPOINT_URL_BEDROCK_RUNTIME`` pointing here. It binds
127.0.0.1 only, never forwards anything, and keeps no state between requests.

Routes:
- ``POST /model/<id>/invoke-with-response-stream``: a scripted reply as an AWS event stream
  (prelude, headers, CRC32s; payload ``{"bytes": base64(anthropic stream event)}``).
- ``POST /model/<id>/invoke``: a short fixed reply (the CLI's model probes, Settings verify).
- ``POST /model/<id>/converse``: a short fixed reply in Converse shape (the backend's own calls).
- ``GET /ping``.

Which reply: a request that carries ``tools`` (the CLI's main loop) and a user message with a
marker ``[fm:<name>]`` gets step N of ``<scripts>/<name>.yaml``, N = the number of assistant
turns after that message. Only user messages without a ``tool_result`` count (a tool's output
may print a marker), and the last marker in the message wins (SwarmAI prepends recalled
context, which may hold an earlier walk's marker, before the typed text). Past the last step,
and for every other request, the default reply ends the turn, so a script can never loop.

Script steps (each may set ``chunk``, characters per delta, and ``delay_ms`` between deltas):
``text`` (``{last_tool_result}`` is replaced by the latest tool output; ``repeat`` repeats
it), ``thinking`` (a thinking block before the step's ``text``), ``tool_use``
(``{name, input}``), ``error`` (``{status, type, message}``: an HTTP error before the stream).

Usage: python server.py --port N --scripts DIR [--log FILE]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import struct
import sys
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import yaml

DEFAULT_TEXT = "OK."
MARKER = re.compile(r"\[fm:([a-z0-9][a-z0-9_-]{0,63})\]")


# ── event stream ────────────────────────────────────────────────────────────

def _header(name: str, value: str) -> bytes:
    n, v = name.encode(), value.encode()
    return bytes([len(n)]) + n + b"\x07" + struct.pack(">H", len(v)) + v


def frame(event: dict) -> bytes:
    """One AWS event-stream message carrying an Anthropic stream event."""
    headers = (_header(":event-type", "chunk") + _header(":content-type", "application/json")
               + _header(":message-type", "event"))
    body = json.dumps({"bytes": base64.b64encode(json.dumps(event).encode()).decode()}).encode()
    total = 12 + len(headers) + len(body) + 4
    prelude = struct.pack(">II", total, len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude) & 0xFFFFFFFF)
    message = prelude + headers + body
    return message + struct.pack(">I", zlib.crc32(message) & 0xFFFFFFFF)


def _new_id(prefix: str) -> str:
    return f"{prefix}_fm{uuid.uuid4().hex[:20]}"


def _pieces(text: str, chunk: int) -> Iterator[str]:
    chunk = max(1, int(chunk))
    for i in range(0, len(text), chunk):
        yield text[i:i + chunk]


def reply_events(model: str, step: dict, tool_output: str) -> Iterator[tuple[dict, float]]:
    """``(event, seconds to wait before it)`` for one assistant turn."""
    delay = max(0, int(step.get("delay_ms") or 0)) / 1000
    chunk = int(step.get("chunk") or 8)
    yield ({"type": "message_start", "message": {
        "id": _new_id("msg"), "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 1}}}, 0)
    index = 0
    stop = "end_turn"
    if step.get("thinking"):
        yield ({"type": "content_block_start", "index": index,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""}}, 0)
        for piece in _pieces(str(step["thinking"]), chunk):
            yield ({"type": "content_block_delta", "index": index,
                    "delta": {"type": "thinking_delta", "thinking": piece}}, delay)
        yield ({"type": "content_block_delta", "index": index,
                "delta": {"type": "signature_delta", "signature": "fm-placeholder-signature"}}, 0)
        yield ({"type": "content_block_stop", "index": index}, 0)
        index += 1
    if "tool_use" in step:
        tool = step["tool_use"] or {}
        stop = "tool_use"
        yield ({"type": "content_block_start", "index": index, "content_block": {
            "type": "tool_use", "id": _new_id("toolu"), "name": str(tool.get("name")), "input": {}}}, 0)
        for piece in _pieces(json.dumps(tool.get("input") or {}), max(chunk, 10)):
            yield ({"type": "content_block_delta", "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": piece}}, delay)
        yield ({"type": "content_block_stop", "index": index}, 0)
    else:
        text = str(step.get("text", DEFAULT_TEXT)).replace("{last_tool_result}", tool_output)
        text = text * max(1, int(step.get("repeat") or 1))
        yield ({"type": "content_block_start", "index": index,
                "content_block": {"type": "text", "text": ""}}, 0)
        for piece in _pieces(text, chunk):
            yield ({"type": "content_block_delta", "index": index,
                    "delta": {"type": "text_delta", "text": piece}}, delay)
        yield ({"type": "content_block_stop", "index": index}, 0)
    yield ({"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": {"output_tokens": 20}}, 0)
    yield ({"type": "message_stop", "amazon-bedrock-invocationMetrics": {
        "inputTokenCount": 10, "outputTokenCount": 20, "invocationLatency": 5,
        "firstByteLatency": 1}}, 0)


# ── script selection ────────────────────────────────────────────────────────

def _blocks(message: dict) -> list:
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def _texts(message: dict) -> str:
    return "\n".join(str(b.get("text", "")) for b in _blocks(message) if b.get("type") == "text")


def _tool_output(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    return " ".join(str(b.get("text", "")) for b in content or [] if isinstance(b, dict))


def choose(doc: dict) -> tuple[str | None, int, str]:
    """``(script name or None, step index, latest tool output)`` for a request body."""
    if not doc.get("tools"):
        return None, 0, ""
    messages = [m for m in doc.get("messages") or [] if isinstance(m, dict)]
    marked, name = None, None
    for i, m in enumerate(messages):
        if m.get("role") != "user" or any(b.get("type") == "tool_result" for b in _blocks(m)):
            continue
        found = MARKER.findall(_texts(m))
        if found:
            marked, name = i, found[-1]
    if marked is None:
        return None, 0, ""
    later = messages[marked + 1:]
    step = sum(1 for m in later if m.get("role") == "assistant")
    results = [b for m in later for b in _blocks(m) if b.get("type") == "tool_result"]
    return name, step, (_tool_output(results[-1]).strip() if results else "")


def load_step(scripts: Path, name: str | None, index: int) -> tuple[dict, str]:
    """``(step, what was chosen)``; the default reply when there is none."""
    if name is None:
        return {"text": DEFAULT_TEXT}, "default"
    path = scripts / f"{name}.yaml"
    try:
        steps = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("steps") or []
    except (OSError, yaml.YAMLError):
        return {"text": f"No script {name}."}, f"{name}:missing"
    if index >= len(steps):
        return {"text": DEFAULT_TEXT}, f"{name}:end"
    step = steps[index]
    return (step if isinstance(step, dict) else {"text": str(step)}), f"{name}:{index}"


# ── HTTP ────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    scripts: Path = Path(".")
    log_path: Path | None = None
    _lock = threading.Lock()

    def _log(self, **row: Any) -> None:
        if self.log_path is None:
            return
        row = {"at": time.strftime("%H:%M:%S"), "path": self.path, **row}
        with self._lock, open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def _json(self, status: int, payload: dict, extra: dict | None = None) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/ping":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"message": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            doc = json.loads(raw) if raw else {}
        except ValueError:
            doc = {}
        m = re.match(r"^/model/([^/]+)/(invoke-with-response-stream|invoke|converse)$", self.path)
        if not m:
            self._log(status=404, bytes=len(raw))
            self._json(404, {"message": f"the stand-in has no route {self.path}"})
            return
        model, action = m.group(1), m.group(2)
        if action == "converse":
            self._log(status=200, model=model, chosen="converse", bytes=len(raw))
            self._json(200, {"output": {"message": {"role": "assistant",
                                                    "content": [{"text": DEFAULT_TEXT}]}},
                             "stopReason": "end_turn",
                             "usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12},
                             "metrics": {"latencyMs": 1}})
            return
        if action == "invoke":
            self._log(status=200, model=model, chosen="invoke", bytes=len(raw))
            self._json(200, {"id": _new_id("msg"), "type": "message", "role": "assistant",
                             "model": model, "content": [{"type": "text", "text": DEFAULT_TEXT}],
                             "stop_reason": "end_turn", "stop_sequence": None,
                             "usage": {"input_tokens": 10, "output_tokens": 2}})
            return
        name, index, tool_output = choose(doc)
        step, chosen = load_step(self.scripts, name, index)
        if "error" in step:
            err = step["error"] or {}
            status = int(err.get("status") or 400)
            self._log(status=status, model=model, chosen=chosen, bytes=len(raw))
            self._json(status, {"message": str(err.get("message") or "Scripted error.")},
                       {"x-amzn-ErrorType": str(err.get("type") or "ValidationException")})
            return
        self._log(status=200, model=model, chosen=chosen, bytes=len(raw))
        self.send_response(200)
        self.send_header("content-type", "application/vnd.amazon.eventstream")
        self.send_header("x-amzn-bedrock-content-type", "application/json")
        self.end_headers()
        try:
            for event, wait in reply_events(model, step, tool_output):
                if wait:
                    time.sleep(wait)
                self.wfile.write(frame(event))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self._log(status=200, model=model, chosen=chosen, note="client went away")

    def log_message(self, *_args: Any) -> None:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--scripts", required=True)
    ap.add_argument("--log")
    args = ap.parse_args(argv)
    Handler.scripts = Path(args.scripts)
    Handler.log_path = Path(args.log) if args.log else None
    if Handler.log_path:
        Handler.log_path.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
