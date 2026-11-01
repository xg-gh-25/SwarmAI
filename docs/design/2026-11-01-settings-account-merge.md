---
created: 2026-11-01
updated: 2026-11-01
title: "设置页合并：Swarm AWS Account、LLM Models，登录过期一键修"
status: r3 — 已实现（2026-11-01）；设计审查和实现审查见 §9；XG 已定（§8）：模型调用两选一；终端只填命令不回车；去掉 Core Engine、Capabilities tab；不再支持 Anthropic key（§3.1）
related: docs/design/2026-10-31-swarmai-aws-account.md
---

# 设置页合并：Swarm AWS Account、LLM Models，登录过期一键修

r2 · 2026-11-01 · 接着 `2026-10-31-swarmai-aws-account.md`（账号记录、能力检查、Hive 数量都已实现）。
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
| Channels / Backup / System | 不变 | — |

去掉：Hive Accounts tab（`HiveTab.tsx`）；设置页里的 `AuthConfigPanel`（Hive 模式的安装向导还用它，组件保留给那里）；
**Core Engine**（`EngineMetricsTab.tsx`，旧的成长指标面板）和 **Capabilities**（`CapabilitiesTab.tsx`，这个二进制里有哪些
模块）两个 tab（XG 定）。前端没有别的读者；后端 `GET /api/system/engine-metrics`、`GET /api/system/capabilities` 和
`core/engine_metrics.py` 另有读者要查，不在这次删（记一条待办）。左侧导航的 Capabilities 卡片（skills / MCP）不受影响。

**认证方式（XG 定）：AWS 的登录方式全部放账号页；LLM Models 里"从哪里调用模型"只有两项：经 Swarm AWS 账号走
Bedrock（默认）、Bedrock API key。不支持 Anthropic API key**（r3，见 §3.1）。LLM Models 里默认一项旁边写账号和
区域并链接到账号页；Bedrock key 那一项注明"Hive、云存储、语音仍需要 Swarm AWS 账号"。读法不变
（`aws_account.get_chat_via()`，现在只有 `aws`、`bedrock_key` 两个值）。

### 3.1 不支持 Anthropic key（r3，XG 2026-11-01）

目标是不挡住任何人启动 SwarmAI，同时只留一条对话路：Bedrock。
- **对话：** `claude_environment` 总是设 `CLAUDE_CODE_USE_BEDROCK`，从不注入 `ANTHROPIC_API_KEY`；旧版本注入进这个
  长寿进程的那一把（等于存着的密钥）会被拿掉，用户自己 export 的不动。没有"没配置认证就报错"的检查了。
- **老用户：** daemon 启动时 `aws_account.retire_anthropic_direct` 把 `use_bedrock=false` / `auth_method=apikey` 改成
  `use_bedrock=true`，`auth_method` 取账号记录的登录方式（ada、Hive 的 iam_role），否则不设（拿通用的修复提示，
  不猜命令）。账号记录建不出来时也照样改。存着的 key 留在密钥库里不用，不删任何人存过的东西。之后对话走本机的
  AWS 凭证或账号；不能用时由提示条和错误卡片指到账号页。
- **删掉的：** `/api/system/anthropic-api-key`、Anthropic 的核实路径、`auth-hint` 里的 `has_api_key`、设置页的
  `anthropic_api_key_configured`、`apikey` 的 remediation；前端的 Anthropic 卡片（Hive 网页版的认证面板也去掉）。
- **没有账号记录的老用户打开 LLM Models：** 默认一项如实写"Bedrock，用本机的 AWS 凭证"和"还没连接 Swarm AWS 账号"，
  点它打开账号页去连接；不再显示成灰掉、点不了。
- **向导里 Midway 过期：** 仍然写"打开本机的终端，运行 `mwinit -f`"，人用自己的终端（向导运行时 SwarmAI 终端还没有）。
  设置页"更换账号 → ADA"的同一等待页有 SwarmAI 终端，多一个"在终端登录"按钮（填好 `mwinit -f`，不回车）。
