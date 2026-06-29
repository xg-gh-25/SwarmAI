---
title: "Skill Portability is a Distribution Problem, Not a Packaging Problem"
created: 2026-06-06
updated: 2026-06-06
status: published
---
<!-- GitHub Discussion #61: https://github.com/xg-gh-25/SwarmAI/discussions/61 -->
## The Temptation

Every AI agent system eventually asks: "How do we let users share skills?" The obvious answer is a package manager — manifests, registries, dependency resolution, versioning.

We looked at this deeply. Here's why we chose NOT to build one.

## What We Learned

**Observation 1: The valuable part of a skill is not the code.**

Our CMHK reporting skills (9 of them) have ~200 lines of code each. But they're useless without:
- 18 table schemas (what columns exist, what values mean)
- Hierarchy logic (5-level org scope resolution)
- SQL templates (28 verified queries)
- Business rules ("YTD means fiscal year, not calendar year")

Packaging the code without the domain knowledge is like shipping a car without fuel. The skill *runs* but produces garbage.

**Observation 2: AI agents ARE the installer.**

Traditional package managers exist because humans can't reliably execute multi-step installations. AI agents can. You don't need `npm install` when you can say "read INSTALL.md and do what it says."

The correct primitive isn't a CLI — it's a well-structured directory that any AI agent can understand:

```
my-skill/
├── SKILL.md          # What this does, when to use it
├── INSTRUCTIONS.md   # How to execute (the actual skill logic)
├── CONTEXT.md        # Domain knowledge required
├── INSTALL.md        # Steps for AI agent to perform
└── scripts/          # Supporting code (if any)
```

**Observation 3: The real barrier is context, not installation.**

When we tried giving our CMHK skills to a colleague's Claude Code instance, installation took 30 seconds. But the skill couldn't answer questions correctly because it didn't know:
- Which Athena table to query
- What `sh_l3` means in the hierarchy
- That `month_sequence` only has value 1 (a data quirk)

The "portable skill" problem is actually a **portable context** problem.

## Our Position

**Don't build a package manager. Build a context bundling standard.**

A skill pack should be:
1. **Self-contained** — includes both code AND the domain knowledge needed to use it correctly
2. **AI-installable** — any agent (Claude Code, Cursor, Windsurf) can read the INSTALL.md and set it up
3. **Platform-agnostic** — works on any system that supports markdown-based skill definitions
4. **Verification-included** — VERIFY.md tells the installing agent how to confirm it works

What it should NOT be:
- A registry (GitHub repos are registries)
- A dependency graph (skills should be self-contained or explicitly document what they need in INSTALL.md)
- A versioning system (git tags are versioning)
- A runtime (the host agent system is the runtime)

## The Anti-Pattern: Skills Without Context

The AI tools ecosystem is racing to build skill marketplaces. Most will fail because they're solving the wrong problem:

| What they build | What users actually need |
|----------------|-------------------------|
| Package registry | A directory on GitHub |
| Dependency resolver | Self-contained skills |
| Version manager | Git tags |
| CLI installer | AI agent reads INSTALL.md |
| Sandbox runtime | Host system permissions |

The winning format will be the one that bundles **context with code** — not the one with the best CLI UX.

## Practical Recommendation

If you're building a skill-sharing system:

1. **Start with a flat directory** — SKILL.md + INSTRUCTIONS.md + CONTEXT.md + INSTALL.md
2. **Include domain knowledge** — schemas, templates, business rules, examples
3. **Write INSTALL.md for an AI agent** — not for a human reading a terminal
4. **Ship a VERIFY.md** — how does the installer know it worked?
5. **Don't build infra you don't need** — GitHub is your registry, git is your version control, the AI agent is your installer

The complexity ceiling for skill distribution is much lower than it appears. The hard part is always the domain knowledge, never the packaging.

---

*This came from evaluating whether to build a full pack system for [SwarmAI](https://github.com/xg-gh-25/SwarmAI). We decided the ROI was negative — the format exists (flat directories), the installer exists (AI agents), and the real value is in context bundling, which no package manager solves.*
