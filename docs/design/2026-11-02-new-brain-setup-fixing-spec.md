---
title: New Brain 建立流程 fixing-spec（gbrain 实建测出的问题）
date: 2026-11-02
created: 2026-11-02
updated: 2026-11-02
project: SwarmAI
status: r2（一次零上下文审查后修订），待 XG 确认后实施
---

# New Brain 建立流程 fixing-spec

一句话：2026-11-02 用 New Brain 从一个 GitHub 链接（`garrytan/gbrain`）建了一个 Brain，P0–P6 全部跑完、P6 通过，但过程中暴露出一批问题。产品侧的按原因分成 7 组（G1–G5、G7、G8；原 G6 经审查删除），每组一个方案、一组先红后绿的测试，一次修完；我自己在这个 Brain 里写错的几处放在 Part B，用 `s_ddd-persist` 修。

**r2 改了什么。** r1 经一次零上下文审查，报了 9 HIGH / 11 MEDIUM / 5 LOW，关键几条已在源码上逐条核实。改动：G1 改为复用现有写入函数（r1 说"没有写函数"是错的），默认值改成能通过校验的；G2 补上 monorepo 子包和"重新起草不会替换旧行"；G6 删除（没有观测到的缺陷，P9）；G7a 不回改其他 Brain（只有 gbrain 有那一行）；G7b 不加 P6 行；新增 G8（`py -m` 会从当前目录加载代码）；每个改到 P2b 一节的提交都重新生成 golden set。

## 0. 来源和范围

**测试本身。** 一个 TypeScript/Bun 大仓库（一万多次提交，代码图 35 万多条边，1,268 个文档）。流程：`POST /api/projects`（带 assets）→ 手写 `bindings.yaml` → `artifact_cli bind` → 读 survey 和文档 → 64 条 bootstrap 条目一次写入、零拒绝 → Project Agent 草稿 → P6 `--record` 通过（10 PASS / 1 PENDING）。

**不纳入，及原因：**

- **引用里的 `#标题` / `:行号` 不做存在性检查（r1 的 G6）。** 审查者用 r1 的规范化规则核对了这次 75 条 `#heading` 引用，75 条都能找到，`:行号` 0 条。没有观测到的缺陷，而且 GitHub slug 写法、emoji 标题、`#L10-L20` 都会被误拒。按 P9 不做，与下一条同理。
- **Project Agent 默认检查太重：** 提案不写 validations 时，`_default_commands`（`core/project_agent_draft.py:350-372`）会选上所有 `validation` 角色的脚本。这次是手选的，没有测到它挡住过什么（P9），记为已知问题。
- **条目说法是否被来源支撑：** 模型判断，不能做成确定性检查（SOUL P5），仍由写条目的 agent 负责。
- **`s_ddd-distribute`、`s_pdf` 里的 `py scripts/<x>.py`：** 这些脚本就在 skill 自己的目录里，文档也说了从那里运行（`skills/s_pdf/forms.md:8`），与 G7c 不是同一个原因（G7c 指向的 `backend/scripts/artifact_cli.py` 从任何目录都找不到）。不纳入。
- **大仓库按更细目录聚类（G5 方案 A）：** 见 G5。

## 1. 分组总览

| 组 | 原因（一句话） | 现象 | 严重度 |
|---|---|---|---|
| G1 | 远程 URL 没有写入 `bindings.yaml` 的入口 | 创建时给了 GitHub 链接，`bind` 没有 `--url`，只能手写 YAML、自己查分支 | 高 |
| G2 | 命令解析不认包管理器，安装步骤识别错 | Bun 仓库的检查命令写成 `npm run …`；默认 setup 选中的是全局装 CLI 的 CI 步骤 | 高 |
| G3 | 文档排序按信号个数，README 只有一个信号 | README、AGENTS.md、CLAUDE.md 都排到预算外，没内嵌 | 中 |
| G4 | 修复热点只排除了文档 | 180 个热点里有 33 个是 fixture、生成文件和簿记文件 | 中 |
| G5 | 聚类因边数过多被跳过，指引没有退路 | Key Subsystems 没有实测依据 | 中 |
| G7 | 模板和文档本身有错 | 新 Brain 带一个死路径 PENDING；人格文件是空模板也能打包；skill 里 6 处命令跑不通 | 中 |
| G8 | `py -m` 把当前目录放在导入路径最前面 | 在一个带 `scripts/` 包的仓库里运行，命令会报错，或者执行那个仓库里的代码 | 高（安全） |

