"""Tests for distillation hook competence extraction.

Verifies that _COMPETENCE_PATTERNS matches the actual writing style of
DailyActivity lessons (third-person declarative, not first-person learning)
and that _extract_competence correctly filters noise.
"""

import pytest
from hooks.distillation_hook import DistillationTriggerHook, _COMPETENCE_PATTERNS


# -- Pattern matching tests --------------------------------------------------

SHOULD_MATCH = [
    ("blocks pattern", "Claude Code sandbox blocks uv/pip PyPI access"),
    ("must run", "full builds must run outside sandbox"),
    ("do NOT source", "macOS GUI apps do NOT source .zshrc"),
    ("Use X to Y", "Use login shell spawn to discover real PATH"),
    ("need both", "AIM MCP wrappers need both wrapper script AND aim CLI"),
    ("Use X when Y", "Use marker strings when spawning shell to extract env vars"),
    ("points to", "sys.executable in PyInstaller bundles points to bundled binary"),
    ("rejects", "Tauri shell.open() rejects local file:// paths"),
    ("content must be preserved", "React editor content must be preserved in ref across unmount cycles"),
    ("always X during", "ALWAYS write DailyActivity during every session"),
    ("always X before", "always scan codebase before assessing progress"),
    ("works by", "The distillation pipeline works by scanning DailyActivity files"),
    ("root cause was", "root cause was a stale cache entry"),
    ("confirmed working", "verified working in production after rebuild"),
    ("uses X instead of", "uses Path.parts instead of str comparison"),
    ("must use", "must use direct import not subprocess"),
    ("cannot find", "cannot find the module when bundled"),
    ("doesn't persist", "state doesn't persist across tab switches"),
    ("hierarchy pattern", "Error UX hierarchy: elapsed timer > toast > modal"),
    ("don't X without", "don't double-notify without checking existing indicators"),
    ("never X before", "never skip hooks before verifying the fix"),
]

SHOULD_NOT_MATCH = [
    ("generic statement", "Fixed the build script"),
    ("user action", "User asked about tab switching"),
    ("git action", "Committed 3 files to git"),
    ("memory update", "Updated MEMORY.md with new entries"),
    ("time observation", "Session took longer than expected"),
    ("meta observation", "Empty EVOLUTION.md Corrections section was the biggest irony"),
    ("comparison", "A bug reported 3x looks the same as a nice-to-have reported 1x"),
    ("process guideline", "Process correction: design-first for architectural features"),
]


@pytest.mark.parametrize("label,text", SHOULD_MATCH, ids=[s[0] for s in SHOULD_MATCH])
def test_competence_pattern_matches(label, text):
    assert _COMPETENCE_PATTERNS.search(text), f"Expected match for: {text}"


@pytest.mark.parametrize("label,text", SHOULD_NOT_MATCH, ids=[s[0] for s in SHOULD_NOT_MATCH])
def test_competence_pattern_rejects_noise(label, text):
    assert not _COMPETENCE_PATTERNS.search(text), f"Unexpected match for: {text}"


# -- Extraction integration tests -------------------------------------------

@pytest.fixture
def hook():
    return DistillationTriggerHook()


def test_extract_competence_from_lessons_section(hook):
    """Competence items extracted from **Lessons:** section."""
    body = """## 18:40 | abc123 | Session title

**Lessons:**
- macOS GUI apps do NOT source .zshrc — PATH is minimal.
- sys.executable in PyInstaller bundles points to bundled binary, NOT Python.
- Error UX hierarchy: elapsed timer > toast > modal.

**Next:** Continue work.
"""
    result = hook._extract_competence(body)
    assert len(result) == 3
    assert any("do NOT source" in r for r in result)
    assert any("points to" in r for r in result)
    assert any("timer > toast > modal" in r for r in result)


def test_extract_competence_from_any_line(hook):
    """Competence items extracted from lines outside Lessons section."""
    body = """## 03:00 | abc123 | Session

**Delivered:**
- React editor content must be preserved in ref across unmount cycles.
- Updated the config file.

**What happened:**
- User asked about tab switching.
"""
    result = hook._extract_competence(body)
    assert len(result) == 1
    assert "content must be preserved" in result[0]


def test_extract_competence_excludes_pure_negative(hook):
    """Pure negative lessons (mistake was, should have) are excluded."""
    body = """**Lessons:**
- The mistake was using str().startswith() for path comparison.
- should have used Path.parts from the beginning.
- next time avoid bundling large binaries.
- sys.executable points to bundled binary in PyInstaller.
"""
    result = hook._extract_competence(body)
    # Only the last one should match (points to) — first 3 are pure negative
    assert len(result) == 1
    assert "points to" in result[0]


def test_extract_competence_caps_at_five(hook):
    """Competence extraction capped at 5 entries."""
    lines = "\n".join(
        f"- Item {i} must run outside sandbox for testing"
        for i in range(10)
    )
    body = f"**Lessons:**\n{lines}\n"
    result = hook._extract_competence(body)
    assert len(result) == 5


def test_extract_competence_skips_short_entries(hook):
    """Entries <= 15 chars are skipped."""
    body = """**Lessons:**
- must run it
- sys.executable in PyInstaller bundles points to bundled binary, NOT Python.
"""
    result = hook._extract_competence(body)
    assert len(result) == 1
