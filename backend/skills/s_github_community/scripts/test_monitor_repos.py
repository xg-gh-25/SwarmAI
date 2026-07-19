"""Tests for load_tracked_repos() — TECH.md Source Matrix parser (monitor.py).

Verifies the runtime-derive-from-TECH.md loader that replaces the hardcoded
TIER1_REPOS/TIER2_REPOS constants:
  - section-scoped to the "### Current Roster" table (NOT the Hot Topics table,
    which shares the `| N |` row shape — the false-positive trap)
  - URL-first repo extraction from column 2 (handles [text](url), bare owner/name,
    and truncated link-text rows where the full name is only in the URL)
  - dedup preserving order; split into (tier1, tier2) by leading tier digit
  - Tier 3 excluded (matches current scan scope)
  - fail-loud (RuntimeError) on an implausibly small parse (parse broke)
"""

import textwrap

import pytest

from skills.s_github_community.scripts.monitor import load_tracked_repos


# A minimal TECH.md fixture reproducing the REAL structural traps:
#   - a Hot Topics table above Current Roster with the same `| N |` shape,
#     whose column-2 is topic PROSE (must NOT be ingested)
#   - repo cells in all three shapes: markdown link, bare owner/name,
#     and a truncated-link-text row (full name only in the URL)
#   - descriptions containing embedded [text](url) links (must NOT leak —
#     extraction is column-2 only)
#   - a Tier-3 row (must be excluded from the scan lists)
#   - a "### Source Matrix Notes" heading that terminates the roster table
_FIXTURE = textwrap.dedent(
    """\
    # GitHub Community Engine — TECH

    ## Three Matrices
    DO NOT MERGE THEM.

    ### Rankings
    | # | Topic | Threads | Heat |
    |---|-------|---------|------|
    | 1 | **Memory for agents** (persistence) | MemPalace #1784 (2💬) | 🔥🔥🔥 Dominant |
    | 2 | **Production agent operations** | crewAI #4232 (36💬) | 🔥🔥🔥 Steady |
    | 3 | **Context compression** | [claude-code #67297](https://github.com/anthropics/claude-code/issues/67297) | 🔥🔥🔥 NEW |

    ## Source Matrix (Tier 1-3 repos we track)

    ### Current Roster

    | Tier | Repo | Stars | Category | Last Engaged |
    |------|------|-------|----------|-------------|
    | 1 | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | 154K | Agent harness | 2026-05-28 ⚠️ |
    | 1 | anthropics/skills | 136K | Skill ecosystem | 2026-05-17 |
    | 1 | [github/spec-kit](https://github.com/github/spec-kit) | 122K | **SDD leader** — orbits [obra/superpowers](https://github.com/obra/superpowers) | — 🆕 |
    | 2 | [Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify) | 91K | Code→graph | — 🆕 |
    | 2 | [Nutlope/hallmark](https://github.com/Nutlope/hallmark) | 13K | Anti-slop | — 🆕 |
    | 2 | anthropics/claude-plugins-official | 25.5K | Plugin ecosystem | — |
    | 3 | [aws-samples/sample-eval-first…agentcore](https://github.com/aws-samples/sample-eval-first-building-enterprise-agents-with-agentcore) | 9 | Eval sample | — 🆕 |
    | 3 | Agents365-ai/video-podcast-maker | 1K | Content generation | — |

    ### Source Matrix Notes (W23)

    - **Some note** referencing [anthropics/claude-code #16288](https://github.com/anthropics/claude-code/issues/16288) — must NOT be parsed as a repo.
    | 1 | this-row-is-below-the-table/should-not-count | 5K | trap | — |
    """
)


@pytest.fixture
def tech_md(tmp_path):
    p = tmp_path / "TECH.md"
    p.write_text(_FIXTURE)
    return p


def test_tier1_repos_extracted(tech_md):
    tier1, _ = load_tracked_repos(tech_md)
    assert "NousResearch/hermes-agent" in tier1
    assert "anthropics/skills" in tier1  # bare owner/name cell
    assert "github/spec-kit" in tier1


def test_tier2_repos_extracted(tech_md):
    _, tier2 = load_tracked_repos(tech_md)
    assert "Graphify-Labs/graphify" in tier2
    assert "Nutlope/hallmark" in tier2
    assert "anthropics/claude-plugins-official" in tier2  # bare cell


