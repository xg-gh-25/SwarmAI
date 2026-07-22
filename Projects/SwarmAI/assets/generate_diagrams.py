#!/usr/bin/env python3
"""Generate SVG + PNG architecture diagrams for SwarmAI design documents.

Style: dark background (#1a1a2e), vibrant colored boxes, white text,
rounded corners, arrows with markers. Matches AIDLC diagram style.

Usage:
    DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 generate_diagrams.py
"""

import os
import pathlib

# Must set before importing cairosvg on macOS/Homebrew
os.environ.setdefault("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")

import cairosvg  # noqa: E402

OUT_DIR = pathlib.Path(__file__).parent / "diagrams"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Palette ──────────────────────────────────────────────────────────
BG = "#1a1a2e"
BOX_BG = "#16213e"
RED = "#e74c3c"
BLUE = "#3498db"
GREEN = "#2ecc71"
ORANGE = "#f39c12"
PURPLE = "#9b59b6"
TEAL = "#1abc9c"
MUTED = "#7f8c8d"
TEXT_LIGHT = "#ecf0f1"
TEXT_MUTED = "#95a5a6"
FONT = "Helvetica Neue, Arial, sans-serif"


# ── Helpers ──────────────────────────────────────────────────────────
def _defs(extra_markers=""):
    return f"""<defs>
    <filter id="sh" x="-2%" y="-2%" width="104%" height="104%">
      <feDropShadow dx="1" dy="2" stdDeviation="3" flood-color="#000" flood-opacity="0.3"/>
    </filter>
    <marker id="arr" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0,10 3.5,0 7" fill="{MUTED}"/>
    </marker>
    <marker id="arrW" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0,10 3.5,0 7" fill="{TEXT_LIGHT}"/>
    </marker>
    <marker id="arrO" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0,10 3.5,0 7" fill="{ORANGE}"/>
    </marker>
    <marker id="arrR" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0,10 3.5,0 7" fill="{RED}"/>
    </marker>
    <marker id="arrG" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0,10 3.5,0 7" fill="{GREEN}"/>
    </marker>
    <marker id="arrB" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0,10 3.5,0 7" fill="{BLUE}"/>
    </marker>
    <marker id="arrP" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0,10 3.5,0 7" fill="{PURPLE}"/>
    </marker>
    <marker id="arrT" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0,10 3.5,0 7" fill="{TEAL}"/>
    </marker>
    {extra_markers}
  </defs>"""


def _box(x, y, w, h, color, title, lines=None, title_size=14, line_size=11):
    """Colored-top card."""
    t = []
    t.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" '
             f'fill="{BOX_BG}" stroke="{color}" stroke-width="1.5" filter="url(#sh)"/>')
    t.append(f'<rect x="{x}" y="{y}" width="{w}" height="4" rx="2" fill="{color}"/>')
    ty = y + 24
    t.append(f'<text x="{x+w//2}" y="{ty}" fill="{color}" font-size="{title_size}" '
             f'font-weight="bold" font-family="{FONT}" text-anchor="middle">{title}</text>')
    if lines:
        for i, ln in enumerate(lines):
            ly = ty + 18 + i * 16
            t.append(f'<text x="{x+w//2}" y="{ly}" fill="{TEXT_MUTED}" font-size="{line_size}" '
                     f'font-family="{FONT}" text-anchor="middle">{ln}</text>')
    return "\n  ".join(t)


def _pill(x, y, w, h, color, label, font_size=10):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h//2}" '
            f'fill="{color}" opacity="0.22"/>\n'
            f'  <text x="{x+w//2}" y="{y+h//2+4}" fill="{color}" font-size="{font_size}" '
            f'font-weight="bold" font-family="{FONT}" text-anchor="middle">{label}</text>')


def _title(x, y, text, color=ORANGE, size=18):
    return (f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" font-weight="bold" '
            f'font-family="{FONT}" text-anchor="middle">{text}</text>')


def _subtitle(x, y, text, color=TEXT_MUTED, size=12):
    return (f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" '
            f'font-family="{FONT}" text-anchor="middle">{text}</text>')


def _arrow(x1, y1, x2, y2, color=MUTED, marker="arr", dashed=False):
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{color}" stroke-width="2" marker-end="url(#{marker})"{dash}/>')


def _path_arrow(d, color=MUTED, marker="arr", dashed=False):
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    return (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" '
            f'marker-end="url(#{marker})"{dash}/>')


def _label(x, y, text, color=TEXT_LIGHT, size=11, anchor="middle", bold=False):
    fw = ' font-weight="bold"' if bold else ""
    return (f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}"{fw} '
            f'font-family="{FONT}" text-anchor="{anchor}">{text}</text>')


def _svg_wrap(w, h, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}">\n'
            f'  {_defs()}\n'
            f'  <rect width="{w}" height="{h}" fill="{BG}" rx="8"/>\n'
            f'  {body}\n</svg>')


def _save(name, svg_text):
    svg_path = OUT_DIR / f"{name}.svg"
    png_path = OUT_DIR / f"{name}.png"
    svg_path.write_text(svg_text, encoding="utf-8")
    cairosvg.svg2png(bytestring=svg_text.encode(), write_to=str(png_path), scale=2)
    print(f"  {svg_path.name}  +  {png_path.name}")


