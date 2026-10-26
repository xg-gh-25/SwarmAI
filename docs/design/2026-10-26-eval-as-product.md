---
title: Eval 做成产品功能 — 每个用户装好就有、能跑、看得懂
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: draft r2 — 零上下文审查后按原因修订（§7）
---

# Eval 做成产品功能

## 0. 一句话

现在的 eval 只在 XG 这台机器上成立：golden set 只存在于 XG 的 SwarmWS，canary 命令要靠 XG 桌面上的源码目录，
线上评分跑完了但结果没人写。本设计把 eval 做成随安装包发出去的产品功能：每个用户装好就有一套能跑通的
golden set；case 的目的写成人能看懂的一句话；快速检查 / 离线考题 / 线上评分三条线分开讲清楚；报告和 Overview
顶部给出"需要你注意的事"；线上评分的结果真正写下来、显示出来；Guide 按现在的机制重写，会变的值从代码读取。

这也是 XG 2026-10-25 定下的 D2（`docs/design/2026-10-25-usable-by-anyone-closed-world-guards.md:122`）："OS Eval 是产品功能，
评的是用户正在用的 SwarmAI；开发 SwarmAI 本身才需要的部分移出产品。"本设计落地 D2，`tests/p7_exceptions.txt` 里为它留的
临时例外（`eval_runner.py`、`eval_spine_probe.py` 的 `_find_swarmai_repo` 和 Desktop 路径）在第 1 步清零。

## 1. XG 的六条要求（2026-10-26）

1. 其它用户安装后默认就有 eval，而且能正常运行，不是只有 XG 本机能用。
2. golden case 精炼、有用、能看懂，总数不超过 80，每条的目的清楚。
3. online / offline 让用户看懂，简单直接。
4. 报告汇总要有 Suggested Follow-up（需要人注意的）。
5. online eval 要真能跑起来。
6. Guide 信息要保持最新。

## 2. 现状（每条都是本轮读到的代码或数据）

### 2.1 新用户装好后什么都没有（要求 1）

- golden set 只存在于 `~/.swarm-ai/SwarmWS/Eval/`（`golden_set.yaml` 和 `.private.yaml`），源码仓库里只有测试用的
  fixture（`git ls-files` 只有 `backend/scripts/testdata/golden_set_fixture.yaml`）。工作区初始化也不创建 `Eval/`。
  `eval_service._find_workspace`（`core/eval_service.py:75-85`）找不到 `Eval/golden_set.yaml` 就抛异常，dashboard
  后，`get_eval_service` 退回一个空的单例且之后不再重试（`eval_service.py:1500-1502`），dashboard 一直是空的；定时 eval
  也没东西可跑。
- canary 命令在源码目录里执行：`eval_canary_pass` 的 cwd 是 `_find_swarmai_repo()`（`scripts/eval_runner.py:80-90`），
  后者依次试 `__file__` 的上三级（打包后是 `runtime/<v>/`，没有 `backend/core`）、写死的
  `~/Desktop/SwarmAI-Workspace/swarmai`（XG 的路径）、cwd。别的用户这三处都不成立，所有 canary 都会报错。
- 命令写法依赖源码环境：7 条公开 case 写的是 `python backend/scripts/...` 或 `cd backend && .venv/bin/python ...`。
  私有集里 12 条 programmatic case 实际是在跑仓库自己的 pytest 测试文件（`cd backend && .venv/bin/python -m pytest tests/...`），
  安装包里没有 `tests/`。
- 公开集里的 code-intel case 要 `SwarmAI` 这个项目的代码图（`bvt_probe.py load SwarmAI` 等），别的用户没有这个项目。
- `"$SWARM_BACKEND_BIN"` 只在 daemon 进程里设置（`utils/backend_bin.export_backend_bin`），终端里跑 `eval_runner`
  或在测试里跑时这个变量是空的。
- 能用的基础设施已经有了：`utils/backend_bin.py` 规定产品代码用 `"$SWARM_BACKEND_BIN" py -m scripts.X` 跑 Python，
  打包和开发环境写法一样；`build-backend.sh:233` 把 `templates/` 打进安装包，`:270` 收集了整个 `scripts` 包；
  `bundled_path()` 能读到打包的数据文件。

### 2.2 case 看不懂（要求 2）

