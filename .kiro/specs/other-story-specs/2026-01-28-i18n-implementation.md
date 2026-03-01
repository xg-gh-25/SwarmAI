# i18n Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Chinese/English bilingual support to Owork desktop app with Chinese as default language.

**Architecture:** Use react-i18next for translations. All UI text will be extracted to JSON files (`zh.json`, `en.json`). Language preference stored in localStorage. Professional terms (Agent, Skill, MCP, Plugin) kept in English.

**Tech Stack:** i18next, react-i18next, TypeScript, React

---

## Task 1: Install i18next Dependencies

**Files:**
- Modify: `desktop/package.json`

**Step 1: Install packages**

Run:
```bash
cd /home/ubuntu/workspace/owork/desktop && npm install i18next react-i18next
```

Expected: Packages installed successfully, package.json updated.

**Step 2: Verify installation**

Run:
```bash
cd /home/ubuntu/workspace/owork/desktop && npm list i18next react-i18next
```

Expected: Both packages listed with versions.

**Step 3: Commit**

```bash
git add desktop/package.json desktop/package-lock.json
git commit -m "chore: add i18next and react-i18next dependencies"
```

---

## Task 2: Create i18n Configuration

**Files:**
- Create: `desktop/src/i18n/index.ts`

**Step 1: Create i18n directory and config file**

Create file `desktop/src/i18n/index.ts`:

```typescript
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import zh from './locales/zh.json';
import en from './locales/en.json';

const savedLanguage = localStorage.getItem('language') || 'zh';

i18n.use(initReactI18next).init({
  resources: {
    zh: { translation: zh },
    en: { translation: en },
  },
  lng: savedLanguage,
  fallbackLng: 'zh',
  interpolation: {
    escapeValue: false,
  },
});

export default i18n;
```

**Step 2: Commit**

```bash
git add desktop/src/i18n/index.ts
git commit -m "feat(i18n): add i18next configuration"
```

---

## Task 3: Create Chinese Translation File

**Files:**
- Create: `desktop/src/i18n/locales/zh.json`

**Step 1: Create Chinese translations**

Create file `desktop/src/i18n/locales/zh.json`:

```json
{
  "common": {
    "button": {
      "save": "保存",
      "cancel": "取消",
      "delete": "删除",
      "add": "添加",
      "edit": "编辑",
      "confirm": "确认",
      "close": "关闭",
      "refresh": "刷新",
      "create": "创建",
      "install": "安装",
      "uninstall": "卸载",
      "sync": "同步",
      "upload": "上传",
      "download": "下载",
      "search": "搜索",
      "clear": "清除",
      "retry": "重试",
      "send": "发送",
      "stop": "停止"
    },
    "status": {
      "active": "活跃",
      "inactive": "未激活",
      "loading": "加载中...",
      "saving": "保存中...",
      "error": "错误",
      "success": "成功",
      "running": "运行中",
      "stopped": "已停止",
      "installed": "已安装",
      "configured": "已配置"
    },
    "label": {
      "name": "名称",
      "description": "描述",
      "status": "状态",
      "actions": "操作",
      "version": "版本",
      "type": "类型",
      "model": "模型",
      "all": "全部",
      "none": "无",
      "yes": "是",
      "no": "否",
      "custom": "自定义",
      "system": "系统",
      "user": "用户"
    },
    "message": {
      "confirmDelete": "确定要删除吗？",
      "cannotUndo": "此操作无法撤销。",
      "noData": "暂无数据",
      "loadFailed": "加载失败",
      "saveFailed": "保存失败",
      "saveSuccess": "保存成功",
      "deleteFailed": "删除失败",
      "deleteSuccess": "删除成功"
    },
    "placeholder": {
      "search": "搜索...",
      "select": "请选择..."
    }
  },
  "nav": {
    "dashboard": "仪表盘",
    "chat": "对话",
    "agents": "Agent 管理",
    "skills": "Skill 管理",
    "plugins": "Plugin 管理",
    "mcp": "MCP 管理",
    "settings": "设置"
  },
  "dashboard": {
    "title": "欢迎使用 Owork",
    "subtitle": "管理你的 AI Agent、Skill 和 MCP 服务器连接",
    "quickActions": "快捷操作",
    "overview": "概览",
    "recentAgents": "最近的 Agent",
    "noAgents": "暂无 Agent",
    "createAgent": "创建 Agent",
    "action": {
      "startChat": "开始对话",
      "startChatDesc": "与 AI Agent 开始对话",
      "manageAgents": "管理 Agent",
      "manageAgentsDesc": "创建和配置你的 AI Agent",
      "viewSkills": "查看 Skill",
      "viewSkillsDesc": "浏览和管理可用的 Skill",
      "mcpServers": "MCP 服务器",
      "mcpServersDesc": "监控和配置 MCP 连接",
      "plugins": "Plugin",
      "pluginsDesc": "从 Git 安装和管理 Plugin"
    },
    "stats": {
      "totalAgents": "Agent 总数",
      "active": "{{count}} 个活跃",
      "availableSkills": "可用 Skill",
      "systemCustom": "{{system}} 个系统, {{custom}} 个自定义",
      "mcpServers": "MCP 服务器",
      "plugins": "Plugin",
      "installed": "{{count}} 个已安装"
    }
  },
  "chat": {
    "title": "对话",
    "placeholder": "输入消息...",
    "selectAgent": "选择一个 Agent 开始对话",
    "noAgent": "请先选择一个 Agent",
    "newChat": "新对话",
    "history": "历史记录",
    "noHistory": "暂无历史记录",
    "clearContext": "清除上下文",
    "workingDirectory": "工作目录",
    "changeDirectory": "更改目录",
    "attachFile": "附加文件",
    "sending": "发送中...",
    "thinking": "思考中...",
    "editAgent": "编辑 Agent",
    "deleteSession": "删除会话",
    "deleteSessionConfirm": "确定要删除这个会话吗？"
  },
  "agents": {
    "title": "Agent 管理",
    "subtitle": "创建、配置和监控你的 AI Agent。",
    "addAgent": "添加 Agent",
    "editAgent": "编辑 Agent",
    "createAgent": "创建新 Agent",
    "deleteAgent": "删除 Agent",
    "deleteConfirm": "确定要删除 <strong>{{name}}</strong> 吗？",
    "noAgents": "未找到 Agent",
    "searchPlaceholder": "按名称或模型搜索 Agent...",
    "startChat": "与此 Agent 开始对话",
    "table": {
      "name": "Agent 名称",
      "status": "状态",
      "model": "基础模型",
      "skills": "已启用 Skill",
      "mcps": "已启用 MCP",
      "actions": "操作"
    },
    "form": {
      "basicInfo": "基本信息",
      "name": "名称",
      "namePlaceholder": "输入 Agent 名称",
      "nameRequired": "名称是必填项",
      "description": "描述",
      "descriptionPlaceholder": "描述这个 Agent 的功能...",
      "model": "模型",
      "selectModel": "选择模型",
      "systemPrompt": "系统提示词",
      "systemPromptPlaceholder": "输入系统提示词来定义 Agent 的行为...",
      "permissionMode": "权限模式",
      "maxTurns": "最大轮数",
      "maxTurnsHelp": "限制对话轮数（留空表示无限制）",
      "skillsSection": "Skill",
      "allowAllSkills": "允许所有 Skill",
      "selectSkills": "选择 Skill",
      "mcpSection": "MCP 服务器",
      "selectMcps": "选择 MCP 服务器",
      "advancedOptions": "高级选项",
      "toolOptions": "工具选项",
      "enableBashTool": "启用 Bash 工具",
      "enableFileTools": "启用文件工具",
      "enableWebTools": "启用 Web 工具",
      "securityOptions": "安全选项",
      "enableToolLogging": "启用工具日志",
      "enableSafetyChecks": "启用安全检查",
      "enableFileAccessControl": "启用文件访问控制",
      "enableHumanApproval": "启用人工审批",
      "globalUserMode": "全局用户模式",
      "globalUserModeHelp": "允许 Agent 访问用户主目录下的文件"
    },
    "allSkills": "所有 Skill"
  },
  "skills": {
    "title": "Skill 管理",
    "subtitle": "管理你的 AI Agent 可以使用的 Skill。",
    "addSkill": "添加 Skill",
    "uploadSkill": "上传 Skill",
    "createSkill": "创建 Skill",
    "refreshSkills": "刷新 Skill",
    "deleteSkill": "删除 Skill",
    "deleteConfirm": "确定要删除 <strong>{{name}}</strong> 吗？",
    "noSkills": "未找到 Skill",
    "searchPlaceholder": "按名称搜索 Skill...",
    "table": {
      "name": "Skill 名称",
      "description": "描述",
      "source": "来源",
      "version": "版本",
      "actions": "操作"
    },
    "source": {
      "system": "系统",
      "user": "用户",
      "plugin": "Plugin"
    },
    "upload": {
      "title": "上传 Skill",
      "dropzone": "拖放 ZIP 文件到此处，或点击选择",
      "selectFile": "选择文件",
      "uploading": "上传中..."
    },
    "create": {
      "title": "创建 Skill",
      "nameLabel": "Skill 名称",
      "namePlaceholder": "输入 Skill 名称",
      "descriptionLabel": "描述",
      "descriptionPlaceholder": "描述这个 Skill 的功能..."
    }
  },
  "mcp": {
    "title": "MCP 管理",
    "subtitle": "管理 Model Context Protocol 服务器连接。",
    "addMcp": "添加 MCP",
    "editMcp": "编辑 MCP",
    "createMcp": "创建新 MCP",
    "deleteMcp": "删除 MCP",
    "deleteConfirm": "确定要删除 <strong>{{name}}</strong> 吗？",
    "noMcps": "未找到 MCP 服务器",
    "searchPlaceholder": "按名称搜索 MCP 服务器...",
    "table": {
      "name": "服务器名称",
      "type": "连接类型",
      "status": "状态",
      "actions": "操作"
    },
    "form": {
      "name": "名称",
      "namePlaceholder": "输入 MCP 服务器名称",
      "description": "描述",
      "descriptionPlaceholder": "描述这个 MCP 服务器...",
      "connectionType": "连接类型",
      "stdio": "标准输入输出 (stdio)",
      "sse": "服务器发送事件 (SSE)",
      "command": "命令",
      "commandPlaceholder": "例如: npx",
      "args": "参数",
      "argsPlaceholder": "例如: -y @modelcontextprotocol/server-filesystem",
      "endpoint": "端点 URL",
      "endpointPlaceholder": "https://example.com/mcp"
    }
  },
  "plugins": {
    "title": "Plugin 管理",
    "subtitle": "安装和管理来自 Git 仓库的 Plugin。",
    "addMarketplace": "添加市场",
    "syncAll": "同步全部",
    "noPlugins": "未找到 Plugin",
    "noMarketplaces": "未添加市场",
    "searchPlaceholder": "按名称搜索 Plugin...",
    "table": {
      "name": "Plugin 名称",
      "version": "版本",
      "marketplace": "市场",
      "status": "状态",
      "actions": "操作"
    },
    "tabs": {
      "installed": "已安装",
      "available": "可用"
    },
    "marketplace": {
      "title": "添加市场",
      "name": "名称",
      "namePlaceholder": "例如: My Marketplace",
      "url": "Git URL",
      "urlPlaceholder": "https://github.com/user/repo.git",
      "branch": "分支",
      "branchPlaceholder": "main"
    }
  },
  "settings": {
    "title": "设置",
    "language": {
      "title": "语言 / Language",
      "description": "选择界面显示语言",
      "zh": "中文",
      "en": "English"
    },
    "apiConfig": {
      "title": "API 配置",
      "useBedrock": "使用 AWS Bedrock",
      "useBedrockDesc": "使用 AWS Bedrock 替代 Anthropic API",
      "customBaseUrl": "自定义 Base URL（可选）",
      "customBaseUrlPlaceholder": "https://api.anthropic.com（默认）",
      "customBaseUrlHelp": "用于代理或自定义端点。留空使用默认值。",
      "apiKey": "API Key",
      "apiKeyConfigured": "已配置",
      "apiKeyPlaceholder": "sk-ant-...",
      "apiKeyHelp": "留空保留现有 Key。你的 API Key 会被安全存储。",
      "authMethod": "认证方式",
      "akSkCredentials": "AK/SK 凭证",
      "bearerToken": "Bearer Token",
      "awsAccessKeyId": "AWS Access Key ID",
      "awsSecretAccessKey": "AWS Secret Access Key",
      "awsSessionToken": "AWS Session Token（可选）",
      "awsSessionTokenHelp": "仅用于临时安全凭证 (STS)。",
      "awsBearerToken": "AWS Bearer Token",
      "awsBearerTokenHelp": "用于 AWS Bedrock 认证的 Bearer Token。",
      "awsRegion": "AWS 区域",
      "selectRegion": "选择 AWS 区域...",
      "saveConfig": "保存 API 配置"
    },
    "claudeAgentSdk": {
      "title": "Claude Agent SDK",
      "status": "状态",
      "bundled": "已捆绑",
      "version": "版本",
      "description": "Claude Agent SDK 包含捆绑的 Claude Code CLI。无需额外安装。"
    },
    "systemDependencies": {
      "title": "系统依赖",
      "nodejs": "Node.js",
      "python": "Python",
      "gitBash": "Git Bash",
      "notFound": "未找到",
      "checking": "检查中...",
      "description": "检测到的系统级依赖。这些不是运行应用所必需的。"
    },
    "gitBashWarning": {
      "title": "需要 Git Bash",
      "message": "Git Bash 是 Claude Agent SDK 在 Windows 上执行 shell 命令所必需的。请安装 Git for Windows 并配置环境变量。",
      "step1": "下载并安装 Git for Windows：",
      "step2": "设置环境变量：",
      "example": "示例（默认安装路径）：",
      "afterSetting": "设置环境变量后，重启应用并点击上方的"刷新"验证。"
    },
    "backendService": {
      "title": "后端服务",
      "status": "状态",
      "running": "运行中",
      "stopped": "已停止",
      "port": "端口"
    },
    "storage": {
      "title": "存储",
      "dataDirectory": "数据目录",
      "skillsDirectory": "Skill 目录",
      "database": "数据库",
      "logsDirectory": "日志目录"
    },
    "about": {
      "title": "关于",
      "version": "版本",
      "platform": "平台",
      "checkForUpdates": "检查更新",
      "checkingUpdates": "检查更新中...",
      "latestVersion": "你正在使用最新版本！",
      "updateAvailable": "{{version}} 版本可用！",
      "downloadInstall": "下载并安装",
      "downloading": "下载中...",
      "updateReady": "更新已下载！重启以应用。",
      "restartNow": "立即重启",
      "updateFailed": "更新失败",
      "tryAgain": "重试"
    }
  }
}
```

