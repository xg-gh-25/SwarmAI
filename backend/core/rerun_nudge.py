"""rerun_nudge — when a run is repeated after its output was cut by a filter, say so.

Why this exists
---------------
Measured on one install's main sessions over 7 days: about one test run in seven
repeated the same scope with no file change in between. Sampled cases had one cause:
the first run's whole output was not kept. It was piped through ``tail`` / ``grep`` /
``head``, so the next question about that run could only be answered by running it
again. AGENT § Tests asks for the whole output in a file; this hook is the reminder at
the moment the waste shows: a slow command runs again, and its earlier run sent its
output through such a filter.

The pattern follows Anthropic's harness guidance of counting in the harness and
appending a short reminder after the tool result when a condition is met
(https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5,
"User-facing progress updates", fourth lever).

What it does NOT claim
----------------------
It never says the earlier result still holds or that the run was unnecessary: whether
anything changed between the two runs is not knowable from here (a failed command, a
multi-line script, a sub-agent, the user's editor can all write unseen). The reminder
states only what the hook saw, that the earlier output was filtered, and how to keep it
next time. A legitimate re-run after a fix gets the same reminder, and the advice holds
there too.

What counts as "the same command"
---------------------------------
The working directory plus the command with its output handling removed: segments
split on ``&&`` / ``||`` / ``;`` / newlines; in each, trailing pipe stages that only
filter output (``_FILTERS``) are dropped (a ``tee <file>`` before them means the
output was kept, not cut), redirects of the remaining command are dropped, a heredoc body
becomes a short hash of itself, ``echo`` / ``printf`` segments are dropped, and a wall-clock cap's seconds
(``alarm 100``) are ignored. A pipe stage that does work (``| xargs mypy``,
``| python a.py``) stays in the key. Only runs that took at least ``_MIN_SECONDS`` and
finished are recorded: a run that was interrupted, hit its wall-clock cap or was moved
to the background returned no whole result.

Failed runs reach hooks through PostToolUseFailure (a non-zero exit), which is when
filtered output hurts most, so the factory serves both events with one shared state.

Inject-only: never denies, never blocks, returns {} on any error. Matches no language
or test framework (SOUL P7): it reads shell syntax and duration only.
"""

from __future__ import annotations

import logging
import re
import zlib
from typing import Any

logger = logging.getLogger(__name__)

NUDGE = (
    "[Repeat run] This command ran earlier in this session and its output went through a "
    "filter (tail/head/grep), so the rest of it was lost. Run a slow command once with the "
    "whole output in a file (`> <log> 2>&1; echo exit=$?`) and read the file for each "
    "question, so a question about the output does not cost another run."
)

_MIN_SECONDS = 2.0
_STATE_KEY = "_rerun_nudge"

# Pipe stages that only cut or reshape output: the run behind them is the same run.
_FILTERS = re.compile(r"^(tail|head|grep|egrep|fgrep|rg|wc|sort|uniq|cut)\b")
_TIMED_OUT = re.compile(r"^Alarm clock|moved to the background", re.M)
_BACKGROUNDED = ("backgroundTaskId", "timedOutAfterMs", "backgroundedByUser",
                 "backgroundedToDeliverMessage", "backgroundedByTurnAbort")
_SEGMENT = re.compile(r"&&|\|\||;|\n")
_PIPE = re.compile(r"(?<!\|)\|&?(?!\|)")
_REDIRECT = re.compile(r"&>>?\s*\S+|[0-9]?>>?\s*\S+")
# The opener line stays (it can carry `| tail` after the marker); the body up to the
# end marker on a line of its own is replaced by a short hash of it.
_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n(.*?)\n[ \t]*\1[ \t]*(?=\n|$)", re.S)


def _blank_quotes(cmd: str) -> str:
    """Replace quoted spans with x's of the same length, so operators inside quotes
    are not matched while positions still line up with the original text."""
    return re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", lambda m: "x" * len(m.group(0)), cmd)


def _split_unquoted(text: str, pattern: re.Pattern) -> list[str]:
    parts, start = [], 0
    for m in pattern.finditer(_blank_quotes(text)):
        parts.append(text[start:m.start()])
        start = m.end()
    parts.append(text[start:])
    return parts


def _strip_redirects(text: str) -> str:
    out, pos = [], 0
    for m in _REDIRECT.finditer(_blank_quotes(text)):
        out.append(text[pos:m.start()])
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def parse_run(command: str) -> tuple[str, bool]:
    """(key, filtered): the command without its output handling, and whether its
    output went through a filter stage instead of being kept whole."""
    body = _HEREDOC.sub(
        lambda m: f"<<heredoc:{zlib.crc32(m.group(3).encode()):08x}{m.group(2)}", command)
    kept, filtered = [], False
    for seg in _split_unquoted(body, _SEGMENT):
        seg = seg.strip()
        if not seg or re.match(r"^(echo|printf)\b", seg):
            continue
        stages = [s.strip() for s in _split_unquoted(seg, _PIPE)]
        cut = False
        while len(stages) > 1 and _FILTERS.match(stages[-1]):
            stages.pop()
            cut = True
        if cut and not any(re.match(r"^tee\b", st) for st in stages[1:]):
            filtered = True  # a `tee <file>` before the filter keeps the whole output
        stages[0] = _strip_redirects(stages[0])
        seg = " | ".join(s for s in stages if s.strip())
        seg = " ".join(re.sub(r"\balarm \d+", "alarm N", seg).split())
        if seg:
            kept.append(seg)
    return " && ".join(kept), filtered


def _finished(input_data: dict) -> bool:
    if input_data.get("is_interrupt"):
        return False
    response = input_data.get("tool_response")
    if isinstance(response, dict):
        if response.get("interrupted") or any(response.get(k) for k in _BACKGROUNDED):
            return False
        text = f"{response.get('stdout', '')}\n{response.get('stderr', '')}"
    else:
        text = str(response or "")
    text += "\n" + str(input_data.get("error") or "")
    return not _TIMED_OUT.search(text)


def create_rerun_nudge_hook(session_context: "dict[str, Any]", event: str = "PostToolUse"):
    """Factory for the Bash hook on *event* (PostToolUse or PostToolUseFailure). Both
    instances share one state in *session_context*. Never denies."""

    async def rerun_nudge(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        try:
            if not isinstance(input_data, dict) or input_data.get("tool_name") != "Bash":
                return {}
            command = (input_data.get("tool_input") or {}).get("command") or ""
            key, filtered = parse_run(command)
            if not key:
                return {}
            key = f"{input_data.get('cwd') or ''}\0{key}"
            runs = session_context.setdefault(_STATE_KEY, {})
            earlier_filtered = runs.get(key)
            seconds = (input_data.get("duration_ms") or 0) / 1000
            if seconds >= _MIN_SECONDS and _finished(input_data):
                runs[key] = filtered
            # Only while the habit persists: a repeat that already keeps its output
            # whole needs no reminder.
            if earlier_filtered and filtered and seconds >= _MIN_SECONDS:
                return {"hookSpecificOutput": {"hookEventName": event,
                                               "additionalContext": NUDGE}}
            return {}
        except Exception:  # noqa: BLE001 — inject-only: an error means no reminder
            logger.debug("rerun_nudge failed open", exc_info=True)
            return {}

    return rerun_nudge