# =====================================================================
# Diagram 1 - Four-Phase Architecture
# =====================================================================
def diagram_01():
    W, H = 800, 560
    parts = []
    parts.append(_title(400, 36, "Next-Gen Agent Intelligence: Four-Phase Architecture"))
    parts.append(_subtitle(400, 54, "Safety &#x2192; Understanding &#x2192; Evolution &#x2192; E2E Hardening"))

    # Phase 1 - Red
    parts.append(_box(30, 80, 170, 190, RED,
                      "Phase 1: Safety",
                      ["MemoryGuard", "SkillMetrics", "SectionCaps", "EntryRefs",
                       "", "Token budgets,", "corruption detection"]))

    # Phase 2 - Blue
    parts.append(_box(220, 80, 170, 190, BLUE,
                      "Phase 2: Understanding",
                      ["UserObserver", "SessionRecall", "SkillRegistry", "SkillGuard",
                       "", "Profile-aware recall,", "registry as truth"]))

    # Phase 3 - Green
    parts.append(_box(410, 80, 170, 190, GREEN,
                      "Phase 3: Evolution",
                      ["SessionMiner", "SkillFitness", "EvolutionOptimizer", "RetentionPolicies",
                       "", "Data-driven self-", "improvement"]))

    # Phase 4 - Purple
    parts.append(_box(600, 80, 170, 190, PURPLE,
                      "Phase 4: E2E Hardening",
                      ["Integration Tests", "Chaos Testing", "Regression Suite", "Perf Benchmarks",
                       "", "Production-grade", "reliability"]))

    # Arrows between phases
    parts.append(_arrow(200, 175, 218, 175, RED, "arrB"))
    parts.append(_arrow(390, 175, 408, 175, BLUE, "arrG"))
    parts.append(_arrow(580, 175, 598, 175, GREEN, "arrP"))

    # Dependency layer
    parts.append(f'<rect x="30" y="300" width="740" height="60" rx="8" '
                 f'fill="{BOX_BG}" stroke="{ORANGE}" stroke-width="1.5" filter="url(#sh)"/>')
    parts.append(_label(400, 322, "Each phase builds on the previous  &#x2014;  "
                        "Phase N+1 unlocks only after N is stable", ORANGE, 12, bold=True))
    parts.append(_label(400, 342, "Safety &#x2192; Understanding &#x2192; Evolution &#x2192; Hardening",
                        TEXT_MUTED, 11))

    # Key principles row
    parts.append(_box(30, 390, 230, 140, TEAL,
                      "Design Principles",
                      ["Budget-first memory", "Lazy-load everything",
                       "Guard before inject", "Measure before evolve"]))
    parts.append(_box(285, 390, 230, 140, ORANGE,
                      "Safety Invariants",
                      ["Token caps enforced", "No silent corruption",
                       "Graceful degrade on OOM", "Audit trail always on"]))
    parts.append(_box(540, 390, 230, 140, BLUE,
                      "Evolution Constraints",
                      ["Confidence-gated deploy", "Human review for HIGH",
                       "Rollback on regression", "Weekly health audit"]))

    body = "\n  ".join(parts)
    _save("01-four-phase-architecture", _svg_wrap(W, H, body))


# =====================================================================
# Diagram 2 - Evolution Pipeline v2
# =====================================================================
def diagram_02():
    W, H = 800, 580
    parts = []
    parts.append(_title(400, 36, "Evolution Pipeline v2: MINE &#x2192; ASSESS &#x2192; ACT &#x2192; AUDIT"))
    parts.append(_subtitle(400, 54, "Confidence-gated self-improvement with data-driven deployment"))

    # Data sources feeding MINE
    parts.append(f'<rect x="30" y="75" width="150" height="155" rx="8" '
                 f'fill="{BOX_BG}" stroke="{MUTED}" stroke-width="1" filter="url(#sh)"/>')
    parts.append(_label(105, 96, "Data Sources", MUTED, 12, bold=True))
    srcs = ["DailyActivity logs", "Session transcripts", "User corrections",
            "Skill usage stats", "Error patterns"]
    for i, s in enumerate(srcs):
        parts.append(_label(105, 118 + i * 18, s, TEXT_MUTED, 10))

    # Four pipeline stages
    sx = 210
    stage_w, stage_h = 130, 155
    gap = 10

    # MINE
    parts.append(_box(sx, 75, stage_w, stage_h, BLUE,
                      "1. MINE",
                      ["SessionMiner scans", "activity + transcripts",
                       "", "Extract patterns,", "corrections, gaps"]))
    # ASSESS
    parts.append(_box(sx + stage_w + gap, 75, stage_w, stage_h, ORANGE,
                      "2. ASSESS",
                      ["SkillFitness scores", "each candidate",
                       "", "Frequency, success,", "impact analysis"]))
    # ACT
    parts.append(_box(sx + 2*(stage_w + gap), 75, stage_w, stage_h, GREEN,
                      "3. ACT",
                      ["EvolutionOptimizer", "applies changes",
                       "", "Deploy, recommend,", "or log only"]))
    # AUDIT
    parts.append(_box(sx + 3*(stage_w + gap), 75, stage_w, stage_h, PURPLE,
                      "4. AUDIT",
                      ["Verify results in", "next N sessions",
                       "", "Rollback if", "regression detected"]))

    # Arrows between stages
    parts.append(_arrow(180, 150, 208, 150, MUTED, "arrB"))
    parts.append(_arrow(sx + stage_w, 150, sx + stage_w + gap - 2, 150, BLUE, "arrO"))
    parts.append(_arrow(sx + 2*stage_w + gap, 150, sx + 2*(stage_w + gap) - 2, 150, ORANGE, "arrG"))
    parts.append(_arrow(sx + 3*stage_w + 2*gap, 150, sx + 3*(stage_w + gap) - 2, 150, GREEN, "arrP"))

    # Confidence Gates
    parts.append(f'<rect x="30" y="260" width="740" height="110" rx="8" '
                 f'fill="{BOX_BG}" stroke="{ORANGE}" stroke-width="1.5" filter="url(#sh)"/>')
    parts.append(_label(400, 284, "Confidence Gates (ACT Phase Decision Matrix)", ORANGE, 14, bold=True))

    # HIGH
    parts.append(_pill(60, 300, 200, 28, GREEN, "HIGH  &#x2265; 0.7  &#x2192;  Auto-Deploy"))
    parts.append(_label(160, 346, "Proven pattern, high frequency, no risk", TEXT_MUTED, 10))

    # MED
    parts.append(_pill(300, 300, 200, 28, ORANGE, "MED  0.3 - 0.7  &#x2192;  Recommend"))
    parts.append(_label(400, 346, "Promising but needs human review", TEXT_MUTED, 10))

    # LOW
    parts.append(_pill(540, 300, 200, 28, RED, "LOW  &lt; 0.3  &#x2192;  Log Only"))
    parts.append(_label(640, 346, "Insufficient data, keep observing", TEXT_MUTED, 10))

    # Verification loop
    parts.append(f'<rect x="30" y="400" width="740" height="150" rx="8" '
                 f'fill="{BOX_BG}" stroke="{PURPLE}" stroke-width="1.5" filter="url(#sh)"/>')
    parts.append(_label(400, 424, "AUDIT: Verification Loop", PURPLE, 14, bold=True))

    parts.append(_box(60, 440, 155, 85, TEAL,
                      "Track Metrics",
                      ["Usage frequency", "Error rate delta", "User satisfaction"],
                      title_size=11, line_size=10))
    parts.append(_box(240, 440, 155, 85, BLUE,
                      "Compare Baseline",
                      ["Pre vs post deploy", "A/B where possible", "Statistical significance"],
                      title_size=11, line_size=10))
    parts.append(_box(420, 440, 155, 85, GREEN,
                      "Verdict",
                      ["KEEP  &#x2192;  promote", "REVERT  &#x2192;  rollback", "ITERATE  &#x2192;  re-mine"],
                      title_size=11, line_size=10))
    parts.append(_box(600, 440, 155, 85, ORANGE,
                      "Update Knowledge",
                      ["EVOLUTION.md", "IMPROVEMENT.md", "Skill manifest"],
                      title_size=11, line_size=10))

    parts.append(_arrow(215, 482, 238, 482, TEAL, "arrB"))
    parts.append(_arrow(395, 482, 418, 482, BLUE, "arrG"))
    parts.append(_arrow(575, 482, 598, 482, GREEN, "arrO"))

    body = "\n  ".join(parts)
    _save("02-evolution-pipeline-v2", _svg_wrap(W, H, body))


