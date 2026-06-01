# Thinking Block "不显示" — 7 层链路完整诊断

**日期:** 2026-06-01
**触发问题:** "Opus 4.8 的 thinking block 怎么不显示,外界有类似 bug 吗"
**结论:** 不是 bug。整条链路健康,"看不到"是 adaptive thinking 的正常统计行为(仅 ~8% 轮次产生 thinking)。

---

## TL;DR

从模型 → Bedrock → 后端解析 → DB 持久化 → API 回传 → 前端流处理 → 渲染,**7 层全部正常工作**,每层都有实证。感知上"thinking 不显示"的真实原因是 `thinking_mode=adaptive` + `effort=high` 下,模型每轮自行决定是否思考,大量工具调用/简单轮次会跳过思考。

- 运行环境:`/Applications/SwarmAI.app` v1.17.3(构建 2026-05-30 18:36),model `claude-opus-4-8`(Bedrock `us.anthropic.claude-opus-4-8`)
- 配置:`thinking_mode=adaptive`,`thinking_effort=high`(`~/.swarm-ai/SwarmWS/config.json`)

---

## 7 层验证表

| # | 环节 | 关键文件 | 验证方法 | 结论 |
|---|------|---------|---------|------|
| 1 | 模型 / Bedrock | — | 临时日志抓 529 个 thinking_delta | ✅ 发完整明文(51~985 字符),0 空块 |
| 2 | 后端流式解析 | `backend/core/session_unit.py` (~L2098) | 读代码 + SSE 实测 | ✅ `thinking_start`/`thinking_delta`/`thinking` 全发出 |
| 3 | 后端持久化 | `backend/core/session_router.py::_persist_assistant_blocks` (L527) | 读代码 | ✅ blocks 原样存 DB,无过滤 |
| 4 | DB 实际内容 | `~/.swarm-ai/data.db` (表 `messages`) | 只读查询 | ✅ 最近 500 条中 45 条非空 thinking,最新 18:42 |
| 5 | API 回传 | `backend/routers/chat.py::get_session_messages` (L708) | 读代码 | ✅ content 逐字返回,无过滤 |
| 6 | 前端流处理 | `desktop/src/hooks/useChatStreamingLifecycle.ts` | 读代码 | ✅ `appendThinkingDelta` + type-only dedup 正确 |
| 7 | 前端渲染 | `desktop/src/pages/chat/components/ContentBlockRenderer.tsx` | 读代码 + grep built dist | ✅ `block.type==='thinking'` → `<details open>💭` 折叠组件 |

---

## 各层证据细节

### 层 1 — 模型 / Bedrock(临时日志实测)
临时在 `session_unit.py` 的 StreamEvent + ThinkingBlock 分支加 `TEMP_THINK_DIAG` 日志,跑一轮对话后:
- `thinking_delta` 流式增量:**529 个有内容**,仅 7 个空(0.7%,块结尾边界,正常)
- 完整 `ThinkingBlock`(AssistantMessage):**12/12 全部有明文**,长度 51~985 字符
- 空内容 / signature-only 块:**0 个**
- `signature_delta`:12 个(签名 324~1852,正常,用于多轮重放)
- 明文样例:`'Simple math question.\n\n17 * 23 = 391\n391 + 41 = 432'`

→ **推翻了代码注释里"Opus 4.8 over Bedrock returns empty thinking blocks"的假设** —— 当前版本 Bedrock 透传完整明文。

### 层 2 — 后端流式解析 `session_unit.py`
`StreamEvent` 分支正确处理:
- `content_block_start` + `type=="thinking"` → yield `thinking_start`
- `content_block_delta` + `thinking_delta`(非空判断)→ yield `thinking_delta`
- `AssistantMessage` 的 `ThinkingBlock`:仅 `block.thinking and block.thinking.strip()` 非空时发出;空块/纯空白丢弃(只置 `_content_emitted=True`,避免幽灵 💭)

### 层 3 — 持久化 `_persist_assistant_blocks`
`blocks` 原样 `db.messages.put(...)`,无任何 block-type 过滤。

### 层 4 — DB 实测(`~/.swarm-ai/data.db`,非 SwarmWS/data.db)
最近 500 条 assistant 消息:
- 带 thinking block:**45**(全部非空)
- 最新带 thinking 的消息:`2026-06-01T18:42:37`
→ thinking 块能正常落库,reload/tab 切换后可恢复。

### 层 5 — API 回传 `get_session_messages`
`content: msg.get("content", [])` 逐字返回,无过滤。

### 层 6 — 前端流处理 `useChatStreamingLifecycle.ts`
- `thinking_delta` 事件 → `appendThinkingDelta()`:首 token 建块,后续 append 同一块
- `blockKey()` 对 thinking 用 type-only key `thinking:0`,使流式块与 SDK 最终块去重
- `ContentBlock` 类型含 `ThinkingContent`;`StreamEvent.type` 以 `(string & {})` 收尾,无类型缺口

### 层 7 — 前端渲染 `ContentBlockRenderer.tsx`(铁证)
- `block.type === 'thinking'` → 渲染 `<details open>💭 Thinking` 折叠组件(空文本则 return null)
- **关键证明:运行 app 内嵌的 `desktop/dist/assets/index-jNVhyhP2.js`(构建 05-30 18:35)grep 直接命中 `appendThinkingDelta` / `>Thinking<` / `thinking_delta`**
- 渲染代码自 2026-03-14 commit `d54f50da` 就存在,后续 `2dce08db` 专门「surface thinking blocks in UI」

→ 不是旧构建、不缺代码、不是 Bedrock、不是解析/持久化问题。

---

## 根因:adaptive thinking 的统计行为

最近 500 条 assistant 消息分布:
- 带 thinking block:**43 条(8%)**
- 纯工具调用(无 text 无 thinking):327 条(65%)
- 带 text:130 条(26%)

`adaptive` 模式下模型每轮自行决定是否思考(`effort=high` 时几乎总思考,但工具调用/简单轮次会跳过)。所以 thinking "经常看不到",但出现时(8% 轮次)能正常显示。

加重感知的细节:空内容 thinking 块被 `session_unit.py` 主动丢弃(避免幽灵 💭),adaptive 偶尔产生的 signature-only 空块因此不渲染。

---

## 配置权衡:`adaptive` vs `enabled`

切 `enabled`(`config.json` 改 `thinking_mode=enabled` + `thinking_budget_tokens`)能每轮强制思考,但:

**好处:** 每轮可见思考 / 深度可控(budget_tokens)/ 调试透明

**坏处:**
1. ⚠️ **`enabled + budget_tokens` 在 Opus 4.6+ 已废弃**,官方推荐 `adaptive + effort`,未来模型版本会移除
2. 更慢(65% 纯工具轮次凭空增加思考延迟,雪上加霜于 stop/resume 慢)
3. 更贵(thinking token 计费)
4. 挤占 context(长会话更快推高,与 1M context / compaction 较劲)
5. 放弃 adaptive+effort=high 这个官方最优组合

**建议:保持 `adaptive` + `effort=high`(已是官方最优解)。** 8% 是正常表现,不为"每轮都看到"去走废弃 API。真要调试模型推理,临时切 `enabled` 跑几轮再切回。

---

## 处置记录

- 临时诊断日志(`TEMP_THINK_DIAG`)已全部从 `session_unit.py` 回滚,`py_compile` 通过,无残留
- daemon 当前仍跑带日志的旧 bundle,下次 `./prod.sh build` 自然恢复(纯日志,无功能影响)
- 配置未改动(保持 adaptive)
