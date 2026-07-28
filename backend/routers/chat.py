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
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from schemas.message import ChatRequest, ChatSessionResponse, AnswerQuestionRequest, ChatMessageResponse
from schemas.chat_thread import ChatThreadResponse
from schemas.context import ThreadBindRequest, ThreadBindResponse
from schemas.permission import PermissionResponseRequest
from database import db
from core.agent_defaults import agent_exists
from core.session_utils import _build_error_event
from core.permission_manager import permission_manager as _pm
from core.chat_thread_manager import chat_thread_manager
from core.session_manager import session_manager

# ── Multi-session architecture ────────────────────────────────────
import os as _os
import logging as _logging
from typing import Optional

_chat_logger = _logging.getLogger(__name__)

# Lazy-initialized singletons — resolved on first chat request
_session_router = None
_lifecycle_manager = None


def _recover_streaming_on_disconnect(session_id: Optional[str]) -> None:
    """Transition a STREAMING session to IDLE after SSE client disconnect
    and schedule subprocess pipe cleanup.

    When the SSE connection drops (network blip, browser stall timeout,
    tab close), the consumer_task in sse_with_heartbeat is cancelled,
    propagating CancelledError into message_generator. Without this
    cleanup, the session stays in STREAMING — the next send() triggers
    force_unstick_streaming → kill → respawn --resume, replaying the
    previous turn's completed output as if it were new content.

    Two-phase cleanup:

    1. **Immediate** — transition STREAMING → IDLE so the next ``send()``
       follows the normal IDLE → STREAMING path instead of the
       force_unstick → kill → --resume replay path.
    2. **Background** — schedule ``_cleanup_subprocess_after_disconnect``
       to interrupt the CLI subprocess.  The subprocess may still be
       running a tool from the interrupted turn; its stdout pipe may
       contain stale events that would contaminate the next ``send()``'s
       ``receive_response()`` iterator.  Interrupting flushes the pipe;
       if the interrupt times out the subprocess is killed so the next
       ``send()`` spawns fresh from COLD.

    This is a best-effort, fire-and-forget cleanup. Errors are logged
    but never propagated.
    """
    if not session_id or _session_router is None:
        return
    try:
        unit = _session_router.get_unit(session_id)
        if unit and unit.recover_from_disconnect():
            _chat_logger.info(
                "SSE disconnect recovery: session %s transitioned "
                "STREAMING → IDLE",
                session_id,
            )
            # Phase 2: schedule subprocess pipe cleanup via unit method.
            # Unit tracks the task so send() can cancel it on quick resume.
            try:
                loop = asyncio.get_running_loop()
                unit.schedule_pipe_flush(
                    loop,
                    cleanup_coro=_cleanup_subprocess_after_disconnect(unit, session_id),
                )
            except RuntimeError:
                pass  # No running event loop — skip async cleanup
    except Exception as e:
        _chat_logger.warning(
            "SSE disconnect recovery failed for session %s: %s",
            session_id, e,
        )


async def _cleanup_subprocess_after_disconnect(
    unit: "SessionUnit",  # noqa: F821 — forward ref, resolved at runtime
    session_id: str,
) -> None:
    """Background task: attempt soft interrupt of subprocess after SSE disconnect.

    The state machine has already been transitioned to IDLE by
    ``recover_from_disconnect()``.  This delegates to the unit's
    ``flush_subprocess_pipe()`` which attempts a soft interrupt.

    On timeout the subprocess is LEFT ALIVE (not killed) — it's likely
    executing a tool call whose output will be persisted to DB by
    session_router._persist_assistant_blocks. The frontend reconciliation
    polling (every 15s) will recover the content from DB.

    Timeout is generous (30s) to allow most tool calls to complete.
    If the tool call takes longer, the subprocess stays alive — the
    lifecycle_manager's 12hr TTL handles actual zombies.
    """
    try:
        await unit.flush_subprocess_pipe(timeout=30.0)
    except asyncio.CancelledError:
        pass  # App shutting down — don't log noise
    except Exception as e:
        _chat_logger.warning(
            "SSE disconnect cleanup failed for session %s: %s",
            session_id, e,
        )


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
    AgentExecutionException,
    AgentTimeoutException,
    ResourceExhaustedException,
)
import json
import asyncio
import logging
import re as _re
import time
from datetime import datetime
from typing import AsyncIterator, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Evolution marker parsing
# ---------------------------------------------------------------------------

