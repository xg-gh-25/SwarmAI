<div align="center">

# SwarmAI

### 人来决策。AI 来交付。

[English](./README.md) | 中文

[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat)](./LICENSE)
[![Stars](https://img.shields.io/github/stars/xg-gh-25/SwarmAI?style=flat)](https://github.com/xg-gh-25/SwarmAI/stargazers)

</div>

<!-- TEMPORARY: re-star notice — remove once star count has recovered -->
> ⭐ **我们的 star 被误清零了。** 2026-06-27 仓库可见性被误操作切换了几分钟，
> 而任何这种切换都会清空 GitHub 的 stargazer 列表——我们 ~200 个 star 一夜归零。
> 代码与历史完好，只有星数被清掉。**如果你之前 star 过 SwarmAI，麻烦再点一次 🙏**
> ——star 是新人发现这个项目的主要途径。

---

**SwarmAI 是一个自进化的 Agent OS** —— 每次交互升级的不是模板，是系统的认知本身。

你的 AI 团队，一个人指挥。

---

## 为什么是 SwarmAI

我们终于有了聪明到能推理、写代码、做判断的软件——可它**每天早上醒来都失忆。** 每个会话从零开始：你给过的上下文、它昨天犯的错、你纠正过的判断——全部归零。大多数"AI 工具"是**扁平**的：一个聪明的模型，困在土拨鼠之日里。

SwarmAI 押的是相反的赌注——价值应该**复利**。每一次交互都该让系统比之前更敏锐一点，且是永久的。

这就重新框定了那个显而易见的问题。当你问*"一个桌面 app 凭什么要 22 万行代码、13 个引擎？"*——你就量错了维度：**这不是应用的复杂度，是一个 agent 认知的复杂度。** 四件事把"心智"和"模型"区分开：它跨时间**连续**、它**自我纠错**、它**遗忘**掉不再重要的东西、它的**判断力随使用复利**。传统软件对这四样没有任何对应物——程序不会在两次运行之间变聪明，更不会改写自己的规则。SwarmAI 就是要建那个缺失的层：不是更大的模型，而是**它周围的认知操作系统。**

这些设计决策，只有透过这个视角才讲得通：

- **进化是 OS 补丁，不是堆数据。** 大多数 agent memory 项目都在堆条目。我们把*认知*（OS）和*知识*（硬盘）分开：改 `SOUL.md` 一行，比加一千条 memory 更能改变判断——而且每次改动都是一个 `git diff`。
- **复发的错误被做成结构上不可能。** 当一个错误类别重复出现，我们不再加一条 lesson——我们加一道门控，再造一条让错误动作物理上无法发生的路径。人类靠 carefulness，agent 该靠 structure。
- **知识必须能死。** 90 天未被引用 → 退役。只积累不淘汰，是所有 memory 系统腐烂的根因。衰减，是 agent 所知之物的自然选择。
- **会话是离散的，智能不该是。** Hooks 在会话*之间*触发，让下一个会话热启动。大多数框架接受冷启动；我们拒绝。

公开验证中的命题：**一个人 + AI 能否达到一整个团队的产出规模？** 不是靠放大模型——而是靠在它周围构建复利闭环。闭环*本身*就是产品；你抽出任何一个引擎，效果都不再成立。

截至 **v1.22.0**，这条闭环已经端到端健康运行——会话自愈、知识自动培育与衰减，进化引擎累计记录 **42 条纠正**，把复发的失败类别转化成结构性门控，而不是重复的 lesson。

> **这不是产品 Demo——这是一个边发生边记录的活实验。** 下面是 60+ 篇深度讨论：每个引擎背后的架构决策、踩过的坑、复盘报告。

### 📚 从这里开始 —— 代码背后的思考

| | |
|---|---|
| 🗺️ **[阅读矩阵 — 3 条精选路径](https://github.com/xg-gh-25/SwarmAI/discussions/35)** | **Builder**（~45 分钟）· **Architect**（~60 分钟）· **Leader**（~30 分钟）—— 别全读，挑你的路径 |
| 💬 **[全部讨论](https://github.com/xg-gh-25/SwarmAI/discussions)** | 思想领导力、架构深潜、复盘报告 —— 同时镜像在 [`docs/discussions/`](./docs/discussions/) |
| 🧭 **[设计哲学 — 六根支柱](https://github.com/xg-gh-25/SwarmAI/discussions/39)** | 信念如何变成强制 —— 每一条都是从一次失败里挣来的 |
| 🕸️ **[撑起这一切的那套 Ontology](https://github.com/xg-gh-25/SwarmAI/discussions/96)** | 一套 ontology（🏷️ 分类 + 🕸️ 关系，不上 Neo4j）统一记忆、DDD 与代码智能 |
| 🎞️ **[AI-Native 转型 — 主题演讲 Deck](https://xg-gh-25.github.io/SwarmAI/AI-Native-Transformation-Deck-CN.html)** | "2x 天花板 → 10x 复利"的范式转移，可在线播放的幻灯片（[English](https://xg-gh-25.github.io/SwarmAI/AI-Native-Transformation-Deck.html)）· 或读[文章](./docs/AI-Native-Transformation-CN.md) |

![SwarmAI](./assets/swarm-2.png)

---

## 快速开始

```bash
git clone https://github.com/xg-gh-25/SwarmAI.git && cd SwarmAI
cd backend && uv sync && cp .env.example .env   # 填入 API key
cd ../desktop && npm install && npm run tauri:dev
```

**macOS (Apple Silicon)：** 或从 [Releases](https://github.com/xg-gh-25/SwarmAI/releases) 下载 `.dmg`

需要：Node.js 18+, Python 3.11+, Rust, [uv](https://astral.sh/uv), [Claude Code CLI](https://github.com/anthropics/claude-code)

> 📖 完整安装指南：[QUICK_START.md](./QUICK_START.md)

---

## 架构

<img src="./assets/platform-architecture.svg" alt="平台架构" width="100%"/>

```
┌─────────────────────────────────────────────────────────────┐
│  交付引擎           Pipeline · Pollinate · Eval              │
├─────────────────────────────────────────────────────────────┤
│  知识层             DDD · Memory · Evolution                 │
├─────────────────────────────────────────────────────────────┤
│  Agent Harness      Context · Sessions · Hooks · Jobs        │
└─────────────────────────────────────────────────────────────┘
```

---

## 核心引擎

如果你也在用 AI 写代码、做内容、跑运营 —— 下面 13 个引擎就是这套"复利押注"的拆解。每个独立有用,组合起来才是那条让系统越用越聪明的闭环。(点 `code` 直接读引擎本身 —— 实现即文档。)

| # | 引擎 | 做什么 | 详情 |
|---|------|--------|------|
| 1 | **上下文管理** | 11 文件 Prompt 架构，100K 预算，三层所有权 | [docs](./docs/DDD-Platform-Overview.md) |
| 2 | **记忆流水线** | 4 层持久化：DailyActivity → 蒸馏 → 复利召回 | [docs](./docs/Memory-Management-Design.md) |
| 3 | **DDD 知识引擎** | 自生长领域知识，7 类本体，达尔文式淘汰 | [docs](./docs/DDD-Cultivation-Engine-HLD.md) |
| 4 | **自主流水线** | 一句需求 → 可推送代码。9 stages · 3 gates（framing/plan/build）· 2 modes（Full + Goal Loop） | [docs](./docs/Autonomous-Pipeline-Design.md) |
| 5 | **Pollinate 引擎** | 一条消息 → 多格式品牌内容。9 阶段 · 11 轨道 · 3 级门控 · DDD 飞轮 | [docs](./docs/Pollinate-Content-Engine.md) · [架构图](./assets/pollinate-architecture.svg) |
| 6 | **自进化** | 认知 L0→L3 补丁。42 条纠正 → 复发类别转化为结构性门控 | [docs](./docs/Self-Evolution-Harness-Design.md) |
| 7 | **自愈合** | 不可见恢复：5 传感器，自动重生，用户无感知 | [code](./backend/core/session_healing.py) |
| 8 | **多标签页 + MessageStore** | 并发会话，阶段门控单写者，跨标签页隔离 | [code](./desktop/src/stores/MessageStore.ts) |
| 9 | **Hook 系统** | 运行时 + 生命周期 hooks。会话永不冷启动 | [code](./backend/core/hook_builder.py) |
| 10 | **任务系统** | 后台智能：13 信号源，定时任务，预算门控 | [code](./backend/jobs/scheduler.py) |
| 11 | **4 平台后端** | macOS daemon · Hive (EC2) · Windows · Linux。编译时隔离 | [code](./backend/main.py) |
| 12 | **技能 + 通道** | 88 技能（lazy/always），Slack 网关，三层权限 | [code](./backend/core/skill_registry.py) |
| 13 | **Eval（本体感觉）** | 解耦、系统级：Golden Set + git 绑定回归门控。证明收敛，而非凭感觉 | [docs](./docs/OS-Eval-Function-Design.md) · [架构图](./assets/eval-architecture.svg) |

**复利闭环：** 记忆 → Pipeline 判断 → DDD → 进化 → 门控 → 记忆。去掉一个，其余变弱。

<img src="./assets/aidlc-autonomous-pipeline-v4.svg" alt="自主流水线 — 9 阶段 · 3 道门 · 2 模式" width="100%"/>

同一套 DDD 驱动的模式，不止用于代码，也用于内容。**Pollinate** 把一条消息变成任意格式 —— 并把经验写回 DDD，让每一次交付都复利：

<img src="./assets/pollinate-architecture.svg" alt="Pollinate — 媒体价值交付引擎 · 9 阶段 · 11 轨道 · 3 级门控 · DDD 飞轮" width="100%"/>

### Eval OS —— Agent 时代的 `assert` 替代品

传统软件靠 `assert` + 一盏绿灯的 CI 就能担保"没有回归"。Agent 不行:输出是**非确定的**(即便 temp=0 也无法逐比特复现),**prompt 就是源码,却没有 diff/review/rollback**,而且**依赖会自己漂移**(模型静默更新 —— 你什么都没发,行为却变了)。所以 SwarmAI 把 **Eval 当作 `assert` 的继任者**:一个解耦的、系统级的子系统,衡量这套 OS 是否依然*正确*,而不只是*活着*。

它是**本体感觉,不是外部打分** —— Eval 对着 Agent 的*真实*规则文件起一个干净会话,从 **6 个维度** / **15 个分类**给判断力打分,每次运行都 **git 绑定**到当次 commit,让回归可追溯。它作为门控嵌进研发生命周期,而不是一个你想起来才跑的脚本:**build 不阻断,release 才阻断** —— CI/部署上的回归或主干变红,直接拦住发布。

> 📖 完整架构 + 方法论(对齐 AWS Eval-First 框架):[Discussion #83](https://github.com/xg-gh-25/SwarmAI/discussions/83)

<img src="./assets/eval-architecture.svg" alt="SwarmAI Eval OS — 解耦系统级子系统 · WRITE → Golden Set → Execute → Consume · 6 维度 · 15 分类 · git 绑定回归门控" width="100%"/>

> 📊 更多架构图：[复利飞轮](./assets/platform-flywheel.svg) · [上下文](./assets/context-engineering.svg) · [记忆](./assets/memory-pipeline.svg) · [DDD](./assets/ddd-three-layer-stack.svg) · [会话](./assets/multi-tab-sessions.svg) · [任务](./assets/job-system.svg) · [进化](./assets/self-evolution.svg)

---

## 论点与设计哲学

**一个 Builder + AI，能不能达到团队级产出？** 我们在实战验证。

1. **一次做对是真正的 token 优化。** 便宜模型迭代 5 次，成本比一次正确交付还高。编码/内容即黑盒：输入 → 有质量保证的输出。
2. **分工是人类认知带宽的妥协，不是最优解。** 一个 Agent，多角色，一层知识。（对抗性审查 spawn 子 Agent ≠ 分工。）
3. **知识必须自己淘汰自己。** 达尔文式衰减：90 天不引用 = 退场。能遗忘的系统 > 只能记住的系统。
4. **进化是认知补丁，不是数据积累。** 我们改的规则你能 `git diff`。"思考方式变了" ≠ "知道更多"。
5. **质量收敛，不是改善。** 错误类别单调递减。小心不能规模化。门控可以。
6. **会话是离散的。智能不应该是。** 21 hooks 会话间自动触发。通过使用变好，不是通过更新。
7. **测量不了的，等于没造。** OS Eval + Golden Set + 变更触发。用 git 里的数据证明收敛。

**复利循环本身就是产品。** 你不能只取其中一块就得到同样效果。

> 📖 **完整论点 + CLASS A 案例 + 收敛证据：** [docs/THESIS.md](./docs/THESIS.md)
>
> 📖 **Discussion #39：** [设计哲学 — 六根支柱](https://github.com/xg-gh-25/SwarmAI/discussions/39)

---

## 代码库（~220K 行，不含测试）

| 层 | 行数 | 入口文件 |
|----|------|---------|
| **Core（脊椎）** | ~13K | `session_unit.py`, `prompt_builder.py`, `session_router.py` |
| **Core（扩展）** | ~60K | `core/` — DDD、进化、主动、代码智能 |
| **Backend（其他）** | ~64K | routers、hooks、jobs、channels、main |
| **Skills** | ~28K | `backend/skills/s_*/`（88 模块） |
| **Frontend** | ~54K | `desktop/src/` — React 19, Tailwind, TanStack Query |
| **Rust (Tauri)** | ~2K | `desktop/src-tauri/` |
| **Tests** | ~150K | pytest + Vitest（后端 117K + 前端 33K） |

**技术栈：** Tauri 2.0 (Rust) · React 19 · FastAPI · Claude Agent SDK + Bedrock · SQLite (WAL + FTS5)

---

## 资源

| 内容 | 链接 |
|------|------|
| **讨论** | [阅读矩阵](https://github.com/xg-gh-25/SwarmAI/discussions/35) — Builder 45min · Architect 60min · Leader 30min · [全部](https://github.com/xg-gh-25/SwarmAI/discussions) |
| **AI Agent 避坑指南** | [EN PDF](./docs/ai-agent-pitfall-guide-en.pdf) · [中文 PDF](./docs/ai-agent-pitfall-guide.pdf) |
| **设计文档** | [平台](./docs/DDD-Platform-Overview.md) · [流水线](./docs/Autonomous-Pipeline-Design.md) · [记忆](./docs/Memory-Management-Design.md) · [进化](./docs/Self-Evolution-Harness-Design.md) · [Pollinate](./docs/Pollinate-Content-Engine.md) |
| **贡献** | [CONTRIBUTING.md](./CONTRIBUTING.md) |

---

## 贡献者

**2,550 commits · 1 个人指挥 · 1 个 AI 交付。** 这个 repo 本身就是论点的最小可验证证据 —— 人定方向、做每一个判断决策,AI 负责构建。自己看:`git log`。

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/xg-gh-25">
        <img src="https://github.com/xg-gh-25.png" width="80px;" alt="XG" style="border-radius:50%"/>
        <br /><sub><b>XG</b></sub>
      </a>
      <br /><sub>Creator & Chief Architect</sub>
    </td>
    <td align="center">
      <a href="https://github.com/xg-gh-25/SwarmAI">
        <img src="./assets/swarm-avatar.svg" width="80px;" alt="Swarm" style="border-radius:50%"/>
        <br /><sub><b>Swarm 🐝</b></sub>
      </a>
      <br /><sub>AI Co-Developer (Claude Opus 4)</sub>
    </td>
  </tr>
</table>

[MIT License](./LICENSE)

---

<div align="center">

**SwarmAI — 人来决策。AI 来交付。**

</div>