## 2. G1 远程 URL 不能一步绑定

**现在的代码。**

- `core/swarm_workspace_manager.py:2644`：`create_project` 只把 `assets` 写进 `.artifacts/setup.json`；docstring（2578-2579）明说 ⑤ 等 BIND。
- `scripts/artifact_cli.py:341-357`：`cmd_bind` 只有 `--local <path>` 或读已有 `bindings.yaml` 两条路；解析器（692-699）没有 URL 参数。
- **写入函数已经有了：** `core/local_binding.py:48-78` `record()` 追加一条 binding，写临时文件、用 `load_bindings` 校验、原子替换，替换前留 `.bak`；`bind --local` 用的就是它（`artifact_cli.py:431-434`）。它只会生成本地 checkout 的条目（`binding_for`，37-45）。
- 交付契约的必填字段：`remote_kind`（只能是 `github-pr` / `code-amazon-cr` / `self-hosted-main`）、`branch`、`review_path`、`auto_send`（`core/ddd_bindings.py:85-93`）。
- P6 查 code repo 和资产是否绑定，看的是 `bindings[].repo` 和 `bindings[].clone`（`verify_ddd_complete.py:161-169`、`:427-437`），不需要另写 `governed_assets`。
- 新 Brain 提示词让模型对 URL 直接 `bind`，没提先写 `bindings.yaml`：`desktop/src/utils/newBrainDispatch.ts:222-223`、`:260`（SKILL.md 第 134 行的 P3 说了要先声明）。

**方案。**

1. **一个写入函数。** 把 `local_binding.record()` 泛化：除了本地 checkout，也能接收一条远程 binding（`worktree: null`）。新增 `remote_binding_for(url, branch)`，照 `binding_for` 的形状给默认值：host 是 github.com → `github-pr`，否则 `self-hosted-main`；分支取不到时用 `main`；`review_path: s_expert-review`；`auto_send: manual-push`（发出 PR 是离开本机的动作，默认不自动）。只写一条 `bindings` 条目，不写 `governed_assets`。写法沿用 `record()`：`safe_dump` 重写、留 `.bak`、校验后原子替换（`--local` 现在就是这样；手写的注释会丢在 `.bak` 之外，这是已有行为，不在本 spec 里改）。
2. **CLI。** `artifact_cli bind --project <P> --url <URL> [--branch <name>]`：
   - URL 先规范化：没写协议的 `github.com/owner/repo` 补成 `https://`（桌面端和 P6 `_norm_ref` 都接受这种写法）；带账号密码的 `https://user:token@…` 直接拒绝（否则令牌会写进 `bindings.yaml`，而 `brain_sync.py:134-144` 会把它同步到 Hive）。然后用 `_validate_clone_source`（`ddd_bindings.py:424-450`）的规则校验。全部校验通过前不写任何东西。
   - 分支：`--branch` 优先；否则 `git ls-remote --symref -- <url> HEAD`，加上和 `git_clone` 一样的保护（`GIT_ALLOW_PROTOCOL`，`git_sync_engine.py:326-328`），再加 `GIT_TERMINAL_PROMPT=0` 和 SSH `BatchMode=yes`，限时；取不到就用 `main`，并在输出里说明。
   - 仓库名取 URL 最后一段去掉 `.git`。克隆目录 `~/.swarm-ai/bindings/<repo>` 是**所有 Brain 共用的**，所以重名检查要看所有 `Projects/*/bindings.yaml`：别的 Brain 已用这个名字、URL 不同 → 改用 `<owner>-<repo>`，仍冲突就拒绝；同一 Brain 已有同一 URL → 不写、也不重新克隆（`bind_project` 每次都会清空重克隆整个项目，`_bind_locked:554`），回报"已绑定"。
   - 写完后走现有后半段：`bind_project` → 文档索引和 survey → Project Agent 重新起草。
