"""The briefings are gone: the Welcome endpoint (docs/design/welcome-screen-v2.md) and the session-start prompt briefing.

What is tested:
- routers.system exposes no /briefing or /briefing/dismiss route: the Welcome
  screen v2 reads /api/attention only, and nothing else called these.
- core.executors has no 'briefing' pool (it only ran the Welcome recompute), so
  no idle thread is reserved for dead work.
Methodology: inspect the router's route table and the pool spec table; no app,
no network, no filesystem.
"""


def test_system_router_has_no_briefing_routes():
    from routers.system import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert not any(p.endswith("/briefing") or p.endswith("/briefing/dismiss") for p in paths), paths


def test_no_briefing_executor_pool():
    from core import executors

    assert "briefing" not in executors._POOL_SPECS


def test_session_briefing_is_gone():
    """The session-start prompt briefing (MEMORY Open Threads + DailyActivity hints)
    is deleted with its scoring and learning-state modules."""
    import importlib.util

    for mod in ("core.proactive_intelligence", "core.proactive_scoring", "core.proactive_learning"):
        assert importlib.util.find_spec(mod) is None, mod


def test_no_briefing_slack_formatter():
    """No job uses notify source 'briefing'; its formatter read the deleted builder."""
    import jobs.executor as ex

    assert not hasattr(ex, "_format_briefing_slack_message")
