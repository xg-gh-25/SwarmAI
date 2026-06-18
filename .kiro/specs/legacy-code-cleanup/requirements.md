# Requirements Document

## Introduction

This feature defines a safe, staged, comprehensive cleanup of legacy, deprecated, and dead code (and obsolete tests) across the SwarmAI codebase — Tauri 2.0 + React 19 frontend (`desktop/`) and Python FastAPI backend (`backend/`) — in preparation for the next version.

The goal is to reduce maintenance debt without regressing behavior for existing users. A central tension drives every requirement: some "legacy" code is **truly dead** (safe to delete), while other "legacy" code is **still required for the upgrade path** of installed user environments (one-time migrations, backward-compatible parsers kept for old on-disk data). The cleanup MUST distinguish these two classes and treat them differently.

The cleanup is scoped to **identification, verification, classification, removal/consolidation, and documentation**. It is explicitly NOT a behavioral rewrite: platform invariants and user-facing behavior are preserved. Removal is gated by verification evidence and performed in small, individually-bisectable commits, each keeping CI green.

The design phase will produce the full per-candidate inventory; these requirements define the rules, scope boundaries, safety gates, and verification standards that govern that work.

## Glossary

- **Cleanup_Workflow**: The overall staged process that identifies, classifies, verifies, removes, and documents legacy code and obsolete tests.
- **Legacy_Candidate**: A specific code element (module, function, class, import, export, commented-out block, or test) flagged as a possible target for removal or consolidation.
- **Inventory_Report**: The catalogued list of all Legacy_Candidates, each annotated with category, classification, and verification evidence.
- **Dead_Candidate**: A Legacy_Candidate with no remaining call sites and no upgrade-path dependency — safe to delete.
- **Upgrade_Path_Candidate**: A Legacy_Candidate that is still required to migrate or read data from previously-installed user environments (for example one-time state-directory migrations or backward-compatible parsers for old on-disk formats).
- **Verification_Evidence**: The recorded proof used to classify a Legacy_Candidate, consisting of call-site search results, the Code_Intel dead-code report, and relevant test coverage.
- **Code_Intel**: The existing AST graph dead-code capability (`backend/core/code_intel/dead_code.py`, `find_dead_code()`) that reports exported-but-unreferenced and unreachable symbols.
- **Platform_Invariant**: A documented non-negotiable behavior of SwarmAI (for example fixed port 18321, `utils.file_lock` instead of raw `fcntl`, no `lsof`, session lifecycle rules, multi-tab isolation, context/memory safety, self-evolution guardrails) defined in `.kiro/steering/`.
- **CI_Gate**: The 4 continuous-integration jobs run on every push to main (backend, backend-windows, frontend, version-check).
- **Removal_Commit**: A single Git commit that removes or consolidates one logically-cohesive set of Legacy_Candidates and is independently bisectable.
- **Deprecation_Lifecycle**: The deprecate-then-prune pattern modeled on `backend/hooks/evolution_maintenance_hook.py`, where an element is marked deprecated before a later release deletes it.

## Requirements

### Requirement 1: Comprehensive Legacy Inventory

**User Story:** As a maintainer preparing the next version, I want a complete catalogue of all legacy candidates across backend and frontend, so that cleanup decisions are made against a known scope rather than ad hoc.

#### Acceptance Criteria

1. WHEN the Cleanup_Workflow begins, THE Cleanup_Workflow SHALL produce an Inventory_Report that lists every Legacy_Candidate found in all files under the `backend/` and `desktop/` directories, excluding files matched by `.gitignore`.
2. THE Cleanup_Workflow SHALL search for Legacy_Candidates using case-sensitive matching of at least the markers `legacy`, `DEPRECATED`, `deprecated`, `backward compat`, `backwards compat`, `back-compat`, `TODO: remove`, and `FIXME`.
3. THE Cleanup_Workflow SHALL include the Code_Intel dead-code report as a source of Legacy_Candidates in the Inventory_Report.
4. THE Inventory_Report SHALL record, for each Legacy_Candidate, the file path, the symbol or line range, the category assigned under Requirement 2, and the matched marker or Code_Intel source that contributed the entry.
5. WHERE a Legacy_Candidate is a parser, serializer, or fallback format reader, THE Inventory_Report SHALL record the data format and the producing code version it supports.
6. WHEN a single location is found by more than one marker, or by both a marker and the Code_Intel dead-code report, THE Inventory_Report SHALL record that location once and SHALL list every contributing source for that entry.
7. IF a path under `backend/` or `desktop/` cannot be read during the scan, THEN THE Cleanup_Workflow SHALL record the inaccessible path in the Inventory_Report and SHALL continue the scan without aborting.
8. IF the Code_Intel dead-code report is unavailable, THEN THE Cleanup_Workflow SHALL record the missing dead-code source in the Inventory_Report and SHALL produce the Inventory_Report from the marker search results.

