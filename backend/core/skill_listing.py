"""skill_listing — how big the CLI's skill listing is, measured at spawn.

The Claude CLI shows the model one line per skill (name + description) in a
listing sent at the start of a session. The listing has a character budget:
context window x 4 chars/token x ``skillListingBudgetFraction`` (default 0.01),
or ``SLASH_COMMAND_TOOL_CHAR_BUDGET`` when set. Each description is first cut at
``skillListingMaxDescChars``. Past the budget, the CLI keeps descriptions only
for the skills it ranks highest by recent use and lists the rest by name, so a
skill that was never used never gets its description. Nothing in the session
says this happened; this module makes it visible (design
docs/design/2026-10-25-skill-listing-recall-and-cleanup.md §3.1, §3.5).

What is counted: every skill the session can see — the skills projected into the
workspace (``<cwd>/.claude/skills/*/SKILL.md``) and the skills of the user's
enabled Claude Code plugins (``enabledPlugins`` in the CLI's ``settings.json``,
install paths from ``plugins/installed_plugins.json``). NOT counted: the CLI's own
bundled skills (they are not on disk). They DO use budget — the CLI sums every
entry before it decides — so the real listing is larger than this estimate, and the
log warns from ``NEAR_FRACTION`` of the budget, not only past it. The line format
mirrors the CLI's (``- name: description``); width effects (CJK, ``name (alias)``)
are not modelled. Good enough to say "fits", "near" or "over", not to the character.

Public: ``SKILL_DESC_CAP``, ``NEAR_FRACTION``, ``listing_settings``, ``budget_chars``,
``collect_entries``, ``measure``, ``log_listing``, ``schedule_log_listing``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

import yaml

logger = logging.getLogger(__name__)

# Per-skill description cap SwarmAI passes to the CLI (skillListingMaxDescChars).
# Equal to scripts/lint_skills.py DESCRIPTION_MAX, so a product skill is never cut;
# a test keeps the two equal.
SKILL_DESC_CAP = 250

# Warn from this share of the budget: the estimate leaves out the CLI's bundled skills.
NEAR_FRACTION = 0.9

_BYTES_PER_TOKEN = 4          # the CLI's estimate
_DEFAULT_WINDOW = 200_000     # the CLI's default when the window is unknown
_DEFAULT_FRACTION = 0.01      # the CLI's default skillListingBudgetFraction


@dataclass(frozen=True)
class ListingEntry:
    name: str                 # as listed: "s_x", or "plugin:skill"
    description: str          # whitespace-collapsed


def listing_settings() -> dict:
    """The skill-listing keys SwarmAI passes in the CLI's ``--settings``."""
    return {"skillListingMaxDescChars": SKILL_DESC_CAP}


