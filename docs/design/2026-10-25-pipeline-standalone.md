---
title: Pipeline v1 standalone — 在 Claude Code、Codex、Kiro CLI / KiroCrew 里都能跑完
created: 2026-10-25
updated: 2026-10-26
project: SwarmAI
status: r5 — T1、T2、T6 已实施（§7、§7.1）；T3–T5、T7、T8 待做
---

# Pipeline v1 standalone

## 0. 一句话

今天 pipeline 的命令已经不依赖 daemon（S6-3），但一个 run 要走完，还依赖 SwarmAI 会话里的 hook：
owner 由会话注入，审查专家的回答由 SubagentStop hook 记录，人的决策和落地批准由 SwarmAI 的审批卡片记录，
REFLECT 收尾和 worktree 回收由 daemon 做。离开 SwarmAI，只有不派专家的小改动能落地，而且 run 停在
`reflecting`。本设计让协议的每一步都能在 Claude Code、Codex、Kiro CLI（含 KiroCrew）里完成：
CLI 自己能做的放进 CLI，必须由宿主提供的（子 agent 的回答、人的输入）通过各宿主的 hook 接进来，
宿主做不到的明确降级，并且在 REPORT 里写明。

## 1. 宿主能力（2026-10-25 查官方文档）

来源：Claude Code `code.claude.com/docs/en/hooks.md`；Codex `developers.openai.com/codex/hooks.md`；
Kiro `kiro.dev/docs/hooks/types.md`、`/hooks/actions.md`、`/crew/capabilities/hooks.md`。

| 能力 | Claude Code | Codex CLI（0.141+） | Kiro CLI V3 / KiroCrew |
|---|---|---|---|
| 配置位置 | `.claude/settings.json`（项目）、`~/.claude/settings.json`、插件 | `.codex/hooks.json` 或 `config.toml [hooks]`、`~/.codex/hooks.json`、插件 | `.kiro/hooks/*.json`、agent 配置 `hooks`；KiroCrew `~/.kiro/crew/hooks.json` |
| 首次启用 | 直接生效 | **要用户在 `/hooks` 里信任**（按 hook 定义的哈希） | 直接生效 |
| PreToolUse 拒绝 | `permissionDecision: deny` | `deny`（或 exit 2） | exit 2（stderr 给模型） |
| PreToolUse 改写输入 | `updatedInput` | `updatedInput`（Bash、apply_patch、MCP、本地函数工具含 `spawn_agent`） | **没有** |
| PreToolUse 问人 | `ask` | 不支持（`ask` 被当作失败） | 没有 |
| 子 agent 结束 | `SubagentStop`：`agent_transcript_path`、`last_assistant_message` | `SubagentStop`：同名两个字段 | **没有**；只能在 PostToolUse 匹配 `subagent` 工具，读 `tool_response` |
| PostToolUse 看结果 | 有 | 有 | 有（`tool_response`） |
| 会话 id | 每个 hook 的 `session_id`；Bash 里有 `CLAUDE_CODE_SESSION_ID` | 每个 hook 的 `session_id`（子 agent 用父会话的） | hook 事件里的 `session_id`；Bash 环境里没有文档写明的变量 |
| 往后续 Bash 注入环境变量 | SessionStart 写 `CLAUDE_ENV_FILE` | 没有（只能 PreToolUse `updatedInput` 改命令） | 没有 |
| transcript | `transcript_path`，JSONL，格式稳定（今天的 `transcript_usage` 就读它） | `transcript_path`，**文档说格式不是稳定接口** | hook 里没有 transcript 路径 |
| 人回答问题的工具 | `AskUserQuestion`（文档写明 PreToolUse 可以代答；PostToolUse 能否读到人的答案**待 T3 实测**） | 没查到通用的问人工具 | 没查到 |

