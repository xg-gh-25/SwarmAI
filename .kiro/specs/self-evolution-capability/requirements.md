# 需求文档：Self-Evolution Capability（自进化能力）

## 简介

为 SwarmAI 赋予自进化能力——当 Agent 遇到自身无法解决的问题或能力缺口时，能够基于一套 Growth Leadership Principles（成长领导力原则）自主判断、尝试构建新能力（Skills、Scripts、CLI 工具、Marketplace 插件等），最多重试三次，若仍无法解决则向用户发起 Help Request。

这不是简单的"重试"机制，而是一个完整的自我认知→诊断→能力构建→验证→学习闭环，让 SwarmAI 从一个被动执行者进化为一个能主动成长的 Agent。

## 术语表

- **Evolution_Engine**: 自进化引擎，负责检测能力缺口、编排能力构建流程、管理重试逻辑的核心模块
- **Growth_Principles**: 成长领导力原则，一组指导 SwarmAI 自我改进决策的原则（类似 Amazon Leadership Principles）
- **Capability_Gap**: 能力缺口，Agent 在执行任务时识别出的自身无法完成的具体能力不足
- **Capability_Builder**: 能力构建器，负责根据缺口类型选择并执行具体构建策略的子模块
- **Build_Strategy**: 构建策略，解决特定能力缺口的具体方法（如创建 Skill、编写 Script、安装插件等）
- **Evolution_Attempt**: 进化尝试，一次完整的"诊断→构建→验证"循环
- **Help_Request**: 求助请求，当自进化尝试耗尽后向用户发起的结构化求助
- **Evolution_Journal**: 进化日志，记录所有自进化尝试及其结果的持久化存储
- **Capability_Registry**: 能力注册表，记录 Agent 已构建的所有自进化能力及其元数据

## 需求

### 需求 1：Growth Leadership Principles 框架

**用户故事：** 作为 SwarmAI 的开发者，我希望为 Agent 定义一套成长领导力原则，使 Agent 在面对能力缺口时能基于这些原则做出一致的、高质量的自我改进决策。

#### 验收标准

1. THE Evolution_Engine SHALL load Growth_Principles from a dedicated context file (`context/GROWTH_PRINCIPLES.md`) at session startup, alongside other context files (SOUL.md, AGENT.md 等)
2. THE Growth_Principles SHALL include at minimum the following原则类别：Bias for Action（行动偏好）、Learn and Compound（学习复利）、Earn Trust Through Competence（以能力赢得信任）、Own the Outcome（对结果负责）、Think Big but Start Small（想大做小）、Frugality（节俭，优先利用已有资源）
3. WHEN the Evolution_Engine evaluates a Capability_Gap, THE Evolution_Engine SHALL reference the applicable Growth_Principles to determine the appropriate Build_Strategy
4. THE Growth_Principles SHALL be user-editable, allowing users to add, modify, or remove principles via the context file system
5. WHEN a Growth_Principle is referenced during an Evolution_Attempt, THE Evolution_Journal SHALL record which principle guided the decision

### 需求 2：能力缺口检测（Capability Gap Detection）

**用户故事：** 作为 SwarmAI Agent，我希望能自动识别执行任务过程中遇到的能力缺口，以便触发自进化流程而非直接失败。

#### 验收标准

1. WHEN the Agent encounters a tool execution failure, THE Evolution_Engine SHALL analyze the error to determine if the failure represents a Capability_Gap (as opposed to a transient error or user input issue)
2. WHEN the Agent cannot find a matching Skill for a user request, THE Evolution_Engine SHALL classify this as a Capability_Gap of type `missing_skill`
3. WHEN the Agent encounters a command or tool that is not available in the current environment, THE Evolution_Engine SHALL classify this as a Capability_Gap of type `missing_tool`
4. WHEN the Agent lacks knowledge to complete a task after exhausting available context, THE Evolution_Engine SHALL classify this as a Capability_Gap of type `knowledge_gap`
5. THE Evolution_Engine SHALL categorize each Capability_Gap with a structured record containing: gap type, triggering context, attempted actions, error details, and timestamp
6. IF the Evolution_Engine cannot determine whether a failure is a Capability_Gap or a transient error, THEN THE Evolution_Engine SHALL default to treating the first occurrence as a transient error and only escalate to Capability_Gap on repeated failure of the same type

### 需求 3：能力构建策略编排（Build Strategy Orchestration）

**用户故事：** 作为 SwarmAI Agent，我希望根据能力缺口的类型自动选择最合适的构建策略，以便高效地弥补自身不足。

#### 验收标准

