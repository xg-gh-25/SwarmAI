# Design: Desktop Tab 冷启动预热(Prewarm)

> 状态:DESIGN(未进 pipeline)。涉及 chat 核心(session 生命周期 + 前后端契约),
> 按 XG 要求做**完整设计**,非补丁。作者:Swarm，2026-08-16。

## 0. 问题(已 dive-deep 证实)

- Cold turn(新 tab 首消息)TTFT ~28–43s,其中 **8–14s 是 `SessionUnit._spawn` 的
  `wrapper.__aenter__()`**:CLI 子进程 fork/exec + SDK `initialize` 握手 + 首轮 Bedrock 往返。
- 实测 clean 空目录同样 ~14s → **结构性成本,压不掉**。唯一杠杆:在用户发消息**之前**
  spawn 好子进程,把这 14s 从可感知路径移走。
- Warm turn 已好(p50 ~11s,纯 Opus thinking),不动。
- channel 已有通用预热设施(`prewarm_channel_session` + `adopt_prewarmed_unit`),
  desktop 从未接入 —— 本设计就是安全地接上。

## 1. 已确认的机制事实(设计地基,逐条读源码验证)

| # | 事实 | 源 | 对设计的含义 |
|---|------|----|------------|
| F1 | `_spawn_lock` 是**模块级全局串行锁**,持有范围 = `_configure_claude_environment` + `wrapper.__aenter__()`(那 8-14s 全在锁内) | session_unit.py:127,3061 | 🔴 **头号约束**:预热 spawn 会独占锁 8-14s,期间**真实 tab 的 spawn 被阻塞**。这是 env 进程级隔离的必要保护(MEMORY 有记),**不能去掉串行**,只能确保预热不与真实 spawn 抢锁 |
| F1a | **为什么必须全局串行**:`_configure_claude_environment` 在 spawn 前往**进程级唯一的 `os.environ`** 写认证/区域/model 配置(`AWS_BEARER_TOKEN_BEDROCK`、`AWS_REGION`、`ANTHROPIC_BASE_URL`、`SWARMAI_OWNER_PID`…),SDK 在 spawn 子进程那一刻读 `os.environ` 传给子进程。若两 spawn 并发:A 写完 token 未 spawn,B 覆盖成自己的 → **A 的子进程拿到 B 的配置 = 串 session/串 tab** | claude_environment.py:33-101,124 | 这把锁是 SELF.md「cross-tab 隔离结构性不可能」的物理地基之一。可优化的只有"缩短临界区",而临界区那 8-14s 是 SDK `__aenter__` 握手、无法缩短 → **唯一出路是让预热不抢这把锁** |
| F2 | prewarm 机制**通用**:`prewarm_channel_session` 唯一 channel 专属是 `channel_context`(Slack 安全规则),desktop 传 `None` 即可;`adopt_prewarmed_unit` 完全通用,`_slot_lock` 下原子改 key,状态非 IDLE 则拒绝 | session_router.py:1590,1666 | 直接复用,新代码集中在"触发+携带+认领" |
| F3 | 新 tab 的 system prompt = **全量、无 resume**,与 prewarm 子进程的 prompt **一致** | session_router.py:2469(is_cold_resume) | ✅ 新 tab 首消息可安全 adopt;已有 tab 的 cold-resume(Mechanism B 注入历史)**prompt 不一致,绝不能认领预热池** |
| F4 | SDK **锁 model per-session**(spawn 后不可改) | gateway.py:742 注释 | 预热单元必须记录 model,adopt 前校验一致 |
| F5 | orphan 判定:IDLE + 不在 `open_tabs.json` + idle>`ORPHAN_GRACE_SECONDS`(600s) → reap;**fail-safe**:open_tabs 读不到则啥都不 reap | lifecycle_manager.py:_check_orphan_sessions | 🔴 预热单元用 `prewarm-` 前缀 id,**永远不在 open_tabs 里** → 会被误判 orphan。必须给独立、更短的 TTL,且从 orphan reaper 豁免 |
| F6 | 前端 tab 有**两个 id**:`tabId`(客户端 `createDefaultTab` 立即生成)+ `sessionId`(后端首消息流里才生成) | useUnifiedTabState.ts:542;chat.py:563 | 枢纽:预热在 `tabId` 存在时即可发起,预热单元绑到 tabId,首消息 adopt 时映射成 sessionId |
| F7 | `closeTab` 前端纯操作(abort stream + clear interval),**不发后端调用**;真正的后端 `release_session` 由 `ChatPage.handleTabClose` 发(R6b) | useUnifiedTabState.ts:558,577 | close 时必须显式 release 预热单元,否则变 orphan 等 TTL |
| F8 | `release_session`:IDLE unit → kill + `_release_session_state` | session_router.py:2914 | 复用它释放未被 adopt 的预热单元 |
| F9 | spawn 由 `spawn_budget(real RAM)` **无条件门控**;预热也走 `_ensure_spawned` → 同样过门 | session_router.py:1818 | 预热必须 best-effort:预算不足静默跳过,绝不阻塞/抢占真实 session |
| F10 | 每 chat tab 是 MessageStore single-writer,SSE 流隔离,cross-tab eviction 结构性不可能(orphan-only) | SELF.md | 预热**绝不能**碰任何真实 tab 的 store/session;只在自己的 prewarm-id 单元上操作 |