**Step 2: Commit**

```bash
git add desktop/src/i18n/locales/zh.json
git commit -m "feat(i18n): add Chinese translation file"
```

---

## Task 4: Create English Translation File

**Files:**
- Create: `desktop/src/i18n/locales/en.json`

**Step 1: Create English translations**

Create file `desktop/src/i18n/locales/en.json`:

```json
{
  "common": {
    "button": {
      "save": "Save",
      "cancel": "Cancel",
      "delete": "Delete",
      "add": "Add",
      "edit": "Edit",
      "confirm": "Confirm",
      "close": "Close",
      "refresh": "Refresh",
      "create": "Create",
      "install": "Install",
      "uninstall": "Uninstall",
      "sync": "Sync",
      "upload": "Upload",
      "download": "Download",
      "search": "Search",
      "clear": "Clear",
      "retry": "Retry",
      "send": "Send",
      "stop": "Stop"
    },
    "status": {
      "active": "Active",
      "inactive": "Inactive",
      "loading": "Loading...",
      "saving": "Saving...",
      "error": "Error",
      "success": "Success",
      "running": "Running",
      "stopped": "Stopped",
      "installed": "Installed",
      "configured": "Configured"
    },
    "label": {
      "name": "Name",
      "description": "Description",
      "status": "Status",
      "actions": "Actions",
      "version": "Version",
      "type": "Type",
      "model": "Model",
      "all": "All",
      "none": "None",
      "yes": "Yes",
      "no": "No",
      "custom": "Custom",
      "system": "System",
      "user": "User"
    },
    "message": {
      "confirmDelete": "Are you sure you want to delete?",
      "cannotUndo": "This action cannot be undone.",
      "noData": "No data",
      "loadFailed": "Failed to load",
      "saveFailed": "Failed to save",
      "saveSuccess": "Saved successfully",
      "deleteFailed": "Failed to delete",
      "deleteSuccess": "Deleted successfully"
    },
    "placeholder": {
      "search": "Search...",
      "select": "Select..."
    }
  },
  "nav": {
    "dashboard": "Dashboard",
    "chat": "Chat",
    "agents": "Agent Management",
    "skills": "Skill Management",
    "plugins": "Plugin Management",
    "mcp": "MCP Management",
    "settings": "Settings"
  },
  "dashboard": {
    "title": "Welcome to Owork",
    "subtitle": "Manage your AI Agents, Skills, and MCP server connections",
    "quickActions": "Quick Actions",
    "overview": "Overview",
    "recentAgents": "Recent Agents",
    "noAgents": "No agents yet",
    "createAgent": "Create Agent",
    "action": {
      "startChat": "Start a Chat",
      "startChatDesc": "Begin a conversation with an AI agent",
      "manageAgents": "Manage Agents",
      "manageAgentsDesc": "Create and configure your AI agents",
      "viewSkills": "View Skills",
      "viewSkillsDesc": "Browse and manage available skills",
      "mcpServers": "MCP Servers",
      "mcpServersDesc": "Monitor and configure MCP connections",
      "plugins": "Plugins",
      "pluginsDesc": "Install and manage plugins from Git"
    },
    "stats": {
      "totalAgents": "Total Agents",
      "active": "{{count}} active",
      "availableSkills": "Available Skills",
      "systemCustom": "{{system}} system, {{custom}} custom",
      "mcpServers": "MCP Servers",
      "plugins": "Plugins",
      "installed": "{{count}} installed"
    }
  },
  "chat": {
    "title": "Chat",
    "placeholder": "Type a message...",
    "selectAgent": "Select an Agent to start chatting",
    "noAgent": "Please select an Agent first",
    "newChat": "New Chat",
    "history": "History",
    "noHistory": "No history yet",
    "clearContext": "Clear Context",
    "workingDirectory": "Working Directory",
    "changeDirectory": "Change Directory",
    "attachFile": "Attach File",
    "sending": "Sending...",
    "thinking": "Thinking...",
    "editAgent": "Edit Agent",
    "deleteSession": "Delete Session",
    "deleteSessionConfirm": "Are you sure you want to delete this session?"
  },
  "agents": {
    "title": "Agent Management",
    "subtitle": "Create, configure, and monitor your AI Agents.",
    "addAgent": "Add Agent",
    "editAgent": "Edit Agent",
    "createAgent": "Create New Agent",
    "deleteAgent": "Delete Agent",
    "deleteConfirm": "Are you sure you want to delete <strong>{{name}}</strong>?",
    "noAgents": "No agents found",
    "searchPlaceholder": "Search agents by name or model...",
    "startChat": "Start chat with this agent",
    "table": {
      "name": "Agent Name",
      "status": "Status",
      "model": "Base Model",
      "skills": "Enabled Skills",
      "mcps": "Enabled MCPs",
      "actions": "Actions"
    },
    "form": {
      "basicInfo": "Basic Info",
      "name": "Name",
      "namePlaceholder": "Enter agent name",
      "nameRequired": "Name is required",
      "description": "Description",
      "descriptionPlaceholder": "Describe what this agent does...",
      "model": "Model",
      "selectModel": "Select model",
      "systemPrompt": "System Prompt",
      "systemPromptPlaceholder": "Enter system prompt to define agent behavior...",
      "permissionMode": "Permission Mode",
      "maxTurns": "Max Turns",
      "maxTurnsHelp": "Limit conversation turns (leave empty for unlimited)",
      "skillsSection": "Skills",
      "allowAllSkills": "Allow All Skills",
      "selectSkills": "Select Skills",
      "mcpSection": "MCP Servers",
      "selectMcps": "Select MCP Servers",
      "advancedOptions": "Advanced Options",
      "toolOptions": "Tool Options",
      "enableBashTool": "Enable Bash Tool",
      "enableFileTools": "Enable File Tools",
      "enableWebTools": "Enable Web Tools",
      "securityOptions": "Security Options",
      "enableToolLogging": "Enable Tool Logging",
      "enableSafetyChecks": "Enable Safety Checks",
      "enableFileAccessControl": "Enable File Access Control",
      "enableHumanApproval": "Enable Human Approval",
      "globalUserMode": "Global User Mode",
      "globalUserModeHelp": "Allow agent to access files in user home directory"
    },
    "allSkills": "All Skills"
  },
  "skills": {
    "title": "Skill Management",
    "subtitle": "Manage skills that your AI Agents can use.",
    "addSkill": "Add Skill",
    "uploadSkill": "Upload Skill",
    "createSkill": "Create Skill",
    "refreshSkills": "Refresh Skills",
    "deleteSkill": "Delete Skill",
    "deleteConfirm": "Are you sure you want to delete <strong>{{name}}</strong>?",
    "noSkills": "No skills found",
    "searchPlaceholder": "Search skills by name...",
    "table": {
      "name": "Skill Name",
      "description": "Description",
      "source": "Source",
      "version": "Version",
      "actions": "Actions"
    },
    "source": {
      "system": "System",
      "user": "User",
      "plugin": "Plugin"
    },
    "upload": {
      "title": "Upload Skill",
      "dropzone": "Drop ZIP file here, or click to select",
      "selectFile": "Select File",
      "uploading": "Uploading..."
    },
    "create": {
      "title": "Create Skill",
      "nameLabel": "Skill Name",
      "namePlaceholder": "Enter skill name",
      "descriptionLabel": "Description",
      "descriptionPlaceholder": "Describe what this skill does..."
    }
  },
  "mcp": {
    "title": "MCP Management",
    "subtitle": "Manage Model Context Protocol server connections.",
    "addMcp": "Add MCP",
    "editMcp": "Edit MCP",
    "createMcp": "Create New MCP",
    "deleteMcp": "Delete MCP",
    "deleteConfirm": "Are you sure you want to delete <strong>{{name}}</strong>?",
    "noMcps": "No MCP servers found",
    "searchPlaceholder": "Search MCP servers by name...",
    "table": {
      "name": "Server Name",
      "type": "Connection Type",
      "status": "Status",
      "actions": "Actions"
    },
    "form": {
      "name": "Name",
      "namePlaceholder": "Enter MCP server name",
      "description": "Description",
      "descriptionPlaceholder": "Describe this MCP server...",
      "connectionType": "Connection Type",
      "stdio": "Standard I/O (stdio)",
      "sse": "Server-Sent Events (SSE)",
      "command": "Command",
      "commandPlaceholder": "e.g., npx",
      "args": "Arguments",
      "argsPlaceholder": "e.g., -y @modelcontextprotocol/server-filesystem",
      "endpoint": "Endpoint URL",
      "endpointPlaceholder": "https://example.com/mcp"
    }
  },
  "plugins": {
    "title": "Plugin Management",
    "subtitle": "Install and manage plugins from Git repositories.",
    "addMarketplace": "Add Marketplace",
    "syncAll": "Sync All",
    "noPlugins": "No plugins found",
    "noMarketplaces": "No marketplaces added",
    "searchPlaceholder": "Search plugins by name...",
    "table": {
      "name": "Plugin Name",
      "version": "Version",
      "marketplace": "Marketplace",
      "status": "Status",
      "actions": "Actions"
    },
    "tabs": {
      "installed": "Installed",
      "available": "Available"
    },
    "marketplace": {
      "title": "Add Marketplace",
      "name": "Name",
      "namePlaceholder": "e.g., My Marketplace",
      "url": "Git URL",
      "urlPlaceholder": "https://github.com/user/repo.git",
      "branch": "Branch",
      "branchPlaceholder": "main"
    }
  },
  "settings": {
    "title": "Settings",
    "language": {
      "title": "Language / 语言",
      "description": "Select display language",
      "zh": "中文",
      "en": "English"
    },
    "apiConfig": {
      "title": "API Configuration",
      "useBedrock": "Use AWS Bedrock",
      "useBedrockDesc": "Use AWS Bedrock instead of Anthropic API",
      "customBaseUrl": "Custom Base URL (Optional)",
      "customBaseUrlPlaceholder": "https://api.anthropic.com (default)",
      "customBaseUrlHelp": "For proxies or custom endpoints. Leave empty for default.",
      "apiKey": "API Key",
      "apiKeyConfigured": "Configured",
      "apiKeyPlaceholder": "sk-ant-...",
      "apiKeyHelp": "Leave blank to keep existing key. Your API key is stored securely.",
      "authMethod": "Authentication Method",
      "akSkCredentials": "AK/SK Credentials",
      "bearerToken": "Bearer Token",
      "awsAccessKeyId": "AWS Access Key ID",
      "awsSecretAccessKey": "AWS Secret Access Key",
      "awsSessionToken": "AWS Session Token (Optional)",
      "awsSessionTokenHelp": "Only needed for temporary security credentials (STS).",
      "awsBearerToken": "AWS Bearer Token",
      "awsBearerTokenHelp": "Bearer token for AWS Bedrock authentication.",
      "awsRegion": "AWS Region",
      "selectRegion": "Select AWS Region...",
      "saveConfig": "Save API Configuration"
    },
    "claudeAgentSdk": {
      "title": "Claude Agent SDK",
      "status": "Status",
      "bundled": "Bundled",
      "version": "Version",
      "description": "The Claude Agent SDK includes a bundled Claude Code CLI. No external installation required."
    },
    "systemDependencies": {
      "title": "System Dependencies",
      "nodejs": "Node.js",
      "python": "Python",
      "gitBash": "Git Bash",
      "notFound": "Not found",
      "checking": "Checking...",
      "description": "System-level dependencies detected in PATH. These are not required for the app to run."
    },
    "gitBashWarning": {
      "title": "Git Bash Required",
      "message": "Git Bash is required for Claude Agent SDK to execute shell commands on Windows. Please install Git for Windows and configure the environment variable.",
      "step1": "Download and install Git for Windows:",
      "step2": "Set the environment variable:",
      "example": "Example (default installation path):",
      "afterSetting": "After setting the environment variable, restart the application and click \"Refresh\" above to verify."
    },
    "backendService": {
      "title": "Backend Service",
      "status": "Status",
      "running": "Running",
      "stopped": "Stopped",
      "port": "Port"
    },
    "storage": {
      "title": "Storage",
      "dataDirectory": "Data Directory",
      "skillsDirectory": "Skills Directory",
      "database": "Database",
      "logsDirectory": "Logs Directory"
    },
    "about": {
      "title": "About",
      "version": "Version",
      "platform": "Platform",
      "checkForUpdates": "Check for Updates",
      "checkingUpdates": "Checking for updates...",
      "latestVersion": "You are using the latest version!",
      "updateAvailable": "Version {{version}} available!",
      "downloadInstall": "Download & Install",
      "downloading": "Downloading...",
      "updateReady": "Update downloaded! Restart to apply.",
      "restartNow": "Restart Now",
      "updateFailed": "Update failed",
      "tryAgain": "Try Again"
    }
  }
}
```

