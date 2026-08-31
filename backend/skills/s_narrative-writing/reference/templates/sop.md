# Standard Operating Procedure (SOP)

## When to use

An SOP defines **how a recurring operational task is performed**, step by step, so that any qualified person executes it the same way with the same outcome. It captures the trigger, the ordered steps, the checks, and the recovery path when a step fails. Use it for a repeatable process someone other than the author must run: a deploy runbook, an on-call response, an onboarding checklist, a data-refresh job.

Not for arguing a decision (use `six-pager.md` or `adr.md`) or designing a system (use `hld.md` / `lld.md`). An SOP tells a reader what to DO, not why the approach was chosen.

## Guided questions (self-answer first)

Answer each from the material and code you have. Mark `[TBD]` only for genuine unknowns. Then list all answers for the user to confirm or revise before writing (see INSTRUCTIONS "Guided Authoring Workflow").

**Scope and trigger**
1. What task does this SOP cover, in one sentence?
2. What event triggers it? (a schedule, an alert, a request, a state change)
3. Who runs it, and what access or role must they hold first?
4. What is explicitly **out of scope** for this SOP?

**Preconditions**
5. What must be true before step 1? (access, tools, prior state, approvals)
6. How does the runner confirm each precondition holds?

**The procedure**
7. What are the ordered steps? (one action per step, with the exact command or click)
8. After each step, what observable result confirms it worked?
9. Which steps are irreversible, and what guard runs before them?

**Failure and recovery**
10. For each step with a failure mode, what does that failure look like and what is the recovery or rollback action?
11. When does the runner stop and escalate instead of continuing? (the abort condition + who to contact)

**Verification**
12. How does the runner confirm the whole task succeeded end to end?

**Open questions**
13. What is unresolved, and the suggested approach to resolve or isolate each?

## Document structure

```markdown
# SOP: [Task Name]

**Author:** [name] | **Date:** [date] | **Owner:** [team/role] | **Runs on:** [trigger]

## Purpose and Scope
[What this task achieves and why it matters. What is out of scope.]

## Preconditions
- [Access / role required]
- [Tools / state that must exist first, each with how to confirm it]

## Procedure
| # | Step (exact action) | Confirm result | Irreversible? |
| 1 | ... | ... | no |
| 2 | ... | ... | yes (guard: ...) |

## Failure and Recovery
| Step | Failure looks like | Recovery / rollback | Escalate to |

## Verification
[The end-to-end check that proves the task succeeded.]

## Open Questions
| # | Question | Suggested approach |
```

## Maps to the 5 criteria

Purpose and Scope satisfies criteria 1-2 (self-contained, problem+why). The Procedure and its per-step confirms satisfy criterion 3 (each action is a considered choice with an observable result). Failure and Recovery satisfies criterion 4 (known risks + mitigation). Open Questions satisfies criterion 5. See INSTRUCTIONS "What Makes a Document Good".