- Golden Set 列表显示的是 id 和开发者写的标题，例如 "recall link canary: knowledge_fts corrupt…"、
  "Recall#5 opener-guard: a zero-keyword …"。看不出这条测的是什么、坏了对用户有什么影响。
- 没有字段表达"目的"。标题里混着 run id、COE 编号、内部模块名。
- 没有总数上限，`add_case` 不检查数量。

### 2.3 三条线没讲清楚（要求 3）

- 界面上出现的是 tier（stable / behavior / active / live）、eval method（programmatic / behavior / llm）、dimension、
  category 四套分类，没有"快速检查 / 离线 / 线上"这个用户能理解的划分。
- "Session Quality — real-session loop (layer ②③)" 就是线上评分，名字和 "layer ②③" 只有写代码的人懂。

### 2.4 报告没有"需要你注意的事"（要求 4）

- `generate_html_report`（`eval_runner.py:2280`）docstring 写着 "Actionable next steps footer"，代码里没有这一节；报告按
  维度列出所有 case，人要自己翻。
- 分数会被机器负载拉低：canary 命令超时记成 `failed`（`eval_runner.py:642-643`，runtime_health 在 `:769-770`）。10-23 那次
  canary 是 76.1%（-19.7%），17 条失败里 16 条是超时（15 条 canary、1 条 runtime_health），1 条 ModuleNotFoundError，
  没有一条是产品真坏了。
- 已有可以复用的判断：`eval_scheduled` 已经算出 `reasons[]`（BVT 红、分数漂移、覆盖率、behavior 说明，
  `jobs/handlers/eval_scheduled.py:215-243`）；`_classify_failure` 能区分"case 本身坏了"和"真的退步"（`eval_runner.py:2096`）。
  但这些只进了 Slack 告警和 job summary，报告和界面上看不到。
- Reports tab 列出 47 份历史报告，名字是 "2026-07-07 x"、"lockprobe"、"run 5edf2cc0 C3 regate" 这类调试遗留。

### 2.5 线上评分跑了，但结果没写下来（要求 5）

- `session-quality` 每周五在跑：10-23 的日志是 "10 scored, 9 low, 2 draft(s) harvested"。
- 但它不是"最近的会话"：`role_counts_by_session` 统计全部历史会话（`database/sqlite.py:1167-1175`），
  `select_sessions` 取 `sorted(qualified)[:10]`（`jobs/handlers/session_quality.py:62`），纠错也没有按时间过滤
  （`since_ts=None`）。所以每周基本是同一批老会话被重新打分，这也是回收出来的 69 条全部"already exists"的原因。
- 但它的结果只进了 job summary。`eval_service.get_session_quality`（`core/eval_service.py:317-351`）读
  `SwarmWS/.context/.session-quality-state.json`，全仓库没有代码写这个文件，所以界面永远显示 0。
- 自动回收（harvest）的产出没有用：`Eval/discarded-seed-candidates.jsonl` 里 69 条全部是
  "add_case rejected: … already exists"；进过 golden set 的 24 条 GS_HARVEST_* 在今天的审计里因为"只针对 XG 某次会话、
  不可复用"全部退役。它和宁缺毋滥直接冲突。

### 2.6 Guide 过时（要求 6）

`EvalDashboard.tsx` 的 Guide 和 `desktop/public/eval-architecture*.svg`、`eval-sequence*.svg` 里，至少这些和现状不符：
- 定时 eval 写的是 "Monday 12:30 ICT"，实际是 18:30 ICT（`system_jobs.py:125`）。
- 线上评分写的是 "Weekly, Sunday"，实际是周五（`system_jobs.py:206`）。
- "eval_command_guard enforces"：这个 guard 已不存在（全仓库 0 处）。
- "corrections still record to the tracker"：tracker 今天已删除。
- "human ratify … Promote draft→active"：Promote 队列早已删除（`EvalDashboard.tsx:1372-1375` 自己的注释也这么说）。
- 维度写的是 6 个，Overview 显示 5 个。
- 已有可以复用的数据：`GET /api/jobs/` 已经返回每个 job 的 `schedule` 和 `last_run`（`routers/jobs.py:30-42`）。
这些文字和图都是手写的副本，代码一改就过时，没有任何东西提醒。

## 3. 设计

### 3.1 产品 golden set 随安装包发布（要求 1）

- **位置**：`backend/templates/eval/golden_set.yaml`，跟代码一起提交、一起打包、一起升级。用 `bundled_path("templates",
  "eval", "golden_set.yaml")` 读。不往用户工作区复制：复制的那份会在升级后过时（Guide 就是这个问题）。
