---
title: 从新建 Brain 到 Hive 单落地，不等人
status: r4 implemented (r4, XG 2026-11-01: the two dispatch questions, which Brain and which mode, become defaults; merged with 2026-10-31-hive-approvals: who answers a wait comes from its approval class)
created: 2026-11-01
updated: 2026-11-01
owner: XG
related: 2026-11-01-project-agent-simple.md, 2026-10-31-swarm-out-agent-owns-its-runs.md, 2026-10-31-swarm-run.md, 2026-10-31-hive-approvals-person-and-host.md
---

# 从新建 Brain 到 Hive 单落地，不等人

## 0. 原则（XG 2026-11-01）

新建 Brain、生成 Project Agent、派 Hive 单、收尾，这条路上**没有一步必须等人**。原来要人出手的地方，每一处都换成一个标准默认值或"不做"。默认值总是安全的那一边：不离开本机、可以撤回。人随时可以插手、改掉默认值，但流程不停下来等他。

产品里只有一件事仍归人，而且它**不在这条路上**：把东西送出本机（push、开 PR、发布、对外发消息）。这条路上没有任何一步需要它，所以也没有任何一步等它。

## 1. 原来要人出手的地方，各自的默认值

| 原来 | 现在的默认 | 为什么安全 |
|---|---|---|
| 起 Brain 的名字 | 名字可以不填：取第一个 GOVERN 项（仓库名或文件夹名），没有就取第一份资料的标题，再不行用 `Brain-<日期>`；重名自动加 `-2`。表单上照样可以改。 | 本机重命名就能改回 |
| 最多 3 个 PRODUCT 问题 | 不问。没有证据的段落记成缺口，P6 通过，缺口在 Brain Hub 上显示，人想补随时补（§2 第 4 条） | 缺口是"不知道"，不是错的内容 |
| Hive 的问题、决定、危险命令批准 | 由派单的 agent 回答，按它自己的权限（XG 2026-10-31：谁派谁负责）。每个等待带一个 approval class（`core/approval_class`，由触发的规则决定，不靠模型判断）：问题、决定、只动 Hive 自己工作区的命令、run 自己的授权，归派单 agent，用 `swarm_out card` / `answer` 回答，记作 host 的答复；其余（离开 Hive 的、对外的、不可逆的、删不可替代数据的、四个留给人的动作）在派出去的会话上**直接拒绝**，写明原因，不产生等待，不再转去问人。只有不带新能力的旧 Hive 还会出这类等待，host 不再给它派新单。 | 拒绝只会让这一步做不成；做不成的，agent 自己接管或者修正重跑 |
| 碰到受保护路径的落地 | agent 不落地受保护路径：让 Hive 去掉这些文件重做；任务确实要改它们时，agent 在本机自己改（自己测、自己审）。人仍然可以在面板上批准落地 | 受保护路径守着 agent 自己，或会在本机执行代码（hooks、`.claude/**`、`.swarmai/**`、CI 脚本）；Hive 改的版本不能凭 agent 自己填的标记落地（security 审查，2026-11-01） |
| push | 不在这条路上。单子做到"落到本机 + 经验进 Brain"就结束 | 没有一步等它 |
| 启动已停的 Hive | 先挑在跑的 Hive；一台都没有时，agent 自己启动一台（`hive.start` 对派单 agent 开放） | 启动只是恢复已有机器；新建、删除、拿密码仍然只给人 |
| 第三次重跑 | 不再问人。两次修正都不行，agent 自己接管来做；做不了就丢弃，并记下原因（Swarm Run §0"常事自己解决"） | 丢弃前照样检查代码、文件、经验都已取回 |
| P0"确认要管的资产"（新建提示词） | 不问。表单上每项资料的角色就是资产清单；P3 绑定后由 P6 对照这份清单检查（§2 第 13 条） | 清单来自人已经做过的选择 |
| 派单时 `route` 认不准 Brain（r4） | 不问。agent 从 `route` 的 `choices` 里挑 summary 和 handles 对得上的那个，都对不上就用 General；派出后用一句话说选了哪个、为什么，人可以改 | 选错只是放在了另一个 Brain 下，经验回到那个 Brain；人一句话就能改派 |
| 一个任务两种 mode 都合适、结果不同（r4） | 不问。选更轻的：结果不改任何仓库的那个（`--returns answer` / `document`），不选返回 commits 的；派出后用一句话说选了哪个、另一个会产出什么。只有一个能在 Hive 上跑时，选能跑的那个 | 报告不改代码，想要代码再说一句；选错的代价是多跑一次，不是改错东西 |
| 建 Brain 中途会话断了，要人点"继续建" | 自动接着做：已有的 job 机制每隔一段时间找出停在 `setting-up`、且已经一段时间没有进展的 Brain，用"继续建"提示词跑一个 agent 任务，做完 P6 --record | 只补缺的阶段，不重写已写的 |
| Hive 的提交封不成 bundle，只能人点"手动已落地" | agent 写一条修正让 Hive 先提交再重交（算一次重跑）；还不行就自己接管。"手动已落地"仍只给人，但不在这条路上 | 不会把没落地的提交连同 Hive 工作区一起丢掉 |

