"""Human-like response formatting for channel messages.

Splits long responses into naturally-sized segments that feel like
a person typing multiple messages, not a terminal dump.

Key exports:
    HumanResponseFormatter — splits responses into message segments
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# HumanResponseFormatter
# ---------------------------------------------------------------------------

@dataclass
class HumanResponseFormatter:
    """Format agent output as human-like message segments.

    Strategy:
    - Short (<2000 chars): single message
    - Medium (2000-6000): conclusion + detail (2 messages)
    - Long (>6000): conclusion + N detail messages

    Splits on natural boundaries (paragraphs, headers, list breaks).
    Never splits mid-sentence or mid-code-block.
    """
    max_single_msg: int = 2000
    max_segment: int = 3000  # max chars per follow-up segment
    inter_message_delay: float = 1.0  # seconds between segments

    def format(self, raw_response: str) -> list[str]:
        """Split raw response into message segments.

        Returns a list of strings, each to be posted as a separate message.
        Minimum 1 segment (even for empty input).
        """
        if not raw_response or not raw_response.strip():
            return ["(No response)"]

        text = raw_response.strip()

        # Short — single message
        if len(text) <= self.max_single_msg:
            return [text]

        # Split on natural boundaries
        segments = self._split_on_boundaries(text)
        return segments

    def _split_on_boundaries(self, text: str) -> list[str]:
        """Split text on paragraph/header boundaries.

        Priority:
        1. Double newline (paragraph break)
        2. Markdown headers (## ...)
        3. Numbered/bulleted list items at top level

        Never splits inside a code block.
        """
        # Identify code blocks to protect them
        code_blocks: list[tuple[int, int]] = []
        for m in re.finditer(r'```[\s\S]*?```', text):
            code_blocks.append((m.start(), m.end()))

        def in_code_block(pos: int) -> bool:
            return any(start <= pos < end for start, end in code_blocks)

        # Find all valid split points
        split_points: list[int] = []

        # Double newlines
        for m in re.finditer(r'\n\n+', text):
            if not in_code_block(m.start()):
                split_points.append(m.start())

        # Markdown headers (lines starting with ##)
        for m in re.finditer(r'\n(?=#{1,3}\s)', text):
            if not in_code_block(m.start()):
                split_points.append(m.start())

        split_points = sorted(set(split_points))

        if not split_points:
            # No natural split — just return as-is
            return [text]

        # Build segments respecting max_segment
        segments: list[str] = []
        current_start = 0

        for sp in split_points:
            chunk = text[current_start:sp].strip()
            if not chunk:
                continue

            if segments and len(segments[-1]) + len(chunk) + 2 <= self.max_segment:
                # Merge with previous segment if it fits
                segments[-1] += "\n\n" + chunk
            else:
                segments.append(chunk)
            current_start = sp

        # Don't forget the tail
        tail = text[current_start:].strip()
        if tail:
            if segments and len(segments[-1]) + len(tail) + 2 <= self.max_segment:
                segments[-1] += "\n\n" + tail
            else:
                segments.append(tail)

        # Ensure we have at least one segment
        if not segments:
            return [text]

        return segments
