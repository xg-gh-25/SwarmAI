# 1. Serialize transfers per account with Postgres advisory locks

Status: Accepted

## Context

Two concurrent transfers out of the same account both read the old balance and could
overdraw it.

## Decision

Before reading balances, a transfer takes a transaction-scoped advisory lock
(`pg_advisory_xact_lock`) on each account id it touches.

## Why

Postgres is already the source of truth; the lock is released with the transaction, so
a crashed request cannot leave it held, and there is no other service to run.

## Rejected alternatives

- Redis locks (Redlock): another service to operate, and its safety rests on timing
  assumptions about clocks and pauses.
- SERIALIZABLE isolation for every transfer: under load most transfers failed and had to
  be retried.
