"""Hook configuration builder for Claude Agent SDK sessions.

Composes security hooks, permission hooks, skill access checkers, and
lifecycle hooks into the ``hooks`` dict for ``ClaudeAgentOptions``.

Key public symbols:

- ``HookRegistry``   — Register hooks by event, chain execution with
                       5s timeout per hook, first 'block' wins.
- ``build_hooks``    — Async entry point, returns (hooks_dict,
                       effective_allowed_skills, allow_all_skills)
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Awaitable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .permission_manager import PermissionManager

from claude_agent_sdk import HookMatcher

from .security_hooks import (
    create_pre_tool_logger,
    create_dangerous_command_gate,
    create_external_approval_gate,
    background_command_guard,
    create_active_run_commit_gate,
    create_session_link_gate,
    create_audit_marker_write_gate,
    create_run_amend_gate,
    bash_syntax_guard,
    create_image_read_dedup_guard,
    create_ask_question_gate,
    create_governance_file_gate,
    create_skill_access_checker,
)
from .gate_decision_log import with_decision_log
from . import hook_timing
from .verdict import Disposition, from_sdk, to_sdk
from .ask_question_manager import ask_question_manager
from .permission_manager import PERMISSION_ANSWER_TIMEOUT_SECONDS
from .agent_defaults import expand_allowed_skills_with_plugins

logger = logging.getLogger(__name__)

# Per-hook timeout — hooks that exceed this are killed silently.
HOOK_TIMEOUT_SECONDS = 5.0

# Type alias for SDK hook callback
HookCallback = Callable[..., Awaitable[dict[str, Any]]]


class HookRegistry:
    """Register SDK hooks by event type with chained execution.

    Multiple hooks for the same event execute sequentially in registration
    order.  If any hook returns ``{"decision": "block"}``, the chain
    short-circuits.  Each chained hook has a 5-second timeout — hanging hooks
    are killed silently — EXCEPT hooks registered with ``no_timeout=True``
    (long-blocking HITL gates), which are exempt and rely on their own internal
    bound (see ``register``'s ``no_timeout`` arg, run_6e780e00).

    Hooks with a ``matcher`` parameter (e.g., ``matcher="Bash"``) are
    registered as separate HookMatcher entries, not chained with
    unmatched hooks for the same event.
    """

    def __init__(self) -> None:
        # Key: (event, matcher|None) -> list of (name, hook_fn, no_timeout)
        self._hooks: dict[tuple[str, Optional[str]], list[tuple[str, HookCallback, bool]]] = defaultdict(list)

    def register(
        self,
        event: str,
        hook: HookCallback,
        name: str,
        matcher: Optional[str] = None,
        no_timeout: bool = False,
    ) -> None:
        """Register a hook for an SDK event.

        Args:
            event: SDK hook event name (e.g., "PreToolUse", "PostToolUse")
            hook: Async callable with signature (input_data, tool_use_id, context) -> dict
            name: Human-readable name for logging
            matcher: Optional tool matcher (e.g., "Bash", "Skill")
            no_timeout: When True, this hook is EXEMPT from the chain's
                ``HOOK_TIMEOUT_SECONDS`` guard. Reserved for hooks that
                LEGITIMATELY block for a long time awaiting a human — e.g.
                ``dangerous_command_gate`` (blocks up to 4h in
                ``wait_for_permission_decision``) and ``ask_question_gate``
                (blocks up to 4h in ``wait_for_answer``). Those hooks carry
                their OWN internal bound, so the 5s chain timeout would only
                ever *wrongly* cancel a live HITL prompt before the user can
                decide (run_6e780e00 — the approve-into-void root cause: the
                Bash slot has 5 hooks → chained → the gate's long wait was
                guillotined at 5s). Do NOT set this on a fast guard — the 5s
                timeout exists to stop a genuinely-hung hook (bash_syntax_guard
                et al.), and exempting one un-bounds that protection.
        """
        if event == "PreToolUse" and not getattr(hook, "_decision_logged", False):
            # Every PreToolUse deny/ask lands in gate_decisions.jsonl with the full
            # tool input, so gates never need their own [BLOCKED] logging to be audited.
            # A compose_gates group logs per member instead (its own flag).
            hook = with_decision_log(name, hook)
        if not no_timeout:  # a HITL hook's duration is a person's answer time
            hook = hook_timing.timed(name, hook)
        self._hooks[(event, matcher)].append((name, hook, no_timeout))

    def build_sdk_hooks(self) -> dict[str, list]:
        """Build the hooks dict for ClaudeAgentOptions.

        Returns:
            Dict mapping event names to lists of HookMatcher objects.
        """
        result: dict[str, list] = defaultdict(list)

        for (event, matcher), hooks in self._hooks.items():
            # A (event, matcher) group that contains ANY no_timeout=True hook is a
            # long-blocking HITL gate (dangerous_command_gate / ask_question_gate).
            # Forward a per-matcher `timeout` so the CLI does NOT cancel the blocking
            # hook at its ~600s default (run_1141ea02: the CLI applies
            # HookMatcher.timeout*1000 as setTimeout(abort) on the hook_callback wait,
            # uncapped on that path — proven against the CLI binary + SDK
            # client.py→query.py serialization). Fast-guard-only groups carry NO
            # timeout (None) → the CLI default stands, so a genuinely hung fast guard
            # is NOT granted a 4h window.
            group_timeout = (
                PERMISSION_ANSWER_TIMEOUT_SECONDS
                if any(no_timeout for _n, _fn, no_timeout in hooks)
                else None
            )

            if len(hooks) == 1:
                # Solo hook: bare-registered with NO wait_for wrapper (this is
                # already how a solo long-blocking hook like ask_question_gate
                # escapes the 5s timeout). no_timeout drives group_timeout above.
                name, fn, _no_timeout = hooks[0]
                the_hook = fn
            else:
                # Chain multiple hooks into a single callable
                the_hook = self._build_chain(hooks)

            hm_kwargs: dict[str, Any] = {"hooks": [the_hook]}
            if matcher:
                hm_kwargs["matcher"] = matcher
            if group_timeout is not None:
                hm_kwargs["timeout"] = group_timeout
            result[event].append(HookMatcher(**hm_kwargs))

        return dict(result)

    def _build_chain(
        self, hooks: list[tuple[str, HookCallback, bool]]
    ) -> HookCallback:
        """Build a chained hook from multiple hooks.

        Execution: sequential, first TERMINAL verdict (deny/hitl) wins, 5s timeout
        per hook — EXCEPT hooks registered with ``no_timeout=True`` (long-blocking
        HITL gates), which are awaited directly (their own internal bound applies).
        Non-terminal (allow/warn) results merge: ``hookSpecificOutput.additionalContext``
        and top-level ``additionalContext`` accumulate (newline-joined, in order); every
        other ``hookSpecificOutput`` field later-overrides (``_merge_specific``).

        Phase 1 refactor (verdict.py): the "what is terminal / which decision wins"
        judgement is now expressed on the ordered ``Disposition`` scale via
        ``from_sdk`` — ``Disposition >= HITL`` (decision=block / permissionDecision
        deny / ask / defer) is terminal and short-circuits, returning the gate's ORIGINAL
        dict verbatim (``to_sdk`` passthrough, so reason/systemMessage/
        permissionDecisionReason bypass fields survive — the lossless invariant). The
        field-ACCUMULATION for non-terminal results is kept as explicit dict merging:
        two allow hooks must BOTH contribute (one carries additionalContext, another
        updatedInput), which a single-winner ``merge_verdicts`` could not express — so
        Verdict governs the terminal/severity decision, dict-merge governs
        accumulation. This preserves every observable output of the legacy chain
        (pinned by the back-to-back equivalence tests in test_hook_builder.py).
        """

        async def chained(input_data: Any, tool_use_id: Any, context: Any) -> dict:
            combined: dict[str, Any] = {}

            for name, hook, no_timeout in hooks:
                try:
                    if no_timeout:
                        # EXEMPT (run_6e780e00): a legitimately long-blocking HITL
                        # gate (dangerous_command_gate 4h / ask_question_gate 4h).
                        # Wrapping it in the 5s guard would cancel the live approval
                        # prompt before the user can decide (the approve-into-void
                        # root cause). Its OWN wait (wait_for_permission_decision /
                        # wait_for_answer) is internally bounded, so no hang risk.
                        result = await hook(input_data, tool_use_id, context)
                    else:
                        result = await asyncio.wait_for(
                            hook(input_data, tool_use_id, context),
                            timeout=HOOK_TIMEOUT_SECONDS,
                        )
                    if not result:
                        continue

                    # Short-circuit on a TERMINAL verdict (Disposition >= HITL):
                    # decision=block / permissionDecision∈{deny,ask,defer}.
                    # Terminal means the CLI resolves the tool right
                    # there, so it must NOT be merged-then-overwritten by a later
                    # hook — without this a later allow-emitting hook silently
                    # clobbers an earlier guard's deny (the CRITICAL from Gate-2 in
                    # run_6af22b0d; deny/ask/defer terminality from run_7da67105).
                    # Returning to_sdk(verdict) hands back the ORIGINAL dict verbatim
                    # (verdict.sdk_payload passthrough), identical to the legacy
                    # `return result` — bypass fields (reason / systemMessage /
                    # permissionDecisionReason) are preserved. Only allow/warn are
                    # non-terminal and keep merging below.
                    verdict = from_sdk(result)
                    if verdict.disposition >= Disposition.HITL:
                        return to_sdk(verdict)

                    # Merge hookSpecificOutput: additionalContext accumulates (the CLI
                    # passes only this field to the model, so a later hook must not
                    # overwrite an earlier one's); every other field — later overrides
                    _merge_specific(combined, result)
                    # Merge other valid top-level keys
                    for key in ("decision", "reason", "systemMessage"):
                        if key in result and result[key] is not None:
                            combined[key] = result[key]
                    # Merge additionalContext — advisory hooks (governance_file_gate,
                    # apply_gate) return it top-level to inject a reminder to the
                    # agent. Without this it was silently dropped (the docstring
                    # promised "last non-empty additionalContext wins" but the code
                    # never merged it — adversarial run_123a6530). Accumulate so
                    # multiple advisory hooks can each contribute.
                    if result.get("additionalContext"):
                        prior = combined.get("additionalContext", "")
                        combined["additionalContext"] = (
                            (prior + "\n" + result["additionalContext"]).strip()
                            if prior else result["additionalContext"]
                        )

                except asyncio.TimeoutError:
                    logger.warning(
                        "Hook '%s' timed out after %.1fs — skipping",
                        name, HOOK_TIMEOUT_SECONDS,
                    )
                except Exception:
                    logger.exception("Hook '%s' raised an error — skipping", name)

            return combined

        return chained


def _merge_specific(combined: dict, result: dict) -> None:
    """Fold *result*'s ``hookSpecificOutput`` into *combined*'s: ``additionalContext``
    is joined with a newline after what earlier hooks gave; other keys later-win."""
    spec = result.get("hookSpecificOutput")
    if not isinstance(spec, dict):
        return
    target = combined.setdefault("hookSpecificOutput", {})
    prior = target.get("additionalContext")
    target.update(spec)
    new = spec.get("additionalContext")
    if prior and new:
        target["additionalContext"] = f"{prior}\n{new}"
    elif prior:
        target["additionalContext"] = prior


def compose_gates(members: list[tuple[str, HookCallback]]) -> HookCallback:
    """One PreToolUse hook that runs ``members`` in order; the first deny/ask wins.

    Each member keeps its own HOOK_TIMEOUT_SECONDS bound and fail-open handling (a
    member that times out or raises is skipped, as in ``_build_chain``) and is logged
    under its own name in gate_decisions.jsonl. Register the group with
    ``no_timeout=True``: the members are bounded here, so the group's total can exceed
    one 5s slot without a member being unbounded. Members are allow/deny gates (no
    updatedInput): of a non-terminal result only ``hookSpecificOutput.additionalContext``
    is kept (a note such as active_run_commit_gate's), accumulated in member order.
    """
    logged = [(n, with_decision_log(n, fn)) for n, fn in members]

    async def composite(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        notes: list[str] = []
        for name, fn in logged:
            try:
                result = await asyncio.wait_for(
                    fn(input_data, tool_use_id, context), timeout=HOOK_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                logger.warning("Hook '%s' timed out after %.1fs — skipping",
                               name, HOOK_TIMEOUT_SECONDS)
                continue
            except Exception:
                logger.exception("Hook '%s' raised an error — skipping", name)
                continue
            if result and from_sdk(result).disposition >= Disposition.HITL:
                return result
            note = ((result or {}).get("hookSpecificOutput") or {}).get("additionalContext")
            if note:
                notes.append(note)
        if not notes:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "additionalContext": "\n".join(notes)}}

    composite._decision_logged = True  # type: ignore[attr-defined]
    composite.members = tuple(n for n, _ in members)  # type: ignore[attr-defined]
    return composite


async def build_hooks(
    agent_config: dict,
    enable_skills: bool,
    enable_mcp: bool,
    resume_session_id: Optional[str],
    session_context: Optional[dict],
    permission_manager: "PermissionManager",
) -> tuple[dict, list[str], bool]:
    """Build hook matchers for ClaudeAgentOptions.

    Uses HookRegistry to compose all hooks, then returns the SDK-compatible
    hooks dict along with skill access info.

    ──────────────────────────────────────────────────────────────────────────
    ⛔ HOOK ADMISSION GATE — read before adding ANY hook here (run_d613bb27).

    A PreToolUse/PostToolUse hook fires for EVERY SwarmAI user on EVERY matching
    command — it is product-wide infrastructure, not a place for one project's own
    conventions. Before registering a hook, it MUST pass all three:

      1. UNIVERSAL — it encodes a general coding/safety principle that helps ANY
         user on ANY project (e.g. "don't background a hang", "don't destroy data
         without approval", "adversarial-review before commit"). If it enforces
         SwarmAI's OWN dev discipline (this repo's identity trailer, its release CI
         marker, its own script names, its internal architecture), it is the WRONG
         layer — put it in the relevant flow/skill (a one-time check), not a hook.
      2. RIGHT-LAYER — a per-command runtime interception is actually needed. A
         once-per-release / once-per-commit check belongs in that flow's script
         (see release-gate --verify in s_swarm-release), not a hook every command pays.
      3. FAIL-SAFE — it fails OPEN on its own error (never blocks the user because
         the guard itself broke), and a normal user's legitimate command is never
         DENIED by it.

    History: run_d613bb27 removed 4 hooks (commit-trailer / release-publish /
    eval-in-pipeline / default-pool-offload) that failed #1 — they enforced this
    project's self-dev rules on every user. "It's a useful check" is NOT sufficient;
    "every user needs this check" is the bar.
    ──────────────────────────────────────────────────────────────────────────

    Args:
        agent_config: Agent configuration dictionary.
        enable_skills: Whether skills are enabled.
        enable_mcp: Whether MCP servers are enabled.
        resume_session_id: Optional session ID for resumed sessions.
        session_context: Optional session context dict for hook tracking.
        permission_manager: The PermissionManager instance.

    Returns:
        Tuple of (hooks_dict, effective_allowed_skills, allow_all_skills).
    """
    registry = HookRegistry()

    # Session identity — resolved ONCE here (closure source for every hook that
    # needs 归属). The SDK hands hooks an empty context ({"signal": None}), so a hook
    # can ONLY learn its session via this closure, never via its context arg.
    agent_id = agent_config.get("id", "default")
    session_key = resume_session_id or agent_id or "unknown"
    # No session context given: the id falls back to the agent id, which no run can ever
    # be linked to — ``_unbound`` tells gates that would ask this session to start a run
    # (worktree_write_gate rule 4) that it never could. (Prewarms pass their unit's own
    # context, which adoption rewrites to the real id.)
    hook_session_context = (
        session_context if session_context is not None
        else {"sdk_session_id": resume_session_id or agent_id, "_unbound": True}
    )

    # ── PreToolUse: tool logger (+ durable SEL audit, closure-attributed) ──
    if agent_config.get("enable_tool_logging", True):
        registry.register(
            "PreToolUse",
            create_pre_tool_logger(session_key, hook_session_context),
            "pre_tool_logger",
        )

    # ── PreToolUse: background-command guard (Bash-scoped) ──
    # Default-deny backgrounding (run_in_background / trailing &/nohup/setsid)
    # except a narrow long-lived-service allowlist. Closes the background
    # runaway hole the foreground 120s timeout can't bound (claude-code#61568).
    registry.register(
        "PreToolUse", background_command_guard,
        "background_command_guard", matcher="Bash",
    )

    # ── PreToolUse: audit-marker write gate (Bash + file tools) ──
    # ALWAYS: DENY a write, move or delete in state/pipeline_agent_audit (redirects,
    # write/copy verbs, interpreter code naming it, rm of an ancestor, cd into it;
    # Write/Edit/MultiEdit/NotebookEdit by resolved path). It holds the review markers
    # and every pipeline run's event log: a hand edit damages a run (S6-3: the log is
    # checksummed, not signed, so this is a guard against mistakes). Reads pass.
    # Fail-open; SWARM_GATE_FORCE=audit_marker_write_gate lets it through.
    # In the Bash chain it runs right after background_command_guard and before
    # session_link_gate and every HITL gate, so a user is never asked to approve a
    # command this gate would then deny.
    audit_marker_gate = create_audit_marker_write_gate(hook_session_context)
    registry.register(
        "PreToolUse", audit_marker_gate, "audit_marker_write_gate", matcher="Bash",
    )
    registry.register(
        "PreToolUse", audit_marker_gate, "audit_marker_write_gate",
        matcher="Write|Edit|MultiEdit|NotebookEdit",
    )
    # ── PreToolUse: session-link gate (Bash-scoped) ──
    # ALWAYS (not only during a run): DENY a command that removes or overrides
    # SWARM_APP_SESSION_ID (unset, env -u/-i in front of python, VAR=… prefixes,
    # export/declare, also inside bash -c / eval / pnpm dlx -c). That variable links a
    # pipeline run to this session; without it the run gates silently stop
    # applying. Reads pass. Fail-open; operator bypass none
    # (gate_force.UNFORCEABLE). Registered before commit_gate so it sees the command
    # before any HITL prompt. (A v1 run's
    # owner is in its protected contract from create; nothing stamps it afterwards.)
    registry.register(
        "PreToolUse", create_session_link_gate(hook_session_context),
        "session_link_gate", matcher="Bash",
    )


    # ── PreToolUse: commit gate (Bash-scoped), two checks on `git commit` ──
    # 1. run_amend_gate: in THIS session's active run, `git commit --amend` is denied and
    #    pointed at the run's landing, `pipeline_v1.py land --message` (run_c5eb0858:
    #    an approval prompt parked an unattended run 2 h).
    # 2. active_run_commit_gate: a file a live v1 run changed (its signed code facts)
    #    lands only through the run's landing; denies when such a run is unreadable.
    # (direct_commit_gate and adversarial_commit_gate were removed: design
    # 2026-10-24-swarmai-usable-by-anyone §3.4 — native commits are never blocked.) Operator bypass SWARM_GATE_FORCE=commit_gate (or one member name).
    # Registered BEFORE dangerous_command_gate: never ask a user to approve a commit
    # this gate would then deny.
    registry.register(
        "PreToolUse", compose_gates([
            ("run_amend_gate", create_run_amend_gate(hook_session_context)),
            ("active_run_commit_gate", create_active_run_commit_gate(hook_session_context)),
        ]),
        "commit_gate", matcher="Bash", no_timeout=True,
    )

    # ── PreToolUse: run-owner gate (Bash-scoped) — pipeline-run-worktree §4.2 ──
    # For pipeline_v1 commands on a run (only while worktree mode is
    # on, or the run is isolated): take a crashed owner's run over (deny while the
    # owner is live), and put this session's SWARM_APP_SESSION_ID before the pipeline
    # call via updatedInput (an adopted prewarm subprocess has none). Fail-open. No
    # other Bash hook may set updatedInput (the chain merge is last-wins); its
    # approved-phase twin after dangerous_command_gate records the person's approval
    # for the commands this phase skips (run_owner_gate.AFTER_APPROVAL).
    # Only for a real app session: the fallback id may be an agent id.
    if session_context is not None:
        from core.run_owner_gate import create_run_owner_gate
        registry.register(
            "PreToolUse", create_run_owner_gate(session_context),
            "run_owner_gate", matcher="Bash",
        )

    # An EXPERT-ID spawn must use the dedicated read-only pipeline-expert type.
    # R1/R2 round limits are enforced by Pipeline v1 handout events, not a legacy
    # marker-counting PreToolUse gate.
    if session_context is not None:
        from core.pipeline_expert_agent import create_expert_type_gate
        registry.register(
            "PreToolUse", create_expert_type_gate(),
            "expert_type_gate", matcher="Agent|Task",
        )

    # ── PreToolUse: a pipeline-expert sub-agent's tool-call cap ──
    # Inside a `pipeline-expert` sub-agent (agent_type on the hook input), calls past
    # EXPERT_TOOL_CAP are denied and the expert is told to write its report now. The
    # prompts' SCOPE BUDGET was prose: the slow tail took 40-72 calls (10-12 min).
    from core.pipeline_expert_agent import create_expert_budget_gate
    registry.register(
        "PreToolUse", create_expert_budget_gate(),
        "expert_budget_gate", matcher="Read|Grep|Glob|Bash",
    )

    # ── PreToolUse: bash-syntax guard (Bash-scoped) ──────────
    # Run `bash -n` (parse-only, no execution) before each command. A
    # syntactically incomplete command (unterminated quote/backtick, unclosed
    # block) makes bash wait on stdin that never arrives in headless mode →
    # hangs forever (run-real 12-min hang escaping the 120s foreground timeout).
    # bash -n exit!=0 == the hang set; DENY those, fail-OPEN on everything else
    # (incl. guard infra failure). Structural prevention the 120s timeout can't
    # give (O030: wall-clock can't tell HANG from SLOW, escaped by auto-bg).
    registry.register(
        "PreToolUse", bash_syntax_guard,
        "bash_syntax_guard", matcher="Bash",
    )

    # ── PreToolUse: symlink-target guard (Bash-scoped) ───────
    # Deny `ln [-s] SRC DST` when DST is already a symlink to a directory and no -n/-h/-T
    # is given: ln would create the link INSIDE the linked directory (exit 0, silent).
    # In a worktree whose node_modules links to the main checkout that wrote a
    # self-link into the main checkout's node_modules, which the next npm install
    # turned into a nested copy and an ENOTEMPTY failure in another session.
    # `ln -sfn` and linking into a real directory pass. Fail-open.
    from core.symlink_target_guard import symlink_target_guard
    registry.register(
        "PreToolUse", symlink_target_guard,
        "symlink_target_guard", matcher="Bash",
    )

    # (inclusive_term_guard and design_lint_guard were SwarmAI-repo rules, not product
    # rules: they moved to scripts/lint_terms.py, run by that repo's CI. Design
    # 2026-10-24-swarmai-usable-by-anyone P7.)

    # ── PreToolUse: image-read dedup guard (Read-scoped) ─────
    # Deny a redundant re-read of an UNCHANGED image (same path+mtime) within a
    # session — the deny reason tells the model the image is already above, so it
    # is not re-injected as a fresh ~tens-of-K vision payload. Root-fix for the
    # observed prompt bloat (s8/s9.png each read 5×, 155K→235-596K). Fail-safe
    # (approve all else); offset/limit param is an explicit escape valve for a
    # compaction-evicted image. Per-session by construction (closure-local cache).
    registry.register(
        "PreToolUse", create_image_read_dedup_guard(),
        "image_read_dedup_guard", matcher="Read",
    )

    # ── PreToolUse: dangerous command gate (Bash-scoped) ─────
    # session_key / hook_session_context resolved once at the top of this function.
    enable_human_approval = agent_config.get("enable_human_approval", True)
    gate = create_dangerous_command_gate(
        hook_session_context, session_key, permission_manager,
        enable_human_approval=enable_human_approval,
    )
    # no_timeout=True: this gate blocks up to 4h (PERMISSION_ANSWER_TIMEOUT_SECONDS)
    # in wait_for_permission_decision awaiting the user's approve/deny. It shares the
    # Bash matcher slot with the fast guards → chained → without this flag the chain's
    # 5s timeout cancels the live approval prompt at 5s (run_6e780e00 approve-into-void
    # root cause). TWO timeouts must be aligned to the 4h window, not one:
    #   (1) the 5s CHAIN timeout — exempted here via no_timeout (build's _build_chain).
    #   (2) the CLI's ~600s HOOK-CALLBACK timeout — the ACTUAL prior ceiling. The CLI
    #       cancels the blocking hook at ITS default (~600s) unless we forward a
    #       per-matcher `timeout`; build_sdk_hooks() now does (any-no_timeout-in-group
    #       → HookMatcher.timeout=PERMISSION_ANSWER_TIMEOUT_SECONDS). Before that fix
    #       the prompt died at ~600s (NOT the 4h05m WAITING_INPUT watchdog — that
    #       watchdog only reaps AFTER the CLI already cancelled + the waiter went
    #       dead; it was never the real ceiling). run_1141ea02.
    registry.register("PreToolUse", gate, "dangerous_command_gate", matcher="Bash", no_timeout=True)
    logger.info(f"Dangerous command gate attached for session_key: {session_key}")

    # ── PreToolUse: Brain gates of a Project Agent session (design
    # 2026-10-28-project-agent-flow §8.2) ──
    # Every session registers it; outside a Project Agent session it does nothing.
    # A matcher of its own (sole occupant of its group) and no_timeout: its gates are
    # bounded one by one (GATE_TIMEOUT_S each) and a slow gate must be REFUSED by that
    # bound, never let through by the chain's 5s cut-off.
    from core.project_agent_session import create_brain_gate_hook
    registry.register(
        "PreToolUse", create_brain_gate_hook(hook_session_context), "brain_gate",
        matcher="Bash|Write|Edit|MultiEdit|NotebookEdit", no_timeout=True,
    )

    # ── PreToolUse: run-owner gate, approved phase (Bash-scoped) ──
    # A command the user approves first (run-land / accept-finding / discard-snapshot
    # --request): its approval is recorded HERE, after the approval above, not in the
    # check phase registered earlier. Must stay directly after dangerous_command_gate.
    if session_context is not None:
        from core.run_owner_gate import create_run_owner_gate
        registry.register(
            "PreToolUse", create_run_owner_gate(session_context, phase="approved"),
            "run_owner_gate_approved", matcher="Bash",
        )

    # ── PreToolUse: AskUserQuestion gate (AskUserQuestion-scoped) ──
    # Intercepts AskUserQuestion before the CLI self-resolves it in headless
    # mode, blocks for the user's answer, then injects answers via updatedInput.
    ask_gate = create_ask_question_gate(
        session_key=session_key,
        session_context=hook_session_context,
        ask_question_mgr=ask_question_manager,
    )
    # no_timeout=True: blocks up to 4h in wait_for_answer. It currently solo-occupies
    # the AskUserQuestion matcher slot so it's bare-registered (no chain, no 5s) — but
    # that exemption is ACCIDENTAL (a 2nd AskUserQuestion hook would silently chain it
    # and re-introduce the 5s guillotine). Declaring no_timeout makes the exemption
    # EXPLICIT and future-proof (run_6e780e00).
    registry.register("PreToolUse", ask_gate, "ask_question_gate", matcher="AskUserQuestion", no_timeout=True)
    logger.info(f"AskUserQuestion gate attached for session_key: {session_key}")

    # ── PreToolUse: external-approval gate (ALL tools, no matcher) ──
    # Routes off-machine (EXTERNAL-classed) NON-Bash tool calls — MCP send/post/
    # CRM-mutate/etc. — through the same PermissionManager approval flow as the
    # Bash gate, closing the hole where non-Bash external side-effects were ungated.
    # no_timeout=True (blocks up to 4h in wait_for_permission_decision).
    #
    # ⚠️ Gate-1 B1: this hook has NO matcher, so it lands in the ("PreToolUse", None)
    # group. A no_timeout member makes build_sdk_hooks() give the WHOLE group the 4h
    # HookMatcher.timeout AND _build_chain awaits every member without the 5s guard.
    # governance_file_gate is a FAST advisory hook — it must NOT lose its 5s hang-guard.
    # Fix: governance_file_gate is registered with matcher="Write|Edit|Bash" (below), which
    # is behavior-preserving (it early-returns approve for every other tool),
    # so external_approval_gate is the SOLO occupant of the None group (solo → no chain
    # → its no_timeout only affects itself).
    external_gate = create_external_approval_gate(
        hook_session_context, session_key, permission_manager,
        enable_human_approval=enable_human_approval,
    )
    registry.register("PreToolUse", external_gate, "external_approval_gate", no_timeout=True)
    logger.info(f"External-approval gate attached for session_key: {session_key}")

    # ── PreToolUse: governance file gate (Write/Edit/Bash-scoped) ──
    # matcher="Write|Edit|Bash" (not None): keeps this fast advisory hook OUT of the
    # no-matcher group so external_approval_gate's no_timeout can't strip its 5s
    # hang-guard (Gate-1 B1). Groups are keyed by the exact matcher string, so it is
    # also NOT in the "Bash" group with dangerous_command_gate (no_timeout) — it runs
    # solo and self-guards its own errors. Bash is included because shell writes
    # (sed -i, redirects, interpreter heredoc patches) reach the same governed files.
    governance_gate = create_governance_file_gate()
    registry.register("PreToolUse", governance_gate, "governance_file_gate", matcher="Write|Edit|Bash")
    logger.debug("Governance file gate attached (advisory mode, Write|Edit|Bash-scoped)")

    # ── PreToolUse: worktree write gate (pipeline-run-worktree §6.1) ──
    # Its own matcher string → its own group: never in the 4-hour Bash chain, solo →
    # bare-registered, so it bounds itself (_GATE_TIMEOUT) and fails open. No-op while
    # no run worktree exists. Read/Grep only get a reminder, never a deny.
    from core.worktree_write_gate import MATCHER as _WT_MATCHER, create_worktree_write_gate
    registry.register("PreToolUse", create_worktree_write_gate(hook_session_context),
                      "worktree_write_gate", matcher=_WT_MATCHER)

    # ── Skill access control ─────────────────────────────────
    allowed_skills = agent_config.get("allowed_skills", [])
    allow_all_skills = agent_config.get("allow_all_skills", False)
    plugin_ids = agent_config.get("plugin_ids", [])
    global_user_mode = agent_config.get("global_user_mode", True)

    if global_user_mode:
        allow_all_skills = True
        allowed_skills = []
        plugin_ids = []
        logger.info("Global User Mode: forcing allow_all_skills=True, ignoring allowed_skills")

    effective_allowed_skills = await expand_allowed_skills_with_plugins(
        allowed_skills, plugin_ids, allow_all_skills
    )

    allowed_skill_names = list(effective_allowed_skills)
    logger.info(
        f"Agent skill access: allow_all={allow_all_skills}, "
        f"{len(effective_allowed_skills)} skills "
        f"({len(allowed_skills)} explicit + {len(plugin_ids)} plugins)"
    )

    if enable_skills and not allow_all_skills:
        from core.skill_manager import skill_manager
        cache = await skill_manager.get_cache()
        builtin_names = [
            name for name, info in cache.items()
            if info.source_tier == "built-in"
        ]

        skill_checker = create_skill_access_checker(
            allowed_skill_names,
            builtin_skill_names=builtin_names,
        )
        registry.register("PreToolUse", skill_checker, "skill_access_checker", matcher="Skill")
        logger.info(
            f"Skill access checker hook added for skills: "
            f"{allowed_skill_names} (built-in: {builtin_names})"
        )

    # ── PostToolUse: Pipeline v1 background REFLECT (Bash-scoped, D1) ──
    # After a pipeline_v1 command, queue this session's ``reflecting`` runs on one
    # worker thread (judge + route + outcome, bounded; the daemon completes REFLECT).
    # Returns at once and never blocks the chat; fails open.
    if session_context is not None:
        from core.run_protocol.reflect_job import create_reflect_trigger
        registry.register(
            "PostToolUse", create_reflect_trigger(session_context),
            "v1_reflect_trigger", matcher="Bash",
        )

    # ── PostToolUse: main-checkout edit reporter (Bash-scoped) ──
    # Only while THIS session owns an isolated run with an active worktree: after each
    # Bash call, report main-checkout files of the run's repos that changed since the
    # last call (an interpreter script's write the worktree gate cannot parse), with
    # the user-approved run-claim-main command. Reports only, never moves anything;
    # bounds itself (3s) and fails open. Chained with the stamper (5s per hook).
    if session_context is not None:
        from core.main_dirty_scan import create_main_dirty_reporter
        registry.register(
            "PostToolUse", create_main_dirty_reporter(session_context),
            "main_dirty_reporter", matcher="Bash",
        )

    # ── PostToolUse: CDF unit usage observer (Read/Grep) ──
    # Maps exact governed-file line ranges to parse_units identities off the event
    # loop. It injects nothing, denies nothing, and fails open.
    if session_context is not None:
        from core.ingestion.usage import create_usage_observer
        registry.register(
            "PostToolUse", create_usage_observer(session_context),
            "cdf_usage_observer", matcher="Read|Grep",
        )

    # ── repository guidance (design 2026-10-24-swarmai-usable-by-anyone §3.7) ──
    # Inject-only, never a deny: the first time this session touches a git repo, that
    # repo's AGENTS.md / CLAUDE.md / .swarmai/review.md go into additionalContext
    # (core/repo_guidance.py). PostToolUse after a read or Bash; PreToolUse before a
    # first write, so the guidance is in context before the agent edits anything.
    if session_context is not None:
        from core.repo_guidance import create_repo_guidance_hook
        registry.register(
            "PostToolUse", create_repo_guidance_hook(session_context, "PostToolUse"),
            "repo_guidance", matcher="Read|Grep|Glob|Bash",
        )
        registry.register(
            "PreToolUse", create_repo_guidance_hook(session_context, "PreToolUse"),
            "repo_guidance_pre", matcher="Write|Edit|MultiEdit|NotebookEdit",
        )

    # ── shared checkout (design 2026-10-24-swarmai-usable-by-anyone §3.8) ──
    # Inject-only, never a deny: the first time this session works in a repo where
    # ANOTHER live session has uncommitted files it wrote, a note names them and the
    # session-worktree command (core/shared_checkout.py). Same matchers as above.
    if session_context is not None:
        from core.shared_checkout import create_shared_checkout_hook
        registry.register(
            "PostToolUse", create_shared_checkout_hook(session_context, "PostToolUse"),
            "shared_checkout", matcher="Read|Grep|Glob|Bash",
        )
        registry.register(
            "PreToolUse", create_shared_checkout_hook(session_context, "PreToolUse"),
            "shared_checkout_pre", matcher="Write|Edit|MultiEdit|NotebookEdit",
        )

    # ── PostToolUse: batch-independent-calls nudge (all tools) ──
    # Inject-only, never a deny: after a round of tool results, one sentence asks the
    # model to request every independent item in one response (Anthropic's documented
    # fix for one-call-per-step coding loops; core/parallel_nudge.py).
    if session_context is not None:
        from core.parallel_nudge import create_parallel_nudge_hook
        registry.register(
            "PostToolUse", create_parallel_nudge_hook(session_context),
            "parallel_nudge",
        )

    # ── PostCompact: capture the compaction summary ──────────
    # The SDK's HookEvent Literal lacks PostCompact, but the bundled CLI calls a
    # registered callback with {"trigger", "compact_summary", ...} BEFORE it emits
    # compact_boundary (probed 2026-10-18). The summary is cleaned and handed to
    # the boundary handler via session_context (core/compact_summary.py).
    if session_context is not None:
        async def _post_compact_hook(hook_input, tool_name, hook_context):
            from core.compact_summary import on_post_compact
            raw = hook_input.get("compact_summary") if isinstance(hook_input, dict) else None
            await on_post_compact(session_context, raw)
            return {}

        registry.register("PostCompact", _post_compact_hook, "post_compact_summary")

    # ── PreCompact: flag session_context ─────────────────────
    if session_context is not None:
        async def _pre_compact_hook(hook_input, tool_name, hook_context):
            # SDK hook inputs are plain dicts (probed: {"trigger": "manual", ...});
            # getattr() on a dict always fell back, so every compaction read "auto".
            trigger = (
                hook_input.get("trigger", "auto") if isinstance(hook_input, dict)
                else getattr(hook_input, "trigger", "auto")
            )
            logger.info(
                f"PreCompact hook fired — trigger={trigger}, "
                f"session={session_context.get('sdk_session_id')}"
            )
            # `_compacted` → consumed by post_compact_injection (agent-facing
            # continuation context). `_compact_notice_pending` + `_compact_trigger`
            # → consumed by SessionUnit._emit_post_stream_metadata to surface a
            # user-visible `context_compacted` SSE notice. TWO INDEPENDENT flags
            # on purpose: each consumer has its own fire-once reset, so sharing one
            # flag would make them race and drop a signal.
            session_context["_compacted"] = True
            session_context["_compact_notice_pending"] = True
            session_context["_compact_trigger"] = trigger
            return {"decision": "approve"}

        registry.register("PreCompact", _pre_compact_hook, "pre_compact_flag")

    # ── Notification: capture rate limits and errors ──────────
    if session_context is not None:
        async def _notification_hook(hook_input, tool_name, hook_context):
            message = hook_input.get("message", "") if isinstance(hook_input, dict) else getattr(hook_input, "message", "")
            notif_type = hook_input.get("notification_type", "") if isinstance(hook_input, dict) else getattr(hook_input, "notification_type", "")
            logger.info(
                "notification_hook: type=%s message=%s session=%s",
                notif_type, message[:120],
                session_context.get("sdk_session_id"),
            )
            session_context["_last_notification"] = {
                "type": notif_type,
                "message": message,
            }
            return {"decision": "approve"}

        registry.register("Notification", _notification_hook, "notification_capture")

    # ── Stop: capture session stop reason ────────────────────
    if session_context is not None:
        async def _stop_hook(hook_input, tool_name, hook_context):
            stop_active = hook_input.get("stop_hook_active", False) if isinstance(hook_input, dict) else getattr(hook_input, "stop_hook_active", False)
            logger.info(
                "stop_hook: stop_hook_active=%s session=%s",
                stop_active,
                session_context.get("sdk_session_id"),
            )
            session_context["_stop_info"] = {
                "stop_hook_active": stop_active,
            }
            return {"decision": "approve"}

        registry.register("Stop", _stop_hook, "stop_capture")

    # ── Runtime hooks (correction capture, error detection) ──
    if session_context is not None:
        try:
            from core.runtime_hooks import register_runtime_hooks
            register_runtime_hooks(registry, session_context)
        except ImportError:
            logger.debug("runtime_hooks not available — skipping")
        except Exception:
            logger.exception("Failed to register runtime hooks — skipping")

    return registry.build_sdk_hooks(), effective_allowed_skills, allow_all_skills
