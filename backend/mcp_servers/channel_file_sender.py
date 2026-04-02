"""MCP stdio server that provides channel file-sending tools to agents.

This script is run as a subprocess by Claude Code. It reads configuration
from environment variables and exposes ``send_file`` as an MCP tool so that
agents running in a channel context can send files (documents, images, etc.)
back to users via the channel (e.g. Slack).

Environment variables:
    CHANNEL_TYPE        — adapter type (e.g. "slack")
    SLACK_BOT_TOKEN     — Slack bot token (xoxb-)
    SLACK_CHANNEL_ID    — target Slack channel to send files to
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_MAX_IMAGE_SIZE = 10 * 1024 * 1024   # 10 MB
_MAX_FILE_SIZE = 30 * 1024 * 1024    # 30 MB


def _is_image(file_path: Path) -> bool:
    return file_path.suffix.lower() in _IMAGE_EXTENSIONS


# ---------------------------------------------------------------------------
# Slack sending logic
# ---------------------------------------------------------------------------

def _send_via_slack(file_path: Path, message: str) -> str:
    """Upload a file to Slack and send it to the configured channel."""
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        return "Error: slack_sdk is not installed. Cannot send files via Slack."

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel_id = os.environ.get("SLACK_CHANNEL_ID", "")

    if not bot_token:
        return "Error: SLACK_BOT_TOKEN is required."
    if not channel_id:
        return "Error: SLACK_CHANNEL_ID is required."

    client = WebClient(token=bot_token)

    try:
        resp = client.files_upload_v2(
            channel=channel_id,
            file=str(file_path),
            filename=file_path.name,
            initial_comment=message or None,
        )
        if resp.get("ok"):
            return f"Successfully sent '{file_path.name}' to the channel."
        return f"Error sending file: {resp.get('error', 'unknown error')}"
    except SlackApiError as e:
        return f"Error sending file via Slack: {e.response['error']}"


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

    if channel_type == "slack":
        return _send_via_slack(path, message)
    else:
        return f"Error: Unsupported channel type '{channel_type}'. Supported: 'slack'."


if __name__ == "__main__":
    server.run()
