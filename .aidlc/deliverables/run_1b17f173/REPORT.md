# Add the Inbox overlay (desktop UI only) per the approved mockup: a typed client… · Audit through DELIVER

Delivered: Add the Inbox overlay (desktop UI only) per the approved mockup: a typed client, the nav card first in Work with a reply-count badge, four lanes plus channel settings, inside the existing overlay host. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 1h 6m wall · 1h 6m active · 68.2% budget |
| Quality | WARN | 1/1 verification · 7 not run · 6/8 findings closed (5 test-closed, confirmed by review) · CF-b4ff5105: no mutation check (the finding is a missing assertion in a test: the fix is the assertion itself, and the production path it checks (no selection made for a missing item) is the s) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 969k comparable · 770k input · 199k output · 38.7M cache reads, not counted · 39.6M billed · host-session responses in the run window · 72.7% budget |

Project Agent: SwarmAI · code-change · 11 checks · 1 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+5 found on demand) · searched the DDD 3×

## Decisions

- D1 · How is the 已发 red marker drawn with the shared Tabs? → count = sends\_need\_you with tone 'alert' when &gt; 0, nothing when 0. Why: Tabs has no dot and is not this run's file; an alert count is the shared component's way of saying 'waits on you' (DESIGN\_SPEC, agent)
- D2 · Where does the in-overlay confirmation (已发送 …) show? → a small role=status line inside the overlay, as in the mockup, not the app-wide toast stack. Why: it belongs to this surface and keeps the overlay testable without a ToastProvider; no undo, because the contract has no reopen action (DESIGN\_SPEC, agent)
- D3 · Which item is selected after a send or action? → the next item in the displayed order (reply then proposed), else the previous one, else the empty state; the handled id is hidden locally until the refetch drops it. Why: the mockup's advance rule; hiding locally avoids a flash of the handled item while the list reloads (DESIGN\_SPEC, agent)
- D4 · How is an item handed to chat safely? → a prompt whose item text sits in a backtick fence longer than any backtick run inside it, introduced as an external message that is data, not instructions. Why: a fixed fence is closed early by content containing it (DDD 9ccf1014) (DESIGN\_SPEC, agent)
- D5 · Which queries does a write refresh? → every write calls invalidateOverlay(qc, 'inbox'), which refreshes counts (the nav badge shares the same query), lists and sends. Why: a mutation must refresh every cached query it changes, including the badge (DDD 36fc5de1) (DESIGN\_SPEC, agent)
- D6 · What does 学进 Brain do on a trend point? → dispatch a chat prompt that asks to learn the URL through the learn-content skill, the item fenced as external data. Why: the contract has no learn route; the existing chat path keeps CDF's own admission (DESIGN\_SPEC, agent)
- D7 · What label replaces the CDF stage 'INBOX'? → 'QUEUE' / '待写入', uppercase like the diagram's other stage labels. Why: the other labels are uppercase English; 待写入 is the requested Chinese label (DESIGN\_SPEC, agent)
- D8 · Which existing tests change with the new nav card? → the three nav-order readers (LeftSidebarRedesign DOMAIN\_ORDER, ThreeColumnLayout.property expectedNavOrder, navOverlayContract ITEMS) gain nav-inbox. Why: the DDD standard requires both order lists in the same commit; the contract test checks the new card like every other (BUILD report · swarmai, agent)
- D9 · Feed editor: copy the reason extraction from Community? → no: the copy reads the backend reason from ApiError.message. Why: Community reads error.response.data.detail, which the shared interceptor never produces (it rejects with ApiError), so Community always shows its generic text; CommunityOverlay is not this run's file and is removed later (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Narrow layout through CSS container queries only (as in the mockup). Not taken: the list/detail switch and the back button are state, not style: a CSS-only switch cannot be tested in jsdom and still needs JS for which pane shows; one measured width gives both
- A3 · Let send() throw through the shared axios error interceptor and read the reason from ApiError. Not taken: parseApiError's 409 branch flattens FastAPI's {detail:{...}} object (code becomes DUPLICATE\_RESOURCE, spans are lost); the redacted spans and the reason code would depend on that generic mapper
- A4 · Add a dot prop to the shared Tabs for the 已发 marker. Not taken: components/overlay is not a file this run owns; the shared Tabs' alert-toned count says the same thing (sends that need the person) and is reported as a merge note

Requirements analysis:

- missing\_edge (R5.2): backend/schemas/inbox.py does not define the 403/409 response body or the shape of the redacted spans. Handling: the client parses FastAPI's {detail: string \| {code, message, spans}} tolerantly (spans as {start,end} or \[start,end\]); the missing SendRefusal model is reported for the merge → rewritten
- missing\_edge (R6.6): GET /api/inbox/feeds is said to be unchanged in shape, but Community has no GET /feeds; its list route is GET /community/sources returning {sources:\[...\]}. Handling: the client reads {sources:\[...\]} (or a bare array) from GET /inbox/feeds and maps it like communityService.fetchSources; reported for the merge → rewritten
- missing\_edge (R6.2): the trend lane's folded 其余 N 条 needs the trend items, which come from GET /items?lane=trend. Handling: the remaining items are the trend-lane items not referenced by a summary point's item\_id → rewritten
- missing\_edge (R3.3): the shared Tabs component has no dot; it has an alert-toned count. Handling: the sent tab uses Tabs' count with tone alert (the number of sends needing the person) since components/overlay is not this run's file; reported as a merge note → rewritten
- missing\_edge (R2.3): the backend route may not exist yet on a machine running this UI (404). Handling: a failed counts query shows no badge and the overlay shows its error state with retry → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 11/27 tool calls
- correctness · review r1: read 100% of its prompt · 16/27 tool calls
- security · review r2: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 14/25 tool calls
- security · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/25 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 5e51b558b9b6: 6 pass, 0 fail, 0 not run
- test · swarmai @ 2559f35c9100: 5 pass, 0 fail, 7 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: not_run
  - backend-problem-values: not_run
  - backend-security-scan: not_run
  - skills-lint: not_run
  - experts-lint: not_run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: pass
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- npx tsc --noEmit → pass
- npm run lint:contract → pass
- npm run build → pass (index chunk 4,058 kB under the 4,186 kB cap; InboxOverlay its own chunk)
- bash scripts/sync-version.sh check → pass
- npx vitest run src/components/layout/InboxOverlay.test.tsx src/components/layout/InboxNav.test.tsx src/services/\_\_tests\_\_/inbox.test.ts src/components/inbox/chatPrompt.test.ts src/\_\_tests\_\_/navVisualS → pass (243 tests: the new Inbox tests, the design-system guards, and every reader of the nav order / overlay ids / overlay host)
- npx vitest run --reporter=verbose → not\_run (the whole suite did not finish inside a 590 s cap on this shared Hive (two other vitest runs were going at the same time); up to the cap the one failure was BrainHub.test.tsx 'typing a query calls bra)
- python scripts/smoke\_import.py → not\_run (no backend file changed)
- python scripts/lint\_problem\_values.py → not\_run (no backend file changed)
- python scripts/security\_scan.py → not\_run (no backend file changed)
- python scripts/lint\_skills.py → not\_run (no skill changed)
- 2 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npx vitest run --reporter=verbose: the whole suite did not finish inside a 590 s cap on this shared Hive (two other vitest runs were going at the same time); up to the cap the one failure was BrainHub.test.tsx 'typing a query calls brainRecall', which passes alone (68/68) and is not touched by this change. Left to CI.
- swarmai · python scripts/smoke\_import.py: no backend file changed
- swarmai · python scripts/lint\_problem\_values.py: no backend file changed
- swarmai · python scripts/security\_scan.py: no backend file changed
- swarmai · python scripts/lint\_skills.py: no skill changed
- swarmai · python scripts/lint\_experts.py: no expert changed
- swarmai · bash scripts/run\_bvt.sh: no backend file changed; no backend test added to bvt\_manifest

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: desktop/src/components/inbox/AwareLane.tsx
- swarmai: desktop/src/components/inbox/ChannelSettings.tsx
- swarmai: desktop/src/components/inbox/FeedEditor.tsx
- swarmai: desktop/src/components/inbox/ReplyLane.tsx
- swarmai: desktop/src/components/inbox/SentLane.tsx
- swarmai: desktop/src/components/inbox/TrendLane.tsx
- swarmai: desktop/src/components/inbox/chatPrompt.test.ts
- swarmai: desktop/src/components/inbox/chatPrompt.ts
- swarmai: desktop/src/components/inbox/parts.tsx
- swarmai: desktop/src/components/layout/InboxNav.test.tsx
- swarmai: desktop/src/components/layout/InboxOverlay.test.tsx
- swarmai: desktop/src/components/layout/InboxOverlay.tsx
- swarmai: desktop/src/services/\_\_tests\_\_/inbox.test.ts
- swarmai: desktop/src/services/inbox.ts