- **聊天里的 Credentials Expired 卡片：** 加"修复登录"按钮。它不自己决定怎么修：发 `FIX_SIGN_IN_EVENT` 给提示条，
  提示条先核实一次，已经登上就显示"已重新登录"，否则显示自己并执行它那一个动作。卡片和提示条因此不会说两套话；
  发消息前的检查常比 health 的下一次采样先发现过期，所以提示条在被请求时即使 health 还没报过期也会出现。

## 4. 登录过期一键修

### 4.1 提示条（mockup ②）

- 从浮层改成一条**在页面流里**的提示条，贴在聊天 tab 栏下面，占一行，把下面的内容往下推，不盖任何东西。
- 内容：一个状态点 + "Midway 登录已过期" + 灰字"对话、Hive 和语音暂时用不了" + 一个主按钮 + "账号设置"链接 + ×。
  × 收起到（状态、方式、动作）下次变化。打开设置时不显示（设置页本身显示同一状态，不说两遍）。
- 挂载位置从 App 根节点移到应用外壳（`ThreeColumnLayout`）：它需要终端，终端只在外壳里。安装向导里不显示，向导的
  账号步骤自己报登录问题。

### 4.2 主按钮按登录方式变

后端 `remediation` 加一个结构化的 `action`（今天只有 `message` / `fix_text` / `settings_tab` 三段文字）：

| 登录方式（`remediation_method`） | `action` | 主按钮 |
|---|---|---|
| `ada_account`（ADA） | `{kind: "terminal", command: "mwinit -f", title: "Midway 登录"}` | 在终端登录 |
| `profile`，且 ~/.aws 里这个 profile 是 SSO（有 `sso_session` 或 `sso_start_url`） | `{kind: "terminal", command: "aws sso login --profile <名字>"}` | 在终端登录 |
| 没有在用的账号记录（老用户、这个版本解析不了的来源、Hive）：按旧的 `auth_method` | ada → `mwinit -f`、sso → `aws sso login`（终端）；iam_role、两种 key → `settings`（LLM Models）；没设 → `settings`（账号页） | 同上 |
| `sso_app`、`aws_login` | `{kind: "sign_in"}` | 重新登录（打开账号页并直接进入该方式的登录） |
| `access_keys`、`account_broken`、`account_mismatch`、其余 | `{kind: "settings", tab}` | 去账号设置 |

命令只由后端从固定模板生成，前端不拼。profile 名字来自记录，写进命令前用 `fullmatch` 按
`[A-Za-z0-9_][A-Za-z0-9_.@-]{0,63}` 校验（只有 ASCII，不能以 `-` 开头，不能有换行），不合格或不是 SSO profile 就退回
`settings`：这串字会被写进一个真实 shell，不能让一个改过的 config 变成命令注入或一个选项。提示条只读
`GET /api/aws-account` 的 `remediation`（不再自己按 auth-hint 拼一份文字），所以所有用户都只有一份说法。
旧的 ada、sso 和兜底的深链接改到账号页（LLM Models 里不再有登录控件）。

### 4.3 点了之后（mockup ③）

1. 提示条发 `swarm:terminal-run` 事件 `{command, title}`；外壳收到后在**当前的终端位置**打开一个新 shell（标题用
   `title`；不在当前位置的 shell 面板看不到，面板还会自己再开一个空的），**等 shell 启动完**（第一次输出之后安静
   400 毫秒，最多等 5 秒）再**只把命令填进提示符，不回车**（XG 定）：人看一眼、自己按回车，再输 PIN、碰安全钥匙。
   启动脚本还在跑时写进去，命令会被它的提问吃掉或显示两遍。
2. 提示条变成"在下面的终端里按回车，完成 Midway 登录 · 完成后自动接上"，按钮"已完成，检查一下"。上一次检查
   回来 5 秒后再 `POST /api/aws-account/verify` 一次（同一时间只有一个请求，带 App 的请求头）；打开设置或卸载时停止。
