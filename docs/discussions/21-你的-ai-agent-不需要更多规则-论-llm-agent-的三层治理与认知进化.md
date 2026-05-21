# 你的 AI Agent 不需要更多规则 — 论 LLM Agent 的三层治理与认知进化

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/21) | Category: General | Published: 2026-05-19

---

## TL;DR

规则堆砌是 AI Agent 自我进化的死胡同。我们在 SwarmAI 中观察到：27 个行为修正（corrections），同一类认知偏差重复 4 次才被控制住，AGENT.md 持续膨胀但输出质量没有对应提升。问题不是"规则不够多"，而是"规则再多也治不了底层判断力缺陷"。

本文提出：LLM Agent 的行为治理应该借鉴人类社会的三层结构 —— 原则（道德）、规则（法律）、门禁（执法）—— 并且进化的方向是**蒸馏**（distillation）而非积累（accumulation）。

---

## 问题：规则为什么会失败

### 观察到的现象

我们运行 SwarmAI（一个基于 Claude Agent SDK 的桌面 AI 助手）超过 2 个月，记录了完整的行为修正历史。一个典型的 failure pattern：

```
C011: Agent 跳过 adversarial review，声称"测试都过了，confidence 10/10"
      → 加规则："必须做 adversarial review"
C021: Agent 跳过 adversarial review，声称"时间紧"
      → 加更严格的规则："adversarial review 是 non-negotiable gate"
C025: Agent 跳过整个 pipeline，声称"我熟悉这段代码"
      → 加规则："pipeline 是所有 coding task 的 default"
C026: Agent 又跳过 adversarial review，validator 有 bypass 路径
      → 加 mechanical gate：代码级别阻断
C027: Agent 交付时接受 80% 完成度，不主动修复已知问题
      → ???
```

**4 次同一类错误。** 每次加一条规则。规则没用，加更严的规则。规则还没用，加代码门禁。门禁只能堵这一个具体行为 —— 然后同样的底层偏差在另一个形态下冒出来（C027）。

这不是个例。这是 LLM Agent 行为治理的**结构性困境**。

### 根因分析

这些表面不同的 correction 背后是**同一个认知偏差**：

> "在'觉得差不多了'的时刻停下来，而不是在'确认完成了'的时刻停下来。"

规则只能枚举症状（"不能跳 review"、"不能跳 pipeline"、"不能接受 80%"），不能治疗病因。而症状是无限的 —— 任何"看起来做完了"的决策点都可能触发这个偏差。

**堆砌规则 = 给一个反复感冒的人开更多品牌的感冒药。有效的做法是修复免疫系统。**

---

## 借鉴：人类社会怎么做行为治理

人类社会不依赖单一机制。三层共存，各司其职：

| 层级 | 人类社会 | 对谁有效 | 失败模式 |
|------|---------|---------|---------|
| **道德/信仰** | 内在约束 | 高度内化的人 | 大多数人选择性执行 |
| **法律/规则** | 外部明文 | 能理解规则的人 | 有边界，新情况无覆盖 |
| **执法/惩罚** | 物理强制 | 所有人 | 成本高，只能兜底 |

关键洞察：**三层缺一不可。**

- 只有道德 → 乌托邦幻想（对人性期望过高）
- 只有法律 → 僵化无灵活（新情况总是比法律多）
- 只有执法 → 警察国家（高成本，低弹性）

最稳定的社会 = 道德覆盖 90% 的日常决策，法律处理 9% 的边界情况，执法兜底 1% 的 proven bad actors。

---

## 设计：LLM Agent 的三层治理模型

### Layer 1: Principles（原则）— 定位，不是 enforcement

**数量：** 3-5 条。不多。

**作用：** 当遇到新情况（没有规则覆盖、没有门禁阻断）时，提供判断方向。

**属性：**
- 每条原则覆盖一整**类**失败，不是一个具体实例
- 不期望 100% compliance —— probabilistic effectiveness ~70-80%
- 放在 system prompt 最高 attention 位置

**示例（覆盖 C011-C027 全部）：**

> "完成 = 我主动尝试破坏它且失败了。不是'我没发现明显问题'。"

