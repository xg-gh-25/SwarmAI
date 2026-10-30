"""pipeline_metrics.tokens: a run's token figure is what it was billed for.

Every dashboard total (run list, analytics, weekly trend) and the run card read this
one rule. It used to be input + output, which on a warm-cache run is a hundredth of
the bill (a measured run: 384,675 shown, 27,717,587 billed).
"""
from core import pipeline_metrics as pm

REAL_RUN_TOKEN_COST = {"input_tokens": 296968, "output_tokens": 87707,
                       "cache_read_tokens": 27332912, "billed_tokens": None,
                       "total_tokens": 384675}


def test_the_runs_billed_measurement_wins():
    raw = {"usage": {"input_tokens": 10, "output_tokens": 5, "billed_tokens": 900},
           "audit_summary": {"token_cost": REAL_RUN_TOKEN_COST}}
    assert pm.tokens(raw) == 900


def test_a_run_measured_before_billed_existed_counts_its_cache_reads():
    raw = {"usage": {"input_tokens": 296968, "output_tokens": 87707},
           "audit_summary": {"token_cost": REAL_RUN_TOKEN_COST}}
    assert pm.tokens(raw) == 296968 + 87707 + 27332912


def test_the_audits_own_billed_figure_is_used_when_present():
    cost = dict(REAL_RUN_TOKEN_COST, billed_tokens=123)
    assert pm.tokens({"audit_summary": {"token_cost": cost}}) == 123


def test_input_and_output_alone_are_not_reported_as_the_runs_tokens():
    assert pm.tokens({"usage": {"input_tokens": 1200, "output_tokens": 300}}) is None
    partial = {"input_tokens": 1, "output_tokens": 2}
    assert pm.tokens({"audit_summary": {"token_cost": partial}}) is None


def test_malformed_values_are_not_counted():
    assert pm.tokens({"usage": {"billed_tokens": True}}) is None
    assert pm.tokens({"usage": "x", "audit_summary": {"token_cost": "y"}}) is None
    assert pm.tokens({}) is None