_EVOLUTION_MARKER_RE = _re.compile(
    r"<!--\s*EVOLUTION_EVENT:\s*(.+?)\s*-->",
    _re.DOTALL,
)


def _extract_evolution_events(message: dict) -> list[dict]:
    """Extract evolution event markers from a message's text content.

    Searches for ``<!-- EVOLUTION_EVENT: {...} -->`` patterns in the
    message text and returns parsed JSON payloads as event dicts.
    Malformed markers are silently ignored.

    Args:
        message: SSE message dict from the agent.

    Returns:
        List of evolution event dicts (may be empty).
    """
    events: list[dict] = []
    # Look for text content in common message fields
    text = ""
    if isinstance(message.get("content"), str):
        text = message["content"]
    elif isinstance(message.get("text"), str):
        text = message["text"]
    elif isinstance(message.get("content"), list):
        # Content blocks format
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                text += block.get("text", "")

    if not text:
        return events

    for match in _EVOLUTION_MARKER_RE.finditer(text):
        try:
            payload = json.loads(match.group(1))
            if isinstance(payload, dict) and "event" in payload:
                # Normalize "event" → "type" so the frontend SSE handler
                # (which switches on event.type) recognises evolution events.
                payload["type"] = payload.pop("event")
                events.append(payload)
        except (json.JSONDecodeError, KeyError):
            logger.debug(
                "Ignoring malformed evolution marker: %s",
                match.group(0)[:100],
            )

    return events

router = APIRouter()
chat_threads_router = APIRouter()

# SSE heartbeat interval in seconds (keeps connection alive during long operations)
SSE_HEARTBEAT_INTERVAL = 15

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
                await message_queue.put(("message", msg))
        except Exception as e:
            await message_queue.put(("error", e))
        finally:
            # Signal completion by putting a sentinel value
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

                    # Check for evolution event markers embedded in agent output
                    for evo_event in _extract_evolution_events(item):
                        try:
                            yield f"data: {json.dumps(evo_event)}\n\n"
                        except (TypeError, ValueError):
                            pass  # Skip non-serializable evolution events
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
                        logger.debug("Sending SSE heartbeat")
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
        # Ensure the consumer task is properly cleaned up
        if not consumer_task.done():
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass


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
        # (streaming_orchestrator.py:594, camelCase `sessionId`). Capture it so a
        # mid-stream client drop recovers the SERVER-created session — passing
        # chat_request.session_id (None) here made recovery a no-op and left the
        # new session stuck STREAMING (run_1c0a1da5).
        # KNOWN RESIDUAL: a drop BEFORE session_start is yielded still leaves
        # captured=None (the unit exists from get_or_create_unit but hasn't
        # emitted its id yet). Follow-up: router-owned recovery keyed off the
        # uuid it generates, so no caller ever passes None.
        captured_session_id = chat_request.session_id
        try:
            logger.info(f"Starting chat stream for agent {chat_request.agent_id}")
            async for msg in _get_router().run_conversation(
                agent_id=chat_request.agent_id,
                user_message=chat_request.message,
                content=chat_request.content,
                session_id=chat_request.session_id,
                enable_skills=chat_request.enable_skills,
                enable_mcp=chat_request.enable_mcp,
                editor_context=chat_request.editor_context.model_dump() if chat_request.editor_context else None,
                terminal_context=chat_request.terminal_context.model_dump() if chat_request.terminal_context else None,
                client_id=chat_request.client_id,
            ):
                if captured_session_id is None:
                    captured_session_id = msg.get("sessionId") or msg.get("session_id")
                logger.debug(f"Yielding message: {msg.get('type')}")
                yield msg
        except asyncio.CancelledError:
            logger.info("Chat stream cancelled (client disconnected)")
            # Transition session STREAMING → IDLE so the next send()
            # doesn't force-unstick and replay the previous turn via --resume.
            # Use the captured server-assigned id (not the request's None).
            _recover_streaming_on_disconnect(captured_session_id)
            return
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
        except asyncio.CancelledError:
            logger.info("Answer-question stream cancelled (client disconnected)")
            _recover_streaming_on_disconnect(answer_request.session_id)
            return
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