Codex 文档原话：工具 hook 是"有用的护栏，不是完整的强制边界"。三家都一样：hook 能挡住大多数误操作，
挡不住一个存心绕开的 agent。本设计按 P5（先量再加）不追求防作弊，只保证"宿主交来的事实"和
"agent 自己说的"在记录里分得开。

## 2. 今天离开 SwarmAI 断在哪（核对代码）

| # | 环节 | 今天靠什么（代码） | 离开 SwarmAI |
|---|---|---|---|
| A | owner 身份 | SwarmAI 会话的 `SWARM_APP_SESSION_ID`；`run_owner_gate` 把它补到 pipeline 命令前 | 调用方自己设，否则 `create` 拒绝 |
| B | 审查专家回答 | `runtime_hooks.create_v1_review_handout_tracker`（PreToolUse Agent：把子 agent 绑到 handout）+ `create_agent_tool_audit_hook`（SubagentStop：从子 agent transcript 读回答，写 `answer`） | 回答记不进去，非小改动卡在 REVIEW |
| C | 专家类型和预算 | `pipeline_expert_agent`：`pipeline-expert` 子 agent 定义、`expert_type_gate`、`expert_budget_gate` | 没有 |
| D | 人的决策 | `ask_question_gate`：人在审批卡片上的选择写成 `human_answer` | 无法回答 |
| E | 人工批准落地 | `run_owner_gate` 批准阶段写 `auth`（`run-land --request`、`accept-finding`、`discard-snapshot`） | 无法批准 |
| F | 隔离 | `worktree_write_gate` | 只靠自觉；落地只认 worktree 的 tree，写到别处的不会落地 |
| G | REFLECT 收尾、worktree 回收 | `reflect_job.create_reflect_trigger`（PostToolUse）、daemon 启动和每小时的 `run_worktree.sweep` | 停在 `reflecting`，worktree 和 `run/` 分支一直留着 |
| H | token | `transcript_usage` 读 Claude JSONL | 只有 Claude Code 可能量到 |

## 3. 原则

1. **协议在 CLI 里，hook 只是把宿主的事实交给 CLI。** 每个 hook 适配器只做"读宿主事件 → 调一个
   `python-backend hook <名字>` → 把结果翻译回宿主格式"，判定逻辑全在 `core/run_protocol` 和现有
   gate 函数里，SwarmAI 会话和三个外部宿主用同一套。
2. **CLI 能做的不靠 hook。** REFLECT 收尾、worktree 回收、token 统计都在 CLI 里做。
3. **证据来源写进记录。** 每条 `answer`、`human_answer`、`auth` 带 `source`：`host`（宿主 hook 交来的，
   主 agent 碰不到）或 `relayed`（主 agent 转交的）。REPORT 照实写。没有 hook 时 pipeline 仍然能跑完，
   只是证据弱一档，并且说出来。
4. **不给任何宿主开特例。** SwarmAI 会话也走适配器那一层（它的 hook 只是在进程里直接调同一批函数）。

## 4. 设计（按 §2 的环节）

### 4.1 适配器层：`python-backend hook <事件> --host <宿主>`

- 新子命令 `hook`：从 stdin 读宿主的事件 JSON，归一成一个内部事件（会话 id、工具名、工具输入、
  子 agent 的 prompt 和最后回答、子 agent transcript 路径、宿主名），调对应的核心函数，再把结论翻成宿主
  的输出格式（Claude / Codex 的 `hookSpecificOutput`，Kiro 的 exit 2 + stderr）。
- 每个宿主一个小翻译模块（`core/hosts/claude.py`、`codex.py`、`kiro.py`），只做字段映射，不做判定。
  SwarmAI 会话的 hook 在进程里调同一批核心函数，不经过子命令。
- `pipeline-v1 install-hooks --host <宿主> [--project | --user]`：写入该宿主的配置
  （`.claude/settings.json`、`.codex/hooks.json`、`.kiro/hooks/swarmai-pipeline.json`）。只追加自己的条目，
  不改别的；再运行一次是幂等的；`--remove` 删掉自己的条目。Codex 写完提示用户去 `/hooks` 信任。
