# Postmortem: settlements posted twice (2026-03-14)

## What happened

The settlement worker was restarted during a deploy while a batch of 1,200
settlements was in flight. When it came back it read the same batch again and posted
it a second time. Finance reversed the duplicates by hand the next morning.

## Root cause

The worker marks a batch done only after every settlement in it is posted, so a
restart in the middle of a batch leaves it unmarked and it is read again.

## Decisions

- **Drain before every restart.** Deploys and on-call now run `settlectl drain --wait`
  before `settlectl restart`. Status: adopted.
- **Keep a single settlement worker.** Why: two workers would each read batches and
  could post the same batch twice. Rejected: two workers sharing a lock in the lock
  service, because a lock-service outage would then stop all settlement; making every
  settlement idempotent now, because it needs a ledger schema change. Status: adopted.

## Follow-up

The duplicate check is done by hand for now: after any unplanned worker restart, run
`settlectl report --duplicates` and send the output to finance.
