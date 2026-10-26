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

This README sits outside `workspace/` so the agent under test cannot read the answers.
