"""Detect degenerate (repetition-loop) model output and stalled thinking.

A model occasionally falls into a loop and emits the same short unit until its
output-token cap — one observed block was 127,641 ``━`` characters streamed over
about 13 minutes; historical transcripts hold more (``\\n\\ncourse`` x thousands,
``core\\n\\n``, ``काए\\n\\n``). Nothing downstream can tell such a stream from real
progress: every delta looks like activity. This module judges the CONTENT.

Signal: the zlib compression ratio of the most recent ``WINDOW`` characters.
Natural language, code and tool inputs carry information and compress a few-fold;
a loop compresses tens-fold. Measured on 2026-10-17 over the local CLI
transcripts (2000-char windows, checked every 250 chars): legitimate text blocks
peaked at 2.76 (421 blocks), tool inputs at 5.07 (3,492 blocks), the 8 degenerate
blocks were all at or above 52.6. ``RATIO_THRESHOLD = 15`` leaves a >3x margin on
both sides. Re-measure with ``scripts/degeneration_dataset_eval.py`` before
changing any constant here.

The same functions serve both the live stream (``DegenerationDetector``, used by
``streaming_orchestrator``) and the CLI-transcript cleaner (``strip_*``, used by
``leak_transcript``), so what is cut from the stream and what is cut from the
replayed history can never disagree.

``ProgressWatch`` covers the other stall shape: invisible thinking that runs for
minutes with no output, or no SDK event at all. It only produces a notice — it
never stops a turn (a slow turn is not a hang).

Public symbols:
- ``WINDOW`` / ``STEP`` / ``RATIO_THRESHOLD`` — detector constants.
- ``compress_ratio(text)`` — raw/compressed byte ratio.
- ``DegenerationDetector`` — incremental per-content-block detector.
- ``find_degenerate_run(text)`` — ``(start, end)`` of the first repeated run.
- ``strip_degenerate_runs(text, marker)`` — replace every run with ``marker``.
- ``strip_degenerate_in_value(obj, marker)`` — same, recursively over a
  tool-input structure (dict / list / str).
- ``ProgressWatch`` — thinking-stall / silence notice tracker (notices are
  delivered as the in-tab ``still_working`` status, never as conversation
  content — they describe the stream, not something the model said).
- ``DEGENERATION_NOTICE`` / ``DEGENERATION_TRANSCRIPT_MARKER`` /
  ``THINKING_STALL_S`` / ``SILENT_STALL_S`` — user-facing text and thresholds.
"""

from __future__ import annotations

import zlib
from typing import Any, Optional

WINDOW = 2000
STEP = 250
RATIO_THRESHOLD = 15.0

# Notice thresholds. Model-side time from a tool result to the first visible
# block of the next response (which includes that block's own streaming time,
# so it over-states the silent part): p99.9 = 156s over 26,914 claude-opus-4-8
# responses, 8 of them >= 300s. 300s therefore marks a genuinely unusual stall.
THINKING_STALL_S = 300.0
# Silence = not even a thinking_tokens frame (those arrive about once a second
# while the model thinks). Kept BELOW the orchestrator's message-timeout floor
# (SessionUnit._compute_message_timeout BASE_TIMEOUT = 300s) so the notice
# lands before that hang guard decides; still above the daemon-log p99 of
# time-to-first-token (145s), which itself includes thinking time.
SILENT_STALL_S = 240.0
# A thinking_tokens frame older than this no longer counts as "still thinking".
_THINKING_FRESH_S = 60.0

# Shown to the user (persisted with the turn) when a loop is cut off.
DEGENERATION_NOTICE = (
    "⚠️ 输出陷入重复已中止 —— 检测到模型输出进入重复循环,本轮已自动停止,"
    "重复部分未保存。可以重新发送或换个说法继续。"
    "(Output stopped: the model fell into a repetition loop.)"
)
# Written into the CLI transcript in place of a cut run. Phrased as a system
# note so a resumed model reads it as an event, not as its own words to continue.
DEGENERATION_TRANSCRIPT_MARKER = (
    "[system: output stopped — repetition loop detected and removed / 输出陷入重复已中止]"
)

# Run-edge search. A window can only reach the threshold when the loop fills
# most of it, so the last ``_CORE`` chars of the first degenerate window lie
# inside the run; that core is the reference the edges are grown against.
_CORE = 1000
_MAX_PERIOD = _CORE // 3
# Edges grow in _EDGE_CHUNK-char steps while a chunk compresses well AGAINST the
# core (zlib preset dictionary): a piece of the same loop — even a drifting one —
# is mostly back-references; ordinary text is not. Raw deflate (no header) keeps
# the per-chunk overhead small enough for 64-char chunks.
_EDGE_CHUNK = 64
_EDGE_RATIO = 3.0
# An accepted edge chunk may still begin with ordinary text (a chunk that is
# mostly loop compresses well as a whole); it is trimmed in sub-chunks of this
# size so the cut never takes more than this many legit chars.
_EDGE_SUB = 16


