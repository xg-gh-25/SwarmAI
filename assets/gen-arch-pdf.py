#!/usr/bin/env python3
"""Generate SwarmAI Architecture Design Doc as PDF using ReportLab + rsvg-convert."""
import subprocess, pathlib, os

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle,
    PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib.utils import ImageReader

ASSETS = pathlib.Path(__file__).parent
OUT_PDF = ASSETS / "SwarmAI-Architecture-Design-Doc.pdf"
TMP = pathlib.Path(os.environ.get("TMPDIR", "/private/tmp/claude"))
TMP.mkdir(exist_ok=True)

# Colors
GOLD = HexColor("#f59e0b")
DARK = HexColor("#1a1a2e")
BLUE = HexColor("#1e3a5f")
GRAY = HexColor("#64748b")
LIGHTGRAY = HexColor("#f8fafc")
BORDERGRAY = HexColor("#e2e8f0")

W, H = A4  # 595 x 842 pts

def svg_to_png(svg_name: str, width_px: int = 1600) -> str:
    """Convert SVG to PNG via rsvg-convert, return PNG path."""
    svg_path = ASSETS / svg_name
    png_path = TMP / f"{svg_path.stem}.png"
    subprocess.run([
        "rsvg-convert", "-w", str(width_px), "-b", "white",
        str(svg_path), "-o", str(png_path)
    ], check=True)
    return str(png_path)

# Pre-convert all SVGs
print("Converting SVGs to PNGs...")
pngs = {}
for name in [
    "swarmai-architecture.svg", "context-engineering.svg", "memory-pipeline.svg",
    "self-evolution.svg", "autonomous-pipeline.svg", "swarm-brain.svg",
    "three-column-layout.svg", "multi-tab-sessions.svg", "swarm-core-engine.svg",
    "job-system.svg"
]:
    pngs[name.replace(".svg", "")] = svg_to_png(name)
    print(f"  {name} -> PNG")

# Build styles
styles = getSampleStyleSheet()
styles.add(ParagraphStyle("CoverTitle", fontName="Helvetica-Bold", fontSize=26, alignment=TA_CENTER, textColor=DARK, spaceAfter=4))
styles.add(ParagraphStyle("CoverSub", fontName="Helvetica-Bold", fontSize=16, alignment=TA_CENTER, textColor=GOLD, spaceAfter=12))
styles.add(ParagraphStyle("CoverTagline", fontName="Helvetica-Oblique", fontSize=11, alignment=TA_CENTER, textColor=GRAY, spaceAfter=20))
styles.add(ParagraphStyle("CoverMeta", fontName="Helvetica", fontSize=9, alignment=TA_CENTER, textColor=GRAY, leading=16))
styles.add(ParagraphStyle("H1", fontName="Helvetica-Bold", fontSize=18, textColor=DARK, spaceAfter=6, spaceBefore=16))
styles.add(ParagraphStyle("H2", fontName="Helvetica-Bold", fontSize=14, textColor=BLUE, spaceAfter=4, spaceBefore=14))
styles.add(ParagraphStyle("H3", fontName="Helvetica-Bold", fontSize=11, textColor=HexColor("#334155"), spaceAfter=3, spaceBefore=10))
styles.add(ParagraphStyle("Body", fontName="Helvetica", fontSize=9.5, textColor=black, leading=14, alignment=TA_JUSTIFY, spaceAfter=6))
styles.add(ParagraphStyle("BulletCustom", fontName="Helvetica", fontSize=9.5, textColor=black, leading=13, leftIndent=16, bulletIndent=6, spaceAfter=3))
styles.add(ParagraphStyle("Caption", fontName="Helvetica-Oblique", fontSize=8, textColor=GRAY, alignment=TA_CENTER, spaceAfter=10))
styles.add(ParagraphStyle("Callout", fontName="Helvetica", fontSize=9, textColor=HexColor("#92400e"), leading=13, leftIndent=12, rightIndent=8, spaceAfter=8, spaceBefore=6, backColor=HexColor("#fffbeb"), borderPadding=6))
styles.add(ParagraphStyle("Footer", fontName="Helvetica", fontSize=7.5, textColor=GRAY, spaceBefore=10))

def heading(text, level=1):
    style = "H1" if level == 1 else ("H2" if level == 2 else "H3")
    return Paragraph(text, styles[style])

def body(text):
    return Paragraph(text, styles["Body"])

def bullet(text):
    return Paragraph(f"<bullet>&bull;</bullet> {text}", styles["BulletCustom"])

def caption(text):
    return Paragraph(text, styles["Caption"])

def callout(text):
    return Paragraph(text, styles["Callout"])

def spacer(h=6):
    return Spacer(1, h)

def hr():
    return HRFlowable(width="100%", thickness=1.5, color=GOLD, spaceAfter=8, spaceBefore=8)

CONTENT_W = W - 18*mm - 18*mm  # available width between margins