**Step 2: Commit**

```bash
git add desktop/src/i18n/locales/en.json
git commit -m "feat(i18n): add English translation file"
```

---

## Task 5: Initialize i18n in Main Entry

**Files:**
- Modify: `desktop/src/main.tsx`

**Step 1: Import i18n module**

Modify `desktop/src/main.tsx` to import i18n before App:

```typescript
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './i18n';  // Initialize i18n before App
import App from './App';
import './index.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

**Step 2: Verify app still runs**

Run:
```bash
cd /home/ubuntu/workspace/owork/desktop && npm run dev
```

Expected: App starts without errors, console shows no i18n warnings.

**Step 3: Commit**

```bash
git add desktop/src/main.tsx
git commit -m "feat(i18n): initialize i18n in main entry"
```

---

## Task 6: Update Sidebar Navigation

**Files:**
- Modify: `desktop/src/components/common/Sidebar.tsx`

**Step 1: Add i18n hook and update navigation items**

Replace the hardcoded labels with translation keys. The key changes:
1. Import `useTranslation` hook
2. Move `navItems` and `bottomNavItems` inside component to use `t()`
3. Update all label strings to use translation keys

```typescript
import { NavLink, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

interface NavItem {
  path: string;
  labelKey: string;
  icon: string;
}

const GITHUB_URL = 'https://github.com/xiehust/owork.git';

// GitHub SVG icon component
const GitHubIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="currentColor"
    xmlns="http://www.w3.org/2000/svg"
  >
    <path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0112 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z"/>
  </svg>
);