# =====================================================================
# Diagram 3 - Skill Lifecycle
# =====================================================================
def diagram_03():
    W, H = 800, 520
    parts = []
    parts.append(_title(400, 36, "Skill Lifecycle: Tiering, Manifest, and SDK Injection"))
    parts.append(_subtitle(400, 54, "Lazy/Always tiering with manifest-driven registration"))

    # Always tier
    parts.append(_box(30, 80, 220, 170, RED,
                      "Always-Load (15 skills)",
                      ["Core skills loaded at", "every session start",
                       "", "Examples:", "save-memory, outlook,", "self-evolution, qa,",
                       "code-review, deliver"]))

    # Lazy tier
    parts.append(_box(280, 80, 220, 170, BLUE,
                      "Lazy-Load (46 skills)",
                      ["Loaded on-demand when", "user intent matches",
                       "", "Examples:", "xlsx, pptx, podcast,", "browser-agent, github,",
                       "deep-research"]))

    # Manifest
    parts.append(_box(530, 80, 240, 170, GREEN,
                      "manifest.yaml",
                      ["name, version, tier", "triggers: [keywords]",
                       "tools: [required MCPs]", "inject: SKILL.md path",
                       "", "Single source of truth", "for skill registry"]))

    # Arrow from manifest to tiers
    parts.append(_arrow(530, 165, 502, 165, GREEN, "arrB", dashed=True))
    parts.append(_arrow(530, 145, 252, 145, GREEN, "arrR", dashed=True))

    # SKILL.md + INSTRUCTIONS.md split
    parts.append(f'<rect x="30" y="280" width="740" height="100" rx="8" '
                 f'fill="{BOX_BG}" stroke="{ORANGE}" stroke-width="1.5" filter="url(#sh)"/>')
    parts.append(_label(400, 304, "Skill Content Split", ORANGE, 14, bold=True))

    parts.append(_pill(60, 318, 320, 26, TEAL, "SKILL.md  &#x2014;  Context for the agent (injected into prompt)"))
    parts.append(_pill(420, 318, 320, 26, PURPLE, "INSTRUCTIONS.md  &#x2014;  How to build/maintain the skill"))
    parts.append(_label(400, 366, "SKILL.md goes into system prompt  |  INSTRUCTIONS.md stays on disk for humans",
                        TEXT_MUTED, 10))

    # SDK Injection Flow
    parts.append(f'<rect x="30" y="405" width="740" height="90" rx="8" '
                 f'fill="{BOX_BG}" stroke="{PURPLE}" stroke-width="1.5" filter="url(#sh)"/>')
    parts.append(_label(400, 428, "SDK Injection Flow", PURPLE, 14, bold=True))

    flow_items = [
        (60, "User Message", BLUE),
        (195, "Intent Match", ORANGE),
        (330, "Load SKILL.md", GREEN),
        (465, "Inject to Prompt", TEAL),
        (600, "Agent Executes", RED),
    ]
    for i, (fx, fl, fc) in enumerate(flow_items):
        parts.append(_pill(fx, 444, 120, 26, fc, fl))
        if i < len(flow_items) - 1:
            nx = flow_items[i+1][0]
            parts.append(_arrow(fx + 120, 457, nx - 2, 457, fc, "arr"))

    body = "\n  ".join(parts)
    _save("03-skill-lifecycle", _svg_wrap(W, H, body))


