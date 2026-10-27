---
title: Pipeline hygiene -- four structural fixes
status: implemented
created: 2026-10-27
updated: 2026-10-27
owner: kiro
---

# Pipeline hygiene: four structural fixes

Four leftovers from run-knowledge (`2026-10-26-run-knowledge.md`). Each one is a
mechanism gap, not a one-off; each fix removes the gap instead of working around it.

## 1. INSTRUCTIONS.md is at its 10,000-character limit

**Now.** `s_autonomous-pipeline/INSTRUCTIONS.md` is 9,993 characters
(`tests/test_lint_skills_size.py`, `scripts/lint_skills.py`). It is loaded for the whole
run, yet three of its sections are stage procedure that a stage card already owns and
`next` already delivers at that stage (`card_text`): `## REVIEW` 2,974 chars
(card `stages/review.md` 1,972), `## Tests` 968 (`stages/test.md` 1,100),
`## Landing and completion` 1,272 (`stages/deliver.md` 1,801). Two homes for one
stage's procedure is why every card change also grew INSTRUCTIONS.

**Rule.** INSTRUCTIONS holds only what applies across stages: start/resume, the
execute loop, asking a person, documents and checks, the human boundary. A stage's
procedure lives in its card (5,000-char limit each) and nowhere else.

**Fix.** Move the stage-only content of those three sections into their cards, leave
one line per section pointing at the card. Card limits still hold; nothing is
deleted that a stage needs. A test asserts each moved rule is in its card, not in
INSTRUCTIONS.

**Accept.** INSTRUCTIONS at least 3,000 characters under the limit; every card under
5,000; `test_pipeline_v1_cards` and the size lint green.

**Built (`16a73429a`).** INSTRUCTIONS 9,993 -> 5,824. Kept cross-stage: dispatching
handouts (REVIEW and Gate-0) and one paragraph saying where tests and landing are
described. The REFLECT paragraph was a duplicate of `reflect.md` and is gone.
Deviation: there were two card limits, `test_pipeline_v1_cards` (< 2,000) and
`lint_skills` (5,000). The test now reads `lint_skills.SIZE_LIMIT_STAGE_CARD`, so
5,000 is the one limit; largest card after the move is `review.md` at 2,921.
Per-run reading is about the same: the moved text is read on entering REVIEW and
DELIVER instead of once up front.

## 2. `test_pipeline_v1_real_e2e.py` takes ~430 s serially

**Now.** 43 independent cases, each a real CLI-driven run in its own `tmp_path`.
Measured with `-n 4`: 116 s wall, durations sum ~430 s. The slowest: the
command-sequence property test 44 s, then ~15-19 s per landing case. A single file
past the 290 s per-call cap cannot be run in one step, so it was run case by case.
The cost is the case count, not a slow command (one CLI call ~0.15 s).

**Rule.** A test file must finish serially within one call cap. CLI-driven e2e files
are split by topic so each stays under ~150 s on this host.

**Fix.** Split into files by topic, sharing `rig`/helpers through
`tests/pipeline_v1_e2e_common.py`: profiles and basics, approvals and landing,
main moves and sync, and the property test alone. The selector picks each file by
its own imports, so a change selects only the files that reach it. No test is
changed or dropped: the case set before and after is compared by name.

**Accept.** Same 43 case ids; each new file under 150 s serially.

**Built (`f471625f5`).** Six files (profiles, sync, human, landing, reflect,
sequences), serially 67 / 62 / 32 / 83 / 29 / 37 s; ids compared by name, all 43
the same. Two conformance files imported helpers from the old module and now
import them from the common one. No automatic guard on file runtime: a timing
assertion would be flaky; the rule stays in this doc and the common module.

## 3. `smoke_import.py` passes with 0 modules

**Now.** The script walks `pkgutil.walk_packages(path=["."])`, so it only works from
`backend/`. `validations.json` runs it with `cwd: "."` (repo root): it discovers 0
modules and exits 0. CI runs it from `backend/` (427 modules), so only the
validation path was blind.

**Fix.** The script finds `backend/` from its own location, not the cwd, and puts it
on `sys.path`; finding 0 modules is a failure (exit 1). Both call sites (CI and
`validations.json`) keep working unchanged.

**Accept.** Same count from repo root and from `backend/`; a 0-module walk exits 1.

**Built (`f9c51735b`).** 427 modules from the repo root, `backend/` and `/tmp`.

## 4. Scripts resolve SwarmWS on their own

**Now.** `config.get_swarmws()` is the one resolver for product code
(`test_swarmws_resolver.py`), but about 25 scripts in `backend/scripts/` and skill
`scripts/` still spell `~/.swarm-ai/SwarmWS` (or a variant). They ignore
`SWARM_WORKSPACE`, so under a relocated workspace they read the wrong tree.

