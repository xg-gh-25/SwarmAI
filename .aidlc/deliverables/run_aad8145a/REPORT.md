# Autonomous Pipeline Report: Send-time Canvas snapshot always describes the tab being sent for

**Run ID:** run_aad8145a | **Project:** SwarmAI | **Profile:** bugfix
**Date:** 2026-10-18 06:14 UTC | **Duration:** 41.5 min (in progress) | **Tokens:** 0 (估算 / estimate)
**Gate 1:** WARN | **Files:** 4 | **Tests:** 137 run, 0 failed

## TL;DR
**解决了什么问题**
- 未记录

**怎么做的**
- 未记录

**遗留问题**（2 条，见 §10）

## 1. Requirement
Fix the stale send-time Canvas snapshot: ChatPage.refreshCanvasForSend is a stale closure inside handleSendMessage/drainQueuedMessage (missing from their deps), so the injected 'Current UI State' reports another tab's Canvas state/output count; queue-drain also snapshots the active tab instead of the drained tab. The send-time snapshot must always be the live state of the tab being sent for, with a regression test that fails on the old code.

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> 修 Canvas 快照旧闭包

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | user-story | user-stated: 修 Canvas 快照旧闭包 | confirmed | ● | Given two tabs with different Canvas output counts, when the user switches tabs and sends, then the request's editorContext.canvas reflects the sending tab |
| FR-2 | user-story | derived: same stale-snapshot path in drainQueuedMessage | confirmed | ● | Given a queued message in a background tab, when it drains, then the request carries that tab's snapshot or none |

### 澄清问答 (Clarifications — who decided what)
_(无澄清记录 / no clarifications)_

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
✅ 无未闭合问题 (no leftover — all requirements closed, no AI assumptions)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 7.00 | |
| Priority | 8.00 | |
| Feasibility | 9.00 | |

**Acceptance Criteria:**
1. AC1: After switching from tab A to tab B, a message sent from tab B carries tab B's live Canvas snapshot (open state and output count), never tab A's
2. AC2: When tab B's output count changes (a finish batch adds rows) the next send from tab B carries the new count without any unrelated re-render rebuilding the send callback
3. AC3: A queued message drained for a tab that is not the active tab never carries the ACTIVE tab's Canvas snapshot as its own; it carries the drained tab's snapshot or no canvas field
4. AC4: A regression test drives the real ChatPage send path (or the extracted send-time snapshot reader) and fails on the old code


## 3. Design & Approach
**Research Findings (THINK):**
- useCanvasHost returns a fresh object each render; its getCanvasSnapshot is useCallback([activeTabId, outputCount]) (useCanvasHost.ts:263-283), so ANY consumer holding an old copy reads an old tab id and count, even though its comment says it reads the live source
- refreshCanvasForSend (ChatPage.tsx:500-502) is called from handleSendMessage (2449) and drainQueuedMessage (2655), and neither callback lists it in its deps (2523, 2699), so the send path runs a stale copy that overwrites uiStateRef.canvas right before the request
- getCanvasSnapshot's only production consumer is ChatPage.refreshCanvasForSend (grep: desktop/src, excluding tests)
- A non-active tab's rail lives in sessionStorage under swarm:referenced-files:<tabId> (useReferencedFiles.ts:141-176); that storage is the source of truth for a background tab, so its output count is computable with the same countOutputs predicate
- The per-tab Canvas slice (open/pinned/muted) lives in useCanvasHost.mapRef for every tab, not only the active one

**Alternatives Evaluated:**
- QUALITY — effort: S (1 session)
- SPEED — effort: S
- DELETION — effort: S

