#!/usr/bin/env python3
"""Generate SwarmAI Architecture Design Document v2.0 as PDF.

Reads SVG files from the assets directory and embeds them inline.
Uses Playwright (headless Chromium) for HTML→PDF conversion.

Usage:
    python3 generate-arch-doc.py

Prerequisites:
    cd /tmp && npm install playwright && npx playwright install chromium

Source of truth hierarchy:
    1. SwarmAI-Architecture-Design-Doc.md  = canonical content (edit this)
    2. This file (generate-arch-doc.py)    = HTML template + PDF generator (mirrors .md)
    3. SwarmAI-Architecture-Design-Doc.pdf = generated output (never edit directly)
    4. TECH.md                             = DDD summary (shorter, cross-references full doc)

Key lessons (earned the hard way):
    - SVG containers MUST have `width: 100%; overflow: hidden` — native 960px SVGs
      overflow A4 pages and Chromium silently truncates all content after the overflow.
      This caused a 19-page doc to render as 7 pages with no error message.
    - CSS @page header/footer + Playwright displayHeaderFooter = duplicates.
      Use Playwright's displayHeaderFooter only (CSS @page is for weasyprint/print).
    - weasyprint needs system C libraries (gobject, pango, cairo) on macOS.
      Playwright is more reliable — Chromium bundles everything.
    - Always test PDF end-to-end after CSS changes. Check file size (0.8MB = broken,
      1.4MB = correct for this doc) and page count (19 pages expected).
"""

import os
from pathlib import Path
from datetime import datetime

ASSETS_DIR = Path(__file__).parent
OUTPUT_PDF = ASSETS_DIR / "SwarmAI-Architecture-Design-Doc.pdf"

