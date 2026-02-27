# Owork 安装指南

Owork 是一个基于 Claude Agent SDK 的 AI Agent 桌面应用，支持创建、管理和与 AI Agent 对话。

## 目录

- [系统要求](#系统要求)
- [安装步骤](#安装步骤)
  - [1. 安装 Node.js](#1-安装-nodejs)
  - [2. 安装 Claude Code CLI](#2-安装-claude-code-cli)
  - [3. 安装 Owork](#3-安装-owork)
  - [4. 配置 API](#4-配置-api)
- [可选：配置feishu渠道](#5-配置feishu渠道)
- [验证安装](#验证安装)
- [常见问题](#常见问题)

---

## 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | macOS 10.15+、Windows 10/11 或 Linux (Ubuntu 20.04+) |
| 处理器 | x86_64 或 ARM64 (Apple Silicon) |
| 内存 | 8GB RAM (推荐 16GB) |
| 磁盘空间 | 500MB 可用空间 |
| 网络 | 需要互联网连接 |

---

## 安装步骤

### 1. 安装 Owork

#### macOS

**方式一：使用 DMG 安装包（推荐）**

1. 下载 [`Owork_x.x.x_aarch64.dmg`](https://d1a1de1i2hajk1.cloudfront.net/owork/release/v0.0.81/Owork_0.0.81_aarch64.dmg)
2. 双击打开 DMG 文件
3. 将 `Owork.app` 拖拽到 `Applications` 文件夹
4. 从启动台或 Applications 文件夹启动 Owork

**首次启动注意事项：**

macOS 可能会阻止未签名的应用运行。如果遇到「无法打开 Owork，因为无法验证开发者」的提示：

1. 打开「系统偏好设置」→「安全性与隐私」
2. 点击「通用」标签
3. 点击「仍要打开」按钮

或者使用终端命令：
```bash
xattr -cr /Applications/Owork.app
```

#### Windows

**方式一：使用 MSI 安装包（推荐）**

1. 下载 `Owork_x.x.x_x64.msi` 安装包
2. 双击运行安装程序
3. 按照向导完成安装（默认安装到 `C:\Program Files\Owork\`）
4. 从开始菜单启动 Owork

**方式二：使用 NSIS 安装包**

1. 下载 [`Owork_x.x.x._x64-setup.zip`](https://d1a1de1i2hajk1.cloudfront.net/owork/release/v0.0.81/Owork_0.0.81_x64-setup.zip)
2. 运行安装程序并按照提示完成安装
3. 从开始菜单或桌面快捷方式启动 Owork

**首次启动注意事项：**

Windows 可能显示 SmartScreen 警告。点击「更多信息」→「仍要运行」即可。

需要安装Git bash 系统依赖：https://git-scm.com/downloads/win

#### 从源码构建（所有平台）

```bash
# 克隆仓库
git clone https://github.com/xiehust/owork.git
cd owork/desktop

# 安装依赖
npm install

# 构建应用（会自动检测当前平台）
npm run build:all

# 构建产物位于 ./src-tauri/target/release/bundle/
# macOS: dmg/Owork_x.x.x_aarch64.dmg 或 macos/Owork.app
# Windows: msi/Owork_x.x.x_x64.msi 或 nsis/Owork_x.x.x_x64-setup.exe
# Linux: deb/owork_x.x.x_amd64.deb 或 appimage/owork_x.x.x_x86_64.AppImage
```


---

### 4. 配置 API

启动 Owork 后，需要配置 API 才能使用 AI 功能。

#### 进入设置页面

1. 启动 Owork
2. 点击左侧边栏的「设置」图标（齿轮图标）
3. 在「API Configuration」区域配置 API

#### 方式一：使用 Litellm Proxy API

1. 使用[litellm gateway](https://docs.litellm.ai/docs/simple_proxy) 创建proxy。
   - 确保「Use AWS Bedrock」开关为关闭状态
   - 在Base URL中填入proxy url
   - 在「API Key」输入框中粘贴你的 API Key
   - 点击「Save API Configuration」
   - 注意在配置lite config yml文件时，需要把model_name设置成Claude 官网的Model Name 如：
```yml
model_list:
  - model_name: claude-sonnet-4-5-20250929
    litellm_params:
      model: bedrock/global.anthropic.claude-sonnet-4-5-20250929-v1:0
```

#### 方式二：使用开源的AWS生产化Proxy方案

1. 使用[Anthropic-Bedrock API Proxy](https://github.com/xiehust/anthropic_api_converter)
2. 部署在AWS ECS上，支持API Key管理，预算分配，流量控制等，默认自动映射官网model id到bedrock model id
![alt text](./assets/image-2p.png)

#### 方式三：使用 AWS Bedrock（中国区和香港地区网络无法直接访问Claude模型）

1. 确保你有 AWS 账号并已启用 Bedrock 服务
2. 在 AWS Console 中申请 Claude 模型访问权限
3. 在 Owork 设置中：
   - 开启「Use AWS Bedrock」开关
   - 选择认证方式：
     - **AK/SK Credentials**：输入 Access Key ID 和 Secret Access Key
     - **Bearer Token**：输入 Bearer Token
   - 选择 AWS Region
   - 点击「Save API Configuration」

---

## 5. 配置feishu渠道

### 第一步：创建飞书应用
​
1. 打开飞书开放平台
访问 **飞书开放平台**，使用飞书账号登录。  
Lark（国际版）请使用 https://open.larksuite.com/app，并在配置中设置 domain: "lark"。
​
2. 创建应用
- 点击 **创建企业自建应用**
- 填写应用名称和描述
- 选择应用图标
- 创建企业自建应用
​
3. 获取应用凭证
- 在应用的 **凭证与基础信息** 页面，复制：
- App ID（格式如 cli_xxx）
- App Secret
❗ 重要：请妥善保管 App Secret，不要分享给他人。
- 获取应用凭证
​
4. 配置应用权限
- 在 **权限管理** 页面，点击 **批量导入** 按钮，粘贴以下 JSON 配置一键导入所需权限：
```json
{
  "scopes": {
    "tenant": [
      "aily:file:read",
      "aily:file:write",
      "application:application.app_message_stats.overview:readonly",
      "application:application:self_manage",
      "application:bot.menu:write",
      "cardkit:card:write",
      "contact:user.employee_id:readonly",
      "corehr:file:download",
      "docs:document.content:read",
      "event:ip_list",
      "im:chat",
      "im:chat.access_event.bot_p2p_chat:read",
      "im:chat.members:bot_access",
      "im:message",
      "im:message.group_at_msg:readonly",
      "im:message.group_msg",
      "im:message.p2p_msg:readonly",
      "im:message:readonly",
      "im:message:send_as_bot",
      "im:resource",
      "sheets:spreadsheet",
      "wiki:wiki:readonly"
    ],
    "user": ["aily:file:read", "aily:file:write", "im:chat.access_event.bot_p2p_chat:read"]
  }
}
```
- 配置应用权限
​
5. 启用机器人能力
- 在 **应用能力 > 机器人** 页面：
- 开启机器人能力
- 配置机器人名称
- 启用机器人能力
​
6. 配置事件订阅
⚠️ 重要提醒：在配置事件订阅前，请务必确保已完成以下步骤：
- Owork已经成功启动
- 在 事件订阅 页面：
- 选择 使用长连接接收事件（WebSocket 模式）
- 添加事件：im.message.receive_v1（接收消息）
⚠️ 注意：如果网关未启动或渠道未添加，长连接设置将保存失败。
- 配置事件订阅
​
7. 发布应用
- 在 **版本管理与发布** 页面创建版本
- 提交审核并发布
- 等待管理员审批（企业自建应用通常自动通过）

## 验证安装

### 检查设置页面状态

打开 Owork 的设置页面，确认以下状态：

| 项目 | 预期状态 |
|------|----------|
| Claude Code CLI - Status | ✓ Installed |
| Claude Code CLI - Node.js | ✓ Available |
| Claude Code CLI - npm | ✓ Available |
| Backend Service - Status | ● Running |
| API Configuration | 已配置（显示 ✓ Configured） |


## 添加Plugins
在**Plugin Management**页面中点击**Install Plugin**按钮，输入plugins github repo地址即可，推荐官方plugins包括：  
| Name | 地址 |
|------|------|
| Cowork knowledge-work-plugins | https://github.com/anthropics/knowledge-work-plugins.git |
| 官方SKILL | https://github.com/anthropics/skills.git |
| 官方PLUGINS | https://github.com/anthropics/claude-plugins-official.git |

## 添加MCP
在**MCP Management**页面中点击**Add MCP Server**按钮，选择Connection Type，例如添加AWS Knowledge
选择http，输入地址 https://knowledge-mcp.global.api.aws 



### 测试 Agent 对话

1. 在「Agents」页面创建一个新的 Agent, 可以自定义启用哪些skills，mcp，plugins
2. 进入「Chat」页面
3. 选择刚创建的 Agent
4. 发送一条测试消息，如「Hello, how are you?」
5. 如果收到 AI 回复，则安装成功

---

## 数据存储位置

Owork 的数据存储位置因操作系统而异：

### macOS

| 类型 | 路径 |
|------|------|
| 数据目录 | `~/Library/Application Support/Owork/` |
| 数据库 | `~/Library/Application Support/Owork/data.db` |
| Skills 目录 | `~/Library/Application Support/Owork/skills/` |
| 日志目录 | `~/Library/Application Support/Owork/logs/` |

**查看日志：**
```bash
cat ~/Library/Application\ Support/Owork/logs/backend.log
```

### Windows

| 类型 | 路径 |
|------|------|
| 数据目录 | `%LOCALAPPDATA%\Owork\` |
| 数据库 | `%LOCALAPPDATA%\Owork\data.db` |
| Skills 目录 | `%LOCALAPPDATA%\Owork\skills\` |
| 日志目录 | `%LOCALAPPDATA%\Owork\logs\` |

通常对应：`C:\Users\YourUsername\AppData\Local\Owork\`

**查看日志：**
```powershell
# PowerShell
Get-Content $env:LOCALAPPDATA\Owork\logs\backend.log

# 或使用记事本打开
notepad $env:LOCALAPPDATA\Owork\logs\backend.log
```

### Linux

| 类型 | 路径 |
|------|------|
| 数据目录 | `~/.local/share/owork/` |
| 数据库 | `~/.local/share/owork/data.db` |
| Skills 目录 | `~/.local/share/owork/skills/` |
| 日志目录 | `~/.local/share/owork/logs/` |

**查看日志：**
```bash
cat ~/.local/share/owork/logs/backend.log
```

---

## 常见问题

### Q: 启动后 Backend Service 显示 Stopped？

**A:** 这通常是时序问题，等待几秒钟后状态会自动更新为 Running。如果持续显示 Stopped：

1. 检查日志文件：
   ```bash
   cat ~/Library/Application\ Support/Owork/logs/backend.log
   ```
2. 尝试重启应用

### Q: Claude Code CLI 显示 Not Found？

**A:** 确保 Node.js 已正确安装：

```bash
# 检查 Node.js
node --version

# 检查 npm
npm --version

# 重新安装 Claude Code CLI
npm install -g @anthropic-ai/claude-code

# 验证
claude --version
```

### Q: 保存 API 配置时显示「Unable to connect to backend service」？

**A:** 这可能是 CORS 或端口问题：

1. 确认 Backend Service 显示为 Running
2. 记下显示的端口号
3. 在终端测试连接：
   ```bash
   curl http://localhost:<端口号>/health
   ```
4. 如果返回 `{"status":"healthy"...}`，尝试重启应用

### Q: 如何完全卸载 Owork？

**A (macOS):**

```bash
# 1. 删除应用
rm -rf /Applications/Owork.app

# 2. 删除数据目录（可选，会删除所有数据）
rm -rf ~/Library/Application\ Support/Owork/

# 3. 删除 Claude Code CLI（可选）
npm uninstall -g @anthropic-ai/claude-code
```

**A (Windows):**

```powershell
# 1. 使用 Windows 设置卸载
# 「设置」→「应用」→「Owork」→「卸载」

# 或使用 MSI 卸载（如果是 MSI 安装）
# 控制面板 → 程序和功能 → Owork → 卸载

# 2. 删除数据目录（可选，会删除所有数据）
Remove-Item -Recurse -Force $env:LOCALAPPDATA\Owork

# 3. 删除 Claude Code CLI（可选）
npm uninstall -g @anthropic-ai/claude-code
```

**A (Linux):**

```bash
# 1. 卸载应用
# DEB 安装：
sudo apt remove owork

# AppImage：直接删除文件
rm owork_*.AppImage

# 2. 删除数据目录（可选，会删除所有数据）
rm -rf ~/.local/share/owork/

# 3. 删除 Claude Code CLI（可选）
npm uninstall -g @anthropic-ai/claude-code
```

### Q: 如何更新 Owork？

**A:**

1. 下载新版本的 DMG 安装包
2. 关闭正在运行的 Owork
3. 将新版本拖拽到 Applications 文件夹并替换旧版本
4. 重新启动 Owork

数据会自动保留，无需重新配置。

---

## 获取帮助

- **GitHub Issues**: [报告问题或建议](https://github.com/xiehust/owork/issues)
- **文档**: 查看项目 README 和 CLAUDE.md 获取更多信息

---

*最后更新: 2025年1月*
