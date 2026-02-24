"""Workspace router for file browser API."""

import base64
import mimetypes
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from config import settings
from schemas.workspace import (
    WorkspaceFileInfo,
    WorkspaceFileResponse,
    WorkspaceListRequest,
    WorkspaceListResponse,
    WorkspaceUploadRequest,
    WorkspaceUploadResponse,
)

router = APIRouter(tags=["workspace"])

# File size limits
MAX_TEXT_FILE_SIZE = 1 * 1024 * 1024  # 1MB for text files
MAX_IMAGE_FILE_SIZE = 5 * 1024 * 1024  # 5MB for images

# Text file extensions (for encoding detection)
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".css",
    ".scss",
    ".less",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".sql",
    ".env",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
    ".eslintrc",
    ".prettierrc",
    "Dockerfile",
    "Makefile",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".log",
    ".csv",
}

# Image file extensions
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp"}


def get_workspace_root(agent_id: str, base_path: str | None = None) -> Path:
    """Get the workspace root directory for an agent.

    Args:
        agent_id: The agent ID
        base_path: Optional custom base path. If provided, uses this instead of
                   the default agent workspace. Used for "work in a folder" feature.

    Returns:
        The workspace root path
    """
    if base_path:
        return Path(base_path)
    return Path(settings.agent_workspaces_dir) / agent_id


def validate_path(workspace_root: Path, requested_path: str) -> Path:
    """Validate that the requested path is within the workspace.

    Args:
        workspace_root: The workspace root directory
        requested_path: The relative path requested

    Returns:
        The resolved absolute path

    Raises:
        HTTPException: If path traversal is detected or path doesn't exist
    """
    # Normalize the requested path to prevent .. traversal
    # Use os.path.normpath to handle .. without following symlinks
    import os
    normalized = os.path.normpath(requested_path)

    # Check for path traversal attempts (paths that would escape workspace)
    if normalized.startswith('..') or normalized.startswith('/'):
        raise HTTPException(
            status_code=403, detail="Access denied: path traversal detected"
        )

    # Build the full path without resolving symlinks first
    full_path = workspace_root / normalized

    # Resolve workspace root (but not the full path, to allow symlinks within)
    workspace_resolved = workspace_root.resolve()

    # For the security check, resolve full_path but handle non-existent paths
    try:
        # resolve(strict=False) doesn't require the path to exist
        full_path_resolved = full_path.resolve()
    except (OSError, ValueError):
        # If resolve fails, use the non-resolved path
        full_path_resolved = full_path

    # Check if the resolved path starts with workspace
    # Also allow the path if it's directly under workspace (before symlink resolution)
    full_path_str = str(full_path_resolved)
    workspace_str = str(workspace_resolved)

    # Path is valid if:
    # 1. It's within the workspace after resolution, OR
    # 2. The non-resolved path is within workspace (allows symlinks)
    is_within_workspace = (
        full_path_str.startswith(workspace_str + os.sep) or
        full_path_str == workspace_str or
        str(full_path).startswith(str(workspace_root))
    )

    if not is_within_workspace:
        raise HTTPException(
            status_code=403, detail="Access denied: path traversal detected"
        )

    return full_path


def is_text_file(path: Path) -> bool:
    """Check if a file is likely a text file based on extension."""
    suffix = path.suffix.lower()
    name = path.name.lower()
    return suffix in TEXT_EXTENSIONS or name in TEXT_EXTENSIONS


def is_image_file(path: Path) -> bool:
    """Check if a file is an image based on extension."""
    return path.suffix.lower() in IMAGE_EXTENSIONS


