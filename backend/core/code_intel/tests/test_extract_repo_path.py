"""Tests for extract_repo_path() — the shared multi-format TECH.md repo-path parser.

run_19eecc9f: routers/code_intel.py:_run_reindex used an inline single-format regex
(``**Repo Path:**``) that matched ZERO of 8 real projects, so reindex was silently
dead for every project. The fix extracts a PURE helper (patterns + order + first-match,
no filesystem I/O) reused by both _run_reindex and _build_project_path_cache.

These tests pin:
  - AC1/AC2: the helper matches every real TECH.md format
  - AC5: None on no-match; helper does NO filesystem I/O (pure string in → path out)
  - order/first-match: the labeled pattern wins over the bare-backtick fallback
    (content-incidental safety — Gate-1 danger-c: must NOT regress the path cache)
"""

from core.code_intel import extract_repo_path


class TestExtractRepoPath:
    def test_local_bold_marker(self):
        """SwarmAI format: '## Codebase Location' + '- **Local:** `path`'."""
        content = (
            "## Codebase Location\n\n"
            "- **Local:** `/Users/gawan/Desktop/SwarmAI-Workspace/swarmai/`\n"
        )
        assert (
            extract_repo_path(content)
            == "/Users/gawan/Desktop/SwarmAI-Workspace/swarmai/"
        )

    def test_repo_path_bold_marker(self):
        """The legacy '**Repo Path:**' label must still work (back-compat)."""
        content = "- **Repo Path:** `/some/repo`\n"
        assert extract_repo_path(content) == "/some/repo"

    def test_codebase_bold_marker(self):
        """'**Codebase ...:**' label variant."""
        content = "- **Codebase Root:** `/x/y`\n"
        assert extract_repo_path(content) == "/x/y"

    def test_bare_backtick_line_fallback(self):
        """ai_ready_repo format: bare backtick path on a line, no bold label."""
        content = (
            "## Codebase Location\n"
            "<!-- maturity: sparse -->\n\n"
            "`/Users/gawan/Desktop/SwarmAI-Workspace/ai-ready-repo`\n\n"
            "## GitHub\n"
        )
        assert (
            extract_repo_path(content)
            == "/Users/gawan/Desktop/SwarmAI-Workspace/ai-ready-repo"
        )

    def test_no_marker_returns_none(self):
        """AC5: no path marker → None (caller logs skip)."""
        assert extract_repo_path("## Some Heading\n\nplain prose, no path.\n") is None

    def test_empty_string_returns_none(self):
        assert extract_repo_path("") is None

    def test_labeled_pattern_wins_over_bare_fallback(self):
        """Order + first-match: a bold label ANYWHERE must win over an earlier
        bare-backtick line — the labeled pattern (pat[0]) is tried first, so the
        bare fallback (pat[1]) never runs when a label exists. This is the
        content-incidental safety Gate-1 flagged: reordering would regress the cache."""
        content = (
            "see `/wrong/inline/path`\n"
            "- **Local:** `/correct/repo`\n"
        )
        assert extract_repo_path(content) == "/correct/repo"

    def test_pure_no_filesystem_io(self):
        """The helper must be pure: a non-existent path string still returns as-is
        (dir-validation is the CALLER's job, not the helper's)."""
        content = "- **Local:** `/does/not/exist/anywhere/xyz`\n"
        assert extract_repo_path(content) == "/does/not/exist/anywhere/xyz"

    def test_real_swarmai_tech_md(self):
        """AC2 regression: the real SwarmAI TECH.md resolves to the source repo."""
        from jobs.paths import PROJECTS_DIR

        tech = PROJECTS_DIR / "SwarmAI" / "TECH.md"
        if not tech.exists():
            import pytest

            pytest.skip("SwarmAI TECH.md not present in this workspace")
        got = extract_repo_path(tech.read_text(encoding="utf-8"))
        assert got is not None
        assert got.rstrip("/").endswith("swarmai")