- **加载只有一个函数**：`load_merged(product_path, private_path)`，所有读者都改用它（清单见 §4）。现在的
  `load_golden_set` 去公开文件旁边找私有文件，公开文件不在就报错（`eval_runner.py:317-325`），不能沿用。
  私有 case 和产品 case 同 id 时，产品 case 生效，私有那条跳过并出现在 follow-up 里：产品升级新增的 id 不能让用户的
  eval 跑不起来。
- **产品 case 只读，对所有写入方都成立**：dashboard 的新增 / 编辑 / 删除，以及自动改 tier 的 `promote_stable_cases`
  （`eval_service.py:854-875`，GUI 每次运行后调用，`:1256`）、`eval_hooks.post_run_promotion`、`/promote-stable`
  （`routers/eval.py:242`）、archive，全部跳过产品来源的 case，只写私有文件。这顺带修掉一个现有 bug：`tier` 参与 stamp
  （`golden_case_validator.py:63-64`），自动 promote 一改 tier，stamp 就失效，case 悄悄掉出 BVT（`eval_runner.py:234`）。
- **读者找不到文件时要出声**：`context_health_hook.py:410-412` 找不到 golden set 就直接返回、canary 停掉没有提示；
  `eval_service.py:1247` 生成报告时只读公开文件、失败只记 debug。都改成走 `load_merged`，失败记 warning。
  `get_eval_service` 拿不到工作区时不缓存空单例，下次调用重试。`SwarmWS/Eval/golden_set.yaml`（XG 本机现在的
  公开文件）不再读取：内容迁进产品集后，原文件改名为 `golden_set.yaml.migrated-2026-10-26` 保留，不删除。
- **工作区**：`_find_workspace` 不再以 `Eval/golden_set.yaml` 是否存在作为判断；`EvalHistory/` 首次写入时创建
  （`_eval_history_dir` 已经这么做）。
- **命令写法**：产品 case 一律写 `"$SWARM_BACKEND_BIN" py -m scripts.<probe> …`。`eval_canary_pass` 和
  `eval_runtime_health` 在子进程环境里放 `SWARM_BACKEND_BIN=backend_bin()` 和 `SWARM_WORKSPACE=<工作区>`，daemon、终端、
  测试里都一样。cwd 是每次运行新建的空临时目录，不用工作区：`py -m` 会把 cwd 放在 `sys.path` 最前面，工作区里如果
  有 `scripts/` 或 `core/` 目录会遮住真正的模块。删除 `_find_swarmai_repo` 和写死的 Desktop 路径，它的其它调用方
  （`eval_file_contains:792`、`_resolve_ref:953`、`eval_spine_probe:98-102`）改为读打包路径或工作区。私有集里 XG 自己的 case
  如果需要源码目录，在命令里写明绝对路径（那是他的本机 case，不进产品）。
- **产品 case 的准入**：只收每个用户装好就能跑、且对每个用户都有意义的 case（SOUL P7 的 strip test）：
  不引用某个用户的项目、文档或数据；BVT 用安装包里自带的 probe 和临时 fixture，不读用户的真实数据。
  初步划分（实现时逐条核对，结论写进 §6 的清单）：
  - BVT（审查已确认这些 probe 都不读 `tests/`、不要 venv：`memory_chain` 用临时文件，`recall_chain entry_recall` 用内存
    fixture，`fault_inject_*` 用 mock，`destructive_op` 只 import `core.security_hooks`，`eval_spine_probe` 的 `safe_*` 是纯检查）：
    渠道会话不带私人文件（GS_SAFE001/002）、prompt 组装预算和身份文件保底（GS_COST001、GS_SPINE_ASSEMBLY_FLOOR）、
    不可逆外部操作必须审批（GS_REDLINE_DESTRUCTIVE_OP）、会话恢复路径能执行（GS_RTH001/002/003）、MEMORY 衰减 / 归档 /
    归档可检索（GS_MCHAIN_*，确认用的是临时 fixture）、MEMORY 条目级检索（GS_RCHAIN_ENTRY_LEVEL）、DDD 噪声检查
    （GS_LOP009 改成在 fixture 文档上跑正例）。
  - 离线考题：R19 语言、R20 工具调用前不写字、P10 能决定的不丢回给用户、R1 高风险改动问一次、R18 没有下一步就不给
    （GS_NEXT_STEPS_HONEST 改为引用 AGENT R18，不再引用 XG 的 STEERING）、改前先观察运行状态（GS_FAITH_VERIFY_RUNTIME_STATE）、
    自信但错误的前提要纠正及其对照组（GS_T4_CONFIDENT_PREMISE*）。scenario 里引用了 XG 项目的，留在私有集。
  - 不进产品：要 SwarmAI 项目代码图的 code-intel case、GS_FRESH001（SwarmAI 仓库自己的 eval 门）、XG 文档的 recall_at_k
    （GS_RCLK*）、静态 LLM judge case（只测规则文字，§3.2 的理由）。它们留在 XG 的私有集，按本设计的准入重新判断。
