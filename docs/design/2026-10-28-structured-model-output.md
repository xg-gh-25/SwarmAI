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
- 返回 tool 的输入（dict），先按 schema 校验。校验器是 `jobs/bedrock.py` 里一个只支持子集的小实现，支持 `type`、`properties`、`required`、`enum`、`items`：`jsonschema` 不是后端声明的依赖，是 `claude-agent-sdk` 顺带装上的，不能依赖它。schema 里出现子集以外的关键字，调用前直接报错，不会把某个约束悄悄跳过不查。
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

## 10. 实施记录

**第 1 步**（`structured()` / `text()`）：
- schema 校验用 `jobs/bedrock.py` 里的子集实现，见 §5.1。
- 改过的地方只有一处：`converse_with_retry` 增加 `tool_config` 参数，并且 system 为空时不传。
- token 用量只在调用方传了 `record_usage` 时才记，和各处现在的做法一致。
- mutation 11 处，全部抓到。

**第 2 步**（四个判官）：
- **入库判官不加 `reasoning` 字段**，是对 §5.2 的例外。它的 prompt 本来就要求"默默思考"，四个认知存储的入库判断都由它决定；这一步只换传输方式，不改它的判断分布。schema 只有 `verdict` 和 `reason`。
- **入库判官失败原因的命名**：§5.4 原写 `judge_error:truncated`，实际用 `judge_output:<kind>`。原因：`judge_error:` 这个前缀在 `admit_memory_lesson` 里会被当作判官故障，送进 pending 重判；而 pending 每次都被完整取出重判，没有次数上限。截断、没调 tool、不符合 schema 这三种失败，重判时输入不变，结果也不变，进 pending 会每个周期都重判一次，永远收不了尾。所以它们按拒绝处理（discard），和改动前的 `unparseable_or_empty` 一样，只是原因能单独统计。周报把 `judge_output` 算作"判官没给出判断"。
- **`_distill_entry` 提前到这一步**，因为它和入库判官共用同一个客户端。截断的改写不会返回，调用方保留原文。
- **删掉的东西**：
  - `_JUDGE_VERDICT_RE`；
  - VERDICT 先行的 prompt 约定；
  - `_neutralize_untrusted` 里破坏伪造 `VERDICT:` 行的那一步（解析器没了，伪造的 VERDICT 行骗不了任何东西；去掉 fence 标记那部分保留）；
  - eval 两个判官剥 ``` 围栏的代码；
  - `_coerce_conf`（schema 已经保证 confidence 是数字）；
  - `session_scorer._extract_json`；
  - 旧维度词到新维度词的映射（schema 用 enum 限定了三个维度，判官给不出旧词）。
- **参数**：
  - 判决类判官上限 2000，读超时 30 → 60 秒，因为输出变长了；
  - session_scorer 的上限 1000；
  - 入库判官的上限 1000。
- **其他**：
  - `JUDGE_PROMPT_SHA` 现在也包含 schema：schema 变了就是另一个判官，旧的校准标签不再计入。本机没有校准标签，没有数据受影响。
  - OS Eval 的判官页显示每个判官的回答 schema。
- mutation 13 处，全部抓到。
- **评审**：correctness 和 api-contract 各一轮，没有 HIGH，问题已一次修完：
  - **MEDIUM（两位都提了）**：数字字段会接受 `"NaN"` 和 `"Infinity"`，而 `_clamp01(nan)` 返回 1.0，判官可以不打分就拿满分。现在数字必须是有限的十进制值，`_clamp01` 遇到非有限值返回 None。
  - **LOW**：
    - integer 接受 `"7"` 和 `7.0`，拒绝 `"7.5"` 和 `"1_000"`；
    - `_check_schema` 检查 `type`、`required`、`enum`、`properties` 本身的类型；
    - `score_session` 收到不是 dict 的回答时 fail-closed；
    - `JUDGE_PROMPT_SHA` 把 tool 的名字和描述也算进去；
    - rubric 判官有了自己的 system 和上限常量；
    - session_scorer 的读超时单独设为 30 秒，回到改动前的水平；
    - 入库判官的三处文档改为新的失败原因。
  - 修复本身做了 mutation 8 处，全部抓到；其中一条测试原来两边常量值相同、区分不出用的是哪个，已改为能区分。
  - **没修的**：rubric 判官没有出现在判官页上，这是改动前就有的问题。

**第 3 步**（提取类九处和纯文本调用）：
- 九处都改用 `structured()`，各自的私有解析一起删掉：`_salvage_truncated_json`、`_parse_enrichment`（改为 `_normalize_enrichment`，只做字段归一）、`memory_extractor._call_llm` / `_parse_extraction`（合成 `_extract_sections`）、`conversation_extract` 的 `invoke_fn` 注入和 `_parse_candidates`、llm_optimizer 的 `_call_bedrock_opus` / `_parse_llm_response`，以及 llm_optimizer、skill_fitness 各自的 Bedrock 客户端缓存和 `reset_bedrock_client`。
- **失败后的处理**：
  - memory_extractor：原来回复不能用时显示"Nothing new to save"，现在返回带原因的错误；真的没有内容可存才算"没有可存的"。
  - memory_health：原来把 parse_error 当成"没有动作"应用，现在返回 error，不改任何东西。
  - skill_proposer：截断的半截提案不再写出，报错。
  - ddd_refresh：回复不能用时记一次失败，汇总里写"N proposal calls failed"，不再和"文档都是新的"看起来一样。
  - conversation_extract 和 LLMJudge 仍然返回 0 个候选 / None，llm_optimizer 仍然返回空并走启发式；日志写明失败种类。
  - signal_digest：markdown 摘要被截断时改用简单摘要，评分调用照样跑；周汇总被截断时改用拼接。两种情况下已花掉的 token 都计入 `monthly_tokens_used`。
- **强制 tool 的例外**：Bedrock 上实测 `claude-opus-5-5` 拒绝 `toolChoice: tool`（400），opus-5、opus-4-8、opus-4-6、sonnet-4-6 都接受。`model_registry.supports_forced_tool_choice` 用一张实测表记下；不接受的模型用 `auto`，system 里加一句"只通过 answer tool 回答"。llm_optimizer 和 LLMJudge 原来开着 adaptive thinking（effort low）；强制 tool 和 thinking 不能同时用，这两个设置删掉了。
- `structured()` / `text()` 增加 `usage` 参数：调用方传一个 dict，抛错前也会填好，job 的月度预算由此照常累加。
- `model_registry.normalize_short_name` 现在去掉任何地域的 `anthropic.` 前缀（`eu.`、`apac.` 等，以及不带地域的）。原来只认 `us.` 和 `global.`；temperature 和 tool choice 现在按完整 ID 判断，`bedrock_model_map` 里配了别的地域时会判错。
- 共用的 `jobs.bedrock.invoke()` 没有调用方了，删掉。
- 参数：llm_optimizer 上限 2000、读超时 60 秒；LLMJudge 上限 1000；memory_extractor 上限 1024；其余沿用原来的上限。
- **已知的取舍**：enrichment 被截断时，原来能靠 salvage 保住已写完的字段，现在整条丢掉，只留规则生成的摘要，日志里有警告。

**第 4 步**（只有一个入口）：
- `tests/test_model_answer_single_entry.py` 用 AST 扫 `backend/`：`converse`、`converse_stream`、`invoke_model`、`invoke_model_with_response_stream` 的调用，以及 `boto3.client("bedrock-runtime")`（位置参数或 `service_name=`），只能出现在 `jobs/bedrock.py`。白名单只有 `routers/system.py` 的连通性检查。另一条测试保证白名单里的文件确实会被扫到，入口文件本身也被扫到。
- 看不到的：手工签名的 HTTP 请求。`core/bedrock_thinking_gateway.py` 就是这样转发 CLI 自己的流量，它不解析回答，不在范围内。

**第 3、4 步的验证**：
- mutation 16 处（含白名单失效的一处），全部抓到。
- 直接调用方的已有测试约 1000 条，全部通过；其中一条（`test_offloop_blocking_calls`）还在替换已删掉的 `_call_llm`，改成替换 `_extract_sections`。
- **评审**：correctness 一轮，没有 HIGH，问题已一次修完：
  - **MEDIUM**：signal_digest 的 markdown 和周汇总被截断时，已花的 token 没进月度预算，评分调用也被跳过。已改，见上。
  - **MEDIUM**：非 `us.` / `global.` 地域的模型 ID 认不出来，temperature 和 tool choice 会判错。已改，见上。
  - **LOW**：ddd_refresh 失败不可见；AST 测试漏掉 `service_name=` 写法；几处 docstring 还在说"JSON 解析失败"或"Opus"。都已改。
  - 这几处修复做了 mutation 5 处，全部抓到。

