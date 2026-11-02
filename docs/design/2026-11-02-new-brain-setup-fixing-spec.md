---
title: New Brain 建立流程 fixing-spec（gbrain 实建测出的问题）
date: 2026-11-02
created: 2026-11-02
updated: 2026-11-02
project: SwarmAI
status: r1，待 XG 确认后实施
---

# New Brain 建立流程 fixing-spec

一句话：2026-11-02 用 New Brain 从一个 GitHub 链接（`garrytan/gbrain`）建了一个 Brain，P0–P6 全部跑完、P6 通过，但过程中暴露出 10 个问题。产品侧的按原因分成 7 组，每组一个方案、一组先红后绿的测试，一次修完；我自己在这个 Brain 里写错的 3 处放在 Part B，用 `s_ddd-persist` 修。

## 0. 来源和范围

**测试本身。** 一个 TypeScript/Bun 大仓库（一万多次提交，代码图 35 万多条边，1,268 个文档）。流程：`POST /api/projects`（带 assets）→ 手写 `bindings.yaml` → `artifact_cli bind` → 读 survey 和文档 → 64 条 bootstrap 条目一次写入、零拒绝 → Project Agent 草稿 → P6 `--record` 通过（10 PASS / 1 PENDING）。

**纳入本 spec：** G1–G7（产品）和 Part B（这个 Brain 的实例修正）。

**不纳入，及原因：**

- **条目说法是否被来源支撑：** 这是模型判断，不能做成确定性检查（SOUL P5）。G6 只补能确定检查的部分（标题、行号存在）；内容对不对仍由写条目的 agent 负责。
- **Project Agent 默认检查太重：** 提案不写 validations 时，`project_agent_draft._default_commands`（`core/project_agent_draft.py:350-372`）会把所有 `validation` 角色的脚本都选上（gbrain 的 `test:full`、`test:e2e`、`test:stress` 这类）。这次是我手选了 3 条，没有真的踩到；没有测到它挡住过什么，先不做（P9），记为已知问题。
- **大仓库按更细目录聚类（G5 方案 A）：** 见 G5，先只修指引。

## 1. 分组总览

| 组 | 原因（一句话） | 现象 | 严重度 |
|---|---|---|---|
| G1 | URL 资产没有一步绑定的路径 | 创建时给了 GitHub 链接，没有 `bindings.yaml`；`bind` 没有 `--url`，只能手写 YAML、自己查分支 | 高 |
| G2 | 命令解析不认包管理器 | Bun 仓库的检查命令被写成 `npm run …`；`bun install` 不被认成安装步骤 | 高 |
| G3 | 文档排序按"信号个数"，README 只有一个信号 | README、AGENTS.md、CLAUDE.md 都排到预算外，没内嵌 | 中 |
| G4 | 修复热点只排除了文档 | 180 个热点里 72 个是测试、生成文件和簿记文件 | 中 |
| G5 | 代码图太大时聚类整步跳过，指引没有退路 | Key Subsystems 没有实测依据 | 中 |
| G6 | 写入只核对路径存在 | `#标题` 写错、`:行号` 越界都照样写入 | 中 |
| G7 | 模板和文档本身有错 | 每个新 Brain 都带一个死路径 PENDING；人格文件是空模板也能打包；skill 里 6 处命令跑不通 | 中 |

## 2. G1 URL 资产不能一步绑定

**现在的代码。**

- `core/swarm_workspace_manager.py:2644`：`create_project` 只把 `assets` 写进 `.artifacts/setup.json`（`write_setup_state`），不写 `bindings.yaml`；docstring（同文件 2577-2578）明说"⑤ waits — provisioned by BIND"。
- `scripts/artifact_cli.py:341-357`：`cmd_bind` 只有两条路：`--local <path>`，或读已存在的 `bindings.yaml`（`bind_project`）。解析器（同文件 692-699）没有 URL 参数。
- `core/ddd_bindings.py` 只有读（`load_bindings`，169 起）和绑定（`bind_repo`，513 起），没有写 `bindings.yaml` 的函数。
- 提示词和文档都说"URL 用 `artifact_cli bind`"：`desktop/src/utils/newBrainDispatch.ts:221-223`、`:260`，`backend/skills/s_project-manager/SKILL.md` 的 P3 行。照着做会直接 `bind` 一个没有 `bindings.yaml` 的项目。
- P6 已经会查"创建时的资产有没有被绑定"（`verify_ddd_complete.py:425-436` `_unbound_assets`），所以缺的只是写的那一步。

