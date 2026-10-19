# CLAUDE.md

Short entry point for Claude Code / Cursor / other AI coding assistants working in this repo.

> **SwarmAI is a self-evolving Agent OS**, not a Claude wrapper. Cognition (how it judges —
> principles, rules, gates) is kept separate from knowledge (what it knows). The model
> proposes; a layer of gates + a staged pipeline + a validator dispose. Much of
> `backend/core/` is "guardrails around an LLM" — that *is* the product.

**Read [`AGENTS.md`](./AGENTS.md) before any non-trivial change.** It is the single source of
truth for this repo: architecture, dev commands, the test command form, the push gate
(build + tests + eval), and commit rules. Coding anti-patterns and lifecycle invariants are
in `.kiro/steering/swarmai-dev-rules.md`.

The three rules people get wrong most often (full detail in AGENTS.md):

1. **No push unless build, tests, and `cd backend && python scripts/ci_eval_gate.py` are all
   green.** Commits land on `main`; never push a branch. Edit in your own git worktree, never in
   the shared main checkout (AGENTS.md "Code changes outside a pipeline run").
2. **pytest needs a wall-clock cap:**
   `perl -e 'alarm 90; exec @ARGV' python -m pytest tests/test_<module>.py -v --timeout=60`
   (from `backend/`). Never pipe pytest through `| tail`.
3. **Every commit ends with `Co-Authored-By: Swarm <swarm@swarmai.dev>`**, never a
   Claude/Anthropic identity. Nothing enforces this for a manual commit — write it yourself.
