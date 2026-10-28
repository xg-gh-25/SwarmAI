# 2. Require an Idempotency-Key on POST /transfers

Status: Accepted

## Context

Clients retry a request that timed out. Each retry created a second transfer.

## Decision

`POST /transfers` requires an `Idempotency-Key` header. The server stores the key with
the response for 24 hours and returns the stored response for a repeated key.

## Rejected alternatives

- Treat the same (from, to, amount) within 60 seconds as a duplicate: two genuine
  identical transfers, such as two equal rent payments, were silently dropped.