def img(png_key):
    """Full-width image with correct aspect ratio from actual PNG dimensions."""
    from PIL import Image as PILImage
    im = PILImage.open(pngs[png_key])
    pw, ph = im.size
    aspect = ph / pw
    w = CONTENT_W
    h = w * aspect
    return Image(pngs[png_key], width=w, height=h)

_cellStyle = ParagraphStyle("Cell", fontName="Helvetica", fontSize=8.5, leading=11, textColor=black)
_cellBoldStyle = ParagraphStyle("CellBold", fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=BLUE)

def _wrap_row(row, is_header=False):
    """Wrap each cell string in a Paragraph so ReportLab auto-wraps text."""
    st = _cellBoldStyle if is_header else _cellStyle
    return [Paragraph(str(c), st) for c in row]

def make_table(header, rows, col_widths=None):
    data = [_wrap_row(header, is_header=True)] + [_wrap_row(r) for r in rows]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), LIGHTGRAY),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDERGRAY),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, HexColor("#fafbfc")]),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return t

# Header/Footer
def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GRAY)
    canvas.drawString(18*mm, H - 10*mm, "SwarmAI — Agentic OS Architecture  •  High-Level Design Document  •  March 2026")
    canvas.drawRightString(W - 18*mm, 10*mm, f"Page {doc.page}")
    canvas.drawString(18*mm, 10*mm, "Internal — For PE / Tech Leadership Review")
    canvas.restoreState()

# Build document
print("Building PDF...")
doc = SimpleDocTemplate(
    str(OUT_PDF), pagesize=A4,
    leftMargin=18*mm, rightMargin=18*mm,
    topMargin=16*mm, bottomMargin=18*mm,
    title="SwarmAI — Agentic OS Architecture",
    author="Xiaogang Wang + Swarm"
)

story = []

# ======== COVER PAGE ========
story.append(Spacer(1, 100))
story.append(Paragraph("SWARMAI", ParagraphStyle("x", fontName="Helvetica-Bold", fontSize=14, alignment=TA_CENTER, textColor=GOLD, letterSpacing=4)))
story.append(Spacer(1, 12))
story.append(Paragraph("Agentic OS Architecture", styles["CoverTitle"]))
story.append(Spacer(1, 6))
story.append(Paragraph("High-Level Design Document", styles["CoverSub"]))
story.append(Spacer(1, 16))
story.append(Paragraph("Harness Engineering: How a Stateless LLM Becomes<br/>a Persistent, Evolving Agent", styles["CoverTagline"]))
story.append(Spacer(1, 40))
# Key highlights as a clean summary box on cover
cover_highlights = [
    ["6 Architecture Layers", "Interface → Intelligence → Harness → Session → Engine → Platform"],
    ["11-File Context Chain", "P0–P10 priority system with token budgets and L0/L1 caching"],
    ["3-Layer Memory Pipeline", "Session capture → distillation → curated long-term memory (git-verified)"],
    ["55+ Skills", "Self-evolution: agent builds new skills when it hits capability gaps"],
    ["8-Stage Autonomous Pipeline", "EVALUATE → THINK → PLAN → BUILD → REVIEW → TEST → DELIVER → REFLECT"],
    ["Multi-Channel Unified Brain", "Desktop + Slack — same agent, same memory, same context"],
]
t = Table(cover_highlights, colWidths=[5*cm, 12.5*cm])
t.setStyle(TableStyle([
    ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
    ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
    ('FONTSIZE', (0, 0), (-1, -1), 9),
    ('TEXTCOLOR', (0, 0), (0, -1), GOLD),
    ('TEXTCOLOR', (1, 0), (1, -1), GRAY),
    ('LEADING', (0, 0), (-1, -1), 13),
    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ('TOPPADDING', (0, 0), (-1, -1), 4),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ('LINEBELOW', (0, 0), (-1, -2), 0.5, BORDERGRAY),
]))
story.append(t)
story.append(Spacer(1, 60))
story.append(Paragraph(
    "<b>Version:</b> 1.0 &nbsp;&nbsp;•&nbsp;&nbsp; <b>Date:</b> March 26, 2026<br/>"
    "<b>Author:</b> Xiaogang Wang (XG) + Swarm (AI Co-Architect)<br/>"
    "<b>Status:</b> For PE / Tech Leadership Review &nbsp;&nbsp;•&nbsp;&nbsp; <b>Classification:</b> Internal",
    styles["CoverMeta"]
))
story.append(PageBreak())

