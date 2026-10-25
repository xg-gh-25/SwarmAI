---
title: usable-by-anyone 收口 — 用闭集检查代替一次性盘点
created: 2026-10-25
updated: 2026-10-25
project: SwarmAI
status: r4 — K1、K2、K3 与 C1、C2、C3 全部落地，三个检查都在 CI；D2（OS Eval 通用化）另起 ToDo
parent: docs/design/2026-10-24-swarmai-usable-by-anyone.md
---

# usable-by-anyone 收口 — 用闭集检查代替一次性盘点

## 0. 一句话

主设计（`2026-10-24-swarmai-usable-by-anyone.md`）的 gate、专家、skill 这三块已经基本做到位，但每一块都是靠"一次性盘点 + 逐项修"推进的。做完以后，没有任何检查会因为"漏了一处"或者"又新加了一处"而变红，所以已经漏下的地方没人发现，以后新加的也一样。本设计做两件事：把三份盘点改成闭集检查（不在清单里就失败），再修掉检查第一次运行会抓出的那些残留。

## 1. 现状（2026-10-25 核对，HEAD `67cd437ea`）

**已经解决的**（不再是问题，列出来是为了不重复做）：

| 项 | 解决于 |
|---|---|
| 挡 native 的 gate 全部删除；SwarmAI 专用的 `design_lint_guard`、`inclusive_term_guard` 移出产品 | S4、`1dfdedba0` |
| 专家 12 个合并为 9 个；旧 id 读接手专家的 prompt | `82de614d7`、`dede1ae2f` |
| 专家 prompt 里的 RP 编号；安全专家只认 SwarmAI 文件名和 Python API；handout 里写死 `python backend/skills/...spawn_prompt.py` | `67cd437ea`（S6-4b）：改为读仓库自己的 `.swarmai/review.md` 和声明 |
| 安装包只带仓库 tracked 的 skill | `1f5afc273` |
| 结构测试 `test_native_not_blocked`、`test_context_templates_native_default`、`test_skills_no_source_checkout` | 已在 main，全绿 |

**还剩下的**（每条都已对着 HEAD 读过源码）：

| # | 位置 | 问题 | 对别的用户的后果 |
|---|---|---|---|
| L1 | `hooks/todo_lifecycle_hook.py:127` `_find_codebase_path` | 在 `~/Desktop/SwarmAI-Workspace/swarmai` 这类路径下找仓库，用来判断 session 里有没有 commit，进而把 ToDo 标成 handled | 永远找不到仓库，ToDo 自动收尾对别的用户一直不工作，也不报错 |
| L2 | `hooks/context_health_hook.py:694` `_auto_update_doc_frontmatter` | 产品 hook 在后台改 SwarmAI 仓库 `docs/*.md` 的 `updated:` 字段 | 对别人无害（找不到就返回），但这就是 P7 第 1 条说的"产品代码认 SwarmAI"，而且是产品在偷偷写一个仓库 |
| L3 | `main.py:2123` `_resolve_project_root` + `POST /api/system/upgrade` | 靠 `prod.sh` 是否存在找 SwarmAI checkout，再从 `desktop/src-tauri/binaries/` rsync 后端。注释原话"frozen daemon on XG's Mac" | 别人调用只会拿到 400。前端没有调用方（`git grep system/upgrade` 只有后端和测试）。和 P7 第 3 条（"不再有 `prod.sh` 注入后的特殊版本"）、S2b 的版本化 runtime 冲突 |
| L4 | `scripts/eval_runner.py:80` `_find_swarmai_repo` | OS Eval 的代码指纹和 command 类 case 都以 SwarmAI checkout 为根（9 处调用） | 别的用户在 System → OS Eval 上跑，找不到 `backend/core` 就抛 `FileNotFoundError` |
| L5 | skill 正文 | 写给某一个人的内容：`s_docx/xg-doc-style-standard.md`（"creating documents for XG"）、`s_context-hygiene`（"needs XG approval"×7）、`s_pollinate` 的默认作者 `'XG / SwarmAI'`、`s_pdf` / `s_radar-todo` 示例里的真人名、`s_project-manager`（"the XG constraint"）、`s_golden-case`、`s_persist` 拿 `Projects/SwarmAI/...` 当示例路径 | 别人读不通；真人名出现在公开仓库里 |
| L6 | `experts/code/concurrency.md:145`、`state-machine.md:89,147`、`api-contract.md:18` | "Examples of HIGH findings from SwarmAI history"、"Known patterns from SwarmAI" | 例子本身有用，只是标题把它说成某个仓库的历史 |
| L7 | `scripts/lint_skills.py:563` | 元数据检查按文件系统 glob `s_*/SKILL.md`，打包按 git（`shipped_skills.py`），两边的范围不一样 | 本机有 untracked 的 `s_internal-*` 时 lint 变红，CI 上看不到 |

