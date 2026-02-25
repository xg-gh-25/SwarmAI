# Claude Agent Platform - Desktop Edition

基于 Tauri 2.0 构建的跨平台桌面应用，支持 macOS 和 Linux。

## 快速开始

### 前提条件

- Node.js 18+
- Rust 1.70+
- Python 3.11+
- Git

### 开发模式

```bash
# 1. 安装依赖
npm install

# 2. 配置环境变量
cp backend.env.example ../backend/.env
# 编辑 ../backend/.env，设置 ANTHROPIC_API_KEY

# 3. 启动开发服务器
npm run tauri:dev
```

### 生产构建

```bash
npm run build:all
```

构建产物位于 `src-tauri/target/release/bundle/`

## 文档

详细构建说明请参阅 [BUILD_GUIDE.md](./BUILD_GUIDE.md)

## 技术栈

| 组件 | 技术 |
|------|------|
| 桌面框架 | Tauri 2.0 |
| 前端 | React 19 + TypeScript + Vite |
| 后端 | Python FastAPI (Sidecar) |
| 数据库 | SQLite |
| 样式 | Tailwind CSS 4 |

## 项目结构

```
desktop/
├── src/              # React 前端
├── src-tauri/        # Tauri/Rust 后端
├── scripts/          # 构建脚本
├── resources/        # 资源文件
└── BUILD_GUIDE.md    # 完整构建指南
```

## 与云端版本的区别

- 使用 SQLite 数据库
- 使用本地文件系统 + Git 替代 S3 存储
- 移除用户认证 (单用户本地应用)
- 直接使用 Anthropic API (非 Bedrock)

## 推荐 IDE 设置

- [VS Code](https://code.visualstudio.com/) + [Tauri](https://marketplace.visualstudio.com/items?itemName=tauri-apps.tauri-vscode) + [rust-analyzer](https://marketplace.visualstudio.com/items?itemName=rust-lang.rust-analyzer)

## 许可证

MIT