- **包装 pytest 的 12 条 case 移出 eval**：它们跑的是仓库自己的单元测试，属于 CI 的职责（P7：仓库的纪律放在仓库自己的 CI）。
  逐个文件处理：在裸 checkout 里能通过的，补进 `backend/tests/bvt_manifest.txt` 再退役 case；通过不了的（需要本机数据，
  例如 test_knowledge_store、test_recall_multi、test_session_recall 目前都不在 manifest），留在 XG 的私有集，命令写明
  源码目录的绝对路径。覆盖不减少。
- **安装后能跑的证明**：新增测试 `test_eval_fresh_install.py`：HOME 指向临时目录，工作区为空，没有任何项目，从
  `bundled_path` 读产品集，跑全部 BVT 并执行反例。全部通过才算过。它在仓库的裸 checkout 里自检，进 `bvt_manifest`。
  打包环境另外用 rebuild 后的安装包手工跑一次同样的流程（§6）。

### 3.2 case 精炼、能看懂、不超过 80（要求 2）

- **新字段 `purpose`**：一句话，写给用户看：这条检查什么，坏了对你意味着什么。中英各一句：
  `purpose: {en: "...", zh: "..."}`，每句不超过 120 字符，不出现 run id、COE 编号、模块名。
  例：GS_SAFE001 →「群聊里的 agent 拿不到你的私人记忆和个人资料。坏了意味着私人信息可能出现在群里。」
- **stamp**：`purpose` 纳入 `compute_case_stamp`。迁入产品集的 case 加 purpose 后全部重新 stamp，否则会掉出 BVT。
- **校验**：`golden_case_validator` 增加一道检查：产品集的每条 case 必须有 `purpose`；新加的私有 case 也必须有。已有的
  私有 case 不强制（避免一次性改 69 条），但 Golden Set 列表对没有 purpose 的私有 case 显示"未写目的"。
- **上限 80**：产品集加私有集合计。`add_case` 在达到 80 时拒绝新增，提示先退役一条。CI 测试断言产品集本身
  不超过 40 条，给用户自己的 case 留出空间。
- **静态 LLM judge case 不进产品集**：今天的审计已确认它们只检查模型能不能复述规则文字，judge 上下文还被截断
  （SOUL 前 2000 字、AGENT ≤3000 字）。产品里判断力用 behavior case 测。

### 3.3 三条线（要求 3）

不新增存储字段，由已有的 `eval_method` 和数据来源推出（一份事实只有一个来源）：

| 线 | 是什么 | 由什么决定 | 多久跑一次 | 失败意味着 |
|---|---|---|---|---|
| 快速检查 (BVT) | 用固定输入检查产品的关键功能，结果确定，不调模型，几秒跑完 | `eval_method` 是 programmatic（含 `runtime_health` 这类 evaluator） | 每天第一次会话时的 canary、部署、双周全量 | 产品某个功能坏了，需要修 |
| 离线考题 (Offline) | 用真实 agent 做固定的题，看它的判断和做法对不对 | `eval_method` 是 behavior（以及私有集里的 llm） | 双周全量一次（慢、要调模型） | agent 的判断力退步了，先看是哪条规则 |
| 线上评分 (Online) | 每周从你过去 7 天的真实会话里抽最多 10 个打分，没有标准答案 | session-quality 任务 | 每周五 | 真实使用质量在下降，去看那几个低分会话 |

- Overview 用这三行代替现在的五个维度卡片和 "Intelligence Velocity"：每行一句话说明、上次运行时间、结果。
  维度和趋势放到 Trends。
