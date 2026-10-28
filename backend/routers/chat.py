"""Chat SSE streaming API and chat-thread management endpoints.

This module provides two routers:

- ``router``              — SSE streaming endpoints for agent chat, session
  management, and permission handling (mounted at ``/api/chat``).
- ``chat_threads_router`` — CRUD and binding endpoints for ChatThread
  entities, including project-filtered listing, global thread listing,
  and mid-session thread binding (mounted at ``/api``).

Key endpoints on ``chat_threads_router``:

- ``GET  /api/projects/{project_id}/threads``   — list threads by project
- ``GET  /api/threads/global``                  — list global (unassociated) threads
- ``POST /api/chat_threads/{thread_id}/bind``   — mid-session thread binding

Content validation helpers (multimodal attachment safety net):

- ``validate_content``       — Enforces block count (20) and payload size (25 MB) limits
- ``_estimate_block_size``   — Estimates byte size of a single content block

Requirements: 26.1, 26.4, 26.5, 35.1, 35.6, 8.1, 8.2, 8.3, 8.4, 8.5, 10.1, 10.2, 10.3, 10.4
"""
from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from schemas.message import ChatRequest, ChatSessionResponse, AnswerQuestionRequest
from schemas.chat_thread import ChatThreadResponse
from schemas.context import ThreadBindRequest, ThreadBindResponse
from schemas.permission import PermissionResponseRequest
from database import db
from core.agent_defaults import agent_exists
from core.session_utils import _build_error_event
from core.permission_manager import permission_manager as _pm
from core.chat_thread_manager import chat_thread_manager
from core.session_manager import session_manager
from core import surface_injection
from core import session_events

# ── Multi-session architecture ────────────────────────────────────
import logging as _logging
from typing import Optional

_chat_logger = _logging.getLogger(__name__)

# Lazy-initialized singletons — resolved on first chat request
_session_router = None
_lifecycle_manager = None


def _get_router():
    """Get or create the SessionRouter singleton."""
    global _session_router, _lifecycle_manager
    if _session_router is None:
        from core import session_registry
        if session_registry.session_router is None:
            from core.app_config_manager import AppConfigManager
            config = AppConfigManager()
            config.load()
            session_registry.initialize(config)
        _session_router = session_registry.session_router
        _lifecycle_manager = session_registry.lifecycle_manager
    return _session_router

from core.exceptions import (
    AgentNotFoundException,
    SessionNotFoundException,
    ValidationException,
    ResourceExhaustedException,
)
import json
import asyncio
import logging
import time
from typing import AsyncIterator, List, Optional

logger = logging.getLogger(__name__)

router = APIRouter()
chat_threads_router = APIRouter()

# SSE heartbeat interval in seconds (keeps connection alive during long operations)
SSE_HEARTBEAT_INTERVAL = 15

#: consumer tasks of turns whose client went away (strong refs until they end)
_DETACHED_TURNS: set = set()

# ---------------------------------------------------------------------------
# Content validation constants and helpers
# ---------------------------------------------------------------------------

MAX_CONTENT_BLOCKS = 20
MAX_TOTAL_PAYLOAD_SIZE = 32 * 1024 * 1024  # 32MB — matches Bedrock payload limit for Claude 4+


def _estimate_block_size(block: dict) -> int:
    """Estimate the wire size of a content block in bytes.

    For base64 blocks (image/document): returns ``len(data)`` which is the
    base64-encoded string length — already ~4/3× the raw file size.  This is
    the actual size that will appear in the JSON payload on the wire.

    For text blocks: UTF-8 encoded length of the text content.
    """
    block_type = block.get("type")
    if block_type in ("image", "document"):
        data = block.get("source", {}).get("data", "")
        return len(data)
    elif block_type == "text":
        return len(block.get("text", "").encode("utf-8"))
    return 0


def _human_size(n: int) -> str:
    """Format byte count as human-readable string (e.g. '15.2 MB')."""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    else:
        return f"{n / (1024 * 1024):.1f} MB"


def validate_content(content: list[dict]) -> list[dict]:
    """Validate content blocks before forwarding to SDK.

    Raises HTTPException(413) with user-friendly messages if limits exceeded.
    """
    if len(content) > MAX_CONTENT_BLOCKS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Too many attachments ({len(content)} files). "
                f"Maximum is {MAX_CONTENT_BLOCKS} per message. "
                f"Try sending fewer files at a time."
            ),
        )

    total_size = sum(_estimate_block_size(block) for block in content)
    if total_size > MAX_TOTAL_PAYLOAD_SIZE:
        limit_mb = MAX_TOTAL_PAYLOAD_SIZE / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=(
                f"Attachments too large ({_human_size(total_size)} total). "
                f"Maximum is {limit_mb:.0f} MB per message. "
                f"Try attaching fewer or smaller files, or send them in separate messages."
            ),
        )

    return content


def create_sse_error(code: str, message: str, detail: str = None, suggested_action: str = None) -> str:
    """Create an SSE-formatted error event."""
    error_data = {
        "type": "error",
        "code": code,
        "message": message,
        "error": message,
    }
    if detail:
        error_data["detail"] = detail
    if suggested_action:
        error_data["suggested_action"] = suggested_action
    return f"data: {json.dumps(error_data)}\n\n"


def create_sse_heartbeat() -> str:
    """Create an SSE heartbeat message to keep the connection alive."""
    return f"data: {json.dumps({'type': 'heartbeat', 'timestamp': time.time()})}\n\n"


