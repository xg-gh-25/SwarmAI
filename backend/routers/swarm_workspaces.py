"""Swarm Workspaces API endpoints."""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from core.exceptions import ForbiddenException, NotFoundException
from core.swarm_workspace_manager import swarm_workspace_manager
from database import db
from schemas.swarm_workspace import SwarmWorkspaceCreate, SwarmWorkspaceResponse, SwarmWorkspaceUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/swarm-workspaces", tags=["swarm-workspaces"])


def _expand_workspace_path(workspace: dict) -> dict:
    """Expand {app_data_dir} placeholder in workspace file_path.
    
    The database stores paths with {app_data_dir} placeholder for portability.
    This function expands it to the actual filesystem path before returning to frontend.
    """
    if workspace and "file_path" in workspace:
        workspace = dict(workspace)  # Don't mutate original
        workspace["file_path"] = swarm_workspace_manager.expand_path(workspace["file_path"])
    return workspace


class SwarmWorkspaceNotFoundException(NotFoundException):
    """Raised when a swarm workspace is not found."""

    code = "SWARM_WORKSPACE_NOT_FOUND"
    message = "The requested workspace could not be found"


class SwarmWorkspaceForbiddenException(ForbiddenException):
    """Raised when an operation on a swarm workspace is forbidden."""

    code = "FORBIDDEN"
    message = "Cannot delete default workspace"


@router.get("", response_model=list[SwarmWorkspaceResponse])
async def list_workspaces(
    include_archived: bool = Query(False, description="Include archived workspaces in the list"),
):
    """List swarm workspaces, filtering out archived by default.

    By default only non-archived workspaces are returned, sorted with
    SwarmWS (is_default) first.  Pass ``include_archived=true`` to see
    archived workspaces as well.

    Validates: Requirements 36.3, 36.4
    """
    workspaces = await swarm_workspace_manager.list_all(db, include_archived=include_archived)
    return [_expand_workspace_path(w) for w in workspaces]


@router.get("/default", response_model=SwarmWorkspaceResponse)
async def get_default_workspace():
    """Get the default system workspace."""
    workspace = await db.swarm_workspaces.get_default()
    if not workspace:
        raise SwarmWorkspaceNotFoundException(
            detail="Default workspace is not configured",
            suggested_action="The default workspace should be auto-created on startup"
        )
    return _expand_workspace_path(workspace)


@router.get("/{workspace_id}", response_model=SwarmWorkspaceResponse)
async def get_workspace(workspace_id: str):
    """Get a specific workspace by ID."""
    workspace = await db.swarm_workspaces.get(workspace_id)
    if not workspace:
        raise SwarmWorkspaceNotFoundException(
            detail=f"Workspace with ID '{workspace_id}' does not exist",
            suggested_action="Please check the workspace ID and try again"
        )
    return _expand_workspace_path(workspace)


@router.post("", response_model=SwarmWorkspaceResponse, status_code=201)
async def create_workspace(request: SwarmWorkspaceCreate):
    """Create a new swarm workspace.

    Creates a new workspace with:
    - Generated UUID for id
    - Current timestamp for created_at and updated_at
    - Folder structure on the filesystem
    - Context files with templates

    Args:
        request: SwarmWorkspaceCreate with name, file_path, context, and optional icon

    Returns:
        The created workspace with 201 status

    Validates: Requirements 4.4, 6.4
    """
    # Generate UUID and timestamps
    now = datetime.now(timezone.utc).isoformat()
    workspace_data = {
        "id": str(uuid.uuid4()),
        "name": request.name,
        "file_path": request.file_path,
        "context": request.context,
        "icon": request.icon,
        "is_default": False,
        "created_at": now,
        "updated_at": now,
    }

    # Create folder structure on filesystem
    try:
        await swarm_workspace_manager.create_folder_structure(request.file_path)
        logger.info(f"Created folder structure for workspace at {request.file_path}")
    except Exception as e:
        logger.error(f"Failed to create folder structure for workspace: {e}")
        raise

    # Create context files (non-critical - log warning but don't fail)
    try:
        await swarm_workspace_manager.create_context_files(
            request.file_path,
            request.name
        )
        logger.info(f"Created context files for workspace '{request.name}'")
    except Exception as e:
        logger.warning(f"Failed to create context files for workspace: {e}")

    # Store workspace in database
    stored_workspace = await db.swarm_workspaces.put(workspace_data)
    logger.info(f"Created workspace '{request.name}' with id: {stored_workspace['id']}")

    return _expand_workspace_path(stored_workspace)


@router.put("/{workspace_id}", response_model=SwarmWorkspaceResponse)
async def update_workspace(workspace_id: str, request: SwarmWorkspaceUpdate):
    """Update an existing swarm workspace.

    Updates only the provided fields and updates the updated_at timestamp.

    Args:
        workspace_id: The ID of the workspace to update
        request: SwarmWorkspaceUpdate with optional name, file_path, context, icon

    Returns:
        The updated workspace

    Raises:
        SwarmWorkspaceNotFoundException: If workspace with given ID doesn't exist

    Validates: Requirements 3.5, 6.6
    """
    # Check if workspace exists
    existing_workspace = await db.swarm_workspaces.get(workspace_id)
    if not existing_workspace:
        raise SwarmWorkspaceNotFoundException(
            detail=f"Workspace with ID '{workspace_id}' does not exist",
            suggested_action="Please check the workspace ID and try again"
        )

    # Block writes on archived workspaces
    if existing_workspace.get("is_archived"):
        raise SwarmWorkspaceForbiddenException(
            code="WORKSPACE_ARCHIVED",
            detail="Cannot modify an archived workspace",
            suggested_action="Unarchive the workspace first to make changes"
        )

    # Build update data with only provided fields
    update_data = {}
    if request.name is not None:
        update_data["name"] = request.name
    if request.file_path is not None:
        update_data["file_path"] = request.file_path
    if request.context is not None:
        update_data["context"] = request.context
    if request.icon is not None:
        update_data["icon"] = request.icon

    # Always update the updated_at timestamp
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Merge with existing workspace data
    updated_workspace_data = {**existing_workspace, **update_data}

    # Store updated workspace in database
    stored_workspace = await db.swarm_workspaces.put(updated_workspace_data)
    logger.info(f"Updated workspace '{stored_workspace['name']}' with id: {workspace_id}")

    return _expand_workspace_path(stored_workspace)