1. THE Capability_Builder SHALL support the following Build_Strategy types: `create_skill`（创建新 Skill）, `write_script`（编写辅助脚本）, `install_plugin`（从 Marketplace 安装插件）, `research_solution`（通过互联网或文档研究解决方案）, `compose_existing`（组合已有能力解决新问题）
2. WHEN a Capability_Gap of type `missing_skill` is detected, THE Capability_Builder SHALL prioritize strategies in this order: `compose_existing` → `install_plugin` → `create_skill`
3. WHEN a Capability_Gap of type `missing_tool` is detected, THE Capability_Builder SHALL prioritize strategies in this order: `install_plugin` → `write_script` → `research_solution`
4. WHEN a Capability_Gap of type `knowledge_gap` is detected, THE Capability_Builder SHALL prioritize strategies in this order: `research_solution` → `compose_existing` → `create_skill`
5. THE Capability_Builder SHALL execute each Build_Strategy within the Agent's existing security sandbox, respecting all file access and command execution restrictions defined in security_hooks.py
6. WHEN the Capability_Builder selects `create_skill` as the Build_Strategy, THE Capability_Builder SHALL use the existing Skill Builder skill (s_skill-builder) workflow to generate a well-structured SKILL.md
7. WHEN the Capability_Builder selects `install_plugin` as the Build_Strategy, THE Capability_Builder SHALL use the existing PluginManager to search and install from configured marketplaces

### 需求 4：重试机制与升级逻辑（Retry and Escalation）

**用户故事：** 作为 SwarmAI 的用户，我希望 Agent 在遇到问题时能智能重试最多三次，每次尝试不同的策略，若仍无法解决则清晰地向我求助，而非无限循环或静默失败。

#### 验收标准

1. THE Evolution_Engine SHALL allow a maximum of 3 Evolution_Attempts per Capability_Gap
2. WHEN an Evolution_Attempt fails, THE Evolution_Engine SHALL select a different Build_Strategy for the next attempt, following the priority order defined for the gap type
3. WHEN an Evolution_Attempt fails, THE Evolution_Engine SHALL record the failure reason and the strategy used to the Evolution_Journal before proceeding to the next attempt
4. IF all 3 Evolution_Attempts for a Capability_Gap fail, THEN THE Evolution_Engine SHALL generate a Help_Request and present it to the user
5. THE Help_Request SHALL contain: a summary of the original task, the identified Capability_Gap, all attempted strategies with their failure reasons, and a suggested next step for the user
6. WHILE an Evolution_Attempt is in progress, THE Evolution_Engine SHALL stream status updates to the frontend via the existing SSE event system so the user can observe the self-evolution process
7. WHEN the user responds to a Help_Request with guidance, THE Evolution_Engine SHALL incorporate the user's input and optionally trigger additional Evolution_Attempts based on the new information

### 需求 5：能力验证（Capability Verification）

**用户故事：** 作为 SwarmAI Agent，我希望在构建新能力后能自动验证其有效性，以确保新能力确实解决了原始问题。

#### 验收标准

1. WHEN a Build_Strategy completes successfully, THE Evolution_Engine SHALL verify the new capability by re-attempting the original task that triggered the Capability_Gap
2. WHEN the verification succeeds, THE Evolution_Engine SHALL register the new capability in the Capability_Registry with metadata including: capability type, creation timestamp, triggering gap, verification status, and usage count
3. IF the verification fails, THEN THE Evolution_Engine SHALL treat the Build_Strategy as failed and proceed to the next Evolution_Attempt
4. THE Evolution_Engine SHALL execute verification within a timeout of 60 seconds to prevent infinite execution loops
5. WHEN a newly created Skill is verified successfully, THE Capability_Registry SHALL record the Skill's folder name and description for future reference

### 需求 6：进化日志与学习（Evolution Journal and Learning）

**用户故事：** 作为 SwarmAI 的用户，我希望能查看 Agent 的自进化历史，了解它学到了什么、构建了什么能力，以便我能监督和引导它的成长方向。

#### 验收标准

1. THE Evolution_Journal SHALL persist all Evolution_Attempts to the SQLite database with fields: id, session_id, gap_type, gap_description, strategy_used, attempt_number, outcome (success/failure), failure_reason, capability_created, principle_applied, duration_ms, and created_at
2. WHEN a session starts, THE Evolution_Engine SHALL review recent Evolution_Journal entries to identify recurring Capability_Gaps that may indicate systemic issues
3. THE Evolution_Engine SHALL write a summary of significant evolution events to the existing DailyActivity log (`Knowledge/DailyActivity/YYYY-MM-DD.md`) for integration with the memory distillation system
4. THE Evolution_Journal SHALL be queryable via a REST API endpoint, supporting filters by gap_type, outcome, date range, and session_id
5. WHEN the same Capability_Gap type occurs more than 3 times across different sessions, THE Evolution_Engine SHALL proactively suggest to the user that a permanent solution (e.g., a dedicated Skill or tool installation) may be needed

### 需求 7：能力注册与复用（Capability Registry and Reuse）

