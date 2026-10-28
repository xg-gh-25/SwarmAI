---
title: 知识寻址（Knowledge Addressing）—— "什么知识在哪、怎么取、怎么引用" 做成一项标准产品能力
date: 2026-10-28
created: 2026-10-28
updated: 2026-10-28
status: proposed (r2)
project: SwarmAI
supersedes-scope-of: docs/design/2026-10-28-ddd-map-and-on-demand-knowledge.md
---

# 知识寻址：一项标准产品能力，所有入口共用

## 0. 主旨

SwarmAI 的知识分在很多库里（DDD 四份文档和归档、DDD 的 `knowledge/` 语料、Library、pipeline 经验……）。
今天"去哪找、一条是什么、怎么告诉读者还有更多、取到的怎么引用"由每个入口自己决定，于是同一个脑，
各扇门说法不一，有的已经指向不存在的路径；而 Agent 从没被告诉过全集在哪，recall 就成了事实上的边界
（有 knowledge.md 的 4 个 run、10 个专家打开 DDD 文件 0 次）。

**这不是新造一层。** 平台已经有大半：`ddd_paths` / `DDD_CANONICAL_DOCS` 是布局的单一来源（有单源测试），
`recall_multi` 统一了五个召回域和 `recall_ddd` 的按用途配置，`ddd_usage` 是用量的单一入口，
`ddd_cultivation.content_signature` 是写入去重的单一闸口。缺的是四样东西，本设计只补这四样，
其余是把各扇门接到同一套上：

1. **一种单元身份**——今天至少 6 种，而且最常用的那种不稳定（§1.2）；
2. **一份从代码和磁盘生成的寻址描述**——让 Agent 知道有什么、在哪、什么问题去哪找（进 system prompt 的稳定部分 + 使用点的坐标）；
3. **一个按需取用的入口**——任何 cwd、任何会话都能用，返回整条 + 地址 + 身份；
4. **一个引用入口**——取到的知识被引用、被审计、被衰减看到，和 recall 起步包平等。

## 1. 现状（2026-10-28 读代码，两轮零上下文审查核实）

### 1.1 各扇门

| 门 | 怎么找 | 现在的问题 |
|---|---|---|
| 聊天注入 `session_router._inject_ddd_for_active_project` | `recall_all(ddd)` + 覆盖率过滤 | 只给 `**TECH.md** § Architecture`（约 112K 字符的小节），无路径无行号；用量按条目内容记，但条目内容并没有展示给 Agent（:1862-1896） |
| pipeline `run_knowledge`（knowledge.md / design / correctness 专家块） | `recall_ddd(purpose)` | 无地图；not-shown / pinned / pointer 行只有文档名；按需取到的不能引用（answers.py:107、hook_bridge.py:260 丢弃） |
| REFLECT `reflect_job` | 每条经验按全项目 `recall_ddd(reflect)` 找候选给判官 | 没问题，保持 |
| 培育写入 `ddd_cultivation` | 全文档 `content_signature` 精确去重 | 没问题，保持（这是 43K 重复的根治闸口，不能换成 BM25 top-N） |
| 衰减用量 `ddd_usage.record_ddd_hit` | 按归一化文本前 120 字作 anchor | 只有聊天一扇门喂它（:1896），pipeline、专家用过的条目照样衰减 |
| Brain Hub 搜索 / 提案（`routers/ddd_brain.py` :1760、:1388） | `recall_all` | UI 预览截到 400 字，没有"看全文"的去处 |
| `repo_guidance._ddd_block` | 4 份文档路径 | 让读 2MB；排在 lessons 后被挤掉，`seen` 先记后判断不重试 |
| 归档检索 `archive_browse.search_archive`（eval.py:705） | 自己的检索 | 和 recall 的单元、身份不一致 |
| `jobs/handlers/ddd_weekly_report.py:39` | **自己复制了一份 `ddd_path` 回退实现** | 第二份布局实现 |
| 技能散文 | 手写路径 | `s_qa` 读 `Projects/<project>/TECH.md`：对已迁移到编号布局的 DDD（如 SwarmAI）失效；`s_internal-crux-cr` 写死了一个具体项目名（P7） |
| native `s_expert-review`、Swarm Out 任务包 | — | 不带任何 DDD 知识 |
| 非 owner 渠道 | `prompt_builder._collect_shareable_ddd_paths`（`.project.json shareable` + 文档白名单 + 软链接逃逸守卫） | 已有正确的分享规则，新入口必须复用它 |
| 可分发包 | Kiro 目标已出 `knowledgeBase` 资源（ddd_packager:1448）；Claude Code 不能读 cwd 外（AIM 标准 §5.5） | 本设计不动（§4） |

