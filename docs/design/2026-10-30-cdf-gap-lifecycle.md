---
title: CDF：gap 的关闭
created: 2026-10-30
updated: 2026-10-30
project: SwarmAI
status: 待确认
follows: 2026-10-29-cdf-refactor.md §8
---

# CDF：gap 的关闭

状态：设计 v2，待 XG 确认。承接 `2026-10-29-cdf-refactor.md` §8。v1 经过一次零上下文评审，
它的前提（让 DDD 读侧去认 CDF 的关系表）被推翻，见 §5。

## 0. 结论

- **谁修的，谁声明。** gap 几乎都是 S3 从 pipeline run 的 REFLECT 报告里写出来的，
  而修 gap 的通常也是 run。写 REFLECT 报告的是这个 run 的 agent，它最清楚自己修了什么。
  所以由它在报告里声明“关掉了哪几个 gap”（按 id 引用），CDF 做确定性复核，然后把这几个 gap
  改成 `- [x]`。全程不用判官，不加新关系，也不改 schema。
- **读侧今天就认 `- [x]`**：`knowledge find`、recall 都会跳过它（`addressing.py:72`、
  `recall_multi.py:1049/:1083/:1222`），Brain 打包原样带着文件走，Hive 上也一样。
  人工关闭走同一个标记，现在就能用。
- **能关多少**：10-29 的 12 个 gap 里有 7 个当天就修好了。其中 4 个有 run 事件报告了修复
  （`run_cddd5362` 修了 3 个，`run_b3dcb4e8` 修了 1 个），这套机制能自动关掉；
  另外 3 个是修了但没有任何单元提过，只能人工关。
- **顺带要改的两处**：查重时，已关闭的 gap 不能把复发的问题当成重复丢掉；
  `deliver.md` 里“RF-4 会点名候选条目”这句话是假的（`checks.py:510` 只做格式校验），要改。

## 1. 现状（本轮实读）

- **gap 从哪来**：S3 主通道不调模型，单元直接来自 run 在 DELIVER 阶段写的五部分报告
  （`sources/s3_run.py:56` `enqueue_report`；格式见 `skills/s_autonomous-pipeline/stages/deliver.md:3-9`，
  校验在 `run_protocol/reflect.py:36` `prepare`）。写到项目 IMPROVEMENT 的
  “What to Watch For”，块尾带 `gap kind: <kind>` 和 `<!-- cand:… -->`。
- **gap 和修复之间没有确定性的连接**：每个 run 的 finding id 都不同，修复 run 的需求里
  也没有记录它在修哪个旧 finding（`run_cddd5362/REQUIREMENTS.md`）。
  run 的 agent 今天也看不到项目里有哪些 open gap：`pipeline-v1 events`
  （`cli_commands.py:409`）只列本 run 的事件。
- **读侧**：`- [x]` 在 find、recall 里都被跳过（见 §0）；`brain_sync.collect`
  （`brain_sync.py:179`）和 `ddd_packager` 原样拷文件。
- **查重**：`card3.load_corpus`（`card3.py:71`）不区分条目是否已完成，
  `pipeline_v2.py:620` 遇到 `same` 命中就以 `already_exists` 拒绝。一个修好后又复发的问题，
  会因为和已关闭的 gap 相同而被丢掉。
- **信任**：S3 的事件全部带污染（线上 5/5），因为 run 读过仓库。所以
  “只有未污染的单元能关 gap”这条规则一次都不会放行，信任必须换一种方式约束（§3）。

## 2. 设计

1. **把 open gap 给 run 看。** `pipeline-v1 events` 的输出里加一段 `open_gaps`：
   本 run 所在项目 IMPROVEMENT 里所有未完成、带 `gap kind:` 的条目，每条给出
   `{id: "cand:…", title, gap_kind}`。agent 本来就要调这个命令拿 evidence 锚点，不用新命令。
   gap 多了以后，按本 run 的需求文本用 BM25 取前 20 条，不再全部列出。
2. **报告里声明。** `delivered` 里的事件单元新增可选字段 `closes: ["cand:…"]`。
   `deliver.md` 加一句：本 run 修好了 `open_gaps` 里的哪条，就写进 `closes`，
   并在 `outcome` 里写明它是怎么修好的。
3. **prepare 时确定性校验**（`reflect.prepare`）：`closes` 里的每个 id 都必须出现在本 run
   拿到的 `open_gaps` 里，否则整份报告被拒，agent 当场就能看到原因。
