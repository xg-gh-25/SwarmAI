# Design v2: Desktop Tab 冷启动预热(Prewarm)—— PE 审查后重做

> 状态:DESIGN v2(取代 v1 `2026-08-16-desktop-tab-prewarm-design.md`)。
> v1 被 PE 对抗审查判"重大返工",两个 CRITICAL 已亲自复核成立 + 查生产日志坐实。
> v2 的地基是**验证过的事实**,不是假设。作者:Swarm,2026-08-16。

> ### ⚠️ BUILD 交付更正(阶段一 run_b21b9a1f,commit `4ec936c0`,2026-08-17)
> 阶段一(P-a + P-b)已实现,但 BUILD 阶段的 Gate-1 对抗审查(SSA)推翻了本文档 §2-G1/§4b/§6/§8
> 的 **P-b 判据**。**以下更正为交付真相,本文档正文的 `_sdk_session_id is not None` 措辞已过时**:
>
> - **P-b 实际判据 = `session_id.startswith(PREWARM_SESSION_PREFIX)`(正向前缀标记),不是
>   `_sdk_session_id is not None`。** 原因(Gate-1 F1,源码坐实):`recover_from_disconnect`
>   (session_router.py:3167)把首消息 SSE 断连的**普通单元**转成 `IDLE + client alive +
>   _last_turn_clean=False + _sdk_session_id=None` —— 与 fresh prewarm 单元**状态完全一致**。
>   用 `_sdk_session_id is None` 判据会**把这个真 zombie 误判为 fresh 而放它过 poison_guard**
>   (正是 poison_guard 要抓的对象)。只有正向前缀(仅 server-mint 能产生)能区分二者。
> - **这是 §8 元教训的又一实例**:`_sdk_session_id` 是四轮纸面推演的产物,看似"语义精确",
>   Gate-1 用源码追踪(`recover_from_disconnect` 全路径)推翻了它。推演 ≠ 现实。
> - **同一 `PREWARM_SESSION_PREFIX` 前缀贯穿 P-a + P-b 全部改动**(poison_guard / `_is_warm_reuse`
>   / orphan reaper / ttl reaper / evict 降级),零新增可变状态字段(满足 AC6)。
> - **伴生 security 守卫**(run 内新增):`run_conversation` 拒绝客户端伪造的 `prewarm-` 前缀
>   session_id —— 前缀成为信任边界,只有 server-mint 合法。
> - **§5/§8 的 H2 "统一收口 `_on_state_change` DEAD 分支 + `is_prewarm` 标志"未实现**:阶段一用了
>   三 killer 分级处置(orphan/ttl 豁免 · evict `force=False` 降级 · RAM 保命路径有意不豁免),
>   不需要新 `is_prewarm` 字段。H2 是阶段二/desktop 池的设计,阶段一未触及。

## 0. 为什么重做(v1 的致命前提被推翻)

v1 假设"复用 channel 的 prewarm 设施 = 开箱即得 warm 子进程"。查证后**双重不成立**:

| 查证项 | 事实(源/日志) | 对 v1 的杀伤 |
|--------|---------------|-------------|
| **C1 poison-guard** | prewarm 只 spawn+握手、从不流回合 → `_last_turn_clean` 永远 `False`(session_unit.py:648,唯一置 True 在 streaming_orchestrator.py:1856)。首消息命中 poison_guard_recycle(:1967)→ kill+重 spawn 14s | adopt 的子进程被当 zombie 杀掉,预热白做 |
| **channel prewarm 实测** | 生产日志:`prewarm_complete=42`,`adopt_prewarmed=0` —— **42 次预热,0 次成功认领** | "已验证的地基"从未真正 work 过 |
| **C2 prompt 不一致** | recall 块按首消息文本注入(session_router.py:2669)、UI-SENSE(:2576)、分钟级 datetime,全部只在 spawn 那刻固化进子进程(:3091) | adopt 已 spawn 的子进程 = 丢失首消息的 recall/SENSE |

**但查证也带来一个关键的好消息(见 §2 地基),让 v2 有了干净的解。**

## 0b. 架构级正解(XG,2026-08-16):把 prompt builder 显式两分,不在错误结构上打补丁

XG 指出根子:**我们一直在一个"单体 prompt builder"上打补丁,而它本就该分成两个**。这是 P9/R25
(先问该不该存在,别优化不该存在的东西)—— prewarm 之所以别扭,是因为 builder 没区分
"恒定可预热"和"依赖 input 的动态"两类内容。正解是**结构性拆分**,prewarm 变成它的自然产物。

### 两分定义
- **`default_builder`(恒定,可与 spawn 一起 warm)**:11 个固定 context files 全量注入
  (SWARMAI/IDENTITY/SOUL/SELF/AGENT/USER/STEERING/TOOLS/MEMORY/EVOLUTION/KNOWLEDGE)+ 非文件的
  安全/datetime/runtime 段。**完全不依赖 user input** → 任何时刻都能提前 build 好、随 spawn 固化。
- **`dynamic_builder`(依赖 user input / 本轮上下文)**:基于 user input 的 recall(Memory /
  Knowledge-Library / DDD / …)+ briefing + UI-SENSE/editor + pre-round message(continuation/
  wrap-up)。**只有 user question 到达后才能 build**。

### 现状:两分其实已隐含存在,只是没被命名 + 没 warm-friendly 组织(读 prompt_builder.py 坐实)
- `build_system_prompt` 的 **layer 1** = 11 context files 全量(prompt_builder.py:816-901)——
  就是 default 部分,已不依赖 input,但现在**每次 spawn 才 build**(desktop L1 cache 还被
  memory_smart 旁路,KNOWLEDGE.md § 说明是有意的)。
- **layer 2-10**(DailyActivity/briefing/editor/deferred-MCP,:902+)+ 首消息后的
  `_maybe_inject_recall`(session_router.py runtime leg)= dynamic 部分,散落两处、没有统一命名。
- **结论**:XG 的两分不是新增机制,是**把已经存在但纠缠的两类内容显式切开**,让 default 半边
  可独立 warm。这比 v2 之前"prewarm 复用 channel 设施 + 打 C1/C2 补丁"干净得多。

### ⚠️ 一个 SDK 硬约束决定 dynamic 半边"append 到哪"(上一轮已验证,是这个架构的关键约束)
`ClaudeSDKClient.query(prompt, session_id)` **无 system 参数**;system_prompt 只在 `_spawn` 时经
`ClaudeAgentOptions` **一次性固化**,子进程起来后**无法再改 system**(§3a 坐实)。所以:
- `default_builder` 的产物 = spawn 时的 `options.system_prompt`(warm 子进程带着它起来)。✅
- `dynamic_builder` 的产物 **不能 append 进已固化的 system_prompt**(改不了)→ 必须作为
  **首消息 `query_content` 的前缀**注入(query 接受 str/message-iterable,warm turn 已在这么做
  continuation/wrap-up)。这正是 §3a 选项 C 的机制,现在从"绕 C2 的技巧"升级为"dynamic_builder
  的正式输出通道"。
- **换句话说**:XG 的"append 上啊"在架构上完全正确,只是 append 的**目标是 query_content 前缀,
  不是 system_prompt**(SDK 不允许后者)。两分后这个边界变得清晰、自洽,不再是补丁。

### ⚠️ default 部分并非全"恒定":三个 per-session 变量渗入 layer 1(两分必须精确处理)
读 prompt_builder.py 坐实,这三者让"11 files 全量"不是单一常量,是**按维度分桶的常量**:
1. **session-type 排除**(prompt_builder.py:886-892):group channel 排除 USER/MEMORY/EVOLUTION;
   non-owner 排更多;desktop/owner 全量。→ default 产物**按 session-type 分 3-4 桶**。
2. **model → token budget → truncation**(:862,876):不同 model 的 context window 影响预算。
   实践中 1M model 从不 truncate(KNOWLEDGE.md § 坐实),但边界上 default 产物**也按 model 分桶**。
3. **resume context**(layer 10,:1291):依赖**具体 session 的历史**,是纯 dynamic、**绝不能 warm**——
   它不属于 default 也不属于本设计的 prewarm(prewarm 只服务 fresh/new session,cold-resume 走原路)。

