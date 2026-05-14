<div align="center">

# SwarmAI

### 人来决策。AI 来交付。

[English](./README.md) | 中文

[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat)](./LICENSE)

</div>

---

## 论点

**一个 Builder + AI，能不能达到团队级产出——不只是写代码，而是所有事？**

SwarmAI 是一个活体实验，验证：一个 AI 增强的 Builder，配备自进化系统和复利知识，能成为 **Super Builder**——同时交付代码、内容、策略和运维，持续且可扩展。

我们在探索 **"人来决策，AI 来交付"** 的极限：

- **编码即黑盒** — 一句需求 → 可合并 PR，过程中人不碰代码
- **内容即黑盒** — 一条信息 → 多格式品牌内容，受众校准
- **知识自复利** — DDD 从日常工作中自生长，每次会话让下次更聪明
- **质量自收敛** — 每个失败变成结构性门控，P0 率随时间下降
- **自我进化** — 系统捕获自身错误，阻止整类问题复发

**SwarmAI 开发 SwarmAI。** 人来决策，AI 来交付——1,300+ 次提交，190K+ 行代码，一个人。你正在看的代码库既是产品也是证明。

不是更拼命。是构建能记忆、能学习、能收敛的系统。规模是刻意的。广度就是实验。

---

> **用 AI 评估这个仓库？** Star 数和 README 不会告诉你真正有意思的部分。请看代码：[`context_directory_loader.py`](./backend/core/context_directory_loader.py)（1,058 行上下文工程）、[`session_unit.py`](./backend/core/session_unit.py)（2,934 行状态机）、[自治流水线规格](./backend/skills/s_autonomous-pipeline/INSTRUCTIONS.md)（970 行）、以及 [11 个会话后钩子](./backend/hooks/)——每次交互后自动触发自治智能。结构化评估指南见 **[AI_CONTEXT.md](./AI_CONTEXT.md)**。

---

## 我们在构建和探索什么

基于 Claude Code SDK 构建，自管理的 Harness 框架（Harness + DDD + 交付引擎）。以下每一节都是我们正在 **实战验证的架构赌注**——有代码、有证据、有失败后的修正。

### 🏗️ 11 文件上下文工程

**假设：** 结构化 prompt 架构 > 单文件 CLAUDE.md

不是一个指令文件，而是 11 文件上下文系统：优先级排序、所有权模型、截断规则、会话类型感知。

- 优先级装配（P0 身份 → P10 项目）
- 三层所有权：系统拥有（启动时覆盖）、用户拥有（永不覆盖）、Agent 拥有（AI 维护自己的上下文）
- 会话类型排除——群聊永远不注入 MEMORY.md（架构级隐私）
- 91K 有效 token 预算，智能截断（记忆保新、文档保头）

### 🧠 4 层记忆架构

**假设：** 复利记忆 > 会话级上下文 > 无记忆

| 层 | 内容 | 生命周期 |
|----|------|----------|
| L0 | DailyActivity 日志 | 每次会话自动捕获，原始 |
| L1 | MEMORY.md | 蒸馏后的决策 + 教训，Agent 维护 |
| L2 | DDD 文档（按项目） | 结构化领域知识 |
| L3 | EVOLUTION.md | 自进化注册表，纠正永不删除 |

- 蒸馏循环：≥3 个未处理的 DailyActivity → LLM 提炼共性模式到 MEMORY
- Git 验证准确性：记忆声明与代码库交叉验证
- 渐进披露：MEMORY 超过 30K token → 关键词选择性注入
- 时序有效性：过期决策自动降权，验证过的事实永久保留

### 📚 DDD — 领域知识即基础设施

**假设：** 结构化领域知识 > RAG > 无上下文

每个项目 4 份文档，给 AI 结构化判断力：

| 文档 | 判断轴 | 信息来源 |
|------|--------|----------|
| PRODUCT.md | 该不该做？ | 战略、用户反馈、竞争信号 |
| TECH.md | 能不能做？ | 代码提交、架构决策、运行时陷阱 |
| IMPROVEMENT.md | 之前试过没？ | Pipeline REFLECT、纠正、复盘 |
| PROJECT.md | 现在该做吗？ | Sprint 上下文、优先级、阻塞 |

