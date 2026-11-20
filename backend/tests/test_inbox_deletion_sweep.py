"""Nothing the Inbox replaced is still read (design docs/design/2026-11-20-inbox.md §10, R27).

Every deleted module, symbol, route, job id and nav id is searched for in the code, the
tests and the skills that ship (backend/, desktop/src/, desktop/scripts/, scripts/); a
name may appear only where a test or a retired-key list says it is gone. Design docs and
run records keep their history and are not searched.

Also pinned here: the moved homes are the ones read (ADAPTER_MAP from core.inbox.feeds,
the SSRF-guarded client from core.http_client, the feed type of the desktop feed editor
from services/inbox), the workspace no longer creates Knowledge/Signals, and the skill
docs name the Inbox instead of the signal chain.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
ROOTS = (BACKEND, REPO / "desktop" / "src", REPO / "desktop" / "scripts", REPO / "scripts")
SKIP_DIRS = {".venv", "node_modules", "__pycache__", "dist", "target", ".pytest_cache"}
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".sh", ".md", ".json", ".yaml", ".yml", ".toml"}
SELF = Path(__file__).resolve()

# name -> files (repo-relative) where it may still appear, and why
DELETED: dict[str, set[str]] = {
    r"\bfallback_script\b": {"backend/jobs/scheduler.py",                # RETIRED_CONFIG_KEYS
                             "backend/tests/test_job_retired_config_keys.py"},
    r"\boutput_as_signals\b": {"backend/jobs/scheduler.py",
                               "backend/tests/test_job_retired_config_keys.py"},
    r"channel_monitor_fallback": set(),
    r"\bsignal_digest\b": {"backend/tests/test_job_retired_config_keys.py"},
    r"\bSIGNAL_DIGEST\b": set(),
    r"\bsignal_fetch\b": {"backend/tests/test_job_retired_config_keys.py"},
    r"\bSIGNAL_FETCH\b": set(),
    r"\bself_tune\b": {"backend/tests/test_job_retired_config_keys.py"},
    r"\bSELF_TUNE\b": set(),
    r"\bsignal_selection\b": set(),
    r"_format_signal_digest_message": {"backend/tests/test_job_retired_config_keys.py"},
    r"_extract_signals_from_output": set(),
    r"load_user_context": set(),
    r"\bjobs\.dedup\b|from \.dedup import": set(),
    r"\braw_signals\b|\bdedup_cache\b": {"backend/tests/test_job_retired_config_keys.py"},
    r"jobs[./]adapters": set(),
    r"github_community\.py|jobs\.adapters\.github_community": set(),
    r"\bcommunity_api\b|\bcommunity_data\b": set(),
    r"CommunityOverlay|CommunityContent|communityService|services/community": set(),
    r"show-community": {"backend/tests/test_ui_actions.py"},             # banned-list entry
    r"nav-community": {"desktop/src/components/layout/InboxNav.test.tsx"},  # absence assert
    r"/api/community": {"desktop/src/services/__tests__/urlContract.test.ts"},  # a lint sample string
    r"RADAR_TODOS": {"backend/skills/s_radar-todo/SKILL.md",             # the ToDo skill's own protocol
                     "backend/tests/test_todo_context_and_purge.py"},
    r"\bsignal-fetch\b|\bsignal-digest\b|\bweekly-rollup\b|\bsignal-notify-slack\b":
        {"backend/tests/test_job_retired_config_keys.py"},
    r"Services/signals|\"Services\" / \"signals\"": {"backend/tests/test_jobs.py",  # absence assert
                                                      "backend/tests/test_memory_index.py"},  # fixture text
}


def _files():
    for root in ROOTS:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in SUFFIXES or path.resolve() == SELF:
                continue
            if SKIP_DIRS & set(path.relative_to(REPO).parts):
                continue
            yield path


@pytest.fixture(scope="module")
def corpus() -> dict[str, str]:
    out = {}
    for path in _files():
        try:
            out[path.relative_to(REPO).as_posix()] = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return out


@pytest.mark.parametrize("pattern", sorted(DELETED))
def test_no_consumer_of_deleted_names(corpus, pattern):
    rx = re.compile(pattern)
    allowed = DELETED[pattern]
    hits = sorted(rel for rel, text in corpus.items() if rx.search(text) and rel not in allowed)
    assert hits == [], f"{pattern!r} is still read in {hits}"


@pytest.mark.parametrize("rel", [
    "backend/jobs/channel_monitor_fallback.py", "backend/jobs/handlers/signal_digest.py",
    "backend/jobs/handlers/signal_fetch.py", "backend/jobs/signal_selection.py",
    "backend/jobs/self_tune.py", "backend/jobs/dedup.py", "backend/jobs/adapters",
    "backend/routers/community_api.py", "backend/core/community_data.py",
    "desktop/src/components/layout/CommunityOverlay.tsx", "desktop/src/services/community.ts",
])
def test_deleted_paths_are_gone(rel):
    assert not (REPO / rel).exists(), rel


def test_overlay_registries_have_no_community():
    layout = REPO / "desktop" / "src" / "components" / "layout"
    for name in ("overlayIds.ts", "navTable.ts", "overlayChunks.ts", "overlaySurfaces.tsx",
                 "overlayQueries.ts", "useExclusiveOverlay.ts", "ThreeColumnLayout.tsx"):
        text = (layout / name).read_text(encoding="utf-8")
        assert not re.search(r"['\"]community['\"]|\bcommunity:", text), name


def test_main_mounts_no_community_router():
    text = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert "community" not in text.lower()


def test_moved_homes_are_the_ones_read():
    from core.inbox.feeds import ADAPTER_MAP
    from core.inbox.sources import feeds as feed_source
    assert feed_source.ADAPTER_MAP is ADAPTER_MAP
    notify = (BACKEND / "skills" / "s_notify" / "notify.py").read_text(encoding="utf-8")
    assert "from core.http_client import safe_client" in notify
    editor = (REPO / "desktop/src/components/inbox/FeedEditor.tsx").read_text(encoding="utf-8")
    assert "from '../../services/inbox'" in editor and "services/community" not in editor


def test_workspace_creates_no_signals_folder():
    from core import swarm_workspace_manager as wm
    from core.library_health import RAW_LOG_CATEGORIES
    assert "Signals" not in wm.KNOWLEDGE_SUBDIRS
    assert not {"Knowledge/Signals", "Knowledge/DailyBriefs"} & wm.SYSTEM_MANAGED_FOLDERS
    assert "Signals" not in RAW_LOG_CATEGORIES


def test_context_files_describe_signals_only_as_archived():
    for name in ("SWARMAI.md", "KNOWLEDGE.md"):
        text = (BACKEND / "context" / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if "Signals/" in line or "DailyBriefs/" in line:
                assert "Archives" in line or "retired" in line or "history" in line, (name, line)
    assert "Community" not in (BACKEND / "context" / "SWARMAI.md").read_text(encoding="utf-8")


def test_skill_docs_name_the_inbox():
    trending = (BACKEND / "skills" / "s_github-trending" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "Inbox" in trending and "trend" in trending
    assert "signal_digest" not in trending and "signal pipeline" not in trending
    manager = (BACKEND / "skills" / "s_job-manager" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert not re.search(r"signal[-_](fetch|digest)", manager)


def test_loops_health_prints_no_radar_todos():
    script = (BACKEND / "skills" / "s_loops-health" / "scripts" / "loops_health_check.py")
    assert "RADAR_TODOS" not in script.read_text(encoding="utf-8")
