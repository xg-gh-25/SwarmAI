---
title: Pluggable Agent Stack — Legacy Claude CLI ‖ NextGen Bedrock Converse
date: 2026-07-08
status: DESIGN (decisions locked, not yet implemented)
project: SwarmAI
author: Swarm + XG
supersedes: none
related:
  - Knowledge/Designs/2026-06-24-session-unit-strangler-fig-extraction-design.md (SessionUnit contract)
tags: [model-agnostic, bedrock-converse, provider-lock-in, side-car, china-ban-contingency]
---

# Pluggable Agent Stack — 并行双栈 + 一个全局开关

## Problem (one sentence)

Claude 模型对中国用户的封禁是悬顶之雷;SwarmAI 的 agent loop 外包给了 Claude
Code CLI(唯一被 provider 锁死的一层),一旦 Claude 在 Bedrock 上对我们不可
用,整个系统停摆——我们需要一条能随时切换到 Bedrock 模型超市(**首选中国头部
开源模型:GLM / Kimi / Qwen / MiniMax / DeepSeek**;Nova 仅作 AWS 一方兜底,
**不做首选** — XG 指示)的降级路径。

**P1 spike 已跑(见下方"P1 Spike 实测结果"),三个 gating 事实全部实证确认:
(1) 本机对非 Claude Bedrock 模型有访问权;(2) 9/10 中国头部模型在 Bedrock 上
原生支持 Converse tool-use,一套代码通吃;(3) Claude Agent SDK 是不可绕的
provider blocker,但只存在于 legacy 侧 —— side-car 设计因此被加固,而非推翻。**

## 核心约束(XG 铁律,2026-07-08)

> **现在的所有都不能动。** 新架构当作一个全新功能来开发,作为将来的预防。
> 开发本身可能要长期调优,期间日常开发/生产必须继续用现路径,零影响。

这条约束把策略从 **refactor(改造现路径)** 翻转为 **side-car(旁挂新栈)**:
新栈是一套独立的、默认休眠的平行实现;开关决定走哪栈;两栈**永不共享 loop
代码**。

## 已核实的现状(P1/R15 — 读真代码,非记忆)

| 事实 | 位置 | 含义 |
|---|---|---|
| agent loop = Claude Code CLI 子进程 | `session_unit.py:59` `from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient` | 唯一真锁 |
| **★ Claude Agent SDK 是不可绕的 provider blocker(实证)★** | SDK v0.2.105 `_internal/transport/subprocess_cli.py:89` `shutil.which("claude")` → `:225` `cmd=[cli,...]` → `:272` `--model`;CLI = `@anthropic-ai/claude-code` v2.1.145(闭源 Node 二进制) | **Python SDK 本身不调 LLM,只是 subprocess 壳,拼 `--model` 启动 CLI**。CLI `--model` help 明说只接受 `sonnet/opus` 别名或 `claude-*` 全名;`--bare` help 明说 3P provider **仅 Bedrock/Vertex/Foundry** 且都是 **Claude 的托管渠道**(Anthropic Messages 协议),给 `qwen.*`/`deepseek.*` 直接拒。**闭源二进制,我们加不了 provider → 只要走 SDK 就永远只能 Claude。这正是必须 side-car 的全部理由。** |
| SDK 的 `fallback_model` 只在 Claude 家族内 fallback | `types.py:1734` + CLI `--fallback-model`(仅 `--print`) | overloaded 时切另一个 Claude;**对封禁场景零帮助**(禁的是整个 Claude)。别误当救命稻草 |
| `CLAUDE_CODE_USE_BEDROCK=true` 只让 CLI 走 Bedrock 的 **Anthropic** 端点 | `claude_environment.py:200` | 该端点只服务 `anthropic.*` 模型 |
| 未知 model ID 是 passthrough | `config.py:100-102` | 传非 Claude ID → CLI 直接 400 |
| streaming = 6 类 SDK message-type 分支 | `streaming_orchestrator.py:588-850`(SystemMessage/StreamEvent/AssistantMessage{Text/Thinking/ToolUse/ToolResult}/UserMessage/ResultMessage) | 新栈要产出兼容它的对象 |
| ~17 个 hook 经 `HookMatcher` 注入 `ClaudeAgentOptions` | `hook_builder.py:98-127, 212+`;签名 `async def(input_data, tool_use_id, context)->dict` | hook **逻辑** agnostic;**触发机制**绑 CLI |
| tool 执行(Read/Write/Edit/Bash/Grep/Glob/Skill/MCP)全是 CLI 内建 | — | 我们没写过一行;新栈得自己实现 |
| **`SessionUnit` 全系统仅 2 处实例化** | `session_router.py:873`(正常)/`:923`(prewarm) | ★ 唯一的开关缝隙 ★ |
| `session_router.py` 是 CRITICAL | 152 callers | seam 改动本身要 pipeline + 等价性测试 |
| 3 处 boto3 旁路已用 Anthropic-native 字段 | `summarization.py:752` invoke_model(anthropic_version)、`llm_optimizer.py:281` converse(`thinking`/`output_config.effort`)、eval judge | 属"现路径",**本设计不碰**(见非目标) |

