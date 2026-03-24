"""Root-level pytest configuration for the backend test suite.

Two Hypothesis profiles control the speed/thoroughness trade-off:

- **default** (local dev): 30 examples per property, 5s deadline.
  Cuts ~70% of PBT execution time vs the implicit 100-example default.
- **ci**: 100 examples per property, 5s deadline.
  Use ``--hypothesis-profile=ci`` in CI for full coverage.

Without a deadline, Hypothesis's shrinking phase can run indefinitely
when it finds a failing example, creating zombie pytest processes that
consume 35%+ CPU (Bug 2 from chat-session-stability-fix spec).
"""

import os
from hypothesis import settings as hypothesis_settings, HealthCheck

# --- Default profile: fast local dev (30 examples) -----------------------
hypothesis_settings.register_profile(
    "default",
    max_examples=30,   # down from implicit 100 — ~70% faster PBT locally
    deadline=5000,     # 5 second deadline per example
    suppress_health_check=[HealthCheck.too_slow],
)

# --- CI profile: full coverage (100 examples) ----------------------------
hypothesis_settings.register_profile(
    "ci",
    max_examples=100,
    deadline=5000,
    suppress_health_check=[HealthCheck.too_slow],
)

# Auto-select: HYPOTHESIS_PROFILE env var > "default"
_profile = os.environ.get("HYPOTHESIS_PROFILE", "default")
hypothesis_settings.load_profile(_profile)
