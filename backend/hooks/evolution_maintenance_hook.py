"""Post-session evolution maintenance hook.

Runs at session close to perform code-enforced EVOLUTION.md housekeeping
that was previously prompt-dependent (and never fired in practice):

- ``EvolutionMaintenanceHook``  — Implements ``SessionLifecycleHook``.
  Scans EVOLUTION.md entries, deprecates idle entries (>30 days),
  prunes deprecated entries with zero usage, and logs all actions
  to EVOLUTION_CHANGELOG.jsonl.

This hook uses ``locked_write.py`` functions directly (imported as a
library) rather than shelling out, for atomicity and testability.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.session_hooks import HookContext

logger = logging.getLogger(__name__)


def _resolve_transcripts_dir(base_dir: Path) -> Path:
    """Resolve the transcript directory with most-recent-activity heuristic.

    Instead of picking the first alphabetically-sorted subdirectory,
    find the subdir whose most recent .jsonl file has the latest mtime.
    Falls back to ``base_dir`` if no subdirs contain .jsonl files.
    """
    if not base_dir.is_dir():
        return base_dir

    best_dir = None
    best_mtime = 0.0

    for subdir in base_dir.iterdir():
        if not subdir.is_dir():
            continue
        jsonl_files = list(subdir.glob("*.jsonl"))
        if not jsonl_files:
            continue
        # Find the most recent .jsonl in this subdir
        latest_mtime = max(f.stat().st_mtime for f in jsonl_files)
        if latest_mtime > best_mtime:
            best_mtime = latest_mtime
            best_dir = subdir

    return best_dir if best_dir is not None else base_dir


# Sections that contain entries with Status + Usage Count fields
_MANAGED_SECTIONS = [
    ("Capabilities Built", "E"),
    ("Competence Learned", "K"),
]

# Regex to match entry headers in both formats:
#   Old: ### E001 | reactive | skill | 2026-03-07
#   New: ### E001 | 2026-03-07
_ENTRY_HEADER_RE = re.compile(
    r"^###\s+([EOKCF]\d{3})\s*\|.*?(\d{4}-\d{2}-\d{2})\s*$",
    re.MULTILINE,
)

# Regex to extract a field value: - **Field Name**: value
_FIELD_RE_TEMPLATE = r"^- \*\*{field}\*\*:\s*(.+)$"


def _get_field(entry_block: str, field_name: str) -> str | None:
    """Extract a field value from an entry block."""
    pattern = re.compile(
        _FIELD_RE_TEMPLATE.format(field=re.escape(field_name)),
        re.MULTILINE,
    )
    match = pattern.search(entry_block)
    return match.group(1).strip() if match else None


def _parse_entries(content: str, section_name: str) -> list[dict]:
    """Parse all entries in a section into structured dicts.

    Returns a list of dicts with keys: id, date, status, usage_count,
    start_pos, end_pos, block.
    """
    from scripts.locked_write import _find_section_range

    section_range = _find_section_range(content, section_name)
    if section_range is None:
        return []

    header_end, next_section_pos = section_range
    section_text = content[header_end:next_section_pos]

    entries = []
    # Find all ### headers in this section
    headers = list(_ENTRY_HEADER_RE.finditer(section_text))

    for i, match in enumerate(headers):
        entry_id = match.group(1)
        date_str = match.group(2)
        entry_start = match.start()
        entry_end = headers[i + 1].start() if i + 1 < len(headers) else len(section_text)
        block = section_text[entry_start:entry_end]

        status = _get_field(block, "Status") or "active"
        usage_str = _get_field(block, "Usage Count") or "0"
        try:
            usage_count = int(usage_str)
        except ValueError:
            # Non-numeric values like "Daily", "Weekly", "Occasional" mean
            # the capability IS actively used — treat as high count to prevent
            # accidental deprecation.
            usage_count = 999 if usage_str.strip() else 0

        entries.append({
            "id": entry_id,
            "date": date_str,
            "status": status,
            "usage_count": usage_count,
            "block": block,
        })

    return entries


def _append_changelog(
    changelog_path: Path,
    action: str,
    entry_id: str,
    summary: str,
    source: str = "maintenance_hook",
) -> None:
    """Append a single JSONL line to the evolution changelog.

    Uses ``fcntl.flock`` on a ``.jsonl.lock`` sidecar file to prevent
    concurrent writes from corrupting the changelog (P0 fix, Req 5.1).
    Rotates when file exceeds 512 KB (keeps newest 500 entries).
    """
    line = json.dumps({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": action,
        "id": entry_id,
        "summary": summary,
        "source": source,
    })
    from utils.file_lock import flock_exclusive, flock_unlock
    lock_path = changelog_path.with_suffix(".jsonl.lock")
    try:
        with open(lock_path, "w") as lock_fd:
            flock_exclusive(lock_fd)
            try:
                with open(changelog_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                # Rotate inside the lock to prevent concurrent rotation
                from utils.jsonl_rotation import rotate_jsonl_if_oversized
                rotate_jsonl_if_oversized(changelog_path)
            finally:
                flock_unlock(lock_fd)
    except OSError as exc:
        logger.warning("Failed to append changelog: %s", exc)


class EvolutionMaintenanceHook:
    """Code-enforced EVOLUTION.md maintenance at session close.

    Performs three operations:
    1. Deprecation — entries with status=active idle >deprecation_days → deprecated
    2. Pruning — entries with status=deprecated + usage_count=0 + deprecated >30 days → removed
    3. Changelog — all actions logged to EVOLUTION_CHANGELOG.jsonl

    Uses locked_write.py's _set_field for atomic field updates.
    """

    name = "evolution_maintenance"

    def __init__(self, context_dir: Path | None = None, deprecation_days: int = 30) -> None:
        self._context_dir = context_dir
        self._deprecation_days = deprecation_days

    def _resolve_context_dir(self) -> Path | None:
        """Resolve the .context directory path."""
        if self._context_dir:
            return self._context_dir
        # Default: ~/.swarm-ai/SwarmWS/.context/
        home = Path.home()
        ctx = home / ".swarm-ai" / "SwarmWS" / ".context"
        return ctx if ctx.is_dir() else None

    async def execute(self, context: HookContext) -> None:
        """Run maintenance on EVOLUTION.md at session close."""
        ctx_dir = self._resolve_context_dir()
        if ctx_dir is None:
            logger.debug("No .context directory found, skipping evolution maintenance")
            return

        evo_path = ctx_dir / "EVOLUTION.md"
        if not evo_path.is_file():
            return

        changelog_path = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self._deprecation_days)

        try:
            content = evo_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read EVOLUTION.md: %s", exc)
            return

        # Quality gate: remove garbage entries BEFORE deprecation checks
        content = self._quality_gate(evo_path, content, changelog_path)

        # Data-point family folding: self-prune the NARRATIVE `## Corrections
        # Captured` region (the append-only landfill the E/K lifecycle below is
        # structurally blind to). Type-aware + fail-safe; re-reads content after.
        content = self._fold_corrections(evo_path, content, changelog_path)

        # System-prompt size control (AFTER fold has shrunk the Corrections region):
        # if EVOLUTION.md still exceeds the injection budget, move the lowest-value
        # NON-evergreen entries to the monthly shard (recall-backed). Evergreen core
        # is hard-protected. Runs AFTER fold so it measures the already-shrunk size
        # (avoids over-archiving). dedup vs the reclaim sweep in context_health_hook is
        # guaranteed by the shared archive_raw_lines content-signature dedup.
        try:
            evicted = self._size_evict(evo_path)
            if evicted:
                _append_changelog(
                    changelog_path, "size_evict", f"{evicted} entries",
                    f"Moved {evicted} low-value entr"
                    f"{'y' if evicted == 1 else 'ies'} to monthly shard "
                    f"(system-prompt size control, recall-backed)",
                    source="size_valve",
                )
                content = evo_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — size control must never break maintenance
            logger.warning("EVOLUTION size-evict skipped (error): %s", exc)

        deprecated_count = 0
        pruned_count = 0

        for section_name, _prefix in _MANAGED_SECTIONS:
            entries = _parse_entries(content, section_name)

            for entry in entries:
                try:
                    entry_date = datetime.strptime(
                        entry["date"], "%Y-%m-%d"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                # Deprecation: active + idle > cutoff + usage_count == 0
                if (
                    entry["status"] == "active"
                    and entry_date < cutoff
                    and entry["usage_count"] == 0
                ):
                    self._deprecate_entry(
                        evo_path, section_name, entry["id"], changelog_path
                    )
                    deprecated_count += 1
                    # Re-read content after modification
                    content = evo_path.read_text(encoding="utf-8")

                # Pruning: deprecated + usage_count == 0 + old enough
                elif (
                    entry["status"] == "deprecated"
                    and entry["usage_count"] == 0
                    and entry_date < cutoff
                ):
                    self._prune_entry(
                        evo_path, section_name, entry["id"], changelog_path
                    )
                    pruned_count += 1
                    # Re-read content after modification
                    content = evo_path.read_text(encoding="utf-8")

        if deprecated_count or pruned_count:
            logger.info(
                "Evolution maintenance: deprecated=%d, pruned=%d",
                deprecated_count,
                pruned_count,
            )

        # Check governance 3x threshold for promotion candidates
        self._check_promotion_threshold(evo_path, content)

        # Evolution v3: auto-resolve correction classes after 30 days silence
        try:
            from core.evolution.correction_tracker import CorrectionClassTracker
            tracker = CorrectionClassTracker()
            resolved = tracker.check_auto_resolve()
            if resolved:
                for cls in resolved:
                    _append_changelog(
                        changelog_path, "auto_resolve", cls,
                        f"Correction class {cls} resolved (30d no recurrence post-gate)",
                        source="correction_tracker",
                    )
                logger.info("Correction tracker auto-resolved: %s", resolved)
        except Exception as exc:
            logger.debug("Correction tracker auto-resolve skipped: %s", exc)

        # Evolution v3 Phase 1: classify + route NEW corrections (watermark-gated).
        # Operational (tool_failure) auto-counts; cognitive (user_correction) parks
        # in the pending-confirm queue for the human Intake Gate. Degrade-to-log.
        try:
            from core.evolution.governance_router import classify_new_corrections

            # Closed taxonomy (SOUL/EVOLUTION.md). Reserved for Phase-2 recurrence
            # matching; Phase 1 passes it for a stable signature.
            summary = classify_new_corrections(
                evolution_classes=["CLASS_A", "CLASS_B", "CLASS_C"]
            )
            if summary.get("processed"):
                logger.info(
                    "judgment classifier: %d processed (%d operational, %d cognitive, %d skipped)",
                    summary["processed"], summary["operational"],
                    summary["cognitive"], summary["skipped"],
                )
        except Exception as exc:
            logger.debug("judgment classifier skipped: %s", exc)

        # Evolution v3 Phase 2: escalation ladder. For each tracked class, decide
        # whether a recurring pattern (count>=3, no existing structural fix) warrants
        # a RULE proposal. Writes proposal DATA to .evolution_proposals.json only —
        # NEVER auto-writes SOUL/AGENT/STEERING. Degrade-to-log.
        try:
            from core.evolution.correction_tracker import CorrectionClassTracker
            from core.evolution.governance_router import escalate_class

            esc_tracker = CorrectionClassTracker()
            proposed = 0
            for cls in esc_tracker.class_names():
                if escalate_class(cls, esc_tracker):
                    proposed += 1
            if proposed:
                logger.info("escalation ladder: %d rule proposal(s) surfaced", proposed)
        except Exception as exc:
            logger.debug("escalation ladder skipped: %s", exc)

        # NOTE: the evolution CYCLE (mine transcripts + Bedrock, ~5 min) is NO
        # LONGER run here. It was removed (run_6ac3fc0b) because a ~293s job on
        # the 180s-budget session-close hook timed out before it could advance
        # .evolution_last_run, re-triggering every session (59x/day) and spawning
        # uncancellable zombie threads. The cycle is now triggered SOLELY by the
        # scheduled `evolution-cycle` job (jobs/system_jobs.py). This hook keeps
        # ONLY the cheap governance work above (quality gate, deprecation,
        # promotion threshold, v3 classifier — all ~7ms file ops on EVOLUTION.md).

    # Regex: commit hash pattern (7+ hex chars at the start of description)
    _COMMIT_HASH_RE = re.compile(r"^[a-f0-9]{7}")

    def _quality_gate(
        self, evo_path: Path, content: str, changelog_path: Path
    ) -> str:
        """Quality gate: remove garbage entries and fix duplicate IDs.

        Called from execute() BEFORE deprecation checks.

        1. Acquire flock on EVOLUTION.md
        2. Re-read file content (authoritative under lock)
        3. Parse "Competence Learned" — remove entries where description
           is <20 chars OR starts with a commit hash pattern.
        4. Parse "Corrections Captured" — detect duplicate IDs, renumber
           the later occurrence to the next available C ID.
        5. Log all removals/renumbers to EVOLUTION_CHANGELOG.jsonl.
        6. Write back and release lock.

        Returns the (possibly modified) content string.
        """
        from scripts.locked_write import _find_entry_in_section
        from utils.file_lock import flock_exclusive, flock_unlock

        lock_path = evo_path.with_suffix(evo_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        try:
            fd = open(lock_path, "w")
            flock_exclusive(fd)

            # Re-read under lock — authoritative content
            try:
                content = evo_path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("Cannot read EVOLUTION.md under lock: %s", exc)
                return content

            modified = False

            # ── Step 1: Clean garbage competence entries ──
            competence_entries = _parse_entries(content, "Competence Learned")
            garbage_ids: list[str] = []

            for entry in competence_entries:
                desc = _get_field(entry["block"], "Competence") or ""
                # Garbage if description has <3 words (PE-review: char count
                # was fragile — "Use uv" is 6 chars but 2 words and legitimate.
                # Word count better separates garbage from terse-but-real entries.)
                if len(desc.split()) < 3:
                    garbage_ids.append(entry["id"])
                    continue
                # Garbage if starts with commit hash pattern
                if self._COMMIT_HASH_RE.match(desc):
                    garbage_ids.append(entry["id"])
                    continue

            # Remove garbage entries (reverse order to preserve positions)
            for entry_id in reversed(garbage_ids):
                entry_range = _find_entry_in_section(content, "Competence Learned", entry_id)
                if entry_range is not None:
                    start, end = entry_range
                    content = content[:start] + content[end:]
                    modified = True
                    _append_changelog(
                        changelog_path, "quality_gate_remove", entry_id,
                        f"Removed garbage competence entry (short or commit-hash)",
                        source="quality_gate",
                    )
                    logger.debug("Quality gate: removed garbage competence %s", entry_id)

            # ── Step 2: Fix duplicate correction IDs ──
            corrections = _parse_entries(content, "Corrections Captured")
            seen_ids: dict[str, bool] = {}
            # Find max existing C-ID for renumbering
            all_c_ids = re.findall(r"### C(\d+)", content)
            next_c_num = max((int(x) for x in all_c_ids), default=0) + 1

            for entry in corrections:
                eid = entry["id"]
                if eid in seen_ids:
                    # Duplicate — renumber to next available
                    new_id = f"C{next_c_num:03d}"
                    # Find this specific duplicate occurrence in the content.
                    # Use _find_entry_in_section to locate the entry block precisely,
                    # then replace the header within that block.  This handles 3+
                    # duplicates correctly because each iteration re-parses content.
                    entry_range = _find_entry_in_section(content, "Corrections Captured", eid)
                    if entry_range is not None:
                        start, end = entry_range
                        block = content[start:end]
                        # Only rename the LAST occurrence of this ID in the section
                        # (first occurrence keeps the original ID)
                        all_positions = [m.start() + start for m in re.finditer(
                            re.escape(f"### {eid} "), content
                        )]
                        if len(all_positions) >= 2:
                            # Replace at the last position (preserves first occurrence)
                            last_pos = all_positions[-1]
                            old_header = f"### {eid} "
                            new_header = f"### {new_id} "
                            content = (
                                content[:last_pos]
                                + new_header
                                + content[last_pos + len(old_header):]
                            )
                            modified = True
                            _append_changelog(
                                changelog_path, "quality_gate_renumber", new_id,
                                f"Renumbered duplicate {eid} -> {new_id}",
                                source="quality_gate",
                            )
                            logger.debug(
                                "Quality gate: renumbered duplicate %s -> %s",
                                eid, new_id,
                            )
                            next_c_num += 1
                else:
                    seen_ids[eid] = True

            if modified:
                evo_path.write_text(content, encoding="utf-8")

        finally:
            if fd is not None:
                try:
                    flock_unlock(fd)
                except OSError:
                    pass
                fd.close()

        return content

    # Cap: how many foldable data-points to KEEP per family before archiving
    # the rest. cap=2 = anchor + capstone (leanest); recent-2 fills remaining
    # slots when no capstone. Set by the goal design (user chose cap=2 for the
    # leanest live file — folded points remain fully traceable in the archive).
    _FOLD_CAP = 2

    # System-prompt size control: EVOLUTION.md is injected in FULL every session.
    # Over this token budget the size-valve moves the LOWEST-value entries to the
    # monthly shard (recall-backed cold storage — NOT deletion). The always-injected
    # core (evergreen judgment) stays bounded. XG directive 2026-08-14: "default 全量
    # 注入的 evolution 最大 15K token, 保证长青的每次都进, 其它依赖 recall".
    _ARCHIVE_THRESHOLD_TOKENS = 15_000   # HIGH watermark — valve TRIGGERS above this
    # LOW watermark — once triggered, evict DOWN TO this (not just under the trigger).
    # The 12–15K band is deliberate headroom so new entries land without re-triggering
    # the valve every session (the thrash the single-threshold version caused).
    _ARCHIVE_TARGET_TOKENS = 12_000

    # Markers that make an entry EVERGREEN core — HARD-PROTECTED, never size-evicted
    # (C046 red-line + O029/L75 lesson: never bury hard-won judgment). Entry-level
    # (NOT section-level) so the valve can distinguish a load-bearing correction from
    # a low-value one-liner inside the SAME section (Gate-1-B decidability fix).
    _EVERGREEN_MARKERS = (
        "**Pattern**", "**Durable tell**", "CAPSTONE", "METHOD FIX",
        "DIRECTIVE-OVERRIDE", "META-CORRECTION",
    )

    @staticmethod
    def _is_evergreen(entry_block: str) -> bool:
        """True if an entry carries core-judgment markers → never size-evictable.
        Entry-level (not section-level): a `### CLASS ...` parent header OR any of the
        core markers (**Pattern**/**Durable tell**/CAPSTONE/METHOD FIX/DIRECTIVE-
        OVERRIDE/META-CORRECTION) inside the block. A plain one-liner (O-Reference,
        an old capability record) carries none → evictable low-value."""
        for line in entry_block.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("### CLASS "):
                return True
        return any(m in entry_block for m in EvolutionMaintenanceHook._EVERGREEN_MARKERS)

    @staticmethod
    def _evolution_archive_shard(today: "date | None" = None) -> str:
        """Monthly archive shard name — mirrors the proven MEMORY pattern
        (context_health_hook:2126 f'MEMORY-archive-{today:%Y-%m}.md'). ALL EVOLUTION
        archive writers (fold, reclaim, size-valve) resolve their target through this
        one helper so the write-month is computed in ONE place, not per-site."""
        if today is None:
            today = datetime.now(timezone.utc).date()
        return f"EVOLUTION-archive-{today.strftime('%Y-%m')}.md"

    def _fold_corrections(
        self, evo_path: Path, content: str, changelog_path: Path
    ) -> str:
        """Self-prune the `## Corrections Captured` narrative region.

        Folds only RECURRENCE/CONTAINMENT DATA-POINT sub-bullets that exceed the
        per-family cap; protects METHOD FIX / capstone / etc. Fail-safe: on ANY
        error the ORIGINAL content is preserved (never eats correction history).
        Returns the (possibly-updated) content; re-reads from disk after a write.
        """
        try:
            from hooks.data_point_folding import fold_corrections_section
            from scripts.locked_write import (
                locked_read_modify_write, LockedWriteError, _find_section_range,
            )
        except ImportError as exc:
            logger.debug("data-point folding unavailable: %s", exc)
            return content

        try:
            result = fold_corrections_section(content, cap=self._FOLD_CAP)
        except Exception as exc:
            # Fail-safe (Gate-1 F4): a folding bug must NEVER corrupt EVOLUTION.md.
            logger.warning("Corrections folding skipped (compute error): %s", exc)
            return content

        if not result.changed:
            return content

        # Extract the NEW Corrections body from the folded content so we can
        # write it back via the same section-range contract locked_write uses.
        rng = _find_section_range(result.new_content, "Corrections Captured")
        if rng is None:
            logger.warning("Corrections folding skipped: section vanished post-fold")
            return content
        body_start, body_end = rng
        new_body = result.new_content[body_start:body_end]

        try:
            # ORDER MATTERS (Gate-2 HIGH #2): replace the EVOLUTION.md body
            # FIRST — it carries the `<!-- folded archived=... -->` idempotency
            # marker. Only AFTER that succeeds do we append to the archive. If
            # the archive append then fails, we have a marker (so the next run
            # is still a no-op — no re-fold, no duplicate archive) and the
            # folded text is recoverable from EVOLUTION's own history. The old
            # order (archive-first) duplicated archive content on every
            # failed-replace cycle because the marker was never written.
            # 1) Replace the Corrections body in EVOLUTION.md (atomic, locked).
            locked_read_modify_write(
                evo_path, "Corrections Captured", new_body, mode="replace",
            )
            # 2) Append folded blocks to the MONTHLY archive shard via the shared
            #    archive_raw_lines chokepoint (converged with reclaim + size-valve;
            #    dedup_by_signature=True makes fold+valve double-move structurally
            #    impossible). Forward-append recovery path — NOT a backup copy
            #    (STEERING #2 bans backup-copy reflex). Lands in gitignored .context/
            #    via source_path (private partition).
            if result.archived_blocks:
                from core.ddd_entry_lifecycle import archive_raw_lines
                shard = self._evolution_archive_shard()
                header = (
                    f"<!-- folded {len(result.archived_blocks)} data-point(s) "
                    f"from Corrections Captured -->"
                )
                try:
                    archive_raw_lines(
                        evo_path.parent, result.archived_blocks, shard,
                        source_path=evo_path,
                        block_header=header,
                        create_header=(
                            "# EVOLUTION Archive — folded data-points\n\n"
                            "Full text of RECURRENCE/CONTAINMENT DATA-POINTs folded "
                            "out of EVOLUTION.md's Corrections Captured section to "
                            "keep the live cognitive file lean. Traceable by run-id."
                        ),
                        dedup_by_signature=True,
                    )
                except (OSError, ValueError) as arch_exc:
                    # Body already folded+marked; a failed archive append is NOT
                    # data loss (folded text lives in EVOLUTION history) and will
                    # NOT re-fold (marker present). Log and continue.
                    logger.warning(
                        "Corrections folded but archive append failed "
                        "(recoverable, no re-fold): %s", arch_exc,
                    )
            _append_changelog(
                changelog_path, "fold_corrections",
                f"{result.families_folded} families",
                f"Folded {result.bullets_archived} data-point(s) to archive "
                f"across {result.families_folded} families (cap={self._FOLD_CAP})",
                source="data_point_folding",
            )
            logger.info(
                "Corrections folded: %d data-point(s) archived across %d families",
                result.bullets_archived, result.families_folded,
            )
            return evo_path.read_text(encoding="utf-8")
        except (OSError, LockedWriteError) as exc:
            # Fail-safe on write error: keep original in-memory content.
            logger.warning("Corrections folding write failed: %s", exc)
            return content

    # ── Low-value ordering for the size-valve (least → most valuable) ──
    # Sections whose entries are relatively low-value are evicted FIRST. Core
    # judgment sections (Corrections, Design Philosophy) are last-resort and their
    # evergreen entries are hard-protected regardless. "Optimizations Learned"
    # O-Reference one-liners are the author-declared "archived for lookup" content.
    _EVICT_SECTION_ORDER = (
        "Failed Evolutions",
        "Competence Learned",
        "Capabilities Built",
        "Optimizations Learned",
        "Corrections Captured",
    )

    def _size_evict(self, evo_path: Path, threshold_tokens: int | None = None,
                    target_tokens: int | None = None) -> int:
        """System-prompt size control (runs AFTER fold+dedup have shrunk the file).

        HIGH/LOW WATERMARK (hysteresis — Gate-1 refined): the valve TRIGGERS only when
        tokens exceed the HIGH watermark `threshold` (_ARCHIVE_THRESHOLD_TOKENS=15K), but
        once triggered it evicts the LOWEST-value NON-evergreen entries DOWN TO the LOW
        watermark `target` (_ARCHIVE_TARGET_TOKENS=12K) — NOT just until it crosses back
        under the trigger. The 12–15K band is intentional HEADROOM so new entries land
        without re-triggering the valve every session (the thrash the single-threshold
        version caused: it stopped ~89 tok under 15K). Evergreen core is NEVER evicted
        (P6: only-core-over-target → stop + raise-the-cap log, don't cut judgment).

        Move-not-delete: every evicted block is appended to the shard via the shared
        archive_raw_lines chokepoint (dedup_by_signature=True → no double-move with
        fold) BEFORE it is stripped from the live file, so nothing is lost.
        """
        from core.context_directory_loader import ContextDirectoryLoader
        from core.ddd_entry_lifecycle import archive_raw_lines
        from scripts.locked_write import _find_section_range, LOCK_TIMEOUT
        from utils.file_lock import flock_exclusive_nb, flock_unlock

        threshold = threshold_tokens if threshold_tokens is not None else self._ARCHIVE_THRESHOLD_TOKENS
        target = target_tokens if target_tokens is not None else self._ARCHIVE_TARGET_TOKENS
        # Clamp: the low watermark can never exceed the high one (Gate-1 check 4) — a
        # threshold override below the default target would otherwise invert the
        # watermarks (stop-line above trigger-line → evict nothing / instant break).
        target = min(target, threshold)

        # Gate-2 finding G (HIGH): the read→evict→write critical section MUST hold the
        # SAME .md.lock flock every other EVOLUTION writer honors (anti-pattern #5) —
        # else a concurrent writer between our read and write is a lost-update that
        # silently clobbers the always-injected cognitive file. Mirror _prune_entry:
        # acquire the flock, re-read INSIDE the lock, mutate, write, release in finally.
        lock_path = evo_path.with_suffix(evo_path.suffix + ".lock")
        fd = None
        try:
            fd = open(lock_path, "w")
            deadline = time.monotonic() + LOCK_TIMEOUT
            while True:
                try:
                    flock_exclusive_nb(fd)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        logger.warning("Lock timeout in size-evict — skipping (no clobber)")
                        return 0
                    time.sleep(0.1)

            # Fresh read UNDER the lock (never trust a pre-lock read — that is the race).
            try:
                content = evo_path.read_text(encoding="utf-8")
            except OSError:
                return 0
            if ContextDirectoryLoader.estimate_tokens(content) <= threshold:
                return 0

            moved = self._size_evict_locked(
                evo_path, content, threshold, target,
                ContextDirectoryLoader, archive_raw_lines, _find_section_range,
            )
            return moved
        finally:
            if fd is not None:
                try:
                    flock_unlock(fd)
                finally:
                    fd.close()

    def _size_evict_locked(
        self, evo_path, content, threshold, target, ContextDirectoryLoader,
        archive_raw_lines, _find_section_range,
    ) -> int:
        """The size-evict critical section — MUST be called while holding the
        EVOLUTION.md flock (see _size_evict). Does the read-already-done →
        evict → write under the caller's lock. Evicts DOWN TO `target` (low
        watermark), not just under `threshold` (the trigger) — hysteresis headroom."""
        moved = 0
        shard = self._evolution_archive_shard()
        # Walk sections least-valuable-first; within each, evict non-evergreen entries.
        for section in self._EVICT_SECTION_ORDER:
            if ContextDirectoryLoader.estimate_tokens(content) <= target:
                break
            rng = _find_section_range(content, section)
            if rng is None:
                continue
            header_end, next_pos = rng
            section_text = content[header_end:next_pos]
            headers = list(_ENTRY_HEADER_RE.finditer(section_text))
            if not headers:
                # Non-###-structured section (e.g. Optimizations bold-label bullets):
                # evict individual non-evergreen bullet LINES.
                content, n = self._evict_bullet_lines(
                    evo_path, content, section, header_end, next_pos,
                    shard, target, ContextDirectoryLoader, archive_raw_lines,
                )
                moved += n
                continue
            # ### entry-structured section: evict whole non-evergreen entry blocks.
            evict_blocks: list[str] = []
            for i, m in enumerate(headers):
                start = m.start()
                end = headers[i + 1].start() if i + 1 < len(headers) else len(section_text)
                block = section_text[start:end]
                if not self._is_evergreen(block):
                    evict_blocks.append(block)
            for block in evict_blocks:
                if ContextDirectoryLoader.estimate_tokens(content) <= target:
                    break
                # Move to shard (recall-backed) BEFORE stripping from live file.
                try:
                    archive_raw_lines(
                        evo_path.parent, [block.rstrip("\n")], shard,
                        source_path=evo_path,
                        block_header=f"<!-- size-evicted from {section} -->",
                        create_header=(
                            "# EVOLUTION Archive — size-evicted entries\n\n"
                            "Lower-value entries moved out of EVOLUTION.md to keep the "
                            "always-injected file within the system-prompt budget. "
                            "Recall-backed cold storage — retrievable on demand."
                        ),
                        dedup_by_signature=True,
                    )
                except (OSError, ValueError) as exc:
                    logger.warning("size-evict archive append failed (skip block): %s", exc)
                    continue
                content = content.replace(block, "", 1)
                moved += 1
            evo_path.write_text(content, encoding="utf-8")

        final = ContextDirectoryLoader.estimate_tokens(content)
        if moved:
            logger.info(
                "EVOLUTION size-evict: moved %d low-value entr%s to %s "
                "(final ~%d tok, target %d)", moved, "y" if moved == 1 else "ies",
                shard, final, target,
            )
        # Raise-the-cap signal (P6): still above the HIGH watermark (the real budget)
        # after evicting everything non-evergreen → only judgment remains, never cut it.
        # Compares to `threshold` (not `target`): being in the 12–15K headroom band is
        # HEALTHY, not over-budget — only >15K with nothing left to evict is the signal.
        if final > threshold:
            logger.warning(
                "EVOLUTION over size budget (%d > %d tok) but only evergreen core "
                "remains — NOT evicting judgment. Raise-the-cap decision needed.",
                final, threshold,
            )
        return moved

    def _evict_bullet_lines(
        self, evo_path, content, section, header_end, next_pos,
        shard, target, loader_cls, archive_raw_lines,
    ) -> "tuple[str, int]":
        """Evict individual non-evergreen bullet LINES from a non-###-structured
        section (e.g. Optimizations Learned's O-Reference one-liners) DOWN TO the
        low-watermark `target`. Returns (new_content, moved_count)."""
        section_text = content[header_end:next_pos]
        lines = section_text.splitlines(keepends=True)
        moved = 0
        kept: list[str] = []
        evicted: list[str] = []
        for line in lines:
            stripped = line.strip()
            is_bullet = stripped.startswith("- ")
            if (is_bullet and not self._is_evergreen(line)
                    and loader_cls.estimate_tokens(content) > target):
                evicted.append(line.rstrip("\n"))
                moved += 1
                # Re-measure against a projected content (approx): drop from running.
                content = content.replace(line, "", 1)
            else:
                kept.append(line)
        if evicted:
            try:
                archive_raw_lines(
                    evo_path.parent, evicted, shard, source_path=evo_path,
                    block_header=f"<!-- size-evicted bullets from {section} -->",
                    create_header=(
                        "# EVOLUTION Archive — size-evicted entries\n\n"
                        "Recall-backed cold storage — retrievable on demand."
                    ),
                    dedup_by_signature=True,
                )
            except (OSError, ValueError) as exc:
                logger.warning("size-evict bullet archive failed: %s", exc)
                return content, 0
            evo_path.write_text(content, encoding="utf-8")
        return content, moved

    def _deprecate_entry(
        self, evo_path: Path, section: str, entry_id: str, changelog_path: Path
    ) -> None:
        """Set an entry's Status to deprecated via locked_write."""
        from scripts.locked_write import locked_field_modify, LockedWriteError
        try:
            locked_field_modify(
                evo_path, section, entry_id, "Status", "set-field", "deprecated"
            )
            _append_changelog(
                changelog_path, "deprecate", entry_id,
                f"Auto-deprecated: idle >{self._deprecation_days}d with 0 usage"
            )
            logger.debug("Deprecated %s in %s", entry_id, section)
        except (ValueError, LockedWriteError) as exc:
            logger.warning("Failed to deprecate %s: %s", entry_id, exc)

    # Bias class pattern: [Bias A], [Bias B], etc. in correction headers
    _BIAS_TAG_RE = re.compile(r"\[Bias ([A-D])\]")

    def _check_promotion_threshold(self, evo_path: Path, content: str) -> None:
        """Check if any bias class has reached the 3x promotion threshold.

        Scans corrections in EVOLUTION.md for [Bias X] tags and counts
        active entries per class. When a class reaches 3+, writes a
        governance candidate signal for the session briefing.

        Uses direct line scanning (not _parse_entries) because correction
        headers contain [Bias X] after the date — the $-anchored
        _ENTRY_HEADER_RE won't match them.

        Gracefully handles missing/empty EVOLUTION.md or missing
        Corrections section (first-run safety — OPS-3 fix).
        """
        # Guard: if no content or no Corrections section, no-op
        if "## Corrections Captured" not in content:
            logger.debug(
                "Governance threshold: no 'Corrections Captured' section, skipping"
            )
            return

        # Count ACTIVE corrections per bias class via direct line scanning.
        # Only count entries whose **Status** is 'active' — skip mitigated,
        # promoted, superseded, resolved (PE Finding #2 fix).
        bias_counts: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}

        in_corrections = False
        current_bias: str | None = None  # Bias from most recent header
        for line in content.splitlines():
            # Detect section boundaries
            if line.startswith("## Corrections Captured"):
                in_corrections = True
                continue
            if in_corrections and line.startswith("## ") and "Corrections" not in line:
                break
            if not in_corrections:
                continue

            # Track bias tag from header (but don't count yet)
            if line.startswith("### C") and "[Bias " in line:
                match = self._BIAS_TAG_RE.search(line)
                current_bias = match.group(1) if match else None
                continue

            # Only count when we confirm the entry is active
            if current_bias and "**Status**:" in line:
                if "active" in line and "mitigated" not in line:
                    bias_counts[current_bias] += 1
                current_bias = None  # Reset — status consumed

        # Signal any class at threshold
        threshold = 3
        promotion_candidates = {
            bias: count
            for bias, count in bias_counts.items()
            if count >= threshold
        }

        signal_path = evo_path.parent / ".governance_promotion_candidates.json"

        if promotion_candidates:
            # Write signal file atomically for session briefing
            try:
                signal_data = {
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "candidates": promotion_candidates,
                    "message": (
                        f"Governance promotion candidates detected: "
                        + ", ".join(
                            f"Bias {b} ({c}x)" for b, c in promotion_candidates.items()
                        )
                    ),
                }
                tmp_path = signal_path.with_suffix(".tmp")
                tmp_path.write_text(
                    json.dumps(signal_data, indent=2), encoding="utf-8"
                )
                os.replace(tmp_path, signal_path)
                logger.info(
                    "Governance: promotion threshold reached for %s",
                    promotion_candidates,
                )
            except OSError as exc:
                logger.debug("Cannot write promotion signal: %s", exc)
        else:
            # Clear stale signal file if no candidates remain
            if signal_path.exists():
                try:
                    signal_path.unlink()
                except OSError:
                    pass

    def _prune_entry(
        self, evo_path: Path, section: str, entry_id: str, changelog_path: Path
    ) -> None:
        """Remove a deprecated entry from EVOLUTION.md with file locking."""
        from scripts.locked_write import _find_entry_in_section, LOCK_TIMEOUT
        from utils.file_lock import flock_exclusive_nb, flock_unlock

        lock_path = evo_path.with_suffix(evo_path.suffix + ".lock")
        fd = None
        try:
            fd = open(lock_path, "w")
            deadline = time.monotonic() + LOCK_TIMEOUT
            while True:
                try:
                    flock_exclusive_nb(fd)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        logger.warning("Lock timeout pruning %s", entry_id)
                        return
                    time.sleep(0.1)

            content = evo_path.read_text(encoding="utf-8")
            entry_range = _find_entry_in_section(content, section, entry_id)
            if entry_range is None:
                return

            start, end = entry_range
            new_content = content[:start] + content[end:]
            evo_path.write_text(new_content, encoding="utf-8")

            _append_changelog(
                changelog_path, "prune", entry_id,
                f"Auto-pruned: deprecated + 0 usage + idle >{self._deprecation_days}d"
            )
            logger.debug("Pruned %s from %s", entry_id, section)
        except OSError as exc:
            logger.warning("Failed to prune %s: %s", entry_id, exc)
        finally:
            if fd is not None:
                try:
                    flock_unlock(fd)
                except OSError:
                    pass
                fd.close()
