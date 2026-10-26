# Demo — Tech

## Service

- The Demo service listens on port **7421** (HTTP, localhost only); the value is set in `app/config/server.json`.
- Health check: `GET http://localhost:7421/health`.
- Status note (2026-01-03): deployed and running.

## Stack

- React 19 front end built with Vite; tests with Vitest.
- Staging database: Postgres (version recorded in the decisions log).

## Sync

- Ticket sync is split across three modules: `app/sync/queue.ts` (what to send),
  `app/sync/worker.ts` (sends it) and `app/sync/backoff.ts` (when to retry).
- A failed sync is retried after 30s, then 60s, then 120s, then given up.
