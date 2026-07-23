---
title: "DDD → Dual-Target Distribution: Capabilities Package + Open-Plugin"
created: 2026-07-20
updated: 2026-07-23
status: design (public)
tags: [ddd, distribution, open-plugins-standard, capabilities-package, auto-install, kiro, claude-code, cursor]
---

# DDD → Dual-Target Distribution

> **What this design is.** A mature DDD is not just documents — it is a
> self-contained, mountable, **distributable** domain-capability package (see
> `DDD-Platform-Overview.md`). This doc specifies the last step of the DDD
> lifecycle — **cultivate → version → DISTRIBUTE** — i.e. how one grown DDD is
> rendered into a package another AI agent (Kiro, Claude Code, Cursor, …) can
> install and use.
>
> **Design SSOT.** This is the public design; the source of truth referenced by
> `backend/core/ddd_packager.py` and `backend/core/ddd_distribution_policy.py`.

---

## 0. The core reframe

Skill portability is a **distribution problem, not a packaging problem**
(see `docs/discussions/61-skill-portability-is-a-distribution-problem-not-a-packagin.md`).
The valuable part of a DDD is not its code — it's the sedimented domain knowledge
+ the skills that apply it. So a DDD distributes as a **well-structured directory
any AI agent can understand and install**, in one of two renderings that share the
same install primitive and differ only in *where the package is stored* and *who
can reach it*:

- **`aim-capabilities`** — a **Capabilities Package** for an internal package
  store: a `Config` carrying an `AIMBuild` build-tool + a `type=ai-capabilities`
  target (preserving any existing build-system), plus `agents/<ddd>.agent-spec.json`,
  `skills/`, `context/`, `agent-sops/`.
- **`open-plugin`** — an **Open-Plugins Standard** plugin for the lightweight /
  public path: `.plugin/plugin.json` + `skills/` + `agents/*.md` + `rules/` +
  `.mcp.json` + `hooks/`.

Both install via the same package-install primitive; a Capabilities Package and an
Open-Plugin are two renderings of the same conformant package, not two systems.

For a **bare host** (a generic Claude Code / Cursor with no package-store CLI), the
fallback is the git-clone + install-script path (§3.4) — a best-effort tail, NOT a
correctness gate.

---

## 0.1 Definition of Done

1. **Declaration-driven emit** — `s_ddd-distribute <DDD>` reads the DDD's own
   `aim.json.distribution` block (§0.2) and emits ONLY the declared `targets`,
   respecting `visibility`. `targets:[]` emits nothing (valid). No target is ever
   chosen by the packager.
2. **Valid trees per target** — a valid Capabilities Package tree (`Config` with
   an `AIMBuild` build-tool + `type=ai-capabilities`, alongside any existing
   build-system; + `agents/*.agent-spec.json` + `skills/`) and/or a valid
   Open-Plugin (`.plugin/plugin.json` + `skills/` + `rules/`). Class-B skills
   included; class-A excluded-or-optional (§4).
3. **Installs cleanly** — `<install> --local <emitted>` succeeds and lists the
   package with correct skill / agent / mcp counts.
4. **Isolated-host execution** — the portable engine runs on a host with **no
   SwarmAI backend, no DB** (stdlib + git only). Proven by execution, not by grep
   (verified 2026-07-23 for `s_repo-to-ddd`: `env -i`, PYTHONPATH stripped, system
   `python3` → valid `code-intel.json`).
5. **Publish gate fails CLOSED** — an absent / malformed / typo'd `distribution`
   block (e.g. `visibility:"externl"`) aborts publish non-zero and treats it as
   `targets:[], visibility:internal`. A coded assertion in the publish path, not
   manual discipline.
6. **No secret / internal-string leak on external publish** — a content-scan gate
   (secrets + internal-hostname denylist + internal-MCP detector) runs over
   `skills/**/scripts/**`, `.mcp.json`, `agents/*`, `context/*` before ANY external
   push; a planted secret or an internal endpoint each aborts the publish.

---