**方案。** 加 `artifact_cli bind --project <P> --url <git URL> [--branch <name>]`，和 `--local` 对称：

1. 校验 URL：复用 `ddd_bindings._validate_clone_source` 的规则（`core/ddd_bindings.py:424-450`，只收 https / ssh / 非受保护本地路径），先校验再写任何东西。
2. 仓库名取 URL 最后一段去掉 `.git`；必须通过 `binding_worktree` 的裸名规则（同文件 290-325）。重名（已有同名 binding、不同 URL）就拒绝并说明，不覆盖。
3. 分支：给了 `--branch` 用它；否则 `git ls-remote --symref <url> HEAD`（只读，有时限）取默认分支；取不到就写 `null` 并在输出里说明。
4. 交付契约默认值：host 是 github.com → `remote_kind: github-pr`、`build_system: none`、`review_path: s_expert-review`；其他 host 一律 `remote_kind: null`，在输出里提示补。
5. 写法：`bindings.yaml` 不存在就新建；存在就**以文本追加**一个 `governed_assets` 条目和一个 `bindings` 条目，不用 `yaml.safe_dump` 重写整份文件（手写的 bindings.yaml 带大量注释，例如 AIDLC，重写会丢掉）。写到临时文件 → `load_bindings` 校验通过 → 原子替换；校验失败则原文件不动。
6. 同一 URL 已经绑定过：不加新条目，直接走现有 `bind_project`（幂等）。
7. 写完后照现有路径继续：`bind_project` → 文档索引和 survey → Project Agent 重新起草（`cmd_bind` 现有的后半段不变）。

同步修改 `newBrainDispatch.ts:221-223`、`:260` 和 SKILL.md 的 P3 行，都改成 `bind --url`。

**测试（先红后绿）。** `tests/test_artifact_cli_bind_url.py`：
- 一个用 `file://` 指向临时 bare repo 的 URL：没有 `bindings.yaml` 时新建，`load_bindings` 读得回，`governed_assets` 和 `bindings` 各一条，分支等于 bare repo 的默认分支。
- 已有带注释的 `bindings.yaml`：追加后原注释逐字还在。
- `git://`、`ext::` URL：拒绝，文件不变。
- 同一 URL 跑两次：条目数不变。
- 同名不同 URL：拒绝，文件不变。

## 3. G2 命令解析不认包管理器

**现在的代码。**

- `core/brain_bootstrap/commands.py:107-117` `_package_json`：不管仓库用什么，一律生成 `npm run <name>`。gbrain 有 `bun.lock`，`package.json` 的脚本被写成 `npm run test`、`npm run verify`，Project Agent 的检查原样照抄（`project-agent.yaml`）。pnpm、yarn 仓库同样会错。
- `core/project_agent_draft.py:45-47` `_SETUP_RE`：认 `npm ci|install`、`yarn install`、`pnpm install`，**不认 `bun install`**。gbrain 的 CI 用的是包装脚本 `bun scripts/ci-bun-install.ts`，所以一个安装步骤都没识别出来，我只能手选。

**方案。**

1. `_package_json` 按这个顺序决定执行器：`package.json` 的 `packageManager` 字段（`bun@…` / `pnpm@…` / `yarn@…` / `npm@…`）→ 同目录的锁文件（`bun.lock`、`bun.lockb` → `bun run`；`pnpm-lock.yaml` → `pnpm run`；`yarn.lock` → `yarn run`；`package-lock.json` → `npm run`）→ 都没有就 `npm run`（与现在一致）。
2. `_SETUP_RE` 加上 `bun install`。
3. 已有 Brain：它们的 `project-agent.yaml` 不会自己变；下一次 `bind` 或 `project-agent-draft` 会重新起草。gbrain 在 Part B 里重新起草一次。