## 2. 设计决策:方案 B(信号驱动) + A(兜底) 组合

XG 已定:**A 兜底 + B 主路径**。

- **B(主)**:**`addTab` 成功那一刻即预热**一个专属该 tab 的子进程(不等 input focus / 首次
  按键),首消息 adopt。命中"开 tab→打字"整个窗口,窗口最长、命中率最高。
  - **触发时机已定 = `addTab`(XG 拍板)**:"开 tab 不打字也预热"**不是浪费,这正是 prewarm
    的定义** —— 提前把子进程备好等着用,就是它的全部价值。若改成"首次按键才预热",窗口被
    压缩到打字后的一两秒,命中率反而低,失去 prewarm 的意义。开 tab 不发消息的极端情况由
    60s TTL 兜底回收(§3 Q4 防线 3),浪费上界 = 一个子进程活 60s,可忽略。
- **A(兜底)**:daemon 维持**至多 2 个** desktop 预热单元(无 tab 归属的"通用池"),
  覆盖 B 来不及 / daemon 刚重启 / app 启动首个 default tab / 用户连开多 tab 的场景。
  - **池深 = 2(XG 拍板)**:理由 —— "现在不怎么吃内存,只要 manage 好、别泄漏就都好办"。
    2 个覆盖"连开两 tab"的常见突发,内存代价约 2 × 子进程(实测冷启动子进程 RSS,
    量级几百 MB;上线用 `spawn_budget` 门控,预算不足时池自动缩到能承受的深度)。
  - **池深是常量 `PREWARM_POOL_TARGET=2`**,不是无界池:被 adopt 走 1 个 → 异步补 1 个,
    始终 ≤2;`spawn_budget` 是**无条件上门**(F9),预算紧时补不进就维持更少,绝不硬撑。

**为什么不是纯池(方案 C)**:常驻 N 个空闲子进程各吃几百 MB,冲突 spawn_budget 与
资源健康,过度工程。A 的"至多 1 个"是 C 的最小安全特例。

## 3. 回答 XG 的每一个问题

### Q1: B 会阻碍用户 input 吗?
**不会 —— 但前提是解决 F1 的锁竞争,这是本设计的核心安全点。**
- 预热是**后台异步任务**,前端 input 框永远不等它;用户随时可打字、发送。
- 真正的风险不是"挡住打字",而是 **F1 锁竞争**:若预热 spawn 正持有 `_spawn_lock`(8-14s),
  此时用户在**另一个** tab 发首消息,那个真实 spawn 要排队等锁 → 真实 TTFT 反而 **变慢**。
