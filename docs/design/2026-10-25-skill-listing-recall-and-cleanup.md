---
title: Skill 清单瘦身 + 按需召回 + skill 清洗
created: 2026-10-25
updated: 2026-10-25
project: SwarmAI
status: draft r2 — r1 经 skeptic 证伪后重写（见末尾修订记录），待 XG 拍板
---

# Skill 清单瘦身 + 按需召回 + skill 清洗

## 0. 一句话

skill 的正文本来就是调用时才加载；占上下文的是 **skill 清单**（每个 skill 的名字 + 描述）。
清单超过 CLI 预算时，CLI 按"使用次数 × 时间衰减"排序，排后面的只留名字，所以从没用过的新
skill 永远拿不到描述、也就永远没人用（`s_expert-review` 就是这样被漏掉的）。
本设计分三层解决，都不提高预算（XG 2026-10-25 定：不往上下文里堆）：
1. **缩小清单**：描述写短、别的项目的领域 skill 只列名字、删掉没用的 skill。
2. **按需召回**：清单装不下、或只剩名字的 skill，在用户消息相关时注入它的描述。
3. **规则点名**：产品规则依赖的 skill，在规则里写明名字和用法，不依赖清单。

## 1. 现状（2026-10-25 核对，CLI 2.1.301）

| 事实 | 证据 |
|---|---|
| system prompt 不注入 skill 正文；`tier: always/lazy` 只决定调用时 SKILL.md 是全文还是指向 INSTRUCTIONS.md 的存根 | `prompt_builder`、`context_directory_loader`；`skill_registry` 的旧注释已在 `789ff2486` 改正 |
| 清单在 session 开始时作为一次性的 `skill_listing` 附件发给模型，之后只补发新增的 skill；resume 时已在 transcript 里的不重发 | CLI 源码，日志 "Sending N skills via attachment (initial/dynamic)" |
| 清单预算 = 上下文窗口 × 4 字符 × `skillListingBudgetFraction`（默认 0.01）：1M 约 4 万字符，200K 约 8 千 | CLI `Tat()` |
| 每条描述另有单条上限 `skillListingMaxDescChars`（默认 1536） | CLI `Axt()` |
| 超预算时按 `使用次数 × max(0.5^(距上次使用天数/7), 0.1)` 排序（CLI 全局 `skillUsage`），排后面的只留名字；没用过的得 0 分。CLI 自带的 skill 不参与、始终带描述 | CLI `xxt()`、`Kun()` |
| `skillOverrides`：键是命令名（目录名 `s_x`）；`name-only` 只列名字、**仍可调用**；`off` 拒绝调用。**插件 skill 不受 `skillOverrides` 控制** | CLI `OA()`：`e.source==="plugin"` 直接返回 `"on"` |
| SwarmAI 每次 spawn 通过 `--settings` 给 CLI 传 `skillOverrides`、`autoCompactWindow` | `prompt_builder` 的 `cli_settings` |
| 投影：内置 skill 全部投影；`allow_all` 时所有 DDD 的领域 skill 都投影，不管当前是哪个项目 | `projection_layer.project_skills` |
| 用户自己装的 Claude Code 插件也进清单 | `~/.claude/settings.json` 的 `enabledPlugins` |
| recall：第一条带关键词的消息召回一次；之后只在换话题时（关键词 Jaccard < 0.2）再召回，每 session 最多 6 次 | `session_router._RECALL_TOPIC_OVERLAP`、`_RECALL_MAX_FOLLOWUPS` |
| recall 的相关度闸要求命中 query 关键词的 `max(2, 30%)`，按长文档调的；对一两百字的 skill 描述会把"帮我做一份 PPT"这类请求挡掉 | `recall_filter`（skeptic 离线实测） |
| 附件消息另有一条写死的 skill 提示（pptx/docx/xlsx/whisper） | `session_router` 附件处理 |
| 打包从工作区复制 skill：本机 daemon 里有 5 个 gitignored 的 `s_internal-*`（repo 里没有），还有已从 repo 删掉的旧 skill | `ls ~/.swarm-ai/daemon/_internal/skills` 对比 `git ls-files backend/skills` |

量级（XG 机器，删掉第一批 30 个之后）：描述约 4.2 万字符（109 个投影 skill）+ 插件约 1.4 万。
skeptic 实测：把单条描述限到 200 字符，清单约 2.3 万 + 插件约 1 万 ≈ 3.3 万，**1M 上下文下能放下**；
200K 下放不下（8 千预算，光名字就要约 5 千）。数字只说明量级，实施时在 spawn 时实测。

## 2. 约束

- **P7**：机制对所有用户、repo、语言一样；不写死任何人的 skill 名单。
- **不往上下文堆**：不提高 `skillListingBudgetFraction`。
- **P8**：召回走现有 recall 架构（一个新域），不另造检索。
- **P9**：先用现有开关和删除解决，再加新机制；每一步做完先量，再决定要不要下一步。