3. 成功：提示条变绿 2 秒"已重新登录 · <账号>，刚才没发出去的消息可以重新发送"，然后消失；终端留着，人自己关。
4. 5 分钟还没成功：停止自动检查（这是停止轮询，不是限制任何工作），提示条回到 ②，按钮改成"重新检查"。
5. 外壳没挂载（极少：启动早期）或不是桌面版：按钮退回"复制命令"。

### 4.4 账号页的同一状态（mockup ④）

- 状态一行（彩色点 + 一句话），不再用像按钮的胶囊；需要人做事时只有一个主按钮，和提示条同一个 `action`。
- remedy 只说一遍。

## 5. 账号页的版式（mockup ⑤）

按"这一页要做的决定"排：①账号对不对、能不能用（一行 + 状态点，正常时没有大按钮）；②主区域；③能用什么（一句话，
缺什么才展开）；④Hive 要几台（页面上唯一的主控件）+ 每台一行；⑤别的账号里的 Hive（复用 `hiveComponents` 的 `AccountCard`：核实、删除）；⑥详情（身份、来源、重新核实、检查能力、复制权限模板）。

没有 SwarmAI 账号的老用户（`hiveAccountView` 里 `swarmai` 为空）：第①块是"连接 AWS 账号"，⑤ 原样搬来 Hive Accounts
tab 的内容（`hiveAccountView` 的 `shown`、`AccountCard` 的核实和删除、`canAdd` 时的"添加账号"），和左侧 Hive 卡片里
的一样，他们原来的部署路不变。有 SwarmAI 账号时 ⑤ 只列别的账号里还有 Hive 的行。

## 6. 要删、要改的东西

- 删：`components/settings/HiveTab.tsx`；`AIModelsTab.tsx` 里的 AWS Account 段；`AwsAccountTab` 里重复的 remedy 括号。
- 改：`SettingsTabs.tsx`（标签名、去掉 `hive`）；`ConnectAccount.tsx:409` 指向 `hive` tab 的链接改到 `aws-account`；
  `CredentialBanner.tsx` 重写并移到外壳；`auth_remediation.py` 加 `action`；`_SETTINGS_TAB` 的文案"AI & Models"
  改成"LLM Models"；`AwsAccountTab` 加主区域选择（写 `aws_region`，和 `AuthConfigPanel` 今天写的同一个键）。
- 不动：后端 `/api/system/auth-method`、`rederive`（Hive 模式向导还用）；`aws_account` 记录和所有读者。
- **Hive 上**（`runMode === 'hive'`）：LLM Models 仍然放 `AuthConfigPanel`，那是 Hive 上看实例角色、改区域、换成
  Bedrock key 的唯一地方（安装向导的"以后再修 IAM 权限"指向它）。
- **LLM Models 两个选项写什么：** "经 Swarm AWS 账号"＝`awsAccountService.useForChat`（今天账号页"改回用账号对话"
  的同一段：`use_bedrock=true`、按记录的来源写回 ada / sso）；没有账号记录时这一项不可选，写"先在 Swarm AWS
  Account 连接账号"并链接过去。"Bedrock API key"＝向导"先只用对话"的同一个表单（先验证、成功才存 key）。
- **旧深链接：** `hive` → `aws-account`；`engine`、`capabilities` → General。`OPEN_SETTINGS_EVENT` 移到一个中立的小
  文件，提示条和其他五处都从那里引。

## 7. 验证计划（定在这里，不在做的中途加）

**谁读写的东西：** 新的 `action` 由 `CredentialBanner` 和 `AwsAccountTab` 读，先写读者（§4.2 表）再改后端。
`aws_region` 已有唯一读者 `aws_account.region()`。

