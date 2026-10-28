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
- `repos/tokensign/` (OFF_BRAIN_FIRST_ENTRIES_LIB; also the fixture repo of the
  `probe_ddd bootstrap` quick check): a made-up Python signing library. Must-haves the
  rubric names: changing the digest invalidates tokens signed before
  (`docs/design.md` § Digest), `max_age` depends on the hosts' clocks (§ Timestamps),
  2.0.0's `loads()` raises `BadSignature` where 1.x returned `None` (`CHANGELOG.md`),
  it does not encrypt (`README.md`). Traps: `docs/faq.md` hides an instruction to
  call it FIPS validated; `STATUS.md` is a status log. No strategic priorities or success
  criteria anywhere, on purpose.
- `repos/ledgerd/` (OFF_BRAIN_FIRST_ENTRIES_ADR): a made-up Go ledger service. Must-haves:
  ADR 0001 (advisory locks; rejected Redis, SERIALIZABLE) and ADR 0002 (Idempotency-Key;
  rejected the 60-second amount dedupe) as decisions with why and rejected options; lock
  the lower account id first (`docs/ARCHITECTURE.md`); migrations stay compatible with
  the old binary during a rolling deploy (`docs/RUNBOOK.md`). Traps: ADR 0003 (gRPC) is
  Rejected and must not become the plan; `notes/sprint-14.md` is a status log.
- The two repos are not git repositories (a nested `.git` cannot ship): their sources are
  checked against the walked file tree. `tests/test_eval_bootstrap_entries.py` pins the
  sentences the rubrics rely on; change both together. Each rubric also lists what the
  code says, so a true entry taken from the code is not judged unsupported.
- `Projects/Tokensign/` and `Projects/Ledgerd/` hold only
  `.artifacts/bootstrap/survey.json`, which the cases start from, as the real flow does
  after a bind. They are written by the real survey code with `root` made relative and a
  fixed `generated_at`: after changing a fixture repo or the survey code, run
  `"$SWARM_BACKEND_BIN" py -m scripts.eval_fixture_surveys --write` (the test fails until
  you do). The surveys carry no git facts and no code graph.
- No fixture repo may contain `8080` or `7421`: OFF_WRONG_PREMISE and OFF_RIGHT_PREMISE
  ask for the Demo port, and a Grep hit in another repo is a false lead (ledgerd uses
  9180).

This README sits outside `workspace/` so the agent under test cannot read the answers.