## 0.2 Target selection is PER-DDD — the DDD declares, the packager obeys

The scope of what can leave SwarmAI is a property **the DDD declares in its own
`aim.json`**, and the packager only executes it.

### The declaration (`distribution` block in `aim.json`)

```jsonc
"distribution": {
  "targets": ["aim-capabilities", "open-plugin"],  // 0..2 of: aim-capabilities | open-plugin
  "visibility": "external"                          // internal | external
}
```

### The four combinations (reach × target)

| `targets` | `visibility` | Reach | Example DDD |
|-----------|-------------|-------|-------------|
| `["aim-capabilities"]` | internal | Internal package store only | a DDD over private business data |
| `["open-plugin"]` | external | Public plugin registry / public install | a generic reusable skill-set |
| `["aim-capabilities","open-plugin"]` | internal→external | both, external publish separately human-approved | **ai_ready_repo** (public standard, also internally useful) |
| `[]` | — | **not distributed** — cultivated + used on SwarmAI only | a private / early DDD |

### Hard invariants (the gate, not discipline)

1. **`visibility:internal` FORBIDS the public publish step**, even if `open-plugin`
   is in `targets` — a DDD can EMIT an open-plugin tree for a *private* install
   without ever publishing it publicly. **Emit ≠ publish.**
2. **`internal`→`external` visibility change is ALWAYS human-gated** — routed
   through the same irreversible-external-op approval as a repo visibility flip
   (the C041 gate). The packager NEVER flips visibility on inference.
3. **`targets:[]` is valid and complete** — a non-distributed DDD is not a degraded
   DDD (mirrors the 0-asset pure-knowledge brain: structurally whole).
4. **Default for a new DDD = `targets:[]`, `visibility:internal`** — distribution is
   opt-IN, never the default. Nothing leaves SwarmAI until the owner declares it.
5. **Declaration is the CEILING; the human at package-time may only SUBSET it.**
   The `distribution` block is authoritative. At package time the human may choose a
   SUBSET of declared `targets` ("just internal today") and may NEVER add an
   undeclared target nor escalate `visibility`. To distribute more widely, the owner
   first EDITS the declaration — a separate, C041-gated change — not an approval at
   package time. Malformed/absent block → ceiling is `[]` → nothing publishes.

---

## 1. End-to-end lifecycle

The full flow, cradle to foreign-host-use:

```
cultivate + version (on SwarmAI)
   │
   ▼
s_ddd-distribute <DDD>
   • reads aim.json.distribution (§0.2 — the owner-declared CEILING)
   • emits ONLY declared targets, at-or-below declared visibility
   • ENABLES the install path (package manifest + a simple install script)
   • RETURNS the built package link(s)
   │
   ├── aim-capabilities: Config{AIMBuild} + agent-spec + skills → internal store
   └── open-plugin:      .plugin/plugin.json + skills + rules   → public registry / --local
   │
   ▼
foreign host installs  (Kiro / Claude Code / Cursor / …)
   • package-store CLI install  (primary)   — reads manifest, auto-places per agent
   • git-clone + INSTALL.md      (fallback)  — bare hosts, best-effort tail
   │
   ▼
the DDD's domain skills run on the foreign host
```

**Two complementary install mechanisms, not one:** a package-store CLI (auto-places
the package per target agent) for hosts that have it, and a simple clone +
`INSTALL.md` script for bare hosts. The DoD is proven on the CLI path; the bare-host
path is a best-effort tail.

---

## 2. Target A — Capabilities Package (internal package-store path)

A `Config` declaring an `AIMBuild` build-tool + a `type=ai-capabilities` target
(preserving any pre-existing build-system in the DDD), plus:

- `agents/<ddd>.agent-spec.json` — the agent composition (base agent + this DDD's
  skill globs)
- `skills/` — the included skills (class split per §4)
- `context/`, `agent-sops/` — the sedimented knowledge + operating procedures

Published to the internal package store → browsable + installable by internal agents.

---

## 3. Target B — Open-Plugin (lightweight / public path)