## 2. 顺带修的十处（2026-11-01 走查结果，均已对源码核实）

1. **还在要人确认 Project Agent。** 改掉新建 Brain 和"继续建"两个提示词（`newBrainDispatch.ts:189`、`:248`、头注释）、`s_project-manager/SKILL.md:212`、7 个 Brain 的 AGENTS.md 和现有定义的文件头；按标准格式重写现有定义（去掉 `status` 等已退役字段），删掉 9 个 `.project-agent-check.json`。迁移先备份。
2. **挑 Hive 不看版本。** `fleet()` 读 `work_orders` 和 `fixed_reserved`，`pick_hive` 只挑能接单的；没有能接单的，agent 用 `update-hive` 自己更新一台，等它更新完再派。skill 写明这一步。
3. **channel DM 里 Hive 的问题被自动答成第一个选项。** 有了 §1 第 3 行，Hive 的问题本来就由 agent 答，不再用问题卡问人，channel 自动作答这条路也就碰不到了。
4. **没有资料的 Brain 永远到不了 ready。** PRODUCT 和 TECH 没有证据的段落也可以记缺口；P6 把"有条目或有缺口"都算通过。
5. **骨架定义看起来能派单。** 准入和 P6 用同一个判断：骨架（没有 summary 或没有常见请求）、schema-1、命令来源找不到，一律算问题。派单时碰到，由 agent 当场补上再派，不报给人。
6. **`bind --local` 只做了一半。** 补上扫描和重新生成 Project Agent；本机目录写进 `bindings.yaml`，作为 `local` 仓库；派单时用已有的 `_ship_repos` 把本机提交打包带给 Hive。
7. **新版 Runner 定义永远落不了地。** seed 的判断是"文件还是产品上次写的那份才替换"，而 Runner 现在是旧确认流程改写过的。迁移时把它当作产品写的：改写过的旧格式文件，只要内容与之前发过的某一版一致（去掉已退役字段之后比较），就替换。
8. **单子会卡死。**
   - 派出去但没拿到会话的单：Hive 上查得到对应的工作单就继续等，查不到就由 agent 丢弃，不再占着那台 Hive。
   - skill 和代码对齐：验收后不要求丢弃，单子会自己了结；`watch` 返回落地方式；`session_unknown` 的处理写清楚，不会一直 watch 下去。
9. **"继续建 Brain"提示词的顺序错了**：改成先绑定、再写知识。
10. **没用的东西删掉：** spec 里的 `delivery`、重复的 `reservedForHuman`、前端没人读的 `sourcePaths`，以及十来处还在说"确认""表单"的文字。

11. **更新 Hive 时，单子可能在经验拉回之前就了结。** 更新中的 Hive 状态不是 running，会被当成"已停"（`swarm_out.py:1222`），而结算规则把"已停"当作经验已经拉完（`swarm_out_settle.py:357`）。改成：更新中按"还在"处理，不算已停；agent 只在一台 Hive 没有未结的单时才更新它。
12. **重新生成 Project Agent 会丢掉 agent 改过的内容。** 绑定仓库时 `refresh_draft` 只带回标准 mode 的 when/skills/source/gates/setup/validations（`project_agent_draft.py:548`），agent 给它加的 repos 等其它字段都丢了。改成：除已退役字段外，agent 写过的都带回；gate 仍按"全部重新启用"处理（安全的方向，保留）。
13. **P0 清单没人检查。** P6 对照新建时记下的资产清单（表单角色），清单里有、`bindings.yaml` 里没有，就算没完成。
14. **代码改动可以在没有任何检查命令的情况下派出。** 仓库在本机没有 checkout、也没有 CI 时，code-change mode 既没有 setup 也没有检查命令，准入照样放行。改成：返回提交的单子必须至少有一条检查命令；没有时由 agent 补上再派。
15. **在 Windows 和 Linux 上，`bind --local` 被拒。** 那里的 backend 是 Tauri 子进程，`SWARMAI_MODE=subprocess`，而 `local_roots` 只认 `""` 和 `daemon`（`local_roots.py:47`、`lib.rs:2027`）。把 `subprocess` 也算作桌面端。
16. **新建失败会留下半个 Brain。** `create_project` 先建目录、最后才写 `setting-up`（`swarm_workspace_manager.py:2651`），中途出错就留下一个不能重试（409）、也不能继续建的目录。改成：建完目录立刻写 `setting-up`，失败的 Brain 能被第 1 节的自动续建接着做完。
17. **SwarmAI 自己的 DDD 里还有讲"确认"的旧条目**（`PROJECT.md:389`、`IMPROVEMENT.md:1782`），会同步到 Hive。通过 CDF 声明覆盖或撤回，不手改。
18. **Hive 上已有的 `.project-agent-check.json` 永远不会删。** Hive 装 Brain 时删掉它。
19. **host 和 Hive 算出的 revision 不一样时，会反复"再发一次"**（推测，`swarm_out.py:833`）。pin 因 revision 不同被拒时，直接报版本不一致并停下，不提示重发；有第 2 条的版本检查之后，这种情况本不该出现。
20. **小问题：**
    - Brain 卡片上有两项检查显示原始名字（`brainMapModel.ts:297`）；
    - `SKILL.md:79` 说有 3 个原生 skill，实际列了 2 个；
    - `SKILL.md:268` 用的是相对路径的 `test -e`；
    - `SKILL.md:519` 假定存在 `backend/.venv`；
    - 常见请求的条数：STANDARD 写 3–5、skill 写 3–8，统一成 3–8；
    - Brain Hub 不显示常见请求，补上。