# =====================================================================
# Diagram 4 - Memory Four Levels
# =====================================================================
def diagram_04():
    W, H = 800, 560
    parts = []
    parts.append(_title(400, 36, "Memory Management: Four-Level Architecture"))
    parts.append(_subtitle(400, 54, "System prompt injection (L1-L2) + On-demand recall (L3-L4)"))

    # Left label: System Prompt Injection
    parts.append(f'<rect x="20" y="80" width="18" height="190" rx="4" fill="{RED}"/>')
    parts.append(f'<text x="29" y="180" fill="{TEXT_LIGHT}" font-size="11" font-weight="bold" '
                 f'font-family="{FONT}" text-anchor="middle" '
                 f'transform="rotate(-90, 29, 180)">ALWAYS INJECTED</text>')

    # L1 Semantic
    parts.append(_box(50, 80, 340, 85, RED,
                      "L1 Semantic Memory  &#x2014;  MEMORY.md",
                      ["Facts, decisions, preferences", "Injected into every session prompt",
                       "Updated by save-memory skill"]))

    # L2 Procedural
    parts.append(_box(50, 185, 340, 85, ORANGE,
                      "L2 Procedural Memory  &#x2014;  EVOLUTION.md",
                      ["Learned patterns, skill rules", "Injected into every session prompt",
                       "Updated by evolution pipeline"]))

    # Right label: On-Demand Recall
    parts.append(f'<rect x="20" y="295" width="18" height="190" rx="4" fill="{BLUE}"/>')
    parts.append(f'<text x="29" y="395" fill="{TEXT_LIGHT}" font-size="11" font-weight="bold" '
                 f'font-family="{FONT}" text-anchor="middle" '
                 f'transform="rotate(-90, 29, 395)">ON-DEMAND</text>')

    # L3 Episodic
    parts.append(_box(50, 295, 340, 85, BLUE,
                      "L3 Episodic Memory  &#x2014;  DailyActivity/",
                      ["Session logs, notes, designs", "RecallEngine fetches on-demand",
                       "Hybrid search: vector + FTS5"]))

    # L4 Verbatim
    parts.append(_box(50, 400, 340, 85, PURPLE,
                      "L4 Verbatim Memory  &#x2014;  JSONL",
                      ["Full session transcripts", "TranscriptIndexer on-demand",
                       "Highest fidelity, largest cost"]))

    # Injection mechanism
    parts.append(_box(430, 80, 340, 120, GREEN,
                      "System Prompt Assembly",
                      ["7 layers assembled at start:",
                       "1. CLAUDE.md  2. MEMORY.md",
                       "3. EVOLUTION.md  4. PROJECT.md",
                       "5. Active skill SKILL.md", "6. Recalled context  7. User msg"]))

    # Recall mechanism
    parts.append(_box(430, 230, 340, 140, TEAL,
                      "RecallEngine",
                      ["Hybrid: 0.6 vector + 0.4 FTS5",
                       "Triggered by focus keywords",
                       "or agent Read tool request",
                       "", "Pre-session: medium precision",
                       "Post-msg: high precision",
                       "Mid-session: highest precision"]))

    # Budget
    parts.append(_box(430, 400, 340, 85, ORANGE,
                      "Token Budget Enforcement",
                      ["MemoryGuard caps per section",
                       "SectionCaps: hard limits per level",
                       "Graceful degrade, never truncate"]))

    # Arrows
    parts.append(_arrow(390, 122, 428, 122, RED, "arrG"))
    parts.append(_arrow(390, 227, 428, 300, BLUE, "arrT"))
    parts.append(_arrow(390, 340, 428, 320, BLUE, "arrT"))
    parts.append(_arrow(390, 442, 428, 420, PURPLE, "arrO"))

    # Footer
    parts.append(_label(400, 530, "L1+L2 = always present in context  |  "
                        "L3+L4 = retrieved when relevant  |  Budget enforced at all levels",
                        TEXT_MUTED, 10))

    body = "\n  ".join(parts)
    _save("04-memory-four-levels", _svg_wrap(W, H, body))


# =====================================================================
# Diagram 5 - Recall Engine Flow
# =====================================================================
def diagram_05():
    W, H = 800, 520
    parts = []
    parts.append(_title(400, 36, "RecallEngine: Three-Stage Precision Recall"))
    parts.append(_subtitle(400, 54, "Progressive precision: pre-session &#x2192; post-first-message &#x2192; mid-session"))

    # Stage 1
    parts.append(_box(30, 80, 230, 160, BLUE,
                      "Stage 1: Pre-Session",
                      ["Trigger: session start", "Input: focus keywords",
                       "from user profile",
                       "", "Precision: MEDIUM", "Broad context priming",
                       "Low latency required"]))

    # Stage 2
    parts.append(_box(285, 80, 230, 160, GREEN,
                      "Stage 2: Post-First-Msg",
                      ["Trigger: first user msg", "Input: actual query text",
                       "with full intent",
                       "", "Precision: HIGH", "Targeted recall",
                       "~200ms budget"]))

    # Stage 3
    parts.append(_box(540, 80, 230, 160, ORANGE,
                      "Stage 3: Mid-Session",
                      ["Trigger: agent Read tool", "Input: specific file or",
                       "knowledge request",
                       "", "Precision: HIGHEST", "Exact retrieval",
                       "Agent-driven"]))

    # Arrows
    parts.append(_arrow(260, 160, 283, 160, BLUE, "arrG"))
    parts.append(_arrow(515, 160, 538, 160, GREEN, "arrO"))

    # Hybrid search engine
    parts.append(f'<rect x="30" y="270" width="740" height="110" rx="8" '
                 f'fill="{BOX_BG}" stroke="{TEAL}" stroke-width="1.5" filter="url(#sh)"/>')
    parts.append(_label(400, 294, "Hybrid Search Engine", TEAL, 14, bold=True))

    parts.append(_pill(60, 308, 200, 26, BLUE, "Vector Search (weight 0.6)"))
    parts.append(_label(160, 350, "Semantic similarity via embeddings", TEXT_MUTED, 10))

    parts.append(_label(280, 322, "+", TEAL, 18, bold=True))

    parts.append(_pill(310, 308, 200, 26, ORANGE, "FTS5 Full-Text (weight 0.4)"))
    parts.append(_label(410, 350, "Keyword matching via SQLite FTS5", TEXT_MUTED, 10))

    parts.append(_label(530, 322, "=", TEAL, 18, bold=True))

    parts.append(_pill(560, 308, 180, 26, GREEN, "Merged + Ranked Results"))
    parts.append(_label(650, 350, "Deduplicated, scored, budget-fit", TEXT_MUTED, 10))

    # Merge/Rank pipeline
    parts.append(f'<rect x="30" y="405" width="740" height="90" rx="8" '
                 f'fill="{BOX_BG}" stroke="{PURPLE}" stroke-width="1.5" filter="url(#sh)"/>')
    parts.append(_label(400, 428, "Merge &#x2192; Rank &#x2192; Inject Pipeline", PURPLE, 14, bold=True))

    steps = [
        (60, "Query Encode", BLUE),
        (190, "Parallel Search", TEAL),
        (320, "Score Merge", GREEN),
        (450, "Dedup + Rank", ORANGE),
        (580, "Budget Trim", RED),
        (690, "Inject", PURPLE),
    ]
    for i, (sx, sl, sc) in enumerate(steps):
        pw = 110 if i < 5 else 80
        parts.append(_pill(sx, 444, pw, 26, sc, sl))
        if i < len(steps) - 1:
            nx = steps[i+1][0]
            parts.append(_arrow(sx + pw, 457, nx - 2, 457, sc, "arr"))

    body = "\n  ".join(parts)
    _save("05-recall-engine-flow", _svg_wrap(W, H, body))


