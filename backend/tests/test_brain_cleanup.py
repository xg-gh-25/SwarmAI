"""The one-time brain cleanup (design 2026-10-30-brain-driven §2.6, I8): after it, the main
files hold no age mark, no maturity line, no tombstone, no ticked item; it keeps every
other byte, backs up first, and a second run changes nothing."""
from __future__ import annotations

import tarfile
from pathlib import Path

from tests.test_cdf_v2_e2e import cdf_env  # noqa: F401  (fixture)

IMPROVEMENT = """# Improvement

## What Worked

<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | promoted: none -->

- [guideline] **Old but kept** Still true. (2026-01-01)
  <!-- ref:0 | last:none | decay:dormant | source:manual -->

- [redacted] **Withdrawn** [已撤回 2026-10-30，原因：wrong]

- [guideline] **Plain** Untouched entry. (2026-10-01)
  <!-- ref:0 | last:none | decay:active | source:manual -->

## What to Watch For

- [x] **Shipped thing** Done now.
- [ ] **Open thing** Still open.
"""


def test_cleanup_leaves_only_valid_content_and_is_idempotent(cdf_env):
    from core.ingestion.api import connect_rw
    from scripts.brain_cleanup import counts, run
    connect_rw().close()
    ws = cdf_env["workspace"]
    doc = cdf_env["ddd"] / "IMPROVEMENT.md"
    doc.write_text(IMPROVEMENT, encoding="utf-8")
    dry = run(ws, [], apply=False)
    assert dry["dry_run"] and dry["before"] == {"dormant": 1, "maturity": 1, "tombstone": 1, "ticked": 1}
    assert doc.read_text(encoding="utf-8") == IMPROVEMENT          # a dry run writes nothing
    first = run(ws, [], apply=True)
    text = doc.read_text(encoding="utf-8")
    assert first["after"] == {"dormant": 0, "maturity": 0, "tombstone": 0, "ticked": 0}
    assert counts(text) == first["after"]
    assert "**Old but kept** Still true." in text and "decay:active | source:manual" in text
    assert "**Plain** Untouched entry." in text and "- [ ] **Open thing**" in text
    assert "Shipped thing" not in text and "[redacted]" not in text and "\n\n\n" not in text
    with tarfile.open(first["backup"], "r:gz") as tar:          # the backup holds the original
        member = next(m for m in tar.getmembers() if m.name.endswith("IMPROVEMENT.md"))
        assert tar.extractfile(member).read().decode("utf-8") == IMPROVEMENT
    second = run(ws, [], apply=True)
    assert second["edited_files"] == 0 and second["ticked_withdrawn"] == 0
    assert doc.read_text(encoding="utf-8") == text