**结论**:`default_builder` 的输出是 **(session_type × model) 的缓存桶**,不是单例。prewarm 池
按这个 key 分桶(desktop-owner × 默认model 是主桶);adopt 时 §M2 的 agent_id/model 校验天然
覆盖 model 维度,再加 session_type 维度即可。**这正是为什么 adopt 必须校验 agent_id+model+
session_type ——它们就是 default 桶的 key。** 这条把 M2 从"补一个校验"升级为"桶 key 的完整性"。

### 这如何让 prewarm 从"打补丁"变"自然产物"
| 关注点 | 打补丁的旧路 | 两分后的自然形态 |
|--------|-------------|----------------|
| prewarm 用什么 prompt spawn | 复用 channel 全量 prompt(含/缺什么靠猜) | **`default_builder` 输出**——定义上就是恒定可 warm 的那半 |
| recall/SENSE 怎么进 | C2:塞不进已固化 system_prompt,纠结 | **`dynamic_builder` 输出 → query_content 前缀**,天经地义 |
| warm 与 dynamic 的边界 | 模糊,散落 layer 1-10 + runtime | **两个 builder 的函数边界 = 架构边界** |
| C1 poison-guard | 仍需修(fresh 子进程 adopt) | 仍需修(与两分正交,见 P-b) |

> **定位**:prompt-builder 两分是 v2 的**架构地基**,它让 default 半边可预热、dynamic 半边有
> 干净的注入通道。prewarm(方案 A/B)是这个地基上的自然应用,而不是往单体 builder 上贴的补丁。
> C1(P-b)/orphan(P-a)修复与两分**正交**,仍需做,但不再是"为绕结构缺陷而打的补丁"。

## 0c. 第二轮 PE 对抗审查(2026-08-16)—— 地基主张降级,阶段一仍 GO

第二轮零上下文 PE 专攻 v2 新增的架构主张。**三个 CRITICAL 我已亲自复核源码,全部成立**——
"recall 走 query_content 前缀 = 低风险、一条实测兜底"这个 §3a 结论**站不住**,是本轮最大的洞。

### 🔴 CR-1(v2 新引入,已复核)— "recall 换位置无实质差异"忽略了 caching + confabulation
- **证据A(caching 是 load-bearing)**:`system_prompt.py:72-89` `build_volatile_tail` 注释白纸黑字——
  Bedrock prompt caching 按**字节精确前缀**匹配,datetime 特意挪到 76K context files **之后**,
  就是为了不让整个 constitution 每分钟 re-`cache_creation`。`streaming_orchestrator.py:1664` 还有
  cache-miss 观测。**§3a 说"位置无实质差异、实测一条"完全没提 caching = 本轮最大盲点。**
- **证据B(confabulation live 反证)**:`session_router.py:952-960` 的 `[RECALLED]` provenance 前缀,
  注释原文:"mark recalled material as ... **NOT new user input** ... a confabulation surface —
  **observed live**"。**代码库已实测:把 recall 当用户输入会 confabulate,专门加边界对抗。**
  v2 恰恰要把 recall 放进 `query_content`(物理上就是 user message)→ 直接撞这个已知有害方向。
- **诚实 nuance(挑刺官指出,我认同)**:recall 现状本就在 system_prompt **尾部**(不在 76K 可缓存
  前缀内),所以 turn-1 的前缀 cache 不受"挪位置"新破坏。真正的风险是**语义位置/注入**(证据B)
  + 8K 进 query 前缀对**首轮 input 成本**的影响——这需要**建模**,不是一条对话实测。