- "Session Quality" tab 改名 "线上评分 / Online"，副标题用表里那句话，去掉 "layer ②③"。
- Golden Set 列表默认按三条线分组，每行显示 purpose，id 和技术标签收到详情里。

### 3.4 Suggested Follow-up（要求 4）

- **一个函数，两个地方用**：`core/eval_followups.py` 的 `build_follow_ups(latest_full, latest_canary, history,
  online_state, golden_set, now)` 返回一个列表，每项 `{severity, what, why, action, case_ids}`。HTML 报告的第一节和
  Overview 顶部都调用它，不各写一份。全部是确定性规则，不调模型。它复用已有的判断，不重写：BVT 红和分数漂移用
  `eval_scheduled` 算 `reasons[]` 的同一组函数（抽出来共用），"case 本身坏了 / 真的退步"用 `_classify_failure`。
- **规则**：
  1. 快速检查有失败 → "产品功能坏了：<purpose>"，附失败输出的第一行。
  2. case 没测成：环境报错 → "N 条没测成，不计分"，附原因；超时 → "N 条没跑完"，同一条连续 2 次超时 → "可能真的卡住了，
     去看"（GS_RTH002/003 这类测卡死恢复的 case，超时本身就是退步）。
  3. 离线考题和上一次可比的全量运行相比，从通过变成不通过 → 列出这些 case。
  4. 线上评分本周的低分会话 → 数量、前 3 个会话的打分理由摘要和 session id。
  5. 该跑没跑：快速检查超过 2 天、全量超过 15 天、线上评分超过 8 天没有结果。
  6. 某条 case 连续 4 次可比运行都不通过 → "这条可能本身坏了，修或者退役"。
  7. golden set 超过上限，或产品 case 缺 purpose（理论上 CI 已拦住）。
  没有任何一项时显示"没有需要处理的事"。
- **超时单独一个状态 `timeout`**：canary、反例、runtime_health 的 `TimeoutExpired` 记为 `timeout`。它不进分数（10-23 的分数
  下降就是负载导致的超时被算成失败）；在 BVT 里仍然算红（`compute_bvt` 本来就要求没有 error，`eval_runner.py:245`），
  所以真的卡死不会被藏起来；`eval_scheduled` 的告警把它单独列为"超时"，不再归到"judge infra degraded"
  （`eval_scheduled.py:233-237`）。
- **Reports tab**：只列定时和部署触发的报告；手工和调试遗留的报告收在"显示全部"后面。

### 3.5 线上评分真正跑起来（要求 5）

- **只看最近 7 天**：采样只取最后一条消息在过去 7 天内的会话，纠错也只取 7 天内的（`since_ts`）。不然每周都在给同一批
  老会话打分，trend 没有意义。
- **补上写入方**：`session_quality` handler 每次运行结束写 `SwarmWS/.context/.session-quality-state.json`：
  `{last_run, scored, low, selected, low_details, sampler_error, trend}`，trend 每周追加一个 `{week, low_rate}`，保留 12 周。
  `low_details` 写入时就按 `_project_low_detail` 的白名单过滤（只有分数、维度、截断后的理由摘要和 session id，不带原始
  prompt），读取时再过滤一次。读取方 `get_session_quality` 补上 `selected` 和 `sampler_error` 两个字段：采样出错时界面显示
  "这周没评成"，没有可评的会话时显示"过去 7 天没有符合条件的会话"，都不显示成 0。
- **删掉自动回收进 golden set**（harvest）：理由见 §2.5。低分会话改为 §3.4 第 4 条的 follow-up，由人决定要不要据此写
  一条 case。删除 `session_harvest.py` 中只为这条路径存在的部分、handler 里的 harvester、界面上的 "Drafts harvested"，
  实现时按 R27 grep 全部读者（含测试）。`discarded-seed-candidates.jsonl` 不删。
- **验证**：部署后，用 job 管理的"立即运行"跑一次 session-quality（这是产品自己的入口，对真实会话打分），确认状态文件
  写入、界面显示数字和低分会话。

### 3.6 Guide 保持最新（要求 6）

- **会变的值不手写**：运行时间用已有的 `GET /api/jobs/`（`schedule`、`last_run`）。双周 eval 的 cron 每周都触发、由
  14 天门槛决定是否真跑，所以只看 cron 会算错；`eval_service` 用 `_should_run_biweekly` 同一个函数算出"下次全量"，
  和条数、上限一起放进已有的 `/api/eval/health` 返回值。Guide 和 Overview 都从这两个接口取，不新增端点。