**核过、不算问题的：**
- **Hive 上的 pipeline 不落地时也会 REFLECT：** 落地和不落地两条路都会进 REFLECT（`run_protocol/cli_commands.py:322`）。
- **"经验已进 Brain"指的是已经交给 CDF 排队：** 写不写由 CDF 的判断决定。CDF 是唯一的写入方，所以这样设计是对的。
- **native 单子只有会话复盘的经验：** native 本来就没有 REFLECT 阶段，会话复盘就是它的经验来源。

## 3. 不变式（最终测试集）

- **Z1** 新建 Brain：不填名字、不回答任何问题，P6 也能通过，得到一个能派单的 Brain。
- **Z2** 派单路径上，没有哪个问人的地方能让一个 run 停住：派出去的会话产生的每个等待，类别都在 `HOST_CLASSES` 里，派单 agent 用 `answer` 一条命令答复；其余当场拒绝，不是"去问人"。
- **Z3** 四个留给人的动作在 Hive 上直接拒绝，不产生等待；在任何默认值下都不会被批准。
- **Z4** 危险命令里会归人批准的（类别不在 `HOST_CLASSES`，由 `_approval_class_of` 从命令触发的所有规则算出），在派出去的会话上一律当场拒绝，从不发出等待。
- **Z5** 准入和 P6 对同一份定义给出同样的判断（同一个函数）。
- **Z6** 挑 Hive 不会挑中没有 `fixed_reserved` 的 Hive。
- **Z7** 迁移前先备份；迁移后所有现有定义都通过准入，没有 `status` 字段，也没有 `.project-agent-check.json`。
- **Z8** 建 Brain 的会话中断后，不用人点也能接着建完；新建中途出错的 Brain 也一样。
- **Z9** 一台 Hive 在更新时，它上面的单子不会在经验拉回之前了结。
- **Z10** 绑定仓库后重新生成定义，agent 写过的字段（退役字段除外）一个不丢。
- **Z11** 派单前只剩一个问人的地方：说不清"做完是什么样"时问验收。Brain 认不准、两种 mode 都合适，都由 agent 按默认值选，不问（s_swarm-out 的"When to ask the person"表）。

## 4. 审查与落地

- **审查：** security（Z2–Z4 改变了谁来回答 Hive 的等待）和 correctness，各一轮，最多两轮。
- **落地顺序：** 先改 host，Hive 的改动（四个留给人的动作直接拒绝）随新版 Hive 包发出；host 只派给带新能力标记的 Hive（与 `fixed_reserved` 一样，在 admission-state 里声明）。旧 Hive 由 agent 自己更新。
- **会失去的：** 人不再收到 Hive 中途的问题。想看可以看面板和历史，每个答复都带原因。
- **r3 合并（2026-11-01）：** 这份设计实现时，`2026-10-31-hive-approvals` 已先落地（等待带 approval class，派单 agent 回答 host 类，其余给人）。合并后答复路径用它的（`card` / `answer`，Hive 端按类别拒收 agent 越权的答复）；本设计只在一点上改它：派出去的会话上，归人的那类等待不再发出，当场拒绝。r2 里自己写的 `answer_run` / `waits` / "agent 只能拒绝" 不再需要，已删。