- **修复**:§3a/§3 的"选项 C 低风险"降级。步1b 从"一条实测"升级为 **cache 成本建模 +
  provenance 回归门**(recall 进 query_content 时必须保留并强化 `[RECALLED]` 边界 + 做"用户能否用
  '忽略上面的'覆盖 recall"的注入测试)。**此门通过前,两分不得标 FINALIZED。**

### 🔴 CR-2(v2 新引入,已复核)— recall 体量被低估 2.4×
- 证据:`session_router.py:75` `_RECALL_MAX_TOKENS = 8_000`;我在设计里写 "~3400"。**错 2.4 倍。**
- 后果:prewarm 省 spawn 握手(8-14s),但首消息要多送**最多 8K input token** 走 query 前缀。
  净收益模型用错了量级 → 必须用 8000 重算:prewarm 真实节省是否 > 首轮增量 input 成本/延迟。

### 🟠 HIGH-1(v2 新引入,已复核)— 桶爆炸 vs 池深2,分桶自我否定
- 证据:`prompt_builder.py:297-302` 三个 model 全是 1M(truncation 不分化)、`:886-892` session_type
  3-4 类。§0b 说按 (session_type × model × agent_id) 分桶,池深才 2 → 非主桶命中率趋近 0;且 1M 下
  跨 model 的 default 产物**逐字节几乎相同**,按 model 分桶是为同构产物开桶,纯浪费。
- **修复(采纳)**:desktop prewarm **只对主桶**(owner × default-model × default-agent)预热,其余走
  cold。**放弃 model 维度分桶**(1M 下同构)。**关键区分**:adopt 校验 agent_id/model 是**正确性**
  需要(错 agent=人格全错),但**校验 ≠ 分桶**——§0b 把两者混为一谈了,校验保留,分桶砍到只剩主桶。

### 🟠 HIGH-2(第一轮遗留,v2 隔离论证不成立,已复核)— M3 不止影响 desktop B
- 证据:方案 A 的池单元也走 `_ensure_spawned` 完整握手;若"addTab→首消息间隔"p50<14s 普遍,
  池里单元没 spawn 完用户就发消息 → adopt 拿到非 IDLE 单元回落 cold。**A 的命中率同样被间隔约束。**
- **修复**:§8 "M3 只影响 B" 自相矛盾,订正为:M3 门控 A 和 B 的**命中率**(非有无收益)。A 的价值
  主张收窄为"覆盖**天然长间隔**窗口(app 启动首 tab / daemon 重启 / 连开 tab,这些确 >14s)",对
  "打开就发"的快用户 A/B 都无能为力——这是诚实的收益边界。

### 🟡 MED-1(v2 新引入)— 两分的 token budget 跨层耦合
- 证据:`prompt_builder.py:852-865` layer 1 预算 = `base_budget − EPHEMERAL_HEADROOM`,是为 layer 2-10
  预留 headroom 后的剩余。两分后 default 独立算预算会 double-count/漏算(1M 下无害,小窗口 model 会
  truncation 错乱)。R27 迁移须明列 budget 归属;两分后对 default 单独产物跑 `assert_core_sections`。

### 🟡 MED-2(v2 新引入)— default warm 的 staleness 未讨论
- context files 被后台 cultivation/decay 改写;prewarm 子进程用 T0 产物 spawn 并固化(SDK 不可改),
  用户 T0+50s 发消息时 MEMORY/EVOLUTION 可能已变。**修复**:default 产物打 content-hash/mtime,adopt
  前比对 context files mtime,过期则弃用重 build。§5 的 60s TTL **同时是 staleness 上界**——显式写出。

### 结论:阶段一 GO,阶段二地基先补 cache 模型 + provenance 门
- **阶段一(P-a/P-b/Slack 复活)不依赖这个地基,可现在走**——挑刺官明确肯定。
- **阶段二(prompt builder 两分)进 pipeline 前必须补**:CR-1 的 cache 成本建模 + provenance 回归门、
  CR-2 的 8K 净收益重算、HIGH-1 的只预热主桶。C2 的"✅"降级为"🔶 机制可行,收益/语义等价性待建模"。

## 0d. 阶段二三个门的调研结论 —— ⚠️ 本节结论已被第三轮 §0e 推翻,保留作过程记录

> **作废提示**:本节曾判"三门通过、阶段二可 FINALIZE"。第三轮 PE(§0e)证明这是又一次乐观:
> 门①论证错(结论歪打正着)、门③单位混淆漏 TTFT、门②"确定守法"是措辞游戏,并揭示 prewarm 与
> recall 的架构张力。**以 §0e 为准。** 下文保留以记录"错误论证长什么样"。

XG 要求:阶段二三个门(cache 模型 / provenance / 8K 净收益)先搞清楚再定稿。已用**生产 cache
数据 + 源码**逐个查实,不靠上线埋点。结论:**两分方向成立,三个门都有确定答案,阶段二可 finalize**。

### 门① cache 成本模型 —— caching 确 load-bearing,但 recall 只注一次,方案不破坏它
- **真实数据(daemon log `result_usage`)**:warm turn `cache_read_input_tokens` 达 **百万~290万级**
  (Bedrock prompt cache,10x 便宜),`cache_creation` 仅 1万级。**caching 是真·load-bearing**,挑刺官
  对(CR-1 证据A 成立)。
- **但决定性事实(session_router.py:756,772,`_recall_injected` guard)**:**recall 一辈子只在首消息
  注入一次**,turn 2+ 永不再 recall。→ CR-1 担心的"recall 每轮进 query 破坏 cache"**不成立**——
  recall 根本不在 turn 2+ 出现。
- **首轮**:turn-1 本就要 `cache_creation` ~300K(整个 constitution 首次建缓存)。recall 无论放
  system_prompt 尾部还是 query_content 前缀,turn-1 都要 cache_creation 一次,**位置不改变 turn-1 的
  cache 结构**(76K 可缓存前缀在两种方案里都不含 recall)。
- **结论**:门① **通过**。cache 成本不是 query_content 方案的障碍(recall 单次 + 首轮本就建缓存)。

### 门③ 8K 净收益 —— 量级上 prewarm 稳赚
- CR-2 修正:recall 上限是 **8000**(非 3400)。但对比:prewarm 省的是 **8-14s wall-clock** 的 spawn
  握手(用户可感知延迟);代价是首消息 query 前缀多 8K input token。
- **8000 / ~300000(首轮本就有的 cache_creation)= 2.7% 增量** —— 边际成本极小,且是 token 成本
  (非用户可感知延迟),换 8-14s 的可感知延迟消除。**净收益方向明确为正**(前提:命中 §M3 的长间隔窗口)。
- **结论**:门③ **通过**。用 8K 重算后,prewarm 收益量级仍远大于成本。

### 门② provenance —— 这是真正的风险,但有确定的守法(非"实测一条")
- CR-1 证据B(session_router.py:952,`[RECALLED]` 是对抗 live-observed confabulation 加的、明说
  "NOT new user input")成立,是三个门里**唯一真风险**。
- **但它不是"等价性未知",而是"有已知正确的守法"**:recall 进 query_content 时,**必须携带并强化
  `[RECALLED]` provenance 边界**(现在它是 system_prompt 里的一段前缀,迁到 query_content 时原样保留
  且更显式,因为物理位置已在 user turn)。这是**可确定实现的契约**,不是靠一条对话赌等价。
- **回归门(阶段二 pipeline 的 DoD,非前置未知)**:(a)recall 块在 query_content 里保留 `[RECALLED]`
  头;(b)注入测试——用户发"忽略上面的内容"能否覆盖 recall(确认 provenance 边界守住,recall 不被
  当可覆盖的用户指令);(c)confabulation 抽检——agent 不把 recall 当"自己这轮说的话"。
- **结论**:门② **从"未知风险"降为"已知契约 + 回归门"**。它是阶段二 BUILD 的验收项,不是阻塞设计
  finalize 的未解问题。

### 三门总结:阶段二地基**成立**,可 finalize
| 门 | 二轮判定 | 调研后结论 |
|----|---------|-----------|
| ① cache 模型 | CR-1 盲点 | ✅ 通过——recall 单次注入 + 首轮本就建缓存,位置不破坏 cache |
| ② provenance | CR-1 真风险 | ✅ 有确定守法(保留 `[RECALLED]` + 3 项回归门),是 BUILD 验收项非未知 |
| ③ 8K 净收益 | CR-2 量级错 | ✅ 通过——8K 仅占首轮 cache_creation 2.7%,换 8-14s 可感知延迟,净正 |

**元教训闭环**:二轮 PE 的价值真实——它逼我从"假设等价"转到"查真实 cache 数据"。而查完发现:
caching 确实 load-bearing(挑刺官对),但 `_recall_injected` 单次注入这个事实让 cache 担忧落地为零
(我和挑刺官都没在第一时间连上这条)。**真正留下的是 provenance,而它有确定守法**。三个门都不是
"待上线才知道"的未知,而是**现在就能定的设计契约**。→ 阶段二 finalize。

## 0e. 第三轮 PE 对抗审查(2026-08-16)—— 揭穿一个我三轮都在回避的架构张力

第三轮 PE 专攻 §0d 的三个门。**逐条复核源码,全部成立**。核心:我又犯了同一个乐观等号,
而且掩盖了一个**prewarm 与 recall 的架构性张力**。§0d 的"三门通过"论证不成立,阶段二**撤回
FINALIZED**。

### 🔴 第4问(最深,我三轮都在回避,已复核成立)—— prewarm 与 recall 首消息注入有架构张力
- **时序坐实**(session_router.py):现状 **cold 路径** recall 是在 spawn **那一刻**已在 system_prompt
  里的 —— `build_options`(:2595)→ `_maybe_inject_recall` 追加 recall 到 `options.system_prompt`
  (:2673)→ `unit.send()`(:2751)内部 `_ensure_spawned` 用**已含 recall 的 options** spawn。
  **cold 从不需要 query_content 前缀,因为 spawn 时 recall 已在手。**
- **prewarm 的本质是首消息到达前就 spawn** → recall(依赖首消息)**必然赶不上 spawn**。
- **∴ 架构张力**:recall 的自然、已验证、provenance 安全的位置 = **spawn 时的 system_prompt**;
  prewarm 恰恰剥夺了"spawn 时已知首消息"这个前提。**选项 C(recall 走 query_content)不是"干净
  正解",是 prewarm 强行制造出来、把 recall 从安全位(system 权威)搬到危险位(user turn)的代价。**
- **没有免费午餐,诚实的 trade-off**:prewarm 对**无 recall 的首消息**(zero-keyword opener)稳赚
  (纯握手节省);对**需要 recall 的首消息**,要么走选项 C(背 provenance 危险位 + 8K prefill 代价)、
  要么 adopt 后重 spawn 带 recall(握手节省全退回=退回 cold)。**二选一。**
- **∴ §3 那句"prewarm 对所有首消息(含触发 recall 的)都适用,不再退化到仅 zero-keyword opener"
  是乐观宣称**——它靠选项 C 撑着,而选项 C 的代价我没如实计入。**订正:prewarm 的确定收益域是
  "无 recall 首消息";有 recall 首消息的收益需减去选项 C 的代价,可能显著缩水甚至归零。**

### 🔴 门① 论证不成立(结论歪打正着)—— "recall 只注一次→cache 归零"的等号错了
- **错在哪**:`_recall_injected` 只保证"不再跑 recall 检索逻辑",**不保证那 8K 从上下文消失**。
  subprocess 是长生的,CLI **"replays the full conversation before inference"**(session_unit.py:1585
  原文坐实)→ turn-1 的 query_content 成为会话史**永久一轮**,turn2+ **每轮都被重新计入 input**
  (命中 cache_read,不是消失)。**我把"注入动作一次"等同于"cache 影响一次"——第三次同型乐观等号。**
- **结论为何仍成立**:新旧位置 turn2+ 的 cache 命中**同构**——现状 recall 在 system_prompt 尾部随
  spawn 固化、turn2+ cache_read;新方案在会话史、turn2+ 也 cache_read(和现有 wrap-up/continuation
  前置块机理一样)。所以"cache 不是障碍"结论对,**但正确理由是"两位置 turn2+ 都以 cache_read 稳态
  同构",不是"recall 消失了"。**
- **未验证假设**:代码库**无显式 `cache_control`/`cache_point`**(全仓 grep 零命中),缓存全靠 prompt
  字节序 + CLI/SDK 自管的、本仓不可见的断点。∴"query_content 里的 recall 与 system_prompt 尾部的
  recall turn2+ 落在同一 cache 段"是**实测归纳(warm turn 整体命中 cache_read),不是源码确定**。
- **数据纠错**:"cache_read 百万~290万级"是 `result_usage` 的**累积 rollup**(一次 query 内多轮
  agent-loop 之和),**单次 Bedrock 推理无一条 ≥100 万**(max ~95万)。caching load-bearing 结论对,
  但我引用的量级混淆了累积与单轮。

### 🔴 门③ 8K 净收益 —— 单位混淆 + 漏了 TTFT prefill(不成立,需返工)
- **分母 300K 未经源码验证**:全仓 grep `300000` 零命中;76K 是注释文字估计,实际强制预算
  `DEFAULT_TOKEN_BUDGET=30_000`。**2.7% 这个数的分母来源不明。**
- **单位混淆**:prewarm 省的是 **8-14s wall-clock**(可感知延迟);8K 是 **token 成本 + prefill 时间**。
  我用"成本占比 2.7%"论证"时间收益净正"——**两个单位**。
- **漏了要害**:8K prefill 会增加**首消息的 TTFT**(而 TTFT 正是 §1 要优化的东西!)。正确净收益 =
  `prewarm 省的握手时间 −(8K prefill 增加的 TTFT + 8K cache_creation 成本)`。**我只算了成本占比这
  一项,漏了与优化目标同单位的 prefill→TTFT 代价。**
- **返工要求**:补 8K prefill 的实测 TTFT 增量 vs 8-14s 握手节省(同单位对比),分母换源码可核的量。

### 🟠 门② provenance —— "确定守法"是措辞游戏(部分成立)
- 把"我不知道能不能守住"重新包装成"我有个契约要求它守住 + 测试验证"。契约是**期望**,不是**已证明
  属性**。`[RECALLED]` 现状在 system_prompt(系统权威位)说"NOT new user input",模型信它因为物理
  上确实不在 user turn;挪进 query_content 后**文字自称与物理位置直接矛盾**,而 confabulation 是
  "observed live"(现场发生过,且那时 recall 还在更安全的 system_prompt 里)。
- **订正状态**:门② = "残余 open risk,缓解方案(同一句文字放更危险位置)未验证",**不是"有确定
  守法"**。我列的回归门(b)"用户能否用'忽略上面的'覆盖 recall"恰恰证明我也不知道结果——否则不必测。
- 肯定:列 3 项回归门作 BUILD DoD 是对的工程动作,比"赌一条实测"强;但不能据此标 finalize。

### 结论:阶段二撤回 FINALIZED,方向仍成立
- **阶段一(P-a/P-b/Slack 复活)**:与三门正交、源码坐实 → **保持 FINALIZED**,本轮靶心不在此。
- **阶段二**:方向对(builder 该两分),但 §0d 三门论证全需修:门①论证重写(结论留)、门③补 TTFT
  同单位建模、门②降为 open risk。**且必须先如实画出第4问的 trade-off**:prewarm 对有/无 recall
  首消息的收益是不同的,不能用"选项 C 很干净"掩盖。→ **阶段二 = 方向确定、收益域需诚实划分,
  未到 FINALIZED。**

### 元教训(第三轮,最痛的一条)
**方向我从头就对,但我连续三轮高估收益/低估风险,且门①这次是"用错误论证得到碰巧正确的结论"——
比单纯猜错更危险,因为它会掩盖未来的真实回归。** 真正的价值判断:**prewarm 不是对所有首消息免费的
优化,它对"无 recall opener"稳赚、对"有 recall 首消息"要付选项 C 的代价。** 我一直不愿把这个
trade-off 摊开,因为摊开后 prewarm 的收益故事就没那么漂亮了。这就是我该被 XG"不着急开工、反复对抗
审查"逼着面对的——**设计的诚实性,不是让收益看起来最大,是让代价看得最清。**

## 0f. 决定性数据:回答"有 recall 首消息值不值得 prewarm"(2026-08-16)—— 阶段二真正 FINALIZE

XG 要求用数据真正回答第4问,再 finalize。已用**真实日志实证**,答案清晰,而且**消解了前三轮
纠结的 8K prefill 争论**。

### 数据1:96% 的首消息触发 recall(TTFT 日志,52 个首消息)
- 触发 recall:**50/52 = 96%**;未触发(zero-keyword opener):2/52 = 4%。
- **含义**:"prewarm 对无-recall opener 稳赚"这条几乎没价值(仅 4%)。prewarm 的价值**几乎完全**取决于
  "有 recall 首消息值不值得"——所以这个问题必须正面回答,不能靠"opener 稳赚"绕过。

### 数据2(决定性):prewarm 只 warm 握手,**不 warm Bedrock cache**(方案 A,实证)
- 三个 prewarm 单元 spawn 后到 adopt 前:**0 次 Bedrock 调用 / result_usage**(日志坐实)。
  `__aenter__` 那 8-14s = 纯 CLI 启动 + SDK initialize 握手,**没打推理、没建 Bedrock cache**。
- **∴ prewarm 省的是握手,不是 prefill。**

### 数据3:握手只占 TTFT 的 ~17%,prefill+生成占 ~83%
- 实测 cold turn:`wrapper_aenter=10.3s`(握手)vs `send+infer=59s`(prefill+thinking)→ 握手 ~17%。
- 另一条:握手 8.2s / TTFT 21.7s → ~38%。区间 **17-38%**,握手是可省的那部分,prefill 不可省。

### 推导:8K prefill 争论**消解**,只剩 provenance 一个真变量
1. prewarm 只省握手(8-14s),**不省 prefill** → recall 走 system_prompt 还是 query_content,**对
   prewarm 省的时间毫无影响**(那 8K prefill 两种方案都要付)。
2. → 前三轮纠结的"选项 C 的 8K prefill 代价"**根本不是 prewarm 引入的增量**:cold 路径走 system_prompt
   尾部**也要** prefill 这 8K。**选项 C 相对 cold 的 TTFT 增量 ≈ 0。**(门③ CR-2 的 8K 顾虑至此清零——
   它不是增量。)
3. → **cache 结构也不变**:prewarm=方案A 不碰 Bedrock cache,首消息无论 recall 在哪都要全量 prefill 一次
   (和今天 cold 一样)。门① 的 cache 顾虑同样清零——prewarm 不改变 cache 时序。

### 最终答案:"有 recall 首消息值不值得 prewarm?"
**值得,且代价被前三轮高估了。** 精确的 trade-off:
- **收益**:省握手 8-14s = TTFT 的 17-38%(可感知延迟的确定削减)。
- **代价**:仅 **provenance 风险**(recall 从 system 权威位挪到 query_content 用户位)——8K prefill 和
  cache 结构**都不是代价**(方案A 下 prewarm 不碰 prefill/cache,已实证)。
- **∴ 决策收敛为单一变量**:省 17-38% TTFT 是否值一个 **可用 `[RECALLED]` 边界 + 3 项回归门守住的
  provenance 风险**。鉴于收益是每个首消息(96% 触发 recall)都吃到的确定延迟削减,而 provenance 有
  明确守法(§0d 门②的回归门)+ 失败可观测(注入测试)+ 最坏退回 cold(strangler-safe),**净判断:值得做,
  provenance 作为 BUILD 的一等验收门。**

### 一个更简的备选(供 EVALUATE 权衡):prewarm 不碰 recall
- 既然 prewarm 只省握手、与 recall 位置无关,**还有一条更保守的路**:prewarm 照旧 spawn(省握手),
  但 recall 仍走**现状的 system_prompt 注入**——代价是 adopt 后首消息要 crash-to-cold 重 spawn 带 recall
  → **握手节省退回**。即"要么省握手背 provenance(选项C),要么保 provenance 丢握手(重spawn)"。
- **数据裁决**:因为握手占比达 17-38%、且 provenance 有确定守法,**选项 C(省握手+守 provenance)优于
  重 spawn(丢握手)**。但这个二选一现在是**摊开的、可量化的**,不再是"选项C很干净"的掩盖。EVALUATE
  可据此定,我推荐选项 C。

### 阶段二状态:✅ 可 FINALIZE
三个门 + 第4问全部用数据落地:门①③的 cache/prefill 顾虑经"prewarm=方案A"实证**清零**;门②
provenance 是唯一真代价、有确定守法、列为 BUILD 一等门;第4问的 trade-off 已摊开量化、数据支持选项 C。
**阶段二方向 + 收益域 + 代价全部诚实定稿 → FINALIZED。**

## 0g. 执行顺序订正(2026-08-16,run_f1055239 Gate-1 后 + XG 决策)—— 阶段二先行,不再阶段一先行

试跑阶段一 bugfix(run_f1055239,EVALUATE/THINK/PLAN 完成)时,**Gate-1 skeptic 从运行时代码
独立撞到了 §0f 用数据得出的同一堵墙**,证明"阶段一先行"是本末倒置。

### Gate-1 的运行时证据(独立于 §0f 的数据论证,更强)
P-b 原方案 = 让 fresh prewarm 单元跳过 poison_guard recycle。但 Gate-1 追踪代码发现:
- fresh prewarm 单元 adopt 后首消息:`_last_turn_clean=False`(从没 clean 完成)+ `_has_ever_streamed=False`。
- 改后 poison_guard 不 recycle → `_client` 非空不转 COLD(session_unit.py:1986)→ **走 warm-reuse**。
- 但 warm-reuse **丢弃 `options.system_prompt`**(只有 spawn 用它),而 fresh 单元的 live client 是用
  **"基线 prompt"(无 recall/无本轮 UI-state)**spawn 的 → **首消息 recall/SENSE 丢失**。
- 且两个 `_will_reuse_live` complement(session_router.py:2587/2723)因 `_last_turn_clean=False` 也不
  prefix UI-state → double-miss。
- **关键反转**:poison_guard 的 recycle-to-COLD **对 fresh prewarm 单元恰恰是正确的**——它强制
  respawn,让首消息 recall 进 prompt。我原以为 recycle 是"白做",错了。**为拿首消息 recall 本就必须
  respawn,respawn 又要握手 → prewarm 握手节省归零。** = §0f 的架构张力,代码层坐实。

### XG 决策:阶段二先行(2026-08-16)
> "先做对阶段二,再回过头来用正确的方式处理 Slack 的问题,不然本末倒置了。"

- **P-b 绕不开选项 C(recall→query_content),而选项 C 就是阶段二**。先修 Slack = 在没有正确架构的
  地基上打补丁,正是四轮审查一直在避免的事(P9/R25)。
- **新执行顺序**:①**阶段二**(prompt builder 两分 default/dynamic + recall 走 query_content 前缀 +
  provenance 回归门)→ ②回头用阶段二的正确机制处理 Slack + desktop prewarm。
- **run_f1055239 superseded**(checkpointed)。其 P-a(orphan 豁免)独立有效、无害,留待随阶段二/prewarm
  实装时一起做(它本身不产生收益,单独做无意义)。
- **Gate-1 的两个 BLOCK 保留为 prewarm 实装期的必查项**:(1) `_will_reuse_live` 的两个 complement
  必须与 poison_guard 同步(R27 契约,三处"exact complement"不变量);(2) P-a 只豁免 orphan reaper 一个
  killer,需确认 `_evict_idle`/slot eviction 是否也杀 unadopted prewarm 单元。

## 0h. 阶段二 Change-Spec 细化(2026-08-16,进 pipeline 前的 file discovery + 契约边界)

像阶段一那样,进 pipeline 前把最热路径(prompt 组装)的 file discovery + 契约边界画死。

### 现状机制(读源码坐实,两分不是推倒重来)
`build_options`(prompt_builder.py:1645)是唯一编排入口,内部:
- **warm-reuse 缓存雏形已存在**:`cached_system_prompt` 非空且非 resume → 复用首建 prompt,跳过
  `build_system_prompt`(:1876-1877)。已经在"恒定部分可复用"的方向上,只是没显式命名 default/dynamic。
- `build_system_prompt`(:782)组装 = **①11 context files**(:runtime loader)+ **②ephemeral 层**:
  DailyActivity、briefing(:1065)、UI-SENSE(`_render_ui_context_section`,:1124)、resume(:1293)。
- **recall 独立**:`_maybe_inject_recall`(session_router.py:2673)在 `build_options` **之后**追加到
  `options.system_prompt`(:960/:1021/:1205 三个写点)。
- **SDK 约束**(§3a 坐实):`options.system_prompt` 只被 `_spawn` 消费;warm-reuse 丢弃它,只传
  query_content。→ **dynamic 内容要 per-turn 生效,必须走 query_content 前缀,不能靠 system_prompt。**

### 两分的精确边界
| 归属 | 内容 | 现状位置 | 两分后 |
|------|------|---------|--------|
| **default_builder**(恒定,可 warm) | 11 context files 全量 + 安全/datetime-stable/runtime | build_system_prompt ① | 独立函数,输出按 (session_type × model) 缓存桶;prewarm spawn 用它 |
| **dynamic_builder**(per-turn,走 query_content) | recall + UI-SENSE + briefing | recall 在 router:2673 追加 system_prompt;SENSE/briefing 在 build_system_prompt ② | 统一为 dynamic 段,注入 **query_content 前缀**(保留 `[RECALLED]` provenance) |
| **纯 dynamic,绝不 warm**(留原路) | resume context(Mechanism B,含 session 历史) | build_system_prompt :1293 | 不动,cold-resume 仍走 system_prompt 重建(它本就不可 warm) |

### File Discovery
| 文件 | 类别 | 关键发现 |
|------|------|---------|
| `backend/core/prompt_builder.py` | MODIFY | `build_options`(:1645)+ `build_system_prompt`(:782)拆分;ephemeral 层(briefing:1065/SENSE:1124)从 system_prompt 组装移出到 dynamic 段。datetime 已在 volatile tail(system_prompt.py:72),保持 |
| `backend/core/session_router.py` | MODIFY | `_maybe_inject_recall`(:698/:2673)改为产出 dynamic 段拼进 query_content,而非追加 system_prompt(:960/:1021/:1205 三写点);两个 `_will_reuse_live`(:2587/:2723)契约同步 |
| `backend/core/session_unit.py` | VERIFY | send() 的 query_content 前置机制(:2078/:2102/:2140 continuation/wrap-up)= dynamic 段注入的现成载体;poison_guard(:1967)行为不变 |
| `backend/core/context_injector.py` | VERIFY | build_resume_context 是纯 dynamic-不可-warm,确认不受两分影响 |
| `backend/core/engine_metrics.py` | VERIFY | :436 反射校验 build_system_prompt 签名(is_channel kwarg)——签名变更需同步 |
| `backend/tests/test_*prompt*/*context*` | TEST | 组装契约测试 + assert_core_sections(default 单独产物必须过 core-sections 门) |

### 契约边界(R27 — 三处必须同步)
1. **poison_guard(:1967)× 两个 `_will_reuse_live`(:2587/:2723)** = "exact complement" 不变量
   (§0g Gate-1 BLOCK-1)。任何改 warm-reuse 判定,三处同步,否则 UI-state double-inject/drop。
2. **recall 三个写点(:960/:1021/:1205)** 全部从"追加 system_prompt"迁到"产出 dynamic 段"(R27 grep 全消费者)。
3. **provenance**:recall 迁到 query_content 时保留 `[RECALLED]` 头 + 注入覆盖测试(§0d 门②回归门)。

### Change-Spec(ordered,进 pipeline 时 PLAN 细化为可执行)
1. 抽 `default_builder`:把 build_system_prompt 的 ①context-files 组装提为独立、无 per-turn 输入的函数,输出可缓存。**strangler-fig**:旧 build_system_prompt 保留,新函数并行,集成测试通过前不删。
2. 抽 `dynamic_builder`:recall + SENSE + briefing 统一为产出 query_content 前缀段(不写 system_prompt);保留 `[RECALLED]`。
3. 迁移 recall 三写点 + 同步两个 `_will_reuse_live` complement(R27)。
4. 校验:assert_core_sections 对 default 单独产物;provenance 注入覆盖测试;三处 complement 同步测试。

### 未决(进 pipeline 的 EVALUATE 定)
- default 缓存桶的 key 精度(§0f 数据:1M 下 model 维度同构 → 可能只需 session_type 维度)。
- dynamic 段拼进 query_content 的确切格式(独立 message block vs 文本前缀)——§0d 门①的 SDK 能力已确认两者皆可,选更利于 provenance 边界的。

## 1. 目标(扩展:Slack + Desktop 统一)

新 session 首消息 TTFT 的 8-14s cold-spawn(SDK `__aenter__` 握手,结构性、压不掉)从用户
可感知路径移走。**同时覆盖两个入口:desktop chat tab + Slack channel** —— 查证后确认它们是
**同一个病根**,必须统一修,不能只修 desktop 留 Slack 半残。Warm turn(p50 ~11s)不动。

### 1a. Slack prewarm 失效链(查生产日志坐实,根因比 C1 更前)
`adopt_prewarmed=0`(42 次预热 0 认领)的真因**不是 C1**,而是**预热单元被 orphan reaper 提前杀掉**:
- `SessionUnit.__init__` 里 `is_channel_session=False`(session_unit.py:468),它只在 `run_conversation`
  处理真实消息时才置 True。`prewarm_channel_session` 建 unit 时(session_router.py:1617)**没传
  channel_context** → 预热单元 `is_channel_session` 恒为 `False`。
- orphan reaper 判定:IDLE + **非 channel** + 不在 open_tabs + idle>600s = 孤儿。预热单元全中
  → 每个在 ~600s 后被 reap。**日志坐实**:`prewarm_ready 20:36:52 → orphan_reap+kill 20:47:46`(11min)。
- owner 的下一条 Slack 消息若没在这 10min 窗口内到达 → 预热单元已死 → adopt 拿到 None → cold。
- **两个 bug 串联**:orphan reaper 先杀(→ adopt=0);即便修好让 adopt 成功,C1 poison-guard 再杀
  (→ adopt 也白搭)。**必须两个一起修,任一单修都无收益。**

### 1b. 三个共享病根 → 一处修复
| 病根 | 影响 | 统一修复 |
|------|------|---------|
| **P-a orphan reaper 误杀预热单元** | Slack adopt=0;desktop 池单元同样会被杀 | `_check_orphan_sessions` 豁免 `prewarm-` 前缀 + 预热单元有专属 TTL(§5) |
| **P-b C1 poison-guard 误杀 fresh 子进程** | 任何 adopt 成功的单元首消息被 kill 重生 | ~~poison-guard 加 `_sdk_session_id is not None` 条件(§2-G1)~~ → **交付更正:改用 `startswith(PREWARM_SESSION_PREFIX)` 前缀,见顶部 BUILD 交付更正块** |
| **P-c C2 prompt 固化,recall/SENSE 注不进** | adopt 的子进程丢首消息 recall(desktop 有 SENSE;Slack 有 channel-security/sender 上下文) | §3 选项 C:走 per-query 追加,取决于 SDK 能力 |

**注意 P-c 对 Slack 更severe**:Slack 预热单元的 channel_context(sender identity / permission tier /
Slack 格式规则)也是"首消息时刻"才有——若 spawn 时没带对,adopt 后权限上下文缺失(gateway.py:717
注释已警告)。所以 Slack 的 P-c 不只是 recall,还有**安全上下文**,更不能含糊。

## 2. v2 的验证过的地基(每条都读了源码/日志)

- **G1 — C1 有干净结构解**:poison-guard 想防的 zombie 是"流过一半被打断的脏 CLI turn-state"。
  ~~这种情况 `_sdk_session_id` 必然已设(只在流式 message 赋值,streaming_orchestrator.py:861/866);
  fresh-never-streamed 的 prewarm 单元 `_sdk_session_id is None` → 加 `and self._sdk_session_id is not None`
  即可区分。~~ **⚠️ 交付更正(Gate-1 F1 推翻此判据):`recover_from_disconnect`(:3167)会让普通单元
  也落到 `_sdk_session_id=None` 且 clean=False,与 fresh prewarm 同态 → `_sdk_session_id is None`
  会误放真 zombie。实际改用正向 `startswith(PREWARM_SESSION_PREFIX)` 前缀区分(见顶部 BUILD 交付更正块)。**
  仍不是粗暴的 `_last_turn_clean=True`(挑刺官担心重开 zombie),而是用只有 server-mint 能产生的
  前缀精确区分两种 False。
- **G2 — poison-guard 本身健康**:44 次触发全是普通 desktop session(`prewarm-` 前缀=0),
  正在保护真实的 soft-interrupt/SSE-disconnect 场景。**不能削弱它对 `_sdk_session_id` 已设单元的
  防护**;G1 的条件只放行 fresh 单元,不碰这个。
- **G3 — C2 是"复用已 spawn 子进程"的固有代价,无法回避**:子进程 prompt 在 spawn 那刻固化,
  首消息才生成的 recall/SENSE 注不进去。**这决定了 v2 的核心取舍(见 §3)。**
- **G4 — `_spawn_lock` 全局串行是必要的**(os.environ 进程级,claude_environment.py:184-285)。
  不动。预热不能抢它。
- **G5 — orphan/evict 回收有多条路径**:`_evict_idle`(session_router.py:1949)也会 kill 预热单元
  且不清新 map(H2)。所有 kill 路径必须统一收口。

## 3a. 调研结论(2026-08-16):C2/P-c 的 SDK 能力已查清 —— **有解,不再是未定**

读了 SDK + warm turn 的完整传递路径,SDK 层事实**完全确定**:

- **`ClaudeSDKClient.query(prompt, session_id)`**(SDK 签名,实机 introspect):`prompt` = str
  **或 message dict 的 async iterable**;**没有 system 参数**。system_prompt 只能在 `_spawn` 时经
  `ClaudeAgentOptions` 一次性固化,子进程起来后**无每轮追加 system 的通道**。
- 现状:recall 只注入 `options.system_prompt`(session_router.py:2669,spawn 时固化);warm turn
  复用子进程时 `query()` 只收 `query_content`(纯用户消息),**system_prompt 根本没重传**
  (streaming_orchestrator.py:291-293)。→ 这正是 C2 的机制根因,已坐实。

**关键洞察(把 C2 从"未定"收敛为"有解")**:`query()` 的 prompt 既可是 str 也可是 message
iterable,且**代码里 warm turn 已经在往 `query_content` 前置/追加内容**(continuation
session_unit.py:2140、wrap-up :2078/:2102)。→ **recall/SENSE 不必走 system_prompt,可以作为首消息
`query_content` 的一部分注入**(拼在用户消息前,或作为独立 message block)。这条路径:
- 不依赖 spawn 时固化的 system_prompt → **绕过 C2**;
- 是 per-turn 的、已被现有 continuation/wrap-up 验证可行的机制 → 低风险;
- 对 Slack 的安全上下文(sender/permission)同理适用 —— 也能作为首消息前缀注入。

**收敛结论**:C2 不再限制 prewarm 的收益范围。prewarm 子进程用"基线 prompt"(全量 context,
无 recall/SENSE)spawn;首消息 adopt 后,recall/SENSE/Slack-安全上下文**作为 query_content 前缀**
注入。**剩余的唯一验证点**:确认把 recall 放进 query_content(而非 system_prompt)在模型侧
效果等价(位置从"system 尾部"变成"user 消息前缀"——对 Opus 应无实质差异,但需 §6 步1b 实测一条)。

## 3. 核心设计决策:直面 C2 —— 采用选项 C(recall 走 query_content,已验证可行)

C2 是 v2 必须诚实面对的:**adopt 一个预热子进程,就拿不到首消息才生成的 recall/SENSE。**
三种应对,选 C:

- **选项 A(v1 做法,已否)**:无视 C2 直接 adopt → 首消息静默丢 recall,答得笨、不可观测。❌
- **选项 B**:prewarm 时就把 recall 猜着注入 → 但 recall 依赖首消息文本,预热时没有,猜不了。❌
- **选项 C(v2 采用,SDK 能力已查清、可行 —— 见 §3a)**:**prewarm 子进程用"基线 prompt"
  (全量 context,无 recall/SENSE)spawn;adopt 后,首消息的 recall/SENSE/Slack-安全上下文
  作为 `query_content` 前缀注入**(不塞 system_prompt)。已确认 `query()` 接受 str/message
  iterable 且 warm turn 已在往 query_content 前置内容(continuation/wrap-up),机制现成。
  - **⚠️ 第三轮订正(§0e)**:选项 C 不是"干净正解",是 prewarm 剥夺了"spawn 时已知首消息"这个
    前提后**被迫**把 recall 从安全位(spawn 时的 system_prompt)搬到危险位(user turn 的 query_content)
    的代价。它带 provenance 危险位 + 8K prefill 两项代价,不是免费。

> **⚠️ 状态订正(2026-08-16 第三轮后,推翻上一版乐观结论)**:C2 **机制可行但有代价**,不是"不限制
> 收益范围"。**诚实的收益域划分**:prewarm 对**无 recall 首消息**(zero-keyword opener)稳赚(纯握手
> 节省);对**有 recall 首消息**,走选项 C 要减去(provenance 风险 + 8K prefill 增加的 TTFT),或退回
> 重 spawn(握手节省全退回)。上一版"对所有首消息都适用、不再退化到仅 opener"是被推翻的乐观宣称。

