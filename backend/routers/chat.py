"""Chat SSE streaming API endpoints."""
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from schemas.message import ChatRequest, ChatSessionResponse, AnswerQuestionRequest, ChatMessageResponse
from schemas.permission import PermissionResponseRequest, PermissionRequestResponse
from database import db
from core.agent_manager import agent_manager, approve_command, set_permission_decision
from core.session_manager import session_manager
from core.exceptions import (
    AgentNotFoundException,
    SessionNotFoundException,
    ValidationException,
    AgentExecutionException,
    AgentTimeoutException,
)
import json
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter()


def create_sse_error(code: str, message: str, detail: str = None, suggested_action: str = None) -> str:
    """Create an SSE-formatted error event."""
    error_data = {
        "type": "error",
        "code": code,
        "message": message,
    }
    if detail:
        error_data["detail"] = detail
    if suggested_action:
        error_data["suggested_action"] = suggested_action
    return f"data: {json.dumps(error_data)}\n\n"


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

    # Verify agent exists
    agent = await db.agents.get(chat_request.agent_id)
    if not agent:
        raise AgentNotFoundException(
            detail=f"Agent with ID '{chat_request.agent_id}' does not exist",
            suggested_action="Please check the agent ID and try again"
        )

    async def generate():
        try:
            logger.info(f"Starting chat stream for agent {chat_request.agent_id}, add_dirs={chat_request.add_dirs}")
            async for msg in agent_manager.run_conversation(
                agent_id=chat_request.agent_id,
                user_message=chat_request.message,
                content=chat_request.content,
                session_id=chat_request.session_id,
                enable_skills=chat_request.enable_skills,
                enable_mcp=chat_request.enable_mcp,
                add_dirs=chat_request.add_dirs,
            ):
                logger.debug(f"Yielding message: {msg.get('type')}")
                yield f"data: {json.dumps(msg)}\n\n"
        except asyncio.TimeoutError:
            logger.error("Agent response timed out")
            yield create_sse_error(
                code="AGENT_TIMEOUT",
                message="Agent response timed out. Your conversation has been saved.",
                suggested_action="Please try again"
            )
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            error_message = str(e)
            logger.error(f"Error in chat stream: {error_message}")
            logger.error(f"Full traceback:\n{error_traceback}")
            # Determine error type and provide appropriate response
            if "timeout" in error_message.lower():
                yield create_sse_error(
                    code="AGENT_TIMEOUT",
                    message="Agent response timed out. Your conversation has been saved.",
                    suggested_action="Please try again"
                )
            elif "connection" in error_message.lower() or "network" in error_message.lower():
                yield create_sse_error(
                    code="SERVICE_UNAVAILABLE",
                    message="Unable to connect to the AI service",
                    detail=error_message,
                    suggested_action="Please check your connection and try again"
                )
            else:
                yield create_sse_error(
                    code="AGENT_EXECUTION_ERROR",
                    message="Agent execution failed",
                    detail=f"{error_message}\n\nTraceback:\n{error_traceback}",
                    suggested_action="Please try again or contact support"
                )

    return StreamingResponse(
        generate(),
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

    # Verify agent exists
    agent = await db.agents.get(answer_request.agent_id)
    if not agent:
        raise AgentNotFoundException(
            detail=f"Agent with ID '{answer_request.agent_id}' does not exist",
            suggested_action="Please check the agent ID and try again"
        )

    async def generate():
        try:
            logger.info(f"Answering question for agent {answer_request.agent_id}, session {answer_request.session_id}")
            async for msg in agent_manager.continue_with_answer(
                agent_id=answer_request.agent_id,
                session_id=answer_request.session_id,
                tool_use_id=answer_request.tool_use_id,
                answers=answer_request.answers,
                enable_skills=answer_request.enable_skills,
                enable_mcp=answer_request.enable_mcp,
            ):
                logger.debug(f"Yielding message: {msg.get('type')}")
                yield f"data: {json.dumps(msg)}\n\n"
        except asyncio.TimeoutError:
            logger.error("Agent response timed out")
            yield create_sse_error(
                code="AGENT_TIMEOUT",
                message="Agent response timed out. Your conversation has been saved.",
                suggested_action="Please try again"
            )
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            error_message = str(e)
            logger.error(f"Error in answer-question stream: {error_message}")
            logger.error(f"Full traceback:\n{error_traceback}")
            yield create_sse_error(
                code="AGENT_EXECUTION_ERROR",
                message="Agent execution failed",
                detail=f"{error_message}\n\nTraceback:\n{error_traceback}",
                suggested_action="Please try again or contact support"
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions", response_model=list[ChatSessionResponse])
async def list_sessions(agent_id: str | None = None):
    """List chat sessions, optionally filtered by agent_id.

    Returns sessions sorted by last_accessed descending.
    """
    sessions = await session_manager.list_sessions(agent_id=agent_id)
    return [
        ChatSessionResponse(
            id=s.session_id,
            agent_id=s.agent_id,
            title=s.title,
            created_at=s.created_at,
            last_accessed_at=s.last_accessed,
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
    )


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageResponse])
async def get_session_messages(session_id: str):
    """Get all messages for a chat session.

    Returns messages in chronological order.
    """
    # Verify session exists
    session = await session_manager.get_session(session_id)
    if not session:
        raise SessionNotFoundException(
            detail=f"Session with ID '{session_id}' does not exist",
            suggested_action="Please check the session ID and try again"
        )

    messages = await agent_manager.get_session_messages(session_id)
    return [
        ChatMessageResponse(
            id=msg.get("id"),
            session_id=msg.get("session_id"),
            role=msg.get("role"),
            content=msg.get("content", []),
            model=msg.get("model"),
            created_at=msg.get("created_at"),
        )
        for msg in messages
    ]


@router.post("/stop/{session_id}")
async def stop_session(session_id: str):
    """Stop a running chat session.

    This will interrupt the currently running agent for the given session.
    The agent will stop processing and the stream will end gracefully.
    """
    logger.info(f"Received stop request for session {session_id}")
    result = await agent_manager.interrupt_session(session_id)

    if result["success"]:
        return {"status": "stopped", "message": result["message"]}
    else:
        # Return 200 even if session not found - client may have already finished
        return {"status": "not_found", "message": result["message"]}


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str):
    """Delete a chat session and all its messages."""
    # First delete all messages for this session
    await db.messages.delete_by_session(session_id)

    # Then delete the session itself
    deleted = await session_manager.delete_session(session_id)
    if not deleted:
        raise SessionNotFoundException(
            detail=f"Session with ID '{session_id}' does not exist",
            suggested_action="Please check the session ID and try again"
        )


