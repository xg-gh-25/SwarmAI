"""Unified Knowledge Persist Routing — single source of truth for content classification.

This module defines WHERE knowledge goes (which doc, which section) based on content type.
Both auto hooks (ddd_cultivation, backflow) and manual skill (s_persist) consume this table.

Design principle: content-type determines destination. Same classification logic,
two consumption modes (auto: confidence-gated + safe_auto check; manual: always writes).

Public symbols:
    ROUTING_TABLE    — dict mapping route keys to {doc, section, safe_auto}
    classify_content — given text + optional project, returns routing decision
"""

from __future__ import annotations

import re
from typing import Optional


# ── Routing Table ────────────────────────────────────────────────────────────
# Single source of truth. Consumed by:
#   - ddd_cultivation.py (auto path — respects safe_auto)
#   - s_persist skill instructions (manual path — ignores safe_auto)
#   - context_health report (counts entries per route)
#
# Keys are semantic route names. Each route maps to:
#   doc:       Target markdown file (relative to project or .context/)
#   section:   Target ## section within the doc (None = append to end)
#   safe_auto: Whether auto hooks can write without escalation

ROUTING_TABLE: dict[str, dict] = {
    # ── EXPERIENTIAL (project-scoped, all safe for auto) ──
    "what_worked": {
        "doc": "IMPROVEMENT.md",
        "section": "What Worked",
        "safe_auto": True,
    },
    "what_failed": {
        "doc": "IMPROVEMENT.md",
        "section": "What Failed",
        "safe_auto": True,
    },
    "watch_for": {
        "doc": "IMPROVEMENT.md",
        "section": "What to Watch For",
        "safe_auto": True,
    },
    # ── TECHNICAL (project-scoped, safe for auto) ──
    "runtime_trap": {
        "doc": "TECH.md",
        "section": "Runtime Traps",
        "safe_auto": True,
    },
    "convention": {
        "doc": "TECH.md",
        "section": "Conventions",
        "safe_auto": True,
    },
    "architecture": {
        "doc": "TECH.md",
        "section": "Architecture",
        "safe_auto": True,
    },
    # ── STRATEGIC (project-scoped, never auto — human curated) ──
    "product_priority": {
        "doc": "PRODUCT.md",
        "section": "Strategic Priorities",
        "safe_auto": False,
    },
    "product_non_goal": {
        "doc": "PRODUCT.md",
        "section": "Non-Goals",
        "safe_auto": False,
    },
    "product_vision": {
        "doc": "PRODUCT.md",
        "section": "Vision",
        "safe_auto": False,
    },
    # ── OPERATIONAL (project-scoped, never auto — human curated) ──
    "project_decision": {
        "doc": "PROJECT.md",
        "section": "Recent Decisions",
        "safe_auto": False,
    },
    "project_focus": {
        "doc": "PROJECT.md",
        "section": "Current Focus",
        "safe_auto": False,
    },
    "project_blocker": {
        "doc": "PROJECT.md",
        "section": "Blocked By",
        "safe_auto": False,
    },
    # ── COGNITIVE (cross-project → MEMORY.md / EVOLUTION.md) ──
    "principle": {
        "doc": "MEMORY.md",
        "section": "Principles",
        "safe_auto": False,
    },
    "cross_project_guideline": {
        "doc": "MEMORY.md",
        "section": "Guidelines",
        "safe_auto": False,
    },
    "correction": {
        "doc": "EVOLUTION.md",
        "section": "Corrections Captured",
        "safe_auto": False,
    },
    # ── REFERENCE (cross-project → KNOWLEDGE.md) ──
    "reference": {
        "doc": "KNOWLEDGE.md",
        "section": None,
        "safe_auto": True,
    },
    # ── HIGH-ORDER, PROJECT-LOCAL homes (declared-type authoritative routing) ──
    # These exist so a declared [principle] on a PROJECT pipeline lesson has a
    # WRITABLE home. Cultivation can only write the 4 canonical docs under
    # 2-understanding/ (ddd_path); MEMORY.md/EVOLUTION.md resolve OUTSIDE project_dir
    # and DO NOT EXIST there (verified run_c7e1e39d Gate-1) — so a declared principle
    # routes here (a real, non-protected PRODUCT section), NOT to the cross-project
    # `principle` route above. safe_auto=False: high-order knowledge is human-curated.
    "project_principle": {
        "doc": "PRODUCT.md",
        "section": "Design Philosophy — When Beliefs Become Enforcement",
        "safe_auto": False,
    },
}