@router.post("/browse", response_model=WorkspaceListResponse)
async def browse_filesystem(request: WorkspaceListRequest):
    """Browse the server filesystem for folder selection.

    This endpoint allows browsing absolute paths on the server,
    used by FolderPickerModal in web browser mode.

    Args:
        request: The list request containing the absolute path to browse

    Returns:
        WorkspaceListResponse with directories and navigation info
    """
    import os

    # Use home directory as default starting point
    home_dir = Path.home()

    # Parse the requested path
    requested_path = request.path
    if requested_path == "." or requested_path == "/" or not requested_path:
        target_path = home_dir
    else:
        # Handle paths - could be absolute or relative to home
        if requested_path.startswith("/"):
            target_path = Path(requested_path)
        else:
            target_path = home_dir / requested_path

    # Normalize the path
    target_path = Path(os.path.normpath(target_path))

    # Check if path exists and is a directory
    if not target_path.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {requested_path}")

    if not target_path.is_dir():
        raise HTTPException(
            status_code=400, detail=f"Path is not a directory: {requested_path}"
        )

    # List directory contents (directories only for folder picker)
    files: list[WorkspaceFileInfo] = []

    try:
        for item in sorted(target_path.iterdir(), key=lambda x: x.name.lower()):
            # Skip hidden files/directories (starting with .)
            if item.name.startswith('.'):
                continue
            try:
                stat = item.stat()
                files.append(
                    WorkspaceFileInfo(
                        name=item.name,
                        type="directory" if item.is_dir() else "file",
                        size=0 if item.is_dir() else stat.st_size,
                        modified=datetime.fromtimestamp(stat.st_mtime),
                    )
                )
            except (PermissionError, OSError):
                # Skip files we can't access
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    # Calculate current path (absolute)
    current_path = str(target_path)

    # Calculate parent path (None if at filesystem root)
    parent_path = None
    if target_path.parent != target_path:  # Not at root
        parent_path = str(target_path.parent)

    return WorkspaceListResponse(
        files=files,
        current_path=current_path,
        parent_path=parent_path,
    )


