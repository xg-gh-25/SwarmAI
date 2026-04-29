# PLAN Stage

Pipeline-owned stage (no sibling skill).

## Methodology

1. Take the recommended (or user-chosen) alternative
2. Produce a design document covering ALL of these sections:
   - Architecture/approach description
   - Data model or API contract (if applicable)
   - Acceptance criteria (carry forward from evaluate + refine)
   - Edge cases and error handling
   - Estimated files to change
   - **Boundaries** (required — see below)
   - **Success criteria** (required — see below)
3. If design requires uncommitted dependencies or API changes -- taste/judgment decision

### Boundaries (Required)

Every design document MUST include a three-tier boundary system. This prevents
the most expensive class of bugs: building the wrong thing because the agent
made an assumption the user didn't intend.

```markdown
## Boundaries

### Always (non-negotiable — agent auto-enforces)
- [Things that must happen regardless, e.g., "every declared state must have a code path"]
- [Quality gates that are never optional, e.g., "run tests before committing"]

### Ask First (agent pauses and confirms with user)
- [Things that need human judgment, e.g., "adding new dependencies"]
- [Scope-expanding decisions, e.g., "changing the public API"]

### Never (hard constraints — agent refuses)
- [Things that must not happen, e.g., "don't mock resource management code"]
- [Anti-patterns for this specific feature, e.g., "don't use setTimeout for state"]
```

**Populate from:**
- IMPROVEMENT.md "What Failed" section → past failures become "Never" items
- TECH.md conventions → existing patterns become "Always" items
- PRODUCT.md non-goals → off-scope becomes "Never" items
- Pre-mortem risks from EVALUATE → risk mitigations become "Always" items

### Success Criteria (Required)

Reframe vague requirements into specific, testable conditions. These become
the exit conditions for the DELIVER stage.

```markdown
## Success Criteria
- [Criterion 1 — specific, measurable, testable]
- [Criterion 2]
- [Criterion 3]
```

**Format rule:** Each criterion must be verifiable by a test, a command output,
or a visual check. "Works correctly" is not a success criterion. "Returns 200
with valid JSON body containing `transcript` field" is.

## Artifact Publish

The design_doc artifact MUST include `boundaries` and `success_criteria` fields.
Pipeline validator will check for their presence.

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type design_doc --producer s_autonomous-pipeline \
  --summary "Design: <approach> for <requirement>" \
  --data '{"approach":"...","acceptance_criteria":[...],"boundaries":{"always":[...],"ask_first":[...],"never":[...]},"success_criteria":[...],"data_model":"...","api_contract":"...","files_to_change":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state build
```