interface SidebarProps {
  collapsed?: boolean;
  onClose?: () => void;
  isOverlay?: boolean;
}

export default function Sidebar({ collapsed, onClose, isOverlay }: SidebarProps) {
  const { t } = useTranslation();
  const location = useLocation();

  const navItems: NavItem[] = [
    { path: '/chat', labelKey: 'nav.chat', icon: 'chat' },
    { path: '/agents', labelKey: 'nav.agents', icon: 'smart_toy' },
    { path: '/skills', labelKey: 'nav.skills', icon: 'construction' },
    { path: '/plugins', labelKey: 'nav.plugins', icon: 'extension' },
    { path: '/mcp', labelKey: 'nav.mcp', icon: 'dns' },
  ];

  const bottomNavItems: NavItem[] = [
    { path: '/settings', labelKey: 'nav.settings', icon: 'settings' },
  ];

  const isActive = (path: string) => {
    if (path === '/') {
      return location.pathname === '/';
    }
    return location.pathname.startsWith(path);
  };

  const handleNavClick = () => {
    if (isOverlay && onClose) {
      onClose();
    }
  };

  // Icon-only collapsed mode
  if (collapsed) {
    return (
      <aside className="w-16 bg-dark-bg border-r border-dark-border flex flex-col flex-shrink-0">
        {/* Dashboard - Top Icon */}
        <div className="h-16 flex items-center justify-center border-b border-dark-border">
          <NavLink
            to="/"
            title={t('nav.dashboard')}
            className={clsx(
              'w-10 h-10 rounded-xl flex items-center justify-center transition-colors',
              isActive('/')
                ? 'bg-primary text-white'
                : 'bg-dark-hover text-muted hover:bg-primary/20 hover:text-primary'
            )}
          >
            <span className="material-symbols-outlined text-xl">dashboard</span>
          </NavLink>
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-4 px-2 space-y-1">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              title={t(item.labelKey)}
              className={clsx(
                'flex items-center justify-center w-12 h-12 rounded-xl transition-colors',
                isActive(item.path)
                  ? 'bg-primary/20 text-primary'
                  : 'text-muted hover:bg-dark-hover hover:text-white'
              )}
            >
              <span className="material-symbols-outlined text-2xl">{item.icon}</span>
            </NavLink>
          ))}
        </nav>

        {/* Bottom navigation */}
        <div className="py-4 px-2 border-t border-dark-border space-y-1">
          {bottomNavItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              title={t(item.labelKey)}
              className={clsx(
                'flex items-center justify-center w-12 h-12 rounded-xl transition-colors',
                isActive(item.path)
                  ? 'bg-primary/20 text-primary'
                  : 'text-muted hover:bg-dark-hover hover:text-white'
              )}
            >
              <span className="material-symbols-outlined text-2xl">{item.icon}</span>
            </NavLink>
          ))}

          {/* GitHub Link */}
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            title="GitHub"
            className="flex items-center justify-center w-12 h-12 rounded-xl transition-colors text-muted hover:bg-dark-hover hover:text-white"
          >
            <GitHubIcon className="w-6 h-6" />
          </a>

          {/* User Avatar */}
          <div className="flex items-center justify-center pt-2">
            <div className="w-10 h-10 rounded-full bg-gradient-to-br from-blue-400 to-purple-500 flex items-center justify-center ring-2 ring-dark-border">
              <span className="material-symbols-outlined text-white text-lg">person</span>
            </div>
          </div>
        </div>
      </aside>
    );
  }

  // Full expanded mode (overlay)
  return (
    <aside
      className={clsx(
        'w-64 bg-dark-bg border-r border-dark-border flex flex-col',
        isOverlay && 'fixed left-0 top-0 h-full z-50 animate-slide-in-left shadow-2xl'
      )}
    >
      {/* Header with Dashboard */}
      <div className="h-16 flex items-center px-4 border-b border-dark-border">
        <NavLink
          to="/"
          onClick={handleNavClick}
          className="flex items-center gap-3 flex-1"
        >
          <div className={clsx(
            'w-10 h-10 rounded-xl flex items-center justify-center transition-colors',
            isActive('/') ? 'bg-primary text-white' : 'bg-dark-hover text-muted'
          )}>
            <span className="material-symbols-outlined">dashboard</span>
          </div>
          <div>
            <h1 className="font-semibold text-white">Agent Platform</h1>
            <p className="text-xs text-muted">{t('nav.dashboard')}</p>
          </div>
        </NavLink>
        {isOverlay && onClose && (
          <button
            onClick={onClose}
            className="p-2 rounded-lg text-muted hover:bg-dark-hover hover:text-white transition-colors"
            aria-label={t('common.button.close')}
          >
            <span className="material-symbols-outlined">close</span>
          </button>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4 px-3 space-y-1">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            onClick={handleNavClick}
            className={clsx(
              'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors',
              isActive(item.path)
                ? 'bg-primary text-white'
                : 'text-muted hover:bg-dark-hover hover:text-white'
            )}
          >
            <span className="material-symbols-outlined text-xl">{item.icon}</span>
            <span className="text-sm font-medium">{t(item.labelKey)}</span>
          </NavLink>
        ))}
      </nav>

      {/* Bottom navigation */}
      <div className="py-4 px-3 border-t border-dark-border space-y-1">
        {bottomNavItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            onClick={handleNavClick}
            className={clsx(
              'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors',
              isActive(item.path)
                ? 'bg-primary text-white'
                : 'text-muted hover:bg-dark-hover hover:text-white'
            )}
          >
            <span className="material-symbols-outlined text-xl">{item.icon}</span>
            <span className="text-sm font-medium">{t(item.labelKey)}</span>
          </NavLink>
        ))}

        {/* GitHub Link */}
        <a
          href={GITHUB_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-muted hover:bg-dark-hover hover:text-white"
        >
          <GitHubIcon className="w-5 h-5" />
          <span className="text-sm font-medium">GitHub</span>
        </a>

        {/* User Profile */}
        <div className="flex items-center gap-3 px-3 py-2.5 mt-2">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-400 to-purple-500 flex items-center justify-center">
            <span className="material-symbols-outlined text-white text-sm">person</span>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-white truncate">{t('common.label.user')}</p>
            <p className="text-xs text-muted truncate">{t('nav.settings')}</p>
          </div>
        </div>
      </div>
    </aside>
  );
}
```

**Step 2: Commit**

```bash
git add desktop/src/components/common/Sidebar.tsx
git commit -m "feat(i18n): translate Sidebar navigation labels"
```

---

## Task 7: Add Language Switcher to Settings Page

**Files:**
- Modify: `desktop/src/pages/SettingsPage.tsx`

**Step 1: Add language selector component and translations**

Add the useTranslation hook and a language selector section at the top of the settings page. The changes involve:
1. Import `useTranslation` from react-i18next
2. Add language switcher section after the page title
3. Replace hardcoded strings with `t()` calls

This is a large file, so only showing the key changes:

At the top, add import:
```typescript
import { useTranslation } from 'react-i18next';
```

At the beginning of the component function, add:
```typescript
const { t, i18n } = useTranslation();