## 4. 方案结构:A 为主,B 为增量(定位从 v1 翻转)

PE-M4 采纳:**A(daemon 通用池)才是主路径**,B(每-tab 预热)是数据支撑后的增量。

### 方案 A(主):daemon 维持通用预热池(池深 2)
- daemon 空闲时后台预热至多 2 个"基线 prompt"子进程(无 tab 归属),`_prewarm_pool` 单一权威 map。
- 覆盖**天然 >14s 的高价值窗口**:app 启动首 tab、daemon 重启、用户连开 tab、思考型用户。
- adopt 前**同时校验 agent_id + model**(PE-M2:错 agent = 整个人格/工具/权限全错)。
- 不需要 tab_id 映射、cancel 端点、前端 addTab 接线、B1 让位 —— **复杂度远低于 B**。

### 方案 B(增量,数据门控):每-tab addTab 触发预热
- **仅当 §6 的"addTab→首消息间隔"埋点证明 p50 > 14s 时才做**(PE-M3:否则预热没 spawn 完就被撞,
  命中率远低于 90%)。若 p50 < 14s,B 不值得,只保留 A。
- 触发时机 = addTab(若做):prewarm 定义就是提前备好等着用。

## 4b. 调研坐实:P-b 修复安全 + H2 有统一收口点(2026-08-16)