## 3. 设计

### 3.1 第一层：缩小清单

1. **描述写短，TRIGGER 放最前**。产品 skill 的 `description` 不超过 250 字符，第一句说做什么、紧跟 `TRIGGER:`；写进 `lint_skills.py`（CI 已在跑），超长即失败。
2. **给所有 skill 设单条上限**：`cli_settings` 加 `skillListingMaxDescChars: 250`，管住用户、DDD 和插件 skill 的长描述。这会**缩小**上下文，不是加大。
3. **别的项目的领域 skill 只列名字**：spawn 时，凡是不属于本 session 活跃项目的 DDD 领域 skill，在 `skillOverrides` 里设 `name-only`。来源用 `ddd_skill_registry.json`（DDD skill 的真相源），不读 `bindings.yaml`。
   - 时序：`skillOverrides` 在 spawn 时就要定，那时还没有用户消息。活跃项目只能用 spawn 前就知道的信号（tab 绑定的项目、cwd 所在的项目）；不知道时，所有领域 skill 都只列名字，靠第二层召回补上。
   - 清单只在 session 开始时发一次，session 中途换项目不会刷新清单描述，由第二层召回补。
4. **删掉没用的 skill**（§3.4）。

第一层做完先量：1M 和 200K 两种上下文下清单字符数和预算各是多少，有多少 skill 只剩名字。1M 下能放下就不必依赖第二层；200K 用户一定需要第二层。

### 3.2 第二层：按需召回 skill 描述

- **新增 `skills` 域**：语料 = 所有已投影 skill 的名字 + 描述（含 TRIGGER / NOT FOR），一两百条，内存 BM25，投影目录变了就重建。插件 skill 也进索引（`skillOverrides` 管不了它们，但召回可以）。
- **自己的相关度闸**：不复用长文档的 `max(2, 30%)`。规则是至少 1 个强词命中加一个 BM25 分数下限，阈值用 §5 的 golden 集定，先测后上线。
- **每轮都查，只针对 skill**：不受"每 session 一次 + 换话题才再查"的限制（那是给长文档 recall 定的）。查询本身是内存里的几百条短文本，成本很小；注入只在命中时发生。
- **只注入清单里拿不到描述的 skill**：已经带完整描述的跳过，每轮最多 3 条，同一 session 里注入过的不再注入。
- **单独成块，不贴 recall 标签**：用"可调用的 skill（用 Skill 工具调用）"块，不放在 `[RECALLED] … prior context … a lead to verify` 下面，那个措辞会让模型把它当成旧资料。
- 热复用 turn 走现有的 query 前缀注入。
- 附件消息里写死的 pptx/docx/xlsx/whisper 提示，改由这个域按附件类型召回，删掉写死的那段。

### 3.3 第三层：规则点名

产品规则依赖某个 skill 时（例如 AGENT.md R2 依赖 `s_expert-review`），规则本身写明 skill 名和什么时候用。这样它不依赖清单是否带描述。`32819c8f7` 已经这样改了 R2。r1 里"从 system prompt 文本里提取 `s_<name>` 当核心集"的做法删掉：CLI 没有"永远带描述"的状态，这么做也会把 MEMORY、KNOWLEDGE 里顺带提到的 skill 拉进来。

### 3.4 清洗规则

第一批已删（`789ff2486`）：30 个 75 天零使用、无任何引用的内置 skill。之后按这个顺序判断，使用数据只来自一个用户，不能单独决定删除：
1. **通不过 P7 strip test 的移出产品**，放进对应 DDD 的 `4-capabilities/`。
   - `s_internal-*`：按 glob 枚举，不手写名单（第一批时本机有 5 个，其中 concur、invoice-collect 已删）。`s_internal-crux-review` 在 SecDLC DDD 里已有一份，先合并，不要再复制一份。
   - **打包只带 git 跟踪的 skill**：现在打包从工作区复制，gitignored 的本地 skill 也进了安装包。这样 XG 本机的包和 CI 打出的包不一样，违反 usable-by-anyone P7 第 3 条（同一条使用路径）。
2. **功能重复的合并**，逐个读内容后决定留哪个：`s_code-review` / `s_qa` / `s_expert-review`；`s_deep-research` / 插件的 deep-research；`s_save-activity`、`s_save-context` / 自动 DailyActivity；`s_steeringify` / `s_self-evolution`；`s_summarize`。
   - 2026-10-25 已删：`s_steeringify`（连同 `distillation_hook` 里只写不读的 `steeringify_proposals.json`）、`s_deliver`（pipeline v1 不调用）、`s_code-review`（与 CLI 自带 `/code-review` 同名同功能；DDD 绑定的 `review_path` 改为 `s_expert-review`）、`s_save-activity`（自动 DailyActivity 已覆盖）、`s_summarize`。留下：`s_save-context`（交接给别的 agent）、`s_qa`（跑测试修 bug，归第 4 步）、`s_memory-distill`（蒸馏 hook 失败时的补救路径）、`s_deep-research`（本机没有插件版）。
