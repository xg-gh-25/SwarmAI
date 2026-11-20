"""Secrets of Inbox sources (a Slack user token, a mail password), kept in the OS store.

Where a secret lives, in order:

1. **macOS**: the login keychain, through the ``security`` CLI (as the backup token in
   ``core/backup_manager.py``), service ``com.swarmai.inbox``.
2. **Linux** with a Secret Service (GNOME Keyring, KWallet): ``secret-tool`` (libsecret).
3. Otherwise (Windows, a headless Linux): ``inbox-secrets.json`` in the app data directory,
   created ``0600``; on POSIX a file that group or others can read is refused, never used.

A secret is never taken from a source's options, a config file or an environment
variable, never passed in a process's argv (it goes on stdin), and never logged: errors
name the source and the secret's name, not its value.

Each secret is keyed by ``<source id>:<name>``, e.g. ``slack-1:user_token``.

Key public symbols: ``read_secret``, ``store_secret``, ``delete_secret``, ``SERVICE``.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SERVICE = "com.swarmai.inbox"
_FILE_NAME = "inbox-secrets.json"
_TIMEOUT_S = 10


def _account(source_id: str, name: str) -> str:
    if not source_id or not name or any(c in source_id + name for c in "\n\r\0\""):
        raise ValueError("invalid secret key")
    return f"{source_id}:{name}"


def _secrets_file() -> Path:
    from config import get_app_data_dir

    return get_app_data_dir() / _FILE_NAME


# -- macOS keychain ---------------------------------------------------------


def _keychain_read(account: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("keychain lookup unavailable: %s", type(exc).__name__)
        return None
    return result.stdout.rstrip("\n") if result.returncode == 0 and result.stdout else None


def _quote_for_security(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _keychain_write(account: str, value: str) -> bool:
    # `security -i` reads the command from stdin, so the secret is never in argv. It reads
    # one command per line: a line break inside a field would end this command and run
    # the rest as another, so such a field is refused (a token never contains one).
    if any(ch in field for field in (SERVICE, account, value) for ch in "\r\n\0"):
        logger.warning("refusing to write the keychain: a field holds a line break or NUL")
        return False
    command = (
        f"add-generic-password -U -s {_quote_for_security(SERVICE)} -a {_quote_for_security(account)}"
        f" -w {_quote_for_security(value)}\n"
    )
    try:
        result = subprocess.run(["security", "-i"], input=command, capture_output=True, text=True, timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("could not write the keychain: %s", type(exc).__name__)
        return False
    return result.returncode == 0


def _keychain_delete(account: str) -> None:
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", SERVICE, "-a", account],
            capture_output=True, timeout=_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        pass


# -- libsecret ---------------------------------------------------------------


def _secret_tool() -> Optional[str]:
    return shutil.which("secret-tool") if sys.platform.startswith("linux") else None


def _libsecret_read(tool: str, account: str) -> Optional[str]:
    try:
        result = subprocess.run(
            [tool, "lookup", "service", SERVICE, "account", account],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.rstrip("\n") if result.returncode == 0 and result.stdout else None


def _libsecret_write(tool: str, account: str, value: str) -> bool:
    try:
        result = subprocess.run(
            [tool, "store", f"--label=SwarmAI Inbox {account}", "service", SERVICE, "account", account],
            input=value, capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _libsecret_delete(tool: str, account: str) -> None:
    try:
        subprocess.run([tool, "clear", "service", SERVICE, "account", account], capture_output=True, timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        pass


# -- the 0600 file ----------------------------------------------------------


def _file_load(path: Path) -> Optional[dict[str, str]]:
    """The file's secrets; ``{}`` when there is no file; ``None`` when it exists but is
    refused (readable by others) or unreadable, so a writer never overwrites it."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    if os.name == "posix" and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        logger.warning("refusing %s: group or others can read it (chmod 600 to use it)", path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        logger.warning("could not read %s", path)
        return None
    if not isinstance(data, dict):
        return None
    return {str(k): str(v) for k, v in data.items()}


def _file_load_for_write(path: Path) -> dict[str, str]:
    data = _file_load(path)
    if data is None:
        raise RuntimeError(f"{path} exists but cannot be used safely; fix or move it first")
    return data


def _file_save(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# -- public -----------------------------------------------------------------


def read_secret(source_id: str, name: str) -> Optional[str]:
    """The secret, or ``None`` when no store has it."""
    account = _account(source_id, name)
    if sys.platform == "darwin":
        value = _keychain_read(account)
        if value:
            return value
    tool = _secret_tool()
    if tool:
        value = _libsecret_read(tool, account)
        if value:
            return value
    return (_file_load(_secrets_file()) or {}).get(account) or None


def store_secret(source_id: str, name: str, value: str) -> str:
    """Store the secret in the first store that takes it; returns ``keychain``,
    ``libsecret`` or ``file``."""
    account = _account(source_id, name)
    if not value:
        raise ValueError("empty secret")
    if sys.platform == "darwin" and _keychain_write(account, value):
        return "keychain"
    tool = _secret_tool()
    if tool and _libsecret_write(tool, account, value):
        return "libsecret"
    path = _secrets_file()
    data = _file_load_for_write(path)
    data[account] = value
    _file_save(path, data)
    return "file"


def delete_secret(source_id: str, name: str) -> None:
    account = _account(source_id, name)
    if sys.platform == "darwin":
        _keychain_delete(account)
    tool = _secret_tool()
    if tool:
        _libsecret_delete(tool, account)
    path = _secrets_file()
    if not path.exists():
        return
    data = _file_load_for_write(path)
    if data.pop(account, None) is not None:
        _file_save(path, data)
