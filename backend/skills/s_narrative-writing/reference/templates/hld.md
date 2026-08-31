# High-Level Design (HLD)

## When to use

An HLD defines a solution at the **system level**, across multiple components or services, how they interact, and the major technology choices. It answers "Are we building the right thing?" and defines interactions at the API level without implementation detail. Use it for:
- Solutions spanning multiple services or components
- New product features needing system-level coordination
- Cross-team projects that must align on system boundaries and interactions

Not for a single component's internals (use `lld.md`) or a single decision (use `adr.md`).

## Guided questions (self-answer first)

Answer each from the material, code, and DDD you have. Mark `[TBD]` only where the answer is genuine user intent you cannot derive. Then list all answers for the user to confirm or revise before writing the document (see INSTRUCTIONS "Guided Authoring Workflow").

**Problem and context**
1. What are you building and why?
2. Who are the customers and what are their needs?
3. What is the current state? (existing systems, pain points, limitations)
4. What are the success criteria, and how will you know the solution works?
5. What constraints exist? (timeline, budget, technology, organizational)
6. What is explicitly **out of scope**?

**Requirements**
7. Functional requirements (frame as user stories where it helps)
8. Non-functional requirements (latency, availability, throughput, cost targets)

**Solution architecture**
9. Proposed approach at a high level (2-3 paragraphs)
10. Systems or services involved (new and existing, with owners)
11. How they interact (synchronous, asynchronous, event-driven)
12. Key API boundaries between systems
13. Data that flows between systems (what, how much, how often)
14. New data stores and their high-level data models

**Key decisions**
15. Major architectural decisions
16. For each: alternatives considered and how they compare against the objectives
17. Which decisions are **one-way doors**? What makes them hard to reverse?
18. What are you explicitly choosing NOT to do?

**Quality attributes**
19. How the availability and latency requirements are met
20. Scaling strategy
21. Security and compliance considerations
22. **Failure modes and graceful degradation**. How does the system behave when a dependency fails?

**Cross-team and rollout**
23. Which teams own the systems involved, and what changes for them?
24. New dependencies introduced
25. Rollout plan (phased, big-bang, feature-flagged)

**Open questions**
26. What is still unresolved, and what is the suggested approach to resolve or isolate each?

## Document structure

```markdown
# High-Level Design: [Solution Name]

**Author:** [name] | **Date:** [date] | **Status:** Draft | In Review | Approved
**Scope:** [Cross-team | Organization+]
**Related:** [Technical Strategy, PRFAQ, or BRD links]

## 1. Overview
### 1.1 Problem Statement
### 1.2 Objectives / Success Criteria
| # | Objective | How Measured |
### 1.3 Requirements (functional + non-functional)
### 1.4 Out of Scope

## 2. Solution Architecture
### 2.1 Approach Summary
### 2.2 Systems and Interactions (diagram + narrative)
### 2.3 API Boundaries
### 2.4 Data Flows and Stores

## 3. Key Decisions
| # | Decision | Alternatives | Rationale | One-way door? |

## 4. Quality Attributes
### 4.1 Availability / Latency / Scaling
### 4.2 Security and Compliance
### 4.3 Failure Modes and Graceful Degradation

## 5. Cross-team Impact and Rollout

## 6. Open Questions
| # | Question | Suggested approach |
```

## Maps to the 5 criteria

Sections 1 (self-contained overview) + 2 satisfy criteria 1-2 (self-contained, problem+why). Section 3 satisfies criterion 3 (decisions+alternatives+rationale). Section 4.3 satisfies criterion 4 (risks+mitigation). Section 6 satisfies criterion 5 (open questions). See INSTRUCTIONS "What Makes a Document Good".
