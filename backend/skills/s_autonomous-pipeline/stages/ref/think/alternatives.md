# THINK ref — constraint-driven alternatives

Read when: the constraints don't fit the problem, the choice of constraints is unclear, or you
need the full constraint table. The card (`stages/think.md` step 2) has the selection rule.

## Why constraints, not labels

Generic Minimal/Ideal/Creative labels are replaced by **explicit constraints** that force
genuinely different designs. Each constraint pushes the design somewhere the others don't.

| Constraint | Forces | Good for |
|-----------|--------|----------|
| **SPEED** | Ship in 1 session, cut scope ruthlessly | Urgent features, proven patterns |
| **QUALITY** | Survive 2 years, full test coverage, extensible | Core architecture |
| **SIMPLICITY** | Junior dev can maintain, minimal abstractions | Utility features |
| **FLEXIBILITY** | Support 3 future use cases you can imagine | Platform features |
| **DELETION** | Easiest to remove if wrong, minimal coupling | Experimental features |

**Selection logic:** pick the 3 most relevant constraints based on the evaluation:
- High feasibility score → include SPEED
- High strategic score → include QUALITY
- Low feasibility → include SIMPLICITY
- Uncertain scope → include DELETION

## Per-approach fields

Each approach: **Constraint** (which one), **What** (1-2 sentences), **Effort** (T-shirt +
sessions), **Risk**, **Tradeoff**. End with a recommendation.

Artifact form (`alternatives[]`): `{"constraint": "SIMPLICITY", "what": "...", "effort": "S", ...}`
plus risk/tradeoff. The `enums` block that `stage-start`/`schema --stage think` prints lists only
SPEED/QUALITY/SIMPLICITY for `alternatives[].constraint` and S/M/L for `effort`. That table is
display-only (not validated), so FLEXIBILITY and DELETION are still valid constraint values.

## Fallback

If constraints don't fit the problem (pure research, docs-only), revert to
Minimal/Ideal/Creative. The minimum-depth gate on the card still applies: 2+ distinct
approaches, each with its cost stated.
