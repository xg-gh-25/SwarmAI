# Operational Pattern Checklist (OP1-OP8)

Architectural invariants that every subsystem must satisfy. Unlike RP1-RP52
(code-level bug patterns), these are **system-level patterns** — they apply
regardless of what code you're writing.

REVIEW stage checks these alongside RP1-RP52 for any changeset that adds or
modifies a subsystem's lifecycle operations (CRUD, deploy, update, etc.).

## Patterns

| # | Pattern | Trigger (when to check) | What to verify | Example gap |
|---|---------|------------------------|----------------|-------------|
| OP1 | **Concurrency guard** | Any state-changing API endpoint or background task | Atomic status gate (`UPDATE WHERE status=X` returns rowcount=0 if concurrent) or explicit lock prevents parallel execution | Hive `update()` had no lock — two concurrent update API calls → parallel rsync + restart → corrupted deploy (G6, run_13f9d60a) |
| OP2 | **Rollback path** | Any destructive operation (deploy, update, migrate, config change) | Backup created BEFORE change + restore path on failure. For SSM scripts: `cp -a dir dir.bak` before rsync, `mv .bak back` on exit 1 | Hive update had no rollback — failed mid-rsync left instance in partial state, manual SSM required to fix (G7, run_13f9d60a) |
| OP3 | **Data backup** | Any persistent user data directory (DB, workspace, config) | Automated backup schedule (cron/systemd timer) + retention policy + tested restore path | Hive `/home/swarm/.swarm-ai/` had `DeleteOnTermination=True` on EBS — instance termination = total data loss (G8, run_13f9d60a) |
| OP4 | **Access control on secrets** | Any endpoint returning credentials, tokens, keys, or passwords | Auth guard appropriate to deployment context (`_require_desktop()`, role check, etc.). Never expose secrets on unauthenticated or over-privileged paths | Hive `GET /credentials` returned plaintext password without `_require_desktop()` — a Hive instance could read its own credentials (G2, run_13f9d60a) |
| OP5 | **Health unauthenticated** | Any `/health`, `/status`, or monitoring endpoint | Health endpoint accessible WITHOUT credentials. External monitors (Route53, UptimeRobot) can't supply auth. Backend health returns no secrets — safe to expose | Hive `/health` was behind `basicauth *` — external monitoring impossible, CloudFront health checks failed (G11, run_13f9d60a) |
| OP6 | **Fail-loud placeholders** | Any template, config, or env file with placeholder values | Placeholder format MUST cause runtime failure if not replaced. Use `INVALID_...` or `REPLACE_ME_...`, never valid-looking values | Hive Caddyfile had `$2a$14$PLACEHOLDER_CHANGE_ME` — looked like a valid bcrypt hash, Caddy accepted it, silently rejected all auth (G13, run_13f9d60a) |
| OP7 | **Single canonical path** | Any operation with >1 way to accomplish it (deploy, update, config) | Only ONE canonical path exists. Alternatives are either deleted or deprecated (`exit 1` + warning). Document which path is canonical | Hive had `update-hive.sh` (SSH) AND `provisioner.update()` (SSM) — SSH script had stale IPs and copied repo Caddyfile over deployed Caddyfile (G1, run_13f9d60a) |
| OP8 | **Config consistency** | Any config that exists in >1 location (template + deployed, repo + instance) | All copies in sync OR explicitly excluded from sync (`rsync --exclude`). Document which is source of truth. Drift between copies = guaranteed future incident | `user_data.py` Caddyfile template and `hive/Caddyfile` diverged: one had logging+read_timeout+Referrer-Policy, the other didn't (G4, run_13f9d60a) |

## Output Format

For each applicable pattern, one line:
```
OP1: pass — update() uses atomic status gate (UPDATE WHERE status='running')
OP2: pass — SSM update script creates backend.bak, restores on exit 1
OP3: N/A — no persistent data in this changeset
OP4: pass — credentials endpoint has _require_desktop()
```

## When to Apply

**Scoped trigger:** Only for infra, cloud, and deploy subsystems — NOT every
API endpoint. The test: "does this code manage external resources (EC2, S3,
CloudFront, systemd, Caddy, cron) or credentials?"

- **Check** when changeset adds/modifies lifecycle operations on: Hive, daemon,
  CI/release pipeline, backup/restore, cron jobs, service management
- **Check** when changeset touches config templates or placeholder values
- **Skip** for regular API endpoints (chat, settings, workspace), UI, test-only,
  in-memory state changes

## Maintenance

Same protocol as REVIEW_PATTERNS.md: when a post-pipeline audit finds an
operational gap the checklist missed, add a new OP pattern here. Each OP must
have: trigger, verification, and a real-world example.