**用户故事：** 作为 SwarmAI Agent，我希望能记住并复用之前自进化构建的能力，避免重复构建相同的能力。

#### 验收标准

1. THE Capability_Registry SHALL store capability metadata in the SQLite database with fields: id, capability_type (skill/script/plugin/knowledge), name, description, source_gap_type, created_at, last_used_at, usage_count, and is_active
2. WHEN a new Capability_Gap is detected, THE Evolution_Engine SHALL first query the Capability_Registry for existing capabilities that match the gap type and description before initiating a new Evolution_Attempt
3. WHEN a registered capability matches the current Capability_Gap, THE Evolution_Engine SHALL attempt to apply the existing capability before trying to build a new one
4. THE Capability_Registry SHALL track usage_count and last_used_at for each registered capability, incrementing on each successful application
5. WHEN a registered capability fails to resolve a Capability_Gap it previously solved, THE Evolution_Engine SHALL mark the capability as `needs_update` in the Capability_Registry and proceed with normal Evolution_Attempt flow

### 需求 8：安全与边界控制（Safety and Boundary Control）

**用户故事：** 作为 SwarmAI 的用户，我希望 Agent 的自进化行为受到严格的安全约束，确保它不会在自我改进过程中执行危险操作或超出授权范围。

#### 验收标准

1. THE Evolution_Engine SHALL execute all Build_Strategies within the Agent's existing security sandbox, inheriting all restrictions from security_hooks.py (file access control, dangerous command blocking, skill access checking)
2. THE Evolution_Engine SHALL require explicit user approval before executing any Build_Strategy that involves: installing external plugins, executing downloaded scripts, or modifying system-level configurations
3. WHILE an Evolution_Attempt is in progress, THE Evolution_Engine SHALL enforce a resource budget: maximum 60 seconds per attempt, maximum 3 file creations per attempt, and maximum 5 command executions per attempt
4. IF a Build_Strategy attempts to access resources outside the Agent's sandbox, THEN THE Evolution_Engine SHALL abort the attempt, log the violation to the Evolution_Journal, and proceed to the next strategy
5. THE Evolution_Engine SHALL tag all self-created capabilities with an `auto_generated` flag in the Capability_Registry, distinguishing them from user-created or system-provided capabilities
6. WHEN the user disables the self-evolution feature via settings, THE Evolution_Engine SHALL immediately cease all Evolution_Attempts and fall back to standard error reporting behavior

### 需求 9：前端可观测性（Frontend Observability）

**用户故事：** 作为 SwarmAI 的用户，我希望能在聊天界面中实时看到 Agent 的自进化过程，包括它在尝试什么、为什么这样做、结果如何，以便我能理解和信任它的自主行为。

#### 验收标准

1. WHEN an Evolution_Attempt begins, THE Evolution_Engine SHALL emit an SSE event of type `evolution_start` containing: gap_type, gap_description, strategy_selected, attempt_number, and principle_applied
2. WHEN an Evolution_Attempt completes, THE Evolution_Engine SHALL emit an SSE event of type `evolution_result` containing: outcome, duration_ms, capability_created (if any), and failure_reason (if failed)
3. WHEN a Help_Request is generated, THE Evolution_Engine SHALL emit an SSE event of type `evolution_help_request` that triggers the existing `ask_user_question` UI flow, presenting the structured Help_Request content
4. THE frontend SHALL render evolution events in the chat message stream as distinct, collapsible UI elements that show the self-evolution process without cluttering the main conversation
5. THE frontend SHALL display a summary badge or indicator on the Swarm Radar panel showing the count of successful evolutions in the current session

### 需求 10：配置与开关（Configuration and Feature Toggle）

**用户故事：** 作为 SwarmAI 的用户，我希望能控制自进化功能的开关和行为参数，以便根据我的信任级别和使用场景调整 Agent 的自主程度。

#### 验收标准

1. THE Evolution_Engine SHALL read its configuration from `~/.swarm-ai/config.json` under a dedicated `evolution` key
2. THE Evolution_Engine SHALL support the following configuration options: `enabled` (boolean, default: true), `max_retries` (integer, default: 3), `auto_approve_skills` (boolean, default: false), `auto_approve_scripts` (boolean, default: false), `auto_approve_plugins` (boolean, default: false)
3. WHEN `auto_approve_skills` is set to false, THE Evolution_Engine SHALL request user confirmation before creating any new Skill via the self-evolution process
4. WHEN `auto_approve_plugins` is set to false, THE Evolution_Engine SHALL request user confirmation before installing any plugin via the self-evolution process
5. THE frontend Settings page SHALL include a "Self-Evolution" section allowing users to toggle the feature and adjust all configuration options
6. WHEN the `enabled` configuration changes from true to false during an active session, THE Evolution_Engine SHALL complete any in-progress Evolution_Attempt but SHALL NOT initiate new ones