- `create` 检查宿主的 hook 是否已装（读同一份配置文件）。没装就照常建 run，但在输出里说明哪些环节会
  降级（§4.3、§4.5），以及装 hook 的命令。

### 4.2 owner（A）

- 优先用宿主的会话 id：
  - Claude Code：SessionStart hook 往 `CLAUDE_ENV_FILE` 写 `export SWARM_APP_SESSION_ID=<session_id>`，
    之后每条 Bash 都带着；没装 hook 时 CLI 读 `CLAUDE_CODE_SESSION_ID`（Bash 里本来就有）。
  - Codex：PreToolUse(Bash) 对 pipeline 命令用 `updatedInput` 在命令前补 `SWARM_APP_SESSION_ID=<session_id>`，
    和今天 `run_owner_gate._with_session` 做的完全一样。
  - Kiro：PreToolUse 改不了命令，Bash 里也没有会话变量。
- 没有宿主会话 id 时：`create` 生成一个 run 级的 owner id，写进 run 记录并在输出里给出；之后的命令用
  `--owner <id>` 或环境变量带上。owner 的作用是"同一个 run 不被两个会话同时推进"，不是认证，
  所以这样够用。
- owner 是否还活着（接管时用）：SwarmAI 会话照旧问 daemon；外部宿主没有存活信号，改成"最后一条命令
  超过 N 分钟（建议 30）没有活动就允许接管"，接管照旧写 `owner_transfer`。

### 4.3 审查专家（B、C）

- **CLI 先把绑定写进日志。** `review-start` 发 handout 时就生成 nonce，写进 handout 事件（今天 nonce 只在
  SwarmAI 进程内存里，外部 hook 每次是新进程，读不到）。handout 的 prompt 里带一行
  `PIPELINE-HANDOUT: <nonce>`。主 agent 看得到 nonce，这没关系：回答不由它交。
- **handout 以文件交给专家**（2026-10-25-pipeline-handout-by-file）。派发的 prompt 只有 4 行，其中
  `PROMPT-FILE` 指向全文的审计 blob；答案只从首条消息就是这 4 行、且读过该文件的子 agent 记下，
  被拒的 spawn 或答案留下 `spawn_rejected` / `answer_rejected` 和原因。
- **回答由宿主交来（`source: host`）：**
  - Claude Code、Codex：SubagentStop hook，用 `agent_transcript_path` 读子 agent 收到的第一条 prompt
    （里面有 nonce）和 `last_assistant_message`，调现有的 `record_answer`。
  - Kiro：没有 SubagentStop。PostToolUse 匹配 `subagent` 标签的工具，从 `tool_input` 里取 prompt、
    从 `tool_response` 里取回答。两者都是宿主给的，主 agent 改不了。文档没写子 agent 工具的
    `tool_response` 是否是完整的最后回答，**待 T5 实测**；不是的话 Kiro 降到 relayed。
- **没装 hook（`source: relayed`）：** 新命令 `review-answer --handout <id> --answer -`，主 agent 把子 agent
  的回答原样转交。可信度和测试报告同一档。REPORT 的审查结论写"回答由 agent 转交"。
- **专家的工具和预算：** `install-hooks` 同时写入宿主的子 agent 定义（Claude `.claude/agents/pipeline-expert.md`
  只给 Read/Grep/Glob/Bash；Codex 和 Kiro 用各自的 agent 配置）。预算（每个专家 24 次调用）在 Claude 和
  Codex 用 PreToolUse 在子 agent 里计数，Kiro 做不到时不限，REPORT 记"预算未强制"。

### 4.4 人的决策和落地批准（D、E）

这是唯一一个 hook 解决不了的环节：要证明"是人答的"，需要一条 agent 自己走不通的路。候选方案（§6 问题 1）：

