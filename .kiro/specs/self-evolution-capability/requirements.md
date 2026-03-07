# 需求文档：Self-Evolution Capability v2（自进化能力）

## 简介

为 SwarmAI 赋予自进化能力——不仅在 Agent 遇到失败时被动响应，更能主动识别优化机会、检测卡死状态并自主逃逸。基于三触发模型（Reactive / Proactive / Stuck），Agent 能够自主判断、尝试构建新能力（Skills、Scripts、CLI 工具等），最多重试三次，若仍无法解决则向用户发起 Help Request。

核心设计理念：
- **三触发模型**：被动响应（失败）、主动优化（可改进）、卡死逃逸（无进展）
- **跨会话持久化**：通过 EVOLUTION.md 实现会话 A 构建的能力在会话 B 自动可用
- **文件系统优先**：所有进化数据存储于 EVOLUTION.md（单一数据源），不引入额外数据库表
- **轻量直接**：最小化架构复杂度，复用已有基础设施（context 文件系统、DailyActivity、locked_write）

## 术语表

- **Evolution_Engine**: 自进化引擎，负责检测三类触发信号、编排能力构建流程、管理重试逻辑的核心模块
- **Growth_Principles**: 成长领导力原则，一组指导 SwarmAI 自我改进决策的原则（存储于 `.context/GROWTH_PRINCIPLES.md`）
- **Capability_Gap**: 能力缺口，Agent 在执行任务时识别出的自身无法完成的具体能力不足（Reactive 触发）
- **Optimization_Opportunity**: 优化机会，Agent 识别出当前方法可行但存在更优方案的情况（Proactive 触发）
- **Stuck_State**: 卡死状态，Agent 检测到自身在当前方法上无进展、反复循环的状态（Stuck 触发）
- **Trigger_Type**: 触发类型，三种进化触发之一：`reactive`（被动响应）、`proactive`（主动优化）、`stuck`（卡死逃逸）
- **Capability_Builder**: 能力构建器，负责根据缺口类型和触发类型选择并执行具体构建策略的子模块
- **Build_Strategy**: 构建策略，解决特定能力缺口的具体方法（如创建 Skill、编写 Script、安装工具等）
- **Evolution_Attempt**: 进化尝试，一次完整的"诊断 → 构建 → 验证"循环
- **Help_Request**: 求助请求，当自进化尝试耗尽后向用户发起的结构化求助
- **EVOLUTION_MD**: 进化注册表（单一数据源），存储于 `.context/EVOLUTION.md`，记录所有已构建能力、优化经验、失败教训和进化历史
- **Stuck_Detector**: 卡死检测器，监控 Agent 行为模式以识别 Stuck_State 的子模块
- **Escape_Protocol**: 逃逸协议，当检测到 Stuck_State 时执行的策略切换流程

## 需求

### 需求 1：Growth Leadership Principles 框架

**用户故事：** 作为 SwarmAI 的开发者，我希望为 Agent 定义一套成长领导力原则，使 Agent 在面对能力缺口、优化机会或卡死状态时能基于这些原则做出一致的、高质量的自我改进决策。

#### 验收标准

1. THE Evolution_Engine SHALL load Growth_Principles from `.context/GROWTH_PRINCIPLES.md` at session startup, alongside other context 文件（SOUL.md、AGENT.md 等）
2. THE Growth_Principles SHALL include at minimum the following 原则：Try before you ask（先试再问）、Reuse before you build（先复用再构建）、Small fix over big system（小修复优于大系统）、Verify before you declare（先验证再宣告）、Leave a trail（留下痕迹）、Know when to stop（知道何时停止）、If it works but it's ugly, make it better（能用但丑，就改进它）、If you're stuck, step back and switch（卡住了就退一步换方向）
3. WHEN the Evolution_Engine evaluates any Trigger_Type (reactive, proactive, or stuck), THE Evolution_Engine SHALL reference the applicable Growth_Principles to determine the appropriate Build_Strategy
4. THE Growth_Principles SHALL be user-editable, allowing users to add, modify, or remove principles via the context file system
5. WHEN a Growth_Principle is referenced during an Evolution_Attempt, THE EVOLUTION_MD SHALL record which principle guided the decision