## 业界调研结论(读真代码/官方文档,2026-07-08)

model-agnostic 只有两种范式,没有第三条:

- **范式 A — 自建 provider adapter 层(OpenClaw)**:`@openclaw/ai` + `StreamFunction`
  契约,每 provider 一个手写 adapter;tool-use 靠每家一个 **projection** 模块双向
  翻译;统一 `AssistantMessageEventStream`;**自己拥有 loop**(`agent-core/agent-loop.ts`);
  三层 fallback。→ 证明"自建 loop"可行,但标了价(tool projection 是重工程)。
- **范式 B — 站在 agnostic 框架上(DeerFlow)**:直接用 LangChain `BaseChatModel` +
  LangGraph;换模型 = 改 `conf.yaml`;但**代价真实**:只支持 non-reasoning 模型、
  Gemma-3 因缺 tool-use 不支持、**无跨 provider failover**——能力 = 各家 integration
  的最低公分母。

**我们比两者都强的地方**:站在 Bedrock 上。**Bedrock Converse API 本身就是 provider
归一层**——一个 `converse()` + 统一 `toolConfig`/`toolUse`/`toolResult`,跨 Nova/Claude/
Llama/Mistral 一套代码。→ **抄 OpenClaw 的架构骨架(自有 loop),但用 Converse 省掉它
最重的 tool-projection 工程。** 不学 DeerFlow 换框架(会丢 Claude Code 成熟度还换不来
可靠性)。

关键边界(已核实):Converse **信封**(`messages`/`toolConfig`/`inferenceConfig`)可移植;
`additionalModelRequestFields` 里的 Claude 专属字段(`thinking`/`output_config.effort`/
`top_k`/beta headers/computer-use tools)**换模型必炸**——这是纯度边界。

## P1 Spike 实测结果(2026-07-08,`/tmp` 探针,不接 router、不碰仓库)

Spike 定位:一次性探针,拿"降级栈能不能用"的 gating 真数据,非 refactor。

### STEP 1 — 连通性 + tool-use 探针(基础模型)

| 模型 | 访问 | tool-use | 结果 |
|---|:---:|:---:|---|
| Nova Pro (`us.amazon.nova-pro-v1:0`) | ✅ | ✅ `tool_use` | tool→round-trip 完美 |
| Nova Lite | ✅ | ✅ | 完美(thinking 啰嗦) |
| Llama 3.3 70B | ✅ | ❌ `end_turn` | **坑**:把 tool call 当纯文本 JSON 吐,不走 toolUse block |
| Claude Sonnet(对照) | ✅ | ✅ | 最干净 |

