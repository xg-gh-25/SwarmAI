"""Generate pre-seeded database for SwarmAI distribution.

This script creates a seed database at build time containing:
- Default SwarmAgent with system configuration
- System skills (DOCUMENT, RESEARCH)
- System MCP servers (Filesystem)
- Default SwarmWorkspace record
- App settings with initialization_complete=true

The seed database is bundled with the app and copied to the user's
data directory on first launch, eliminating runtime initialization delays.
"""
import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.sqlite import SQLiteDatabase

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def parse_skill_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from a skill markdown file.
    
    Args:
        content: The full content of the skill markdown file
        
    Returns:
        Dictionary with parsed frontmatter fields (name, description, version)
    """
    result = {"name": "", "description": "", "version": "1.0.0"}
    
    if not content.startswith("---"):
        return result
    
    # Find the closing ---
    end_idx = content.find("---", 3)
    if end_idx == -1:
        return result
    
    frontmatter = content[3:end_idx].strip()
    
    # Simple YAML parsing for key: value pairs
    for line in frontmatter.split("\n"):
        line = line.strip()
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip().strip('"').strip("'")
            if key in result:
                result[key] = value
    
    return result


class SeedDatabaseGenerator:
    """Generates a pre-seeded SQLite database for distribution."""
    
    def __init__(self, output_path: Path, resources_dir: Path):
        """Initialize the generator.
        
        Args:
            output_path: Path where seed.db will be created
            resources_dir: Path to desktop/resources directory containing config files
        """
        self.output_path = output_path
        self.resources_dir = resources_dir
        self.db = SQLiteDatabase(db_path=output_path)
        self._now = datetime.now(timezone.utc).isoformat()
    
    async def generate(self) -> bool:
        """Generate the seed database with all default resources.
        
        Returns:
            True if generation and validation succeeded
        """
        logger.info(f"Generating seed database at {self.output_path}")
        
        # Remove existing seed.db if present (idempotency)
        if self.output_path.exists():
            self.output_path.unlink()
            logger.info("Removed existing seed.db")
        
        # Initialize database schema
        await self.db.initialize()
        logger.info("Database schema initialized")
        
        # Insert all default records
        await self._insert_default_agent()
        skill_ids = await self._insert_system_skills()
        mcp_ids = await self._insert_system_mcps()
        await self._insert_default_workspace()
        await self._insert_app_settings()
        
        # Update agent with skill and MCP IDs
        await self._update_agent_references(skill_ids, mcp_ids)
        
        # Validate the generated database
        if not await self._validate():
            logger.error("Seed database validation failed")
            return False
        
        logger.info("Seed database generated successfully")
        return True
    
    async def _insert_default_agent(self) -> None:
        """Insert the SwarmAgent record from default-agent.json."""
        config_path = self.resources_dir / "default-agent.json"
        
        if not config_path.exists():
            raise FileNotFoundError(f"default-agent.json not found at {config_path}")
        
        with open(config_path, "r") as f:
            agent_config = json.load(f)
        
        # Build the agent record with all required fields
        agent = {
            "id": agent_config.get("id", "default"),
            "name": agent_config.get("name", "SwarmAI"),
            "description": agent_config.get("description", "SwarmAI — Your AI Team, 24/7"),
            "model": agent_config.get("model", "claude-opus-4-5-20250514"),
            "permission_mode": agent_config.get("permission_mode", "bypassPermissions"),
            "max_turns": agent_config.get("max_turns", 100),
            "system_prompt": agent_config.get("system_prompt", ""),
            "allowed_tools": json.dumps(agent_config.get("allowed_tools", [])),
            "plugin_ids": json.dumps(agent_config.get("plugin_ids", [])),
            "skill_ids": json.dumps([]),  # Will be updated after skills are inserted
            "allow_all_skills": 1 if agent_config.get("allow_all_skills", True) else 0,
            "mcp_ids": json.dumps([]),  # Will be updated after MCPs are inserted
            "working_directory": agent_config.get("working_directory"),
            "enable_bash_tool": 1 if agent_config.get("enable_bash_tool", True) else 0,
            "enable_file_tools": 1 if agent_config.get("enable_file_tools", True) else 0,
            "enable_web_tools": 1 if agent_config.get("enable_web_tools", False) else 0,
            "enable_tool_logging": 1 if agent_config.get("enable_tool_logging", True) else 0,
            "enable_safety_checks": 1 if agent_config.get("enable_safety_checks", True) else 0,
            "enable_file_access_control": 1 if agent_config.get("enable_file_access_control", True) else 0,
            "allowed_directories": json.dumps(agent_config.get("allowed_directories", [])),
            "global_user_mode": 1 if agent_config.get("global_user_mode", False) else 0,
            "enable_human_approval": 1 if agent_config.get("enable_human_approval", True) else 0,
            "sandbox_enabled": 1 if agent_config.get("sandbox_enabled", True) else 0,
            "sandbox": json.dumps(agent_config.get("sandbox", {})),
            "is_default": 1 if agent_config.get("is_default", True) else 0,
            "is_system_agent": 1,  # Always true for the default agent
            "status": "active",
            "user_id": None,
            "created_at": self._now,
            "updated_at": self._now,
        }
        
        await self.db.agents.put(agent)
        logger.info(f"Inserted default agent: {agent['name']} (id={agent['id']})")
    
    async def _insert_system_skills(self) -> list[str]:
        """Insert system skill records from default-skills/*.md.
        
        Returns:
            List of inserted skill IDs
        """
        skills_dir = self.resources_dir / "default-skills"
        skill_ids = []
        
        if not skills_dir.exists():
            logger.warning(f"default-skills directory not found at {skills_dir}")
            return skill_ids
        
        for skill_file in skills_dir.glob("*.md"):
            content = skill_file.read_text()
            frontmatter = parse_skill_frontmatter(content)
            
            # Generate skill ID from filename
            skill_name = skill_file.stem.lower()
            skill_id = f"default-{skill_name}"
            
            skill = {
                "id": skill_id,
                "name": frontmatter.get("name", skill_file.stem),
                "description": frontmatter.get("description", ""),
                "folder_name": skill_name,
                "local_path": None,  # Will be set at runtime
                "source_type": "system",
                "source_plugin_id": None,
                "source_marketplace_id": None,
                "git_url": None,
                "git_branch": "main",
                "git_commit": None,
                "created_by": None,
                "version": frontmatter.get("version", "1.0.0"),
                "is_system": 1,
                "current_version": 0,
                "has_draft": 0,
                "user_id": None,
                "created_at": self._now,
                "updated_at": self._now,
            }
            
            await self.db.skills.put(skill)
            skill_ids.append(skill_id)
            logger.info(f"Inserted system skill: {skill['name']} (id={skill_id})")
        
        return skill_ids
    
    async def _insert_system_mcps(self) -> list[str]:
        """Insert system MCP server records from default-mcp-servers.json.
        
        Returns:
            List of inserted MCP server IDs
        """
        config_path = self.resources_dir / "default-mcp-servers.json"
        mcp_ids = []
        
        if not config_path.exists():
            logger.warning(f"default-mcp-servers.json not found at {config_path}")
            return mcp_ids
        
        with open(config_path, "r") as f:
            mcp_configs = json.load(f)
        
        for mcp_config in mcp_configs:
            mcp = {
                "id": mcp_config.get("id", str(uuid.uuid4())),
                "name": mcp_config.get("name", "Unknown"),
                "description": mcp_config.get("description", ""),
                "connection_type": mcp_config.get("connection_type", "stdio"),
                "config": json.dumps(mcp_config.get("config", {})),
                "allowed_tools": json.dumps(mcp_config.get("allowed_tools", [])),
                "rejected_tools": json.dumps(mcp_config.get("rejected_tools", [])),
                "source_type": "system",
                "endpoint": mcp_config.get("endpoint"),
                "version": mcp_config.get("version"),
                "is_active": 1 if mcp_config.get("is_active", True) else 0,
                "is_system": 1,  # Always true for system MCPs
                "user_id": None,
                "created_at": self._now,
                "updated_at": self._now,
            }
            
            await self.db.mcp_servers.put(mcp)
            mcp_ids.append(mcp["id"])
            logger.info(f"Inserted system MCP server: {mcp['name']} (id={mcp['id']})")
        
        return mcp_ids
    
    async def _insert_default_workspace(self) -> None:
        """Insert the SwarmWS workspace_config singleton record.
        
        Uses {app_data_dir}/SwarmWS placeholder path which is expanded
        at runtime to the platform-specific location.
        
        Also inserts a legacy swarm_workspaces row so the migration path
        from seed DB works correctly (migration reads from swarm_workspaces
        to seed workspace_config).
        """
        now = self._now
        
        # Insert into workspace_config (new singleton model)
        await self.db.workspace_config.put({
            "id": "swarmws",
            "name": "SwarmWS",
            "file_path": "{app_data_dir}/SwarmWS",
            "icon": "🏠",
            "context": "Default SwarmAI workspace for general tasks and projects.",
            "created_at": now,
            "updated_at": now,
        })
        logger.info("Inserted workspace_config singleton (id=swarmws)")
        
        # Also insert a legacy swarm_workspaces row for migration compatibility
        import aiosqlite
        async with aiosqlite.connect(str(self.db.db_path)) as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO swarm_workspaces "
                "(id, name, file_path, context, icon, is_default, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "SwarmWS", "{app_data_dir}/SwarmWS",
                 "Default SwarmAI workspace for general tasks and projects.",
                 "🏠", 1, now, now),
            )
            await conn.commit()
        logger.info("Inserted legacy swarm_workspaces row for migration compatibility")
    
    async def _insert_app_settings(self) -> None:
        """Insert app_settings with initialization_complete=true."""
        settings = {
            "id": "default",
            "anthropic_api_key": "",
            "anthropic_base_url": None,
            "use_bedrock": 0,
            "bedrock_auth_type": "credentials",
            "aws_access_key_id": "",
            "aws_secret_access_key": "",
            "aws_session_token": None,
            "aws_bearer_token": "",
            "aws_region": "us-east-1",
            "available_models": json.dumps([]),
            "default_model": "claude-sonnet-4-5-20250929",
            "initialization_complete": 1,  # KEY: Set to true
            "created_at": self._now,
            "updated_at": self._now,
        }
        
        await self.db.app_settings.put(settings)
        logger.info("Inserted app_settings with initialization_complete=true")
    
    async def _update_agent_references(self, skill_ids: list[str], mcp_ids: list[str]) -> None:
        """Update the default agent with skill and MCP references.
        
        Args:
            skill_ids: List of inserted skill IDs
            mcp_ids: List of inserted MCP server IDs
        """
        await self.db.agents.update("default", {
            "skill_ids": json.dumps(skill_ids),
            "mcp_ids": json.dumps(mcp_ids),
            "updated_at": self._now,
        })
        logger.info(f"Updated agent with skill_ids={skill_ids}, mcp_ids={mcp_ids}")
    
    async def _validate(self) -> bool:
        """Validate that all required records exist.
        
        Returns:
            True if validation passes
        """
        logger.info("Validating seed database...")
        valid = True
        
        # Validate SwarmAgent exists with correct flags
        agent = await self.db.agents.get("default")
        if not agent:
            logger.error("Validation failed: SwarmAgent with id='default' not found")
            valid = False
        elif not agent.get("is_system_agent"):
            logger.error("Validation failed: SwarmAgent is_system_agent is not true")
            valid = False
        else:
            logger.info("✓ SwarmAgent validated")
        
        # Validate system skills exist
        skills = await self.db.skills.list_by_system()
        if not skills:
            logger.error("Validation failed: No system skills found")
            valid = False
        else:
            for skill in skills:
                if not skill.get("is_system"):
                    logger.error(f"Validation failed: Skill {skill['id']} is_system is not true")
                    valid = False
            logger.info(f"✓ {len(skills)} system skills validated")
        
        # Validate system MCPs exist
        mcps = await self.db.mcp_servers.list_by_system()
        if not mcps:
            logger.error("Validation failed: No system MCP servers found")
            valid = False
        else:
            for mcp in mcps:
                if not mcp.get("is_system"):
                    logger.error(f"Validation failed: MCP {mcp['id']} is_system is not true")
                    valid = False
            logger.info(f"✓ {len(mcps)} system MCP servers validated")
        
        # Validate app_settings has initialization_complete=true
        settings = await self.db.app_settings.get("default")
        if not settings:
            logger.error("Validation failed: app_settings not found")
            valid = False
        elif not settings.get("initialization_complete"):
            logger.error("Validation failed: initialization_complete is not true")
            valid = False
        else:
            logger.info("✓ app_settings validated")
        
        # Validate workspace_config singleton exists
        workspace = await self.db.workspace_config.get_config()
        if not workspace:
            logger.error("Validation failed: workspace_config singleton not found")
            valid = False
        elif workspace.get("id") != "swarmws":
            logger.error("Validation failed: workspace_config id is not 'swarmws'")
            valid = False
        else:
            logger.info("✓ workspace_config singleton validated")
        
        return valid


async def main():
    """Entry point for seed database generation."""
    # Determine paths
    backend_dir = Path(__file__).parent.parent
    project_root = backend_dir.parent
    
    # Default output path
    output_path = project_root / "desktop" / "resources" / "seed.db"
    resources_dir = project_root / "desktop" / "resources"
    
    # Allow override via command line argument
    if len(sys.argv) > 1:
        output_path = Path(sys.argv[1])
    
    logger.info(f"Backend directory: {backend_dir}")
    logger.info(f"Resources directory: {resources_dir}")
    logger.info(f"Output path: {output_path}")
    
    # Validate resources directory exists
    if not resources_dir.exists():
        logger.error(f"Resources directory not found: {resources_dir}")
        sys.exit(1)
    
    # Generate the seed database
    generator = SeedDatabaseGenerator(output_path, resources_dir)
    success = await generator.generate()
    
    if not success:
        logger.error("Seed database generation failed")
        sys.exit(1)
    
    logger.info(f"Seed database created at: {output_path}")
    logger.info(f"File size: {output_path.stat().st_size} bytes")


if __name__ == "__main__":
    asyncio.run(main())
