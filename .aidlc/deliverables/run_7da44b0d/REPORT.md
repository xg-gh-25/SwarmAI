# Autonomous Pipeline Report: Context usage: backend single authority for the chat-input ring

**Run ID:** run_7da44b0d | **Project:** SwarmAI | **Profile:** full
**Date:** 2026-10-18 04:49 UTC | **Duration:** 633.5 min (in progress) | **Tokens:** 3,485,198 (measured, $57.17, cache read 119,173,492; 不含当前一轮 / excludes the turn in flight; 含建 run 前的同一轮 / includes the pre-run part of the creating turn)
**Gate 1:** WARN | **Files:** 21 | **Tests:** 615 run, 0 failed

## TL;DR
Context usage backend single-authority: the chat-input ContextUsageRing must reflect the REAL backend context state with the backend as sole authority. Today context usage is a one-shot context_warnin

## 1. Requirement
Context usage backend single-authority: the chat-input ContextUsageRing must reflect the REAL backend context state with the backend as sole authority. Today context usage is a one-shot context_warning SSE event emitted only on ResultMessage and held only in frontend memory (tabState.contextWarning) -> (1) ring frozen across whole multi-call turns though per-call main-agent usage is captured at streaming_orchestrator _last_call_usage; (2) empty after tab restore / app restart (no read API); (3) cleared to null after compact; (4) stale/lying after true cold resume. Make SessionUnit own a context_usage snapshot updated per main-agent API call, derive it from the CLI transcript when no live subprocess (no new persisted copy), expose a read API + streaming-state field, push the backend snapshot via SSE, frontend becomes a pure view that reads the API on tab activate/restore. Constraints: no chat performance regression (no transcript I/O on the streaming hot path), reuse existing logic, no surface patch.

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> 起 pipeline：context 用量后端单一权威

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | user-story | user-stated: XG: 它应该反映我们真实后端的情况 你不应该依赖前端去刷新; 还有这个tab 恢复bug 一起想想 | confirmed | ● | Given a turn with N main-agent API calls, when each call completes, then the ring shows that call's usage from the backend snapshot |
| FR-2 | user-story | user-stated: XG: 它应该反映我们真实后端的情况 你不应该依赖前端去刷新; 还有这个tab 恢复bug 一起想想 | confirmed | ● | Given a session with an existing CLI transcript and the app restarted, when the tab becomes active, then the ring shows the transcript-derived value without blocking render |
| FR-3 | user-story | assumption: XG: 它应该反映我们真实后端的情况 你不应该依赖前端去刷新; 还有这个tab 恢复bug 一起想想 | assumed | ● | Given a compact_boundary with post_tokens, when context_compacted arrives, then the ring shows post_tokens, not empty |
| FR-4 | user-story | assumption: XG: 它应该反映我们真实后端的情况 你不应该依赖前端去刷新; 还有这个tab 恢复bug 一起想想 | assumed | ● | Given a session whose identity was cleared, when the API is read, then source=none and the ring is empty |
| FR-5 | user-story | user-stated: XG: 它应该反映我们真实后端的情况 你不应该依赖前端去刷新; 还有这个tab 恢复bug 一起想想 | confirmed | ● | Given streaming a turn, when snapshots update, then no file I/O occurs in the stream loop |

### 澄清问答 (Clarifications — who decided what)
_(无澄清记录 / no clarifications)_

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
- [ASSUMPTION] FR-3: After compaction the ring shows the post-compact size reported by the CLI
- [ASSUMPTION] FR-4: When the next spawn would rebuild context (no sdk_session_id/transcript), the ring shows no value instead of a stale number

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 8.00 | |
| Priority | 8.00 | |
| Feasibility | 7.00 | |

**Acceptance Criteria:**
1. AC1: During a multi-call turn the ring updates after each main-agent API call (sub-agent calls excluded), sourced from the backend snapshot
2. AC2: On context_compacted the ring shows the backend-reported post-compact size (compact_boundary post_tokens), never null
3. AC3: GET /api/chat/sessions/{id}/context-usage returns {tokens,window,pct,level,source,measured_at}: live unit -> source=live; no live data but sdk_session_id + transcript -> source=transcript (last main-chain non-zero usage, or post_tokens if a compact_boundary follows it); no sdk id/transcript -> source=none
4. AC4: After app restart / tab hydrate / tab switch the active tab's ring reflects the API value without blocking render; background tabs read on activation
5. AC5: Identity change (clear_identity / refresh / new sdk session) invalidates the snapshot; never shows a value measured under another sdk_session_id
6. AC6: Critical toast fires only on level transition into critical, not per snapshot
7. AC7: No transcript I/O on the streaming hot path; transcript read only in the read API, off the event loop, reading backwards from EOF
8. AC8: A newer snapshot is never overwritten by an older one (measured_at ordering) on the frontend


