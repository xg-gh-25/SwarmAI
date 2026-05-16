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
import time
from datetime import date, datetime, timedelta, timezone
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
        # PE-4: Shared cultivation deadline (25s total for BOTH passes).
        # BackgroundHookExecutor has 30s timeout — 25s leaves 5s headroom.
        _cultivation_deadline = time.monotonic() + 25.0

        # Auto-cultivate pipeline lessons — promote REFLECT output into DDD docs
        # without requiring the agent to remember to run `run-cultivate` manually.
        try:
            self._auto_cultivate_pipeline_lessons(root, _deadline=_cultivation_deadline)
        except Exception as exc:
            logger.debug("context_health: auto-cultivation skipped: %s", exc)

        # Auto-cultivate session signals — promote corrections (Ch6) and
        # decisions (Ch5) from DailyActivity JSONL into DDD docs.
        try:
            self._auto_cultivate_session_signals(root, _deadline=_cultivation_deadline)
        except Exception as exc:
            logger.debug("context_health: session signal cultivation skipped: %s", exc)

        # T4: Maturity evidence update + promotion evaluation.
        # Runs AFTER cultivation so new changelog entries are counted.
        try:
            self._update_maturity(root, _deadline=_cultivation_deadline)
        except Exception as exc:
            logger.debug("context_health: maturity update skipped: %s", exc)

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

        # Code Intelligence — incremental graph refresh (<2s for typical changeset)
        try:
            self._refresh_code_intel(root)
        except Exception as exc:
            logger.debug("context_health: code_intel refresh skipped: %s", exc)

    def _refresh_code_intel(self, root: Path) -> None:
        """Refresh code_intel.db if the indexed commit is behind HEAD."""
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return

        from core.code_intel import load_project_graph
        from core.code_intel.freshness import check_freshness

        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            db_path = project_dir / "code_intel.db"
            if not db_path.exists():
                continue

            graph = load_project_graph(project_dir.name)
            if not graph:
                continue

            freshness = check_freshness(graph)
            if not freshness.stale:
                continue

            if freshness.suggest_full_rebuild:
                logger.info(
                    "code_intel %s: %d commits behind, %d files — suggest rebuild",
                    project_dir.name, freshness.commits_behind,
                    len(freshness.changed_files),
                )
                continue  # don't auto-rebuild large changes at session start

            # Incremental update for small changes
            from core.code_intel.parser import parse_file
            from pathlib import Path as P

            repo_root = P(graph.get_meta("repo_root") or "")
            if not repo_root.is_dir():
                continue

            for rel_path in freshness.changed_files[:50]:  # cap at 50
                full_path = repo_root / rel_path
                if full_path.exists():
                    # P1-7: Isolate per-file errors so one bad file doesn't skip the rest
                    try:
                        result = parse_file(full_path, repo_root)
                        if result.nodes:
                            file_hash = result.nodes[0].sha256 or ""
                            graph.store_file_nodes_edges(
                                rel_path, result.nodes, result.edges, file_hash
                            )
                    except Exception as file_err:
                        logger.debug("code_intel: failed to parse %s: %s", rel_path, file_err)
                else:
                    # File was deleted — remove stale nodes/edges
                    graph.remove_file(rel_path)

            graph.rebuild_fts()
            if freshness.current_head:
                graph.set_meta("last_indexed_commit", freshness.current_head)

            logger.info(
                "code_intel %s: incremental update — %d files refreshed",
                project_dir.name, len(freshness.changed_files),
            )

    # ------------------------------------------------------------------
    # Auto-cultivation — promote REFLECT lessons into DDD docs
    # ------------------------------------------------------------------

    def _auto_cultivate_pipeline_lessons(self, root: Path, *, _deadline: float = 0) -> None:
        """Auto-cultivate uncultivated pipeline REFLECT lessons into DDD docs.

        Scans all Projects/*/.artifacts/runs/*/run.json for completed pipeline
        runs that have reflect.lessons populated but no cultivated:true flag.
        For each, calls cultivate_from_reflect() to auto-apply safe additive
        lessons and escalate risky ones, then marks the run as cultivated.

        This replaces the manual `run-cultivate` CLI call that the agent had
        to remember (and failed 100% of the time — 141 runs, 0 cultivated).

        Capped at 5 cultivations per session AND 25s cooperative time budget.
        The hook executor enforces a 30s timeout via asyncio.wait_for, but that
        cannot actually cancel a thread-pool thread in CPython — it just stops
        waiting while the thread continues silently. The cooperative budget bails
        early so the hook finishes cleanly within the executor's window.

        Remaining uncultivated runs are processed in subsequent sessions.
        """
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return

        from core.ddd_cultivation import cultivate_from_reflect

        _MAX_PER_SESSION = 5
        # PE-4: use shared deadline from _light_refresh (25s total for all cultivation)
        _effective_deadline = _deadline if _deadline > 0 else (time.monotonic() + 25.0)
        cultivated_count = 0

        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            runs_dir = project_dir / ".artifacts" / "runs"
            if not runs_dir.is_dir():
                continue

            # Sort by mtime (oldest first) to ensure FIFO processing.
            # Filter to last 30 days — older uncultivated runs are stale and
            # won't produce useful DDD content. Also bounds scan cost to O(recent)
            # instead of O(total history) as pipelines accumulate.
            # Cache stat to avoid double syscall (meta-review finding).
            mtime_cutoff = time.time() - 30 * 86400
            run_items = [
                (d, d.stat().st_mtime)
                for d in runs_dir.iterdir()
                if d.is_dir()
            ]
            run_dirs = sorted(
                ((d, mt) for d, mt in run_items if mt > mtime_cutoff),
                key=lambda x: x[1],
            )

            for run_dir, _ in run_dirs:
                # Cooperative time budget — bail cleanly before hook timeout
                if time.monotonic() > _effective_deadline:
                    logger.info(
                        "context_health: auto-cultivate hit shared deadline, "
                        "deferring remaining to next session",
                    )
                    break

                run_file = run_dir / "run.json"
                if not run_file.exists():
                    continue

                try:
                    run_data = json.loads(run_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    logger.debug(
                        "context_health: auto-cultivate skipped corrupt %s", run_file
                    )
                    continue

                # Find reflect stage with lessons
                reflect_stage = None
                reflect_idx = -1
                for idx, stage in enumerate(run_data.get("stages", [])):
                    if stage.get("stage") == "reflect":
                        reflect_stage = stage
                        reflect_idx = idx
                        break

                if reflect_stage is None:
                    continue
                if reflect_stage.get("cultivated"):
                    continue  # Already done
                lessons = reflect_stage.get("lessons", [])
                if not lessons:
                    continue  # Nothing to cultivate

                # Cap per session to keep _light_refresh fast
                if cultivated_count >= _MAX_PER_SESSION:
                    break

                # Cultivate
                project_name = project_dir.name
                try:
                    run_id = run_data.get("id", run_dir.name)
                    result = cultivate_from_reflect(
                        lessons, run_id, project_name, project_dir
                    )

                    # Atomic write: mark cultivated in run.json via tmp+replace
                    run_data["stages"][reflect_idx]["cultivated"] = True
                    tmp_file = run_file.with_suffix(".tmp")
                    tmp_file.write_text(
                        json.dumps(run_data, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    os.replace(tmp_file, run_file)
                    cultivated_count += 1

                    logger.info(
                        "context_health: auto-cultivated %s/%s — "
                        "applied=%d, escalated=%d, rejected=%d",
                        project_name, run_id,
                        result.get("applied", 0),
                        result.get("escalated", 0),
                        result.get("rejected", 0),
                    )
                except Exception as exc:
                    logger.warning(
                        "context_health: auto-cultivate failed for %s/%s: %s",
                        project_name, run_dir.name, exc,
                    )

            if cultivated_count >= _MAX_PER_SESSION:
                break
            if time.monotonic() > _effective_deadline:
                break

        if cultivated_count > 0:
            logger.info(
                "context_health: auto-cultivated %d pipeline run(s)", cultivated_count
            )

    # ------------------------------------------------------------------
    # Auto-cultivation — promote session corrections + decisions into DDD
    # ------------------------------------------------------------------

    # State file: tracks which JSONL session records have been cultivated
    _SESSION_CULTIVATED_STATE = ".context/.session_cultivated.json"

    def _auto_cultivate_session_signals(self, root: Path, *, _deadline: float = 0) -> None:
        """Auto-cultivate corrections and decisions from DailyActivity JSONL into DDD docs.

        Reads recent DailyActivity JSONL sidecars (last 7 days), extracts
        corrections (Ch6 — highest priority) and decisions (Ch5), feeds them
        through the same keyword classifier used by pipeline REFLECT cultivation.

        Idempotency: tracks cultivated session_ids in a state file. Each session
        is processed at most once. State file is a simple JSON list of session IDs.

        Capped at 10 sessions per invocation, sharing the same cooperative time
        budget mindset as pipeline cultivation.
        """
        da_dir = root / "Knowledge" / "DailyActivity"
        if not da_dir.is_dir():
            return

        from core.ddd_cultivation import cultivate_from_corrections, cultivate_from_decisions

        # Load cultivated state (ordered list of session_ids already processed).
        # Uses a list to preserve insertion order — capping takes oldest first.
        state_path = root / self._SESSION_CULTIVATED_STATE
        cultivated_list: list = []
        if state_path.is_file():
            try:
                cultivated_list = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(cultivated_list, list):
                    cultivated_list = []
            except (json.JSONDecodeError, OSError):
                cultivated_list = []
        cultivated_ids: set = set(cultivated_list)  # O(1) lookup

        # Scan JSONL sidecars from last 7 days
        today = date.today()
        cutoff = today - timedelta(days=7)

        jsonl_files = sorted(da_dir.glob("*.jsonl"))
        # Filter to recent files by filename date prefix
        recent_jsonls = []
        for jf in jsonl_files:
            try:
                file_date = date.fromisoformat(jf.stem[:10])
                if file_date >= cutoff:
                    recent_jsonls.append(jf)
            except (ValueError, IndexError):
                continue

        if not recent_jsonls:
            return

        # Determine project directory for cultivation target.
        # Default to SwarmAI (the workspace project — corrections about the
        # agent itself are the most common signal). Future: infer from session
        # topics or files_modified.
        projects_dir = root / "Projects"
        default_project = "SwarmAI"
        default_project_dir = projects_dir / default_project
        if not default_project_dir.is_dir():
            return

        _MAX_SESSIONS = 10
        # PE-4: use shared deadline from _light_refresh (25s total for all cultivation)
        _effective_deadline = _deadline if _deadline > 0 else (time.monotonic() + 15.0)
        processed = 0
        total_applied = 0
        total_escalated = 0

        from core.daily_activity_writer import read_jsonl_sidecar

        for jsonl_path in recent_jsonls:
            if processed >= _MAX_SESSIONS:
                break
            if time.monotonic() > _effective_deadline:
                break

            records = read_jsonl_sidecar(jsonl_path)
            for record in records:
                if processed >= _MAX_SESSIONS:
                    break
                if time.monotonic() > _effective_deadline:
                    break

                session_id = record.get("session_id", "")
                if not session_id or session_id in cultivated_ids:
                    continue

                corrections = record.get("corrections", [])
                decisions = record.get("decisions", [])

                if not corrections and not decisions:
                    cultivated_ids.add(session_id)
                    # Don't count empty sessions toward _MAX_SESSIONS —
                    # only actual cultivations should consume the budget.
                    continue

                # Cultivate corrections (Ch6 — highest priority per HLD)
                if corrections:
                    try:
                        result = cultivate_from_corrections(
                            corrections, session_id, default_project, default_project_dir
                        )
                        total_applied += result.get("applied", 0)
                        total_escalated += result.get("escalated", 0)
                    except Exception as exc:
                        logger.debug(
                            "context_health: session correction cultivation failed "
                            "for %s: %s", session_id[:8], exc,
                        )

                # Cultivate decisions (Ch5)
                if decisions:
                    try:
                        result = cultivate_from_decisions(
                            decisions, session_id, default_project, default_project_dir
                        )
                        total_applied += result.get("applied", 0)
                        total_escalated += result.get("escalated", 0)
                    except Exception as exc:
                        logger.debug(
                            "context_health: session decision cultivation failed "
                            "for %s: %s", session_id[:8], exc,
                        )

                cultivated_ids.add(session_id)
                processed += 1

        # Persist state — cap at 500 most recent to prevent unbounded growth.
        # Atomic write (tmp + os.replace) prevents corruption on crash/SIGKILL.
        # Persist whenever new IDs were added (including empty-signal sessions).
        if len(cultivated_ids) > len(cultivated_list):
            # Rebuild ordered list: old (from file) + newly added, deduped, capped
            new_ids = [sid for sid in cultivated_list if sid in cultivated_ids]
            existing_set = set(new_ids)  # PE-2: O(1) lookup, built once
            for sid in cultivated_ids:
                if sid not in existing_set:
                    new_ids.append(sid)
            capped_ids = new_ids[-500:]  # Keep 500 newest (oldest evicted first)
            try:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = state_path.with_suffix(".tmp")
                tmp_path.write_text(
                    json.dumps(capped_ids, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(tmp_path, state_path)
            except OSError as exc:
                logger.debug("context_health: failed to persist cultivation state: %s", exc)

            logger.info(
                "context_health: session signal cultivation — "
                "processed=%d, applied=%d, escalated=%d",
                processed, total_applied, total_escalated,
            )

    # High-volume dirs get compact summary instead of per-file table (saves ~1500 tokens)
    _COMPACT_INDEX_DIRS = {"DailyActivity", "JobResults", "Signals"}

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

            # High-volume dirs: compact summary only (saves ~1500 tokens in system prompt)
            if subdir.name in self._COMPACT_INDEX_DIRS:
                first_date = files[0].stem[:10] if len(files[0].stem) > 10 else "unknown"
                last_date = files[-1].stem[:10] if len(files[-1].stem) > 10 else "unknown"
                index_lines.append(f"\n### {subdir.name}\n")
                index_lines.append(
                    f"{len(files)} files from {first_date} to {last_date}. "
                    f"Pattern: `Knowledge/{subdir.name}/YYYY-MM-DD-*.md`. Read on demand."
                )
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

    # ------------------------------------------------------------------
    # T4: Maturity evidence update + auto-promotion
    # ------------------------------------------------------------------

    def _update_maturity(self, root: Path, *, _deadline: float = 0) -> None:
        """Update maturity evidence from changelog and auto-promote eligible sections.

        Steps:
        1. For each project with DDD docs, update source_count from changelog.
        2. Evaluate promotions (sparse→growing, growing→mature).
        3. Apply promotions + log to changelog.

        Runs after cultivation so new changelog entries are counted.
        Respects shared _deadline from _light_refresh (PE-3).
        """
        from core.ddd_maturity import (
            evaluate_all_promotions,
            promote_section,
            update_evidence_from_changelog,
        )

        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return

        _effective_deadline = _deadline if _deadline > 0 else (time.monotonic() + 10.0)

        for project_path in projects_dir.iterdir():
            if not project_path.is_dir():
                continue
            # PE-3: Respect time budget
            if time.monotonic() > _effective_deadline:
                logger.debug("context_health: maturity update hit deadline, stopping")
                break
            # Skip projects without DDD docs
            if not (project_path / "TECH.md").exists() and not (project_path / "PRODUCT.md").exists():
                continue

            try:
                # Step 1: Update evidence from changelog
                update_evidence_from_changelog(project_path)

                # Step 2+3: Evaluate and apply promotions
                promotions = evaluate_all_promotions(project_path)
                for promo in promotions:
                    success = promote_section(
                        project_path, promo["doc"], promo["section"], promo["to_level"]
                    )
                    if success:
                        # Log promotion to changelog
                        self._log_maturity_promotion(project_path, promo)
                        logger.info(
                            "context_health: maturity promoted %s/%s %s → %s",
                            promo["doc"], promo["section"],
                            promo["from_level"], promo["to_level"],
                        )
            except Exception as exc:
                logger.debug(
                    "context_health: maturity update for %s skipped: %s",
                    project_path.name, exc,
                )

    def _log_maturity_promotion(self, project_dir: Path, promo: dict) -> None:
        """Log a maturity promotion event to the DDD changelog."""
        import json as _json

        changelog_path = project_dir / ".artifacts" / "ddd-changelog.jsonl"
        changelog_path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_doc": promo["doc"],
            "target_section": promo["section"],
            "source_stage": "maturity_promotion",
            "change_type": "promotion",
            "detail": f"{promo['from_level']} → {promo['to_level']}",
            "evidence": promo.get("evidence", {}),
        }
        with open(changelog_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry) + "\n")

    def _track_memory_usage(self, root: Path) -> None:
        """Scan session transcripts for memory key references.

        Finds patterns like ``[RC04]``, ``[KD05]``, ``[LL07]``, ``[COE02]``
        in recent Claude session transcripts (JSONL) where the agent actually
        references memory entries during conversations.

        Previous implementation scanned DailyActivity (0 signal — DA never
        contains memory keys). Session transcripts DO contain them in both
        tool_use blocks and assistant responses.

        Distillation reads this file to decide eviction order: entries with
        zero usage are evicted first when section caps are exceeded, forming
        the compound loop: use → track → evict unused → memory improves.
        """
        _KEY_RE = re.compile(r"\[([A-Z]{2,3}\d{2,3})\]")

        # Load existing usage (cumulative — don't reset each session)
        usage_path = root / ".context" / ".memory-usage.json"
        usage: dict[str, int] = {}
        if usage_path.exists():
            try:
                usage = json.loads(usage_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        # Source 1: Recent session transcripts (last 7 days)
        transcripts_dir = Path.home() / ".claude" / "projects"
        if transcripts_dir.is_dir():
            cutoff = (date.today() - timedelta(days=7)).isoformat()
            # Track which files we've already scanned (avoid re-counting)
            scanned_marker = root / ".context" / ".memory-usage-scanned.txt"
            scanned_set: set[str] = set()
            if scanned_marker.exists():
                try:
                    scanned_set = set(scanned_marker.read_text().splitlines())
                except OSError:
                    pass

            new_scanned: list[str] = []
            for jsonl_file in transcripts_dir.rglob("*.jsonl"):
                # Only scan recent files (mtime check is fast)
                try:
                    if jsonl_file.stat().st_mtime < (time.time() - 7 * 86400):
                        continue
                except OSError:
                    continue
                file_key = str(jsonl_file)
                if file_key in scanned_set:
                    continue
                # Read and scan for memory key references
                try:
                    content = jsonl_file.read_text(encoding="utf-8", errors="ignore")
                    for key in _KEY_RE.findall(content):
                        usage[key] = usage.get(key, 0) + 1
                    new_scanned.append(file_key)
                except OSError:
                    continue

            # Update scanned marker — prune entries for files older than scan window (7d)
            # Using date-based cutoff instead of count-based cap to prevent re-scanning
            # files that fell off the cap but are still within the 7d mtime window (G1 fix).
            if new_scanned:
                all_scanned = list(scanned_set) + new_scanned
                # Prune: only keep entries whose files still exist and are within 7d
                mtime_cutoff = time.time() - 7 * 86400
                pruned = []
                for entry in all_scanned:
                    try:
                        if Path(entry).stat().st_mtime >= mtime_cutoff:
                            pruned.append(entry)
                    except OSError:
                        pass  # File deleted — drop from marker
                scanned_marker.write_text("\n".join(pruned), encoding="utf-8")

        # Source 2: DailyActivity (secondary — may contain refs from session summaries)
        daily_dir = root / "Knowledge" / "DailyActivity"
        if daily_dir.is_dir():
            cutoff_da = (date.today() - timedelta(days=7)).isoformat()
            for f in sorted(daily_dir.glob("*.md"), reverse=True):
                if f.stem < cutoff_da:
                    break
                try:
                    body = f.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for key in _KEY_RE.findall(body):
                    usage[key] = usage.get(key, 0) + 1

        usage_path.parent.mkdir(parents=True, exist_ok=True)
        usage_path.write_text(json.dumps(usage, indent=2, sort_keys=True), encoding="utf-8")

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

        Recovery: detects entries with metadata but no vector (embed_failed
        on a prior run) and retries them. This prevents the "stuck forever"
        state where delta-sync skips because hash matches but vector is empty.

        Rate-limiting: on cold-start (>20 entries to embed), processes max 10
        per session to avoid blocking session close. Full sync completes over
        multiple sessions (~9 sessions for 85 entries).
        """
        try:
            from core.memory_embeddings import MemoryEmbeddingStore
            from core.embedding_client import EmbeddingClient
            from core.vec_db import open_vec_db

            with open_vec_db() as conn:
                if conn is None:
                    logger.warning("context_health: vec_db conn is None — sqlite-vec not available")
                    return

                store = MemoryEmbeddingStore(conn)
                store.ensure_tables()

                client = EmbeddingClient()
                embed_failures = 0

                def _safe_embed(text: str) -> list[float] | None:
                    """Embed text, returning None on failure. Tracks failures."""
                    nonlocal embed_failures
                    result = client.embed_text(text)
                    if result is None:
                        embed_failures += 1
                    return result

                stats = store.sync_from_memory(
                    memory_content,
                    embed_fn=_safe_embed,
                )

                # Recovery: find entries with metadata but no vector (orphaned
                # from prior failed embed attempts). Re-embed up to 10 per session.
                orphaned = conn.execute("""
                    SELECT me.key, me.full_text FROM memory_entries me
                    LEFT JOIN memory_vec mv ON me.key = mv.key
                    WHERE mv.key IS NULL
                    LIMIT 10
                """).fetchall()

                recovery_count = 0
                for key, full_text in orphaned:
                    vec = client.embed_text(full_text)
                    if vec is not None:
                        store._upsert_vec(key, vec)
                        recovery_count += 1
                if recovery_count:
                    conn.commit()

            # Always log at INFO level so failures are visible in daemon logs
            total_actioned = stats["embedded"] + recovery_count
            if total_actioned > 0 or embed_failures > 0:
                logger.info(
                    "context_health: memory embeddings — "
                    "embedded=%d, recovered=%d, skipped=%d, removed=%d, failed=%d",
                    stats["embedded"], recovery_count,
                    stats["skipped"], stats["removed"], embed_failures,
                )
            elif stats["skipped"] > 0:
                logger.debug(
                    "context_health: memory embeddings — all %d entries up-to-date",
                    stats["skipped"],
                )
        except Exception as exc:
            # Log at WARNING (not debug!) — silent failures here caused memory_vec
            # to stay empty for weeks. This is P0 infrastructure.
            logger.warning("context_health: memory embedding sync FAILED: %s", exc)

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

        # 3. DDD Cultivation — delegated to orchestrator (extracted from PE-7 God Object)
        #    V2 dual-path: orchestrator.run() (legacy) + dispatcher.emit(SESSION_CLOSE)
        try:
            from core.ddd_orchestrator import DddCultivationOrchestrator
            orchestrator = DddCultivationOrchestrator()
            findings += orchestrator.run(root, ws_path)
        except Exception as exc:
            logger.warning("context_health: DDD orchestrator failed (non-blocking): %s", exc)

        # V2 dual-path: also emit SESSION_CLOSE via the singleton dispatcher.
        # Phase E will remove orchestrator.run() above once dispatcher is validated.
        try:
            from core.cultivation_dispatcher import EventType, emit_cultivation_event
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(
                    emit_cultivation_event(
                        EventType.SESSION_CLOSE,
                        source="context_health_hook",
                        payload={"trigger": "deep_check"},
                        priority=2,
                    )
                )
        except Exception as exc:
            logger.debug("context_health: v2 dispatcher emit skipped: %s", exc)

        # 3h. Adversarial meta-monitoring — surface degradation in session briefing
        try:
            from core.adversarial_meta import check_adversarial_health
            artifacts_dir = root / "Projects" / "SwarmAI" / ".artifacts"
            if artifacts_dir.is_dir():
                health = check_adversarial_health(artifacts_dir)
                if health.get("degradation_warning"):
                    findings.append(
                        f"[gap/high] Adversarial review may be degraded — "
                        f"{health['consecutive_zero_count']} consecutive pipeline runs "
                        f"with >50 changed lines had 0 findings. Consider rotating "
                        f"adversarial prompt."
                    )
        except Exception as exc:
            logger.debug("context_health: adversarial meta-check skipped: %s", exc)

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

    def _inject_ddd_into_knowledge(self, root: Path) -> None:
        """Delegate to DddCultivationOrchestrator (backward compat)."""
        from core.ddd_orchestrator import DddCultivationOrchestrator
        DddCultivationOrchestrator()._ch_inject_knowledge(root, str(root))

    def _detect_knowledge_staleness(self, root: Path, ws_path: str) -> list[str]:
        """Delegate to DddCultivationOrchestrator (backward compat)."""
        from core.ddd_orchestrator import DddCultivationOrchestrator
        return DddCultivationOrchestrator()._ch_knowledge_staleness(root, ws_path)

    def _check_ddd_staleness(self, root: Path, ws_path: str) -> list[str]:
        """Delegate to DddCultivationOrchestrator (backward compat)."""
        from core.ddd_orchestrator import DddCultivationOrchestrator
        return DddCultivationOrchestrator()._ch_ddd_staleness(root, ws_path)


    def _auto_apply_ddd_proposals(self, root: Path) -> None:
        """Delegate to DddCultivationOrchestrator (backward compat)."""
        from core.ddd_orchestrator import DddCultivationOrchestrator
        DddCultivationOrchestrator()._auto_apply_ddd_proposals(root)

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
                except ImportError:
                    pass  # memory_guard module not available yet
                except Exception as guard_exc:
                    logger.warning(
                        "context_health: MemoryGuard failed during OT archival: %s",
                        guard_exc,
                    )
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

    def _validate_entity_index(self, root: Path) -> list[str]:
        """Delegate to DddCultivationOrchestrator (backward compat)."""
        from core.ddd_orchestrator import DddCultivationOrchestrator
        return DddCultivationOrchestrator()._ch_entity_index(root, str(root))