**测试。** `tests/test_brain_bootstrap.py` 新增：有 `bun.lock` → `bun run test`；`packageManager: pnpm@9` 且没有锁文件 → `pnpm run test`；子目录 `admin/package.json` 带自己的锁文件 → `(cd admin && <对应执行器> run build)`；都没有 → `npm run test`（防回归）。`tests/test_project_agent_draft.py` 新增：CI 步骤 `bun install --frozen-lockfile` 的角色是 `setup`。

## 4. G3 README 排在阅读预算外

**现在的代码。** `core/brain_bootstrap/docs.py:238-239` 按 `(tier, -信号个数, 路径)` 排序，`_inline_tier1`（同文件 253-288）照这个顺序在 160K 字符预算（55 行 `T1_BUDGET_CHARS`）里贪心内嵌。根目录 README 只有一个信号 `root-readme`，被链接多的文档全排在它前面；gbrain 的 README、AGENTS.md、CLAUDE.md 都标了 "over the tier-1 reading budget"，预算给了 CONTRIBUTING、SECURITY、RETRIEVAL 等。

**方案。** tier 1 内部加一个固定的前置组：根目录 README，以及根目录的 AGENTS.md / CLAUDE.md（新信号 `root-agent-guide`），先于其他 tier-1 文档进入预算；其余排序不变。

**测试。** `tests/test_brain_bootstrap.py`：一个 fixture 仓库，README 只被自己引用，另有 4 个 tier-1 文档各带 3 个信号，预算设成只够 README 加一个文档 → README 被内嵌（今天是红的）。

## 5. G4 修复热点里混进非代码文件

**现在的代码。** `core/brain_bootstrap/survey.py:335-337` 只排除了 `_docs.is_doc` 的文件；`rr.git_gotchas`（`core/brain_bootstrap/repo_read.py:295-326`）凡是被 ≥2 次修复提交碰过的文件都算。gbrain 180 个热点里有 72 个是 `test/`、`llms-full.txt`、`scripts/module-size-limits.tsv`、`test/fixtures/goldens/*.json` 这类，它们是随修复一起改的簿记文件，不是"老出问题的代码"。

**方案。** 热点只保留源代码文件：后缀在代码图的 `LANGUAGE_MAP` 里（`core/code_intel/parser.py`，与它判断"是不是源代码"用同一张表，非源代码后缀见同文件 510 `_NON_SOURCE_EXTENSIONS`），且路径不在 fixture / golden / vendored / 生成目录下（复用 `docs._NOT_DOC_DIRS` 和 `repo_read.is_skipped_dir`）。测试文件保留，但加 `test: true` 标记；SKILL.md 的 Runtime Traps 行改成"取非测试热点"。

**测试。** `tests/test_brain_bootstrap.py`：造一段修复历史，碰过 `src/a.ts`、`test/a.test.ts`、`test/fixtures/goldens/x.json`、`llms-full.txt`、`scripts/limits.tsv` → 热点只有 `src/a.ts` 和带 `test: true` 的 `test/a.test.ts`。

## 6. G5 大仓库没有子系统依据

**现在的代码。** `core/code_intel/clustering.py:93-95` 边数超过 `_MAX_EDGES`（50K）直接跳过聚类（为防内存爆掉，这个保护本身是对的）。survey 的 `structure` 里其实已有 `modules`（两级目录）和 `module_edges`（两级目录之间的调用次数，`core/code_intel/analysis.py:165-180`），gbrain 有 267 条。但 SKILL.md 的 Key Subsystems 行（`backend/skills/s_project-manager/SKILL.md:373`）只说"每个 `graph_clusters` 簇一条"，聚类被跳过时没有任何指引。

**两个方案。**

