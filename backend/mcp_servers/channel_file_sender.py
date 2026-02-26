"""MCP stdio server that provides channel file-sending tools to agents.

This script is run as a subprocess by Claude Code. It reads configuration
from environment variables and exposes ``send_file`` as an MCP tool so that
agents running in a channel context can send files (documents, images, etc.)
back to users via the channel (e.g. Feishu).

Environment variables:
    CHANNEL_TYPE        — adapter type (e.g. "feishu")
    FEISHU_APP_ID       — Feishu app credentials
    FEISHU_APP_SECRET   — Feishu app credentials
    CHAT_ID             — target chat to send files/images to
    REPLY_TO_MESSAGE_ID — (optional) message to reply to
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Feishu helpers (lazy-imported so the server can still start if lark_oapi is
# missing — the tool will simply return an error on invocation)
# ---------------------------------------------------------------------------

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

_FILE_TYPE_MAP = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}

_MAX_IMAGE_SIZE = 10 * 1024 * 1024   # 10 MB
_MAX_FILE_SIZE = 30 * 1024 * 1024    # 30 MB


def _is_image(file_path: Path) -> bool:
    return file_path.suffix.lower() in _IMAGE_EXTENSIONS


def _feishu_file_type(file_path: Path) -> str:
    return _FILE_TYPE_MAP.get(file_path.suffix.lower(), "stream")


# ---------------------------------------------------------------------------
# Feishu sending logic
# ---------------------------------------------------------------------------

def _send_via_feishu(file_path: Path, message: str) -> str:
    """Upload a file/image to Feishu and send it to the configured chat."""
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import (
            CreateFileRequest,
            CreateFileRequestBody,
            CreateImageRequest,
            CreateImageRequestBody,
            CreateMessageRequest,
            CreateMessageRequestBody,
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )
    except ImportError:
        return "Error: lark_oapi is not installed. Cannot send files via Feishu."

    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    chat_id = os.environ.get("CHAT_ID", "")
    reply_to = os.environ.get("REPLY_TO_MESSAGE_ID", "")

    if not app_id or not app_secret:
        return "Error: FEISHU_APP_ID and FEISHU_APP_SECRET are required."
    if not chat_id:
        return "Error: CHAT_ID is required."

    client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()

    is_image = _is_image(file_path)

    # --- Upload -----------------------------------------------------------
    if is_image:
        with file_path.open("rb") as f:
            req = (
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(f)
                    .build()
                )
                .build()
            )
            resp = client.im.v1.image.create(req)
        if not resp.success():
            return f"Error uploading image: {resp.code} - {resp.msg}"
        image_key = resp.data.image_key

        # --- Send image message -------------------------------------------
        content_payload = json.dumps({"image_key": image_key})
        msg_type = "image"
    else:
        file_type = _feishu_file_type(file_path)
        with file_path.open("rb") as f:
            req = (
                CreateFileRequest.builder()
                .request_body(
                    CreateFileRequestBody.builder()
                    .file_type(file_type)
                    .file_name(file_path.name)
                    .file(f)
                    .build()
                )
                .build()
            )
            resp = client.im.v1.file.create(req)
        if not resp.success():
            return f"Error uploading file: {resp.code} - {resp.msg}"
        file_key = resp.data.file_key

        # --- Send file message --------------------------------------------
        content_payload = json.dumps({"file_key": file_key})
        msg_type = "file"

    # --- Deliver the message (reply or new) -------------------------------
    if reply_to:
        req = (
            ReplyMessageRequest.builder()
            .message_id(reply_to)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type(msg_type)
                .content(content_payload)
                .build()
            )
            .build()
        )
        resp = client.im.v1.message.reply(req)
    else:
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type(msg_type)
                .content(content_payload)
                .build()
            )
            .build()
        )
        resp = client.im.v1.message.create(req)

    if not resp.success():
        return f"Error sending {msg_type} message: {resp.code} - {resp.msg}"

    result = f"Successfully sent {msg_type} '{file_path.name}' to the chat."

    # --- Optionally send an accompanying text message ---------------------
    if message:
        text_content = json.dumps({"text": message})
        if reply_to:
            text_req = (
                ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("text")
                    .content(text_content)
                    .build()
                )
                .build()
            )
            text_resp = client.im.v1.message.reply(text_req)
        else:
            text_req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(text_content)
                    .build()
                )
                .build()
            )
            text_resp = client.im.v1.message.create(text_req)
        if not text_resp.success():
            result += f" (Warning: accompanying text message failed: {text_resp.code} - {text_resp.msg})"

    return result


# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

server = FastMCP("channel-tools")


@server.tool()
def send_file(file_path: str, message: str = "") -> str:
    """Send a file from the workspace to the user via the channel.

    Supports images (png, jpg, jpeg, gif, webp, bmp) and general files
    (pdf, doc/docx, xls/xlsx, ppt/pptx, and others). Images are rendered
    inline; other files are sent as downloadable attachments.

    Args:
        file_path: Absolute path to the file to send.
        message: Optional text message to accompany the file.

    Returns:
        A status string indicating success or describing the error.
    """
    path = Path(file_path).resolve()

    # Validate path is within workspace if WORKSPACE_DIR is set
    workspace = os.environ.get("WORKSPACE_DIR", "")
    if workspace:
        workspace_root = Path(workspace).resolve()
        if not str(path).startswith(str(workspace_root) + os.sep) and path != workspace_root:
            return f"Error: File path must be within the agent workspace: {workspace}"

    # Validate existence
    if not path.exists():
        return f"Error: File not found: {file_path}"
    if not path.is_file():
        return f"Error: Path is not a file: {file_path}"

    # Validate size
    size = path.stat().st_size
    if _is_image(path):
        if size > _MAX_IMAGE_SIZE:
            return (
                f"Error: Image '{path.name}' is {size / (1024*1024):.1f} MB, "
                f"which exceeds the 10 MB limit for images."
            )
    else:
        if size > _MAX_FILE_SIZE:
            return (
                f"Error: File '{path.name}' is {size / (1024*1024):.1f} MB, "
                f"which exceeds the 30 MB limit for files."
            )

    channel_type = os.environ.get("CHANNEL_TYPE", "")

    if channel_type == "feishu":
        return _send_via_feishu(path, message)
    else:
        return f"Error: Unsupported channel type '{channel_type}'. Currently only 'feishu' is supported."


if __name__ == "__main__":
    server.run()