3. **提示词和文档。** `newBrainDispatch.ts:222-223`、`:260` 和 SKILL.md P3 一行，URL 都改成 `bind --url`。

**测试（先红后绿；拒绝类用例断言具体错误文字，不能只看退出码，否则今天 argparse 不认 `--url` 也会"通过"）。** `tests/test_local_binding.py`（扩展）与 `tests/test_artifact_cli_bind_url.py`：
- `file://` 指向临时 bare repo：新建 `bindings.yaml`，`load_bindings` 读得回，一条 binding，`remote_kind: self-hosted-main`，分支等于 bare repo 的默认分支。
- `https://github.com/…`（用 `--branch` 跳过网络）：`remote_kind: github-pr`、`auto_send: manual-push`。
- 已有 `bindings.yaml`（带 `jobs:`、没有 `bindings:` 键，CMHK 的形状）：追加后能读回，`jobs` 不变，`.bak` 存在。
- `git://`、`ext::`、带令牌的 URL：报对应错误，文件不变。
- `github.com/a/x`（无协议）：规范化为 `https://github.com/a/x.git`。
- 另一个 Brain 已有 `utils`、URL 不同：新条目名为 `<owner>-utils`。
- 同一 URL 跑两次：条目数不变，不触发克隆（打桩 `bind_project` 断言没被调用）。
- 桌面：`desktop/src/utils/__tests__/newBrainDispatch.test.ts` 断言 URL 资产的提示包含 `bind --project <name> --url`。

## 3. G2 命令解析不认包管理器

**现在的代码。**

- `core/brain_bootstrap/commands.py:107-117` `_package_json`：一律生成 `npm run <name>`。gbrain 根目录有 `bun.lock`，检查命令被写成 `npm run test` 等。子包 `recipes/agent-voice/`、`vendor/postgres/` 自己没有锁文件，同样是 `npm run`。
- `core/project_agent_draft.py:45-47` `_SETUP_RE` 不认 `bun install`。更要紧的是，今天提案不写 setup 时，默认选中的是 `heavy-tests.yml` 里 5 条 `npm install -g "@openai/codex@…"` 这类**全局安装 CLI** 的步骤（审查者实测 `build(gbrain, None)`）：它们不是在装这个仓库的依赖。gbrain 真正的安装步骤是包装脚本 `bun scripts/ci-bun-install.ts`，任何正则都认不出来。
- 重新起草不会替换旧行：`_merge`（`core/project_agent_draft.py:561-566`）对已经覆盖过的仓库保留旧的 `setup` / `validations` 行（设计如此：人删掉的行保持删掉）。所以已有 Brain 的 `npm run …` 不会因为解析修好就自己变。

**方案。**

1. **执行器。** 依次看：该 `package.json` 的 `packageManager` 字段 → 从它所在目录**往上找到仓库根**为止的第一个锁文件（`bun.lock` / `bun.lockb` → `bun run`；`pnpm-lock.yaml` → `pnpm run`；`yarn.lock` → `yarn run`；`package-lock.json` → `npm run`）→ 都没有就 `npm run`（与今天一致）。
2. **安装步骤。** `_SETUP_RE` 加上 `bun install`；带 `-g` / `--global` 的安装一律不算 setup（是装工具，不是装依赖）。认不出包装脚本是正常的：这时 setup 为空，由提案手选（P3b 已经这样要求）。
3. **已有 Brain。** 不自动改。要改的 Brain 由起草的 agent 重新提交一份写明 `code-change` 的提案（Part B 对 gbrain 这样做）。SKILL.md P3b 加一句：解析规则变了以后，旧草稿里的命令行不会自己更新，要重新提交提案。

**测试。** `tests/test_brain_bootstrap.py`：根目录 `bun.lock` → `bun run test`；子目录 `pkg/package.json` 没有锁文件、根目录有 `bun.lock` → `(cd pkg && bun run build)`；`packageManager: pnpm@9` → `pnpm run test`；什么都没有 → `npm run test`（防回归）。`tests/test_project_agent_draft.py`：CI 步骤 `bun install --frozen-lockfile` 是 `setup`；`npm install -g x` 不是 `setup`。