## 3. Design & Approach
**Research Findings (THINK):**
- Per-call main-agent usage already reaches SessionUnit (_last_call_usage, streaming_orchestrator.py:1670, sub-agents excluded by parent_tool_use_id) but is consumed only at turn end (session_unit.py:2965).
- The CLI emits one AssistantMessage per content block with identical usage for the same API call (transcript rows share message.id + usage) -> per-call emission must dedupe on token value.
- compact_boundary already reaches the orchestrator live (_compaction_events, streaming_orchestrator.py:732) with compact_metadata.post_tokens; transcript rows carry compactMetadata.postTokens (489748->16120 observed). The frontend currently throws this away by nulling contextWarning on context_compacted.
- Session identity (_sdk_session_id) is cleared at ~10 sites (clear_identity=True in session_unit/retry_manager, refresh_context). Invalidating a cache at each site would be the 'every entrance must remember' anti-pattern; keying the snapshot to the sdk_session_id it was measured under makes a stale read structurally impossible.
- Without a unit, session_id -> sdk_session_id comes from SessionRouter._persisted_sdk_ids (session_state_persistence); leak_transcript.find_transcript(sdk_id) already locates + validates the transcript path (UUID regex, no glob injection).
- Transcripts reach ~30MB; usage rows are near EOF -> read backwards in chunks, off the event loop (asyncio.to_thread); zero-usage synthetic assistant rows exist right after compaction and must be skipped; sidechain rows excluded.
- Frontend ring color thresholds (60/80, ContextUsageRing.tsx:16) disagree with backend level thresholds (70/85, prompt_builder.py:423) -> two authorities for 'is this critical'.
- Critical toast effect (ChatPage.tsx:1763) re-adds on every contextWarning object change; with per-call snapshots a dismissed toast would reappear every API call.

**Alternatives Evaluated:**
- QUALITY — effort: M
- SIMPLICITY — effort: S
- SPEED — effort: S

**Chosen Approach:** One backend authority. New pure module backend/core/context_usage.py builds the snapshot (reusing PromptBuilder.build_context_warning) and derives it from a CLI transcript (backward chunked read, main-chain only, zero-usage rows skipped, compact_boundary postTokens honoured). SessionUnit stores the snapshot stamped with the sdk_session_id it was measured under (_record_context_usage / _record_compact_boundary / context_usage_snapshot); a snapshot whose stamp != current _sdk_session_id is invisible, so identity clears need no invalidation calls. The orchestrator records + yields a context_warning snapshot after every main-agent AssistantMessage (dedup on token value) and on compact_boundary; compact() records the boundary seen in its own drain; _emit_post_stream_metadata records/re-emits at turn end. GET /api/chat/sessions/{id}/context-usage returns the live snapshot or the transcript-derived one (asyncio.to_thread) or a source=none snapshot. Frontend: pure helpers in desktop/src/hooks/contextUsage.ts (toContextWarning, isNewer, a typed stale-notifier); the lifecycle hook applies every snapshot measuredAt-ordered (SSE and GET share one apply path), stops nulling on context_compacted, exposes refreshContextUsage(tabId); ChatPage refreshes on (activeTabId, sessionId) change and after refresh; chatService.compactSession/refreshSession notify staleness; critical toast fires on level transition only; ring colour comes from the backend level.

**Success Criteria:**
- new backend test file green
- new frontend test file green
- existing compaction/context-warning suites green
- mode:verify green

**Always:**
- reuse PromptBuilder.build_context_warning for level/pct/message
- keep the context_warning event type
- run transcript I/O via asyncio.to_thread only in the GET handler

