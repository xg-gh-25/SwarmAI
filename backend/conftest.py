"""Root-level pytest configuration for the backend test suite.

Registers a default Hypothesis profile with a 5-second deadline and
suppressed ``too_slow`` health check.  Without a deadline, Hypothesis's
shrinking phase can run indefinitely when it finds a failing example,
creating zombie pytest processes that consume 35 %+ CPU (Bug 2 from
chat-session-stability-fix spec).
"""

from hypothesis import settings as hypothesis_settings, HealthCheck

# Prevent infinite Hypothesis shrinking loops (Bug 2 from chat-session-stability-fix spec).
# Without a deadline, Hypothesis's shrinking phase can run indefinitely when it
# finds a failing example, creating zombie pytest processes that consume 35%+ CPU.
hypothesis_settings.register_profile(
    "default",
    deadline=5000,  # 5 second deadline per example
    suppress_health_check=[HealthCheck.too_slow],
)
hypothesis_settings.load_profile("default")
