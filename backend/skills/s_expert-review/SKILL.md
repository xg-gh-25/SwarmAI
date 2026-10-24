---
name: expert-review
description: "Review your own native code change with the right review experts before you commit it. You decide whether a change needs experts and which ones (correctness, security, red-team, concurrency, state-machine, api-contract, integration, performance, operational, ux-test, ...), run them as sub-agents, verify and fix what they find, and report it.\n  TRIGGER: after you finish a non-trivial native code change and its tests; \"review this change\", \"have experts check it\", \"请专家审一下\".\n  NOT FOR: pipeline runs (the pipeline's own Gate-0 / Gate-2 review them), docs-only or tiny changes, reviewing someone else's PR (use code-review)."
tier: lazy
---

# Expert review (native mode)

See INSTRUCTIONS.md (when to call which expert, how to run them, what to report) and
CATALOG.md (every expert, generated from its prompt file).
