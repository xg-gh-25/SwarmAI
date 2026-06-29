---
title: "AI-Ready Repo 标准 — 为什么 CLAUDE.md + System Specs 不够用"
created: 2026-05-27
updated: 2026-05-27
status: published
---
<!-- GitHub Discussion #52: https://github.com/xg-gh-25/SwarmAI/discussions/52 -->

### AI-Ready Repo 标准 — 为什么 CLAUDE.md + System Specs 不够用

---

## 这些问题是不是很眼熟？

以下是正在落地 AI coding 的工程团队真实遇到的问题。如果任何一条戳中你——这篇文章就是写给你的。

### "AI Ready Repo 怎么建？"

> *"目前知识库以项目目录 + CLAUDE.md 文件形式存放，怎么设计才能让 AI 更好读取和利用？"*

你已经比大多数团队做得多了：有文档，写了 CLAUDE.md，甚至加了 steering files。但 agent 还是不*懂*。它遵循了你规则的字面意思，但抓不住架构的精神。

### "PRD 多来源怎么沉淀？"

> *"PRD 散落在 Notion、Google Docs、Confluence、Slack 频道，还有人脑子里。怎么完整呈现并让 AI 可消费？"*

问题不是 PRD 不存在——是它存在于 5 个地方，没有一个在 agent 的 context window 里。你不能把 30 页 PRD 贴进 CLAUDE.md。你需要的是蒸馏过的判断底座，不是文档堆积。

### "知识库有了但输出不准"

> *"已经放了 system spec 和开发规范到 steering files，但 AI 输出还是有偏差。需要收集具体 bad case 来分析原因。"*

这是最令人沮丧的阶段：你做了工作，写了文档，配了工具——结果输出还是不符合预期。问题几乎从来不是"agent 蠢"。通常是：正确的 context 没在正确的时间加载，或者过去失败的教训没有被捕获。

### "To B 场景的合规约束"

> *"我们是 ISV，要在客户环境（如 AIA 平台）里更新代码。必须遵守客户的审批流程、权限模型、发布规范。怎么在合规 + 多方协作的约束下用 AI coding，既提效又不拖节奏？"*

企业团队面临独特约束：agent 不仅需要知道"怎么写代码"，还需要知道"什么不允许做"。合规规则、部署门禁、审批流程——这些是硬约束，一个平的规则文件无法用足够的细粒度表达。

### "AI 不懂业务"

> *"我们在储能/新能源领域。领域知识没有注入。Agent 写的代码语法正确但对业务逻辑零理解。"*

通用编码能力是基本功。一个不知道"电池 SOC 不能低于 20%"或"逆变器指令需要安全联锁"的 agent 写出的代码能过 lint 但在生产环境出事。领域知识需要一个归属地。

### "生成代码质量不高"

> *"缺统一规范和 Review 标准。不同团队成员得到的输出质量差异巨大。"*

没有"在这个 codebase 里什么叫好代码"的共享基线，质量完全取决于谁写 prompt。repo 自身应该编码质量标准——不是依赖个人的 prompt 技巧。

### "Spec 编写困难"

> *"团队不清楚面向 AI 的 Spec 怎么写。传统 PRD 格式对 agent context 不起作用。"*

确实。AI 可消费的 spec 不是传统 PRD。它更短、更结构化、以判断为导向（非目标比功能列表更重要），并且用 agent 能解析为行动的格式。

### "成本控制——没有 ROI 度量"

> *"多工具并行（Claude Code + Cursor + Copilot）。无法衡量每个工具的 ROI。难以 justify 预算。"*

如果你不能度量"这个 sprint AI 帮我们省了多少"，你就无法守住预算。而如果 repo 没有 AI ready，工具表现不佳 → ROI 看起来比实际差 → 工具被砍 → 所有人输。

---

## 共同的根因

以上每一个问题都追溯到同一个 gap：

**你的 repo 有 context 文件，但没有 context 架构。**

一个 markdown 告诉 agent "用 snake_case" 或 "我们用 PostgreSQL" 是 configuration，不是 knowledge。这是给新员工发 style guide 和给他六个月 institutional knowledge 的区别。