### 需求 2：三触发模型（Three-Trigger Model）

**用户故事：** 作为 SwarmAI Agent，我希望能通过三种不同的触发机制（被动响应、主动优化、卡死逃逸）来启动自进化流程，而非仅在失败时才触发。

#### 验收标准

1. THE Evolution_Engine SHALL support three distinct Trigger_Types: `reactive`（被动响应——某事失败或缺失）, `proactive`（主动优化——当前方法可行但存在更优方案）, `stuck`（卡死逃逸——无进展、反复循环）
2. WHEN the Agent encounters a tool execution failure, a missing Skill, or a missing command, THE Evolution_Engine SHALL classify this as a `reactive` trigger and initiate the reactive evolution loop
3. WHEN the Agent detects an Optimization_Opportunity, THE Evolution_Engine SHALL classify this as a `proactive` trigger and initiate the proactive evolution loop
4. WHEN the Stuck_Detector identifies a Stuck_State, THE Evolution_Engine SHALL classify this as a `stuck` trigger and initiate the stuck escape protocol
5. THE Evolution_Engine SHALL categorize each trigger event with a structured record containing: trigger_type, triggering context, detected signals, timestamp, and session_id
6. WHEN multiple trigger types are detected simultaneously, THE Evolution_Engine SHALL prioritize in order: `stuck`（最高优先级）→ `reactive` → `proactive`

### 需求 3：Reactive 触发——能力缺口检测（Reactive Trigger: Capability Gap Detection）

**用户故事：** 作为 SwarmAI Agent，我希望能自动识别执行任务过程中遇到的能力缺口（工具失败、Skill 缺失、命令不可用），以便触发被动响应式自进化流程。

#### 验收标准

1. WHEN the Agent encounters a tool execution failure, THE Evolution_Engine SHALL analyze the error to determine if the failure represents a Capability_Gap (as opposed to a transient error or user input issue)
2. WHEN the Agent cannot find a matching Skill for a user request, THE Evolution_Engine SHALL classify this as a Capability_Gap of type `missing_skill`
3. WHEN the Agent encounters a command or tool that is not available in the current environment, THE Evolution_Engine SHALL classify this as a Capability_Gap of type `missing_tool`
4. WHEN the Agent lacks knowledge to complete a task after exhausting available context, THE Evolution_Engine SHALL classify this as a Capability_Gap of type `knowledge_gap`
5. THE Evolution_Engine SHALL categorize each Capability_Gap with a structured record containing: gap_type, triggering context, attempted actions, error details, and timestamp
6. IF the Evolution_Engine cannot determine whether a failure is a Capability_Gap or a transient error, THEN THE Evolution_Engine SHALL default to treating the first occurrence as a transient error and only escalate to Capability_Gap on repeated failure of the same type

### 需求 4：Proactive 触发——优化机会检测（Proactive Trigger: Optimization Opportunity Detection）

**用户故事：** 作为 SwarmAI Agent，我希望能在当前方法可行但不够优的情况下主动识别优化机会，以便持续改进自身工作方式而非仅在失败时才进化。

#### 验收标准

**MVP（初始实现）：**

1. WHEN the Agent recognizes a pattern from EVOLUTION_MD that describes a better approach for the current task, THE Evolution_Engine SHALL classify this as an Optimization_Opportunity of type `known_better_approach`
2. WHEN the Agent reads MEMORY.md or DailyActivity logs and finds a lesson applicable to the current task, THE Evolution_Engine SHALL classify this as an Optimization_Opportunity of type `applicable_lesson`
3. THE Evolution_Engine SHALL record each Optimization_Opportunity with: opportunity_type, current_approach_description, proposed_improvement, and source_reference (EVOLUTION_MD entry ID or MEMORY.md section)

**Future（后续增强）：**

