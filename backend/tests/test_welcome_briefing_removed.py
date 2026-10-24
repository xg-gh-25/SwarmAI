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


def test_welcome_briefing_data_builder_is_gone():
    """The structured Welcome briefing (and everything only it used) is deleted;
    the session-start prompt briefing stays."""
    import core.proactive_intelligence as pi

    for name in (
        "build_session_briefing_data", "get_focus_keywords", "_extract_working_items",
        "_extract_jobs_summary", "_tail_read_lines", "_extract_report_field",
        "_extract_report_confidence", "_extract_first_heading", "_detect_content_media_type",
    ):
        assert not hasattr(pi, name), name
    assert callable(pi.build_session_briefing)


def test_no_briefing_slack_formatter():
    """No job uses notify source 'briefing'; its formatter read the deleted builder."""
    import jobs.executor as ex

    assert not hasattr(ex, "_format_briefing_slack_message")
