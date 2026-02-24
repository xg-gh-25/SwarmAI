"""Skill CRUD API endpoints."""
import logging
import re
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from schemas.skill import (
    SkillCreateRequest,
    SkillGenerateRequest,
    SkillResponse,
    SyncResultResponse,
    SkillGenerateWithAgentRequest,
    SkillFinalizeRequest,
    SkillVersionResponse,
    SkillVersionListResponse,
    PublishDraftRequest,
    RollbackRequest,
)
from database import db
from core.skill_manager import skill_manager
from core.agent_manager import agent_manager
from core.exceptions import (
    SkillNotFoundException,
    ValidationException,
)
from config import settings
import asyncio
import json

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[SkillResponse])
async def list_skills():
    """List all skills with enriched source information."""
    skills = await db.skills.list()

    # Get plugins and marketplaces for enrichment
    plugins = {p["id"]: p for p in await db.plugins.list()}
    marketplaces = {m["id"]: m for m in await db.marketplaces.list()}

    # Enrich skills with source names
    enriched_skills = []
    for skill in skills:
        skill_dict = dict(skill)

        # Add source names if from plugin
        if skill.get("source_plugin_id"):
            plugin = plugins.get(skill["source_plugin_id"])
            if plugin:
                skill_dict["source_plugin_name"] = plugin.get("name")

        if skill.get("source_marketplace_id"):
            marketplace = marketplaces.get(skill["source_marketplace_id"])
            if marketplace:
                skill_dict["source_marketplace_name"] = marketplace.get("name")

        enriched_skills.append(skill_dict)

    return enriched_skills


@router.get("/system", response_model=list[SkillResponse])
async def list_system_skills():
    """List system-provided skills."""
    skills = await db.skills.list()
    return [s for s in skills if s.get("is_system", False)]


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(skill_id: str):
    """Get a specific skill by ID."""
    skill = await db.skills.get(skill_id)
    if not skill:
        raise SkillNotFoundException(
            detail=f"Skill with ID '{skill_id}' does not exist",
            suggested_action="Please check the skill ID and try again"
        )
    return skill


@router.post("/upload", response_model=SkillResponse, status_code=201)
async def upload_skill(
    file: UploadFile = File(...),
    name: str = Form(None),
):
    """Upload a skill package (ZIP file) as a draft.

    This will:
    1. Validate the ZIP contains SKILL.md
    2. Extract to workspace/.claude/skills/{name}/
    3. Upload extracted files to S3 draft folder
    4. Save/update metadata to database with has_draft=True

    After upload, use POST /{skill_id}/publish to publish as a new version.
    """
    if not file.filename or not file.filename.endswith(".zip"):
        raise ValidationException(
            message="Invalid file format",
            detail="Skill packages must be uploaded as ZIP archives",
            suggested_action="Please ensure your file has a .zip extension and try again"
        )

    # Determine skill name
    skill_name = name or file.filename.replace(".zip", "")
    # Sanitize skill name for use as folder name
    skill_name = re.sub(r'[^a-zA-Z0-9_-]', '-', skill_name.lower())

    try:
        # Read file content
        zip_content = await file.read()

        # Upload skill package (extract to local, upload to S3 draft)
        result = await skill_manager.upload_skill_package(
            zip_content=zip_content,
            skill_name=skill_name,
            original_filename=file.filename
        )

        # Determine if we're in local mode (SQLite) or cloud mode (DynamoDB)
        is_local_mode = settings.database_type == "sqlite"

        # Check if skill already exists (by folder_name, local_path, or s3_location)
        existing_skills = await db.skills.list()
        existing_skill = None
        for s in existing_skills:
            # Match by folder_name first (most reliable)
            if s.get("folder_name") == skill_name:
                existing_skill = s
                break
            # Also match by local_path (exact match on directory name)
            local_path = s.get("local_path") or ""
            if local_path and Path(local_path).name == skill_name:
                existing_skill = s
                break
            # In cloud mode, also check s3_location
            if not is_local_mode:
                s3_loc = s.get("s3_location") or s.get("draft_s3_location") or ""
                if f"/skills/{skill_name}/" in s3_loc:
                    existing_skill = s
                    break

        if existing_skill:
            # Update existing skill - immediately active in local mode, draft in cloud mode
            update_data = {
                "name": result["name"],
                "description": result["description"],
                "version": result["version"],
                "local_path": result["local_path"],
                "folder_name": skill_name,
            }
            if is_local_mode:
                # Local mode: immediately active, no draft workflow
                update_data["has_draft"] = False
                update_data["current_version"] = existing_skill.get("current_version", 0) + 1
            else:
                # Cloud mode: create draft for review
                update_data["has_draft"] = True
                update_data["draft_s3_location"] = result["draft_s3_location"]

            skill = await db.skills.update(existing_skill["id"], update_data)
            logger.info(f"Updated skill '{skill_name}' at: {result['local_path']}")
        else:
            # Create new skill record
            skill_data = {
                "name": result["name"],
                "description": result["description"],
                "version": result["version"],
                "local_path": result["local_path"],
                "folder_name": skill_name,
                "created_by": "user",
                "is_system": False,
                "source_type": "user",
            }
            if is_local_mode:
                # Local mode: immediately active
                skill_data["has_draft"] = False
                skill_data["current_version"] = 1
            else:
                # Cloud mode: create draft for review
                skill_data["s3_location"] = None  # No published version yet
                skill_data["draft_s3_location"] = result["draft_s3_location"]
                skill_data["has_draft"] = True
                skill_data["current_version"] = 0  # Never published

            skill = await db.skills.put(skill_data)
            logger.info(f"Created new skill '{skill_name}' at: {result['local_path']}")

        return skill

    except ValueError as e:
        raise ValidationException(
            message="Invalid skill package",
            detail=str(e),
            suggested_action="Ensure your ZIP contains a valid SKILL.md file"
        )
    except Exception as e:
        logger.error(f"Failed to upload skill: {e}")
        raise ValidationException(
            message="Failed to upload skill",
            detail=str(e),
            suggested_action="Please check the file and try again"
        )


