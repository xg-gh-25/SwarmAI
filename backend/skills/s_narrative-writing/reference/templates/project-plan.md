# Project Plan

## When to use

A project plan defines **how a bounded effort will be delivered**: its scope, the ordered milestones, the risks that threaten it, and the dependencies it relies on. It aligns a team on what "done" is, who owns each part, and when each part lands. Use it to launch a project, request resourcing, or give stakeholders a shared view of the path and its risks.

Not for arguing whether to do the project (use `six-pager.md`, since the plan assumes the decision is already made) and not for a repeatable operational task (use `sop.md`). A plan projects a path forward. It does not run a recurring procedure.

## Guided questions (self-answer first)

Answer each from the material and code you have. Mark `[TBD]` only for genuine unknowns. Then list all answers for the user to confirm or revise before writing (see INSTRUCTIONS "Guided Authoring Workflow").

**Scope and goal**
1. What is the goal of this project, in one sentence?
2. What is in scope, and what is explicitly **out of scope**?
3. What does success look like, and how is it measured?

**Milestones**
4. What are the ordered milestones from now to done? (each a deliverable, not an activity)
5. For each milestone: the owner, the target date, and the observable proof it is complete.
6. Which milestone is on the critical path? (if it slips, the whole plan slips)

**Dependencies**
7. What does the project depend on that the team does not control? (another team, an approval, an external launch)
8. For each dependency: who owns it, when it is needed, and the fallback if it is late.

**Risks**
9. What are the top risks that threaten the plan, ranked by impact?
10. For each risk: its potential impact and the **mitigation or contingency**.

**Resourcing**
11. What people, budget, and tools does the plan require, and are they committed?

**Open questions**
12. What is unresolved, and the suggested approach to resolve or isolate each?

## Document structure

```markdown
# Project Plan: [Project Name]

**Author:** [name] | **Date:** [date] | **Sponsor:** [name] | **Target:** [end date]

## Goal and Scope
[The goal in one line. In scope vs out of scope. How success is measured.]

## Milestones
| # | Milestone (deliverable) | Owner | Target date | Proof of done | On critical path? |
| 1 | ... | ... | ... | ... | yes |

## Dependencies
| Dependency | Owner (external) | Needed by | Fallback if late |

## Risks
| Risk | Impact | Likelihood | Mitigation / contingency |

## Resourcing
[People, budget, tools required, and whether each is committed or still to secure.]

## Open Questions
| # | Question | Suggested approach |
```

## Maps to the 5 criteria

Goal and Scope satisfies criteria 1-2 (self-contained, problem+why). Milestones and Dependencies satisfy criterion 3 (the path is a considered sequence with owners and proofs). Risks satisfies criterion 4 (known risks + mitigation). Open Questions satisfies criterion 5. See INSTRUCTIONS "What Makes a Document Good".