### Requirement 2: Legacy Category Assignment

**User Story:** As a maintainer, I want each candidate sorted into a defined category, so that similar items are handled with a consistent rule.

#### Acceptance Criteria

1. WHEN the Cleanup_Workflow processes a Legacy_Candidate, THE Cleanup_Workflow SHALL assign that candidate to exactly one of the following categories: deprecated module, stale one-time migration, backward-compatible fallback parser, legacy platform shim, dead code reported by Code_Intel, obsolete test, duplicate test, commented-out code, unused import, or unused export.
2. IF a Legacy_Candidate satisfies the matching conditions of more than one category, THEN THE Cleanup_Workflow SHALL assign the candidate to the first matching category in the order listed in criterion 1 and SHALL record the assignment as the candidate's single category.
3. IF a Legacy_Candidate matches none of the defined categories, THEN THE Cleanup_Workflow SHALL record the candidate as "uncategorized" and SHALL exclude that candidate from removal until a maintainer assigns one of the categories listed in criterion 1.
4. WHEN the Inventory_Report is generated, THE Inventory_Report SHALL group all Legacy_Candidates by their assigned category, including a distinct group for candidates recorded as "uncategorized".

### Requirement 3: Dead vs Upgrade-Path Classification

**User Story:** As a maintainer, I want every candidate classified as either truly dead or still-needed-for-upgrade, so that I never delete code that existing user installs still depend on to upgrade.

#### Acceptance Criteria

1. THE Cleanup_Workflow SHALL assign each Legacy_Candidate to exactly one of two mutually exclusive classifications, Dead_Candidate or Upgrade_Path_Candidate, before that candidate is eligible for removal.
2. IF a Legacy_Candidate reads, migrates, or transforms data written by any version released to users prior to the current release, THEN THE Cleanup_Workflow SHALL classify that candidate as an Upgrade_Path_Candidate.
3. THE Cleanup_Workflow SHALL retain every Upgrade_Path_Candidate in the codebase during this cleanup.
4. WHERE an Upgrade_Path_Candidate is retained, THE Cleanup_Workflow SHALL apply the Deprecation_Lifecycle by recording the candidate as deprecated and naming the earliest future version in which deletion is permitted.
5. IF the classification of a Legacy_Candidate cannot be determined from Verification_Evidence, THEN THE Cleanup_Workflow SHALL classify that candidate as an Upgrade_Path_Candidate.
6. WHEN a Legacy_Candidate is classified as a Dead_Candidate, THE Cleanup_Workflow SHALL mark that candidate as eligible for removal during this cleanup.

### Requirement 4: Removal Verification Gate

**User Story:** As a maintainer, I want removals gated by recorded evidence, so that deletion is justified by proof rather than assumption.

#### Acceptance Criteria

1. WHEN a Dead_Candidate is selected for removal, THE Cleanup_Workflow SHALL record Verification_Evidence containing the call-site search result, the Code_Intel dead-code report status, and the removal decision before the candidate is removed.
2. WHEN a Dead_Candidate is verified, THE Cleanup_Workflow SHALL perform a call-site search across `backend/` and `desktop/` and SHALL confirm that zero referencing call sites remain.
3. WHEN a Dead_Candidate is verified, THE Cleanup_Workflow SHALL confirm that the candidate is reported as dead by the Code_Intel dead-code report.
4. IF the Code_Intel dead-code report does not report a Dead_Candidate as dead, THEN THE Cleanup_Workflow SHALL record a written justification stating why the report does not apply before proceeding with removal.
5. IF a call-site search returns one or more active references (non-comment, non-string call sites) to a Legacy_Candidate, THEN THE Cleanup_Workflow SHALL reclassify that candidate as an Upgrade_Path_Candidate or retained code and SHALL exclude it from removal.
6. WHEN searching for call sites of a function before its removal, THE Cleanup_Workflow SHALL search all test directories under `backend/` and `desktop/` using the pattern `function_name(`.

### Requirement 5: Platform Invariant Preservation

**User Story:** As a maintainer, I want the cleanup to leave every platform invariant intact, so that core runtime behavior does not regress.

#### Acceptance Criteria

