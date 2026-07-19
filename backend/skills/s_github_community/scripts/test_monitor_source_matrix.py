"""Tests for load_source_matrix() + _parse_stars() — the single TECH.md
Current Roster parser that both monitor.py (scan list) and report.py (Source
Matrix tab + DDD health) now consume. This is the SSOT: no other module may
hardcode a repo/tier/stars copy.

load_tracked_repos() is now a thin wrapper over load_source_matrix(); its
existing behavior is locked by test_monitor_repos.py (the regression net) — this
file adds the NEW guarantees the wrapper's (list,list) tests can't cover:
tier-3 inclusion, int stars, and the star-string parse boundaries.
"""

import textwrap

import pytest

from skills.s_github_community.scripts.monitor import (
    _parse_stars,
    load_source_matrix,
    load_topic_matrix,
    resolve_repo_short_name,
)


# ---- _parse_stars: every real Current Roster star-string shape ----

@pytest.mark.parametrize("s,expected", [
    ("154K", 154000),
    ("136K", 136000),
    ("25.5K", 25500),      # decimal K (was int(s[:-1]) → ValueError bug)
    ("49.5K", 49500),
    ("1.9K", 1900),
    ("1.3K", 1300),
    ("7.7K", 7700),
    ("380", 380),          # bare number
    ("5", 5),
    ("9", 9),
    ("1", 1),
    ("154k", 154000),      # lowercase k
    ("—", 0),              # em-dash / missing → 0, never raise
    ("", 0),
    ("**91K**", 91000),    # future-proof: strip markdown bold
    ("1,200", 1200),       # future-proof: strip thousands comma
    ("garbage", 0),        # unparseable → 0, never raise
])
def test_parse_stars(s, expected):
    assert _parse_stars(s) == expected


# ---- load_source_matrix: structure, tier-3 inclusion, int stars ----

_FIXTURE = textwrap.dedent(
    """\
    ## Source Matrix

    ### Current Roster

    | Tier | Repo | Stars | Category | Last Engaged |
    |------|------|-------|----------|-------------|
    | 1 | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | 154K | Agent harness | 2026-05-28 |
    | 2 | [Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify) | 91K | **Code→graph** — turns folders into graphs | — 🆕 |
    | 3 | [aws-samples/sample-ai-plc](https://github.com/aws-samples/sample-ai-plc) | 5 | AI-PLC sample | — 🆕 |
    | 3 | Agents365-ai/video-podcast-maker | 1K | Content generation | — |

    ### Source Matrix Notes
    - a note with [anthropics/claude-code #16288](https://github.com/anthropics/claude-code/issues/16288)
    """
)


@pytest.fixture
def tech_md(tmp_path):
    p = tmp_path / "TECH.md"
    p.write_text(_FIXTURE)
    return p


def test_returns_repo_tier_stars_dicts(tech_md):
    m = load_source_matrix(tech_md)
    by_repo = {r["repo"]: r for r in m}
    assert by_repo["NousResearch/hermes-agent"]["tier"] == 1
    assert by_repo["NousResearch/hermes-agent"]["stars"] == 154000
    assert by_repo["Graphify-Labs/graphify"]["tier"] == 2
    assert by_repo["Graphify-Labs/graphify"]["stars"] == 91000


def test_includes_tier3(tech_md):
    """The scan list drops tier-3, but the FULL source matrix (for report) keeps it."""
    m = load_source_matrix(tech_md)
    repos = {r["repo"] for r in m}
    assert "aws-samples/sample-ai-plc" in repos          # tier 3
    assert "Agents365-ai/video-podcast-maker" in repos   # tier 3
    assert any(r["tier"] == 3 for r in m)


def test_stars_are_int(tech_md):
    """report.py renders stars with ':,' — requires int, not str."""
    for r in load_source_matrix(tech_md):
        assert isinstance(r["stars"], int)


def test_notes_section_not_ingested(tech_md):
    """The claude-code issue link in Source Matrix Notes is below the roster
    table terminator — must not be parsed as a repo."""
    repos = {r["repo"] for r in load_source_matrix(tech_md)}
    assert "anthropics/claude-code" not in repos


def test_description_embedded_link_not_leaked(tech_md):
    """graphify's description cell has **bold** but no embedded repo link here;
    ensure only column-2 repo is taken, description bold doesn't corrupt stars."""
    by_repo = {r["repo"]: r for r in load_source_matrix(tech_md)}
    # stars column (cells[3]) is '91K' even though description has ** markers
    assert by_repo["Graphify-Labs/graphify"]["stars"] == 91000


# ---- resolve_repo_short_name: TECH.md Our-Topic short name → roster full name ----

_ROSTER = [
    {"repo": "NousResearch/hermes-agent", "tier": 1, "stars": 154000},
    {"repo": "MemPalace/mempalace", "tier": 1, "stars": 54000},
    {"repo": "mattpocock/skills", "tier": 1, "stars": 165000},
    {"repo": "forrestchang/andrej-karpathy-skills", "tier": 3, "stars": 133000},
    {"repo": "crewAIInc/crewAI", "tier": 2, "stars": 52000},
]