- **解法(必须做,否则预热是负优化)**:
  - **B1 预热单元 spawn 让位真实 spawn**:引入一个轻量优先级 —— 真实 spawn 请求到达时,
    若预热 spawn 尚未进入锁临界区,则真实优先拿锁。实现:预热获取锁用 `_spawn_lock`
    的一个"可被真实请求抢先"的包装(真实 spawn 计数 >0 时,预热 `await` 让步)。
  - **B2 全局至多 1 个在途预热 spawn**:一个 `asyncio.Semaphore(1)`(独立于 _spawn_lock)
    限制"同时只有一个预热在 spawn",避免多 tab 同开时堆叠预热把锁占满。
  - **净效果**:预热永远是"锁空闲时才做的填充",真实 spawn 永远优先。最坏情况退化为
    "预热没来得及,首消息走正常 cold 路径"—— 即今天的行为,**不会比现状差**。
- **让位退化会频繁吗?—— 不会,实测 ~10%(spawn_perf 埋点,08-15~16,83 次真实 spawn)**:
  | 指标 | 数值 | 含义 |
  |------|------|------|
  | 相邻 spawn 间隔中位数 | **293s(~5min)** | 绝大多数 spawn 孤立,锁空闲,不会撞 |
  | 实际等锁 >100ms | **9.6%(8/83)** | 今天已有 ~10% spawn 在互相等锁(既存痛点,与预热无关) |
  | 时间窗重叠的并发 spawn | **9.6%** | 真正并发 spawn 仅 ~10% |
  | 间隔 <15s(预热窗口内可能撞) | **9.8%** | 与上一致 |
  - **90% 情况**:预热独占锁、零竞争 → 首消息 TTFT 从 ~14s 降到接近 0。
  - **~10% 情况**(两 spawn 撞一起):让位生效,预热退让,真实 spawn 走 cold = 今天行为,不变差。
  - **净期望 = 大幅改善,零下行风险**。今天那 8 次等锁(中位 8.4s、max 16s)是"两个真实
    spawn 互相排队"的既存现象,让位逻辑只让给真实请求、不加剧它。

### Q2: App start 后第一个 default tab 怎么搞?
- app 启动时前端恢复/创建 default tab(useUnifiedTabState 从 open_tabs.json 恢复或建默认)。
- 由 **方案 A 兜底**覆盖:daemon 的 `lifecycle_manager` 启动完成后,后台预热 1 个通用单元
  (无 tabId 归属)。default tab 的首消息到达时,走 **adopt 优先级**:
  1. 先找该 tabId 的专属预热单元(B,首个 default tab 通常没有,因为前端刚起);
  2. 再找通用池的 1 个兜底单元(A)→ adopt。
- 若两者都没有(daemon 还没热好)→ 正常 cold 路径。**永不阻塞启动**。

### Q3: Click "open new tab" 都能 work 吗?
- 能。`addTab` 生成 tabId 后,前端发 `POST /chat/prewarm {tab_id, agent_id, model}`(fire-and-forget)。
- 后端 best-effort 预热,把 `prewarm_id` 记在 **tabId→prewarm_id 映射**里(见 §4 状态)。
- `addTab` 在 `map.size >= chatMax` 时返回 undefined(已有的 tab 上限保护)——预热信号
  只在 addTab 成功后发,天然不超限。
- 连开多个 tab:每个 tab 各发一次预热信号,但受 **B2 Semaphore(1)** 串行化 + **B1 让位**,
  不会把锁占满;来不及预热的 tab 首消息走 cold(可接受)。

### Q4: Close tab 后怎么管理?怎么避免一堆 orphan?确保资源健康?
**四层防线,确保零 orphan:**
1. **主动 release(前端)**:`ChatPage.handleTabClose` 在关 tab 时,若该 tabId 有未被
   adopt 的 `prewarm_id`,发 `POST /chat/prewarm/cancel {tab_id}` → 后端 `release_session`
   杀掉预热子进程 + 清状态。(复用 F8)
