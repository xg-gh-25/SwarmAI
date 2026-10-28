"""Recall fallback for MEMORY units omitted by CDF prompt selection."""
from datetime import datetime, timezone

from core.ingestion.store import connect_rw
from core.memory_injection import select_context_file
from core.recall_multi import _memory_outside_prompt


def test_context_files_recall_reads_recorded_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "app-data"))
    ctx = tmp_path / "SwarmWS/.context"
    ctx.mkdir(parents=True)
    memory = ctx / "MEMORY.md"
    memory.write_text(
        "# Memory\n\n## Principles\n"
        "- [principle] **resident** " + "R" * 80 + "\n"
        "  <!-- ref:0 | last:2026-10-20 | decay:active | source:manual -->\n"
        "\n## Decisions\n"
        "- [decision] **omitted target** needle-for-recall " + "X" * 220 + "\n"
        "  <!-- ref:0 | last:2026-10-20 | decay:active | source:manual -->\n",
        encoding="utf-8",
    )
    conn = connect_rw()
    selected = select_context_file(
        memory, budget=140, estimate=len,
        now=datetime(2026, 10, 28, tzinfo=timezone.utc), conn=conn,
    )
    conn.close()

    outside = _memory_outside_prompt(memory.read_text(encoding="utf-8"),
                                     memory.read_text(encoding="utf-8"), memory)
    assert "omitted target" in outside
    assert "resident" not in outside
