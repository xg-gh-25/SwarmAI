---
title: 模型的结构化输出 — 一个入口，格式由 API 保证
created: 2026-10-28
updated: 2026-10-28
project: SwarmAI
status: XG 已同意（2026-10-28），直接改，不走 pipeline
related:
  - docs/design/2026-10-27-new-brain-initial-ddd.md   # r10，暴露问题的离线测试
---

# 模型的结构化输出：一个入口，格式由 API 保证

- 起因：New Brain 离线测试 `OFF_BRAIN_FIRST_ENTRIES_LIB` 第一次真跑，判官回复无法解析，case 报 error。

## 1. 问题

后端有 13 处让模型返回结构化结果（判决或提取内容），全部是 **在 prompt 里要求"只回 JSON"，再各自解析文本**。格式只靠 prompt 约束，API 不保证。

| 调用点 | 用途 | 输出上限 | 解析方式 | 解析失败时 |
|---|---|---|---|---|
| `eval_runner._judge_decision_direction` | 离线测试判官 | 400 | 剥 ``` 后 `json.loads` | error |
| `eval_runner.eval_llm_judge` | rubric 判官（用户自己的 GS_* case） | 1000 | 同上 | error（6 月前是 skipped，不计入分母） |
| `session_scorer.default_judge_fn` | 在线评分 | 500 | 直接 loads，失败再贪婪匹配 `\{.*\}` | error |
| `ingestion_gate._self_adversarial_judge_impl` | 四个认知存储的入库判官 | 400 | 正则找 `VERDICT:` 行 | suspect |
| `skill_fitness.LLMJudge` | skill 评分 | 200 | `json.loads` | — |
| `summarization._call_enrichment_llm` | 会话摘要 enrichment | 2048 | 找首尾 `{}`；查 `stop_reason` | 丢掉 enrichment |
| `memory_extractor` | 手动"存入记忆" | 1024 | 剥围栏、找首尾 `{}` | 返回空 |
| `conversation_extract` | 会话候选知识 | 1536 | 剥围栏 | 0 个候选 |
| `ddd_refresh` / `memory_health` / `skill_proposer` | 定时 job | 4096 / 2048 / 8192 | `json.loads` | 各自处理 |
| `signal_digest`（scores） | 信号打分 | 1500 | `json.loads` | — |
| `llm_optimizer` | 文本优化 | 2000 | `raw_decode` 第一个对象 | — |

一共 10 种不同的解析写法，只有 summarization 一处检查了截断。原始回复都不保存，事后无从知道模型到底回了什么。

另有要纯文本的调用，问题同源：截断后的半截文本被当成完整结果。
- `ingestion_gate._distill_entry`：把入库条目改写成规则，上限 400，不检查截断，半截改写会直接写进 MEMORY；
- `signal_digest`：markdown 摘要，两处调用。
- 共用的 `jobs.bedrock.invoke` 本身也不看 `stop_reason`。

## 2. 实测

- **离线测试判官**（今天）：同一请求复现 2 次，两次都是 `stopReason=max_tokens`。判官先写分析，写满 400 token 时还没开始写 JSON；从已写出的分析看，它的结论是对的（应判 FAIL）。
- **rubric 判官**（EvalHistory，189 次运行）：约 104 次 "Judge response not valid JSON"，其中 9 月 66 次、10 月 27 次，至今仍在发生。
  - "Expecting value: line 1 column 1"：回复开头不是 JSON（先写了文字）；
  - "Extra data"：JSON 后面还跟着内容。
- **入库判官**：2026-08-11 发现 68% 的回复无法解析。这些条目被打成 suspect，等于悄悄丢弃了知识。当时的修法是只改它自己的 prompt，要求"先写 VERDICT 行"。之后 telemetry 里又记了 38 次 `unparseable_or_empty`。
- **summarization enrichment**：日志覆盖的约 10 天里失败 13 次，都是 "Unterminated string"，出错位置在第 26 到 1919 个字符之间，远没到 2048 的上限。不是截断，是模型在 JSON 字符串里写了没转义的字符。
- **同一类问题已经在三个地方各修过一次**：入库判官改 VERDICT 先行；session_scorer 加了贪婪正则；llm_optimizer 改用 raw_decode。每次只修出事的那一处。

## 3. 根因

1. **格式只靠 prompt 约束。** 模型可以先写分析、加围栏、在字符串里写未转义的引号或换行，也可以在 JSON 后面继续写。
2. **截断和格式错误分不出来。** 输出被上限截断时，返回的同样是一段解析不了的文本。
3. **上限按"答案很短"定**，与 rubric 和答案的实际长度无关。prompt 越长，模型越倾向先分析，截断就越容易发生。

## 4. 方案比较

| | 做法 | 能防住 | 防不住 | 代价 |
|---|---|---|---|---|
| A | 各处继续打补丁（调大上限、加容错解析） | 当前出事的那一处 | 下一处；前缀文字、未转义字符 | 每次都小，但永远修不完 |
| B | 统一一个容错文本解析器，加截断检查 | 前缀文字、尾随内容、截断 | 字符串里未转义的字符；格式仍靠 prompt | 中 |
| **C（推荐）** | **统一一个入口，用 Bedrock Converse 的强制 tool 调用加 JSON schema** | 格式由 API 保证；截断由 `stopReason` 明确给出；schema 校验 | 模型判断本身的对错（那是 eval 的事） | 中：13 处调用要迁移 |

C 的可行性已在 Bedrock 上实测，用的就是今天截断的那个请求和判官模型 sonnet-4-6：
- 上限 2000：返回 `stopReason=tool_use`，分析写在 `reasoning` 字段里，`verdict=failed`，notes 写着"4/4、0、0、违反 (c)"，共 755 token。
- 上限 400：返回 `stopReason=max_tokens`，tool 的输入为空。截断变成了能识别的信号。

## 5. 设计

### 5.1 一个入口

在 `jobs/bedrock.py` 里加 `structured(...)`，它是后端所有"要结构化结果"的模型调用唯一的入口：

```python
structured(prompt, *, schema: dict, system: str, model_id: str, max_tokens: int,
           temperature: float = 0.0, read_timeout: int | None = None,
           max_attempts: int | None = None, name: str = "answer") -> dict