| | 做什么 | 代价 | 风险 |
|---|---|---|---|
| A | 把符号边聚合成更细的目录图（三级目录，一条 SQL 聚合，不必载入整张图），在目录图上聚类 | 中：新的分析路径和测试 | 新机制；要证明三级目录对多数仓库有意义 |
| B | 只改指引：聚类被跳过时，Key Subsystems 用 `structure.modules` + `module_edges`，再参考仓库自己的架构索引；条目注明依据 | 小：SKILL.md 一行加一个测试 | gbrain 这种 `src/core` 一个目录两千多文件的仓库，两级目录还是太粗 |

**建议 B。** 先让作者用上已经算好的数据（这次我就没用上 `module_edges`）；A 等第二个大仓库再碰到时再做。

**测试。** SKILL.md 的指引变更由 `scripts/eval_fixture_brains.py` 重新生成的 golden case 覆盖（见 G7c）；另加一个 `tests/test_brain_bootstrap.py` 用例：聚类被跳过时，survey 的 `code_shape.graph_clusters.skipped` 和 `structure.module_edges` 同时存在（锁住作者需要的那份数据）。

## 7. G6 写入只核对路径存在

**现在的代码。** `core/brain_bootstrap/apply.py:98` `_ANCHOR_RE` 把 `#heading` 和 `:line` 直接剥掉，`Evidence.check`（同文件 154-180）只看路径是不是被跟踪的文件。我这次的引用里如果把标题写错，或者行号超出文件，都会照样写进 Brain，读的人顺着找不到。

**方案。** 只补能确定检查的两项：

1. `<path>#heading` 且文件是 Markdown：标题必须存在。比较时用 `core.ddd_skeleton.normalize_heading` 那套规范化，再去掉行内代码的反引号和链接语法；找不到 → `evidence` 拒绝，原因里列出该文件的前 20 个标题。
2. `<path>:N` 或 `:N-M`：N、M 不超过文件行数，且 N ≤ M；否则 `evidence` 拒绝。

已经写进去的旧 Brain 不回头检查（bootstrap 只在新生 Brain 上写）。

**测试。** `tests/test_brain_bootstrap_apply.py`：`README.md#Install` 通过；`README.md#Nope` 拒绝并列出标题；带反引号的标题 ``CLAUDE.md#Capturing test output (NEVER pipe through `tail` / `head`)`` 通过；`a.py:99999` 拒绝；`a.py:3-2` 拒绝。

## 8. G7 模板和文档本身的错

**G7a 每个新 Brain 带一个死路径。** `core/swarm_workspace_manager.py:507`（AGENTS.md 模板）写着 `` `assets/data-source/` ``，新 Brain 没有这个目录，`ddd_format.dangling_references`（`core/ddd_format.py:508-537`）每次都报 PENDING。改成不指向具体目录的写法（`` `assets/<kind>/` ``，kind 举例 data-source）。已有 Brain：只替换内容与模板原句逐字相同的那一行，列出改了哪些 Brain；改过这一行的不动。
- 测试：`tests/test_swarm_workspace_manager.py`：新建项目后 `dangling_references(project_dir) == []`（今天是红的）。

**G7b 空模板人格也能打包。** `core/ddd_packager.py:1058-1066` 只查 `SYSTEM_PROMPT.md` 存不存在；模板（`core/swarm_workspace_manager.py:549` 起）原样留着也能打包，装好的 agent 拿到的是"请重写这个文件"。打包时若文件仍含模板原句（"Rewrite this file to define who this installed agent is"）→ `PackagingError`，提示先写人格。P6 加一行 PENDING："persona not written (needed only to package)"，不是 FAIL（不打包的 Brain 不需要它）。
- 测试：`tests/test_ddd_packager.py`：模板原样 → 打包报错；写过的 → 正常。`test_verify_ddd_complete.py`：模板原样 → PENDING，整体仍 PASS。