2. **adopt 即转正**:一旦首消息 adopt,预热单元 re-key 成真实 sessionId,进入正常
   tab 生命周期(受 open_tabs.json 归属 + R6b on-close release 管理),不再是预热单元。
3. **预热单元独立 TTL(后端兜底)**:每个预热单元记 `created_at`;lifecycle_manager 每
   60s 扫描,**未被 adopt 且 age > `PREWARM_TTL`(建议 60s)** → kill + 清映射。
   60s 远大于"开 tab→打字"的正常窗口,又远小于 orphan 的 600s,不会误杀在用窗口。
4. **orphan reaper 豁免**:预热单元(prewarm- 前缀)从 `_check_orphan_sessions` **显式豁免**
   (它本就不在 open_tabs,不能按 orphan 逻辑判),改由防线 3 的专属 TTL 回收。避免两套
   回收逻辑打架(P8 一致性)。

**资源健康总账 + 无泄漏保证(XG 的硬条件:"manage 好、别内存泄漏")**:

任一时刻挂起的预热单元有**严格上界**:
```
挂起总数 ≤ (每 tab 至多 1 个 B 单元 × 活 tab 数,上限 = chatMax) + (通用兜底池 ≤ 2)
```
且每一个都受**至少一条回收路径**覆盖 —— 这是"无泄漏"的核心不变式:

> **不变式:每个预热子进程从诞生起就绑定一个必达的死亡路径,四条至少命中一条。**
> ① adopt → 转正常 tab 生命周期(R6b on-close release 接管);
> ② close tab → 主动 cancel → `release_session` kill;
> ③ 未 adopt 且 age>60s → lifecycle_manager 专属 TTL kill;
> ④ daemon 关停 → `_tracked_child_pids` shutdown 清理(所有子进程统一兜底)。

**防泄漏的具体机制(实现时必须逐条落地 + 测)**:
- **池计数用单一权威**:`_prewarm_pool: dict[prewarm_id → unit]` + `tab_id → prewarm_id`
  两个映射,全部 `_slot_lock` 保护;补池/adopt/cancel/TTL-kill **只经这一处增删**
  (single-writer,禁止任何旁路 mutate)—— 计数漂移 = 泄漏源,单点收口。
- **kill 与 map 清理同事务**:任何 kill 预热单元的路径,必须在**同一个 `_slot_lock` 临界区**
  内 pop 掉两个映射 + `_release_session_state`(清 system_prompt_metadata / recall_snapshot
  等 module-level dict,F8 已有),杜绝"进程杀了但 map 残留 / map 删了但进程还在"。
- **补池收敛**:补池是"目标深度 - 当前存活"的**幂等收敛**,不是"每次 adopt +1"的累加
  (避免 adopt 抖动把池撑爆);单次补池也过 B2 Semaphore(1) + spawn_budget。
- **孤儿进程双保险**:预热子进程 spawn 时打 `SWARMAI_OWNER_PID` 标签(F1a 里的既有机制),
  即便 map 意外丢了引用,startup orphan reaper 的 OS 级"无主进程"扫描仍能兜底杀掉。

**结论**:上界有限 + 每单元必达回收 + 计数单点收口 + kill/清理同事务 → **结构上不会累积
orphan、不会内存泄漏**。这正是 XG 那句"manage 好了都好办"的落地形式。

### Q5: 不能引入 race / 串 tab / 串 session
**逐个 race 点分析 + 防线:**