| 方案 | 怎么做 | agent 能不能自己走通 | 代价 |
|---|---|---|---|
| a. 宿主的问人工具 | Claude Code：agent 调 `AskUserQuestion`，PostToolUse hook 从 `tool_response` 里读答案，写 `human_answer` / `auth`（`source: host`） | 不能（答案是宿主的 UI 收的） | 只有 Claude Code 有；Codex、Kiro 没查到通用的问人工具 |
| b. 终端命令 | 人在自己的终端里运行 `python-backend pipeline-v1 approve <request>`；命令要求 `/dev/tty` 可读，并要人敲一遍 run id 的后四位 | 通常不能：agent 的 Bash 没有控制终端。但不是绝对的 | 人要切到终端；跨平台的 tty 判断有坑 |
| c. 本机确认页 | CLI 在 `127.0.0.1` 起一次性页面，URL 带一次性 token，打印给人；人在浏览器点确认 | 能（agent 可以 `curl` 那个 URL） | 防不住 agent，只是"多一步" |
| d. 不做人工通道 | 外部宿主里 `decision-request` 和 `run-land --request` 返回"需要在 SwarmAI 里完成" | — | 外部宿主里这两条路走不完 |

建议：**a + b**。Claude Code 用 a；Codex、Kiro 用 b。c 防不住 agent，不做。
`decision-request` 在没有人工通道时，照今天的规则在决策上限内由 agent 按证据选（DC-1），并记 `agent_after_cap`。

### 4.5 隔离（F）

- 把 `worktree_write_gate.judge` 接到三个宿主的 PreToolUse（Claude、Codex 用 deny；Kiro 用 exit 2）。
  规则不变：run 的写入留在自己的 worktree，不许写 `.aidlc/deliverables/`。
- 没装 hook 时不挡。落地本来就只认 worktree 的 tree，所以这只影响"agent 有没有被及时提醒"，不影响
  落地的正确性。

### 4.6 REFLECT 收尾和 worktree 回收（G）

- `land` 成功、并且 REFLECT 的经验已经由 `reflect-prepare` 交了之后，CLI 在同一个命令里同步跑
  `reflect_job.process_run`（判断是确定性的，不调用模型；有次数和时间上限），跑完写 `reflect_complete`，
  再回收这个 run 的 worktree 和 `run/` 分支。今天 REFLECT 放到 daemon 里跑，是为了不拖慢 chat，
  而 CLI 本来就不在 chat 路径上。
- SwarmAI 会话照旧可以让 daemon 在后台跑；CLI 同步跑完以后，daemon 那边看到的是已完成的 run，什么也不做。
- 中途被打断（进程被杀）：下一条 pipeline 命令（任何 run）或 daemon 启动时补跑，和今天的
  `reschedule_reflecting` 一样。

### 4.7 token（H）

- 每个宿主一个 transcript 读取器，接口相同：给会话 id 和时间窗口，返回调用次数和 token。
  - Claude Code：今天的 `transcript_usage`，从 `CLAUDE_CODE_SESSION_ID` 或 hook 的 `transcript_path` 找文件。
  - Codex：hook 的 `transcript_path` 可以拿到文件，但文档说格式不稳定。先写一个读取器，读不出就记
    `usage_not_available(reason: transcript format not recognised)`，Token Cost 是 WARN。
  - Kiro：hook 里没有 transcript 路径，记 `usage_not_available(reason: host gives no transcript)`。
- 不让 agent 自报 token。

## 5. 每个宿主最后能到哪一档

