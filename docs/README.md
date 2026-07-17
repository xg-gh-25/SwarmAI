# SwarmAI --- Technical Documentation

> **Harness Engineering: How a Stateless LLM Becomes a Persistent, Evolving Agent**

## PE Review Documents (3-doc set)

| # | Document | Pages | Description |
|---|----------|-------|-------------|
| 1 | [SwarmAI Architecture Design](SwarmAI-Architecture-Design.pdf) | 22 | **Start here.** Six-layer architecture, 11-file context chain, compound learning loop, daemon-first backend, OOM resilience, competitive positioning. |
| 2 | [Self-Evolution Harness](Self-Evolution-Harness-Design.pdf) | 27 | **The core innovation.** Context engineering (11-file P0-P10 chain), 9 post-session hooks, 19-module agent intelligence (4-phase), evolution pipeline v2 (MINE->ASSESS->ACT->AUDIT), 82-skill architecture, proactive intelligence (L0-L4), 7-layer safety. 28 files, 14,500+ lines. |
| 3 | [Memory Management](Memory-Management-Design.pdf) | 19 | **How the agent remembers.** 4-level cognitive memory (Semantic->Verbatim), pure-filesystem recall engine (keyword/FTS5/BM25), transcript indexing (1,500+ JSONL, 700MB+), temporal validity. 9 modules, 5,447 lines. |

## Reading Order

1. **Architecture Design** --- what SwarmAI is, how the layers fit together
2. **Self-Evolution Harness** --- how it works: the harness that turns a stateless LLM into a persistent, evolving agent
3. **Memory Management** --- deep dive into how the agent remembers and recalls across sessions

## Reference Documents

| Document | Pages | Description |
|----------|-------|-------------|
| [AIDLC Phase 3: Autonomous Pipeline](AIDLC-Phase3-Design.pdf) | 30 | The methodology framework (implementation-agnostic). DDD+SDD+TDD closed loop, 9-stage pipeline, decision classification, self-improvement flywheel. |
| [DDD Platform Overview](DDD-Platform-Overview.md) | — | One knowledge layer powering multiple delivery engines. Platform thesis, 3-layer architecture, 8 feed channels. |
| [Autonomous Pipeline Design](Autonomous-Pipeline-Design.md) | — | 9-stage pipeline + Quality Convergence Loop. Push-ready delivery guarantee. |
| [Goal Loop Design](Goal-Loop-Design.md) | — | Iterative convergence for open-ended objectives. Inline + scheduled modes, DoD-driven cycles, velocity tracking. |
| [Pollinate v3 Content Engine](Pollinate-Content-Engine.md) | — | Personal content delivery engine. DISCOVER stage + 11 production tracks (A–K) + audience simulation + cross-format consistency. |
| [Session Observation Layer](Session-Observation-Layer-Design.md) | — | Real-time tool-call recording. ObservationRing + DDD event emission + crash recovery enrichment. |
| [DDD Cultivation Engine](DDD-Cultivation-Engine-HLD.md) | — | How DDD self-grows from normal work. 8 feed channels, health scoring, cross-project routing. |

## Markdown Sources

Each PDF has a corresponding `.md` source in this directory. To regenerate:

```bash
# Self-Evolution Harness & Memory Management (LaTeX via pandoc+tectonic)
cd docs
pandoc Self-Evolution-Harness-Design.md -o Self-Evolution-Harness-Design.pdf \
  --pdf-engine=tectonic --toc --toc-depth=3 -N \
  -V geometry:margin=1in -V fontsize=11pt \
  -V colorlinks=true -V linkcolor=blue

pandoc Memory-Management-Design.md -o Memory-Management-Design.pdf \
  --pdf-engine=tectonic --toc --toc-depth=3 -N \
  -V geometry:margin=1in -V fontsize=11pt \
  -V colorlinks=true -V linkcolor=blue

# Architecture Design (HTML->PDF via Playwright, uses embedded SVGs)
cd ../Projects/SwarmAI/assets && python3 -c "
from playwright.sync_api import sync_playwright
import os
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(f'file://{os.path.abspath(\"arch-doc-v2.html\")}', wait_until='networkidle')
    page.pdf(path='SwarmAI-Architecture-Design-Doc.pdf', format='A4',
             margin={'top':'0.75in','bottom':'0.75in','left':'0.6in','right':'0.6in'},
             print_background=True)
    browser.close()
"
```

