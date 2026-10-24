"""Ordered gate Verdict value object — Phase 1 of the gate-governance refactor.

## Why this exists

PreToolUse gates (`security_hooks.py`) express the SAME small
set of decisions — allow / warn / hand-to-human / deny — through SIX inconsistent
dict carriers:

  1. ``{"decision": "approve"}``                              → ALLOW
  2. ``{"decision": "approve", "additionalContext": "..."}``  → WARN  (advisory nudge)
  3. ``{"decision": "block", "reason": "..."}``               → DENY
  4. ``{"hookSpecificOutput": {"permissionDecision": "deny"|"ask"|"defer"|"allow", ...}}``
  5. ``{"hookSpecificOutput": {"permissionDecision": "allow", "updatedInput": {...}}}`` → ALLOW

(A top-level ``{"behavior": "deny"}`` is NOT a chain carrier — the legacy
``_build_chain`` never inspected ``behavior``, so from_sdk must not either; see the
note at that branch. It is used only by the non-chain file_access callback.)

``hook_builder._build_chain`` historically hand-merged these raw dicts with four
implicit, bug-patched rules (block short-circuit; terminal ``permissionDecision``
short-circuit — the run_6af22b0d clobber fix where a later ``allow`` must not
overwrite an earlier ``deny``; ``.update()`` last-wins merge; ``additionalContext``
accumulation). That logic was correct but un-typed and easy to regress.

This module collapses the six carriers onto ONE ordered scale
(``ALLOW < WARN < HITL < DENY``) with a ``max``-severity merge, so "strictest
wins / earlier wins on a tie" is a property of the type, not of five lines of
hand-written dict juggling.

## Scope (Phase 1 — deliberately narrow)

- Verdict lives ONLY inside ``_build_chain``'s merge step. Gates still RETURN
  dicts (their signatures are untouched); ``from_sdk`` adapts a returned dict to a
  Verdict, ``to_sdk`` adapts the merged Verdict back. The solo-hook path
  (``hook_builder.py`` ``len(hooks) == 1``) never enters the chain, so a solo
  gate's dict reaches the SDK unchanged — zero regression surface.
- NOT in scope (Phase 2+): a Policy∩Profile two-level model, a matcher registry,
  a SCOPE_CATALOG, or changing any gate's decision LOGIC. This module imports no
  gate and makes no policy decision — it only re-expresses a carrier.

## The lossless-passthrough invariant (load-bearing)

``from_sdk(d)`` stashes the ORIGINAL dict in ``Verdict.sdk_payload``, and
``to_sdk`` returns it verbatim when present. So ``to_sdk(from_sdk(d)) == d`` for
every real carrier — which is exactly what lets ``_build_chain``'s terminal
short-circuit hand back the original dict (preserving bypass fields like
``reason`` / ``systemMessage`` / ``permissionDecisionReason``) while still routing
the decision through the typed Disposition scale.

Conservative mapping: an UNRECOGNISED dict maps to ALLOW (never silently promoted
to deny/hitl, which would false-block a legitimate tool), and a deny is never
downgraded to allow. The carrier is always preserved in ``sdk_payload`` regardless.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional

__all__ = ["Disposition", "Verdict", "merge_verdicts", "from_sdk", "to_sdk"]


class Disposition(IntEnum):
    """Ordered gate decision scale — strictness increases with the value.

    The ordering is the whole point: ``max`` over a list of dispositions yields
    the strictest, and that single comparison replaces the hand-written
    short-circuit/clobber rules in the legacy chain.
    """

    ALLOW = 0  # tool proceeds, nothing added
    WARN = 1   # tool proceeds, but an advisory additionalContext rides along
    HITL = 2   # hand to a human (permissionDecision ask/defer)
    DENY = 3   # tool blocked (decision=block / permissionDecision=deny)


# Terminal SDK permissionDecision values — these STOP the chain in the legacy
# merge (a later hook must not clobber them). Kept here as the single source so
# from_sdk and _build_chain agree on what "terminal" means.
_TERMINAL_PERMISSION = ("deny", "ask", "defer")


@dataclass(frozen=True)
class Verdict:
    """A single gate's decision, on the ordered Disposition scale.

    ``sdk_payload`` is the ORIGINAL dict the gate returned (or ``None`` for a
    synthetic verdict). It is what makes the adapter lossless: ``to_sdk`` returns
    it verbatim, so no bypass field (reason / systemMessage / updatedInput /
    permissionDecisionReason) is ever dropped by the round-trip.
    """

    disposition: Disposition
    reason: str = ""
    sdk_payload: Optional[dict] = field(default=None)


def merge_verdicts(verdicts: list[Verdict]) -> Verdict:
    """Combine gate verdicts: strictest disposition wins; a TIE keeps the FIRST.

    "Tie keeps first" is the typed form of the run_6af22b0d clobber fix — a later
    verdict of equal (or lower) severity must never displace an earlier one, so a
    later ``allow`` can never overwrite an earlier ``deny``, and two equal-severity
    verdicts resolve to the earlier guard's.

    Empty list → ALLOW (a no-op, matching the legacy chain's empty ``combined``).
    """
    best: Optional[Verdict] = None
    for v in verdicts:
        if best is None or v.disposition > best.disposition:
            best = v
    if best is None:
        return Verdict(Disposition.ALLOW)
    return best


def from_sdk(d: Any) -> Verdict:
    """Map a gate's returned SDK dict onto a Verdict, preserving the dict verbatim.

    Recognises all six real carriers; an unrecognised/empty dict is ALLOW. NEVER
    upgrades an unknown carrier to deny/hitl (no false-block) and never downgrades
    a deny to allow. The original dict is always stashed in ``sdk_payload`` so the
    mapping is reversible (``to_sdk``).
    """
    if not isinstance(d, dict) or not d:
        return Verdict(Disposition.ALLOW, sdk_payload=d if isinstance(d, dict) else None)

    # Carrier 3: top-level decision=block → DENY
    if d.get("decision") == "block":
        return Verdict(Disposition.DENY, reason=str(d.get("reason", "")), sdk_payload=d)

    # NOTE on the `behavior` carrier: the legacy _build_chain NEVER inspected a
    # top-level `behavior` key — a `{"behavior": "deny"}` dict fell through to its
    # merge branch and contributed nothing (combined stayed empty = a no-op/ALLOW).
    # Phase 1 is a byte-for-byte-equivalent refactor of the CHAIN, so from_sdk must
    # NOT invent a terminality the chain never had: `behavior` is deliberately NOT a
    # DENY carrier here. (The only emitter of `behavior:deny` today is the
    # file_access can_use_tool callback — prompt_builder.py — which does not route
    # through _build_chain at all.) Treating it as DENY was an out-of-scope behavior
    # change caught by Gate-2 adversarial review; removed to preserve zero-change.

    # Carrier 4/6: hookSpecificOutput.permissionDecision
    hso = d.get("hookSpecificOutput")
    if isinstance(hso, dict):
        pd = hso.get("permissionDecision")
        if pd == "deny":
            return Verdict(Disposition.DENY,
                           reason=str(hso.get("permissionDecisionReason", "")), sdk_payload=d)
        if pd in ("ask", "defer"):
            return Verdict(Disposition.HITL,
                           reason=str(hso.get("permissionDecisionReason", "")), sdk_payload=d)
        # pd == "allow" or absent → non-terminal; falls through to the allow/warn
        # classification below (an allow hso may carry additionalContext).

    # Carrier 2: advisory — approve + additionalContext → WARN
    if d.get("additionalContext"):
        return Verdict(Disposition.WARN, sdk_payload=d)
    hso_ac = hso.get("additionalContext") if isinstance(hso, dict) else None
    if hso_ac:
        return Verdict(Disposition.WARN, sdk_payload=d)

    # Carrier 1/6 and everything else recognised-but-benign → ALLOW
    return Verdict(Disposition.ALLOW, sdk_payload=d)


def to_sdk(verdict: Verdict) -> dict:
    """Render a Verdict back to the SDK dict carrier.

    When ``sdk_payload`` is present (the common case — the verdict came from a real
    gate via ``from_sdk``), it is returned VERBATIM: the round-trip is lossless, so
    every bypass field survives. Only a synthetic verdict (no payload) is
    materialised into a minimal canonical dict.
    """
    if verdict.sdk_payload is not None:
        return verdict.sdk_payload
    if verdict.disposition is Disposition.DENY:
        return {"decision": "block", "reason": verdict.reason}
    if verdict.disposition is Disposition.HITL:
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "ask",
            "permissionDecisionReason": verdict.reason,
        }}
    if verdict.disposition is Disposition.WARN:
        return {"decision": "approve", "additionalContext": verdict.reason}
    return {}
