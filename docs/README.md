# SwarmAI --- Technical Documentation

> **Harness Engineering: How a Stateless LLM Becomes a Persistent, Evolving Agent**

## Current Designs

These describe SwarmAI as it works today. Where a document and the code disagree, the code wins.

| Document | Description |
|----------|-------------|
| [DDD Platform Overview](DDD-Platform-Overview.md) | **Start here.** One knowledge layer that several delivery engines read and write. What a project's brain (DDD) is, how knowledge gets in through CDF (five sources, no approval queue), and how the pipeline and Pollinate use it. |
| [Autonomous Pipeline HLD](Autonomous-Pipeline-HLD.md) | The pipeline for risky changes or when asked: up to eight stages from EVALUATE to REFLECT, independent expert review, tests on the reviewed code, and one local commit that the person reads and pushes. |
| [Pollinate](Pollinate-Content-Engine.md) | Content you revise many times: decks (HTML, PDF, PPTX), posters, pages, copy. A work and its versions, a declared revision scope with a guard that keeps hand edits, measured craft checks; the person publishes. |
| [SwarmAI Architecture Design](SwarmAI-Architecture-Design.md) | The layers of SwarmAI and how they fit together: the 11 context files, the session lifecycle, CDF and recall, the daemon-first backend, the in-process scheduler, the security hooks. |
| [DDD Distribution](2026-07-20-ddd-dual-target-distribution-design.md) | How a grown DDD is packaged as an organization's internal capabilities package or an Open-Plugins plugin, never beyond the reach its manifest declares, with a content scan that blocks secrets, machine paths and internal names. |

## Historical Designs (superseded)

Kept as a record of how SwarmAI was designed at the time. Each one starts with a banner that says what changed and where the current design is.

| Document | Description |
|----------|-------------|
| [Self-Evolution Harness](Self-Evolution-Harness-Design.md) (superseded) | The harness as designed in April--July 2026: the context chain, the post-session hooks, the MINE->ASSESS->ACT->AUDIT evolution pipeline and the L0-L4 session briefing. |
| [DDD HLD](DDD-Cultivation-Engine-HLD.md) (superseded) | The DDD as designed in May--July 2026, with an approval queue, health scores, maturity stages and an Entity Index. Its six-section structure and its distribution model still hold. |
| [DDD Runtime Activation](2026-06-10-ddd-runtime-activation-design.md) (superseded) | Five fixes meant to wire health scores, trust annotations and maturity promotion into sessions. All of these mechanisms were retired. |
| [Autonomous Pipeline Design](Autonomous-Pipeline-Design.md) (superseded) | The pipeline retired on 2026-10-23: nine stages, adversarial review, the Quality Convergence Loop and a push-ready gate. |
| [Goal Loop Design](Goal-Loop-Design.md) (superseded) | Iterative cycles toward a Definition of Done. The goal profile was removed from the pipeline on 2026-10-26. |
| [Memory Management](Memory-Management-Design.md) (superseded) | Memory as designed in April--July 2026: four memory levels, section caps and graduation, the transcript indexer. Recall by keyword search still holds; the write side is now CDF. |
| [Session Observation Layer](Session-Observation-Layer-Design.md) (superseded) | The per-session ring of tool calls. The ring still runs; most of the consumers described here were removed. |

## PDFs

PDFs are no longer kept in the repo; each document above is its own source. To make a PDF of one (LaTeX via pandoc and tectonic):

```bash
cd docs
pandoc Memory-Management-Design.md -o Memory-Management-Design.pdf \
  --pdf-engine=tectonic --toc --toc-depth=3 -N \
  -V geometry:margin=1in -V fontsize=11pt \
  -V colorlinks=true -V linkcolor=blue
```

A PDF made from a superseded source is historical in the same way.

## Architecture Diagrams

The PNG figures in [`diagrams/`](diagrams/) belong to the older overviews and historical designs above. They show the system as those documents describe it, not as it works today.

**Self-Evolution Harness diagrams (historical):**

| Diagram | Used In | Description |
|---------|---------|-------------|
| `01-four-phase-architecture` | Harness, Section 4 | Safety -> Understanding -> Evolution -> E2E Hardening |
| `02-evolution-pipeline-v2` | Harness, Section 5 | MINE->ASSESS->ACT->AUDIT with confidence gates |
| `03-skill-lifecycle` | Harness, Section 6 | Lazy/always tiering, manifest.yaml, SDK injection |
| `07-compound-loop` | Architecture, Section 2.2 | Flywheel: session -> hooks -> memory -> evolution |
| `08-context-engineering` | Harness, Section 2 | The context chain with budget tiers and cache, as designed then |
| `09-hook-pipeline` | Harness, Section 3 | The post-session hooks of that design, in firing order |
| `10-proactive-intelligence` | Harness, Section 7 | L0-L4 levels converging into a session briefing |

**Memory Management diagrams (older overview):**

| Diagram | Used In | Description |
|---------|---------|-------------|
| `04-memory-four-levels` | Memory, Section 2 | The four memory levels of that design, with injection strategies |
| `05-recall-engine-flow` | Memory, Section 4 | Three-stage recall, search pipeline |
| `06-memory-pipeline-lifecycle` | Memory, Section 8 | Session start -> end -> weekly maintenance |

**AIDLC diagrams (historical, from a removed AIDLC Phase 3 design):**

| Diagram | Description |
|---------|-------------|
| `01-overall-architecture` | DDD+SDD+TDD closed loop with the old 9-stage pipeline |
| `02-ddd-four-pillars` | PRODUCT / TECH / IMPROVEMENT / PROJECT documents |
| `03-pipeline-stages` | EVALUATE->REFLECT end-to-end flow of the old pipeline |
| `04-self-improvement-flywheel` | MINE->ASSESS->ACT->AUDIT confidence-gated cycle |
| `05-decision-classification` | Mechanical / Taste / Judgment with escalation levels (retired) |
| `06-skill-lifecycle` | Skill creation, tiering, self-improvement, and guard system |

## Discussions (Thought Leadership)

The discussion articles are mirrored in [`discussions/`](discussions/) for DeepWiki indexing and offline reading. Each one reflects the system on the day it was written.

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

*Last updated: November 2026*