4. **CDF 写入时关闭。** 事件写入成功后，对 `closes` 里的每个 gap，
   用 `governed_edit.apply_user_edit(actor="cdf")` 把该行的 `- [<type>]` 改成 `- [x]`，
   并在行尾加上“（已由 <run_id> 关闭：<事件标题>，<日期>）”。cand 注释保持不动，
   所以锚点和 unit id 都不变（`unit_id` 在算 id 之前会去掉注释）。
   版本冲突（用户正在改这个文件）时，下一轮维护会重试。
   事件本身没写入（被拒或推迟）时，不关闭任何 gap。
5. **查重排除已完成的条目**：`same` 或 `reinforce` 命中一个 `- [x]` 条目时，降为
   `derived_from`。复发的问题会作为新 gap 写入，并链接到旧的那条。
6. **人工关闭**：直接改成 `- [x]`。10-29 修好的 7 条，先逐条对照代码确认，再这样关闭。

## 3. 信任

S3 的事件都带污染，所以关闭不能以“未污染”为条件。实际起约束作用的是：
- 只能关掉系统自己列给这个 run 的 gap（第 3 步）：同项目、确实是 open gap，
  外部内容没办法让它去关一个它没被列出的条目。
- 只有 run 真的落地了代码才能关闭（`land` 的结果是 landed）。research run 和没落地的 run
  修不了代码，`closes` 会被忽略，同时记一条事件。
- 关闭是可见、可撤销的：文件里能看到改成了 `- [x]`，也写明了是谁关的；TIMELINE 里的事件
  带着 `closes` 列表；要撤销，把那行改回去就行。

剩下的风险：仓库里被注入的内容可以诱导 agent 声称“修好了”某个确实列给了它的 risk gap。
同样的注入本来就能诱导 agent 写出错误的代码，关闭 gap 并没有扩大这个攻击面，
而且关闭的记录都看得见。

## 4. 不在本次范围

- **CDF 在 DDD 里的“取代”**：现有 12 条活跃的 supersedes 全部来自已经删掉的 refine/replace
  路径，抽查约一半是把不相干的说法配成了对（例：“两个默认 Brain”被“Plan B”取代；
  正确的 `npm ENOTEMPTY` 被写反了的 `ln -s` 取代）。现在没有读者执行它们，这反而是对的。
  新流程下同文件 `contradicts` 只记一条关系，DDD 里两条都还显示。要不要在同一个 plan 里
  给旧条目打 `superseded_by:`、要加什么信任门槛，单独设计。
- 不是 pipeline 的会话（S7）修好 gap 后的声明：先看 run 这条路的效果。
- Brain Hub 里“已关闭”的专门展示。

## 5. 为什么不是 v1

v1 的做法是：关系表作为权威，所有读者都去查它，打包时把结果应用进包里，再加一个由判官认定的
`resolves` 关系（需要 schema v9）。评审发现：
- 现有关系大约一半是错的，让读侧执行它们会把仍然有效的决定藏起来（HIGH）。
- 关系锚点用的是 SwarmWS 相对路径，三个读者各用各的 key，大小写不同的项目目录也对不上（HIGH）。
- 打包器的过期检测、查重语料、写侧 predicate 的过滤，都漏改了（MED）。
- 回过头看，两条真正报告了修复的都是 S3 的 run 事件，写这些事件的 agent 本身就有完整的上下文。

v2 去掉了 schema 迁移、新关系、打包改写和判官那一路，只用现成的 `- [x]`，以及现成的受管编辑路径。

## 6. 验证计划

**最终测试集（实现前先写，应当是红的）：**
1. `pipeline-v1 events` 列出本项目的 open gap，不包括已完成的和别的项目的。
2. `prepare` 拒绝 `closes` 里没被列出的 id。
3. 落地的 run：事件写入后，gap 那一行变成 `- [x]` 并带上关闭说明，unit id 不变，
   `knowledge find` 不再返回它。
4. 没落地的 run，或事件没写入时，gap 保持 open。
5. 用户同时在改这个文件时不会覆盖用户的修改，下一轮会重试。
6. 复发的问题命中已关闭的 gap 时，作为新 gap 写入，不会被判成 `already_exists`。

每个修复都跑 `scripts.mutation_check`。

**回放**（数据副本，10-29 的 run）：把 `run_cddd5362`、`run_b3dcb4e8` 的报告补上
`closes` 后重放。期望正好关掉那 4 个 gap，其余 8 个保持 open。这次不需要真实判官。

**评审**：correctness + security 各一位，看 `reflect.prepare` 的校验、
关闭的写入路径和信任约束。`deliver.md` 的文字由评审一起看。