@router.post("/refresh", response_model=SyncResultResponse)
async def refresh_skills():
    """Synchronize skills between local directory and database.

    This will:
    1. Scan local workspace/.claude/skills/ directory for user-created skills
    2. Sync differences with database:
       - Local only → Add to DB
       - DB only (orphaned user skills) → Mark for removal

    Note: Plugin skills are managed separately by the plugin system.
    """
    try:
        # Get current DB skills
        db_skills = await db.skills.list()

        # Run synchronization
        sync_result, skills_to_add = await skill_manager.refresh(db_skills)

        # Add new skills to database
        for skill_data in skills_to_add:
            await db.skills.put(skill_data)
            logger.info(f"Added skill to DB: {skill_data['name']}")

        # Convert to response format
        response = SyncResultResponse(
            added=sync_result.added,
            updated=sync_result.updated,
            removed=sync_result.removed,
            errors=[{"skill": e["skill"], "error": e["error"]} for e in sync_result.errors],
            total_local=sync_result.total_local,
            total_plugins=sync_result.total_plugins,
            total_db=sync_result.total_db,
        )

        logger.info(f"Skill refresh complete: added={len(sync_result.added)}, updated={len(sync_result.updated)}, errors={len(sync_result.errors)}")
        return response

    except Exception as e:
        logger.error(f"Failed to refresh skills: {e}")
        raise ValidationException(
            message="Failed to refresh skills",
            detail=str(e),
            suggested_action="Please check the local skills directory and try again"
        )


