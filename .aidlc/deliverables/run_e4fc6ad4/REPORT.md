# Autonomous Pipeline Report: AppConfigManager: preserve, never destroy, unreadable config.json / secrets.json

**Run ID:** run_e4fc6ad4 | **Project:** SwarmAI | **Profile:** bugfix
**Date:** 2026-10-17 08:57 UTC | **Duration:** 36.3 min (in progress) | **Tokens:** 210,000
**Gate 1:** WARN | **Files:** 4 | **Tests:** True/1

## TL;DR
Stop AppConfigManager destroying user config/secrets on suspected corruption (STEERING #20): invalid/empty config.json is currently overwritten with DEFAULT_CONFIG and a corrupt secrets.json is overwr

## 1. Requirement
Stop AppConfigManager destroying user config/secrets on suspected corruption (STEERING #20): invalid/empty config.json is currently overwritten with DEFAULT_CONFIG and a corrupt secrets.json is overwritten with only the newly-set key. Isolate the suspect file (data_safety.isolate_store rename .corrupt-<ts>) + write a recovery marker surfaced via attention_authority before any default/fresh write; make config.json writes atomic (tmp+os.replace) so a crash cannot truncate it; generalize the recovery attention item so it is correct for non-DB stores.

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> Stop AppConfigManager destroying user config/secrets on suspected corruption (STEERING #20): invalid/empty config.json is currently overwritten with DEFAULT_CONFIG and a corrupt secrets.json is overwritten with only the newly-set key. Isolate the suspect file (data_safety.isolate_store rename .corrupt-<ts>) + write a recovery marker surfaced via attention_authority before any default/fresh write; make config.json writes atomic (tmp+os.replace) so a crash cannot truncate it; generalize the recovery attention item so it is correct for non-DB stores.

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | bug | user-stated: XG 2026-10-17: 修第3条 (STEERING #20) | confirmed | ● | Given config.json containing '{bad json', when AppConfigManager.load(), then a config.json.corrupt-* file exists with the original bytes and config.json holds defaults |
| FR-2 | bug | ddd-derived: STEERING #20 surface-to-user; existing data_safety recovery-marker design | confirmed | ● | Given an isolated config.json, when attention sources are collected, then a BLOCKING item names the isolated path with config-specific recovery steps |
| FR-3 | bug | ddd-derived: root cause of the empty-file corruption class; secrets path already atomic | confirmed | ● | Given _write_to_disk, when it writes, then it writes a temp file and os.replace()s it |

### 澄清问答 (Clarifications — who decided what)
| Q | 问题 (question) | 回答 (answer) | 答者 (answered_by) |
|---|------------------|---------------|---------------------|
| Q1 | After isolating a corrupt config.json, write defaults to disk or stay in-memory only? | A. Write defaults (same as missing-file path; original preserved as .corrupt) | 🧠 self |
| Q2 | Fix secrets.json in this run too? | A. Yes, same class same file | 🧠 self |

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
- [SELF-ANSWERED] Q1: A. Write defaults (same as missing-file path; original preserved as .corrupt) (🧠 AI 替你拍的板 — resolution: Follows the approved data.db option-B precedent (main.py isolate + reseed fresh + marker); once renamed away nothing is destroyed, and staying file-less would make every other reader (jobs/model_resolve) re-create defaults anyway.)
- [SELF-ANSWERED] Q2: A. Yes, same class same file (🧠 AI 替你拍的板 — resolution: SOUL P4: guard the CLASS (irreplaceable data destroyed without approval/backup), not one path; same module.)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 8.00 | |
| Priority | 9.00 | |
| Feasibility | 9.00 | |

**Acceptance Criteria:**
1. AC1: load() on an empty / invalid-JSON / non-dict config.json RENAMES it to config.json.corrupt-<ts> (bytes preserved) before writing defaults; a recovery marker is written naming the isolated path.
2. AC2: if the isolate fails (IsolationError), load() does NOT overwrite the original file — it runs on in-memory defaults and logs ERROR.
3. AC3: set_secret on a corrupt (invalid JSON / non-dict) secrets.json isolates it (rename) + writes a recovery marker before writing the new store; on an OSError reading secrets.json it raises and writes nothing.
4. AC4: _write_to_disk is atomic (temp file + fsync + os.replace) with 0o600 perms preserved.
5. AC5: the attention item for a non-DB isolated store gives correct, store-specific recovery instructions (not data.db/daemon-pool text); DB items unchanged.
6. AC6: FileNotFoundError path still creates defaults with no isolate/marker (nothing to preserve).


## 3. Design & Approach
**Research Findings (THINK):**
- data_safety.isolate_store (data_safety.py:313) renames target + -wal/-shm sidecars only if they exist, raises IsolationError when it cannot preserve — directly reusable for a JSON file.
- write_recovery_marker accrues a pending list in <app_dir>/.db-recovery-pending.json; attention_authority._collect_db_recovery surfaces each live entry as a BLOCKING item, but its title/message are hard-coded for data.db (daemon connection pool, -wal/-shm).
- AppConfigManager(config_path=tmp) is used by tests; get_app_data_dir() is the real ~/.swarm-ai — the marker dir must follow the config location for a custom path, or tests would write markers into the live app dir.
- core.data_safety imports core.permission_manager (5ms); core package already imported by app_config_manager — lazy import inside the failure branch keeps the hot path untouched.
- set_secret already writes atomically (tmp + fsync + os.replace + 0o600); config.json does not — same pattern can be shared.
- Live config.json is a regular file (not a symlink); secrets.json absent on this install.

**Alternatives Evaluated:**
- SIMPLICITY — effort: S
- QUALITY — effort: M
- DELETION — effort: S

**Chosen Approach:** Reuse data_safety.isolate_store + write_recovery_marker inside AppConfigManager on a parse/shape failure of config.json (load) or secrets.json (set_secret); share one atomic writer; make the attention item text store-aware.

**Success Criteria:**
- pytest test_app_config_manager.py test_attention_authority.py test_data_safety.py green
- Reverting the isolate call in load() turns the AC1 test RED
- Reverting the OSError split in set_secret turns the AC3 test RED

**Always:**
- Preserve bytes before any write over a present file
- Never isolate on a transient OSError
- Reuse data_safety primitives

**Never:**
- unlink / os.replace over an unreadable user store
- Write markers into the real app dir from tests
- Add async approval waits inside load()


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Profile bugfix (known root cause, multi-file) | mechanical | decision tree: clear bug with known root cause |
| evaluate | Include secrets.json in scope | taste | same class, same module |
| think | Reuse data_safety primitives, no approval wait in load() | mechanical | guard_destructive is async + session-bound; load() |
| plan | Marker dir follows custom config_path | mechanical | tests must not write into the live app dir |
| build | Unique mkstemp temp name instead of fixed .tmp (Gate-1) | mechanical | multiple AppConfigManager instances write without  |
| review | Gitignore secrets.json + *.corrupt-* in workspace template a | mechanical | isolation would otherwise commit API keys; same ch |
| test | 18 failures classified pre-existing via clean HEAD worktree | mechanical | identical failure set at HEAD |

**Summary:** 6 mechanical, 1 taste, 0 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**3 findings** (verdict: ?)
- [LOW] backend/core/app_config_manager.py set_secret: non-UTF-8 secrets.json raised a r
- [MEDIUM] backend/core/app_config_manager.py _isolate_corrupt: each failed isolation appen
- [LOW] backend/core/attention_authority.py could-not-isolate message claimed default se

### 7.6 Completion Audit
(No completion audit data)


## 8. Files Changed
- `backend/core/app_config_manager.py`
- `backend/core/attention_authority.py`
- `backend/tests/test_app_config_manager.py`
- `backend/tests/test_attention_authority.py`
- `backend/core/swarm_workspace_manager.py`
- `backend/tests/test_swarm_workspace_manager.py`

## 9. Lessons
- [pitfall] A recovery path that 'falls back to defaults' on a parse error is a destroy-user-data path in disguise whenever it then WRITES those defaults over the file — the fallback must preserve (rename) the unreadable store before any write, and must never treat a transient read error (OSError) as corruption.
- [pitfall] Preserving a corrupt file next to the original inherits the original's leak surface but not its ignore rule: an exact-name .gitignore entry (secrets.json) does not cover the isolated copy (secrets.json.corrupt-<ts>), so every isolation mechanism inside a git-tracked workspace needs a matching wildcard ignore rule shipped in the template AND the existing-workspace migration.
- [principle] A write that can now be refused must not mutate in-memory state first — commit to the cache only after the durable write succeeds, or a refused write leaves the process running on settings that vanish on restart.
- [pitfall] A deduplicating marker that prunes entries only when their target file disappears will grow without bound for any entry whose target stays in place (e.g. 'could not isolate'); dedupe on write by key and on read by item id.


## 10. Known Gaps & Attention Flags
None identified.


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-17 08:57 UTC*