| 环节 | Claude Code（装 hook） | Codex（装 hook 并信任） | Kiro CLI / KiroCrew（装 hook） | 任何宿主（不装 hook） |
|---|---|---|---|---|
| owner | 宿主会话 id | 宿主会话 id | run 级 owner id | run 级 owner id |
| 专家回答 | host | host | host（PostToolUse） | relayed |
| 专家预算 | 强制 | 强制 | 不强制 | 不强制 |
| 人的决策 / 落地批准 | host（AskUserQuestion） | 终端命令 | 终端命令 | 终端命令 |
| 隔离提醒 | 有 | 有 | 有 | 无（落地仍只认 worktree） |
| REFLECT 收尾、回收 | CLI 同步 | CLI 同步 | CLI 同步 | CLI 同步 |
| token | 量得到 | 尽量（格式不稳定） | 量不到（WARN） | Claude 量得到，其余 WARN |

## 6. XG 定的三件事（2026-10-25）

- 人工通道用 **a + b**：Claude Code 用 `AskUserQuestion`（PostToolUse 读人的答案，T3 先实测），Codex 和 Kiro 用终端命令 `pipeline-v1 approve`。
- **没装 hook 也允许跑**：专家回答记为 `relayed`，REPORT 写明。
- `install-hooks` **默认写用户级**配置，`--project` 才写项目级。

原来的问题：


1. **人工通道**：§4.4 选 a + b（Claude 用 AskUserQuestion，其余用终端命令），还是只做 d（外部宿主里人的
   决策和人工落地不支持，只能在 SwarmAI 里做）？前者多一个子命令和 tty 判断，后者范围最小。
2. **没装 hook 时还跑不跑**：允许跑（审查回答是 relayed，REPORT 写明），还是要求先 `install-hooks`？
   允许跑覆盖面最大；要求装 hook 证据更强，但 Codex 用户每次改 hook 都要重新信任。
3. **`install-hooks` 写到哪**：默认写项目级（`.claude/settings.json` 等，会出现在用户仓库的 diff 里），
   还是默认写用户级（`~/.claude/settings.json` 等，对所有仓库生效）？建议默认用户级，`--project` 可选。

## 7. 实施顺序（每步一个 commit，测试先行，变异验证）

| 步 | 内容 | 验收 |
|---|---|---|
| T1（已做） | REFLECT 和回收在 CLI 里同步做（§4.6）。实现：`reflect_job.finish_in_cli`（`land` 或未落地收尾的同一条命令里跑 REFLECT，失败记 degraded），`process_run` 在 run 的操作锁里并在锁内重判状态；`RunEngine.release_landed_worktrees` 只删和落地 commit 完全一致的 worktree，落地后又改过的先快照进 ledger；`reflecting` 的 run 由 `resume` 收尾，`next` 指向它 | daemon 离线时 Node 和无测试样例仓库的 run 结束在 `completed`，worktree 和 `run/` 分支已回收（`test_pipeline_v1_acceptance_matrix`、`test_reflect_in_cli`） |
| T2（已做） | handout 绑定写进日志；`hook` 子命令和归一事件；SwarmAI 会话的 hook 改成调同一批函数。实现：handout 事件带 `nonce`，prompt 第二行是 `PIPELINE-HANDOUT: <run> <nonce>`，prompt 不再被 hook 改写；`hook_bridge` 变成无状态函数（`bind_spawn` 写 `spawn` 事件，只认和 handout 完全相同的 prompt；`record_answer` 从 transcript 开头读绑定，要求有对应 `spawn`，或宿主交来完整 prompt 时 `exact=True`；回答带 `source: host`）；`on_spawn` / `on_subagent_stop` 是 SwarmAI 会话和 `python-backend hook <event> --host claude` 共用的入口；子 agent transcript 读取挪到 `core/subagent_transcript.py` | 外部进程的 `hook --host claude` 记下的回答让 full run 落地（`test_host_hook_binding`）；改过的 prompt 不绑定；绑定单元测试（`test_run_protocol_hook_bridge`） |
| T3 | Claude Code 适配器 + `install-hooks --host claude` | 一个真实的 `claude -p` full run 在 Node 样例仓库从 create 到 land，专家回答 `source: host` |
| T4 | Codex 适配器 | 同上，`codex exec` |
| T5 | Kiro 适配器（PostToolUse 读子 agent 回答） | 同上，`kiro-cli chat --no-interactive` |
| T6（已做） | 无 hook 降级：`review-answer`、run 级 owner、REPORT 写证据来源。实现见 §7.1 | 不装 hook 的 full run 落地，REPORT 写 `Review answers: N of N relayed by the agent`（`test_pipeline_v1_relayed`） |
| T7 | 人工通道（按 §6 问题 1 的结论） | 决策和人工落地在每个宿主各跑一次 |
| T8 | 宿主 transcript 读取器（Codex、Kiro 尽量） | Token Cost 在 Claude 有数字，其余是 WARN 并写明原因 |

