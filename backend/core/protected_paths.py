"""protected_paths — what an agent-run command or file tool may not write, and what an
unattended job may not read: one list, one path syntax, read by the chat settings
builder (``prompt_builder._chat_cli_settings``) and the job executor
(``jobs.executor._job_settings``).

Why the deny rules are the mechanism: the CLI applies ``Edit(...)`` / ``Read(...)`` deny
rules to the file tools in every permission mode, ``bypassPermissions`` and subagents
included, and when its OS sandbox is on it merges the same rules into the sandbox's
``denyWrite`` / ``denyRead`` (CLI 2.1.301 settings schema). So one rule set covers both
Bash, when sandboxed, and Write/Edit/Read, always. A command's text is never consulted:
however a write is phrased, the OS refuses it.

The lists:

- ``credential_rules()``: files holding secrets (cloud, SSH, GitHub CLI, netrc, SwarmAI's
  own secrets). Unattended jobs may neither read nor write them; a non-owner channel
  session adds its own extras (``prompt_builder.restricted_session_policy``).
- ``control_rules()``: SwarmAI's own control files (its runtime, startup script, config,
  dangerous-command list, CLI settings). ``startup_rules()``: the startup hooks a shell,
  ssh, git or launchd runs on their own. ``protected_write_rules()`` is both: unattended
  jobs and non-owner channel sessions never write them. The owner's own sessions are
  watched by the owner, who may edit their dotfiles: they get ``control_rules()`` only,
  and only with the sandbox on (``prompt_builder._chat_cli_settings``).
- ``runs_later_rules()``: what a later run executes or reads as instructions: job
  definitions, the workspace's ``.claude`` (skills, hooks), every project's
  ``4-capabilities``, every repository's ``.git`` in the workspace, ``.context``,
  ``~/.claude`` and the rest of the app data dir (skill folders, worktrees, config). An
  unattended job may not write them, so it cannot hand a later, wider run new code.
- ``CREDENTIAL_ENV_VARS``: variables a sandboxed job command must not see.

Not a secret boundary: measured on CLI 2.1.301, a sandboxed Bash on macOS still reads the
login keychain (``security find-generic-password``, ``gh auth token``). The sandbox hides
credential files and variables only; a job that reads text someone else wrote must not
declare Bash (``jobs.executor``).

Path syntax: ``~/…`` under the home directory and ``//…`` for any other absolute path.
A single leading ``/`` is read relative to the settings' project, and such a rule was
measured to deny nothing.

Public: ``rule_path``, ``credential_rules``, ``control_rules``, ``startup_rules``,
``protected_write_rules``, ``runs_later_rules``, ``deny_rules``, ``CREDENTIAL_ENV_VARS``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

#: environment variables a sandboxed job command must not see (the CLI's own model calls
#: are made by the CLI process itself, outside the sandbox, and keep them)
CREDENTIAL_ENV_VARS = (
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK", "GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY",
)

#: home-relative directories that hold credentials
_CREDENTIAL_DIRS = (".aws", ".ada", ".midway", ".ssh", ".config/gh")
#: home-relative files that hold credentials
_CREDENTIAL_FILES = (".netrc", ".git-credentials")
#: home-relative startup hooks a shell, ssh or git runs by itself
_STARTUP_FILES = (
    ".zshrc", ".zshenv", ".zprofile", ".zlogin", ".bashrc", ".bash_profile", ".profile",
    ".ssh/authorized_keys", ".ssh/config", ".ssh/rc", ".ssh/environment", ".gitconfig",
)
_STARTUP_DIRS = (".config/git", "Library/LaunchAgents")


def rule_path(path: Path) -> str:
    """*path* in Claude Code's permission-rule syntax: ``~/…`` under the home directory,
    ``//…`` for any other absolute path."""
    path = Path(path).expanduser()
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return "/" + path.as_posix()


def _dir(path: Path) -> str:
    return rule_path(path) + "/**"


def _data_dir() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir())


def _workspace() -> Path:
    from config import get_swarmws
    return Path(get_swarmws())