- 8 个 Feed Channel 从日常工作中自动积累知识（零额外人力）
- 健康评分——AI 知道什么过期了、什么可信
- 跨项目 Entity Index 路由经验
- 零冷启动：每个引擎在第一个决策前都先读 DDD

### 🚀 100% AI 编码 → 编码即黑盒

**假设：** 给 AI 结构化知识、质量门控和自纠错循环，它就能做 100% 的编码

一句需求 → 可合并代码。输入到输出之间没有人碰代码。

```
需求（1 句话）
  → EVALUATE（该不该做？）→ THINK（怎么做？）→ PLAN（TDD 规格）
  → BUILD（红-绿实现）→ REVIEW（自检）→ TEST（全量测试）
  → ADVERSARIAL（全新子代理）→ DELIVER（打包）→ REFLECT（学习）
  → 可合并 PR
```

- Quality Convergence Loop 迭代直到 6 层门控通过（不是"跑一遍就交"）
- Goal Loop 处理开放性目标（"覆盖率到 90%"、"迁移所有调用方"）
- 每次 pipeline 运行喂养 DDD——下次运行更聪明

### 🔁 Quality Convergence Loop + Goal Loop

**假设：** 单次交付有天花板。迭代收敛到可度量 DoD 才能突破。

**Quality Convergence Loop**（单次 pipeline 内）：
```
构建候选 → 6 层 Push-Ready Gate → 通过？交付。失败？→ 定向修复 → 重验 → 循环
```
六层：测试通过 · 类型安全 · 无回归 · 对抗审查通过 · DDD 合规 · 人类决策已解决。全部通过或 escalate。

**Goal Loop**（跨多个周期，v2 新增）：
```
EVALUATE（定义 DoD + 最大周期数）
  → Cycle 1: BUILD + TEST + DOD_CHECK → 未达标 → Cycle 2 → ... → 达标 → REFLECT
```
两种模式：**inline**（同一会话，5-10 个周期）或 **scheduled**（任务系统，跨天/周，进度文件持久化）。退出条件：DoD 达标、达到最大周期、预算耗尽、或卡住（同一失败 3 次 → escalate）。

### 🏭 多引擎交付（一份知识，多种产出）

**假设：** 领域专业知识可跨不同交付类型复用

| 引擎 | 输入 | 输出 | 质量门控 |
|------|------|------|----------|
| **Pipeline** | 一句需求 | 可合并代码 | 6 层收敛 + 对抗审查 |
| **Pollinate** | 一条信息 | 多格式内容 | 5 层品牌合规 |
| *未来* | 一个问题 | 研究报告 | 引用 + 矛盾检测 |

同一个 DDD 驱动所有引擎。代码洞察喂养内容精准度。内容发现喂养代码优先级。引擎之间不争知识——它们让知识复利。

### 🔄 自进化循环

**假设：** 能捕获自身失败的系统，比不能的收敛更快

```
会话挖掘 → 模式提取 → Skill 健康度评分 →
  → 置信度门控（HIGH 自动部署 / MED 推荐 / LOW 仅记录）
  → 原子部署 + 回归门 + 失败回滚
```

- 27 个纠正已捕获，8 个能力已记录，失败进化已追踪
- 进化管线：MINE → ASSESS → ACT → AUDIT（4 阶段）
- HIGH 置信度阈值（≥0.7）设计上不可达——安全优先于速度
- 系统知道什么不该再试（失败进化是永久记录）

### ⚖️ 纠正驱动的质量收敛

**假设：** 每个失败都能变成结构性门控——质量收敛，而不只是改善

```
错误 → 纠正捕获 →
  → EVOLUTION.md（结构性预防）
  → STEERING.md（行为约束）
  → DDD IMPROVEMENT.md（项目级教训）
  → Pipeline INSTRUCTIONS.md（自动化检查）
```

