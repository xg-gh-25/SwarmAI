# External Content Design Principles

> Source of truth: THIS DDD's ② `PRODUCT.md` (its Brand / External Communication section)
> + `brand/identity.yaml`. This file is the DDD-native content-principles reference.
> Fill in your own brand; nothing here is SwarmAI-specific.

## When These Apply

These principles govern ALL content produced by Pollinate that is **externally facing** — social media posts, posters, video scripts, README "why" sections, demo pitches, brand materials.

**Does NOT apply to:** internal docs (DDD, TECH.md, DailyActivity), agent self-use artifacts, technical reports for XG only.

## The 8 Principles

### P1: Value > Output

代码量/commit数/天数是 output 不是 value。Thesis 比 metrics 有力。

- Test: 去掉数字这句话还成立吗？是 → 不需要数字。
- ❌ "60天写了 190K 行代码"
- ✅ "越用越懂你，不是每次从零开始"

### P2: What's true > What I did

弱化第一人称。观点输出者，不是成就展示者。让读者自己推导"这人在做这件事"。

- Test: 主语是"我"还是"事物本身"？
- ❌ "我造了一个会记忆的系统"
- ✅ "一个没有记忆的 AI，每次都在猜你是谁"

### P3: Thesis 驱动

从 worldview 出发，不从 feature 出发。先输出 belief，能力是自然延伸。

- Structure: Thesis → Observation → Implication → Tagline
- ❌ "我们支持 8 Feed Channels 自动更新知识"
- ✅ "AI 的根本瓶颈不是智力，是基础设施"

### P4: 效果 > 机制

说"对你意味着什么"，不说"内部叫什么"。

- ❌ "Quality Convergence Loop"
- ✅ "犯过的错永远不再犯"

Exception: 概念足够短且自解释时可保留（`DDD`, `Coding as Black Box`）

### P5: 英文只在比中文更有力时用

- Test: 翻成中文会失去什么？Nothing → 用中文。
- 保留: `Human directs. AI delivers.` / `DDD` / `Coding as Black Box`
- 不保留: `Compound interest on engineering knowledge`（中文"越用越懂"更好）

### P6: 每条独立能打

不依赖系列上下文。碎片阅读环境，单条必须自足。

- Test: 发给一个没看过任何前置内容的人，能打动吗？

### P7: Briefing 说价值不列术语

产品介绍用人话说价值，术语最多作为注脚。

- ❌ "· Domain Expertise as Infrastructure (DDD)"
- ✅ "领域知识从工作中自己生长，不靠人维护"

### P8: 严格遵循定位层次

所有内容必须在这个层次里工作：

```
Tagline:  Human directs. AI delivers.
Belief:   探索 AI 的边界
Proof:    一个人 + AI 能顶一个团队
Theses:   T1-T6 (价值锚)
Ability:  DDD / Pipeline / Memory / Evolution / Quality (能力锚)
```

内容 = Thesis × Ability，经过 P1-P7 filter 输出。不在框架里 = 跑偏。

---

## Thesis Anchors (每条内容至少锚定一个)

| ID | Thesis | 核心表达 |
|----|--------|---------|
| T1 | Memory is the moat | 记忆是连续性，不是存储量 |
| T2 | Paradigm shift is structural | 认知底座在变，不是渠道 |
| T3 | Understanding > Execution | 判断力不可外包，用进废退 |
| T4 | AI-Native org design | 协作成本 = 信息不对称的代价 |
| T5 | Culture as code | 工程文化编码进系统 = 品味 |
| T6 | Tooling commoditizes | 知识是资产，工具是手段 |

## Ability Anchors (产品能力，英文原样)

- DDD — Domain expertise as infrastructure
- Coding as Black Box — One sentence → push-ready
- Memory Compounds — 越用越懂，不是每次从零
- Self-Evolution — 犯过的错在结构上不可能再犯
- Quality Convergence — 每轮输出比上轮更接近正确
- 5 Black Boxes — Coding · Content · Knowledge · Quality · Evolution

---

## Stage Integration

| Pollinate Stage | How to use this file |
|-----------------|---------------------|
| EVALUATE | Check: is the topic anchored to a thesis? If not → reframe or reject |
| MESSAGE | Apply P1-P5 to distill the core message |
| PRODUCE | Apply P6 (each piece self-sufficient) + P4 (效果 > 机制) |
| QUALITY | Gate check: scan for anti-patterns below |

## Branding (DDD-configurable — opt-in, never SwarmAI-locked)

Branding is sourced from THIS DDD's `brand/identity.yaml` + env, and is **opt-in**:

1. **Link:** your project's link (`{{PROJECT_LINK}}` in identity.yaml) — include only if set.
2. **QR code:** optional; supply your own asset + set `POLLINATE_REQUIRE_QR=1` to enforce.
3. **Watermark:** optional; set `POLLINATE_WATERMARK="<text>"` to require it.

L7 (convergence_gate) + the validator brand checks enforce ONLY what you configure — with
nothing set, an unbranded poster PASSES. Never inject `xg-gh-25/SwarmAI` (that is SwarmAI's
brand, not this DDD's). A DDD that wants no branding ships clean.

## Anti-Pattern Checklist (Quality Gate)

Before delivering any external content, verify NONE of these appear:

- [ ] LOC / commit count / "XX天" used as value claim (P1)
- [ ] First-person hero framing — "我造了", "我的AI" (P2)
- [ ] Internal architecture terms in body text — 8 Feed Channels, Cultivation Engine, etc. (P4)
- [ ] English phrase embedded in Chinese paragraph that could be said in Chinese (P5)
- [ ] Feature pitch without thesis backing (P3)
- [ ] Piece requires reading other pieces to make sense (P6)
- [ ] Briefing/intro lists internal module names instead of user value (P7)
- [ ] **Missing GitHub QR code or link in visual output (BLOCKING)**

Any checkbox triggered → fix before delivery.
