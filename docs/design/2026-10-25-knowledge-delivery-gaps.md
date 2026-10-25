---
title: 知识送达的缺口 — REFLECT 没有 DDD 时写到哪，以及 Recall 送不到的地方
created: 2026-10-25
updated: 2026-10-25
project: SwarmAI
status: r1 — 待 XG 看
---

# 知识送达的缺口

## 0. 一句话

pipeline 的 REFLECT 在用户没有 DDD 时，经验只留在 run 自己的记录里，之后没有任何东西读它。
XG 定的方向：写进公共 Library（`Knowledge/` 在 Recall 范围里）。顺着这个方向检查"知识能不能在需要时送到
agent 手上"，一共四处缺口。本文逐个说明现状（核对代码）、该不该修、怎么修。

## 1. 现状（2026-10-25 核对代码）

- **REFLECT 写到哪**（`core/run_protocol/reflect.py: destination`、`reflect_job.route`）：
  - `domain:<project>` 的经验写进这个项目的 DDD 文档（PRODUCT / TECH / IMPROVEMENT / PROJECT 的某一节）；
  - `product` 的经验进项目的 proposal 队列（`self-evolution-proposal`）；`instance` 的经验是 MEMORY
    或 EVOLUTION，也进项目的 proposal 队列，等人审；
  - 项目不存在、或者项目没有对应的 DDD 文档：`kept_in_run`，只留在 `artifacts/reflect.json`，REPORT 列一遍。
- **Recall**：
  - 索引：`knowledge_store.sync_knowledge_index` 给整个 `Knowledge/` 建 FTS5 索引，由会话结束时的
    `context_health_hook` 运行（`hooks/context_health_hook.py:2115`）；
  - 第一次召回：`session_router._maybe_inject_recall`，在会话第一条有关键词的消息时运行，经
    `recall_filter` 过滤相关性，注入 system prompt（warm 复用时放到 query 前面）。同时按当时识别出的
    active project 注入 DDD 里相关的节（`_inject_ddd_for_active_project`）；
  - 换话题时再召回：`_maybe_followup_recall`（`cff25aeb6`，2026-10-22）。新消息的关键词和上次召回的
    重合度低于 `_RECALL_TOPIC_OVERLAP` 时再召回一次，每个会话最多 6 次（`_RECALL_MAX_FOLLOWUPS`），
    只补没注入过的内容。**不重新识别 active project，也不再注入 DDD**：项目在第一次召回时就定了；
  - channel 会话（Slack 等）：第一次召回和换话题召回都跳过（`is_channel_session`）。
- **审查专家**：handout 里有 diff、通用清单，以及仓库 `.swarmai/review.md` 里命中的条目
  （`review.py:63-103`，在 run 的 base 读）；没有 DDD 和 Library 的内容。

更正：我 2026-10-25 对 XG 说过两句不准确的话。一是"Library 只在有人搜时才找得到"，实际上它会自动召回。
二是"召回每个会话只做一次"，实际上换话题时会再召回。真正的缺口比我当时说的窄，见 §2。

## 2. 缺口和结论

| # | 缺口 | 做不做 | 理由 |
|---|---|---|---|
| G1 | 没有 DDD 时 REFLECT 的经验没有读者 | **做**（§3.1） | 经验写了没人读，REFLECT 就白做了。这是 XG 定的方向 |
| G2 | 会话中途换了项目，召回和 DDD 还按第一条消息的项目走 | **pipeline 部分做**（§3.2）；**native 部分不做** | pipeline 的 run 有确定的仓库和项目，可以在 create 时直接点名，不经过猜测。native 会话的 active project 本来就是按消息内容推断的（`detect_active_project`），每轮都重新推断会给发送路径加成本，而且这还是一个推断；目前也没有证据说明这个缺口造成过问题 |
| G3 | 审查专家拿不到 DDD 和 Library 的经验 | **不做，放进 S5 待看项** | 专家要的是独立视角。仓库自己的审查要求已经通过 `review.md` 确定地送到了。往专家 prompt 里加召回结果，会多出不确定的内容和 token，也说不出能多发现什么问题。按 P5 先量后加：S5 有了 finding 数据，看是不是漏了经验里已经写过的问题，再决定 |
| G4 | channel 会话不做召回 | **不做，XG 定是否例外** | 2026-04-14 加入时（`ab06a8321`）的理由是"channel 是简短对话，不需要深度召回"，当时召回预算只有 150 ms。今天这条理由已经不太成立，但还有一条更重的理由：channel 会话可能是 allowlist 里的其他人在跟 SwarmAI 说话，召回会把 MEMORY、过去的会话和 Library 里的私人内容带进给别人的回复。可以考虑的例外：只对 owner 自己的私聊开召回。是否需要，由 XG 定（§5 问题 2） |
| G5 | 新写进 `Knowledge/` 的文件要等某个会话结束才进索引 | **不做** | REFLECT 写的经验在 §3.2 由 create 直接读文件，不经过索引；其余写入场景下，下一次会话结束就会进索引，目前没有证据说明这段延迟造成过问题 |