三个 gating 事实确认:①本机能调非 Claude Bedrock ②Converse 统一 tool-use 在
Nova 上真跑通(`toolConfig`→`stopReason=tool_use`→`toolResult` 一套代码同 Claude)
③`toolUseId` 精确回显(OpenClaw 坑#7)有效。当场印证 Llama 需 tool-call-repair 层。

### STEP 1b — 中国头部模型 tool-use 探针(封禁场景的真备胎)

`bedrock.list_foundation_models()` 实测 us-east-1 共 121 个 FM,中国厂商齐全:
DeepSeek / Qwen(通义) / Moonshot(Kimi) / Z.AI(智谱 GLM) / MiniMax 全在。

| 模型 | 厂商 | 访问 | 原生 tool_use | 备注 |
|---|---|:---:|:---:|---|
| Qwen3 235B VL | 阿里通义 | ✅ | ✅ | **最干净**(191 tok) |
| Qwen3 Next 80B / Coder Next | 阿里通义 | ✅ | ✅ | 干净 |
| Kimi K2.5 / K2 Thinking | Moonshot | ✅ | ✅ | 干净 |
| GLM-5 / GLM-4.7 | 智谱 Z.AI | ✅ | ✅ | 干净 |
| MiniMax M2.5 | MiniMax | ✅ | ✅ | 干净 |
| DeepSeek V3.2 | DeepSeek | ✅ | ✅ | ⚠️ `<｜DSML｜>` 标记漏进 text,需清洗 |
| DeepSeek R1 | DeepSeek | ⚠️ | — | 需 inference profile ARN,不支持 on-demand |

**结论:9/10 中国头部模型原生 tool-use 直接跑通,一份 `toolConfig` 通吃。封禁
备胎从"能保命"升级为"选择丰富"** —— 五大中国厂商全部可切,不是将就一个 Nova。
同时坐实了我们比 OpenClaw 省工程:它每家手写 projection,我们对 Converse 一个
API 写一次即可。

### 模型能力评测(2026-07-08,agentic-coding + tool-use 维度,独立 leaderboard 优先)

来源:swebench.com / tbench.ai / taubench.com / gorilla BFCL V4 / livecodebench +
各厂 HF 卡片。**维度对齐我们用途(coding agent),非通用 MMLU。**

| 模型 | SWE-bench Verified | Terminal-Bench 2.0 | tool-use(BFCL/tau2) | tool-call 格式 |
|---|:---:|:---:|:---:|---|
| **GLM-5** | 72.8% (LB) | **52.4%(全场最高)** | tau2 最强(可得) | ✅ 最干净,无泄漏 |
| Kimi K2.5 | 70.8% (LB) / 76.8 卡片 | 43.2% | K2 系 BFCL 59% | ✅ 标准 JSON |
| MiniMax M2.5 | **75.8%(SWE 榜首)** | 42.7% | 无独立数据 | ⚠️ `<think>` 须留在历史 |
| Qwen3-235B-Thinking | [未验证] | [未验证] | BFCL ~48% | ⚠️ Coder 版是 XML,须用 Thinking 版 |
| DeepSeek V3.2 | 70.0% | 39.6% | BFCL 54% | ❌ `<｜DSML｜>` Bedrock 实锤泄漏 |

**诚实缺口**(评测方明说):最新 thinking 模型(K2.5/GLM-5/M2.5)在最干净的独立
tool-use 榜(BFCL/tau2)尚未覆盖 → "GLM-5 首选"是**选型输入,非终审**,最终靠
STEP 2 我们自己用真工具跑 loop 坐实。

### ✅ 有序候选列表(`nextgen_stack_models`,best-first;Nova 已按 XG 指示踢出首选)

1. **GLM-5** (`zai.glm-5`) — 首选:tool-use 最可靠 + Terminal-Bench 榜首 + 格式最干净。
2. **Kimi K2.5** (`moonshotai.kimi-k2.5`) — 次选:最强 coder(LiveCodeBench 85),专为长 tool-call 链设计。
3. **MiniMax M2.5** (`minimax.minimax-m2.5`) — SWE 榜首;注意 `<think>` 历史保留坑。
4. **Qwen3-235B-Thinking** (`qwen.*-thinking`,**不用 Coder 版** — XML 格式 Converse 不带 parser) — 稳健通用备胎。
5. **DeepSeek V3.2** (`deepseek.v3.2`) — 末位/需 DSML 清洗层才可用。
6. ~~Nova~~ — 不进首选序,仅 AWS 一方兜底。

### STEP 2 — 真流式多轮 agent loop(玩具负载,内建 OpenClaw 坑)

自建 `converse_stream` loop + 真工具(Read/Bash),可验证任务(读数字文件→bash 求和→
报告,唯一正确答案 sum=50/count=5)。内建 OpenClaw 坑:#1 只在 `stopReason==tool_use`
派发、#4 max-iteration 守卫、#5 delta 按 `contentBlockIndex` 累积、#6 arg JSON
字符串 buffer 到 `contentBlockStop` 才 parse、#7 `toolUseId` 精确回显、#10 throttling
重试带 jitter/不重试 validation、#11 first-event stall 守卫。

| 排名 | 模型 | 结果 | 轮次 | 工具 | 耗时 | tok | flags |
|:---:|---|:---:|:---:|:---:|:---:|:---:|---|
| 1 | Kimi K2.5 | ✅ | 3 | 2 | 4.0s | 797 | 干净 |
| 2 | MiniMax M2.5 | ✅ | 3 | 2 | 6.4s | 1371 | `has_reasoning` |
| 3 | GLM-5 | ✅ | 3 | 2 | 7.2s | 1162 | 干净 |
| 4 | Qwen3-235B | ✅ | 4 | 3 | 7.6s | 1646 | 干净 |
| 5 | DeepSeek V3.2 | ✅ | 4 | 3 | 10.4s | 2831 | ⚠️`dsml_leak` |

**5/5 全部正确完成多轮 agentic 任务。** OpenClaw 的坑提前内建 → 零踩雷(调研直接
省掉试错)。当场复现:DeepSeek `<｜DSML｜>` 真漏进 text(最慢最贵,末位坐实);
MiniMax `reasoningContent` 真出现(印证 `<think>` 须留历史)。

### STEP 2b — ★ 真实 system-prompt 负载下重跑(补盲区)★

STEP 2 用的是玩具 system prompt(一句话)。这轮用**真实组装**:全量 context 文件
(SOUL/AGENT/MEMORY…)+ **89 个真 skill 描述** = **257K chars / ~86K tok**(镜像
selective-injection + 91K 预算后的真实有效负载),重跑同一 loop。

| 排名 | 模型 | 结果 | 轮次 | input tok | 耗时 | flags |
|:---:|---|:---:|:---:|:---:|:---:|---|
| 1 | Kimi K2.5 | ✅ | 3 | 206K | **16.6s** | 干净 |
| 2 | Qwen3-235B | ✅ | 3 | 222K | 18.5s | 干净 |
| 3 | DeepSeek V3.2 | ✅ | 3 | 216K | 25.9s | ⚠️`dsml_leak` |
| 4 | GLM-5 | ✅ | 3 | 210K | 26.0s | 干净 |

**4/4 全部正确 —— 降级栈扛得住真实认知负载,不是玩具能跑。** 三个真实负载暴露的事实:
1. **延迟涨 4-6 倍**(玩具 4-7s → 真实 16-26s),因每轮重发 86K prompt。
   → 初判"用 Converse `cachePoint` 解决"——但 **STEP 2d 实证推翻:候选中国模型
   目前不支持 cachePoint**(详见下节)。延迟处理见 STEP 2d + "延迟决策"。
2. **排序更稳**:Kimi K2.5 玩具+真实两轮都最快最省 → "速度/成本首选"证据增强;
   GLM-5 简单任务垫底,其 Terminal-Bench 优势要复杂 agentic 任务才显(单任务不翻盘)。
3. **DeepSeek `dsml_leak` 大 prompt 下依然复现** → 末位 + 需清洗层,稳定结论。

### STEP 2c — ★ 真实 hook 核心可移植性(补盲区:"没接真 hook 生命周期")★

设计原声称"复用 hook 本体、只复刻分发"——此前是**推断**,这轮**实证**。真的从 Claude
CLI 外部导入**真实生产的** `dangerous_command_gate` 检测核心并调用:

| 实证项 | 结果 |
|---|---|
| `security_hooks.py` 顶层 SDK import | **零** — hook 模块不依赖 Claude SDK |
| `context: Any`(SDK 对象)在 gate 体内被引用 | **从不** — gate 只读纯 dict `input_data` + 我们自己的 `session_context` 闭包 |
| 真实 gate 核心从 CLI 外部调用(含 C041 `gh --visibility`/`git push --force` 防线) | **8/8 判定正确** |

**结论:hook 输入契约是纯 dict `{tool_name, tool_input}`** —— 正是 nextgen loop 里
`execute_tool` 已持有的东西。工具执行前按同一 dict 契约调真实 hook 函数,**安全层
原样生效,无需重写、无需 Claude CLI。** "hook 可移植"从推断升级为实证。

**诚实划界(仍属 P3,不夸大)**:
1. 这轮只验了 **1 个** hook(最关键的安全 gate);其余 16 个(code_intel/correction_capture/
   observation_recorder…)各有依赖,须逐个核实——但"契约是 dict-based、SDK-context-free"
   这个**可移植性前提已实证**,逐个移植有了地基,非空谈。
2. **HITL 审批流要重接**:`dangerous_command_gate` 的**检测**核心可移植(已证),但其后半段
   human-approval 流(`permission_mgr.enqueue` → 前端弹窗)绑我们自己的 permission 基础设施,
   nextgen loop 要重新接线。检测能用,审批 UI 要重接。
3. **`permissionDecision` 消费**:CLI 读 `hookSpecificOutput.permissionDecision` 决定放不放行;
   nextgen loop 得自己复刻"读 hook 返回 → deny 则不执行工具"。

### STEP 2d — ★ cachePoint 实证:候选中国模型不支持(推翻假设)★

STEP 2b 的延迟(16-26s)初判用 Converse `cachePoint` 缓存 system prompt 解决。这轮
实证:发一个 ~13K tok system prompt 两次(turn1 写缓存 / turn2 读缓存),量 cacheRead。

| 模型 | cachePoint 支持 | turn2 cacheRead | 延迟 |
|---|:---:|:---:|---|
| GLM-5 | ❌ `AccessDeniedException` | — | "unsupported model or request did not allow prompt caching" |
| Kimi K2.5 | ❌ `AccessDeniedException` | — | 同上 |
| Qwen3-235B | ❌ `AccessDeniedException` | — | 同上 |
| Nova Pro | ✅ | 10356(100% 命中) | 2.5s→**1.4s** |
| Claude Sonnet(对照) | ✅ | 10922(100% 命中) | 2.5s→1.4s |

**结论:我们的首选候选(GLM/Kimi/Qwen)目前在 Bedrock 上不支持 Converse prompt
caching。只有 Nova(AWS 自家)和 Claude 支持。** 这与两条约束撞车:①中国模型是封禁
备胎首选(Nova 不做首选);②全量 86K system prompt 是**硬约束**(见下)。

**关键约束澄清(XG 定,2026-07-08):精简 prompt 绝对不可接受。**
降级栈**必须背完整 system prompt**(SOUL/AGENT/MEMORY/EVOLUTION + 全部 skill),与
legacy 完全一致。理由:这套系统的护城河就是这 86K 认知负载;降级栈若背不动它,切过去
的不是"降级的 Swarm",而是"另一个失忆的 agent"——违背 side-car 的全部意义(保住的
是**完整的我**,不是一个能跑的壳)。→ **"缩 prompt 换延迟"永久出局,不再评估。**

**★ 延迟决策(XG 定,2026-07-09):先让路通 = 接受延迟。★**
- 降级栈是**封禁应急**,非日常路径。全量 prompt 下 16-26s/轮的延迟**予以接受**——
  换回"完整的我还在",这个代价合理。
- **cachePoint = PENDING Bedrock 支持,不作为当前阻塞项。** 候选中国模型一旦获得
  Converse cachePoint 支持,nextgen 栈应立即启用(system prompt 是稳定前缀,天然
  适合缓存,预计命中率≈100%,可把延迟砍回个位数秒)。这是**未来加分项,不是硬需求**。
- **不做**:①缩 prompt(出局);②自建 KV 复用(Converse 是无状态 REST,不重发即无
  上下文,方向不成立);③为了 cache 改用 Nova 首选(违背"Nova 不做首选")。
- **持续跟踪**:Bedrock 正在快速为模型补 cache 支持;"今天不支持 ≠ 永远"。P4 或独立
  跟踪项定期重探候选模型的 cachePoint 支持,支持即启用。

### ✅ P1 终审结论 — GO(信心充足,盲区已补)

| 验证项 | 状态 |
|---|:---:|
| Claude SDK 不可绕(实证:subprocess 壳 + `--model` 只认 claude-*) | ✅ |
| 中国头部模型 Bedrock 原生 tool-use | ✅ 9/10 |
| 自建 Converse loop 多轮 agentic(玩具负载) | ✅ 5/5 |
| **自建 loop 扛真实 86K system prompt(89 skills)** | ✅ 4/4 |
| **真实安全 hook 核心可从 CLI 外部调用(dict 契约)** | ✅ 8/8 |
| 一份代码通吃 N 模型(验证比 OpenClaw 省工程) | ✅ |

**仍未验(P2/P3,非 P1 职责)**:完整 17-hook 分发 + HITL 重接 + `ConverseSessionUnit`
duck-type 现有 router 契约(P2 已提取 36 符号契约面,见下)+ resume。P1 该答的(loop
可行性 + 真实负载 + 安全层可移植性)全部实证 GO。

**P1 实测得来的设计定论**:
1. **延迟:接受**(全量 prompt 16-26s/轮)。cachePoint 候选中国模型不支持(STEP 2d)→
   **PENDING Bedrock 支持,非阻塞**。缩 prompt 出局(护城河=完整认知负载,不可牺牲)。
2. **候选排序实测微调**:Kimi K2.5 = 速度/成本首选;GLM-5 = 复杂 agentic 首选(待更大
   任务集坐实);DeepSeek 末位需清洗层。

## 架构:两条平行栈,一个全局开关

```
                    ┌──────────────────────────────────┐
   请求 ──────────► │  Switch: config.json.agent_stack   │
                    │        "legacy" (默认) | "nextgen"  │
                    └───────┬───────────────────┬────────┘
                       OFF  │                   │  ON(显式 opt-in)
              (默认,永远)   ▼                   ▼
        ┌──────────────────────────┐  ┌──────────────────────────┐
        │ LEGACY 栈(现状 · 冻结)    │  │ NEXTGEN 栈(新建 · 隔离)    │
        │ SessionUnit               │  │ ConverseSessionUnit        │
        │  → Claude Code CLI         │  │  → 自有 run_loop           │
        │  → streaming_orchestrator  │  │  → Bedrock Converse         │
        │  ★ 零字节改动 ★           │  │  ★ 独立文件,慢慢调 ★      │
        └────────────┬─────────────┘  └────────────┬─────────────┘
                     └──── 共享 L4 护城河(只读)────┘
                    memory / DDD / evolution / context assembly
```

**设计铁则:两栈不共享 loop 代码,不抽 `AgentBackend` 统一接口。** 抽象接口会诱使
把现路径重构成"legacy backend"——那就是动了现在的。新栈是与 `SessionUnit` **平级、
独立文件、独立测试** 的全新类,靠 **duck-type 对外契约** 与现有 router/lifecycle 共存。

## 决策(LOCKED,XG 拍板 2026-07-08)

### D1 — 开关放 factory seam(router 两行)✅
`session_router.py` 的两处 `SessionUnit(...)`(`:873` / `:923`)改为调用一个新
factory。这是**全系统唯一**对现有代码的改动。

```python
# 新文件 session_unit_factory.py
def create_session_unit(session_id, agent_id, **kw):
    stack = AppConfigManager.instance().get("agent_stack") or "legacy"
    if stack == "nextgen":
        from .converse_session_unit import ConverseSessionUnit
        return ConverseSessionUnit(session_id, agent_id, **kw)
    return SessionUnit(session_id, agent_id, **kw)   # 现栈,原样构造
```
- `stack=="legacy"`(默认)下**与现状逐字节等价**:同一个类、同一个构造签名。
- 这不算"动现在的":纯 seam,不改现路径任何逻辑,只在创建那一刻问一句走哪栈。
- ⚠️ `session_router` 是 CRITICAL(152 callers)→ 这两行也走 pipeline,且必须有
  **mutation 测试**:删掉 nextgen 分支 → legacy 路径全部测试仍绿(证明 OFF 时零影响)。

### D2 — 开关粒度:全局(config.json 单开关)✅
`agent_stack: "legacy" | "nextgen"`,一个全局值。
- 理由:XG 要"确保现在的不碰"——全局开关最简单、最不侵入,不需要在 session 表
  加字段、不改任何 per-session 逻辑。
- 默认永远 `legacy`。`nextgen` 是显式 opt-in(改 config + 新建 session 生效)。
- 代价:不能同时一个 tab 跑 nextgen、其余 legacy。可接受——这是"将来的预防",
  不是日常并行需求;要测新栈时全局切一下,测完切回。

### D3 — 定位:全新功能 / 将来的预防 ✅
不追功能对等,不追日常使用。目标是"封禁真发生时,有一条已验证能保命工作的降级
路径",平时休眠。开发节奏不紧,慢慢调优,期间日常开发始终在 legacy。

### D4 — 事件流:新栈自产 Claude-shaped 对象(B1,唯一选项)
新栈 `run_loop` 消费 Converse 输出,**合成** `AssistantMessage`/`ToolUseBlock`/
`ResultMessage` 喂给现有 `streaming_orchestrator`。
- 因为"orchestrator 不能动",重写中立事件流(B2)= 动 spine = 违规,出局。
- Claude SDK message 类型是各 provider 超集,Converse 的 text/toolUse/toolResult
  能干净映射进去。技术债(中立格式=Claude 类型)在 seam 处标注,接受。

## `ConverseSessionUnit` 要实现的对外契约(= 现状白送的东西)

新栈要被现有 router/lifecycle/registry 当"一个 unit"管理,必须 duck-type 出
`SessionUnit` 的**对外契约**(非内部实现)。

### ★ 契约面已提取(P2 只读扫全仓,2026-07-08)★

**36 个外部访问符号 = MEDIUM 面**(11 async 方法 + 13 public 属性/property + 12 个
被外部读写的内部属性)。**关键判定:duck-type 可行,且 NOT 要求 wrap Claude CLI** —
这 36 个符号的类型全是 dict / `SessionState` enum / primitive(state 是我们自己的
enum;`send()` 返回我们自己定义的 dict 事件流,不是 SDK 对象)。所以新栈只要"产出同
形状的状态转换 + dict 事件",无需模仿任何 Claude-CLI 内部机制。

- **构造**:`(session_id: str, agent_id: str, *, on_state_change: Callable[[str, SessionState, SessionState], None])`(`session_unit.py:454`)
- **11 方法**(全 async):`send()`(核心,返回 `AsyncIterator[dict]` SSE 事件)、`kill()`、
  `interrupt()`、`compact()`、`health_check()`、`force_unstick_streaming()`、
  `force_unstick_waiting_input()`、`reap_dead_waiting_input()`、`_ensure_spawned()`(prewarm)、
  `continue_with_permission()`、`continue_with_answer()`。
- **13 public 属性/property**:`session_id`/`agent_id`/`state`/`created_at`/`last_used`/
  `is_channel_session` + property `pid`/`is_alive`/`is_post_disconnect_flushing`/`is_protected`/
  `streaming_stall_seconds`/`stop_event`/`has_outstanding_tool_use`。
- **12 个外部读写的内部属性**(router/lifecycle 直接读写):`_sdk_session_id`、`_recall_injected`、
  `_recall_keyword_misses`、`_channel_history_injected`、`_hooks_enqueued`、`_peak_tree_rss_bytes`、
  `_health_sensor`、`_recovery_coordinator`、`_user_stopped_current_turn`、`_last_proactive_restart`、
  `_last_drained_seqs`、`_last_metrics`。→ 这些是**封装泄漏**,新栈得把它们做成可读写的
  普通属性(值可为降级语义的占位,如 `_peak_tree_rss_bytes` 对无子进程的 loop 可置 0)。
- **状态机**:`SessionState` enum(`COLD/IDLE/STREAMING/WAITING_INPUT/DEAD`,`from core.session_unit import SessionState`);`on_state_change(session_id, old, new)` 必须在**每次**状态转换后调用(router 的 slot 协调 + drain worker 依赖它,漏调=协调断裂)。
- **send() 流式契约**:`AsyncIterator[dict]`,事件形如 `{"type":"text_delta"/"tool_use"/"tool_result"/"result"/"error"/"session_start"/"ask_user_question"/...}`(D4:新栈从 Converse 输出合成这些 dict)。

→ router 之上的 152 个 caller **一个都不改**,它们只跟"一个 unit"打交道。

**P2 的活 = 把这 36 个符号在 `ConverseSessionUnit` 上实现出来**:状态机 + `on_state_change`
可靠触发是最 load-bearing 的(HIGH 复杂度不在符号数,在状态机精确性 + 每次转换必回调)。
12 个封装泄漏属性用降级占位即可。`resume` 语义可先弱化。

## 新栈的 tool 执行 + hook(隔离实现,慢慢长)

- **tool 执行**:自有 loop = 自己执行工具。`run_loop`:`converse(toolConfig)` →
  `stopReason==tool_use`? 是→自己的 `ToolExecutor` 跑 → 回填 `toolResult` → 再
  `converse`;否→done。
- **P0 工具集**:Read / Write / Edit / Bash / Grep / Glob(Python 文件系统 + subprocess,
  量可控)。MCP(需自写 client bridge → toolConfig)、Skill(降级为"把 SKILL.md 正文
  喂进 prompt")是长尾,后期补。
- **hook**:L3 那 17 个 hook 的逻辑函数 agnostic(**STEP 2c 已实证**:`security_hooks.py`
  顶层零 SDK import,`context` 参数在 gate 体内从不引用,真实 `dangerous_command_gate`
  核心从 CLI 外部调用 8/8 正确)→ **复用函数本体(只读,不改)**,在新栈 loop 里按纯
  dict 契约 `{tool_name, tool_input}` **复刻分发** + 消费 `hookSpecificOutput.
  permissionDecision`(deny→不执行工具)。**安全 gate 在新栈必须照样生效**——降级时正是
  最脆弱的时刻(封禁中),绝不裸奔。HITL 审批 UI 那半段绑我们自己的 permission 基础设施,
  需重接(检测核心可移植,审批流重接)。
- **prompt caching = PENDING Bedrock 支持,非阻塞(STEP 2d 实证)**:真实 86K prompt
  每轮重发 → 16-26s/轮。**候选中国模型(GLM/Kimi/Qwen)目前不支持 Converse `cachePoint`**
  (仅 Nova/Claude 支持)。延迟决策 = **先让路通,接受延迟**(降级=应急,非日常)。
  全量 prompt 是硬约束(缩 prompt 出局)。候选模型一旦获得 cachePoint 支持即启用
  (system prompt 是稳定前缀,命中率≈100%)——加分项,不阻塞交付。持续重探。

## 切换机制

```jsonc
// config.json
{
  "agent_stack": "legacy",              // legacy(默认) | nextgen — 全局单开关(D2)
  // nextgen 用的 Bedrock 模型 = 有序候选列表(best-first),不是硬编码单模型。
  // 排序依据 = P1 spike 实测 tool-use 保真度 + agentic-coding 评测。Nova 不入首选。
  "nextgen_stack_models": [
    "zai.glm-5",                        // 首选:tool-use 最可靠 + Terminal-Bench 榜首 + 格式最干净
    "moonshotai.kimi-k2.5",             // 次选:最强 coder,专为长 tool-call 链设计
    "minimax.minimax-m2.5",             // SWE 榜首;须保留 <think> 于历史
    "qwen.qwen3-235b-a22b-thinking"     // 稳健通用备胎(Thinking 版,非 Coder — Coder 是 XML)
    // "deepseek.v3.2" 末位/需 DSML 清洗层;Nova 仅 AWS 兜底,不入首选
  ]
}
```
- **候选列表语义**:按序尝试,首个"可访问 + 能力达标"者胜出。这也是 OpenClaw
  fallback 思路的落地(但先做最简单的有序尝试,不做复杂路由)。
- 现阶段手动切(改 config)。
- **不引 LiteLLM**:多一个 provider + 一个 hop,违背 pure-filesystem/自控哲学。
  将来若要"自动 Claude 挂→切 nextgen"或复杂 failover 路由,再单独评估 LiteLLM Router
  / Strands。当前手写全局开关足够。

## 分期(全部围绕"现路径零改动")

| 期 | 内容 | 碰现路径? | Profile | 风险 |
|---|---|---|---|---|
| **P0** | `agent_stack` config(默认 legacy)+ factory seam(router 两行)+ **mutation 测试**(删 nextgen 分支 → legacy 全绿) | **仅 2 行 seam** | bugfix | 低(seam 等价性钉死即安全) |
| **P1** ✅ 完成 | Converse spike:多轮 tool-use loop(玩具+真实 86K prompt)+ hook 核心可移植性 + cachePoint 探针,不接 router | 否 | research | **GO** — 见 STEP 1/1b/2/2b/2c/2d |
| **P2** | `ConverseSessionUnit` 骨架:实现 36-符号 duck-type 契约面(见"契约面")+ 5 态机 + `on_state_change` + 合成 Claude-shaped dict 事件 + 全量 system prompt + `stack=nextgen` 被 router 接住走通最简对话。**不做 cachePoint**(候选模型不支持,接受延迟) | 否(新文件) | goal | 中 |
| **P3** | P0 工具集 + hook 生命周期复刻(逐个移植 17 hook)+ 安全 gate 生效 + **HITL 审批流重接** + `permissionDecision` 消费 | 否 | goal | 中 |
| **P4** | MCP bridge / Skill 降级 / resume 降级 / DeepSeek DSML 清洗层(若用) / **重探候选模型 cachePoint 支持,支持即启用** | 否 | goal | 中 |

P1 起全在新文件里,日常开发始终在 legacy,零影响;`nextgen` 只在手动测试时开。

## 非目标(划清,防 scope 爆炸)

- ❌ 不做 `AgentBackend` 统一抽象(诱使重构现路径)。
- ❌ 不动 `streaming_orchestrator` / hook 逻辑 / `session_router` 除 D1 那两行外任何行。
- ❌ 不脱敏现有 3 处 boto3 旁路(summarization/llm_optimizer/eval)——那是"现路径"。
  新栈需要 Converse 信封时,在**新栈内**独立写,不改旧旁路。
- ❌ 不追功能对等(降级就是降级)。
- ❌ 不引 LiteLLM / Strands 做主框架;不支持非 Bedrock provider。
- ❌ `agent_stack` 默认永远 legacy;nextgen 显式 opt-in。

## 最大风险

1. **factory seam 等价性**——`session_router` CRITICAL,那两行必须证明 OFF 时行为
   不变(mutation test)。这是**唯一能伤到现开发的点**,P0 钉死。
2. **契约漂移**——`SessionUnit` 会演进,`ConverseSessionUnit` 要跟其对外契约。用
   契约测试锁,不追内部实现。
3. **tool 执行长尾**——P0 六件套好写,MCP/Skill 是长尾,别在 P3 一次吞。
4. **新栈质量长期低于 legacy**——接受,这是"降级备胎"的定位,诚实标注。

## Definition of Done(何时算"预防到位")

- `agent_stack=nextgen` 下,能用一个非 Claude Bedrock 模型(如 Nova)完成一轮
  带 tool-use(至少 Read+Bash)的真实对话,且 **dangerous_command_gate 生效**。
- `agent_stack=legacy`(默认)下,现系统所有行为逐字节不变(mutation test 证明)。
- 全局开关切换无需改代码、无需重启以外的操作。
```