@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(workspace_id: str):
    """Delete a swarm workspace.

    Deletes a workspace from the database. The default workspace cannot be deleted.

    Args:
        workspace_id: The ID of the workspace to delete

    Returns:
        204 No Content on success

    Raises:
        SwarmWorkspaceNotFoundException: If workspace with given ID doesn't exist
        SwarmWorkspaceForbiddenException: If attempting to delete the default workspace

    Validates: Requirements 1.3, 6.7, 6.8
    """
    # Check if workspace exists
    existing_workspace = await db.swarm_workspaces.get(workspace_id)
    if not existing_workspace:
        raise SwarmWorkspaceNotFoundException(
            detail=f"Workspace with ID '{workspace_id}' does not exist",
            suggested_action="Please check the workspace ID and try again"
        )

    # Check if workspace is the default workspace
    if existing_workspace.get("is_default", False):
        raise SwarmWorkspaceForbiddenException(
            detail="Cannot delete default workspace",
            suggested_action="The default workspace is protected and cannot be deleted"
        )

    # Delete workspace from database
    await db.swarm_workspaces.delete(workspace_id)
    logger.info(f"Deleted workspace '{existing_workspace['name']}' with id: {workspace_id}")

    # Return 204 No Content (FastAPI handles this automatically with status_code=204)
    return None

@router.post("/{workspace_id}/archive", response_model=SwarmWorkspaceResponse)
async def archive_workspace(workspace_id: str):
    """Archive a workspace, making it read-only and hidden from default lists.

    SwarmWS (the default workspace) cannot be archived.

    Args:
        workspace_id: The ID of the workspace to archive

    Returns:
        The updated workspace

    Raises:
        SwarmWorkspaceNotFoundException: If workspace doesn't exist
        SwarmWorkspaceForbiddenException: If workspace is the default (SwarmWS)

    Validates: Requirements 36.1, 36.2
    """
    try:
        updated = await swarm_workspace_manager.archive(workspace_id, db)
    except ValueError:
        raise SwarmWorkspaceNotFoundException(
            detail=f"Workspace with ID '{workspace_id}' does not exist",
            suggested_action="Please check the workspace ID and try again"
        )
    except PermissionError:
        raise SwarmWorkspaceForbiddenException(
            detail="Cannot archive the default workspace (SwarmWS)",
            suggested_action="The default workspace is always active and cannot be archived"
        )

    logger.info(f"Archived workspace '{workspace_id}'")
    return _expand_workspace_path(updated)


@router.post("/{workspace_id}/unarchive", response_model=SwarmWorkspaceResponse)
async def unarchive_workspace(workspace_id: str):
    """Unarchive a workspace, restoring full functionality.

    Args:
        workspace_id: The ID of the workspace to unarchive

    Returns:
        The updated workspace

    Raises:
        SwarmWorkspaceNotFoundException: If workspace doesn't exist

    Validates: Requirements 36.10
    """
    try:
        updated = await swarm_workspace_manager.unarchive(workspace_id, db)
    except ValueError:
        raise SwarmWorkspaceNotFoundException(
            detail=f"Workspace with ID '{workspace_id}' does not exist",
            suggested_action="Please check the workspace ID and try again"
        )

    logger.info(f"Unarchived workspace '{workspace_id}'")
    return _expand_workspace_path(updated)


@router.post("/{workspace_id}/init-folders", status_code=200)
async def init_workspace_folders(workspace_id: str):
    """Initialize or re-initialize folder structure for a workspace.

    Retrieves the workspace by ID and creates the standard folder structure
    at the workspace's file path. This can be used to:
    - Re-create folders that were accidentally deleted
    - Initialize folders for a workspace that was created without them

    Args:
        workspace_id: The ID of the workspace to initialize folders for

    Returns:
        dict with success message

    Raises:
        SwarmWorkspaceNotFoundException: If workspace with given ID doesn't exist

    Validates: Requirement 6.10
    """
    # Check if workspace exists
    workspace = await db.swarm_workspaces.get(workspace_id)
    if not workspace:
        raise SwarmWorkspaceNotFoundException(
            detail=f"Workspace with ID '{workspace_id}' does not exist",
            suggested_action="Please check the workspace ID and try again"
        )

    # Create folder structure
    try:
        await swarm_workspace_manager.create_folder_structure(workspace["file_path"])
        logger.info(f"Initialized folder structure for workspace '{workspace['name']}' at {workspace['file_path']}")
    except Exception as e:
        logger.error(f"Failed to initialize folder structure for workspace: {e}")
        raise

    return {"message": f"Folder structure initialized for workspace '{workspace['name']}'"}