# ── Declared-type honoring (root-fix run_c7e1e39d) ────────────────────────────
# ROOT: a REFLECT lesson's TYPE is known at author-time, but classify_content
# re-derived the route purely from prose — and the project-scoped keyword branch
# STRUCTURALLY cannot reach the high-order homes, starving decision/principle/
# correction. When the author DECLARES a leading `[type]` (as s_persist already
# does, SKILL.md:158), we honor it — the guess becomes a fallback, not the primary.
#
# Import VALID_TYPES from ddd_entry_lifecycle (ONE source of truth — verified no
# import cycle: ddd_entry_lifecycle imports neither this module nor anything that
# reaches back here). Guarded so this leaf module still imports if the dependency
# graph ever shifts (falls back to the literal 7-set).
try:
    from core.ddd_entry_lifecycle import VALID_TYPES as _DECLARED_TYPES
except ImportError:  # pragma: no cover - defensive; keeps persist_routing importable if the dep graph shifts
    _DECLARED_TYPES = (
        "guideline", "pitfall", "decision", "model", "process",
        "principle", "correction",
    )

# HIGH-ORDER declared types → authoritative project-local route_key. Keyword
# routing is BYPASSED for these (it can never reach these homes). Every target is
# a real ## section in a WRITABLE canonical doc and safe_auto=False.
#   correction → What Failed (re-homed off cross-project EVOLUTION.md, unwritable
#   by the project-scoped cultivation path — Gate-1 BLOCK 4a, run_c7e1e39d).
TYPE_ROUTE: dict[str, str] = {
    "decision": "project_decision",
    "principle": "project_principle",
    "correction": "what_failed",
}

# OPERATIONAL declared types keep keyword routing (their keyword-reachable sections
# are type-coherent) — EXCEPT a protected-zone landing, which would be silently
# DROPPED (skipped_protected). Fence: remap a protected keyword-route to the
# type-coherent default so an operational lesson is never lost.
_OPERATIONAL_DEFAULT_ROUTE: dict[str, str] = {
    "pitfall": "what_failed",
    "guideline": "watch_for",
    "process": "convention",
    "model": "convention",
}
# Protected (never-auto) route_keys an operational tag must be fenced away from.
_PROTECTED_ROUTE_KEYS = frozenset({
    "product_priority", "product_non_goal", "product_vision",
})

# Leading `[type] ` prefix detector (mirrors ddd_cultivation.py:675 + the
# _ALREADY_TITLED_RE tag form — ONE shape, validated against _DECLARED_TYPES).
_DECLARED_TYPE_RE = re.compile(r"^\[(\w+)\]\s+")


# ── Keyword classifiers ──────────────────────────────────────────────────────
# Moved from ddd_cultivation.py to be the single source of truth.

_TECH_KEYWORDS = re.compile(
    r"\b(pattern|convention|rule|always|never|must|prefer|use\s+\w+\s+instead|"
    r"standing\s+rule|port|daemon|config|architecture|invariant|guard|"
    r"nc\s+-z|lsof|asyncio|subprocess|Path\.home|"
    r"separates|prevents|eliminates|enables|correct\s+\w+\s+for|"
    r"safer\s+than|trivial\s+to|atomic|idempotent|"
    r"should\s+be\s+stored|needs\s+\d+|include\s+a\s+\w+|"
    r"skill.layer|CLI\s+bridge|content.addressable|"
    r"polling|ETag|per.tab|stabilization)\b",
    re.IGNORECASE,
)

