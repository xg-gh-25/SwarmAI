# TEST ref — Layer 4: cross-boundary E2E

Read only when the EVALUATE artifact has `cross_boundary.value == true`.

`RUN` = `perl -e 'alarm 590; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`

## Layer 4: Cross-Boundary E2E (CONDITIONAL — fires ONLY when `cross_boundary.value == true`) `[GATE·artifact_cli]` <!-- R-test-002 -->

> When EVALUATE set `cross_boundary=true`, this layer is code-enforced at DELIVER:
> `artifact_cli.py`'s `cross_boundary_e2e` gate `sys.exit(1)`s if the TEST artifact
> lacks a passing `cross_boundary_e2e.run` record. When `false`, the layer is skipped
> (no gate).

Read the EVALUATE artifact's `cross_boundary` flag (see EVALUATE § Cross-Boundary
Classification; `stage-start` returns the upstream EVALUATE summary).
- **`false`** → skip: record
  `"cross_boundary_e2e": {"run": false, "reason": "not a cross-boundary change"}` and
  move on (no ceremony tax).
- **`true`** → MANDATORY DoD item. The run is not TEST-complete without it.

Layers 1-3 are unit-shaped: each exercises one side of a seam (the AC's own test,
importers, a module import). None drives the SEAM. Layer 4 drives the **real system
through the actual boundary** the change crosses.

### Two hard requirements (both, or it is theater)

1. **Drive the REAL wiring, not a mock of it.** Exercise the actual contract end to
   end: fire the real event through the real listener/registry; send the real payload
   through the real serializer; run the real reader against the real writer's output.
   **The test MUST NOT `patch`/mock the thing-under-change.** Mocking the event bus,
   the registry or the serializer you just edited is the CLASS-A test theater this
   layer exists to prevent. Mock only the far leaf boundary (a network call, the LLM),
   never the seam itself.
2. **Mutation-verify it is non-vacuous.** Revert the one contract line the change
   added (the dispatch→open mapping, the migrated field, the allowlist entry): the
   Layer-4 test MUST go RED. A green-on-revert Layer-4 test proves nothing. State the
   mutation you ran and that it went RED in the artifact.

### Per boundary kind, "drive the real system" means

- *event-bus / ACT-SENSE* → mount the real provider + host + registry, `dispatchEvent`
  the real `swarm:*` event, assert the surface opens (and that its state is READ
  back). Canonical example: `overlayHostE2E.test.tsx` (7 surfaces × real registry ×
  real event; mutation = revert open-on-show → all RED).
- *frontend↔backend contract* → assert the frontend table is DERIVED from (or bound by
  a test to) the backend SSOT, so a divergence is impossible or RED (e.g.
  `test_backend_allowlist_is_bound_to_leftnav_ssot`).
- *data/schema migration* → run the real reader against a real writer's output shape
  (real production data shape, not a fixture that encodes your assumption).
- *multi-subsystem shared path* → smoke EACH subsystem independently through the
  shared path before combining (R16: send 1 msg → stream → content persists on tab
  switch).

### Running it through the runner

- Backend Layer-4 test: put it in BUILD's `ac_coverage` so `RUN --mode regression` runs
  it with L1 (`ac_coverage` refs resolve `.py` files only). Frontend Layer-4 test: the
  regression call's `vitest related` picks it up when it imports a changed file.
  Written during TEST: `RUN --mode tdd --files <test_file>` (max 5; backend or
  `desktop/`) until green, then the final `RUN --mode regression`.
- Backend mutation-verify, ONE call (replaces reverting the line by hand and
  re-running): write `/tmp/<R>/mutations.json`

  ```json
  {"tests": ["backend/tests/test_x.py::test_seam_end_to_end"],
   "mutations": [{"id": "M1", "file": "backend/core/x.py",
                  "find": "<the contract line>", "replace": "<the reverted line>"}]}
  ```

  then `RUN --mode mutation --mutations /tmp/<R>/mutations.json`. It applies each
  mutation in a throwaway worktree (HEAD + this session's changed files), never touches
  the shared tree, runs every mutation in the one call with `--maxfail=0`, and reports
  per mutation `killed` + `red_tests`. Every Layer-4 mutation must be `killed: true`;
  a survivor means the test is vacuous.
- Frontend mutation-verify (mutation mode runs pytest tests only): via
  `RUN --mode probe --name l4-mutation-<id> -- <wrapper>`, where `<wrapper>` reverts the
  contract line, runs the vitest file, restores the line in a `finally`, and prints
  `{"findings": []}` when the test went RED or `{"findings": [{"id": "survived-<id>"}]}`
  when it stayed green. Any finding = vacuous test.

### Record in the TEST artifact

```json
"cross_boundary_e2e": {"run": true, "test_file": "...",
  "drives_real": "what real wiring it exercises",
  "mutation": "reverted X → test RED (mutation M1 killed)"}
```