def read_svg(name: str) -> str:
    """Read an SVG file and return its content for inline embedding."""
    path = ASSETS_DIR / name
    if not path.exists():
        return f'<p style="color:red">Missing: {name}</p>'
    content = path.read_text()
    # Strip XML declaration if present
    if content.startswith("<?xml"):
        content = content[content.index("?>") + 2:].strip()
    return content


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  @page {
    size: A4;
    margin: 2.2cm 2cm 2.5cm 2cm;
    /* Header/footer handled by Playwright displayHeaderFooter — no @top/@bottom rules here */
  }
  body { font-family: Arial, Helvetica, sans-serif; font-size: 10pt; line-height: 1.5; color: #1e293b; }
  h1 { font-size: 18pt; color: #1e293b; border-bottom: 2.5px solid #f59e0b; padding-bottom: 6px; margin-top: 30px; page-break-after: avoid; }
  h2 { font-size: 14pt; color: #334155; margin-top: 22px; page-break-after: avoid; }
  h3 { font-size: 11pt; color: #475569; margin-top: 16px; page-break-after: avoid; }
  p { margin: 6px 0; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0 14px 0; font-size: 9pt; page-break-inside: avoid; }
  th { background: #f1f5f9; color: #475569; font-weight: bold; text-align: left; padding: 6px 8px; border: 1px solid #e2e8f0; }
  td { padding: 5px 8px; border: 1px solid #e2e8f0; vertical-align: top; }
  tr:nth-child(even) td { background: #f8fafc; }
  .callout { background: #fffbeb; border-left: 3px solid #f59e0b; padding: 10px 14px; margin: 12px 0; font-size: 9.5pt; }
  .callout-blue { background: #eff6ff; border-left: 3px solid #3b82f6; padding: 10px 14px; margin: 12px 0; font-size: 9.5pt; }
  .svg-container { margin: 12px 0; text-align: center; page-break-inside: avoid; overflow: hidden; }
  .svg-container svg { max-width: 100%; width: 100%; height: auto; border-radius: 10px; display: block; margin: 0 auto; }
  .figure-caption { text-align: center; font-size: 8.5pt; color: #64748b; margin-top: 4px; }
  .cover-page { text-align: center; padding-top: 120px; page-break-after: always; }
  .cover-title { font-size: 32pt; font-weight: bold; color: #1e293b; margin: 0; }
  .cover-subtitle { font-size: 16pt; color: #f59e0b; font-weight: bold; margin: 8px 0 24px 0; }
  .cover-tagline { font-size: 12pt; color: #64748b; margin: 0 0 40px 0; }
  .cover-brand { font-size: 14pt; color: #f59e0b; font-weight: bold; letter-spacing: 3px; margin-bottom: 12px; }
  .cover-meta { margin-top: 30px; font-size: 9pt; color: #94a3b8; }
  .cover-meta table { margin: 16px auto; width: auto; border: none; }
  .cover-meta td { border: none; padding: 3px 12px; background: none !important; }
  .cover-meta td:first-child { color: #f59e0b; font-weight: bold; text-align: right; }
  ul { margin: 4px 0; padding-left: 20px; }
  li { margin: 2px 0; }
  .toc { page-break-after: always; }
  .toc h1 { border-bottom-color: #f59e0b; }
  .toc ol { font-size: 11pt; line-height: 2; }
  .toc ol ol { font-size: 10pt; line-height: 1.6; color: #64748b; }
  .new-badge { background: #dcfce7; color: #166534; font-size: 7.5pt; padding: 1px 5px; border-radius: 3px; font-weight: bold; vertical-align: middle; }
  .updated-badge { background: #dbeafe; color: #1e40af; font-size: 7.5pt; padding: 1px 5px; border-radius: 3px; font-weight: bold; vertical-align: middle; }
  code { background: #f1f5f9; padding: 1px 4px; border-radius: 3px; font-size: 9pt; }
</style>
</head>
<body>

<!-- ============================================================ -->
<!-- COVER PAGE -->
<!-- ============================================================ -->
<div class="cover-page">
  <div class="cover-brand">SWARMAI</div>
  <h1 class="cover-title" style="border:none; padding:0;">Agentic OS Architecture</h1>
  <div class="cover-subtitle">High-Level Design Document</div>
  <p class="cover-tagline">Harness Engineering: How a Stateless LLM Becomes<br>a Persistent, Evolving Agent</p>

  <div class="cover-meta">
    <table>
      <tr><td>6 Architecture Layers</td><td>Interface → Intelligence → Harness → Session → Engine → Platform</td></tr>
      <tr><td>11-File Context Chain</td><td>P0–P10 priority system with token budgets and L0/L1 caching</td></tr>
      <tr><td>3-Layer Memory Pipeline</td><td>Session capture → distillation → curated long-term memory (git-verified)</td></tr>
      <tr><td>Hybrid Memory Recall</td><td>FTS5 keyword + sqlite-vec vector search (Bedrock Titan v2 embeddings)</td></tr>
      <tr><td>56+ Skills</td><td>Self-evolution: agent builds new skills when it hits capability gaps</td></tr>
      <tr><td>8-Stage Autonomous Pipeline</td><td>EVALUATE → THINK → PLAN → BUILD → REVIEW → TEST → DELIVER → REFLECT</td></tr>
      <tr><td>Daemon-First Backend</td><td>launchd daemon runs 24/7 — desktop app is optional UI layer</td></tr>
      <tr><td>OOM Resilience</td><td>Proactive RSS-based restart at 1.2GB — prevents macOS jetsam kills</td></tr>
      <tr><td>Multi-Channel Unified Brain</td><td>Desktop + Slack — same agent, same memory, same context</td></tr>
    </table>
  </div>

  <div style="margin-top: 40px; font-size: 9pt; color: #94a3b8;">
    <p>Version: 2.1  •  Date: April 10, 2026</p>
    <p>Author: REDACTED_NAME (XG) + Swarm (AI Co-Architect)</p>
    <p>Status: For PE / Tech Leadership Review  •  Classification: Internal</p>
  </div>
</div>

<!-- ============================================================ -->
<!-- TABLE OF CONTENTS -->
<!-- ============================================================ -->
<div class="toc">
  <h1>Table of Contents</h1>
  <ol>
    <li><strong>Executive Summary</strong></li>
    <li><strong>Architecture Overview</strong>
      <ol><li>Six-Layer Architecture • 2.2 The Compound Loop</li></ol>
    </li>
    <li><strong>Core Engine &amp; Growth Trajectory</strong></li>
    <li><strong>The Harness — Core Innovation</strong>
      <ol><li>Context Engineering • 4.2 Memory Pipeline <span class="updated-badge">UPDATED</span> • 4.3 Self-Evolution — Next-Gen Agent Intelligence <span class="updated-badge">UPDATED</span> • 4.4 Safety &amp; Self-Harness</li></ol>
    </li>
    <li><strong>Swarm Brain — Multi-Channel Architecture</strong> <span class="updated-badge">UPDATED</span></li>
    <li><strong>Session Architecture &amp; Multi-Tab Parallel Sessions</strong></li>
    <li><strong>Intelligence Layer</strong>
      <ol><li>Autonomous Pipeline • 7.2 Job System • 7.3 Proactive Intelligence</li></ol>
    </li>
    <li><strong>Three-Column Command Center</strong></li>
    <li><strong>Daemon-First Backend</strong> <span class="new-badge">NEW</span></li>
    <li><strong>OOM Resilience &amp; Proactive Restart</strong> <span class="new-badge">NEW</span></li>
    <li><strong>Key Design Decisions &amp; Tradeoffs</strong> <span class="updated-badge">UPDATED</span></li>
    <li><strong>Competitive Positioning</strong></li>
    <li><strong>Future Roadmap</strong> <span class="updated-badge">UPDATED</span></li>
  </ol>
</div>

<!-- ============================================================ -->
<!-- 1. EXECUTIVE SUMMARY -->
<!-- ============================================================ -->
<h1>1. Executive Summary</h1>

<p>SwarmAI is a desktop application that wraps Claude's Agent SDK inside a harness — a structured layer of context management, persistent memory, self-evolution, and safety controls that transforms a stateless large language model into a persistent, evolving personal AI agent.</p>

<p>The core thesis: most AI tools reset when you close them. Context is lost, decisions are forgotten, and users re-explain the same things session after session. SwarmAI solves this structurally — not through fine-tuning, but through engineered knowledge persistence.</p>

<div class="callout">
Key Innovation: The "Harness" — an 11-file context priority chain, 3-layer memory distillation pipeline with hybrid vector+keyword recall, self-evolution registry, and 7 post-session hooks that create a compound loop: every session makes the next one better. Every correction prevents a class of future mistakes.
</div>

<h2>Key Metrics (April 2026)</h2>
<table>
  <tr><th>Metric</th><th>Value</th></tr>
  <tr><td>Commits</td><td>723+</td></tr>
  <tr><td>Built-in Skills</td><td>56+ (curated + self-built)</td></tr>
  <tr><td>Context Files</td><td>11 (P0–P10 priority chain)</td></tr>
  <tr><td>Post-Session Hooks</td><td>7 (auto-commit, DailyActivity, distillation, evolution ×2, context-health, improvement)</td></tr>
  <tr><td>Pipeline Stages</td><td>8 (EVALUATE → REFLECT)</td></tr>
  <tr><td>Session States</td><td>5 (COLD → STREAMING → IDLE → WAITING_INPUT → DEAD)</td></tr>
  <tr><td>Core Engine Level</td><td>L4 (Autonomous) — 12-module self-evolution loop closed + DDD refresh + hybrid recall</td></tr>
  <tr><td>Backend Mode</td><td>Daemon-first (launchd 24/7) — desktop app is optional UI layer</td></tr>
  <tr><td>OOM Protection</td><td>Proactive RSS restart at 1.2GB — prevents jetsam kills</td></tr>
  <tr><td>Memory Recall</td><td>Hybrid: FTS5 keyword + sqlite-vec vector (Bedrock Titan v2, 1024-dim)</td></tr>
  <tr><td>Channels</td><td>Desktop + Slack (unified brain)</td></tr>
  <tr><td>Tech Stack</td><td>4 languages: Rust (Tauri), TypeScript (React), Python (FastAPI), SQL (SQLite)</td></tr>
</table>

<!-- ============================================================ -->
<!-- 2. ARCHITECTURE OVERVIEW -->
<!-- ============================================================ -->
<h1>2. Architecture Overview</h1>

<h2>2.1 Six-Layer Architecture</h2>
<p>SwarmAI's architecture is organized into six horizontal layers. Each layer has a clear responsibility boundary. The Harness layer (Layer 3) is the core innovation — it is what differentiates SwarmAI from a simple LLM wrapper.</p>

<div class="svg-container">
  """ + read_svg("swarmai-architecture.svg") + """
</div>
<p class="figure-caption">Figure 1: SwarmAI Agentic OS Architecture — Six-layer design with the Harness as the core innovation</p>

<table>
  <tr><th>Layer</th><th>What It Does</th><th>Key Components</th></tr>
  <tr><td>Interface</td><td>Visual workspace, multi-tab chat, dashboard, channels</td><td>SwarmWS Explorer, Chat (1–4 tabs), Radar, Gateway (Slack)</td></tr>
  <tr><td>Intelligence</td><td>Proactive awareness, autonomous execution, jobs</td><td>Proactive Intelligence, Signal Pipeline, Autonomous Pipeline, Job System</td></tr>
  <tr><td>Harness</td><td>Core: raw Claude → persistent, evolving agent</td><td>Context (11 files), Memory (3-layer + hybrid recall), Evolution (56+ skills), Safety</td></tr>
  <tr><td>Session</td><td>Multi-session lifecycle, isolation, recovery</td><td>SessionRouter, SessionUnit (5-state), LifecycleManager, 7 Hooks</td></tr>
  <tr><td>Engine</td><td>AI model access, tool ecosystem</td><td>Claude Agent SDK, Bedrock/Anthropic, MCP Servers (7+), Skills Engine</td></tr>
  <tr><td>Platform</td><td>Desktop infra, all local, zero cloud</td><td>Tauri 2.0, React 19, FastAPI, SQLite, filesystem, launchd daemon (24/7)</td></tr>
</table>

<h2>2.2 The Compound Loop</h2>
<p>The defining characteristic is the compound loop — a feedback cycle where every session's output becomes the next session's input:</p>
<ul>
  <li><strong>Session executes</strong> — user interacts, decisions are made, code is written, files are created</li>
  <li><strong>Hooks fire</strong> — 7 post-session hooks capture: DailyActivity, auto-commit, distillation, evolution, context-health, improvement, evolution-trigger</li>
  <li><strong>Memory updates</strong> — DailyActivity accumulates; ≥3 unprocessed files trigger distillation promoting recurring themes to MEMORY.md</li>
  <li><strong>Context enriched</strong> — next session's system prompt assembled from updated 11-file chain with latest memory and project context</li>
  <li><strong>Agent is smarter</strong> — next session starts with full awareness of everything that happened and mistakes to avoid</li>
</ul>

<div class="callout">Design Principle: Prevention over recovery. The compound loop makes errors structurally impossible over time, not handled after they occur.</div>

<!-- ============================================================ -->
<!-- 3. CORE ENGINE -->
<!-- ============================================================ -->
<h1>3. Core Engine &amp; Growth Trajectory</h1>

<p>The Swarm Core Engine is the meta-architecture that ties all six flywheels together. Each flywheel feeds the others: memory informs context, context improves sessions, sessions trigger evolution, evolution builds skills, skills improve memory capture — compound growth with every interaction.</p>

<div class="svg-container">
  """ + read_svg("swarm-core-engine.svg") + """
</div>
<p class="figure-caption">Figure 2: Swarm Core Engine — Six interconnected flywheels and growth trajectory (L4 Autonomous — current)</p>

<table>
  <tr><th>Flywheel</th><th>What It Does</th><th>Key Components</th></tr>
  <tr><td>Self-Evolution</td><td>Observes user patterns, measures skill performance, auto-optimizes underperformers, never repeats mistakes</td><td>EVOLUTION.md, 56+ skills, SkillMetrics, EvolutionOptimizer, SessionMiner, SkillFitness, UserObserver, SkillGuard</td></tr>
  <tr><td>Self-Memory</td><td>3-layer distillation + hybrid recall, SessionRecall (FTS5 cross-session), MemoryGuard (all writes sanitized), git-verified, weekly LLM pruning</td><td>DailyActivity, distillation, MEMORY.md, SessionRecall, MemoryGuard, briefing, sqlite-vec</td></tr>
  <tr><td>Self-Context</td><td>11-file P0-P10 priority chain + token budgets + caching</td><td>Context loader, prompt builder, budget tiers, freshness</td></tr>
  <tr><td>Self-Harness</td><td>Validates context files, detects DDD staleness, auto-refresh</td><td>ContextHealthHook (light+deep), auto-commit, integrity</td></tr>
  <tr><td>Self-Health</td><td>Monitors services, resources, sessions; proactive restart, auto-restart</td><td>Service manager, resource monitor, lifecycle manager, OOM governance</td></tr>
  <tr><td>Self-Jobs</td><td>Background automation, scheduled tasks, signal pipeline</td><td>Job scheduler, service manager, signal fetch/digest</td></tr>
</table>

<h2>Growth Trajectory</h2>
<table>
  <tr><th>Level</th><th>State</th><th>Capabilities</th><th>Status</th></tr>
  <tr><td>L0</td><td>Reactive</td><td>Responds to questions, no memory</td><td>Complete</td></tr>
  <tr><td>L1</td><td>Self-Maintaining</td><td>Remembers, self-commits, captures corrections, health monitoring</td><td>Complete</td></tr>
  <tr><td>L2</td><td>Self-Improving</td><td>Weekly LLM maintenance, unified jobs, feedback loops closed</td><td>Complete</td></tr>
  <tr><td>L3</td><td>Self-Governing</td><td>Session-type context, proactive gap detection, DDD auto-sync</td><td>Complete</td></tr>
  <tr><td>L4</td><td>Autonomous</td><td><strong>Next-Gen Agent Intelligence: 12-module self-evolution loop closed.</strong> UserObserver → SkillMetrics → SessionMiner → EvolutionOptimizer → auto-deploy with backup. Plus DDD refresh, skill proposer, hybrid recall, MemoryGuard, proactive OOM restart.</td><td>Current (4/6 + 2 new)</td></tr>
</table>

<!-- ============================================================ -->
<!-- 4. THE HARNESS -->
<!-- ============================================================ -->
<h1>4. The Harness — Core Innovation</h1>

<p>The Harness is what makes SwarmAI more than a ChatGPT wrapper. It is a structured engineering layer between the user interface and the raw LLM that provides four critical capabilities: context continuity, memory persistence, self-improvement, and safety.</p>

<h2>4.1 Context Engineering</h2>
<p>Most AI tools assemble a single system prompt. SwarmAI maintains an 11-file priority chain (P0–P10) that is assembled, cached, and budget-managed through a multi-stage pipeline. This is the most token-intensive subsystem and the one with the highest impact on agent quality.</p>

<div class="svg-container">
  """ + read_svg("context-engineering.svg") + """
</div>
<p class="figure-caption">Figure 3: Context Engineering — 11-file priority chain with token budget management and L0/L1 caching</p>

<h3>Priority Chain</h3>
<table>
  <tr><th>P</th><th>File</th><th>Owner</th><th>Truncation</th><th>Purpose</th></tr>
  <tr><td>P0</td><td>SWARMAI.md</td><td>System</td><td>Never</td><td>Core identity &amp; principles</td></tr>
  <tr><td>P1</td><td>IDENTITY.md</td><td>System</td><td>Never</td><td>Agent name, avatar, intro</td></tr>
  <tr><td>P2</td><td>SOUL.md</td><td>System</td><td>Never</td><td>Personality &amp; tone</td></tr>
  <tr><td>P3</td><td>AGENT.md</td><td>System</td><td>Truncatable</td><td>Behavioral directives</td></tr>
  <tr><td>P4</td><td>USER.md</td><td>User</td><td>Truncatable</td><td>User preferences &amp; background</td></tr>
  <tr><td>P5</td><td>STEERING.md</td><td>User</td><td>Truncatable</td><td>Session-level overrides</td></tr>
  <tr><td>P6</td><td>TOOLS.md</td><td>User</td><td>Truncatable</td><td>Tool &amp; environment config</td></tr>
  <tr><td>P7</td><td>MEMORY.md</td><td>Agent</td><td>Head-trimmed</td><td>Persistent memory (newest kept)</td></tr>
  <tr><td>P8</td><td>EVOLUTION.md</td><td>Agent</td><td>Head-trimmed</td><td>Self-evolution registry</td></tr>
  <tr><td>P9</td><td>KNOWLEDGE.md</td><td>Auto</td><td>Truncatable</td><td>Domain knowledge index</td></tr>
  <tr><td>P10</td><td>PROJECTS.md</td><td>Auto</td><td>Lowest</td><td>Active projects index</td></tr>
</table>

<h3>Key Design Decisions</h3>
<ul>
  <li><strong>Session-type-aware loading</strong> — Channel DMs skip EVOLUTION.md, PROJECTS.md, DailyActivity (~30% token savings)</li>
  <li><strong>L0/L1 cache</strong> — L1 uses git-first freshness; L0 is AI-summarized compact version for constrained models</li>
  <li><strong>Head-trimming</strong> — MEMORY.md and EVOLUTION.md keep newest content; old entries trim from top</li>
  <li><strong>Token budget</strong> — 100K tokens for 1M context models; priority truncation removes P10 first, never touches P0–P2</li>
  <li><strong>Resume context checkpoint</strong> <span class="new-badge">NEW</span> — Structured ~600-token checkpoint (last request, files touched, git commits, agent spawns, tool activity) replaces 200K raw history dump for session recovery</li>
</ul>

<h2>4.2 Memory Pipeline <span class="updated-badge">UPDATED</span></h2>
<p>The memory pipeline is now a two-part system: a <strong>distillation pipeline</strong> that converts raw session activity into durable, curated knowledge, and a <strong>recall system</strong> that ensures any memory entry — regardless of age — can be found when relevant.</p>

<div class="svg-container">
  """ + read_svg("memory-pipeline.svg") + """
</div>
<p class="figure-caption">Figure 4: Memory Pipeline — Three-layer distillation + hybrid recall system with vector embeddings</p>

<h3>Distillation Pipeline</h3>
<table>
  <tr><th>Layer</th><th>Storage</th><th>Lifecycle</th><th>Content</th></tr>
  <tr><td>1. Capture</td><td>DailyActivity/YYYY-MM-DD.md</td><td>30 days → archived</td><td>Per-session: deliverables, git commits, decisions, lessons, next steps</td></tr>
  <tr><td>2. Distillation</td><td>Triggered when ≥3 files</td><td>At session start (silent)</td><td>Recurring themes promoted; noise filtered; claims verified against git log</td></tr>
  <tr><td>3. Curated</td><td>MEMORY.md</td><td>Permanent (weekly maint.)</td><td>Open Threads (P0/P1/P2), Key Decisions, Lessons, COE Registry</td></tr>
</table>

<h3>Recall System <span class="new-badge">NEW</span></h3>
<table>
  <tr><th>Layer</th><th>What</th><th>How</th><th>When</th></tr>
  <tr><td>L0: Memory Index</td><td>Compact ~500-token index of ALL memory entries</td><td>Value-based tiers (Permanent/Active), keyword aliases, stable keys [RC14], [KD08]</td><td>Always injected into system prompt</td></tr>
  <tr><td>L1: Section Selection</td><td>Topic-triggered loading of 0-3 MEMORY.md sections</td><td>Keyword relevance matching against first user message, budget-capped</td><td>Only when MEMORY.md exceeds 30K tokens</td></tr>
  <tr><td>L2: Hybrid Search</td><td>Semantic + keyword search over Knowledge Library</td><td>FTS5 keyword (0.4) + sqlite-vec vector (0.6), Bedrock Titan v2 embeddings (1024-dim)</td><td>Agent uses Read tool on demand</td></tr>
</table>

<div class="callout">Design Principle: "Power over token budget" — Token saving is NEVER the primary concern. Primary goal is always powerful function and maximum recall. Any memory entry, regardless of age, can be recalled when relevant.</div>

<h3>Git Cross-Reference (Safety)</h3>
<p>Born from a real Sev-2 incident (COE C005): the distillation hook verifies all implementation claims against <code>git log</code> before promoting to MEMORY.md. Without this, mid-session snapshots captured before later commits create false memories that compound across sessions.</p>

<h2>4.3 Self-Evolution — Next-Gen Agent Intelligence <span class="updated-badge">UPDATED</span></h2>
<p>Self-evolution is a <strong>closed-loop system</strong> across 12 modules in 4 phases. The agent observes user behavior, measures skill performance, mines correction patterns from session transcripts, and automatically rewrites underperforming skills — all with safety gates and audit trails.</p>

<div class="svg-container">
  """ + read_svg("self-evolution.svg") + """
</div>
<p class="figure-caption">Figure 5: Next-Gen Agent Intelligence — 12 modules across 4 phases forming a closed observe-measure-mine-optimize loop</p>

<table>
  <tr><th>Phase</th><th>Modules</th><th>Purpose</th></tr>
  <tr><td><strong>1: Safety</strong></td><td>MemoryGuard, SkillMetrics, SectionCaps, EntryRefs</td><td>Guard all MEMORY.md writes, measure skill performance, enforce memory size limits, cross-reference entries with 1-hop loading</td></tr>
  <tr><td><strong>2: Understanding</strong></td><td>UserObserver, SessionRecall, SkillRegistry, SkillGuard</td><td>Detect user patterns → USER.md suggestions, FTS5 cross-session search, compact skill index in prompts, trust-level security scanning</td></tr>
  <tr><td><strong>3: Evolution</strong></td><td>SessionMiner, SkillFitness, EvolutionOptimizer, RetentionPolicies</td><td>Mine transcripts for eval examples, 3-signal fitness scoring, correction-pattern rewriting with .bak backup + deploy + audit log</td></tr>
  <tr><td><strong>4: E2E Hardening</strong></td><td>12 fixes across 13 files</td><td>MemoryGuard on all write paths, wire dead ends, singleton caches, word-boundary recall, 1-hop ref loading</td></tr>
</table>

<h3>The Evolution Cycle</h3>
<p>Triggered at session close (7-day cadence) + Thursday 04:00 UTC cron fallback:</p>
<p><code>SessionMiner.mine_all() → SkillMetrics.get_evolution_candidates() → SkillFitnessEvaluator.score_batch() → EvolutionOptimizer.optimize_skill() → deploy_optimization() (SKILL.md.bak + modified SKILL.md + EVOLUTION.md log)</code></p>

<h3>EVOLUTION.md Registry</h3>
<table>
  <tr><th>Category</th><th>Lifecycle</th><th>Examples</th></tr>
  <tr><td>Capabilities Built</td><td>Active → archived if 0 usage for 30d</td><td>Browser agent, context monitor, workspace finder</td></tr>
  <tr><td>Optimizations</td><td>Permanent</td><td>Use CDP over WebSocket for persistent browser sessions</td></tr>
  <tr><td>Corrections</td><td>Permanent (NEVER deleted)</td><td>Reported features as 'not started' when fully shipped (C005)</td></tr>
  <tr><td>Competence</td><td>Cross-referenced</td><td>SSE streaming pipeline, multi-session architecture</td></tr>
  <tr><td>Failed Evolutions</td><td>Permanent</td><td>Approaches attempted and abandoned (with reasons)</td></tr>
</table>

<div class="callout">Design Principle: Corrections are the highest-value entries — proven failure modes with known patterns. Deleting a correction is equivalent to removing a safety guard. The registry is append-mostly; corrections are append-only.</div>

<div class="callout-blue">Key Lesson (2026-04-10): 206 unit tests passed across all 3 phases, yet E2E review found 3 critical wiring gaps (MemoryGuard bypass on 4 write paths, UserObserver dead-end output, 178 lines dead code). Unit tests prove components work; only E2E trace proves they're wired. Full design: <code>Next-Gen-Agent-Intelligence-Design-Doc.md</code>.</div>

<h2>4.4 Safety &amp; Self-Harness</h2>
<p>Safety is a structural property, not a feature. SwarmAI implements defense-in-depth through seven independent layers:</p>

<table>
  <tr><th>Layer</th><th>Mechanism</th><th>Details</th></tr>
  <tr><td>Tool Logger</td><td>Audit trail</td><td>Every tool invocation logged with timestamp, parameters, result</td></tr>
  <tr><td>Command Blocker</td><td>Pattern matching</td><td>13 dangerous patterns blocked (rm -rf, DROP TABLE, force push, etc.)</td></tr>
  <tr><td>Permission Dialog</td><td>Human approval</td><td>First-time external actions require approval; approvals persist</td></tr>
  <tr><td>Bash Sandbox</td><td>Claude SDK sandbox</td><td>Filesystem write restrictions, network allowlists, process isolation</td></tr>
  <tr><td>Escalation Protocol</td><td>Confidence-gated</td><td>3 levels: INFORM (act+tell), CONSULT (options+ask), BLOCK (stop+wait)</td></tr>
  <tr><td>ContextHealthHook</td><td>Integrity validation</td><td>Light (every session): file existence/format. Deep (weekly): staleness</td></tr>
  <tr><td>Decision Classification</td><td>Judgment framework</td><td>mechanical (auto), taste (batch), judgment (block for human)</td></tr>
</table>

<!-- ============================================================ -->
<!-- 5. SWARM BRAIN -->
<!-- ============================================================ -->
<h1>5. Swarm Brain — Multi-Channel Architecture</h1>

<p>Swarm is a personal assistant with one brain. Regardless of channel — desktop or Slack — it is the same Swarm, same memory, same context. Adding a new channel: write an adapter (~250 lines), register in gateway, map user identity. Zero architecture change.</p>

<div class="svg-container">
  """ + read_svg("swarm-brain.svg") + """
</div>
<p class="figure-caption">Figure 6: Swarm Brain — One AI, every channel, three layers of continuity</p>

<table>
  <tr><th>Layer</th><th>Mechanism</th><th>Scope</th></tr>
  <tr><td>L1: Shared Memory</td><td>11 context files loaded at every prompt build</td><td>All sessions (tabs + channels)</td></tr>
  <tr><td>L2: Cross-Channel Session</td><td>All channels share ONE Claude conversation (--resume)</td><td>Slack + future channels</td></tr>
  <tr><td>L3: Active Session Digest</td><td>Sibling session summaries injected into prompts</td><td>Tabs ↔ Channels (bidirectional)</td></tr>
</table>

<h3>Key Design Decisions</h3>
<ul>
  <li>Chat tabs are parallel (multi-slot, per-topic) — for deep work</li>
  <li>Channel session is serialized (single dedicated slot) — for quick exchanges across platforms</li>
  <li>One dedicated channel slot always reserved (min_tabs = 2) — channels never starve chat, chat never starves channels</li>
  <li>User identity mapping ties platform IDs (Slack W017T04E) to one unified user_key</li>
</ul>

<!-- ============================================================ -->
<!-- 6. SESSION ARCHITECTURE -->
<!-- ============================================================ -->
<h1>6. Session Architecture &amp; Multi-Tab Parallel Sessions</h1>

<p>Replaced a monolithic AgentManager (5,428 lines) with four focused components during the v7 re-architecture. Driven by real need: parallel chat tabs + dedicated channel slots without resource exhaustion.</p>

<div class="svg-container">
  """ + read_svg("multi-tab-sessions.svg") + """
</div>
<p class="figure-caption">Figure 7: Multi-Tab Parallel Sessions — SessionRouter, 5-state SessionUnits, dedicated channel slot</p>

<table>
  <tr><th>Component</th><th>Responsibility</th></tr>
  <tr><td>SessionRouter</td><td>Slot acquisition, IDLE eviction, queue timeout (60s), MAX_CONCURRENT=2</td></tr>
  <tr><td>SessionUnit</td><td>5-state machine (COLD→STREAMING→IDLE→WAIT→DEAD), subprocess spawn, 3x retry with --resume, SSE</td></tr>
  <tr><td>LifecycleManager</td><td>60s health loop, 12hr TTL kill, DEAD→COLD cleanup, startup orphan reaper, <strong>proactive RSS restart</strong></td></tr>
  <tr><td>SessionRegistry</td><td>Module-level singletons, initialize() wires components, configure_hooks()</td></tr>
</table>

<h3>Key Invariants</h3>
<ul>
  <li>Protected states (STREAMING, WAITING_INPUT) are never evicted</li>
  <li>Subprocess spawn serialized via module-level locks</li>
  <li>Retry uses <code>--resume</code> to restore conversation context across crashes</li>
  <li>Hooks fire via BackgroundHookExecutor — never block the request path</li>
  <li>One dedicated slot always reserved for channels (min_tabs = 2)</li>
  <li><strong>Proactive restart</strong> <span class="new-badge">NEW</span>: when RSS exceeds 1.2GB, compact → kill → lazy resume (prevents jetsam OOM kills)</li>
</ul>

<!-- ============================================================ -->
<!-- 7. INTELLIGENCE LAYER -->
<!-- ============================================================ -->
<h1>7. Intelligence Layer</h1>

<p>The Intelligence layer provides proactive awareness, autonomous execution, and background automation. While the Harness ensures the agent remembers and improves, this layer ensures it anticipates, acts, and automates.</p>

<h2>7.1 Autonomous Pipeline</h2>
<p>Drives the full development lifecycle from a one-sentence requirement to PR-ready delivery. Implementation of AIDLC Phase 3 (AI-Management): AI makes autonomous decisions, humans step in when needed.</p>

<div class="svg-container">
  """ + read_svg("autonomous-pipeline.svg") + """
</div>
<p class="figure-caption">Figure 8: Autonomous Pipeline — 8-stage lifecycle with DDD+SDD+TDD methodology and safety mechanisms</p>

<table>
  <tr><th>Stage</th><th>Output</th><th>Gate</th></tr>
  <tr><td>EVALUATE</td><td>ROI score, GO/DEFER/REJECT</td><td>ROI ≥ 3.5 to proceed</td></tr>
  <tr><td>THINK</td><td>3 alternatives (Minimal/Ideal/Creative)</td><td>User picks approach</td></tr>
  <tr><td>PLAN</td><td>Design doc (SDD) + acceptance criteria</td><td>Design approval</td></tr>
  <tr><td>BUILD</td><td>Code + tests (TDD: RED → GREEN → VERIFY)</td><td>All tests pass</td></tr>
  <tr><td>REVIEW</td><td>Code quality scan + security scan</td><td>No high-severity findings</td></tr>
  <tr><td>TEST</td><td>Full suite, regression check</td><td>WTF Gate (halt if risky)</td></tr>
  <tr><td>DELIVER</td><td>PR description, decision log, report</td><td>Taste decisions batched</td></tr>
  <tr><td>REFLECT</td><td>Lessons → IMPROVEMENT.md</td><td>—</td></tr>
</table>

<p>Methodology Stack (DDD + SDD + TDD): DDD (4 project docs) provides autonomous judgment — "should we build this?". SDD (design doc with acceptance criteria) produces specs. TDD (tests before code) verifies delivery. Key insight: when no human reviews every line, the test suite IS the quality gate.</p>

<h2>7.2 Job System</h2>
<p>Background automation via macOS launchd — runs independently of chat sessions. The scheduler evaluates due jobs every hour, routes them to type-specific handlers via the executor, and persists state across restarts. The service manager handles long-running sidecars (Slack bot) with auto-restart and health monitoring.</p>

<div class="svg-container">
  """ + read_svg("job-system.svg") + """
</div>
<p class="figure-caption">Figure 9: Job System — launchd scheduler, executor routing, signal pipeline, and sidecar services</p>

<table>
  <tr><th>Job Type</th><th>Handler</th><th>Examples</th><th>Token Cost</th></tr>
  <tr><td>signal_fetch</td><td>httpx adapters (HN, RSS, GitHub)</td><td>3x daily signal collection</td><td>Zero (no LLM)</td></tr>
  <tr><td>signal_digest</td><td>Sonnet 4.6 relevance scoring</td><td>Daily digest, weekly rollup</td><td>~2K tokens/run</td></tr>
  <tr><td>agent</td><td>Headless Claude CLI + MCP</td><td>Morning inbox, custom tasks</td><td>Variable</td></tr>
  <tr><td>script</td><td>Subprocess (deterministic)</td><td>self-tune, feed calibration</td><td>Zero (no LLM)</td></tr>
  <tr><td>maintenance</td><td>Prune + cleanup + L4 proposals</td><td>Weekly cache cleanup, DDD refresh, skill proposer</td><td>~$0.25/week</td></tr>
</table>

<h2>7.3 Proactive Intelligence</h2>
<p>1,142 lines, 106+ tests. Provides session-start briefings through five levels of analysis:</p>

<table>
  <tr><th>Level</th><th>Capability</th><th>How</th></tr>
  <tr><td>L0</td><td>Parsing</td><td>Extract structured data from DailyActivity, MEMORY.md, open threads</td></tr>
  <tr><td>L1</td><td>Temporal awareness</td><td>Time-sensitive items, deadlines, recency weighting</td></tr>
  <tr><td>L2</td><td>Scoring engine</td><td>Priority × staleness × frequency × blocking × momentum per item</td></tr>
  <tr><td>L3</td><td>Cross-session learning</td><td>JSON-persisted: skip penalty for ignored, affinity bonus for accepted</td></tr>
  <tr><td>L4</td><td>Signal highlights</td><td>External intelligence (HN, RSS, GitHub) with effectiveness scoring</td></tr>
</table>

<!-- ============================================================ -->
<!-- 8. THREE-COLUMN COMMAND CENTER -->
<!-- ============================================================ -->
<h1>8. Three-Column Command Center</h1>

<p>The interface is a single integrated system where the Chat Center orchestrates everything. Three columns are views into one unified workspace connected by drag-to-chat context injection.</p>

<div class="svg-container">
  """ + read_svg("three-column-layout.svg") + """
</div>
<p class="figure-caption">Figure 10: Three-Column Command Center — SwarmWS, Chat Center, Swarm Radar with drag-to-chat</p>

<table>
  <tr><th>Column</th><th>Purpose</th><th>Key Interactions</th></tr>
  <tr><td>SwarmWS Explorer (left)</td><td>Persistent local workspace</td><td>Git-tracked + ETag polling. Agent reads/writes/commits directly.</td></tr>
  <tr><td>Chat Center (center)</td><td>Multi-session command surface</td><td>SSE streaming, per-tab isolation, 56+ skills, MCP tools. Controls Explorer and Radar.</td></tr>
  <tr><td>Swarm Radar (right)</td><td>Attention dashboard</td><td>ToDos, sessions, artifacts, jobs. Drag work packets to chat for instant context.</td></tr>
</table>

<!-- ============================================================ -->
<!-- 9. DAEMON-FIRST BACKEND (NEW) -->
<!-- ============================================================ -->
<h1>9. Daemon-First Backend <span class="new-badge">NEW</span></h1>

<p>A fundamental architectural shift (March 30, 2026): <strong>Tauri now connects to a launchd-managed daemon instead of spawning a sidecar.</strong> The backend runs independently of the desktop app, enabling 24/7 operation for Slack, background jobs, and scheduled tasks.</p>

<h3>How It Works</h3>
<table>
  <tr><th>Step</th><th>What Happens</th><th>Detail</th></tr>
  <tr><td>1. App Launch</td><td>Tauri probes for running daemon</td><td>Retry 5×2s at discovered port via psutil</td></tr>
  <tr><td>2. No Daemon?</td><td>Auto-bootstrap via launchctl</td><td><code>launchctl bootstrap gui/&lt;uid&gt; com.swarmai.backend.plist</code></td></tr>
  <tr><td>3. Still No?</td><td>Fallback: spawn as sidecar</td><td>Legacy mode — same as v1.0, but rare</td></tr>
  <tr><td>4. App Closes</td><td>Daemon stays alive</td><td>Slack, jobs, signals continue 24/7</td></tr>
  <tr><td>5. Crash Recovery</td><td>launchd auto-restarts</td><td>KeepAlive=true, max 3 retries, exit 37 for bootstrap conflict</td></tr>
</table>

<h3>Key Benefits</h3>
<ul>
  <li><strong>Always-on Slack</strong> — Slack bot stays connected when app is closed. No missed messages.</li>
  <li><strong>Background jobs run 24/7</strong> — Morning inbox, signal fetching, self-tune all execute on schedule without the desktop app.</li>
  <li><strong>Instant app startup</strong> — No cold-start delay; backend is already warm.</li>
  <li><strong>Crash resilience</strong> — launchd auto-restarts the daemon; desktop app reconnects seamlessly.</li>
</ul>

<div class="callout-blue">Architecture Insight: The desktop app is now an <strong>optional UI layer</strong>, not the system's brain. SwarmAI's intelligence lives in the daemon — the app just visualizes it.</div>

<!-- ============================================================ -->
<!-- 10. OOM RESILIENCE (NEW) -->
<!-- ============================================================ -->
<h1>10. OOM Resilience &amp; Proactive Restart <span class="new-badge">NEW</span></h1>

<p>macOS jetsam kills processes that exceed memory pressure thresholds — with no warning, no cleanup, no resume. SwarmAI now <strong>proactively</strong> restarts sessions before jetsam acts, preserving conversation context.</p>

<h3>The Problem</h3>
<p>Each Claude CLI subprocess + its MCP servers costs ~500MB RSS. Two active tabs = ~1GB. With context growth during long sessions, processes can reach 1.2-1.5GB, triggering jetsam exit code -9 kills. Previously: total data loss for that session.</p>

<h3>The Solution: Proactive Restart-with-Resume</h3>
<table>
  <tr><th>Component</th><th>What</th><th>Detail</th></tr>
  <tr><td>Trigger B (primary)</td><td>Post-turn RSS check</td><td>After STREAMING → IDLE, check process tree RSS. If &gt; 1.2GB: trigger restart.</td></tr>
  <tr><td>Trigger A (fallback)</td><td>Lifecycle maintenance loop</td><td>Every 60s, check all IDLE sessions. Catches sessions that grew but received no new messages.</td></tr>
  <tr><td>Restart Flow</td><td>Compact → kill → lazy resume</td><td>Trigger SDK compaction, kill subprocess, mark COLD. Next message auto-respawns with <code>--resume</code>.</td></tr>
  <tr><td>Cooldown</td><td>3-minute per-session cooldown</td><td>Prevents restart loops. OOM crash adds 30-120s backoff.</td></tr>
  <tr><td>Auto-resume</td><td><code>_ensure_spawned</code> always checks</td><td>If COLD + existing <code>_sdk_session_id</code> → auto-inject <code>--resume</code>. Covers ALL kill-then-respawn paths.</td></tr>
</table>

<h3>Resource Monitoring</h3>
<ul>
  <li><strong>Effective memory</strong>: <code>total - available</code> (matches macOS jetsam logic, NOT <code>psutil.virtual_memory().used</code>)</li>
  <li><strong>Pressure levels</strong>: ok (&lt;80%) | warning (80-90%) | critical (≥90%)</li>
  <li><strong>Spawn budget</strong>: Dynamic tab limit from available RAM — prevents overcommit</li>
</ul>

<div class="callout">Design Principle: "Prevent, don't handle." Proactive restart <em>prevents</em> jetsam kills structurally — it doesn't try to recover after the fact. The best error handler is the one that never fires.</div>

<!-- ============================================================ -->
<!-- 11. KEY DESIGN DECISIONS -->
<!-- ============================================================ -->
<h1>11. Key Design Decisions &amp; Tradeoffs</h1>

<table>
  <tr><th>Decision</th><th>Choice</th><th>Alternative</th><th>Rationale</th></tr>
  <tr><td>Memory</td><td>3-layer distillation + hybrid recall (files + vector)</td><td>Pure RAG / Vector DB</td><td>Files are git-trackable, human-readable, editable. Vector adds semantic recall without replacing files.</td></tr>
  <tr><td>Sessions</td><td>4-component decomposition</td><td>Monolithic AgentManager</td><td>5,428-line God Object caused 15+ bugs (COE). Clean error boundaries.</td></tr>
  <tr><td>Context</td><td>11-file priority chain + budget</td><td>Single system prompt</td><td>Priority truncation ensures identity/safety survive under pressure</td></tr>
  <tr><td>Channels</td><td>Shared session (serialized)</td><td>Independent per channel</td><td>'One brain': Slack knows what desktop said. No fragmentation.</td></tr>
  <tr><td>Skills</td><td>SKILL.md instruction files</td><td>Compiled plugins</td><td>LLM-native: agent reads as natural language. New skill = markdown file.</td></tr>
  <tr><td>Data</td><td>All local (SQLite + filesystem)</td><td>Cloud database</td><td>Zero cloud dependency. Privacy by default. Works offline.</td></tr>
  <tr><td>Safety</td><td>Defense-in-depth (7 layers)</td><td>Single permission gate</td><td>No single layer sufficient. Redundant protection.</td></tr>
  <tr><td>Jobs</td><td>macOS launchd daemon</td><td>In-process cron</td><td>Survives app restarts, runs when app is closed, managed by OS.</td></tr>
  <tr><td>Backend</td><td>Daemon-first (launchd)</td><td>App-managed sidecar</td><td>24/7 operation: Slack, jobs, signals run without desktop app.</td></tr>
  <tr><td>OOM</td><td>Proactive RSS restart</td><td>Reactive crash recovery</td><td>Prevention &gt; recovery. Jetsam kills lose all context; proactive restart preserves it.</td></tr>
  <tr><td>Agents</td><td>Single-agent role-switching</td><td>Multi-agent orchestration</td><td>Zero context transfer cost. Multi-agent coordination is a tax on limited cognition.</td></tr>
</table>

<!-- ============================================================ -->
<!-- 12. COMPETITIVE POSITIONING -->
<!-- ============================================================ -->
<h1>12. Competitive Positioning</h1>

<p>SwarmAI occupies a unique position: not a code editor, not an IDE, not a CLI agent, not a multi-platform connector. It is an agentic operating system optimizing for depth over breadth.</p>

<table>
  <tr><th>Capability</th><th>SwarmAI</th><th>Claude Code</th><th>Kiro</th><th>Cursor</th><th>OpenClaw</th></tr>
  <tr><td>Memory</td><td>3-layer + hybrid vector recall</td><td>CLAUDE.md (manual)</td><td>Per-project</td><td>Per-project</td><td>Session pruning</td></tr>
  <tr><td>Context</td><td>11-file + budgets</td><td>Single prompt</td><td>Spec-driven</td><td>Codebase index</td><td>Standard</td></tr>
  <tr><td>Multi-session</td><td>1-4 parallel tabs</td><td>1 session</td><td>1 session</td><td>1 session</td><td>Per-channel</td></tr>
  <tr><td>Self-evolution</td><td>Closed-loop: 12 modules, auto-optimize</td><td>No</td><td>No</td><td>No</td><td>No</td></tr>
  <tr><td>Autonomous pipeline</td><td>8-stage + DDD+TDD</td><td>Manual</td><td>Spec-driven</td><td>No</td><td>No</td></tr>
  <tr><td>Multi-channel</td><td>Unified brain</td><td>Terminal</td><td>IDE only</td><td>IDE only</td><td>21+ (isolated)</td></tr>
  <tr><td>Scope</td><td>All knowledge work</td><td>Coding</td><td>Coding</td><td>Coding</td><td>Messaging</td></tr>
  <tr><td>OOM protection</td><td>Proactive RSS restart</td><td>No</td><td>No</td><td>No</td><td>No</td></tr>
  <tr><td>Always-on</td><td>launchd daemon 24/7</td><td>No</td><td>No</td><td>No</td><td>Per-channel</td></tr>
</table>

<div class="callout">Core Differentiator: The Harness. No competitor provides the compound loop of context engineering + memory distillation with hybrid recall + self-evolution + safety harness that makes an AI agent genuinely improve over time.</div>

<!-- ============================================================ -->
<!-- 13. FUTURE ROADMAP -->
<!-- ============================================================ -->
<h1>13. Future Roadmap</h1>

<table>
  <tr><th>Phase</th><th>Target</th><th>Key Deliverables</th></tr>
  <tr><td>L4 Completion</td><td>Q2 2026</td><td>Judgment framework, compound-without-user autonomous execution</td></tr>
  <tr><td>Signal Fetcher Service</td><td>Q2 2026</td><td>Services/signals/ directory, HN API + RSS feeds (no Tavily)</td></tr>
  <tr><td>MCP Gateway</td><td>When SDK supports</td><td>Shared MCP instances across sessions (20 → 5 instances, ~2.9GB → ~750MB)</td></tr>
  <tr><td>Multi-User</td><td>Q4 2026</td><td>Team workspace, role-based access, collaborative memory</td></tr>
  <tr><td>Cross-Platform</td><td>Q4 2026</td><td>Linux support (launchd → systemd for background jobs)</td></tr>
</table>

<div style="margin-top: 30px; font-size: 8.5pt; color: #94a3b8;">
  <p>Document History:</p>
  <p>v2.1 (April 10, 2026) — Next-Gen Agent Intelligence: 12-module self-evolution loop (4 phases), updated SVG, flywheel tables, L4 growth level, competitive positioning.</p>
  <p>v2.0 (April 8, 2026) — Major refresh: Daemon-first backend, OOM resilience, hybrid memory recall, updated metrics &amp; competitive positioning.</p>
  <p>v1.0 (March 26, 2026) — Initial release for PE/Tech Leadership review.</p>
  <p>Generated by: Swarm (Claude Opus 4.6) under supervision of REDACTED_NAME (XG).</p>
  <p>Repository: github.com/xg-gh-25/SwarmAI</p>
</div>

</body>
</html>
"""

def main():
    import subprocess
    import shutil

    print("Generating SwarmAI Architecture Design Document v2.0...")
    print(f"Reading SVGs from: {ASSETS_DIR}")

    html_content = HTML_TEMPLATE

    # Write intermediate HTML
    html_path = ASSETS_DIR / "arch-doc-v2.html"
    html_path.write_text(html_content)
    print(f"HTML written: {len(html_content):,} chars → {html_path}")

    # Generate PDF via Playwright (Chromium headless)
    # Playwright must be installed: cd /tmp && npm install playwright && npx playwright install chromium
    node_script = f"""
    const {{ chromium }} = require('playwright');
    const fs = require('fs');
    (async () => {{
      const browser = await chromium.launch();
      const page = await browser.newPage();
      await page.goto('file://{html_path}', {{ waitUntil: 'networkidle', timeout: 30000 }});
      await page.pdf({{
        path: '{OUTPUT_PDF}',
        format: 'A4',
        margin: {{ top: '2.2cm', bottom: '2.5cm', left: '2cm', right: '2cm' }},
        printBackground: true,
        displayHeaderFooter: true,
        headerTemplate: '<div style="font-size:7.5pt;color:#64748b;font-family:Arial,sans-serif;width:100%;padding:0 2cm;">SwarmAI — Agentic OS Architecture  •  High-Level Design Document  •  April 2026</div>',
        footerTemplate: '<div style="font-size:7.5pt;color:#64748b;font-family:Arial,sans-serif;width:100%;padding:0 2cm;display:flex;justify-content:space-between;"><span>Internal — For PE / Tech Leadership Review</span><span>Page <span class="pageNumber"></span></span></div>',
      }});
      await browser.close();
      const stats = fs.statSync('{OUTPUT_PDF}');
      console.log('PDF generated: ' + (stats.size / 1024 / 1024).toFixed(1) + ' MB');
    }})().catch(e => {{ console.error(e.message); process.exit(1); }});
    """

    # Find playwright — check /tmp/node_modules first (where we installed it)
    playwright_dirs = ["/tmp", str(Path.home() / "node_modules")]
    node_cwd = None
    for d in playwright_dirs:
        if (Path(d) / "node_modules" / "playwright").exists():
            node_cwd = d
            break

    if not node_cwd:
        print("ERROR: Playwright not found. Install it:")
        print("  cd /tmp && npm install playwright && npx playwright install chromium")
        raise SystemExit(1)

    print(f"Using Playwright from: {node_cwd}")
    result = subprocess.run(
        ["node", "-e", node_script],
        cwd=node_cwd,
        capture_output=True, text=True, timeout=60
    )

    if result.returncode != 0:
        print(f"ERROR: {result.stderr}")
        raise SystemExit(1)

    print(result.stdout.strip())

    # Cleanup intermediate HTML
    html_path.unlink(missing_ok=True)

    size_mb = OUTPUT_PDF.stat().st_size / (1024 * 1024)
    print(f"Done: {OUTPUT_PDF.name} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