@router.post("/generate", response_model=SkillResponse, status_code=201)
async def generate_skill(request: SkillGenerateRequest):
    """Generate a skill using AI."""
    # Simulate AI generation delay
    await asyncio.sleep(1)

    # Generate skill name from description
    words = request.description.split()[:2]
    skill_name = "".join(w.capitalize() for w in words) or "NewSkill"

    skill_data = {
        "name": skill_name,
        "description": request.description,
        "created_by": "ai-agent",
        "version": "1.0.0",
        "is_system": False,
    }
    skill = await db.skills.put(skill_data)
    return skill


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(skill_id: str):
    """Delete a skill from database, local directory, S3 (all versions), and version records."""
    skill = await db.skills.get(skill_id)
    if not skill:
        raise SkillNotFoundException(
            detail=f"Skill with ID '{skill_id}' does not exist",
            suggested_action="Please check the skill ID and try again"
        )

    if skill.get("is_system", False):
        raise ValidationException(
            message="Cannot delete system skill",
            detail="System skills are protected and cannot be deleted",
            suggested_action="Only user-created skills can be deleted"
        )

    # Extract skill folder name from s3_location or draft_s3_location or name
    s3_location = skill.get("s3_location") or skill.get("draft_s3_location", "")
    skill_folder_name = None

    if s3_location:
        # Extract from s3://bucket/skills/name/... format
        match = re.search(r'/skills/([^/]+)/', s3_location)
        if match:
            skill_folder_name = match.group(1)

    if not skill_folder_name:
        # Fallback: sanitize skill name
        skill_folder_name = re.sub(r'[^a-zA-Z0-9_-]', '-', skill.get("name", "").lower())

    # Delete files from local and S3 (all versions)
    if skill_folder_name:
        try:
            await skill_manager.delete_skill_files(skill_folder_name)
            logger.info(f"Deleted skill files for: {skill_folder_name}")
        except Exception as e:
            logger.warning(f"Failed to delete skill files for {skill_folder_name}: {e}")
            # Continue to delete from DB even if file deletion fails

    # Delete all version records from database
    try:
        deleted_versions = await db.skill_versions.delete_by_skill(skill_id)
        logger.info(f"Deleted {deleted_versions} version records for skill: {skill_id}")
    except Exception as e:
        logger.warning(f"Failed to delete version records for {skill_id}: {e}")

    # Clean up agent references to this skill
    agents_updated = await _remove_skill_from_agents(skill_id)
    logger.info(f"Removed skill {skill_id} from {agents_updated} agents")

    # Delete skill from database
    await db.skills.delete(skill_id)
    logger.info(f"Deleted skill from DB: {skill_id}")


@router.post("/generate-with-agent")
async def generate_skill_with_agent(request: Request):
    """Generate a skill using an AI agent with streaming response.

    This endpoint:
    1. Creates a specialized Skill Creator Agent
    2. Runs an interactive conversation to create the skill
    3. Agent creates files in workspace/.claude/skills/{skill_name}/
    4. Returns SSE stream of agent responses

    After this completes, call /finalize to sync to S3 and save to DB.
    """
    try:
        body = await request.json()
        skill_name = body.get("skill_name")
        skill_description = body.get("skill_description")
        session_id = body.get("session_id")
        message = body.get("message")
        model = body.get("model")

        if not skill_name:
            raise ValidationException(
                message="Missing skill_name",
                detail="skill_name is required",
                suggested_action="Provide a skill_name in the request body"
            )

        if not skill_description and not message:
            raise ValidationException(
                message="Missing skill_description or message",
                detail="Either skill_description (for initial creation) or message (for follow-up) is required",
                suggested_action="Provide skill_description for new skill or message for iteration"
            )

        # Sanitize skill name for use as folder name
        sanitized_name = re.sub(r'[^a-zA-Z0-9_-]', '-', skill_name.lower())

        logger.info(f"Starting skill generation with agent: {sanitized_name}, model: {model or 'default'}")

        async def event_generator():
            """Generate SSE events from agent conversation."""
            try:
                async for event in agent_manager.run_skill_creator_conversation(
                    skill_name=sanitized_name,
                    skill_description=skill_description or "",
                    user_message=message,
                    session_id=session_id,
                    model=model,
                ):
                    yield f"data: {json.dumps(event)}\n\n"
            except asyncio.CancelledError:
                logger.info("Client disconnected from skill generation stream")
                raise
            except Exception as e:
                logger.error(f"Error in skill generation stream: {e}")
                error_event = {
                    "type": "error",
                    "error": str(e),
                }
                yield f"data: {json.dumps(error_event)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    except ValidationException:
        raise
    except Exception as e:
        logger.error(f"Failed to start skill generation: {e}")
        raise ValidationException(
            message="Failed to start skill generation",
            detail=str(e),
            suggested_action="Please check your request and try again"
        )