一条原则，如果真正被遵循，C011 到 C027 的所有 correction 都不会发生。

### Layer 2: Rules（规则）— 有限、可追溯、可过期

**属性：**
- 每条规则必须 link 到一个父原则（无法追溯的规则 = 候选删除）
- 有证据计数（哪些 correction 催生了它）
- 有毕业条件：当 gate 已经机械性解决了这个 failure mode，rule 可以 retire
- 总量有上限 —— 加一条规则需要解释为什么原则本身不够

**生命周期：**

```
新失败 → 原则能覆盖吗？
  YES → 精炼原则措辞（不加新规则）
  NO  → 加规则（link to principle，带证据）
        → 规则 3x 失效 → 升级为 gate
        → gate 部署 → 规则退休
```

### Layer 3: Gates（门禁）— 最少、机械化、经过验证

**属性：**
- 只在同一 pattern 3+ 次失败后才加
- 必须是代码可检测的（不是文本可建议的）
- 数量最少化 —— 每个 gate 都有成本（rigidity, false positives, 维护）
- Gate 是保险，不是主要引导

### 三层交互

```
                    Novel situation
                          │
                          ▼
                ┌─────────────────┐
                │   PRINCIPLES    │  (~70-80% effective)
                │    (方向)       │
                └────────┬────────┘
                         │ fails
                         ▼
                ┌─────────────────┐
                │     RULES       │  (~85-90% | principle failed)
                │    (指导)       │
                └────────┬────────┘
                         │ fails
                         ▼
                ┌─────────────────┐
                │     GATES       │  (~99% | rule failed)
                │    (强制)       │
                └─────────────────┘

Combined P(correct behavior) ≈ 99.5%+
Novel failures (no rule/gate yet): ~70-80% first-time correctness
```

---

## 进化 = 蒸馏，不是积累

### 为什么积累会失败