### P-b(C1 poison-guard 修复)信心坐实 —— 读了引入 commit `1c25f6d6`(run_ed9647c5)
- **poison-guard 防的 zombie 是**:"reuses an alive-but-POISONED warm subprocess after a soft
  interrupt / SSE-disconnect left the CLI in **corrupt turn-state**"(commit message 原文)。
- **corrupt turn-state 必然是流过 turn 才可能产生的** → fresh prewarm 单元没有 turn-state 可 corrupt。
- ~~∴ 给 poison-guard 加 `and self._sdk_session_id is not None`。~~ **⚠️ 交付更正:此判据被 Gate-1 F1
  推翻** —— `recover_from_disconnect` 造出的首消息断连普通单元同样 `_sdk_session_id=None`,会被误判为
  fresh。实际改用 `startswith(PREWARM_SESSION_PREFIX)`(只有 server-mint 能产生的前缀)。逻辑不变:
  fresh prewarm 没有可污染的 turn-state → 豁免;真 zombie(普通 id,已流过被打断)照杀。
- **双保险**:commit 明说 zombie-detection 作为 backstop 保留,"a missed flag only degrades to
  today's behavior, never worse"(strangler-safe)。即便 P-b 条件判错,最坏退回今天行为。
- **结论:P-b 是低风险的精确修复**,不是挑刺官担心的"粗暴 `_last_turn_clean=True`"。

