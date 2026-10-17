# PLAN ref — Security Boundaries (only when `cross_boundary.value == true`)

Read when: EVALUATE set `cross_boundary.value == true`. The gate line is on the card.

This is the pipeline's ONLY design-level security checkpoint. Every other security review
(REVIEW security specialist, DELIVER Gate-2 adversarial) is code-level, i.e. after the code
exists. A design-level miss, such as an endpoint whose auth is *assumed* but never enforced,
passes those and surfaces only at runtime. This block catches it at PLAN.

## When

ONLY when EVALUATE set `cross_boundary.value == true`: a new external input surface,
endpoint, auth, credential, tool/delegation, or agent capability (the boundary kinds EVALUATE
already classifies). When `cross_boundary.value == false`, OMIT the section and the
`security_boundaries` field entirely. The completion-time validator reads the evaluation
artifact's `cross_boundary`, BLOCKS a `true` plan that lacks the block, and no-ops on `false`.

## How: assumption-VERIFICATION, not threat-ENUMERATION

Do not brainstorm every possible attack on an unwritten design (that hallucinates). For each
trust boundary the design introduces:

1. Name the boundary.
2. State the trust ASSUMPTION.
3. **Cite the code locus (`path:line`) that ENFORCES it**, after actually reading/grepping
   that locus to confirm it enforces what you claim.
4. If you cannot verify the assumption is code-enforced, set `escalate: true` with a `reason`
   and hand it to the reviewer.

The defense must be a DETERMINISTIC boundary in code (allowlist, scoped credential, verified
principal, egress jail), never a prompt instruction or a client-side toggle.

## Design-doc section

```markdown
## Security Boundaries
- **Boundary:** frontend↔backend API `POST /api/workspace/write`
  - **Trust assumption:** caller is authenticated before reaching the handler
  - **Enforcement:** `backend/middleware/hive_auth.py:62`  ← grep it; if it only fires
    in one mode/config, the assumption is FALSE and this is the design-level catch
- **Boundary:** a newly-registered agent tool that writes files
  - **Trust assumption:** the write path is confined to the workspace tree
  - **Escalate:** true — reason: no code-enforced confinement exists yet, needs review
```

## Artifact field

```json
"security_boundaries": [
  {"boundary": "POST /api/workspace/write", "trust_assumption": "caller authenticated",
   "enforcement": "backend/middleware/hive_auth.py:62"},
  {"boundary": "file-writing agent tool", "trust_assumption": "confined to workspace",
   "escalate": true, "reason": "no code-enforced confinement yet"}
]
```

## Validator contract (`_check_security_boundaries`, completion-time)

- Each entry is VALID iff it has a real enforcement locus (`path:line` whose file EXISTS on
  disk) OR `escalate: true`.
- A cited locus whose file does not exist → BLOCK (fabricated or stale locus).
- A `cross_boundary=true` plan with no (or an empty) `security_boundaries` list → BLOCK.
- The validator verifies the locus is REAL; you and the DELIVER security specialist verify it
  is CORRECT (semantics are beyond a validator).

Edge cases in the validator:
- `escalate` must be the JSON boolean `true`; `"true"` or `1` is validated like a normal
  entry (and BLOCKS without a locus). The validator does not check `reason`; write one anyway.
- A relative `path:line` with no known source root is un-verifiable, so it WARNs instead of
  blocking. That is not permission to guess: grep the locus yourself.
- Evaluation artifact unreadable or without a `cross_boundary` field (e.g. a `direct` run has
  no EVALUATE) → WARN only. If the change opens an exposure surface, add the block anyway.

Related: a change that adds an endpoint/handler should also declare the `security` expert
(card step 8).