| Race | 场景 | 防线 |
|------|------|------|
| R-a 双 adopt | 同一预热单元被两个请求同时认领 | `adopt_prewarmed_unit` 已在 `_slot_lock` 下 pop-once + 状态校验(F2),TOCTOU 安全 |
| R-b 预热未完成就发消息 | 用户开 tab 立刻发消息,子进程还在 spawn | adopt 时 `state != IDLE` → 返回 False → 走正常 cold(F2),**无死等** |
| R-c close 与 adopt 竞争 | 用户关 tab 的同时首消息在途 | 两者都经 `_slot_lock`;先到者赢:adopt 先→转正常 tab 由 close 的 release 处理;cancel 先→pop 掉,adopt 拿不到→cold。**无泄漏、无双杀** |
| R-d model 不匹配 | 预热用 model X,tab 切到 model Y 后发消息 | adopt 前校验 `unit.model == request.model`,不符则拒绝该预热单元→cold(F4)。**绝不用错 model 的子进程** |
| R-e 串 tab/session | 预热单元被错误 tab 认领 | 预热单元按 **tabId 精确绑定**;通用兜底单元(无 tabId)只在无专属时用,且 adopt 后立即从池移除。map 是 tabId→prewarm_id 单射,**结构上不可能串** |
| R-f 锁竞争拖慢真实 spawn | 见 Q1 | B1 让位 + B2 Semaphore(1) |
| R-g 碰真实 tab store | 预热误写某真实 tab | 预热只操作自己的 prewarm-id 单元,从不 setMessages/碰真实 store(F10);adopt 只 re-key 路由,不动 store(test_eviction_context_loss.py:545 已证 adopt 只碰 _units+_slot_lock) |

**串 tab/session 结构性不可能的根因**:预热单元有独立 id 空间(prewarm- 前缀),
adopt 是**唯一**把它接入真实 session 的路径,且在 `_slot_lock` 下原子完成 id 映射。
没有任何路径能让预热子进程的输出流到非认领它的 tab。

## 4. 需要的状态与契约(实现清单)

### 后端
- `SessionRouter.prewarm_desktop_session(agent_id, model, tab_id)` —— 复用
  `prewarm_channel_session` 的核心(channel_context=None),记录 `model` + `tab_id` +
  `created_at` 到预热单元。受 **B2 Semaphore(1)** + **spawn_budget**(best-effort)。
- `tab_id → prewarm_session_id` 映射(router 上,`_slot_lock` 保护)。
- `adopt` 增强:按 tab_id 找预热单元 → 校验 model → adopt;失败回退 cold。
  兜底通用单元作为 fallback 查找。
- `PREWARM_TTL`(60s)+ lifecycle_manager 专属回收 + orphan reaper 豁免。
- 通用兜底单元:daemon start 后台预热 1 个;被 adopt 后异步补 1 个(至多 1)。

### API
- `POST /chat/prewarm {tab_id, agent_id, model}` → best-effort,返回 202,不阻塞。
- `POST /chat/prewarm/cancel {tab_id}` → release 未 adopt 的预热单元。
- `POST /chat/stream` 增加可选 `tab_id` 字段,run_conversation 首消息据此查 adopt。

### 前端
- `addTab` 成功后 → fire `POST /chat/prewarm`(fire-and-forget,失败静默)。
- 首消息 `chat_stream` 请求带 `tab_id`。
- `handleTabClose` → 若该 tab 有未 adopt 预热 → `POST /chat/prewarm/cancel`。
- **input 永不等待预热**(Q1)。

## 5. 部署与验证(R16 拓扑)

耦合子系统:session_router(spawn/adopt/release)、lifecycle_manager(TTL/orphan)、
chat 路由、前端 tab state。**不是零行为变更**,需:
- 各子系统独立单测(prewarm spawn / adopt / TTL 回收 / orphan 豁免 / cancel)。
- E2E smoke:①开 tab 不发消息 60s → 预热单元被 TTL 回收(无 orphan);②开 tab→发消息
  → adopt 命中,TTFT 显著下降;③开 tab→秒关 → cancel 释放;④两 tab 同开 → 无锁堆叠、
  真实 spawn 不被拖慢;⑤model 切换 → 不误用预热单元;⑥daemon 重启后首 default tab →
  A 兜底命中。