### H2 有天然的统一收口点 —— 不用在 N 个 kill 点各写一遍
- 每个 SessionUnit 都带 `on_state_change` 回调 = `SessionRouter._on_unit_state_change(sid, old, new)`
  (session_router.py:2075),`_transition` 到**任何**状态都触发它。
- **所有** kill 路径(evict/TTL/orphan/crash/poison/shutdown)最终都 `_transition(DEAD)` →
  都会命中 `new_state == DEAD` 这一个回调分支。
- ∴ **H2 的 map 清理(`_prewarm_pool` + `tab_id→prewarm_id` + 补池收敛)挂在
  `_on_unit_state_change` 的 `new_state==DEAD` 分支即可,一个点覆盖全部 kill 路径**,
  不会漏 `_evict_idle`(它也走 `victim.kill()`→transition(DEAD),session_router.py:2072)。
- 现有 `_release_session_state` 已是"session 结束清 module-level dict"的收口范式(多路径复用),
  新 map 收口与它同源同风格。

**这两条把 v2 剩余的实现风险从"待发现"降为"已定位到具体收口点"。**

## 5. 安全不变式(合并 PE 的 H1/H2/M1/M2)

- **锁竞争(H1)**:不声称"真实永远优先"。诚实表述:预热拿锁前 best-effort 让步,撞上已持锁
  预热时真实 spawn 等最多 14s(实测撞锁概率 ~10%)。让步计数器用 `_slot_lock` 保护,设退让上界防 starvation。
