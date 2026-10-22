"""Tests for scripts/clean_daily_activity.py -- one-shot DailyActivity cleanup.

Properties: one entry per session; no distinct bullet lost; Git activity
dropped; Files keeps only file-name tokens; test-ses dropped; marker blocks stay
well-formed; non-session sections untouched; idempotent.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from clean_daily_activity import clean_text  # noqa: E402

DAY = """---
date: "2026-10-17"
---

## Session Resume
- keep me

## 01:00 | aaaa1111 | 📸 Mid-session checkpoint
**Files:** `a.py`, `null`, `activate`, `settings.json')))['env']`
**Git activity:**
- `1111111 someone else's commit`

## 01:10 | aaaa1111 | Fix the parser

**What happened:**
- first ask

**Decisions:**
- decision one

## 01:20 | aaaa1111 | Fix the parser

**What happened:**
- first ask
- second ask

**Lessons:**
- lesson one

**Git activity:**
- `2222222 unrelated`

## 01:30 | test-ses | 📸 Mid-session checkpoint
**Files:** `t.py`

<!-- da:checkpoint:bbbb2222-0000 -->
## 02:00 | bbbb2222 | 📸 Mid-session checkpoint
**Files:** `b.ts`
<!-- /da -->

<!-- da:summary:bbbb2222-0000 -->
## 02:05 | bbbb2222 | Ship it

**What happened:**
- ship
<!-- /da -->
"""


def test_cleanup():
    out = clean_text(DAY)
    assert out.count("| aaaa1111 |") == 1 and out.count("| bbbb2222 |") == 1
    assert "## 01:20 | aaaa1111 | Fix the parser" in out
    for kept in ("- first ask", "- second ask", "- decision one", "- lesson one",
                 "`a.py`", "`b.ts`", "- keep me", "- ship"):
        assert kept in out, kept
    for gone in ("Git activity", "someone else", "`null`", "activate", "['env']",
                 "test-ses", "Mid-session checkpoint", "da:checkpoint"):
        assert gone not in out, gone
    assert out.count("- first ask") == 1
    assert out.count("<!-- da:summary:bbbb2222-0000 -->") == 1
    assert out.count("<!-- /da -->") == 1
    assert out.startswith('---\ndate: "2026-10-17"\n---\n')
    assert clean_text(out) == out