1. THE Cleanup_Workflow SHALL preserve the fixed backend port 18321 as the single bound listening port and SHALL NOT introduce dynamic or runtime-selected port allocation.
2. THE Cleanup_Workflow SHALL keep all file locking routed through `utils.file_lock` and SHALL NOT introduce any module-level `import fcntl` in any retained or modified file.
3. THE Cleanup_Workflow SHALL keep all process checks and port checks free of any `lsof` invocation in any retained or modified script.
4. WHILE removing code in a Regression-Prone Area listed in `.kiro/steering/swarmai-dev-rules.md`, THE Cleanup_Workflow SHALL preserve the session lifecycle, multi-tab isolation, context-and-memory safety, and self-evolution guardrail invariants documented in the corresponding steering files, such that no documented invariant in those files is removed, weakened, or altered by the cleanup.
5. WHEN the Cleanup_Workflow completes a removal affecting a Regression-Prone Area, THE Cleanup_Workflow SHALL confirm that the existing targeted tests covering that area produce identical pass/fail results to the pre-cleanup baseline.
6. IF a proposed removal would alter any Platform_Invariant (defined as the conditions in criteria 1 through 4), THEN THE Cleanup_Workflow SHALL exclude that removal from this cleanup and SHALL retain the affected code unchanged.

### Requirement 6: Behavior Preservation for Existing Users

**User Story:** As an existing SwarmAI user upgrading to the next version, I want my installed data and workflows to keep working, so that the cleanup is invisible to me.

#### Acceptance Criteria

1. WHEN a user upgrades from any previous version, THE Cleanup_Workflow SHALL preserve the on-disk state-directory migration path such that an environment created by a previous version loads successfully after upgrade with no loss of previously-stored state data and with no manual intervention required.
2. WHEN a user upgrades from any previous version, THE Cleanup_Workflow SHALL preserve the MCP configuration migration path such that configuration files stored by a previous version are converted to the current format with no loss of previously-stored configuration entries.
3. WHEN a removal targets code on a user upgrade path, THE Cleanup_Workflow SHALL retain that code under Requirement 3.
4. THE Cleanup_Workflow SHALL produce no change to the documented SSE streaming event shapes (session_start, assistant, tool_use, tool_result, ask_user_question, cmd_permission_request, result, and error) consumed by the frontend.
5. IF the on-disk state-directory migration fails during upgrade, THEN THE Cleanup_Workflow SHALL retain the original pre-upgrade state directory unmodified and return an error indication identifying the migration failure.
6. IF the MCP configuration migration fails during upgrade, THEN THE Cleanup_Workflow SHALL retain the original pre-upgrade configuration files unmodified and return an error indication identifying the conversion failure.

### Requirement 7: Obsolete and Duplicate Test Cleanup

**User Story:** As a maintainer, I want obsolete and duplicate tests removed or consolidated, so that the test suite reflects current behavior and runs faster.

#### Acceptance Criteria

1. WHEN every production symbol exercised by a test has been removed as a Dead_Candidate, THE Cleanup_Workflow SHALL remove that test in the same Removal_Commit as the code it covered.
2. IF a test exercises at least one production symbol that has not been removed, THEN THE Cleanup_Workflow SHALL retain that test.
3. WHERE two or more tests assert identical expected outcomes for identical input values, THE Cleanup_Workflow SHALL consolidate them into a single test that preserves every distinct assertion and every distinct input combination from the original tests.
4. THE Cleanup_Workflow SHALL retain every test that covers an Upgrade_Path_Candidate.
5. IF removing a test would leave a retained behavior with no remaining test exercising that behavior, THEN THE Cleanup_Workflow SHALL keep that test or add a replacement test exercising the same behavior with the same inputs and expected outcomes before completing the removal.
6. WHEN the Cleanup_Workflow completes a Removal_Commit, THE Cleanup_Workflow SHALL run the affected test suite and confirm that all retained tests pass; IF any retained test fails or fails to compile, THEN THE Cleanup_Workflow SHALL revert the Removal_Commit and report the failing test.

### Requirement 8: Frontend Code Hygiene

**User Story:** As a frontend maintainer, I want unused imports, exports, and dead components removed cleanly, so that the TypeScript build stays strict and green.

#### Acceptance Criteria

1. WHEN a frontend Legacy_Candidate is removed, THE Cleanup_Workflow SHALL remove every import statement and import symbol that becomes unreferenced and SHALL NOT disable, weaken, or suppress the `noUnusedLocals` check through any suppression directive.
2. WHEN a hook or module is replaced, THE Cleanup_Workflow SHALL delete the obsolete source file and SHALL remove its re-export from the corresponding index file (for example `hooks/index.ts`).
3. WHEN an individual frontend removal completes, THE Cleanup_Workflow SHALL confirm that `tsc` reports zero errors and that `eslint` reports zero errors before beginning the next removal.
4. IF `tsc` or `eslint` reports one or more errors after a frontend removal, THEN THE Cleanup_Workflow SHALL halt, report the failing errors, and SHALL NOT mark the cleanup complete until both `tsc` and `eslint` report zero errors.
5. WHEN a backend field is added, removed, or renamed, THE Cleanup_Workflow SHALL ensure that every remaining backend field has exactly one camelCase mapping in the `toCamelCase()` conversion functions and that no mapping references a removed field.