_IMPROVEMENT_KEYWORDS = re.compile(
    r"\b(worked|failed|caught|missed|broke|bug|regression|crash|"
    r"highest.ROI|anti-pattern|root.cause|fix|prevented|discovered|"
    r"SMOKE|adversarial|PE.review|pipeline|"
    r"zero\s+regression|integration\s+gap|wiring\s+matters|"
    r"reusing\s+existing|battle.tested|diagnostic|"
    r"trivial\s+to\s+test|zero\s+mocking)\b",
    re.IGNORECASE,
)

_PRODUCT_KEYWORDS = re.compile(
    # NOTE: 'scope'/'phase'/'milestone' were REMOVED (2026-07-13, run_eba5fc53) —
    # they are process/pipeline vocabulary, not product-doc vocabulary. Reflect-stage
    # lessons ("Scope discipline held", "Phase-1-of-3 rollout") tripped the PRODUCT
    # branch → product_priority catch-all → PRODUCT.md#Strategic Priorities (a protected
    # zone that can't auto-apply) → escalations piled up. Only genuine product-doc words
    # (priority/non-goal/strategic/roadmap/vision/defer/user-facing) belong here.
    r"\b(priority|non-goal|strategic|user.facing|"
    r"defer|roadmap|vision)\b",
    re.IGNORECASE,
)

# Governance boundary: content targeting SOUL/AGENT/STEERING behavioral rules.
# Two-part detection: (1) governance action words AND (2) governance targets.
# "Standing rule" alone is NOT governance — it could be a technical convention.
# Governance = intent to modify agent behavioral rules (SOUL/AGENT/STEERING).
_GOVERNANCE_ACTION_KEYWORDS = re.compile(
    r"\b(new\s+rule|add\s+rule|from\s+now\s+on|behavioral\s+rule|"
    r"intake\s+gate|standing\s+rule)\b",
    re.IGNORECASE,
)
_GOVERNANCE_TARGET_KEYWORDS = re.compile(
    r"\b(steering|soul\.md|agent\.md|AGENT|SOUL|STEERING|"
    r"all\s+sessions|every\s+session|always\s+run|never\s+skip)\b",
    re.IGNORECASE,
)

NOISE_PATTERNS = re.compile(
    r"^(tests?\s+pass|report\s+written|\d+\s+(lessons?|findings?)\s+captured|"
    r"all\s+green|done|completed|shipped|fixed)\.?$",
    re.IGNORECASE,
)

# Minimum content length to be routable
_MIN_LENGTH = 30


# ── Classification function ──────────────────────────────────────────────────


