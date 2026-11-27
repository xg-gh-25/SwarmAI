"""Shared gate for the skill's browser tests: they need SwarmAI's pinned browser runtime (core.feature_map.runtime:
Node, @playwright/test, Chromium headless shell; the tests never install it, render.py does on first use) and
ffmpeg/ffprobe on PATH. Without them the tests skip, unless MV_REQUIRE_BROWSER=1, which turns a missing browser into a
failure."""
import asyncio
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import render  # noqa: E402

_STATE = {}


def browser_unavailable():
    """None when render.py's browser starts here, else why not (cached for the test session)"""
    if "why" not in _STATE:
        async def version(browser):
            return browser.version

        try:
            # never install from a test (a first-use download takes minutes, past any per-test timeout)
            runtime = render.runtime_module()
            try:
                runtime.ensure(install=False)
            except runtime.RuntimeUnavailable as e:
                not_installed = (not os.environ.get("SWARM_FEATURE_MAP_RUNTIME")
                                 and not (runtime.runtime_dir() / "ready.json").is_file())
                hint = "; install it once with: cd backend && python -m core.feature_map.runtime" if not_installed else ""
                raise render.UsageError(f"the pinned browser runtime is not available here: {render.one_line(e.reason)}"
                                        + hint) from None
            asyncio.run(render.with_browser(version))
            _STATE["why"] = None
        except render.UsageError as e:
            _STATE["why"] = str(e)
    return _STATE["why"]


def require_browser(ffmpeg=False):
    """skip (or with MV_REQUIRE_BROWSER=1 fail) when the pinned browser, and with ffmpeg=True ffmpeg, is missing"""
    why = browser_unavailable()
    if why is None and ffmpeg and not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        why = "ffmpeg/ffprobe not on PATH"
    if why:
        if os.environ.get("MV_REQUIRE_BROWSER") == "1":
            pytest.fail(f"MV_REQUIRE_BROWSER=1 but the browser is unavailable: {why}")
        pytest.skip(why)


@pytest.fixture
def pinned_browser():
    require_browser()


@pytest.fixture
def pinned_browser_ffmpeg():
    require_browser(ffmpeg=True)


@pytest.fixture
def browser_gate():
    """this module, for the tests of the gate itself (its browser_unavailable and the cached _STATE)"""
    return sys.modules[__name__]