# ======== TABLE OF CONTENTS ========
story.append(heading("Table of Contents"))
story.append(hr())
toc_items = [
    "1. Executive Summary",
    "2. Architecture Overview",
    "    2.1 Six-Layer Architecture  •  2.2 The Compound Loop",
    "3. Core Engine & Growth Trajectory",
    "4. The Harness — Core Innovation",
    "    4.1 Context Engineering  •  4.2 Memory Pipeline  •  4.3 Self-Evolution  •  4.4 Safety & Self-Harness",
    "5. Swarm Brain — Multi-Channel Architecture",
    "6. Session Architecture & Multi-Tab Parallel Sessions",
    "7. Intelligence Layer",
    "    7.1 Autonomous Pipeline  •  7.2 Job System  •  7.3 Proactive Intelligence",
    "8. Three-Column Command Center",
    "9. Key Design Decisions & Tradeoffs",
    "10. Competitive Positioning",
    "11. Future Roadmap",
]
for item in toc_items:
    indent = 16 if item.startswith("    ") else 0
    story.append(Paragraph(item.strip(), ParagraphStyle("toc", fontName="Helvetica" if indent else "Helvetica-Bold", fontSize=10 if indent else 11, leftIndent=indent, spaceAfter=4, textColor=DARK if not indent else GRAY)))
story.append(PageBreak())

# ======== 1. EXECUTIVE SUMMARY ========
story.append(heading("1. Executive Summary"))
story.append(hr())
story.append(body(
    "SwarmAI is a desktop application that wraps Claude's Agent SDK inside a <b>harness</b> — a structured layer of "
    "context management, persistent memory, self-evolution, and safety controls that transforms a stateless large language "
    "model into a persistent, evolving personal AI agent."
))
story.append(body(
    "The core thesis: <b>most AI tools reset when you close them</b>. Context is lost, decisions are forgotten, and users "
    "re-explain the same things session after session. SwarmAI solves this structurally — not through fine-tuning, but "
    "through engineered knowledge persistence."
))
story.append(callout(
    "<b>Key Innovation:</b> The \"Harness\" — an 11-file context priority chain, 3-layer memory distillation pipeline, "
    "self-evolution registry, and 7 post-session hooks that create a <i>compound loop</i>: every session makes the next one "
    "better. Every correction prevents a class of future mistakes."
))
story.append(heading("Key Metrics (March 2026)", 3))
story.append(make_table(
    ["Metric", "Value"],
    [
        ["Commits", "613+"],
        ["Built-in Skills", "55+ (curated + self-built)"],
        ["Context Files", "11 (P0–P10 priority chain)"],
        ["Post-Session Hooks", "7 (auto-commit, DailyActivity, distillation, evolution ×2, context-health, improvement)"],
        ["Pipeline Stages", "8 (EVALUATE → REFLECT)"],
        ["Session States", "5 (COLD → STREAMING → IDLE → WAITING_INPUT → DEAD)"],
        ["Core Engine Level", "L4 (Autonomous) — two compound-value loops closed: DDD auto-refresh + auto-skill proposals"],
        ["Channels", "Desktop + Slack (unified brain)"],
        ["Tech Stack", "4 languages: Rust (Tauri), TypeScript (React), Python (FastAPI), SQL (SQLite)"],
    ],
    col_widths=[5.5*cm, 12*cm]
))
story.append(PageBreak())

# ======== 2. ARCHITECTURE OVERVIEW ========
story.append(heading("2. Architecture Overview"))
story.append(hr())
story.append(heading("2.1 Six-Layer Architecture", 2))
story.append(body(
    "SwarmAI's architecture is organized into six horizontal layers. Each layer has a clear responsibility boundary. "
    "The <b>Harness layer</b> (Layer 3) is the core innovation — it is what differentiates SwarmAI from a simple LLM wrapper."
))
story.append(img("swarmai-architecture"))
story.append(caption("Figure 1: SwarmAI Agentic OS Architecture — Six-layer design with the Harness as the core innovation"))
# Use full content width for this table
_fw = CONTENT_W
story.append(make_table(
    ["Layer", "What It Does", "Key Components"],
    [
        ["Interface", "Visual workspace, multi-tab chat, dashboard, channels", "SwarmWS Explorer, Chat (1–4 tabs), Radar, Gateway (Slack)"],
        ["Intelligence", "Proactive awareness, autonomous execution, jobs", "Proactive Intelligence, Signal Pipeline, Autonomous Pipeline, Job System"],
        ["Harness", "Core: raw Claude → persistent, evolving agent", "Context (11 files), Memory (3-layer), Evolution (55+ skills), Safety"],
        ["Session", "Multi-session lifecycle, isolation, recovery", "SessionRouter, SessionUnit (5-state), LifecycleManager, 7 Hooks"],
        ["Engine", "AI model access, tool ecosystem", "Claude Agent SDK, Bedrock/Anthropic, MCP Servers (5+), Skills Engine"],
        ["Platform", "Desktop infra, all local, zero cloud", "Tauri 2.0, React 19, FastAPI, SQLite, filesystem, launchd"],
    ],
    col_widths=[_fw*0.12, _fw*0.38, _fw*0.50]
))