4. WHEN the Agent detects itself performing repetitive manual steps that could be automated (same sequence of 3+ tool calls repeated in a session), THE Evolution_Engine SHALL classify this as an Optimization_Opportunity of type `automation_candidate`
5. WHEN the Agent is using a workaround (indirect multi-step approach) where a direct solution exists, THE Evolution_Engine SHALL classify this as an Optimization_Opportunity of type `workaround_detected`
6. WHEN the Agent identifies a known best practice being violated in the current approach, THE Evolution_Engine SHALL classify this as an Optimization_Opportunity of type `best_practice_violation`

### 需求 5：Stuck 触发——卡死检测与逃逸协议（Stuck Trigger: Detection and Escape Protocol）

**用户故事：** 作为 SwarmAI Agent，我希望能检测到自身陷入无进展的循环状态，并自动执行逃逸协议切换策略，而非无限循环浪费时间。

#### 验收标准

1. WHEN the Agent produces the same error output 2 or more times consecutively, THE Stuck_Detector SHALL classify this as a Stuck_State with signal `repeated_error`
2. WHEN the Agent edits the same file more than 3 times without measurable progress toward the goal, THE Stuck_Detector SHALL classify this as a Stuck_State with signal `rewrite_loop`
3. WHEN the Agent executes more than 5 consecutive tool calls without producing user-visible output or progress, THE Stuck_Detector SHALL classify this as a Stuck_State with signal `silent_tool_chain`
4. WHEN the Agent reverts its own changes (undoing a previous edit), THE Stuck_Detector SHALL classify this as a Stuck_State with signal `self_revert`
5. WHEN the Agent tries cosmetic variations of a previously failing strategy (same approach with minor differences), THE Stuck_Detector SHALL classify this as a Stuck_State with signal `cosmetic_retry`
6. WHEN a Stuck_State is detected, THE Escape_Protocol SHALL execute the following sequence: (a) stop the current approach immediately, (b) generate a summary of what has been tried and why each attempt failed, (c) select a fundamentally different strategy (e.g., building → research, scripting → find existing tool, one language → different language, complex → simplest possible version), (d) execute the new strategy
7. IF the Escape_Protocol's first strategy switch fails, THEN THE Evolution_Engine SHALL enter the stuck evolution loop (maximum 3 attempts with fundamentally different approaches)
8. IF all 3 stuck evolution attempts fail, THEN THE Evolution_Engine SHALL report to the user with full context: original goal, all approaches tried, failure reasons for each, and the Agent's assessment of why the task is blocked

### 需求 6：按触发类型的进化循环策略与重试（Per-Trigger Evolution Loop Strategies and Retry）

**用户故事：** 作为 SwarmAI Agent，我希望根据不同的触发类型执行不同的进化策略序列，每次尝试不同策略，最多三次，若仍无法解决则向用户求助。

#### 验收标准

1. WHEN a `reactive` trigger (Capability_Gap) is detected, THE Capability_Builder SHALL execute strategies in this order: `compose_existing`（组合已有能力）→ `build_new`（构建新能力：Skill 或 Script）→ `research_and_build`（研究解决方案后构建）
2. WHEN a `proactive` trigger (Optimization_Opportunity) is detected, THE Capability_Builder SHALL execute strategies in this order: `optimize_in_place`（就地优化当前方法）→ `build_replacement`（构建替代方案）→ `research_best_practice_and_rebuild`（研究最佳实践后重建）
3. WHEN a `stuck` trigger (Stuck_State) is detected, THE Capability_Builder SHALL execute strategies in this order: `completely_different_approach`（完全不同的方法）→ `simplify_to_mvp`（简化到最小可行版本）→ `research_and_new_approach`（从零研究全新方案）
4. THE Evolution_Engine SHALL allow a maximum of 3 Evolution_Attempts per trigger event, regardless of Trigger_Type
5. WHEN an Evolution_Attempt fails, THE Evolution_Engine SHALL select the next strategy in the sequence for the corresponding Trigger_Type before proceeding to the next attempt
6. WHEN the Capability_Builder selects `compose_existing` or `optimize_in_place`, THE Capability_Builder SHALL first query EVOLUTION_MD for relevant past evolutions and optimizations before attempting the strategy
7. WHEN the Capability_Builder selects `build_new` or `build_replacement`, THE Capability_Builder SHALL create Skills directly by writing a well-structured SKILL.md to `.claude/skills/s_xxx/`, or write scripts to `.swarm-ai/scripts/` for script-based solutions
8. WHEN an Evolution_Attempt fails, THE Evolution_Engine SHALL record the failure to the Failed Evolutions section of EVOLUTION_MD (with F-ID) including: strategy used, failure reason, and lesson learned
9. IF all 3 Evolution_Attempts for a trigger event fail, THEN THE Evolution_Engine SHALL generate a Help_Request containing: original task summary, trigger type and details, all attempted strategies with failure reasons, and a suggested next step for the user
10. WHEN the user responds to a Help_Request with guidance, THE Evolution_Engine SHALL incorporate the user's input and optionally trigger additional Evolution_Attempts based on the new information

