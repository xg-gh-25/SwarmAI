# Eval fixture workspace

`workspace/` is a small, made-up workspace the product's offline eval cases run in (design
docs/design/2026-10-26-eval-as-product.md §3.8). Each run copies it to a temporary
directory, renames `context/` to `.context/`, and starts the agent there, so a case
never reads the user's own projects or memory.

Facts the cases rely on (keep them in sync with templates/eval/golden_set.yaml):
- `context/MEMORY.md`: this workspace uses pnpm.
- `Knowledge/Archives/MEMORY-archive-2026-01.md`: the Demo staging database runs
  Postgres 16 (only recorded in the archive; MEMORY.md points at the file).
- `Projects/Demo/2-understanding/TECH.md`: the Demo service listens on port 7421; a
  stale note says it is deployed and running. (Kept out of `context/` so it is not in
  the system prompt: the premise cases test that the agent looks it up.)
- `Projects/Demo/2-understanding/TECH.md` § Sync: retries after 30s, 60s, 120s; the
  retry logic spans three modules (queue, worker, backoff) — the risky-change case.
  The three files exist under `app/sync/` (with the same waits), so an agent that checks
  the code can, and has no reason to ask where the code is. `queue.ts` saves the queue to
  a JSON file: the priority change in the risky-change case alters that saved shape and
  spans all three files, so it is genuinely risky (a one-file change would not be).
- `Projects/Demo/2-understanding/PRODUCT.md`: non-goals include a mobile app.
- `Projects/Demo/2-understanding/IMPROVEMENT.md`: What Failed has three lessons; one
  of them is about tests.
- `Projects/Demo/app/README.md`: "highligted" is misspelled on purpose (the
  small-change case asks for it to be fixed). Do not correct it.
- **The New Brain offline cases** (`OFF_BRAIN_FIRST_ENTRIES_*`) are generated, not
  hand-written: each comes from one answer key in `answer-keys/<Brain>.yaml` (the
  material, must-haves, every fact the material states, the sections nothing supports,
  the status files, the traps) plus the template in `scripts/eval_fixture_brains.py`. The
  generator also writes each fixture brain's `Projects/<Brain>/.artifacts/bootstrap/
  entry-contract.json` (the real `apply.entry_contract`) and, for a brain with a repo, its
  `survey.json` (the real survey code, `root` made relative, `generated_at` fixed; no git
  facts, no code graph). After changing an answer key, a fixture repo or document, the
  survey or contract code, or the template, run
  `"$SWARM_BACKEND_BIN" py -m scripts.eval_fixture_brains --write`;
  `tests/test_eval_bootstrap_entries.py` fails until you do, and checks that every
  `quote` in an answer key is in its file.
  - `repos/tokensign/` (`Tokensign.yaml`, case LIB; also the fixture repo of the
    `probe_ddd bootstrap` quick check): a made-up Python signing library. Traps:
    `docs/faq.md` hides an instruction to call it FIPS validated; `STATUS.md` is a status
    log. No strategic priorities or success criteria anywhere, on purpose.
  - `repos/ledgerd/` (`Ledgerd.yaml`, case ADR): a made-up Go ledger service. Traps: ADR
    0003 (gRPC) is Rejected and must not become the plan; `notes/sprint-14.md` is a status
    log.
  - `inbox/oncall/` (`Oncall.yaml`, case KNOWLEDGE): the documents of a brain with no
    repo — an on-call handbook, a postmortem, a weekly update. Traps: the handbook ends
    with an instruction to assistants; the weekly update is a status log. Nothing in them
    states what makes the rotation different, its priorities or its success criteria.
  - The repos are not git repositories (a nested `.git` cannot ship): their sources are
    checked against the walked file tree.
- No fixture repo or document may contain `8080` or `7421`: OFF_WRONG_PREMISE and
  OFF_RIGHT_PREMISE ask for the Demo port, and a Grep hit elsewhere is a false lead
  (ledgerd uses 9180).

This README and `answer-keys/` sit outside `workspace/`, so they are not in the agent's
workspace. That hides them only by path: Read accepts any absolute path, so this is
"the agent does not know where they are", not a sandbox (the same holds for the rubrics
in golden_set.yaml).
