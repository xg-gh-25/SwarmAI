"""Skill storage and synchronization management."""
import zipfile
import shutil
import re
import logging
from pathlib import Path
from dataclasses import dataclass, field

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of skill synchronization."""
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    total_local: int = 0
    total_plugins: int = 0
    total_db: int = 0


@dataclass
class SkillMetadata:
    """Metadata extracted from SKILL.md."""
    name: str
    description: str
    version: str = "1.0.0"
    author: str = "unknown"


class SkillManager:
    """Manages skill storage and synchronization between local filesystem and database.

    Storage structure:
    - Local: workspace/.claude/skills/{skill-name}/SKILL.md, ...
    - Skills are stored as extracted folder contents, NOT ZIP files
    """

    def __init__(self):
        self.local_dir = Path(settings.agent_workspace_dir) / ".claude" / "skills"

    def _ensure_local_dir(self):
        """Ensure local skills directory exists."""
        self.local_dir.mkdir(parents=True, exist_ok=True)

    def scan_local_skills(self) -> dict[str, Path]:
        """Scan local skills directory and return dict of skill_name -> path."""
        self._ensure_local_dir()
        skills = {}

        for item in self.local_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                # Check if it has SKILL.md (valid skill directory)
                skill_md = item / "SKILL.md"
                if skill_md.exists():
                    skills[item.name] = item
                else:
                    logger.warning(f"Skipping directory without SKILL.md: {item.name}")

        logger.info(f"Found {len(skills)} local skills: {list(skills.keys())}")
        return skills

    def extract_skill_metadata(self, skill_dir: Path) -> SkillMetadata:
        """Extract metadata from SKILL.md file."""
        skill_md = skill_dir / "SKILL.md"

        name = skill_dir.name
        description = f"Skill: {name}"
        version = "1.0.0"
        author = "unknown"

        if skill_md.exists():
            content = skill_md.read_text(encoding='utf-8')

            # Try to extract name from first heading
            name_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
            if name_match:
                name = name_match.group(1).strip()

            # Try to extract description (first paragraph after heading)
            desc_match = re.search(r'^#[^\n]+\n+([^\n#]+)', content, re.MULTILINE)
            if desc_match:
                description = desc_match.group(1).strip()

            # Try to extract version
            version_match = re.search(r'[Vv]ersion[:\s]+([0-9.]+)', content)
            if version_match:
                version = version_match.group(1)

        return SkillMetadata(
            name=name,
            description=description,
            version=version,
            author=author
        )

    async def upload_to_draft(self, skill_name: str, skill_dir: Path) -> str:
        """Return local path for skill directory."""
        logger.info(f"Skill {skill_name} stored at {skill_dir}")
        return f"file://{skill_dir}"

    async def publish_draft(self, skill_name: str, new_version: int) -> str:
        """Publish draft as a new version. Returns local path."""
        local_path = self.local_dir / skill_name
        logger.info(f"Published {skill_name} v{new_version} at {local_path}")
        return f"file://{local_path}"

    async def discard_draft(self, skill_name: str) -> int:
        """Discard draft for a skill. No-op in local mode."""
        logger.info(f"Discard draft for {skill_name} (no-op)")
        return 0

    async def download_version_to_local(self, skill_name: str, version: int) -> Path:
        """Return local path for skill (already local)."""
        local_skill_dir = self.local_dir / skill_name
        logger.info(f"Skill {skill_name} v{version} at {local_skill_dir}")
        return local_skill_dir

    async def check_draft_exists(self, skill_name: str) -> bool:
        """Check if a draft exists for a skill."""
        local_path = self.local_dir / skill_name
        return local_path.exists()

    def extract_zip_to_directory(self, zip_path: Path, skill_name: str) -> Path:
        """Extract ZIP file to skills directory."""
        self._ensure_local_dir()
        dest_dir = self.local_dir / skill_name

        # Remove existing directory if exists
        if dest_dir.exists():
            shutil.rmtree(dest_dir)

        # Extract ZIP
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Check if ZIP contains a root folder or files directly
            namelist = zf.namelist()

            # Detect if there's a single root folder
            root_folders = set()
            for name in namelist:
                parts = name.split('/')
                if len(parts) > 1 and parts[0]:
                    root_folders.add(parts[0])

            if len(root_folders) == 1:
                # ZIP has a single root folder, extract and rename
                root_folder = list(root_folders)[0]
                temp_dir = self.local_dir / f"_temp_{skill_name}"
                zf.extractall(temp_dir)

                # Move the root folder to the correct name
                extracted_dir = temp_dir / root_folder
                if extracted_dir.exists():
                    shutil.move(str(extracted_dir), str(dest_dir))
                    shutil.rmtree(temp_dir)
                else:
                    # Fallback: rename temp dir
                    shutil.move(str(temp_dir), str(dest_dir))
            else:
                # ZIP contains files directly, extract to dest_dir
                dest_dir.mkdir(parents=True, exist_ok=True)
                zf.extractall(dest_dir)

        logger.info(f"Extracted ZIP to: {dest_dir}")
        return dest_dir

    async def upload_skill_package(
        self,
        zip_content: bytes,
        skill_name: str,
        original_filename: str  # noqa: ARG002
    ) -> dict:
        """Upload skill package: extract to local directory.

        Args:
            zip_content: The ZIP file content as bytes
            skill_name: Name for the skill
            original_filename: Original filename for logging (unused but kept for API compatibility)

        Returns:
            dict with skill metadata and draft location
        """
        import tempfile

        self._ensure_local_dir()

        # Save ZIP to temp file
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            tmp.write(zip_content)
            tmp_path = Path(tmp.name)

        try:
            # Validate ZIP has SKILL.md
            with zipfile.ZipFile(tmp_path, 'r') as zf:
                namelist = zf.namelist()
                has_skill_md = any(
                    name.endswith('SKILL.md') or name == 'SKILL.md'
                    for name in namelist
                )
                if not has_skill_md:
                    raise ValueError("ZIP must contain a SKILL.md file")

            # Extract to local directory
            skill_dir = self.extract_zip_to_directory(tmp_path, skill_name)

            # Extract metadata
            metadata = self.extract_skill_metadata(skill_dir)

            # Return local path as draft location
            draft_location = f"file://{skill_dir}"

            return {
                "name": metadata.name,
                "description": metadata.description,
                "version": metadata.version,
                "draft_s3_location": draft_location,
                "local_path": str(skill_dir),
            }

        finally:
            # Cleanup temp file
            tmp_path.unlink(missing_ok=True)

    async def delete_skill_files(self, skill_name: str) -> None:
        """Delete skill from local directory."""
        local_path = self.local_dir / skill_name
        if local_path.exists():
            shutil.rmtree(local_path)
            logger.info(f"Deleted local skill directory: {local_path}")

    async def refresh(self, db_skills: list[dict]) -> tuple[SyncResult, list[dict]]:
        """
        Synchronize skills between local directory and database.

        Scans local skills directory for user-created skills.
        Plugin skills are managed separately by the plugin system.

        Args:
            db_skills: Current skills from database

        Returns:
            Tuple of (SyncResult, list of skills to add to DB)
        """
        result = SyncResult()
        skills_to_add = []

        # Scan local skills
        local_skills = self.scan_local_skills()

        # Build DB skill maps by folder name
        db_skill_map = {}
        db_plugin_skills = {}  # Skills from plugins (source_type='plugin')

        for skill in db_skills:
            folder_name = skill.get('folder_name')
            if not folder_name:
                # Fallback: use sanitized skill name
                folder_name = skill.get('name', '').lower().replace(' ', '-')

            if folder_name:
                # Separate plugin skills from user skills
                if skill.get('source_type') == 'plugin':
                    db_plugin_skills[folder_name] = skill
                else:
                    db_skill_map[folder_name] = skill

        result.total_local = len(local_skills)
        result.total_plugins = len(db_plugin_skills)
        result.total_db = len(db_skills)

        # Process local skills (user-created)
        for skill_name, skill_dir in local_skills.items():
            in_db = skill_name in db_skill_map
            is_plugin_skill = skill_name in db_plugin_skills

            try:
                if is_plugin_skill:
                    # Skip plugin skills - they are managed by the plugin system
                    logger.debug(f"Skill {skill_name}: from plugin, skipping")
                    continue

                if not in_db:
                    # Local skill not in DB: add to DB
                    logger.info(f"Skill {skill_name}: local only, adding to DB")
                    metadata = self.extract_skill_metadata(skill_dir)
                    skills_to_add.append({
                        "name": metadata.name,
                        "folder_name": skill_name,
                        "description": metadata.description,
                        "version": metadata.version,
                        "local_path": str(skill_dir),
                        "source_type": "local",
                        "is_system": False,
                        "created_by": "sync",
                    })
                    result.added.append(skill_name)
                else:
                    # Already in DB - update local_path if needed
                    existing = db_skill_map[skill_name]
                    if existing.get('local_path') != str(skill_dir):
                        logger.info(f"Skill {skill_name}: updating local_path")
                        result.updated.append(skill_name)

            except Exception as e:
                logger.error(f"Error syncing skill {skill_name}: {e}")
                result.errors.append({"skill": skill_name, "error": str(e)})

        # Check for orphaned DB entries (user skills without local files)
        for skill_name, skill in db_skill_map.items():
            if skill_name not in local_skills:
                # Only mark user-created skills as orphaned, not plugin skills
                source_type = skill.get('source_type', 'user')
                if source_type in ('user', 'local'):
                    logger.info(f"Skill {skill_name}: DB only (orphaned), marking for removal")
                    result.removed.append(skill_name)

        return result, skills_to_add


# Global instance
skill_manager = SkillManager()