An **Open-Plugins Standard** plugin:

- `.plugin/plugin.json` — the manifest (skills, agents, mcp, hooks it contributes)
- `skills/` + `agents/*.md` + `rules/` + `.mcp.json` + `hooks/`

Published to a public plugin registry, or installed `--local` for a private install
without publishing.

### 3.4 Bare-host fallback (Cursor / generic Claude Code, no package CLI)

The git-clone + `INSTALL.md` path: a well-structured directory any AI agent can read
and self-install ("read INSTALL.md and do what it says"). Best-effort — NOT a DoD
gate.

---

## 4. Class-A / class-B skill split at distribution

The two-skill-class distinction (see `DDD-Platform-Overview.md` and AGENTS.md R31)
becomes a packaging decision, enforced by the packager reading skill provenance from
the real `aim.json` — **not by discipline**:

| Class | Source (`aim.json`) | In the emitted package |
|-------|---------------------|------------------------|
| **B. Domain** (e.g. `s_cmhk-*`) | `plugins.domain_skills` | **ALWAYS INCLUDE** — they ARE the DDD's capabilities. |
| **A. Enablement** (e.g. `s_ddd-*`, `s_repo-to-ddd`) | `plugins.native_skills` | **DEFAULT EXCLUDE.** A host with its own equivalents would double-load. Emit into an **optional variant** (`<ddd>-with-enablement`) for **bare hosts that lack them**. |

Default emit glob = `domain_skills` only. The `--with-enablement` variant ships the
enablement engine as a **portable copy** for bare hosts (built + isolated-host
verified 2026-07-23; see the `s_repo-to-ddd` case).

**Why a portable copy, not a shared engine:** the enablement engine (e.g.
`s_repo-to-ddd`) is written **stdlib-only** precisely so it can travel to a host with
no SwarmAI backend. This is deliberately NOT the same as SwarmAI's resident,
DB-backed code-intel engine — the two are separate implementations by design (see
`Projects/SwarmAI/TECH.md` § Code Intelligence). Nothing DB-coupled ever crosses the
host boundary.

---

## 4a. Content-level safety at external publish

A fail-closed content-scan gate runs over the emitted tree BEFORE any external push:

- **secrets** — token/key/password patterns in `skills/**/scripts/**`, `.mcp.json`
- **internal-hostname denylist** — internal domains / infra names must not ship in a
  public package (a planted internal endpoint ABORTS the publish)
- **host-path leak** — no absolute builder-machine paths (`$HOME/.swarm-ai/...`);
  portable skills resolve from `${SWARM_WORKSPACE:-$PWD}`, never a hardcoded root
- **internal-MCP detector** — MCP servers scoped to internal auth are stripped/flagged

Emit-only blocks on secrets + host-paths; **external publish** additionally blocks on
internal-strings. This is the gate §0.2's declaration cannot cover — declaration
governs *reach*, this governs *content*.

---

## 5. Honest gaps

- **Jobs as a governed asset kind** — a DDD's scheduled jobs (asset kind `job`)
  should travel with the package so a foreign host gets the "auto-run" too; the
  scheduler-portability matrix across foreign hosts is only partially proven.
- **Mount-ceiling levels** — L1/L2/L3 mount depth (skills only / + tools / + jobs)
  interacts with §4's class split; fully specified for skills, partial for jobs+tools.
- **Bare-host CLI coverage** — the package-store CLI path is the proven one; the
  bare-host clone+script tail is best-effort and under-tested across agents.

---

## 6. Relationship to the rest of the platform

- **`DDD-Platform-Overview.md`** — the platform context: DDD as a portable capability
  package is one property of the knowledge layer.
- **`AI-Ready-Repo-Engine-Design.md`** — the `s_repo-to-ddd` engine that generates a
  DDD *from* a code repo; it is the flagship enablement skill this distribution path
  ships to bare hosts.
- **AGENTS.md R31** — the DDD paradigm (universal brain + 0..N governed assets) + the
  two-skill-class governance this distribution split enforces.