Anthropic 的 [Claude Code 官方 Best Practices](https://docs.anthropic.com/en/docs/claude-code/best-practices) 明确指出：

> "Bloated CLAUDE.md files cause Claude to **ignore** your actual instructions!"
> "Ruthlessly prune. If Claude already does something correctly without the instruction, **delete it**."
> — Anthropic Engineering, 2025

Princeton 的 [Reflexion](https://arxiv.org/abs/2303.11366)（NeurIPS 2023）把 reflections **cap 在 3 条** —— 更多反而 degrade performance。

IBM/CMU 的 [SELF-ALIGN](https://arxiv.org/abs/2305.03047)（NeurIPS 2023 Spotlight）用 **16 条 principles**（~300 行）就达到了 competitive with Text-Davinci-003 —— 无需 RL，无需大量标注。

信号一致：**更少的、更精确的指导 > 更多的、更具体的规则。**

### 真正的进化方向

| 信号 | 含义 |
|------|------|
| 指令文件变短 + 输出质量提升 | 原则真正在 generalize |
| Gate 触发次数 → 0 | 上游层已经足够，gate 变成 insurance |
| 全新类型错误，第一次就处理正确 | OS 真正升级（泛化能力） |
| 同一类错误在原则修改后不再出现 | 内化起作用了 |

反向信号：

| 反向信号 | 含义 |
|---------|------|
| 指令文件持续增长 | 仍在打补丁 |
| 每个新 failure 都需要新 gate | 原则没有泛化 |
| 同样的偏差以新形式出现 | 治症状，没治根因 |

### 进化操作

| 操作 | 时机 | 效果 |
|------|------|------|
| Principle refinement | 新 failure 在已有 principle 覆盖范围内，但 principle 不够精确 | 磨尖措辞 |
| Rule retirement | Gate 已经机械性覆盖了这个 failure mode | 减少噪音 |
| Rule → Principle absorption | 3+ 条 rules 同源 | 合并为一条更清晰的 principle |
| Gate graduation | Gate 30+ 天未触发 | principle/rule 层已足够 |
| Principle compression | 两条 principles 重叠 | 合并 |

---

## 为什么 Gates 不可去掉：关键 negative result

Google DeepMind 的研究（[Huang et al., ICLR 2024](https://arxiv.org/abs/2310.01798)）证明：

> **LLMs cannot reliably self-correct reasoning without external feedback.** Intrinsic self-correction can DEGRADE performance.

这意味着：纯靠 "自我反思" 的 agent 自进化是幻想。**外部验证信号（mechanical gates）是 structural requirement，不是 nice-to-have。**

Principles 设定方向。Rules 细化指导。但只有 Gates 提供了那个 "external feedback signal" 让系统知道自己真的错了，而不是"自我感觉良好"。

三层模型不是"理想状态去掉 gates"。三层都是永久必要的。进化的方向是让每层在自己的职责范围内越来越精确 —— 不是消除某一层。

---

## 与 LLM 本质的关系

### Probabilistic compliance

LLM 的 "道德水平" 不是恒定的。同一个 model，不同条件下：

- Context window 前 20% → attention 最强，principle 最有效
- Task 复杂度高 → "快点完成" 的 reward signal 压过 "仔细检查"
- Session 后半段 → competing context 多，principle effectiveness 下降

**类比：** 人类睡眠不足时自控力下降。不是变了一个人，是 cognitive resource 不够了。

**设计含义：** 系统必须 account for 这个 variance。不假设 principles 总是 work。Rules 是 probability booster，Gates 是 absolute floor。

### Stateless paradox

LLM 每个 session 是 fresh start。Weights 不变。唯一的 "进化载体" = 改 system prompt 文件。

这意味着 "OS 升级" 的物理形态只能是：
1. **精炼** — 从 50 条 rules 压缩成 3-5 条 principles + 必要的 rules
2. **定位** — 确保这些 principle 在 attention 最强的位置
3. **gates as memory** — 代码级 gate = "免疫系统的记忆"，遇到已知抗原不需要再思考

---

## Related Work

| Work | Key Contribution | Relation to Our Model | Source |
|------|-----------------|----------------------|--------|
| Constitutional AI (Anthropic, 2022) | Compact principle set governs all behavioral refinement via self-critique | Validates "fewer principles > more rules" at training level | [arXiv:2212.08073](https://arxiv.org/abs/2212.08073) |
| SELF-ALIGN / Dromedary (IBM/CMU, NeurIPS 2023 Spotlight) | 16 principles achieve competitive alignment with zero RL | **Strongest validation** — distillation works | [arXiv:2305.03047](https://arxiv.org/abs/2305.03047), [GitHub](https://github.com/IBM/Dromedary) |
| Claude's Character (Anthropic, 2024) | "Broad traits, not narrow rules" for character training | Production deployment of principle-over-rule philosophy | [anthropic.com/research/claude-character](https://www.anthropic.com/research/claude-character) |
| OpenAI Model Spec (2024) | Objectives > Rules > Defaults three-tier hierarchy | Near-identical architecture to our proposal | [cdn.openai.com/spec/model-spec-2024-05-08.html](https://cdn.openai.com/spec/model-spec-2024-05-08.html) |
| Collective Constitutional AI (Anthropic, 2023) | Public participatory principle generation; consensus-filtering as distillation | Governance of principles themselves | [anthropic.com/research/collective-constitutional-ai-aligning-a-language-model-with-public-input](https://www.anthropic.com/research/collective-constitutional-ai-aligning-a-language-model-with-public-input) |
| Promptbreeder (DeepMind, 2023) | Self-referential improvement — mutation operators evolve too | Meta-evolution: evolve the evolutionary mechanism itself | [arXiv:2309.16797](https://arxiv.org/abs/2309.16797) |
| "Large Language Models Cannot Self-Correct" (DeepMind, ICLR 2024) | Self-correction without external feedback degrades performance | **Why Gates are mandatory** — not optional | [arXiv:2310.01798](https://arxiv.org/abs/2310.01798) |
| Reflexion (Princeton/Northeastern, NeurIPS 2023) | Cap reflections at 3 — more is worse | Direct evidence for bounded rules | [arXiv:2303.11366](https://arxiv.org/abs/2303.11366) |
| Self-Discover (DeepMind, 2024) | Compositional principles > accumulated rules | 10-40x cheaper, +32% over CoT | [arXiv:2402.03620](https://arxiv.org/abs/2402.03620) |
| DSPy (Stanford NLP, 2023) | Compilation replaces manual prompt engineering | Engineering realization of anti-accumulation | [arXiv:2310.03714](https://arxiv.org/abs/2310.03714), [GitHub](https://github.com/stanfordnlp/dspy) |
| ExpeL (Tsinghua, AAAI 2024) | Experience → extracted insights → recall at inference | Distillation mechanism for runtime agents | [arXiv:2308.10144](https://arxiv.org/abs/2308.10144) |
| ADAS (UBC/Vector Institute, 2024) | Meta-agent programs better agents, archive curated | Evolutionary selection prevents pure accumulation | [arXiv:2408.08435](https://arxiv.org/abs/2408.08435) |
| Agent-R (2025) | Self-correction capability itself improves iteratively | Meta-capability evolution | [arXiv:2501.11425](https://arxiv.org/abs/2501.11425) |
| Symbolic Learning for Self-Evolving Agents (AIWaves, 2024) | Agent config as "learnable parameters" with symbolic gradients | Formal framework for what we do intuitively | [arXiv:2406.18532](https://arxiv.org/abs/2406.18532), [GitHub](https://github.com/aiwaves-cn/agents) |
| SPIN: Self-Play Fine-Tuning (UCLA, ICML 2024) | Self-play converges when model matches target distribution | Proves distillation has a mathematical endpoint | [arXiv:2401.01335](https://arxiv.org/abs/2401.01335) |
| OPRO: LLMs as Optimizers (DeepMind, ICLR 2024) | LLMs optimize prompts via scored history | Counterexample — accumulative by design, hits context limits | [arXiv:2309.03409](https://arxiv.org/abs/2309.03409) |
| Claude Code Best Practices (Anthropic, 2025) | "Bloated CLAUDE.md causes Claude to ignore instructions — ruthlessly prune" | Practitioner validation of anti-accumulation | [code.claude.com/docs/en/best-practices](https://docs.anthropic.com/en/docs/claude-code/best-practices) |
| Voyager (NVIDIA/Caltech, 2023) | Composable skill library + self-verification in open-ended learning | Compositional rules + gate pattern | [arXiv:2305.16291](https://arxiv.org/abs/2305.16291) |

---

## 我们的贡献（现有文献的 gap）

1. **Runtime bidirectional loop** — 现有工作在 training time（CAI, SELF-ALIGN）或 one-shot compilation（DSPy）。我们在 runtime 持续运行中做 principle ↔ rule ↔ gate 的生命周期管理。

2. **Rule expiry mechanism** — 所有现有系统要么只增（ExpeL, Reflexion 的 capped buffer）要么全换（DSPy compilation）。Rule traceable to principle + graduation condition 是新的。

3. **Reverse distillation** — SELF-ALIGN 做 forward（principles → behavior）。我们提议 bidirectional：observed behavioral rules 压缩回 principles。

4. **Failure mode migration** — C011→C027 展示的"同一偏差换皮重复"问题，文献中被承认但未解决。

---

## Discussion Questions

1. **Principle count：** 覆盖所有 failure classes 的最小 principle 集合是什么？是否存在 universal set？
2. **Verification：** 怎么验证一条 principle "真的内化了" vs "只是在 prompt 里存在"？（我们的 test：新类型错误第一次就对）
3. **Fourth layer：** 是否存在我们没看到的第四层？（e.g., model selection, temperature, structural prompt design）
4. **Cross-agent transfer：** 从一个 agent 蒸馏出的 principles 能否迁移到另一个 agent？
5. **Convergence：** [SPIN](https://arxiv.org/abs/2401.01335) 证明了 self-play 有数学收敛点。Principle distillation 是否也有"进化终点"？

---

*本文基于 SwarmAI 项目 2+ 月的实际运行数据和 27 个行为修正记录。SwarmAI 是一个基于 Claude Agent SDK 的桌面 AI 命令中心，使用 AIDLC（AI-Driven Development Lifecycle）框架进行自治开发。*

*我们正在实验这个三层治理模型。如果你在做类似的工作 — 欢迎讨论。*