@router.get("/sessions/streaming-state")
async def get_streaming_state_endpoint():
    """Return the streaming state for all active sessions.

    Frontend polls this every 15s (while any tab is streaming) to reconcile
    stale isStreaming state when SSE events are lost. Returns all non-prewarm
    sessions — frontend iterates its own tab map and indexes by session ID.

    **State desync (Root-1 SSOT Phase 2, Option B):** After an SSE disconnect the
    unit transitions to a CLEAN IDLE immediately (so the next send() works) — there
    is NO generating-limbo flag, so `streaming` is simply `state == STREAMING` and a
    disconnected-but-still-flushing turn reports `streaming=false, state='idle'`.
    The subprocess is left alive (1A) to finish a long turn; its output persists to
    DB and loads on the next reconcile. To let the frontend distinguish
    "alive-but-flushing" from "genuinely done" (else it surfaces a false
    'Connection lost' error at heal-grace expiry — OT01), we ALSO emit
    `post_disconnect_flushing` (true iff `is_post_disconnect_flushing`, derived from
    the live `_pipe_flush_task`). The reconcile loop keeps waiting while it is true.

    Must be registered BEFORE /sessions/{session_id} to avoid path parameter
    capturing 'streaming-state' as a session ID.
    """
    from core.session_router import PREWARM_SESSION_PREFIX

    sr = _get_router()
    result: dict[str, dict] = {}
    for unit in sr.list_units():
        if not unit.session_id or unit.session_id.startswith(PREWARM_SESSION_PREFIX):
            continue
        # Root-1 SSOT Phase 2 (L6, Option B): streaming is now simply
        # state==STREAMING. A disconnect yields a CLEAN IDLE (no generating-limbo
        # flag), so there is no special case. If the subprocess is still finishing
        # a long turn post-disconnect (is_post_disconnect_flushing), the mirror
        # shows IDLE and the completed content loads from DB on the next reconcile.
        is_streaming = unit.state.value == "streaming"
        # Root-1 SSOT Phase 2 (L5): the frontend mirror reads these directly
        # instead of inferring. waiting_input surfaces a (possibly SSE-lost)
        # AskUserQuestion; pending_count drives the "queued" badge; pending_question
        # lets the FE RE-RENDER the question from authoritative state even when the
        # original ask_user_question SSE event was dropped (F5).
        try:
            from core import session_pending
            pending_count = await session_pending.count_pending(unit.session_id)
        except Exception:
            pending_count = 0

        # The primary source is the transient _pending_question (set on the
        # WAITING_INPUT emit). But that field — and state==waiting_input — are
        # LOST if the surface SSE event never fired (the hang bug: the permission
        # was enqueued but the orchestrator's surface block never ran) or after a
        # respawn. Fix #1B: when _pending_question is None, fall back to the
        # DURABLE permission store, but ONLY if a live waiter still exists for the
        # request (has_live_waiter) — otherwise the hook is dead and re-surfacing
        # would let the user "approve" into the void.
        pending_question = (
            getattr(unit, "_pending_question", None)
            if unit.state.value == "waiting_input" else None
        )
        if pending_question is None:
            try:
                from core.permission_manager import permission_manager as _pm
                for req in _pm.get_pending_for_session(unit.session_id):
                    req_id = req.get("id")
                    if req_id and _pm.has_live_waiter(req_id):
                        raw_input = req.get("tool_input", {})
                        # Durable store keeps tool_input as a JSON string
                        # (security_hooks persist); _pending_question uses a dict.
                        if isinstance(raw_input, str):
                            try:
                                raw_input = json.loads(raw_input)
                            except (ValueError, TypeError):
                                raw_input = {}
                        pending_question = {
                            "tool_use_id": req_id,
                            "request_id": req_id,
                            "tool_name": req.get("tool_name", "Bash"),
                            "tool_input": raw_input,
                            "reason": req.get("reason", ""),
                            "options": req.get("options", ["approve", "deny"]),
                        }
                        break  # surface the oldest live pending request
            except Exception:
                pending_question = None

        # Honest-signal (OT01): expose whether the subprocess is still finishing a
        # long turn post-disconnect. The unit is CLEAN-IDLE (streaming=false) but
        # alive; without this the frontend's heal-grace expiry surfaces a false
        # "Connection lost" error while the answer is still being produced. getattr
        # fail-safe to False so an older/mocked unit lacking the property never
        # falsely claims flushing.
        try:
            post_disconnect_flushing = bool(unit.is_post_disconnect_flushing)
        except Exception:
            post_disconnect_flushing = False

        result[unit.session_id] = {
            "streaming": is_streaming,
            "state": unit.state.value,
            # pid = the owned Claude subprocess pid (unit.pid → _wrapper.pid),
            # None when no live subprocess. The session-health-probe's wedged
            # check needs it to sample tree CPU; a None pid → probe skips the
            # session (fail-safe). Additive field — frontend ignores unknowns.
            "pid": unit.pid,
            "post_disconnect_flushing": post_disconnect_flushing,
            # waiting_input must also reflect a re-surfaced durable request, else
            # the frontend won't render the prompt it was just handed.
            "waiting_input": unit.state.value == "waiting_input" or pending_question is not None,
            "pending_count": pending_count,
            "pending_question": pending_question,
            "last_drained_seqs": getattr(unit, "_last_drained_seqs", []),
        }
    return {"sessions": result}


