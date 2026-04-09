"""Context Health Harness — keeps SwarmAI's brain accurate and current.

Single hook, two modes:
- **Light** (every session): refresh KNOWLEDGE.md + PROJECTS.md indexes
  if workspace changed since last refresh.
- **Deep** (once per day): validate all 11 context files, check MEMORY.md
  accuracy vs git, detect DDD staleness, verify git health.

All checks are filesystem-only (no LLM, no network).  Auto-fixes what
it can, logs what it can't.  Total budget: <3s light, <10s deep.
"""

import logging
import os
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

        # ── Light: refresh indexes if workspace changed ──────────────
        self._light_refresh(root, ws_path)

        # ── Deep: once per calendar day ──────────────────────────────
        today = date.today().isoformat()
        if self._last_deep_date != today:
            self._deep_check(root, ws_path)
            self._last_deep_date = today

    # ------------------------------------------------------------------
    # Light refresh — every session, <2s
    # ------------------------------------------------------------------

    def _light_refresh(self, root: Path, ws_path: str) -> None:
        """Refresh KNOWLEDGE.md index and MEMORY.md index if workspace changed."""
        # Memory index regen runs unconditionally — it's <10ms and must
        # catch uncommitted MEMORY.md writes (Edit tool, locked_write)
        # that happen within the same git rev.
        try:
            self._refresh_memory_index(root)
        except Exception as exc:
            logger.warning("context_health: MEMORY.md index refresh failed: %s", exc)

        # KNOWLEDGE.md refresh is git-gated (filesystem scan is heavier)
        current_rev = self._git_rev(ws_path)
        if current_rev and current_rev == self._last_refresh_rev:
            return  # Nothing changed since last refresh

        try:
            self._refresh_knowledge_sync(root)
        except Exception as exc:
            logger.warning("context_health: KNOWLEDGE.md refresh failed: %s", exc)

        # Knowledge Library vector+FTS5 indexing (incremental, <5s)
        try:
            self._sync_knowledge_library(root)
        except Exception as exc:
            logger.debug("context_health: knowledge library sync skipped: %s", exc)

        self._last_refresh_rev = current_rev
        logger.info("context_health: indexes refreshed (rev=%s)",
                    current_rev[:8] if current_rev else "?")

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
        import fcntl
        lock_path = memory_file.with_suffix(".md.lock")
        lock_fd = None
        try:
            lock_fd = open(lock_path, "w")  # noqa: SIM115
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            # Another writer holds the lock — skip this cycle, next hook run will catch it
            if lock_fd:
                lock_fd.close()
            logger.debug("context_health: MEMORY.md locked by another writer, skipping index regen")
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
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
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

        # 6. L1 cache freshness — if source .md newer than cache, invalidate
        self._check_cache_freshness(context_dir, findings)

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