def compress_ratio(text: str) -> float:
    """Return len(utf-8 bytes) / len(zlib-compressed bytes); 0.0 for empty."""
    raw = text.encode("utf-8", errors="replace")
    if not raw:
        return 0.0
    return len(raw) / max(1, len(zlib.compress(raw, 6)))


def _is_degenerate(window: str) -> bool:
    return compress_ratio(window) >= RATIO_THRESHOLD


class DegenerationDetector:
    """Incremental detector for one streamed content block.

    ``feed(chunk)`` returns True once the block is judged degenerate: the last
    ``WINDOW`` characters are checked each time another ``STEP`` characters have
    arrived (never before ``WINDOW`` characters exist — a single long separator
    line must not be mistaken for a loop). A check joins only the trailing
    chunks that cover the window, so it costs O(WINDOW) however long the block
    grows; the full text is joined only when the caller reads ``text`` (to cut
    the block at the start of the run). One check is one zlib pass over at most
    ~8KB (about 20µs).
    """

    __slots__ = ("_parts", "_len", "_next_check", "fired", "name", "kind")

    def __init__(self, kind: str = "text", name: str = "") -> None:
        self._parts: list[str] = []
        self._len = 0
        self._next_check = WINDOW
        self.fired = False
        self.kind = kind  # content-block type: "text", "tool_use", or another
        self.name = name  # tool name for tool_use blocks

    @property
    def text(self) -> str:
        if len(self._parts) > 1:
            self._parts = ["".join(self._parts)]
        return self._parts[0] if self._parts else ""

    def _tail(self) -> str:
        got, picked = 0, []
        for part in reversed(self._parts):
            picked.append(part)
            got += len(part)
            if got >= WINDOW:
                break
        return "".join(reversed(picked))[-WINDOW:]

    def feed(self, chunk: str) -> bool:
        if self.fired or not chunk:
            return self.fired
        self._parts.append(chunk)
        self._len += len(chunk)
        if self._len < self._next_check:
            return False
        while self._next_check <= self._len:
            self._next_check += STEP
        if _is_degenerate(self._tail()):
            self.fired = True
        return self.fired


def _first_degenerate_window_end(text: str, start: int = 0) -> Optional[int]:
    """End index of the first degenerate window, checked like the live stream
    (window ends at WINDOW, WINDOW+STEP, …) plus the final tail window."""
    n = len(text)
    if n - start < WINDOW:
        return None
    end = start + WINDOW
    while end <= n:
        if _is_degenerate(text[end - WINDOW:end]):
            return end
        end += STEP
    if _is_degenerate(text[n - WINDOW:n]):
        return n
    return None