@router.get("/sessions/{session_id}/sub-agent-progress")
async def get_sub_agent_progress(session_id: str):
    """Return progress info when a sub-agent (Agent tool) is active.

    Frontend polls this every 5s while streaming to render tiered
    awareness banners (T0-T4) based on elapsed time. Lightweight:
    single field read, no computation.
    """
    import time as _time

    sr = _get_router()
    unit = sr.get_unit(session_id)
    if not unit:
        raise HTTPException(status_code=404, detail="Session not found")

    # Snapshot the dict — prevents TOCTOU if .pop() mutates during iteration
    active_tools = unit._active_agent_tools.copy()
    is_streaming = unit.state.value == "streaming"

    if not active_tools or not is_streaming:
        return {"active": False, "elapsed_s": 0, "label": None, "count": 0}

    # elapsed_s tracks the OLDEST (longest-running) sub-agent — this is the
    # "is something stuck?" signal that drives the tiered awareness banner.
    # label tracks the NEWEST sub-agent — so the banner reflects CURRENT
    # activity instead of freezing on the first-spawned one. With parallel
    # reviewers (Gate 2 spawns several), anchoring both on the oldest made
    # the label stick on "Spec compliance review" for the whole run.
    oldest = active_tools[min(active_tools, key=lambda k: active_tools[k]["start_time"])]
    newest = active_tools[max(active_tools, key=lambda k: active_tools[k]["start_time"])]
    elapsed = _time.time() - oldest["start_time"]
    return {
        "active": True,
        "elapsed_s": round(elapsed, 1),
        "label": newest.get("label"),
        "count": len(active_tools),
    }


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


