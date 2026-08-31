# Six-Pager (Narrative)

## When to use

A six-pager is the Amazon narrative for a **decision, proposal, or strategy** that a group reads silently and then discusses. It is prose (not bullets), at most six pages of main body, with supporting detail pushed to appendices. Use it when you need a reader to understand a situation, follow an argument, and make or endorse a decision.

Not for a customer-facing launch (use `prfaq.md`) or a purely technical design (use `hld.md`/`lld.md`).

This template covers risk and reversibility (one-way doors) but intentionally omits the failure-mode/graceful-degradation question the technical templates carry. A six-pager argues a decision, not a system's runtime behavior. If the decision has system-level failure modes worth documenting, write the accompanying HLD.

## Guided questions (self-answer first)

Answer each from your material, data, and context. Mark `[TBD]` only for genuine user intent you cannot derive. Then list all answers for the user to confirm or revise before writing (see INSTRUCTIONS "Guided Authoring Workflow"). This uses the SCQA spine (Situation, Complication, Question, Answer).

**Situation and complication**
1. What is the situation the reader already agrees is true? (shared starting ground)
2. What changed or what tension makes action necessary now? (the complication)
3. What is the one question this document answers?

**The argument**
4. What is your recommendation, in one sentence?
5. What is the evidence: the specific metrics and facts that support it?
6. What major alternatives did you weigh, and why does the recommendation win?
7. What are you explicitly **not** proposing? (out of scope)

**Consequences and risk**
8. What does success look like, and how is it measured?
9. What is the main risk, its potential impact, and the **mitigation**?
10. Is any part a **one-way door**? What makes it hard to reverse?

**Decision and next steps**
11. What decisions do you need from the reader, and who owns each?
12. What are the concrete next steps with owners and dates?

**Open questions**
13. What is unresolved, and the suggested approach to resolve or isolate each?

## Document structure

```markdown
# [Title]: [Decision or Proposal]

**Type:** Six-Pager | **Date:** [date] | **Author:** [name]

## Executive Summary
[Purpose in one sentence. The recommendation with 2-3 supporting points. The decisions needed, each with an owner. Impact: cost, timeline, resources.]

## 1. Situation
[The shared context the reader already accepts.]

## 2. Complication
[What changed / the tension that makes action necessary now.]

## 3. Recommendation
[The answer, stated plainly, then the argument with evidence.]

## 4. Alternatives Considered
| Option | Trade-off | Verdict |

## 5. Success Criteria and Risks
[How success is measured. Main risk + impact + mitigation. One-way doors.]

## 6. Decision and Next Steps
[Decisions needed with owners. Next steps with owners and dates.]

## Open Questions
| # | Question | Suggested approach |

## Appendix
[Supporting detail that would break the main-body flow.]
```

## Maps to the 5 criteria

Sections 1-3 satisfy criteria 1-2 (self-contained, problem+why). Section 4 satisfies criterion 3 (alternatives+rationale). Section 5 satisfies criterion 4 (risk+mitigation). Open Questions satisfies criterion 5. See INSTRUCTIONS "What Makes a Document Good", "Document Structure", and "Working Backwards".
