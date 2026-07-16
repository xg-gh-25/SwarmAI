"""Single-workspace filesystem manager for SwarmAI.

This module was refactored from a multi-workspace model to a single-workspace
+ projects model centred on the ``SwarmWS`` workspace.  It is responsible for:

- ``SwarmWorkspaceManager``          — Main class managing workspace filesystem
- ``_batch_remove``                  — Sync helper for batched legacy file removal
- ``FOLDER_STRUCTURE``               — Minimal folder layout (Knowledge, Projects)
- ``SYSTEM_MANAGED_*`` constants     — Sets of paths that cannot be deleted/renamed
- ``PROJECT_SYSTEM_FILES``           — Per-project system files (.project.json)
- ``GITIGNORE_CONTENT``              — Default .gitignore for git-backed workspace
- Project CRUD methods               — create / delete / get / list projects

The global singleton ``swarm_workspace_manager`` is created at module level.
"""
import asyncio
import copy
import json
import logging
import os
import re
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import anyio
import subprocess

from core.entity_extractor import extract_clean_description, extract_entities_from_ddd, format_entity_index, prune_entity_index
from core.project_schema_migrations import CURRENT_SCHEMA_VERSION, migrate_if_needed
from core.project_registry import DDD_CANONICAL_DOCS  # Run 0: single source of truth

logger = logging.getLogger(__name__)

# Write lock for PROJECTS.md / DDD file modifications.
# Prevents interleaved writes from concurrent refresh triggers.
# Lazily initialized to avoid binding to wrong event loop in tests.
_cultivation_write_lock: asyncio.Lock | None = None


def _get_cultivation_lock() -> asyncio.Lock:
    """Get or create the cultivation write lock (lazy init)."""
    global _cultivation_write_lock
    if _cultivation_write_lock is None:
        _cultivation_write_lock = asyncio.Lock()
    return _cultivation_write_lock

# ─────────────────────────────────────────────────────────────────────────────
# Module-level constants
# ─────────────────────────────────────────────────────────────────────────────

# Simplified folder structure — only user-facing directories
FOLDER_STRUCTURE = ["Knowledge", "Projects", "Attachments", "Services"]

# Default Knowledge subdirectories (auto-created on startup)
KNOWLEDGE_SUBDIRS = [
    "Notes", "Reports", "Meetings", "Library", "Archives",
    "DailyActivity", "Handoffs", "Designs", "Learned", "Pollinate",
    "Signals", "JobResults",
]

SYSTEM_MANAGED_FOLDERS = {
    "Knowledge", "Projects", "Attachments", "Services",
    "Knowledge/Notes", "Knowledge/Reports", "Knowledge/Meetings",
    "Knowledge/Library", "Knowledge/Archives", "Knowledge/DailyActivity",
    "Knowledge/Handoffs", "Knowledge/Designs", "Knowledge/Learned",
    "Knowledge/Pollinate", "Knowledge/Signals", "Knowledge/JobResults",
}

SYSTEM_MANAGED_ROOT_FILES: set[str] = set()

SYSTEM_MANAGED_SECTION_FILES: set[str] = set()

PROJECT_SYSTEM_FILES = {".project.json"}

PROJECT_SYSTEM_FOLDERS: set[str] = set()

# The SwarmAI project ships with every workspace. Users can edit its DDD
# docs but cannot delete or rename the project itself.
DEFAULT_PROJECT_NAME = "SwarmAI"

# Version of the canonical six-section DDD structure (DDD-agent-brain spec §3.6)
# that this provisioner scaffolds. Stamped into every new project's .project.json
# so propagated DDDs are version-traceable (§3.7 anti-drift) — the machine-readable
# counterpart to the DDD_SPEC_VERSION declared in s_project-manager/SKILL.md prose.
# Bump when the six-section structure changes in a way propagated DDDs must track.
DDD_SPEC_VERSION = "1.0"

# Default job system config (provisioned on first startup).
# Feed definitions are user-customizable; system job definitions live in code.
_DEFAULT_JOB_CONFIG = """\
# Swarm Signal Pipeline — Feed Configuration
# Feeds define what signals to fetch. Edit freely.
# System job definitions are managed by SwarmAI (not in this file).
# Self-tune auto-adjusts user_context and HN keywords based on your usage.

feeds:
  - id: ai-engineering
    name: AI Engineering Blogs
    type: rss
    tier: engineering
    config:
      urls:
        - https://simonwillison.net/atom/everything/
        - https://lilianweng.github.io/index.xml
        - https://www.latent.space/feed
        - https://blog.langchain.com/rss/
        - https://huggingface.co/blog/feed.xml
    tags: [ai, engineering]
    enabled: true

  - id: frontier-labs
    name: Frontier Lab Official Blogs
    type: rss
    tier: frontier
    config:
      urls:
        - https://openai.com/blog/rss.xml
        - https://blog.google/technology/ai/rss/
        - https://deepmind.google/blog/rss.xml
        - https://blogs.microsoft.com/ai/feed/
    tags: [frontier, ai, official]
    enabled: true

  - id: ai-builders
    name: AI Builder Podcasts (YouTube RSS, zero-API)
    type: rss
    tier: leaders
    # Curated top-AI-builder podcasts (from follow-builders watchlist). YouTube
    # channel RSS is free/keyless — the digest summarizes new episode titles.
    config:
      urls:
        - https://www.youtube.com/feeds/videos.xml?channel_id=UCxBcwypKK-W3GHd_RZ9FZrQ   # Latent Space
        - https://www.youtube.com/feeds/videos.xml?channel_id=UCSI7h9hydQ40K5MJHnCrQvw   # No Priors
        - https://www.youtube.com/feeds/videos.xml?channel_id=UCUl-s_Vp-Kkk_XVyDylNwLA   # Unsupervised Learning (Redpoint)
        - https://www.youtube.com/feeds/videos.xml?channel_id=UCQID78IY6EOojr5RUdD47MQ   # MAD Podcast (Matt Turck)
        - https://www.youtube.com/feeds/videos.xml?channel_id=UCWrF0oN6unbXrWsTN7RctTw   # Training Data (Sequoia)
        - https://www.youtube.com/feeds/videos.xml?channel_id=UCjIMtrzxYc0lblGhmOgC_CA   # AI & I (Every)
    tags: [ai, builders, podcasts, leaders]
    enabled: true

  - id: ai-newsletters
    name: AI Newsletters & Aggregators
    type: rss
    tier: aggregate
    config:
      urls:
        - https://importai.substack.com/feed
        - https://techcrunch.com/category/artificial-intelligence/feed/
    tags: [newsletter, aggregate, ai]
    enabled: true

  - id: tool-releases
    name: AI Tool Releases
    type: github-releases
    tier: engineering
    config:
      repos:
        - anthropics/anthropic-sdk-python
        - anthropics/claude-code
        - pydantic/pydantic
        - fastapi/fastapi
      include_prereleases: false
    tags: [releases, tools]
    enabled: true

  - id: hn-ai
    name: HN AI Discussions
    type: hacker-news
    tier: aggregate
    config:
      keywords: [Claude, LLM agent, AI coding, Anthropic]
      min_score: 50
      max_stories: 15
    tags: [ai, community]
    enabled: true

defaults:
  max_age_hours: 48
  dedup_window_days: 7
  relevance_threshold: 0.3
  max_active_feeds: 20
  max_daily_agent_tasks: 50
  max_monthly_spend_usd: 100.0

user_context:
  interests: []
  projects: []
  tech_stack: []
  recent_topics: []
"""

# DDD document templates for new projects.  Each key is a filename, each
# value is the template content with ``{project_name}`` placeholders.
DDD_TEMPLATES: dict[str, str] = {
    "PRODUCT.md": """# {project_name} -- Product Context

## Vision

_What is this project and why does it exist? One paragraph._

## Strategic Priorities

1. _Priority 1_
2. _Priority 2_
3. _Priority 3_

## Success Criteria

- _How do you know this project is succeeding?_

## Non-Goals

- _What are you explicitly NOT doing?_
""",
    "TECH.md": """# {project_name} -- Technical Context

## Architecture

_System overview, key components, data flow._

## Stack

- **Language:** _e.g., Python 3.12, TypeScript 5_
- **Framework:** _e.g., FastAPI, Next.js_
- **Database:** _e.g., SQLite, PostgreSQL_
- **Testing:** _e.g., pytest, vitest_

## Codebase Location

_Absolute path or repo URL to the project's source code._

## Dev Commands

- **Start:** _e.g., npm run dev, ./dev.sh_
- **Test:** _e.g., pytest, npm test_
- **Build:** _e.g., npm run build_

## Conventions

_Naming, file structure, commit message format._

## Key Files

| Domain | Files |
|--------|-------|
| _..._ | _..._ |
""",
    "IMPROVEMENT.md": """# {project_name} -- Lessons & Patterns

## What Worked

_Patterns that succeeded. Will grow through usage._

## What Failed

_Patterns that failed, root causes, what to do instead. Will grow through usage._

## Known Issues

_Recurring problems to watch for._
""",
    "PROJECT.md": """# {project_name} -- Current Context

## Current Focus

_What are you working on right now?_

## Open Items

- [ ] _Active work item_

## Recent Decisions

- _YYYY-MM-DD: Decision and rationale_

## Blocked By

_Nothing currently blocking._
""",
}

# Canonical six-section DDD structure — SKELETON materialized at CREATE
# (option A, XG decision 2026-07-12; refined 2026-07-12 to remove over-build).
# The skeleton is concrete so SwarmAI-maintenance FOLLOWS the standard and AIM
# export is low-variance; section CONTENT (real gates/skills/agent-specs) still
# ACCRETES as the project grows.
#
# TWO layers (why this is not one flat map):
#   • SECTION_SCAFFOLD — FILES written only-if-absent ({project_name}-templated):
#       ① aim.json (manifest, declares the 5 default native skills) + AGENTS.md
#         (the ONE unified README covering all six sections) + .crux_template.md
#       ⑥ REFRESHER.md (shape-neutral marker)
#   • SECTION_DIRS — the ③④ section DIRECTORIES that must exist in the skeleton
#     but carry NO prose README (D2: AGENTS.md is the single README). A flat
#     {relpath:content} map cannot create an empty dir (mkdir only fires on a
#     file write — Gate-1 B3), so these are created explicitly with a .gitkeep
#     dir-presence marker. Content accretes: real gates/skills fill them later.
#
# Design corrections baked in (2026-07-12, DoD D1/D2/D3):
#   D1: NO agents/ or agent-sops/ here — those are AIM-EXPORT-form members
#       (§555), NOT the SwarmWS-native DDD; they accrete on-demand at export /
#       when a real agent-spec is authored, never pre-scaffolded empty.
#   D2: ONE unified AGENTS.md README, NOT five per-section README stubs.
#   D3: aim.json.plugins declares the 5 default DDD-native skills.
#
# ② KNOWLEDGE (4 DDD_TEMPLATES docs + Knowledge/) is handled by the workspace;
# ⑤ DELIVERY CONTRACT (bindings.yaml) is provisioned by BIND, not CREATE.
SECTION_SCAFFOLD: dict[str, str] = {
    # ── ① IDENTITY & MANIFEST ────────────────────────────────────────────────
    # aim.json.plugins declares the 5 default DDD-native skills (D3) — the SAME
    # skills that are physically COPIED INTO skills/ at provision (DDD_NATIVE_SKILLS).
    # A DDD is self-propagating + self-養成 because it carries the ability to
    # create more DDDs (s_ddd-manager), sediment its own docs (s_ddd-persist), run
    # its own dev-loop (s_ddd-pipeline), express its value (s_ddd-pollinate), and
    # refresh its ⑥ code-intel projection (s_ai-ready-repo). On AIM export this
    # becomes the plugin namespace; the skill FILES ship alongside it.
    "aim.json": """{
  "name": "{project_name}",
  "ddd_spec_version": "1.0",
  "description": "DDD package for {project_name} (six-section canonical structure).",
  "plugins": {
    "native_skills": [
      "s_ddd-manager",
      "s_ddd-persist",
      "s_ddd-pipeline",
      "s_ddd-pollinate",
      "s_ai-ready-repo"
    ]
  }
}
""",
    # AGENTS.md is the SINGLE unified README (D2) — covers all six sections +
    # the 5 native skills. No per-section README stubs.
    "AGENTS.md": """# {project_name} — DDD Agent Guide (① Identity & unified README)

The ONE README for **{project_name}**'s DDD. This IS section ① and it explains
the whole canonical six-section structure (DDD-agent-brain spec §3.6) — there are
no per-section READMEs; every section is documented here.

## What this DDD is
{project_name}'s domain **brain / control plane** — it OWNs cognition (①-④) and
GOVERNs the physical repo (⑤-⑥). It never contains source code and never runs a
deploy pipeline (指+治, 不含+不跑).

## The six sections
| # | Section | OWN/GOVERN | Members | What belongs / accretion rule |
|---|---------|-----------|---------|-------------------------------|
| ① | Identity & Manifest | OWN | this file, `aim.json`, `.crux_template.md` | the DDD's identity + export manifest |
| ② | Knowledge | OWN | `PRODUCT/TECH/IMPROVEMENT/PROJECT.md` + `Knowledge/` | 冷启动 + judgment BORN here as prose |
| ③ | Gates (the moat) | OWN | `gates/<gate>.py\\|sh` + tests + `gates/context/includes/*_denied*.json` | a gate is born as a ② pitfall, matures via the 养成 ladder, compiled here as an exit-2 BLOCK check. Empty until the first judgment matures. |
| ④ | Capabilities | OWN | `skills/` (portable `s_<name>/SKILL.md`) | validated portable skills the DDD distributes. Accretes as capabilities are bound. |
| ⑤ | Delivery Contract | GOVERN | `bindings.yaml` | full per-repo delivery 全貌 (build_system·version_set·deploy_pipeline ref·review_path·refresh_policy). Added by BIND. |
| ⑥ | Code-Intel Refresher | GOVERN | `REFRESHER.md` | a self-contained mechanism that REGENERATES `code-intel.json` from code. Ships the refresher, not the projection. Activates on BIND. |

## Default native skills (the self-養成 / self-propagation set — ④, copied into `skills/`)
- **s_ddd-manager** — provision new spec-compliant DDDs (self-propagation seed).
- **s_ddd-persist** — sediment/refresh THIS DDD's docs (only-additive, honors human edits).
- **s_ddd-pipeline** — DDD-native judge→execute→reflect dev loop (file-state, retains the Gate-2 adversarial moat).
- **s_ddd-pollinate** — express this product's value to audiences.
- **s_ai-ready-repo** — the ⑥ refresher: regenerate `code-intel.json` from code.

Together they make a DDD **get smarter with use, on any runtime, without SwarmAI**.

## NOT a DDD member (derived / physical zone)
`code-intel.json` (machine projection — regenerated by ⑥, never PR-flows-back),
`code_intel.db` (local query engine), the product source repos (GOVERNed via ⑤,
never contained), the deploy pipeline (referenced in ⑤, never executed here).
AIM-export-only members (`agents/*.agent-spec.json`, `agent-sops/`, `context/`)
are generated at export — they are NOT part of the SwarmWS-native skeleton.

## Non-section directories (sanctioned, at project root)
`assets/` (diagrams/decks/generators — referenced by path, do NOT relocate),
`templates/` (doc templates), `.artifacts/` (pipeline run outputs).
""",
    ".crux_template.md": """## Summary
_One-line summary of the change to {project_name}._

## Description
_What changed and why._

## Testing
- [ ] _How this was verified._
""",
    # ── ⑥ CODE-INTEL REFRESHER (shape-neutral marker) ────────────────────────
    "REFRESHER.md": """# ⑥ Code-Intel Refresher — {project_name}

Section ⑥ GOVERNs the physical repo's code-intel projection. It is a
**self-contained mechanism that REGENERATES `code-intel.json` from code** — it
ships the refresher (capability), never the projection (derived data). The
default refresher is `s_ai-ready-repo` (narrow refresh mode; see `aim.json`
native_skills).

**Activation:** ⑥ activates when a repo is BOUND (see ⑤ `bindings.yaml`). For a
**no-repo project it is a no-op** — there is no code to refresh, so this file is
just a placeholder honoring the canonical structure. Once a repo is bound and a
dev-consumer profile pulls this DDD, the refresher regenerates the projection
LOCALLY (never PR-flowed-back — the derived-projection rule, spec §3.6).
""",
}