def budget_chars(context_window: Optional[int], fraction: float = _DEFAULT_FRACTION,
                 env: Optional[Mapping[str, str]] = None) -> int:
    """The CLI's listing budget in characters."""
    env = os.environ if env is None else env
    raw = (env.get("SLASH_COMMAND_TOOL_CHAR_BUDGET") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return max(1, int((context_window or _DEFAULT_WINDOW) * _BYTES_PER_TOKEN * fraction))


def _description(skill_md: Path) -> Optional[str]:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end < 0:
        return ""
    try:
        meta = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return ""
    if not isinstance(meta, dict):
        return ""
    desc = " ".join(str(meta.get("description") or "").split())
    when = " ".join(str(meta.get("when_to_use") or "").split())
    return f"{desc} - {when}" if when else desc


def _config_dir(env: Mapping[str, str]) -> Path:
    raw = env.get("CLAUDE_CONFIG_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


def _plugin_skill_dirs(config_dir: Path) -> Iterable[tuple[str, Path]]:
    try:
        enabled = json.loads((config_dir / "settings.json").read_text(encoding="utf-8"))
        installed = json.loads(
            (config_dir / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    enabled = enabled.get("enabledPlugins") if isinstance(enabled, dict) else None
    installed = installed.get("plugins") if isinstance(installed, dict) else None
    if not isinstance(enabled, dict) or not isinstance(installed, dict):
        return
    for key, on in enabled.items():
        if not on:
            continue
        installs = installed.get(key)
        if isinstance(installs, dict):            # older file shape: one install per key
            installs = [installs]
        for install in installs if isinstance(installs, list) else ():
            path = install.get("installPath") if isinstance(install, dict) else None
            if path:
                yield key.split("@", 1)[0], Path(path)
                break


def collect_entries(cwd: Path, env: Optional[Mapping[str, str]] = None) -> list[ListingEntry]:
    """Every skill the CLI will list for a session started in *cwd*."""
    env = os.environ if env is None else env
    entries: list[ListingEntry] = []
    for skill_md in sorted((cwd / ".claude" / "skills").glob("*/SKILL.md")):
        desc = _description(skill_md)
        if desc is not None:
            entries.append(ListingEntry(skill_md.parent.name, desc))
    for plugin, root in _plugin_skill_dirs(_config_dir(env)):
        for skill_md in sorted(root.glob("skills/*/SKILL.md")):
            desc = _description(skill_md)
            if desc is not None:
                entries.append(ListingEntry(f"{plugin}:{skill_md.parent.name}", desc))
    return entries


def measure(entries: list[ListingEntry], *, context_window: Optional[int],
            overrides: Optional[Mapping[str, str]] = None,
            desc_cap: int = SKILL_DESC_CAP,
            env: Optional[Mapping[str, str]] = None) -> dict:
    """Listing size against the budget: ``{skills, chars, budget, over, long}``.

    *overrides* is the session's ``skillOverrides``: ``off`` skills are not listed,
    ``name-only`` ones cost their name only. ``long`` counts descriptions the CLI
    will cut at *desc_cap*."""
    overrides = overrides or {}
    chars = 0
    listed = 0
    long_ = 0
    for e in entries:
        mode = overrides.get(e.name, "on")
        if mode == "off":
            continue
        listed += 1
        if mode == "name-only" or not e.description:
            chars += len(f"- {e.name}") + 1
            continue
        if len(e.description) > desc_cap:
            long_ += 1
        chars += len(f"- {e.name}: {e.description[:desc_cap]}") + 1
    budget = budget_chars(context_window, env=env)
    return {"skills": listed, "chars": chars, "budget": budget,
            "over": chars > budget, "long": long_}


def log_listing(cwd: Path, *, context_window: int,
                overrides: Optional[Mapping[str, str]] = None) -> Optional[dict]:
    """Measure and log one spawn's listing; never raises."""
    try:
        m = measure(collect_entries(cwd), context_window=context_window, overrides=overrides)
    except Exception as exc:  # noqa: BLE001 — a measurement must never stop a spawn
        logger.warning("skill listing not measured: %s: %s", type(exc).__name__, exc)
        return None
    if m["chars"] >= NEAR_FRACTION * m["budget"]:
        logger.warning(
            "skill listing %s budget: %d skills, ~%d chars (CLI-bundled skills not counted) "
            "vs %d; past it the CLI lists the least-used skills by name only "
            "(%d descriptions cut at %d chars)",
            "over" if m["over"] else "near", m["skills"], m["chars"], m["budget"],
            m["long"], SKILL_DESC_CAP)
    else:
        logger.info("skill listing: %d skills, ~%d of %d chars, CLI-bundled skills not "
                    "counted (%d descriptions cut at %d)",
                    m["skills"], m["chars"], m["budget"], m["long"], SKILL_DESC_CAP)
    return m


_PENDING: set = set()


def schedule_log_listing(cwd: Path, *, context_window: Optional[int],
                         overrides: Optional[Mapping[str, str]] = None) -> None:
    """Run ``log_listing`` off the event loop without waiting for it (it is log-only;
    a spawn must not pay the ~0.2 s of SKILL.md reads)."""
    try:
        task = asyncio.get_running_loop().create_task(asyncio.to_thread(
            log_listing, cwd, context_window=context_window, overrides=overrides))
    except RuntimeError:          # no running loop (sync caller): measure inline
        log_listing(cwd, context_window=context_window, overrides=overrides)
        return
    _PENDING.add(task)            # keep a reference until it finishes
    task.add_done_callback(_PENDING.discard)
