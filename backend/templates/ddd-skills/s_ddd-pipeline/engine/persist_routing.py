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
    # ── HIGH-ORDER, PROJECT-LOCAL homes (declared-type authoritative routing) ──
    # Mirror of core/persist_routing.py (root-fix run_c7e1e39d). A declared [principle]
    # on a PROJECT lesson needs a WRITABLE home — cultivation writes only canonical docs.
    "project_principle": {
        "doc": "PRODUCT.md",
        "section": "Design Philosophy — When Beliefs Become Enforcement",
        "safe_auto": False,
    },
    # ── REFERENCE (cross-project → KNOWLEDGE.md) ──
    "reference": {
        "doc": "KNOWLEDGE.md",
        "section": None,
        "safe_auto": True,
    },
}


# ── Declared-type honoring (root-fix run_c7e1e39d — mirror of core/persist_routing) ──
# Portable copy: the 7-type set is inline (no ddd_entry_lifecycle in the packaged engine).
_DECLARED_TYPES = (
    "guideline", "pitfall", "decision", "model", "process", "principle", "correction",
)
TYPE_ROUTE: dict[str, str] = {
    "decision": "project_decision",
    "principle": "project_principle",
    "correction": "what_failed",
}
_OPERATIONAL_DEFAULT_ROUTE: dict[str, str] = {
    "pitfall": "what_failed",
    "guideline": "watch_for",
    "process": "convention",
    "model": "convention",
}
_PROTECTED_ROUTE_KEYS = frozenset({
    "product_priority", "product_non_goal", "product_vision",
})
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

    # ── Declared-type honoring (root-fix run_c7e1e39d — mirror of core) ───────
    _declared: Optional[str] = None
    _m = _DECLARED_TYPE_RE.match(stripped)
    if _m and _m.group(1).lower() in _DECLARED_TYPES:
        _declared = _m.group(1).lower()
        # Governance outranks declared type (test on tag-free body).
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
                "safe_auto": False,
                "confidence": 0.7,
                "is_governance": False,
                "route_key": TYPE_ROUTE[_declared],
                "declared_type": _declared,
            }
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
    # (a lone priority/strategic/roadmap in a process lesson) is NOT strategic content.
    # Genuine strategy carries >=2 product words OR an explicit-intent phrase
    # (non-goal/vision/defer/mission/thesis); a lone incidental word falls through to
    # the IMPROVEMENT/TECH selector below. Prevents the product_priority catch-all from
    # dumping process lessons into PRODUCT.md#Strategic Priorities (protected zone).
    # NOTE: 'defer' is deliberately NOT a single-hit intent word (Gate-2 F2, run_dca69c87):
    # "defer the retry/fix" is a process idiom, not a product Non-Goal. Stays in the
    # product_non_goal sub-branch so a defer with >=2 product hits still routes correctly.
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
    }