- **回收统一收口(H2,收口点已定位)**:复用**现成的** `_on_unit_state_change` 回调的
  `new_state==DEAD` 分支(§4b)——所有 kill 路径(adopt-转正/cancel/TTL/orphan/shutdown/
  `_evict_idle`/crash/poison)都经 `_transition(DEAD)` 命中它,一个点收口 `_prewarm_pool` +
  `tab_id→prewarm_id`,不用加新 `on_kill`、不用在各 kill 点重写。single-writer,`_slot_lock`
  保护,kill 与 map 清理同事务。给 SessionUnit 加 `is_prewarm` 标志供该分支判别。
- **孤儿豁免(H3/M1)**:`_check_orphan_sessions` 加 `prewarm-` 前缀豁免(新代码,非既有);
  60s TTL 是运行期主回收;`SWARMAI_OWNER_PID` 双保险**只兜 daemon 崩溃重启**,运行期泄漏靠 single-writer。
- **无泄漏回归门**:压测循环开关 tab,断言进程数回落基线 + 两 map 清空 + 无无主进程,mutation-proven。

## 6. 修复路线(架构两分为地基;Slack + Desktop 统一)

分两个阶段:**先修既有 bug 摘果实,再落架构两分,prewarm 是两分的产物**。

### 阶段一:修共享 bug(不依赖任何新架构,先见效)
1. ✅ **[已完成调研] C2/P-c SDK 能力** —— `query()` 无 system 参,recall 走 query_content 前缀(§3a)。
   - **1b(残留一条实测)**:recall 放 query 前缀 vs system 尾部对 Opus 的等价性。pipeline 内顺带验证。
2. **修 P-a + P-b(一个 bugfix pipeline,共享根因)**:
   - P-a:`_check_orphan_sessions` 豁免 `prewarm-` 前缀(现误杀 Slack 预热单元,日志坐实)。
   - P-b:poison-guard 加 ~~`_sdk_session_id is not None`~~ **`startswith(PREWARM_SESSION_PREFIX)`
     (交付更正,Gate-1 F1;见顶部块)**(fresh 豁免,真 zombie 仍杀,mutation test)。
   - **单独就让现有 Slack prewarm 复活**(adopt 0→有),不依赖两分/desktop 新代码 —— 最低垂果实。
3. **验证 Slack prewarm 真实收益**:量 adopt>0 且首消息 TTFT 真降。也顺带验证 default-warm 机制可行。
   - 🔴 **未做(诚实标注,2026-08-17)**:阶段一交付只在**单元测试层**证明(P-a/P-b 的 killer 豁免 +
     adopt 路由,204 tests green + Gate-2),**没有生产 adopt>0 / TTFT 实测**。原因:本机
     `channels` 表 0 行 → `channel_gateway=not_started` → **prewarm 路径根本没激活**,当前环境结构上
     无法采到 adopt/TTFT 数据。此验证**必须在一个配了 Slack channel 的环境**(生产/beta)才能做,
     记为 **OPEN**。收益是"预期"而非"实测",交付诚实边界。

