# Owork Desktop 构建指南

本指南详细说明如何构建 Owork 的 Mac/Linux 桌面应用版本。

## 目录

- [项目概述](#项目概述)
- [系统要求](#系统要求)
- [环境准备](#环境准备)
- [开发模式运行](#开发模式运行)
- [生产构建](#生产构建)
- [安装与启动](#安装与启动)
- [构建脚本详解](#构建脚本详解)
  - [build-backend.sh](#scriptsbuild-backendsh---python-后端打包脚本)
  - [build.sh](#scriptsbuildsh---完整构建脚本)
  - [npm 脚本命令](#npm-脚本命令)
  - [构建故障排除](#构建故障排除)
- [项目结构](#项目结构)
- [配置说明](#配置说明)
- [常见问题](#常见问题)
- [构建优化](#构建优化)
- [版本发布检查清单](#版本发布检查清单)

---

## 项目概述

Desktop 版本基于以下技术栈：

| 组件 | 技术 |
|------|------|
| 桌面框架 | Tauri 2.0 (Rust) |
| 前端 | React 19 + TypeScript + Vite |
| 后端 | Python FastAPI (作为 sidecar 进程) |
| 数据库 | SQLite |
| 技能管理 | 本地文件系统 + Git 版本控制 |
| 打包 | PyInstaller (Python) + Tauri (Rust/前端) |

### 与云端版本的区别

| 功能 | 桌面版本 |
|------|----------|
| 数据库 | SQLite |
| 技能存储 | 本地文件系统 |
| 版本控制 | Git |
| 认证 | 无 (单用户) |
| Claude API | Anthropic API / Bedrock |

---

## 系统要求

### macOS
- macOS 10.15 (Catalina) 或更高版本
- Xcode Command Line Tools
- 约 2GB 磁盘空间用于构建

### Linux
- Ubuntu 20.04+ / Debian 11+ / Fedora 34+
- GTK 3 开发库
- WebKit2GTK 开发库

### 通用要求
- **Node.js**: v18.0.0 或更高版本
- **npm**: v8.0.0 或更高版本
- **Python**: 3.11 或更高版本
- **Rust**: 1.70.0 或更高版本
- **Git**: 用于技能版本控制

---

## 环境准备

### 1. 安装 Node.js

**macOS (使用 Homebrew):**
```bash
brew install node
```

**Ubuntu/Debian:**
```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

**验证安装:**
```bash
node --version  # 应显示 v18.x.x 或更高
npm --version   # 应显示 8.x.x 或更高
```

### 2. 安装 Rust

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env
```

**验证安装:**
```bash
rustc --version  # 应显示 1.70.0 或更高
cargo --version
```

### 3. 安装系统依赖

**macOS:**
```bash
xcode-select --install
```

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y \
    libwebkit2gtk-4.1-dev \
    libappindicator3-dev \
    librsvg2-dev \
    patchelf \
    pkg-config \
    libglib2.0-dev \
    libssl-dev \
    build-essential
```

**Fedora:**
```bash
sudo dnf install -y \
    webkit2gtk4.1-devel \
    openssl-devel \
    curl \
    wget \
    file \
    libappindicator-gtk3-devel \
    librsvg2-devel
```

### 4. 安装 Python 依赖

```bash
cd backend
uv sync
uv pip install pyinstaller
source .venv/bin/activate
```

---

## 开发模式运行

开发模式允许热重载，方便调试。

### 步骤 1: 安装前端依赖

```bash
cd desktop
npm install
```

### 步骤 2: 配置环境变量

```bash
# 复制示例配置
cp backend.env.example ../backend/.env

# 编辑配置，设置你的 Anthropic API Key
nano ../backend/.env
```

必须设置的关键配置：
```env
DATABASE_TYPE=sqlite
ANTHROPIC_API_KEY=your-api-key-here
CLAUDE_CODE_USE_BEDROCK=false
```

### 步骤 3: 启动开发服务器

```bash
RUST_LOG=debug npm run tauri:dev
```

这将：
1. 启动 Vite 开发服务器 (前端热重载)
2. 编译 Rust 代码
3. 启动 Tauri 窗口
4. 自动启动 Python 后端 sidecar

**注意:** 首次运行会下载 Rust 依赖并编译，可能需要几分钟。

### 开发模式端口

| 服务 | 端口 |
|------|------|
| Vite 前端 | 1420 |
| HMR WebSocket | 1421 |
| Python 后端 | 动态分配 (默认 8000) |

---

## 生产构建

### 方法 1: 使用构建脚本 (推荐)

```bash
cd desktop
npm run build:all
```

这个脚本会：
1. 构建 Python 后端为独立可执行文件 (PyInstaller)
2. 构建前端资源 (Vite)
3. 构建 Tauri 应用程序
4. 创建安装包 (DMG/AppImage/DEB)

### 方法 2: 分步构建

```bash
# 步骤 1: 构建 Python 后端
npm run build:backend

# 步骤 2: 构建 Tauri 应用
npm run tauri:build
```

### 构建产物位置

**macOS:**
```
desktop/src-tauri/target/release/bundle/
├── dmg/
│   └── Owork_1.0.0_aarch64.dmg  # DMG 安装包
└── macos/
    └── Owork.app/               # 应用程序包
```

**Linux:**
```
desktop/src-tauri/target/release/bundle/
├── deb/
│   └── owork_1.0.0_amd64.deb   # Debian 包
└── appimage/
    └── owork_1.0.0_amd64.AppImage  # AppImage
```

### 安装与启动

构建完成后，按以下步骤安装和启动应用：

**macOS:**

1. **方法 1: 使用 DMG 安装包 (推荐)**
   ```bash
   # 打开 DMG 文件
   open desktop/src-tauri/target/release/bundle/dmg/Owork_1.0.0_aarch64.dmg

   # 在打开的窗口中，将 Owork.app 拖拽到 Applications 文件夹
   # 然后从 Launchpad 或 Applications 文件夹启动
   ```

2. **方法 2: 直接运行 .app**
   ```bash
   # 直接打开应用程序包
   open desktop/src-tauri/target/release/bundle/macos/Owork.app
   ```

3. **首次运行注意事项**

   由于应用未经 Apple 签名，首次打开时可能会提示"无法打开，因为无法验证开发者"。解决方法：

   - 右键点击 `Owork.app` → 选择"打开" → 在弹出对话框中点击"打开"

   或在终端执行：
   ```bash
   xattr -cr /Applications/Owork.app
   open /Applications/Owork.app
   ```

**Linux:**

1. **Debian/Ubuntu (.deb)**
   ```bash
   # 安装
   sudo dpkg -i desktop/src-tauri/target/release/bundle/deb/owork_1.0.0_amd64.deb

   # 启动 (从应用菜单或终端)
   owork
   ```

2. **AppImage (通用)**
   ```bash
   # 添加执行权限
   chmod +x desktop/src-tauri/target/release/bundle/appimage/owork_1.0.0_amd64.AppImage

   # 直接运行
   ./desktop/src-tauri/target/release/bundle/appimage/owork_1.0.0_amd64.AppImage
   ```

**启动后的初始化:**

1. 首次启动时，应用会自动创建数据目录和 SQLite 数据库
2. 进入应用后，首先在设置中配置 `ANTHROPIC_API_KEY`
3. 创建你的第一个 Agent，即可开始使用

---

## 构建脚本详解

项目提供两个构建脚本，位于 `desktop/scripts/` 目录下。

### scripts/build-backend.sh - Python 后端打包脚本

此脚本使用 PyInstaller 将 Python 后端打包为独立可执行文件。

**功能：**
- 自动检测目标平台和架构 (macOS/Linux, x86_64/aarch64)
- 创建临时构建环境
- 安装 Python 依赖
- 使用 PyInstaller 打包为单文件可执行程序
- 输出到 `src-tauri/binaries/` 目录

**使用方法：**
```bash
# 直接运行
./scripts/build-backend.sh

# 或通过 npm
npm run build:backend
```

**输出文件命名规则：**
```
python-backend-{target}
```

其中 `{target}` 根据平台自动确定：
| 平台 | Target |
|------|--------|
| macOS (Apple Silicon) | `aarch64-apple-darwin` |
| macOS (Intel) | `x86_64-apple-darwin` |
| Linux (ARM64) | `aarch64-unknown-linux-gnu` |
| Linux (x86_64) | `x86_64-unknown-linux-gnu` |

**脚本执行流程：**
```
1. 检测平台和架构
       ↓
2. 创建临时构建目录
       ↓
3. 复制后端代码到临时目录
       ↓
4. 创建桌面版入口脚本 (desktop_main.py)
       ↓
5. 创建虚拟环境并安装依赖
       ↓
6. 生成 PyInstaller spec 文件
       ↓
7. 运行 PyInstaller 打包
       ↓
8. 复制产物到 binaries/ 目录
       ↓
9. 清理临时文件
```

**自定义打包选项：**

如需修改打包配置，编辑脚本中的 `backend.spec` 部分：

```python
# 添加隐藏导入 (某些动态导入的模块)
hiddenimports=[
    'uvicorn.logging',
    'aiosqlite',
    # 添加更多...
],

# 添加数据文件
datas=[
    ('config.json', '.'),
    # 添加更多...
],
```

### scripts/build.sh - 完整构建脚本

此脚本执行完整的构建流程，包括后端打包和 Tauri 应用构建。

**功能：**
- 检查所有构建前提条件
- 调用 `build-backend.sh` 打包 Python 后端
- 安装前端依赖
- 构建 Tauri 应用程序
- 生成安装包 (DMG/DEB/AppImage)

**使用方法：**
```bash
# 直接运行
./scripts/build.sh

# 或通过 npm
npm run build:all
```

**脚本执行流程：**
```
1. 检查前提条件
   ├── Node.js 版本
   ├── npm 版本
   ├── Python 版本
   └── Rust/Cargo 版本
       ↓
2. 构建 Python 后端 (调用 build-backend.sh)
       ↓
3. 安装前端依赖 (npm install)
       ↓
4. 构建 Tauri 应用 (npm run tauri build)
       ↓
5. 显示构建产物位置
```

**前提条件检查：**

脚本会检查以下工具是否已安装：
- `node` - Node.js 运行时
- `npm` - Node.js 包管理器
- `python3` - Python 3 解释器
- `cargo` - Rust 包管理器

如果任何工具缺失，脚本会报错并退出。

### npm 脚本命令

在 `package.json` 中定义了以下构建相关命令：

```json
{
  "scripts": {
    "build:backend": "./scripts/build-backend.sh",
    "build:all": "./scripts/build.sh",
    "tauri:build": "tauri build",
    "tauri:dev": "tauri dev"
  }
}
```

| 命令 | 说明 |
|------|------|
| `npm run build:backend` | 仅打包 Python 后端 |
| `npm run build:all` | 完整构建 (后端 + 前端 + Tauri) |
| `npm run tauri:build` | 仅构建 Tauri 应用 (需先打包后端) |
| `npm run tauri:dev` | 开发模式运行 |

### 构建故障排除

**问题：build-backend.sh 执行失败**

1. 检查 Python 版本：
   ```bash
   python3 --version  # 需要 3.11+
   ```

2. 检查 PyInstaller 是否安装：
   ```bash
   pip install pyinstaller
   ```

3. 查看详细错误：
   ```bash
   ./scripts/build-backend.sh 2>&1 | tee build.log
   ```

**问题：缺少 Python 模块**

如果 PyInstaller 打包后运行时报 `ModuleNotFoundError`，需要在 `backend.spec` 的 `hiddenimports` 中添加缺失的模块。

**问题：二进制文件过大**

可以通过以下方式减小体积：
1. 使用 `--strip` 移除调试符号
2. 使用 UPX 压缩 (在 spec 文件中启用 `upx=True`)
3. 排除不需要的模块

---

## 项目结构

```
desktop/
├── src/                          # React 前端源码
│   ├── components/               # UI 组件
│   │   ├── common/              # 通用组件 (Layout, Sidebar)
│   │   ├── chat/                # 聊天相关组件
│   │   ├── agents/              # Agent 管理组件
│   │   ├── skills/              # 技能管理组件
│   │   └── mcp/                 # MCP 服务器组件
│   ├── pages/                   # 页面组件
│   │   ├── ChatPage.tsx
│   │   ├── AgentsPage.tsx
│   │   ├── SkillsPage.tsx
│   │   ├── SettingsPage.tsx     # 桌面版设置页面
│   │   └── ...
│   ├── services/
│   │   ├── api.ts               # API 客户端 (动态端口)
│   │   ├── tauri.ts             # Tauri IPC 桥接
│   │   └── ...
│   ├── hooks/                   # React Hooks
│   ├── types/                   # TypeScript 类型定义
│   └── App.tsx                  # 应用入口
│
├── src-tauri/                   # Tauri/Rust 后端
│   ├── src/
│   │   ├── lib.rs               # Rust 主逻辑
│   │   │                        # - Sidecar 管理
│   │   │                        # - CLI 检测/安装
│   │   │                        # - 后端状态管理
│   │   └── main.rs              # 入口点
│   ├── binaries/                # Sidecar 可执行文件
│   │   └── python-backend-*     # PyInstaller 打包的后端
│   ├── icons/                   # 应用图标
│   ├── Cargo.toml               # Rust 依赖
│   ├── tauri.conf.json          # Tauri 配置
│   └── capabilities/            # 权限配置
│
├── resources/
│   └── skill-registry.json      # 推荐技能列表
│
├── scripts/
│   ├── build.sh                 # 完整构建脚本
│   └── build-backend.sh         # 后端打包脚本
│
├── package.json                 # 前端依赖和脚本
├── vite.config.ts               # Vite 配置
├── tailwind.config.js           # Tailwind CSS 配置
├── backend.env.example          # 桌面版环境配置模板
└── BUILD_GUIDE.md               # 本文档
```

---

## 配置说明

### 桌面版环境变量 (backend/.env)

```env
# 应用模式
DEBUG=false

# 服务器配置
HOST=127.0.0.1
PORT=8000

# 数据库 - 桌面版使用 SQLite
DATABASE_TYPE=sqlite
# SQLITE_DB_PATH 留空则使用默认路径:
# - macOS: ~/Library/Application Support/Owork/data.db
# - Linux: ~/.local/share/owork/data.db

# Claude API 配置 (必须)
ANTHROPIC_API_KEY=sk-ant-xxx

# 模型配置
DEFAULT_MODEL=claude-sonnet-4-5-20250929

# 桌面版不使用 Bedrock
CLAUDE_CODE_USE_BEDROCK=false

# 速率限制 (本地可以更宽松)
RATE_LIMIT_PER_MINUTE=1000
```

### Tauri 配置 (src-tauri/tauri.conf.json)

关键配置项：

```json
{
  "productName": "Owork",
  "version": "1.0.0",
  "identifier": "com.owork.desktop",
  "build": {
    "devUrl": "http://localhost:1420",
    "frontendDist": "../dist"
  },
  "bundle": {
    "targets": ["dmg", "app"],  // macOS
    // "targets": ["deb", "appimage"],  // Linux
    "externalBin": ["binaries/python-backend"]
  }
}
```

### 数据存储位置

| 平台 | 数据目录 |
|------|----------|
| macOS | `~/Library/Application Support/Owork/` |
| Linux | `~/.local/share/owork/` |

目录结构：
```
Owork/
├── data.db              # SQLite 数据库
├── skills/              # 本地技能
│   ├── skill-name/
│   │   ├── .git/       # Git 版本控制
│   │   ├── SKILL.md
│   │   └── ...
│   └── ...
├── workspaces/          # Agent 工作空间
└── logs/                # 日志文件
```

---

## 常见问题

### Q: 构建时提示找不到 webkit2gtk

**A:** 安装 WebKit 开发库：
```bash
# Ubuntu/Debian
sudo apt-get install libwebkit2gtk-4.1-dev

# Fedora
sudo dnf install webkit2gtk4.1-devel
```

### Q: Rust 编译失败，提示 Emitter trait 错误

**A:** 确保 `src-tauri/src/lib.rs` 中包含以下导入：
```rust
use tauri::{Emitter, Manager};
```

### Q: Python 后端启动失败

**A:** 检查以下几点：
1. 确保 `python-backend-*` 文件存在于 `src-tauri/binaries/`
2. 确保文件有执行权限: `chmod +x binaries/python-backend-*`
3. 检查端口是否被占用

### Q: 首次运行没有数据

**A:** 这是正常的。SQLite 数据库会在首次运行时自动创建。

### Q: 如何安装技能？

**A:** 桌面版支持两种方式：
1. **Git URL:** 在技能管理页面输入 Git 仓库地址
2. **ZIP 上传:** 上传包含 SKILL.md 的 ZIP 文件

### Q: Claude Code CLI 未安装

**A:** 在设置页面可以自动安装，或手动运行：
```bash
npm install -g @anthropic-ai/claude-code
```

### Q: 如何更新技能？

**A:** 对于 Git 安装的技能，在技能详情页点击"更新"按钮，会执行 `git pull`。

### Q: 应用无法签名 (macOS)

**A:** 未签名的应用首次打开时，需要：
1. 右键点击应用
2. 选择"打开"
3. 在弹出的对话框中点击"打开"

或在终端运行：
```bash
xattr -cr "/Applications/Owork.app"
```

---

## 构建优化

### 减小构建体积

1. **PyInstaller 优化:**
   ```bash
   # 在 build-backend.sh 中添加
   pyinstaller --onefile --strip --clean
   ```

2. **Tauri 发布构建:**
   ```bash
   npm run tauri build -- --release
   ```

### 加速构建

1. **使用 Rust 缓存:**
   ```bash
   export CARGO_INCREMENTAL=1
   ```

2. **并行编译:**
   ```bash
   export CARGO_BUILD_JOBS=8
   ```

---

## 版本发布检查清单

- [ ] 更新版本号 (`package.json`, `tauri.conf.json`, `Cargo.toml`)
- [ ] 测试所有核心功能
- [ ] 测试首次启动体验
- [ ] 测试技能安装/更新
- [ ] 测试 Claude Code CLI 检测/安装
- [ ] 生成构建产物
- [ ] 测试安装包 (DMG/DEB/AppImage)
- [ ] 更新 CHANGELOG

---

## 联系与支持

如遇到问题，请：
1. 查看日志文件 (`~/Library/Logs/Owork/` 或终端输出)
2. 检查 [常见问题](#常见问题) 部分
3. 提交 Issue 到 GitHub 仓库

---

*最后更新: 2025-01-19*