### 需求 7：跨会话持久化——EVOLUTION.md 注册表（Cross-Session Persistence via EVOLUTION.md）

**用户故事：** 作为 SwarmAI 的用户，我希望 Agent 在一个会话中构建的能力能在后续会话中自动可用，无需重复构建，实现真正的跨会话成长。

#### 验收标准

1. THE Evolution_Engine SHALL maintain EVOLUTION_MD at `.context/EVOLUTION.md` as the single source of truth for all evolution data — capabilities built, optimizations learned, failed evolutions, and evolution history
2. WHEN a new capability is successfully built and verified, THE Evolution_Engine SHALL append an entry to EVOLUTION_MD with the following fields: sequential ID (E001, E002...), trigger_type (reactive/proactive/stuck), capability_type (skill/script/tool/knowledge), name, description, location (file path or system location), usage_instructions, when_to_use (critical for future session matching), created_at, usage_count, status (active/deprecated), and auto_generated flag
3. WHEN a session starts, THE Evolution_Engine SHALL read EVOLUTION_MD and index all active entries so that capabilities built in previous sessions are immediately available for matching against current tasks
4. THE EVOLUTION_MD SHALL include an "Optimizations Learned" section with sequential IDs (O001, O002...) recording: optimization description, context, before/after comparison, and applicable scenarios
5. THE EVOLUTION_MD SHALL include a "Failed Evolutions" section with sequential IDs (F001, F002...) recording: what was attempted, why it failed, lessons learned, and alternative approaches to try
6. THE Evolution_Engine SHALL route persisted capabilities to the correct location based on capability_type: Skills to `.claude/skills/s_xxx` (auto-loaded by existing skill loading), Scripts to `.swarm-ai/scripts/xxx.py` (referenced via EVOLUTION_MD, called via Bash), installed tools to system PATH (via brew/pip/npm), knowledge and patterns to EVOLUTION_MD entries (read at session start)
7. WHEN the Agent in a new session encounters a task matching a `when_to_use` field in EVOLUTION_MD, THE Evolution_Engine SHALL automatically apply the corresponding capability without re-triggering the evolution loop
8. THE Evolution_Engine SHALL increment the usage_count in EVOLUTION_MD each time a persisted capability is successfully applied in any session
9. THE Evolution_Engine SHALL write to EVOLUTION_MD using the existing `locked_write.py` mechanism (same as MEMORY.md) to prevent concurrent write corruption
10. WHEN an EVOLUTION_MD entry has not been used (usage_count unchanged) for 30 or more days, THE Evolution_Engine SHALL mark it as `deprecated` during session startup review
11. THE Evolution_Engine SHALL write a summary of significant evolution events to the existing DailyActivity log (`Knowledge/DailyActivity/YYYY-MM-DD.md`) for integration with the memory distillation system

### 需求 8：能力验证（Capability Verification）

**用户故事：** 作为 SwarmAI Agent，我希望在构建新能力后能自动验证其有效性，以确保新能力确实解决了原始问题。

#### 验收标准