```

- 用 Converse 发请求，带 `toolConfig`：只有一个 tool，`inputSchema` 就是传入的 schema，`toolChoice` 强制调用它。
- 返回 tool 的输入（dict），先按 schema 校验，用的是已安装的 `jsonschema`。
- 失败时抛 `StructuredOutputError(kind, raw)`，`kind` 只有三种：
  - `truncated`：`stopReason == "max_tokens"`；
  - `missing`：回复里没有调用这个 tool；
  - `invalid`：不符合 schema。

  `raw` 是回复原文的前 2000 个字符，写进调用方的 notes 或日志，事后能查到模型到底回了什么。
- 凭证失效后重试、单独设读超时的客户端，沿用 `converse_with_retry` 现有的做法，不另写。
- 纯文本调用用同一个文件里的 `text(...)`，即现有的 `invoke` 加上截断检查：`stopReason` 是 `max_tokens` 时抛 `StructuredOutputError("truncated", raw)`，不再把半截文本当完整结果返回。
- **不做**截断后自动加大上限重试。截断说明上限定错了，应该让它可见，而不是偷偷多花一次调用把它盖过去（SOUL P4）。

### 5.2 Schema 的写法

- 判决类：`reasoning` 字段放在第一位，然后是 `verdict`、`confidence`、`notes`。让模型先在字段里写分析，结论写在后面。这样分析是结构的一部分，不会挤掉答案。
- 枚举值（`passed`/`failed`、`pass`/`suspect`/`noise`）用 `enum`，数值用 `number`；返回后统一转换类型（今天实测 confidence 返回的是字符串 `"0.95"`）。
- 提取类：沿用各处现有的字段结构，只是从 prompt 文字搬进 schema。prompt 里关于"输出格式"的段落随之删掉。

### 5.3 上限

每处按"最长的正常输出加分析"重新定。判决类统一 2000；提取类保留现有数值，其中已经偏小的（memory_extractor 1024）按实测调整。截断一律报 `truncated`，不会被当成格式错误。

### 5.4 失败语义（每扇门不变，只是原因变清楚）

| 门 | 失败时 | 变化 |
|---|---|---|
| eval 两个判官、session_scorer | `error`，notes 写 `kind` 和原文前段 | 不再出现 skipped |
| 入库判官 | `suspect`，reason 写 `judge_error:truncated` / `missing` / `invalid` | 原来的 `unparseable_or_empty` 拆成三种。截断是确定性的，换同样的输入重判还是截断，所以不进"判官不可用、待重判"那条路（MEMORY：重试之间什么都没变的话，重试永远不会收敛） |
| 提取类 | 记 WARNING 并附 `kind`，不写入 | 和"真的没东西可提取"区分开：0 个候选是正常结果，解析失败要能在日志里看到 |

### 5.5 删除

每迁移一处，就删掉它私有的解析函数和 prompt 里的格式说明：
- 入库判官的 `_JUDGE_VERDICT_RE` 和 VERDICT 先行的写法；
- `session_scorer._extract_json`；
- `memory_extractor._parse_extraction` 里剥围栏的部分；
- `conversation_extract._parse_candidates` 里剥围栏的部分；
- `summarization._parse_enrichment`；
- `llm_optimizer._parse_llm_response`；
- eval 两个判官里剥围栏的代码；
- 各 job 里的 `json.loads(text)`。

## 6. 落地顺序（strangler-fig，每步可单独提交、单独验证）

1. **`structured()` 加测试**：用假的 Converse 客户端覆盖正常、截断、没调 tool、schema 不符四种回复。
2. **判官四处**：离线测试判官、rubric 判官、session_scorer、入库判官。这一步直接修好今天的问题。
3. **提取类九处**：summarization、memory_extractor、conversation_extract、ddd_refresh、memory_health、signal_digest、skill_proposer、skill_fitness、llm_optimizer。纯文本的 `_distill_entry` 和 signal_digest 的 markdown 摘要也在这一步改用 `text()`；截断的改写不写入，入库条目保留原文（它已经通过了判官）。
4. **加一条测试，钉住"只有一个入口"**：用 AST 扫描后端代码，`converse`、`invoke_model` 的调用只能出现在 `jobs/bedrock.py` 里。白名单只放探活这类不要结构化结果的调用，例如 `routers/system.py` 的连通性检查，每条都写明原因。以后新加一处直接解析模型文本的代码，CI 就会红。

## 7. 代价

- **热路径**：都不在发送和流式输出的路径上。summarization enrichment 在 `executors` 的 `llm` 池里跑，其余在 job 或会话结束后跑。
- **token**：判决类多出 `reasoning` 字段，每次调用多约 300–700 个输出 token。入库判官从有记录以来约 3100 次调用。没有新增任何调用。

## 8. 不在范围内

- **API key 模式**：后端自己的模型调用只走 Bedrock，Anthropic API key 模式下本来就不可用。这是改动前就有的问题；统一入口之后，补上它只需改 `structured()` 一处。
- **判官模型的选择、rubric 本身写得好不好**：不在本设计里。

## 9. 验证计划

- **最终测试集**：
  - `structured()` 的四种回复；`text()` 遇到截断时抛错；
  - 每个迁移点各一条测试：正常回复得到正确结果，截断、没调 tool、schema 不符分别得到 §5.4 那张表里的状态；
  - "只有一个入口"的 AST 测试；
  - 原有测试中直接调用这些函数的那些。
- **mutation 检查**：每个迁移点去掉截断判断、去掉 schema 校验，测试都必须变红。
- **评审**：correctness 和 api-contract 两位专家看 `structured()` 和入库判官（它管着四个认知存储的入口），各一轮。
- **线上确认**：rebuild 之后重跑 `OFF_BRAIN_FIRST_ENTRIES_LIB`；之后看一次定时 eval 里 rubric 判官的 error 数，以及入库判官 telemetry 里 `judge_error:*` 的分布。
