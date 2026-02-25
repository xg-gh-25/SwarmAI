"""Plugin installation and management."""
import asyncio
import json
import shutil
import subprocess
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PluginMetadata:
    """Parsed plugin.json metadata."""
    name: str
    version: str
    description: str = ""
    author: str = ""
    license: str = ""
    homepage: str = ""
    repository: str = ""
    keywords: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    mcp_servers: list[dict] = field(default_factory=list)


@dataclass
class InstallResult:
    """Result of plugin installation."""
    success: bool
    plugin_id: Optional[str] = None
    name: str = ""
    version: str = ""
    description: str = ""
    author: str = ""
    installed_skills: list[str] = field(default_factory=list)
    installed_commands: list[str] = field(default_factory=list)
    installed_agents: list[str] = field(default_factory=list)
    installed_hooks: list[str] = field(default_factory=list)
    installed_mcp_servers: list[str] = field(default_factory=list)
    install_path: Optional[str] = None  # Path to installed plugin directory
    error: Optional[str] = None


@dataclass
class AvailablePlugin:
    """Plugin available in a marketplace."""
    name: str
    version: str
    description: str = ""
    author: str = ""
    keywords: list[str] = field(default_factory=list)


@dataclass
class SyncResult:
    """Result of syncing a git repository."""
    plugins: list[AvailablePlugin]
    is_marketplace: bool = True  # True if repo contains marketplace.json, False if single plugin
    marketplace_name: Optional[str] = None  # Name from marketplace.json if exists