**Rule.**
- `backend/scripts/*`: in-repo, may import `config` -> call `get_swarmws()`.
- Skill scripts: may run with no backend on `sys.path` (packaged `~/.claude/skills`
  copies), so they cannot import `config`. They carry one function, `_swarmws()`,
  AST-identical to `SKILL_RESOLVER` in `test_swarmws_resolver.py`, which applies
  config's full order (SWARM_WORKSPACE, SWARMWS, $SWARM_DATA_DIR/SwarmWS,
  ~/.swarm-ai/SwarmWS). A test checks it equals `get_swarmws()` under all 8
  settings of the three variables.

**Fix.** Rewrite each site to its rule; extend the structural test to scan
`backend/scripts/` and skill scripts with the same AST check.

**Built (`fbd9ae826`).** 14 files in `backend/scripts/`, 6 skill scripts.
Deviation: no `SWARM_WORKSPACE` export in `main.lifespan`; the skill resolver
follows config's whole order, so nothing needs the variable set. Three other
variable names (`SWARMWS_DIR`, `SWARM_WS`, `SWARMAI_WORKSPACE`) that only scripts
read and nothing sets are gone and count as resolver reads in the scan. Test files
are not scanned: setting the variables is how a test sandboxes. Red on main (3);
a literal path, a reordered skill resolver and an odd variable each turn it red.

**Accept.** Structural test green over all four trees; a mutation that re-adds one
literal path turns it red.

## 5. Skill scripts resolve the app data dir on their own (r2, 2026-10-27)

**Now.** notify, library and radar-todo's `todo_db` each re-implement
`config.get_app_data_dir` with a deprecated `SWARM_APP_DATA_DIR` fallback nothing
sets; five Pollinate scripts (`brand_config`, `check_prereqs`, `log_publish`,
`publish_meta`, `topic_backlog`) and `publish_to_pages`' clone default spell
`~/.swarm-ai/...` and ignore `SWARM_DATA_DIR`, so a sandboxed run writes the live
files. Same class as §4, one directory up.

**Rule.** As §4: a skill script carries one `_app_data_dir()`, AST-identical to
`SKILL_RESOLVER` in `tests/test_skill_data_dir_resolver.py` (`$SWARM_DATA_DIR`, then
`~/.swarm-ai`), checked equal to `get_app_data_dir()`. Outside it and `_swarmws()`,
no skill script builds `Path.home() / ".swarm-ai"`, expands `"~/.swarm-ai..."`, or
reads `SWARM_DATA_DIR` / `SWARM_APP_DATA_DIR`. Help text naming `~/.swarm-ai` stays.

**Built.** Nine skill scripts; `SWARM_APP_DATA_DIR` is gone. `test_pollinate_brand`
pinned the clone default to the live home path; it now expects the app data dir.
`apps_index.json` regenerated (Pollinate digest). Red on main (3); putting the alias
back, a literal Pollinate path, or `todo_db` ignoring the variable each turn it red.
Product code outside `skills/`: §6.

## 6. Product code resolves the app data dir on its own (r3, 2026-10-27)

**Now.** About 15 sites in product code and `backend/scripts` build
`Path.home() / ".swarm-ai"` or expand `"~/.swarm-ai..."`. Five do it as a fallback
when `from config import get_app_data_dir` fails (kill_audit, tool_risk, sel,
runtime_paths, run_worktree, two job handlers); that import cannot fail in the
backend (config imports only the standard library and model_registry, ~25 ms).
The others (gate_decision_log, permission_manager, backup_manager's token and
default root, session_health_probe's alert state, the workspace browser's start,
three scripts, system jobs' `"${HOME}/.swarm-ai"` cwd) ignore `SWARM_DATA_DIR`
outright. `scripts/lint_hardcoded_paths.py` was meant to stop this but nothing ran it.

**Rule.** Product code calls `config.get_app_data_dir()` (logs via
`get_log_file_path()`); no fallback. A script that cannot import config (the
uninstall tool) carries the verbatim `_app_data_dir()` of §5. System script jobs set
no cwd; the executor's default is `get_app_data_dir()`, the one source.

**Built.** `tests/test_app_data_dir_single_source.py` replaces the lint: an AST scan
of product code and `backend/scripts`, ALLOWED with reasons (`daemon_guard`: shared
with `swarmai_guardian.sh` and copied standalone to the guardian dir;
`verify_build`: sets `SWARM_DATA_DIR` for its probe HOME), plus the moved readers
following `SWARM_DATA_DIR`. Red on main (3); a home path, a `${HOME}` string and an
expanduser each turn it red.

**Not done.** `app_config_manager`'s default `sandbox_additional_write_paths` is the
setting text `"~/.swarm-ai/"`, which `prompt_builder` expands; with a non-default
`SWARM_DATA_DIR` the sandbox does not cover the data dir. Changing it changes a
permission default, so it is left for a decision.

## Out of scope

Behaviour of any script beyond where it finds the workspace. Historical data.
