# Architecture Decision Record (ADR)

## When to use

An ADR captures **one significant decision**: its context, the choice made, the alternatives weighed, and the consequences. It is short (one page) and immutable once accepted (supersede it with a new ADR rather than editing). Use it when a decision is worth remembering the reasoning for: a technology choice, a pattern adoption, a boundary, a trade-off with lasting impact.

Not for a whole system (use `hld.md`) or a component's internals (use `lld.md`). If you are describing more than one decision, write multiple ADRs.

## Guided questions (self-answer first)

Answer each from the material and code you have. Mark `[TBD]` only for genuine unknowns. Then list all answers for the user to confirm or revise before writing (see INSTRUCTIONS "Guided Authoring Workflow").

**Context**
1. What decision needs to be made, in one sentence?
2. What forces are at play? (constraints, requirements, non-functional pressures, deadlines)
3. What is the current state that makes this decision necessary now?

**Decision**
4. What is the decision? (state it as a position: "We will ...")
5. What alternatives did you consider, and how does each compare against the forces in Q2?
6. Why does the chosen option win?

**Consequences**
7. What becomes easier or better because of this decision?
8. What becomes harder, or what are you giving up? (state the cost honestly)
9. Is this a **one-way door**? What would it take to reverse it later?
10. What is now explicitly **out of scope** or deferred as a result?

**Failure and risk**
11. What is the main risk this decision introduces, and the **mitigation / graceful fallback**?

**Open questions**
12. What is unresolved, and the suggested approach to resolve or isolate each?

## Document structure

```markdown
# ADR-[NNN]: [Decision Title]

**Author:** [name] | **Date:** [date] | **Status:** Proposed | Accepted | Superseded by ADR-[NNN]

## Context
[The forces, constraints, and current state that make this decision necessary. Self-contained: a reader needs no other doc to judge it.]

## Decision
We will [the position].

## Alternatives Considered
| Option | How it compares against the forces | Verdict |
| [chosen] | ... | selected |
| [alt A] | ... | rejected because ... |

## Consequences
**Better:** [what improves]
**Cost:** [what gets harder / what we give up]
**Reversibility:** [one-way door? cost to reverse]
**Out of scope / deferred:** [...]
**Main risk + mitigation:** [...]

## Open Questions
| # | Question | Suggested approach |
```

## Maps to the 5 criteria

Context satisfies criteria 1-2 (self-contained, problem+why). Alternatives Considered satisfies criterion 3. Consequences (risk + mitigation) satisfies criterion 4. Open Questions satisfies criterion 5. See INSTRUCTIONS "What Makes a Document Good".
