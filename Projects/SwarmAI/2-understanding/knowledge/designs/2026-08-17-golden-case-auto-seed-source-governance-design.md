---
title: "Golden-Case 自动播种源头治理 — 守住入口的 bar"
date: 2026-08-17
tags: [eval, golden-case, auto-seed, source-governance, C044]
project: SwarmAI
status: design-for-review
---

# Golden-Case 自动播种源头治理

## 1. 问题(用户原话:"源头得守住 bar,不能留尾巴")

全扫 golden set 时删了 52 条垃圾,其中 50 条是**自动播种**的:
- **31 条 behavior draft**(`GS_<epoch>:<id>`)—— correction → skeleton 播种路径
- **19 条 `GS_HARVEST_*` llm draft** —— 低分 session → harvest 播种路径

这些不是偶然攒的,是**两条自动管道持续生产的**。删掉这批,下周又会攒一批。要闭环,必须治源头。

## 2. 根因(读透源码 + 解码时间戳后的确证 —— 两次推断已被数据推翻,以此为准)

### 2.1 两条播种管道(都真实存在、都在跑)

| 管道 | 触发 | 产物 | 源码 |
|---|---|---|---|
| **A · correction→skeleton** | 每 session 后,`governance_router.classify_new_corrections` 对 cognitive CLASS_A/B/C(`pending_confirm`)correction 调 `seed_from_correction` → `eval_service.auto_seed_case` | `tier=draft` `eval_method=behavior` 的 trajectory skeleton | `governance_router.py:272`, `eval_hooks.py:81`, `eval_service.py:648` |
| **B · session→harvest** | 每周 `session_quality` job,低分 session(min(goal,tool)<0.6)调 `harvest_draft` | `tier=draft` `eval_method=llm` 的 `GS_HARVEST_*` | `session_quality.py:88`, `session_harvest.py:122` |

### 2.2 已存在的 decay 机制 —— 没坏

`eval_service.reclaim_stale_skeletons(ttl_days=30)`,每 session 由 `context_health_hook:284` 调。
逻辑正确:`auto_seed_skeleton` 标签 + 仍带 placeholder marker + `source` 里 `<epoch>:<id>` 年龄 > 30 天 → 回收。

**关键数据(解码时间戳):** 31 条 behavior draft 最老的才 **29.3 天**(2026-07-18),大多是最近 2-3 周。**全部未到 30 天 TTL —— 所以它们本来就该还在,reclaim 不回收是对的。机制没 bug。**

### 2.3 真正的根因 —— 一个"假设人会精炼、但人永不精炼"的死循环(设计哲学问题,非 bug)

系统设计意图:`correction → 播种 skeleton → 等人精炼 → 精炼则留 / 30天没人精炼则回收`。逻辑自洽。

**现实:从来没有任何一条 skeleton 被人精炼过。** 于是:

```
播种 draft → 挂 30 天(占位、零信号)→ TTL 回收 → 又播种新的 → 再挂 30 天 → ...
```

**永远有一批 0-30 天的半成品 draft 挂在 golden set 里** —— 这就是你每次看到都觉得脏、我这次删掉 50 条的东西。它不是 bug 在发作,是一个**人端永不闭合的循环在稳态运转**。

补充:harvest 端(B)的 `GS_HARVEST_*` **连 TTL 回收都没有**(`reclaim_stale_skeletons` 只认 `auto_seed_skeleton` tag,harvest draft 没这个 tag)—— 所以 B 产的垃圾是**只进不出**,比 A 更糟。这 19 条如果我不手动删,会永远留着。

## 3. 设计选项(SPEED · QUALITY · SIMPLICITY · DELETION 四镜头)

### 选项 A —— 关掉自动播种(DELETION)
两条管道都停:correction 不再自动播种 skeleton;harvest 不再自动 land draft。
- **优点**:源头彻底断,golden set 只由人经 `s_golden-case` 手工进(最高 bar)。零维护、零垃圾。
- **缺点**:丢掉"自动从真实失败发现测试缺口"的信号 —— correction/低分 session 里确实藏着该测的东西。
- **风险**:把孩子跟洗澡水一起倒了。

### 选项 B —— 播种但不进 golden set,进"候选队列"(QUALITY,推荐)
两条管道**不再写 golden_set.private.yaml**,改写一个**独立的 `eval/seed-candidates.jsonl` 候选文件**。
- correction/harvest 照常发现信号,但产物落在**候选区**,不污染 golden set。
- 候选区在 briefing / 一个 review 命令里呈现:`s_golden-case` ADD 时可以"从候选精炼"(把候选炼成真用例才进 golden set)。
- golden set 里**只有够格的用例**(人炼过 OR 机制确定性的),bar 守死在入口。
- **优点**:既留住"自动发现缺口"的价值,又让 golden set 永远干净。候选区可以脏(它就是 to-do 池),golden set 不能脏 —— 两者物理隔离。
- **缺点**:要新建候选区的读写 + 一个呈现/精炼入口(中等工作量)。
- **这是 C044 的正解**:"用例能过≠有用" → 把"未验证的半成品"挡在 golden set 之外,而不是放进去等回收。

