# PR/FAQ (Working Backwards)

## When to use

A PR/FAQ starts from the customer and works backward to the solution: a mock **press release** written as if the product already launched, followed by an **FAQ** that answers the hard questions before a reviewer asks them. Use it for a new product, feature, or initiative where the first job is to prove the customer value is real, not to specify the build.

Not for an internal technical decision (use `adr.md`) or a system design (use `hld.md`).

This template intentionally omits the one-way-door and failure-mode questions the technical templates carry. A PR/FAQ argues customer value before the build exists, so reversibility and degradation are premature here. They belong in the HLD or ADR that follows.

## Guided questions (self-answer first)

The PR/FAQ question source is the **5 Customer Questions** in the INSTRUCTIONS "Working Backwards" section. Do not restate them here. Answer them from your material, then add the questions below. Mark `[TBD]` only for genuine user intent you cannot derive. Then list all answers for the user to confirm or revise before writing (see "Guided Authoring Workflow").

Beyond the 5 Customer Questions:
1. If this launched today, what is the single headline a customer would care about?
2. What is the customer's quote: why does this matter to them in their words?
3. What is the current workaround, and why is this meaningfully better?
4. What must be true for customers to adopt it? (the key assumption)
5. What is explicitly **out of scope** for the first launch?
6. What are the hardest FAQ questions a skeptical reviewer, customer, or press would ask? (answer them)
7. What is the main risk to the launch, and the **mitigation**?
8. What is still an **open question**, with a suggested approach to resolve it?

## Document structure

```markdown
# PR/FAQ: [Product / Feature Name]

**Author:** [name] | **Date:** [date] | **Status:** Draft | In Review

## Press Release (written as if launched today)
**Headline:** [customer-facing benefit in one line]
**Subhead:** [who it is for and the outcome]
[Opening paragraph: the customer, their problem, and what is now possible.]
[Customer quote.]
[How it works, in plain customer terms (benefits, not features).]
[Call to action / availability.]

## FAQ
### Customer questions
- [hard question] → [honest answer]
### Internal / stakeholder questions
- [scope, cost, dependency, risk] → [answer]
### Out of scope (first launch)
- [what is deliberately deferred]

## Open Questions
| # | Question | Suggested approach |
```

## Maps to the 5 criteria

The press release satisfies criteria 1-2 (self-contained, problem+why from the customer). The FAQ satisfies criterion 3 (decisions and trade-offs surfaced as answers) and criterion 4 (risk + mitigation in the risk FAQ). Open Questions satisfies criterion 5. See INSTRUCTIONS "What Makes a Document Good" and "Working Backwards".
