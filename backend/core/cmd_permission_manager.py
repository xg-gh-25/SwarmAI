"""Filesystem-based command permission manager with in-memory cache.

This module provides the ``CmdPermissionManager`` class for managing dangerous
bash command approvals.  It replaces the per-session in-memory approval dict
with a shared, persistent filesystem store under ``~/.swarm-ai/cmd_permissions/``.

Key design decisions:

- **Zero IO on checks**: Both ``is_dangerous()`` and ``is_approved()`` read
  from in-memory lists loaded at startup — no file IO on the hot path.
- **Glob matching**: Uses ``fnmatch`` so patterns like ``rm -rf /tmp/*``
  match concrete commands like ``rm -rf /tmp/old``.
- **Shared across sessions**: All agent sessions share the same approved
  list.  Approve once, applies everywhere.
- **Human-editable files**: Both JSON files can be edited manually; call
  ``reload()`` to pick up external changes.
- **Overly-broad rejection**: ``approve()`` rejects bare ``*`` patterns
  that would approve every command.

Public API:

- ``CmdPermissionManager`` — Main class with ``load()``, ``is_dangerous()``,
  ``is_approved()``, ``approve()``, ``reload()`` methods.
- ``DEFAULT_DANGEROUS_PATTERNS`` — List of default dangerous glob patterns.

File structure::

    ~/.swarm-ai/cmd_permissions/
    ├── approved_commands.json
    └── dangerous_patterns.json
"""

import fnmatch
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_app_data_dir

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DANGEROUS_PATTERNS: list[str] = [
    "rm -rf *",
    "rm -rf /*",
    "rm -rf ~*",
    "sudo *",
    "chmod 777 *",
    "chmod -R 777 *",
    "chown -R * /",
    "kill -9 *",
    "mkfs.*",
    "dd if=*",
    "curl *|bash*",
    "curl *|sh*",
    "wget *|bash*",
    "wget *|sh*",
    "> /dev/sda*",
    "> /dev/hda*",
    "> /dev/nvme*",
    "> /dev/vda*",
    "> /etc/*",
    ":()*{*:*|*:*&*}*;*:*",
]

# Patterns that are too broad to approve — would match everything
_OVERLY_BROAD_PATTERNS: frozenset[str] = frozenset({"*", "**", "* *"})



# ---------------------------------------------------------------------------
# CmdPermissionManager
# ---------------------------------------------------------------------------