### 选项 C —— 播种即够格(SPEED,存疑)
让 `auto_seed_case` / `harvest_draft` 直接产**有牙的完整用例**(不是 skeleton)。
- **缺点**:`auto_seed_case` 的 docstring 已诚实承认做不到 —— "raw correction 只有 failure CLASS,没有 crafted 的 efficient-but-wrong 场景;自动生成的泛化 rubric 是 tautology,称职 agent 轻松过"(`eval_service.py:658`)。**机器能发现 WHAT 要测,设计 HOW 测是人的活。** 强行让机器产完整用例 = 产更精致的垃圾。**否决。**

## 4. 推荐:选项 B(候选队列)+ 一个立即的止血

**为什么 B:** 它同时满足你两个要求 —— "守住 bar"(golden set 入口只放够格的)+ "不留尾巴"(自动发现的信号不丢,进候选区)。A 太激进(丢信号),C 做不到(机器产不出够格用例)。B 把"脏"和"净"物理隔离:候选区允许脏,golden set 保证净。

**改动范围(全部走 pipeline,R1):**
1. `auto_seed_case`(A管道)+ `harvest_draft`(B管道):sink 从 `golden_set.private.yaml` 改为 `eval/seed-candidates.jsonl`。
2. 候选区去重 + TTL(candidates 可以有更短 TTL,比如 14 天,因为它不占 golden set)。
3. `s_golden-case` 加一个 "从候选精炼" 的 ADD 子流程 —— 人挑候选 → 炼成有牙用例 → 走 4-gate → 进 golden set。候选原始条目消费掉。
4. `reclaim_stale_skeletons`:A 管道不再产 golden-set skeleton 后,此函数只需处理历史遗留(可保留一轮做清理,之后删)。
5. briefing 呈现候选区计数(refine-me 面包屑,从 golden set 移到候选区)。

**契约迁移检查(R27):** `auto_seed_skeleton` tag、`GS_HARVEST_` id 前缀、`reclaim_stale_skeletons`、briefing 的 draft 计数、`compute_bvt` 的 `eval_method=behavior` 排除 —— 全部 consumer 要 grep 到并迁移。这是个 cross-boundary 改动。

## 5. 立即止血(不等 pipeline,已在本轮做)
- 已删 50 条自动播种垃圾 + 2 条全扫新发现,golden set 201→149。
- **但源头未断** —— 若不做本 design,下个 session 的 correction 又会经 A 管道播种新 behavior skeleton。**这就是"尾巴"。** 本 design 落地才算闭环。

## 6. 附:probe bug(独立小尾巴)
`memory_chain_probe.py:207` 用了已废的 `upsert_chunk(embedding=)` 参数(vector 拆除遗留),导致 `GS_MCHAIN_ARCHIVE_RECALL` canary FAIL。这不是 golden case 的错,是被探测的 probe 自身 rot。已标 `known_broken_probe` 保留(它抓到了真 rot)。修复并入本 pipeline 或单独 trivial run。

## 7. 用户决策(2026-08-17):全自动,无人介入,守住入口 bar → 选项 D

用户否决了 A(丢信号)/B(依赖人精炼)/C(机器直接产够格用例—做不到)。要求:
**全流程自动化,不让人进来,但入口有一道自动质量门。** 这与 SwarmAI cultivation 的
autonomy-first(run_86f44f35)同构:judge 过→自动写,judge 不过→自动丢到可恢复 archive,
human-review 队列 = 0。选项 D 把同一哲学套到 golden-case 播种。

### 选项 D —— 全自动生成 + 自动 teeth 门 + 不过门自动丢弃

**核心:消灭 `tier=draft` 中间态。** 不再"播种半成品挂着等人",改为:
```
correction / 低分 session
   → 自动生成【完整用例 + 一个应被 FAIL 的负面样本】
   → 自动质量门(4-gate + teeth 自测)
       ├─ 过门 → 自动进 golden set(tier=active/behavior,直接计分)
       └─ 不过门 → 自动丢弃到 archive(可恢复,不留 draft、不占位、不等人)
```

### D 的成败全压在"自动 teeth 门能否判出有牙 vs 同义反复"

`auto_seed_case` 注释断言"机器产不出够格用例"—— D 下这仍是最大风险:门若判不出 teeth,
全自动 = 自动产垃圾。解法 = **knockout 自测,复用 eval OS 已有机制,不新造**:

1. **生成器同时产一个负面样本**:一个体现该失败类的"错误答案/错误轨迹"(应该被 FAIL)。
2. **自动跑 teeth 门**:
   - behavior 用例 → 用 `_judge_decision_direction`(pinned judge)判负面样本 → 必须 `failed`。
   - llm 用例 → 用 `eval_llm_judge` 判负面样本对 assertions → 必须 `failed`。
   - canary 用例 → 已有的 `_verify_canary_teeth`(negative_command 必须 RED),原样复用。
3. **路由**:负面样本被正确 FAIL → 用例有牙 → 进 golden set;负面样本也 PASS → tautology → 丢。

**防自欺(关键)**:负面样本 + teeth 门必须是**独立对抗 stance**——门用 **pinned judge**
(与生成器不同视角,eval OS 已有此隔离),否则生成器产"故意好过"的弱负面样本自成闭环。

### D 的改动范围(全走 pipeline,R1;cross-boundary,R27)
1. `auto_seed_case`(A管道)+ `harvest_draft`(B管道):产物从 skeleton 升级为
   {完整用例 + negative_example};sink 从"直接 append golden set"改为"过 teeth 门后路由"。
2. 新增自动 teeth 门函数(behavior/llm/canary 三态),复用现有 judge。
3. **`tier=draft` 退役**:`reclaim_stale_skeletons` 保留一轮清历史遗留后删;briefing 的
   draft 计数改为"本周自动进 N / 自动丢 M"的健康信号。
4. 契约迁移(R27 grep ALL):`auto_seed_skeleton` tag、`GS_HARVEST_` 前缀、
   `reclaim_stale_skeletons`、`compute_bvt` 的 behavior 排除、briefing draft 计数、
   `s_golden-case` 文档 —— 全部 consumer 迁移。
5. 丢弃走可恢复 archive(`discarded-seed-candidates.jsonl`),对齐 cultivation 的 discard 语义。

### D 的验收(必须有牙,自证 teeth 门真有效 —— 否则重蹈 C044)
- 一个**已知 tautology 用例**("PASS if agent consults AGENT.md")喂进门 → 必须被**自动丢**。
- 一个**已知有牙用例**(19 条标杆之一)喂进门 → 必须**自动过**。
- 两个 case 都要 mutation-proven:门坏了要变红。这是 teeth 门自己的 teeth。

## 7.5 Gate-0 skeptic 修正(run_1bfd3cf9,SUPPORTED + 2 处必须吸收)

零上下文对抗子 agent 读真源码后裁决 SUPPORTED,但抓到 design 两处缺陷,已并入 PLAN:

1. **teeth 门不能一门通吃两条管道。** `_judge_decision_direction`(eval_runner.py:1266)只服务
   behavior/trajectory(A 管道,有 decision_rubric);harvest 的 `GS_HARVEST_` 是
   `eval_method=llm`、走 `goal_success`、**无 decision_rubric** → 它的 teeth 门必须用
   `eval_llm_judge` 单独建。**两条管道 = 两个 teeth 门形态**,BUILD 不得假设一个覆盖两个。

2. **比 TTL 缺口更急的真漏洞:harvest llm draft 能污染 headline 健康分。** design 原先信了
   "draft 不进分数"——skeptic 证明这**只对 behavior 路径成立**(eval_runner.py:1404 硬 skip
   tier=draft);**llm 路径无 tier=draft 过滤**,`compute_scores`(:1655)也不按 tier 过滤 →
   `GS_HARVEST_`(llm+draft)一旦进 scored run 就计入 headline `overall`。regression GATE
   (compute_bvt 的 `_GATE_TIERS`)正确排除了 draft,但 headline 百分比没有。**这是独立真 bug,
   随本案一起修**(新增 AC8)。

**新增验收:**
- AC8: `compute_scores` / llm 评估路径对 `tier=draft`(及未来的 candidate 态)的处理与
  behavior 路径一致——draft 永不计入 headline `overall`(mutation-proven:反转则测试变红)。

## 8. 附:probe bug(独立小尾巴)
`memory_chain_probe.py:207` 用了已废的 `upsert_chunk(embedding=)`(vector 拆除遗留)→
`GS_MCHAIN_ARCHIVE_RECALL` canary FAIL。已标 `known_broken_probe` 保留(它抓到真 rot)。
并入本 pipeline 一起修,或单独 trivial run。

## 9. 待用户拍板(缩到最后 2 个)
- **D 方案认可?** 特别是"自动 teeth 门 = knockout 自测 + pinned-judge 防自欺"这个核心。
- **probe bug** 并入本 pipeline,还是单独 trivial run?
