# Runbook

## Deploys

Migrations run before the new binary starts, and the old binary keeps serving traffic
until the rolling deploy finishes. A migration must therefore never drop or rename a
column the old binary still reads: add the new column, deploy, and remove the old one in
a later release.
