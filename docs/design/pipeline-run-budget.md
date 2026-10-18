---
title: Pipeline run 预算 — 派遣轮数、测试重跑在命令边界上强制
date: 2026-10-18
created: 2026-10-18
updated: 2026-10-18
project: SwarmAI
status: IMPLEMENTED (r3) — H1-H5 落地；实施中 Gate-2 发现的问题见 §10
related: pipeline-speed-systematic-fix.md（工作流 A-G；本文是工作流 H，补它没覆盖的"轮数和重跑"）
---
# Pipeline run 预算：派遣轮数、测试重跑在命令边界上强制

## 1. 问题一句话

每个专家的 prompt 有范围上限，测试有统一入口，但**同一个审查角度被派几轮、同一组测试在
没改动的情况下重跑几遍，都没有上限**；而测试浪费的最大来源是 runner 自身的两个缺陷
（一个测试超时连坐整个 shard；recheck 会丢掉没跑到的测试），它们让"重跑"成为唯一出路。

## 2. 实测证据（逐条归因）

| 观察 | 归因 | 谁能防住 |
|---|---|---|
| 一个 bugfix run 派了 19 个子 agent | security 在 REVIEW 审两次、Gate-2 再审一次 | 已修：每个角度一个负责方 |
| 同一 run 里 Gate-2 跑了两整轮 | 第一轮专家在中途被拒，重派了一轮 | 不是设计问题；H1 让第三轮需要理由 |
| verify / regression 多次重跑，结果相同 | 范围混进别的 session 的改动 + 超时连坐，AC 测试从没真正跑到 | 已修范围；H4 修连坐；H3 拒绝相同树重跑 |
| 一个测试超时，后面 12 个测试（含全部 AC）没跑 | `pipeline_test.py` shard 一旦被杀就整批记为 not-run | H4 |
| tdd 一个 run 24 次 | 每次都在改动之后（修复、mutation 检查），缓存管不着 | **不设上限**（攒改动是执行纪律，不是 gate 能判断的） |
| 同一工作拆成两个 run，gate 各走一遍 | 跨 run 的重复审查 | 本文**不在结构上解决**：一个系统的工作放进一个 goal run（流程规则，§8） |

## 3. 设计原则

1. **先修让浪费成为唯一出路的缺陷，再限次数。** 证据不可信时限制重跑，只会把坏证据固定下来。
2. **限制"再开始一次"，从不打断进行中的工作。** 不加 wall-clock 杀进程，不截断进行中的审查或
   测试，不是成本上限（STEERING #2）。超出 = 先用一条命令写理由，再执行（SOUL P6：上限是烟雾报警器）。
3. **只限制能被准确识别的东西。** 派遣只按 `EXPERT-ID:` 计数；没有 id 的 spawn 不拦，只在预算段里显示。
4. **复用已有机制。** spawn gate 照抄 `pipeline_test_gate` 的"按 session 找活跃 run"；归属沿用
   `runtime_hooks._extract_expert_id`；测试复用 `pipeline_test.py` 的文件树摘要和 recheck。

## 4. 机制（按实施顺序）

### H4. runner：recheck 不假绿、AC 先行、超时不连坐 —— 先做

- **recheck 不假绿（现有 bug）**：recheck 只重新瞄准 `backend/` 失败，`desktop::` / `import::` 失败被
  丢掉；上一次失败全是这两类时它选中 0 个测试并以 0 退出。改为：保留这两类（重跑对应的 smoke /
  frontend job）；上一次有失败而本次目标为 0 时，判失败，不判绿。
- **recheck 读对证据**：只取最近一次 verify / regression，不取 tdd。
- **AC 先行**：AC 目标现在排在最后，先改成排在最前（只改顺序）。AC 引用若是测试 nodeid 且所在
  文件已整文件入选，拆出来时对原文件做 deselect，避免跑两遍；AC 引用是整文件时不拆。
- **超时不连坐**：shard 被单测超时打断（进程以错误码退出，不是 deadline 杀）后，解析它的结果，
  把没跑到的测试**重排一次**（单次、不循环，排除超时的那个；serial suite 的重排仍保持串行）。
  结果按 nodeid 去重，后一次结果覆盖 notrun。同一文件第二个测试也超时 → 不再重排，按原样记录
  （防 module 级 fixture 卡死时把 deadline 耗光）。

### H3. verify / regression 重跑：自动转 recheck，不拒绝

- "相同"按已存的 `cache_key` 判断（它覆盖 HEAD、依赖、AC 列表），不是只看变更文件的摘要。
- 相同且上一次结果可缓存 → 现在已经直接返回缓存，不变。
- 相同且上一次不可缓存（有超时 / unknown / runner error）→ **自动改跑 recheck**，只跑失败和没跑到的，
  证据里注明。不拒绝、不需要理由——拒绝只会把 flaky 超时的人卡住。
- 依赖 H4 的 recheck 修复先落地，否则自动 recheck 会继承假绿。
- TEST 阶段只读 `latest-regression.json`（`artifact_cli.py` 的派生字段逻辑），不读 recheck 证据。所以
  regression 之后的 recheck 要把结果按 nodeid **覆盖回** regression 证据（重写 latest-regression，并记录
  `rechecked_from`），不另起一份证据让 gate 看不见。

### H1. 派遣轮数：从完成记录计数，在 stage 收尾时要理由

- 不新建 Agent 的 PreToolUse gate（对抗审查：现在没有任何 gate 挂在 Agent 上，hook 能否看到 prompt
  未验证；台账写 run.json 会和其他写入者竞争；且改一下 id 就能绕过）。