这篇文章提出一个 convention standard：让任何仓库对 AI ready——不绑定任何特定工具。

---

### Gap：静态配置 vs 活的知识

现在每个工具提供什么：

| 工具 | Context 机制 | 内容 | 限制 |
|------|-------------|------|------|
| **Claude Code** | `CLAUDE.md` | 风格规则、项目描述、命令 | 平的。无结构。无关注点分离。不学习。 |
| **Kiro** | `.kiro/steering/` + `.kiro/specs/` | Steering 规则 + feature specs | Per-feature 范围。Specs 是 one-shot（写→执行→完）。无跨 session 记忆。 |
| **Cursor** | `.cursorrules` + `@docs` | 规则 + 索引文档 | 规则静态。文档是参考，不是判断。 |
| **Windsurf** | `.windsurfrules` | 风格规则 | 和 .cursorrules 一样——config 不是 knowledge。 |

**它们都缺什么：**

1. **关注点分离** — "我们在建什么？" vs "怎么建？" vs "学到了什么？" 是三种根本不同的知识类型，需要不同的更新节奏
2. **积累的教训** — 错误、坏模式、纠正。最有价值的 context 是"我们试了 X，因为 Y 失败了"——今天没有工具捕获这个
3. **结构化代码感知** — 谁调了谁，改了这里什么会 break。文件级规则无法表达
4. **活的知识** — 从工作中生长的文档，不是从忽视中腐烂的文档

CLAUDE.md 是一个平的单文件。就像用一个 Google Doc 代替组织架构图 + 知识库 + 事后复盘库来经营公司。

---

### 标准：`.ai-context/`

一个 convention——任何 AI coding tool 都能消费的目录结构：

```
your-repo/
├── .ai-context/
│   ├── PRODUCT.md          # 建什么？为什么？给谁用？
│   ├── TECH.md             # 怎么建？技术栈、约定、API
│   ├── IMPROVEMENT.md      # 什么行得通、什么失败了、避什么坑
│   ├── PROJECT.md          # 当前焦点、最近决策、待定事项
│   └── code-intel.db       # （可选）预计算的依赖图
│
├── .claude/                 # 工具专用（Claude Code hooks, skills）
├── .kiro/                   # 工具专用（Kiro steering, specs）
├── .cursorrules             # 工具专用（Cursor rules）
└── src/                     # 你的代码
```

### 4 个文档 — 关注点分离

| 文档 | 回答什么 | 更新频率 | 谁更新 |
|------|---------|---------|--------|
| **PRODUCT.md** | 建*什么*？为什么？给谁？不做什么？ | 每周（战略调整时） | 人 |
| **TECH.md** | *怎么*建？技术栈、约定、关键子系统、API 契约 | 架构变更时 | 人 + Agent |
| **IMPROVEMENT.md** | 什么*行了*？什么*挂了*？*避*什么？ | 每个重要任务后 | Agent（人审核） |
| **PROJECT.md** | *现在*在做什么？当前焦点、最近决策、阻塞项 | 每个 session | Agent |

**为什么 4 个文件，不是 1 个？**

因为它们的以下属性完全不同：
- **作者** — PRODUCT.md 是人写的战略。IMPROVEMENT.md 是 agent 积累的经验。
- **节奏** — PROJECT.md 每天变。TECH.md 每月变。混在一起 = 过时的战略或嘈杂的架构文档。
- **消费者** — Agent 写代码时读 TECH.md。审查时读 IMPROVEMENT.md。评估需求时读 PRODUCT.md。不同阶段需要不同上下文。

---

### 为什么这比单文件好

#### 场景 1："Agent 不理解我们的架构"

**用 CLAUDE.md（平的）：**
```markdown
# Project
We use FastAPI, React, SQLite...
[200 行什么都混在一起]
```
Agent 读 200 行，注意力稀释。关键的 "macOS 上永远不要用 lsof" 淹没在 "我们用 Tailwind" 和 "通过 launchd 部署" 之间。