T1 不依赖任何宿主，可以先做。T3–T5 需要本机装有对应的 CLI 并登录；验收用真实 agent，不用脚本驱动的 rig。

### 7.1 T6 的实施（r5）

- **owner 从哪来**（`cli_main._session`）：`SWARM_APP_SESSION_ID` → Claude Code 的 `CLAUDE_CODE_SESSION_ID`
  （没装 hook 时 Bash 里也有）→ 都没有时 create 生成一个 uuid 当 run 级 owner。contract 记 `owner_source`
  （`host` / `host_transcript` / `run`）。**和 §4.2 不同**：run 级 owner 不加 `--owner` 参数，而是由
  `next.command` 带上 `SWARM_APP_SESSION_ID=<id>` 前缀（`next_step._cmd`），agent 照着输出的命令跑就对，
  不用每个子命令都加参数。
- **回答怎么来，create 时定死**：contract 的 `review_answers` = `host`（`SWARM_APP_SESSION_ID` 存在：只有宿主
  hook 会设它，SwarmAI 会话或装了适配器的宿主）或 `relayed`。T6 之前建的 run 没有这个字段，按 `host`。
- **`review-answer --handout <id> --answer -`**：只有 `relayed` 的 run 收，只收 owner 的，每个 handout 一次，
  写的 answer 和 hook 写的同一个函数（`hook_bridge._write`），`source: relayed`、producer `cli`
  （`answer` / `finding_occ` / `finding_status` 的 producer 因此放宽到 cli 或 hook）。**`host` 的 run 拒收**：
  不然 SwarmAI 会话里的 agent 可以绕过 hook 自己写专家回答，证据就变弱了；SwarmAI 会话里
  `SWARM_APP_SESSION_ID` 本来就删不掉（`session_link_gate`），所以也建不出 `relayed` 的 run。
- **agent 怎么知道要转交**：`INSTRUCTIONS.md` 已经 9945 / 10000 字符，没再加字。改由 CLI 输出说：create 的
  `notes` 说明要转交，`relayed` 的 run 里每个 handout 带 `answer_command`。
- **REPORT**：有转交的回答时加一行 `Review answers: M of N relayed by the agent (no host hook recorded them)`；
  `run_audit.json` 的 `details.answer_sources` 按来源计数。全是 host 时没有这一行。
- **没做的**：§4.2 的"30 分钟没动静就允许接管"。外部宿主里没有 gate，另一个会话跑命令只会拿到 `not_owner`，
  不会出错；要换会话继续，用 create 输出的 owner id。等 T3–T5 有了真实宿主再看是否需要。
- **已知的边界**：用户在没装 hook 的终端里手动设了 `SWARM_APP_SESSION_ID`，run 会被当成 `host`，审查会一直
  等 hook 的回答。T5 的 Kiro 有 hook 但设不了这个变量，到时要加自己的判断。

## 修订记录

- r1（2026-10-25）：初稿。宿主能力按 2026-10-25 的官方文档整理（§1）。
- r2（2026-10-25，XG）：人工通道 a + b；没装 hook 也允许跑；hook 默认写用户级。
- r3（2026-10-25）：T1 实施。
- r4（2026-10-25）：T2 实施。
- r5（2026-10-26）：T6 实施（§7.1）。