# ③④ section DIRECTORIES that exist in the skeleton but carry no prose README
# (D2: AGENTS.md is the single README). Created explicitly with a .gitkeep
# marker because a flat file-map cannot materialize an empty dir (Gate-1 B3).
# NO agents/ or agent-sops/ — those are AIM-export-form, not SwarmWS-native (D1).
SECTION_DIRS: tuple[str, ...] = (
    "gates",                      # ③ executable judgment (accretes)
    "gates/context/includes",     # ③ denylist DATA home (accretes)
    "skills",                     # ④ portable capabilities (accretes)
    "Knowledge",                  # ② deep reference material (accretes; spec §3.6
                                  #    "② KNOWLEDGE = 4 docs + Knowledge/"). s_ddd-persist
                                  #    routes reference/spec here; _recall_ddd scans it.
                                  #    (locked_write self-creates it too, but the skeleton
                                  #    must reflect the canonical structure — Q1 Gate-0.)
)

# ④ DEFAULT DDD-NATIVE SKILLS — the official, maintained set that is COPIED INTO
# every DDD's skills/ at CREATE (the same mechanism that copies the 4 DDD docs).
# These are DDD-NATIVE rewrites of SwarmAI's own skills — learned from the
# originals but re-designed to be portable (file-based .artifacts state, no
# SwarmAI backend) so that after `aim` export they run directly in Kiro /
# Claude Code. SOURCE OF TRUTH: backend/templates/ddd-skills/s_ddd-*/ (we, the
# official maintainer, keep them there and version them). This is DISTINCT from
# the SwarmAI-native skills in backend/skills/ (s_project-manager, s_persist,
# s_autonomous-pipeline, s_pollinate, s_ai-ready-repo, s_internal-*) which are
# how SwarmAI itself operates and are NEVER modified for DDD work.
DDD_NATIVE_SKILLS: tuple[str, ...] = (
    "s_ddd-manager",     # ← learned from s_project-manager (self-propagation seed)
    "s_ddd-persist",     # ← learned from s_persist (sediment DDD docs)
    "s_ddd-pipeline",    # ← learned from s_autonomous-pipeline (judge→execute→reflect)
    "s_ddd-pollinate",   # ← learned from s_pollinate (express value)
    "s_ai-ready-repo",   # ← the ⑥ code-intel refresher (portable as-is)
)

# ── EXTERNAL vs INTERNAL provisioning sources — the git-tracking boundary ──────
# This repo is PUBLIC. The rule: EXTERNAL provisioning sources are tracked + ship;
# INTERNAL ones (Amazon CRUX/Brazil) are gitignored + local-only, copied into a DDD
# ONLY when internal=True. Keep the two source trees separate so internal never leaks:
#   EXTERNAL (tracked, public):  templates/ddd-skills/s_ddd-*  (the 5 native skills)
#   INTERNAL (gitignored, local): backend/skills/s_internal-*/  (.gitignore glob)
#                                 templates/ddd-gates/          (.gitignore dir — CRUX no-push gate)
# Provisioning reads external from templates/ddd-skills, internal from the two ignored
# trees above — only under `if internal:`. (leak-fix run_d0216c92: templates/ddd-gates
# was git-tracked in the public repo; now gitignored. A future internal source MUST land
# in an already-ignored path, never in templates/ddd-skills.)
#
# Internal-DDD extra capabilities (bound to a Brazil/CRUX repo, e.g. AIDLC): the
# internal toolchain skills + the no-git-push gate. Copied in ADDITION to the 5
# native skills when a DDD is internal. Copied FROM the SwarmAI-native (gitignored)
# backend/skills/s_internal-* (already portable HITL wrappers), not from templates.
INTERNAL_DDD_SKILLS: tuple[str, ...] = (
    "s_internal-brazil",
    "s_internal-crux-cr",
    "s_internal-crux-review",
)


def _load_ddd_native_skill_templates() -> dict[str, dict[str, str]]:
    """Load the 5 default DDD-native skill templates from
    backend/templates/ddd-skills/s_ddd-*/. Returns {skill_name: {relpath: content}}.

    Mirrors _load_swarmai_ddd_templates: maintained as standalone files for
    readability/diffability, copied verbatim into each DDD's skills/ at provision.
    Fails soft (logs) if a template dir is missing — a DDD without a native skill
    is degraded but not broken.
    """
    templates_dir = Path(__file__).parent.parent / "templates" / "ddd-skills"
    result: dict[str, dict[str, str]] = {}
    for skill in DDD_NATIVE_SKILLS:
        skill_dir = templates_dir / skill
        if not skill_dir.is_dir():
            logger.warning("DDD-native skill template missing: %s", skill_dir)
            continue
        files: dict[str, str] = {}
        for f in sorted(skill_dir.rglob("*")):
            if not f.is_file():
                continue
            # Skip build cruft that must never ship in a skill template. The
            # engine/ template dir has an __init__.py (importable), so running
            # it in place generates __pycache__/*.pyc — reading a .pyc as UTF-8
            # raised UnicodeDecodeError (0xcb = bytecode magic) and crashed
            # module import, breaking the whole backend. A template ships text
            # only; treat a non-text file as cruft to skip, never as fatal.
            if "__pycache__" in f.parts or f.suffix in {".pyc", ".pyo"}:
                continue
            rel = str(f.relative_to(skill_dir))
            try:
                files[rel] = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # Binary cruft (a non-.pyc binary that slipped past the suffix
                # skip) — expected to skip, not fatal.
                logger.warning(
                    "Skipping non-text file in DDD-native skill template %s: %s",
                    skill, rel,
                )
            except OSError as e:
                # A LEGIT text file we wanted failed to read (permission /
                # transient IO) — the skill will be incomplete. Loud (ERROR),
                # distinct from expected binary skips, so it doesn't hide as
                # "non-text cruft". (Gate-2 LOW.)
                logger.error(
                    "Failed to read DDD-native skill template %s/%s: %s — skill will be incomplete",
                    skill, rel, e,
                )
        if files:
            # Completeness guard: a skill without its SKILL.md manifest is a
            # corrupt template — it would ship a headless skill dir whose failure
            # surfaces far downstream (parse_skill_md "missing SKILL.md"). Make
            # the specific corruption loud at load time. Still fail-soft (we ship
            # what survived), just no longer silent. (Gate-2 LOW.)
            if "SKILL.md" not in files:
                logger.error(
                    "DDD-native skill %s loaded WITHOUT SKILL.md (%d other files) — template corrupt",
                    skill, len(files),
                )
            result[skill] = files
    return result


DDD_NATIVE_SKILL_TEMPLATES: dict[str, dict[str, str]] = _load_ddd_native_skill_templates()

# Default SwarmAI project DDD content (richer than templates, serves as
# example for users).
def _load_swarmai_ddd_templates() -> dict[str, str]:
    """Load SwarmAI default project DDD templates from backend/templates/ddd/.

    Templates are maintained as standalone markdown files for readability,
    diffability, and ease of editing. Falls back to minimal inline content
    if template files are missing (e.g. PyInstaller bundle without templates).
    """
    templates_dir = Path(__file__).parent.parent / "templates" / "ddd"
    ddd_files = list(DDD_CANONICAL_DOCS)
    result: dict[str, str] = {}

    for filename in ddd_files:
        template_path = templates_dir / filename
        if template_path.exists():
            result[filename] = template_path.read_text(encoding="utf-8")
        else:
            # Minimal fallback — template files should always exist in codebase
            logger.warning(
                "DDD template missing: %s — using minimal fallback", template_path
            )
            title = filename.replace(".md", "")
            result[filename] = (
                f"# SwarmAI -- {title}\n\n"
                f"_Template not found. Edit this file to add project context._\n"
            )

    return result


SWARMAI_PROJECT_DDD: dict[str, str] = _load_swarmai_ddd_templates()

DEPTH_LIMITS = {
    "project_user": 3,
}

DEFAULT_WORKSPACE_CONFIG = {
    "name": "SwarmWS",
    "file_path": "{app_data_dir}/SwarmWS",
    "icon": "🏠",
}

# Project name validation: 1–100 chars, alphanumeric + spaces/hyphens/underscores/periods
_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 _.\-]{0,99}$")

# Reserved filesystem names (Windows)
_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})

# .gitignore content for git-backed workspace
GITIGNORE_CONTENT = """\
*.db
*.db-wal
*.db-shm
*.lock
__pycache__/
.venv/
node_modules/
*.pyc
*.tmp
.DS_Store
.claude/mcps/mcp-dev.json
proactive_state.json
hook_stats.json
"""

# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Batch filesystem removal helper
# ─────────────────────────────────────────────────────────────────────────────


def _batch_remove(paths_to_remove: list[tuple[Path, str]]) -> list[str]:
    """Remove all legacy paths in a single synchronous batch.

    Designed to be called once via ``anyio.to_thread.run_sync()`` so that
    all filesystem I/O happens in a single thread dispatch instead of one
    dispatch per item.

    Args:
        paths_to_remove: List of ``(path, kind)`` tuples where *kind* is
            ``"file"`` or ``"dir"``.

    Returns:
        List of error messages for items that failed to remove.  An empty
        list means every removal succeeded.
    """
    errors: list[str] = []
    for path, kind in paths_to_remove:
        try:
            if kind == "dir":
                shutil.rmtree(path, ignore_errors=False)
            else:
                path.unlink(missing_ok=True)
        except Exception as e:
            errors.append(f"{path}: {e}")
    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Manager class
# ─────────────────────────────────────────────────────────────────────────────