@router.post("/permission-response", response_model=PermissionRequestResponse)
async def handle_permission_response(request: PermissionResponseRequest):
    """Handle user's decision on a permission request (non-streaming).

    This endpoint is called when the user approves or denies a dangerous command
    that was flagged by the human approval hook. Use /permission-continue for
    streaming response.
    """
    logger.info(f"Received permission response for request {request.request_id}: {request.decision}")

    # Get the permission request from database
    permission_request = await db.permission_requests.get(request.request_id)
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

    # Update the permission request in database
    await db.permission_requests.update(
        request.request_id,
        {
            "status": request.decision + "d",  # "approved" or "denied"
            "decided_at": datetime.now().isoformat(),
            "user_feedback": request.feedback
        }
    )

    # If approved, add the command to the approved list for this session
    if request.decision == "approve":
        tool_input = permission_request.get("tool_input", {})
        # Handle both dict and JSON string (SQLite may have parsed it already)
        if isinstance(tool_input, str):
            tool_input = json.loads(tool_input)
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        if command:
            approve_command(request.session_id, command)
            logger.info(f"Command approved for session {request.session_id}: {command[:50]}...")

    # Signal any waiting tasks
    set_permission_decision(request.request_id, request.decision)

    return PermissionRequestResponse(
        status="recorded",
        request_id=request.request_id
    )


@router.post("/permission-continue")
async def permission_continue(request: Request):
    """Continue chat after user makes a permission decision via SSE.

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
    perm_req = await db.permission_requests.get(permission_request.request_id)
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

    # Verify agent exists
    agent = await db.agents.get(agent_id)
    if not agent:
        raise AgentNotFoundException(
            detail=f"Agent with ID '{agent_id}' does not exist",
            suggested_action="Please check the agent ID and try again"
        )

    async def generate():
        try:
            logger.info(f"Processing permission decision for request {permission_request.request_id}: {permission_request.decision}")
            async for msg in agent_manager.continue_with_permission(
                agent_id=agent_id,
                session_id=permission_request.session_id,
                request_id=permission_request.request_id,
                decision=permission_request.decision,
                feedback=permission_request.feedback,
                enable_skills=body.get("enable_skills", False),
                enable_mcp=body.get("enable_mcp", False),
            ):
                logger.debug(f"Yielding message: {msg.get('type')}")
                yield f"data: {json.dumps(msg)}\n\n"
        except asyncio.TimeoutError:
            logger.error("Agent response timed out")
            yield create_sse_error(
                code="AGENT_TIMEOUT",
                message="Agent response timed out. Your conversation has been saved.",
                suggested_action="Please try again"
            )
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            error_message = str(e)
            logger.error(f"Error in permission-continue stream: {error_message}")
            logger.error(f"Full traceback:\n{error_traceback}")
            yield create_sse_error(
                code="AGENT_EXECUTION_ERROR",
                message="Agent execution failed",
                detail=f"{error_message}\n\nTraceback:\n{error_traceback}",
                suggested_action="Please try again or contact support"
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
