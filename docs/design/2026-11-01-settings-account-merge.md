---
created: 2026-11-01
updated: 2026-11-01
title: "设置页合并：Swarm AWS Account、LLM Models，登录过期一键修"
status: r0 — 待 XG 定 §8 两项
related: docs/design/2026-10-31-swarmai-aws-account.md
---

# 设置页合并：Swarm AWS Account、LLM Models，登录过期一键修

r0 · 2026-11-01 · 接着 `2026-10-31-swarmai-aws-account.md`（账号记录、能力检查、Hive 数量都已实现）。
Mockup：`Knowledge/Mockups/2026-11-01-settings-aws-account-merge.html`（六屏）。

## 1. 一句话

和 AWS 有关的设置只放在一个地方（Swarm AWS Account）；模型设置只管模型（LLM Models）；登录过期时，
提示条一键打开 SwarmAI 自己的终端并运行登录命令，登录完成后自动接上。

## 2. 现在的问题（2026-11-01 读过代码和截图）

- **过期提示**（`components/common/CredentialBanner.tsx`）：`fixed top-3` 的浮层，盖住聊天 tab 栏，打开设置后还压在
  设置页上。正文让人"在终端运行 `mwinit -f`"，但没说哪个终端；唯一的按钮"Open Settings"打开的账号页也只是再说一遍
  （`AwsAccountTab.tsx` 的 remedy 一行还把同一句话说了两遍：译文 + 括号里的后端原文）。
- **同一件事三个地方，互相矛盾**：
  - SwarmAI Account（`AwsAccountTab.tsx`）：账号、状态、能力、Hive 数量。截图里显示"Midway 已过期"。
  - AI & Models（`AIModelsTab.tsx:198-201` 挂着 `AuthConfigPanel`，562 行）：Ada / SSO / Bedrock API key 卡片、
    账号 ID、区域、ADA 角色、"Verify Connection"。同一时刻显示"ADA credentials active"，还给出
    `ada credentials update --account=… --role=Admin --provider=isengard`（没有 `--profile`，会覆盖用户的默认
    profile，正是账号设计 §5 不再让人跑的那条命令）。
  - Hive Accounts（`HiveTab.tsx`）：有 SwarmAI 账号时只列 SwarmAI 那一行和别的账号里还有 Hive 的行
    （`services/hive.ts:hiveAccountView`），不能新增。
- 状态标签"Sign in again"做成了带边框的胶囊，看起来像按钮，点了没反应。

## 3. 新的划分

| Tab | 管什么 | 来自 |
|---|---|---|
| General | 不变 | — |
| **Swarm AWS Account**（id 仍是 `aws-account`） | 账号和登录方式、主区域、能用什么、Hive 数量和每台 Hive、别的账号里留下的 Hive、详情（身份、重新核实、检查能力、权限模板） | 原 SwarmAI Account + AI & Models 的区域 + Hive Accounts |
| **LLM Models**（id 仍是 `ai-models`，深链接不变） | 从哪里调用模型、默认模型、可用模型、思考 | 原 AI & Models 去掉 AWS 部分 |
| Channels / Backup / Core Engine / System / Capabilities | 不变 | — |

去掉：Hive Accounts tab（`HiveTab.tsx`）；设置页里的 `AuthConfigPanel`（Hive 模式的安装向导还用它，组件保留给那里）。

**"认证方式都放到账号页吗"——我的判断：AWS 的登录方式全部放账号页；"从哪里调用模型"留在 LLM Models。**
Bedrock API key 和 Anthropic key 只是调用模型的钥匙，不是 AWS 账号：一个只有 Anthropic key 的外部用户不该被送到
"AWS 账号"页去填。LLM Models 里这一项只有三个选项，默认"Bedrock，经 Swarm AWS 账号"，旁边写账号和区域并链接到
账号页；另外两项注明"Hive、云存储、语音仍需要 Swarm AWS 账号"。这和账号设计 §5.1"对话的路和 AWS 账号分开"一致，
读法不变（`aws_account.get_chat_via()`）。备选：三项也搬进账号页、LLM Models 只剩模型——更少一个地方，但外部用户
要去 AWS 页填 Anthropic key。要改成这样说一声。

## 4. 登录过期一键修

### 4.1 提示条（mockup ②）

- 从浮层改成一条**在页面流里**的提示条，贴在聊天 tab 栏下面，占一行，把下面的内容往下推，不盖任何东西。
- 内容：一个状态点 + "Midway 登录已过期" + 灰字"对话、Hive 和语音暂时用不了" + 一个主按钮 + "账号设置"链接 + ×。
  × 只收起到状态下次变化。打开设置时不显示（设置页本身显示同一状态，不说两遍）。
- 挂载位置从 App 根节点移到应用外壳（`ThreeColumnLayout`）：它需要终端，终端只在外壳里。安装向导里不显示，向导的
  账号步骤自己报登录问题。

### 4.2 主按钮按登录方式变

后端 `remediation` 加一个结构化的 `action`（今天只有 `message` / `fix_text` / `settings_tab` 三段文字）：