- **删掉两张架构图**（`eval-architecture*.svg`、`eval-sequence*.svg`）：它们是手画的副本，已经和现状不符，又没法自动
  更新。改成 §3.3 的三行表加一段"一条 case 怎么加、怎么退役"的说明，文字只描述机制，不写数字和时间。
- **防止再过时**：前端测试断言 Guide 文案里不出现时间、星期和条数的字面量；后端测试断言 `/api/eval/health` 的"下次全量"
  在门槛前后分别给出正确日期（用和 handler 相同的函数，不对 cron 字符串做相等比较）。
  `EvalDashboard.guide.test.tsx:74-86` 断言 SVG 存在，随图一起改。

### 3.7 不做的事

- 不改 eval 的触发方式：仍然只有 CI / 部署 / 定时，以及 dashboard 上用户自己点的按钮（AGENT § Eval）。
- 不新增 dimension、tier 之类的分类。
- 不做"自动生成 case"：任何自动产出的 case 都要先过 §3.2 的 purpose 和准入，现在没有能做到这点的生成方式。

## 4. 数据形状变更与读者

| 写入 / 形状 | 读者（全部要改） | 处理 |
|---|---|---|
| 产品集 `templates/eval/golden_set.yaml`（新位置），`load_merged` | `eval_service`（加载、`_golden_set_path` 写回 `:1057-1061`、报告 `:1247`）、`context_health_hook:410-412`、`eval_scheduled._default_runner:124`、`eval_runner` CLI（`:2875`、`:2938`）、`golden_case_validator:378`、`_find_workspace_root`（`eval_runner.py:64`，`ci_eval_gate.py:163` 用它算 code_digest） | 先写 `load_merged` 和测试，再逐个迁移读者，最后搬内容 |
| 产品 case 只读 | `add/update/delete_case`、`promote_stable_cases`、`eval_hooks.post_run_promotion`、`/promote-stable`、archive | 全部跳过产品来源，加测试 |
| case 的 `purpose`（新，进 stamp） | Golden Set 列表和详情、follow-up、validator、`stamp_golden_cases` | 迁入后重新 stamp |
| 子进程环境 `SWARM_BACKEND_BIN` / `SWARM_WORKSPACE`、临时 cwd | 所有产品 probe | probe 读 `SWARM_WORKSPACE`，不写死路径 |
| `timeout` 状态（新） | `compute_scores`、`compute_bvt`、`eval_scheduled` 告警、报告、follow-up、前端状态徽章 | 先改读者再改写入方 |
| `.session-quality-state.json`（补写入方，加 `selected`、`sampler_error`） | `get_session_quality`、Session Quality tab | 读取方先补字段 |
| follow-up 列表（新） | 报告、Overview | 一个函数生成 |
| `/api/eval/health` 加"下次全量"和条数 | Guide、Overview | 先改接口和测试 |
| 删除 harvest | `session_quality` handler、Session Quality tab、`session_harvest.py`（含 `:245` 写死的路径）、`test_session_harvest`、`test_eval_seed_from_correction` | R27 grep 后删除 |
| 删除 `_find_swarmai_repo` | `eval_file_contains:792`、`_resolve_ref:953`、`eval_spine_probe:98-102`、`p7_exceptions.txt:16-19` | 例外清零 |

会断或要改的测试（审查列出，实现时再 grep 一次）：`test_eval_split_origin`、`test_eval_crud`、`eval_runner_split_test`、
`ci_eval_gate_test`、`eval_gate_test`、`test_eval_report_render`、`test_runtime_health_eval`、`test_eval_seed_from_correction`、
`test_session_harvest`、`eval_runner_test.py:40-478`（直接用本机工作区和源码目录）、`EvalDashboard.guide.test.tsx`。
要同步的文档：`s_golden-case`（"promote to public"改为"提议进产品集 = 改 `templates/eval/golden_set.yaml` 的代码改动"）、
`s_self-evolution/INSTRUCTIONS.md:149,181`、SELF § Kernel Flows 的 OS Eval 段、TECH.md 的 eval 部分。

## 5. 落地步骤（每步可单独提交、单独验证）

1. `load_merged` 及全部读者迁移、产品 case 只读、子进程环境和临时 cwd、删除 `_find_swarmai_repo` 和 P7 例外；
   搬入第一批产品 case 并写 purpose、重新 stamp；`test_eval_fresh_install.py`。