class PluginManager:
    """Manages plugin installation, updates, and removal.

    Storage structure:
    - Cache: ~/.claude/plugins/cache/{marketplace}/{plugin-name}/
    - Install directories:
      - ~/.claude/skills/      (plugin skills)
      - ~/.claude/commands/    (plugin commands)
      - ~/.claude/hooks/       (plugin hooks)
    """

    def __init__(self, base_dir: Optional[Path] = None):
        """Initialize PluginManager.

        Args:
            base_dir: Base directory for plugin storage. Defaults to ~/.claude/
        """
        if base_dir is None:
            base_dir = Path.home() / ".claude"

        self.base_dir = base_dir
        self.cache_dir = self.base_dir / "plugins" / "cache"
        self.skills_dir = self.base_dir / "skills"
        self.commands_dir = self.base_dir / "commands"
        self.hooks_dir = self.base_dir / "hooks"
        self._ensure_directories()

    def _ensure_directories(self):
        """Ensure required directories exist."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.commands_dir.mkdir(parents=True, exist_ok=True)
        self.hooks_dir.mkdir(parents=True, exist_ok=True)

    def get_marketplace_cache_dir(self, marketplace_name: str) -> Path:
        """Get cache directory for a marketplace.

        Marketplace names like 'owner/repo' are converted to nested paths.
        """
        # Normalize the name (handle both / and backslash)
        safe_name = marketplace_name.replace("\\", "/")
        return self.cache_dir / safe_name

    async def sync_git_marketplace(
        self,
        marketplace_name: str,
        git_url: str,
        branch: str = "main"
    ) -> SyncResult:
        """Clone/pull git repo and scan for plugins.

        Returns SyncResult with:
        - plugins: list of available plugins
        - is_marketplace: True if repo has marketplace.json, False if single plugin
        - marketplace_name: Name from marketplace.json if exists
        """
        market_cache = self.get_marketplace_cache_dir(marketplace_name)

        try:
            if (market_cache / ".git").exists():
                # Pull latest
                logger.info(f"Pulling latest from {git_url} branch {branch}")
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "-C", str(market_cache), "fetch", "origin", branch],
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    logger.warning(f"Git fetch failed: {result.stderr}")

                result = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "-C", str(market_cache), "reset", "--hard", f"origin/{branch}"],
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    logger.warning(f"Git reset failed: {result.stderr}")
            else:
                # Clone fresh
                logger.info(f"Cloning {git_url} branch {branch}")
                market_cache.mkdir(parents=True, exist_ok=True)
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "clone", "-b", branch, "--depth", "1", git_url, str(market_cache)],
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    raise RuntimeError(f"Git clone failed: {result.stderr}")

            # Check for marketplace.json at root - this determines if it's a marketplace or single plugin
            marketplace_json = market_cache / ".claude-plugin" / "marketplace.json"

            if marketplace_json.exists():
                # This is a marketplace repo - parse marketplace.json
                plugins, actual_name = self._parse_marketplace_json_with_name(marketplace_json)
                logger.info(f"Found marketplace '{actual_name}' with {len(plugins)} plugins")
                return SyncResult(
                    plugins=plugins,
                    is_marketplace=True,
                    marketplace_name=actual_name,
                )

            # No marketplace.json - treat repo as a single plugin
            logger.info(f"No marketplace.json found, treating repo as single plugin")
            plugin = self._detect_repo_as_plugin(market_cache, marketplace_name)
            if plugin:
                return SyncResult(
                    plugins=[plugin],
                    is_marketplace=False,
                    marketplace_name=None,
                )

            # Fallback: Scan directories for plugins and skills
            plugins = []
            scan_dirs = [market_cache]

            # Also check common subdirectories where skills might be located
            for subdir_name in ["skills", "plugins", "packages"]:
                subdir = market_cache / subdir_name
                if subdir.exists() and subdir.is_dir():
                    scan_dirs.append(subdir)

            for scan_dir in scan_dirs:
                for item in scan_dir.iterdir():
                    if item.is_dir() and not item.name.startswith('.'):
                        # Check for full plugin format (.claude-plugin/plugin.json)
                        plugin_json = item / ".claude-plugin" / "plugin.json"
                        if plugin_json.exists():
                            try:
                                metadata = self._parse_plugin_json(plugin_json)
                                plugins.append(AvailablePlugin(
                                    name=metadata.name,
                                    version=metadata.version,
                                    description=metadata.description,
                                    author=metadata.author,
                                    keywords=metadata.keywords,
                                ))
                            except Exception as e:
                                logger.warning(f"Failed to parse {plugin_json}: {e}")
                        else:
                            # Check for standalone skill format (directory with .md file)
                            skill_info = self._detect_standalone_skill(item)
                            if skill_info:
                                plugins.append(skill_info)

            logger.info(f"Found {len(plugins)} plugins in {marketplace_name}")
            return SyncResult(
                plugins=plugins,
                is_marketplace=len(plugins) > 1,
                marketplace_name=None,
            )

        except Exception as e:
            logger.error(f"Failed to sync marketplace {marketplace_name}: {e}")
            raise

    def _parse_marketplace_json_with_name(self, path: Path) -> tuple[list[AvailablePlugin], str]:
        """Parse .claude-plugin/marketplace.json and return (plugins, marketplace_name).

        Expected format:
        {
            "name": "Marketplace Name",
            "metadata": {"version": "1.0.0"},
            "plugins": [
                {
                    "name": "plugin-name",
                    "source": "./",  # Optional: plugin source directory
                    "skills": [...]  # Optional if source has skills/
                }
            ]
        }
        """
        try:
            with open(path) as f:
                data = json.load(f)

            marketplace_name = data.get("name", "Unknown Marketplace")
            marketplace_base = path.parent.parent  # Parent of .claude-plugin
            plugins = []
            marketplace_version = data.get("metadata", {}).get("version", "1.0.0")

            for plugin_data in data.get("plugins", []):
                name = plugin_data.get("name", "")
                if not name:
                    continue

                # Get skills count - either explicit or auto-detect from source
                skills_list = plugin_data.get("skills", [])

                if not skills_list:
                    # Auto-detect skills from source directory
                    source_path = plugin_data.get("source", "")
                    # Ensure source_path is a string (could be dict in some formats)
                    if source_path and isinstance(source_path, str):
                        clean_source = source_path.lstrip("./")
                        if clean_source:
                            plugin_source_dir = marketplace_base / clean_source
                        else:
                            plugin_source_dir = marketplace_base

                        skills_dir = plugin_source_dir / "skills"
                        if skills_dir.exists() and skills_dir.is_dir():
                            skills_list = [
                                d.name for d in skills_dir.iterdir()
                                if d.is_dir() and not d.name.startswith('.')
                            ]

                # Use version from plugin_data if available
                plugin_version = plugin_data.get("version", marketplace_version)

                plugins.append(AvailablePlugin(
                    name=name,
                    version=plugin_version,
                    description=plugin_data.get("description", ""),
                    author=plugin_data.get("author", {}).get("name", "") or data.get("owner", {}).get("name", ""),
                    keywords=[f"{len(skills_list)} skills"],
                ))

            return plugins, marketplace_name
        except Exception as e:
            logger.warning(f"Failed to parse marketplace.json {path}: {e}")
            return [], "Unknown"

    def _detect_repo_as_plugin(self, repo_path: Path, fallback_name: str) -> Optional[AvailablePlugin]:
        """Detect if a repo is itself a single plugin.

        Checks for:
        1. .claude-plugin/plugin.json - full plugin format
        2. skills/ directory with skill content
        3. Markdown files at root (standalone skill)
        """
        # Check for plugin.json
        plugin_json = repo_path / ".claude-plugin" / "plugin.json"
        if plugin_json.exists():
            try:
                metadata = self._parse_plugin_json(plugin_json)
                return AvailablePlugin(
                    name=metadata.name,
                    version=metadata.version,
                    description=metadata.description,
                    author=metadata.author,
                    keywords=metadata.keywords,
                )
            except Exception as e:
                logger.warning(f"Failed to parse plugin.json: {e}")

        # Check for skills/ directory
        skills_dir = repo_path / "skills"
        if skills_dir.exists() and skills_dir.is_dir():
            skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir() and not d.name.startswith('.'))
            if skill_count > 0:
                # Extract name from repo path (last component)
                repo_name = repo_path.name
                return AvailablePlugin(
                    name=repo_name,
                    version="1.0.0",
                    description=f"Plugin with {skill_count} skills",
                    author="",
                    keywords=[f"{skill_count} skills"],
                )

        # Check for standalone skill (markdown files at root)
        skill_info = self._detect_standalone_skill(repo_path)
        if skill_info:
            return skill_info

        return None

    def _parse_marketplace_json(self, path: Path) -> list[AvailablePlugin]:
        """Parse .claude-plugin/marketplace.json file for plugin definitions.

        Expected format:
        {
            "name": "marketplace-name",
            "metadata": {"version": "1.0.0", "description": "..."},
            "plugins": [
                {
                    "name": "plugin-name",
                    "description": "...",
                    "skills": ["./skills/skill1", "./skills/skill2"]
                }
            ]
        }
        """
        try:
            with open(path) as f:
                data = json.load(f)

            plugins = []
            marketplace_version = data.get("metadata", {}).get("version", "1.0.0")

            for plugin_data in data.get("plugins", []):
                name = plugin_data.get("name", "")
                if not name:
                    continue

                # Count skills from the skills array
                skills_list = plugin_data.get("skills", [])

                plugins.append(AvailablePlugin(
                    name=name,
                    version=marketplace_version,
                    description=plugin_data.get("description", ""),
                    author=data.get("owner", {}).get("name", ""),
                    keywords=[f"{len(skills_list)} skills"],
                ))

            return plugins
        except Exception as e:
            logger.warning(f"Failed to parse marketplace.json {path}: {e}")
            return []

    def _detect_standalone_skill(self, skill_dir: Path) -> Optional[AvailablePlugin]:
        """Detect if a directory is a standalone skill (not a full plugin).

        Standalone skills are directories containing:
        - A markdown file (skill definition) like SKILL.md, README.md, or {name}.md
        - Or a skill.json metadata file
        """
        try:
            # Look for skill metadata file
            skill_json = skill_dir / "skill.json"
            if skill_json.exists():
                with open(skill_json) as f:
                    data = json.load(f)
                return AvailablePlugin(
                    name=data.get("name", skill_dir.name),
                    version=data.get("version", "1.0.0"),
                    description=data.get("description", ""),
                    author=data.get("author", ""),
                    keywords=data.get("keywords", ["skill"]),
                )

            # Look for common skill markdown files
            md_files = list(skill_dir.glob("*.md"))
            if not md_files:
                return None

            # Try to extract description from the first markdown file
            description = ""
            for md_file in md_files:
                if md_file.name.lower() in ["readme.md", "skill.md", f"{skill_dir.name.lower()}.md"]:
                    try:
                        content = md_file.read_text(encoding="utf-8")
                        # Get first non-empty, non-header line as description
                        for line in content.split("\n"):
                            line = line.strip()
                            if line and not line.startswith("#") and not line.startswith("```"):
                                description = line[:200]  # Limit length
                                break
                    except Exception:
                        pass
                    break

            # Consider it a skill if it has markdown files
            return AvailablePlugin(
                name=skill_dir.name,
                version="1.0.0",
                description=description,
                author="",
                keywords=["skill"],
            )
        except Exception as e:
            logger.warning(f"Failed to detect skill in {skill_dir}: {e}")
            return None

    def _parse_plugin_json(self, path: Path) -> PluginMetadata:
        """Parse plugin.json file."""
        with open(path) as f:
            data = json.load(f)

        return PluginMetadata(
            name=data.get("name", path.parent.parent.name),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            license=data.get("license", ""),
            homepage=data.get("homepage", ""),
            repository=data.get("repository", ""),
            keywords=data.get("keywords", []),
            skills=data.get("skills", []),
            commands=data.get("commands", []),
            agents=data.get("agents", []),
            hooks=data.get("hooks", []),
            mcp_servers=data.get("mcp_servers", []),
        )

    async def _clone_plugin_source(
        self,
        git_url: str,
        plugin_name: str,
        market_cache: Path
    ) -> Path:
        """Clone a git repository for a plugin with URL source.

        Returns the path to the cloned repository.
        """
        import subprocess

        # Create a subdirectory for this plugin's source
        plugin_cache_dir = market_cache / "_sources" / plugin_name
        plugin_cache_dir.parent.mkdir(parents=True, exist_ok=True)

        if plugin_cache_dir.exists():
            # Pull latest changes
            logger.info(f"Updating existing plugin source at {plugin_cache_dir}")
            result = subprocess.run(
                ["git", "-C", str(plugin_cache_dir), "pull", "--ff-only"],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                logger.warning(f"Git pull failed: {result.stderr}, will re-clone")
                shutil.rmtree(plugin_cache_dir)

        if not plugin_cache_dir.exists():
            # Clone the repository
            logger.info(f"Cloning plugin source from {git_url} to {plugin_cache_dir}")
            result = subprocess.run(
                ["git", "clone", "--depth", "1", git_url, str(plugin_cache_dir)],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to clone {git_url}: {result.stderr}")

        return plugin_cache_dir

    async def _install_from_marketplace_json(
        self,
        marketplace_json: Path,
        plugin_name: str,
        market_cache: Path
    ) -> Optional[InstallResult]:
        """Install a plugin defined in marketplace.json.

        marketplace.json format:
        {
            "plugins": [
                {
                    "name": "plugin-name",
                    "description": "...",
                    "source": "./",  # Optional: plugin source directory
                    "skills": ["./skills/skill1", "./skills/skill2"]  # Optional if source has skills/
                }
            ]
        }

        Returns InstallResult if plugin found and installed, None if not found.
        """
        try:
            with open(marketplace_json) as f:
                data = json.load(f)

            # Find the plugin definition
            plugin_data = None
            available_names = []
            for p in data.get("plugins", []):
                name = p.get("name", "")
                available_names.append(name)
                if name == plugin_name:
                    plugin_data = p
                    break

            if not plugin_data:
                logger.info(f"Plugin '{plugin_name}' not in marketplace.json. Available: {available_names}")
                return None  # Plugin not found in marketplace.json

            logger.info(f"Found plugin '{plugin_name}' in marketplace.json")
            logger.info(f"Plugin data: {plugin_data}")

            # Get marketplace base directory (parent of .claude-plugin)
            marketplace_base = marketplace_json.parent.parent
            logger.info(f"Marketplace base: {marketplace_base}")

            # Get skill paths - either explicit or auto-detect from source directory
            skill_paths = plugin_data.get("skills", [])
            logger.info(f"Explicit skill paths from plugin_data: {skill_paths}")

            # Resolve plugin source directory (needed for install_path and auto-detection)
            plugin_source_dir = None
            source_info = plugin_data.get("source", "")

            # Handle dict source format: {'source': 'url', 'url': 'https://...'}
            if isinstance(source_info, dict) and source_info.get("source") == "url":
                git_url = source_info.get("url", "")
                if git_url:
                    logger.info(f"Source is a git URL: {git_url}")
                    try:
                        plugin_source_dir = await self._clone_plugin_source(
                            git_url, plugin_name, market_cache
                        )
                        logger.info(f"Cloned plugin source to: {plugin_source_dir}")
                    except Exception as e:
                        logger.error(f"Failed to clone plugin source: {e}")
                        plugin_source_dir = None

            # Handle string source path (local path relative to marketplace)
            elif source_info and isinstance(source_info, str):
                clean_source = source_info.lstrip("./")
                if clean_source:
                    plugin_source_dir = marketplace_base / clean_source
                else:
                    plugin_source_dir = marketplace_base
                logger.info(f"Source is local path: {plugin_source_dir} (exists: {plugin_source_dir.exists()})")

            # If no explicit skills, auto-detect from source directory
            if not skill_paths:
                logger.info(f"No explicit skills, auto-detecting from source")

                if plugin_source_dir and plugin_source_dir.exists():
                    skills_dir = plugin_source_dir / "skills"
                    logger.info(f"Looking for skills dir at: {skills_dir} (exists: {skills_dir.exists()})")
                    if skills_dir.exists() and skills_dir.is_dir():
                        logger.info(f"Auto-detecting skills from {skills_dir}")
                        for skill_subdir in skills_dir.iterdir():
                            if skill_subdir.is_dir() and not skill_subdir.name.startswith('.'):
                                rel_path = f"./skills/{skill_subdir.name}"
                                skill_paths.append(rel_path)
                        logger.info(f"Auto-detected {len(skill_paths)} skills: {skill_paths}")

                        # Update marketplace_base to point to the source directory for skill installation
                        marketplace_base = plugin_source_dir
                        logger.info(f"Updated marketplace_base to: {marketplace_base}")
                    else:
                        logger.warning(f"Skills directory not found at {skills_dir}")
                else:
                    logger.warning(f"No valid source path - cannot auto-detect skills")

            logger.info(f"Plugin has {len(skill_paths)} skill paths: {skill_paths}")

            # Track the plugin source directory for install_path (used by Claude SDK)
            # For git URL plugins, this should be the cloned repo directory
            plugin_install_path = plugin_source_dir if plugin_source_dir else None

            # Install skills from the paths
            installed_skills = []
            for skill_path_str in skill_paths:
                # Skip non-string skill paths (could be dict in some formats)
                if not isinstance(skill_path_str, str):
                    continue
                # Resolve the skill path relative to marketplace base
                # skill_path is like "./skills/skill-name" or "skills/skill-name"
                clean_path = skill_path_str.lstrip("./")
                skill_src = marketplace_base / clean_path

                if not skill_src.exists():
                    # Try relative to market_cache as well
                    skill_src = market_cache / clean_path

                logger.info(f"Looking for skill at: {skill_src} (exists: {skill_src.exists()})")

                if skill_src.exists() and skill_src.is_dir():
                    skill_name = skill_src.name
                    dest = self.skills_dir / skill_name

                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(skill_src, dest)
                    installed_skills.append(skill_name)
                    logger.info(f"Installed skill from marketplace.json: {skill_name}")
                else:
                    logger.warning(f"Skill path not found: {skill_path_str} -> {skill_src}")

            marketplace_version = data.get("metadata", {}).get("version", "1.0.0")
            author = data.get("owner", {}).get("name", "")

            logger.info(f"Successfully installed plugin '{plugin_name}' with {len(installed_skills)} skills")

            # Determine install_path for Claude SDK:
            # - For git URL plugins: use the cloned plugin source directory
            # - For local plugins: use first installed skill's path as fallback
            if plugin_install_path:
                install_path = str(plugin_install_path)
            elif installed_skills:
                install_path = str(self.skills_dir / installed_skills[0])
            else:
                install_path = None

            logger.info(f"Plugin install_path: {install_path}")

            return InstallResult(
                success=True,
                name=plugin_name,
                version=marketplace_version,
                description=plugin_data.get("description", ""),
                author=author,
                installed_skills=installed_skills,
                installed_commands=[],
                installed_agents=[],
                installed_hooks=[],
                installed_mcp_servers=[],
                install_path=install_path,
            )

        except Exception as e:
            logger.error(f"Failed to install from marketplace.json: {e}", exc_info=True)
            return InstallResult(
                success=False,
                error=f"Failed to install plugin: {str(e)}"
            )

    async def install_plugin(
        self,
        plugin_name: str,
        marketplace_name: str,
        version: Optional[str] = None
    ) -> InstallResult:
        """Install a plugin from marketplace cache.

        Supports three formats:
        1. marketplace.json-based (virtual plugins with skill paths)
        2. Full plugin with .claude-plugin/plugin.json
        3. Standalone skill (directory with markdown files)

        Steps:
        1. Find plugin in marketplace cache
        2. Parse plugin.json or detect standalone skill
        3. Copy skills/ to ~/.claude/skills/
        4. Copy commands/ to ~/.claude/commands/
        5. Copy hooks/ to ~/.claude/hooks/
        6. Return installation result with metadata
        """
        market_cache = self.get_marketplace_cache_dir(marketplace_name)
        logger.info(f"Installing plugin '{plugin_name}' from marketplace '{marketplace_name}'")
        logger.info(f"Market cache dir: {market_cache} (exists: {market_cache.exists()})")

        # First, check if this is a marketplace.json-based plugin
        marketplace_json_locations = [
            market_cache / ".claude-plugin" / "marketplace.json",
            market_cache / "skills" / ".claude-plugin" / "marketplace.json",
            market_cache / "plugins" / ".claude-plugin" / "marketplace.json",
        ]

        for marketplace_json in marketplace_json_locations:
            logger.info(f"Checking marketplace.json at: {marketplace_json} (exists: {marketplace_json.exists()})")
            if marketplace_json.exists():
                result = await self._install_from_marketplace_json(
                    marketplace_json, plugin_name, market_cache
                )
                if result:
                    logger.info(f"Installed from marketplace.json: success={result.success}")
                    return result
                else:
                    logger.info(f"Plugin not found in this marketplace.json, trying next...")

        # Fallback: Search for the plugin directory
        plugin_dir = None
        search_locations = [
            market_cache / plugin_name,
            market_cache / "skills" / plugin_name,
            market_cache / "plugins" / plugin_name,
            market_cache / "packages" / plugin_name,
        ]

        for location in search_locations:
            if location.exists():
                plugin_dir = location
                break

        if not plugin_dir:
            return InstallResult(
                success=False,
                error=f"Plugin '{plugin_name}' not found in marketplace '{marketplace_name}'"
            )

        plugin_json = plugin_dir / ".claude-plugin" / "plugin.json"
        is_standalone_skill = not plugin_json.exists()
        skill_info: Optional[AvailablePlugin] = None

        # For standalone skills, verify it's a valid skill directory
        if is_standalone_skill:
            skill_info = self._detect_standalone_skill(plugin_dir)
            if not skill_info:
                return InstallResult(
                    success=False,
                    error=f"Invalid plugin: not a valid plugin or skill directory"
                )

        try:
            installed_skills = []
            installed_commands = []
            installed_agents = []
            installed_hooks = []
            installed_mcp_servers = []

            if is_standalone_skill:
                # For standalone skills, copy the entire directory as a skill
                dest = self.skills_dir / plugin_name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(plugin_dir, dest)
                installed_skills.append(plugin_name)
                logger.info(f"Installed standalone skill: {plugin_name}")

                return InstallResult(
                    success=True,
                    name=plugin_name,
                    version="1.0.0",
                    description=skill_info.description if skill_info else "",
                    author=skill_info.author if skill_info else "",
                    installed_skills=installed_skills,
                    installed_commands=[],
                    installed_agents=[],
                    installed_hooks=[],
                    installed_mcp_servers=[],
                    install_path=str(dest),  # Path to installed skill
                )

            # Full plugin format - parse metadata
            metadata = self._parse_plugin_json(plugin_json)

            # Copy skills
            skills_src = plugin_dir / "skills"
            if skills_src.exists():
                for skill_subdir in skills_src.iterdir():
                    if skill_subdir.is_dir():
                        dest = self.skills_dir / skill_subdir.name
                        if dest.exists():
                            shutil.rmtree(dest)
                        shutil.copytree(skill_subdir, dest)
                        installed_skills.append(skill_subdir.name)
                        logger.info(f"Installed skill: {skill_subdir.name}")

            # Copy commands
            commands_src = plugin_dir / "commands"
            if commands_src.exists():
                for cmd_file in commands_src.iterdir():
                    if cmd_file.is_file():
                        dest = self.commands_dir / cmd_file.name
                        shutil.copy2(cmd_file, dest)
                        installed_commands.append(cmd_file.name)
                        logger.info(f"Installed command: {cmd_file.name}")

            # Copy agents
            agents_src = plugin_dir / "agents"
            if agents_src.exists():
                agents_dest = self.base_dir / "agents"
                agents_dest.mkdir(parents=True, exist_ok=True)
                for agent_file in agents_src.iterdir():
                    if agent_file.is_file():
                        dest = agents_dest / agent_file.name
                        shutil.copy2(agent_file, dest)
                        installed_agents.append(agent_file.name)
                        logger.info(f"Installed agent: {agent_file.name}")

            # Copy hooks
            hooks_src = plugin_dir / "hooks"
            if hooks_src.exists():
                for hook_file in hooks_src.iterdir():
                    if hook_file.is_file():
                        dest = self.hooks_dir / hook_file.name
                        shutil.copy2(hook_file, dest)
                        installed_hooks.append(hook_file.name)
                        logger.info(f"Installed hook: {hook_file.name}")

            # Parse MCP servers from .mcp.json
            mcp_json = plugin_dir / ".mcp.json"
            if mcp_json.exists():
                with open(mcp_json) as f:
                    mcp_data = json.load(f)
                    for server_name in mcp_data.get("mcpServers", {}).keys():
                        installed_mcp_servers.append(server_name)
                        logger.info(f"Found MCP server: {server_name}")

            return InstallResult(
                success=True,
                name=metadata.name,
                version=metadata.version,
                description=metadata.description,
                author=metadata.author,
                installed_skills=installed_skills,
                installed_commands=installed_commands,
                installed_agents=installed_agents,
                installed_hooks=installed_hooks,
                installed_mcp_servers=installed_mcp_servers,
                install_path=str(plugin_dir),  # Path to plugin source directory
            )

        except Exception as e:
            logger.error(f"Failed to install plugin {plugin_name}: {e}")
            return InstallResult(success=False, error=str(e))

    async def uninstall_plugin(
        self,
        plugin_name: str,
        installed_skills: list[str],
        installed_commands: list[str],
        installed_agents: list[str],
        installed_hooks: list[str],
    ) -> dict:
        """Uninstall a plugin by removing its installed files.

        Returns dict with lists of removed items.
        """
        removed = {
            "skills": [],
            "commands": [],
            "agents": [],
            "hooks": [],
        }

        # Remove skills
        for skill_name in installed_skills:
            skill_path = self.skills_dir / skill_name
            if skill_path.exists():
                shutil.rmtree(skill_path)
                removed["skills"].append(skill_name)
                logger.info(f"Removed skill: {skill_name}")

        # Remove commands
        for cmd_name in installed_commands:
            cmd_path = self.commands_dir / cmd_name
            if cmd_path.exists():
                cmd_path.unlink()
                removed["commands"].append(cmd_name)
                logger.info(f"Removed command: {cmd_name}")

        # Remove agents
        agents_dir = self.base_dir / "agents"
        for agent_name in installed_agents:
            agent_path = agents_dir / agent_name
            if agent_path.exists():
                agent_path.unlink()
                removed["agents"].append(agent_name)
                logger.info(f"Removed agent: {agent_name}")

        # Remove hooks
        for hook_name in installed_hooks:
            hook_path = self.hooks_dir / hook_name
            if hook_path.exists():
                hook_path.unlink()
                removed["hooks"].append(hook_name)
                logger.info(f"Removed hook: {hook_name}")

        return removed

    def list_cached_plugins(self, marketplace_name: str) -> list[AvailablePlugin]:
        """List plugins cached from a marketplace (without syncing)."""
        market_cache = self.get_marketplace_cache_dir(marketplace_name)
        plugins = []

        if not market_cache.exists():
            return plugins

        # First, try to find marketplace.json which defines plugins
        marketplace_json_locations = [
            market_cache / ".claude-plugin" / "marketplace.json",
            market_cache / "skills" / ".claude-plugin" / "marketplace.json",
            market_cache / "plugins" / ".claude-plugin" / "marketplace.json",
        ]

        for marketplace_json in marketplace_json_locations:
            if marketplace_json.exists():
                return self._parse_marketplace_json(marketplace_json)

        # Fallback: Scan directories for plugins/skills
        scan_dirs = [market_cache]

        # Also check common subdirectories where skills might be located
        for subdir_name in ["skills", "plugins", "packages"]:
            subdir = market_cache / subdir_name
            if subdir.exists() and subdir.is_dir():
                scan_dirs.append(subdir)

        for scan_dir in scan_dirs:
            for item in scan_dir.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    # Check for full plugin format
                    plugin_json = item / ".claude-plugin" / "plugin.json"
                    if plugin_json.exists():
                        try:
                            metadata = self._parse_plugin_json(plugin_json)
                            plugins.append(AvailablePlugin(
                                name=metadata.name,
                                version=metadata.version,
                                description=metadata.description,
                                author=metadata.author,
                                keywords=metadata.keywords,
                            ))
                        except Exception as e:
                            logger.warning(f"Failed to parse {plugin_json}: {e}")
                    else:
                        # Check for standalone skill format
                        skill_info = self._detect_standalone_skill(item)
                        if skill_info:
                            plugins.append(skill_info)

        return plugins


# Global instance
plugin_manager = PluginManager()