story.append(heading("2.2 The Compound Loop", 2))
story.append(body(
    "The defining characteristic is the <b>compound loop</b> — a feedback cycle where every session's output becomes "
    "the next session's input:"
))
for step in [
    "<b>Session executes</b> — user interacts, decisions are made, code is written, files are created",
    "<b>Hooks fire</b> — 7 post-session hooks capture: DailyActivity, auto-commit, distillation, evolution, context-health, improvement, evolution-trigger",
    "<b>Memory updates</b> — DailyActivity accumulates; ≥3 unprocessed files trigger distillation promoting recurring themes and decisions to MEMORY.md",
    "<b>Context enriched</b> — next session's system prompt assembled from updated 11-file chain with latest memory and project context",
    "<b>Agent is smarter</b> — next session starts with full awareness of everything that happened and mistakes to avoid",
]:
    story.append(bullet(step))
story.append(callout("<b>Design Principle:</b> Prevention over recovery. The compound loop makes errors structurally impossible over time, not handled after they occur."))

# ======== 3. CORE ENGINE (moved up — most important) ========
story.append(heading("3. Core Engine & Growth Trajectory"))
story.append(hr())
story.append(body(
    "The Swarm Core Engine is the meta-architecture that ties all six flywheels together. Each flywheel feeds the others: "
    "memory informs context, context improves sessions, sessions trigger evolution, evolution builds skills, "
    "skills improve memory capture — compound growth with every interaction."
))
story.append(img("swarm-core-engine"))
story.append(caption("Figure 2: Swarm Core Engine — Six interconnected flywheels and growth trajectory (L4 Autonomous — current)"))
story.append(make_table(
    ["Flywheel", "What It Does", "Key Components"],
    [
        ["Self-Evolution", "Builds new skills, captures corrections, never repeats mistakes", "EVOLUTION.md, 55+ skills, gap detection, correction registry"],
        ["Self-Memory", "3-layer distillation, git-verified, weekly LLM pruning", "DailyActivity, distillation hooks, MEMORY.md, briefing"],
        ["Self-Context", "11-file P0-P10 priority chain + token budgets + caching", "Context loader, prompt builder, budget tiers, freshness"],
        ["Self-Harness", "Validates context files, detects DDD staleness, auto-refresh", "ContextHealthHook (light+deep), auto-commit, integrity"],
        ["Self-Health", "Monitors services, resources, sessions; auto-restart", "Service manager, resource monitor, lifecycle manager"],
        ["Self-Jobs", "Background automation, scheduled tasks, signal pipeline", "Job scheduler, service manager, signal fetch/digest"],
    ],
    col_widths=[CONTENT_W*0.14, CONTENT_W*0.40, CONTENT_W*0.46]
))
story.append(heading("Growth Trajectory", 3))
story.append(make_table(
    ["Level", "State", "Capabilities", "Status"],
    [
        ["L0", "Reactive", "Responds to questions, no memory", "Complete"],
        ["L1", "Self-Maintaining", "Remembers, self-commits, captures corrections, health monitoring", "Complete"],
        ["L2", "Self-Improving", "Weekly LLM maintenance, unified jobs, feedback loops closed", "Complete"],
        ["L3", "Self-Governing", "Session-type context, proactive gap detection, DDD auto-sync", "Complete"],
        ["L4", "Autonomous", "Stale docs → auto-fix (DDD refresh), recurring gaps → auto-skill proposals. Two compound-value loops closed.", "Current"],
    ],
    col_widths=[CONTENT_W*0.07, CONTENT_W*0.16, CONTENT_W*0.55, CONTENT_W*0.22]
))
story.append(PageBreak())

# ======== 4. THE HARNESS ========
story.append(heading("4. The Harness — Core Innovation"))
story.append(hr())
story.append(body(
    "The Harness is what makes SwarmAI more than a ChatGPT wrapper. It is a structured engineering layer between the "
    "user interface and the raw LLM that provides four critical capabilities: <b>context continuity</b>, "
    "<b>memory persistence</b>, <b>self-improvement</b>, and <b>safety</b>."
))

# 3.1 Context Engineering
story.append(heading("4.1 Context Engineering", 2))
story.append(body(
    "Most AI tools assemble a single system prompt. SwarmAI maintains an <b>11-file priority chain (P0–P10)</b> that is "
    "assembled, cached, and budget-managed through a multi-stage pipeline. This is the most token-intensive subsystem "
    "and the one with the highest impact on agent quality."
))
story.append(img("context-engineering"))
story.append(caption("Figure 2: Context Engineering — 11-file priority chain with token budget management and L0/L1 caching"))