@router.post("/{agent_id}/list", response_model=WorkspaceListResponse)
async def list_files(
    agent_id: str,
    request: WorkspaceListRequest,
    base_path: str | None = Query(None, description="Custom base path for file browser")
):
    """List files and directories in the specified path.

    Args:
        agent_id: The agent ID
        request: The list request containing the relative path
        base_path: Optional custom base path (e.g., from "work in a folder" selection)

    Returns:
        WorkspaceListResponse with files and navigation info
    """
    workspace_root = get_workspace_root(agent_id, base_path)

    # Check if workspace exists
    if not workspace_root.exists():
        raise HTTPException(status_code=404, detail=f"Workspace not found for agent: {agent_id}")

    # Validate and resolve the path
    target_path = validate_path(workspace_root, request.path)

    # Check if path exists and is a directory
    if not target_path.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {request.path}")

    if not target_path.is_dir():
        raise HTTPException(
            status_code=400, detail=f"Path is not a directory: {request.path}"
        )

    # List directory contents
    files: list[WorkspaceFileInfo] = []

    try:
        for item in sorted(target_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                stat = item.stat()
                files.append(
                    WorkspaceFileInfo(
                        name=item.name,
                        type="directory" if item.is_dir() else "file",
                        size=0 if item.is_dir() else stat.st_size,
                        modified=datetime.fromtimestamp(stat.st_mtime),
                    )
                )
            except (PermissionError, OSError):
                # Skip files we can't access
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    # Calculate current and parent paths
    # Don't resolve symlinks - use the logical path within the workspace
    # This allows symlinked directories (like skills) to work correctly
    try:
        relative_path = target_path.relative_to(workspace_root)
    except ValueError:
        # If target_path was resolved (e.g., /private/tmp on macOS), try resolving workspace_root too
        relative_path = target_path.resolve().relative_to(workspace_root.resolve())
    current_path = str(relative_path) if str(relative_path) != "." else "."

    # Calculate parent path (None if at root)
    parent_path = None
    if current_path != ".":
        parent = relative_path.parent
        parent_path = str(parent) if str(parent) != "." else "."

    return WorkspaceListResponse(
        files=files,
        current_path=current_path,
        parent_path=parent_path,
    )


@router.get("/{agent_id}/read", response_model=WorkspaceFileResponse)
async def read_file(
    agent_id: str,
    path: str = Query(..., description="Relative path to file"),
    base_path: str | None = Query(None, description="Custom base path for file browser")
):
    """Read the content of a file.

    Args:
        agent_id: The agent ID
        path: The relative path to the file
        base_path: Optional custom base path (e.g., from "work in a folder" selection)

    Returns:
        WorkspaceFileResponse with file content and metadata
    """
    workspace_root = get_workspace_root(agent_id, base_path)

    # Check if workspace exists
    if not workspace_root.exists():
        raise HTTPException(status_code=404, detail=f"Workspace not found for agent: {agent_id}")

    # Validate and resolve the path
    file_path = validate_path(workspace_root, path)

    # Check if file exists and is a file
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    if file_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is a directory: {path}")

    # Get file info
    try:
        stat = file_path.stat()
        file_size = stat.st_size
    except (PermissionError, OSError) as e:
        raise HTTPException(status_code=403, detail=f"Cannot access file: {e}")

    # Detect MIME type
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    # Determine if text or binary
    is_text = is_text_file(file_path)
    is_image = is_image_file(file_path)

    # Check file size limits
    if is_text and file_size > MAX_TEXT_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large for preview. Max size: {MAX_TEXT_FILE_SIZE // 1024}KB",
        )

    if is_image and file_size > MAX_IMAGE_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large for preview. Max size: {MAX_IMAGE_FILE_SIZE // 1024 // 1024}MB",
        )

    # Read file content
    try:
        if is_text:
            # Read as text
            try:
                content = file_path.read_text(encoding="utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                # Fallback to binary if UTF-8 fails
                content = base64.b64encode(file_path.read_bytes()).decode("ascii")
                encoding = "base64"
                mime_type = "application/octet-stream"
        elif is_image:
            # Read as base64 for images
            content = base64.b64encode(file_path.read_bytes()).decode("ascii")
            encoding = "base64"
        else:
            # Binary file - check size limit (use text file limit for safety)
            if file_size > MAX_TEXT_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"Binary file too large for preview. Max size: {MAX_TEXT_FILE_SIZE // 1024}KB",
                )
            # Read as base64
            content = base64.b64encode(file_path.read_bytes()).decode("ascii")
            encoding = "base64"
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {e}")

    return WorkspaceFileResponse(
        content=content,
        encoding=encoding,
        size=file_size,
        mime_type=mime_type,
    )


# Maximum upload file size (10MB for TXT/CSV)
MAX_UPLOAD_SIZE = 10 * 1024 * 1024

# Allowed upload extensions for text files
ALLOWED_UPLOAD_EXTENSIONS = {".txt", ".csv"}


@router.post("/{agent_id}/upload", response_model=WorkspaceUploadResponse)
async def upload_file(agent_id: str, request: WorkspaceUploadRequest):
    """Upload a file to the agent's workspace.

    This endpoint is used for uploading TXT/CSV files that Claude
    will read using the Read tool (rather than base64 embedding).

    Args:
        agent_id: The agent ID
        request: Upload request with filename, content (base64), and optional path

    Returns:
        WorkspaceUploadResponse with the saved file path
    """
    workspace_root = get_workspace_root(agent_id)

    # Check if workspace exists, create if not
    if not workspace_root.exists():
        try:
            workspace_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to create workspace: {e}")

    # Validate filename extension
    filename = request.filename
    suffix = Path(filename).suffix.lower()

    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Only {', '.join(ALLOWED_UPLOAD_EXTENSIONS)} files are supported for upload."
        )

    # Decode base64 content
    try:
        file_bytes = base64.b64decode(request.content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 content: {e}")

    # Check file size
    if len(file_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max size: {MAX_UPLOAD_SIZE // 1024 // 1024}MB"
        )

    # Validate and resolve the target directory
    target_dir = validate_path(workspace_root, request.path)

    # Create directory if it doesn't exist
    if not target_dir.exists():
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to create directory: {e}")

    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Target path is not a directory: {request.path}")

    # Generate unique filename if file already exists
    target_path = target_dir / filename
    counter = 1
    original_stem = target_path.stem
    original_suffix = target_path.suffix

    while target_path.exists():
        target_path = target_dir / f"{original_stem}_{counter}{original_suffix}"
        counter += 1

    # Write file
    try:
        target_path.write_bytes(file_bytes)
    except (PermissionError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {e}")

    # Calculate relative path from workspace root
    try:
        relative_path = target_path.relative_to(workspace_root)
    except ValueError:
        relative_path = target_path.resolve().relative_to(workspace_root.resolve())

    return WorkspaceUploadResponse(
        path=str(relative_path),
        filename=target_path.name,
        size=len(file_bytes)
    )