- 关键回归门 1:真实 spawn 的 p50 spawn_perf **不因预热变大**(B1/B2 有效性证明)——
  "预热不是负优化"的硬指标。
- 关键回归门 2(**无泄漏,XG 硬条件**):压测脚本 —— 循环 N 次{开 tab→(随机)发消息/秒关/
  放置 60s},结束后断言 **存活子进程数回落到基线**(仅真实 tab 的 + 池 ≤2)、`_prewarm_pool`
  与 `tab_id→prewarm_id` 两映射清空到预期、无 `SWARMAI_OWNER_PID` 无主进程残留。这是"结构上
  不泄漏"的可执行证明,必须 mutation-proven(去掉任一回收路径 → 该测试 RED)。

## 5b. ⚠️ PE 对抗性审查结论(2026-08-16)—— 结论从 GO 翻转为**重大返工**

一位零上下文 Principal Engineer 审了本文档 + 逐条读源码验证。**我(作者)亲自复核了两个
CRITICAL,均成立**。对抗发现是 LEAD 不是判决,以下每条都已对源码复核。

### 🔴 C1(CRITICAL,已复核成立)—— adopt 的子进程会被 poison-guard 杀掉重生,14s 收益归零
- **证据**:`_last_turn_clean` 初始 `False`(session_unit.py:648),唯一置 True 点是
  一次**完整流式回合**成功(streaming_orchestrator.py:1856)。prewarm 只跑 `_ensure_spawned`
  (spawn+握手),**从不流回合** → 永远 `_last_turn_clean=False`。
- 首消息 `send()` 命中 **poison_guard_recycle**(session_unit.py:1967-1979):
  `IDLE + _client 非空 + not _last_turn_clean` → `_crash_to_cold_async` → COLD →
  **重新 spawn 14s**。`test_session_unit_cleanliness.py:235` 正断言此行为。
- **净效果:预热白做,还多一次 kill+spawn = 比现状略慢。主路径失效。**
- **连带**:现有 channel prewarm 走同一路径 → **大概率早已失效,无人量过**。
  "复用已验证的 channel 地基"这个前提**不成立**。
- **修复前置**:必须先解决"adopt/fresh-never-streamed 的 unit 如何绕过 poison-guard"
  (不能简单置 `_last_turn_clean=True`——要证明不重开 run_ed9647c5 的 zombie bug)。

### 🔴 C2(CRITICAL,已复核成立)—— F3"prompt 一致"不成立,adopt 牺牲 recall/SENSE
- **证据**:真实首消息的 `options.system_prompt` 含**首消息时刻才注入**的内容:
  `_maybe_inject_recall`(session_router.py:2669)按用户**首消息文本**追加 `## Recalled
  Knowledge`;UI-state/open-file SENSE 段(:2576);分钟级 datetime tail(system_prompt.py:300)。
  这些**只在 spawn 那刻随 prompt 固化进子进程**(session_unit.py:3091)。prewarm 时无用户
  消息 → 不可能有 recall 块。
- **后果**:若 adopt 已 spawn 的子进程,用户得到一个 **prompt 里缺少针对他这条消息 recall
  出的知识 + 缺当前 UI 上下文**的子进程 → 回答变笨,且**不可观测**(无报错)。
- **F3"无 resume 一致"成立,但"prompt 一致"不成立**——recall/SENSE 是首消息才有的。
- **C1+C2 是连体缺陷**:只要"adopt 复用已 spawn 子进程",就同时踩两个——子进程 prompt
  已固化,首消息才生成的 recall/SENSE 注不进去。这直接击穿 §3-Q1 的"零下行风险"。

### 其它已采纳的发现
- **H1**:B1"让位"在 `asyncio.Lock`(无优先级)上**无法保证真实永远优先**——已持锁的预热
  那 14s 抢不了;让步计数器有 TOCTOU + starvation 风险。§3-Q1 措辞需从"真实永远优先/零下行"
  改为"~90% 不撞,撞上真实等最多 14s"。