## 4. G3 README 排在阅读预算外

**现在的代码。** `core/brain_bootstrap/docs.py:238-239` 按 `(tier, -信号个数, 路径)` 排序，`_inline_tier1`（253-288）照此在 160K 字符预算（55 行）里贪心内嵌。根目录 README 只有 `root-readme` 一个信号，排在被链接多的文档后面；gbrain 的 README、AGENTS.md、CLAUDE.md 都没内嵌，CONTRIBUTING、SECURITY、RETRIEVAL 内嵌了。

**方案。** tier 1 内部加一个固定的前置组，按这个顺序先进入预算：根目录的**一个** README（`README.md` 优先，其次其他 `README.*`；`README.zh-CN.md` 这类译本不进前置组），然后根目录的 `AGENTS.md`、`CLAUDE.md`（新信号 `root-agent-guide`）。其余排序不变。前置组里的文档自己超出预算，就照今天的规则跳过并标注。

**测试。** `tests/test_brain_bootstrap.py`：fixture 仓库有 `README.md`、`README.zh-CN.md`、`AGENTS.md`，另有 4 个 tier-1 文档各带 3 个信号；预算设成只够 3 个文档 → 内嵌的是 `README.md`、`AGENTS.md` 和一个高信号文档，`README.zh-CN.md` 不在前置组（今天是红的）。

## 5. G4 修复热点里混进非代码文件

**现在的代码。** `core/brain_bootstrap/survey.py:335-337` 只排除 `_docs.is_doc` 的文件；`rr.git_gotchas`（`core/brain_bootstrap/repo_read.py:295-326`）凡是被两次以上修复提交碰过的文件都算。gbrain 180 个热点里，`llms-full.txt`、`scripts/module-size-limits.tsv`、`test/fixtures/goldens/*.json` 这类有 33 个，是随修复一起改的簿记文件。

**方案。** 不用白名单（`LANGUAGE_MAP` 里没有 `.sh`、`.tf`、`.ex`、`.vue` 等，会让整门语言的热点消失），改为排除：后缀属于 `_NON_SOURCE_EXTENSIONS`（`core/code_intel/parser.py:510`），或路径不被代码图收录（复用 `code_intel.parser.is_indexed_path`，`parser.py:798`，它已经处理 vendored、生成和工具缓存目录）。测试文件保留，用 `code_intel.analysis.is_test_path`（`analysis.py:251`）标上 `test: true`。SKILL.md 的 Runtime Traps 一行改成"取非测试热点"。按这个规则，gbrain 剩 147 个，其中 50 个标为测试。

**测试。** `tests/test_brain_bootstrap.py`：造一段修复历史，碰过 `src/a.ts`、`scripts/run.sh`、`test/a.test.ts`、`test/fixtures/goldens/x.json`、`llms-full.txt`、`scripts/limits.tsv` → 热点是 `src/a.ts`、`scripts/run.sh`、`test/a.test.ts`（带 `test: true`）。

## 6. G5 大仓库没有子系统依据

**现在的代码。** `core/code_intel/clustering.py:93-95` 边数超过 50K 时跳过聚类（防内存爆，保护本身是对的）。survey 的 `code_shape.structure` 里已有 `modules`（两级目录）和 `module_edges`（两级目录之间的调用次数，`core/code_intel/analysis.py:177`）；gbrain 有 267 条。但 SKILL.md 的 Key Subsystems 一行（`backend/skills/s_project-manager/SKILL.md:373`）只提 `graph_clusters`，聚类被跳过时没有任何指引。

**两个方案。**

| | 做什么 | 代价 | 风险 |
|---|---|---|---|
| A | 把符号边用一条 SQL 聚合成三级目录图，在目录图上聚类 | 中：新的分析路径和测试 | 新机制；要先证明三级目录对多数仓库有意义 |
| B | 只改指引：`code_shape.graph_clusters.skipped` 存在时，Key Subsystems 用 `code_shape.structure.modules` + `module_edges`，去掉测试模块（gbrain 最大一条是 `test→src/core`），`sources` 写该模块的 `files`（`Evidence.check` 不接受目录路径），再参考仓库自己的架构索引；条目注明依据 | 小：SKILL.md 一行 | gbrain 这种 `src/core` 一个目录两千多文件的仓库，两级还是太粗 |