**最终测试集：**
1. `remediation` 每种登录方式给出 §4.2 表里的 `action`；profile 名字不合格时退回 `settings`（含 `;`、空格、`$()`、
   开头的 `-`、换行、超长）；不是 SSO 的 profile 退回 `settings`；没有账号记录时按 `auth_method` 给（含 Hive）。
2. 提示条：`terminal` 时点主按钮发出带正确命令的事件；外壳收到后在当前位置（含一个 repo 位置）打开一个 shell，
   等到第一次输出之后才只写入命令（没有回车）；验证成功后提示
   条消失；5 分钟后停止检查；设置打开时不显示；没有外壳时是"复制命令"。
3. 设置页：没有 `hive` tab；`ai-models` 深链接打开 LLM Models；LLM Models 里没有任何 AWS 登录控件；账号页的主区域
   改动写进 `aws_region`；有 SwarmAI 账号时"别的账号里的 Hive"只在有 Hive 时出现，没有时原样列出老的 Hive 账号；
   `hive` 深链接打开账号页；Hive 上 LLM Models 仍有 `AuthConfigPanel`；没有 Core Engine、Capabilities tab；
   模型调用只有两项，任何情况都没有 Anthropic 一项；向导的"先只用对话"没有 Anthropic。
4. 不变的：没有账号的老用户能连接账号，原来的 Hive 账号仍列出；Hive 模式的安装向导仍用 `AuthConfigPanel`；
   `get_chat_via()` 对 Bedrock 两个选项的结果和今天相同（r3：旧的 Anthropic 设置得到 `aws`）。
5. `npm run build` 的包大小检查通过；前端每个新逻辑做一次 mutation（去掉就变红）。

**审查：** 实现后 correctness + ux 各一轮；终端写入命令那一段加 security（§4.2 的校验、命令只由后端生成）。

**端到端：** build 并重启后（要你同意），让 Midway 过期一次，从提示条点到重新接上，录一下每一步。

## 8. XG 的决定（2026-11-01）

1. 模型调用方式留在 LLM Models，只保留经 Swarm AWS 账号走 Bedrock（默认）和 Bedrock API key。
2. 终端里只填好命令，人自己按回车。
3. 去掉 Core Engine、Capabilities 两个 tab。
4. （r3）不支持 Anthropic key；向导里 Midway 过期仍让人用自己的终端跑 `mwinit -f`，设置页里可以用 SwarmAI 终端；
   Hive 网页版一起清掉；目标是不挡住任何人启动 SwarmAI（§3.1）。

## 9. 审查记录

**r1 → r2：** 一次零上下文审查（correctness），3 HIGH、7 MED、3 LOW，核实后吸收：
- **终端：** shell 要开在当前的终端位置（否则面板看不到，还会自己再开一个空的）；命令要等 shell 启动完再写
  （否则被启动脚本的提问吃掉或显示两遍）；`OpenTerminalOptions` 加 `title`。
- **提示条不能丢掉没有账号记录的人：** 后端对不在用账号的情况也按旧的 `auth_method` 给 remediation 和 action（老用户、
  Hive），前端只读这一份；旧 ada、sso 和兜底的深链接改到账号页。
- **profile：** 只有 SSO profile 给 `aws sso login --profile`；名字规则收紧（不以 `-` 开头、64 字以内、不能有换行）。
- **Hive 上**保留 LLM Models 里的 `AuthConfigPanel`；老用户的 Hive 账号原样搬到账号页，不去掉"添加账号"。
- **LLM Models 的写入**定下来（复用现有两段代码）；检查轮询一次一个、带请求头、设置打开时停；旧 tab 深链接改指向；
  `OPEN_SETTINGS_EVENT` 移到中立文件；收起按（状态、方式、动作）。

**实现后的审查（correctness、security、ux 各一轮）：** 没有 HIGH；correctness 5 MED、4 LOW，security 2 LOW，ux 4 个
重要、1 个偏离设计、7 个建议。核实后按原因一次改完：
- **提示条的状态：** 打开设置时停止检查，关掉后马上再查一次并继续；凭证恢复后所有状态（收起、等待、停止）都清掉，
  下次过期重新开始；成功提示用自己的计时器，开设置不会让它卡住；同一时间只发一个核实请求；StrictMode 下也能用。