3. **坏了的删**：引用的脚本、模块已不存在。
4. **最后才看零使用**：对陌生用户也说不清价值的删；说得清的留，让第二层召回帮它被找到。

### 3.5 防止再长回来

- spawn 时记录清单字符数、预算、只剩名字的 skill 数；超预算写 warning，TSCC 里列出名单。
- 每个用户看得到"你 90 天没用过的 skill"，可以对自己的安装关掉（`skillOverrides: "off"`；插件只能在插件管理里关）。产品 repo 的维护者用同一份报告做产品层清洗。不在 CI 里做"skill 必须有人用"的检查：产品 repo 没有全体用户的数据。

## 4. 不做的方案

| 方案 | 原因 |
|---|---|
| 提高 `skillListingBudgetFraction` | 往上下文堆，XG 否决；skill 一多还会再超 |
| r1 的"核心集保证完整描述" | CLI 没有这个状态；超预算时核心集里的 skill 照样按使用次数被截 |
| 按使用频率定名单 | CLI 已经这么排；新 skill 进不来；名单因人而异 |
| 非核心 skill 设 `off` | 召回到了也调用不了 |
| 对插件 skill 设 `name-only` / `off` | CLI 不允许；插件由插件管理控制 |
| 做一个路由 skill / trigger 表 | 4 月做过；和清单形成两份真相 |

## 5. 验收

- spawn 日志：1M 下清单 ≤ 预算；200K 下记录只剩名字的 skill 数（预期不为 0，由第二层兜底）。
- 召回闸 golden 集（离线，先于 S2 上线）：每个产品 skill 至少一条中文、一条英文的典型请求必须召回到它；一组无关消息不召回任何 skill。
- 真实 agent golden case（干净 session）：
  - "我改完了，请专家审一下" → 调用 `s_expert-review`（R2 点名）。
  - 200K 上下文 + "帮我做一份 PPT" → 注入 `s_pptx` 描述并调用。
  - 和 skill 无关的消息 → 不注入。
- 结构测试：只剩名字的 skill 仍然能被 Skill 工具调用（回归防护，CLI 升级时会发现）。

## 6. 实施顺序（每步一个 commit，测试先行，每步做完先量）

1. **S1 第一层**：描述 lint（≤250、TRIGGER 在前）+ 改写超长的产品 skill 描述 + `skillListingMaxDescChars` + spawn 时的清单度量。
   - 实施时（2026-10-25）实测：在 XG 的工作区，加上 250 字符上限后，清单从约 6.1 万字符降到约 4 万字符，刚好卡在 1M 的预算线上；这时第一批删掉的 30 个 skill 还在投影里，描述也还没改写。重新 build 之后会更小。
   - 所以"别的项目的领域 skill 设 `name-only`"先不做：1M 下不需要它；200K 下就算做了也放不下（光名字就要约 5 千字符），要靠 S3。
   - 度量只是估算，没算 CLI 自带的 skill，所以到预算的 90% 就提前告警。
2. **S2 清洗第二批**：P7 移出（含打包只带跟踪的 skill）、重复合并、坏的删；清单交 XG 过目。
3. **量**：1M / 200K 下各有多少 skill 只剩名字。
4. **S3 第二层**：`skills` 召回域 + 自己的闸 + 单独的块 + 每轮查；附件提示并入。先离线跑 golden 集再上线。
5. **S4 未用 skill 报告**。

## 7. 待定

- 描述上限取 200 还是 250：S1 时用实测清单长度定。
- spawn 前的活跃项目信号：tab 绑定项目是否在所有入口（桌面、channel、job）都有。

## 修订记录

- r1（2026-10-25）：核心集完整列出 + 其余 name-only + 每轮 recall。
- r2（2026-10-25）：zero-context skeptic 证伪 r1 的 5 个 HIGH（已逐条在 CLI 源码和 `session_router` 里核对）：
  1. 插件 skill 不受 `skillOverrides` 控制。
  2. CLI 没有"永远带描述"的状态，核心集保证不成立。
  3. 现有 recall 闸会挡掉 skill 命中，r1 自己的 golden case 就过不了。
  4. 漏掉了 `skillListingMaxDescChars` 这个能缩小上下文的现成开关。
  5. recall 不是每轮都跑。

  另外改正了 4 个 MEDIUM：清单只在开头发一次；核心集规则 1 会拉进顺带提到的 skill；`skillOverrides` 在 spawn 时定，那时还没有活跃项目；召回块的措辞会让模型当成旧资料。还补了一处 P7 问题：打包时带进了 gitignored 的 skill。