- **H2**:回收不变式**漏了第五条路径 `_evict_idle`**(预算紧时真实 spawn 会 evict 预热单元,
  但它只 `victim.kill()`、不清新增的 `_prewarm_pool`/`tab_id→prewarm_id` map → 进程杀了 map 残留
  = 泄漏)。修复:给 SessionUnit 加 `is_prewarm` + `on_kill` 回调,让**所有** kill 路径统一收口,
  而非在已知调用点各写一遍。
- **H3**:防线4"orphan reaper 豁免"是**待写新代码**(现 `_check_orphan_sessions` 无 prewarm 豁免),
  不是既有事实的复用。
- **M1**:`SWARMAI_OWNER_PID` 双保险**只兜 daemon 崩溃重启**,对"运行期 map 丢引用"无效
  (活 daemon 的孩子 ppid 未变 → 不判 orphan)。运行期防泄漏只能靠 single-writer + kill/map 同事务。
- **M2**:adopt **只校验 model 不够,必须同时校验 `agent_id`**——多 agent 场景下 adopt 到错
  agent 的整个 system prompt(人格/工具/权限全错)比 model 不匹配严重得多。通用兜底池需按
  agent_id 分桶或仅服务同 agent 的 tab。
- **M3**:收益估算缺关键数据——**"addTab→首消息"间隔分布未知**。预热需 >14s 才 spawn 完;
  用户开 tab 常常是"立刻要问",间隔大概率 <14s → 命中率可能远低于 90%。**进 pipeline 前
  必须先埋这个点**。
- **M4(方案定位翻转)**:**A 才该是主路径,B 是增量**。方案 A(daemon 池深 2)天然覆盖
  "用户还没打字"的高价值窗口(app 启动首 tab / daemon 重启 / 连开 tab)——这些窗口 >14s、
  命中率高,且**不需要 tab_id 映射/cancel 端点/前端 addTab 接线/B1 让位**,回收也只有池计数
  一个 single-writer。B 的增量价值(覆盖"开 tab 后 >14s 才打字")需 M3 数据支撑。

### 修正后的推进顺序(取代原 §6 的"3 run 全套 A+B")
1. **先量现有 channel prewarm 到底省没省时间**(写测试,预期结论:因 C1 = 0 省)。
2. **修 poison-guard 对 fresh-never-streamed unit 的误判**(C1),证明不重开 zombie bug。
3. **补"addTab→首消息间隔"埋点**(M3),拿真实命中窗口数据。
4. **只做方案 A(池深 2)+ agent_id 校验 + H2 统一 kill 收口**,量真实 TTFT 改善。
5. **有数据再决定 B 值不值得**(C2 决定 B 是否要接受"牺牲首消息 recall"或干脆不适用)。

### "结构性不可能"断言复核
- ✅ **成立**:F1/F1a(全局串行锁必要性)、R-a(双 adopt TOCTOU 安全)、R-g(不碰真实 store)、
  F6/F7(前端两 id)、F9(spawn_budget 无条件门控)。
- ❌ **站不住**:F2("复用即得 warm 子进程"——被 C1 击穿)、F3("prompt 一致"——C2)、
  §3-Q1("真实永远优先/零下行"——H1)、§3 四条回收不变式("四条至少命中一条"——漏 H2 第五条)。

## 6. Profile 建议(⚠️ 已被 §5b 修正 —— 见上,不再是全套 A+B)
- 走 **full 或 goal pipeline**(涉及 session 生命周期 + 前后端契约 + 并发安全)。
- 分解:①后端预热+adopt+TTL+orphan豁免(1 run)→ ②API+前端接线(1 run)→
  ③锁让位/Semaphore 并发安全(1 run,最需对抗审查)。或一个 goal run 带 DoD。
- 由 pipeline 在 EVALUATE 定 profile(R1)。
