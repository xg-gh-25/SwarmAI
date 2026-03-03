"""Generate pre-seeded database for SwarmAI distribution.

This script creates a seed database at build time containing:

- Default SwarmAgent with system configuration
- System MCP servers (Filesystem)
- Default SwarmWorkspace record
- App settings with initialization_complete=true

Skills are no longer seeded into the database.  Built-in skills live in
``backend/skills/`` and are discovered at runtime by ``SkillManager``.

The output DB uses DELETE journal mode (not WAL) so it is a single
portable file suitable for bundling — no ``-wal`` / ``-shm`` sidecars.
"""
import asyncio
import json
import logging
import sqlite3
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
        mcp_ids = await self._insert_system_mcps()
        await self._insert_default_workspace()
        await self._insert_app_settings()
        
        # Update agent with MCP IDs
        await self._update_agent_references(mcp_ids)
        
        # Validate the generated database
        if not await self._validate():
            logger.error("Seed database validation failed")
            return False
        
        # Ensure DELETE journal mode so the seed DB is a single portable file
        # (WAL mode creates -wal and -shm sidecar files that break bundling)
        conn = sqlite3.connect(str(self.output_path))
        try:
            mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if mode.lower() != "delete":
                logger.warning(f"Failed to set DELETE journal mode, got: {mode}")
            else:
                logger.info("Set journal_mode=DELETE for portable seed DB")
        finally:
            conn.close()
        
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
            "model": None,  # Model resolved at runtime from config.json
            "permission_mode": agent_config.get("permission_mode", "bypassPermissions"),
            "max_turns": agent_config.get("max_turns", 100),
            "system_prompt": agent_config.get("system_prompt", ""),
            "allowed_tools": json.dumps(agent_config.get("allowed_tools", [])),
            "plugin_ids": json.dumps(agent_config.get("plugin_ids", [])),
            "allowed_skills": json.dumps([]),  # Built-in skills always available without explicit listing
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
    
    async def _insert_app_settings(self) -> None:
        """Insert app_settings with initialization_complete=true.
        
        Only id, initialization_complete, created_at, updated_at are stored.
        All other config moved to ~/.swarm-ai/config.json (AppConfigManager).
        Credentials delegated to AWS credential chain.
        """
        settings = {
            "id": "default",
            "initialization_complete": 1,  # KEY: Set to true
            "created_at": self._now,
            "updated_at": self._now,
        }
        
        await self.db.app_settings.put(settings)
        logger.info("Inserted app_settings with initialization_complete=true")
    
    async def _update_agent_references(self, mcp_ids: list[str]) -> None:
        """Update the default agent with MCP references.
        
        Args:
            mcp_ids: List of inserted MCP server IDs
        """
        await self.db.agents.update("default", {
            "mcp_ids": json.dumps(mcp_ids),
            "updated_at": self._now,
        })
        logger.info(f"Updated agent with mcp_ids={mcp_ids}")
    
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
