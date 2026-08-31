# Low-Level Design (LLD)

## When to use

An LLD defines the **internals of a single component or service**: the classes, functions, data structures, algorithms, and interfaces one team builds and owns. It answers "Are we building the thing right?" Use it when an HLD (or a well-understood system boundary) already fixes what the component must do, and you need to specify how it does it.

Not for multi-service coordination (use `hld.md`) or a single isolated decision (use `adr.md`).

## Guided questions (self-answer first)

Answer each from the code and design you have. Mark `[TBD]` only for genuine unknowns. Then list all answers for the user to confirm or revise before writing (see INSTRUCTIONS "Guided Authoring Workflow").

**Scope and contract**
1. What component is this, and what is its single responsibility?
2. What is the public interface (API, function signatures, events consumed/emitted)?
3. What does the HLD or system boundary require this component to guarantee?
4. What is explicitly **out of scope** for this component?

**Internal design**
5. What are the main internal modules/classes and their responsibilities?
6. What are the key data structures and their invariants?
7. What are the core algorithms or control flows? (sequence for the main path)
8. What state does it hold, and what is the lifecycle of that state?
9. What are the concurrency or ordering constraints?

**Correctness and failure**
10. What are the error conditions, and how is each handled?
11. **Failure modes and graceful degradation**. How does it behave when a dependency is slow or down?
12. What are the idempotency / retry / timeout semantics?

**Decisions**
13. What non-obvious implementation decisions did you make, with alternatives and rationale?
14. Which are **one-way doors** (hard to change once shipped: data format, public API, storage schema)?

**Verification**
15. How is it tested (unit, integration, the invariants each test locks)?

**Open questions**
16. What is unresolved, and the suggested approach to resolve or isolate each?

## Document structure

```markdown
# Low-Level Design: [Component Name]

**Author:** [name] | **Date:** [date] | **Status:** Draft | In Review | Approved
**Related:** [HLD link, owning service]

## 1. Scope and Contract
### 1.1 Responsibility (one sentence)
### 1.2 Public Interface
### 1.3 Guarantees required by the HLD
### 1.4 Out of Scope

## 2. Internal Design
### 2.1 Modules / Classes
### 2.2 Data Structures and Invariants
### 2.3 Core Control Flows (main path sequence)
### 2.4 State and Lifecycle
### 2.5 Concurrency / Ordering

## 3. Correctness and Failure
### 3.1 Error Handling
### 3.2 Failure Modes and Graceful Degradation
### 3.3 Idempotency / Retry / Timeout

## 4. Implementation Decisions
| # | Decision | Alternatives | Rationale | One-way door? |

## 5. Test Strategy

## 6. Open Questions
| # | Question | Suggested approach |
```

## Maps to the 5 criteria

Section 1 satisfies criteria 1-2 (self-contained, problem+why in context of the HLD). Section 4 satisfies criterion 3. Section 3.2 satisfies criterion 4. Section 6 satisfies criterion 5. See INSTRUCTIONS "What Makes a Document Good".