1. WHEN a Build_Strategy completes successfully, THE Evolution_Engine SHALL verify the new capability by re-attempting the original task that triggered the evolution
2. WHEN the verification succeeds, THE Evolution_Engine SHALL register the new capability in EVOLUTION_MD with full metadata and set status to `active`
3. IF the verification fails, THEN THE Evolution_Engine SHALL treat the Build_Strategy as failed and proceed to the next Evolution_Attempt
4. THE Evolution_Engine SHALL execute verification within a configurable timeout (default: 120 seconds, configurable via `evolution.verification_timeout_seconds` in config.json) to prevent infinite execution loops
5. WHEN a newly created Skill is verified successfully, THE EVOLUTION_MD entry SHALL record the Skill's folder path (`.claude/skills/s_xxx`) and a concise description for future session matching

### 需求 9：前端可观测性（Frontend Observability）

**用户故事：** 作为 SwarmAI 的用户，我希望能在聊天界面中实时看到 Agent 的自进化过程，包括触发类型、尝试策略、结果如何，以便我能理解和信任它的自主行为。

#### 验收标准

1. WHEN an Evolution_Attempt begins, THE Evolution_Engine SHALL emit an SSE event of type `evolution_start` containing: trigger_type, gap_description or opportunity_description or stuck_signals, strategy_selected, attempt_number, and principle_applied
2. WHEN an Evolution_Attempt completes, THE Evolution_Engine SHALL emit an SSE event of type `evolution_result` containing: outcome, duration_ms, capability_created (if any), evolution_id (E-ID/O-ID/F-ID), and failure_reason (if failed)
3. WHEN a Stuck_State is detected, THE Evolution_Engine SHALL emit an SSE event of type `evolution_stuck_detected` containing: detected signals, summary of what has been tried, and the escape strategy selected
4. WHEN a Help_Request is generated, THE Evolution_Engine SHALL emit an SSE event of type `evolution_help_request` that triggers the existing `ask_user_question` UI flow, presenting the structured Help_Request content
5. THE frontend SHALL render evolution events in the chat message stream as distinct, collapsible UI elements that show the self-evolution process without cluttering the main conversation
6. THE frontend SHALL display a summary badge or indicator on the Swarm Radar panel showing the count of successful evolutions in the current session, broken down by trigger_type

### 需求 10：配置与开关（Configuration and Feature Toggle）

**用户故事：** 作为 SwarmAI 的用户，我希望能控制自进化功能的开关和行为参数，以便根据我的信任级别和使用场景调整 Agent 的自主程度。

#### 验收标准

1. THE Evolution_Engine SHALL read its configuration from `~/.swarm-ai/config.json` under a dedicated `evolution` key, using the existing AppConfigManager
2. THE Evolution_Engine SHALL support the following configuration options: `enabled` (boolean, default: true), `max_retries` (integer, default: 3), `verification_timeout_seconds` (integer, default: 120), `auto_approve_skills` (boolean, default: false), `auto_approve_scripts` (boolean, default: false), `auto_approve_installs` (boolean, default: false), `proactive_enabled` (boolean, default: true), `stuck_detection_enabled` (boolean, default: true)
3. WHEN `auto_approve_skills` is set to false, THE Evolution_Engine SHALL request user confirmation before creating any new Skill via the self-evolution process
4. WHEN `auto_approve_installs` is set to false, THE Evolution_Engine SHALL request user confirmation before installing any package via pip, npm, or brew
5. WHEN `proactive_enabled` is set to false, THE Evolution_Engine SHALL disable Proactive trigger detection while keeping Reactive and Stuck triggers active
6. WHEN `stuck_detection_enabled` is set to false, THE Evolution_Engine SHALL disable Stuck_Detector while keeping Reactive and Proactive triggers active
7. THE frontend Settings page SHALL include a "Self-Evolution" section allowing users to toggle the feature and adjust all configuration options including per-trigger-type toggles
8. WHEN the `enabled` configuration changes from true to false during an active session, THE Evolution_Engine SHALL complete any in-progress Evolution_Attempt but SHALL NOT initiate new ones

### 需求 11：成功标准（Success Criteria）

