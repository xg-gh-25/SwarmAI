"""Agent UI-action (ACT) channel — proprioception Run 2 (efferent/传出).

The afferent half (Run 1, SENSE) lets the agent PERCEIVE its own UI state. This
module is the efferent half: it lets the agent ACT on the UI — from a chat turn,
call the `ui_action` tool with an enum command, and the frontend executes it by
re-dispatching to the EXISTING `swarm:*` window handlers.

Security model (the whole point of this module — Gate-1 hardened):

1. **Structured enum, no raw injection.** The agent picks a `cmd` from a fixed
   set; it can NEVER supply a raw `swarm:*` event string. The event name is
   derived server-side from UI_COMMAND_ALLOWLIST, never from agent input.
2. **Fail-closed allowlist.** A cmd not in UI_COMMAND_ALLOWLIST is rejected —
   `validate_ui_command` returns None, `build_ui_command_event` returns None, and
   the orchestrator emits nothing. The allowlist is the ONLY authority.
3. **Non-destructive nav/display only.** Run 2's allowlist is limited to opening
   Canvas, switching a fullscreen nav overlay, and returning to chat — all
   `window`-target, none carrying a data payload. Explicitly EXCLUDED (would be a
   real hazard if reachable): `open-terminal-here` (spawns a PTY),
   `inject-chat-input` (could self-drive the chat), `open-file` (its resolver
   allows arbitrary host paths → information disclosure — Gate-1 BLOCK 3).
4. **Frontend owns the mapping (defense in depth).** The SSE event carries `cmd`
   (+ event/target for logging parity), but the frontend derives its dispatch
   event+target from ITS OWN cmd-keyed table — it does not trust the wire's event
   name. So even a buggy/compromised backend can only pick from the enum.

Mechanism: the agent calls the `ui_action` tool (a `create_sdk_mcp_server`
in-process tool). The tool body validates `cmd` and returns an ACK that points
the agent at the passive next-turn SENSE readback (what to verify in the
"## Current UI State" snapshot) — it is NOT synchronous confirmation, since the
tool body cannot observe the frontend. The streaming orchestrator observes the
`ToolUseBlock` by name, validates `cmd` again, and yields an additive
`{type: "ui_command", cmd, event, target}` SSE event (the SDK still delivers the
tool's normal result to the agent). This mirrors the `file_changed` emit pattern.
This ACK closes the proprioception feedback arc: ACT (this emit) → next-turn
SENSE snapshot → the agent verifies the effect took hold.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional, TypedDict

from core.project_registry import get_swarmws

logger = logging.getLogger(__name__)


class UiCommandEntry(TypedDict):
    event: str  # the swarm:* CustomEvent name the frontend will dispatch
    target: str  # "window" | "document" — which EventTarget the listener is on


# ── The allowlist — the security SSOT ───────────────────────────────────────
# Every entry is a VERIFIED-LIVE, non-destructive, non-data-carrying nav/display
# command. Keep in sync with desktop/src/utils/uiCommands.ts UI_COMMAND_TABLE.
#
# Run 2 scope = window-target nav/display only:
#   - open-canvas          → ThreeColumnLayout window listener
#   - back-to-chat         → useExclusiveOverlay window listener (closes overlays)
#   - show-* (9 overlays)  → useExclusiveOverlay ALL_SHOW_EVENTS (all window)
# DROPPED on purpose: open-file (host-path infoleak), toast/nav-activate (no
# live listener), show-library (not in ALL_SHOW_EVENTS). Anything side-effecting
# is NEVER added without an explicit human decision (STEERING / EVALUATE gate).
UI_COMMAND_ALLOWLIST: dict[str, UiCommandEntry] = {
    "open-canvas": {"event": "swarm:open-canvas", "target": "window"},
    # open-canvas-file: open a CURRENT-workspace file in Canvas (run_c0550cc2). This
    # is DISTINCT from the dropped raw `open-file`: it carries a `path` that rides the
    # EXISTING generic swarm:open-file → /workspace/file/resolve filter, which is
    # workspace-scoped and rejects abs/host paths + `..` traversal (returns 400 →
    # frontend drops). So ui_action adds NO path validation of its own — the generic
    # canvas file filter is the single authority. document-target (all open-file
    # dispatchers listen on document, per useCanvasHost EVENT-TARGET CONTRACT).
    "open-canvas-file": {"event": "swarm:open-file", "target": "document"},
    "back-to-chat": {"event": "swarm:back-to-chat", "target": "window"},
    "show-swarmws": {"event": "swarm:show-swarmws", "target": "window"},
    "show-brain-hub": {"event": "swarm:show-brain-hub", "target": "window"},
    "show-context": {"event": "swarm:show-context", "target": "window"},
    "show-pipeline": {"event": "swarm:show-pipeline", "target": "window"},
    "show-pollinate": {"event": "swarm:show-pollinate", "target": "window"},
    "show-history": {"event": "swarm:show-history", "target": "window"},
    "show-todo": {"event": "swarm:show-todo", "target": "window"},
    "show-jobs": {"event": "swarm:show-jobs", "target": "window"},
    # Capabilities domain (run_b5d98151) — opens the CapabilitiesOverlay
    # ("what your AI can do" — skills + connections). Payload-less show-only:
    # frontend derives event/target from its own table, never trusts wire data;
    # internal-skill filtering is enforced backend-side regardless of opener.
    "show-capabilities": {"event": "swarm:show-capabilities", "target": "window"},
    # New Brain launcher — non-destructive: opens the collect-modal only; "Create"
    # still routes through chat (autoSend:false, human reviews before send). Safe
    # for the agent to open per the Run-2 nav/display charter.
    "show-new-brain": {"event": "swarm:show-new-brain", "target": "window"},
    # Unified Need You channel (2026-08-08) — opens the needs-you overlay (the
    # AlertsPill's fullscreen view). Payload-less show-only nav; the agent uses it
    # to ACT on its own attention queue when the user says "show me Need You".
    "show-needs-you": {"event": "swarm:show-needs-you", "target": "window"},
    # Community overlay (run_5165013e) — opens the CommunityOverlay (SwarmAI's
    # two-way membrane: inbound signals + outbound engagement). Payload-less
    # show-only nav; the agent opens it when the user asks about signals /
    # community / subscriptions. Read-only in Phase-1.
    "show-community": {"event": "swarm:show-community", "target": "window"},
}

# The enum the agent chooses from — sorted for a stable tool schema.
UI_COMMAND_IDS: list[str] = sorted(UI_COMMAND_ALLOWLIST.keys())


def validate_ui_command(cmd: object) -> Optional[UiCommandEntry]:
    """Return the allowlist entry for *cmd*, or None (fail-closed).

    Rejects: unknown ids, destructive/excluded ids, raw ``swarm:*`` strings, and
    any non-str / empty input. This is the single authority — a None result means
    "never emit, never dispatch".
    """
    if not isinstance(cmd, str) or not cmd:
        return None
    return UI_COMMAND_ALLOWLIST.get(cmd)


def _expected_post_state(cmd: str) -> str:
    """The observable post-state to verify AFTER dispatching *cmd*, phrased as the
    SENSE-rendered signal the agent will actually see next turn.

    The agent perceives its UI via the "## Current UI State" section that
    prompt_builder._render_ui_context_section injects — which is PROSE, not the
    raw schema fields. So this points at the RENDERED line, not `canvas.open` /
    `active_overlay`:
      - Canvas  → the "Canvas (output panel): … open …" line
      - overlay → the "Open overlay: …" line (we echo the cmd id rather than the
                  human label, so this never drifts from _OVERLAY_LABELS and needs
                  no import of prompt_builder — which imports THIS module).
      - back-to-chat → that "Open overlay:" line should be ABSENT (overlays closed).

    Assumes *cmd* is already allowlist-validated (callers gate on validate_ui_command).
    """
    if cmd == "open-canvas-file":
        return (
            "the 'Current UI State' section should show a 'Canvas (output panel): "
            "… open …' line AND the 'Open file:' line should name the file you "
            "opened (if it's absent, the path was outside the workspace or not found "
            "→ the generic file filter dropped it)"
        )
    if cmd == "open-canvas":
        return (
            "the 'Current UI State' section should show a "
            "'Canvas (output panel): … open …' line"
        )
    if cmd == "back-to-chat":
        return (
            "the 'Current UI State' section should have NO 'Open overlay:' line "
            "(overlays closed, back to chat/Canvas)"
        )
    if cmd.startswith("show-"):
        return (
            "the 'Current UI State' section should show an 'Open overlay:' line "
            f"for the '{cmd}' view"
        )
    # Any other allowlisted cmd (future-proofing) — generic pointer, still honest.
    return (
        f"the 'Current UI State' section should reflect the '{cmd}' action"
    )


def build_ui_ack(cmd: object, path: object = None) -> Optional[str]:
    """Build the SUCCESS ack text for an allowlisted *cmd*, or None if not
    allowlisted (fail-closed — the caller returns the rejection instead).

    The ack is NOT a synchronous confirmation (the tool body cannot see the
    frontend). It is a pointer to the PASSIVE next-turn SENSE readback: it names
    what the agent should verify in the 'Current UI State' snapshot on its next
    turn, and says the effect is unconfirmed until then.
    """
    if validate_ui_command(cmd) is None:
        return None
    expected = _expected_post_state(cmd)  # type: ignore[arg-type]  # validated str
    target = f"'{cmd}'"
    if cmd in _PATH_CARRYING_CMDS and isinstance(path, str) and path:
        target = f"'{cmd}' (path={path})"
    return (
        f"Dispatched {target} to the UI. This is NOT synchronous confirmation — "
        f"the frontend applies it after this turn. On your NEXT turn, verify: "
        f"{expected}. If it doesn't, the command did not reach the UI."
    )


# Commands that carry a `path` payload (per-cmd opt-in, mirroring the frontend's
# "re-introduce a payload ONLY per-cmd" rule). ONLY open-canvas-file: its path is
# NOT validated here — it rides the generic workspace-scoped swarm:open-file filter
# (/workspace/file/resolve), which is the single authority that rejects abs/host
# paths + `..`. A pure-nav command never gains a path key even if one is supplied.
_PATH_CARRYING_CMDS = frozenset({"open-canvas-file"})


def build_ui_command_event(
    cmd: object, path: object = None, allow_abs: bool = False
) -> Optional[dict]:
    """Build the ``ui_command`` SSE payload for *cmd*, or None if not allowlisted.

    Fail-closed at the source: a non-allowlisted cmd yields no event. The payload
    carries the derived event/target for frontend cross-check + logging parity —
    but the frontend re-derives its own dispatch from its own table (never trusts
    these fields blindly).

    ``path`` is attached ONLY for the path-carrying commands (open-canvas-file)
    and IGNORED for every other (pure-nav) command. This function IS a
    session-type security gate for the path (run_c0550cc2 / run_cbaecb86) — it is
    the *sole authority* on whether the agent may reach the resolver with an
    absolute host path:

      * ``..`` traversal is ALWAYS rejected (every session type).
      * an ABSOLUTE path is rejected UNLESS ``allow_abs`` is True. The caller sets
        ``allow_abs = not self._parent._has_channel_context`` — i.e. True only on a
        genuine LOCAL DESKTOP session (owner already has host file-picker access
        there), False on ANY channel incl. owner-over-channel (C041 leak defense).

    When ``allow_abs`` is True and an absolute path is admitted, the event carries
    ``allowAbs: True`` so the frontend's independent leading-``/`` reject (defense
    in depth) knows this backend-authored path was session-type-authorized. The
    frontend STILL rejects ``~`` and ``..`` unconditionally.
    """
    entry = validate_ui_command(cmd)
    if entry is None:
        logger.warning("ui_action: rejected non-allowlisted cmd %r (fail-closed)", cmd)
        return None
    ev = {
        "type": "ui_command",
        "cmd": cmd,
        "event": entry["event"],
        "target": entry["target"],
    }
    # Attach path ONLY for a path-carrying cmd AND only when a non-empty str given.
    if cmd in _PATH_CARRYING_CMDS and isinstance(path, str) and path:
        # SECURITY (Gate-2 CRITICAL, run_c0550cc2 + run_cbaecb86): the AGENT efferent
        # channel is workspace-RELATIVE only, EXCEPT a genuine local-desktop owner
        # session (allow_abs=True) where absolute paths are legitimate (the owner can
        # already open any host file via the picker). The downstream
        # /workspace/file/resolve HAPPILY resolves an ABSOLUTE host path
        # (/etc/passwd, ~/.aws/credentials) — that branch exists for USER-CLICK opens
        # of source-repo files, and the agent must NOT reach it on ANY channel
        # (owner-over-channel included: the owner on Slack can't browse the host FS,
        # so a prompt-injected turn could exfiltrate a host file into the channel —
        # the exact C041/Gate-1-BLOCK-3 infoleak). `..` traversal is rejected in ALL
        # cases. Fail-closed: reject → drop the whole event (no open).
        _norm = os.path.normpath(path)
        _is_abs = os.path.isabs(_norm)
        if _norm.startswith("..") or (_is_abs and not allow_abs):
            logger.warning(
                "ui_action: rejected open-canvas-file with non-workspace-relative "
                "path %r (allow_abs=%s — agent channel is workspace-relative unless "
                "local-desktop owner)", path, allow_abs
            )
            return None
        ev["path"] = path
        if _is_abs:
            # Audit trail: a sensitive capability (absolute host-file open) was
            # authorized for a local-desktop session. Also tells the frontend this
            # abs path was session-type-authorized (relaxes its leading-/ reject).
            logger.info(
                "ui_action: ALLOWED open-canvas-file absolute path %r "
                "(local-desktop owner session, allow_abs=True)", path
            )
            ev["allowAbs"] = True
    return ev


# ── Finish-time PR-review batch: surface a run's coding files (run_b8ea6d5c) ──
#
# CHANNEL A. Coding/source files are suppressed mid-run (needs_human_review →
# kind=source → dropped at useReferencedFiles.ts). At pipeline COMPLETE the agent
# calls the `surface_run_outputs` tool with the run_id; the streaming orchestrator
# observes that ToolUseBlock BY NAME (exactly like ui_action) and yields N
# `file_changed` SSE events built HERE — one per file this run committed — carrying
# kind="source-final", the ONE kind the rail accepts for a finish batch (mid-run
# `source` stays dropped). This reuses the rail-is-a-projection-of-the-tool-stream
# invariant, so persistence + tab-scope are automatically correct; run-commit (a
# CLI subprocess) structurally cannot push rows, which is why this rides the live
# COMPLETE turn instead. The tool reads run.json.commits (populated by run-commit),
# NOT agent-supplied paths — so it cannot fabricate rows for arbitrary files.

# Live-surfaced knowledge/content files (DDD/design docs written by an agent Write
# tool) already have a rail row and must NOT be re-emitted here. But the run's
# REPORT.md is the EXCEPTION (run_14e560ed): it is written by the run-report CLI
# subprocess, so the live emit never observed it and it is NOT in commits[].files —
# it has no other channel, so it IS appended here (LAST, kind=knowledge). The
# committed-source batch itself stays source-only.
def build_surface_events(run_id: object, workspace_root: object = None) -> list[dict]:
    """Build the finish-time batch of file_changed SSE events for a run.

    Emits, in order: (1) one ``kind=source-final`` event per committed source file
    from ``run.json.commits[].files`` (repo-relative paths the run actually
    committed), then (2) — appended LAST — the run's ``REPORT.md`` as a
    ``kind=knowledge`` event when it exists on disk, so the Canvas auto-selects and
    renders the report as CONTENT (last-write-wins) while source files stay as rows.
    Returns [] on any problem (fail-safe — never crashes the turn).

    Reads ``run.json.commits[].files`` (repo-relative paths the run actually
    committed) for ``run_id``, resolves each to a workspace-relative display path,
    and emits one ``file_changed`` event per file with ``kind="source-final"`` +
    ``operation="written"``. The frontend rail accepts source-final and persists it.
    """
    if not isinstance(run_id, str) or not run_id:
        return []
    try:
        import json
        from pathlib import Path
        if workspace_root is not None:
            ws = Path(str(workspace_root))
        else:
            ws = Path(get_swarmws())  # module-level import
        ws = ws.resolve()
        # Validate run_id BEFORE putting it in a glob pattern (a `*`/`..` run_id would
        # glob-match an arbitrary run or escape the runs dir). Same shape as the CLI's
        # _RUN_ID_PATTERN — an alnum/_/- token, no glob metachars, no separators.
        import re
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
            return []
        # Locate the run dir across projects (run ids are unique).
        run_json = None
        for proj in (ws / "Projects").glob("*/.artifacts/runs/" + run_id + "/run.json"):
            run_json = proj
            break
        if run_json is None or not run_json.exists():
            return []
        data = json.loads(run_json.read_text())
        events: list[dict] = []
        seen: set[str] = set()
        for commit in data.get("commits", []) or []:
            repo = commit.get("repo", "")
            sha = commit.get("sha", "")
            # baseRef = the commit's PARENT (<sha>^) — the file state BEFORE this run's
            # change (run_030dc98e). The frontend passes it to /workspace/file/committed
            # so a just-committed file diffs against its pre-run baseline, not HEAD
            # (== working tree → empty). Only a syntactically-safe short/full sha earns a
            # baseRef; else omit → the row falls back to HEAD (empty diff, but no error).
            base_ref = f"{sha}^" if re.fullmatch(r"[0-9a-fA-F]{7,40}", sha or "") else None
            for f in commit.get("files", []) or []:
                if not f or f in seen:
                    continue
                seen.add(f)
                # Absolute physical path (repo root + repo-relative file). Display
                # path stays repo-relative (what the user recognizes in a PR).
                abs_path = str(Path(repo) / f) if repo else f
                ev = {
                    "type": "file_changed",
                    "path": f,
                    "absolutePath": abs_path,
                    "operation": "written",
                    "relevance": "deliverable",
                    "kind": "source-final",
                }
                if base_ref:
                    ev["baseRef"] = base_ref
                events.append(ev)

        # ── D2 (run_57929039): gitignored-project fallback ────────────────────
        # A run in a GITIGNORED project (STEERING #5 — only Projects/SwarmAI/ is
        # git-trackable) has an EMPTY commits[] because `git add` refuses its files,
        # so the loop above emitted ZERO source rows. The run STILL wrote real source
        # (recorded in files_touched, the BUILD ground truth). Without this fallback
        # the Canvas OUTPUTS rail gets nothing to review, and the completion gate's
        # surface requirement (completion_gate.completion_surface_verdict) is vacuous
        # — a blind outputs_surfaced=true with no rows. Emit source rows from
        # files_touched when — and ONLY when — commits[] produced nothing (a trackable
        # project's committed rows WIN; the fallback never double-emits). Gitignored
        # files have no committed sha, so NO baseRef → the row renders working-tree
        # CONTENT, not a diff (same as REPORT.md, exactly right for uncommitted source).
        if not events:
            for f in data.get("files_touched", []) or []:
                if not isinstance(f, str) or not f or f in seen:
                    continue
                # ⚠️ WORKSPACE-CONTAINMENT (Gate-2 MEDIUM, run_57929039): files_touched
                # is NOT "source the run authored" — it is READ ∪ WRITE ∪ Bash-targets
                # (runtime_hooks._TRACKED_TOOLS includes "Read"), recorded as ABSOLUTE
                # paths with no scoping. Surfacing it blindly + auto-fetching into the
                # Canvas would leak ANY file the agent merely READ, incl. secrets under
                # $HOME (~/.aws/credentials, ~/.ssh/…). Unlike the commit path (git-
                # verified, repo-scoped), this input is agent-recorded and unverified.
                # So surface ONLY files that resolve UNDER the workspace; anything
                # outside ws is dropped (never surfaced, never fetched). This also
                # yields the recognizable ws-relative display for free — no absolute
                # path ever leaks into the row (F3).
                try:
                    _resolved = Path(f).resolve()
                    display = str(_resolved.relative_to(ws))
                except (ValueError, OSError):
                    continue  # outside workspace (or unresolvable) → do NOT surface
                # Skip a since-deleted / non-file path — it would emit a dead row that
                # 404s on fetch. is_file() also follows symlinks, so a dangling symlink
                # is dropped too (defense-in-depth on top of the containment check).
                if not _resolved.is_file():
                    continue
                seen.add(f)
                events.append({
                    "type": "file_changed",
                    "path": display,
                    "absolutePath": f,
                    "operation": "written",
                    "relevance": "deliverable",
                    "kind": "source-final",
                    # NO baseRef — a gitignored file has no committed parent to diff.
                })
        # ── Append the run's REPORT.md LAST (run_14e560ed) ────────────────────
        # The pipeline REPORT.md is written by the run-report CLI subprocess, so
        # the SDK never sees a Write tool for it (the live _build_file_write_events
        # emit can't observe it) and it is NOT in commits[].files — it has NO other
        # surface channel. Append it here as the LAST event: the frontend debounce
        # is last-write-wins, so REPORT.md becomes the auto-SELECTED file at finish,
        # while the source rows above stay as rows the user clicks. kind=knowledge
        # (rail-kept + auto-pop eligible) + NO baseRef → renders CONTENT, not a diff.
        # exists-guarded (defense-in-depth). The ORDERING is now enforced by the
        # CONSUMER: the orchestrator awaits ensure_report_for_run() BEFORE calling
        # this (run_f1fbf37d), so on the live COMPLETE path the report is present.
        # This guard stays because build_surface_events is PURE + may be called
        # standalone (tests, future callers) without that pre-step — absent report →
        # skip cleanly (source-only auto-open, never a broken row).
        report_path = run_json.parent / "REPORT.md"
        if report_path.exists():
            try:
                display = str(report_path.relative_to(ws))
            except ValueError:
                display = str(report_path)
            events.append({
                "type": "file_changed",
                "path": display,
                "absolutePath": str(report_path),
                "operation": "written",
                "relevance": "deliverable",
                "kind": "knowledge",
            })
        elif events:
            # LOUD-on-degradation (run_14e560ed + run_f1fbf37d): we emitted source rows
            # but REPORT.md is absent. Fail-safe still holds (source rows auto-open); the
            # report row is lost — make that observable. D2 (run_57929039): the source
            # rows may come from commits[] (trackable run) OR from the files_touched
            # fallback (gitignored run) — distinguish so the log line is not misleading
            # ("committed" would be false for a gitignored run whose commits[] is empty).
            _committed = bool(data.get("commits"))
            _kind = "committed pipeline run" if _committed else "gitignored run (files_touched fallback)"
            logger.warning(
                "build_surface_events: %s %r emitted %d source row(s) but REPORT.md "
                "is absent at %s — report generation did not produce a healthy report "
                "(check ensure_report_for_run / run-report), so the report row was skipped",
                _kind, run_id, len(events), report_path,
            )
        return events
    except Exception as e:  # noqa: BLE001 — hot-path fail-safe
        logger.warning("build_surface_events failed for run %r: %s", run_id, e)
        return []


async def build_surface_events_async(
    run_id: object, workspace_root: object = None
) -> list[dict]:
    """OFF-LOOP entry point for build_surface_events — the ONLY form the streaming
    hot path may call.

    ROOT-FIX (audit Finding 2): build_surface_events does synchronous FS I/O
    (glob + read + stat over run.json/REPORT.md). On the daemon's single shared
    event loop, calling it inline stalls EVERY session (and /health) on cold-cache /
    slow-disk until it returns. The orchestrator has three completion branches that
    each need this batch; requiring every caller to remember `await asyncio.to_thread`
    is exactly the omission that shipped (one of three sites was left on-loop).
    Wrapping the offload HERE makes off-loop the property of the entry point, not a
    discipline each call site must re-implement — the on-loop form is no longer
    reachable from the hot path. Same fail-safe contract as the sync fn (returns []
    on any problem; to_thread propagates no new exceptions the sync body didn't).
    """
    return await asyncio.to_thread(build_surface_events, run_id, workspace_root)


# Minimum bytes for a REPORT.md to count as "real" (a present-but-stub report is
# the documented failure mode — IMPROVEMENT.md: 5/6 runs once froze empty). Mirrors
# the run-update completion gate (artifact_cli.py `report_size < 500`).
_MIN_REPORT_BYTES = 500


def _run_report_sync(project: str, run_id: str, ws_root: str) -> None:
    """Blocking: generate REPORT.md for (project, run_id) by DIRECT-IMPORTING the
    run-report CLI logic — NOT a subprocess.

    Why direct-import (Gate-1, run_f1fbf37d): the orchestrator runs inside the
    frozen PyInstaller daemon, where ``sys.executable`` is ``python-backend`` (not a
    Python interpreter) and the source tree's relative script path need not exist on
    disk. A ``python backend/scripts/artifact_cli.py`` subprocess would therefore
    silently no-op in prod (pass in dev pytest, do nothing in the daemon). The
    sanctioned pattern is a direct import (memory_extractor.py does the same for
    exactly this reason). cmd_run_report only reads ``args.project`` / ``args.run_id``
    (+ ``getattr(args, "force", False)`` → default False, so a human-edited REPORT.md
    is never overwritten — the ``report_autogenerated`` flag protects it).

    ``ws_root`` is the SAME workspace root ``ensure_report_for_run`` used to LOCATE the
    run (via ``get_swarmws``). We pin ``SWARM_WORKSPACE`` to it for the call so the CLI's
    OWN resolver (``_get_workspace`` → env ``SWARM_WORKSPACE``, a DIFFERENT env var than
    ``get_swarmws``'s ``SWARMWS``) writes REPORT.md to the exact dir ensure will re-check
    (Gate-2 #2: unify the two resolvers so "generate to A, read back B" can't happen even
    if the two env vars ever diverge). Restored in a finally.

    Runs OFF the event loop via asyncio.to_thread (caller). Raising is fine — the
    async caller catches everything (fail-safe).
    """
    from types import SimpleNamespace
    from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

    prev = os.environ.get("SWARM_WORKSPACE")
    os.environ["SWARM_WORKSPACE"] = ws_root
    try:
        reg = ArtifactRegistry(_get_workspace())
        args = SimpleNamespace(project=project, run_id=run_id)
        cmd_run_report(args, reg)
    finally:
        if prev is None:
            os.environ.pop("SWARM_WORKSPACE", None)
        else:
            os.environ["SWARM_WORKSPACE"] = prev


async def ensure_report_for_run(run_id: object) -> bool:
    """CONSUMER-SIDE precondition guard for the finish-time surface (run_f1fbf37d).

    Root-fix for the surface-vs-run-report ORDERING COUPLING: the Canvas finish
    batch (build_surface_events) appends the run's REPORT.md as the auto-selected
    row, but ONLY if REPORT.md already exists on disk. REPORT.md is produced by a
    SEPARATE agent step (run-report). Relying on the agent to call run-report
    BEFORE surface_run_outputs is a "caller must remember the order" invariant — it
    can (and did) slip, silently dropping the report row.

    This moves the invariant to the consumer: BEFORE the orchestrator emits the
    surface batch, it awaits this. If the run committed source (commits[] non-empty)
    but REPORT.md is missing OR a <500-byte stub, we generate it in-process
    (off-loop). Then build_surface_events — still PURE — finds the report and emits
    its row. The agent can no longer surface "too early": surface makes itself
    not-early.

    Returns True if a healthy REPORT.md is present (pre-existing or freshly
    generated), False otherwise. The orchestrator ignores the return — it is
    fail-safe by construction: ANY failure degrades to the prior behavior (source
    rows still auto-open; the existing loud-WARN branch in build_surface_events
    still fires). NEVER raises — this runs on the hot streaming turn.

    OFF-LOOP (run_a1f4c2d8): the regeneration was already dispatched to a thread, but
    the PROBE in front of it was not — a ``Projects/*/.artifacts/runs/<id>/run.json``
    glob (one directory scan per project), a read_text of run.json, and a stat of
    REPORT.md all ran directly on the event loop. This function is awaited from the
    streaming token loop (streaming_orchestrator), so that probe stalled the loop on
    EVERY surface_run_outputs call, including the common case where the report is
    already healthy and there is nothing to do. Probe and regeneration are now each one
    thread hop; the async body only branches.
    """
    if not isinstance(run_id, str) or not run_id:
        return False

    def _probe() -> tuple[str, str, bool] | None:
        """(project, ws, healthy) — or None when this run needs no report guard.

        One helper, not three awaits: the glob → read → stat chain is a single logical
        lookup and splitting it would triple the hops for no benefit.
        """
        import json
        import re
        from pathlib import Path

        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
            return None
        ws = Path(str(get_swarmws())).resolve()
        run_json = None
        for hit in (ws / "Projects").glob("*/.artifacts/runs/" + run_id + "/run.json"):
            run_json = hit
            break
        if run_json is None or not run_json.exists():
            return None

        # Only a committed SOURCE run pairs a report with rows — a docs/knowledge-only
        # run (commits[] empty) has no source batch, so no report row to guard.
        data = json.loads(run_json.read_text())
        if not (data.get("commits") or []):
            return None

        # parents: [0]=<run_id>dir [1]=runs [2]=.artifacts [3]=<PROJECT> [4]=Projects
        project = run_json.parents[3].name
        report_path = run_json.parent / "REPORT.md"
        healthy = report_path.exists() and report_path.stat().st_size >= _MIN_REPORT_BYTES
        return project, str(ws), healthy

    def _report_healthy(ws: str) -> bool:
        """Re-check after regeneration — also off-loop (it is another stat)."""
        from pathlib import Path
        for hit in (Path(ws) / "Projects").glob("*/.artifacts/runs/" + run_id + "/REPORT.md"):
            try:
                return hit.stat().st_size >= _MIN_REPORT_BYTES
            except OSError:
                return False
        return False

    try:
        probed = await asyncio.to_thread(_probe)
        if probed is None:
            return False
        project, ws, healthy = probed
        if healthy:
            return True  # no-op: a real report is already there

        # Missing or stub → regenerate off the event loop (blocking CLI logic).
        # Pass the SAME ws root we located the run under, so the CLI writes REPORT.md
        # to the dir we re-check below (Gate-2 #2: unify the dual resolver).
        await asyncio.to_thread(_run_report_sync, project, run_id, ws)
        return await asyncio.to_thread(_report_healthy, ws)
    except Exception as e:  # noqa: BLE001 — hot-path fail-safe, never break the turn
        logger.warning("ensure_report_for_run failed for run %r: %s", run_id, e)
        return False


# ── Backend-auto Canvas surface on pipeline completion (run_beff6754) ─────────
# Canvas auto-open on pipeline completion had ONE trigger: the frontend
# useCanvasAutoSurface hook reacting to a file_changed(kind=knowledge) event, which
# build_surface_events emits — but the orchestrator only called it when it OBSERVED
# the agent invoke the surface_run_outputs tool. complete.md tells docs-only runs
# (0 source commits) to SKIP that call, so the most common pipeline (708 completed
# runs, the vast majority commits=0 WITH a REPORT.md) never emitted the knowledge
# event and Canvas never auto-opened. A prior 2026-08-05 fix added PROSE to
# complete.md ("remember to call surface") and did NOT hold — leaving the trigger on
# agent discipline is the recurring CLASS-A failure.
#
# The structural fix: the orchestrator OBSERVES the `run-update --status completed`
# Bash command and auto-fires build_surface_events. This pure function is the cheap
# PRE-FILTER — it recognizes such a command and extracts its run_id. It is NOT the
# authority: a BLOCKED completion still `return`s exit 0 from the CLI (artifact_cli
# gate prints an error and returns, it does NOT sys.exit non-zero), so the command
# string alone does not prove the run completed. The orchestrator confirms authority
# by reading run.json status == "completed" before emitting. This parser only decides
# "is this a completion-attempt worth checking?" — keeping the hot-path regex cheap.


def parse_completion_run_id(command: object) -> Optional[str]:
    """Return the run_id if `command` is an artifact_cli ``run-update`` invocation
    that sets ``--status completed``; else None. Pure, never raises.

    Recognizes BOTH flag forms — ``--status completed`` (the spaced form argparse
    accepts, what the CLI actually emits) AND ``--status=completed`` (a defensive
    hand-typed variant) — and is flag-order independent. Requires the command to be a
    ``run-update`` subcommand carrying BOTH ``--status completed`` and a ``--run-id``.

    This is a PRE-FILTER, not proof of completion (a blocked completion still exits 0).
    The orchestrator re-confirms against run.json status before surfacing.
    """
    if not isinstance(command, str) or not command:
        return None
    # Must be a run-update subcommand — else a foo.py that happens to carry the flags
    # (or an unrelated tool) would false-match.
    if "run-update" not in command:
        return None
    import re
    # (^|\s)--status completed | --status=completed  (value token is exactly
    # "completed", not merely containing it — a --reason 'not completed yet' must NOT
    # match). The leading (?:^|\s) anchors --status to a real token boundary so a
    # hypothetical sibling flag ending in "-status" can't substring-match (RP: matcher
    # robustness). The trailing (?:\s|$) keeps "completed" a whole value, not a prefix.
    if not re.search(r"(?:^|\s)--status[=\s]+completed(?:\s|$)", command):
        return None
    m = re.search(r"--run-id[=\s]+([A-Za-z0-9_-]+)", command)
    if not m:
        return None
    return m.group(1)


def read_run_status(run_id: object) -> Optional[str]:
    """Return run.json ``status`` for ``run_id`` (the AUTHORITY for "did it actually
    complete?"), or None if the run can't be found / read. Pure, never raises.

    The completion CLI prints an error and ``return``s (exit 0) when a gate BLOCKS —
    so the ``run-update --status completed`` command string is NOT proof the run
    completed. The orchestrator reads THIS after the completion command's successful
    tool_result and only surfaces when status == "completed". This is a single
    off-loop stat+read (the orchestrator wraps it in asyncio.to_thread).
    """
    if not isinstance(run_id, str) or not run_id:
        return None
    try:
        import json
        import re
        from pathlib import Path
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
            return None
        ws = Path(str(get_swarmws())).resolve()
        for hit in (ws / "Projects").glob("*/.artifacts/runs/" + run_id + "/run.json"):
            return json.loads(hit.read_text()).get("status")
        return None
    except Exception:  # noqa: BLE001 — pure/fail-safe reader on the hot path
        return None


# ── The SDK-MCP tool the agent calls ─────────────────────────────────────────

# Tool name as the agent sees it (SDK-MCP convention: mcp__<server>__<tool>).
UI_MCP_SERVER_NAME = "swarm_ui"
UI_ACTION_TOOL_NAME = "ui_action"
UI_ACTION_FULL_TOOL_NAME = f"mcp__{UI_MCP_SERVER_NAME}__{UI_ACTION_TOOL_NAME}"
SURFACE_OUTPUTS_TOOL_NAME = "surface_run_outputs"
SURFACE_OUTPUTS_FULL_TOOL_NAME = f"mcp__{UI_MCP_SERVER_NAME}__{SURFACE_OUTPUTS_TOOL_NAME}"
SENSE_ATTENTION_TOOL_NAME = "sense_attention"
SENSE_ATTENTION_FULL_TOOL_NAME = f"mcp__{UI_MCP_SERVER_NAME}__{SENSE_ATTENTION_TOOL_NAME}"


def format_attention_for_agent(brain: Optional[str] = None) -> str:
    """Render the unified Need You queue as agent-readable text (the SENSE half of
    the Need You channel — the afferent complement to the `show-needs-you` ACT).

    Reads the SAME backend authority the frontend overlay + brain-card use
    (core.attention_authority.collect), so the agent sees EXACTLY what the user
    sees — one source of truth. Read-only; never mutates. Grouped by tier
    (BLOCKING first) then brain, mirroring the overlay's double-axis layout so the
    agent can relay it faithfully and then dispatch each item (resume run / review
    proposal / triage job) via a normal chat action.
    """
    try:
        from .attention_authority import collect
        from jobs.paths import SWARMWS
    except Exception as e:  # pragma: no cover - import guard
        return f"(Could not load the attention authority: {e})"

    result = collect(SWARMWS, brain=brain)
    if not result.items:
        scope = f" for brain '{brain}'" if brain else ""
        return f"Need You{scope}: nothing needs you right now (0 items)."

    lines: list[str] = [
        f"Need You — {result.counts['blocking']} blocking, "
        f"{result.counts['review']} review"
        + (f" (brain '{brain}' only)" if brain else "") + ":",
    ]
    for tier, label in (("blocking", "🔴 BLOCKING (stopped — needs you now)"),
                        ("review", "🟡 REVIEW (self-advancing — confirm/override)")):
        tier_items = [it for it in result.items if it.tier == tier]
        if not tier_items:
            continue
        lines.append(f"\n{label}:")
        # group by brain (None → OS-level), preserving order
        seen_brains: list[Optional[str]] = []
        for it in tier_items:
            if it.brain not in seen_brains:
                seen_brains.append(it.brain)
        for b in seen_brains:
            lines.append(f"  [{b or 'OS-level'}]")
            for it in (x for x in tier_items if x.brain == b):
                lines.append(f"    • ({it.source}) {it.title}")
                msg = (it.dispatch or {}).get("message")
                if msg:
                    lines.append(f"        → to handle: {msg}")
    return "\n".join(lines)


def get_ui_mcp_server():
    """Build the in-process SDK-MCP server exposing the `ui_action` tool.

    The tool body is intentionally trivial: it validates and returns an ack
    string. The actual UI effect is produced by the streaming orchestrator, which
    observes this tool call and emits the `ui_command` SSE event. Returns the
    server config to merge into ClaudeAgentOptions.mcp_servers, or None if the SDK
    primitives are unavailable (fail-open on the SDK, not on the security gate).
    """
    try:
        from claude_agent_sdk import create_sdk_mcp_server, tool
    except Exception as e:  # pragma: no cover - SDK always present in prod
        logger.warning("ui_action tool unavailable (SDK import failed): %s", e)
        return None

    _cmd_list = ", ".join(UI_COMMAND_IDS)

    @tool(
        UI_ACTION_TOOL_NAME,
        (
            "Act on your OWN UI in the SwarmAI desktop app: open a panel, switch a "
            "navigation view, or open one of THIS session's workspace files in Canvas. "
            "Use when the user asks you to show/open something in the app. cmd MUST be "
            f"one of: {_cmd_list}. For 'open-canvas-file', also pass `path` = the "
            "workspace-relative file path to show in Canvas (e.g. "
            "'Knowledge/Designs/foo.md'); it is resolved by the workspace file "
            "filter, so only files inside the workspace open. Non-destructive only — "
            "this cannot write, delete, send, or run anything."
        ),
        {"cmd": str, "path": str},
    )
    async def ui_action(args: dict) -> dict:
        cmd = args.get("cmd")
        path = args.get("path")
        entry = validate_ui_command(cmd)
        if entry is None:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Rejected: '{cmd}' is not an allowed UI command. "
                        f"Allowed: {_cmd_list}."
                    ),
                }],
                "is_error": True,
            }
        # The orchestrator emits the ui_command SSE event on observing this call.
        # The ack points the agent at the passive next-turn SENSE readback (what to
        # verify in "## Current UI State") — it is NOT synchronous confirmation.
        return {"content": [{"type": "text", "text": build_ui_ack(cmd, path)}]}

    @tool(
        SURFACE_OUTPUTS_TOOL_NAME,
        (
            "At pipeline COMPLETE, surface THIS run's committed coding files as a "
            "batch of review rows in the Canvas OUTPUTS list — a local-PR review "
            "experience. Call this ONCE at the end of a pipeline run that committed "
            "source, after run-commit has recorded the commits. Pass `run_id` = the "
            "pipeline run id (e.g. 'run_b8ea6d5c'). The rows are read from the run's "
            "recorded commits (not from you) — non-destructive, display only."
        ),
        {"run_id": str},
    )
    async def surface_run_outputs(args: dict) -> dict:
        run_id = args.get("run_id")
        # The orchestrator observes this call and emits the batch of file_changed
        # events (build_surface_events). The tool body just acks + points at the
        # SENSE readback, mirroring ui_action (it cannot see the frontend).
        n = len(build_surface_events(run_id))
        if n == 0:
            return {"content": [{"type": "text", "text": (
                f"No committed source files found for run {run_id!r} — nothing to "
                "surface. (Did run-commit record commits for this run yet?)"
            )}]}
        return {"content": [{"type": "text", "text": (
            f"Surfacing {n} committed file(s) from run {run_id!r} to the Canvas "
            "OUTPUTS list. This is NOT synchronous — the frontend adds the rows "
            "after this turn. On your NEXT turn, verify the 'Current UI State' Canvas "
            "outputs count reflects them; click a row to review that file's changes."
        )}]}

    @tool(
        SENSE_ATTENTION_TOOL_NAME,
        (
            "SENSE your unified 'Need You' queue — everything across the whole OS "
            "that structurally needs the user: paused pipeline decisions, blocked "
            "escalations, pending knowledge/governance proposals, circuit-broken "
            "jobs. Use when the user asks to see, review, or handle 'Need You' / "
            "attention items, or when you want to check what is waiting before "
            "acting. Returns the SAME queue the user's Need You overlay shows "
            "(grouped by tier: BLOCKING first, then REVIEW; then by brain). "
            "Optionally pass `brain` = a project name to scope to one brain "
            "(OS-level governance is excluded from a per-brain query). Read-only — "
            "this reads state, it changes nothing; to act on an item, follow its "
            "'to handle' message as a normal chat step."
        ),
        {"brain": str},
    )
    async def sense_attention(args: dict) -> dict:
        # Read-only SENSE: return the live queue as text. Unlike ui_action /
        # surface_run_outputs (which ack + defer to a next-turn SSE effect), this
        # tool's RESULT IS the data — the agent reads it THIS turn and acts.
        brain = args.get("brain") or None
        try:
            text = format_attention_for_agent(brain if isinstance(brain, str) else None)
        except Exception as e:  # noqa: BLE001 — never break the turn on a read
            text = f"(Could not read the Need You queue: {e})"
        return {"content": [{"type": "text", "text": text}]}

    return create_sdk_mcp_server(
        name=UI_MCP_SERVER_NAME,
        tools=[ui_action, surface_run_outputs, sense_attention],
    )
