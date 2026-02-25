<div align="center">

# Owork - 个人办公智能体平台

### 个人办公智能体平台

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=flat&logo=react&logoColor=black)](https://react.dev/)
[![Tauri](https://img.shields.io/badge/Tauri-2.0-FFC131?style=flat&logo=tauri&logoColor=white)](https://tauri.app/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6?style=flat&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Claude](https://img.shields.io/badge/Claude-Agent_SDK-191919?style=flat&logo=anthropic&logoColor=white)](https://github.com/anthropics/claude-code)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat)](./LICENSE)

基于 Claude Agent SDK 的全栈 AI 智能体平台，支持桌面应用和云端部署两种模式。

**简体中文** | [![English](https://img.shields.io/badge/lang-English-blue?style=flat)](./README_EN.md)

[✨ 特性](#功能特性) • [🖥️ 桌面应用](#桌面应用-owork) • [☁️ 云端部署](#云端部署) • [🛡️ 安全性](#安全性) • [📚 文档](#文档) • [📖 公众号介绍《On My Work - Owork来了》](https://mp.weixin.qq.com/s/JB2FghiYCDJYZ7BJUwjmeg)

</div>

---

## 概述

Owork 是一个功能强大的 AI Agent 管理平台，让您可以：
![alt text](image.png)
- **与 AI 智能体对话**：通过 SSE 流式传输的交互式聊天界面
![alt text](./assets/image-4.png)
- **创建后台任务运行**：创建后台任务批量运行
![alt text](./assets/image-5.png)
![alt text](./assets/image-7.png)

- **管理智能体**：创建、配置和监控 AI 智能体
![alt text](./assets/image-3.png)
- **管理技能**：上传、安装和管理自定义技能（支持 Git 版本控制）
![alt text](./assets/image-2.png)
- **管理插件**：扩展平台功能的插件系统
![alt text](./assets/image-1.png)
- **管理 MCP 服务器**：配置模型上下文协议（Model Context Protocol）服务器
![alt text](./assets/image.png)
## 部署模式

| 模式 | 前端 | 后端 | 数据库 | 技能存储 | 适用场景 |
|------|------|------|--------|----------|----------|
| **桌面版** | Tauri 2.0 + React | Python FastAPI (Sidecar) | SQLite | 本地文件系统 + Git | 个人使用 |
| **云端版** | React (S3/CloudFront) | FastAPI (ECS Fargate/EC2) | DynamoDB | S3 | 团队/企业 |

---

## 桌面应用 (Owork)

### 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | macOS 10.15+ (Catalina 或更高) |
| 处理器 | Apple Silicon (M1/M2/M3) 或 Intel |
| 内存 | 8GB RAM (推荐 16GB) |
| Node.js | 18.0+ |

### 快速安装

#### 1. 安装 Owork （MAC 版）

1. 下载 [`Owork_x.x.x_aarch64.dmg`](https://d1a1de1i2hajk1.cloudfront.net/owork/release/v0.0.81/Owork_0.0.81_aarch64.dmg)
2. 双击打开 DMG 文件
3. 将 Owork.app 拖拽到 Applications 文件夹
4. 注意如果运行提示“文件损坏”，需要在终端执行以下命令
```shell
# 移除隔离属性
xattr -cr /Applications/Owork.app
```

#### 1. 安装 Owork （Windows 版）
1. 下载 [`Owork_x.x.x._x64-setup.zip`](https://d1a1de1i2hajk1.cloudfront.net/owork/release/v0.0.81/Owork_0.0.81_x64-setup.zip)
2. Windows版可能显示 SmartScreen 警告。点击「更多信息」→「仍要运行」即可。
3. 需要安装Git bash 系统依赖：https://git-scm.com/downloads/win

#### 2. 配置 API
启动Owork,后进入 Settings 页面配置：

- **Anthropic API**：输入 API Key
- **AWS Bedrock**：开启 Bedrock 开关，配置认证信息

> 📖 详细安装说明请参阅 [QUICK_START.md](./QUICK_START.md)


**首次启动注意事项：**

Windows 可能显示 SmartScreen 警告。点击「更多信息」→「仍要运行」即可。

需要安装Git bash 系统依赖：https://git-scm.com/downloads/win

#### 从源码构建（所有平台）

```bash
# 克隆仓库
git clone https://github.com/xiehust/awesome-skills-claude-agents.git
cd awesome-skills-claude-agents/desktop

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
2. 部署在AWS ECS上，支持API Key管理，预算分配，流量控制等，支持同时非Claude API
![alt text](./assets/image-2p.png)


### 从源码构建

```bash
cd desktop

# 安装依赖
npm install

# 配置环境变量
cp backend.env.example ../backend/.env
# 编辑 ../backend/.env 添加 ANTHROPIC_API_KEY

# 开发模式
npm run tauri:dev

# 构建生产版本
npm run build:all
```

### 数据存储

| 类型 | macOS 路径 |
|------|------------|
| 数据目录 | `~/Library/Application Support/Owork/` |
| 数据库 | `~/Library/Application Support/Owork/data.db` |
| 技能 | `~/Library/Application Support/Owork/skills/` |
| 日志 | `~/Library/Application Support/Owork/logs/` |

---

## 云端部署

### 前置要求

- Node.js 18+ 和 npm
- Python 3.12+
- uv（Python 包管理器，推荐）或 pip
- AWS 账号（用于 DynamoDB、S3 等）
- ANTHROPIC_API_KEY

### 快速启动

```bash
# 启动后端和前端
./start.sh

# 停止所有服务
./stop.sh
```

### 手动设置

#### 前端

```bash
cd frontend
npm install
npm run dev
```

前端将在 http://localhost:5173 可用

#### 后端

```bash
cd backend

# 创建虚拟环境
uv sync
source .venv/bin/activate

# 配置环境变量
cp .env.example .env
# 编辑 .env 添加 ANTHROPIC_API_KEY

# 启动服务器
python main.py
```

后端 API 将在 http://localhost:8000 可用

API 文档：http://localhost:8000/docs

---

## 技术栈

### 桌面版
- **前端框架**：Tauri 2.0 + React 19 + TypeScript
- **后端**：FastAPI (PyInstaller 打包为 Sidecar)
- **数据库**：SQLite
- **构建工具**：Vite + Rust

### 云端版
- **前端**：React 19 + TypeScript + Vite
- **后端**：FastAPI + Uvicorn
- **数据库**：DynamoDB
- **部署**：AWS ECS Fargate + S3 + CloudFront

### 通用
- **AI 引擎**：Claude Agent SDK
- **样式**：Tailwind CSS 4.x
- **状态管理**：TanStack Query
- **路由**：React Router v6

---

## 功能特性

### 聊天界面
- SSE 实时流式响应
- 消息历史记录
- 工具调用可视化
- 文件附件支持（图片、PDF、TXT、CSV）
- 拖拽和粘贴上传

### 智能体管理
- 创建、编辑和删除智能体
- 配置模型、最大令牌数和权限
- 为智能体分配技能和 MCP 服务器
- 全局用户模式（访问完整文件系统）
- 人工审批模式（危险操作需确认）

### 技能管理
- 从 Git 仓库安装技能
- 本地技能目录
- Git 版本控制（更新、回滚）
- 上传 ZIP 包安装

### 插件管理
- 插件启用/禁用
- 插件配置

### MCP 服务器管理
- 支持 stdio、SSE、HTTP 连接类型
- 连接状态监控
- 测试连接功能

---

## 项目结构

```
awesome-skills-claude-agents/
├── desktop/                 # 桌面应用 (Tauri 2.0)
│   ├── src/                 # React 前端源码
│   │   ├── components/      # UI 组件
│   │   ├── pages/           # 页面组件
│   │   ├── services/        # API 服务
│   │   └── types/           # TypeScript 类型
│   ├── src-tauri/           # Tauri/Rust 代码
│   │   ├── src/lib.rs       # Rust 主逻辑
│   │   └── tauri.conf.json  # Tauri 配置
│   ├── scripts/             # 构建脚本
│   └── INSTALL_GUIDE.md     # 安装指南
│
├── frontend/                # 云端版前端 (React)
│   ├── src/
│   └── package.json
│
├── backend/                 # FastAPI 后端
│   ├── main.py              # 应用入口
│   ├── config.py            # 配置
│   ├── routers/             # API 路由
│   ├── core/                # 核心业务逻辑
│   │   ├── agent_manager.py # Agent 管理
│   │   ├── session_manager.py
│   │   └── local_skill_manager.py
│   ├── database/
│   │   ├── sqlite.py        # SQLite (桌面版)
│   │   └── dynamodb.py      # DynamoDB (云端版)
│   └── schemas/             # Pydantic 模型
│
├── CLAUDE.md                # Claude Code 开发指南
├── SECURITY.md              # 安全架构文档
├── ARCHITECTURE.md          # 系统架构文档
└── README.md                # 本文件
```

---

## 安全性

平台实施了**纵深防御安全模型**：

### 四层安全防护

1. **工作空间隔离**：每个智能体在独立目录运行
2. **技能访问控制**：PreToolUse 钩子验证技能调用
3. **文件工具访问控制**：验证所有文件操作路径
4. **Bash 命令保护**：解析和验证 bash 命令中的文件路径

### 智能体模式

| 模式 | 描述 |
|------|------|
| 默认模式 | 仅能访问工作空间内文件 |
| 全局用户模式 | 可访问完整文件系统（`~/` 为工作目录） |
| 人工审批模式 | 危险操作需用户确认 |

> 📖 详细安全文档请参阅 [SECURITY.md](./SECURITY.md)

---

## 配置

### 环境变量

#### 必需
- `ANTHROPIC_API_KEY` - Anthropic API 密钥

#### API 配置
- `ANTHROPIC_BASE_URL` - 自定义 API 端点
- `CLAUDE_CODE_USE_BEDROCK` - 使用 AWS Bedrock
- `DEFAULT_MODEL` - 默认模型

#### 服务器配置
- `DEBUG` - 调试模式
- `HOST` - 服务器主机
- `PORT` - 服务器端口
- `DATABASE_TYPE` - 数据库类型 (`sqlite` / `dynamodb`)

完整配置请参见 `backend/.env.example` 或 `desktop/backend.env.example`

---

## 文档

| 文档 | 描述 |
|------|------|
| [INSTALL_GUIDE.md](./desktop/INSTALL_GUIDE.md) | 桌面版安装指南 |
| [CLAUDE.md](./CLAUDE.md) | Claude Code 开发指南 |
| [SECURITY.md](./SECURITY.md) | 安全架构文档 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 系统架构文档 |
| [SKILLS_GUIDE.md](./SKILLS_GUIDE.md) | 技能开发指南 |

---

## 设计系统

- **主色调**：`#2b6cee`（蓝色）
- **背景**：`#101622`（深色）
- **卡片背景**：`#1a1f2e`
- **字体**：Space Grotesk
- **图标**：Material Symbols Outlined

---

## 许可证

MIT License

---

## 贡献

欢迎提交 Issue 和 Pull Request！

- **GitHub**: https://github.com/xiehust/awesome-skills-claude-agents
