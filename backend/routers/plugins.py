"""Plugin and Marketplace CRUD API endpoints."""
import json
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter
from schemas.marketplace import (
    MarketplaceCreate,
    MarketplaceUpdate,
    MarketplaceResponse,
    PluginInstallRequest,
    PluginResponse,
    AvailablePlugin,
    AvailablePluginInfo,
)
from database import db
from core.plugin_manager import plugin_manager
from core.exceptions import (
    NotFoundException,
    ValidationException,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ============== Marketplace Endpoints ==============

@router.get("/marketplaces", response_model=list[MarketplaceResponse])
async def list_marketplaces():
    """List all configured marketplaces."""
    items = await db.marketplaces.list()
    return [_marketplace_to_response(m) for m in items]


@router.get("/marketplaces/{marketplace_id}", response_model=MarketplaceResponse)
async def get_marketplace(marketplace_id: str):
    """Get a specific marketplace by ID."""
    marketplace = await db.marketplaces.get(marketplace_id)
    if not marketplace:
        raise NotFoundException(
            detail=f"Marketplace with ID '{marketplace_id}' not found"
        )
    return _marketplace_to_response(marketplace)


@router.post("/marketplaces", response_model=MarketplaceResponse, status_code=201)
async def create_marketplace(request: MarketplaceCreate):
    """Add a new marketplace source."""
    # Validate type
    if request.type not in ("git", "http", "local"):
        raise ValidationException(
            message="Invalid marketplace type",
            detail=f"Type must be one of: git, http, local"
        )

    marketplace_data = {
        "name": request.name,
        "description": request.description,
        "type": request.type,
        "url": request.url,
        "branch": request.branch or "main",
        "is_active": True,
        "cached_plugins": [],
    }
    marketplace = await db.marketplaces.put(marketplace_data)
    return _marketplace_to_response(marketplace)


@router.put("/marketplaces/{marketplace_id}", response_model=MarketplaceResponse)
async def update_marketplace(marketplace_id: str, request: MarketplaceUpdate):
    """Update a marketplace."""
    marketplace = await db.marketplaces.get(marketplace_id)
    if not marketplace:
        raise NotFoundException(
            detail=f"Marketplace with ID '{marketplace_id}' not found"
        )

    updates = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.description is not None:
        updates["description"] = request.description
    if request.url is not None:
        updates["url"] = request.url
    if request.branch is not None:
        updates["branch"] = request.branch

    if updates:
        marketplace = await db.marketplaces.update(marketplace_id, updates)

    return _marketplace_to_response(marketplace)


@router.post("/marketplaces/{marketplace_id}/sync")
async def sync_marketplace(marketplace_id: str):
    """Sync a marketplace to fetch available plugins."""
    marketplace = await db.marketplaces.get(marketplace_id)
    if not marketplace:
        raise NotFoundException(
            detail=f"Marketplace with ID '{marketplace_id}' not found"
        )

    is_marketplace = True
    actual_name = marketplace["name"]

    if marketplace["type"] == "git":
        # Derive cache key from URL (stable path for cache directory)
        url = marketplace["url"]
        cache_key = "/".join(url.replace(".git", "").rstrip("/").split("/")[-2:])

        sync_result = await plugin_manager.sync_git_marketplace(
            marketplace_name=cache_key,
            git_url=marketplace["url"],
            branch=marketplace.get("branch", "main"),
        )
        plugins = sync_result.plugins
        is_marketplace = sync_result.is_marketplace
        # Use marketplace name from marketplace.json if available
        if sync_result.marketplace_name:
            actual_name = sync_result.marketplace_name
    elif marketplace["type"] == "local":
        # For local marketplace, just scan the directory
        plugins = plugin_manager.list_cached_plugins(marketplace["name"])
    else:
        raise ValidationException(
            message="Unsupported marketplace type",
            detail=f"Type '{marketplace['type']}' is not yet supported for syncing"
        )

    # Convert AvailablePlugin dataclasses to dicts for storage
    plugins_data = [
        {
            "name": p.name,
            "version": p.version,
            "description": p.description,
            "author": p.author,
            "keywords": p.keywords,
        }
        for p in plugins
    ]

    # Update marketplace with cached plugins and actual name
    update_data = {
        "cached_plugins": plugins_data,
        "last_synced_at": datetime.now().isoformat(),
    }
    # Update name if we got it from marketplace.json
    if actual_name != marketplace["name"]:
        update_data["name"] = actual_name

    await db.marketplaces.update(marketplace_id, update_data)

    return {
        "marketplace_id": marketplace_id,
        "marketplace_name": actual_name,
        "is_marketplace": is_marketplace,
        "plugins_found": len(plugins),
        "plugins": plugins_data,
        "synced_at": datetime.now().isoformat(),
    }


@router.delete("/marketplaces/{marketplace_id}", status_code=204)
async def delete_marketplace(marketplace_id: str):
    """Remove a marketplace."""
    marketplace = await db.marketplaces.get(marketplace_id)
    if not marketplace:
        raise NotFoundException(
            detail=f"Marketplace with ID '{marketplace_id}' not found"
        )
    await db.marketplaces.delete(marketplace_id)


# ============== Plugin Endpoints ==============

@router.get("/plugins", response_model=list[PluginResponse])
async def list_plugins():
    """List all installed plugins."""
    items = await db.plugins.list()
    # Enrich with marketplace names
    marketplaces = {m["id"]: m["name"] for m in await db.marketplaces.list()}
    return [_plugin_to_response(p, marketplaces.get(p["marketplace_id"])) for p in items]


@router.get("/plugins/{plugin_id}", response_model=PluginResponse)
async def get_plugin(plugin_id: str):
    """Get a specific installed plugin."""
    plugin = await db.plugins.get(plugin_id)
    if not plugin:
        raise NotFoundException(
            detail=f"Plugin with ID '{plugin_id}' not found"
        )

    # Get marketplace name
    marketplace = await db.marketplaces.get(plugin["marketplace_id"])
    marketplace_name = marketplace["name"] if marketplace else None

    return _plugin_to_response(plugin, marketplace_name)


@router.post("/plugins/install", response_model=PluginResponse, status_code=201)
async def install_plugin(request: PluginInstallRequest):
    """Install a plugin from a marketplace."""
    # Verify marketplace exists
    marketplace = await db.marketplaces.get(request.marketplace_id)
    if not marketplace:
        raise NotFoundException(
            detail=f"Marketplace with ID '{request.marketplace_id}' not found"
        )

    # Check if already installed
    existing = await db.plugins.list()
    for p in existing:
        if p["name"] == request.plugin_name and p["marketplace_id"] == request.marketplace_id:
            raise ValidationException(
                message="Plugin already installed",
                detail=f"Plugin '{request.plugin_name}' is already installed from this marketplace"
            )

    # Derive cache key from URL (original name before marketplace.json update)
    # This ensures we use the correct cache path even if display name was updated
    url = marketplace["url"]
    cache_key = "/".join(url.replace(".git", "").rstrip("/").split("/")[-2:])

    # Install plugin
    logger.info(f"Installing plugin: name='{request.plugin_name}', cache_key='{cache_key}'")
    result = await plugin_manager.install_plugin(
        plugin_name=request.plugin_name,
        marketplace_name=cache_key,
        version=request.version,
    )

    if not result.success:
        logger.warning(f"Plugin installation failed: {result.error}")
        raise ValidationException(
            message="Plugin installation failed",
            detail=result.error or "Unknown error during installation"
        )

    # Save to database (JSON serialize list fields for SQLite)
    plugin_data = {
        "name": result.name or request.plugin_name,
        "description": result.description,
        "version": result.version or request.version or "latest",
        "marketplace_id": request.marketplace_id,
        "author": result.author,
        "installed_skills": json.dumps(result.installed_skills),
        "installed_commands": json.dumps(result.installed_commands),
        "installed_agents": json.dumps(result.installed_agents),
        "installed_hooks": json.dumps(result.installed_hooks),
        "installed_mcp_servers": json.dumps(result.installed_mcp_servers),
        "status": "installed",
        "install_path": result.install_path,  # Path to installed plugin/skill
        "installed_at": datetime.now().isoformat(),
    }
    plugin = await db.plugins.put(plugin_data)

    # Sync installed skills to skills table
    await _sync_plugin_skills_to_db(
        plugin_id=plugin["id"],
        marketplace_id=request.marketplace_id,
        installed_skills=result.installed_skills,
        skills_dir=plugin_manager.skills_dir,
    )

    return _plugin_to_response(plugin, marketplace["name"])


@router.delete("/plugins/{plugin_id}")
async def uninstall_plugin(plugin_id: str):
    """Uninstall a plugin."""
    plugin = await db.plugins.get(plugin_id)
    if not plugin:
        raise NotFoundException(
            detail=f"Plugin with ID '{plugin_id}' not found"
        )

    # Remove installed files
    removed = await plugin_manager.uninstall_plugin(
        plugin_name=plugin["name"],
        installed_skills=plugin.get("installed_skills", []),
        installed_commands=plugin.get("installed_commands", []),
        installed_agents=plugin.get("installed_agents", []),
        installed_hooks=plugin.get("installed_hooks", []),
    )

    # Remove skills from skills database
    removed_skill_names = await _remove_plugin_skills_from_db(plugin_id)
    logger.info(f"Removed {len(removed_skill_names)} skills from DB for plugin: {plugin_id}")

    # Clean up agent references to this plugin
    agents_updated = await _remove_plugin_from_agents(plugin_id)
    logger.info(f"Removed plugin {plugin_id} from {agents_updated} agents")

    # Delete from database
    await db.plugins.delete(plugin_id)

    return {
        "plugin_id": plugin_id,
        "removed_skills": removed["skills"],
        "removed_commands": removed["commands"],
        "removed_agents": removed["agents"],
        "removed_hooks": removed["hooks"],
    }


@router.post("/plugins/{plugin_id}/disable")
async def disable_plugin(plugin_id: str):
    """Disable a plugin without uninstalling."""
    plugin = await db.plugins.get(plugin_id)
    if not plugin:
        raise NotFoundException(
            detail=f"Plugin with ID '{plugin_id}' not found"
        )

    await db.plugins.update(plugin_id, {"status": "disabled"})
    return {"status": "disabled", "plugin_id": plugin_id}


@router.post("/plugins/{plugin_id}/enable")
async def enable_plugin(plugin_id: str):
    """Re-enable a disabled plugin."""
    plugin = await db.plugins.get(plugin_id)
    if not plugin:
        raise NotFoundException(
            detail=f"Plugin with ID '{plugin_id}' not found"
        )

    await db.plugins.update(plugin_id, {"status": "installed"})
    return {"status": "installed", "plugin_id": plugin_id}


# ============== Helper Functions ==============

def _marketplace_to_response(m: dict) -> MarketplaceResponse:
    """Convert database record to response model."""
    cached_plugins = m.get("cached_plugins", [])
    # Ensure cached_plugins is a list of AvailablePlugin-compatible dicts
    if isinstance(cached_plugins, str):
        import json
        try:
            cached_plugins = json.loads(cached_plugins)
        except:
            cached_plugins = []

    return MarketplaceResponse(
        id=m["id"],
        name=m["name"],
        description=m.get("description"),
        type=m["type"],
        url=m["url"],
        branch=m.get("branch", "main"),
        is_active=bool(m.get("is_active", True)),
        last_synced_at=m.get("last_synced_at"),
        cached_plugins=[
            AvailablePluginInfo(
                name=p.get("name", ""),
                version=p.get("version", "1.0.0"),
                description=p.get("description"),
                author=p.get("author"),
                keywords=p.get("keywords", []),
            )
            for p in cached_plugins
        ] if cached_plugins else [],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


async def _sync_plugin_skills_to_db(
    plugin_id: str,
    marketplace_id: str,
    installed_skills: list[str],
    skills_dir,
) -> None:
    """Sync installed skills from a plugin to the skills database.

    This creates skill records in the skills table for skills installed from plugins,
    allowing them to appear in the Skills Management page.
    """
    from pathlib import Path

    for skill_name in installed_skills:
        skill_path = skills_dir / skill_name
        if not skill_path.exists():
            logger.warning(f"Skill directory not found: {skill_path}")
            continue

        # Try to read skill description from markdown file
        description = f"Skill installed from plugin"
        for md_file in skill_path.glob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                # Get first non-header line as description
                for line in content.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("```"):
                        description = line[:500]
                        break
                break
            except Exception:
                pass

        # Check if skill already exists (by folder_name)
        existing_skills = await db.skills.list()
        existing = None
        for s in existing_skills:
            if s.get("folder_name") == skill_name:
                existing = s
                break

        if existing:
            # Update existing skill
            await db.skills.update(existing["id"], {
                "source_type": "plugin",
                "source_plugin_id": plugin_id,
                "source_marketplace_id": marketplace_id,
                "local_path": str(skill_path),
                "description": description,
            })
            logger.info(f"Updated skill '{skill_name}' from plugin")
        else:
            # Create new skill record
            skill_data = {
                "name": skill_name,
                "description": description,
                "folder_name": skill_name,
                "local_path": str(skill_path),
                "source_type": "plugin",
                "source_plugin_id": plugin_id,
                "source_marketplace_id": marketplace_id,
                "version": "1.0.0",
                "is_system": 0,
                "current_version": 0,
                "has_draft": 0,
                "created_by": "plugin",
            }
            await db.skills.put(skill_data)
            logger.info(f"Created skill '{skill_name}' from plugin")


async def _remove_plugin_skills_from_db(plugin_id: str) -> list[str]:
    """Remove skills associated with a plugin from the database.

    Returns list of removed skill names.
    """
    removed = []
    all_skills = await db.skills.list()

    for skill in all_skills:
        if skill.get("source_plugin_id") == plugin_id:
            await db.skills.delete(skill["id"])
            removed.append(skill["name"])
            logger.info(f"Removed skill '{skill['name']}' (plugin uninstalled)")

    return removed


async def _remove_plugin_from_agents(plugin_id: str) -> int:
    """Remove plugin ID from all agents that reference it.

    Returns the number of agents updated.
    """
    agents_updated = 0
    all_agents = await db.agents.list()

    for agent in all_agents:
        plugin_ids = agent.get("plugin_ids", [])
        # Handle JSON string format from SQLite
        if isinstance(plugin_ids, str):
            try:
                plugin_ids = json.loads(plugin_ids)
            except Exception:
                plugin_ids = []

        if plugin_id in plugin_ids:
            # Remove the plugin ID from the list
            new_plugin_ids = [pid for pid in plugin_ids if pid != plugin_id]
            await db.agents.update(agent["id"], {"plugin_ids": json.dumps(new_plugin_ids)})
            agents_updated += 1
            logger.info(f"Removed plugin {plugin_id} from agent '{agent['name']}' ({agent['id']})")

    return agents_updated


def _plugin_to_response(p: dict, marketplace_name: Optional[str] = None) -> PluginResponse:
    """Convert database record to response model."""
    # Parse JSON fields if they're strings
    def parse_list(val):
        if isinstance(val, str):
            try:
                return json.loads(val)
            except:
                return []
        return val or []

    return PluginResponse(
        id=p["id"],
        name=p["name"],
        description=p.get("description"),
        version=p["version"],
        marketplace_id=p["marketplace_id"],
        marketplace_name=marketplace_name,
        author=p.get("author"),
        license=p.get("license"),
        installed_skills=parse_list(p.get("installed_skills")),
        installed_commands=parse_list(p.get("installed_commands")),
        installed_agents=parse_list(p.get("installed_agents")),
        installed_hooks=parse_list(p.get("installed_hooks")),
        installed_mcp_servers=parse_list(p.get("installed_mcp_servers")),
        status=p.get("status", "installed"),
        install_path=p.get("install_path"),
        installed_at=p["installed_at"],
        updated_at=p["updated_at"],
    )