story.append(heading("Priority Chain", 3))
story.append(make_table(
    ["P", "File", "Owner", "Truncation", "Purpose"],
    [
        ["P0", "SWARMAI.md", "System", "Never", "Core identity & principles"],
        ["P1", "IDENTITY.md", "System", "Never", "Agent name, avatar, intro"],
        ["P2", "SOUL.md", "System", "Never", "Personality & tone"],
        ["P3", "AGENT.md", "System", "Truncatable", "Behavioral directives"],
        ["P4", "USER.md", "User", "Truncatable", "User preferences & background"],
        ["P5", "STEERING.md", "User", "Truncatable", "Session-level overrides"],
        ["P6", "TOOLS.md", "User", "Truncatable", "Tool & environment config"],
        ["P7", "MEMORY.md", "Agent", "Head-trimmed", "Persistent memory (newest kept)"],
        ["P8", "EVOLUTION.md", "Agent", "Head-trimmed", "Self-evolution registry"],
        ["P9", "KNOWLEDGE.md", "Auto", "Truncatable", "Domain knowledge index"],
        ["P10", "PROJECTS.md", "Auto", "Lowest", "Active projects index"],
    ],
    col_widths=[1*cm, 3.2*cm, 1.8*cm, 2.8*cm, 8.7*cm]
))
story.append(heading("Key Design Decisions", 3))
for item in [
    "<b>Session-type-aware loading</b> — Channel DMs skip EVOLUTION.md, PROJECTS.md, DailyActivity (~30% token savings)",
    "<b>L0/L1 cache</b> — L1 uses git-first freshness; L0 is AI-summarized compact version for constrained models",
    "<b>Head-trimming</b> — MEMORY.md and EVOLUTION.md keep newest content; old entries trim from top",
    "<b>Token budget</b> — 100K tokens for 1M context models; priority truncation removes P10 first, never touches P0–P2",
]:
    story.append(bullet(item))
story.append(PageBreak())

# 3.2 Memory Pipeline
story.append(heading("4.2 Memory Pipeline", 2))
story.append(body(
    "The memory pipeline is a three-layer distillation system that converts raw session activity into durable, curated knowledge. "
    "It solves the fundamental problem of AI amnesia: without this pipeline, every session starts from zero."
))
story.append(img("memory-pipeline"))
story.append(caption("Figure 3: Memory Pipeline — Three-layer distillation from session capture to curated long-term memory"))
story.append(make_table(
    ["Layer", "Storage", "Lifecycle", "Content"],
    [
        ["1. Capture", "DailyActivity/YYYY-MM-DD.md", "30 days → archived", "Per-session: deliverables, git commits, decisions, lessons, next steps"],
        ["2. Distillation", "Triggered when ≥3 files", "At session start (silent)", "Recurring themes promoted; noise filtered; claims verified against git log"],
        ["3. Curated", "MEMORY.md", "Permanent (weekly maint.)", "Open Threads (P0/P1/P2), Key Decisions, Lessons, COE Registry"],
    ],
    col_widths=[2*cm, 4*cm, 3.5*cm, 8*cm]
))
story.append(heading("Git Cross-Reference (Safety)", 3))
story.append(body(
    "Born from a real Sev-2 incident (COE C005): the distillation hook verifies all implementation claims against "
    "<font face='Courier' size='8'>git log</font> before promoting to MEMORY.md. Without this, mid-session snapshots "
    "captured before later commits create false memories that compound across sessions."
))
story.append(PageBreak())

# 3.3 Self-Evolution
story.append(heading("4.3 Self-Evolution", 2))
story.append(body(
    "Self-evolution makes SwarmAI <i>get better over time</i>. When the agent encounters a capability gap, it can build "
    "a new skill, test it, and register it. When the user corrects a mistake, the correction is captured permanently."
))
story.append(img("self-evolution"))
story.append(caption("Figure 4: Self-Evolution — Capability building, correction capture, and continuous growth loop"))
story.append(make_table(
    ["Category", "Lifecycle", "Examples"],
    [
        ["Capabilities Built", "Active → archived if 0 usage for 30d", "Browser agent, context monitor, workspace finder"],
        ["Optimizations", "Permanent", "Use CDP over WebSocket for persistent browser sessions"],
        ["Corrections", "Permanent (NEVER deleted)", "Reported features as 'not started' when fully shipped (C005)"],
        ["Competence", "Cross-referenced", "SSE streaming pipeline, multi-session architecture"],
        ["Failed Evolutions", "Permanent", "Approaches attempted and abandoned (with reasons)"],
    ],
    col_widths=[3*cm, 4.5*cm, 10*cm]
))
story.append(callout("<b>Design Principle:</b> Corrections are the highest-value entries — proven failure modes with known patterns. Deleting a correction is equivalent to removing a safety guard. The registry is append-mostly; corrections are append-only."))