@router.post("/finalize", response_model=SkillResponse, status_code=201)
async def finalize_skill(request: SkillFinalizeRequest):
    """Finalize skill creation by uploading to S3 draft and saving to database.

    This endpoint:
    1. Validates the skill directory exists locally
    2. Extracts metadata from SKILL.md
    3. Uploads to S3 draft folder
    4. Saves/updates metadata to database with has_draft=True

    Call this after generate-with-agent completes successfully.
    After this, use POST /{skill_id}/publish to publish as a new version.
    """
    # Sanitize skill name
    skill_name = re.sub(r'[^a-zA-Z0-9_-]', '-', request.skill_name.lower())

    # Get display name (user input) or fall back to sanitized name
    display_name = request.display_name

    logger.info(f"Finalizing skill: original='{request.skill_name}', sanitized='{skill_name}', display_name='{display_name}'")

    # Check if skill directory exists
    skills_dir = Path(settings.agent_workspace_dir) / ".claude" / "skills"
    skill_path = skills_dir / skill_name

    logger.info(f"Looking for skill at: {skill_path}, exists: {skill_path.exists()}")

    if not skill_path.exists():
        # List available directories for debugging
        available = [d.name for d in skills_dir.iterdir() if d.is_dir()] if skills_dir.exists() else []
        logger.error(f"Skill directory not found. Available directories: {available}")
        raise ValidationException(
            message="Skill directory not found",
            detail=f"Expected skill at: {skill_path}. Available: {available}",
            suggested_action="Ensure the skill was created successfully before finalizing"
        )

    skill_md_path = skill_path / "SKILL.md"
    if not skill_md_path.exists():
        raise ValidationException(
            message="SKILL.md not found",
            detail=f"Skill directory exists but missing SKILL.md at: {skill_md_path}",
            suggested_action="Ensure the agent created a valid SKILL.md file"
        )

    try:
        # Extract metadata from SKILL.md
        metadata = skill_manager.extract_skill_metadata(skill_path)

        # Upload to S3 DRAFT folder (in local mode, this just returns the local path)
        draft_location = await skill_manager.upload_to_draft(skill_name, skill_path)

        # Check if skill already exists (by folder_name or local_path)
        existing_skills = await db.skills.list()
        existing_skill = None
        for s in existing_skills:
            # Match by folder_name or local_path
            if s.get("folder_name") == skill_name:
                existing_skill = s
                break
            local_path = s.get("local_path") or ""
            if local_path and skill_name in local_path:
                existing_skill = s
                break
            # Also check s3_location for cloud mode
            s3_loc = s.get("s3_location") or s.get("draft_s3_location") or ""
            if f"/skills/{skill_name}/" in s3_loc:
                existing_skill = s
                break

        # Determine if we're in local mode (SQLite) or cloud mode (DynamoDB)
        is_local_mode = settings.database_type == "sqlite"

        if existing_skill:
            # Update existing skill (no draft, immediately active)
            update_data = {
                "name": display_name or metadata.name or skill_name,
                "description": metadata.description or "",
                "version": metadata.version or "1.0.0",
                "has_draft": False,
                "current_version": existing_skill.get("current_version", 0) + 1,
                "local_path": str(skill_path),
                "folder_name": skill_name,
            }
            # Fix legacy skills that were incorrectly set to "local"
            if existing_skill.get("source_type") == "local":
                update_data["source_type"] = "user"
            # Only add S3 fields in cloud mode
            if not is_local_mode:
                update_data["draft_s3_location"] = draft_location

            skill = await db.skills.update(existing_skill["id"], update_data)
            logger.info(f"Updated skill '{skill_name}' with new draft at: {draft_location}")
        else:
            # Create new skill record (immediately active, no draft)
            skill_data = {
                "name": display_name or metadata.name or skill_name,
                "description": metadata.description or "",
                "version": metadata.version or "1.0.0",
                "local_path": str(skill_path),
                "folder_name": skill_name,
                "has_draft": False,
                "current_version": 1,  # Immediately published
                "created_by": "ai-agent",
                "is_system": False,
                "source_type": "user",  # AI-generated skills are user-created
            }
            # Only add S3 fields in cloud mode
            if not is_local_mode:
                skill_data["s3_location"] = None  # No published version yet
                skill_data["draft_s3_location"] = draft_location

            skill = await db.skills.put(skill_data)
            logger.info(f"Created new skill '{skill_name}' with draft at: {draft_location}")

        return skill

    except Exception as e:
        logger.error(f"Failed to finalize skill: {e}")
        raise ValidationException(
            message="Failed to finalize skill",
            detail=str(e),
            suggested_action="Please check the skill files and try again"
        )