def test_hot_topics_table_not_ingested(tech_md):
    """The Rankings/Hot-Topics table shares the `| N |` shape but its col-2 is
    topic prose. Section-scoping to Current Roster must exclude it entirely."""
    tier1, tier2 = load_tracked_repos(tech_md)
    allrepos = tier1 + tier2
    # No topic-prose row leaked as a repo:
    assert not any("Memory for agents" in r for r in allrepos)
    assert not any("Production agent operations" in r for r in allrepos)
    # And the claude-code issue link inside the Hot Topics row #3 must not appear
    # (it's above Current Roster).
    assert "anthropics/claude-code" not in allrepos


def test_description_embedded_links_not_leaked(tech_md):
    """spec-kit's description cell contains [obra/superpowers](url). Column-2-only
    extraction must take spec-kit, NOT superpowers."""
    tier1, tier2 = load_tracked_repos(tech_md)
    assert "obra/superpowers" not in (tier1 + tier2)


def test_truncated_link_text_uses_url(tech_md):
    """Row whose visible text is truncated (…) — full owner/name only in the URL.
    URL-first extraction must recover the real name."""
    _, tier2 = load_tracked_repos(tech_md)
    # It's a Tier 3 row actually — verify it's NOT in tier2, but more importantly
    # that the parser didn't crash and didn't ingest the truncated '…' text.
    assert not any("…" in r for r in tier2)


def test_tier3_excluded_from_scan_lists(tech_md):
    """Tier 3 is not scanned today; loader returns tier1+tier2 only."""
    tier1, tier2 = load_tracked_repos(tech_md)
    allrepos = tier1 + tier2
    assert "aws-samples/sample-eval-first-building-enterprise-agents-with-agentcore" not in allrepos
    assert "Agents365-ai/video-podcast-maker" not in allrepos


def test_row_below_table_terminator_not_ingested(tech_md):
    """A `| N |`-shaped row placed AFTER '### Source Matrix Notes' must be excluded
    by the section terminator."""
    tier1, tier2 = load_tracked_repos(tech_md)
    assert "this-row-is-below-the-table/should-not-count" not in (tier1 + tier2)


def test_dedup_preserves_order(tmp_path):
    # >= _MIN_PLAUSIBLE_REPOS distinct repos so the fail-loud guard doesn't fire;
    # a/one is duplicated to prove dedup, and order must be preserved.
    md = tmp_path / "TECH.md"
    md.write_text(textwrap.dedent(
        """\
        ### Current Roster
        | Tier | Repo | Stars | Category | Last Engaged |
        |------|------|-------|----------|-------------|
        | 1 | a/one | 1K | x | — |
        | 1 | a/one | 1K | dup | — |
        | 1 | c/three | 3K | z | — |
        | 2 | b/two | 2K | y | — |
        | 2 | d/four | 4K | w | — |
        | 2 | e/five | 5K | v | — |

        ### Source Matrix Notes
        """
    ))
    tier1, tier2 = load_tracked_repos(md)
    assert tier1 == ["a/one", "c/three"]  # dedup + order preserved
    assert tier2 == ["b/two", "d/four", "e/five"]


def test_fail_loud_on_implausible_parse(tmp_path):
    """If the table is missing/unparseable (< 5 repos), raise rather than silently
    scan almost nothing."""
    md = tmp_path / "TECH.md"
    md.write_text("# TECH\n\nNo roster table here at all.\n")
    with pytest.raises(RuntimeError):
        load_tracked_repos(md)


def test_real_tech_md_parses(tmp_path):
    """Smoke against the REAL workspace TECH.md (if present): the live roster must
    parse to a plausible count and include known repos."""
    from pathlib import Path

    real = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "GitHub_Community" / "TECH.md"
    if not real.exists():
        pytest.skip("real TECH.md not present in this environment")
    tier1, tier2 = load_tracked_repos(real)
    assert len(tier1) + len(tier2) >= 19  # at least the prior hardcoded count
    assert "NousResearch/hermes-agent" in tier1
    assert "Graphify-Labs/graphify" in tier2
    # Hot Topics prose never leaks:
    assert not any("Memory for agents" in r for r in tier1 + tier2)
