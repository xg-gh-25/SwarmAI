---
title: Pipeline v1 — revisions to the v13 design text
created: 2026-10-23
updated: 2026-10-23
status: for XG to apply to the v13 design (KiroCrew does not write ~/.swarm-ai)
---

# Pipeline v1：对 v13 设计文本的修订

v13 设计（`~/.swarm-ai/SwarmWS/Projects/SwarmAI/2-understanding/knowledge/designs/2026-10-21-pipeline-structural-decisions.md`）按它的 §F 规则由 XG 或 SwarmAI 写入。KiroCrew 不写 `~/.swarm-ai/`。下面每条给出：要改的章节、现在的文字、改成的文字、理由和出处。最后一节是要加进附录 2 的修订记录行。

依据有两份：conformance fixing spec §4 和 §6，以及 `docs/design/pipeline-v1-remaining-design.md` r6（XG 2026-10-23 在会话中批准 §8 的五项和 auth 时间规则，r6 的 frontmatter 已同步更新）。代码已经按下面的文字实现，commit 见 `docs/design/pipeline-v1-fixing-log.md`。

## 1. §B3.4 存储（三层）：final 改为每个 repo 一份

- **现在：** 结论层是 `evidence/tests/final.json`，所有 repo 共用一个文件。
- **改为：** 结论层是 `evidence/tests/final-<repo>.json`，每个 repo 一份，各带自己的来源记录封印（§A3.6）。读取方（TS-1、LG-1、LG-2、LG-5、push_ready）把它们合成 `{repos: {<repo>: …}}` 再判定，结构和原 `final.json` 相同。
- **理由：** 多仓库 run 里各 repo 的 code_tree 和 base 本来就分开，分别续跑、分别封印。一个聚合文件只会让一个 repo 的重跑重写另一个 repo 的结论。
- **出处：** fixing spec L7 / §4。

## 2. §C4.1 布局：cache 路径

- **现在：** `~/.swarm-ai/state/pipeline_cache/<kind>/<key>`。
- **改为：** `~/.swarm-ai/cache/pipeline_test/v1/<kind>/<key>`（runner 的跨 run 结果缓存，按不可变输入寻址，只增不改，规则不变）。
- **理由：** 缓存可以重建，属于 `cache/`，不属于需要备份的 `state/`。
- **出处：** fixing spec L7 / §4。

## 3. §A3.1 事件信封：测试明细的位置

- **现在：** "原始答案、prompt 全文和测试明细一律放进 blob。"
- **改为：** "原始答案、prompt 全文放进 blob。测试明细写进工作区里带封印的 evidence（`evidence/tests/calls/`），事件里只放 receipt 和 hash。"
- **理由：** 封印已经提供同等的防篡改能力（§A3.6），再把明细搬进 blob 只多一份拷贝。
- **出处：** fixing spec L7 / §4、§6 Q3。

## 4. §A3.7 树的身份：第 2 步之后的失败

- **现在：** "第 2 步之后任何一步失败，都不产生 commit。"
- **改为：** "第 2 步之后任何一步失败或竞争，都不产生**未记录的** commit。已经提交的 repo 和失败的 repo 写进签名的 `partial_land` 事件（`landed`、`failed`、`detail`），也写进 REPORT。之后分两种情况：
  - 普通 DELIVER：走未落地路径，run 以 `completed(not_landed)` 结束，reason 为 `partial_land`；
  - 人批准的 `run-land`：只记录事件，返回当前生命周期状态，由人决定下一步。"
- **理由：** 多个 repo 无法放进一个原子 git 事务（K2 的结论）。能保证的是每个 commit 都有记录，并且记录之后不会再继续往下落地。
- **出处：** fixing spec F8 / §4、fixing log §12。

## 5. §A3.6 和 §A4.2：auth 的时间字段

- **现在：** §A4.2 的 `auth` 有 `decided_at`，`auth_consumed` 有 `consumed_at` 和 `by`；§A3.6 的来源记录要求生成时间。
- **改为：**
  - `auth` 的决定时间就是该事件签名的 `at`。
  - `auth_consumed` 的消费时间就是它自己的 `at`。不设 `by` 字段：执行这次授权的落地事件（`land` / `partial_land`）用 `auth_consumed_event_id` 指回它，oracle 的 `oracle_receipt` 证明它被消费过一次。
  - final 的生成时间是 `generated_at`，写在记录哈希之内。
  - 删去 `decided_at`、`consumed_at` 和 `by` 三个字段名。
- **理由：** 事件的 `at` 已经签名，在它旁边再放一个时间字段，就成了同一事实的第二个来源。
- **出处：** fixing spec L5、r6 §4。

## 6. §C6.2：删除跨 run 复用审查答案