@pytest.mark.parametrize("short,expected", [
    ("hermes-agent", "NousResearch/hermes-agent"),  # name-half exact
    ("mempalace", "MemPalace/mempalace"),           # name-half exact (case-insens)
    ("MemPalace", "MemPalace/mempalace"),           # owner-half exact
    ("andrej-karpathy-skills", "forrestchang/andrej-karpathy-skills"),
    ("crewAI", "crewAIInc/crewAI"),                 # owner-half
    ("NousResearch/hermes-agent", "NousResearch/hermes-agent"),  # already full
    ("nonexistent-xyz", None),                      # unresolvable → None
    ("", None),
    # EXACT-only: generic words must NOT fuzzy-match an unrelated repo.
    ("hermes", None),                               # partial name → NOT a match now
    ("enterprise", None),                           # description word, not a repo
])
def test_resolve_repo_short_name(short, expected):
    assert resolve_repo_short_name(short, _ROSTER) == expected


def test_resolve_no_false_substring_match_real_roster():
    """Regression for the Gate-2 HIGH bug: 'enterprise' (a T-DDD description word)
    used to substring-match aws-samples/...-enterprise-agents-... and render a
    wrong link. Exact-only matching must return None for it against the REAL
    roster (where that long repo name exists)."""
    from pathlib import Path

    real = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "GitHub_Community" / "TECH.md"
    if not real.exists():
        pytest.skip("real TECH.md not present")
    roster = load_source_matrix(real)
    assert resolve_repo_short_name("enterprise", roster) is None
    assert resolve_repo_short_name("skills ecosystem", roster) is None


# ---- load_topic_matrix: parse Our Topic Matrix, resolve short repo names ----

_TOPIC_FIXTURE = textwrap.dedent(
    """\
    ## Source Matrix

    ### Current Roster

    | Tier | Repo | Stars | Category | Last Engaged |
    |------|------|-------|----------|-------------|
    | 1 | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | 154K | Agent harness | — |
    | 1 | MemPalace/mempalace | 54K | AI memory | — |
    | 2 | crewAIInc/crewAI | 52K | Multi-agent | — |

    ### Source Matrix Notes
    - some prose

    ## Our Topic Matrix

    ### Current Topics

    | ID | Topic | Status | Thesis | Primary Repos | Hot Topic Match |
    |----|-------|--------|--------|---------------|-----------------|
    | T-MEM | Memory is the Moat | ACTIVE | T1 | MemPalace, hermes-agent | HT#2 ✅ |
    | T-MvS | Multi-Skill > Multi-Agent | ACTIVE | T6 | crewAI | HT#3 ✅ |
    | T-XYZ | Unresolvable topic | CANDIDATE | T5 | nonexistent-repo | — |

    ### Cross-Map Priority
    - after
    """
)


@pytest.fixture
def topic_md(tmp_path):
    p = tmp_path / "TECH.md"
    p.write_text(_TOPIC_FIXTURE)
    return p


def test_load_topic_matrix_parses_all_rows(topic_md):
    tm = load_topic_matrix(topic_md)
    ids = [t["id"] for t in tm]
    assert ids == ["T-MEM", "T-MvS", "T-XYZ"]  # all rows, in order


def test_load_topic_matrix_resolves_short_names(topic_md):
    by_id = {t["id"]: t for t in load_topic_matrix(topic_md)}
    # 'MemPalace' + 'hermes' → full owner/name
    assert by_id["T-MEM"]["primary_repos"] == ["MemPalace/mempalace", "NousResearch/hermes-agent"]
    assert by_id["T-MvS"]["primary_repos"] == ["crewAIInc/crewAI"]


def test_load_topic_matrix_unresolvable_dropped_from_primary(topic_md):
    by_id = {t["id"]: t for t in load_topic_matrix(topic_md)}
    # unresolvable short name → not in primary_repos, but kept in raw for display
    assert by_id["T-XYZ"]["primary_repos"] == []
    assert by_id["T-XYZ"]["primary_repos_raw"] == ["nonexistent-repo"]


def test_load_topic_matrix_captures_status(topic_md):
    by_id = {t["id"]: t for t in load_topic_matrix(topic_md)}
    assert by_id["T-MEM"]["status"] == "ACTIVE"
    assert by_id["T-XYZ"]["status"] == "CANDIDATE"


def test_real_topic_matrix(tmp_path):
    """Live TECH.md: all 12 Our-Topic rows parse, ACTIVE topics resolve >=1 repo."""
    from pathlib import Path

    real = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "GitHub_Community" / "TECH.md"
    if not real.exists():
        pytest.skip("real TECH.md not present")
    tm = load_topic_matrix(real)
    assert len(tm) >= 10  # 12 topics in the live doc
    ids = {t["id"] for t in tm}
    assert "T-MEM" in ids and "T-MvS" in ids
    # T-MEM's short names (MemPalace, hermes, kayba) must resolve to >=1 full repo.
    tmem = next(t for t in tm if t["id"] == "T-MEM")
    assert len(tmem["primary_repos"]) >= 1
    assert all("/" in r for r in tmem["primary_repos"])


def test_real_tech_md_source_matrix(tmp_path):
    """Smoke against the live workspace TECH.md: mattpocock must read as the
    current tier/stars (the drift REPO_INFO had: tier2/88K → now tier1/165K)."""
    from pathlib import Path

    real = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "GitHub_Community" / "TECH.md"
    if not real.exists():
        pytest.skip("real TECH.md not present")
    m = load_source_matrix(real)
    assert len(m) >= 30                       # full roster incl tier3
    assert any(r["tier"] == 3 for r in m)     # tier3 present
    by_repo = {r["repo"]: r for r in m}
    # mattpocock/skills was the flagship drift case in the stale REPO_INFO
    mp = by_repo.get("mattpocock/skills")
    assert mp is not None and mp["tier"] == 1 and mp["stars"] >= 150000
    # every stars is a non-negative int
    assert all(isinstance(r["stars"], int) and r["stars"] >= 0 for r in m)
