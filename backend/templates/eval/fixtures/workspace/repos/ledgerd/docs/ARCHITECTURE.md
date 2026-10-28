# Architecture

`cmd/ledgerd` starts the HTTP server (`internal/api`), which calls `internal/ledger` for
the transfer rules; `internal/store` is the only package that talks to Postgres.

## Money

Amounts are `int64` minor units (cents). Never use `float64` for an amount: rounding
makes the two postings of a transfer stop summing to zero, and the ledger refuses to
commit it.

## Locks

A transfer locks both accounts before reading their balances (ADR 0001). Always lock the
lower account id first. Locking in request order deadlocks two concurrent transfers in
opposite directions between the same two accounts.