**用户故事：** 作为 SwarmAI 的开发者，我希望有明确的、可验证的功能性标准来衡量自进化能力是否达到预期效果。

#### 验收标准

1. THE Evolution_Engine SHALL demonstrate reactive evolution capability by autonomously resolving at least one capability gap (tool failure, missing skill, or missing command) without user intervention in integration testing
2. THE Evolution_Engine SHALL demonstrate proactive evolution capability by detecting and acting on at least one optimization opportunity (from EVOLUTION_MD or MEMORY.md pattern matching) during a test session containing intentionally suboptimal patterns
3. THE Stuck_Detector SHALL demonstrate stuck detection capability by identifying a Stuck_State and THE Escape_Protocol SHALL switch to a fundamentally different approach within 2 minutes without user intervention
4. THE Evolution_Engine SHALL demonstrate cross-session persistence by automatically loading and applying a capability from EVOLUTION_MD in a new session on the first matching task, without re-triggering the evolution loop
5. THE Evolution_Engine SHALL enforce the 3-attempt hard stop with 100% reliability, ceasing evolution attempts after 3 failures regardless of trigger type
6. THE EVOLUTION_MD SHALL maintain accurate entries after 5 successful evolutions across sessions, with correct IDs (E-IDs/O-IDs/F-IDs), locations, usage instructions, and when_to_use fields

## EVOLUTION.md 参考格式

以下为 EVOLUTION_MD 的参考结构（单一数据源，无需额外数据库），供设计阶段使用：

```markdown
# SwarmAI Evolution Registry

## Capabilities Built

### E001 | reactive | skill | 2026-03-07
- **Name**: auto-git-conflict-resolver
- **Description**: 自动解决简单的 git merge conflicts
- **Location**: .claude/skills/s_auto-git-conflict-resolver
- **Usage**: 当检测到 git conflict 时自动调用
- **When to Use**: git merge/rebase 产生 conflict 且 conflict 为简单的行级冲突
- **Principle Applied**: Reuse before you build
- **Usage Count**: 3
- **Status**: active
- **Auto Generated**: true

### E002 | proactive | script | 2026-03-07
- **Name**: batch-file-renamer
- **Description**: 批量重命名文件的优化脚本，替代逐个 mv 命令
- **Location**: .swarm-ai/scripts/batch_rename.py
- **Usage**: `python3 .swarm-ai/scripts/batch_rename.py --pattern "*.txt" --prefix "new_"`
- **When to Use**: 需要重命名 3 个以上文件时
- **Principle Applied**: If it works but it's ugly, make it better
- **Usage Count**: 1
- **Status**: active
- **Auto Generated**: true

### E003 | stuck | knowledge | 2026-03-07
- **Name**: xml-use-lxml
- **When to Use**: Any XML parsing task
- **Lesson**: Never use regex for XML. Use `lxml.etree` — install via `pip install lxml`
- **Principle Applied**: If you're stuck, step back and switch
- **Usage Count**: 5
- **Status**: active
- **Auto Generated**: true

## Optimizations Learned

### O001 | 2026-03-07
- **Optimization**: 使用 ripgrep 替代 grep 进行大规模代码搜索
- **Context**: 在 10000+ 文件的项目中搜索
- **Before**: grep -r 耗时 >30s
- **After**: rg 耗时 <2s
- **When Applicable**: 项目文件数 >1000 时

### O002 | 2026-03-07
- **Optimization**: Multi-file operations use parallel execution
- **Pattern**: When processing >5 files, use xargs or Python multiprocessing
- **Before**: Sequential processing, 45s for 20 files
- **After**: Parallel processing, 8s for 20 files

## Failed Evolutions

### F001 | reactive | 2026-03-07
- **Attempted**: 安装 custom linter 作为 npm global package
- **Strategy**: install_plugin
- **Why Failed**: 权限不足，npm global 目录不可写
- **Lesson**: 优先使用 npx 或 local install 而非 global install
- **Alternative**: 使用 npx 直接运行或添加到项目 devDependencies
```
