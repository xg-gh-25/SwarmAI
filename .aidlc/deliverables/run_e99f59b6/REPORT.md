# Extend the desktop no-hardcoded-ui-text lint to .ts files, user-facing object p… · Audit through DELIVER

Delivered: Extend the desktop no-hardcoded-ui-text lint to .ts files, user-facing object properties and text-showing calls, migrate every string it finds to en/zh i18n keys, neutralise instance words in the loca. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 47m 9s wall · 47m 9s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 0/3 findings closed · review coverage: security r1 read 18% of its prompt; correctness r1 read 9% of its prompt, stopped at its 28-call cap |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 998k comparable · 774k input · 224k output · 32.3M cache reads, not counted · 33.3M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 3 (+2 found on demand) · searched the DDD 1×

## Decisions

- D1 · Which property keys count as user-facing → label, title, subtitle, navLabel, desc, description, message, detail, placeholder, tooltip, hint, and any camelCase key whose last word is one of them; keys ending in Key never. Why: covers the named shapes without a file list; a key holder is named \*Key (DESIGN\_SPEC, agent)
- D2 · Which calls → setError, set&lt;Name&gt;Error, setErrorMessage, alert, confirm, toast, toast.&lt;x&gt;, addToast, showToast (also as a member call, e.g. toastCtx?.addToast): their first argument. Why: the callees the tree uses for text a person reads; an object argument is covered by D1 (DESIGN\_SPEC, agent)
- D3 · How data that already holds an i18n key passes → a literal equal to a leaf key of locales/en.json (or a plural family base) is not reported. Why: exact and deterministic (CMBrainOverlay QUEUE\_META, ExplorerHeader sort options) (DESIGN\_SPEC, agent)
- D4 · New exemptions → exact: streamChat, answer-question, cmd-permission-continue (SSE log labels), 'User answers: ${…}' (sent to the model), HTTP, the TSCC scopeLabel default 'Workspace: SwarmWS (General)'; patterns: AWS region codes, a modifier symbol plus one key cap. Why: same in every language or never shown; a compared sentinel translated would change behaviour in zh (DESIGN\_SPEC, agent)
- D5 · Key names → ui.&lt;FileBase&gt;.&lt;name&gt;, the full key written as a literal next to its use; template families flattened to ui.X.prefix\_${k}. Why: existing convention; ui-keys-used.test sees only literal or one-level template keys (DESIGN\_SPEC, agent)
- D6 · Amazon onboarding strings → reword to an internal (ADA) setup in en and zh, keep showing them. Why: AuthConfigPanel shows the switch on low-confidence detection on purpose (F3); hiding would break that (DESIGN\_SPEC, agent)
- D7 · mwinit in ADA remedy strings vs the new locale scan → the locale scan parses the JSON and allows mwinit only under a key path with an ada segment (a path segment with ada as an underscore word); every other term and path fails; p7\_exceptions.txt untouched. Why: matches the stated rule structurally, with no count file (DESIGN\_SPEC, agent)
- D8 · Terminal shortcuts off mac → Ctrl+Shift+C/A/K/Enter, never plain Ctrl; Ctrl+Shift+C swallowed even with no selection; labels from isMac()/modKeyLabel(), mac computed once per mount. Why: no clash with the shell's Ctrl-C; per-keystroke path stays a boolean check (DESIGN\_SPEC, agent)
- D9 · Migration in parallel → four sub-agents with disjoint file groups; each merges its keys into en/zh through one merge script under a file lock that refuses conflicting values and en/zh key mismatches; each en value equals the replaced text. Why: about 60 files; the locale files are the only shared write; equal en text keeps the English UI and its tests unchanged (DESIGN\_SPEC, agent)
- D10 · Visible English under keys the rule does not cover (short, name, suggestedAction, STATUS\_LABEL-like maps outside D1 keys) → left for a follow-up and listed in the delivery. Why: widening the key set is a separate design decision with many false positives (DESIGN\_SPEC, agent)
- D1 · The rule's explicit endsWith('Key') guard survived mutation → deleted it; the doc says a \*Key name never matches by construction. Why: a \*Key name's last camelCase word is Key, which is never a user-facing word, so the guard was dead code (BUILD report · swarmai, agent)
- D2 · Locale key loading in the rule → resolve en.json via fileURLToPath/join and let a missing file throw (no silent catch). Why: under vitest's jsdom a URL object fails readFileSync and the silent catch turned the key pass off; a loud failure cannot hide that (BUILD report · swarmai, agent)
- D3 · Product surface names in zh nav (Library, Inbox, Pipeline, Pollinate, Brain Hub, C&amp;M) → kept as in the existing zh overlay titles. Why: consistency with the zh UI's existing names for those surfaces (BUILD report · swarmai, agent)
- D4 · Displayed English under keys the rule does not read (api.ts suggestedAction, ToolSelector category names, CapabilitiesOverlay STATUS\_LABEL/DOMAIN\_LABEL, LibraryOverlay MANAGE\_ROWS and mount toasts, brainMapModel sectionCounts, thinking option names) → left for a follow-up and listed in the delivery; a few unreported shown texts were migrated in passing (CMBrainOverlay item names, backendIssue short pills, attachment errors). Why: widening the rule's key set is a separate design decision with many false positives (BUILD report · swarmai, agent)
- D5 · VirtualizedTree section label was also an id (collapsed state, localStorage key) → split into id (unchanged values) and labelKey. Why: translating it would have changed localStorage keys and comparisons in zh (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · A second lint rule only for .ts files. Not taken: duplicates the exemption list and leaves .tsx object literals (FileContextMenu items, LibraryOverlay tabs) uncovered; rejected before (PROJECT decision bbb41fb9).
- A3 · A baseline file of today's findings and fail only on new ones. Not taken: the task asks for no baseline; a baseline keeps \~290 English strings in the zh UI and grows stale.
- A4 · Translate labels when the table is defined (t() at module load). Not taken: freezes the language at import time, so a later language switch shows the old language; helpers importing the app's i18n init break tests that mock react-i18next.

Requirements analysis:

- missing\_edge (R1.4): Some tables already hold i18n keys in label-like properties (CMBrainOverlay QUEUE\_META, ExplorerHeader sort options); without a key pass they would be false positives. → rewritten
- missing\_edge (R1.5, R2.1): A few found strings are never rendered (SSE log labels, the message sent to the model, the TSCC scopeLabel default that TSCCPanel compares as a value); translating a compared sentinel would change behaviour in zh. → rewritten
- missing\_edge (R5.1): Ctrl+Shift+C with no selection would otherwise reach the shell as an interrupt-like key; it is swallowed. → rewritten
- missing\_edge (R2.2): Visible English under keys the rule does not cover (short, name, suggestedAction, ...) stays for a follow-up: widening the key set is a separate design decision with many false positives. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 18% of its prompt · 15/28 tool calls
- correctness · review r1: read 9% of its prompt · 29/28 tool calls · stopped at the cap
- correctness · review r2: read 100% of its prompt · 4/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 5bc0e65d70f0: 10 pass, 0 fail, 0 not run
- test · swarmai @ 6a832818dd1a: 12 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: pass
  - backend-bvt: pass
  - frontend-run-frontend-tests: pass

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- bash scripts/run\_bvt.sh → pass
- npx tsc --noEmit → pass
- npm run lint:contract → pass
- npx vitest run --reporter=verbose → pass
- 2 more in run_audit.json

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: desktop/src/components/terminal/TerminalTab.shortcuts.test.tsx
