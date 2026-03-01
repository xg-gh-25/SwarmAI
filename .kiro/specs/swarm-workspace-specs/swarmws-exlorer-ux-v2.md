# Requirements Document — SwarmWS Explorer Refined UX

## 1. 概述 (Overview)

本规范定义了 SwarmWS Explorer 的最终视觉与交互标准。目标是将传统的“文件树”进化为“语义驱动的 AI 协作空间”，通过视觉层级区分**系统规则、共享知识与执行任务**。

## 2. 核心布局架构 (Core Layout)

### 2.1 头部：System Context (只读元数据区)

* **视觉表现**：紧贴 `SwarmWS` 标题下方。
* **组件化**：由 3 个横向排列的 **Pill-shaped Tags (Chips)** 组成：`context-L0.md`, `context-L1.md`, `system-prompts.md`。
* **状态标识**：区域末尾显示一个微型灰色锁定图标 (🔒)。
* **排版约束**：
* 取消文件夹图标。
* 不占用完整行高，采用 `flex-wrap` 布局。
* 移除 Hover 态背景色，明确其“非管理”属性。



### 2.2 中部：Shared Knowledge (知识资产区)

* **视觉表现**：采用 **Card-based Style**。使用极淡的背景色（如 `#F8F9FA`）包裹整个区域。
* **包含路径**：`Knowledge/Memory`, `Knowledge/Knowledge Base`, `Knowledge/Notes`。
* **操作逻辑**：
* **Zone Title**: "SHARED KNOWLEDGE"。
* **Hover Actions**: 鼠标悬停在标题行时，右侧显示透明度为 50% 的 `+` 按钮。
* **Dropdown**: 点击 `+` 弹出 `[New Note Folder, New File, Search Knowledge]`。



### 2.3 底部：Active Projects (任务执行区)

* **视觉表现**：透明背景，动态高度。
* **功能特性**：
* **Pinning (钉选)**：支持项目置顶，并在侧边显示垂直 `PINNED` 标签。
* **Status Indicator**：项目名左侧或右侧带有状态圆点（🟢 Active / ⚪ Idle）。
* **Sub-text Preview**：项目文件夹下方以 10px 灰色文字显示该项目的 `L0 Context` 摘要。
* **Compact View**：当项目总数 > 10 时，提供紧凑视图模式切换，隐藏摘要并缩小行间距。



---

## 3. 交互与导航逻辑 (UX Logic)

### 3.1 渐进式披露 (Progressive Disclosure)

* **默认状态**：`Knowledge` 文件夹保持展开，`Projects` 仅展开当前活跃项目。
* **智能收起**：当用户在 `Projects` 间切换时，系统自动收起非活跃且非钉选的项目。

### 3.2 动作触发 (Action Triggers)

* **新增项目**：在 `ACTIVE PROJECTS` 区域标题的 `+` 按钮中触发。
* **上下文感知**：右键菜单项必须基于当前所属区域进行过滤（如在 Knowledge 区不显示 "Run Task"）。

### 3.3 搜索与过滤 (Filter Interface)

* 在 `ACTIVE PROJECTS` 标题下方集成一个微型搜索框（仅在 Hover 时或点击图标后出现），支持对项目名进行模糊匹配。

---

## 4. 技术实施规范 (Technical Specs)

### 4.1 视觉变量 (CSS Variables)

```css
:root {
  --explorer-bg: #FFFFFF;
  --system-chip-bg: #F1F3F4;
  --knowledge-card-bg: #F8F9FA;
  --active-project-accent: #FFF7ED; /* 淡橙色背景用于选中态 */
  --status-active: #22C55E;
  --status-idle: #94A3B8;
}

```

### 4.2 适配器逻辑 (Adapter Logic)

前端渲染层需要通过 `PathAdapter` 将后端的物理结构重新映射：

1. **Extract Root Files**: 将根目录下的特定 `.md` 文件提取并传递给 `<SystemContextChips />`。
2. **Filter Knowledge**: 将物理路径包含 `Knowledge/` 的 Node 聚合到 `<SharedKnowledgeCard />`。
3. **Map Projects**: 将物理路径包含 `Projects/` 的 Node 渲染至 `<ActiveProjectList />`。

---

## 5. 最终视觉预览 (Final Mockup Ref)