story.append(heading("4.4 Safety & Self-Harness", 2))
story.append(body("Safety is a structural property, not a feature. SwarmAI implements defense-in-depth through seven independent layers:"))
story.append(make_table(
    ["Layer", "Mechanism", "Details"],
    [
        ["Tool Logger", "Audit trail", "Every tool invocation logged with timestamp, parameters, result"],
        ["Command Blocker", "Pattern matching", "13 dangerous patterns blocked (rm -rf, DROP TABLE, force push, etc.)"],
        ["Permission Dialog", "Human approval", "First-time external actions require approval; approvals persist"],
        ["Bash Sandbox", "Claude SDK sandbox", "Filesystem write restrictions, network allowlists, process isolation"],
        ["Escalation Protocol", "Confidence-gated", "3 levels: INFORM (act+tell), CONSULT (options+ask), BLOCK (stop+wait)"],
        ["ContextHealthHook", "Integrity validation", "Light (every session): file existence/format. Deep (weekly): staleness"],
        ["Decision Classification", "Judgment framework", "mechanical (auto), taste (batch), judgment (block for human)"],
    ],
    col_widths=[3*cm, 3*cm, 11.5*cm]
))
story.append(PageBreak())

# ======== 5. SWARM BRAIN (moved up — important) ========
story.append(heading("5. Swarm Brain — Multi-Channel Architecture"))
story.append(hr())
story.append(body(
    "Swarm is a personal assistant with <b>one brain</b>. Regardless of channel — desktop, Slack — "
    "it is the same Swarm, same memory, same context. Adding a new channel: write an adapter (~250 lines), "
    "register in gateway, map user identity. Zero architecture change."
))
story.append(img("swarm-brain"))
story.append(caption("Figure 7: Swarm Brain — One AI, every channel, three layers of continuity"))
story.append(make_table(
    ["Layer", "Mechanism", "Scope"],
    [
        ["L1: Shared Memory", "11 context files loaded at every prompt build", "All sessions (tabs + channels)"],
        ["L2: Cross-Channel Session", "All channels share ONE Claude conversation (--resume)", "Slack + future"],
        ["L3: Active Session Digest", "Sibling session summaries injected into prompts", "Tabs ↔ Channels (bidirectional)"],
    ],
    col_widths=[CONTENT_W*0.22, CONTENT_W*0.44, CONTENT_W*0.34]
))
story.append(heading("Key Design Decisions", 3))
for item in [
    "<b>Chat tabs are parallel</b> (multi-slot, per-topic) — for deep work",
    "<b>Channel session is serialized</b> (single dedicated slot) — for quick exchanges across platforms",
    "<b>One dedicated channel slot always reserved</b> (min_tabs = 2) — channels never starve chat, chat never starves channels",
    "<b>User identity mapping</b> ties platform IDs (Slack W017T04E,  ou_abc) to one unified user_key",
]:
    story.append(bullet(item))
story.append(PageBreak())

# ======== 6. SESSION ARCHITECTURE ========
story.append(heading("6. Session Architecture & Multi-Tab Parallel Sessions"))
story.append(hr())
story.append(body(
    "Replaced a monolithic AgentManager (5,428 lines) with four focused components during the v7 re-architecture. "
    "Driven by real need: parallel chat tabs + dedicated channel slots without resource exhaustion."
))
story.append(img("multi-tab-sessions"))
story.append(caption("Figure 8: Multi-Tab Parallel Sessions — SessionRouter, 5-state SessionUnits, dedicated channel slot"))
story.append(make_table(
    ["Component", "Responsibility"],
    [
        ["SessionRouter", "Slot acquisition, IDLE eviction, queue timeout (60s), MAX_CONCURRENT=2"],
        ["SessionUnit", "5-state machine (COLD→STREAMING→IDLE→WAIT→DEAD), subprocess spawn, 3x retry with --resume, SSE"],
        ["LifecycleManager", "60s health loop, 12hr TTL kill, DEAD→COLD cleanup, startup orphan reaper"],
        ["SessionRegistry", "Module-level singletons, initialize() wires components, configure_hooks()"],
    ],
    col_widths=[CONTENT_W*0.20, CONTENT_W*0.80]
))
story.append(heading("Key Invariants", 3))
for item in [
    "Protected states (STREAMING, WAITING_INPUT) are <b>never evicted</b>",
    "Subprocess spawn serialized via module-level locks",
    "Retry uses <font face='Courier' size='8'>--resume</font> to restore conversation context across crashes",
    "Hooks fire via BackgroundHookExecutor — never block the request path",
    "One dedicated slot always reserved for channels (min_tabs = 2)",
]:
    story.append(bullet(item))
story.append(PageBreak())

# ======== 7. INTELLIGENCE LAYER ========
story.append(heading("7. Intelligence Layer"))
story.append(hr())
story.append(body(
    "The Intelligence layer provides proactive awareness, autonomous execution, and background automation. "
    "While the Harness ensures the agent <i>remembers and improves</i>, this layer ensures it <i>anticipates, acts, and automates</i>."
))