| 登录方式（`remediation_method`） | `action` | 主按钮 |
|---|---|---|
| `ada_account`（ADA） | `{kind: "terminal", command: "mwinit -f", title: "Midway 登录"}` | 在终端登录 |
| `profile`（~/.aws 里的 SSO profile） | `{kind: "terminal", command: "aws sso login --profile <名字>"}` | 在终端登录 |
| `sso_app`、`aws_login` | `{kind: "sign_in"}` | 重新登录（打开账号页并直接进入该方式的登录） |
| `access_keys`、`account_broken`、`account_mismatch`、其余 | `{kind: "settings", tab}` | 去账号设置 |

命令只由后端从固定模板生成，前端不拼。profile 名字来自记录，写进命令前按 `[A-Za-z0-9_.@-]+` 校验，不合格就退回
`settings`：这串字会被写进一个真实 shell，不能让一个改过的 config 变成命令注入。

### 4.3 点了之后（mockup ③）

1. 提示条发 `swarm:terminal-run` 事件 `{command, title}`；外壳收到后打开终端面板，新开一个 shell（标题用 `title`），
   写入命令并回车（`TerminalStore.openTerminal` + `pty.write`）。人在终端里输 PIN、碰安全钥匙。
2. 提示条变成"在下面的终端里完成 Midway 登录 · 完成后自动接上"，按钮"已完成，检查一下"。每 5 秒
   `POST /api/aws-account/verify` 一次。
3. 成功：提示条变绿 2 秒"已重新登录 · <账号>，刚才没发出去的消息可以重新发送"，然后消失；终端留着，人自己关。
4. 5 分钟还没成功：停止自动检查（这是停止轮询，不是限制任何工作），提示条回到 ②，按钮改成"重新检查"。
5. 外壳没挂载（极少：启动早期）或不是桌面版：按钮退回"复制命令"。

### 4.4 账号页的同一状态（mockup ④）

- 状态一行（彩色点 + 一句话），不再用像按钮的胶囊；需要人做事时只有一个主按钮，和提示条同一个 `action`。
- remedy 只说一遍。

## 5. 账号页的版式（mockup ⑤）

按"这一页要做的决定"排：①账号对不对、能不能用（一行 + 状态点，正常时没有大按钮）；②主区域；③能用什么（一句话，
缺什么才展开）；④Hive 要几台（页面上唯一的主控件）+ 每台一行；⑤别的账号里的 Hive（只能停止、删除，复用
`hiveComponents` 的 `AccountCard` 现有动作）；⑥详情（身份、来源、重新核实、检查能力、复制权限模板）。

没有 SwarmAI 账号的老用户（`hiveAccountView` 里 `swarmai` 为空）：第①块是"连接 AWS 账号"，⑤ 列出他们原来的
Hive 账号；不再提供"添加 Hive 账号"——连接账号就是新的路。

## 6. 要删、要改的东西

- 删：`components/settings/HiveTab.tsx`；`AIModelsTab.tsx` 里的 AWS Account 段；`AwsAccountTab` 里重复的 remedy 括号。
- 改：`SettingsTabs.tsx`（标签名、去掉 `hive`）；`ConnectAccount.tsx:409` 指向 `hive` tab 的链接改到 `aws-account`；
  `CredentialBanner.tsx` 重写并移到外壳；`auth_remediation.py` 加 `action`；`_SETTINGS_TAB` 的文案"AI & Models"
  改成"LLM Models"；`AwsAccountTab` 加主区域选择（写 `aws_region`，和 `AuthConfigPanel` 今天写的同一个键）。
- 不动：后端 `/api/system/auth-method`、`rederive`（Hive 模式向导还用）；`aws_account` 记录和所有读者。

## 7. 验证计划（定在这里，不在做的中途加）

**谁读写的东西：** 新的 `action` 由 `CredentialBanner` 和 `AwsAccountTab` 读，先写读者（§4.2 表）再改后端。
`aws_region` 已有唯一读者 `aws_account.region()`。

**最终测试集：**
1. `remediation` 每种登录方式给出 §4.2 表里的 `action`；profile 名字不合格时退回 `settings`（含 `;`、空格、`$()` 三例）。
2. 提示条：`terminal` 时点主按钮发出带正确命令的事件；外壳收到后打开一个 shell 并写入"命令 + 回车"；验证成功后提示
   条消失；5 分钟后停止检查；设置打开时不显示；没有外壳时是"复制命令"。
3. 设置页：没有 `hive` tab；`ai-models` 深链接打开 LLM Models；LLM Models 里没有任何 AWS 登录控件；账号页的主区域
   改动写进 `aws_region`；"别的账号里的 Hive"只在有 Hive 时出现，只有停止和删除。
4. 不变的：没有账号的老用户能连接账号，原来的 Hive 账号仍列出；Hive 模式的安装向导仍用 `AuthConfigPanel`；
   `get_chat_via()` 对三个选项的结果和今天相同。
5. `npm run build` 的包大小检查通过；前端每个新逻辑做一次 mutation（去掉就变红）。

**审查：** 实现后 correctness + ux 各一轮；终端写入命令那一段加 security（§4.2 的校验、命令只由后端生成）。

**端到端：** build 并重启后（要你同意），让 Midway 过期一次，从提示条点到重新接上，录一下每一步。

## 8. 要你定

1. LLM Models 保留"从哪里调用模型"三选一（推荐），还是三项都搬进账号页（§3）。
2. 终端里命令直接回车运行（推荐，人只需输 PIN），还是只填好命令等人自己回车。
