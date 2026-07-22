---
name: repo-to-ddd
description: "The ⑥ Code-Intel Refresher for a DDD — regenerate code-intel.json from the ⑤-bound repo's source via the BUNDLED engine (scripts/refresh_code_intel.py + ai_ready_helpers.py, pure-stdlib + git, no SwarmAI backend). Narrow refresh mode: NEVER touch the 4 DDD docs. Ships the refresher (capability), never the projection (derived data).\n  TRIGGER: \"refresh code intel\", \"regenerate code graph\", \"ddd refresher\".\n  NOT FOR: generating the 4 DDD docs (that's a full ai-ready-repo run / s_ddd-manager)."
tier: lazy
---
# AI-Ready-Repo — the DDD's ⑥ Code-Intel Refresher

Section ⑥ of a DDD GOVERNs the bound repo's **code-intel projection**. This skill is the
self-contained mechanism that **REGENERATES `code-intel.json` from code** — it ships the
refresher (the capability), never the projection (derived, gitignored data).

> **Bundled engine (copied + decoupled, not a stub).** `scripts/ai_ready_helpers.py`
> (the `extract_import_graph` indexer — pure-regex, zero SwarmAI-core dependency) +
> `scripts/refresh_code_intel.py` (the runnable ⑥ CLI) ship INSIDE the DDD, so ⑥ runs
> identically inside Kiro / Claude Code after `aim` export. The only SwarmAI coupling
> (a `~/.swarm-ai` output path) was decoupled → resolves from `$SWARM_WORKSPACE`, else
> repo-adjacent.

## Run it (the ⑥ refresh)

```bash
python scripts/refresh_code_intel.py <bound-repo-path>
# → writes code-intel.json (v2) under $SWARM_WORKSPACE/.artifacts/ai-ready/… (or repo-adjacent)
# exit 0 = written · exit 1 = not a git repo · exit 2 = 0 edges (language-detection miss, loud)
```

Every `depends_on`/`depended_by` edge is a REAL `git ls-files`-scanned import with a
file:line citation — never guessed. Verified: imports + runs with SwarmAI's `core/`
completely off `sys.path`.

## The ⑥ contract (narrow refresh mode)

1. **Activates on BIND.** ⑥ is a no-op for a no-repo DDD (nothing to index). Once ⑤
   `bindings.yaml` binds a repo, this refresher has a target.
2. **Regenerate ONLY the projection.** Produce `code-intel.json` (v2 schema) from the
   bound repo's source. **NEVER touch the 4 DDD docs** — those are OWNed cognition, this
   is a derived projection (the derived-projection rule, §3.6).
3. **Local, never PR-flows-back.** The projection is regenerated at each consumer end
   (gitignored). It is not a `bindings.yaml` member and never travels as a PR.
4. **Refresh policy** comes from ⑤ `bindings.yaml.refresh_policy` (e.g. `on-develop` →
   regenerate when the bound repo changes).

## What it produces vs what it is NOT

| Is | Is NOT |
|----|--------|
| the refresher mechanism (ships in the DDD) | `code-intel.json` (derived — regenerated, gitignored) |
| `code-intel.json` v2 schema output | `code_intel.db` (a local query engine, never shipped) |
| scoped to the bound repo's source | the 4 DDD docs (OWNed, never touched by ⑥) |

## Why portable

The indexer needs only the filesystem + regex. A DDD carries this refresher so, wherever
the package lands, a dev-consumer can regenerate the code graph locally — the projection
is always fresh at the point of use, never a stale shipped artifact.
