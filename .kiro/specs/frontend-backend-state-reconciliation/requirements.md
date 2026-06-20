# Requirements Document

## Introduction

整晚反复出现的多个症状（resume 分割线、审批框不出现、卡在 THINK 阶段、
"代码改完了但 UI 还在读文件"、"前端好多没 response"）是**同一个结构性根因**的不同
表现，不是几个独立 bug。

**总根因（one-line root cause):** 前端对一个会话的视图，只是**一条临时 SSE 流**的投影。
后端在 SSE 客户端断开后**故意继续干活并把结果写入 DB**（防止丢工作）；但前端在重连时
**不做内容级对账**——它只通过 `GET /chat/sessions/streaming-state`（每 15s）同步
`isStreaming` 旗标，而消息的 DB 重载被一条粗暴 guard 关掉了（`ChatPage.tsx` 中
"tab 内存里只要还有任何消息就跳过 refetch"）。因此**断开窗口内后端产出的所有 response
都在 DB 里，却永远不显示**。同理，落在已拆掉的流上的 `cmd_permission_request` 事件
也不会被重新 surface，于是审批框永远不出现，子进程却傻等最长 2 小时。

这个会话之所以特别严重：它整晚在 **Stop / zombie-kill / cold-resume / daemon 重启**
之间反复横跳，每一次都是一个"SSE 断开、后端继续产出、前端不在听"的窗口。窗口越多，
丢失/不一致越多 —— 这是结构性必然，不是偶发。

**Ground truth evidence (this session):**
- `routers/chat.py:_recover_streaming_on_disconnect` — SSE 断开 → 状态转 IDLE，
  **子进程留着不杀**，输出由 `_persist_assistant_blocks` 写入 DB；注释明说
  "frontend reconciliation polling will recover the content from DB"，但实际没有内容级对账。
- `ChatPage.tsx`（约 473 行）— 仅当 `!isStreaming && messages.length === 0` 才
  `loadSessionMessages()`；只要内存里有消息就 `skipping refetch`。
- `/sessions/streaming-state` 只同步 `isStreaming`，不同步消息内容。
- `permission_manager.py` — pending 请求存在 `_pending_requests`，但**无任何重连补发路径**
  （`get_pending_request` 只被 approve/deny endpoint 消费）。
- `wait_for_permission_decision(timeout=7200)` — 审批门最长阻塞 2 小时。
- `dangerous_commands.json` — `rm -rf *` 把无害的 `/tmp` 清理也拦下审批。

## Glossary

- **Disconnect window** — SSE 流被拆掉到前端重连之间的时间段（Stop、tab 切换、网络抖动、
  zombie-kill、cold-resume、daemon 重启都会产生）。
- **Content reconcile** — 重连时按 message id/timestamp 从 DB 增量合并消息内容到前端。
- **Flag reconcile** — 已有的 15s `/streaming-state` 轮询，只同步 `isStreaming`。

## Requirements

### Requirement 1: Content reconcile on reconnect (核心)

**User Story:** As a user, when an SSE stream drops and reconnects, I want any
responses the backend produced during the disconnect window to appear in the UI,
so that I never see a frozen/stale conversation while the backend has moved ahead.

#### Acceptance Criteria

1. WHEN a tab (re)connects or its stream resumes AND the backend DB holds
   messages newer than the frontend's last-known message for that session,
   THEN the system SHALL fetch and merge those newer messages into the tab.
2. WHEN merging messages from DB, THEN the system SHALL merge by stable message
   id / timestamp (append-or-update), and SHALL NOT re-introduce older history
   the user already scrolled past (the regression the current coarse guard was
   protecting against).
3. WHEN a tab already has in-memory messages AND the backend produced new content
   during a disconnect window, THEN the system SHALL surface that new content
   (the current `messages.length === 0` gate SHALL NOT suppress it).
4. WHEN the tab is actively streaming on a live SSE, THEN the reconcile SHALL NOT
   overwrite in-flight optimistic/streaming content (no double-render, no clobber).
5. WHEN content reconcile runs, THEN it SHALL be idempotent (running twice yields
   the same message list).

### Requirement 2: Re-surface pending permission requests on reconnect

**User Story:** As a user, when a dangerous-command approval is requested while my
stream is detached, I want the approval dialog to appear when I reconnect, so the
turn does not silently block waiting for a decision I was never shown.

#### Acceptance Criteria

1. WHEN a tab (re)connects AND `permission_manager` holds a pending request for
   that session, THEN the system SHALL re-emit / surface a `cmd_permission_request`
   so the dialog renders.
2. WHEN a permission request is enqueued but the active read loop never surfaced it
   (no `session_unit.permission_surfaced` log), THEN reconnect re-surfacing SHALL
   still deliver it.
3. WHEN the same pending request is re-surfaced multiple times, THEN the frontend
   SHALL render exactly one dialog (dedup by requestId).
4. WHEN a pending request has already been resolved, THEN reconnect SHALL NOT
   re-surface it.

### Requirement 3: Bounded, visible permission wait

**User Story:** As a user, I want a missed approval to fail fast and visibly rather
than block a turn for hours.

#### Acceptance Criteria

1. WHEN an interactive session awaits a permission decision, THEN the blocking
   wait SHALL use a bounded timeout (e.g. 300s) rather than the 7200s default.
2. WHEN the wait times out, THEN the system SHALL auto-deny AND emit a visible
   "approval timed out" message, and the turn SHALL NOT hang silently.
3. WHEN refining `wait_for_permission_decision`'s return contract, THEN the
   timeout-vs-deny distinction SHALL be preserved for its single caller
   (`security_hooks.py`). (NOTE: in-flight in a parallel session as "fix #2".)

### Requirement 4: Do not gate benign temp cleanups

**User Story:** As a user, I don't want harmless `/tmp` cleanups to trip the
dangerous-command approval gate.

#### Acceptance Criteria

1. WHEN a command is `rm -rf` scoped to `/tmp/*` or `/var/folders/*` (OS temp),
   THEN the dangerous-command gate SHALL auto-approve without prompting.
2. WHEN a command is `rm -rf` targeting dangerous roots (`/`, `~`, `$HOME`,
   workspace root), THEN the gate SHALL still require approval.
3. The test `test_harmless_tmp_rm_auto_approved_no_prompt` SHALL pass without
   hanging. (NOTE: this test currently FAILS by 60s timeout — the in-flight
   "fix #3" does not yet narrow the pattern; the `/tmp` rm still prompts.)

### Requirement 5: Pipeline / stage display reflects backend truth

**User Story:** As a user, I want the displayed pipeline stage to reflect what the
backend actually did, not a frozen pre-teardown snapshot.

#### Acceptance Criteria

1. WHEN the backend has progressed past the last stage the frontend displayed
   (e.g. THINK shown, BUILD actually completed), THEN reconnect SHALL update the
   displayed stage from backend ground truth.
2. WHEN a turn completed on the backend (content persisted) but the UI still shows
   a running spinner, THEN flag-reconcile + content-reconcile together SHALL clear
   the spinner and show the completed content.

## Out of scope

- Stopping the upstream churn (mid-stream daemon rebuilds, frequent Stop). That is
  operational discipline, tracked separately; this spec makes the UI *resilient*
  to teardown, it does not prevent teardown.
- Per-tool execution timeout / CPU-IO-silent watchdog (separate P1 item).