## 3. 方案

### 3.1 G1：没有 DDD 时，经验写进 Library

- **位置**：`Knowledge/Library/pipeline-lessons/<仓库名>.md`，一个仓库一个文件。
  - 仓库名取主 checkout 目录的名字。文件头记下主 checkout 的绝对路径。如果同名文件记的是别的路径，文件名改成
    `<仓库名>-<路径 sha256 前 8 位>.md`。这样规则是确定的，两个同名仓库不会写进同一个文件。
  - 文件标题里带仓库名，消息里提到这个仓库时更容易被召回命中。
  - 选 Library 而不选 `Learned/`：Library 的定位是"长期参考资料"，`Learned/` 是文章卡片。放子目录是为了不和用户自己的资料混在一起。
- **什么时候写这里**：原来会得到 `kept_in_run` 的经验，即项目不存在或没有对应的 DDD 文档。
  - `domain:<project>` 的经验写进"经验"一节，节名用原来 DDD 的目标节名（如 `TECH:Conventions`），以后建 DDD 时可以直接对上；
  - `product` / `instance` 的经验写进"待审"一节。它们改的是 agent 自己的规则，必须有人看过才生效，Library 里只是记录，
    REPORT 提示用户去看。原来的 proposal 队列在项目存在时照旧使用。
- **一个 run 有多个仓库**：经验要多一个可选字段 `repo`（run 的仓库名之一），有多个仓库时必须填，否则仍是 `kept_in_run`，
  REPORT 照实写原因。只有一个仓库时不用填。
- **写法**：和写 DDD 一样，先过 `judge`（噪音和质量过滤）；`reinforces` / `refines` / `contradicts` 照现有规则，作用于这个
  文件里的条目（`annotate_entry` 改成接受一个文件路径）。每条带 run id 和日期，只追加，不改旧条目。写入经过
  `utils.file_lock`，路径检查沿用 `reflect_job` 现在对 `Projects/` 的做法：目录 fd 逐级走，不跟随符号链接。
- **结果名**：新增 `library`（写进了 Library），`kept_in_run` 只留给多仓库没填 `repo` 和写入失败两种情况。REPORT 的
  "没有写进项目 DDD 的经验"一节改为列出写到的 Library 路径。
- **以后用户为这个仓库建了 DDD**：这个文件就是现成的素材。这次不做自动迁移，REPORT 提示一句。

### 3.2 G2（pipeline 部分）：create 时点名经验文件和 DDD

- `create` 的输出加 `knowledge`：这个 run 的每个仓库如果有 `Knowledge/Library/pipeline-lessons/` 下的经验文件，就列出它的
  路径；项目有 DDD 时列出 DDD 文档路径。只列已经存在的文件，不推断、不召回。
- run 的第一个阶段卡片（EVALUATE；direct 是 BUILD）加一句：开始前先读 `knowledge` 里列出的文件。
- 这样不依赖会话是从哪条消息开始的，也不依赖索引有没有更新。

## 4. 测试（测试先行，变异验证）

- G1：
  - 项目没有 DDD 的 run 落地后，经验出现在 `Knowledge/Library/pipeline-lessons/<仓库>.md` 的对应节，结果是 `library`；
  - 同名不同路径的两个仓库写进两个文件；
  - 多仓库 run 的经验没填 `repo` 时是 `kept_in_run`；
  - `instance` 的经验进"待审"一节；
  - 重复的经验按 `reinforces` 加证据，不另起一条；
  - 符号链接替换目标目录时拒绝写入。
  - 变异：去掉 Library 分支，第一条测试变红。
- G2：
  - 已有经验文件时 `create` 输出里有它，第一个阶段的卡片有那句话；没有时不列；
  - 变异：不列文件，测试变红。
- 跑现有的 reflect / pipeline / run_protocol 测试文件。

## 5. 要 XG 定的

1. §3.1 的位置和命名（`Knowledge/Library/pipeline-lessons/<仓库名>.md`）。
2. G4：channel 会话是否给 owner 自己的私聊开召回。我的建议：先不开，等有具体需要时再做。
3. G3 放进 S5 待看项，同意吗。

## 修订记录

- r1（2026-10-25）：初稿。XG 定了 G1 写进 Library、按仓库归档、create 时点名。
