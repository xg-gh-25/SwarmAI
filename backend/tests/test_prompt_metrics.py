"""core/prompt_metrics (design 2026-10-30-cdf-agent-decides §4: value first, measure only).

Questions: does an assembled prompt record the tokens of the every-turn files (and
only those); does a TTFT record carry its parts; does the daily reader give each
file's last and largest size and the TTFT median / p90 with the model part (minus
pre-send and a cold spawn, retried turns left out); is everything written under the
test's own data dir.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core import prompt_metrics as pm


def test_only_the_every_turn_files_are_recorded(tmp_path):
    pm.record_sections([("Memory", 900), ("SwarmAI", 5000), ("Tools", 40), ("Evolution Registry", 300)])
    pm.flush()
    (day,) = pm.daily(1)
    assert day["files"] == {"Memory": {"last": 900, "max": 900}, "Tools": {"last": 40, "max": 40},
                            "Evolution Registry": {"last": 300, "max": 300}}
    assert day["turns"] == 0


def test_the_daily_reader_gives_last_and_largest_size_and_ttft_percentiles(tmp_path):
    pm.record_sections([("Memory", 900)])
    pm.record_sections([("Memory", 1200)])
    pm.record_sections([("Memory", 1000)])
    for ms, pre, spawn, retries in [(10_000, 500, None, 0), (20_000, 1000, 5000, 0),
                                    (12_000, 400, None, 0), (90_000, 600, None, 2)]:
        pm.record_ttft(ms, pre, spawn, retries)
    pm.flush()
    (day,) = pm.daily(1)
    assert day["files"]["Memory"] == {"last": 1000, "max": 1200}
    assert day["turns"] == 4
    assert day["ttft_median_ms"] == 16000 and day["ttft_p90_ms"] == 90000
    # model part: 9500, 14000, 11600 — the retried turn is left out
    assert day["model_median_ms"] == 11600 and day["model_p90_ms"] == 14000


def test_records_go_under_the_tests_data_dir_and_by_month(tmp_path):
    from config import get_app_data_dir
    pm.record_ttft(5, 1)
    pm.flush()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    assert (get_app_data_dir() / "metrics" / f"prompt-metrics-{month}.jsonl").exists()


def test_context_assembly_records_the_every_turn_sections(tmp_path):
    from core.context_directory_loader import ContextDirectoryLoader
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "TOOLS.md").write_text("# Tools\n\nuse the knowledge cli for recall\n", encoding="utf-8")
    loader = ContextDirectoryLoader(context_dir=ctx, templates_dir=tmp_path / "none")
    assert "knowledge cli" in loader._assemble_from_sources(200_000)
    pm.flush()
    (day,) = pm.daily(1)
    assert day["files"]["Tools"]["last"] > 0