### 1.2 单元身份：至少 6 种，最常用的不稳定

pipeline 的 `item_id` 是对单元原文取哈希，而 `split_blocks` 把条目下面的元数据注释一并算进单元
（ddd_entry_lifecycle.py:771）。实测 SwarmAI 的 IMPROVEMENT.md：1712 条条目里 **1481 条的单元文本含 `<!-- … -->` 注释**（ref/decay 元数据或 maturity 标记）。
衰减任务每次改写 `ref:` / `last:` / `decay:`，这些条目的 id 就变了——**没有人编辑过，引用就对不上**。
其他身份：用量的 anchor（前 120 字）、小节名、`content_signature`、退役/注入用的 `(title, section)`、REFLECT 的 `doc#section: head[:120]`。

### 1.3 按需入口本身是坏的

`py -m scripts.context_recall_cli` 把 cwd 放到 `sys.path` 最前（desktop_main.py:70、:77），在别的仓库里 ImportError，
在 SwarmAI worktree 里加载 run 正在改的代码；`--domains ddd` 返回小节名和分数，外加一个带内容的最佳条目，没有行号和身份。

## 2. 设计：补四样，接所有门

### 2.1 单元身份：一个函数 `unit_id(text)`（放在单元切分的家 `ddd_entry_lifecycle`）

- `unit_id = sha256(归一化文本)[:8]`，归一化 = 去掉元数据注释（复用 `ddd_usage._META_LINE_RE`）和行尾日期戳（`_TRAILING_STAMP_RE`），
  条目再按 `content_signature` 同样的规则去掉格式差异（bullet、writeback 前缀、`[type]`、`**`、大小写、空白）。
- **不改任何 DDD 文件**，不回填，不涉及 STEERING #20。衰减改写元数据不再改变身份；一条经验换个写法写两次，身份也相同——
  和写入去重闸口对"同一条"的定义一致。
- 用在：recall 起步包的 id（`run_knowledge.item_id` 改调它）、`knowledge` 命令的输出、引用校验。
- 代价：已有 run 审计里记的旧 id 是旧算法算的；旧 run 只对自己记录的集合校验，不受影响，新 run 用新算法。
- 不选"生产者把 id 写进元数据注释"：`_META_RE` 是锚定、定序的（:324-331），`to_comment()` 在每次注入/衰减/归档时重建注释，
  加字段会被丢；大部分条目没有元数据注释，而自动给散文或无标题条目加注释是被禁止的（:233-241）；写入方至少 8 处，
  移动 = 复制后退役还会重发 id。代价大、收益靠归一化哈希就能拿到。

### 2.2 寻址描述：一个生成器 `core/addressing.py`，数据挂在现有单一来源旁边

不新建库登记表。需要的描述作为数据加在已有的家里：`ddd_paths` 旁加每份文档"装什么、回答什么问题"，
`recall_multi.DOMAINS` 旁加每个域"能到哪"。生成器读这些常量和磁盘，产出三种大小：

| 产出 | 内容 | 给谁 |
|---|---|---|
| **寻址段**（system prompt 可缓存基段） | SwarmWS 布局；DDD 六段结构；路由表（产品意图/非目标 → PRODUCT，架构/约定/陷阱 → TECH，教训/踩坑 → IMPROVEMENT，当前焦点/决定 → PROJECT，深度参考 → `2-understanding/knowledge/`，跨项目 → Library，仓库经验 → `pipeline-lessons/<repo>.md`）；怎么取、怎么引用；项目表（名字 + DDD 根 + 绑定仓库）。**无大小、行号、条目数、摘要**（R30#4；也不破缓存） | 每个主会话 |
| `map(project, full)` | 每份文档和归档的绝对路径、大小、`##` 行号，超过 20K 的 `##` 列 `###`；`knowledge/` 文件清单；gates / capabilities 名；整行取舍，预算 8000 字符 | pipeline knowledge 文件、correctness 专家 |
| `map(project, brief)` | 根 + 每份文档路径和大小 + 怎么取，约 600 字符 | 其他专家、native expert-review、Swarm Out 任务包、repo_guidance |