def _merge_consecutive_assistant_messages(messages: list[dict]) -> list[dict]:
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
    """
    if not messages:
        return []

    merged: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if (
            role == "assistant"
            and merged
            and merged[-1].get("role") == "assistant"
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
            merged.append(entry)

    return merged


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    request: Request,
    limit: Optional[int] = Query(None, ge=1, le=200),
    before_id: Optional[str] = Query(None),
):
    """Get messages for a chat session with optional cursor-based pagination.

    Supports ETag caching: the ETag is ``"session_id:msg_count"``.
    Messages are append-only, so count change = content change.
    Frontend sends ``If-None-Match`` → 304 when unchanged (no message
    fetch, no serialization).  ETag skipped for cursor-paginated requests.

    Returns messages in chronological order.
    """
    from fastapi.responses import JSONResponse, Response as FastAPIResponse

    # Verify session exists
    session = await session_manager.get_session(session_id)
    if not session:
        raise SessionNotFoundException(
            detail=f"Session with ID '{session_id}' does not exist",
            suggested_action="Please check the session ID and try again"
        )

    # ── ETag: count query → 304 if unchanged ──
    # Only for non-cursor queries (before_id changes per request).
    etag: str | None = None
    if before_id is None:
        msg_count = await db.messages.count_by_session(session_id)
        etag = f'"{session_id}:{msg_count}"'
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match == etag:
            return FastAPIResponse(status_code=304, headers={"ETag": etag})

    # ── Fetch messages ──
    if limit is not None or before_id is not None:
        messages = await db.messages.list_by_session_paginated(
            session_id, limit=limit, before_id=before_id
        )
    else:
        messages = await db.messages.list_by_session(session_id)

    # Merge consecutive assistant messages into one.
    # The backend persists each agentic turn as a separate DB row (crash safety),
    # but the frontend expects one assistant bubble per user→agent exchange.
    # Without merging, loadSessionMessages renders 5-20 separate assistant bubbles
    # with overlapping text content for a single response.
    data = _merge_consecutive_assistant_messages(messages)

    headers = {"ETag": etag} if etag else {}
    return JSONResponse(content=data, headers=headers)


@router.post("/stop/{session_id}")
async def stop_session(session_id: str):
    """Stop a running chat session.

    This will interrupt the currently running agent for the given session.
    The agent will stop processing and the stream will end gracefully.
    """
    logger.info(f"Received stop request for session {session_id}")
    result = await _get_router().interrupt_session(session_id)

    if result["success"]:
        return {"status": "stopped", "message": result["message"]}
    else:
        # Return 200 even if session not found - client may have already finished
        return {"status": "not_found", "message": result["message"]}


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
        Without force, an active session is left untouched (``skipped_active``)
        because the SSE-disconnect recovery path already handles it.
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
    return result


@router.post("/refresh/{session_id}")
async def refresh_session(session_id: str):
    """Refresh a session's context by killing subprocess and preparing for resume.

    This is a user-triggered "same-tab restart": the subprocess is killed,
    but _sdk_session_id is preserved so the next send() will use --resume,
    injecting a structured summary of the conversation into the new context.

    The frontend should:
    1. Insert a visual separator in the chat
    2. Dim old messages
    3. Send the next user message normally (which triggers auto-resume)
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
            )
            _get_router()  # Ensure initialized
            if _lifecycle_manager:
                _lifecycle_manager.enqueue_hooks(context)
    except Exception as exc:
        logger.warning("Hook fire failed for delete_session %s: %s", session_id, exc)

    # 2. Clean up SessionUnit subprocess
    unit = _get_router().get_unit(session_id)
    if unit and unit.is_alive:
        await unit.kill()

    # 3. Delete messages and session from DB
    await db.messages.delete_by_session(session_id)
    deleted = await session_manager.delete_session(session_id)
    if not deleted:
        raise SessionNotFoundException(
            detail=f"Session with ID '{session_id}' does not exist",
            suggested_action="Please check the session ID and try again"
        )

    # 4. Clean up per-session state to prevent unbounded memory growth
    from core.lifecycle_manager import LifecycleManager
    LifecycleManager._release_session_state(session_id)


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
    from core.permission_manager import permission_manager as _pm
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
        except asyncio.CancelledError:
            logger.info("Permission-continue stream cancelled (client disconnected)")
            _recover_streaming_on_disconnect(permission_request.session_id)
            return
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