- 计数来源：SubagentStop 已经为每个完成的子 agent 写 marker，带 `run_id` 和 `expert_id`
  （`runtime_hooks.py` 里给 D7 commit gate 用的那份）。只数完成的 spawn，被拒、中途被停的不算。
- 计数单位：run × goal cycle × expert_id（goal run 每个 cycle 重新计）。
- `stage-complete` 时同一单位超过 2 次 → 要求在 artifact 里给 `over_budget_reasons`，否则校验失败；
  理由进 REPORT。不在 spawn 时拦，不打断进行中的工作。
- 现在只有专家 prompt 由 `_write_expert_prompts` 注入 `EXPERT-ID:`；`spawn_prompt.py` 输出的 gate prompt
  没有。给所有 gate prompt 加首行，让 gate 也被计数。没有 id 的 spawn
  记为 unattributed，在预算段里列出。

### H2. 复审规则（prompt 层）

- 第 2 次只用于修复：输入是修复部分的 diff 和上一轮 HIGH / BLOCK 项。

### H5. 预算段（真正的约束点）

- 每次 `stage-complete` 输出：各 expert_id 次数、unattributed 次数、超额及理由、各模式测试次数、
  自动转 recheck 的次数、本 stage 耗时。DELIVER 生成的 REPORT 汇总全 run。

## 5. 已完成 / 待做

| | 状态 |
|---|---|
| 每个角度一个负责方、升级阈值只按源码、REVIEW 独立清单、`dispatch_plan` 落盘、spawn 一律前台、profiler 子 agent 段 | 已实现，未提交 |
| runner 只测本 run 文件 + 点名范围外文件、shard 按优先级排 | 已实现，未提交 |
| H4 → H3 → H1/H2/H5 | 已实现（同一个 goal run，四轮） |

## 6. 验收（profiler 在真实 run 上测，不靠自述）

- regression 里 AC 测试全部真正运行；一个测试超时不再让同 shard 其他测试变成 not-run。
- recheck 在上一次有失败时绝不以 0 个目标报绿；相同 cache_key 的 verify / regression 重跑一律转为 recheck。
- 同一 run × cycle × expert_id 超过 2 次时，stage-complete 必须带理由。
- 与 §2 基线对比：重跑次数、子 agent 数、总时长下降。

## 7. 明确不做

- 不限 tdd 次数（Gate-0：改动后的 tdd 是正当迭代，缓存已覆盖未改动的情况）。
- 不按 stage 预发派遣许可（Gate-0：会默认挡掉 Gate-0 / Gate-1 / final-gate / meta-review）。
- 不在 Agent spawn 时拦截（对抗审查：可绕过、写入竞争、hook 未验证）；改为完成记录计数 + stage 收尾要理由。
- 不拒绝重跑（对抗审查：只会卡住 flaky 超时）；改为自动转 recheck。
- 不加 wall-clock 超时；不放宽任何 gate 的检查内容；不按 token 或金额设上限。

## 8. 流程规则（不靠 gate）

- 一个系统的改动放进一个 run；出现第二个缺陷时扩进当前 goal run，而不是另起 run，避免同一 diff
  的 gate 重复。

## 9. Gate-0 结果（r1 → r2 的修改）

r1 判 PARTIAL：测试浪费的主因是 runner 缺陷而非缺上限；按 stage 预发许可会挡掉必需 gate；tdd 上限
会截断正当迭代；recheck 会丢掉 not-run 测试。r2 据此：把 H4 提到最前并补 recheck 两处修复；H1 从"清单
许可"改为"按 EXPERT-ID 计轮数"；删除 tdd 上限；跨 run 重复改为流程规则并明确不在结构上解决。

r2 → r3（对抗审查）：H1 从"spawn 时 DENY"改为"SubagentStop 完成记录计数、stage-complete 要理由"，
单位改为 run × cycle × expert_id；H3 从"拒绝"改为"按 cache_key 判断、自动转 recheck"；H4 增加
recheck 假绿修复（现有 bug）、结果按 nodeid 去重、重排只做一次且同文件二次超时即停、AC 拆分时 deselect。

## 10. 实施中学到的（Gate-2 + 复审）

- **pytest 的 `--deselect` 按前缀匹配。** 拆出 `test_parse` 会连带去掉 `test_parse_empty`，且不留痕迹。
  AC 拆分改由 runner 自己的 pytest 插件做匹配：等于该 nodeid、或是它的参数化（`[…]`）/ 类成员（`::…`）。
- **自动 recheck 必须以"正在重跑的那个模式"的证据为基准**，并且只有真正覆盖回该模式的证据才算数；
  不能重新定位的失败（`shard::` runner error）、recheck 自己有预算跳过、没覆盖上 → 退回完整重跑。
- **重排只用在主运行。** flaky 重跑、HEAD 基线、mutation 不重排：被杀的 mutant 不值得再花 deadline。
- **每子 agent 的 marker 用微秒时间戳命名**，否则并行结束的同 session 子 agent 互相覆盖（轮数少计，D7 漏专家）。
- **goal profile 的 Final Quality Gate 与 DELIVER Gate-2 审的是同一份总 diff。** 本 run 把两者合成一轮
  （final gate 的跨路径 DoD 检查放进 correctness 专家），没有在结构上修。后续：goal profile 的 DELIVER
  直接以 final gate 的 Gate-2 批次为准，不再单独派。
- **仍未解决：** 前端结果缓存键只看被测文件（被测源码变了仍命中缓存）——另一个 run 在修；没有 EXPERT-ID 的
  spawn 只报警不拦（调研类 agent 本来就没有 id）；跨 run 的重复审查只靠流程规则。