**G7c skill 里 6 处命令跑不通。** `backend/skills/s_project-manager/SKILL.md` 的第 158、252、313、330、391、542 行写的是 `"$SWARM_BACKEND_BIN" py scripts/artifact_cli.py …`。实测从 `/tmp`、`$HOME` 运行都是退出码 2；`py -m scripts.artifact_cli …`（同文件 514、557 行用的写法）退出码 0。全部改成 `-m` 写法。P2b 一节被 `scripts/eval_fixture_brains.py:95-106` 逐字嵌进 `templates/eval/golden_set.yaml` 的三个 case，改完要重新生成。
- 测试：新增 `tests/test_skill_commands.py`：skill 目录下的 `SKILL.md` 里不再出现 `py scripts/artifact_cli.py`，且每个 `py -m scripts.artifact_cli <sub>` 的 `<sub>` 都是 `artifact_cli` 解析器里真实存在的子命令（从解析器读，不从文本猜）。

**G7d** 见 G1，提示词和 P3 文档一起改。

## 9. Part B gbrain Brain 的实例修正（`s_ddd-persist`，不进代码）

1. **删掉快照条目。** TECH › Runtime Traps "Persistence and quote-fencing files are the most frequently re-fixed code"：内容来自当下的 git 历史，会过期（R30#4）。改成方法："改持久化、刷新、引用防护这几块前，先看 survey 的非测试热点"，或者整条删掉。
2. **推断降为 gap。** PRODUCT › Success Criteria "An install is done only when a remember/recall round trip works…"：这是安装验收步骤，不是项目自己说的成功标准，改成 IMPROVEMENT › What to Watch For 的一条 gap。
3. **补记没读全的文档。** What to Watch For 加一条 gap：`docs/TESTING.md`、`docs/RELEASING.md`、`docs/ENGINES.md`、`docs/architecture/topologies.md`、`docs/guides/bootstrap.md`、`docs/guides/troubleshooting.md` 只读了标题或片段。
4. **G2 落地后**重新跑 `project-agent-draft`，检查命令改成 `bun run …`；**G7a 落地后**替换 AGENTS.md 那一行，P6 的 PENDING 消失。

## 10. 实施顺序和提交

一个需求一次做完，按依赖排顺序，每组一个提交：

1. G7c（只改文档 + 重新生成 golden set）
2. G2（命令解析）
3. G3、G4（survey，彼此独立）
4. G6（apply 检查）
5. G1 + G7d（新写入路径 + 提示词 + P3 文档）
6. G7a、G7b（模板、打包、P6）
7. G5（指引）
8. Part B（Brain 实例，在 SwarmWS，不进 git）

## 11. 验证计划（现在定下，不在收尾时再加）

- **每组：** 上面列的测试，先在未修代码上跑红，修完跑绿；每个修复用 `scripts.mutation_check` 回退一次，确认测试会变红。
- **提交前：** 改动函数及其直接调用方的现有测试：`tests/test_brain_bootstrap.py`、`test_brain_bootstrap_apply.py`、`test_project_agent_draft.py`、`test_ddd_bindings.py`、`test_ddd_packager.py`、`test_swarm_workspace_manager.py`（只跑 create_project 相关用例）、`test_eval_bootstrap_entries.py`、`test_verify_ddd_complete.py`。
- **收尾一次（不变量）：**
  1. 从一个小的公开 GitHub 仓库走完 New Brain：`create` → `bind --url` → survey → P6，全程不手写 YAML。
  2. P6 不再出现 `assets/data-source/` 的 PENDING。
  3. Bun 仓库的 Project Agent 检查是 `bun run …`。
  4. README 被内嵌。
- **审查：** G1 是新的写入路径，也是对外的 CLI 接口，请 correctness 和 security 专家审（URL 校验、追加写的原子性、注释保留）；G6 请 correctness 专家审；其余是小改动，不另请专家。

## 12. 待 XG 确认

1. G5 选 B（只改指引），A 暂缓。可以吗？
2. G1 跨模块，还新增一个 CLI 接口：直接改，还是走 pipeline？
3. G7a 是否回改已有 Brain 的 AGENTS.md（只改与模板原句完全相同的那一行）？