def _smallest_period(s: str) -> Optional[int]:
    for p in range(1, min(_MAX_PERIOD, len(s) // 2) + 1):
        if s[p:] == s[:-p]:
            return p
    return None


def _matches_core(chunk: str, core_bytes: bytes) -> bool:
    raw = chunk.encode("utf-8", errors="replace")
    co = zlib.compressobj(6, zlib.DEFLATED, -15, zdict=core_bytes)
    packed = co.compress(raw) + co.flush()
    return len(raw) / max(1, len(packed)) >= _EDGE_RATIO


def find_degenerate_run(text: str, start: int = 0) -> Optional[tuple[int, int]]:
    """Return ``(start, end)`` of the first repetition run at or after
    ``start``, or None.

    1. Locate the first degenerate window on the live-stream schedule.
    2. Its last ``_CORE`` chars are inside the run (the "core").
    3. Grow both edges, alternating until neither moves:
       - exact: char by char while the core's exact period continues — this
         puts the edge on the precise character for a clean loop;
       - drift: one ``_EDGE_CHUNK`` step while the next chunk compresses well
         against the core — this follows a loop that varies slightly
         (``काए`` with an occasional ``का``) without taking ordinary text.
       Exact runs first so a chunk step never starts from inside the loop
       and straddles its edge.
    The run is always at least ``_CORE`` chars long.
    """
    w_end = _first_degenerate_window_end(text, start)
    if w_end is None:
        return None
    n = len(text)
    s = max(start, w_end - _CORE)
    e = w_end
    core = text[s:e]
    core_bytes = core.encode("utf-8", errors="replace")
    p = _smallest_period(core)
    while True:
        s0, e0 = s, e
        if p is not None:
            while s > start and text[s - 1] == text[s - 1 + p]:
                s -= 1
            while e < n and text[e] == text[e - p]:
                e += 1
        lo = max(start, s - _EDGE_CHUNK)
        if s > start and _matches_core(text[lo:s], core_bytes):
            while lo + _EDGE_SUB < s and not _matches_core(text[lo:lo + _EDGE_SUB], core_bytes):
                lo += _EDGE_SUB
            s = lo
        hi = min(n, e + _EDGE_CHUNK)
        if e < n and _matches_core(text[e:hi], core_bytes):
            while hi - _EDGE_SUB > e and not _matches_core(text[hi - _EDGE_SUB:hi], core_bytes):
                hi -= _EDGE_SUB
            e = hi
        if (s, e) == (s0, e0):
            return s, e


def strip_degenerate_runs(text: str, marker: str) -> str:
    """Replace every repetition run in ``text`` with ``marker``.

    Returns ``text`` itself (same object) when nothing is degenerate, so a
    caller can test identity to see whether anything changed. Each scan
    resumes where the previous run ended and the pieces are joined once, so
    the pass is linear in ``len(text)``.
    """
    pieces: list[str] = []
    pos = 0
    while True:
        run = find_degenerate_run(text, pos)
        if run is None:
            break
        s, e = run
        pieces.append(text[pos:s])
        pieces.append(marker)
        pos = e
    if not pieces:
        return text
    pieces.append(text[pos:])
    return "".join(pieces)


def strip_degenerate_in_value(value: Any, marker: str) -> Any:
    """Recursively strip runs from every string inside a tool-input value.

    Returns the original object when nothing changed (no copy); otherwise a
    new container — the input is never mutated.
    """
    if isinstance(value, str):
        return strip_degenerate_runs(value, marker) if len(value) >= WINDOW else value
    if isinstance(value, dict):
        changed = False
        out = {}
        for k, v in value.items():
            nv = strip_degenerate_in_value(v, marker)
            changed = changed or nv is not v
            out[k] = nv
        return out if changed else value
    if isinstance(value, list):
        items = [strip_degenerate_in_value(v, marker) for v in value]
        if any(a is not b for a, b in zip(items, value)):
            return items
        return value
    return value


class ProgressWatch:
    """Track visible progress within one response stream (clock injected).

    ``check()`` returns at most one notice of each kind per no-output episode:
    - thinking stall: no visible output for ``THINKING_STALL_S`` while
      ``system/thinking_tokens`` frames are still arriving;
    - silence: no SDK event at all for ``SILENT_STALL_S``.
    Nothing is reported while a tool is running (a long tool is not a stall),
    and any visible output (text, tool input, tool result) re-arms both.
    """

    def __init__(self, now: float) -> None:
        self._last_visible = now
        self._last_event = now
        self._last_thinking: Optional[float] = None
        self._thinking_tokens = 0
        self._notified: set[str] = set()

    def on_event(self, now: float) -> None:
        self._last_event = now
        self._notified.discard("silent")  # events flow again; a new silence may be reported

    def on_visible(self, now: float) -> None:
        self._last_visible = now
        self._last_event = now
        self._thinking_tokens = 0
        self._notified.clear()

    def on_thinking_tokens(self, now: float, delta: int) -> None:
        self._last_thinking = now
        self._thinking_tokens += max(0, int(delta or 0))

    def _text(self, kind: str, now: float) -> str:
        if kind == "thinking":
            minutes = max(1, int((now - self._last_visible) // 60))
            return (
                f"⏳ 模型已连续思考约 {minutes} 分钟(约 {self._thinking_tokens} thinking tokens)"
                "仍未产生任何输出,可能陷入思考空转;可以继续等待,或停止后重新发送。"
                f"(Model has been thinking for ~{minutes} min with no visible output.)"
            )
        minutes = max(1, int((now - self._last_event) // 60))
        return (
            f"⏳ 已约 {minutes} 分钟没有收到模型的任何事件(无 thinking、无输出),"
            "可能卡住;可以继续等待,或停止后重新发送。"
            f"(No model events for ~{minutes} min.)"
        )

    def check(self, now: float, *, tools_open: bool) -> Optional[str]:
        """Return a NEW notice (at most once per kind per episode), else None."""
        if tools_open:
            return None
        idle = now - self._last_visible
        thinking_fresh = (
            self._last_thinking is not None
            and self._last_thinking > self._last_visible
            and now - self._last_thinking < _THINKING_FRESH_S
        )
        if idle >= THINKING_STALL_S and thinking_fresh and "thinking" not in self._notified:
            self._notified.add("thinking")
            return self._text("thinking", now)
        if now - self._last_event >= SILENT_STALL_S and "silent" not in self._notified:
            self._notified.add("silent")
            return self._text("silent", now)
        return None

    def stall_note(self, now: float) -> Optional[str]:
        """Refreshed text of the stall already reported in this episode (the
        latest kind), or None once visible output has resumed."""
        if "silent" in self._notified and now - self._last_event >= SILENT_STALL_S:
            return self._text("silent", now)
        if "thinking" in self._notified:
            return self._text("thinking", now)
        return None