# ============== Helper Functions ==============

async def _remove_skill_from_agents(skill_id: str) -> int:
    """Remove skill ID from all agents that reference it.

    Returns the number of agents updated.
    """
    import json
    agents_updated = 0
    all_agents = await db.agents.list()

    for agent in all_agents:
        skill_ids = agent.get("skill_ids", [])
        # Handle JSON string format from SQLite
        if isinstance(skill_ids, str):
            try:
                skill_ids = json.loads(skill_ids)
            except Exception:
                skill_ids = []

        if skill_id in skill_ids:
            # Remove the skill ID from the list
            new_skill_ids = [sid for sid in skill_ids if sid != skill_id]
            await db.agents.update(agent["id"], {"skill_ids": json.dumps(new_skill_ids)})
            agents_updated += 1
            logger.info(f"Removed skill {skill_id} from agent '{agent['name']}' ({agent['id']})")

    return agents_updated


# ============== Version Control Endpoints ==============

def _get_skill_folder_name(skill: dict) -> str:
    """Extract skill folder name from skill record."""
    s3_location = skill.get("s3_location") or skill.get("draft_s3_location", "")
    if s3_location:
        # Extract from s3://bucket/skills/name/... format
        match = re.search(r'/skills/([^/]+)/', s3_location)
        if match:
            return match.group(1)
    # Fallback: sanitize skill name
    return re.sub(r'[^a-zA-Z0-9_-]', '-', skill.get("name", "").lower())


@router.get("/{skill_id}/versions", response_model=SkillVersionListResponse)
async def list_skill_versions(skill_id: str):
    """List all versions of a skill.

    Returns version history with current version and draft status.
    """
    skill = await db.skills.get(skill_id)
    if not skill:
        raise SkillNotFoundException(
            detail=f"Skill with ID '{skill_id}' does not exist",
            suggested_action="Please check the skill ID and try again"
        )

    # Get all versions from database
    versions = await db.skill_versions.list_by_skill(skill_id)

    return SkillVersionListResponse(
        skill_id=skill_id,
        skill_name=skill.get("name", ""),
        current_version=skill.get("current_version", 0),
        has_draft=skill.get("has_draft", False),
        versions=[
            SkillVersionResponse(
                id=v["id"],
                skill_id=v["skill_id"],
                version=v["version"],
                git_commit=v.get("git_commit"),
                local_path=v.get("local_path"),
                created_at=v["created_at"],
                change_summary=v.get("change_summary")
            )
            for v in versions
        ]
    )


@router.get("/{skill_id}/versions/{version}", response_model=SkillVersionResponse)
async def get_skill_version(skill_id: str, version: int):
    """Get details of a specific skill version."""
    skill = await db.skills.get(skill_id)
    if not skill:
        raise SkillNotFoundException(
            detail=f"Skill with ID '{skill_id}' does not exist",
            suggested_action="Please check the skill ID and try again"
        )

    version_record = await db.skill_versions.get_by_skill_and_version(skill_id, version)
    if not version_record:
        raise ValidationException(
            message="Version not found",
            detail=f"Version {version} does not exist for this skill",
            suggested_action="Use the list versions endpoint to see available versions"
        )

    return SkillVersionResponse(
        id=version_record["id"],
        skill_id=version_record["skill_id"],
        version=version_record["version"],
        git_commit=version_record.get("git_commit"),
        local_path=version_record.get("local_path"),
        created_at=version_record["created_at"],
        change_summary=version_record.get("change_summary")
    )