const handleLanguageChange = (lang: 'zh' | 'en') => {
  i18n.changeLanguage(lang);
  localStorage.setItem('language', lang);
};
```

After the `<h1>` tag and before API Configuration section, add the language selector:
```tsx
{/* Language Settings */}
<section className="mb-8 bg-[#1a1f2e] rounded-lg p-6">
  <h2 className="text-lg font-semibold text-white mb-2">{t('settings.language.title')}</h2>
  <p className="text-sm text-gray-400 mb-4">{t('settings.language.description')}</p>
  <div className="flex gap-3">
    <button
      onClick={() => handleLanguageChange('zh')}
      className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
        i18n.language === 'zh'
          ? 'bg-[#2b6cee] text-white'
          : 'bg-[#101622] text-gray-400 border border-gray-700 hover:border-gray-500'
      }`}
    >
      {i18n.language === 'zh' && <span className="material-symbols-outlined text-sm">check</span>}
      {t('settings.language.zh')}
    </button>
    <button
      onClick={() => handleLanguageChange('en')}
      className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
        i18n.language === 'en'
          ? 'bg-[#2b6cee] text-white'
          : 'bg-[#101622] text-gray-400 border border-gray-700 hover:border-gray-500'
      }`}
    >
      {i18n.language === 'en' && <span className="material-symbols-outlined text-sm">check</span>}
      {t('settings.language.en')}
    </button>
  </div>