## 2. 根因：为什么会漏

不是哪一处写错了，而是三份盘点都只做了一次，做完后没有变成检查：

1. **P7 的代码盘点（主设计 §3.9）**是一次 grep 加人工归类。L1–L4 当时没进清单，现在也没有测试会因为它们而失败。主设计 §5.2 写了"结构测试保证产品代码里没有识别 SwarmAI 的逻辑"，这个测试一直没写。
2. **gate 的分类（主设计 §3.4）**落成了 `test_native_not_blocked` 里的 `REMOVED` / `KEPT` 两个名单。这是"列出我知道的"，不是闭集：新注册一个会拒绝的 gate，两个名单都不会变红。§5.2 的原话是"新增 gate 不进表就失败"，这条没有做到。
3. **发布内容的检查（`lint_skills.py`）**只管 run id、私有登记号和带用户名的绝对路径，不管"这句话是写给某个人的"。所以 L5 一直能进来。它的检查范围和打包范围也不一致（L7）。

这和 MEMORY 里"CI 选测用 allowlist，不用 everything-minus-denylist"是同一类错误：只列已知项的名单会随时间烂掉，闭集才有边界。

## 3. 设计

### 3.1 三个闭集检查（都是 SwarmAI 仓库自己的测试和 lint，不是产品 gate，符合 P7）

**K1 P7 代码扫描**：新增 `backend/tests/test_p7_no_swarmai_special.py`。

- 范围：`git ls-files` 里 `backend/**/*.py` 中所有会进安装包的产品代码，排除 `backend/tests/` 和 `**/test_*.py`；外加 `backend/skills/**/scripts/*` 和 `*.sh`。范围由 git 列出，不用文件系统 glob。
- 规则（命中即失败）：
  - `SwarmAI-Workspace`
  - `Path.home() / "Desktop"`
  - `prod.sh`
  - 代码里的 `"Projects/SwarmAI"` 字面量
  - 只为找 SwarmAI checkout 而存在的环境变量：`SWARMAI_CODEBASE_DIR`、`SWARMAI_DIR`、`SWARMAI_PROJECT_ROOT`
  - 函数名 `_find_swarmai_root`、`_find_swarmai_repo`
- 例外清单 `backend/tests/p7_exceptions.txt`：每行 `路径 | 规则 | 理由`，只收主设计 §3.9 A/A' 类（产品名本身、seed 默认项目、`ddd_packager` 的脱敏正则、docstring 里的示例）。
- 防止测试自己失效（MEMORY 里的两条 pitfall）：
  - 例外清单里的每一行，必须在不加例外时确实会命中，否则测试失败（防止留下不再起作用的豁免）。
  - 断言扫描到的文件数超过一个下限，并且包含 `core/`、`hooks/`、`routers/`、`jobs/`、`scripts/`（防止范围悄悄变窄、对着空集报绿）。
- 先红：在当前 main 上应命中 L1–L4，共 4 个文件。

**K2 gate 闭集**：改 `test_native_not_blocked.py`。

- `REMOVED` / `KEPT` 换成一个完整的字典 `GATE_CLASS = {名字: 类别}`，类别只能是 §3.4 的五类之一：安全和会话、记忆和 DDD（只提示）、pipeline run 完整性、保护 session 本身、观测和日志。
- 断言 `build_hooks` 注册的每一个名字都在字典里，字典里的每一个名字也都已注册（双向相等）。
- 以后新增 hook 时，作者要么在这里写明它属于哪一类，要么测试变红，逼着回答 P3 的两个问题。
- 变异：注册一个叫 `x_gate` 的空 gate，测试变红。