@router.post("/{skill_id}/publish", response_model=SkillResponse)
async def publish_skill_draft(skill_id: str, request: PublishDraftRequest | None = None):
    """Publish the draft as a new version.

    This will:
    1. Copy draft folder to v{n+1} folder in S3
    2. Delete draft folder
    3. Update skill's current_version and s3_location
    4. Create version record in database
    5. Download new version to local workspace
    """
    skill = await db.skills.get(skill_id)
    if not skill:
        raise SkillNotFoundException(
            detail=f"Skill with ID '{skill_id}' does not exist",
            suggested_action="Please check the skill ID and try again"
        )

    if not skill.get("has_draft", False):
        raise ValidationException(
            message="No draft to publish",
            detail="This skill has no unpublished draft",
            suggested_action="Upload or modify the skill first to create a draft"
        )

    try:
        skill_folder_name = _get_skill_folder_name(skill)
        current_version = skill.get("current_version", 0)
        new_version = current_version + 1

        # Publish draft to new version in S3
        s3_location = await skill_manager.publish_draft(skill_folder_name, new_version)

        # Create version record
        change_summary = request.change_summary if request else None
        version_record = {
            "skill_id": skill_id,
            "version": new_version,
            "s3_location": s3_location,
            "change_summary": change_summary,
        }
        await db.skill_versions.put(version_record)

        # Update skill record
        updated_skill = await db.skills.update(skill_id, {
            "current_version": new_version,
            "s3_location": s3_location,
            "has_draft": False,
            "draft_s3_location": None,
        })

        # Download to local workspace (local always reflects published version)
        await skill_manager.download_version_to_local(skill_folder_name, new_version)

        logger.info(f"Published skill {skill_id} as v{new_version}")
        return updated_skill

    except Exception as e:
        logger.error(f"Failed to publish skill draft: {e}")
        raise ValidationException(
            message="Failed to publish draft",
            detail=str(e),
            suggested_action="Please check S3 connectivity and try again"
        )


@router.delete("/{skill_id}/draft", status_code=204)
async def discard_skill_draft(skill_id: str):
    """Discard the unpublished draft.

    This will:
    1. Delete draft folder from S3
    2. Clear draft status in database
    """
    skill = await db.skills.get(skill_id)
    if not skill:
        raise SkillNotFoundException(
            detail=f"Skill with ID '{skill_id}' does not exist",
            suggested_action="Please check the skill ID and try again"
        )

    if not skill.get("has_draft", False):
        raise ValidationException(
            message="No draft to discard",
            detail="This skill has no unpublished draft",
            suggested_action="No action needed"
        )

    try:
        skill_folder_name = _get_skill_folder_name(skill)

        # Delete draft from S3
        await skill_manager.discard_draft(skill_folder_name)

        # Update skill record
        await db.skills.update(skill_id, {
            "has_draft": False,
            "draft_s3_location": None,
        })

        logger.info(f"Discarded draft for skill {skill_id}")

    except Exception as e:
        logger.error(f"Failed to discard skill draft: {e}")
        raise ValidationException(
            message="Failed to discard draft",
            detail=str(e),
            suggested_action="Please check S3 connectivity and try again"
        )


@router.post("/{skill_id}/rollback", response_model=SkillResponse)
async def rollback_skill_version(skill_id: str, request: RollbackRequest):
    """Rollback to a specific version.

    This will:
    1. Update skill's current_version and s3_location to point to target version
    2. Download target version to local workspace
    3. Discard any existing draft
    """
    skill = await db.skills.get(skill_id)
    if not skill:
        raise SkillNotFoundException(
            detail=f"Skill with ID '{skill_id}' does not exist",
            suggested_action="Please check the skill ID and try again"
        )

    target_version = request.version

    # Verify target version exists
    version_record = await db.skill_versions.get_by_skill_and_version(skill_id, target_version)
    if not version_record:
        raise ValidationException(
            message="Version not found",
            detail=f"Version {target_version} does not exist for this skill",
            suggested_action="Use the list versions endpoint to see available versions"
        )

    try:
        skill_folder_name = _get_skill_folder_name(skill)

        # Discard draft if exists
        if skill.get("has_draft", False):
            await skill_manager.discard_draft(skill_folder_name)

        # Update skill record to point to target version
        updated_skill = await db.skills.update(skill_id, {
            "current_version": target_version,
            "s3_location": version_record["s3_location"],
            "has_draft": False,
            "draft_s3_location": None,
        })

        # Download target version to local workspace
        await skill_manager.download_version_to_local(skill_folder_name, target_version)

        logger.info(f"Rolled back skill {skill_id} to v{target_version}")
        return updated_skill

    except Exception as e:
        logger.error(f"Failed to rollback skill: {e}")
        raise ValidationException(
            message="Failed to rollback",
            detail=str(e),
            suggested_action="Please check S3 connectivity and try again"
        )
