# Requirements Document — Swarm Radar Sidebar (Refined)

## 1. 概述 (Overview)

**Swarm Radar** 是 SwarmAI 的右侧注意力与动作控制面板。它通过实时监控任务生命周期，将海量信息过滤为可感知的状态流，引导用户关注最紧迫的决策。本版本采用了 **Chat-Centric CRUD** 逻辑，移除复杂的 UI 按钮，转而通过自然语言和拖拽进行交互。

## 2. 核心布局架构 (Core Layout)

### 2.1 整体结构 (Global Structure)

* **定位**：独立右侧边栏，移除所有左侧导航元素和顶栏干扰。
* **滚动机制**：各语义区支持独立的 `overflow-y: auto` 滚动，确保在单区记录超过 10 条时仍可全局导航。
* **折叠功能**：各区域标题行支持点击折叠/展开，以优化垂直空间利用率。

### 2.2 语义区定义 (Semantic Zones)

1. **🔴 Needs Attention (Urgency Monitor)**:
* **内容**：逾期 ToDos、等待人工输入 (Waiting Input) 的任务及高风险待审计项。
* **视觉**：高优先级条目左侧带有加粗红色边框。


2. **🟡 In Progress (Lifecycle Status)**:
* **内容**：当前正在执行的 WIP 任务流。
* **视觉**：卡片背景带有微弱的脉冲动画，指示 AI 正在活跃执行。


3. **🟢 Completed (Recent Closure)**:
* **内容**：最近 7 天内完成的任务。
* **逻辑**：仅显示高价值或需审计的任务，其余项静默归档。


4. **🤖 Autonomous Jobs (Automated Monitor)**:
* **内容**：系统同步任务及用户定义的定时作业。
* **视觉**：显示明确的调度时间（如 `Daily at 9:00 AM`）及运行/暂停状态。



## 3. 交互规范 (Interaction Specs)

### 3.1 极简卡片设计 (Minimalist Card)

* **移除描述文字**：卡片正面仅保留标题、来源图标和核心状态标签（如 `Due Today`），移除冗长的辅助说明文字。
* **扩展功能 (Expansion)**：每张卡片右侧设有下箭头 (Chevron)，点击后向下展开显示详细元数据（如任务 ID、关联项目、上次运行摘要）。

### 3.2 Chat-Driven CRUD (核心逻辑)

* **不再提供行内按钮**：移除卡片上常驻的 `Edit`、`Delete`、`Add` 等按钮。
* **自然语言控制**：用户通过中心 Chat Window 下达指令（如“删除逾期的邮件任务”），Radar 实时同步状态。
* **拖拽即命令 (Drag-to-Command)**：
* **ToDo → Chat**: 拖入时自动填充任务上下文，触发 AI 询问执行意图。
* **WIP → Chat**: 拖入时 Chat 自动定位到该任务的活跃线程。
* **删除/修改**: 拖入 Chat 后输入“删除”或“改到明天”，实现隐式 CRUD。



## 4. 视觉设计细节 (Visual Design)

| 元素 | 规范 |
| --- | --- |
| **状态圆点** | 使用标准颜色：🔴 (Needs Action), 🟡 (WIP), 🟢 (Done)。 |
| **优先级色带** | 高优先级条目左侧 4px 红色 Accent Bar。 |
| **卡片间距** | 采用紧凑型排版，`margin-bottom: 8px`。 |
| **动画反馈** | 任务状态转换（如 WIP -> Done）需伴随平滑的滑出/消失动画。 |

---

## 5. 实施建议 (Implementation Notes)

> **Kiro 指令参考：**
> 1. **状态流同步**：确保 Radar 组件订阅全局任务状态机，任何 Chat 产生的副作用需在 200ms 内反映在 Radar 上。
> 2. **拖拽容器实现**：使用 `dnd-kit` 或类似库，定义 Chat 区域为唯一的 `DropZone`。
> 3. **动态高度管理**：实现 `Expansion` 逻辑时使用 `AnimatePresence` 确保高度切换平滑。
> 
> 

