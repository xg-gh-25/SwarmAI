<div align="center">

# SwarmAI

### 人来决策。AI 来交付。

[English](./README.md) | 中文

[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat)](./LICENSE)
[![Stars](https://img.shields.io/github/stars/xg-gh-25/SwarmAI?style=flat)](https://github.com/xg-gh-25/SwarmAI/stargazers)

</div>

---

**SwarmAI 是一个自进化的 Agent OS** —— 每次交互升级的不是模板，是系统的认知本身。

你的 AI 团队，一个人指挥。

![SwarmAI](./assets/swarm-2.png)

---

## 为什么是 SwarmAI

大多数 AI 工具是**扁平**的：每次会话从零开始，每个错误重犯一遍，所谓"记忆"不过是一份没人从中学习的对话记录。SwarmAI 是**复利**的——它把每一次交互都当成升级系统自身的机会。

它不只是*记住*发生了什么，而是把经验**蒸馏**成持久知识，**淘汰**不再有用的部分，并在错误重复出现时**改写自己的操作规则**——这些改动你能在 `git diff` 里读到。编码流水线不会迭代到"差不多就行"，而是**收敛**到一个明确的完成定义，不通过对抗性门控就拒绝交付。曾经反复出现的错误类别单调递减——因为细心不可规模化，门控才可以。

我们正在公开验证的命题：**一个人 + AI 能否达到一整个团队的产出规模？** 不是靠把模型做得更大，而是靠在它周围构建**操作系统**——把上下文、记忆、进化、测量接进同一条复利闭环。闭环本身就是产品；你无法抽出单个引擎还得到同样的效果。

> **这不是产品 Demo——这是一个边发生边记录的活实验。** 下面是 60+ 篇深度讨论：架构决策、踩过的坑、复盘报告，以及每个引擎背后的设计哲学。

### 📚 从这里开始 —— 代码背后的思考

| | |
|---|---|
| 🗺️ **[阅读矩阵 — 3 条精选路径](https://github.com/xg-gh-25/SwarmAI/discussions/35)** | **Builder**（~45 分钟）· **Architect**（~60 分钟）· **Leader**（~30 分钟）—— 别全读，挑你的路径 |
| 💬 **[全部讨论（68 篇）](https://github.com/xg-gh-25/SwarmAI/discussions)** | 思想领导力、架构深潜、复盘报告 —— 同时镜像在 [`docs/discussions/`](./docs/discussions/) |
| 🧭 **[设计哲学 — 六根支柱](https://github.com/xg-gh-25/SwarmAI/discussions/39)** | 信念如何变成强制 —— 每一条都是从一次失败里挣来的 |

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

13 个互联引擎。每个独立有价值；组合在一起产生复利。

| # | 引擎 | 做什么 | 详情 |
|---|------|--------|------|
| 1 | **上下文管理** | 11 文件 Prompt 架构，100K 预算，三层所有权 | [docs](./docs/DDD-Platform-Overview.md) |
| 2 | **记忆流水线** | 4 层持久化：DailyActivity → 蒸馏 → 复利召回 | [docs](./docs/Memory-Management-Design.md) |
| 3 | **DDD 知识引擎** | 自生长领域知识，7 类本体，达尔文式淘汰 | [docs](./docs/DDD-Cultivation-Engine-HLD.md) |
| 4 | **自主流水线** | 一句需求 → 可推送代码。双模式：Full + Goal Loop | [docs](./docs/Autonomous-Pipeline-Design.md) |
| 5 | **Pollinate 引擎** | 一条消息 → 多格式品牌内容 | [docs](./docs/Pollinate-Content-Engine.md) |
| 6 | **自进化** | 认知 L0→L3 补丁。37 条纠正，零类别重复 | [docs](./docs/Self-Evolution-Harness-Design.md) |
| 7 | **自愈合** | 不可见恢复：5 传感器，自动重生，用户无感知 | — |
| 8 | **多标签页 + MessageStore** | 并发会话，阶段门控单写者，跨标签页隔离 | — |
| 9 | **Hook 系统** | 21 hooks（17 运行时 + 4 生命周期）。会话永不冷启动 | — |
| 10 | **任务系统** | 后台智能：13 信号源，定时任务，预算门控 | — |
| 11 | **4 平台后端** | macOS daemon · Hive (EC2) · Windows · Linux。编译时隔离 | — |
| 12 | **技能 + 通道** | 86 技能（lazy/always），Slack 网关，三层权限 | — |
| 13 | **Eval（本体感觉）** | 解耦、系统级：Golden Set + git 绑定回归门控。证明收敛，而非凭感觉 | [docs](./docs/OS-Eval-Function-Design.md) |

**复利闭环：** 记忆 → Pipeline 判断 → DDD → 进化 → 门控 → 记忆。去掉一个，其余变弱。

<img src="./assets/pipeline-architecture.svg" alt="流水线 — 双模式 (Full + Goal)" width="100%"/>

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

## 代码库（~170K 行）

| 层 | 行数 | 入口文件 |
|----|------|---------|
| **Core（脊椎）** | ~10K | `session_unit.py`, `prompt_builder.py` |
| **Core（扩展）** | ~41K | `core/` — DDD、进化、主动、代码智能 |
| **Skills** | ~50K | `backend/skills/s_*/`（86 模块） |
| **Frontend** | ~68K | `desktop/src/` — React 19, Tailwind, TanStack Query |
| **Tests** | ~76K | pytest + Vitest |

**技术栈：** Tauri 2.0 (Rust) · React 19 · FastAPI · Claude Agent SDK + Bedrock · SQLite (WAL + FTS5)

---

## 资源

| 内容 | 链接 |
|------|------|
| **讨论（68 篇）** | [阅读矩阵](https://github.com/xg-gh-25/SwarmAI/discussions/35) — Builder 45min · Architect 60min · Leader 30min · [全部](https://github.com/xg-gh-25/SwarmAI/discussions) |
| **AI Agent 避坑指南** | [EN PDF](./docs/ai-agent-pitfall-guide-en.pdf) · [中文 PDF](./docs/ai-agent-pitfall-guide.pdf) |
| **设计文档** | [平台](./docs/DDD-Platform-Overview.md) · [流水线](./docs/Autonomous-Pipeline-Design.md) · [记忆](./docs/Memory-Management-Design.md) · [进化](./docs/Self-Evolution-Harness-Design.md) · [Pollinate](./docs/Pollinate-Content-Engine.md) |
| **贡献** | [CONTRIBUTING.md](./CONTRIBUTING.md) |

---

## 贡献者

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