story.append(heading("7.1 Autonomous Pipeline", 2))
story.append(body(
    "Drives the full development lifecycle from a one-sentence requirement to PR-ready delivery. "
    "Implementation of AIDLC Phase 3 (AI-Management): AI makes autonomous decisions, humans step in when needed."
))
story.append(img("autonomous-pipeline"))
story.append(caption("Figure 5: Autonomous Pipeline — 8-stage lifecycle with DDD+SDD+TDD methodology and safety mechanisms"))
story.append(make_table(
    ["Stage", "Output", "Gate"],
    [
        ["EVALUATE", "ROI score, GO/DEFER/REJECT", "ROI ≥ 3.5 to proceed"],
        ["THINK", "3 alternatives (Minimal/Ideal/Creative)", "User picks approach"],
        ["PLAN", "Design doc (SDD) + acceptance criteria", "Design approval"],
        ["BUILD", "Code + tests (TDD: RED → GREEN → VERIFY)", "All tests pass"],
        ["REVIEW", "Code quality scan + security scan", "No high-severity findings"],
        ["TEST", "Full suite, regression check", "WTF Gate (halt if risky)"],
        ["DELIVER", "PR description, decision log, report", "Taste decisions batched"],
        ["REFLECT", "Lessons → IMPROVEMENT.md", "—"],
    ],
    col_widths=[2.5*cm, 6.5*cm, 8.5*cm]
))
story.append(body(
    "<b>Methodology Stack (DDD + SDD + TDD):</b> DDD (4 project docs) provides autonomous judgment — \"should we build this?\". "
    "SDD (design doc with acceptance criteria) produces specs. TDD (tests before code) verifies delivery. "
    "Key insight: when no human reviews every line, the test suite IS the quality gate."
))

story.append(heading("7.2 Job System", 2))
story.append(body(
    "Background automation via macOS launchd — runs independently of chat sessions. The scheduler evaluates "
    "due jobs every hour, routes them to type-specific handlers via the executor, and persists state across restarts. "
    "The service manager handles long-running sidecars (Slack bot) with auto-restart and health monitoring."
))
story.append(img("job-system"))
story.append(caption("Figure 10: Job System — launchd scheduler, executor routing, signal pipeline, and sidecar services"))
story.append(make_table(
    ["Job Type", "Handler", "Examples", "Token Cost"],
    [
        ["signal_fetch", "httpx adapters (HN, RSS, GitHub)", "3x daily signal collection", "Zero (no LLM)"],
        ["signal_digest", "Sonnet 4.6 relevance scoring", "Daily digest, weekly rollup", "~2K tokens/run"],
        ["agent", "Headless Claude CLI + MCP", "Morning inbox, custom tasks", "Variable"],
        ["script", "Subprocess (deterministic)", "self-tune, feed calibration", "Zero (no LLM)"],
        ["maintenance", "Prune + cleanup", "Weekly cache cleanup", "Zero"],
    ],
    col_widths=[CONTENT_W*0.14, CONTENT_W*0.26, CONTENT_W*0.30, CONTENT_W*0.30]
))
story.append(body(
    "System jobs (signal-fetch, signal-digest, self-tune, weekly-maintenance, weekly-rollup) are defined in code and "
    "read-only. User jobs live in <font face='Courier' size='8'>user-jobs.yaml</font> with full CRUD via job_manager.py."
))

story.append(heading("7.3 Proactive Intelligence", 2))
story.append(body("1,142 lines, 106+ tests. Provides session-start briefings through five levels of analysis:"))
story.append(make_table(
    ["Level", "Capability", "How"],
    [
        ["L0", "Parsing", "Extract structured data from DailyActivity, MEMORY.md, open threads"],
        ["L1", "Temporal awareness", "Time-sensitive items, deadlines, recency weighting"],
        ["L2", "Scoring engine", "Priority × staleness × frequency × blocking × momentum per item"],
        ["L3", "Cross-session learning", "JSON-persisted: skip penalty for ignored, affinity bonus for accepted"],
        ["L4", "Signal highlights", "External intelligence (HN, RSS, GitHub) with effectiveness scoring"],
    ],
    col_widths=[CONTENT_W*0.08, CONTENT_W*0.20, CONTENT_W*0.72]
))
story.append(PageBreak())

# ======== 8. INTERFACE LAYER ========
story.append(heading("8. Three-Column Command Center"))
story.append(hr())
story.append(body(
    "The interface is a single integrated system where the Chat Center orchestrates everything. "
    "Three columns are views into one unified workspace connected by drag-to-chat context injection."
))
story.append(img("three-column-layout"))
story.append(caption("Figure 8: Three-Column Command Center — SwarmWS, Chat Center, Swarm Radar with drag-to-chat"))
story.append(make_table(
    ["Column", "Purpose", "Key Interactions"],
    [
        ["SwarmWS Explorer (left)", "Persistent local workspace", "Git-tracked + ETag polling. Drag files to chat. Agent reads/writes/commits directly."],
        ["Chat Center (center)", "Multi-session command surface", "SSE streaming, per-tab isolation, 55+ skills, MCP tools. Controls Explorer and Radar."],
        ["Swarm Radar (right)", "Attention dashboard", "ToDos, sessions, artifacts, jobs. Drag work packets to chat for instant context."],
    ],
    col_widths=[3.5*cm, 4.5*cm, 9.5*cm]
))
story.append(PageBreak())