class CmdPermissionManager:
    """Filesystem-based command approval with in-memory cache.

    Stores dangerous patterns and approved commands in JSON files under
    ``~/.swarm-ai/cmd_permissions/``.  Both files are loaded into memory
    at startup; all ``is_dangerous()`` and ``is_approved()`` calls are
    pure lookups against the cache — zero file IO.

    Typical lifecycle::

        mgr = CmdPermissionManager()
        mgr.load()                              # once at startup
        if mgr.is_dangerous("rm -rf /tmp/old"):
            if not mgr.is_approved("rm -rf /tmp/old"):
                # prompt user …
                mgr.approve("rm -rf /tmp/*")    # persist + cache
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir: Path = base_dir or (
            get_app_data_dir() / "cmd_permissions"
        )
        self._approved_path: Path = self._base_dir / "approved_commands.json"
        self._dangerous_path: Path = self._base_dir / "dangerous_patterns.json"
        self._approved: list[dict[str, Any]] | None = None
        self._dangerous: list[str] | None = None

    # -- public API --------------------------------------------------------

    def load(self) -> None:
        """Load both permission files into memory.  Called once at startup.

        If the ``cmd_permissions/`` directory or its files do not exist,
        they are created with default content (built-in dangerous patterns
        and an empty approved list).
        """
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._load_dangerous_patterns()
        self._load_approved_commands()
        logger.info(
            "CmdPermissionManager loaded: %d dangerous patterns, "
            "%d approved commands",
            len(self._dangerous or []),
            len(self._approved or []),
        )

    def is_dangerous(self, command: str) -> bool:
        """Check *command* against dangerous patterns (glob matching).

        Returns ``True`` if the command matches any pattern in
        ``dangerous_patterns.json``.  Reads from the in-memory cache
        only — zero file IO.
        """
        if self._dangerous is None:
            self.load()
        assert self._dangerous is not None
        return any(
            fnmatch.fnmatch(command, pattern)
            for pattern in self._dangerous
        )

    def is_approved(self, command: str) -> bool:
        """Check *command* against approved patterns (glob matching).

        Returns ``True`` if the command matches any approved pattern in
        ``approved_commands.json``.  Reads from the in-memory cache
        only — zero file IO.
        """
        if self._approved is None:
            self.load()
        assert self._approved is not None
        return any(
            fnmatch.fnmatch(command, entry["pattern"])
            for entry in self._approved
        )

    def approve(self, command_pattern: str) -> None:
        """Append *command_pattern* to ``approved_commands.json`` and cache.

        Raises ``ValueError`` if the pattern is overly broad (e.g. bare
        ``*``) which would approve every command.

        Args:
            command_pattern: Glob pattern to approve (e.g. ``rm -rf /tmp/*``).
        """
        stripped = command_pattern.strip()
        if stripped in _OVERLY_BROAD_PATTERNS:
            raise ValueError(
                f"Pattern {stripped!r} is too broad — it would approve "
                "all commands.  Use a more specific pattern."
            )

        if self._approved is None:
            self.load()
        assert self._approved is not None

        entry: dict[str, Any] = {
            "pattern": stripped,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "approved_by": "user",
        }
        self._approved.append(entry)
        self._write_approved()
        logger.info("Approved command pattern: %s", stripped)

    def reload(self) -> None:
        """Force re-read from files (e.g. after manual edits)."""
        self._approved = None
        self._dangerous = None
        self.load()

    # -- private helpers ----------------------------------------------------

    def _load_dangerous_patterns(self) -> None:
        """Load dangerous patterns from file, seeding defaults if missing."""
        try:
            raw = self._dangerous_path.read_text(encoding="utf-8").strip()
            if not raw:
                raise ValueError("empty file")
            data = json.loads(raw)
            if not isinstance(data, dict) or "patterns" not in data:
                raise ValueError("missing 'patterns' key")
            self._dangerous = list(data["patterns"])
            logger.info(
                "Loaded %d dangerous patterns from %s",
                len(self._dangerous),
                self._dangerous_path,
            )
        except FileNotFoundError:
            logger.info(
                "dangerous_patterns.json not found — seeding defaults"
            )
            self._dangerous = list(DEFAULT_DANGEROUS_PATTERNS)
            self._write_dangerous()
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Invalid dangerous_patterns.json (%s) — using defaults",
                exc,
            )
            self._dangerous = list(DEFAULT_DANGEROUS_PATTERNS)
            self._write_dangerous()

    def _load_approved_commands(self) -> None:
        """Load approved commands from file, creating empty if missing."""
        try:
            raw = self._approved_path.read_text(encoding="utf-8").strip()
            if not raw:
                raise ValueError("empty file")
            data = json.loads(raw)
            if not isinstance(data, dict) or "commands" not in data:
                raise ValueError("missing 'commands' key")
            self._approved = list(data["commands"])
            logger.info(
                "Loaded %d approved commands from %s",
                len(self._approved),
                self._approved_path,
            )
        except FileNotFoundError:
            logger.info(
                "approved_commands.json not found — creating empty file"
            )
            self._approved = []
            self._write_approved()
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Invalid approved_commands.json (%s) — starting empty",
                exc,
            )
            self._approved = []
            self._write_approved()

    def _write_dangerous(self) -> None:
        """Persist current dangerous patterns to disk."""
        if self._dangerous is None:
            return
        payload = {"patterns": self._dangerous}
        self._dangerous_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_approved(self) -> None:
        """Persist current approved commands to disk."""
        if self._approved is None:
            return
        payload = {"commands": self._approved}
        self._approved_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