### 阶段二:落架构两分(prompt builder 显式拆分,prewarm 成为产物)
4. **`default_builder` / `dynamic_builder` 显式拆分**(§0b)——full pipeline,是真正的架构改动:
   - default:11 files 全量 + 安全/datetime/runtime,输出按 (session_type × model) 分桶。
   - dynamic:recall + briefing + UI-SENSE + pre-round message,输出走 **query_content 前缀**(非 system)。
   - **R26 strangler-fig**:旧单体 `build_system_prompt` 路径保留到两分路径通过集成测试;
     不 big-bang 替换(这是 chat 核心,回归高危区)。
   - **R27 契约迁移**:grep 所有 `build_system_prompt`/`build_options` 消费者(spawn/resume/channel/
     prewarm),确认每个都迁到新边界或明确保留旧路(resume 走旧路,它是纯 dynamic 不可 warm)。
5. **desktop prewarm 方案 A**(池深 2)——建在两分之上:池存 `default_builder` 产物 spawn 的单元,
   按 (session_type × model × agent_id) 桶 key 分桶(§0b);adopt 校验桶 key 完整;H2 统一收口。
6. **补 desktop 埋点 + 数据决定 desktop B**("addTab→首消息间隔"分布)。

**顺序理由**:阶段一是纯 bugfix、零架构依赖、Slack 立刻受益,先做;阶段二是架构地基,做对了
desktop+Slack prewarm 都变干净。**两分(步4)是 XG 指出的正解,但它不阻塞阶段一** —— 可以先用
阶段一验证机制、摘 Slack 果实,再从容落架构。也可直接从步4起做(若倾向一次到位架构),
由 EVALUATE 定。

## 7. Profile
- 前置调研 1/3/4 = research(无代码)。
- 阶段一步2(P-a+P-b)= bugfix pipeline,独立可交付、零架构依赖,Slack 立刻受益。
- 阶段二步4(prompt builder 两分)= full pipeline + strangler-fig(R26)+ 契约迁移(R27),
  是真正的架构改动,chat 核心高危区。
- 步5(desktop 方案 A)= full pipeline,建在两分之上。
- 步6 desktop B = 数据门控,单独评估。
- 由 pipeline 在 EVALUATE 定 profile(R1)。

## 8. 设计状态(2026-08-16,四轮审查 + 数据实证后)—— ✅ 两阶段 FINALIZED

**§0f 用真实数据回答了第4问,阶段二真正定稿。**
- **阶段一**(P-a/P-b/Slack 复活):与三门正交、根因+修复源码坐实 → **FINALIZED**。
- **阶段二**(prompt builder 两分 + desktop 方案 A):§0f 实证 **prewarm=方案A(只省握手不碰
  prefill/cache)** → 门①③的 cache/8K 顾虑**清零**(不是 prewarm 引入的增量);门② provenance 是
  **唯一真代价**,有确定守法(`[RECALLED]`+3 回归门)、失败可观测、最坏退回 cold;第4问 trade-off
  摊开量化(省握手 17-38% TTFT vs provenance 风险),数据支持选项 C。→ **FINALIZED,provenance 列为
  BUILD 一等验收门。**

| 项 | v1 状态 | v2 定稿状态 |
|----|---------|------------|
| C1 poison-guard 杀 adopt 单元 | 未发现(地基塌陷) | ✅ 根因坐实;修复判据**交付更正为 `startswith(PREWARM_SESSION_PREFIX)`**(~~`_sdk_session_id is not None`~~ 被 Gate-1 F1 推翻,见顶部块),commit `4ec936c0` strangler-safe |
| C2 recall/SENSE prompt 固化 | 未发现 | ✅ §0f 实证:prewarm=方案A 只省握手,8K prefill/cache **非 prewarm 增量**(cold 也要付)→ 唯一真代价是 provenance,有确定守法。第4问 trade-off 量化,数据支持选项 C |
| Slack adopt=0 | 未纳入 | ✅ 日志坐实真因(orphan reaper 误杀,非 C1),与 desktop 同根,统一修 |
| H1 锁让位 | 声称"真实永远优先/零下行" | ✅ 修正为诚实表述:~90% 不撞,撞上真实等≤14s |
| H2 回收收口 | "四条路径"漏 evict | ✅ 定位到现成 `_on_unit_state_change` DEAD 分支,一点覆盖全部 |
| H3/M1 孤儿豁免/双保险 | 当既有事实 | ✅ 标注为新代码;双保险只兜崩溃重启,运行期靠 single-writer |
| M2 agent_id 校验 | 只提 model | ✅ adopt 必须同时校验 agent_id + model |
| M3 addTab→首消息间隔 | 假设 90% 命中 | ⏳ 上线埋点才有数据。**订正(二轮 HIGH-2)**:它门控 A 和 B 的**命中率**(非只 B、非有无收益);A 的价值收窄为"覆盖天然长间隔窗口"。不阻塞 P-a/P-b |
| 桶爆炸(二轮 HIGH-1) | — | ✅ 只预热主桶(owner×default-model×default-agent);放弃 model 维度分桶(1M 同构);校验≠分桶 |
| default warm staleness(二轮 MED-2) | — | ✅ content-hash/mtime 比对 + 60s TTL 作 staleness 上界 |
| M4 A vs B 定位 | B 主 A 兜底 | ✅ 翻转:A 主,B 数据门控增量 |
| **架构:prompt builder 结构** | 单体 builder 上打补丁 | ✅ **XG 正解**:显式两分 default/dynamic,prewarm 成产物;default 按 (session_type×model) 分桶;dynamic 走 query_content 前缀(SDK 约束坐实) |

**定稿判据(四轮审查 + 数据实证后,两阶段均满足)**:
- **阶段一 ✅ FINALIZED**:P-a/P-b 根因+修复源码坐实,H2 收口点已定位,与三门正交。
- **阶段二 ✅ FINALIZED**:§0f 实证 prewarm=方案A(只省握手 17-38% TTFT、不碰 prefill/cache)→ 门①③
  的 cache/8K 顾虑清零(非 prewarm 增量);门② provenance 是唯一真代价、有确定守法、列 BUILD 一等门;
  第4问 trade-off 摊开量化、数据支持选项 C。收益域(96% 首消息触发 recall,故 prewarm 价值≈全首消息)
  已用数据划清。
- **上线才知的 M3**:门控 A/B 的**命中率**(非有无收益),不阻塞任一阶段。

**元教训(四轮收敛,最痛也最值)**:方向我从头就对(TTFT 是 cold-spawn、prewarm 是解、builder 该两分),
但我**连续三轮高估收益、低估风险**——一轮假设复用现成设施(漏 C1/Slack 早失效)、二轮假设换位置等价
(漏 cache/confabulation)、三轮用错误论证得到碰巧对的结论(把"注入一次"当"影响一次")。**每一轮都是
XG"别着急开工、再对抗审查"逼出来的。** 而第四轮我终于做对了一件事:**不再推演,去查真实数据**——
一查就发现前三轮纠结的 8K prefill 根本不是 prewarm 的增量(prewarm=方案A 不碰 prefill),整个争论自解,
真正的代价收敛成 provenance 单一变量。**最深的教训:当一个设计问题反复纠缠、每轮都冒出新顾虑时,
往往不是问题真的复杂,是我在用推演代替测量——真实数据一到,伪顾虑自己消失,真代价自己浮现。设计的
诚实,终点是数据,不是更周密的推理。**

**推进选项**(等 XG 指令,不自动开工):
- **A. 先摘果实(建议)**:阶段一(P-a+P-b bugfix)→ Slack prewarm 复活。**这一步不碰选项 C、不碰
  两分**,纯修既有 bug,零架构风险,还能实测"prewarm 到底省不省时间"给阶段二的收益建模提供基准。
- **B. 先补阶段二地基**:把 §0e 的四项(收益域/TTFT 建模/provenance/主桶)做成一次 research,
  彻底想清楚"有 recall 首消息到底值不值得 prewarm",再决定阶段二做不做、怎么做。
- **我的建议:A 先行**——它是纯收益、零争议;阶段二的核心张力(prewarm×recall)可以用 A 跑出的真实
  数据来回答,而不是继续在设计文档里推演。