# ======== 9. KEY DESIGN DECISIONS ========
story.append(heading("9. Key Design Decisions & Tradeoffs"))
story.append(hr())
story.append(make_table(
    ["Decision", "Choice", "Alternative", "Rationale"],
    [
        ["Memory", "3-layer distillation (files)", "Vector DB (RAG)", "Files are git-trackable, human-readable, editable, version-controlled"],
        ["Sessions", "4-component decomposition", "Monolithic AgentManager", "5,428-line God Object caused 15+ bugs (COE). Clean error boundaries."],
        ["Context", "11-file priority chain + budget", "Single system prompt", "Priority truncation ensures identity/safety survive under pressure"],
        ["Channels", "Shared session (serialized)", "Independent per channel", "'One brain': Slack knows what  said. No fragmentation."],
        ["Skills", "SKILL.md instruction files", "Compiled plugins", "LLM-native: agent reads as natural language. New skill = markdown file."],
        ["Data", "All local (SQLite + filesystem)", "Cloud database", "Zero cloud dependency. Privacy by default. Works offline."],
        ["Safety", "Defense-in-depth (7 layers)", "Single permission gate", "No single layer sufficient. Redundant protection."],
        ["Jobs", "macOS launchd", "In-process cron", "Survives app restarts, runs when app is closed, managed by OS."],
    ],
    col_widths=[2*cm, 3.8*cm, 3.5*cm, 8.2*cm]
))
story.append(PageBreak())

# ======== 10. COMPETITIVE POSITIONING ========
story.append(heading("10. Competitive Positioning"))
story.append(hr())
story.append(body(
    "SwarmAI occupies a unique position: not a code editor, not an IDE, not a CLI agent, not a multi-platform connector. "
    "It is an <b>agentic operating system</b> optimizing for depth over breadth."
))
story.append(make_table(
    ["Capability", "SwarmAI", "Claude Code", "Kiro", "Cursor", "OpenClaw"],
    [
        ["Memory", "3-layer pipeline", "CLAUDE.md (manual)", "Per-project", "Per-project", "Session pruning"],
        ["Context", "11-file + budgets", "Single prompt", "Spec-driven", "Codebase index", "Standard"],
        ["Multi-session", "1-4 parallel tabs", "1 session", "1 session", "1 session", "Per-channel"],
        ["Self-evolution", "55+ skills, corrections", "No", "No", "No", "No"],
        ["Autonomous pipeline", "8-stage + DDD+TDD", "Manual", "Spec-driven", "No", "No"],
        ["Multi-channel", "Unified brain", "Terminal", "IDE only", "IDE only", "21+ (isolated)"],
        ["Scope", "All knowledge work", "Coding", "Coding", "Coding", "Messaging"],
    ],
    col_widths=[2.8*cm, 3.2*cm, 2.8*cm, 2.5*cm, 2.5*cm, 3.7*cm]
))
story.append(callout(
    "<b>Core Differentiator:</b> The Harness. No competitor provides the compound loop of context engineering + "
    "memory distillation + self-evolution + safety harness that makes an AI agent genuinely improve over time."
))

# ======== 11. FUTURE ROADMAP ========
story.append(heading("11. Future Roadmap"))
story.append(hr())
story.append(make_table(
    ["Phase", "Target", "Key Deliverables"],
    [
        ["L3 Completion", "Q2 2026", "Growth metrics dashboard, DDD auto-sync, stale correction auto-healing"],
        ["L4 Autonomous", "Q3 2026", "Full AIDLC pipeline with checkpoint/resume, self-directed learning, judgment framework"],
        ["MCP Gateway", "When SDK supports", "Shared MCP instances across sessions (20 → 5 instances, ~2.9GB → ~750MB)"],
        ["Multi-User", "Q4 2026", "Team workspace, role-based access, collaborative memory"],
        ["Cross-Platform", "Q4 2026", "Linux support (launchd → systemd for background jobs)"],
    ],
    col_widths=[3*cm, 3*cm, 11.5*cm]
))

story.append(Spacer(1, 20))
story.append(Paragraph(
    "<b>Document History:</b> v1.0 (March 26, 2026) — Initial release for PE/Tech Leadership review.<br/>"
    "<b>Generated by:</b> Swarm (Claude Opus 4.6) under supervision of Xiaogang Wang (XG).<br/>"
    "<b>Repository:</b> github.com/xg-gh-25/SwarmAI",
    styles["Footer"]
))

# Build
doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
size_mb = OUT_PDF.stat().st_size / (1024 * 1024)
print(f"\nPDF generated: {OUT_PDF}")
print(f"Size: {size_mb:.1f} MB")
