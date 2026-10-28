---
name: memory-distill
description: >
  Run Cognitive Distillation Flow now and report what it did. TRIGGER: "distill now",
  "process memory now", "沉淀一下", "run the distillation". NOT FOR: saving one specific
  fact (use s_persist).
tier: always
---
# Memory Distill — manual trigger for Cognitive Distillation Flow

MEMORY.md, EVOLUTION.md and the DDD docs are written by one writer: Cognitive
Distillation Flow (CDF). Sessions, runs and explicit saves become candidate units in
its Inbox; the worker in the daemon judges, routes and writes them on its own schedule.
This skill only asks it to run now and tells the user what came out.

## Steps

1. Wake the worker and read the last 24 hours of the Flow:

   ```bash
   "$SWARM_BACKEND_BIN" ingest wake --wait 10
   ```

   The command prints JSON: `status` and `flow`, the 24h Flow overview (`outcomes` counts
   written / reinforced / merged / rejected / deferred / failed / expired units,
   `total_units`, and per-source `extraction_coverage`).
2. Summarise for the user in a few lines: how many units were written or reinforced, how
   many were rejected, deferred or failed, and the total. Point to the Cognitive
   Distillation Flow view for the full audit (destinations and reasons per item).
3. If something the user expected is missing, say which state it is in; do not write
   it yourself.

## Rules

- Never edit MEMORY.md, EVOLUTION.md, a DDD doc or a DailyActivity file directly.
- A specific fact the user wants kept goes through s_persist (`ingest submit`).
- A session that just ended may not be in the Inbox yet: say so instead of retrying.

## Verification

- [ ] `ingest wake` ran and its output was read.
- [ ] The summary names the written, reinforced, rejected, deferred and failed counts.
- [ ] No governed file was written directly.