**用 `.ai-context/`（结构化）：**
- Agent 写代码？→ 加载 TECH.md（约定、API、陷阱）
- Agent 审查代码？→ 加载 IMPROVEMENT.md（过去的失败、反模式）
- Agent 评估需求？→ 加载 PRODUCT.md（非目标、优先级）

**正确的时间，正确的上下文。**

#### 场景 2："我们反复犯同样的错"

**用 CLAUDE.md：**
每次出 bug 之后手动加 "不要做 X"。没人记得更新。文档腐烂。

**用 IMPROVEMENT.md：**
每个重要任务后 agent 自动追加：
```markdown
## What Failed
- 2026-05-20: subprocess.run() 在 async 上下文阻塞事件循环 → 用 asyncio.to_thread() + timeout
- 2026-05-15: 改了 shared function 签名 → 下游 3 个 caller 挂了（先查 blast_radius）
```

**repo 随每个任务变得更聪明。**

#### 场景 3："PRD 散落各处"

**用 CLAUDE.md：**
把整个 PRD 贴进去。3000 token 产品 context 和 500 token 编码规则混一起。两者都不 effective。

**用 PRODUCT.md：**
蒸馏过的产品 context — vision、优先级、非目标、audience map。不是完整 PRD，是**判断底座**：足以让 agent 回答"这个该不该建？"和"这跟我们方向对不对齐？" 完整 PRD 作为链接引用。

---

### 结构感知：按 Repo 规模分级

4 个文档给 agent *判断力*（建什么、怎么建、避什么）。但代码改动时，还需要*结构感知*——谁依赖谁。方案按代码库规模分级：

#### 小 repo（<5 万行）：`CODEBASE.md` — 手写地图

小项目里 agent 可以 grep 和读到大部分文件。计算图 overkill。但一个**手写的模块地图**仍然有用——告诉 agent "各个部分怎么连接"，不用它读 100 个文件才发现架构：

```
.ai-context/
└── CODEBASE.md       # ~50-100 行，手动维护
```

示例 `CODEBASE.md`：
```markdown
# Codebase Map

## 模块概览
- `src/auth/` — 认证与授权（JWT, OAuth2）
- `src/billing/` — 支付处理、订阅管理
- `src/api/` — REST 端点（依赖 auth + billing）
- `src/workers/` — 后台任务（依赖 billing）

## 关键依赖
- api → auth（每个端点都验证 token）
- api → billing（checkout、订阅端点）
- workers → billing（发票生成、支付重试）
- billing → auth（支付操作的权限检查）

## 共享接口（修改需谨慎）
- `src/auth/validator.py::validate_token()` — api + workers + billing 都调
- `src/billing/models.py::Subscription` — 4 个模块使用
- `src/common/errors.py` — 所有模块 import 错误类型

## 入口
- `src/api/main.py` — FastAPI 应用
- `src/workers/scheduler.py` — Celery beat
- `scripts/migrate.py` — DB 迁移
```

**为什么这对小 repo 有效：** Agent 在 session 开始时读一次 CODEBASE.md → 知道架构 → 对 blast radius 做出明智决策。成本：5 分钟写，~200 token 注入。新增模块时更新（大概一个月一次）。

**什么时候升级：** 如果你发现 agent 经常 break 跨模块接口，或者 CODEBASE.md 超过 200 行 → 该上 code-intel.db 了。

---

#### 中等 repo（5-20 万行）：两者都用

用 CODEBASE.md 做高层架构概览（人类写摘要还是比 parser 好）+ code-intel.db 做精确的 caller/callee 查询。Agent 读地图做定位，查图做具体决策。

---

#### 大 repo（>20 万行）：`code-intel.db` — 预计算图

这个规模没人能手动维护准确的依赖地图。需要自动化的结构分析：

```
.ai-context/
├── CODEBASE.md       # 高层模块概览（仍然有用）
└── code-intel.db     # SQLite, 20 万行约 30-50MB
```

Schema:
```sql
CREATE TABLE code_nodes (
    id TEXT PRIMARY KEY,           -- "src/auth/validator.py::validate_token"
    file_path TEXT NOT NULL,
    node_type TEXT NOT NULL,       -- function | class | method
    name TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    language TEXT
);

CREATE TABLE code_edges (
    source_id TEXT NOT NULL,       -- 调用方
    target_id TEXT NOT NULL,       -- 被调方
    edge_type TEXT DEFAULT 'calls',
    confidence REAL DEFAULT 1.0
);
```