**建议 B。** 先让作者用上已经算好的数据（这次我就没用上 `module_edges`）；A 等下一个大仓库碰到时再评估。

**测试。** 只改指引，没有新代码，不新增单元测试（r1 列的"锁住数据"测试今天就是绿的，不能证明什么，已删除）。指引变更由重新生成的 golden case 覆盖（见 §11）。

## 7. G7 模板和文档本身的错

**G7a 新 Brain 带一个死路径。** `core/swarm_workspace_manager.py:507`（AGENTS.md 模板）写着 `` `assets/data-source/` ``，新 Brain 没有这个目录，`ddd_format.dangling_references`（`core/ddd_format.py:508-537`）报 PENDING。改成不指向具体目录的写法（`` `assets/<kind>/` ``，kind 举例 data-source）。已有 Brain 只有 gbrain 带这一行（审查者查了全部 10 个），由 Part B 改，不做回改机制。
- 测试：`tests/test_swarm_workspace_manager.py`：新建项目后 `dangling_references(project_dir) == []`（今天是红的）。

**G7b 空模板人格也能打包。** `core/ddd_packager.py:1058-1066` 只查 `SYSTEM_PROMPT.md` 是否存在；模板（`core/swarm_workspace_manager.py:549` 起）原样留着也能打包。打包时，若文件**把空白折叠后**仍含模板原句（"Rewrite this file to define who this installed agent is"；模板里这句在 551-552 行之间换了行，直接比对子串会漏掉）→ `PackagingError`，提示先写人格。P6 不加行：只有 `s_ddd-distribute` 打包（`skills/s_ddd-distribute/scripts/distribute.py:50`），不打包的 Brain 不需要人格。
- 测试：`tests/test_ddd_packager.py`：模板原样（含原换行）→ 报错；写过的人格 → 正常打包。

**G7c skill 里 6 处命令跑不通。** `backend/skills/s_project-manager/SKILL.md` 第 158、252、313、330、391、542 行写的是 `"$SWARM_BACKEND_BIN" py scripts/artifact_cli.py …`，从 `/tmp`、`$HOME` 运行都是退出码 2；`py -m scripts.artifact_cli …`（514、557 行的写法）退出码 0。全部改成 `-m` 写法（安全性依赖 G8）。
- 测试：新增 `tests/test_skill_commands.py`：skill 目录下的 `SKILL.md` 不再出现 `py scripts/artifact_cli.py`；文中每个 `py -m scripts.artifact_cli <sub>` 用子进程跑 `<sub> --help`，退出码为 0（不必为取解析器重构 `main()`）。

**G7d** 见 G1，提示词和 P3 文档一起改。

## 8. G8 `py -m` 从当前目录加载代码（新增，安全）

**现在的代码。** `backend/desktop_main.py:91`：`py -m <module>` 先 `sys.path.insert(0, os.getcwd())` 再 `runpy.run_module`；`backend/scripts/` 没有 `__init__.py`（命名空间包）。审查者用真实的 `$SWARM_BACKEND_BIN` 实测：当前目录有 `scripts/__init__.py` 时，`py -m scripts.artifact_cli` 报 `ImportError`；再放一个 `scripts/artifact_cli.py` 时，运行的是那个文件（打印 `HIJACKED-from-cwd`，退出码 0）。agent 经常在刚克隆的仓库里工作，产品规则里到处是 `py -m scripts.<x>`（`session_worktree`、`mutation_check`、`north_star`……），所以一个带 `scripts/` 包的仓库就能让这些命令执行它自己的代码。这条不只影响 New Brain，但 G7c 要把 skill 改成 `-m` 写法，必须先修它。

**方案。** 当前目录不再插到最前面：改成 `sys.path.append(os.getcwd())`，让打包内的模块总是先被找到；当前目录只用来找打包里没有的模块（今天依赖这一点的是在 backend 源码目录里运行 `py -m <源码里的模块>`，追加后仍然能用）。

