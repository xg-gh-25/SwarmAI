"""Root-level pytest configuration for the SwarmAI backend test suite.

This file handles ONLY cross-cutting concerns that must live at the root:
1. Hypothesis profile registration (default=30, ci=100)

All other test infrastructure (markers, watchdogs, timeouts, fixtures,
DB setup) lives in tests/conftest.py — the single authority.

Usage:
    pytest -m "not pbt"        # fast: skip PBT (~1400 unit tests)
    pytest -m "not slow"       # skip stress + heavy PBT
    pytest                     # all ~2000 tests (xdist auto-injects -n 4)
    HYPOTHESIS_PROFILE=ci pytest  # CI: 100 examples per property
"""

import os

from hypothesis import HealthCheck, settings as hypothesis_settings

# ---------------------------------------------------------------------------
# Hypothesis profiles — single source of truth for example counts
# ---------------------------------------------------------------------------
# All PBT test files MUST use PROPERTY_SETTINGS or PROPERTY_SETTINGS_MINIMAL
# from tests/helpers.py — never hardcode max_examples.

hypothesis_settings.register_profile(
    "default",
    max_examples=30,
    deadline=5000,
    suppress_health_check=[HealthCheck.too_slow],
)

hypothesis_settings.register_profile(
    "ci",
    max_examples=100,
    deadline=5000,
    suppress_health_check=[HealthCheck.too_slow],
)

_profile = os.environ.get("HYPOTHESIS_PROFILE", "default")
hypothesis_settings.load_profile(_profile)