def classify_content(
    text: str,
    project: Optional[str] = None,
) -> dict:
    """Classify knowledge content and return routing decision.

    Used by both auto hooks (ddd_cultivation) and manual skill (s_persist).
    Returns a dict with: doc, section, project, safe_auto, confidence, is_governance.

    Args:
        text: The knowledge content to classify.
        project: Active project name (None = cross-project, routes to MEMORY/KNOWLEDGE).

    Returns:
        {
            "doc": "IMPROVEMENT.md",
            "section": "What Failed",
            "project": "SwarmAI" or None,
            "safe_auto": True,
            "confidence": 0.7,
            "is_governance": False,
            "route_key": "what_failed",
        }
    """
    stripped = text.strip()

    # ── Declared-type honoring (root-fix run_c7e1e39d) ────────────────────────
    # If the author declared a VALID leading `[type]`, honor it (the fact was known
    # at author-time — don't re-guess it from prose). HIGH-ORDER types get an
    # authoritative project-local route; OPERATIONAL types have the tag stripped so
    # it can't pollute keyword hits, then keyword-route with a protected-zone fence.
    # An INVALID/absent tag → unchanged keyword routing (strangler-fig).
    _declared: Optional[str] = None
    _m = _DECLARED_TYPE_RE.match(stripped)
    if _m and _m.group(1).lower() in _DECLARED_TYPES:
        _declared = _m.group(1).lower()
        # GOVERNANCE OUTRANKS DECLARED TYPE (Gate-2 Security Finding 1, run_c7e1e39d):
        # a behavioral-rule change (action+target keywords, e.g. "add rule: … STEERING …
        # always …") targets SOUL/AGENT/STEERING and MUST hit the governance human-gate
        # — a declared [decision]/[principle] prefix must NOT reroute it into an ordinary
        # DDD section and skip that gate. Test governance on the TAG-FREE body FIRST; if
        # it's governance, fall through (do NOT honor the type) so the governance branch
        # below fires. `is_quality_lesson`/length gates are unaffected (they run upstream).
        _body = stripped[_m.end():]
        _is_gov = (
            _GOVERNANCE_ACTION_KEYWORDS.search(_body) is not None
            and _GOVERNANCE_TARGET_KEYWORDS.search(_body) is not None
        )
        if _declared in TYPE_ROUTE and project is not None and not _is_gov:
            route = ROUTING_TABLE[TYPE_ROUTE[_declared]]
            return {
                "doc": route["doc"],
                "section": route["section"],
                "project": project,
                # High-order knowledge is human-curated BY NATURE — always escalate,
                # even when the physical home (e.g. correction→What Failed) is an
                # otherwise-auto section. safe_auto is a per-proposal property, so a
                # declared [correction] escalates while a bare failure lesson in the
                # same section still auto-applies.
                "safe_auto": False,
                # Confidence reflects an explicit author declaration (strong signal).
                "confidence": 0.7,
                "is_governance": False,
                "route_key": TYPE_ROUTE[_declared],
                "declared_type": _declared,
            }
        # Operational (or high-order with no project context): strip the tag so the
        # literal type word can't skew keyword counting; keyword routing continues
        # on the tag-free body below.
        stripped = stripped[_m.end():].strip()

    # Reject noise
    if len(stripped) < _MIN_LENGTH or NOISE_PATTERNS.match(stripped):
        return {
            "doc": "IMPROVEMENT.md",
            "section": "What to Watch For",
            "project": project,
            "safe_auto": True,
            "confidence": 0.1,
            "is_governance": False,
            "route_key": "watch_for",
        }

    # Governance boundary detection (target = SOUL/AGENT/STEERING)
    # Requires BOTH an action keyword AND a target keyword to avoid false positives.
    # "Standing rule: prefer atomic writes" = technical convention (no target).
    # "New standing rule for STEERING: always run adversarial" = governance (action + target).
    governance_action = len(_GOVERNANCE_ACTION_KEYWORDS.findall(stripped))
    governance_target = len(_GOVERNANCE_TARGET_KEYWORDS.findall(stripped))
    if governance_action >= 1 and governance_target >= 1:
        return {
            "doc": "AGENT.md",
            "section": None,
            "project": None,
            "safe_auto": False,
            "confidence": min(0.5 + (governance_action + governance_target) * 0.1, 0.95),
            "is_governance": True,
            "route_key": "governance",
        }

    # Keyword matching
    tech_hits = len(_TECH_KEYWORDS.findall(stripped))
    improvement_hits = len(_IMPROVEMENT_KEYWORDS.findall(stripped))
    product_hits = len(_PRODUCT_KEYWORDS.findall(stripped))
    total_hits = tech_hits + improvement_hits + product_hits

    lower = stripped.lower()

    # No project context → cross-project (MEMORY.md)
    if project is None:
        # Principle detection
        if any(w in lower for w in ("principle", "philosophy", "fundamental", "axiom")):
            route = ROUTING_TABLE["principle"]
            return {
                **route, "project": None,
                "confidence": min(0.5 + total_hits * 0.1, 0.95),
                "is_governance": False, "route_key": "principle",
            }
        # Default cross-project: guideline in MEMORY
        route = ROUTING_TABLE["cross_project_guideline"]
        return {
            **route, "project": None,
            "confidence": min(0.4 + total_hits * 0.1, 0.85),
            "is_governance": False, "route_key": "cross_project_guideline",
        }

    # Project-scoped classification
    # Priority: PRODUCT (most specific keywords) > TECH > IMPROVEMENT (default)
    #
    # PRODUCT-branch ENTRY BAR (run_dca69c87): a SINGLE incidental product word
    # (a lone priority/strategic/roadmap that shows up in a process lesson —
    # "roadmap kill-path", "priority chain matters", "the strategic core") is NOT
    # strategic content. It used to fire the product_priority catch-all → PRODUCT.md
    # #Strategic Priorities (a protected zone that can't auto-apply) → escalations
    # piled up. Genuine strategy carries either >=2 product words OR an explicit-intent
    # phrase (non-goal/vision/defer/mission/thesis). A lone incidental word now falls
    # through to the existing IMPROVEMENT/TECH selector below — no keyword duplication,
    # worked/failed/watch nuance preserved for free. (Gate-1 F6; NOT a source-aware
    # cap in ddd_cultivation — source is unknown at classify time for 3/4 callers.)
    # NOTE: 'defer' is deliberately NOT a single-hit intent word (Gate-2 F2, run_dca69c87):
    # "defer the retry/fix/cleanup" is a process idiom, not a product Non-Goal — as a lone
    # trigger it dumped task-deferral lessons into the protected PRODUCT#Non-Goals zone.
    # It stays in the product_non_goal SUB-branch below, so a defer with >=2 product hits
    # (a genuine "defer <feature>" strategic call) still routes correctly.
    _product_intent = any(
        w in lower for w in ("non-goal", "not going to", "won't",
                             "vision", "mission", "thesis")
    )
    if (product_hits > 0 and product_hits >= tech_hits and product_hits >= improvement_hits
            and (product_hits >= 2 or _product_intent)):
        # PRODUCT.md — product keywords are specific (non-goal, vision, strategic)
        if any(w in lower for w in ("non-goal", "not going to", "won't", "defer")):
            route_key = "product_non_goal"
        elif any(w in lower for w in ("vision", "mission", "thesis")):
            route_key = "product_vision"
        else:
            route_key = "product_priority"
    elif tech_hits > improvement_hits:
        # TECH.md — technical patterns, conventions, traps
        if any(w in lower for w in ("trap", "daemon", "env", "path", "port", "launchd", "mode guard")):
            route_key = "runtime_trap"
        elif any(w in lower for w in ("architecture", "subsystem", "module", "layer")):
            route_key = "architecture"
        else:
            route_key = "convention"
    elif tech_hits == improvement_hits and tech_hits > 0:
        # Tie-break (F2): outcome words → IMPROVEMENT, rule words → TECH
        outcome_signal = any(w in lower for w in ("caught", "found", "missed", "review", "discovered", "prevented"))
        if outcome_signal:
            route_key = "what_worked" if any(w in lower for w in ("caught", "prevented", "found")) else "watch_for"
        else:
            route_key = "convention"
    else:
        # IMPROVEMENT.md (default for project-scoped)
        if any(w in lower for w in ("worked", "roi", "caught", "prevented", "highest", "effective")):
            route_key = "what_worked"
        elif any(w in lower for w in ("failed", "broke", "bug", "crash", "gap", "wrong", "killed")):
            route_key = "what_failed"
        else:
            route_key = "watch_for"

    # Operational-tag protected-zone fence (root-fix run_c7e1e39d): an author who
    # DECLARED an operational [type] must never have their lesson DROPPED because the
    # prose keywords steered it into a protected PRODUCT zone (skipped_protected). Remap
    # to the type-coherent default so it lands in a writable, auto-applicable home.
    if _declared in _OPERATIONAL_DEFAULT_ROUTE and route_key in _PROTECTED_ROUTE_KEYS:
        route_key = _OPERATIONAL_DEFAULT_ROUTE[_declared]

    route = ROUTING_TABLE[route_key]
    confidence = min(0.4 + total_hits * 0.1, 0.95) if total_hits > 0 else 0.3

    return {
        "doc": route["doc"],
        "section": route["section"],
        "project": project,
        "safe_auto": route["safe_auto"],
        "confidence": confidence,
        "is_governance": False,
        "route_key": route_key,
        "declared_type": _declared,
    }