PreToolUse hook 在每次 `Read` 时查询：
```
Agent 读: src/auth/validator.py
注入: "⚡ validate_token() 有 12 个 callers 跨 4 个包。Blast radius: HIGH。"
```

没有这个，agent 改 `validate_token()` 签名 → 4 个包 break。有了这个，agent 改之前就知道。

（完整实现：[Discussion #50](https://github.com/xg-gh-25/SwarmAI/discussions/50)）

---

#### 总结：按规模选方案

| Repo 规模 | 结构感知方案 | 投入 | 什么时候升级 |
|-----------|------------|------|------------|
| **<5 万行** | `CODEBASE.md`（手写） | 5 分钟 | Agent 反复 break 跨模块代码时 |
| **5-20 万行** | `CODEBASE.md` + `code-intel.db` | 30 分钟配置 | 不用——这是 sweet spot |
| **>20 万行 / monorepo** | `code-intel.db` + 跨包引用 | 1 小时配置 + CI 集成 | N/A——已经是天花板 |

---

### 怎么对接你的工具

#### Claude Code

```
.ai-context/           → CLAUDE.md 里引用："Read .ai-context/ docs for project knowledge"
.claude/hooks/         → PreToolUse hook 根据任务类型加载对应 .ai-context/ 文档
                       → PostSession hook 追加 IMPROVEMENT.md
```

Claude Code 的 hooks 是 enforcement 机制。Convention 提供知识。组合 = 无需手动维护的活 context。

#### Kiro

```
.ai-context/PRODUCT.md   → 告知需求生成（产品 context）
.ai-context/TECH.md      → 告知设计 spec（架构约束）
.ai-context/IMPROVEMENT.md → Steering file："避免这些模式"
.kiro/steering/ai-context.md → "生成 design spec 之前总是读 .ai-context/TECH.md"
```

Kiro 的 spec-driven 工作流从 PRODUCT.md（塑造需求）和 TECH.md（约束设计）获益最大。IMPROVEMENT.md 变成防止重复过去错误的 steering file。

---

### 活的循环（为什么这跟"多写点文档"不同）

静态文档会腐烂。关键创新不是目录结构——是**积累循环**：

```
        ┌─────────────────────────────────────────┐
        │                                         │
        ▼                                         │
   Agent 执行任务                                  │
        │                                         │
        ├─ 读 TECH.md（怎么建）                     │
        ├─ 读 IMPROVEMENT.md（避什么）              │
        │                                         │
        ▼                                         │
   任务完成（或失败）                                │
        │                                         │
        ├─ 学到了什么？                             │
        │   └─ 追加 IMPROVEMENT.md ───────────────┘
        │
        ├─ 架构变了？
        │   └─ 更新 TECH.md
        │
        └─ 做了什么决策？
            └─ 更新 PROJECT.md
```

**每个任务让 repo 更聪明。** 不是因为有人记得更新文档——是因为 agent 结构性地做这件事。

没有循环，你有的是 documentation。有了循环，你有的是 **institutional memory**。

---

### 5 分钟快速开始

```bash
mkdir -p .ai-context

cat > .ai-context/PRODUCT.md << 'EOF'
# 产品 Context

## 我们在建什么
[一段话：这个项目是什么？]

## 为什么
[我们在解决什么问题]

## 给谁用
[目标用户]

## 不做什么
[明确不会做的事——对 agent 判断至关重要]
EOF

cat > .ai-context/TECH.md << 'EOF'
# 技术 Context

## 技术栈
[语言、框架、关键库]

## 架构
[关键子系统及其连接方式]

## 约定
[命名、文件结构、遵循的模式]

## 陷阱
[看起来对但在这个 codebase 里是错的东西]
EOF

cat > .ai-context/IMPROVEMENT.md << 'EOF'
# 改进日志

## 行得通的
[产生好结果的模式]

## 失败的
[导致 bug 的模式——最有价值的部分]

## 注意事项
[反复出问题的区域]
EOF

cat > .ai-context/PROJECT.md << 'EOF'
# 当前项目 Context

## 当前焦点
[现在在做什么？]

## 最近决策
[最近 3-5 个重要决策及其理由]

## 待定事项
[未解决的问题、阻塞项]
EOF

# 在你的工具配置里引用
echo "Read .ai-context/ docs for project knowledge." >> CLAUDE.md
```

**上手时间：5 分钟。** 填写括号里的内容。你的 agent 立即有了结构化 context，而不是什么都没有（或者一个 500 行的平文件）。

---

### 设计决策

#### 为什么是文件系统，不是服务？

Git-tracked 文件意味着：
- 免费的版本历史
- 离线可用
- 适用于所有工具（任何工具都能 Read 文件）
- 团队在 PR 里看到变更
- 无配置、无认证、无 API key

#### 为什么是 markdown，不是 YAML/JSON？

Agent 能消费，人也能读。工程师真的会编辑 markdown。没人自愿编辑 JSON 配置来做知识管理。

#### 为什么 4 个文件，不是 7 或 12 个？

最小可行的关注点分离。4 个足以消除交叉污染（战略 vs 战术 vs 教训 vs 状态），又不至于创建没人维护的归档系统。

#### 为什么是 `.ai-context/` 不是 `.claude/` 或 `.kiro/`？

工具无关。你的 context 架构不应该锁在一个 vendor 上。Claude Code 读它。Kiro 读它。Cursor 读它。换工具，保留知识。

---

### 和现有方案对比

| 方案 | 知识结构 | 学习能力 | 代码感知 | 工具锁定 |
|------|---------|---------|---------|:---:|
| **CLAUDE.md** | 1 个平文件 | ❌ 手动 | ❌ | Claude |
| **Kiro SDD** | 3 per-feature specs | ❌ One-shot | ❌ | Kiro |
| **.cursorrules** | 1 个规则文件 | ❌ 手动 | ❌ | Cursor |
| **Aider conventions** | `.aider.conf.yml` | ❌ 手动 | Repo map（基础） | Aider |
| **`.ai-context/`（本标准）** | 4 个关注点分离文档 | ✅ Agent 积累 | ✅ code-intel.db | **无** |

---

### 这能做到什么（今天没有其他方案能做到的）

1. **跨 session 学习** — IMPROVEMENT.md 持久化失败教训跨 agent session。今天没有 agent 记得昨天哪里出了问题，除非你告诉它。

2. **正确时间正确 context** — 不同文件给不同阶段（编码 vs 审查 vs 规划）。没有注意力稀释。

3. **Blast radius 感知** — code-intel.db 提供结构化代码理解。没有其他 convention 包含这个。

4. **团队知识，不是个人配置** — Git-tracked 意味着整个团队受益。新成员的 agent 立即拥有所有积累的知识。Onboarding 成本 → 零。

5. **换工具零成本** — 从 Cursor 换到 Claude Code 换到 Kiro？`.ai-context/` 跟着你走。只有工具专用的 hooks 要改。

---

### 行动号召

如果这篇有共鸣，试试：
1. 在你的 repo 里创建 `.ai-context/`
2. 填写 4 个文档（10 分钟）
3. 在工具配置里引用（CLAUDE.md, .kiro/steering/ 等）
4. 下次重要任务后，往 IMPROVEMENT.md 加一条

反馈：agent 输出质量提升了吗？还缺什么？

相关文章：
- [DDD Cultivation — 从工作中生长的领域知识](https://github.com/xg-gh-25/SwarmAI/discussions/9)
- [AI Agent 读不懂 50 万行代码——预计算代码图](https://github.com/xg-gh-25/SwarmAI/discussions/50)
- [设计哲学 — 六根支柱](https://github.com/xg-gh-25/SwarmAI/discussions/39)

---

*发布自 [SwarmAI](https://github.com/xg-gh-25/SwarmAI) — 这套标准已在生产环境运行 3 个月，覆盖 7 个项目、17 万行代码，每周自动积累 14 条教训。*