**Never:**
- read transcripts on the streaming hot path
- persist a second copy of context usage
- write to or modify CLI transcripts
- block tab render on the GET


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Profile full, work_type existing-feature | mechanical | Bounded cross-module feature with known approach;  |
| evaluate | TSCC system_prompt_metadata (in-memory registry) out of scop | taste | It already has a backend read API (tscc.py:113); e |
| evaluate | CompactionGuard keeps its turn-level feed; it becomes a cons | taste | Guard's drop-heuristic needs turn-granularity prev |
| think | Key the snapshot to the sdk_session_id it was measured under | judgment | Makes stale reads structurally impossible; avoids  |
| think | Reuse the existing context_warning event type/shape, extende | taste | All 4 frontend consumers already read that shape;  |
| think | Use AssistantMessage usage (verified) rather than StreamEven | taste | message_start would update ~TTFT earlier but its u |
| think | Prompt-too-large synthetic 100% banner stays frontend-side | taste | It reports a rejected request (an error state), no |
| plan | Do not add context usage to streaming-state | taste | It is polled only while streaming and only lists l |
| plan | Staleness after manual compact/refresh signalled by a typed  | taste | Testable both halves; avoids the dead-decoupled-ev |
| plan | Update useChatStreamingLifecycle.test.ts context_compacted c | judgment | The spec deliberately changes: clearing on compact |
| plan | compact() drain records compact_boundary | mechanical | Manual compact bypasses the SSE stream; without it |
| build | Changed test_context_warning_bridge exact-equality assertion | judgment | The event deliberately became a superset (snapshot |
| build | Updated two useChatStreamingLifecycle tests and ContextUsage | judgment | Clearing the ring on compaction and frontend pct c |
| build | Critical toast moved from a ChatPage effect into the hook's  | taste | The transition needs the per-tab previous level, w |
| build | VERIFY/regression sweeps hit the deadline because the shared | taste | 2-run cap reached; the only failures in the sweeps |
| review | Rebuilt the review/commit patch by replaying anchored edits  | mechanical | Other sessions' uncommitted hunks shift line numbe |
| review | Declined CQ4 (2-line parse dedupe), CQ5 (private field read) | taste | Matches existing router style; a transcript value  |
| review | Critical toast scoped to the active tab and dismissed on lea | taste | The old ChatPage effect was active-tab only; a sta |
| test | Bound the SSE/GET wire to one committed JSON fixture asserte | taste | Cross-language contract: divergence on either side |
| test | Completed regression coverage with targeted tdd runs instead | taste | The sweep's scope and the machine load (parallel p |
| deliver | Committed only this run's hunks in the 5 shared files by swa | judgment | run-commit stages whole files; peers confirmed own |
| deliver | Toast became a projection of the active tab's level instead  | judgment | Three experts showed per-tab-history keying breaks |
| deliver | Fixed the bridge suite's flaky teardown hang in the test (st | taste | Mechanism is test-infra (Hypothesis examples x aut |

**Summary:** 3 mechanical, 14 taste, 6 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**13 found · 13 fixed · 0 open**
- [MEDIUM] session_unit._emit_post_stream_metadata: turn-end turn_average/last_call overwro
- [MEDIUM] routers/chat.py get_context_usage_endpoint: derived/none answer stamped in the w
- [MEDIUM] routers/chat.py: transcript I/O on asyncio.to_thread (shared default pool, O006/
- [MEDIUM] useChatStreamingLifecycle: critical toast keyed on per-tab history (background c
- [MEDIUM] Out-of-stream backend changes (identity dropped on crash/give-up, background com

### 7.6 Completion Audit
**Requirements met:** 9/9
**All green:** yes


## 8. Files Changed
- `backend/core/context_usage.py`
- `backend/core/session_unit.py`
- `backend/core/streaming_orchestrator.py`
- `backend/core/session_router.py`
- `backend/routers/chat.py`
- `backend/tests/test_context_usage_authority.py`
- `backend/tests/test_context_warning_bridge.py`
- `desktop/src/hooks/contextUsage.ts`
- `desktop/src/hooks/useChatStreamingLifecycle.ts`
- `desktop/src/hooks/useUnifiedTabState.ts`
- `desktop/src/services/chat.ts`
- `desktop/src/pages/ChatPage.tsx`
- `desktop/src/pages/chat/components/ChatInput.tsx`
- `desktop/src/pages/chat/components/ContextUsageRing.tsx`
- `desktop/src/__tests__/contextUsage.test.ts`
- `desktop/src/__tests__/useChatStreamingLifecycle.test.ts`
- `desktop/src/pages/chat/components/__tests__/ContextUsageRing.preservation.test.tsx`
- `backend/tests/test_context_usage_wire_contract.py`
- `desktop/src/__tests__/contextUsage.wire.test.ts`
- `desktop/src/__tests__/fixtures/context-usage-snapshots.json`
- `desktop/src/types/index.ts`

## 9. Lessons
- [principle] A UI value that comes from a one-shot event and lives only in frontend state is structurally stale: any period with no event (a multi-call turn, an app restart, a tab hydrate, a compaction that nulls it) freezes it or blanks it. The fix is ownership, not more refresh triggers: the backend keeps the value as a snapshot, pushes it whenever it changes, and serves it on a read endpoint; the frontend becomes a pure view that keeps the newest snapshot by measuredAt.
- [decision] Stamp a cached derived value with the identity it was measured under (here the sdk session id), and treat it as absent when the identity no longer matches, instead of invalidating it at every place that clears the identity. That removes the need to find and hook about ten clear sites, which a future edit would silently miss.
- [pitfall] A read endpoint that stamps its answer with the source row's own timestamp can lose to an older push the client already holds, so the UI rolls back. Stamp the answer with the request's arrival time, and re-check the live value after the slow read so a value recorded during the read still wins.
- [pitfall] A notification keyed to per-tab transition history (fire on entering critical) breaks on tab switches: a background tab goes critical unseen, or a dismissed toast never comes back. Make the notification a projection of the ACTIVE tab's current level, with a per-tab episode flag, so it is decidable and testable.
- [process] To commit one run's hunks from files that parallel sessions also edit, when the commit tool stages whole files: rebuild HEAD plus only your edits by anchored text replay (never a -U0 patch, which misplaces hunks on a moved base), confirm hunk ownership with the peers and get a write freeze, swap your version in, commit, restore their bytes, cmp-verify the restore, and diff the commit against your own patch.
- [pitfall] The pipeline validator reads evidence through a 2 MB size cap and returns None for anything bigger. A full verify report is about 4 MB, so it is judged 'no latest-verify.json evidence': an unreadable store is reported as an empty one, and the exit criterion reads as never run instead of as too large to read.


## 10. Known Gaps & Attention Flags
None identified.


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 04:49 UTC*