- **现在：** "只有 prompt sha + 被审的 code_tree + 审查者定义的 sha + 模型 id 完全一致时，才复用已有答案，记一条 `answer_reuse` 事件。"
- **改为：** "审查答案不跨 run 复用。每个 run 的每次派发都由本 run 的专家回答，没有 `answer_reuse` 事件。run 内的复用规则见 §C5.2。"
- **理由：** 为了这一点 Token 节省，要多维护一条答案来源和一套缓存键。XG 选了 c，即删除。
- **出处：** fixing spec §6 Q3，fixing log §15。

## 7. §B3 / §A2.3：BUILD、TEST 的衔接（r6 §2.3）

在 §B3.5 规则末尾加：

- **推进方式：** 以下响应带 `next`，即下一步动作和仍然失败的检查（纯函数，只读签名事件、检查结果和 stage card），所以不需要单独的 `stage-start`：
  - `pipeline_v1.py` 的每个正常响应；拒绝和基础设施错误只给出错误本身；
  - `pipeline_test.py` 在 tdd、probe、regression、verify、recheck、validation 模式下写完 final 的响应。

  worktree 由进入代码阶段的那次调用幂等创建。
- **自动完成：** runner 的 BUILD 或 TEST 结果通过该阶段的全部检查时，阶段自动完成。TEST 还要求每个 repo 的 final 都是 green，并且 LG-5 已通过。只有 run 自己的会话才会触发自动完成，完成时在操作锁内核对阶段，阶段已经变了就拒绝。
- **不变的规则：** TS-1 仍然是 TEST 的完成条件，red 或 incomplete 时由 agent 手动完成。

## 8. §E5 Token Cost：主 agent transcript 不可得

在 BLOCK 条件"无法统计子 agent 和重试的 Token"后加：

- **主 agent 的 transcript 拿不到时：** 记一条 `metric(usage_not_available, reason)`，Token Cost 为 WARN，`not_available` 字段写明原因。
- **派发过专家但一个都没计量时：** 仍然是 BLOCK。
- **谁来计量：** daemon 在 REFLECT 结束时从 run 的 transcript 计量，agent 不调用计量命令。
- **出处：** r6 §2.3。

## 9. 只支持 v1 run：给旧 pipeline 段落加退役标注

v13 第 4 行已写明"只支持按 v1 协议新建的 run。历史 run 不迁移、不兼容"。下列段落描述的是切换期间与旧 pipeline 的关系，或旧代码的位置。旧 pipeline 已整体删除（R1–R7），标为"已退役"的段落前加一行 `> 已退役（2026-10-23）：旧 pipeline 已删除，本段只作历史记录。`；仍然有效的决定只加执行注记。

| 位置 | 标注 |
|---|---|
| §D1 第 2、3 条（"main 上的旧 pipeline 照常工作，直到切换"、R26 的说明）和回滚说明（"旧 run 的数据目录不动，回滚后仍然可读"） | 已退役：切换期间新旧 pipeline 的关系 |
| §D1 切换前提 3（"没有在跑的旧 run"） | 已退役：切换前提 |
| §D3.3 表中"新旧 pipeline 并存"一行 | 已履行的决定（仍有效：不并存），只加注"已于 v1 切换时执行" |
| §C2 表中以 stage-start 为主语的问题行（第 822 行"stage-start 在 worktree 创建失败时退回共享 checkout"） | 已退役：旧行为（v1 已改为 paused，且 stage-start 已删除） |
| Part F 中指向旧代码的部分："可复用的现有代码"表里的 `artifact_cli.*` 等旧模块位置，以及"main 在实施期间持续前进"一行（`artifact_cli.py` 是热点文件） | 已退役：旧代码位置。Part F 的阅读顺序、安全边界和交付标准仍然有效，不标 |
| 附录 1 全部（旧实现的问题定位，含 `artifact_cli.py:<行号>` 和其他旧模块行号） | 已退役：整节加一行标注，不逐行改 |
## 10. 附录 2 要加的修订记录行

```
| v13.1 | v1 实施后的文字修订（KiroCrew，conformance fixing spec §4/§6 与 r6（XG 2026-10-23 批准））：final 按 repo 一份（§B3.4）；cache 在 ~/.swarm-ai/cache/pipeline_test/v1（§C4.1）；测试明细在封印的 workspace evidence（§A3.1）；第 2 步之后不产生未记录的 commit，partial_land（§A3.7）；auth 时间即事件 at（§A3.6、§A4.2）；审查答案不跨 run 复用（§C6.2）；每个响应带 next，BUILD/TEST 结果通过即自动完成，TEST 另要 green 与 LG-5（§B3.5）；主 agent transcript 不可得记 usage_not_available，Token WARN（§E5）；旧 pipeline 相关段落标注已退役（§D1、§C2、Part F 的旧代码位置、附录 1；§D3.3 注明已执行） |
```
