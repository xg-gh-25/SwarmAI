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
from schemas.permission import PermissionResponseRequest, PermissionRequestResponse
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
            # Phase 2: schedule subprocess pipe cleanup
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    _cleanup_subprocess_after_disconnect(unit, session_id)
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
    """Background task: flush subprocess pipe after SSE disconnect.

    The state machine has already been transitioned to IDLE by
    ``recover_from_disconnect()``.  This delegates to the unit's
    ``flush_subprocess_pipe()`` which handles interrupt + kill fallback.
    """
    try:
        await unit.flush_subprocess_pipe(timeout=3.0)
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

    Args:
        message_generator: The async generator that yields message dicts
        heartbeat_interval: Seconds between heartbeats (default: 15)

    Yields:
        SSE-formatted strings (data messages and heartbeats)
    """
    message_queue: asyncio.Queue = asyncio.Queue()
    generator_done = False

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
            ):
                logger.debug(f"Yielding message: {msg.get('type')}")
                yield msg
        except asyncio.CancelledError:
            logger.info("Chat stream cancelled (client disconnected)")
            # Transition session STREAMING → IDLE so the next send()
            # doesn't force-unstick and replay the previous turn via --resume.
            _recover_streaming_on_disconnect(chat_request.session_id)
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

            answer_text = json.dumps(answer_request.answers) if answer_request.answers else ""
            async for msg in _get_router().continue_with_answer(
                session_id=answer_request.session_id,
                answer=answer_text,
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

    data = [
        {
            "id": msg.get("id"),
            "session_id": msg.get("session_id"),
            "role": msg.get("role"),
            "content": msg.get("content", []),
            "model": msg.get("model"),
            "created_at": msg.get("created_at"),
        }
        for msg in messages
    ]

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


@router.post("/cmd-permission-response", response_model=PermissionRequestResponse)
async def handle_cmd_permission_response(request: PermissionResponseRequest):
    """Handle user's decision on a permission request (non-streaming).

    This endpoint is called when the user approves or denies a dangerous command
    that was flagged by the human approval hook. Use /cmd-permission-continue for
    streaming response.

    NOTE: This endpoint uses the shared permission_manager singleton. The permission
    decision is delivered to the waiting subprocess via asyncio.Event signaling,
    independent of which SessionUnit holds the session.
    """
    logger.info(f"Received permission response for request {request.request_id}: {request.decision}")

    # Get the permission request from in-memory store
    from core.permission_manager import permission_manager as _pm
    permission_request = _pm.get_pending_request(request.request_id)
    if not permission_request:
        raise ValidationException(
            message="Permission request not found",
            detail=f"No pending permission request found with ID '{request.request_id}'"
        )

    # Verify session matches
    if permission_request.get("session_id") != request.session_id:
        raise ValidationException(
            message="Session mismatch",
            detail="The session ID does not match the permission request"
        )

    # Update the permission request in memory
    _pm.update_pending_request(
        request.request_id,
        {
            "status": request.decision + "d",  # "approved" or "denied"
            "decided_at": datetime.now().isoformat(),
            "user_feedback": request.feedback
        }
    )

    # If approved, store in per-session PermissionManager (in-memory only)
    if request.decision == "approve":
        tool_input = permission_request.get("tool_input", {})
        if isinstance(tool_input, str):
            tool_input = json.loads(tool_input)
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        if command:
            _pm.approve_command(request.session_id, command)
            logger.info(f"Command approved for session {request.session_id}: {command[:50]}...")

    # Signal any waiting tasks
    _pm.set_permission_decision(request.request_id, request.decision)

    # On deny: the hook unblocks and returns deny to the SDK, but nobody
    # is reading _read_formatted_response anymore (we returned from it
    # after yielding cmd_permission_request and transitioning to
    # WAITING_INPUT).  Interrupt the session to cleanly abort the
    # response cycle so the session transitions WAITING_INPUT → IDLE.
    if request.decision == "deny":
        unit = _get_router().get_unit(request.session_id)
        if unit and unit.state.value == "waiting_input":
            try:
                await unit.interrupt()
                logger.info(
                    "Session %s interrupted after permission deny "
                    "(WAITING_INPUT → IDLE)",
                    request.session_id,
                )
            except Exception as e:
                logger.warning(
                    "Failed to interrupt session %s after deny: %s",
                    request.session_id, e,
                )

    return PermissionRequestResponse(
        status="recorded",
        request_id=request.request_id
    )


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
        raise ValidationException(
            message="Permission request not found",
            detail=f"No pending permission request found with ID '{permission_request.request_id}'"
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
