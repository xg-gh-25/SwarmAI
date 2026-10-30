"""Output rate of the main model over one turn — how fast it writes once it has started.

WHY: TTFT measures the wait before the model's first output. What follows is the
model writing: thinking, text and tool-call input, one API call per step of the turn.
Nothing measured that, so "is streaming slow?" could not be answered past the first
token. This meter answers it from the stream the CLI already relays.

HOW: per API call of the main agent, the stream carries ``message_start`` →
``content_block_start`` … ``message_delta`` (its ``usage.output_tokens`` is the
call's running output count) → ``message_stop``. The writing time of one call runs
from its first ``content_block_start`` to its ``message_stop``; the turn's rate is
all output tokens over all writing time. Sub-agent calls are not fed (they run
alongside the main agent, so their time would overlap). A call whose stop never
arrives (interrupt, error) is left out: it has no complete writing time.

Durations come from the caller's clock (the orchestrator passes ``awake_time()``,
which leaves out time the machine slept). Observability only: nothing here decides
anything.
"""
from __future__ import annotations

from typing import Optional


class StreamRateMeter:
    """Accumulates the main agent's output tokens and writing time for one turn."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.calls = 0
        self.output_tokens = 0
        self.writing_s = 0.0
        self._first_block: Optional[float] = None
        self._call_tokens = 0
        self._open = False

    def message_start(self) -> None:
        self._open = True
        self._first_block = None
        self._call_tokens = 0

    def block_start(self, now: float) -> None:
        if self._open and self._first_block is None:
            self._first_block = now

    def output_so_far(self, tokens: object) -> None:
        if self._open and isinstance(tokens, int) and tokens >= 0:
            self._call_tokens = tokens

    def message_stop(self, now: float) -> None:
        if self._first_block is not None:  # only an open call sets it (block_start)
            self.calls += 1
            self.output_tokens += self._call_tokens
            self.writing_s += now - self._first_block
        self._open = False
        self._first_block = None
        self._call_tokens = 0

    def log_line(self) -> Optional[str]:
        """``stream_rate …`` for the log, or None when no call completed."""
        if not self.calls:
            return None
        rate = self.output_tokens / self.writing_s if self.writing_s > 0 else 0.0
        return (f"stream_rate calls={self.calls} out_tokens={self.output_tokens} "
                f"writing_s={self.writing_s:.1f} tok_per_s={rate:.1f}")