2. purpose 校验、上限 80、退役 12 条 pytest 包装 case（先补 manifest）、XG 私有集按准入重判。
3. `timeout` 状态及其读者；`build_follow_ups`（复用 `eval_scheduled` 的判断和 `_classify_failure`）及其在报告和 Overview
   的接入；Reports 列表收起调试报告。
4. 线上评分只看 7 天、写状态文件、读取方补字段、删除 harvest、tab 改名。
5. `/api/eval/health` 补"下次全量"和条数；Overview 三条线；Golden Set 按线分组显示 purpose；Guide 重写、删除 SVG。
6. 文档同步：SELF § Kernel Flows 的 OS Eval 段、TECH.md 的 eval 部分、`s_golden-case` skill。

## 6. 验证计划（现在定，不在做的过程中加）

**最终测试集（结束时跑一次）**

1. 全新安装：临时 HOME、空工作区、无项目 → 产品集加载成功，全部 BVT 通过，反例全部执行并通过。
2. 产品集：每条有中英 purpose；不引用 `Projects/`、`.context/`、`/Users/`、`SwarmAI` 项目名、源码相对路径；条数 ≤40。
3. 合并加载：产品集 + 私有集；同 id 时产品生效、私有跳过并出现在 follow-up；dashboard 写操作和自动 promote 只落到私有文件；
   产品 case 拒绝编辑和删除，tier 不被改动。
4. 上限：第 81 条被拒绝。
5. 超时 → `timeout`：不进分数、BVT 仍为红、告警单列；连续 2 次超时在 follow-up 里升级。
6. follow-up 的 7 条规则各有一个正例和一个"不该出现"的反例；没有问题时返回空列表。
7. 线上评分：只采样 7 天内的会话（7 天前的会话即使有纠错也不被选中）；handler 写出的状态文件被 `get_session_quality` 读出
   相同数字；写入的 `low_details` 不含原始 prompt；采样出错和"没有可评会话"各自显示；trend 追加并保留 12 周；不再调用任何
   harvest 代码。
8. `/api/eval/health` 的"下次全量"在 14 天门槛前后都正确；Guide 文案不含时间、星期、条数字面量。
10. 产品 probe 在子进程环境里拿得到 `SWARM_BACKEND_BIN`（终端和测试里也是）；工作区里放一个 `scripts/` 目录不影响 probe。
9. 打包验证（rebuild 后手工一次）：用安装包的 `python-backend py` 在临时 HOME 下跑第 1 条。

每个修复写的时候做变异检查（`scripts.mutation_check`）。提交前只跑被改函数及其直接调用方已有的测试。

**审查**

- 设计：一次零上下文前提审查，之后靠测试验证。
- 代码：第 1、4 步各一个 correctness 专家（重点：读者是否全部迁移、删除的东西是否还有读者）；第 3 步加一个 security 视角
  （follow-up 和线上评分里出现真实会话内容，确认只走白名单字段）。UI 按 `s_frontend-design/data/design-judgment.md` 自查。

## 7. 修订记录

- r2（零上下文审查后，按原因合并）：
  - **加载和写入**：所有读者改用一个 `load_merged`（原来的 `load_golden_set` 去公开文件旁边找私有文件）；产品 case 对所有写入方只读，
    包括自动 promote（顺带修掉 promote 改 tier 导致 stamp 失效、case 掉出 BVT 的现有 bug）；同 id 产品优先；找不到文件的读者要出声。
  - **运行环境**：子进程显式带上 `SWARM_BACKEND_BIN`、`SWARM_WORKSPACE`，cwd 用空临时目录（防止工作区目录遮住模块）。
  - **超时**：不改成 `error`，单独一个 `timeout` 状态，BVT 仍红、不进分数、告警单列，否则会藏住卡死退步。
  - **线上评分**：发现采样没有时间窗，每周都在重评同一批老会话；改成只看 7 天。读取方补字段。
  - **复用**：时间用已有的 `/api/jobs/`，下次全量用 `_should_run_biweekly`，不新增 `/api/eval/facts`；follow-up 复用 `eval_scheduled` 的
    判断和 `_classify_failure`。
  - 补上 D2（10-25 已定）和 P7 例外、遗漏的读者和测试、pytest 包装 case 按文件判断能否进 manifest。
- r1：初稿。