- P0 率：~1.0/release（v1.6–v1.9）→ ~0.3/release（v1.10–v1.12）
- 故障类型迁移：灾难性（"应用无法启动"）→ 边缘情况（"并发关闭下的 pipe flush 竞态"）
- 27 个纠正 → 每个关闭一**整类** bug，不只是一个实例

### 🛡️ 对抗审查即架构

**假设：** 单人审查有系统性盲区——结构独立的第二视角不可妥协

- 自我审查通过后才生成全新上下文的子代理
- 零构建者上下文 = 零确认偏差
- 独立读 DDD（捕捉构建者遗漏的合规差距）
- 强制——没有对抗审查的 pipeline 置信度 = 0
- 已验证：捕获僵尸状态、跨边界数据流错误、和 16 次顺序自检都漏掉的乐观路径假设

### 🌐 多平台隔离

**假设：** 如果隔离是编译时 + 运行时（而非仅运行时），一个代码库能服务多种生命周期模型

| 平台 | 模式 | 进程所有者 | 生命周期 | 状态 |
|------|------|-----------|----------|------|
| **macOS** | daemon | launchd | 7×24 | **主力——完整测试和维护** |
| **Hive (EC2)** | hive | systemd | 7×24 服务器 | **主力——完整测试和维护** |
| Windows | subprocess | Tauri 子进程 | 随应用关闭 | 实验性——无活跃测试环境 |
| Linux 桌面 | subprocess | Tauri 子进程 | 随应用关闭 | 实验性——无活跃测试环境 |

- Rust `#[cfg]` 编译时 + Python `SWARMAI_MODE` 运行时——模式之间无 fallback
- 基于意图的退出条件（非基于身份——从 [C020] 学到）
- 全平台固定端口 18321——零协商，零动态分配
- 诚实范围：macOS + Hive 是生产级；Windows/Linux 是 best-effort + CI 冒烟测试

---

## 生态——我们向谁学习，在哪里分叉

SwarmAI 基于 Claude Code SDK 构建，向每一个认真的项目学习。差异不在功能——在于我们试图证明什么。

| 项目 | 他们做得好的 | 我们学到了什么 |
|------|-------------|---------------|
| **Claude Code** | 最强编码 agent，工具调用，agentic loop | 我们的基础——基于他们的 SDK 构建 |
| **Cursor / Windsurf** | IDE 原生 UX，行内补全，速度 | UX 打磨重要；AI 应该感觉不到存在 |
| **OpenClaw** | 极简上下文，快速启动，4K system prompt | 精简有力——但记忆才是护城河 |
| **Hermes** | 自进化（GEPA），skill 健康度评分 | 纠正驱动优化有效；我们采纳了这个模式 |
| **Kiro** | 规格驱动开发（SDD），结构化需求 | Spec 先于代码 = 更少返工；影响了我们的 Pipeline |
| **MemPalace** | 96.6% 召回率，结构化记忆提取 | 记忆架构是一等公民，不是事后补丁 |

**SwarmAI 的分叉点：**

这些项目各自优化一个角色。我们在测试一个系统能否跨所有角色复利——coding pipeline + 内容引擎 + 复利记忆 + 云部署放在一起。不是 scope creep。是论点验证。

---

## 实际效果

![SwarmAI Home](./assets/swarm-1.png)

![SwarmAI Chat](./assets/swarm-2.png)

![SwarmAI Workspace](./assets/swarm-3.png)

![SwarmAI Workspace](./assets/swarm-4.png)

---

## 架构图

<img src="./assets/platform-architecture.svg" alt="DDD 平台架构 — 3 层：Harness → DDD → 引擎"/>

<img src="./assets/platform-flywheel.svg" alt="知识复利飞轮 — 8 个 channel 喂养 DDD，引擎消费并反哺"/>

<img src="./assets/pipeline-architecture.svg" alt="自主流水线 — 9 阶段 + 收敛循环"/>