**Chosen Approach:** Fix at the producer. useCanvasHost.getCanvasSnapshot becomes a stable function (useCallback with no changing deps) that reads the active tab id and output count from a ref synced in a layout effect after every commit, and takes an optional tabId: for the active tab it uses the live slice in mapRef plus the live count; for another tab it uses that tab's slice in mapRef plus the count of its stored rail rows (new useReferencedFiles.countStoredOutputs). ChatPage.refreshCanvasForSend takes the sending tab id and depends only on the stable getCanvasSnapshot; handleSendMessage passes the active tab id, drainQueuedMessage passes the drained tab id.

**Success Criteria:**
- new tests pass and fail on the old useCanvasHost
- existing useCanvasHost suites stay green
- tsc passes
- commit diff for ChatPage.tsx contains only this run's hunk

**Always:**
- commit only this run's ChatPage.tsx hunk (swarmws-60 owns other uncommitted hunks)
- keep the CanvasSnapshot wire shape unchanged

**Never:**
- touch other sessions' hunks
- add a dependency that rebuilds the send callbacks every render


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Profile bugfix: clear bug with a code-traced, observation-ba | mechanical | evaluate.md profile rule: clear bug, known root ca |
| think | Fix at the producer (useCanvasHost) instead of patching the  | judgment | removes the stale-closure class for every consumer |
| think | Drained background tab reports its own Canvas snapshot (slic | taste | the tab's own Canvas is what that session controls |
| plan | New test file instead of extending useCanvasHost.test.ts | taste | keeps the regression scenario readable on its own; |
| plan | No required experts | mechanical | no endpoint, async protocol or state machine chang |
| build | Adopted all three Gate-1 WARN actions: empty id -> null, dra | mechanical | each closes a concrete way AC3 could silently brea |
| build | Active-tab count still comes from the committed render (live | taste | the active tab's in-memory rows can be ahead of st |
| build | Tech debt kept: other uiStateRef fields (open file, active o | taste | those are global UI facts, not per-tab; out of thi |
| review | Closed the untested-wiring finding with a type-level guard ( | judgment | tsc runs in every verify; a missing tab id cannot  |
| test | Fixed the one tsc error the regression run found (activeTabI | mechanical | undefined and null both mean no active tab and sha |
| deliver | Both send paths build editorContext through one helper and n | judgment | one rule for every send site; a background tab's C |
| deliver | Count from storage for every tab id, live count only for no  | judgment | storage is updated in the same update as the rows  |
| deliver | Accepted the sessionStorage quota edge | taste | rows capped at 100; quota failure already breaks t |

**Summary:** 4 mechanical, 5 taste, 4 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**2 found · 2 fixed · 0 open**
- [LOW] desktop/src/hooks/useCanvasHost.ts getCanvasSnapshotFor: right after a tab switc
- [LOW] desktop/src/hooks/useReferencedFiles.ts saveToStorage swallows a quota error, so

### 7.6 Completion Audit
**Requirements met:** 2/2
**All green:** yes


## 8. Files Changed
- `desktop/src/hooks/useCanvasHost.ts`
- `desktop/src/hooks/useReferencedFiles.ts`
- `desktop/src/pages/ChatPage.tsx`
- `desktop/src/hooks/__tests__/useCanvasHost.sendSnapshot.test.ts`

## 9. Lessons
- [pitfall] A memoized React callback that calls a function from a non-memoized hook return object, without listing it in its deps, runs a stale copy forever: here the send path reported another tab's Canvas (2 outputs for a 23-row rail). Make such getters stable and ref-backed at the producer so no consumer can hold a stale one, instead of patching each consumer's deps.
- [principle] When a getter's correct answer depends on WHICH entity it is for, take that entity as a required argument: an omitted argument becomes a compile error, which guards the call site wiring that a hook-level test cannot see.
- [pitfall] A test that always reads result.current.fn() can never catch a stale-closure bug; the regression test must capture the function BEFORE the state change and call the captured copy afterwards.


## 10. Known Gaps & Attention Flags
- Not deployed: frontend change needs a desktop rebuild
- Known limit: open-file / overlay fields of a drained background-tab message still describe the foreground view


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 06:14 UTC*