# =====================================================================
# Diagram 6 - Memory Pipeline Lifecycle
# =====================================================================
def diagram_06():
    W, H = 800, 620
    parts = []
    parts.append(_title(400, 36, "Memory Pipeline: End-to-End Session Lifecycle"))
    parts.append(_subtitle(400, 54, "Session Start &#x2192; During &#x2192; Session End (7 hooks) &#x2192; Weekly Maintenance"))

    # Session Start
    parts.append(_box(30, 80, 170, 180, BLUE,
                      "Session Start",
                      ["Prompt Assembly:", "1. CLAUDE.md", "2. MEMORY.md",
                       "3. EVOLUTION.md", "4. PROJECT.md",
                       "5. Active SKILL.md", "6. Recalled ctx", "7. User message"]))

    # During Session
    parts.append(_box(225, 80, 170, 180, GREEN,
                      "During Session",
                      ["Agent works with", "injected context",
                       "", "May trigger:", "- Mid-session recall",
                       "- Skill lazy-load", "- Tool invocations"]))

    # Session End
    parts.append(_box(420, 80, 350, 180, RED,
                      "Session End  &#x2014;  7 Post-Session Hooks",
                      ["1. DailyActivity  &#x2014;  extract key points",
                       "2. Distillation  &#x2014;  compress to MEMORY.md",
                       "3. Evolution (mine)  &#x2014;  find patterns",
                       "4. Evolution (apply)  &#x2014;  update skills",
                       "5. Improvement  &#x2014;  IMPROVEMENT.md",
                       "6. ContextHealth  &#x2014;  check token budgets",
                       "7. AutoCommit  &#x2014;  git commit changes"]))

    # Arrows
    parts.append(_arrow(200, 170, 223, 170, BLUE, "arrG"))
    parts.append(_arrow(395, 170, 418, 170, GREEN, "arrR"))

    # Hook detail boxes
    parts.append(f'<rect x="30" y="290" width="740" height="140" rx="8" '
                 f'fill="{BOX_BG}" stroke="{ORANGE}" stroke-width="1.5" filter="url(#sh)"/>')
    parts.append(_label(400, 314, "Post-Session Hook Detail", ORANGE, 14, bold=True))

    parts.append(_box(50, 325, 155, 85, TEAL,
                      "Capture",
                      ["DailyActivity log", "Key decisions", "Action items taken"],
                      title_size=11, line_size=10))
    parts.append(_box(225, 325, 155, 85, BLUE,
                      "Distill",
                      ["Compress insights", "Update MEMORY.md", "Prune duplicates"],
                      title_size=11, line_size=10))
    parts.append(_box(400, 325, 155, 85, GREEN,
                      "Evolve",
                      ["Mine patterns", "Assess fitness", "Apply if confident"],
                      title_size=11, line_size=10))
    parts.append(_box(575, 325, 155, 85, PURPLE,
                      "Persist",
                      ["ContextHealth check", "Git auto-commit", "Verified state"],
                      title_size=11, line_size=10))

    parts.append(_arrow(205, 367, 223, 367, TEAL, "arrB"))
    parts.append(_arrow(380, 367, 398, 367, BLUE, "arrG"))
    parts.append(_arrow(555, 367, 573, 367, GREEN, "arrP"))

    # Weekly Maintenance
    parts.append(f'<rect x="30" y="460" width="740" height="130" rx="8" '
                 f'fill="{BOX_BG}" stroke="{TEAL}" stroke-width="1.5" filter="url(#sh)"/>')
    parts.append(_label(400, 484, "Weekly Maintenance Cycle", TEAL, 14, bold=True))

    parts.append(_box(55, 498, 200, 72, BLUE,
                      "Memory Health",
                      ["Token budget audit", "Stale entry cleanup"],
                      title_size=11, line_size=10))
    parts.append(_box(280, 498, 200, 72, GREEN,
                      "DDD Refresh",
                      ["Update PRODUCT.md", "Refresh TECH.md"],
                      title_size=11, line_size=10))
    parts.append(_box(505, 498, 200, 72, PURPLE,
                      "Skill Proposer",
                      ["Detect new patterns", "Propose new skills"],
                      title_size=11, line_size=10))

    parts.append(_arrow(255, 534, 278, 534, BLUE, "arrG"))
    parts.append(_arrow(480, 534, 503, 534, GREEN, "arrP"))

    body = "\n  ".join(parts)
    _save("06-memory-pipeline-lifecycle", _svg_wrap(W, H, body))


# =====================================================================
# Diagram 7 - Compound Learning Loop
# =====================================================================
def diagram_07():
    W, H = 800, 520
    parts = []
    parts.append(_title(400, 36, "SwarmAI: Compound Learning Loop"))
    parts.append(_subtitle(400, 54, "Every session makes the next session smarter  &#x2014;  the flywheel effect"))

    # Central flywheel ring (6 nodes in a hex-ish layout)
    cx, cy = 400, 280
    nodes = [
        (cx, cy - 140, "User Session", RED, 130, 50),
        (cx + 165, cy - 70, "Hook Capture", ORANGE, 130, 50),
        (cx + 165, cy + 50, "Memory Distill", BLUE, 130, 50),
        (cx, cy + 130, "Evolution Pipeline", GREEN, 148, 50),
        (cx - 165, cy + 50, "Better Skills", PURPLE, 130, 50),
        (cx - 165, cy - 70, "Better Memory", TEAL, 130, 50),
    ]

    # Draw connecting arrows in a loop
    for i in range(len(nodes)):
        nx = (i + 1) % len(nodes)
        x1, y1 = nodes[i][0], nodes[i][1]
        x2, y2 = nodes[nx][0], nodes[nx][1]
        # offset slightly toward center for cleaner arrows
        dx = (x2 - x1)
        dy = (y2 - y1)
        length = (dx**2 + dy**2) ** 0.5
        # start and end offset
        sx = x1 + dx * 0.35
        sy = y1 + dy * 0.35
        ex = x1 + dx * 0.72
        ey = y1 + dy * 0.72
        color = nodes[i][3]
        mid = "arrO" if i == 0 else "arrB" if i == 1 else "arrG" if i == 2 else "arrP" if i == 3 else "arrT" if i == 4 else "arrR"
        parts.append(_arrow(int(sx), int(sy), int(ex), int(ey), color, mid))

    # Draw node boxes
    for x, y, label, color, w, h in nodes:
        parts.append(f'<rect x="{x - w//2}" y="{y - h//2}" width="{w}" height="{h}" rx="8" '
                     f'fill="{BOX_BG}" stroke="{color}" stroke-width="2" filter="url(#sh)"/>')
        parts.append(f'<rect x="{x - w//2}" y="{y - h//2}" width="{w}" height="4" rx="2" fill="{color}"/>')
        parts.append(_label(x, y + 5, label, color, 12, bold=True))

    # Center label
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="38" fill="{BOX_BG}" stroke="{ORANGE}" stroke-width="2"/>')
    parts.append(_label(cx, cy - 4, "Flywheel", ORANGE, 12, bold=True))
    parts.append(_label(cx, cy + 12, "Effect", ORANGE, 10))

    # Detail boxes below
    parts.append(_box(30, 440, 230, 55, RED,
                      "Input",
                      ["Each user session = training data"],
                      title_size=11, line_size=10))
    parts.append(_box(285, 440, 230, 55, GREEN,
                      "Processing",
                      ["Hooks + Pipeline = automated learning"],
                      title_size=11, line_size=10))
    parts.append(_box(540, 440, 230, 55, PURPLE,
                      "Output",
                      ["Better skills + memory = compound growth"],
                      title_size=11, line_size=10))

    parts.append(_arrow(260, 467, 283, 467, RED, "arrG"))
    parts.append(_arrow(515, 467, 538, 467, GREEN, "arrP"))

    body = "\n  ".join(parts)
    _save("07-compound-loop", _svg_wrap(W, H, body))