**K3 发布内容不写给某一个人**：扩展 `scripts/lint_skills.py`。

> r2：K3 推迟。skill 里写给个人的内容（C2）由另一个 session 在 skill listing 设计的 S2 里清理（`f43275e41`、`9809d979d` 已开始）。K3 在 C2 清完后落地，否则落地就把 CI 弄红。L7（元数据范围和打包对齐）不依赖 C2，已先做。

- 新规则 `OWNER_IDENTITY`：名字列表放在 SwarmAI 仓库自己的 `scripts/owner_identity.txt`（XG、Xiaogang、gawan，以及出现过的同事名）。这是仓库自己的 lint 配置，产品代码不读它。
- 检查范围从 skill 扩到 `backend/experts/`、`backend/context/`（`USER.example.md` 作为模板例外）。
- 元数据检查的范围改成和打包一样，调用 `scripts/shipped_skills.py`，修 L7。
- 先红：在当前 main 上应命中 L5 的文件。

### 3.2 清理（让 K1 和 K3 变绿，按原因分组，每组一个 commit）

**C1 产品代码靠猜开发路径找 SwarmAI 仓库（L1–L4）**

- **L1**：`_find_codebase_path` 删除，改用 `core.session_changes.working_repo(session_id, db_path)`，也就是终端和 Canvas Changes 已经在用的"这个 session 实际改的绑定仓库"。找不到就只看 SwarmWS，和现在别的用户的实际行为一样，但 XG 的仓库从此也走同一条路径。需要一个测试：在临时绑定仓库里 commit，ToDo 被标为 handled；把旧的路径猜测加回来，测试仍然是绿的，证明不再依赖路径。
- **L2**：`_auto_update_doc_frontmatter` 及其调用删除。是否把 SwarmAI 仓库已有的 `scripts/lint_doc_frontmatter.py --staged` 接进 CI，见 D3。
- **L3**：见 D1。
- **L4**：见 D2。

**C2 写给某个人的发布内容（L5）**

> r2：由另一个 session 做（见 K3 的说明），本设计不再重复。

- "XG" 一律改成"the user"或"the owner"。示例里的真人名换成占位名。`s_pollinate` 的默认作者改为从 `USER.md` 读用户名，读不到就留空。`Projects/SwarmAI/...` 示例改为 `Projects/<project>/...`。
- `s_docx/xg-doc-style-standard.md` 见 D4。

**C3 专家例子的标题（L6）**

- 只改标题和引导语（例如"Examples of HIGH findings"），例子本身保留。不归 K3 管，因为 `SwarmAI` 是产品名，不是人名。

## 4. 不在本设计范围

- 主设计 S6-5（删 `utils/source_repo.py`、`SWARM_WORKTREE_MODE`、旧命令形式）、S5 度量、G1 的通用 pipeline 验收：都按主设计 §7 走。K1 的规则不包括 `source_repo`，避免和 S6-5 撞车，等 S6-5 落地后再加进 K1。
- `s_internal-*` 迁进 DDD：属于 skill listing 设计（`2026-10-25-skill-listing-recall-and-cleanup.md`）。
- `core/run_protocol/` 和 `backend/experts/` 的 prompt 正文：另一个 session 正在做 S6-4，本设计除了 C3 的标题不碰它们。

## 5. 验收

- K1、K2、K3 在当前 main 上先红，红的理由分别是 L1–L4、一个假 gate、L5。
- C1–C3 落地后三项全绿。
- 变异验证：把 L1 的路径猜测加回去，K1 变红；注册一个假 gate，K2 变红；在任意 skill 里写 "needs XG approval"，K3 变红；在例外清单里加一行不会命中的豁免，K1 变红。
- `backend/tests/test_todo_lifecycle*.py` 和 `test_context_health*.py` 受影响的部分全绿。

## 6. 待 XG 定