def _config_file() -> Path:
    from core.app_config_manager import AppConfigManager
    return Path(AppConfigManager.instance()._config_path)  # noqa: SLF001


def _secrets_file() -> Path:
    from core.app_config_manager import AppConfigManager
    return Path(AppConfigManager.instance()._secret_path)  # noqa: SLF001


def credential_rules() -> list[str]:
    """Rule paths of the credential stores."""
    home = Path.home()
    data = _data_dir()
    return [
        *(_dir(home / d) for d in _CREDENTIAL_DIRS),
        *(rule_path(home / f) for f in _CREDENTIAL_FILES),
        _dir(data / "aws"),
        rule_path(data / ".env"),
        rule_path(_secrets_file()),
        rule_path(data / "background_credentials.json"),
    ]


def control_rules() -> list[str]:
    """Rule paths of SwarmAI's own control files: its runtime, startup script, config,
    dangerous-command list and the CLI settings it is started with."""
    home = Path.home()
    data = _data_dir()
    ws = _workspace()
    return [
        _dir(data / "runtime"),
        _dir(data / "daemon"),
        rule_path(data / "swarmai_backend.sh"),
        rule_path(data / "dangerous_commands.json"),
        rule_path(_config_file()),
        rule_path(ws / ".claude" / "settings.json"),
        rule_path(ws / ".claude" / "settings.local.json"),
        rule_path(home / ".claude" / "settings.json"),
        rule_path(home / ".claude" / "settings.local.json"),
    ]


def startup_rules() -> list[str]:
    """Rule paths of the startup hooks a shell, ssh, git or launchd runs by itself."""
    home = Path.home()
    return [*(rule_path(home / f) for f in _STARTUP_FILES),
            *(_dir(home / d) for d in _STARTUP_DIRS)]


def protected_write_rules() -> list[str]:
    """Rule paths a session or job nobody (or someone else) watches may not write:
    ``control_rules()`` and ``startup_rules()``."""
    return [*control_rules(), *startup_rules()]


def _app_data_rules() -> list[str]:
    """Rule paths of everything in the app data dir except the workspace: the skill
    folders an owner session loads, run and session worktrees, MCP and notify config,
    the stores. Listed one entry at a time, since a deny on the whole dir would also
    cover the workspace inside it and a deny always wins over an allow."""
    data = _data_dir()
    ws = _workspace()
    try:
        ws_real = ws.resolve()
        children = sorted(data.iterdir())
    except OSError:
        return []
    if data.resolve() not in ws_real.parents:
        return [_dir(data)]  # the workspace lives elsewhere: the whole dir is off-limits
    out = []
    for child in children:
        real = child.resolve()
        if real == ws_real or real in ws_real.parents:
            continue
        out.append(_dir(data / child.name) if child.is_dir() else rule_path(data / child.name))
    return out


def runs_later_rules() -> list[str]:
    """Rule paths of what a later run executes or reads as instructions, which an
    unattended job may not write: job definitions, skills and hooks, capability scripts,
    git hooks and config of every repository in the workspace (the workspace sync runs
    git outside any sandbox), the context files put into every later prompt, the user's
    Claude folder, and everything in the app data dir but the workspace. The Write/Edit
    tools obey only these deny rules (the sandbox's allowWrite binds Bash alone)."""
    ws = _workspace()
    return [
        _dir(ws / "Services" / "swarm-jobs"),
        _dir(ws / ".claude"),
        rule_path(ws / "Projects") + "/*/4-capabilities/**",
        rule_path(ws) + "/**/.git/**",
        _dir(ws / ".context"),
        _dir(Path.home() / ".claude"),
        *_app_data_rules(),
    ]


def deny_rules(*, read: Iterable[str] = (), edit: Iterable[str] = ()) -> list[str]:
    """``Read(...)`` and ``Edit(...)`` deny rules for rule paths, each once, in order."""
    out: list[str] = []
    for kind, paths in (("Read", read), ("Edit", edit)):
        for p in paths:
            rule = f"{kind}({p})"
            if rule not in out:
                out.append(rule)
    return out
