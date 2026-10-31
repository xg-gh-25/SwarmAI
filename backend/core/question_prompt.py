"""A question the agent asked (AskUserQuestion), stored with its turn and its answer.

Design: docs/design/chat-transcript-single-source.md, R2. The question used to
live only in the live stream: a reload lost the card and the answer given. Now,
like a permission prompt (core/permission_prompt):

- ``question_block(event)`` — the block stored for an ``ask_user_question``
  event (``SessionRouter._persist_stream_event`` adds it to the turn's row, in
  the shape the live card has: ``{type, toolUseId, questions}``);
- ``record_answer(session_id, tool_use_id, answer_json, answered_by)`` — the answers (and who gave them) onto that
  block (``answers``: the dict the card shows), before the turn continues. The
  write reaches other tabs on the transcript feed (core/transcript_feed).

The block is display only: it is kept out of the cold-resume history
(context_injector._TOOL_ONLY_TYPES); the model has the question in its own
transcript.

Public: ``question_block``, ``record_answer``, ``BLOCK_TYPE``.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

BLOCK_TYPE = "ask_user_question"


def question_block(event: dict) -> Optional[dict]:
    tid = event.get("toolUseId")
    questions = event.get("questions")
    if not isinstance(tid, str) or not tid or not isinstance(questions, list):
        return None
    return {"type": BLOCK_TYPE, "toolUseId": tid, "questions": questions}


async def record_answer(session_id: str, tool_use_id: str, answer_json: str,
                        answered_by: Optional[str] = None) -> bool:
    """Write the answers (the JSON the answer endpoint sends on) onto the block, with who
    gave them (``person`` / ``host``; the Swarm Out result counts it)."""
    try:
        answers = json.loads(answer_json) if answer_json else None
    except (TypeError, ValueError):
        answers = None
    if not isinstance(answers, dict) or not answers:
        return False
    try:
        from database import db
        return await db.messages.update_question_answers(
            session_id, tool_use_id, {str(k): str(v) for k, v in answers.items()}, answered_by)
    except Exception as exc:  # noqa: BLE001 — a display record never fails the answer
        logger.warning("question_prompt.record_failed session=%s tool_use=%s: %s",
                       session_id, tool_use_id, exc)
        return False
