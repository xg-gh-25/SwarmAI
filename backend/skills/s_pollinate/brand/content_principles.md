# External Content Design Principles

> The writing principles below are the default for everyone. The user's own positioning
> (tagline, belief, thesis anchors, ability anchors) comes from the `positioning` block of
> their brand file (`scripts/brand_config.py`; `~/.swarm-ai/pollinate-brand.yaml`).

## When These Apply

These principles govern ALL content produced by Pollinate that is **externally facing** — social media posts, posters, video scripts, README "why" sections, demo pitches, brand materials.

**Does NOT apply to:** internal docs, agent self-use artifacts, technical reports meant only for the user.

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

Exception: 概念足够短且自解释时可保留（例如一个读者已熟悉的缩写或产品名）

### P5: 英文只在比中文更有力时用

- Test: 翻成中文会失去什么？Nothing → 用中文。
- 保留: 品牌 tagline、业内通用缩写（`API`、`ROI`）、没有好译法的专有名词
- 不保留: 只是听起来高级的英文短语，例如 `Compound interest on engineering knowledge`（中文"越用越懂"更好）

### P6: 每条独立能打

不依赖系列上下文。碎片阅读环境，单条必须自足。

- Test: 发给一个没看过任何前置内容的人，能打动吗？

### P7: Briefing 说价值不列术语

产品介绍用人话说价值，术语最多作为注脚。

- ❌ "· Domain Expertise as Infrastructure (DDD)"
- ✅ "领域知识从工作中自己生长，不靠人维护"

### P8: 在用户的定位框架里工作（配置了才适用）

用户的品牌文件里有 `positioning` 时，所有内容都在它定义的层次里：

```yaml
positioning:
  tagline: "..."         # 一句话
  belief: "..."          # 信念
  proof: "..."           # 证明点
  theses:                # 价值锚：每条内容至少锚定一条
    - {id: T1, thesis: "...", expression: "..."}
  abilities:             # 能力锚：产品/作者的能力，原样引用
    - "..."
```

内容 = Thesis × Ability，经过 P1-P7 filter 输出；不在框架里 = 跑偏。没有配置
`positioning` 时，从选题本身提炼一条核心论点（Thesis），用它代替价值锚。

---

## Stage Integration

| Pollinate Stage | How to use this file |
|-----------------|---------------------|
| EVALUATE | Check: is the topic anchored to a thesis (one of `positioning.theses`, or the topic's own core claim)? If not → reframe or reject |
| MESSAGE | Apply P1-P5 to distill the core message |
| PRODUCE | Apply P6 (each piece self-sufficient) + P4 (效果 > 机制) |
| QUALITY | Gate check: scan for anti-patterns below |

## Branding

**Every visual output (poster, card, thumbnail) carries the user's configured signature**
(`scripts/brand_config.py --signature`): its link as visible text, its QR image
(120×120px, the variant matching the direction's background), its watermark at the
bottom. Unset elements are left out. See `poster_design_system.md` § Default Branding.

L7 in the 8-Layer Gate checks every configured element. A configured element missing =
gate FAIL = poster never reaches the user. No "poster is too short", no "QR doesn't fit
the design."

## Anti-Pattern Checklist (Quality Gate)

Before delivering any external content, verify NONE of these appear:

- [ ] LOC / commit count / "XX天" used as value claim (P1)
- [ ] First-person hero framing — "我造了", "我的AI" (P2)
- [ ] Internal architecture terms in body text — 8 Feed Channels, Cultivation Engine, etc. (P4)
- [ ] English phrase embedded in Chinese paragraph that could be said in Chinese (P5)
- [ ] Feature pitch without thesis backing (P3)
- [ ] Piece requires reading other pieces to make sense (P6)
- [ ] Briefing/intro lists internal module names instead of user value (P7)
- [ ] **A configured signature element (watermark / QR / link) missing from a visual output (BLOCKING)**

Any checkbox triggered → fix before delivery.