- 寻址段在自己的 try 里，失败只丢这一段并 ERROR，核心文件不连坐。
- 和 08-14 删 PROJECTS.md 的关系：那是内容索引（摘要，会过期），理由"可经 recall + Glob 发现"已被 0/4 的实测推翻；
  这里只放结构和地址，内容永远现取。
- 隐私：群聊**和非 owner 私信**只给结构不给项目表；非 owner 渠道能看到的 DDD 只按 `_collect_shareable_ddd_paths` 的规则。

### 2.3 按需入口：`"$SWARM_BACKEND_BIN" knowledge map | find | get`

- 顶层子命令，和 `pipeline-v1` 一样在 `desktop_main.main` 分派，**不走 `py -m`**，不受 cwd 影响。
- `find --project P --query "<词>" [--doc TECH.md] [--in ddd|library]`：包 `recall_ddd` / `recall_all(library)`，返回**整条**单元 +
  `绝对路径:行号` + `unit_id`；放不下的用 `not_shown_note` 列出。`get <unit_id>|<path>:<line>`：取一条全文和它的 `unit_id`
  （Grep 找到行号后用它拿身份）。
- 只读，只到 DDD 根和 Library；MEMORY / EVOLUTION 仍走带隐私门的 `context_recall_cli`。
- 会话类型：沿用 `context_recall_cli` 的必填 `--session-type`、默认拒绝；渠道类型只放行 shareable 项目。
  **实施第一步先核实**非 owner 渠道会话是否有 Bash、daemon 是否给子进程设了可信的会话类型环境变量；有就用它，不靠 Agent 自报。

### 2.4 引用入口：`cite`

- 已知身份集合 = 本 run 召回过的 ∪ 项目 DDD（含 `*-archive.md`）里现存单元的 `unit_id`（项目取自 contract）。
- REQUIREMENTS / DESIGN_SPEC 的 `knowledge_refs`：未知仍拒绝，提示用 `knowledge get` 重新取；专家 finding 的 `knowledge_ref`：
  `answers.py` 形状不变，`hook_bridge._occ` 的 `carried` 换成已知集合，**未知仍丢弃、finding 照样算**。
- R27 全部读者：`schemas` 文案、`_check_knowledge_refs`、engine.py:2807 的计数（拆"起步包 / 按需"）、`audit.py`、`events.py:137`、
  `stages/evaluate.md`、`test_pipeline_v1_expert_knowledge.py:95-112`。REPORT 只出计数。
- 被引用的单元调 `ddd_usage.record_ddd_hit`（anchor 仍由 `entry_anchor_text` 算，`.ddd-usage.json` 形状不变，不迁移）。
  聊天门改为**只在内容真的展示给 Agent 时**才记用量。

## 3. 各扇门接法

| 门 | 改动 |
|---|---|
| 聊天 | 命中小节附 `绝对路径:行号`；无命中时不再单独注入指针（寻址段有项目表）；用量只记展示过的 |
| pipeline | knowledge 文件 = 起步包 + `map(full)`；not-shown / pinned / pointer 行改为绝对路径 + 行号；引用走 §2.4；stage 文档改写"读 knowledge 文件"那句 |
| 专家 | correctness：起步包 + `map(full)`；其他专家：`map(brief)`，handout 写明"只在 finding 需要时查"（按需读会花专家调用预算） |
| native `s_expert-review` | 专家提示带 `map(brief)` |
| Swarm Out | 任务包带被派往项目的 `map(brief)`（Hive 上有该 DDD 时） |
| `repo_guidance` | 一行"绑定到项目 P，见 Workspace Addressing"，放在 lessons 之前，放进去才记 `seen` |
| Brain Hub | 预览保留，旁边给 `path:line`，点开看全文 |
| `ddd_weekly_report` | 删自带的 `ddd_path` 回退，改用 `ddd_paths` |
| 技能 | `s_qa` 改用 `knowledge find --doc TECH.md --query 'Dev Commands'`；`s_internal-crux-cr` 去掉具体项目名；`scripts/lint_skills.py` 加一条：禁止根下旧布局 `Projects/<X>/<DOC>.md` 和具体项目名（允许 `2-understanding/` 新布局，写这些文档的技能如 s_persist 需要它） |
| REFLECT / 培育 / 分发包 | 不动 |

## 4. 不做的

