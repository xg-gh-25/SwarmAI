"""Context Health Harness — keeps SwarmAI's brain accurate and current.

Single hook, two modes:
- **Light** (every session): refresh KNOWLEDGE.md + PROJECTS.md indexes
  if workspace changed since last refresh.
- **Deep** (once per day): validate all 11 context files, check MEMORY.md
  accuracy vs git, detect DDD staleness, verify git health.

All checks are filesystem + Bedrock embedding (delta-sync).  Auto-fixes
what it can, logs what it can't.  Heavy work runs in a thread pool to
avoid blocking the asyncio event loop.  Budget: <3s light, <10s deep.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from core.initialization_manager import initialization_manager
from core.session_hooks import HookContext

logger = logging.getLogger(__name__)


class ContextHealthHook:
    """Unified context health harness.

    Registered AFTER auto-commit so it sees committed state.
    Runs light refresh every session, deep check once per calendar day.
    """

    name = "context_health"

    # Git timeout matches auto_commit_hook
    _GIT_TIMEOUT = 10

    def __init__(self) -> None:
        self._last_deep_date: Optional[str] = None
        # Track last refresh git rev to skip no-op refreshes
        self._last_refresh_rev: Optional[str] = None

    async def execute(self, context: HookContext) -> None:
        ws_path = initialization_manager.get_cached_workspace_path()
        if not ws_path:
            return

        root = Path(ws_path)
        if not root.is_dir():
            return

        # Both _light_refresh and _deep_check are sync-heavy: git
        # subprocesses (5-10s timeouts each), Bedrock embedding calls
        # (3s timeout per chunk), file I/O.  Run in thread pool so the
        # asyncio event loop stays responsive for FastAPI/SSE.
        loop = asyncio.get_running_loop()

        # ── Light: refresh indexes if workspace changed ──────────────
        await loop.run_in_executor(None, self._light_refresh, root, ws_path)

        # ── Deep: once per calendar day ──────────────────────────────
        today = date.today().isoformat()
        if self._last_deep_date != today:
            await loop.run_in_executor(None, self._deep_check, root, ws_path)
            self._last_deep_date = today

    # ------------------------------------------------------------------
    # Light refresh — every session, <2s
    # ------------------------------------------------------------------

    def _light_refresh(self, root: Path, ws_path: str) -> None:
        """Refresh KNOWLEDGE.md index, MEMORY.md index, and vector/FTS5 stores."""
        # Memory usage tracking — scan recent DailyActivity for memory key
        # references ([RC04], [KD05], etc.) and write counts to
        # .context/.memory-usage.json.  Used by distillation for smart
        # eviction (lowest-usage entries evicted first instead of oldest).
        try:
            self._track_memory_usage(root)
        except Exception as exc:
            logger.debug("context_health: memory usage tracking skipped: %s", exc)

        # Memory index regen runs unconditionally — it's <10ms and must
        # catch uncommitted MEMORY.md writes (Edit tool, locked_write)
        # that happen within the same git rev.
        try:
            self._refresh_memory_index(root)
        except Exception as exc:
            logger.warning("context_health: MEMORY.md index refresh failed: %s", exc)

        # KNOWLEDGE.md text index refresh is git-gated (only reads git-tracked files)
        current_rev = self._git_rev(ws_path)
        if not (current_rev and current_rev == self._last_refresh_rev):
            try:
                self._refresh_knowledge_sync(root)
            except Exception as exc:
                logger.warning("context_health: KNOWLEDGE.md refresh failed: %s", exc)
            self._last_refresh_rev = current_rev

        # Knowledge Library + Transcript vector/FTS5 indexing runs OUTSIDE
        # the git-rev gate.  These stores have their own delta-sync via
        # content_hash — unchanged files are skipped cheaply (~50ms for
        # 160 hash lookups).  Many Knowledge/ files are written by hooks
        # and jobs WITHOUT git commits (DailyActivity, JobResults, Signals),
        # so the git gate was blocking them from ever being indexed.
        # Bug: previously inside git-rev gate, only 1/160 files indexed.
        try:
            self._sync_knowledge_library(root)
        except Exception as exc:
            logger.debug("context_health: knowledge library sync skipped: %s", exc)

        # Transcript indexing (incremental, <10s) — P1 Memory Architecture v2
        try:
            self._sync_transcript_index(root)
        except Exception as exc:
            logger.debug("context_health: transcript sync skipped: %s", exc)

    def _refresh_knowledge_sync(self, root: Path) -> None:
        """Synchronous KNOWLEDGE.md index refresh — filesystem scan only."""
        knowledge_dir = root / "Knowledge"
        context_file = root / ".context" / "KNOWLEDGE.md"
        if not context_file.exists() or not knowledge_dir.is_dir():
            return

        # Scan Knowledge/ subdirs for .md files
        index_lines: list[str] = []
        subdirs = sorted(
            d for d in knowledge_dir.iterdir()
            if d.is_dir() and d.name not in {"Archives", "__pycache__"}
        )

        for subdir in subdirs:
            files = sorted(
                f for f in subdir.iterdir()
                if f.suffix == ".md" and f.is_file()
            )
            if not files:
                continue

            index_lines.append(f"\n### {subdir.name}\n")
            index_lines.append("| Date | File | Topic |")
            index_lines.append("|------|------|-------|")
            for f in files:
                # Extract date and title from filename
                name = f.stem
                date_str = name[:10] if len(name) > 10 and name[4] == "-" else "unknown"
                # Try to read first heading for topic
                topic = self._extract_title(f) or name
                index_lines.append(
                    f"| {date_str} | `{subdir.name}/{f.name}` | {topic} |"
                )

        if not index_lines:
            return

        # Replace Knowledge Index section in KNOWLEDGE.md
        try:
            content = context_file.read_text(encoding="utf-8")
            marker = "## Knowledge Index"
            if marker not in content:
                return  # No section to replace

            before = content.split(marker)[0]
            # Find the next ## section after Knowledge Index
            after_marker = content.split(marker, 1)[1]
            next_section_idx = after_marker.find("\n## ")
            if next_section_idx >= 0:
                after = after_marker[next_section_idx:]
            else:
                after = "\n\n---\n\n_Auto-refreshed on startup from Knowledge/ directories._\n"

            new_content = before + marker + "\n" + "\n".join(index_lines) + "\n" + after
            context_file.write_text(new_content, encoding="utf-8")
        except Exception as exc:
            logger.warning("context_health: KNOWLEDGE.md refresh failed: %s", exc)

    def _track_memory_usage(self, root: Path) -> None:
        """Scan recent DailyActivity for memory key references.

        Finds patterns like ``[RC04]``, ``[KD05]``, ``[LL07]``, ``[COE02]``
        in DailyActivity files from the last 7 days and writes cumulative
        counts to ``.context/.memory-usage.json``.

        Distillation reads this file to decide eviction order: entries with
        zero usage are evicted first when section caps are exceeded, forming
        the compound loop: use → track → evict unused → memory improves.
        """
        daily_dir = root / "Knowledge" / "DailyActivity"
        if not daily_dir.is_dir():
            return

        cutoff = (date.today() - timedelta(days=7)).isoformat()
        usage: dict[str, int] = {}
        _KEY_RE = re.compile(r"\[([A-Z]{2,3}\d{2,3})\]")

        for f in sorted(daily_dir.glob("*.md"), reverse=True):
            if f.stem < cutoff:
                break
            try:
                body = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for key in _KEY_RE.findall(body):
                usage[key] = usage.get(key, 0) + 1

        usage_path = root / ".context" / ".memory-usage.json"
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        usage_path.write_text(json.dumps(usage, indent=2), encoding="utf-8")

    def _refresh_memory_index(self, root: Path) -> None:
        """Regenerate the compact index block in MEMORY.md.

        Called after every session to keep the index in sync with MEMORY.md
        content, regardless of how it was written (Edit tool, locked_write,
        direct I/O, weekly job).  This is the single reliable regeneration
        point — all write paths converge here.
        """
        memory_file = root / ".context" / "MEMORY.md"
        if not memory_file.exists():
            return

        try:
            from core.memory_index import inject_index_into_memory
        except ImportError:
            return  # Module not yet available (first startup)

        # Use flock to avoid racing with locked_write.py (skills, distillation)
        from utils.file_lock import flock_exclusive, flock_unlock
        lock_path = memory_file.with_suffix(".md.lock")
        lock_fd = None
        try:
            lock_fd = open(lock_path, "w")  # noqa: SIM115
            flock_exclusive(lock_fd)
        except OSError:
            if lock_fd:
                lock_fd.close()
            logger.debug("context_health: MEMORY.md lock failed, skipping index regen")
            return

        try:
            content = memory_file.read_text(encoding="utf-8")
            updated = inject_index_into_memory(content)
            if updated != content:
                memory_file.write_text(updated, encoding="utf-8")
                logger.info("context_health: MEMORY.md index regenerated")

            # Sync memory embeddings for hybrid retrieval (delta — only changed entries)
            self._sync_memory_embeddings(content)
        finally:
            flock_unlock(lock_fd)
            lock_fd.close()

    def _sync_memory_embeddings(self, memory_content: str) -> None:
        """Delta-sync MEMORY.md entries into sqlite-vec for hybrid retrieval.

        Always-on: keeps memory_entries indexed regardless of MEMORY.md size.
        Power-first (KD03) — infrastructure stays warm so selective injection
        has zero cold-start when MEMORY.md grows, and vector search is
        available for user recall queries even in full-injection mode.

        Only re-embeds entries whose content changed (via content_hash).
        Failures are silent — hybrid retrieval degrades to keyword-only.
        """
        try:
            from core.memory_embeddings import MemoryEmbeddingStore
            from core.embedding_client import EmbeddingClient
            from core.vec_db import open_vec_db

            with open_vec_db() as conn:
                if conn is None:
                    return

                store = MemoryEmbeddingStore(conn)
                store.ensure_tables()

                client = EmbeddingClient()

                def _safe_embed(text: str) -> list[float] | None:
                    """Embed text, returning None (not []) on failure.

                    Returning None causes sync_from_memory to skip the
                    vector upsert — no garbage zero-vectors in the index.
                    """
                    return client.embed_text(text)

                stats = store.sync_from_memory(
                    memory_content,
                    embed_fn=_safe_embed,
                )

            if stats["embedded"] > 0:
                logger.info(
                    "context_health: memory embeddings synced — "
                    "%d embedded, %d skipped, %d removed",
                    stats["embedded"], stats["skipped"], stats["removed"],
                )
        except Exception as exc:
            logger.debug("context_health: memory embedding sync skipped: %s", exc)

    def _sync_knowledge_library(self, root: Path) -> None:
        """Incremental sync of Knowledge/ files into FTS5 + sqlite-vec.

        Scans Knowledge/ for new/changed .md files, chunks them, and
        delta-syncs into knowledge_chunks + knowledge_fts + knowledge_vec.
        Typical: 1-3 file changes, <5s. First full index: ~100s.

        Failures are silent — recall engine degrades gracefully.
        """
        knowledge_dir = root / "Knowledge"
        if not knowledge_dir.is_dir():
            return

        from core.knowledge_store import KnowledgeStore, sync_knowledge_index
        from core.embedding_client import EmbeddingClient
        from core.vec_db import open_vec_db

        with open_vec_db() as conn:
            if conn is None:
                logger.debug("context_health: sqlite-vec not available, skipping library sync")
                return

            store = KnowledgeStore(conn)
            store.ensure_tables()

            # Create embedding function (graceful fallback if Bedrock unavailable)
            client = EmbeddingClient()

            def _safe_embed(text: str) -> list[float] | None:
                return client.embed_text(text)

            stats = sync_knowledge_index(store, knowledge_dir, embed_fn=_safe_embed)

        if stats.get("chunks_added", 0) > 0 or stats.get("files_removed", 0) > 0:
            logger.info(
                "context_health: knowledge library synced — "
                "%d files scanned, %d chunks added, %d skipped, %d removed",
                stats["files_scanned"], stats["chunks_added"],
                stats["chunks_skipped"], stats["files_removed"],
            )

    def _sync_transcript_index(self, root: Path) -> None:
        """Incremental sync of JSONL transcripts into FTS5 + sqlite-vec.

        Indexes Claude Code session transcripts for verbatim recall via
        the Recall Engine (Memory Architecture v2, Phase 5 / P1).
        MemPalace benchmark: raw verbatim scores 96.6% vs 84.2% for summaries.

        Follows the same pattern as _sync_knowledge_library: open vec DB,
        create store, embed, sync. Failures are silent.
        """
        from core.transcript_indexer import TranscriptStore, sync_transcript_index
        from core.embedding_client import EmbeddingClient
        from core.vec_db import open_vec_db

        # Derive transcript dir from the authoritative workspace path
        # (initialization_manager — always set at startup) rather than
        # config.json (which may not have workspace_path yet on first run).
        #
        # NEVER fall back to scanning ~/.claude/projects/ base dir — it
        # contains dirs with "Desktop" in the path, triggering macOS TCC
        # "would like to access Desktop" permission popups.
        base = Path.home() / ".claude" / "projects"
        transcripts_dir = None

        def _path_to_slug(p: str) -> str:
            """Convert a filesystem path to Claude SDK project slug.

            SDK format: replace / with - (keeping leading -), replace . with -.
            e.g. /Users/gawan/.swarm-ai/SwarmWS -> -Users-gawan--swarm-ai-SwarmWS
            """
            return str(Path(p).resolve()).replace("/", "-").replace(".", "-")

        # Primary: derive from initialization_manager (always available)
        ws_path = initialization_manager.get_cached_workspace_path()
        if ws_path:
            slug = _path_to_slug(ws_path)
            candidate = base / slug
            if candidate.is_dir():
                transcripts_dir = candidate

        # Secondary: also check swarmai repo path from config
        if transcripts_dir is None:
            try:
                from core.app_config_manager import app_config_manager
                if app_config_manager is not None:
                    swarmai_dir = app_config_manager.get("swarmai_dir")
                    if swarmai_dir:
                        candidate = base / _path_to_slug(swarmai_dir)
                        if candidate.is_dir():
                            transcripts_dir = candidate
            except (ImportError, Exception):
                pass

        if transcripts_dir is None:
            logger.debug(
                "context_health: no matching transcript dir found for workspace %s, "
                "skipping transcript indexing this cycle", ws_path,
            )
            return

        if not transcripts_dir.is_dir():
            return

        with open_vec_db() as conn:
            if conn is None:
                logger.debug("context_health: sqlite-vec not available, skipping transcript sync")
                return

            store = TranscriptStore(conn)
            store.ensure_tables()

            client = EmbeddingClient()

            def _safe_embed(text: str) -> list[float] | None:
                return client.embed_text(text)

            stats = sync_transcript_index(store, transcripts_dir, embed_fn=_safe_embed)

        if stats.get("files_indexed", 0) > 0:
            logger.info(
                "context_health: transcripts synced — %d indexed, %d skipped, %d chunks",
                stats["files_indexed"], stats["files_skipped"], stats["chunks_added"],
            )

    # ------------------------------------------------------------------
    # Deep check — once per day, <10s
    # ------------------------------------------------------------------

    def _deep_check(self, root: Path, ws_path: str) -> None:
        """Full context health validation."""
        findings: list[str] = []

        # 1. Context files exist and non-empty
        context_dir = root / ".context"
        if context_dir.is_dir():
            for md_file in sorted(context_dir.glob("*.md")):
                if md_file.name.startswith("L") and md_file.name.endswith("_SYSTEM_PROMPTS.md"):
                    continue  # Cache files, not source
                size = md_file.stat().st_size
                if size == 0:
                    findings.append(f"EMPTY: {md_file.name} (0 bytes)")

        # 2. Git health
        findings += self._check_git_health(root, ws_path)

        # 3. DDD staleness (per project)
        findings += self._check_ddd_staleness(root, ws_path)

        # 3b. Auto-apply mechanical DDD refresh proposals (non-blocking)
        try:
            self._auto_apply_ddd_proposals(root)
        except Exception as exc:
            logger.warning("context_health: DDD auto-apply failed (non-blocking): %s", exc)

        # 4. DailyActivity — today's file should exist if we're running
        da_dir = root / "Knowledge" / "DailyActivity"
        today_file = da_dir / f"{date.today().isoformat()}.md"
        if da_dir.is_dir() and not today_file.exists():
            findings.append(f"MISSING: DailyActivity/{today_file.name} (no session logged today)")

        # 5. Enforce section caps on MEMORY.md (daily, not just post-distillation)
        memory_path = context_dir / "MEMORY.md"
        if memory_path.exists():
            try:
                from hooks.distillation_hook import DistillationTriggerHook
                DistillationTriggerHook._enforce_section_caps(memory_path, root)
            except Exception as exc:
                logger.warning("context_health: section cap enforcement failed: %s", exc)

        # 6. Memory consistency — detect stale claims in MEMORY.md body
        if memory_path.exists():
            findings += self._detect_stale_memory_claims(memory_path)

        # 7. L1 cache freshness — if source .md newer than cache, invalidate
        self._check_cache_freshness(context_dir, findings)

        # 8. Enforce retention policies (archive/delete old files)
        try:
            self._enforce_retention_policies(ws_path)
        except Exception as exc:
            logger.warning("context_health: retention policy enforcement failed: %s", exc)

        # Persist findings for session briefing
        self._persist_findings(root, findings)

        # Report
        if findings:
            logger.warning(
                "context_health: deep check found %d issue(s):\n  %s",
                len(findings), "\n  ".join(findings),
            )
        else:
            logger.info("context_health: deep check passed — all healthy")

    def _check_git_health(self, root: Path, ws_path: str) -> list[str]:
        """Check git state: stale locks, uncommitted context files."""
        findings = []

        # Stale index.lock
        lock_file = root / ".git" / "index.lock"
        if lock_file.exists():
            age = datetime.now().timestamp() - lock_file.stat().st_mtime
            if age > 300:  # > 5 minutes = definitely stale
                try:
                    lock_file.unlink()
                    findings.append("AUTO-FIXED: removed stale .git/index.lock (age=%.0fs)" % age)
                except OSError:
                    findings.append("STALE: .git/index.lock (age=%.0fs, cannot remove)" % age)

        # Uncommitted .context/ changes
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", ".context/"],
                cwd=ws_path, capture_output=True, text=True,
                timeout=self._GIT_TIMEOUT,
            )
            if result.stdout.strip():
                uncommitted = [
                    l.strip() for l in result.stdout.strip().splitlines()
                ]
                findings.append(
                    f"UNCOMMITTED: {len(uncommitted)} context file(s): "
                    + ", ".join(l.split()[-1] for l in uncommitted[:5])
                )
        except (subprocess.TimeoutExpired, OSError):
            pass

        return findings

    def _check_ddd_staleness(self, root: Path, ws_path: str) -> list[str]:
        """Flag DDD docs stale >14 days vs active code commits."""
        findings = []
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return findings

        cutoff = datetime.now() - timedelta(days=14)

        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue

            # Check if TECH.md or PRODUCT.md are stale
            for ddd_name in ("TECH.md", "PRODUCT.md"):
                ddd_file = project_dir / ddd_name
                if not ddd_file.exists():
                    continue

                mtime = datetime.fromtimestamp(ddd_file.stat().st_mtime)
                if mtime > cutoff:
                    continue  # Recently updated, skip

                # Check if there have been recent commits touching this project
                # (heuristic: any commit mentioning the project name)
                try:
                    result = subprocess.run(
                        ["git", "log", "--oneline", "--since=14 days ago",
                         "--grep", project_dir.name, "--", "."],
                        cwd=ws_path, capture_output=True, text=True,
                        timeout=self._GIT_TIMEOUT,
                    )
                    if result.stdout.strip():
                        commit_count = len(result.stdout.strip().splitlines())
                        days_stale = (datetime.now() - mtime).days
                        findings.append(
                            f"DDD-STALE: {project_dir.name}/{ddd_name} "
                            f"({days_stale}d old, {commit_count} recent commits)"
                        )
                except (subprocess.TimeoutExpired, OSError):
                    pass

        return findings

    # Sections that are never auto-applied (require human judgment)
    _SEMANTIC_SECTIONS = ("Non-Goals", "Vision", "Architecture")

    def _auto_apply_ddd_proposals(self, root: Path) -> None:
        """Auto-apply mechanical DDD refresh proposals.

        Scans Projects/*/.artifacts/ddd-refresh-*.md for proposals.
        For each proposal with confidence >= 8:
        - Parse Current/Proposed code blocks
        - Classify: mechanical (only adds lines) vs semantic (modifies/deletes)
        - Skip changes targeting Non-Goals, Vision, or Architecture sections
        - Apply mechanical changes to the target DDD doc
        - Rename proposal to .applied after processing
        - Log applied changes to health_findings.json
        """
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return

        applied_changes: list[dict] = []

        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            artifacts_dir = project_dir / ".artifacts"
            if not artifacts_dir.is_dir():
                continue

            proposals = sorted(artifacts_dir.glob("ddd-refresh-*.md"))
            # Skip already-applied proposals
            proposals = [p for p in proposals if not p.name.endswith(".applied")]

            for proposal_path in proposals:
                try:
                    content = proposal_path.read_text(encoding="utf-8")

                    # Extract confidence score
                    conf_match = re.search(r"\*\*Confidence:\*\*\s*(\d+)/10", content)
                    if not conf_match:
                        continue
                    confidence = int(conf_match.group(1))
                    if confidence < 8:
                        # Rename to .applied anyway to prevent re-processing
                        proposal_path.rename(proposal_path.with_suffix(".md.applied"))
                        continue

                    # Check for semantic section targets by parsing _Targets:_ line,
                    # NOT the whole proposal body (avoids false positives from
                    # incidental mentions of "architecture" in descriptions).
                    targets_line = ""
                    for line in content.splitlines():
                        if "_Targets:" in line or "Targets:" in line:
                            targets_line = line.lower()
                            break
                    targets_semantic = any(
                        s.lower() in targets_line
                        for s in self._SEMANTIC_SECTIONS
                    )

                    # Parse Current/Proposed blocks
                    changes_applied = False
                    block_pattern = re.compile(
                        r"\*\*Current:\*\*\s*\n```\n(.*?)\n```\s*\n+"
                        r"\*\*Proposed:\*\*\s*\n```\n(.*?)\n```",
                        re.DOTALL,
                    )
                    for match in block_pattern.finditer(content):
                        current_block = match.group(1)
                        proposed_block = match.group(2)

                        # Classify: mechanical if proposed only ADDS lines
                        current_lines = current_block.strip().splitlines()
                        proposed_lines = proposed_block.strip().splitlines()

                        is_mechanical = (
                            len(proposed_lines) > len(current_lines)
                            and proposed_lines[:len(current_lines)] == current_lines
                        )

                        if not is_mechanical or targets_semantic:
                            continue  # Skip semantic changes

                        # Find and apply in target DDD doc
                        from utils.file_lock import flock_exclusive, flock_unlock
                        for ddd_name in ("TECH.md", "IMPROVEMENT.md", "PRODUCT.md"):
                            ddd_path = project_dir / ddd_name
                            if not ddd_path.exists():
                                continue
                            applied_this = False
                            lock_path = ddd_path.with_suffix(ddd_path.suffix + ".lock")
                            lock_file = open(lock_path, "w")
                            flock_exclusive(lock_file)
                            try:
                                ddd_content = ddd_path.read_text(encoding="utf-8")
                                if current_block in ddd_content:
                                    new_content = ddd_content.replace(
                                        current_block, proposed_block, 1
                                    )
                                    ddd_path.write_text(new_content, encoding="utf-8")
                                    changes_applied = True
                                    applied_this = True
                            finally:
                                flock_unlock(lock_file)
                                lock_file.close()
                            if applied_this:
                                applied_changes.append({
                                    "project": project_dir.name,
                                    "doc": ddd_name,
                                    "proposal": proposal_path.name,
                                    "type": "mechanical_append",
                                })
                                logger.info(
                                    "DDD auto-apply: applied mechanical change to %s/%s from %s",
                                    project_dir.name, ddd_name, proposal_path.name,
                                )
                                break

                    # Rename proposal to .applied
                    proposal_path.rename(proposal_path.with_suffix(".md.applied"))

                except Exception as exc:
                    logger.warning("DDD auto-apply failed for %s: %s", proposal_path.name, exc)

        # Log to health_findings.json
        if applied_changes:
            findings_dir = root / "Services" / "swarm-jobs"
            findings_file = findings_dir / "health_findings.json"
            if findings_file.exists():
                try:
                    data = json.loads(findings_file.read_text(encoding="utf-8"))
                    for change in applied_changes:
                        data["findings"].append({
                            "level": "info",
                            "message": (
                                f"DDD-AUTO-APPLY: {change['type']} in "
                                f"{change['project']}/{change['doc']} "
                                f"from {change['proposal']}"
                            ),
                        })
                    findings_file.write_text(
                        json.dumps(data, indent=2, default=str),
                        encoding="utf-8",
                    )
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning("Failed to log DDD auto-apply: %s", exc)

    @staticmethod
    def _detect_stale_memory_claims(memory_path: Path) -> list[str]:
        """Detect stale or inconsistent claims in MEMORY.md body.

        Mechanical checks only — no LLM needed.  Catches the class of bugs
        where facts change (feature shipped, concept eliminated, item resolved)
        but the memory entry still says otherwise.  COE03/C005 pattern.

        Checks:
        1. Open Threads body↔state: ✅ entries under active subsections
        2. Stale forward-references: "Next:", "TODO:", "NOT yet" in entries
           older than 14 days (likely completed but not updated)
        3. Index↔body count mismatch (caught structurally by index regen,
           but flagged here for visibility)
        """
        findings: list[str] = []
        try:
            content = memory_path.read_text(encoding="utf-8")
        except OSError:
            return findings

        # ── Check 1: ✅ entries in active OT subsections ──
        # These should only appear under "### Resolved" — if they're under
        # P0/P1/P2, someone resolved it but didn't move it.
        ot_match = re.search(
            r"## Open Threads\n(.*?)(?=\n## |\Z)", content, re.DOTALL
        )
        if ot_match:
            ot_body = ot_match.group(1)
            # Split by ### subsections
            current_subsection = ""
            for line in ot_body.split("\n"):
                if line.startswith("### "):
                    current_subsection = line.strip()
                elif (
                    line.strip().startswith("- \u2705")
                    and "Resolved" not in current_subsection
                ):
                    title = line.strip()[:80]
                    findings.append(
                        f"STALE-OT: resolved entry in active section "
                        f"({current_subsection}): {title}"
                    )

        # ── Check 2: Stale forward-references in old entries ──
        # Patterns that suggest "this hasn't happened yet" in entries > 14d old
        stale_patterns = [
            (r"NOT yet (?:created|built|implemented|shipped)", "NOT yet"),
            (r"Next:\s+build\b", "Next: build"),
            (r"TODO:\s+\w", "TODO:"),
            (r"not yet built", "not yet built"),
            (r"\bdeferred\b|\bon hold\b", "deferred/on hold"),  # only flag if > 30d
        ]
        today = date.today()

        for section_name in ("Recent Context", "Key Decisions"):
            # Extract section body
            sec_match = re.search(
                rf"## {section_name}\n(.*?)(?=\n## |\Z)", content, re.DOTALL
            )
            if not sec_match:
                continue

            for line in sec_match.group(1).split("\n"):
                line = line.strip()
                if not line.startswith("- "):
                    continue

                # Extract date from entry
                date_match = re.match(r"- (\d{4}-\d{2}-\d{2})", line)
                if not date_match:
                    continue

                try:
                    entry_date = datetime.strptime(
                        date_match.group(1), "%Y-%m-%d"
                    ).date()
                except ValueError:
                    continue

                age_days = (today - entry_date).days
                # "deferred/on hold" only stale after 30d, others after 14d
                for pattern, label in stale_patterns:
                    threshold = 30 if "deferred" in pattern else 14
                    if age_days > threshold and re.search(pattern, line, re.IGNORECASE):
                        title = line[2:72]  # strip "- ", cap at 70 chars
                        findings.append(
                            f"STALE-CLAIM: \"{label}\" in {section_name} "
                            f"entry ({age_days}d old): {title}..."
                        )
                        break  # one finding per entry

        return findings

    def _check_cache_freshness(self, context_dir: Path, findings: list[str]) -> None:
        """If any source .context/*.md is newer than L1 cache, invalidate."""
        cache_file = context_dir / "L1_SYSTEM_PROMPTS.md"
        if not cache_file.exists():
            return

        cache_mtime = cache_file.stat().st_mtime
        for source in context_dir.glob("*.md"):
            if source.name.startswith("L") or source.name == cache_file.name:
                continue
            if source.stat().st_mtime > cache_mtime:
                try:
                    cache_file.unlink()
                    findings.append(
                        f"AUTO-FIXED: invalidated L1 cache ({source.name} is newer)"
                    )
                except OSError:
                    findings.append(f"STALE-CACHE: L1 cache older than {source.name}")
                break  # Only need to invalidate once

    def _persist_findings(self, root: Path, findings: list[str]) -> None:
        """Write findings to health_findings.json for session briefing.

        The proactive intelligence system reads this file at session start
        to surface health alerts. Structured as:
        {
            "timestamp": "ISO8601",
            "findings": [{"level": "warning|info|critical", "message": "..."}],
            "memory_health": null  // populated by weekly maintenance job
        }
        """
        import json

        findings_dir = root / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True, exist_ok=True)
        findings_file = findings_dir / "health_findings.json"

        structured = []
        for f in findings:
            level = "critical" if f.startswith("EMPTY") else \
                    "warning" if any(f.startswith(p) for p in ("UNCOMMITTED", "STALE", "MISSING")) else \
                    "info"
            structured.append({"level": level, "message": f})

        data = {
            "timestamp": datetime.now().isoformat(),
            "findings": structured,
            "memory_health": None,  # Populated by weekly-maintenance job
        }

        try:
            # Merge memory_health from previous run (weekly job may have written it)
            if findings_file.exists():
                try:
                    prev = json.loads(findings_file.read_text(encoding="utf-8"))
                    if prev.get("memory_health"):
                        data["memory_health"] = prev["memory_health"]
                except (json.JSONDecodeError, OSError):
                    pass

            findings_file.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Failed to persist health findings: %s", e)

    # ------------------------------------------------------------------
    # Retention Policies
    # ------------------------------------------------------------------

    def _enforce_retention_policies(self, ws_path: str) -> None:
        """Enforce time-based archival and cleanup.

        1. DailyActivity >90 days -> move to Knowledge/Archives/
        2. Archives >365 days -> delete (except MEMORY-archive-*.md)
        3. Open Threads with resolved marker >7 days -> log for manual review
           (actual removal is handled by section cap enforcement, not here)
        """
        root = Path(ws_path)
        da_dir = root / "Knowledge" / "DailyActivity"
        archive_dir = root / "Knowledge" / "Archives"
        archive_dir.mkdir(parents=True, exist_ok=True)

        cutoff_90 = datetime.now() - timedelta(days=90)
        cutoff_365 = datetime.now() - timedelta(days=365)
        cutoff_7 = datetime.now() - timedelta(days=7)

        # 1. Archive old DailyActivity
        if da_dir.exists():
            for f in da_dir.glob("*.md"):
                try:
                    file_date = datetime.strptime(f.stem, "%Y-%m-%d")
                    if file_date < cutoff_90:
                        # Protect undistilled files from archival — but only up to
                        # 180 days.  Beyond that, archive regardless to prevent
                        # unbounded DailyActivity growth from distillation failures.
                        cutoff_180 = datetime.now() - timedelta(days=180)
                        if file_date >= cutoff_180:
                            content = f.read_text(encoding="utf-8")
                            if "distilled: true" not in content[:500]:  # check frontmatter only
                                logger.warning("Skipping undistilled file %s (>90d but not yet distilled)", f.name)
                                continue
                        dest = archive_dir / f.name
                        f.rename(dest)
                        logger.info("Archived DailyActivity: %s", f.name)
                except ValueError:
                    continue

        # 2. Delete old archives (except MEMORY-archive-*)
        # Note: MEMORY-archive-* files are double-protected:
        # (a) name prefix check skips them explicitly, and
        # (b) their stems (e.g. "MEMORY-archive-2026-04") fail strptime
        #     on [:10] slice ("MEMORY-arc"), so they'd be skipped anyway.
        if archive_dir.exists():
            for f in archive_dir.glob("*.md"):
                if f.name.startswith("MEMORY-archive-"):
                    continue  # Never delete memory archives
                try:
                    file_date = datetime.strptime(f.stem[:10], "%Y-%m-%d")
                    if file_date < cutoff_365:
                        f.unlink()
                        logger.info("Deleted old archive: %s", f.name)
                except (ValueError, IndexError):
                    continue

        # 3. Archive resolved Open Threads >7 days — remove from MEMORY.md
        #    and append to MEMORY-archive-YYYY-MM.md (same pattern as
        #    _enforce_section_caps in distillation_hook.py).
        memory_path = root / ".context" / "MEMORY.md"
        if memory_path.exists():
            self._archive_resolved_open_threads(memory_path, root, cutoff_7)

    # ------------------------------------------------------------------
    # Open Thread archival
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_ot_entry_date(line: str) -> Optional[datetime]:
        """Parse date from an Open Thread entry line. Returns None if unparseable."""
        # Format 1: ISO date at line start: "- 2024-03-22: ..."
        iso_start = re.match(r"- (\d{4}-\d{2}-\d{2})", line)
        if iso_start:
            try:
                return datetime.strptime(iso_start.group(1), "%Y-%m-%d")
            except ValueError:
                pass
        # Format 2: ISO date anywhere in parens: "- ... (2024-03-22)"
        iso_any = re.search(r"\((\d{4}-\d{2}-\d{2})\)", line)
        if iso_any:
            try:
                return datetime.strptime(iso_any.group(1), "%Y-%m-%d")
            except ValueError:
                pass
        # Format 3: Short month/day in parens: (3/22), (12/5)
        short_date = re.search(r"\((\d{1,2})/(\d{1,2})\)", line)
        if short_date:
            try:
                month = int(short_date.group(1))
                day = int(short_date.group(2))
                return datetime(datetime.now().year, month, day)
            except (ValueError, OverflowError):
                pass
        return None

    def _archive_resolved_open_threads(
        self, memory_path: Path, root: Path, cutoff: datetime
    ) -> None:
        """Remove resolved OT entries >cutoff from MEMORY.md, append to archive.

        Uses flock on the MEMORY.md.lock sidecar file, matching the
        locking pattern in distillation_hook._enforce_section_caps and
        scripts/locked_write.py.
        """
        from utils.file_lock import flock_exclusive, flock_unlock

        lock_path = memory_path.with_suffix(memory_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        try:
            fd = open(lock_path, "w")  # noqa: SIM115
            flock_exclusive(fd)
            try:
                content = memory_path.read_text(encoding="utf-8")
                ot_match = re.search(
                    r"(## Open Threads\n)(.*?)(?=\n## |\Z)",
                    content, re.DOTALL,
                )
                if not ot_match:
                    return

                ot_header = ot_match.group(1)
                ot_body = ot_match.group(2)
                lines = ot_body.split("\n")
                keep_lines: list[str] = []
                archived_lines: list[str] = []

                for line in lines:
                    stripped = line.strip()
                    if not stripped.startswith("- ") or "\u2705" not in stripped:
                        keep_lines.append(line)
                        continue
                    entry_date = self._parse_ot_entry_date(stripped)
                    if entry_date is None or entry_date >= cutoff:
                        keep_lines.append(line)
                        continue
                    # Resolved and older than cutoff — archive it
                    archived_lines.append(stripped)
                    logger.info("Archiving resolved OT entry: %s", stripped[:80])

                if not archived_lines:
                    return

                # Rewrite MEMORY.md without the archived entries
                new_ot_body = "\n".join(keep_lines)
                new_content = (
                    content[:ot_match.start()]
                    + ot_header + new_ot_body
                    + content[ot_match.end():]
                )
                # MemoryGuard: sanitize before writing
                try:
                    from core.memory_guard import MemoryGuard
                    new_content = MemoryGuard().sanitize(new_content)
                except (ImportError, Exception):
                    pass  # graceful degradation
                memory_path.write_text(new_content, encoding="utf-8")

                # Append archived entries to MEMORY-archive-YYYY-MM.md
                archive_dir = root / "Knowledge" / "Archives"
                archive_dir.mkdir(parents=True, exist_ok=True)
                today = date.today()
                archive_name = f"MEMORY-archive-{today.strftime('%Y-%m')}.md"
                archive_path = archive_dir / archive_name

                archive_block = f"\n### Archived Open Threads ({today.isoformat()})\n"
                archive_block += "\n".join(archived_lines) + "\n"

                if archive_path.exists():
                    existing = archive_path.read_text(encoding="utf-8")
                    archive_path.write_text(existing + archive_block, encoding="utf-8")
                else:
                    archive_path.write_text(
                        f"# Memory Archive — {today.strftime('%Y-%m')}\n" + archive_block,
                        encoding="utf-8",
                    )
                logger.info(
                    "context_health: archived %d resolved OT entries to %s",
                    len(archived_lines), archive_name,
                )
            finally:
                flock_unlock(fd)
        except Exception as exc:
            logger.warning("context_health: OT archival failed: %s", exc)
        finally:
            if fd:
                fd.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _git_rev(self, ws_path: str) -> Optional[str]:
        """Get current HEAD rev, or None."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ws_path, capture_output=True, text=True,
                timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError):
            return None

    @staticmethod
    def _extract_title(filepath: Path) -> Optional[str]:
        """Read first markdown heading or YAML title from a file."""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                in_frontmatter = False
                for i, line in enumerate(f):
                    if i == 0 and line.strip() == "---":
                        in_frontmatter = True
                        continue
                    if in_frontmatter:
                        if line.strip() == "---":
                            in_frontmatter = False
                            continue
                        if line.startswith("title:"):
                            title = line.split(":", 1)[1].strip().strip("\"'")
                            return title
                        continue
                    if line.startswith("# "):
                        return line[2:].strip()
                    if i > 15:
                        break
        except Exception:
            pass
        return None
