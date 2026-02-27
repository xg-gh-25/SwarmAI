"""Local skill storage and Git-based version management for desktop application."""
import asyncio
import zipfile
import shutil
import re
import subprocess
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from config import settings, get_app_data_dir

logger = logging.getLogger(__name__)


@dataclass
class GitInfo:
    """Git repository information."""
    is_git_repo: bool = False
    remote_url: Optional[str] = None
    current_branch: str = "main"
    current_commit: Optional[str] = None
    commit_message: Optional[str] = None
    commit_date: Optional[str] = None
    has_uncommitted_changes: bool = False


@dataclass
class SkillVersion:
    """Information about a skill version (Git commit)."""
    commit_hash: str
    message: str
    author: str
    date: str
    is_current: bool = False


@dataclass
class SkillMetadata:
    """Metadata extracted from SKILL.md."""
    name: str
    description: str
    version: str = "1.0.0"
    author: str = "unknown"


@dataclass
class InstallResult:
    """Result of skill installation."""
    success: bool
    skill_name: str
    local_path: str
    git_url: Optional[str] = None
    error: Optional[str] = None


class LocalSkillManager:
    """Manages local skill storage with Git-based version control.

    Storage structure:
    - Local: {data_dir}/skills/{skill-name}/
      - SKILL.md (required)
      - .git/ (for Git-managed skills)
      - Other skill files...

    Features:
    - Install skills from Git repositories
    - Update skills via git pull
    - View version history via git log
    - Rollback to previous versions via git checkout
    - Local-only skills without Git
    """

    def __init__(self, data_dir: Optional[Path] = None):
        """Initialize the local skill manager.

        Args:
            data_dir: Base data directory. If None, uses platform-specific default.
        """
        if data_dir is None:
            data_dir = get_app_data_dir()

        self.data_dir = data_dir
        self.skills_dir = data_dir / "skills"
        self._ensure_dirs()

    def _ensure_dirs(self):
        """Ensure necessary directories exist."""
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    async def _run_git_command(
        self,
        args: list[str],
        cwd: Optional[Path] = None,
        check: bool = True
    ) -> subprocess.CompletedProcess:
        """Run a git command asynchronously."""
        return await asyncio.to_thread(
            subprocess.run,
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=check
        )

    def _is_git_installed(self) -> bool:
        """Check if git is installed."""
        try:
            result = subprocess.run(
                ["git", "--version"],
                capture_output=True,
                text=True,
                check=True
            )
            return result.returncode == 0
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def scan_local_skills(self) -> dict[str, Path]:
        """Scan local skills directory and return dict of skill_name -> path."""
        self._ensure_dirs()
        skills = {}

        for item in self.skills_dir.iterdir():
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

    async def get_git_info(self, skill_dir: Path) -> GitInfo:
        """Get Git repository information for a skill directory."""
        git_dir = skill_dir / ".git"
        if not git_dir.exists():
            return GitInfo(is_git_repo=False)

        info = GitInfo(is_git_repo=True)

        try:
            # Get remote URL
            result = await self._run_git_command(
                ["remote", "get-url", "origin"],
                cwd=skill_dir,
                check=False
            )
            if result.returncode == 0:
                info.remote_url = result.stdout.strip()

            # Get current branch
            result = await self._run_git_command(
                ["rev-parse", "--abbrev-ref", "HEAD"],
                cwd=skill_dir,
                check=False
            )
            if result.returncode == 0:
                info.current_branch = result.stdout.strip()

            # Get current commit hash
            result = await self._run_git_command(
                ["rev-parse", "HEAD"],
                cwd=skill_dir,
                check=False
            )
            if result.returncode == 0:
                info.current_commit = result.stdout.strip()[:8]

            # Get commit message
            result = await self._run_git_command(
                ["log", "-1", "--pretty=%s"],
                cwd=skill_dir,
                check=False
            )
            if result.returncode == 0:
                info.commit_message = result.stdout.strip()

            # Get commit date
            result = await self._run_git_command(
                ["log", "-1", "--pretty=%ci"],
                cwd=skill_dir,
                check=False
            )
            if result.returncode == 0:
                info.commit_date = result.stdout.strip()

            # Check for uncommitted changes
            result = await self._run_git_command(
                ["status", "--porcelain"],
                cwd=skill_dir,
                check=False
            )
            if result.returncode == 0:
                info.has_uncommitted_changes = bool(result.stdout.strip())

        except Exception as e:
            logger.warning(f"Error getting git info for {skill_dir}: {e}")

        return info

    async def install_from_git(
        self,
        git_url: str,
        skill_name: Optional[str] = None,
        branch: str = "main"
    ) -> InstallResult:
        """Install a skill from a Git repository.

        Args:
            git_url: Git repository URL (https:// or git@)
            skill_name: Optional custom name for the skill directory
            branch: Branch to clone (default: main)

        Returns:
            InstallResult with success status and details
        """
        if not self._is_git_installed():
            return InstallResult(
                success=False,
                skill_name="",
                local_path="",
                error="Git is not installed. Please install Git first."
            )

        # Extract skill name from URL if not provided
        if skill_name is None:
            # Handle URLs like https://github.com/user/repo.git or git@github.com:user/repo.git
            match = re.search(r'/([^/]+?)(?:\.git)?$', git_url)
            if match:
                skill_name = match.group(1)
            else:
                skill_name = f"skill-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # Sanitize skill name (skill_name is guaranteed non-None at this point)
        skill_name = re.sub(r'[^a-zA-Z0-9_-]', '-', str(skill_name).lower())
        dest_dir = self.skills_dir / skill_name

        # Check if already exists
        if dest_dir.exists():
            return InstallResult(
                success=False,
                skill_name=skill_name,
                local_path=str(dest_dir),
                error=f"Skill '{skill_name}' already exists. Use update instead."
            )

        try:
            # Clone the repository
            result = await self._run_git_command(
                ["clone", "--branch", branch, "--depth", "1", git_url, str(dest_dir)],
                check=True
            )

            # Verify SKILL.md exists
            skill_md = dest_dir / "SKILL.md"
            if not skill_md.exists():
                # Clean up
                shutil.rmtree(dest_dir)
                return InstallResult(
                    success=False,
                    skill_name=skill_name,
                    local_path="",
                    error="Repository does not contain a SKILL.md file."
                )

            logger.info(f"Successfully installed skill '{skill_name}' from {git_url}")
            return InstallResult(
                success=True,
                skill_name=skill_name,
                local_path=str(dest_dir),
                git_url=git_url
            )

        except subprocess.CalledProcessError as e:
            # Clean up on failure
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            error_msg = e.stderr if e.stderr else str(e)
            return InstallResult(
                success=False,
                skill_name=skill_name,
                local_path="",
                error=f"Git clone failed: {error_msg}"
            )

    async def update_skill(self, skill_name: str) -> tuple[bool, str]:
        """Update a Git-managed skill by pulling latest changes.

        Args:
            skill_name: Name of the skill to update

        Returns:
            Tuple of (success, message)
        """
        skill_dir = self.skills_dir / skill_name
        if not skill_dir.exists():
            return False, f"Skill '{skill_name}' not found."

        git_info = await self.get_git_info(skill_dir)
        if not git_info.is_git_repo:
            return False, f"Skill '{skill_name}' is not a Git repository."

        try:
            # Stash any local changes
            if git_info.has_uncommitted_changes:
                await self._run_git_command(
                    ["stash", "push", "-m", "auto-stash before update"],
                    cwd=skill_dir,
                    check=False
                )

            # Pull latest changes
            result = await self._run_git_command(
                ["pull", "--rebase"],
                cwd=skill_dir,
                check=True
            )

            message = result.stdout.strip() if result.stdout else "Updated successfully"
            logger.info(f"Updated skill '{skill_name}': {message}")
            return True, message

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            return False, f"Update failed: {error_msg}"

    async def get_version_history(
        self,
        skill_name: str,
        limit: int = 20
    ) -> list[SkillVersion]:
        """Get version history (Git commits) for a skill.

        Args:
            skill_name: Name of the skill
            limit: Maximum number of versions to return

        Returns:
            List of SkillVersion objects
        """
        skill_dir = self.skills_dir / skill_name
        if not skill_dir.exists():
            return []

        git_info = await self.get_git_info(skill_dir)
        if not git_info.is_git_repo:
            return []

        try:
            # Get commit log
            result = await self._run_git_command(
                ["log", f"-{limit}", "--pretty=format:%H|%s|%an|%ci"],
                cwd=skill_dir,
                check=True
            )

            versions = []
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                parts = line.split('|', 3)
                if len(parts) >= 4:
                    commit_hash = parts[0]
                    versions.append(SkillVersion(
                        commit_hash=commit_hash,
                        message=parts[1],
                        author=parts[2],
                        date=parts[3],
                        is_current=(commit_hash == git_info.current_commit or
                                    commit_hash.startswith(git_info.current_commit or ""))
                    ))

            return versions

        except subprocess.CalledProcessError:
            return []

    async def rollback_to_version(
        self,
        skill_name: str,
        commit_hash: str
    ) -> tuple[bool, str]:
        """Rollback a skill to a specific Git commit.

        Args:
            skill_name: Name of the skill
            commit_hash: Git commit hash to rollback to

        Returns:
            Tuple of (success, message)
        """
        skill_dir = self.skills_dir / skill_name
        if not skill_dir.exists():
            return False, f"Skill '{skill_name}' not found."

        git_info = await self.get_git_info(skill_dir)
        if not git_info.is_git_repo:
            return False, f"Skill '{skill_name}' is not a Git repository."

        try:
            # Checkout the specific commit
            result = await self._run_git_command(
                ["checkout", commit_hash],
                cwd=skill_dir,
                check=True
            )

            logger.info(f"Rolled back skill '{skill_name}' to commit {commit_hash[:8]}")
            return True, f"Rolled back to commit {commit_hash[:8]}"

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            return False, f"Rollback failed: {error_msg}"

    def extract_zip_to_directory(self, zip_path: Path, skill_name: str) -> Path:
        """Extract ZIP file to skills directory."""
        self._ensure_dirs()
        dest_dir = self.skills_dir / skill_name

        # Remove existing directory if exists
        if dest_dir.exists():
            shutil.rmtree(dest_dir)

        # Extract ZIP
        with zipfile.ZipFile(zip_path, 'r') as zf:
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
                temp_dir = self.skills_dir / f"_temp_{skill_name}"
                zf.extractall(temp_dir)

                # Move the root folder to the correct name
                extracted_dir = temp_dir / root_folder
                if extracted_dir.exists():
                    shutil.move(str(extracted_dir), str(dest_dir))
                    shutil.rmtree(temp_dir)
                else:
                    shutil.move(str(temp_dir), str(dest_dir))
            else:
                # ZIP contains files directly
                dest_dir.mkdir(parents=True, exist_ok=True)
                zf.extractall(dest_dir)

        logger.info(f"Extracted ZIP to: {dest_dir}")
        return dest_dir

    async def upload_skill_from_zip(
        self,
        zip_content: bytes,
        skill_name: str
    ) -> dict:
        """Upload skill from ZIP file content.

        Args:
            zip_content: The ZIP file content as bytes
            skill_name: Name for the skill

        Returns:
            dict with skill metadata
        """
        import tempfile

        self._ensure_dirs()

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

            return {
                "name": metadata.name,
                "description": metadata.description,
                "version": metadata.version,
                "local_path": str(skill_dir),
                "folder_name": skill_name,
            }

        finally:
            # Cleanup temp file
            tmp_path.unlink(missing_ok=True)

    async def delete_skill(self, skill_name: str) -> bool:
        """Delete a skill from local storage.

        Args:
            skill_name: Name of the skill to delete

        Returns:
            True if deleted successfully
        """
        skill_dir = self.skills_dir / skill_name
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            logger.info(f"Deleted local skill directory: {skill_dir}")
            return True
        return False

    def get_skill_path(self, skill_name: str) -> Optional[Path]:
        """Get the local path to a skill directory.

        Args:
            skill_name: Name of the skill

        Returns:
            Path to skill directory or None if not found
        """
        skill_dir = self.skills_dir / skill_name
        if skill_dir.exists() and (skill_dir / "SKILL.md").exists():
            return skill_dir
        return None


# Global instance
local_skill_manager = LocalSkillManager()