- **账号页只说一遍：** 有终端动作时状态行只说哪里坏了，怎么做在按钮和它下面那一行里（按回车，再按提示输 PIN、碰钥匙
  或在浏览器里确认）；提示条刚在终端填过命令时，账号页直接把"重新核实"作为主按钮；"未核实"的主按钮是"重新核实"；
  `sign_in` 从提示条点过来时直接进入 App 里的登录。
- **LLM Models：** Bedrock key 的表单可以取消；中文"在 Swarm AWS Account 里改"补全；正文统一叫 Swarm AWS Account。
- **没有账号记录的人：** 桌面上总能改主区域；Hive 上提示永远是实例角色（不看留下来的 `auth_method`）；还在用
  Anthropic key 的人，提示改成"到 LLM Models 选 Bedrock API key 或经账号的 Bedrock"（r3 起 Anthropic 不再支持，这条提示也删了）。
- **测试：** profile 名字的坏例子都写成真实的 SSO profile，确保是名字规则挡住的；加一条"agent 的 ui_command 够不到
  终端事件"。

**r3（不支持 Anthropic key）的实现审查（correctness、security 各一轮）：** 没有 HIGH；correctness 2 MED、5 LOW，
security 1 MED、3 LOW。核实后按原因一次改完：
- **只剩 Bedrock 一条路，所有读者都要知道：** 聊天（`prompt_builder`）和 job（`jobs/model_resolve`）的模型名不再看旧的
  `use_bedrock`，一律转成 Bedrock 的名字；否则启动时那次改写还没跑（或 config.json 写不进去）时，旧 Anthropic 用户发出去的
  是 Bedrock 不认的名字。
- **改写只动该动的：** 只有 `auth_method=apikey` 才换方法；`use_bedrock=false` 配着 Bedrock key、sso 等，只把开关改回来，
  方法保留。测试也查了 config.json 里没有写进密钥。
- **提示条被卡片请求时：** 只有终端动作会让它留在上面（等登录完成）；打开设置页或 App 内登录的动作只打开那个页面，不留
  一条不会自己消失的提示；已经在等终端时再点一次只是重新检查，不会再开一个终端；卡片的核实和轮询各用自己的标记；
  刚从核实拿到的修复方法不会被随后的读取盖掉。
- 过时的注释、没用的 `import httpx`、测试里不可能再出现的 `"anthropic"` 值一并改了。

**r3 决定接受、写在这里的风险：** 旧 Anthropic 用户升级后，对话会走启动时从本机配置推出来的账号（例如 `~/.aws` 的
default profile），没有先问他。这个账号原来就已经被 Hive、存储、语音使用；第一次核实成功后会弹一次"SwarmAI 账号已接上：
账号 ID"。不推账号就只能让他的对话直接不能用，这和"不挡住任何人"相反。

**已知、没在这次处理：**
- 用户自己在 shell 里 export 的 `ANTHROPIC_API_KEY`（和存着的那把不同）仍留在 daemon 的环境里、会传给子进程（原来就这样）。
- `useForChat`（从账号页搬过来的原有代码）分两次写，第二次失败时设置只改了一半；它也不先核实账号。
- 没有账号记录的老用户一旦改用 Bedrock key，不能在 LLM Models 改回本机凭证（点默认一项会打开账号页去连接）。
- 后端 `GET /api/system/engine-metrics`、`GET /api/system/capabilities` 和 `core/engine_metrics.py` 已经没有前端读者，
  另外检查后再删。
- 前端 `buttonScale`、`iconScale`、`navVisualSystem` 三个设计规范测试在 main 上本来就是红的（别处的旧代码），这次没有
  增加新的违规。
