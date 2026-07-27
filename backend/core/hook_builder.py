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
    pre_tool_logger,
    create_dangerous_command_gate,
    create_external_approval_gate,
    background_command_guard,
    pytest_command_guard,
    eval_command_guard,
    release_publish_guard,
    bash_syntax_guard,
    inclusive_term_guard,
    create_image_read_dedup_guard,
    create_ask_question_gate,
    create_governance_file_gate,
    create_skill_access_checker,
)
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

        Execution: sequential, first 'block' wins, 5s timeout per hook —
        EXCEPT hooks registered with ``no_timeout=True`` (long-blocking HITL
        gates), which are awaited directly (their own internal bound applies).
        Results are merged: last non-empty additionalContext wins.
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

                    # Short-circuit on block decision
                    if result.get("decision") == "block":
                        return result

                    # Short-circuit on a TERMINAL permissionDecision (run_7da67105).
                    # deny/ask/defer are terminal — the CLI resolves the tool right
                    # there — so they must NOT be merged-then-overwritten by a later
                    # hook. Without this, the .update() below lets any later
                    # allow-emitting hook silently clobber an earlier guard's deny
                    # (the CRITICAL found by Gate-2 in run_6af22b0d). Only "allow"
                    # is non-terminal (it carries updatedInput / additionalContext),
                    # so allow keeps merging below. No hook emits ask/defer today,
                    # but including them closes the same clobber gap pre-emptively.
                    hso = result.get("hookSpecificOutput")
                    if isinstance(hso, dict) and hso.get("permissionDecision") in (
                        "deny", "ask", "defer",
                    ):
                        return result

                    # Merge hookSpecificOutput — later hooks override earlier
                    if "hookSpecificOutput" in result:
                        combined.setdefault("hookSpecificOutput", {})
                        combined["hookSpecificOutput"].update(result["hookSpecificOutput"])
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

    # ── PreToolUse: tool logger ──────────────────────────────
    if agent_config.get("enable_tool_logging", True):
        registry.register("PreToolUse", pre_tool_logger, "pre_tool_logger")

    # ── PreToolUse: background-command guard (Bash-scoped) ──
    # Default-deny backgrounding (run_in_background / trailing &/nohup/setsid)
    # except a narrow long-lived-service allowlist. Closes the background
    # runaway hole the foreground 120s timeout can't bound (claude-code#61568).
    registry.register(
        "PreToolUse", background_command_guard,
        "background_command_guard", matcher="Bash",
    )

    # ── PreToolUse: pytest anti-pattern guard (Bash-scoped) ──
    # Deny pytest piped to tail/head (output swallowed by auto-backgrounding)
    # and pytest without a timeout (unbounded hang). R9 in prose; this is the
    # structural backstop (run_6af22b0d). Pure deny, fail-safe (approve all else).
    registry.register(
        "PreToolUse", pytest_command_guard,
        "pytest_command_guard", matcher="Bash",
    )

    # ── PreToolUse: eval-in-pipeline guard (Bash-scoped) ─────
    # Deny running eval (eval_runner / ci_eval_gate / eval_service run) from the
    # agent's Bash path. Eval is a system-level decoupled subsystem (DEC05/PIT179)
    # that scores the DEPLOYED system via CI/deploy/scheduled — running it on
    # un-deployed changes tests the OLD binary and hung the judge's Bedrock call
    # (2026-06-28). R6/R9/STEERING #5 in prose; this is the structural backstop.
    registry.register(
        "PreToolUse", eval_command_guard,
        "eval_command_guard", matcher="Bash",
    )

    # ── PreToolUse: release-publish guard (Bash-scoped) ──────
    # Deny `gh release create` unless CI is green on the CURRENT HEAD (marker
    # written by `artifact_cli.py release-gate --poll`). Code-enforced half of
    # s_swarm-release Stage 7b (run_9fec1fb1) — prevents publishing a GitHub
    # Release on an unvalidated HEAD (the v1.24.0 miss; CLASS A skip-verification).
    # All-local (reads marker + git HEAD, no network — cannot hang). Fail-closed;
    # SWARM_RELEASE_GATE_FORCE=1 escapes for a legit manual re-publish.
    registry.register(
        "PreToolUse", release_publish_guard,
        "release_publish_guard", matcher="Bash",
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

    # ── PreToolUse: inclusive-term guard (Write|Edit|MultiEdit) ──
    # WARN (never deny) on non-inclusive terminology in written content — an
    # in-session nudge before a term reaches a commit, complementing Amazon's
    # post-commit InclusiveTechScanner (which caught 8 findings on CR-291472994
    # that none of our own review layers would have — run_74567b08). Advisory
    # only (STEERING #2: wording is not security); the hook is self-guarded so a
    # scan error can never block a write. Distinct matcher from governance_file_gate
    # so it also covers MultiEdit (edits[].new_string), which that gate omits.
    registry.register(
        "PreToolUse", inclusive_term_guard,
        "inclusive_term_guard", matcher="Write|Edit|MultiEdit",
    )

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
    agent_id = agent_config.get("id", "default")
    session_key = resume_session_id or agent_id or "unknown"
    enable_human_approval = agent_config.get("enable_human_approval", True)

    hook_session_context = (
        session_context if session_context is not None
        else {"sdk_session_id": resume_session_id or agent_id}
    )
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
    # Fix: governance_file_gate is registered with matcher="Write|Edit" (below), which
    # is behavior-preserving (it early-returns approve for every non-Edit/Write tool),
    # so external_approval_gate is the SOLO occupant of the None group (solo → no chain
    # → its no_timeout only affects itself).
    external_gate = create_external_approval_gate(
        hook_session_context, session_key, permission_manager,
        enable_human_approval=enable_human_approval,
    )
    registry.register("PreToolUse", external_gate, "external_approval_gate", no_timeout=True)
    logger.info(f"External-approval gate attached for session_key: {session_key}")

    # ── PreToolUse: governance file gate (Edit/Write-scoped) ──
    # matcher="Write|Edit" (not None): keeps this fast advisory hook OUT of the
    # no-matcher group so external_approval_gate's no_timeout can't strip its 5s
    # hang-guard (Gate-1 B1). Behavior-preserving — governance_file_gate already
    # early-returns approve for every tool that isn't Write/Edit.
    governance_gate = create_governance_file_gate()
    registry.register("PreToolUse", governance_gate, "governance_file_gate", matcher="Write|Edit")
    logger.debug("Governance file gate attached (advisory mode, Write|Edit-scoped)")

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

    # ── PreCompact: flag session_context ─────────────────────
    if session_context is not None:
        async def _pre_compact_hook(hook_input, tool_name, hook_context):
            trigger = getattr(hook_input, "trigger", "auto")
            logger.info(
                f"PreCompact hook fired — trigger={trigger}, "
                f"session={session_context.get('sdk_session_id')}"
            )
            session_context["_compacted"] = True
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

    # ── Code Intelligence: inject dependency context on Read/Grep ──
    if agent_config.get("code_intel_enabled", True):
        try:
            from core.code_intel.code_intel_hook import create_code_intel_hook
            ci_hook = create_code_intel_hook()

            async def _code_intel_wrapper(input_data, tool_use_id, hook_context):
                data = input_data if isinstance(input_data, dict) else getattr(input_data, "__dict__", {})
                tool_name = data.get("tool_name", "")
                tool_input = data.get("tool_input", {})
                return ci_hook(tool_name, tool_input)

            registry.register("PreToolUse", _code_intel_wrapper, "code_intel_context")
            logger.info("Code intelligence hook registered")
        except ImportError:
            logger.debug("code_intel not available — skipping")
        except Exception:
            logger.exception("Failed to register code_intel hook — skipping")

    return registry.build_sdk_hooks(), effective_allowed_skills, allow_all_skills