- **D1 `POST /api/system/upgrade`**：前端没有调用方，它依赖 `prod.sh` 和源码 checkout，和 P7 第 3 条、S2b 的版本化 runtime 冲突。建议整个删除，连同 `_resolve_project_root`、`_resolve_sidecar_binary`、`/upgrade/status`，以及 `daemon_guard` 里只为它服务的结果文件逻辑。删之前要先 grep 它的所有调用方（skill、job、文档）。另一个选项是保留，但改成走 S2b 的 runtime 部署；这要重新设计，建议不做。
- **D2 OS Eval**（XG 已定，2026-10-25）：OS Eval 是产品功能，评的是用户正在用的 SwarmAI。开发 SwarmAI 本身才需要的部分（以源码 checkout 为根的代码指纹、在源码仓库里跑的 canary、测 SwarmAI 自身代码的 case）移出产品，只留所有用户都用得上的。工作量大，单独一个 ToDo，不在本设计里做。在那之前，`eval_runner`、`eval_spine_probe`、`eval_service` 作为 K1 的临时例外。
- **D3 文档 frontmatter**：删掉自动改写以后，是把 `lint_doc_frontmatter.py` 接进 SwarmAI 的 CI（只查改动过的文档），还是干脆不管 `updated:` 字段？我倾向接进 CI，只查改动过的文件。
- **D4**：已解决。`f43275e41` 把它换成了中性的 `default-doc-style.md`（另一个 session 做的）。

## 7. 实施顺序

1. K2：只动一个测试文件，不依赖任何决定。
2. K1 和 K3 先落红的版本，然后 C1（L1、L2）、C2、C3 逐组让它们变绿。D1、D2 没定之前，L3、L4 先作为带理由的临时例外写进 `p7_exceptions.txt`，定了以后删例外、做清理。
3. D1、D2 定了以后，各一个 commit。

整个过程走 native。每组跑受影响的测试，C1 落地后请 correctness 和 integration 两位专家审（它改了 hook 的行为）。改动跨 hooks、main.py、skills 和 tests，如果你想走 pipeline，告诉我就行。

## 修订记录

- r1（2026-10-25）：初稿。§1 对着 HEAD `67cd437ea` 核对；S6-4b 已解决的三项从问题清单里移出。
- r2（2026-10-25）：D2 由 XG 定，另起 ToDo；D4 已由 `f43275e41` 解决；C2 和 K3 交给 / 等待做 skill 清理的那个 session。已落地：C1（L1 ToDo hook 用 `working_repo`，L2 删 frontmatter 自动改写；K1 首次扫描另外发现的三处一起删：`proactive_intelligence._RADAR_ZONES` 是已删 Recurrence Radar 的残留，`scripts/replay_test_selection.py` 回放已删的 runner，`scripts/memory_judge_audit.py` 写死了用户路径，都没有调用方）、K1（AST 扫描 + `p7_exceptions.txt`，加入 BVT manifest）、K2（`GATE_CLASS` 闭集）、C3、L7。L3（D1）和 L4（D2）在 K1 里作为带理由的临时例外。
- r3（2026-10-25）：D1 由 XG 定为删除：`/api/system/upgrade`、`/upgrade/status`、`_resolve_project_root`、`_resolve_sidecar_binary`、启动时的 deployed_no_restart 扫描和 `daemon_guard` 的结果文件逻辑一起删掉，K1 里 `main.py` 的临时例外随之删除。D3 由 XG 定为接 CI：`lint_doc_frontmatter.py` 新增 `--diff BASE`，CI 只查本次 push / PR 新增或修改的 `docs/**/*.md`，范围与本地 pre-commit 一致；改名后又改内容的文档也会被检查。
- r4（2026-10-25）：不再有另一个 session，C2 和 K3 由本 session 做完。C2：skill、context 种子和一个测试 fixture 里写给 XG 的话改成 "the user" 或占位名；`EVOLUTION_CHANGELOG.jsonl` 种子重新清空（它带着一条真实的变更记录，会复制进每个新工作区）；`USER.example.md` 示例名改成 `[your name]`；`SELF.md` 去掉两个 run id 和一个登记号。K3：`lint_skills.py` 新增 owner 名字规则，名字列表在 `scripts/owner_identity.txt`，只列 owner 自己的公开标识，不列同事（文件是公开的）；正文检查范围扩到 `backend/experts` 和 `backend/context`。K2、L7、K3 的测试加进 BVT 清单。
