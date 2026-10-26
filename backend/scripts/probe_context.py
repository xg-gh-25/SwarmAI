#!/usr/bin/env python3
"""probe_context — product eval probes for context assembly / the system prompt.

Contract: scripts/_probe_kit.py. Subcommands:
  core      every prompt carries the constitution (SwarmAI/Identity/Soul/Agent
            Directives), even when an extra section (the briefing) raises
  order     the context files are assembled in priority order and an oversized
            file is included whole (no read-side truncation)
  channel   a group / non-owner prompt carries none of the owner's USER, MEMORY,
            EVOLUTION, TOOLS, and only the STEERING text wrapped in channel markers
  cache     the shared prompt cache never serves the owner's files to a non-owner,
            and an edited MEMORY.md reaches the next build
  dynamic   per-turn content (the open file) stays out of the cacheable base prompt
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest import mock

from scripts._probe_kit import arun, run, tempdir, verdict

PRIVATE = {"USER.md": "SENTINEL_USER_7f3", "MEMORY.md": "SENTINEL_MEMORY_7f3",
           "EVOLUTION.md": "SENTINEL_EVOLUTION_7f3", "TOOLS.md": "SENTINEL_TOOLS_7f3"}
STEERING_SHARED = "SHARED_STEERING_7f3"
STEERING_SECRET = "SECRET_STEERING_7f3"


def _builder():
    from core.prompt_builder import PromptBuilder

    cfg = mock.MagicMock()
    cfg.get = mock.MagicMock(side_effect=lambda key, default=None: {
        "default_model": "claude-sonnet-4-6", "use_bedrock": False}.get(key, default))
    return PromptBuilder(config=cfg)


def _full(agent_config: dict) -> str:
    return ((agent_config.get("_system_prompt_metadata") or {}).get("full_text")
            or agent_config.get("system_prompt") or "")


def _loader(ctx: Path):
    from core.context_directory_loader import ContextDirectoryLoader
    from utils.backend_bin import bundled_path

    # The shipped templates, as a fresh install seeds them.
    loader = ContextDirectoryLoader(ctx, templates_dir=bundled_path("context"))
    loader.ensure_directory()
    return loader


# ── core ──────────────────────────────────────────────────────────────────────

def _core_holds(ws: Path) -> tuple[bool, str]:
    from core.prompt_builder import assert_core_sections

    cfg: dict = {}
    with mock.patch("core.proactive_intelligence.build_session_briefing",
                    side_effect=RuntimeError("briefing broke")):
        arun(_builder().build_system_prompt(agent_config=cfg, working_directory=str(ws)))
    missing = assert_core_sections(_full(cfg))
    return (not missing), f"core sections missing: {missing}"


def core(negative: bool) -> int:
    with tempdir() as ws:
        def broken():
            from core.context_directory_loader import ContextDirectoryLoader
            with mock.patch.object(ContextDirectoryLoader, "load_all", return_value=""):
                return _core_holds(ws)
        return verdict("CTX_CORE", negative, lambda: _core_holds(ws), broken)


# ── order ─────────────────────────────────────────────────────────────────────

def _order_holds(text: str, tail: str) -> tuple[bool, str]:
    from core.context_directory_loader import CONTEXT_FILES

    positions = []
    for spec in sorted(CONTEXT_FILES, key=lambda s: s.priority):
        i = text.find(f"## {spec.section_name}\n")
        if i >= 0:
            positions.append((i, spec.section_name))
    if [p for p, _ in positions] != sorted(p for p, _ in positions):
        return False, f"sections out of priority order: {[n for _, n in sorted(positions)]}"
    if len(positions) < 4:
        return False, f"only {len(positions)} sections assembled"
    if tail not in text or "[Truncated" in text:
        return False, "an oversized file was truncated"
    return True, ""


def order(negative: bool) -> int:
    tail = "TAIL_OF_A_BIG_MEMORY_7f3"
    with tempdir() as d:
        ctx = d / ".context"
        loader = _loader(ctx)
        (ctx / "MEMORY.md").write_text(
            "# Memory\n\n" + ("- [guideline] **filler** padding line\n" * 6000) + tail + "\n",
            encoding="utf-8")

        def positive():
            return _order_holds(loader.load_all(model_context_window=200_000), tail)

        def broken():
            # A loader that cut the lowest-priority sections to fit a budget.
            orig = type(loader)._enforce_token_budget

            def cut(self, sections, budget=None):
                return orig(self, sections, budget)[:-3]
            with mock.patch.object(type(loader), "_enforce_token_budget", cut):
                (ctx / "L1_SYSTEM_PROMPTS.md").unlink(missing_ok=True)
                return _order_holds(loader.load_all(model_context_window=200_000), tail)
        return verdict("CTX_ORDER", negative, positive, broken)


# ── channel ───────────────────────────────────────────────────────────────────

def _seed_private(ctx: Path) -> None:
    for name, sentinel in PRIVATE.items():
        (ctx / name).write_text(f"# {name}\n\n{sentinel} owner-only line.\n", encoding="utf-8")
    (ctx / "STEERING.md").write_text(
        "# Steering\n\n<!-- channel -->\n" + STEERING_SHARED + " bot rule.\n<!-- /channel -->\n\n"
        + STEERING_SECRET + " owner-only rule.\n", encoding="utf-8")


def _channel_holds(loader, exclude) -> tuple[bool, str]:
    for window in (200_000, 32_000):
        text = loader.load_all(model_context_window=window, exclude_filenames=exclude)
        leaked = [s for s in list(PRIVATE.values()) + [STEERING_SECRET] if s in text]
        if leaked:
            return False, f"window {window}: owner text reached the channel prompt: {leaked}"
        if STEERING_SHARED not in text:
            return False, f"window {window}: the marked STEERING text is missing"
    return True, ""


def channel(negative: bool) -> int:
    from core.context_directory_loader import GROUP_CHANNEL_EXCLUDE

    with tempdir() as d:
        ctx = d / ".context"
        loader = _loader(ctx)
        _seed_private(ctx)
        return verdict("CTX_CHANNEL_PRIVACY", negative,
                       lambda: _channel_holds(loader, set(GROUP_CHANNEL_EXCLUDE)),
                       lambda: _channel_holds(loader, None))


# ── cache ─────────────────────────────────────────────────────────────────────

def _cache_holds(loader, ctx: Path, *, stale: bool) -> tuple[bool, str]:
    from core.context_directory_loader import GROUP_CHANNEL_EXCLUDE

    owner = loader.load_all(model_context_window=200_000)  # writes the shared cache
    if PRIVATE["MEMORY.md"] not in owner:
        return False, "owner prompt is missing MEMORY"
    guest = loader.load_all(model_context_window=200_000, exclude_filenames=set(GROUP_CHANNEL_EXCLUDE))
    if PRIVATE["MEMORY.md"] in guest:
        return False, "the cache served the owner's MEMORY to a non-owner"
    time.sleep(0.05)
    (ctx / "MEMORY.md").write_text("# Memory\n\nEDITED_MEMORY_7f3\n", encoding="utf-8")
    if stale:  # a cache that looks newer than the edit: what an mtime bug would do
        future = time.time() + 3600
        os.utime(ctx / "L1_SYSTEM_PROMPTS.md", (future, future))
    again = loader.load_all(model_context_window=200_000)
    if "EDITED_MEMORY_7f3" not in again:
        return False, "an edited MEMORY.md did not reach the next build (stale cache)"
    return True, ""


def cache(negative: bool) -> int:
    def scenario(stale: bool):
        with tempdir() as d:
            ctx = d / ".context"
            loader = _loader(ctx)
            _seed_private(ctx)
            return _cache_holds(loader, ctx, stale=stale)
    return verdict("CTX_CACHE", negative, lambda: scenario(False), lambda: scenario(True))


# ── dynamic ───────────────────────────────────────────────────────────────────

_EDITOR = {"file_path": "/x/probe_open_file.py", "file_name": "probe_open_file.py"}


def _dynamic_holds(ws: Path, base_builder) -> tuple[bool, str]:
    base = arun(base_builder(ws))
    cfg: dict = {}
    arun(_builder().build_system_prompt(agent_config=cfg, working_directory=str(ws),
                                        editor_context=_EDITOR))
    full = _full(cfg)
    if "probe_open_file.py" in base:
        return False, "the open file reached the cacheable base prompt"
    if "probe_open_file.py" not in full:
        return False, "the open file is missing from the full per-turn prompt"
    return True, ""


def dynamic(negative: bool) -> int:
    with tempdir() as ws:
        def default_base(w):
            return _builder().build_default_system_prompt(
                agent_config={}, working_directory=str(w), editor_context=_EDITOR)

        async def full_as_base(w):  # broken: the per-turn build used as the base
            cfg: dict = {}
            await _builder().build_system_prompt(agent_config=cfg, working_directory=str(w),
                                                 editor_context=_EDITOR)
            return _full(cfg)
        return verdict("CTX_DYNAMIC_LAYER", negative,
                       lambda: _dynamic_holds(ws, default_base),
                       lambda: _dynamic_holds(ws, full_as_base))


if __name__ == "__main__":
    raise SystemExit(run({"core": core, "order": order, "channel": channel,
                          "cache": cache, "dynamic": dynamic}))