async def sse_with_heartbeat(
    message_generator: AsyncIterator[dict],
    heartbeat_interval: int = SSE_HEARTBEAT_INTERVAL,
    request: Optional[Request] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> AsyncIterator[str]:
    """Wrap an async message generator with heartbeat support.

    Sends heartbeat messages at regular intervals when no data is being sent,
    keeping the SSE connection alive during long operations.

    The generator is a TURN and belongs to its session, not to this response:
    when the client goes away (disconnect, the session's stop event, an
    unexpected stream error) the consumer task is DETACHED, never cancelled. It
    keeps driving the turn to its own end (a result, an error, or an interrupt
    from /chat/stop) and stops queueing what nobody will read; it is held in
    ``_DETACHED_TURNS`` until it finishes. Subscribed tabs keep receiving the
    turn from the session event hub (core/session_events). Before this, the
    disconnect cancelled the consumer and interrupted the CLI, and a synthetic
    "continue" turn re-drove it.

    During extended thinking (thinking_start received, no text_start/text_delta
    yet), emits ``thinking_progress`` events instead of plain heartbeats so the
    frontend can show elapsed time and suppress "session stalled" warnings.

    Args:
        message_generator: The async generator that yields message dicts
        heartbeat_interval: Seconds between heartbeats (default: 15)

    Yields:
        SSE-formatted strings (data messages and heartbeats)
    """
    message_queue: asyncio.Queue = asyncio.Queue()
    generator_done = False
    detached = False

    # ── Thinking phase tracking ────────────────────────────────────────
    # When the SDK is in extended thinking, heartbeat slots emit
    # thinking_progress events so the frontend knows the session is alive
    # even when no thinking_delta arrives (content redacted by API).
    thinking_active = False
    thinking_start_time: float = 0.0

    async def consume_messages():
        """Consume messages from the generator and put them in the queue."""
        nonlocal generator_done
        try:
            async for msg in message_generator:
                if not detached:
                    await message_queue.put(("message", msg))
        except Exception as e:
            if not detached:
                await message_queue.put(("error", e))
            else:
                logger.warning("Detached turn ended with an error: %s", e)
        finally:
            # Signal completion by putting a sentinel value
            if not detached:
                await message_queue.put(("done", None))
            generator_done = True

    # Start consuming messages in the background
    consumer_task = asyncio.create_task(consume_messages())

    try:
        while True:
            try:
                # Wait for a message with timeout for heartbeat
                item_type, item = await asyncio.wait_for(
                    message_queue.get(),
                    timeout=heartbeat_interval
                )

                if item_type == "done":
                    # Generator finished — send explicit [DONE] sentinel
                    # so the frontend doesn't rely on HTTP stream close
                    # (which may be delayed by buffering in uvicorn/OS/webview).
                    yield "data: [DONE]\n\n"
                    break
                elif item_type == "message":
                    try:
                        yield f"data: {json.dumps(item)}\n\n"
                    except (TypeError, ValueError) as json_err:
                        logger.warning("SSE json.dumps failed: %s", json_err)
                        yield create_sse_error("SERIALIZATION_ERROR", str(json_err))
                        continue

                    # ── Track thinking phase for liveness events ────────
                    msg_type = item.get("type", "") if isinstance(item, dict) else ""
                    if msg_type == "thinking_start":
                        thinking_active = True
                        thinking_start_time = time.time()
                    elif msg_type in ("text_start", "text_delta", "result",
                                      "assistant", "content_block_stop"):
                        # Thinking phase ends when text output begins or turn completes
                        if thinking_active and msg_type != "content_block_stop":
                            thinking_active = False
                        # content_block_stop for the thinking block itself — check index
                        # For simplicity: any text-producing event ends thinking
                        if msg_type in ("text_start", "text_delta", "result", "assistant"):
                            thinking_active = False
                elif item_type == "error":
                    raise item

            except asyncio.TimeoutError:
                # No message received within heartbeat interval, send heartbeat
                if not generator_done:
                    # Check if session was stopped
                    if stop_event is not None and stop_event.is_set():
                        logger.info("SSE stop event received, ending stream")
                        break
                    # Check if client disconnected
                    if request is not None:
                        try:
                            if await request.is_disconnected():
                                logger.info("SSE client disconnected, stopping stream")
                                break
                        except Exception:
                            pass  # is_disconnected() can fail — don't break the loop

                    # During thinking: emit thinking_progress (frontend shows elapsed
                    # timer and suppresses stall warning). Otherwise: plain heartbeat.
                    if thinking_active:
                        elapsed = int(time.time() - thinking_start_time)
                        progress_event = {
                            "type": "thinking_progress",
                            "elapsed_seconds": elapsed,
                            "timestamp": time.time(),
                        }
                        yield f"data: {json.dumps(progress_event)}\n\n"
                    else:
                        # INFO (was DEBUG) — run_69198f8c observability: a 15s SSE
                        # heartbeat is naturally throttled (fires at most every
                        # SSE_HEARTBEAT_INTERVAL), so INFO does not flood. Making it
                        # visible lets a high-TTFT window show the heartbeat cadence
                        # or its ABSENCE — the latter (paired with a heartbeat.py
                        # loop_age spike) proves event-loop starvation, vs heartbeats
                        # logged-as-sent while the client still stalls (buffering).
                        logger.info("Sending SSE heartbeat")
                        yield create_sse_heartbeat()
            except Exception as unexpected_err:
                # Catch-all: send structured error to client before closing
                logger.error("Unexpected SSE stream error: %s", unexpected_err, exc_info=True)
                try:
                    yield create_sse_error("STREAM_ERROR", str(unexpected_err))
                except Exception:
                    pass  # Last resort — can't even send error, just close
                break
    finally:
        # The client is gone (or the stream ended): a running turn is detached,
        # not cancelled — see the docstring.
        if not consumer_task.done():
            detached = True
            while not message_queue.empty():
                message_queue.get_nowait()
            _DETACHED_TURNS.add(consumer_task)
            consumer_task.add_done_callback(_DETACHED_TURNS.discard)


@router.post("/transcribe")
async def transcribe_voice(request: Request):
    """Transcribe uploaded audio to text via Amazon Transcribe Streaming.

    Accepts multipart form data with an ``audio`` file field.
    Returns JSON: ``{"transcript": str, "language": str, "duration_ms": int}``
    """
    from starlette.datastructures import UploadFile as StarletteUploadFile

    form = await request.form()
    try:
        audio_field = form.get("audio")

        # Validate: must be a file upload, not a text field or missing
        if not isinstance(audio_field, StarletteUploadFile):
            raise HTTPException(status_code=400, detail="audio field must be a file upload")

        audio_data = await audio_field.read()
        if not audio_data:
            raise HTTPException(status_code=400, detail="Empty audio file")

        language = form.get("language")  # optional string or None

        from core.voice_transcribe import transcribe_audio
        result = await transcribe_audio(
            audio_data,
            language=language if isinstance(language, str) else None,
        )
        return result
    except HTTPException:
        raise
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _logging.getLogger(__name__).error(
            "Voice transcription failed: %s", e, exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Transcription service error")
    finally:
        await form.close()  # Release SpooledTemporaryFile backing the upload


async def _record_task_repos(session_id: str, repos: list) -> None:
    """Write a dispatched task's repositories into its new session's metadata
    (``task_repos``), read at every turn end (SessionRouter._task_repo_facts). A
    failure is logged: the task still runs, its outcome then names no repository."""
    try:
        row = await db.sessions.get(session_id)
        if row is None:
            logger.error("task_repos: session %s not found to record its repositories", session_id)
            return
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        await db.sessions.update(session_id, {"metadata": {**meta, "task_repos": repos}})
    except Exception as exc:  # noqa: BLE001 — never breaks the turn
        logger.error("task_repos: recording failed for session %s: %s", session_id, exc)


@router.post("/stream")
async def chat_stream(request: Request):
    """Stream chat responses via SSE."""
    try:
        body = await request.json()
        chat_request = ChatRequest(**body)
    except json.JSONDecodeError as e:
        raise ValidationException(
            message="Invalid JSON format",
            detail=f"Failed to parse request body: {str(e)}",
        )
    except Exception as e:
        raise ValidationException(
            message="Invalid request data",
            detail=str(e),
        )

    # Validate multimodal content blocks if present
    if chat_request.content:
        validate_content(chat_request.content)

    # Swarm Out: the task's repositories, validated before the turn starts and
    # recorded on the session once its id is known (core/task_repos). A request for an
    # existing session does not change what that session declared.
    task_repos: list = []
    if chat_request.task_repos and chat_request.session_id is None:
        from core import task_repos as _task_repos
        try:
            task_repos = _task_repos.parse(chat_request.task_repos)
        except _task_repos.TaskRepoError as e:
            raise ValidationException(message="Invalid task repositories", detail=str(e))

    # Project Agent (design 2026-10-28-project-agent-flow §8): a new session that works
    # under a Brain's Project Agent is pinned before the turn starts, so its hooks
    # enforce the definition from the first tool call.
    project_agent_pin = None
    if chat_request.project_agent and chat_request.session_id is None:
        from core.project_agent_session import PinError, make_pin
        from core.project_registry import get_swarmws
        pa = chat_request.project_agent
        try:
            project_agent_pin = await asyncio.to_thread(
                make_pin, get_swarmws(), str(pa.get("brain") or ""),
                pa.get("mode") if isinstance(pa.get("mode"), str) else None,
                pa.get("revision") if isinstance(pa.get("revision"), str) else None)
        except PinError as e:
            raise ValidationException(message="Invalid Project Agent", detail=str(e))

    _get_router()  # Ensure session infrastructure initialized

    # Verify agent exists (lightweight check — no config assembly)
    if not await agent_exists(chat_request.agent_id):
        raise AgentNotFoundException(
            detail=f"Agent with ID '{chat_request.agent_id}' does not exist",
            suggested_action="Please check the agent ID and try again"
        )

    async def message_generator():
        """Generate messages from the agent conversation."""
        # For a NEW session the request carries session_id=None; the real id is
        # assigned server-side and first arrives on the `session_start` event
        # (camelCase `sessionId`). Captured for the Layer-2 injection queue below
        # and to publish its events to the session hub.
        captured_session_id = chat_request.session_id
        last_turn: dict = {}
        # Layer-2 Canvas live-surfacing (surface_injection): register an injection
        # queue for this turn so the daemon fs-watcher can push author-agnostic
        # writes (sub-agent/CLI/hook — SDK-sidechain-filtered from Layer-1) onto
        # this ALREADY-OPEN SSE stream. Drained after each SDK message below;
        # unregistered in finally (fires on completion AND CancelledError, so a
        # dropped turn never leaks a queue). No-op unless this session ends up the
        # sole streaming one (the bleed-proof attribution gate lives in
        # surface_injection.resolve_sole_streaming_session).
        _injection_registered: Optional[str] = None
        if captured_session_id is not None:
            surface_injection.register(captured_session_id)
            _injection_registered = captured_session_id
        _turn_stream = None
        try:
            logger.info(f"Starting chat stream for agent {chat_request.agent_id}")
            # Closed in `finally` (docs/design/chat-turn-liveness.md S1): when this
            # POST stream is closed (a disconnect), the turn's generator closes at
            # once, so a turn claimed at admission never outlives its request.
            _turn_stream = _get_router().run_conversation(
                agent_id=chat_request.agent_id,
                user_message=chat_request.message,
                content=chat_request.content,
                session_id=chat_request.session_id,
                enable_skills=chat_request.enable_skills,
                enable_mcp=chat_request.enable_mcp,
                editor_context=chat_request.editor_context.model_dump() if chat_request.editor_context else None,
                terminal_context=chat_request.terminal_context.model_dump() if chat_request.terminal_context else None,
                client_id=chat_request.client_id,
                project_agent=project_agent_pin,
            )
            async for msg in _turn_stream:
                if captured_session_id is None:
                    captured_session_id = msg.get("sessionId") or msg.get("session_id")
                    if captured_session_id is not None and task_repos:
                        await _record_task_repos(captured_session_id, task_repos)
                    # A NEW session's id first arrives here — register now so the
                    # watcher can attribute to it for the rest of this turn.
                    if captured_session_id is not None and _injection_registered is None:
                        surface_injection.register(captured_session_id)
                        _injection_registered = captured_session_id
                logger.debug(f"Yielding message: {msg.get('type')}")
                if msg.get("turnId"):
                    last_turn = msg
                yield msg
                # Interleave any watcher-surfaced file_changed events into the
                # live stream (non-blocking; [] when none pending). While the
                # turn is running they are published to the session hub too,
                # so a subscribed tab gets them even with this POST gone.
                if _injection_registered is not None:
                    for _inj in surface_injection.drain_nowait(_injection_registered):
                        if last_turn and last_turn.get("type") != "turn_end" and captured_session_id:
                            _inj = session_events.hub().publish(
                                captured_session_id, _inj, turn_id=last_turn["turnId"],
                                turn_client_id=last_turn.get("turnClientId"))
                        yield _inj
            # Final drain (fix3, run_bfbbe0fd): the async-for has ended (all SDK
            # messages consumed). This catches an event that PASSED the in-flight
            # gate (a tool was open when the watcher published it) and was
            # enqueued AFTER the last in-loop drain — otherwise the finally's
            # unregister would drop it. SCOPE (important, do not overclaim — this
            # comment was corrected in an integration audit): it does NOT rescue a
            # tool-CLOSED write — the in-flight gate in publish_file_event drops
            # those BEFORE they ever enter the queue, so there is nothing here to
            # drain; a tool-closed end-of-turn write is surfaced only by the
            # pipeline-finish sweep_run_changes. This is the NORMAL-completion
            # path (safe to yield); deliberately NOT in the CancelledError branch
            # below (a generator cannot yield during GeneratorExit).
            if _injection_registered is not None:
                for _inj in surface_injection.drain_nowait(_injection_registered):
                    yield _inj
        except asyncio.TimeoutError:
            logger.error("Agent response timed out")
            yield {
                "type": "error",
                "code": "AGENT_TIMEOUT",
                "message": "The AI agent took too long to respond. This can happen when the Claude API is under heavy load or processing a complex request.",
                "suggested_action": "Your conversation is saved. Send your message again to continue."
            }
        except ResourceExhaustedException as e:
            logger.warning(
                "Resource exhausted for agent %s: %s",
                chat_request.agent_id, e.message,
            )
            yield _build_error_event(
                code="RESOURCE_EXHAUSTED",
                message=e.message,
                detail=e.detail,
                suggested_action=e.suggested_action,
            )
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            error_message = str(e)
            logger.error(f"Error in chat stream: {error_message}")
            logger.error(f"Full traceback:\n{error_traceback}")
            # ── Error classification: specific codes → useful UX ───────
            if "Cannot send() in state" in error_message:
                # Session state conflict (e.g., WAITING_INPUT pending)
                yield _build_error_event(
                    code="SESSION_BUSY",
                    message="This session is busy (a permission prompt may be pending)",
                    detail=error_message,
                    suggested_action="Complete any pending permission prompts, or wait a moment and try again.",
                )
            elif "timeout" in error_message.lower():
                yield {
                    "type": "error",
                    "code": "AGENT_TIMEOUT",
                    "message": "The AI agent took too long to respond. This can happen when the Claude API is under heavy load or processing a complex request.",
                    "suggested_action": "Your conversation is saved. Send your message again to continue."
                }
            elif "connection" in error_message.lower() or "network" in error_message.lower():
                yield _build_error_event(
                    code="SERVICE_UNAVAILABLE",
                    message="Unable to connect to the AI service",
                    detail=error_message,
                    suggested_action="Please check your connection and try again",
                )
            else:
                yield _build_error_event(
                    code="AGENT_EXECUTION_ERROR",
                    message="Agent execution failed",
                    detail=error_traceback,
                    suggested_action="Please try again or contact support",
                )
        finally:
            if _turn_stream is not None:
                await _turn_stream.aclose()
            # Drop the Layer-2 injection queue (fires on normal completion AND
            # CancelledError) so a dropped/crashed turn never leaks a queue.
            if _injection_registered is not None:
                surface_injection.unregister(_injection_registered)

    # Get unit's stop event for SSE notification
    _unit = _get_router().get_unit(chat_request.session_id) if chat_request.session_id else None
    _stop_evt = _unit.stop_event if _unit else None

    return StreamingResponse(
        sse_with_heartbeat(message_generator(), request=request, stop_event=_stop_evt),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/answer-question")
async def answer_question(request: Request):
    """Continue chat by answering an AskUserQuestion via SSE.

    This endpoint is used when Claude asks the user a question via the
    AskUserQuestion tool. The frontend collects the user's answers and
    sends them here to continue the conversation.
    """
    try:
        body = await request.json()
        answer_request = AnswerQuestionRequest(**body)
    except json.JSONDecodeError as e:
        raise ValidationException(
            message="Invalid JSON format",
            detail=f"Failed to parse request body: {str(e)}",
        )
    except Exception as e:
        raise ValidationException(
            message="Invalid request data",
            detail=str(e),
        )

    # Verify agent exists (lightweight check — no config assembly)
    if not await agent_exists(answer_request.agent_id):
        raise AgentNotFoundException(
            detail=f"Agent with ID '{answer_request.agent_id}' does not exist",
            suggested_action="Please check the agent ID and try again"
        )

    async def message_generator():
        """Generate messages from the answer continuation."""
        try:
            logger.info(f"Answering question for agent {answer_request.agent_id}, session {answer_request.session_id}")

            # Defensive guard (AC5): never inject an empty answers dict. An empty
            # submission would unblock the ask_question_gate hook with {} answers —
            # indistinguishable from a real "no option selected" — and consume the
            # question. Reject it instead so the hook stays blocked (live waiter
            # intact) and the user can resubmit a real answer.
            if not answer_request.answers:
                logger.warning(
                    "answer-question: empty answers for session %s tool_use %s — rejecting",
                    answer_request.session_id, answer_request.tool_use_id,
                )
                yield _build_error_event(
                    code="EMPTY_ANSWER",
                    message="No answer was provided",
                    detail="The answer submission was empty. Select an option and resubmit.",
                    suggested_action="Choose an answer to the question and submit again.",
                )
                return

            answer_text = json.dumps(answer_request.answers) if answer_request.answers else ""
            async for msg in _get_router().continue_with_answer(
                session_id=answer_request.session_id,
                answer=answer_text,
                tool_use_id=answer_request.tool_use_id,
            ):
                logger.debug(f"Yielding message: {msg.get('type')}")
                yield msg
        except asyncio.TimeoutError:
            logger.error("Agent response timed out")
            yield {
                "type": "error",
                "code": "AGENT_TIMEOUT",
                "message": "The AI agent took too long to respond. This can happen when the Claude API is under heavy load or processing a complex request.",
                "suggested_action": "Your conversation is saved. Send your message again to continue."
            }
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            error_message = str(e)
            logger.error(f"Error in answer-question stream: {error_message}")
            logger.error(f"Full traceback:\n{error_traceback}")
            yield _build_error_event(
                code="AGENT_EXECUTION_ERROR",
                message="Agent execution failed",
                detail=error_traceback,
                suggested_action="Please try again or contact support",
            )

    _unit = _get_router().get_unit(answer_request.session_id) if answer_request.session_id else None
    _stop_evt = _unit.stop_event if _unit else None

    return StreamingResponse(
        sse_with_heartbeat(message_generator(), request=request, stop_event=_stop_evt),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


EVENTS_HEARTBEAT_INTERVAL = 15


@router.get("/events")
async def session_events_stream(
    request: Request,
    cursors: Optional[str] = Query(None, max_length=65536),
    epoch: Optional[str] = Query(None, max_length=64),
):
    """Subscribe to every session's turn events (one stream per desktop window).

    ``cursors`` is a JSON map ``{sessionId: lastEventSeq}`` (``-1`` = join the
    session's active turn); ``epoch`` is the ``events_hello.epoch`` the cursors
    came from. See core/session_events for replay / resync semantics. Unlike a
    turn's POST stream, a client disconnect ENDS this subscription: nothing is
    owned by it.
    """
    parsed = session_events.parse_cursors(cursors)
    sub = session_events.hub().subscribe(parsed, epoch)

    async def stream() -> AsyncIterator[str]:
        nxt: Optional[asyncio.Task] = None
        try:
            while True:
                if nxt is None:
                    nxt = asyncio.ensure_future(sub.__anext__())
                done, _ = await asyncio.wait({nxt}, timeout=EVENTS_HEARTBEAT_INTERVAL)
                if nxt in done:
                    try:
                        env = nxt.result()
                    except StopAsyncIteration:
                        return
                    nxt = None
                    yield f"data: {json.dumps(env)}\n\n"
                    continue
                try:
                    if await request.is_disconnected():
                        return
                except Exception as exc:  # noqa: BLE001 — is_disconnected can fail; keep serving
                    logger.debug("session_events.is_disconnected_failed: %s", exc)
                yield create_sse_heartbeat()
        finally:
            if nxt is not None and not nxt.done():
                nxt.cancel()
                try:
                    await nxt
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
            await sub.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/turn-diff")
async def get_turn_diff(
    git_root: str = Query(..., max_length=4096),
    start: str = Query(..., max_length=64),
    end: str = Query(..., max_length=64),
    path: str = Query(..., max_length=4096),
):
    """One file's diff on a turn's "files changed" card (core/turn_changes).

    The card's rows carry the two change trees, not the diff text, so diffs never
    sit in the messages table (its FTS index and session recall feed prompts).
    Only a known code tree's object store is read; ids and path are validated in
    ``diff_text``. 404 when it is gone for good (unknown repo, bad input, pruned
    tree), 503 on a transient failure (worth a retry). Runs on the "turn_changes"
    pool, never on 'subprocess' (its fail-open safety gates must not wait on it)."""
    from core import executors
    from core.turn_changes import DiffUnavailable, diff_text

    try:
        text = await executors.run_in("turn_changes", diff_text, git_root, start, end, path)
    except DiffUnavailable:
        raise HTTPException(status_code=503, detail="Diff temporarily unavailable")
    if text is None:
        raise HTTPException(status_code=404, detail="Diff not available")
    return {"diff": text}


@router.get("/sessions", response_model=list[ChatSessionResponse])
async def list_sessions(
    agent_id: str | None = None,
    limit: int | None = None,
):
    """List chat sessions, optionally filtered by agent_id.

    Returns sessions sorted by last_accessed DESC, created_at DESC.

    Args:
        agent_id: Optional agent ID filter.
        limit: Optional max number of sessions to return (1–100).
               Values <= 0 are rejected with 422. Values > 100 are
               silently capped at 100.
    """
    if limit is not None:
        if limit <= 0:
            raise HTTPException(
                status_code=422,
                detail="limit must be a positive integer",
            )
        limit = min(limit, 100)

    sessions = await session_manager.list_sessions(agent_id=agent_id, limit=limit)
    return [
        ChatSessionResponse(
            id=s.session_id,
            agent_id=s.agent_id,
            title=s.title,
            created_at=s.created_at,
            last_accessed_at=s.last_accessed,
            work_dir=s.work_dir,
        )
        for s in sessions
    ]


@router.get("/sessions/admission-state")
async def get_admission_state_endpoint():
    """Return daemon-wide concurrent-streaming admission state (OT03).

    Read-only probe that exposes the R6 concurrent-streaming cap so a health
    check can tell SATURATED (busy — a new turn would queue) apart from a broken
    daemon. Critically it consumes NO streaming slot: it only READS the
    module-level counter + the cap constant, never starts a stream.

    Used by ``scripts/smoke_e2e.py`` to skip (not fail) the chat_stream check
    when the daemon is already at ``MAX_CONCURRENT_STREAMS`` — preventing the
    false-red where a busy-but-healthy daemon looked broken (the smoke would
    queue behind the cap and hit its wall-clock timeout).

    Returns:
        streaming_count: sessions currently in STREAMING state (daemon-wide)
        max_concurrent: the cap (SessionUnit.MAX_CONCURRENT_STREAMS)
        saturated: streaming_count >= max_concurrent (a new turn would queue)
        stalled_streaming: count of STREAMING sessions whose last SDK event is
            older than AUTO_RECOVER_STALL_THRESHOLD — i.e. WEDGED, not working.
            This is the discriminator between "legitimately busy" (advancing
            streams) and "stuck" (the OT01/recovery wedge): a smoke probe must
            FAIL on saturation-by-stall, only SKIP on saturation-by-advancing.
        idle_live_units: count of non-streaming live units (advisory — NOT a
            queue depth; most idle units never intend to stream. Renamed from
            the misleading 'queued' per adversarial review).

    Must be registered BEFORE /sessions/{session_id} so the path param doesn't
    capture 'admission-state' as a session ID.
    """
    from core.session_unit import _get_streaming_count, SessionUnit, AUTO_RECOVER_STALL_THRESHOLD

    from core.session_router import PREWARM_SESSION_PREFIX

    streaming_count = _get_streaming_count()
    max_concurrent = SessionUnit.MAX_CONCURRENT_STREAMS
    sr = _get_router()
    idle_live_units = 0
    stalled_streaming = 0
    for u in sr.list_units():
        if not u.session_id or u.session_id.startswith(PREWARM_SESSION_PREFIX):
            continue
        if u.state.value == "streaming":
            # streaming_stall_seconds is None if not streaming or no events yet;
            # a long stall while STREAMING = the wedge signature (OT01 class).
            stall = getattr(u, "streaming_stall_seconds", None)
            if stall is not None and stall > AUTO_RECOVER_STALL_THRESHOLD:
                stalled_streaming += 1
        else:
            idle_live_units += 1
    return {
        "streaming_count": streaming_count,
        "max_concurrent": max_concurrent,
        "saturated": streaming_count >= max_concurrent,
        "stalled_streaming": stalled_streaming,
        "idle_live_units": idle_live_units,
    }


from core.session_state import session_state_entry, bg_snapshot as _bg_snapshot  # noqa: E402,F401


@router.get("/sessions/streaming-state")
async def get_streaming_state_endpoint():
    """Return the streaming state for all active sessions.

    Frontend polls this every 15s (while any tab is streaming) to reconcile
    stale isStreaming state when SSE events are lost. Returns all non-prewarm
    sessions — frontend iterates its own tab map and indexes by session ID.

    `streaming` is simply `state == STREAMING`. A client disconnect no longer
    touches the turn: it keeps running (and reports streaming) to its own end, and
    the session subscription (GET /events) carries it to the tabs.

    Must be registered BEFORE /sessions/{session_id} to avoid path parameter
    capturing 'streaming-state' as a session ID.
    """
    from core.session_router import PREWARM_SESSION_PREFIX

    sr = _get_router()
    result: dict[str, dict] = {}
    for unit in sr.list_units():
        if not unit.session_id or unit.session_id.startswith(PREWARM_SESSION_PREFIX):
            continue
        result[unit.session_id] = await session_state_entry(unit, sr)
    return {"sessions": result}


@router.get("/sessions/{session_id}/context-usage")
async def get_context_usage_endpoint(session_id: str):
    """The session's context-window usage — the backend's authoritative snapshot.

    The chat input's context ring reads this when a tab becomes active (app
    start, tab restore, tab switch) and after compact/refresh; during a turn the
    same snapshot arrives as ``context_warning`` SSE events. Shape:
    ``core.context_usage`` (``source`` live | transcript | none).

    Resolution — what the session's NEXT request would actually carry:
    1. a unit with a snapshot for its current sdk_session_id → that (``live``);
    2. otherwise the CLI transcript of the session's sdk_session_id — the unit's
       own id when a unit exists (None after refresh / a true cold resume means
       the next spawn rebuilds context, so a persisted id must not resurrect the
       old transcript), the persisted id only when there is no unit yet;
    3. nothing measurable → ``source: none`` (the ring shows empty).

    The transcript read runs on the dedicated "io" pool (core.executors) — never
    on the event loop, never on the default pool that schedules /health.

    Ordering: a derived/none answer is stamped with the time this request was
    received, and a live snapshot recorded WHILE the transcript was being read
    is returned instead — so this answer can never look newer than a live value
    the frontend already received over SSE. If the session's identity changed
    during the read (a give-up cleared it, a new spawn replaced it), the
    derived answer describes another conversation and ``none`` is returned.
    """
    import time as _time

    from core import context_usage, executors

    requested_at = _time.time()
    sr = _get_router()
    unit = sr.get_unit(session_id)
    if unit is not None:
        snap = unit.context_usage_snapshot()
        if snap is not None:
            return snap
        sdk_id = unit._sdk_session_id
        model = unit._model_name
    else:
        sdk_id = sr.persisted_sdk_session_id(session_id)
        model = None
    derived = await executors.run_in(
        "io", context_usage.snapshot_from_transcript, sdk_id, model, requested_at,
    )
    after = sr.get_unit(session_id)
    if after is not None:
        live = after.context_usage_snapshot()
        if live is not None:
            return live
        if after._sdk_session_id != sdk_id:
            return context_usage.none_snapshot(measured_at=requested_at)
    return derived


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_session(session_id: str):
    """Get a specific chat session by ID."""
    session = await session_manager.get_session(session_id)
    if not session:
        raise SessionNotFoundException(
            detail=f"Session with ID '{session_id}' does not exist",
            suggested_action="Please check the session ID and try again"
        )
    return ChatSessionResponse(
        id=session.session_id,
        agent_id=session.agent_id,
        title=session.title,
        created_at=session.created_at,
        last_accessed_at=session.last_accessed,
        work_dir=session.work_dir,
    )


def _turn_key(msg: dict) -> Optional[str]:
    """The turn a row belongs to: its client_id without the ``-asst`` suffix
    (a continuation and a Stop receipt carry their turn's), or None (old rows)."""
    md = msg.get("metadata")
    cid = md.get("client_id") if isinstance(md, dict) else None
    if not isinstance(cid, str) or not cid:
        return None
    return cid[:-5] if cid.endswith("-asst") else cid


def _merge_consecutive_assistant_messages(
    messages: list[dict], compaction_times: Optional[list[str]] = None,
) -> list[dict]:
    """Merge consecutive assistant DB rows into single message dicts.

    The backend persists each agentic turn as a separate row (crash safety).
    When loaded for display, consecutive assistant rows should appear as ONE
    message bubble — matching what the frontend shows during streaming.

    Rules:
    - Consecutive assistant rows (no user row between them) get their content
      arrays concatenated into a single message dict.
    - The merged message uses the FIRST row's id and created_at (stable reference).
    - The LAST row's model wins (most recent model attribution).
    - Non-assistant rows and user rows are never merged.
    - A row read with its transcript rev (``_rev``) is listed in its message's
      ``row_revs`` ({row id: rev}): which rows a merged bubble holds, at which
      version. A transcript change for one of them at or below that rev is
      already in the bubble (frontend hooks/transcriptFeed skips it).
    - A compaction (``compaction_times``) between two rows of DIFFERENT turns
      ends the bubble: the divider sits between them, as the live chat shows it
      (a turn after a divider starts its own bubble). Within one turn the bubble
      continues, and the divider is placed after it (compact_summary.splice).
    """
    if not messages:
        return []

    times = sorted(t for t in (compaction_times or []) if t)
    merged: list[dict] = []
    last_row: Optional[dict] = None   # the previous raw row
    for msg in messages:
        role = msg.get("role")
        split = False
        if times and last_row is not None and role == "assistant":
            lo, hi = last_row.get("created_at") or "", msg.get("created_at") or ""
            k_prev, k_new = _turn_key(last_row), _turn_key(msg)
            split = (k_prev is not None and k_new is not None and k_prev != k_new
                     and any(lo < t <= hi for t in times))
        last_row = msg
        if (
            role == "assistant"
            and merged
            and merged[-1].get("role") == "assistant"
            and not split
        ):
            # Merge into previous assistant message
            prev = merged[-1]
            prev_content = prev.get("content") or []
            new_content = msg.get("content") or []
            # Normalize: if content is a bare string (legacy), wrap in text block
            if isinstance(prev_content, str):
                prev_content = [{"type": "text", "text": prev_content}]
            if isinstance(new_content, str):
                new_content = [{"type": "text", "text": new_content}]
            # Defensive copy: avoid mutating the source row's content list
            # in case a read cache is added in the future.
            prev["content"] = list(prev_content) + list(new_content)
            # Take latest model
            if msg.get("model"):
                prev["model"] = msg["model"]
            if "_rev" in msg:
                prev.setdefault("row_revs", {})[msg.get("id")] = msg["_rev"] or 0
        else:
            # New message (user, or first assistant after user)
            entry = {
                "id": msg.get("id"),
                "session_id": msg.get("session_id"),
                "role": role,
                "content": msg.get("content", []),
                "model": msg.get("model"),
                "created_at": msg.get("created_at"),
            }
            # Preserve metadata (carries client_id for optimistic dedup)
            if msg.get("metadata"):
                entry["metadata"] = msg["metadata"]
            if "_rev" in msg:
                entry["row_revs"] = {msg.get("id"): msg["_rev"] or 0}
            merged.append(entry)

    return merged


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    request: Request,
    limit: Optional[int] = Query(None, ge=1, le=200),
    before_id: Optional[str] = Query(None),
    turns: Optional[int] = Query(None, ge=1, le=100),
):
    """Get messages for a chat session with optional cursor-based pagination.

    ``turns`` (preferred for display): the last N user turns before ``before_id``,
    each whole (core.history_page). The response carries ``X-Has-More: 1|0`` —
    whether older rows remain — because the merged bubble count says nothing
    about the row count behind it. ``limit`` stays for row-tail callers.

    Supports ETag caching: the ETag is ``"session_id:msg_count"``.
    Messages are append-only, so count change = content change.
    Frontend sends ``If-None-Match`` → 304 when unchanged (no message
    fetch, no serialization).  ETag skipped for cursor-paginated requests.

    Returns messages in chronological order.
    """
    from fastapi.responses import JSONResponse, Response as FastAPIResponse
    # Called directly (tests), the default is the Query() marker, not None.
    if not isinstance(turns, int):
        turns = None

    # Verify session exists
    session = await session_manager.get_session(session_id)
    if not session:
        raise SessionNotFoundException(
            detail=f"Session with ID '{session_id}' does not exist",
            suggested_action="Please check the session ID and try again"
        )

    # The permission prompts a hook of this session is blocked on now
    # (core/permission_prompt): a stored prompt not among them reads "expired", and
    # the set is part of the ETag (a prompt leaving it changes the body, not the
    # row count).
    from core import permission_prompt as _perm_prompt
    try:
        _live_perms = _perm_prompt.live_request_ids(_get_router().get_unit(session_id), session_id)
    except Exception:  # noqa: BLE001 — history must load even if this fails
        logger.debug("live permission read failed session=%s", session_id, exc_info=True)
        _live_perms = set()

    # The snapshot's transcript rev (schema v15), read BEFORE the rows: every
    # change at or below it is in this body, so a tab applies only the feed's
    # changes above it (core/transcript_feed). A change landing between this read
    # and the rows is in both — applying it again is a no-op.
    transcript_rev: int | None
    try:
        transcript_rev = await db.transcript_rev(session_id)
    except Exception:  # noqa: BLE001
        logger.debug("transcript rev read failed session=%s", session_id, exc_info=True)
        transcript_rev = None

    # ── ETag: transcript rev → 304 if unchanged ──
    # The rev moves on every insert, in-place update (a decision, an answer, a
    # background agent's result) and delete of a messages or compactions row
    # (schema v15 triggers), so it stands for the rows and the spliced compactions;
    # a row count misses in-place updates and served a stale body on 304. Only for
    # non-cursor queries (before_id changes per request); no ETag when the rev
    # could not be read.
    etag: str | None = None
    if before_id is None and transcript_rev is not None:
        # The page shape is part of the body: a turns page and a full fetch of the
        # same session must not share an ETag.
        shape = f"t{turns}" if turns is not None else (f"l{limit}" if limit is not None else "all")
        etag = f'"{session_id}:r{transcript_rev}:{_perm_prompt.etag_part(_live_perms)}:{shape}"'
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match == etag:
            return FastAPIResponse(status_code=304, headers={
                "ETag": etag, "X-Transcript-Rev": str(transcript_rev)})

    # ── Fetch messages ──
    has_more: bool | None = None
    if turns is not None:
        from core.history_page import page_by_turns

        async def _fetch(n: int, cursor: Optional[str]):
            return await db.messages.list_by_session_paginated(
                session_id, limit=n, before_id=cursor, with_rev=True)

        messages, has_more = await page_by_turns(_fetch, turns, before_id)
    elif limit is not None or before_id is not None:
        messages = await db.messages.list_by_session_paginated(
            session_id, limit=limit, before_id=before_id, with_rev=True
        )
    else:
        messages = await db.messages.list_by_session(session_id, with_rev=True)

    # Merge consecutive assistant messages into one.
    # The backend persists each agentic turn as a separate DB row (crash safety),
    # but the frontend expects one assistant bubble per user→agent exchange.
    # Without merging, loadSessionMessages renders 5-20 separate assistant bubbles
    # with overlapping text content for a single response.
    try:
        _compactions = await db.list_compactions(session_id)
    except Exception:  # noqa: BLE001 — history must load even if this fails
        logger.warning("compaction read failed session=%s", session_id, exc_info=True)
        _compactions = []
        etag = None
    data = _merge_consecutive_assistant_messages(
        messages, [c.get("created_at") or "" for c in _compactions])
    _perm_prompt.annotate(data, _live_perms)

    # Context compactions as synthetic `compact-<id>` system rows (divider +
    # summary card), placed after the merge so a mid-turn compaction never
    # splits that turn's bubble. Display only: they are not messages rows, so
    # cold resume, recall and counts never see them.
    try:
        compactions = _compactions
        if compactions:
            from core.compact_summary import splice_into_history
            # An older page is bounded by the row it was cut before, so every
            # compaction lands on exactly one page.
            upper = None
            if before_id is not None:
                cursor_row = await db.messages.get(before_id)
                upper = (cursor_row or {}).get("created_at") or None
            data = splice_into_history(session_id, data, compactions, upper=upper)
    except Exception:  # noqa: BLE001 — history must load even if this fails
        logger.warning("compaction splice failed session=%s", session_id, exc_info=True)
        etag = None  # never cache a body that lacks rows the ETag counts

    headers = {"ETag": etag} if etag else {}
    if transcript_rev is not None:
        headers["X-Transcript-Rev"] = str(transcript_rev)
    if has_more is not None:
        headers["X-Has-More"] = "1" if has_more else "0"
    return JSONResponse(content=data, headers=headers)


@router.get("/sessions/{session_id}/transcript")
async def get_transcript_since(
    session_id: str,
    since_rev: int = Query(..., ge=0),
):
    """What changed in a session's transcript after ``since_rev`` (schema v15):
    ``{rev, reset, rows: [{row, rev, pending}]}``, rows as the history returns
    them (a compaction as its ``compact-<id>`` system row), unmerged, oldest
    first. ``reset``: a row was removed after ``since_rev`` — read the history
    again. A reconnecting tab asks for the changes it missed instead of the
    whole history."""
    session = await session_manager.get_session(session_id)
    if not session:
        raise SessionNotFoundException(
            detail=f"Session with ID '{session_id}' does not exist",
            suggested_action="Please check the session ID and try again")
    from core import permission_prompt as _perm_prompt
    from core.compact_summary import history_message
    out = await db.transcript_since(session_id, since_rev)
    try:
        live = _perm_prompt.live_request_ids(_get_router().get_unit(session_id), session_id)
    except Exception:  # noqa: BLE001
        logger.debug("live permission read failed session=%s", session_id, exc_info=True)
        live = set()
    fields = ("id", "session_id", "role", "content", "model", "created_at", "metadata")
    msgs = [{k: m[k] for k in fields if k in m} for _, m in out["messages"]]
    _perm_prompt.annotate(msgs, live)
    rows = [{"row": m, "rev": rv, "pending": False} for m, (rv, _) in zip(msgs, out["messages"])]
    rows += [{"row": history_message(session_id, c), "rev": rv, "pending": False}
             for rv, c in out["compactions"]]
    rows.sort(key=lambda r: (r["row"].get("created_at") or "", r["rev"]))
    return {"rev": out["rev"], "reset": out["reset"], "rows": rows}


async def _pipeline_session_signal(kind: str, session_id: str) -> None:
    """Record a user Stop / a closed tab on the pipeline runs *session_id* owns.
    Off-loop and fail-safe: a pipeline problem never fails the chat request."""
    try:
        from core import pipeline_human
        fn = pipeline_human.on_user_stop if kind == "stop" else pipeline_human.on_session_lost
        await asyncio.to_thread(fn, session_id)
    except Exception:  # noqa: BLE001
        logger.warning("pipeline %s signal failed for %s", kind, session_id, exc_info=True)


@router.post("/stop/{session_id}")
async def stop_session(session_id: str):
    """Stop a running chat session.

    This will interrupt the currently running agent for the given session.
    The agent will stop processing and the stream will end gracefully.
    """
    logger.info(f"Received stop request for session {session_id}")
    result = await _get_router().interrupt_session(session_id)

    if result["success"]:
        # A user Stop pauses the pipeline runs this session drives (a run waiting
        # for a human keeps waiting) — docs/design/pipeline-v1-human-loop.md.
        await _pipeline_session_signal("stop", session_id)
        return {"status": "stopped", "message": result["message"],
                "receipt": result.get("receipt") or []}
    else:
        # Return 200 even if session not found - client may have already finished
        return {"status": "not_found", "message": result["message"]}


@router.post("/stop/{session_id}/task/{task_id}")
async def stop_task(
    session_id: str,
    task_id: str = Path(..., pattern=r"^[A-Za-z0-9_-]{1,128}$"),
):
    """Stop ONE sub-agent of the session's running turn; the turn goes on.

    The id must be a task this session reported running in its current turn
    (SessionUnit.stop_task allowlist); anything else is refused without reaching
    the SDK. The Agents panel's per-agent ✕ calls this; "Stop all" is /stop/{id}.
    """
    logger.info("Received stop-task request session=%s task=%s", session_id, task_id)
    return await _get_router().stop_task(session_id, task_id)


@router.post("/release/{session_id}")
async def release_session(session_id: str, request: Request):
    """Release a session's concurrency slot on tab close (R6b).

    Frees the backend SessionUnit's slot (kills the subprocess, clears
    per-session module state) so a closed chat tab does not hold a slot until
    the 12h idle TTL.  Does NOT delete DB messages — the conversation survives
    and the user can reopen it from history.

    Best-effort + idempotent: ALWAYS returns 200 (the frontend fires this
    fire-and-forget on close).  The body is parsed defensively from the raw
    Request — a missing, empty, or even malformed body degrades to force=False
    rather than a 422, because a fire-and-forget release must never error.  The
    router applies the channel/active-state safety guards (see
    ``SessionRouter.release_session``).

    Optional JSON body:
        { "force": true }  — confirmed close of a STREAMING/WAITING_INPUT tab.
        Without force, an active session is left untouched (``skipped_active``):
        its turn is session-owned and runs to its own end.
    """
    # Defensive parse: never 422 on a fire-and-forget endpoint. Strict on force —
    # only an explicit JSON boolean true enables the destructive interrupt-active
    # branch; truthiness-coercion ({"force":"false"} → True) is rejected.
    force = False
    try:
        raw = await request.body()
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                force = parsed.get("force") is True
    except (ValueError, UnicodeDecodeError):
        force = False  # malformed body → best-effort unforced release
    logger.info("Received release request for session %s (force=%s)", session_id, force)
    result = await _get_router().release_session(session_id, force=force)
    if isinstance(result, dict) and result.get("status") in ("released", "not_found"):
        await _pipeline_session_signal("lost", session_id)  # the owner's tab is closed
    return result


@router.post("/refresh/{session_id}")
async def refresh_session(session_id: str):
    """Refresh a session's context by killing subprocess + dropping resume id.

    User-triggered "same-tab restart": the subprocess is killed AND
    _sdk_session_id is DROPPED (session_unit.refresh_context calls
    _crash_to_cold_async(clear_identity=True) → _sdk_session_id=None). This is
    the load-bearing choice: with the SDK session id gone, the next send() does
    NOT take the SDK --resume path (which would replay the FULL transcript and
    defeat the button's purpose). Instead it is a TRUE cold resume — session_router
    injects a STRUCTURED conversation summary (mechanism B) into a fresh system
    prompt, shedding the bloated transcript. (Code is truth: an earlier version of
    this docstring wrongly claimed _sdk_session_id is preserved — it is dropped.)

    The frontend should:
    1. Insert a visual separator in the chat
    2. Dim old messages
    3. Send the next user message normally (which triggers the cold-resume summary)
    """
    logger.info(f"Received refresh request for session {session_id}")
    result = await _get_router().refresh_session(session_id)

    if result["success"]:
        return {"status": "refreshed", "message": result["message"]}
    elif "not found" in result["message"].lower():
        raise HTTPException(status_code=404, detail=result["message"])
    else:
        # Session is busy (STREAMING or WAITING_INPUT)
        raise HTTPException(status_code=409, detail=result["message"])


@router.post("/compact/{session_id}")
async def compact_session(session_id: str, body: Optional[dict] = None):
    """Trigger manual compaction of a session's context window.

    Sends the /compact slash command to the running Claude CLI subprocess,
    compressing the conversation history into a summary to free context space.

    Optional JSON body:
        { "instructions": "Preserve the database schema discussion" }
    """
    instructions = body.get("instructions") if body else None
    logger.info(f"Received compact request for session {session_id}")
    result = await _get_router().compact_session(session_id, instructions=instructions)

    if result["success"]:
        return {"status": "compacted", "message": result["message"]}
    else:
        return {"status": "not_found", "message": result["message"]}


@router.post("/sessions/{session_id}/enable-mcp")
async def enable_mcp_for_session(session_id: str, body: dict):
    """Activate a deferred MCP for an existing session.

    Kills the subprocess (must be IDLE) so the next message spawns fresh
    with the requested MCP included. Used by Lazy MCP Loading when the
    agent needs an on-demand MCP mid-conversation.

    JSON body:
        { "mcp_name": "aws-outlook-mcp" }
    """
    mcp_name = body.get("mcp_name", "")
    if not mcp_name:
        raise HTTPException(status_code=400, detail="mcp_name is required")

    result = await _get_router().enable_mcp_for_session(session_id, mcp_name)
    if result["success"]:
        return {"status": "reclaimed", "message": result["message"]}
    else:
        raise HTTPException(status_code=409, detail=result["message"])


@router.delete("/sessions/{session_id}/pending/{client_id}")
async def cancel_pending_message(session_id: str, client_id: str):
    """Cancel one queued message (docs/design/chat-turn-liveness.md S3).

    Only a message no drain has taken yet: it is removed and its send-ledger
    entry released. 409 when a drain already took it (it is being sent), 404
    when there is no such queued message. The session's state (with its queue)
    is pushed to its tabs after a cancel."""
    from core import session_pending, session_state
    result = await session_pending.cancel_unclaimed(session_id, client_id)
    if result == "claimed":
        raise HTTPException(status_code=409, detail="The message is already being sent.")
    if result == "missing":
        raise HTTPException(status_code=404, detail="No such queued message.")
    session_state.publish(_get_router(), session_id)
    return {"status": "cancelled", "clientId": client_id}


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str):
    """Delete a chat session and all its messages.

    Fires post-session-close hooks BEFORE deleting data so hooks can
    read the conversation log.  Also kills the SessionUnit subprocess
    and cleans up session registry metadata.
    """
    # 1. Fire lifecycle hooks BEFORE data deletion (fire-and-forget).
    try:
        session = await session_manager.get_session(session_id)
        if session:
            from core.session_hooks import HookContext
            message_count = await db.messages.count_by_session(session_id)
            context = HookContext(
                session_id=session_id,
                agent_id=session.agent_id,
                message_count=message_count,
                session_start_time=session.created_at,
                session_title=session.title,
                deleting=True,
            )
            _get_router()  # Ensure initialized
            if _lifecycle_manager:
                _lifecycle_manager.enqueue_hooks(context)
    except Exception as exc:
        logger.warning("Hook fire failed for delete_session %s: %s", session_id, exc)
    await _pipeline_session_signal("lost", session_id)

    # 2. Clean up SessionUnit subprocess
    unit = _get_router().get_unit(session_id)
    if unit and unit.is_alive:
        await unit.kill()

    # 3. Delete messages and session from DB
    await db.messages.delete_by_session(session_id)
    try:
        await db.delete_compactions_by_session(session_id)
    except Exception:  # noqa: BLE001 — leftover rows are invisible without the session
        logger.warning("delete compactions failed session=%s", session_id, exc_info=True)
    deleted = await session_manager.delete_session(session_id)
    if not deleted:
        raise SessionNotFoundException(
            detail=f"Session with ID '{session_id}' does not exist",
            suggested_action="Please check the session ID and try again"
        )

    # 4. Clean up per-session state to prevent unbounded memory growth
    from core.lifecycle_manager import LifecycleManager
    LifecycleManager._release_session_state(session_id)
    # 5. The session's Canvas record (core/session_outputs) goes with it.
    try:
        from core import executors, session_outputs
        await executors.run_in("canvas_session", session_outputs.forget, session_id)
    except Exception:  # noqa: BLE001 — a leftover record is invisible without the session
        logger.warning("canvas record cleanup failed session=%s", session_id, exc_info=True)


# NOTE: the non-streaming /cmd-permission-response endpoint was removed in
# run_74992978. Both approve AND deny now stream via /cmd-permission-continue
# (run_ec351cc9), so a decision always lets the agent continue. The
# approve-into-void reap it once held is NOT lost — reap_dead_waiting_input is a
# SessionUnit method still invoked by the live /cmd-permission-continue not-found
# path (below), lifecycle_manager, and session_unit's own send path.


@router.post("/cmd-permission-continue")
async def cmd_permission_continue(request: Request):
    """Continue chat after user makes a command permission decision via SSE.

    This endpoint is used when the user approves or denies a dangerous command.
    It records the decision and continues the conversation stream.
    """
    try:
        body = await request.json()
        permission_request = PermissionResponseRequest(**body)
    except json.JSONDecodeError as e:
        raise ValidationException(
            message="Invalid JSON format",
            detail=f"Failed to parse request body: {str(e)}",
        )
    except Exception as e:
        raise ValidationException(
            message="Invalid request data",
            detail=str(e),
        )

    # Verify permission request exists
    perm_req = _pm.get_pending_request(permission_request.request_id)
    if not perm_req:
        # Approve-into-void recovery (run_65f317db): the request is gone because
        # the waiter coroutine was cancelled before the decision arrived. A
        # PRE-STREAM `raise` here becomes an HTTP 400 (response not ok) on the
        # frontend, which leaves `isStreaming` pinned true — the tab looks alive
        # but is dead. Instead, if the session is a dead-waiter zombie, reap it to
        # COLD and return a STREAMING response that yields ONE terminal `error`
        # event: the frontend stream handler resets isStreaming + tab status on an
        # `error`-type event (useChatStreamingLifecycle.ts:2939), and the trailing
        # [DONE] fires onComplete which clears permissionLoadingTabs.
        #
        # NOTE on the FE auto-retry (Gate-2 LOW): this path does NOT set a
        # retryStreamFn, but retryStreamFn is a PERSISTENT tabState field that an
        # EARLIER send() turn may have left set — so the FE error handler's
        # auto-retry (hasReceivedData=false && retryStreamFn && attempt<1) MAY fire
        # here. That outcome is benign and self-consistent: the one bounded retry
        # re-sends the last message into the now-COLD session, which is exactly the
        # suggested_action ("send your message again to continue"). It does NOT
        # loop into this not-found path (the resend goes through the normal send
        # endpoint, not cmd-permission-continue). So recovery holds either way.
        #
        # We emit PERMISSION_EXPIRED unconditionally (whether or not the reap
        # actually fired): if the session was a dead-waiter zombie the reap
        # recovered it; if the request was merely already-resolved/absent on a
        # healthy session, a generic terminal error that resets the FE is still the
        # correct, safe response. (Asymmetry-with-the-non-streaming-endpoint is
        # intentional — the streaming path has no cheap way to signal "nothing to
        # do" other than a terminal event.)
        _reap_unit = _get_router().get_unit(permission_request.session_id) if permission_request.session_id else None
        if _reap_unit is not None:
            try:
                await _reap_unit.reap_dead_waiting_input()
            except Exception as e:
                logger.warning(
                    "Failed to reap dead WAITING_INPUT for session %s: %s",
                    permission_request.session_id, e,
                )

        async def _expired_generator():
            yield {
                "type": "error",
                "code": "PERMISSION_EXPIRED",
                "message": "This permission request is no longer active (it expired or was cancelled).",
                "suggested_action": "Your conversation is saved. Send your message again to continue.",
            }

        return StreamingResponse(
            sse_with_heartbeat(_expired_generator(), request=request, stop_event=None),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Get agent_id from the session
    session = await session_manager.get_session(permission_request.session_id)
    if not session:
        raise SessionNotFoundException(
            detail=f"Session with ID '{permission_request.session_id}' does not exist",
            suggested_action="Please check the session ID and try again"
        )

    agent_id = session.agent_id

    # Verify agent exists (lightweight check — no config assembly)
    if not await agent_exists(agent_id):
        raise AgentNotFoundException(
            detail=f"Agent with ID '{agent_id}' does not exist",
            suggested_action="Please check the agent ID and try again"
        )

    async def message_generator():
        """Generate messages from the permission continuation."""
        try:
            logger.info(f"Processing permission decision for request {permission_request.request_id}: {permission_request.decision}")
            allowed = permission_request.decision == "approve"
            async for msg in _get_router().continue_with_cmd_permission(
                session_id=permission_request.session_id,
                request_id=permission_request.request_id,
                allowed=allowed,
            ):
                logger.debug(f"Yielding message: {msg.get('type')}")
                yield msg
        except asyncio.TimeoutError:
            logger.error("Agent response timed out")
            yield {
                "type": "error",
                "code": "AGENT_TIMEOUT",
                "message": "The AI agent took too long to respond. This can happen when the Claude API is under heavy load or processing a complex request.",
                "suggested_action": "Your conversation is saved. Send your message again to continue."
            }
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            error_message = str(e)
            logger.error(f"Error in cmd-permission-continue stream: {error_message}")
            logger.error(f"Full traceback:\n{error_traceback}")
            yield _build_error_event(
                code="AGENT_EXECUTION_ERROR",
                message="Agent execution failed",
                detail=error_traceback,
                suggested_action="Please try again or contact support",
            )

    _unit = _get_router().get_unit(permission_request.session_id) if permission_request.session_id else None
    _stop_evt = _unit.stop_event if _unit else None

    return StreamingResponse(
        sse_with_heartbeat(message_generator(), request=request, stop_event=_stop_evt),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )



# ---------------------------------------------------------------------------
# Chat-thread project association and binding endpoints
# ---------------------------------------------------------------------------


@chat_threads_router.get(
    "/projects/{project_id}/threads",
    response_model=List[ChatThreadResponse],
)
async def list_threads_by_project(project_id: str):
    """List all chat threads associated with a specific project.

    Returns threads where ``project_id`` matches the given UUID, ordered
    by ``updated_at`` descending.

    Validates: Requirements 26.1, 26.5
    """
    threads = await chat_thread_manager.list_threads_by_project(project_id)
    return threads


@chat_threads_router.get(
    "/threads/global",
    response_model=List[ChatThreadResponse],
)
async def list_global_threads():
    """List all chat threads not associated with any project.

    Returns threads where ``project_id IS NULL``, representing global
    SwarmWS chats.

    Validates: Requirements 26.4
    """
    threads = await chat_thread_manager.list_global_threads()
    return threads


@chat_threads_router.post(
    "/chat_threads/{thread_id}/bind",
    response_model=ThreadBindResponse,
)
async def bind_thread(
    thread_id: str,
    request: ThreadBindRequest,
    force: bool = Query(False, description="Override cross-project binding guardrail"),
):
    """Bind or rebind a thread to a task/todo mid-session.

    Accepts a ``ThreadBindRequest`` body with ``task_id``, ``todo_id``,
    and ``mode`` (replace | add).  An optional ``force`` query parameter
    overrides the cross-project binding guardrail.

    Returns 409 Conflict if the task belongs to a different project than
    the thread and ``force`` is not set (PE Enhancement C).

    Validates: Requirements 35.1, 35.6
    """
    # Merge force from query param and body (body takes precedence if set)
    effective_force = request.force if request.force is not None else force

    result = await chat_thread_manager.bind_thread(
        thread_id=thread_id,
        task_id=request.task_id,
        todo_id=request.todo_id,
        mode=request.mode,
        force=effective_force,
    )

    # Handle error responses from the manager
    if "error" in result:
        status_code = result.get("status", 500)
        if status_code == 409:
            raise HTTPException(status_code=409, detail=result["error"])
        elif status_code == 404:
            raise HTTPException(status_code=404, detail=result["error"])
        else:
            raise HTTPException(status_code=status_code, detail=result["error"])

    return ThreadBindResponse(
        thread_id=result["thread_id"],
        task_id=result.get("task_id"),
        todo_id=result.get("todo_id"),
        context_version=result["context_version"],
    )