</section>
```

Also update the page title:
```tsx
<h1 className="text-2xl font-bold text-white mb-6">{t('settings.title')}</h1>
```

**Step 2: Commit**

```bash
git add desktop/src/pages/SettingsPage.tsx
git commit -m "feat(i18n): add language switcher to Settings page"
```

---

## Task 8: Translate DashboardPage

**Files:**
- Modify: `desktop/src/pages/DashboardPage.tsx`

**Step 1: Add translations to Dashboard**

Key changes:
1. Import `useTranslation`
2. Update quickActions array to use translation keys
3. Replace all hardcoded strings with `t()` calls

**Step 2: Commit**

```bash
git add desktop/src/pages/DashboardPage.tsx
git commit -m "feat(i18n): translate DashboardPage"
```

---

## Task 9: Translate AgentsPage

**Files:**
- Modify: `desktop/src/pages/AgentsPage.tsx`

**Step 1: Add translations to AgentsPage**

Key changes:
1. Import `useTranslation`
2. Update AGENT_COLUMNS to use `t()` for headers
3. Replace all hardcoded strings with `t()` calls

**Step 2: Commit**

```bash
git add desktop/src/pages/AgentsPage.tsx
git commit -m "feat(i18n): translate AgentsPage"
```

---

## Task 10: Translate Remaining Pages

**Files:**
- Modify: `desktop/src/pages/SkillsPage.tsx`
- Modify: `desktop/src/pages/MCPPage.tsx`
- Modify: `desktop/src/pages/PluginsPage.tsx`
- Modify: `desktop/src/pages/ChatPage.tsx`

**Step 1: Translate each page following the same pattern**

For each page:
1. Import `useTranslation`
2. Add `const { t } = useTranslation();`
3. Replace hardcoded strings with appropriate `t('key')` calls

**Step 2: Commit all together**

```bash
git add desktop/src/pages/SkillsPage.tsx desktop/src/pages/MCPPage.tsx desktop/src/pages/PluginsPage.tsx desktop/src/pages/ChatPage.tsx
git commit -m "feat(i18n): translate remaining pages (Skills, MCP, Plugins, Chat)"
```

---

## Task 11: Translate Common Components

**Files:**
- Modify: `desktop/src/components/common/AgentFormModal.tsx`
- Modify: `desktop/src/components/common/ConfirmDialog.tsx`
- Modify: `desktop/src/components/common/Modal.tsx`

**Step 1: Update common components with translations**

For each component, add useTranslation and replace hardcoded strings.

**Step 2: Commit**

```bash
git add desktop/src/components/common/AgentFormModal.tsx desktop/src/components/common/ConfirmDialog.tsx desktop/src/components/common/Modal.tsx
git commit -m "feat(i18n): translate common components (AgentFormModal, ConfirmDialog, Modal)"
```

---

## Task 12: Final Testing and Cleanup

**Step 1: Run the app and test language switching**

```bash
cd /home/ubuntu/workspace/owork/desktop && npm run dev
```

Test:
1. Default language is Chinese
2. Navigate to Settings, switch to English
3. Verify all pages display in English
4. Refresh page, verify language persists
5. Switch back to Chinese

**Step 2: Run linting**

```bash
cd /home/ubuntu/workspace/owork/desktop && npm run lint
```

Expected: No errors.

**Step 3: Run tests**

```bash
cd /home/ubuntu/workspace/owork/desktop && npm run test:run
```

Expected: All tests pass.

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat(i18n): complete i18n implementation with Chinese/English support"
```

---

## Summary

This plan implements bilingual (Chinese/English) support for the Owork desktop app:

- **Phase 1** (Tasks 1-5): Set up i18next infrastructure
- **Phase 2** (Tasks 6-7): Update navigation and add language switcher
- **Phase 3** (Tasks 8-11): Translate all pages and components
- **Phase 4** (Task 12): Testing and final cleanup

Total estimated translation entries: ~200
Files to modify: ~15