### Requirement 9: Build and CI Verification

**User Story:** As a maintainer, I want every cleanup step verified against the build and targeted tests, so that no removal ships a broken state.

#### Acceptance Criteria

1. WHEN a backend Removal_Commit is created, THE Cleanup_Workflow SHALL run the targeted tests for the affected modules using a per-test timeout of 60 seconds, where affected modules are defined as modules whose files were modified by the Removal_Commit or whose tests reference a removed symbol.
2. THE Cleanup_Workflow SHALL NOT run the full backend test suite proactively.
3. WHERE the full backend test suite is run, THE Cleanup_Workflow SHALL invoke it only through the documented `SWARMAI_SUITE` invocation using a per-test timeout of 120 seconds.
4. WHEN the cleanup is evaluated for completion, THE Cleanup_Workflow SHALL confirm that the backend production build command `./prod.sh build` completes with a success exit status.
5. IF a targeted test or the build step fails after a removal, THEN THE Cleanup_Workflow SHALL indicate which step failed, revert or correct the removal, and confirm success before proceeding to the next Removal_Commit.
6. WHEN a Removal_Commit is pushed to main, THE Cleanup_Workflow SHALL keep all 4 CI_Gate jobs passing.

### Requirement 10: Staged Bisectable Commits

**User Story:** As a maintainer, I want each cleanup change isolated in its own commit, so that any regression can be bisected to a single removal.

#### Acceptance Criteria

1. WHEN the Cleanup_Workflow records a removal or consolidation, THE Cleanup_Workflow SHALL place exactly one logically-cohesive change set in its own Removal_Commit, where a logically-cohesive change set is defined as all edits required to remove or consolidate a single named symbol, file, module, or re-export together with the edits required to keep the build green.
2. IF a single removal or consolidation references two or more unrelated named symbols, files, modules, or re-exports, THEN THE Cleanup_Workflow SHALL split the change into one Removal_Commit per unrelated item and SHALL NOT combine them into a single Removal_Commit.
3. WHEN the Cleanup_Workflow creates a Removal_Commit, THE Cleanup_Workflow SHALL verify that the build is in a green state at that commit, where green state is defined as the project build command completing with a success exit code and all executed tests passing with zero failures, so that the commit is independently bisectable.
4. IF the build is not in a green state at a Removal_Commit, THEN THE Cleanup_Workflow SHALL withhold that Removal_Commit, leave the working tree and prior commit history unchanged, and produce an error indication identifying the failing commit.
5. WHEN the Cleanup_Workflow creates any Removal_Commit, THE Cleanup_Workflow SHALL end the commit trailer with `Co-Authored-By: Swarm <swarm@swarmai.dev>` and SHALL NOT include a Claude or Anthropic identity in any commit trailer.

### Requirement 11: Documentation and Invariant Capture

**User Story:** As a maintainer, I want the cleanup recorded and the temporary anti-pattern list replaced by durable design invariants, so that future contributors understand what was removed and why the structure prevents reintroduction.

#### Acceptance Criteria

1. WHEN the Cleanup_Workflow removes or deprecates a Legacy_Candidate, THE Cleanup_Workflow SHALL record in `CHANGELOG.md` the candidate identifier, whether the candidate was removed or deprecated, and the reason for the change before the task is considered complete.
2. IF appending an entry to `CHANGELOG.md` fails, THEN THE Cleanup_Workflow SHALL preserve the existing changelog content and SHALL report an error identifying the failed entry.
3. WHEN the temporary anti-pattern list referenced in project memory is replaced, THE Cleanup_Workflow SHALL leave no remaining reference to that temporary anti-pattern list.
4. THE design-invariants document SHALL contain one structural rule for each removed legacy pattern that states how the structure prevents that pattern from returning.
5. THE Cleanup_Workflow SHALL keep every retained module compliant with the code documentation standard in `.kiro/steering/swarmai-dev-rules.md` by providing each retained module with a module-level docstring containing a one-line summary.
6. THE Cleanup_Workflow SHALL exclude the non-inclusive terms master, slave, whitelist, blacklist, whiteday, and blackday from all added or modified code, comments, and documentation.