- 不新建知识库登记表，不包一层新 API 去替代 `recall_multi`。
- 不改 DDD 文件形状、不回填 id。
- 不动写入去重（`content_signature`）和 REFLECT 候选。
- 分发包不加离线检索：Kiro 已有 `knowledgeBase`；Claude Code 读不到 cwd 外的安装目录，`find.py` 和绝对路径都会撞同一堵墙，
  且绝对路径会触发打包的宿主路径检查。要做，先用 `claude -p` 在外部 cwd 验证，另起设计。
- 不加"必须先查"的 gate；不加向量检索。

## 5. 怎么量

- 把 transcript 统计脚本收进 `backend/scripts/`：每个 run / 会话的按需读取（`knowledge` 调用 + 对 DDD 路径的 Read/Grep）、
  起步包引用 vs 按需引用。
- **run 1 上线后先量**：寻址段和 `knowledge` 命令到位后，看几个真实 run 和聊天；按需读取仍为 0 → 先改寻址段写法，再做 run 2。
- 衰减：被引用过的条目不进归档候选（回归测试）。

## 6. 要 XG 定的

1. 寻址段常驻 system prompt 可缓存基段（约 3K 字符）。我倾向进。
2. 实施走 pipeline（下面 3 个 run），还是直接改。跨 prompt_builder / run_protocol / recall / CLI 分派，我倾向 pipeline。

## 7. 实施（3 个 run，每个独立可验证）

| run | 内容 | 关键验收（每条先在 main 上红，做变异检查） |
|---|---|---|
| 1 能力本体 | `unit_id`；`core/addressing.py`（寻址段 + `map`）；`knowledge` 子命令；system prompt 寻址段 | 衰减改写元数据后 `unit_id` 不变；同一经验两种写法 `unit_id` 相同；寻址段在基段、无数字快照、群聊和非 owner 私信无项目表、生成失败只丢这一段；布局常量一改输出跟着变、旧布局也能出；`map` 只列根内 `.md`、软链接逃逸不列、行号指向标题行、超预算整行取舍；从别的仓库 cwd 调 `knowledge` 不加载那里的代码；无 DDD / 缺文档 / 非 UTF-8 不抛异常 |
| 2 接门 | pipeline、专家、native expert-review、Swarm Out、repo_guidance、聊天、Brain Hub 链接、引用和用量 | 按需 `unit_id` 被接受、编造的被拒/被丢；被引用条目进用量；not-shown 行是存在的绝对路径；repo_guidance 指针在 lessons 吃满时仍送达；聊天不记未展示内容的用量 |
| 3 清理 | `ddd_weekly_report` 去重复实现；s_qa、crux-cr；技能 linter 规则；本机 6 份过期 AGENTS.md（非产品代码，备份后改） | linter 在当前 s_qa 上红、改后绿，且不误报 s_persist 等新布局写法 |

每个 run 的审查：correctness + security（路径边界、prompt 注入面、隐私）；run 1、2 加 api-contract（身份、引用契约）。

## 修订

- r1（2026-10-28）：初稿：库登记表 + 单元模型 + 四操作 + 五种传输 + 6 个 run。
- r2（2026-10-28）：零上下文审查（已逐条对源码核实）：
  - 生产者赋 id 会被 `_META_RE` / `to_comment()` 丢掉、覆盖不到大部分条目、要动 8 处以上写入方 → 改为归一化内容哈希 `unit_id`，不改文件；
    同时发现现行 `item_id` 把元数据注释算进去，衰减一改写就变（1481/1712 条）。
  - 把 REFLECT 和培育去重迁到 `find` 会削弱 43K 重复的根治闸口，且"REFLECT 只认本 run 召回过的"说错了 → 两者不动。
  - 库登记表、HTTP 层、离线包大多是包装已有机制（P9）→ 删掉，只补四样；离线包和 AIM 标准 §5.5 冲突 → 不做。
  - 隐私规则来源错了 → 复用 `shareable` 解析，非 owner 私信也排除项目表。
  - linter 规则会误报写 DDD 的技能 → 只禁旧根布局和具体项目名。
  - 补上漏掉的门：`search_archive`、`ddd_weekly_report` 的第二份布局实现、Brain Hub 两处预览、非 owner 渠道的分享规则。
  - 计数修正：IMPROVEMENT 元数据注释 878 处（不是 1918）；身份至少 6 种（不是 3 种）。
