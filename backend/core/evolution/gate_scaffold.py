"""gate_scaffold — the ②→③ last mile: scaffold an inert gate stub on human-accept.

When a human ACCEPTS a `proposal_kind="gate"` in the governance dashboard (a
correction class that recurred past RED even after a rule was deployed — the
CLASS-A lesson "rules don't stop it, only gates do"), the loop records tracker
state via ``register_gate`` but never produced a FILE. ``scaffold_gate_stub``
closes that: it writes a syntactically-valid, **fail-open** ``GATE_<cls>.py`` stub
the human then completes with real match logic and wires into a hook chain.

P7 COMPLIANCE (why this is not autonomous gate-building):
  - The human already APPROVED the gate at the dashboard (P7's "HUMAN APPROVE" rung).
  - The stub ENFORCES NOTHING: its body is ``sys.exit(0)`` (allow every tool) with a
    TODO match block. It is not wired to any hook chain (project gates run only via a
    hand-authored ``preToolUse`` list compiled by compile_gate_wiring — there is NO
    directory scan, verified run_90b8aeed Gate-1). An un-completed / half-edited stub
    is therefore inert and cannot brick the agent.
  - The human still (a) writes the match logic and (b) wires it. "Model proposes, OS
    disposes" (P7) holds: this removes blank-page friction, it does not dispose.

Idempotent: ``skip-if-exists`` returns None without touching a human-completed gate.
The filename uses ``canonical_class_key`` (matching the tracker's state key) so a
drifted spelling of the same logical class maps to the SAME file — no orphaned dup.
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.evolution.class_key import canonical_class_key

logger = logging.getLogger(__name__)


# A FAIL-OPEN PreToolUse gate stub. Mirrors the backend/templates/ddd-gates/no_git_push.py
# convention (shebang, WHY docstring, stdin-JSON contract, exit-2 BLOCK / exit-0 ALLOW,
# fail-open) — but the match body is a TODO and the gate exits 0 (allows all) until a
# human completes it. {cls} = canonical class key; {evidence} = why this gate was born.
_STUB_TEMPLATE = '''#!/usr/bin/env python3
"""
PreToolUse gate: enforce the {cls} correction class (SCAFFOLD — human must complete).

WHY: {cls} recurred past the RED threshold even after a prose rule was deployed —
the CLASS-A lesson ("rules don't stop it, only gates do", SOUL P7). A human ACCEPTED
a gate proposal for this class in the governance dashboard, which scaffolded this
stub. {evidence}

CONTRACT (PreToolUse hook): read the event JSON on stdin; exit 2 (reason on stderr)
BLOCKS the tool; exit 0 ALLOWS it. Fail-OPEN: any parse/logic error exits 0 — a gate
bug must never brick the agent (fail-closed only on a CONFIRMED match).

🚧 SCAFFOLD — NOT YET ENFORCING. This stub is deliberately fail-open (exits 0, blocks
nothing) and is NOT wired into any hook chain. To activate it, a human must:
  1. TODO: implement the match logic below (what tool-invocation pattern to BLOCK).
  2. Add this gate to the project's preToolUse hook list + run compile_gate_wiring.
Until BOTH are done, this gate is inert (allows everything). That is by design (P7:
the model scaffolds, the human disposes).
"""
import json
import sys


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {{}}
    except Exception:
        # Fail-open: a malformed event must never block a tool.
        return 0

    # TODO(human): inspect `event` (tool_name / tool_input) and return 2 with a reason
    # on stderr for the specific pattern this gate must BLOCK to enforce {cls}.
    # Example shape (from no_git_push.py):
    #     command = (event.get("tool_input") or {{}}).get("command", "")
    #     if _matches_forbidden_pattern(command):
    #         sys.stderr.write("BLOCKED ({cls}): <reason>\\n")
    #         return 2
    _ = event  # unused until the match logic is written

    # Fail-open default: allow the tool. This is what keeps an un-completed gate inert.
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def scaffold_gate_stub(
    gates_dir: str | Path,
    class_name: str,
    gate_id: str,
    evidence: str = "",
) -> Path | None:
    """Write a fail-open ``GATE_<canonical_class>.py`` stub into *gates_dir*.

    Args:
        gates_dir: the project's ``gates/`` directory (created if absent).
        class_name: the correction-class name (raw or canonical); the filename is
            derived from ``canonical_class_key(class_name)`` so a drifted spelling of
            the same logical class maps to the SAME file (no orphaned duplicate).
        gate_id: the tracker gate id (e.g. ``GATE_CLASS_A``) — used verbatim as the
            filename stem when it already matches the canonical form; otherwise the
            canonical key wins to stay consistent with tracker state.
        evidence: optional one-line "why" appended to the stub's WHY docstring.

    Returns:
        The written ``Path`` on success, or ``None`` if a gate file for this class
        already exists (skip-if-exists — never clobber a human-completed gate) or the
        class name is empty.
    """
    ckey = canonical_class_key(class_name) or canonical_class_key(gate_id.replace("GATE_", ""))
    if not ckey:
        logger.debug("scaffold_gate_stub: empty class key for %r/%r — skipping", class_name, gate_id)
        return None

    # SECURITY (Gate-2 HIGH, run_90b8aeed): canonical_class_key splits on ':'/spaces
    # but does NOT strip path separators — a crafted class label ("A/../../PWNED",
    # "/etc/x", "A{0}") would traverse OUTSIDE gates_dir or produce a malformed
    # filename. source_class flows from the free-form EVOLUTION.md miner (not a fixed
    # enum), so treat it as untrusted. Restrict the filename stem to [A-Z0-9_]; a stem
    # that has no safe chars left is refused. This subsumes traversal + odd-filename.
    safe_stem = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in ckey).strip("_")
    if not safe_stem or ".." in safe_stem:
        logger.warning("scaffold_gate_stub: refusing unsafe class key %r (stem=%r)", ckey, safe_stem)
        return None

    gdir = Path(gates_dir).resolve()
    target = (gdir / f"GATE_{safe_stem}.py").resolve()
    # Defense-in-depth: the resolved target MUST stay directly inside gdir (never let a
    # sanitizer miss escape into a rmtree/overwrite outside the gates dir).
    if target.parent != gdir:
        logger.warning("scaffold_gate_stub: refusing target escaping gates dir: %s", target)
        return None
    # Rebind ckey to the sanitized stem so the docstring/filename agree (Gate-2 MED:
    # untrusted text must not reach the .format() docstring either).
    ckey = safe_stem

    if target.exists():
        # Skip-if-exists: a re-accept / re-run must never overwrite a gate a human
        # may already have completed. Idempotent no-op.
        logger.info("scaffold_gate_stub: %s already exists — not clobbering", target)
        return None

    gdir.mkdir(parents=True, exist_ok=True)
    # Gate-2 MED: evidence is interpolated into the stub's docstring — an untrusted
    # value containing a triple-quote (or backslash) would break out and produce a
    # syntactically-broken .py. Neutralize the docstring terminators before insertion.
    ev = (evidence or "").strip().replace('"""', "'''").replace("\\", "/")
    ev = ev or "See the correction class's evidence chain in EVOLUTION.md."
    target.write_text(_STUB_TEMPLATE.format(cls=f"CLASS_{ckey}" if not ckey.startswith("CLASS") else ckey,
                                            evidence=ev),
                      encoding="utf-8")
    logger.info("scaffold_gate_stub: wrote fail-open gate stub %s (class=%s)", target, ckey)
    return target