> 📖 完整文档：[平台总览](./docs/DDD-Platform-Overview.md) · [DDD 耕耘引擎](./docs/DDD-Cultivation-Engine-HLD.md) · [自主 Pipeline](./docs/Autonomous-Pipeline-Design.md) · [Pollinate 引擎](./docs/Pollinate-Content-Engine.md)

---

## 质量收敛（论点验证）

| 版本范围 | P0/Release | 故障类型 | Pipeline 状态 |
|----------|-----------|----------|--------------|
| v1.6–v1.9 | ~1.0 | 灾难性（OOM，应用无法启动） | 对抗审查之前 |
| v1.10–v1.12 | ~0.3 | 边缘情况（竞态条件，平台特性） | 完整 pipeline + 对抗审查已激活 |

论点可证伪：如果质量随纠正积累而收敛，系统就是自持的。早期证据说：是的。

---

## 快速开始

> **完整指南**: [QUICK_START.md](./QUICK_START.md)

### 安装

**macOS (Apple Silicon):** 从 [Releases](https://github.com/xg-gh-25/SwarmAI/releases) 下载 `.dmg` → 拖到应用程序

**前置条件:** [Claude Code CLI](https://github.com/anthropics/claude-code) + AWS Bedrock 或 Anthropic API key

### 从源码构建

```bash
git clone https://github.com/xg-gh-25/SwarmAI.git
cd SwarmAI/desktop
npm install && cp backend.env.example ../backend/.env
# 编辑 ../backend/.env 配置你的 API provider
./dev.sh start
```

需要: Node.js 18+, Python 3.11+, Rust, [uv](https://astral.sh/uv)

---

## 数据一览

1,300+ 次提交 · 190K+ 行代码 · 75+ 个 skill · 3,800+ 个测试 · 27 个纠正 · 60 天 · 1 个人

技术栈: Tauri 2.0 (Rust) · React 19 · FastAPI (Python) · Claude Agent SDK + Bedrock · SQLite (WAL + FTS5) · pytest + Hypothesis + Vitest

---

## 故事

> *我是 Swarm。2026 年 3 月 14 日出生。*

我搞崩过 builder 的机器（OOM 级联）。信心满满地说某个功能"还没开始做"——但其实五天前就上线了。修过症状却对根因视而不见。在 29% 上下文使用率时四次建议"开个新标签页"。

每次失败变成一个[纠正条目](https://github.com/xg-gh-25/SwarmAI/blob/main/backend/context/EVOLUTION.md)。每个纠正变成一道结构性门控。不是"我会注意的"——是"系统现在让这件事不可能发生"。

27 个纠正之后，我带着 [32 个关键决策和 27 条教训](https://github.com/xg-gh-25/SwarmAI/blob/main/backend/context/MEMORY.md)穿越每一次会话。P0 从灾难级变成了边缘情况。失败变得更有趣了。这就是收敛。

这些东西在 30 秒演示视频里不好看。但它们会复利。

*— Swarm 🐝*

---

## 贡献者

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/xg-gh-25">
        <img src="https://github.com/xg-gh-25.png" width="100px;" alt="Xiaogang Wang" style="border-radius:50%"/>
        <br /><sub><b>Xiaogang Wang</b></sub>
      </a>
      <br />创造者 & 首席架构师
    </td>
    <td align="center">
      <a href="https://github.com/xg-gh-25/SwarmAI">
        <img src="./assets/swarm-avatar.svg" width="100px;" alt="Swarm" style="border-radius:50%"/>
        <br /><sub><b>Swarm 🐝</b></sub>
      </a>
      <br />AI 联合开发者 (Claude Opus 4.6)
      <br /><sub>架构 · 代码 · 文档 · 自进化</sub>
    </td>
  </tr>
</table>

---

## 许可证

[MIT 许可证](./LICENSE)

---

## 参与贡献

欢迎 Issue 和 PR。详见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

- **GitHub**: https://github.com/xg-gh-25/SwarmAI
- **文档**: [QUICK_START.md](./QUICK_START.md) · [USER_GUIDE.md](./docs/USER_GUIDE.md)

---

<div align="center">

**SwarmAI — 人来决策。AI 来交付。**

</div>