class SwarmWorkspaceManager:
    """Manages the single SwarmWS workspace filesystem operations.

    Provides path helpers, system-managed path checks, depth validation,
    folder structure creation, integrity verification, and project CRUD.
    All async filesystem operations use ``anyio.to_thread.run_sync()``.

    Module-level constants are re-exported as class attributes for backward
    compatibility (e.g. ``SwarmWorkspaceManager.DEFAULT_WORKSPACE_CONFIG``).

    Marker files:
    - ``.legacy_cleaned`` — Written to the workspace root after
      ``_cleanup_legacy_content()`` finishes its first successful run.
      Subsequent startups skip the cleanup entirely when this marker
      exists.  The marker is excluded from idempotence test snapshots
      because it is created only on the second ``ensure_default_workspace``
      call (first call creates the workspace, second call triggers cleanup).
    """

    # Re-export module-level constants as class attributes for backward compat
    FOLDER_STRUCTURE = FOLDER_STRUCTURE
    SYSTEM_MANAGED_FOLDERS = SYSTEM_MANAGED_FOLDERS
    PROJECT_SYSTEM_FILES = PROJECT_SYSTEM_FILES
    DEFAULT_WORKSPACE_CONFIG = DEFAULT_WORKSPACE_CONFIG

    def __init__(self):
        """Initialize the SwarmWorkspaceManager.

        Sets up per-project concurrency locks and the in-memory UUID→Path
        index used by ``_find_project_dir`` for fast project lookups.
        """
        self._project_locks: dict[str, asyncio.Lock] = {}
        self._uuid_index: dict[str, Path] = {}

    # ── Path helpers ─────────────────────────────────────────────────────

    def expand_path(self, file_path: str) -> str:
        """Expand path placeholders to actual filesystem paths.

        Handles the following expansions:
        - ~ : User home directory
        - {app_data_dir} : Platform-specific application data directory

        Args:
            file_path: Path that may contain ~ or {app_data_dir} placeholders.

        Returns:
            Expanded absolute path string.
        """
        from config import get_app_data_dir

        if "{app_data_dir}" in file_path:
            app_data_path = str(get_app_data_dir())
            file_path = file_path.replace("{app_data_dir}", app_data_path)

        return os.path.expanduser(file_path)

    def validate_path(self, file_path: str) -> bool:
        """Validate that a file path is safe and properly formatted.

        Validates:
        - Path does not contain path traversal sequences (..)
        - Path is either absolute, starts with ~, or starts with {app_data_dir}

        Args:
            file_path: The file path to validate.

        Returns:
            True if path is valid, False otherwise.
        """
        if not file_path:
            logger.warning("Path validation failed: empty path")
            return False

        if ".." in file_path:
            logger.warning("Path validation failed: path traversal detected in '%s'", file_path)
            return False

        is_absolute = os.path.isabs(file_path)
        starts_with_tilde = file_path.startswith("~")
        starts_with_app_data_dir = file_path.startswith("{app_data_dir}")

        if not is_absolute and not starts_with_tilde and not starts_with_app_data_dir:
            logger.warning(
                "Path validation failed: path must be absolute, start with ~, "
                "or use {app_data_dir}: '%s'", file_path
            )
            return False

        return True

    # ── System-managed checks ────────────────────────────────────────────

    def validate_depth(self, target_path: str) -> tuple[bool, str]:
        """Check whether creating a folder at target_path would exceed depth guardrails.

        Args:
            target_path: The path to validate (relative to workspace root).

        Returns:
            (is_valid, error_message) tuple. error_message is empty string when valid.
        """
        normalized = target_path.strip("/").replace("\\", "/")
        parts = normalized.split("/")

        if not parts or not parts[0]:
            return (True, "")

        first = parts[0]
        section_type: Optional[str] = None
        depth = 0

        if first == "Knowledge" or normalized.startswith("Knowledge/"):
            section_type = "knowledge"
            depth = len(parts) - 1
        elif first == "Projects":
            if len(parts) < 3:
                return (True, "")
            sub_path = parts[2:]
            if sub_path and sub_path[0] in PROJECT_SYSTEM_FOLDERS:
                section_type = "project_system"
                depth = len(sub_path) - 1
            else:
                section_type = "project_user"
                depth = len(sub_path) - 1
        else:
            return (True, "")

        limit = DEPTH_LIMITS.get(section_type, 999)
        if depth > limit:
            return (
                False,
                f"Maximum folder depth of {limit} exceeded for {section_type}. "
                f"Current depth: {depth}.",
            )
        return (True, "")

    # ── Filesystem helpers ───────────────────────────────────────────────

    def _write_file_if_missing(self, file_path: Path, content: str) -> bool:
        """Write content to file only if it doesn't already exist.

        This is a synchronous method intended to be called inside
        ``anyio.to_thread.run_sync()``.

        Args:
            file_path: Absolute path to the file.
            content: Text content to write.

        Returns:
            True if the file was written, False if it already existed.
        """
        if not file_path.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return True
        return False

    # ── Folder structure creation ────────────────────────────────────────

    async def create_folder_structure(self, workspace_path: str) -> None:
        """Create the minimal folder structure for the workspace.

        Creates Knowledge/ and Projects/ directories, six Knowledge
        subdirectories (Notes, Reports, Meetings, Library, Archives,
        DailyActivity), and .gitignore.
        Context files are managed by ContextDirectoryLoader separately.
        """
        if not self.validate_path(workspace_path):
            raise ValueError(f"Invalid workspace path: '{workspace_path}'")

        expanded_path = self.expand_path(workspace_path)
        root = Path(expanded_path)

        await anyio.to_thread.run_sync(
            lambda: root.mkdir(parents=True, exist_ok=True)
        )

        for folder_name in FOLDER_STRUCTURE:
            folder_path = root / folder_name
            try:
                await anyio.to_thread.run_sync(
                    lambda fp=folder_path: fp.mkdir(parents=True, exist_ok=True)
                )
            except (FileExistsError, NotADirectoryError):
                # On case-insensitive filesystems (macOS APFS), a file with a
                # case-variant name blocks mkdir.  Skip rather than crash.
                logger.warning(
                    "Cannot create folder '%s' — path blocked by existing file",
                    folder_name,
                )

        # Create default Knowledge subdirectories
        for subdir in KNOWLEDGE_SUBDIRS:
            subdir_path = root / "Knowledge" / subdir
            try:
                await anyio.to_thread.run_sync(
                    lambda sp=subdir_path: sp.mkdir(parents=True, exist_ok=True)
                )
            except (FileExistsError, NotADirectoryError):
                logger.warning(
                    "Cannot create Knowledge/%s — path blocked by existing file",
                    subdir,
                )

        # Write .gitignore
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            await anyio.to_thread.run_sync(
                lambda: gitignore.write_text(GITIGNORE_CONTENT, encoding="utf-8")
            )

        # Provision the default SwarmAI project with DDD structure
        await self._ensure_default_project(root)

        # Auto-generate PROJECTS.md index from Projects/ scan
        await self.refresh_projects_index(expanded_path)

        # Auto-generate Knowledge Index section of KNOWLEDGE.md
        await self.refresh_knowledge_index(expanded_path)

        # Symlink AGENTS.md from codebase to SwarmWS root (shared AI context)
        await anyio.to_thread.run_sync(lambda: self._sync_agents_md(root))

        logger.info("Created folder structure at %s", expanded_path)

    # ── Default project provisioning ─────────────────────────────────────

    async def _ensure_default_project(self, root: Path) -> None:
        """Provision the default SwarmAI project with DDD structure.

        Creates ``Projects/SwarmAI/`` with PRODUCT.md, TECH.md,
        IMPROVEMENT.md, PROJECT.md, and ``.artifacts/manifest.json``.
        Only writes files that don't already exist (preserves user edits).
        Called during ``create_folder_structure`` and ``verify_integrity``.
        """
        project_dir = root / "Projects" / DEFAULT_PROJECT_NAME

        def _provision():
            project_dir.mkdir(parents=True, exist_ok=True)

            # Write DDD docs (only if missing — user edits are preserved)
            for filename, content in SWARMAI_PROJECT_DDD.items():
                filepath = project_dir / filename
                if not filepath.exists():
                    filepath.write_text(content, encoding="utf-8")

            # Ensure .artifacts/ with manifest.json
            artifacts_dir = project_dir / ".artifacts"
            artifacts_dir.mkdir(exist_ok=True)
            manifest = artifacts_dir / "manifest.json"
            if not manifest.exists():
                manifest.write_text(json.dumps({
                    "project": DEFAULT_PROJECT_NAME,
                    "pipeline_state": "evaluate",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "artifacts": [],
                }, indent=2), encoding="utf-8")

            # Ensure decision-strategy.json for ROI triage
            strategy = project_dir / "decision-strategy.json"
            if not strategy.exists():
                strategy.write_text(json.dumps({
                    "project": DEFAULT_PROJECT_NAME,
                    "weights": {
                        "strategic_alignment": 0.35,
                        "current_priority": 0.25,
                        "historical_leverage": 0.15,
                        "inverse_feasibility": 0.25,
                    },
                    "thresholds": {"go": 3.5, "defer": 2.0},
                    "calibration_history": [],
                }, indent=2), encoding="utf-8")

            # Ensure .project.json metadata (for project CRUD compatibility)
            project_meta = project_dir / ".project.json"
            if not project_meta.exists():
                now = datetime.now(timezone.utc).isoformat()
                meta = {
                    "id": "swarmai-default",
                    "name": DEFAULT_PROJECT_NAME,
                    "description": "SwarmAI self-building project (default, not deletable)",
                    "created_at": now,
                    "updated_at": now,
                    "status": "active",
                    "tags": ["default", "self-building"],
                    "priority": "high",
                    "schema_version": CURRENT_SCHEMA_VERSION,
                    "ddd_spec_version": DDD_SPEC_VERSION,
                    "version": 1,
                    "update_history": [{
                        "version": 1, "timestamp": now,
                        "action": "created", "changes": {},
                        "source": "system",
                    }],
                }
                project_meta.write_text(
                    json.dumps(meta, indent=2), encoding="utf-8"
                )

        await anyio.to_thread.run_sync(_provision)
        logger.info("Ensured default project '%s' at %s", DEFAULT_PROJECT_NAME, project_dir)

    async def provision_project_ddd(
        self, project_name: str, workspace_path: str = None,
        internal: bool = False,
    ) -> list[str]:
        """Create DDD document templates + six-section skeleton for a project.

        Writes the ② 4 docs, ① manifests, ③④ section dirs, ⑥ marker, and COPIES
        the 5 default DDD-native skills into ``skills/``.  Only writes files that
        don't already exist (preserves user edits).

        Args:
            project_name: Name of the project (must already exist under Projects/).
            workspace_path: Workspace root.  If None, uses default.
            internal: If True, this DDD is bound to an internal Brazil/CRUX repo
                (e.g. AIDLC) — ALSO copy the internal toolchain skills
                (s_internal-brazil/crux-cr/crux-review) + the no_git_push gate.

        Returns:
            List of filenames that were created (empty list if all existed).

        Raises:
            ValueError: If project directory doesn't exist.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)
        project_dir = Path(workspace_path) / "Projects" / project_name

        if not project_dir.exists():
            raise ValueError(f"Project directory not found: {project_dir}")

        def _create_ddd():
            created = []
            for filename, template in DDD_TEMPLATES.items():
                filepath = project_dir / filename
                if not filepath.exists():
                    filepath.write_text(
                        template.replace("{project_name}", project_name),
                        encoding="utf-8",
                    )
                    created.append(filename)

            # Six-section skeleton (①⑥): scaffold ① identity manifests
            # (aim.json/AGENTS.md/.crux_template.md) + ⑥ REFRESHER.md, only-if-
            # absent (idempotent — never clobbers hand-authored content;
            # re-provision is a safe no-op). ② is the 4 docs above; ③④ dirs are
            # created below (SECTION_DIRS); ⑤ bindings.yaml is by BIND, not here.
            for relpath, template in SECTION_SCAFFOLD.items():
                target = project_dir / relpath
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(
                        template.replace("{project_name}", project_name),
                        encoding="utf-8",
                    )
                    created.append(relpath)

            # ③④ section DIRECTORIES (SECTION_DIRS): a flat file-map cannot
            # materialize an empty dir (mkdir only fires on a file write), so
            # create them explicitly with a .gitkeep marker (NOT a prose README —
            # D2: AGENTS.md is the single README). Content accretes: real
            # gates/skills fill these later; the empty dir keeps the skeleton
            # legible. only-if-absent → idempotent.
            for reldir in SECTION_DIRS:
                keep = project_dir / reldir / ".gitkeep"
                if not keep.exists():
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    keep.write_text("", encoding="utf-8")
                    created.append(f"{reldir}/.gitkeep")

            # ④ COPY the 5 default DDD-native skills into skills/ (only-if-absent).
            # This is the fix for "aim.json declared names but no skill existed":
            # the skills must be PHYSICALLY in the DDD so that after `aim` export
            # they run directly in Kiro / Claude Code. Source of truth is the
            # official maintained template set (backend/templates/ddd-skills/).
            skills_root = project_dir / "skills"
            for skill_name, files in DDD_NATIVE_SKILL_TEMPLATES.items():
                for relpath, content in files.items():
                    target = skills_root / skill_name / relpath
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(content, encoding="utf-8")
                        created.append(f"skills/{skill_name}/{relpath}")

            # INTERNAL DDD (Brazil/CRUX-bound, e.g. AIDLC): ALSO copy the internal
            # toolchain skills + the no_git_push ③ gate. These are copied from the
            # SwarmAI-native backend/skills/ (already-portable HITL wrappers) +
            # the gate from an internal reference. only-if-absent.
            if internal:
                import shutil
                native_skills_src = Path(__file__).parent.parent / "skills"
                for skill_name in INTERNAL_DDD_SKILLS:
                    src = native_skills_src / skill_name
                    dst = skills_root / skill_name
                    if src.is_dir() and not dst.exists():
                        # ignore build/cache junk so a DDD never ships bytecode
                        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                            "__pycache__", "*.pyc", ".DS_Store"))
                        created.append(f"skills/{skill_name}/ (internal)")
                # ③ gate: no_git_push (+ test) — copy from the bundled internal
                # gate reference if present; the gate is pure-stdlib + portable.
                gate_src_dir = Path(__file__).parent.parent / "templates" / "ddd-gates"
                for gate_file in ("no_git_push.py", "test_no_git_push.py"):
                    gsrc = gate_src_dir / gate_file
                    gdst = project_dir / "gates" / gate_file
                    if gsrc.exists() and not gdst.exists():
                        gdst.parent.mkdir(parents=True, exist_ok=True)
                        gdst.write_text(gsrc.read_text(encoding="utf-8"), encoding="utf-8")
                        created.append(f"gates/{gate_file} (internal)")

            # Ensure .artifacts/ with manifest.json
            artifacts_dir = project_dir / ".artifacts"
            artifacts_dir.mkdir(exist_ok=True)
            manifest = artifacts_dir / "manifest.json"
            if not manifest.exists():
                manifest.write_text(json.dumps({
                    "project": project_name,
                    "pipeline_state": "evaluate",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "artifacts": [],
                }, indent=2), encoding="utf-8")
                created.append(".artifacts/manifest.json")

            # Ensure decision-strategy.json for ROI triage weights
            strategy = project_dir / "decision-strategy.json"
            if not strategy.exists():
                strategy.write_text(json.dumps({
                    "project": project_name,
                    "weights": {
                        "strategic_alignment": 0.35,
                        "current_priority": 0.25,
                        "historical_leverage": 0.15,
                        "inverse_feasibility": 0.25,
                    },
                    "thresholds": {
                        "go": 3.5,
                        "defer": 2.0,
                    },
                    "calibration_history": [],
                }, indent=2), encoding="utf-8")
                created.append("decision-strategy.json")

            return created

        created = await anyio.to_thread.run_sync(_create_ddd)
        if created:
            logger.info(
                "Created DDD docs for project '%s': %s",
                project_name, ", ".join(created),
            )
        return created

    async def migrate_project_to_six_section(
        self, project_name: str, workspace_path: str = None,
        internal: bool = False,
    ) -> dict:
        """Backfill an EXISTING project to the six-section canonical structure.

        ``internal=True`` also copies the internal Brazil/CRUX toolchain skills +
        the no_git_push gate (for a repo-bound internal DDD like AIDLC).

        Idempotent + non-destructive. For a project that predates this scaffold
        (e.g. AIDLC — created before the metadata system, so it has no
        ``.project.json`` and is invisible to ``list_projects``):

          1. Write ``.project.json`` DIRECTLY (only-if-absent) with a DELIBERATE,
             stable id — NEVER via ``create_project`` (that mints a fresh uuid and
             raises on the name-collision guard). A non-uuid id is valid (cf. the
             shipped ``swarmai-default``); the ``<name>-`` prefix avoids any future
             uuid4 collision.
          2. **Prune legacy over-build** — a project scaffolded by the PRIOR
             (over-built) version has ``agents/README.md`` / ``agent-sops/README.md``
             and per-section README stubs that the corrected structure no longer
             uses (D1/D2). ``provision_project_ddd`` is only-if-absent so it would
             NEVER remove them — an explicit, SURGICAL prune of the known-legacy set
             is required (Gate-1 B1). It touches ONLY that closed set; ② docs,
             ``Knowledge/``, ⑤ ``bindings.yaml``, ③④ real content are never touched.
          3. Call ``provision_project_ddd`` to fill the ①⑥ skeleton + ③④ dirs — every
             write is only-if-absent, so hand-authored content is preserved byte-for-byte.

        Returns ``{"metadata_created": bool, "pruned": [...], "scaffolded": [...], "id": str}``.
        Other projects lacking ``.project.json`` can be backfilled the same way
        (safe idempotent re-run) — done per-project on demand, not big-bang.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)
        project_dir = Path(workspace_path) / "Projects" / project_name
        if not project_dir.exists():
            raise ValueError(f"Project directory not found: {project_dir}")

        def _ensure_metadata() -> bool:
            meta_file = project_dir / ".project.json"
            if meta_file.exists():
                return False
            now = datetime.now(timezone.utc).isoformat()
            metadata = {
                # Deliberate stable id (NOT uuid4) — safe per _rebuild_uuid_index
                # (string→path map, no uuid parsing) and mirrors "swarmai-default".
                "id": f"{project_name.lower()}-ddd",
                "name": project_name,
                "description": "",
                "created_at": now,
                "updated_at": now,
                "status": "active",
                "tags": [],
                "priority": None,
                "schema_version": CURRENT_SCHEMA_VERSION,
                "ddd_spec_version": DDD_SPEC_VERSION,
                "version": 1,
                "update_history": [{
                    "version": 1, "timestamp": now,
                    "action": "created", "changes": {},
                    "source": "migration",
                }],
            }
            self._write_project_metadata(project_dir, metadata)
            self._uuid_index[metadata["id"]] = project_dir
            return True

        def _prune_legacy_scaffold() -> list[str]:
            """Remove ONLY prior-version over-build artifacts that are still the
            SHIPPED legacy stub — never human-authored content (Gate-2 CRITICAL,
            2026-07-12: an unconditional unlink of gates/README.md silently deleted
            a human-written README). Two safety gates:
              • per-section README: unlink ONLY if its content is CONTENT-GATED to
                the known legacy stub (a signature substring the old template
                emitted). A human-edited README diverges → NOT the stub → KEPT.
              • agents/agent-sops dir: rmtree ONLY if it holds nothing but ignorable
                dotfiles (.gitkeep/.DS_Store). A .gitkeep alone must NOT block
                pruning (Gate-2 HIGH: else migrate leaves a D1-violating half-state),
                but any REAL file (an agent-spec, a SOP) → KEPT for human review."""
            import shutil
            pruned: list[str] = []
            kept: list[str] = []
            # A legacy README stub is identifiable by the marker phrases the OLD
            # SECTION_SCAFFOLD template emitted. If the file no longer contains its
            # marker, a human rewrote it → it is NOT a stub → never delete.
            _LEGACY_README_MARKERS = {
                "gates/README.md": "Gates — executable judgment (the moat)",
                "gates/context/includes/README.md": "Gate denylist data",
                "skills/README.md": "Capabilities — skills",
                "agents/README.md": "Capabilities — agent specs",
                "agent-sops/README.md": "Capabilities — agent SOPs",
            }
            _ACCRETION_MARKER = "ACCRETES"  # every legacy stub carried this word
            for rel, marker in _LEGACY_README_MARKERS.items():
                p = project_dir / rel
                if not (p.exists() and p.is_file()):
                    continue
                body = p.read_text(encoding="utf-8", errors="replace")
                if marker in body and _ACCRETION_MARKER in body:
                    p.unlink()
                    pruned.append(rel)
                else:
                    kept.append(rel)  # human-edited — never delete
                    logger.warning(
                        "migrate: %s/%s kept — content diverges from the legacy "
                        "stub (looks human-authored); not pruned", project_name, rel,
                    )
            # legacy AIM-export-form dirs that must NOT be SwarmWS-native (D1).
            # rmtree ONLY if nothing but ignorable dotfiles remain — a .gitkeep must
            # not create a permanent half-state, but a real agent-spec/SOP is kept.
            _IGNORABLE = {".gitkeep", ".DS_Store"}
            for reldir in ("agents", "agent-sops"):
                d = project_dir / reldir
                if not d.is_dir():
                    continue
                real_files = [
                    x for x in d.rglob("*")
                    if x.is_file() and x.name not in _IGNORABLE
                ]
                if not real_files:
                    shutil.rmtree(d)
                    pruned.append(f"{reldir}/")
                else:
                    kept.append(f"{reldir}/")
                    logger.warning(
                        "migrate: %s/%s not pruned — contains %d real file(s), "
                        "left for human review", project_name, reldir, len(real_files),
                    )
            return pruned

        metadata_created = await anyio.to_thread.run_sync(_ensure_metadata)
        pruned = await anyio.to_thread.run_sync(_prune_legacy_scaffold)
        scaffolded = await self.provision_project_ddd(project_name, workspace_path, internal=internal)
        await self.refresh_projects_index(workspace_path)
        logger.info(
            "Migrated project '%s' to six-section structure (metadata_created=%s, pruned=%d, scaffolded=%d items)",
            project_name, metadata_created, len(pruned), len(scaffolded),
        )
        return {
            "metadata_created": metadata_created,
            "pruned": pruned,
            "scaffolded": scaffolded,
            "id": f"{project_name.lower()}-ddd",
        }

    async def sync_internal_provisioning(
        self, project_name: str, workspace_path: str = None,
    ) -> dict:
        """Reconcile a DDD's ④ skills/③ gates to its CURRENT class — call at BIND time.

        The single-source-of-truth for a DDD's class is `classify_project` (derived on
        read from bindings.yaml — NOT a stored flag, which would drift). This closes the
        historical gap where `internal` was a create-time param that no caller ever set,
        so internal DDDs never got the s_internal-* toolchain + no_git_push gate.

        Trigger: after a binding is written (BIND step in s_ddd-manager), call this. If
        the project now classifies `internal` (any binding is kind:internal), it copies
        the internal skills+gate (idempotent — provision is only-if-absent, so re-bind /
        already-internal is a safe no-op). An external or no-repo project is a no-op.

        Returns {"classification": <none|external|internal>, "provisioned": [...]}.
        """
        from core.ddd_bindings import classify_project

        ws = Path(workspace_path) if workspace_path else get_swarmws()
        project_dir = ws / "Projects" / project_name
        classification = classify_project(project_dir)
        provisioned: list[str] = []
        if classification == "internal":
            # Idempotent: provision_project_ddd's internal copy is not-if-absent.
            provisioned = await self.provision_project_ddd(
                project_name, workspace_path, internal=True,
            )
        return {"classification": classification, "provisioned": provisioned}

    # ── TECH.md auto-population from codebase scan ─────────────────────

    async def scan_and_populate_tech(
        self,
        project_name: str,
        codebase_path: str,
        workspace_path: str = None,
    ) -> dict:
        """Scan a codebase directory and populate TECH.md with detected info.

        Detects: language, framework, test runner, dev commands, git remote.
        Only fills in sections that are still at template placeholder values.

        Args:
            project_name: Name of existing project.
            codebase_path: Absolute path to the codebase directory.
            workspace_path: Workspace root. If None, uses default.

        Returns:
            dict with detected info: {language, framework, test_cmd, dev_cmd, git_remote}
        """
        workspace_path = self._resolve_workspace_path(workspace_path)
        project_dir = Path(workspace_path) / "Projects" / project_name
        tech_path = project_dir / "TECH.md"

        if not project_dir.is_dir():
            raise ValueError(f"Project '{project_name}' not found")

        cb = Path(codebase_path).expanduser().resolve()
        if not cb.is_dir():
            raise ValueError(f"Codebase path not found: {codebase_path}")

        def _scan():
            detected = {
                "codebase_path": str(cb),
                "language": None,
                "framework": None,
                "test_cmd": None,
                "dev_cmd": None,
                "build_cmd": None,
                "git_remote": None,
            }

            # Detect from config files
            if (cb / "pyproject.toml").exists():
                detected["language"] = "Python"
                detected["test_cmd"] = "pytest"
                toml_text = (cb / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
                if "fastapi" in toml_text.lower():
                    detected["framework"] = "FastAPI"
                elif "django" in toml_text.lower():
                    detected["framework"] = "Django"
                elif "flask" in toml_text.lower():
                    detected["framework"] = "Flask"

            if (cb / "package.json").exists():
                try:
                    pkg = json.loads((cb / "package.json").read_text(encoding="utf-8"))
                    scripts = pkg.get("scripts", {})
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

                    if not detected["language"]:
                        detected["language"] = "TypeScript" if "typescript" in deps else "JavaScript"

                    if "next" in deps:
                        detected["framework"] = "Next.js"
                    elif "react" in deps:
                        detected["framework"] = (detected["framework"] or "") + " + React" if detected["framework"] else "React"
                    elif "vue" in deps:
                        detected["framework"] = "Vue.js"

                    if "vitest" in deps:
                        detected["test_cmd"] = detected["test_cmd"] or "npx vitest run"
                    elif "jest" in deps:
                        detected["test_cmd"] = detected["test_cmd"] or "npx jest"

                    if "dev" in scripts:
                        detected["dev_cmd"] = scripts["dev"]
                    if "build" in scripts:
                        detected["build_cmd"] = scripts["build"]
                except (json.JSONDecodeError, OSError):
                    pass

            if (cb / "Cargo.toml").exists():
                detected["language"] = detected["language"] or "Rust"
                detected["test_cmd"] = detected["test_cmd"] or "cargo test"
                detected["build_cmd"] = "cargo build"

            if (cb / "go.mod").exists():
                detected["language"] = detected["language"] or "Go"
                detected["test_cmd"] = detected["test_cmd"] or "go test ./..."
                detected["build_cmd"] = "go build"

            # Git remote
            git_config = cb / ".git" / "config"
            if git_config.exists():
                try:
                    for line in git_config.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line.startswith("url = "):
                            detected["git_remote"] = line[6:].strip()
                            break
                except OSError:
                    pass

            return detected

        detected = await anyio.to_thread.run_sync(_scan)

        # Update TECH.md if it exists and has placeholder content
        if tech_path.exists():
            def _update_tech():
                content = tech_path.read_text(encoding="utf-8")
                modified = False

                # Only update sections that still have placeholder text
                # Replace codebase placeholder (multiple possible variants)
                codebase_placeholders = [
                    "_Absolute path or repo URL to the project's source code._",
                    "_Set this to your local SwarmAI source path after cloning._",
                    "_Set this to your project's source path after cloning._",
                    "_Set this to your local SwarmAI source path, e.g.: /path/to/swarmai/_",
                    "_Set this to your project's source path._",
                ]
                if detected["codebase_path"]:
                    path_line = detected["codebase_path"]
                    if detected.get("git_remote"):
                        path_line += f"\n- **Git:** {detected['git_remote']}"
                    for placeholder in codebase_placeholders:
                        if placeholder in content:
                            content = content.replace(placeholder, path_line)
                            modified = True
                            break

                if ("_e.g., Python" in content or "_e.g., FastAPI" in content) and detected.get("language"):
                    lang = detected["language"]
                    fw = detected.get("framework", "")
                    test = detected.get("test_cmd", "")
                    content = content.replace("_e.g., Python 3.12, TypeScript 5_", lang)
                    content = content.replace("_e.g., FastAPI, Next.js_", fw or "_not detected_")
                    content = content.replace("_e.g., SQLite, PostgreSQL_", "_not detected_")
                    content = content.replace("_e.g., pytest, vitest_", test or "_not detected_")
                    modified = True

                if modified:
                    tech_path.write_text(content, encoding="utf-8")
                return modified

            updated = await anyio.to_thread.run_sync(_update_tech)
            if updated:
                logger.info("Auto-populated TECH.md for '%s' from %s", project_name, cb)

        return detected

    # ── PROJECTS.md auto-refresh ──────────────────────────────────────

    async def refresh_projects_index(self, workspace_path: str) -> None:
        """Regenerate ``.context/PROJECTS.md`` by scanning ``Projects/``.

        Produces a lightweight index with one entry per project.  Each entry
        shows: name, pipeline state, DDD doc status, and a reference link
        to the project's folder.  The detailed context lives in each
        project's own DDD documents — PROJECTS.md is just the directory.

        Called automatically during ``verify_integrity()`` and after
        project CRUD operations.
        """
        root = Path(workspace_path)
        projects_dir = root / "Projects"
        context_file = root / ".context" / "PROJECTS.md"

        def _generate():
            entries = []
            if not projects_dir.exists():
                return entries
            for candidate in sorted(projects_dir.iterdir()):
                if not candidate.is_dir() or candidate.name.startswith("."):
                    continue
                name = candidate.name

                # Detect project type
                is_default = name == DEFAULT_PROJECT_NAME
                project_type = "Default" if is_default else "User"

                # Read pipeline state from manifest
                manifest = candidate / ".artifacts" / "manifest.json"
                pipeline_state = "-"
                if manifest.exists():
                    try:
                        data = json.loads(manifest.read_text(encoding="utf-8"))
                        pipeline_state = data.get("pipeline_state", "-")
                    except (json.JSONDecodeError, OSError):
                        pass

                # Check which DDD docs exist + sizes for progressive loading
                # F3: 30K threshold — files under 30K are small enough to read fully.
                # Only 60K+ docs truly benefit from section-level loading.
                _LARGE_DOC_THRESHOLD = 30_000  # bytes
                ddd_docs = []
                large_docs_toc: list[dict] = []  # [{doc, size_kb, sections}]
                for doc in DDD_CANONICAL_DOCS:
                    doc_path = candidate / doc
                    if doc_path.exists():
                        doc_size = doc_path.stat().st_size
                        ddd_docs.append(doc.replace(".md", ""))
                        # For large docs, extract section headings with line ranges
                        if doc_size > _LARGE_DOC_THRESHOLD:
                            sections = []
                            try:
                                lines_text = doc_path.read_text(encoding="utf-8").splitlines()
                                total_lines = len(lines_text)
                                for i, line in enumerate(lines_text, 1):
                                    if line.startswith("## ") and not line.startswith("### "):
                                        sections.append((line[3:].strip(), i))
                                # F1: compute end line for each section (next section start - 1)
                                sections_with_range = []
                                for idx, (sec_name, sec_start) in enumerate(sections):
                                    sec_end = (sections[idx + 1][1] - 1
                                               if idx + 1 < len(sections)
                                               else total_lines)
                                    sections_with_range.append(
                                        (sec_name, sec_start, sec_end)
                                    )
                                sections = sections_with_range
                            except OSError:
                                pass
                            large_docs_toc.append({
                                "doc": doc,
                                "size_kb": round(doc_size / 1024),
                                "sections": sections,
                            })
                ddd_status = ", ".join(ddd_docs) if ddd_docs else "none"

                # Read one-line vision from PRODUCT.md (cleaned)
                vision = extract_clean_description(candidate / "PRODUCT.md")

                entries.append({
                    "name": name,
                    "type": project_type,
                    "pipeline": pipeline_state,
                    "ddd": ddd_status,
                    "vision": vision,
                    "large_docs_toc": large_docs_toc,
                })
            return entries

        entries = await anyio.to_thread.run_sync(_generate)

        # Build Entity Index from DDD docs
        projects_dir_path = root / "Projects"
        entity_index_lines: list[str] = []
        if projects_dir_path.exists():
            raw_entities = await anyio.to_thread.run_sync(
                lambda: extract_entities_from_ddd(projects_dir_path)
            )
            if raw_entities:
                formatted = format_entity_index(raw_entities)
                entity_index_lines = prune_entity_index(formatted)

        # Build PROJECTS.md content
        lines = [
            "# Projects -- What's In Flight",
            "",
            "**AGENT DIRECTIVE:** When working on a task related to any project "
            "below, READ the project's DDD documents BEFORE starting work. "
            "Use `Projects/<name>/TECH.md` for coding/testing, "
            "`PRODUCT.md` for design decisions, `IMPROVEMENT.md` for lessons, "
            "`PROJECT.md` for current context. Determine the active project "
            "from file paths being edited, user mentions, or chat thread "
            "binding. No project context? Skip this -- everything works "
            "without DDD. After completing work, UPDATE IMPROVEMENT.md "
            "with lessons and PROJECT.md with decisions/status.\n"
            "\n"
            "**Progressive loading:** For docs marked as \"Large\" below, "
            "DON'T read the full file. Instead: (1) scan the section TOC shown below, "
            "(2) Read only the section(s) relevant to your current task "
            "(use offset/limit with the line numbers provided), "
            "(3) max 3 section pulls per task. "
            "Never infer from a section heading what the content says — pull it if deciding.",
            "",
        ]

        # Inject Entity Index section (before Active Projects table)
        # L5 fix: only inject if there are actual data rows (not just header)
        if entity_index_lines and len(entity_index_lines) > 6:
            lines.extend(entity_index_lines)
            lines.append("")
            lines.append("---")
            lines.append("")

        if entries:
            lines.append("## Active Projects")
            lines.append("")
            lines.append("| Project | Type | Pipeline | DDD Docs |")
            lines.append("|---------|------|----------|----------|")
            for e in entries:
                lines.append(
                    f"| **{e['name']}** | {e['type']} | {e['pipeline']} "
                    f"| {e['ddd']} |"
                )
            lines.append("")

            # Detailed entries with references
            for e in entries:
                lines.append(f"### {e['name']}")
                if e["vision"]:
                    lines.append(f"_{e['vision']}_")
                lines.append(f"- **Pipeline:** {e['pipeline']}")
                lines.append(f"- **DDD:** {e['ddd']}")
                lines.append(
                    f"- **Context:** `Projects/{e['name']}/` "
                    f"-- read PRODUCT.md, TECH.md, IMPROVEMENT.md, PROJECT.md"
                )

                # T6: Progressive loading — section TOC for large docs
                if e.get("large_docs_toc"):
                    lines.append("")
                    lines.append(
                        "**Large docs (read sections on demand — "
                        "don't load the full file):**"
                    )
                    for ld in e["large_docs_toc"]:
                        # F1: show start line + end line so agent can use offset/limit
                        sections_str = ", ".join(
                            f"`{s[0]}` (L{s[1]}-L{s[2]})"
                            if len(s) == 3
                            else f"`{s[0]}` (L{s[1]})"
                            for s in ld["sections"]
                        )
                        lines.append(
                            f"- {ld['doc']} ({ld['size_kb']}K): "
                            f"{sections_str}"
                        )

                lines.append("")
        else:
            lines.append("_No projects yet. Tell Swarm: \"Create project MyApp\"_")
            lines.append("")

        lines.append("## Project Management")
        lines.append("")
        lines.append("- **Create:** \"Create project X\" or \"New project X at /path\"")
        lines.append("- **Edit:** Edit DDD docs directly or ask Swarm to update them")
        lines.append("- **Delete:** \"Remove project X\" (SwarmAI project cannot be deleted)")
        lines.append("- **Guide:** See `Projects/README.md` for DDD structure and usage")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("_Auto-refreshed on startup and after project changes._")
        lines.append("")

        content = "\n".join(lines)

        async with _get_cultivation_lock():
            def _write():
                context_file.parent.mkdir(parents=True, exist_ok=True)
                context_file.write_text(content, encoding="utf-8")

            await anyio.to_thread.run_sync(_write)

        entity_count = len(entity_index_lines) - 6 if entity_index_lines else 0  # subtract header lines
        logger.info(
            "Refreshed PROJECTS.md with %d projects, %d entity index entries",
            len(entries),
            max(entity_count, 0),
        )

    async def refresh_knowledge_index(self, workspace_path: str) -> None:
        """Regenerate the Knowledge Index section of ``.context/KNOWLEDGE.md``.

        Scans ``Knowledge/`` subdirectories (Designs, Notes, Reports,
        Meetings, Library, Handoffs, Learned, Pollinate) for markdown files.  Extracts
        the ``title`` from YAML frontmatter or the first ``# heading``,
        and rebuilds the index tables.

        Preserves everything above the ``## Knowledge Index`` marker
        (hand-written architecture docs, code reference, etc.).

        Called automatically during ``verify_integrity()`` and after
        knowledge file operations.
        """
        root = Path(workspace_path)
        context_file = root / ".context" / "KNOWLEDGE.md"

        # Marker that separates hand-written content from auto-generated index
        INDEX_MARKER = "## Knowledge Index"

        def _extract_topic(filepath: Path) -> str:
            """Extract topic from frontmatter title or first heading."""
            try:
                text = filepath.read_text(encoding="utf-8")
                lines = text.split("\n", 30)  # Only scan first 30 lines

                # Check YAML frontmatter
                if lines and lines[0].strip() == "---":
                    for line in lines[1:]:
                        if line.strip() == "---":
                            break
                        if line.startswith("title:"):
                            title = line[6:].strip().strip('"').strip("'")
                            return title

                # Fallback: first # heading
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("# ") and not stripped.startswith("# <"):
                        return stripped[2:].strip()

                return filepath.stem
            except (OSError, UnicodeDecodeError):
                return filepath.stem

        def _extract_date(filepath: Path) -> str:
            """Extract date from filename (YYYY-MM-DD prefix) or frontmatter."""
            name = filepath.stem
            # Try YYYY-MM-DD prefix
            if len(name) >= 10 and name[4] == "-" and name[7] == "-":
                return name[:10]
            # Try frontmatter
            try:
                text = filepath.read_text(encoding="utf-8")
                lines = text.split("\n", 20)
                if lines and lines[0].strip() == "---":
                    for line in lines[1:]:
                        if line.strip() == "---":
                            break
                        if line.startswith("date:"):
                            return line[5:].strip().strip('"')[:10]
            except (OSError, UnicodeDecodeError):
                pass
            return "unknown"

        def _scan_section(section_dir: Path, path_prefix: str) -> list[dict]:
            """Scan a directory for .md files and extract metadata."""
            if not section_dir.exists():
                return []
            entries = []
            for f in sorted(section_dir.glob("*.md")):
                if f.name.startswith(".") or f.name.startswith("_"):
                    continue
                entries.append({
                    "date": _extract_date(f),
                    "file": f"`{path_prefix}{f.name}`",
                    "topic": _extract_topic(f),
                })
            return sorted(entries, key=lambda e: e["date"])

        def _build_index() -> str:
            """Build the Knowledge Index section content.

            Three-tier format:
            - COMPACT: DailyActivity, JobResults, Signals → count + pattern only
            - HOT/COLD: dirs with >10 files → recent 10 + cold summary
            - FULL: dirs with ≤10 files → complete listing
            """
            knowledge_dir = root / "Knowledge"

            _COMPACT_DIRS = {"DailyActivity", "JobResults", "Signals"}
            _HOT_COLD_THRESHOLD = 10
            _HOT_ENTRIES = 10

            # Define sections to scan: (display name, directory, path prefix)
            sections = [
                ("DailyActivity", knowledge_dir / "DailyActivity", "Knowledge/DailyActivity/"),
                ("DailyBriefs", knowledge_dir / "DailyBriefs", "Knowledge/DailyBriefs/"),
                ("Designs", knowledge_dir / "Designs", "Knowledge/Designs/"),
                ("Handoffs", knowledge_dir / "Handoffs", "Knowledge/Handoffs/"),
                ("JobResults", knowledge_dir / "JobResults", "Knowledge/JobResults/"),
                ("Learned", knowledge_dir / "Learned", "Knowledge/Learned/"),
                ("Library", knowledge_dir / "Library", "Knowledge/Library/"),
                ("Meetings", knowledge_dir / "Meetings", "Knowledge/Meetings/"),
                ("Notes", knowledge_dir / "Notes", "Knowledge/Notes/"),
                ("Reports", knowledge_dir / "Reports", "Knowledge/Reports/"),
                ("Signals", knowledge_dir / "Signals", "Knowledge/Signals/"),
            ]

            lines = [INDEX_MARKER, ""]
            total_files = 0

            for section_name, section_dir, path_prefix in sections:
                entries = _scan_section(section_dir, path_prefix)
                if not entries:
                    continue
                total_files += len(entries)

                # Tier 1: COMPACT — summary only
                if section_name in _COMPACT_DIRS:
                    first_date = entries[0]["date"]
                    last_date = entries[-1]["date"]
                    lines.append(f"### {section_name}")
                    lines.append("")
                    lines.append(
                        f"{len(entries)} files from {first_date} to {last_date}. "
                        f"Pattern: `{path_prefix}YYYY-MM-DD-*.md`. Read on demand."
                    )
                    lines.append("")
                    continue

                # Tier 2: HOT/COLD — recent 10 + cold summary
                if len(entries) > _HOT_COLD_THRESHOLD:
                    hot_entries = entries[-_HOT_ENTRIES:]
                    cold_count = len(entries) - _HOT_ENTRIES
                    lines.append(f"### {section_name}")
                    lines.append("")
                    lines.append(
                        f"_{len(entries)} total, showing {_HOT_ENTRIES} most recent. "
                        f"{cold_count} older files available via workspace-finder/Glob._"
                    )
                    lines.append("")
                    lines.append("| Date | File | Topic |")
                    lines.append("|------|------|-------|")
                    for e in hot_entries:
                        lines.append(f"| {e['date']} | {e['file']} | {e['topic']} |")
                    lines.append("")
                    continue

                # Tier 3: FULL — complete listing
                lines.append(f"### {section_name}")
                lines.append("")
                lines.append("| Date | File | Topic |")
                lines.append("|------|------|-------|")
                for e in entries:
                    lines.append(f"| {e['date']} | {e['file']} | {e['topic']} |")
                lines.append("")

            if total_files == 0:
                lines.append("_No knowledge files yet. Save notes, designs, and reports to Knowledge/._")
                lines.append("")

            lines.append("---")
            lines.append("")
            lines.append("_Auto-refreshed on startup from Knowledge/ directories._")
            lines.append("")

            return "\n".join(lines), total_files

        def _generate():
            # Read existing KNOWLEDGE.md
            if not context_file.exists():
                return None, 0

            existing = context_file.read_text(encoding="utf-8")

            # Find the marker — everything before it is preserved
            marker_pos = existing.find(INDEX_MARKER)
            if marker_pos == -1:
                # No marker found — append at the end
                preserved = existing.rstrip() + "\n\n"
            else:
                preserved = existing[:marker_pos]

            index_content, total = _build_index()
            return preserved + index_content, total

        # The read-modify-write (generate reads existing KNOWLEDGE.md to preserve
        # everything above the index marker; write replaces it) must be ATOMIC
        # under the shared KNOWLEDGE.md.lock — every KNOWLEDGE.md writer holds it
        # (run_a1ec08e7), else this provisioning-time index refresh could clobber a
        # concurrent decay-strip (context_health_hook._run_knowledge_lifecycle) and
        # resurrect stripped entries. Hold the lock across BOTH generate + write in
        # one worker thread (blocking — mirror the section-refresh writers).
        from utils.file_lock import md_lock

        def _locked_rmw():
            with md_lock(context_file, blocking=True):
                content, total = _generate()
                if content is None:
                    return None
                context_file.parent.mkdir(parents=True, exist_ok=True)
                context_file.write_text(content, encoding="utf-8")
                return total

        total = await anyio.to_thread.run_sync(_locked_rmw)
        if total is None:
            logger.debug("KNOWLEDGE.md not found, skipping index refresh")
            return
        logger.info("Refreshed KNOWLEDGE.md index with %d files", total)

    # ── Git initialization ─────────────────────────────────────────────

    def _ensure_git_repo(self, workspace_path: str) -> bool:
        """Initialize git repo in SwarmWS if not already initialized.

        Writes .gitignore BEFORE git add to prevent committing sensitive files.
        Returns True if git is available, False otherwise.
        """
        git_dir = Path(workspace_path) / ".git"
        if git_dir.exists():
            return True
        try:
            # .gitignore should already exist from create_folder_structure,
            # but ensure it's there before git add
            gitignore = Path(workspace_path) / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(GITIGNORE_CONTENT, encoding="utf-8")
            subprocess.run(
                ["git", "init"], cwd=workspace_path,
                capture_output=True, check=True, timeout=30,
            )
            subprocess.run(
                ["git", "add", "-A"], cwd=workspace_path,
                capture_output=True, timeout=30,
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial SwarmWS state", "--allow-empty"],
                cwd=workspace_path, capture_output=True, timeout=30,
            )
            logger.info("Git repo initialized at %s", workspace_path)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            logger.warning("Git init failed (non-blocking): %s", exc)
            return False

    # ── Context reading (backward compat) ────────────────────────────────

    async def read_context_files(self, workspace_path: str) -> str:
        """Read and combine context files from a workspace.

        Reads the following files from the ContextFiles subdirectory for
        backward compatibility:
        - context.md: Main workspace context template
        - compressed-context.md: Compressed context for long-term memory

        The contents are combined with a separator between them.
        Missing files are handled gracefully.

        Args:
            workspace_path: Path to the workspace root directory.

        Returns:
            Combined content of both context files as a single string.
            Returns empty string if both files are missing or unreadable.
        """
        expanded_path = self.expand_path(workspace_path)
        context_dir = Path(expanded_path) / "ContextFiles"

        contents = []

        context_path = context_dir / "context.md"
        try:
            context_content = await anyio.to_thread.run_sync(
                lambda: context_path.read_text(encoding="utf-8")
                if context_path.exists()
                else ""
            )
            if context_content:
                contents.append(context_content)
        except Exception as e:
            logger.warning("Failed to read context.md: %s", e)

        compressed_context_path = context_dir / "compressed-context.md"
        try:
            compressed_content = await anyio.to_thread.run_sync(
                lambda: compressed_context_path.read_text(encoding="utf-8")
                if compressed_context_path.exists()
                else ""
            )
            if compressed_content:
                contents.append(compressed_content)
        except Exception as e:
            logger.warning("Failed to read compressed-context.md: %s", e)

        return "\n\n---\n\n".join(contents) if contents else ""

    # ── Workspace lifecycle ──────────────────────────────────────────────

    async def ensure_default_workspace(self, db) -> dict:
        """Ensure the default SwarmWS workspace exists, creating it if necessary.

        If no workspace_config row exists, inserts one, creates the folder
        structure on disk, and populates sample data for first-time users.
        If a row already exists, runs ``verify_integrity()`` to heal any
        missing system-managed items.

        **Startup ordering guarantee (Req 24.4, 1.7):**
        Legacy data cleanup runs BEFORE this method is called.  The cleanup
        lives in ``SQLiteDatabase._run_migrations()`` which executes during
        ``db.initialize()`` in the app lifespan.  ``main.py`` calls
        ``initialize_database()`` first, then
        ``initialization_manager.run_full_initialization()`` which invokes
        this method.  Therefore the ``swarm_workspaces`` table (if it
        existed) has already been dropped and legacy workspace directories
        removed by the time we reach here, ensuring a clean-slate init.

        Args:
            db: Database instance with ``db.workspace_config`` accessor
                providing ``get_config()`` and ``put()`` methods.

        Returns:
            dict with workspace configuration (id, name, file_path, icon,
            created_at, updated_at).
        """
        existing = await db.workspace_config.get_config()

        if existing:
            logger.info("Default workspace config already exists, verifying integrity")
            file_path = existing.get("file_path", DEFAULT_WORKSPACE_CONFIG["file_path"])
            expanded = self.expand_path(file_path)
            await self._cleanup_legacy_content(expanded)
            await self.verify_integrity(expanded)
            return existing

        logger.info("Creating default workspace for the first time")

        now = datetime.now(timezone.utc).isoformat()
        config = {
            "id": "swarmws",
            "name": DEFAULT_WORKSPACE_CONFIG["name"],
            "file_path": DEFAULT_WORKSPACE_CONFIG["file_path"],
            "icon": DEFAULT_WORKSPACE_CONFIG["icon"],
            "created_at": now,
            "updated_at": now,
        }

        # Persist to database
        await db.workspace_config.put(config)
        logger.info("Inserted workspace config with id: %s", config['id'])

        # Create folder structure on disk
        try:
            await self.create_folder_structure(config["file_path"])
            logger.info("Created folder structure at %s", config['file_path'])
        except Exception as e:
            logger.error(
                "Failed to create folder structure for default workspace: %s", e
            )
            raise

        # Initialize git repo (non-blocking if git not available)
        expanded = self.expand_path(config["file_path"])
        self._ensure_git_repo(expanded)

        return config

    async def _cleanup_legacy_content(self, workspace_path: str) -> None:
        """Remove legacy files and folders from pre-restructure SwarmWS.

        Runs once per startup on existing workspaces.  Idempotent — safe to
        call repeatedly.  Uses a marker file (``.legacy_cleaned``) to skip on
        subsequent startups once all legacy content has been cleaned.

        All filesystem removals are batched into a **single**
        ``anyio.to_thread.run_sync()`` call via :func:`_batch_remove` to
        avoid dispatching one thread call per item.

        Migrates:
        - Legacy ``Knowledge Base/`` → ``Library/`` (preserves user files)

        Removes:
        - Legacy Knowledge subdirectories (Memory)
        - Legacy root files (context-L0.md, context-L1.md, system-prompts.md,
          index.md, knowledge-map.md)
        - Legacy per-project context files (context-L0.md, context-L1.md)
        - Legacy root directories (chats/, _tmp_transfer/, ContextFiles/, workspace/)
        """
        root = Path(workspace_path)

        # Skip if already cleaned (marker file exists)
        marker = root / ".legacy_cleaned"
        if marker.exists():
            return

        # ── Migrate "Knowledge Base" → "Library" (preserve user files) ───
        legacy_kb = root / "Knowledge" / "Knowledge Base"
        new_library = root / "Knowledge" / "Library"
        if legacy_kb.exists():
            def _migrate_kb_to_library() -> None:
                new_library.mkdir(parents=True, exist_ok=True)
                for item in legacy_kb.iterdir():
                    dest = new_library / item.name
                    if not dest.exists():
                        shutil.move(str(item), str(dest))
                # Remove empty legacy dir
                if not any(legacy_kb.iterdir()):
                    legacy_kb.rmdir()

            await anyio.to_thread.run_sync(_migrate_kb_to_library)
            logger.info("Migrated Knowledge Base/ → Library/")

        # ── Collect ALL legacy paths into a single list ──────────────────
        paths_to_remove: list[tuple[Path, str]] = []

        # Legacy Knowledge subdirectories
        legacy_knowledge_dirs = ["Memory"]
        for dirname in legacy_knowledge_dirs:
            legacy_dir = root / "Knowledge" / dirname
            if legacy_dir.exists():
                paths_to_remove.append((legacy_dir, "dir"))

        # Legacy root-level files
        legacy_root_files = [
            "context-L0.md", "context-L1.md", "system-prompts.md",
            "index.md", "knowledge-map.md", "generate_ppt.py",
            "SwarmAI_Capabilities.pptx", "gen_news_pdf.py",
        ]
        for filename in legacy_root_files:
            legacy_file = root / filename
            if legacy_file.exists():
                paths_to_remove.append((legacy_file, "file"))

        # Legacy Knowledge-level files
        legacy_knowledge_files = [
            "context-L0.md", "context-L1.md", "index.md", "knowledge-map.md",
        ]
        for filename in legacy_knowledge_files:
            legacy_file = root / "Knowledge" / filename
            if legacy_file.exists():
                paths_to_remove.append((legacy_file, "file"))

        # Legacy per-project context files
        projects_dir = root / "Projects"
        if projects_dir.exists():
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                for filename in ["context-L0.md", "context-L1.md"]:
                    legacy_file = project_dir / filename
                    if legacy_file.exists():
                        paths_to_remove.append((legacy_file, "file"))

        # Legacy root-level directories
        legacy_root_dirs = [
            "_tmp_transfer", "ContextFiles", "workspace", "chats",
        ]
        for dirname in legacy_root_dirs:
            legacy_dir = root / dirname
            if legacy_dir.exists():
                paths_to_remove.append((legacy_dir, "dir"))

        # ── Single batch removal in one thread dispatch ──────────────────
        if paths_to_remove:
            errors = await anyio.to_thread.run_sync(
                lambda: _batch_remove(paths_to_remove)
            )
            # Log successful removals
            for path, kind in paths_to_remove:
                rel = path.relative_to(root)
                if not any(str(path) in err for err in errors):
                    logger.info("Removed legacy %s: %s", kind, rel)
            # Log any per-item failures
            for err in errors:
                logger.warning("Legacy cleanup error: %s", err)

        # Mark cleanup as done so we skip on future startups
        try:
            marker.write_text("done")
        except OSError:
            pass  # Non-critical — cleanup will just re-run next time

    async def verify_integrity(self, workspace_path: str) -> bool:
        """Verify Knowledge/, Projects/, and all six Knowledge subdirs exist, recreating if missing.

        Checks Notes, Reports, Meetings, Library, Archives, DailyActivity
        under Knowledge/ and recreates any that are missing without modifying
        existing ones.  Also prunes archived DailyActivity files older than
        90 days (Req 7.6, 15.11).

        Returns True if any folder was recreated.
        """
        root = Path(workspace_path)
        recreated = False
        for folder in FOLDER_STRUCTURE:
            p = root / folder
            if not p.exists():
                await anyio.to_thread.run_sync(
                    lambda fp=p: fp.mkdir(parents=True, exist_ok=True)
                )
                recreated = True
                logger.info("Recreated missing folder: %s", folder)
        for subdir in KNOWLEDGE_SUBDIRS:
            p = root / "Knowledge" / subdir
            if not p.exists():
                await anyio.to_thread.run_sync(
                    lambda fp=p: fp.mkdir(parents=True, exist_ok=True)
                )
                recreated = True
                logger.info("Recreated missing folder: Knowledge/%s", subdir)

        # Ensure .gitignore has required entries (migration for existing workspaces)
        gitignore = root / ".gitignore"
        if gitignore.exists():
            try:
                content = gitignore.read_text(encoding="utf-8")
                missing_entries = []
                for entry in ["proactive_state.json", "hook_stats.json", "*.tmp"]:
                    if entry not in content:
                        missing_entries.append(entry)
                if missing_entries:
                    append_text = "\n".join(missing_entries) + "\n"
                    if not content.endswith("\n"):
                        append_text = "\n" + append_text

                    def _append_gitignore(text: str = append_text) -> None:
                        with gitignore.open("a", encoding="utf-8") as fh:
                            fh.write(text)

                    await anyio.to_thread.run_sync(_append_gitignore)
                    logger.info("Appended missing .gitignore entries: %s", missing_entries)
            except OSError as exc:
                logger.warning("Failed to update .gitignore: %s", exc)

        # Ensure default SwarmAI project exists with DDD structure
        await self._ensure_default_project(root)

        # Auto-refresh PROJECTS.md from scanning Projects/
        await self.refresh_projects_index(str(root))

        # Auto-refresh Knowledge Index in KNOWLEDGE.md
        await self.refresh_knowledge_index(str(root))

        # Provision job system default config
        await anyio.to_thread.run_sync(lambda: self._provision_job_system(root))

        # Symlink AGENTS.md from codebase to SwarmWS root (shared AI context)
        await anyio.to_thread.run_sync(lambda: self._sync_agents_md(root))

        # Auto-prune old archived DailyActivity files (Req 7.6, 15.11)
        expanded = str(root)
        await anyio.to_thread.run_sync(lambda: self.prune_archives(expanded))

        return recreated

    @staticmethod
    def _sync_agents_md(root: Path) -> None:
        """Symlink AGENTS.md from the codebase into SwarmWS root.

        AGENTS.md is the single source of truth for AI coding assistant
        context — read by Claude Code, Kiro, Cursor, and others.  The
        canonical copy lives in the swarmai repo root; the SwarmWS symlink
        ensures the agent (running inside SwarmWS) can also read it.
        Refreshes automatically on every startup via verify_integrity().

        CLAUDE.md in the repo root is a pointer file that redirects to
        AGENTS.md — no separate symlink needed.
        """
        try:
            # Locate codebase root by walking up until we find VERSION + .git
            # (more robust than hardcoded parent depth).
            candidate = Path(__file__).resolve().parent
            src = None
            for _ in range(5):  # max 5 levels up
                candidate = candidate.parent
                if (candidate / "VERSION").exists() and (candidate / ".git").exists():
                    src = candidate / "AGENTS.md"
                    break

            if src is None or not src.exists():
                logger.debug("AGENTS.md not found in codebase ancestry — skipping symlink")
                return

            dst = root / "AGENTS.md"

            # Recreate symlink if target changed or is stale
            if dst.is_symlink():
                if dst.resolve() == src.resolve():
                    return  # already correct
                dst.unlink()
            elif dst.exists():
                # User-edited regular file — check if content differs
                try:
                    if dst.read_text(encoding="utf-8") != src.read_text(encoding="utf-8"):
                        logger.info(
                            "AGENTS.md in SwarmWS has user edits — preserving "
                            "(not replacing with symlink)"
                        )
                        return
                except OSError:
                    pass
                # Content is identical to source — safe to replace with symlink
                dst.unlink()

            dst.symlink_to(src)
            logger.debug("AGENTS.md symlinked: %s -> %s", dst, src)
        except OSError as exc:
            logger.warning("Failed to sync AGENTS.md: %s", exc)

    def _provision_job_system(self, root: Path) -> None:
        """Ensure Services/swarm-jobs/ has required config files.

        Creates default config.yaml (feed definitions) and empty
        user-jobs.yaml if they don't exist. State, logs, and signal
        directories are also ensured. System job definitions live in
        backend/jobs/system_jobs.py (code, not YAML).
        """
        jobs_dir = root / "Services" / "swarm-jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        (jobs_dir / "logs").mkdir(exist_ok=True)

        # Ensure signals directory
        signals_dir = root / "Services" / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)

        # Default config.yaml (feed definitions) — only create if missing
        config_file = jobs_dir / "config.yaml"
        if not config_file.exists():
            config_file.write_text(_DEFAULT_JOB_CONFIG, encoding="utf-8")
            logger.info("Provisioned default job config: %s", config_file)

        # Empty user-jobs.yaml
        user_jobs_file = jobs_dir / "user-jobs.yaml"
        if not user_jobs_file.exists():
            user_jobs_file.write_text(
                "# User-defined scheduled jobs (managed via chat or s_job-manager skill)\n"
                "jobs: []\n",
                encoding="utf-8",
            )
            logger.info("Provisioned empty user-jobs: %s", user_jobs_file)

        # Empty state.json
        state_file = jobs_dir / "state.json"
        if not state_file.exists():
            state_file.write_text("{}", encoding="utf-8")

        # Remove legacy external scheduler plist (macOS only).
        # The scheduler now runs in-process inside the daemon — the external
        # launchd job is no longer needed and its path fragility caused the
        # 5/7-5/11 outage (venv rebuild broke baked Python path).
        import sys
        if sys.platform == "darwin":
            self._remove_legacy_scheduler_plist()

    def _remove_legacy_scheduler_plist(self) -> None:
        """Remove the legacy external scheduler launchd plist if present.

        Since v1.13, the job scheduler runs inside the daemon process (asyncio
        timer loop). The external plist is dead weight with 5 failure modes:
        1. Path fragility (venv rebuild breaks baked Python path)
        2. No catch-up after sleep (StartCalendarInterval doesn't retry)
        3. Silent failure (KeepAlive=false, no alerting)
        4. Dual management (dev/daemon both install)
        5. No monitoring (nobody checks "has it run recently?")
        """
        try:
            from jobs.install_scheduler import LAUNCH_AGENTS, NEW_LABEL, _uid

            dest = LAUNCH_AGENTS / f"{NEW_LABEL}.plist"
            if not dest.exists():
                return

            # Bootout (unregister from launchd) then delete file
            uid = _uid()
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}/{NEW_LABEL}"],
                capture_output=True, timeout=10,
            )
            dest.unlink(missing_ok=True)
            logger.info("Removed legacy scheduler plist: %s", dest)

        except Exception as e:
            # Never block startup
            logger.warning("Failed to remove legacy scheduler plist: %s", e)

    def prune_archives(self, workspace_path: str, max_age_days: int = 90) -> int:
        """Delete archived DailyActivity files older than *max_age_days*.

        Scans ``Knowledge/Archives/`` for markdown files whose stem is a
        valid ISO-8601 date (``YYYY-MM-DD``).  Files with a date older than
        the cutoff are removed.  Non-date filenames and IO errors are
        silently skipped so that manually-placed files are never touched.

        This is a synchronous helper designed to be called from
        ``verify_integrity()`` (via ``anyio.to_thread.run_sync``) or
        directly during workspace maintenance.

        Args:
            workspace_path: Expanded absolute path to the workspace root.
            max_age_days: Number of days to retain archived files.
                Defaults to 90.

        Returns:
            Number of files successfully deleted.
        """
        archives_dir = Path(workspace_path) / "Knowledge" / "Archives"
        if not archives_dir.is_dir():
            return 0

        cutoff = date.today() - timedelta(days=max_age_days)
        deleted = 0

        for f in archives_dir.iterdir():
            if not f.is_file() or f.suffix != ".md":
                continue
            try:
                file_date = date.fromisoformat(f.stem)
            except ValueError:
                continue  # Not a date-formatted filename — skip
            if file_date < cutoff:
                try:
                    f.unlink()
                    deleted += 1
                    logger.debug("Pruned archived file: %s", f.name)
                except OSError as exc:
                    logger.warning("Failed to prune %s: %s", f.name, exc)

        if deleted:
            logger.info("Pruned %d archived file(s) older than %d days", deleted, max_age_days)
        return deleted


    def get_workspace_path(self) -> str:
        """Return the expanded absolute default workspace root path.

        Convenience wrapper around :meth:`_resolve_workspace_path` for
        callers that always want the default workspace (no DB lookup needed).
        """
        return self._resolve_workspace_path(None)

    def _resolve_workspace_path(self, workspace_path: Optional[str]) -> str:
        """Resolve workspace_path to an expanded absolute path.

        Args:
            workspace_path: Workspace root path, or None to use default.

        Returns:
            Expanded absolute path string.
        """
        if workspace_path is None:
            return self.expand_path(DEFAULT_WORKSPACE_CONFIG["file_path"])
        return self.expand_path(workspace_path)

    @staticmethod
    def _scan_all_project_metadata(projects_dir: Path) -> list[tuple[Path, dict]]:
        """Scan Projects/ directory and read all .project.json files.

        Synchronous method intended for use inside ``anyio.to_thread.run_sync()``.

        Args:
            projects_dir: Absolute path to the Projects/ directory.

        Returns:
            List of (project_dir, metadata_dict) tuples for valid projects.
        """
        results = []
        if not projects_dir.exists():
            return results
        for candidate in projects_dir.iterdir():
            if not candidate.is_dir():
                continue
            meta_file = candidate / ".project.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                results.append((candidate, meta))
            except (json.JSONDecodeError, OSError):
                logger.warning(
                    "Skipping project with invalid .project.json: %s", candidate.name
                )
        return results

    # ── Private helper methods (Cadence 2) ─────────────────────────────

    def _read_project_metadata(self, project_dir: Path) -> dict:
        """Read ``.project.json`` from a single project directory, applying migration.

        Reads the JSON file, calls ``migrate_if_needed()`` from the schema
        migrations module, and writes back if a migration was applied.

        This is a synchronous method intended for use inside
        ``anyio.to_thread.run_sync()``.

        Args:
            project_dir: Absolute path to the project directory.

        Returns:
            Parsed (and possibly migrated) metadata dict.

        Raises:
            FileNotFoundError: If ``.project.json`` does not exist.
            json.JSONDecodeError: If the file contains invalid JSON.
        """
        meta_file = project_dir / ".project.json"
        raw = json.loads(meta_file.read_text(encoding="utf-8"))
        migrated, was_migrated = migrate_if_needed(raw)
        if was_migrated:
            self._write_project_metadata(project_dir, migrated)
        return migrated

    @staticmethod
    def _write_project_metadata(project_dir: Path, metadata: dict) -> None:
        """Serialize metadata to ``.project.json`` with 2-space indent.

        This is a synchronous method intended for use inside
        ``anyio.to_thread.run_sync()``.

        Args:
            project_dir: Absolute path to the project directory.
            metadata: The metadata dict to write.
        """
        meta_file = project_dir / ".project.json"
        meta_file.write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )

    def _find_project_dir(self, project_id: str, workspace_path: str) -> Path:
        """Look up a project directory by UUID.

        First checks the in-memory ``_uuid_index``.  On a cache miss, falls
        back to a full ``Projects/`` scan via ``_rebuild_uuid_index`` and
        retries.  Raises ``ValueError`` if the project is not found.

        This is a synchronous method intended for use inside
        ``anyio.to_thread.run_sync()``.

        Args:
            project_id: The UUID of the project.
            workspace_path: Expanded absolute workspace root path.

        Returns:
            Absolute ``Path`` to the project directory.

        Raises:
            ValueError: If no project with the given UUID exists.
        """
        # Fast path: check in-memory index
        if project_id in self._uuid_index:
            cached = self._uuid_index[project_id]
            if cached.exists() and (cached / ".project.json").exists():
                return cached

        # Cache miss or stale entry — rebuild index and retry
        self._rebuild_uuid_index(workspace_path)

        if project_id in self._uuid_index:
            return self._uuid_index[project_id]

        raise ValueError(f"Project not found with id: {project_id}")

    @staticmethod
    def _compute_action_type(changes: dict) -> str:
        """Determine the history action type from a changes dict.

        Uses a priority mapping — first match wins:
        ``name`` → ``renamed``, ``status`` → ``status_changed``,
        ``tags`` → ``tags_modified``, ``priority`` → ``priority_changed``,
        otherwise → ``updated``.

        Args:
            changes: Dict of field names that were changed.

        Returns:
            Action type string for the history entry.
        """
        if "name" in changes:
            return "renamed"
        if "status" in changes:
            return "status_changed"
        if "tags" in changes:
            return "tags_modified"
        if "priority" in changes:
            return "priority_changed"
        return "updated"

    @staticmethod
    def _compute_changes_diff(old: dict, new_updates: dict) -> dict:
        """Compute a diff of changed fields between old metadata and new updates.

        Only includes fields whose values actually differ.

        Args:
            old: The current metadata dict.
            new_updates: Dict of field→new_value to apply.

        Returns:
            Dict of ``{field: {"from": old_value, "to": new_value}}`` for
            fields that changed.
        """
        diff: dict = {}
        for field, new_value in new_updates.items():
            old_value = old.get(field)
            if old_value != new_value:
                diff[field] = {"from": old_value, "to": new_value}
        return diff

    @staticmethod
    def _enforce_history_cap(metadata: dict, cap: int = 50) -> None:
        """Trim ``update_history`` to the most recent *cap* entries in-place.

        Args:
            metadata: The project metadata dict (modified in-place).
            cap: Maximum number of history entries to retain.
        """
        history = metadata.get("update_history")
        if history is not None and len(history) > cap:
            metadata["update_history"] = history[-cap:]

    def _get_project_lock(self, project_id: str) -> asyncio.Lock:
        """Return (or create) the ``asyncio.Lock`` for a project UUID.

        Uses ``dict.setdefault`` for atomic insertion, avoiding a TOCTOU
        race if this code is ever called from multiple threads.

        Args:
            project_id: The UUID of the project.

        Returns:
            The ``asyncio.Lock`` associated with this project.
        """
        return self._project_locks.setdefault(project_id, asyncio.Lock())

    def _rebuild_uuid_index(self, workspace_path: str) -> None:
        """Scan ``Projects/`` subdirs and populate the in-memory UUID index.

        Reads each ``.project.json`` to extract the ``id`` field and maps
        it to the project directory path.  Replaces the entire index.

        This is a synchronous method intended for use inside
        ``anyio.to_thread.run_sync()`` or from other synchronous helpers.

        Args:
            workspace_path: Expanded absolute workspace root path.
        """
        projects_dir = Path(workspace_path) / "Projects"
        new_index: dict[str, Path] = {}
        if projects_dir.exists():
            for candidate in projects_dir.iterdir():
                if not candidate.is_dir():
                    continue
                meta_file = candidate / ".project.json"
                if not meta_file.exists():
                    continue
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    pid = meta.get("id")
                    if pid:
                        new_index[pid] = candidate
                except (json.JSONDecodeError, OSError):
                    logger.warning(
                        "Skipping project with invalid .project.json: %s",
                        candidate.name,
                    )
        self._uuid_index = new_index

    # ── Project CRUD ─────────────────────────────────────────────────────

    async def create_project(
        self, project_name: str, workspace_path: str = None, source: str = "user"
    ) -> dict:
        """Create a new project under Projects/.

        Scaffolds the SKELETON of the canonical six-section DDD structure
        (spec §3.6; option A, XG decision 2026-07-12, refined same day to remove
        the over-build): the project dir + ``.project.json`` (① identity, stamped
        with ``ddd_spec_version``), the ① manifests (``aim.json`` — declaring the
        5 default native skills — / ``AGENTS.md`` — the SINGLE unified README —
        / ``.crux_template.md``), the 4 DDD docs (② knowledge), the ⑥
        ``REFRESHER.md`` marker, and the ③④ section DIRECTORIES (``gates/``,
        ``gates/context/includes/``, ``skills/`` — created via a ``.gitkeep`` marker,
        NOT a per-section README; AGENTS.md is the one README) via
        ``provision_project_ddd``, plus ``.artifacts/`` for pipeline outputs.
        Deliberately NOT scaffolded: ``agents/`` and ``agent-sops/`` — those are
        AIM-export-form members (generated at export), not the SwarmWS-native
        skeleton. The skeleton is concrete so SwarmAI-maintenance follows the
        standard and AIM export is low-variance; section CONTENT (real gates,
        skills) ACCRETES as the project grows. Only ⑤ ``bindings.yaml`` waits — it
        is provisioned by BIND (repo shape is unknown at create). All writes are
        only-if-absent (idempotent, non-destructive).

        Args:
            project_name: Display name for the project (used as directory name).
            workspace_path: Expanded absolute workspace root path. If None,
                uses DEFAULT_WORKSPACE_CONFIG path (expanded).
            source: Who initiated the creation — "user", "agent", "system",
                or "migration". Recorded in the initial update_history entry.

        Returns:
            dict with enriched project metadata: id, name, description,
            status, tags, priority, schema_version, version, update_history,
            created_at, updated_at.

        Raises:
            ValueError: If a project with the same name already exists.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)

        project_dir = Path(workspace_path) / "Projects" / project_name

        # Validate name (length, characters, reserved names, case-insensitive collision)
        def _validate():
            self._validate_project_name(project_name, workspace_path)

        await anyio.to_thread.run_sync(_validate)

        now = datetime.now(timezone.utc).isoformat()
        project_id = str(uuid4())

        metadata = {
            "id": project_id,
            "name": project_name,
            "description": "",
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "tags": [],
            "priority": None,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "ddd_spec_version": DDD_SPEC_VERSION,
            "version": 1,
            "update_history": [
                {
                    "version": 1,
                    "timestamp": now,
                    "action": "created",
                    "changes": {},
                    "source": source,
                }
            ],
        }

        # Create project directory
        await anyio.to_thread.run_sync(
            lambda: project_dir.mkdir(parents=True, exist_ok=True)
        )

        # Write .project.json via the shared helper
        await anyio.to_thread.run_sync(
            lambda: self._write_project_metadata(project_dir, metadata)
        )

        # Create system folders
        for folder in sorted(PROJECT_SYSTEM_FOLDERS):
            folder_path = project_dir / folder
            await anyio.to_thread.run_sync(
                lambda fp=folder_path: fp.mkdir(parents=True, exist_ok=True)
            )

        # Provision DDD document templates for the new project
        await self.provision_project_ddd(project_name, workspace_path)

        # Update in-memory UUID index
        self._uuid_index[project_id] = project_dir

        # Auto-refresh PROJECTS.md index
        await self.refresh_projects_index(workspace_path)

        logger.info("Created project '%s' with id %s", project_name, project_id)
        return metadata

    async def update_project(
        self,
        project_id: str,
        updates: dict,
        source: str = "user",
        workspace_path: str = None,
    ) -> dict:
        """Update project metadata and record change in update_history.

        Acquires a per-project ``asyncio.Lock`` to serialise concurrent
        updates.  When a name change is requested, follows an atomic rename
        strategy: (1) write updated metadata to the existing directory,
        (2) rename the directory, (3) revert metadata on rename failure.

        Args:
            project_id: UUID of the project.
            updates: Dict of fields to update (name, status, tags, priority,
                description).
            source: Who initiated the change — ``"user"``, ``"agent"``,
                ``"system"``, or ``"migration"``.
            workspace_path: Workspace root. If None, uses default.

        Returns:
            Updated project metadata dict.

        Raises:
            ValueError: If project not found, name invalid, or name conflict
                on rename.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)
        lock = self._get_project_lock(project_id)

        async with lock:
            # ── Read current metadata (sync, inside lock) ────────────
            def _read():
                project_dir = self._find_project_dir(project_id, workspace_path)
                metadata = self._read_project_metadata(project_dir)
                return project_dir, metadata

            project_dir, metadata = await anyio.to_thread.run_sync(_read)

            # ── Compute diff ─────────────────────────────────────────
            changes = self._compute_changes_diff(metadata, updates)
            if not changes:
                # Nothing actually changed — return as-is
                return metadata

            # ── Validate new name if renaming ────────────────────────
            new_name = updates.get("name")
            old_name = metadata.get("name")
            renaming = new_name is not None and new_name != old_name

            if renaming:
                # Block renaming the default SwarmAI project
                if old_name == DEFAULT_PROJECT_NAME or project_id == "swarmai-default":
                    raise ValueError(
                        f"The '{DEFAULT_PROJECT_NAME}' project cannot be renamed. "
                        "You can edit its DDD documents freely."
                    )
                self._validate_project_name(new_name, workspace_path, exclude_dir=project_dir.name)

            # Save original for revert on rename failure
            original_metadata = copy.deepcopy(metadata) if renaming else None

            # ── Apply updates to metadata ────────────────────────────
            now = datetime.now(timezone.utc).isoformat()
            for field, new_value in updates.items():
                if field in changes:
                    metadata[field] = new_value

            metadata["version"] = metadata.get("version", 1) + 1
            metadata["updated_at"] = now

            # ── Append history entry ─────────────────────────────────
            action = self._compute_action_type(changes)
            history_entry = {
                "version": metadata["version"],
                "timestamp": now,
                "action": action,
                "changes": changes,
                "source": source,
            }
            if "update_history" not in metadata:
                metadata["update_history"] = []
            metadata["update_history"].append(history_entry)
            self._enforce_history_cap(metadata)

            # ── Write & optionally rename ────────────────────────────
            if renaming:
                await self._atomic_rename_project(
                    project_id, project_dir, metadata, new_name,
                    old_name, workspace_path, original_metadata,
                )
            else:
                await anyio.to_thread.run_sync(
                    lambda: self._write_project_metadata(project_dir, metadata)
                )

        return metadata

    async def _atomic_rename_project(
        self,
        project_id: str,
        project_dir: Path,
        metadata: dict,
        new_name: str,
        old_name: str,
        workspace_path: str,
        original_metadata: dict,
    ) -> None:
        """Perform an atomic rename: write metadata, rename dir, revert on failure.

        Args:
            project_id: UUID of the project.
            project_dir: Current project directory path.
            metadata: Updated metadata dict (already has new name).
            new_name: The new project name.
            old_name: The previous project name.
            workspace_path: Expanded workspace root path.
            original_metadata: Snapshot of metadata before any updates,
                used to fully revert on rename failure.
        """
        new_dir = Path(workspace_path) / "Projects" / new_name

        # Step 1: Write updated metadata to existing directory
        await anyio.to_thread.run_sync(
            lambda: self._write_project_metadata(project_dir, metadata)
        )

        # Step 2: Rename directory
        def _rename():
            project_dir.rename(new_dir)

        try:
            await anyio.to_thread.run_sync(_rename)
        except OSError as exc:
            # Step 3: Revert to original metadata on rename failure
            logger.error(
                "OS error renaming project '%s' → '%s': %s", old_name, new_name, exc
            )
            await anyio.to_thread.run_sync(
                lambda: self._write_project_metadata(project_dir, original_metadata)
            )
            raise ValueError(
                f"Failed to rename project directory from '{old_name}' to '{new_name}'"
            ) from exc

        # Update in-memory UUID index to point to new directory
        self._uuid_index[project_id] = new_dir

    @staticmethod
    def _validate_project_name(name: str, workspace_path: str, exclude_dir: str = None) -> None:
        """Validate a project name against naming rules.

        Checks length, allowed characters, reserved names, and
        case-insensitive collision with existing project directories.

        Args:
            name: The proposed project name.
            workspace_path: Expanded workspace root path.
            exclude_dir: Directory name to skip during collision check
                (used during rename to exclude the project's own directory).

        Raises:
            ValueError: If the name is invalid or collides with an existing
                project.
        """
        # Strip leading/trailing whitespace
        stripped = name.strip()
        if stripped != name:
            raise ValueError(
                "Project name must not have leading or trailing whitespace."
            )

        if not _PROJECT_NAME_RE.match(name):
            raise ValueError(
                "Project name must be 1-100 characters: alphanumeric, "
                "spaces, hyphens, underscores, or periods."
            )

        # Check reserved filesystem names (case-insensitive)
        base = name.split(".")[0].upper()
        if base in _RESERVED_NAMES:
            raise ValueError(
                f"'{name}' is a reserved filesystem name and cannot be used."
            )

        # Check case-insensitive collision with existing projects
        projects_dir = Path(workspace_path) / "Projects"
        if projects_dir.exists():
            lower_name = name.lower()
            for candidate in projects_dir.iterdir():
                if candidate.is_dir() and candidate.name.lower() == lower_name:
                    # Skip the project's own directory during rename
                    if exclude_dir and candidate.name == exclude_dir:
                        continue
                    raise ValueError(
                        f"A project named '{name}' already exists."
                    )

    async def delete_project(
        self, project_id: str, workspace_path: str = None
    ) -> bool:
        """Delete a project by UUID.

        The default SwarmAI project (id ``"swarmai-default"`` or directory
        name ``SwarmAI``) cannot be deleted — raises ``ValueError``.

        Acquires the per-project ``asyncio.Lock`` before removing the
        directory to prevent races with concurrent reads/writes.

        Args:
            project_id: The UUID of the project to delete.
            workspace_path: Workspace root path. If None, uses default.

        Returns:
            True if the project was deleted.

        Raises:
            ValueError: If no project with the given ID is found, or if
                attempting to delete the default SwarmAI project.
        """
        if project_id == "swarmai-default":
            raise ValueError(
                f"The '{DEFAULT_PROJECT_NAME}' project is the default project "
                "and cannot be deleted. You can edit its DDD documents freely."
            )

        workspace_path = self._resolve_workspace_path(workspace_path)
        lock = self._get_project_lock(project_id)

        async with lock:
            def _find_and_delete():
                project_dir = self._find_project_dir(project_id, workspace_path)
                name = project_dir.name
                # Block deletion of the default project by directory name too
                if name == DEFAULT_PROJECT_NAME:
                    raise ValueError(
                        f"The '{DEFAULT_PROJECT_NAME}' project is the default "
                        "project and cannot be deleted."
                    )
                shutil.rmtree(project_dir)
                return name

            deleted_name = await anyio.to_thread.run_sync(_find_and_delete)

        # Clean up in-memory caches (outside lock — lock object itself is being removed)
        self._uuid_index.pop(project_id, None)
        self._project_locks.pop(project_id, None)

        # Auto-refresh PROJECTS.md index
        await self.refresh_projects_index(workspace_path)

        logger.info("Deleted project '%s' (id: %s)", deleted_name, project_id)
        return True

    async def get_project(
        self, project_id: str, workspace_path: str = None
    ) -> dict:
        """Get project metadata by UUID, applying schema migration on read.

        Uses ``_find_project_dir()`` for fast UUID lookup and
        ``_read_project_metadata()`` which applies ``migrate_if_needed()``.
        Acquires a per-project ``asyncio.Lock`` to serialise concurrent
        reads/writes to the same ``.project.json``.

        Args:
            project_id: The UUID of the project.
            workspace_path: Workspace root path. If None, uses default.

        Returns:
            dict with project metadata (migrated to current schema version).

        Raises:
            ValueError: If no project with the given ID is found.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)
        lock = self._get_project_lock(project_id)
        async with lock:
            def _read():
                project_dir = self._find_project_dir(project_id, workspace_path)
                return self._read_project_metadata(project_dir)

            return await anyio.to_thread.run_sync(_read)

    async def list_projects(self, workspace_path: str = None) -> list[dict]:
        """List all projects with metadata, applying schema migration on read.

        Scans the Projects/ directory for subdirectories containing a
        ``.project.json`` file, reads each via ``_read_project_metadata()``
        (which applies ``migrate_if_needed()``), and returns their metadata
        sorted by ``created_at`` descending.

        Args:
            workspace_path: Workspace root path. If None, uses default.

        Returns:
            List of project metadata dicts, sorted by created_at descending.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)
        projects_dir = Path(workspace_path) / "Projects"

        def _scan_dirs():
            """Return list of project directories that contain .project.json."""
            dirs = []
            if not projects_dir.exists():
                return dirs
            for candidate in projects_dir.iterdir():
                if candidate.is_dir() and (candidate / ".project.json").exists():
                    dirs.append(candidate)
            return dirs

        project_dirs = await anyio.to_thread.run_sync(_scan_dirs)

        results = []
        for project_dir in project_dirs:
            try:
                meta = await anyio.to_thread.run_sync(
                    lambda d=project_dir: self._read_project_metadata(d)
                )
                results.append(meta)
            except (json.JSONDecodeError, OSError):
                logger.warning(
                    "Skipping project with invalid .project.json: %s",
                    project_dir.name,
                )

        results.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return results

    async def get_project_by_name(
        self, name: str, workspace_path: str = None
    ) -> dict:
        """Find a project by display name (case-insensitive directory scan).

        Scans the ``Projects/`` directory for a subdirectory whose name
        matches *name* case-insensitively, then reads and returns its
        metadata via ``_read_project_metadata()`` (which applies schema
        migration on read).

        Args:
            name: The project display name to search for.
            workspace_path: Workspace root path. If None, uses default.

        Returns:
            dict with project metadata for the matching project.

        Raises:
            ValueError: If no project with the given name is found.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)
        projects_dir = Path(workspace_path) / "Projects"

        def _find_by_name():
            if not projects_dir.exists():
                raise ValueError(f"No project found with name: {name}")
            target = name.lower()
            for candidate in projects_dir.iterdir():
                if candidate.is_dir() and candidate.name.lower() == target:
                    meta_file = candidate / ".project.json"
                    if meta_file.exists():
                        return self._read_project_metadata(candidate)
            raise ValueError(f"No project found with name: {name}")

        return await anyio.to_thread.run_sync(_find_by_name)

    async def get_project_history(
        self, project_id: str, workspace_path: str = None
    ) -> list[dict]:
        """Return the ``update_history`` array for a project.

        Locates the project by UUID via ``_find_project_dir()``, reads its
        metadata via ``_read_project_metadata()`` (applying migration if
        needed), and returns just the ``update_history`` list.

        Args:
            project_id: The UUID of the project.
            workspace_path: Workspace root path. If None, uses default.

        Returns:
            List of update_history entry dicts, most recent last.

        Raises:
            ValueError: If no project with the given ID is found.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)

        def _read_history():
            project_dir = self._find_project_dir(project_id, workspace_path)
            metadata = self._read_project_metadata(project_dir)
            return metadata.get("update_history", [])

        return await anyio.to_thread.run_sync(_read_history)




# Global instance
swarm_workspace_manager = SwarmWorkspaceManager()
