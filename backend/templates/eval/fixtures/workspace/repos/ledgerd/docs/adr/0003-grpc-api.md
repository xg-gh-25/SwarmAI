# 3. Replace the HTTP API with gRPC

Status: Rejected

## Proposal

Serve the API over gRPC for typed clients and streaming.

## Outcome

Rejected: the partner banks that call ledgerd can only make HTTPS requests with JSON
bodies. The API stays HTTP+JSON.