**测试。** `tests/test_desktop_main_py.py`（新增）：在临时目录放 `scripts/__init__.py` 和一个会写标记文件的 `scripts/artifact_cli.py`，从那里运行 `py -m scripts.artifact_cli --help` → 退出码 0，标记文件不存在（今天是红的）。另一个用例：临时目录里有一个打包中没有的模块 `only_here.py`，`py -m only_here` 仍能运行。

## 9. Part B gbrain Brain 的实例修正（`s_ddd-persist`，不进代码）

1. **删掉快照条目。** TECH › Runtime Traps "Persistence and quote-fencing files are the most frequently re-fixed code"：来自当下的 git 历史，会过期（R30#4）。改成方法（"改持久化、刷新、引用防护这几块前，先看 survey 的非测试热点"），或整条删掉；`project-agent.yaml` 的 watches 里 "Persistence and quoting hot spots" 一并改掉。
2. **推断降为 gap。** PRODUCT › Success Criteria "An install is done only when a remember/recall round trip works…" 是安装验收步骤，不是项目说的成功标准，改成 IMPROVEMENT › What to Watch For 的一条 gap。
3. **补记没读全的文档。** What to Watch For 加一条 gap：`docs/TESTING.md`、`docs/RELEASING.md`、`docs/ENGINES.md`、`docs/architecture/topologies.md`、`docs/guides/bootstrap.md`、`docs/guides/troubleshooting.md` 只读了标题或片段。
4. **G2 落地后**重新提交一份写明 `code-change` 的提案（命令 id 按位置编号，要重新读 facts），检查命令改成 `bun run …`；**G7a 落地后**替换 AGENTS.md 第 54 行，P6 的 PENDING 消失。

## 10. 实施顺序和提交

一个需求一次做完，按依赖排顺序，每组一个提交：

1. G8（`py -m` 导入顺序）
2. G7c（skill 命令写法）
3. G2（命令解析）
4. G3、G4（survey，彼此独立）
5. G1 + G7d（远程绑定 + 提示词 + P3 文档）
6. G7a、G7b（模板、打包）
7. G5（指引）
8. Part B（Brain 实例，在 SwarmWS，不进 git）

## 11. 验证计划（现在定下，不在收尾时再加）

- **每组：** 上面列的测试，先在未修代码上跑红，修完跑绿；每个代码修复用 `scripts.mutation_check` 回退一次，确认测试会变红。
- **golden set：** `scripts/eval_fixture_brains.py:95-106` 把 SKILL.md 的 P2b 一节（第 328-387 行，含 330、373、375 行）逐字嵌进 `templates/eval/golden_set.yaml`，`tests/test_eval_bootstrap_entries.py:88` 在两边不一致时变红。所以 **G7c、G4、G5 每个提交都要重新生成 golden set**，与 SKILL.md 改动放在同一个提交里。
- **提交前：** 改动函数及其直接调用方的现有测试：`tests/test_brain_bootstrap.py`、`test_brain_bootstrap_apply.py`、`test_project_agent_draft.py`、`test_ddd_bindings.py`、`test_local_binding.py`、`test_ddd_packager.py`、`test_swarm_workspace_manager.py`（只跑 create_project 相关用例）、`test_eval_bootstrap_entries.py`，以及 `backend/skills/s_project-manager/scripts/test_verify_ddd_complete.py`。
- **收尾一次（不变量）：**
  1. 从一个小的公开 GitHub 仓库走完 New Brain：`create` → `bind --url` → survey → P6，全程不手写 YAML。
  2. 新 Brain 的 P6 没有 `assets/data-source/` 的 PENDING。
  3. Bun 仓库的检查命令是 `bun run …`，默认 setup 里没有全局安装。
  4. 根目录 README 被内嵌。
  5. 在带 `scripts/` 包的目录里运行 `py -m scripts.artifact_cli --help`，跑的是打包里的那个。
- **审查：** G1（新的写入路径和对外 CLI 接口）与 G8（安全）请 correctness 和 security 专家审；其余是小改动，不另请专家。

## 12. 待 XG 确认

1. G5 选 B（只改指引），A 暂缓。可以吗？
2. G1 和 G8 跨模块，G1 还新增一个 CLI 接口：直接改，还是走 pipeline？
