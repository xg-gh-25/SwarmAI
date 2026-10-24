"""Welcome screen v2 — the briefing endpoint is gone (docs/design/welcome-screen-v2.md).

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