## Architecture Diagrams

All diagrams live in [`diagrams/`](diagrams/) as PNG files with dark backgrounds.

**Self-Evolution Harness diagrams:**

| Diagram | Used In | Description |
|---------|---------|-------------|
| `01-four-phase-architecture` | Harness, Section 4 | Safety -> Understanding -> Evolution -> E2E Hardening |
| `02-evolution-pipeline-v2` | Harness, Section 5 | MINE->ASSESS->ACT->AUDIT with confidence gates |
| `03-skill-lifecycle` | Harness, Section 6 | Lazy/always tiering, manifest.yaml, SDK injection |
| `07-compound-loop` | Architecture, Section 2.2 | Flywheel: session -> hooks -> memory -> evolution |
| `08-context-engineering` | Harness, Section 2 | 11-file P0-P10 chain with budget tiers and cache |
| `09-hook-pipeline` | Harness, Section 3 | 9 post-session hooks firing sequence |
| `10-proactive-intelligence` | Harness, Section 7 | L0-L4 levels converging into session briefing |

**Memory Management diagrams:**

| Diagram | Used In | Description |
|---------|---------|-------------|
| `04-memory-four-levels` | Memory, Section 2 | L1 Semantic -> L4 Verbatim, injection strategies |
| `05-recall-engine-flow` | Memory, Section 4 | Three-stage recall, hybrid search pipeline |
| `06-memory-pipeline-lifecycle` | Memory, Section 8 | Session start -> end -> weekly maintenance |

**AIDLC diagrams:**

| Diagram | Description |
|---------|-------------|
| `01-overall-architecture` | DDD+SDD+TDD closed loop with 9-stage pipeline |
| `02-ddd-four-pillars` | PRODUCT / TECH / IMPROVEMENT / PROJECT documents |
| `03-pipeline-stages` | EVALUATE->REFLECT end-to-end flow with artifacts |
| `04-self-improvement-flywheel` | MINE->ASSESS->ACT->AUDIT confidence-gated cycle |
| `05-decision-classification` | Mechanical / Taste / Judgment with escalation levels |
| `06-skill-lifecycle` | Skill creation, tiering, self-improvement, and guard system |

## Discussions (Thought Leadership)

All 70+ discussion articles are mirrored in [`discussions/`](discussions/) for DeepWiki indexing and offline reading.

| Theme | Key Articles |
|-------|-------------|
| **Agent Architecture** | Agent Harness Autonomy Levels (#33), Multi-Skill vs Multi-Agent (#12), Compound Intelligence (#10) |
| **Memory & Knowledge** | Memory as Moat (#3), 4-Layer Memory (#37), No Neo4j (#19/#20), DDD Cultivation (#9) |
| **Quality & Governance** | Three-Layer Governance (#21/#22), Adversarial Review (#29/#30), Personality Trap (#31/#32) |
| **Autonomous Delivery** | Coding as Black Box (#4), Content as Black Box (#5), AI for Data (#36) |
| **AI Strategy** | S×T Tension Matrix (#11), Flat vs Compound (#13), Startup Stages (#16) |

**Reading paths:** [Builder / Architect / Leader guide](discussions/35-reading-matrix-3-paths-through-72-discussions.md)

## Other Documentation

| Document | Description |
|----------|-------------|
| [User Guide](USER_GUIDE.md) | Getting started, daily workflows, tips |
| [Quick Start](../QUICK_START.md) | Installation and first-run guide |
| [AI Context](../AI_CONTEXT.md) | Structured evaluation guide for AI reviewers |

---

*Last updated: July 2026 --- v1.25.0*