# =====================================================================
# Diagram 8 - Context Engineering (11-file P0-P10 chain)
# =====================================================================
def diagram_08():
    W, H = 800, 820
    parts = []
    parts.append(_title(400, 36, "Context Engineering: The 11-File System Prompt Chain"))
    parts.append(_subtitle(400, 54, "P0-P10 priority files assembled into a single system prompt"))

    # ── Column headers ──
    col_lx, col_mx, col_rx = 40, 290, 540
    col_w = 210
    parts.append(_label(col_lx + col_w // 2, 82, "System-Owned", RED, 13, bold=True))
    parts.append(_label(col_mx + col_w // 2, 82, "User-Owned", BLUE, 13, bold=True))
    parts.append(_label(col_rx + col_w // 2, 82, "Agent / Auto", GREEN, 13, bold=True))

    # ── Left column: P0-P3 (system-owned, red) ──
    left_files = [
        ("P0  SWARMAI.md", "Core identity + rules"),
        ("P1  IDENTITY.md", "Persona + personality"),
        ("P2  SOUL.md", "Values + ethics"),
        ("P3  AGENT.md", "Agent config + tools"),
    ]
    for i, (title, desc) in enumerate(left_files):
        y = 96 + i * 72
        parts.append(_box(col_lx, y, col_w, 58, RED, title, [desc], title_size=12, line_size=10))

    # ── Middle column: P4-P6 (user-owned, blue) ──
    mid_files = [
        ("P4  USER.md", "User profile + preferences"),
        ("P5  STEERING.md", "Session steering hints"),
        ("P6  TOOLS.md", "Tool config + permissions"),
    ]
    for i, (title, desc) in enumerate(mid_files):
        y = 96 + i * 72
        parts.append(_box(col_mx, y, col_w, 58, BLUE, title, [desc], title_size=12, line_size=10))

    # ── Right column: P7-P10 (agent/auto, green) ──
    right_files = [
        ("P7  MEMORY.md", "Distilled session facts"),
        ("P8  EVOLUTION.md", "Learned patterns + rules"),
        ("P9  KNOWLEDGE.md", "Domain knowledge index"),
        ("P10 PROJECTS.md", "Active project context"),
    ]
    for i, (title, desc) in enumerate(right_files):
        y = 96 + i * 72
        parts.append(_box(col_rx, y, col_w, 58, GREEN, title, [desc], title_size=12, line_size=10))

    # ── Token Budget Tiers ──
    tier_y = 410
    parts.append(f'<rect x="40" y="{tier_y}" width="500" height="120" rx="8" '
                 f'fill="{BOX_BG}" stroke="{ORANGE}" stroke-width="1.5" filter="url(#sh)"/>')
    parts.append(_label(290, tier_y + 22, "Token Budget Tiers", ORANGE, 14, bold=True))

    tiers = [
        ("30K tokens", "Default context", MUTED),
        ("50K tokens", "Large context", BLUE),
        ("100K tokens", "1M-model context", GREEN),
    ]
    for i, (size, desc, color) in enumerate(tiers):
        ty = tier_y + 42 + i * 28
        bar_w = 120 + i * 100
        parts.append(f'<rect x="60" y="{ty}" width="{bar_w}" height="20" rx="4" '
                     f'fill="{color}" opacity="0.3"/>')
        parts.append(f'<rect x="60" y="{ty}" width="{bar_w}" height="20" rx="4" '
                     f'fill="none" stroke="{color}" stroke-width="1"/>')
        parts.append(_label(60 + bar_w // 2, ty + 14, size, color, 11, bold=True))
        parts.append(_label(60 + bar_w + 12, ty + 14, desc, TEXT_MUTED, 10, anchor="start"))

    # ── L0/L1 Cache (right side) ──
    cache_x, cache_y = 560, tier_y
    parts.append(f'<rect x="{cache_x}" y="{cache_y}" width="200" height="120" rx="8" '
                 f'fill="{BOX_BG}" stroke="{PURPLE}" stroke-width="1.5" filter="url(#sh)"/>')
    parts.append(_label(cache_x + 100, cache_y + 22, "Prompt Cache", PURPLE, 14, bold=True))
    parts.append(_pill(cache_x + 15, cache_y + 36, 170, 24, TEAL, "L0  Anthropic API Cache"))
    parts.append(_label(cache_x + 100, cache_y + 76, "Prefix match &#x2192; cache hit", TEXT_MUTED, 10))
    parts.append(_pill(cache_x + 15, cache_y + 86, 170, 24, ORANGE, "L1  Local File Cache"))

    # ── Convergence arrows into Assembly box ──
    asm_y = 560
    # Left column arrows
    parts.append(_arrow(col_lx + col_w // 2, 96 + 3 * 72 + 58, col_lx + col_w // 2, asm_y + 20, RED, "arrR"))
    # Middle column arrows
    parts.append(_arrow(col_mx + col_w // 2, 96 + 2 * 72 + 58, col_mx + col_w // 2, asm_y + 20, BLUE, "arrB"))
    # Right column arrows
    parts.append(_arrow(col_rx + col_w // 2, 96 + 3 * 72 + 58, col_rx + col_w // 2, asm_y + 20, GREEN, "arrG"))

    # ── System Prompt Assembly box ──
    parts.append(f'<rect x="40" y="{asm_y + 20}" width="720" height="80" rx="8" '
                 f'fill="{BOX_BG}" stroke="{ORANGE}" stroke-width="2" filter="url(#sh)"/>')
    parts.append(f'<rect x="40" y="{asm_y + 20}" width="720" height="5" rx="2" fill="{ORANGE}"/>')
    parts.append(_label(400, asm_y + 52, "System Prompt Assembly", ORANGE, 16, bold=True))
    parts.append(_label(400, asm_y + 72, "Priority-ordered concatenation  |  Budget enforcement  |  "
                        "Cache-aligned prefix", TEXT_MUTED, 10))

    # ── Flow: Assembly -> LLM ──
    parts.append(_arrow(400, asm_y + 100, 400, asm_y + 120, ORANGE, "arrO"))
    parts.append(_pill(320, asm_y + 122, 160, 30, TEAL, "&#x2192;  Claude LLM"))

    # ── Footer ──
    parts.append(_label(400, H - 16, "P0-P3 = immutable system core  |  P4-P6 = user customizable  |  "
                        "P7-P10 = auto-maintained by hooks + pipeline", TEXT_MUTED, 10))

    body = "\n  ".join(parts)
    _save("08-context-engineering", _svg_wrap(W, H, body))


# =====================================================================
# Diagram 9 - Hook Pipeline (8 post-session hooks)
# =====================================================================
def diagram_09():
    W, H = 800, 870
    parts = []
    parts.append(_title(400, 36, "Post-Session Hook Pipeline: 9 Hooks Firing Sequence"))
    parts.append(_subtitle(400, 54, "BackgroundHookExecutor (async) unless noted  |  Session End triggers all"))

    # ── "Session End" trigger at top ──
    parts.append(f'<rect x="300" y="72" width="200" height="40" rx="20" '
                 f'fill="{RED}" opacity="0.85"/>')
    parts.append(_label(400, 97, "Session End", TEXT_LIGHT, 14, bold=True))

    # ── Vertical pipeline of hooks ──
    hooks = [
        ("DailyActivityExtractionHook", ORANGE, "Captures session summary",
         "DailyActivity/", "async"),
        ("DistillationHook", BLUE, "Promotes insights to MEMORY.md",
         "MEMORY.md", "async"),
        ("EvolutionTriggerHook", GREEN, "Captures corrections mid-session",
         "EVOLUTION.md", "sync"),
        ("EvolutionMaintenanceHook", GREEN, "Status check + pipeline trigger",
         "Evolution Pipeline", "async"),
        ("SkillMetricsHook", PURPLE, "Records skill invocations",
         "skill_metrics.json", "async"),
        ("UserObserverHook", TEAL, "Detects behavioral patterns",
         "user_profile.json", "async"),
        ("ImprovementWritebackHook", "#f1c40f", "Updates project docs",
         "IMPROVEMENT.md", "async"),
        ("ContextHealthHook", RED, "Refreshes indexes, retention, sync",
         "context_health.json", "async"),
        ("AutoCommitHook", MUTED, "Git commit all changes",
         "git repository", "async"),
    ]

    hook_x = 60
    hook_w = 400
    hook_h = 62
    gap = 10
    start_y = 124
    target_x = 530
    target_w = 220

    # Vertical line from Session End down
    pipe_cx = hook_x + hook_w // 2
    last_y = start_y + len(hooks) * (hook_h + gap)
    parts.append(f'<line x1="400" y1="112" x2="400" y2="{start_y + 6}" '
                 f'stroke="{RED}" stroke-width="2" marker-end="url(#arrR)"/>')

    for i, (name, color, desc, target, mode) in enumerate(hooks):
        y = start_y + i * (hook_h + gap)

        # Hook box
        parts.append(f'<rect x="{hook_x}" y="{y}" width="{hook_w}" height="{hook_h}" rx="8" '
                     f'fill="{BOX_BG}" stroke="{color}" stroke-width="1.5" filter="url(#sh)"/>')
        parts.append(f'<rect x="{hook_x}" y="{y}" width="{hook_w}" height="4" rx="2" fill="{color}"/>')

        # Hook number circle
        cx_n = hook_x + 22
        cy_n = y + hook_h // 2
        parts.append(f'<circle cx="{cx_n}" cy="{cy_n}" r="12" fill="{color}" opacity="0.25"/>')
        parts.append(_label(cx_n, cy_n + 4, str(i + 1), color, 11, bold=True))

        # Hook name + description
        parts.append(_label(hook_x + 42, y + 24, name, color, 12, anchor="start", bold=True))
        parts.append(_label(hook_x + 42, y + 42, desc, TEXT_MUTED, 10, anchor="start"))

        # Mode pill (sync vs async)
        if mode == "sync":
            pill_color = RED
            pill_label = "Synchronous (mid-session)"
        else:
            pill_color = TEAL
            pill_label = "BackgroundHookExecutor (async)"
        pill_w = 100 if mode == "sync" else 118
        parts.append(_pill(hook_x + hook_w - pill_w - 8, y + 44, pill_w, 16, pill_color, mode.upper(), font_size=8))

        # Arrow to target
        parts.append(_arrow(hook_x + hook_w, y + hook_h // 2, target_x - 2, y + hook_h // 2, color, "arr"))

        # Target box
        parts.append(f'<rect x="{target_x}" y="{y + 8}" width="{target_w}" height="{hook_h - 16}" rx="6" '
                     f'fill="{BOX_BG}" stroke="{color}" stroke-width="1" opacity="0.8"/>')
        parts.append(_label(target_x + target_w // 2, y + hook_h // 2 + 4, target, color, 10, bold=True))

        # Vertical connector to next hook
        if i < len(hooks) - 1:
            ny = y + hook_h
            parts.append(f'<line x1="{hook_x + 22}" y1="{ny}" x2="{hook_x + 22}" y2="{ny + gap}" '
                         f'stroke="{MUTED}" stroke-width="1.5" stroke-dasharray="4 3"/>')

    # ── Footer ──
    parts.append(_label(400, H - 16, "All hooks are fire-and-forget except EvolutionTriggerHook "
                        "(synchronous, captures corrections during the session)", TEXT_MUTED, 10))

    body = "\n  ".join(parts)
    _save("09-hook-pipeline", _svg_wrap(W, H, body))


# =====================================================================
# Diagram 10 - Proactive Intelligence (L0-L4)
# =====================================================================
def diagram_10():
    W, H = 800, 780
    parts = []
    parts.append(_title(400, 36, "Proactive Intelligence: L0-L4 Awareness Levels"))
    parts.append(_subtitle(400, 54, "Multi-level context analysis feeding the session briefing"))

    # ── L0-L4 stacked levels ──
    levels = [
        ("L0", "Parsing", MUTED,
         ["Open threads", "Continue hints", "Pattern signals"],
         "Raw signal extraction from session state"),
        ("L1", "Temporal", BLUE,
         ["Session gaps", "Stale P0 detection", "First-session-of-day"],
         "Time-aware context: when was last session? what changed?"),
        ("L2", "Scoring", GREEN,
         ["ScoredItem ranking", "Priority x Momentum", "Weighted urgency"],
         "Rank all signals by importance and recency"),
        ("L3", "Learning", ORANGE,
         ["Skip penalty (-0.2)", "Affinity bonus (+0.1)", "Effectiveness trend"],
         "Adaptive weights from user behavior patterns"),
        ("L4", "Signals", PURPLE,
         ["signal_digest.json", "Job results", "Health alerts", "Skill health"],
         "External async signals from background jobs"),
    ]

    level_x = 40
    level_w = 720
    level_h = 90
    level_gap = 12
    start_y = 78

    for i, (code, name, color, items, desc) in enumerate(levels):
        y = start_y + i * (level_h + level_gap)

        # Level box
        parts.append(f'<rect x="{level_x}" y="{y}" width="{level_w}" height="{level_h}" rx="8" '
                     f'fill="{BOX_BG}" stroke="{color}" stroke-width="1.5" filter="url(#sh)"/>')
        parts.append(f'<rect x="{level_x}" y="{y}" width="6" height="{level_h}" rx="3" fill="{color}"/>')

        # Level badge
        badge_x = level_x + 24
        badge_y = y + 12
        parts.append(f'<rect x="{badge_x}" y="{badge_y}" width="56" height="28" rx="14" '
                     f'fill="{color}" opacity="0.25"/>')
        parts.append(_label(badge_x + 28, badge_y + 18, code, color, 14, bold=True))

        # Level name
        parts.append(_label(badge_x + 74, badge_y + 18, name, color, 14, anchor="start", bold=True))

        # Description
        parts.append(_label(badge_x + 18, y + 58, desc, TEXT_MUTED, 10, anchor="start"))

        # Item pills
        pill_start_x = 300
        for j, item in enumerate(items):
            px = pill_start_x + j * 140
            pw = max(len(item) * 7 + 16, 80)
            parts.append(_pill(px, y + 14, pw, 22, color, item, font_size=9))

        # Connector to next level
        if i < len(levels) - 1:
            ny = y + level_h
            parts.append(f'<line x1="400" y1="{ny}" x2="400" y2="{ny + level_gap}" '
                         f'stroke="{color}" stroke-width="1.5" marker-end="url(#arr)"/>')

    # ── Convergence arrows ──
    conv_y = start_y + len(levels) * (level_h + level_gap)

    # Draw 5 arrows from each level center down to the briefing box
    for i, (code, name, color, items, desc) in enumerate(levels):
        ly = start_y + i * (level_h + level_gap) + level_h
        # Offset x slightly per level for visual spread
        ax = 200 + i * 100
        parts.append(_path_arrow(
            f"M {ax} {ly} C {ax} {conv_y - 10}, {ax} {conv_y - 10}, 400 {conv_y + 18}",
            color, "arr", dashed=True))

    # ── Session Briefing box ──
    brief_y = conv_y + 8
    parts.append(f'<rect x="120" y="{brief_y}" width="560" height="65" rx="8" '
                 f'fill="{BOX_BG}" stroke="{ORANGE}" stroke-width="2" filter="url(#sh)"/>')
    parts.append(f'<rect x="120" y="{brief_y}" width="560" height="5" rx="2" fill="{ORANGE}"/>')
    parts.append(_label(400, brief_y + 30, "Session Briefing", ORANGE, 16, bold=True))
    parts.append(_label(400, brief_y + 50, "Top-N ranked items  |  Temporal context  |  "
                        "Pending signals  |  Health status", TEXT_MUTED, 10))

    # ── Arrow to System Prompt ──
    sp_y = brief_y + 80
    parts.append(_arrow(400, brief_y + 65, 400, sp_y + 2, ORANGE, "arrO"))

    parts.append(f'<rect x="220" y="{sp_y}" width="360" height="45" rx="22" '
                 f'fill="{TEAL}" opacity="0.2"/>')
    parts.append(f'<rect x="220" y="{sp_y}" width="360" height="45" rx="22" '
                 f'fill="none" stroke="{TEAL}" stroke-width="1.5"/>')
    parts.append(_label(400, sp_y + 28, "&#x2192;  Injected into System Prompt", TEAL, 13, bold=True))

    # ── Footer ──
    parts.append(_label(400, H - 16, "L0-L2 = deterministic  |  L3 = adaptive (learns from skips/engagement)  |  "
                        "L4 = async external signals", TEXT_MUTED, 10))

    body = "\n  ".join(parts)
    _save("10-proactive-intelligence", _svg_wrap(W, H, body))


# =====================================================================
# Main
# =====================================================================
if __name__ == "__main__":
    print("Generating SwarmAI architecture diagrams...")
    diagram_01()
    diagram_02()
    diagram_03()
    diagram_04()
    diagram_05()
    diagram_06()
    diagram_07()
    diagram_08()
    diagram_09()
    diagram_10()
    print(f"\nAll diagrams saved to {OUT_DIR}")
