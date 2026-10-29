"""A Brain installed onto a Hive is journaled as a sync, not left as an unknown edit
(2026-10-29-cdf-launch §4): the baseline holds the installed bytes, the change is
recorded with the Brain sync as its writer, and nothing is left "out of band"."""
from __future__ import annotations

import json
from pathlib import Path

from core import brain_sync as bs
from tests.test_cdf167_safety import _connect, safety_corpus  # noqa: F401


def _package(root: Path, body: str, product: bool = True) -> dict[str, bytes]:
    b = root / "Projects" / "Lib"
    (b / "2-understanding").mkdir(parents=True, exist_ok=True)
    if product:
        (b / "2-understanding" / "PRODUCT.md").write_text("# PRODUCT\n\n## Users\n\n- [decision] **U** Users.\n")
    (b / "2-understanding" / "TECH.md").write_text(body)
    (b / "AGENTS.md").write_text("# Lib\n")
    return bs.collect(b, False)


def test_an_install_is_recorded_as_a_sync(safety_corpus, tmp_path):
    from core.ingestion.writer import detect_out_of_band, ensure_baseline
    ws = safety_corpus["workspace"]
    first = _package(tmp_path / "host1", "# TECH\n\n## Conventions\n\n- [guideline] **A** One.\n")
    bs.install(ws, "Lib", first, bs.content_digest(first))
    ensure_baseline()
    second = _package(tmp_path / "host2", "# TECH\n\n## Conventions\n\n- [guideline] **B** Two.\n",
                      product=False)                                      # the package dropped PRODUCT.md
    bs.install(ws, "Lib", second, bs.content_digest(second))
    tech = ws / "Projects/Lib/2-understanding/TECH.md"
    assert "**B** Two." in tech.read_text()
    from core.ingestion.governed_edit import revision_of
    baseline = ensure_baseline()
    assert baseline["files"]["Projects/Lib/2-understanding/TECH.md"]["file_hash"] == revision_of(tech)
    assert "Projects/Lib/2-understanding/PRODUCT.md" not in baseline["files"]
    conn = _connect(safety_corpus["database"])
    writers = [json.loads(r[0])["writer"] for r in conn.execute(
        "SELECT detail FROM ingestion_events WHERE kind='sync_applied'")]
    assert writers and all(w == "sync:brain-sync" for w in writers)
    assert detect_out_of_band(conn) == []
    conn.close()


def test_an_install_outside_the_live_workspace_records_nothing(safety_corpus, tmp_path):
    files = _package(tmp_path / "host", "# TECH\n")
    bs.install(tmp_path / "elsewhere", "Lib", files, bs.content_digest(files))
    conn = _connect(safety_corpus["database"])
    assert conn.execute("SELECT COUNT(*) FROM ingestion_writes").fetchone()[0] == 0
    conn.close()
