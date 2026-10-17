# REVIEW ref — System checks: depth & seam (8), blast radius (9), OP (10), inverse (11), cross-file (12), neighborhood (13), blocking constraints (17)
Read when: new files (8); infra/release/deploy/CI/cross-service config (9); lifecycle ops
on infra/cloud/deploy subsystems (10); new state transitions (11); a modified file with
siblings (12); modified functions (13); always for 17 (passes silently without the section).

## 8. Depth & Seam Analysis (T3 + P4) — per NEW file
Ousterhout (*A Philosophy of Software Design*) + Feathers' seams. Vocabulary, exactly:
| Term | Definition |
|------|-----------|
| **Module** | Anything with an interface and an implementation (function, class, file, package) |
| **Interface** | Everything a caller must know: types, invariants, error modes, ordering, config — not just the signature |
| **Deep** | Small interface hiding significant implementation; high **leverage** and **locality** |
| **Shallow** | Interface nearly as complex as the implementation; low leverage |
| **Seam** | Where a module's interface lives — behavior alterable without editing in place. Use this, NOT "boundary" |
| **Adapter** | A concrete thing satisfying an interface at a seam |
| **Deletion test** | Delete the module: complexity vanishes → pass-through; reappears across N callers → earning its keep |
**Part A — Depth.** Per new file: (1) the interface surface (public functions, params,
config keys, exceptions, invariants, error modes); (2) what the implementation hides
(state, algorithms, I/O, retry, caching, format translation); (3) does the interface hide
significant complexity? **DEEP** → note, move on. **MODERATE** (leaks ordering, config
keys or error modes) → acceptable. **SHALLOW** → deletion test: vanishes → suggest
inlining/merging; reappears → has value, needs deepening. Modified files: assess only if
the public interface changed.
**Part B — Seam discipline.** Per new interface/abstract class/protocol: 0 adapters →
dead interface, WARN "interface without implementation"; 1 adapter → hypothetical seam
(is a second adapter planned? a test fake counts; if not, suggest the concrete type);
2+ adapters → real seam ✅. Internal seams exposed externally = leaking implementation.
Record `"depth_analysis": {modules_checked, deep, shallow, findings: [{module, verdict,
interface, implementation}]}` and `"seam_audit": {new_interfaces, real_seams,
hypothetical_seams, findings}`. **Informational, not a gate** — shallow modules and
hypothetical seams are warnings.

## 9. Blast Radius — System Lifecycle Trace (RP25)
Only when the changeset touches infra, release, deploy, CI or cross-service config; skip
pure feature code. After the diff review, step OUTSIDE the diff:
1. **List every system-level flow** the change participates in (build→package→deploy→
   update→run; config→startup→runtime→shutdown).
2. **Trace each flow step by step.** At each step: does existing code there **consume**
   what the change produces, compatibly? Does the change alter a **format** (config file,
   tar.gz layout, API shape) a downstream step depends on? If config changed, does the
   **runtime reload** it without a restart (Caddy, systemd, nginx…)?
3. **Check adjacent code** — files in the same directory/module the change DIDN'T touch
   that take part in the same flow.
Output: `Blast radius trace:` then per flow `Flow N: a → b → c` and one `step: ✅|❌ <why>`
line per step (e.g. `SSM update: ❌ doesn't reload Caddy when Caddyfile changes`).
**Fix every ❌ before advancing to TEST** — always real bugs, invisible in the diff.

## 10. Operational Pattern Checklist (P2)
**Trigger:** the change adds/modifies lifecycle operations on **infra, cloud or deploy
subsystems** (Hive, daemon, CI, release, backup, cron jobs). NOT for regular API
endpoints (chat, settings, workspace GET/POST), UI changes, test-only changes or
in-memory state changes. Read `backend/skills/s_autonomous-pipeline/OPERATIONAL_PATTERNS.md`
and apply OP1-OP8 for the affected subsystem. Record under `"operational_patterns"`.

## 11. Inverse Operation Check (P4) — per NEW state transition
| New operation | Required inverse | Example |
|---------------|-----------------|---------|
| create/deploy | cleanup/delete | EC2 launch → terminate + release EIP + delete SG |
| start/enable | stop/disable | EC2 start → stop + disable CloudFront |
| update/modify | rollback/restore | rsync new code → restore .bak on failure |
| write config | validate + rollback | write Caddyfile → caddy validate → mv .bak back |
| acquire resource | release in finally | allocate EIP → release in cleanup |
Verify the inverse exists AND is reachable from the failure path, not just the happy
path. Missing inverse = **P0 finding** (the system can get stuck irrecoverably). Record
under `"inverse_operations"`.

## 12. Cross-File Consistency (P3) — per MODIFIED file
| If you modified… | Also check… | Compare |
|-------------------|---------------|-----------------|
| A template/config | Other templates of the same config | Same features, same structure |
| A deploy script | Other deploy/update scripts | Same mechanism, no duplicates |
| An API endpoint | Adjacent endpoints in the same router | Same guards, same patterns |
| A shell script | Other scripts in the same directory | Same conventions, no stale values |
Find them by listing the file's directory and grepping for the same config/resource
names (e.g. `grep -rl "Caddyfile\|basicauth\|reverse_proxy" . --include="*.py" --include="*.sh"`).
Drift between related files = **WARN**: sync them, or document why they differ. Record
under `"cross_file_consistency"`.

## 13. Neighborhood Review (P5) — per MODIFIED function
Read the 2 functions immediately above and below it in the same file:
1. **Same pattern applied?** If it now has a guard/lock/check, do neighbors doing similar
   work have it too?
2. **Stale references?** Do neighbors use constants, imports or patterns the change
   invalidated?
3. **Copy-paste drift?** If it was copied from a neighbor, are both in sync?
Extends RP25 from "system lifecycle" to "code neighborhood": bugs cluster. Record under
`"neighborhood_review"`.

## 17. Blocking Constraints (TECH.md per-project rules)
1. Read the project's TECH.md; find `## Blocking Constraints`.
2. For each rule with a `Verify:` field, run that mechanical check on the changeset.
3. Violation = **blocking finding** (same severity as an RP violation), citing the
   constraint ID.
Expected format: `- SEC-01: No hardcoded credentials | Verify: grep -rn 'AKIA[A-Z0-9]{16}' in changed files`.
Section with rules → run each check; section empty → pass; section missing → pass
silently (no constraints declared yet).
