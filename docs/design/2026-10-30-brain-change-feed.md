---
title: Brain 学到了什么 —— 按三层七类显示 CDF 写入的知识，用户只能撤回
created: 2026-10-30
updated: 2026-10-30
project: SwarmAI
status: 待确认（r4：对现有系统的影响逐条核实，§9；EVOLUTION 编号、库版本兼容、计算缓存）
follows: 2026-10-26-swarm-cognitive-distillation-flow.md（知识只由 CDF 写入，全自动）；2026-10-29-cdf-post-launch.md
---

# Brain 学到了什么

## 0. 结论

Brain 页上的 review 改成一张"**它学到了什么**"：列出 CDF 写进这个 Brain 的知识，按 ontology 的
**三层七类**分组，最新的在前。知识是自动进来的，用户不需要批准，所以只有一个动作：**撤回**。撤回就是
从 Brain 里删掉这一条（原文另存备份），AI 从此不再使用它。CDF 以后如果从新的证据里再学到同样的知识，
照常写入，不做拦截：自主的流程不被干预，只靠独立证据强化。

不依赖 git，对每个用户、每个 Brain 都一样。git diff 的整套 review 删掉。

**r2 相对 r1 的变化**（用户 2026-10-30）：去掉"知道了"和已看集合；撤回从"留墓碑"改为删除；按三层七类
显示；不拦截再学。

## 1. 问题

**出厂设置下，用户自己的 Brain 都不在 git 里。** 新工作区的 `.gitignore` 由
`SwarmWorkspaceManager` 写入，含 `Projects/*` 和 `!Projects/SwarmAI/`
（`core/swarm_workspace_manager.py` `GITIGNORE_CONTENT` :752，两行在 :797–:798）。这是有意的：
Brain 里有客户数据，工作区曾挂过公开 remote（STEERING #19）。

**review 的每一处都依赖 git。**
- 后端：`GET /brains/{name}/review` 用 `git diff <watermark>..HEAD` 算代码块（`routers/ddd_brain.py`
  `_scoped_diff_hunks` :1340，`get_review` :1390）；"知道了"把 `.artifacts/.last-reviewed-sha` 写成
  HEAD（`approve_review` :1427）；"拒绝"用 `git apply -R` 反向应用一个代码块（`reject_review` :1447）。
- 前端：review 视图（`BrainHub.tsx` `ReviewView` :1135）、每份文档的"新增"（`dddOverview.ts`
  :72–:80）、Brain Map 的"written since review"（`brainMapModel.ts` :168）。

对每个用户自己的 Brain，这些永远是 0。用户最想知道的"我的 Brain 自己学到了什么"，产品看不到。

## 2. 现有的、可以直接用的东西（都已对源码核实）

1. **条目自带永久身份。** CDF 写的每个条目行尾带 `<!-- cand:<unit_id> -->`（`core/ingestion/writer.py`
   `_render_ddd` :345、通用分支 :442）。`core.ingestion.units.parse_units`（:186）解析出的单元，
   `identity` 是 `cand:<unit_id>`（:106），`anchor` 是 `<path>#<section>#<identity>`（:49）。
2. **类型和层是固定映射。** 七类：`principle` 原则、`correction` 纠错（元认知）；`decision` 决定、
   `model` 心智模型（认知）；`guideline` 经验、`pitfall` 坑、`process` 流程（操作）。层由类型唯一决定
   （CDF 设计 §5.3，`TYPE_TO_LAYER`）。条目行上的 `[decision]` 等就是这个类型。另有 GapUnit
   （`[gap:<kind>]`，待跟进事项），不属于七类（`units.py` `layer_for` :258 把它和七类一起允许在 DDD 节里）。
3. **知识只由 CDF 写入；用户编辑、撤回和格式整理也会改这些文件。** CDF 在一个 Brain 里写四份文档
   `Projects/*/2-understanding/{PROJECT,PRODUCT,TECH,IMPROVEMENT}.md`，跨项目的写进
   `Knowledge/Learned/_common/*.md` 和 `Knowledge/Learned/repos/*.md`（common DDD，`writer.py`
   `_ensure_common_ddd` :190）。改这些文件的还有 `apply_user_edit`、`redact` 和 `ddd_format.normalize`。
   实测 10-29 以来：SwarmAI 四份文档 86 条，`Knowledge/Learned` 约 50 条，其余 Brain 0 条（还没有处理过
   与它们相关的任务）。
4. **写入时间和来源。** `ingestion_writes`（`api.py` :83）每个单元最多一行（`op_id = ingestion:<unit_id>`），
   `updated_at` 是写入完成时间，没有来源字段；来源在 `ingestion_inbox.source`，经
   `ingestion_units.candidate_id` 关联。每日清理按**收集时间**过 30 天把候选连同写入行删掉
   （`worker.py` :608、:662）。删之前来源和判官记录复制进 `ingestion_eval_log`，保留 180 天
   （`eval_log.py` :56、:23），它的 `at` 是收集时间。`queries.py` `item_detail`（:269）已经按
   `unit_id` 把单元、写入、关系和来源连在一起，复用这段关联。按 `unit_id` 关联，不按路径（§8）。
5. **替换关系** 在 `ingestion_relations`（`api.py` :107，只有 `supersedes`），不被清理。`refine`
   保留旧条目的字节（`writer.py` :545）；`replace` 删除旧条目、旧字节存到 `.archive/`（:547–:550）。
6. **撤回已经有了，但现在留墓碑。** `governed_edit.redact(anchor, reason, actor="user")`（:491）把单元
   换成 `- [redacted] **Withdrawn** [...] <!-- cand:<原 id> | redacted:<hash> -->`（:511），原文存进
   `state/redacted/<hash>.bin`，并把这条的替换关系和证据支持置为失效、写一行 `redactions`（:458–:468）。
   路由 `POST /api/ingestion/redact`（`routers/ingestion_redact.py`），只接受本机且带
   `X-Swarm-User-Action: redact` 的请求；前端 `services/ingestion.ts` :173 已在调用。
   **墓碑没有读者**：完成判断只比对文件哈希（`recover_redact_plan` :437），"被撤回比例"读写入记录的
   `relations`（`queries.py` :531），代码里没有任何地方读文件里的墓碑；`redactions` 表也没有读者。
7. **`.artifacts/` 不随 Brain 同步到 Hive**（`core/brain_sync.py` :14）。Hive 上的新知识以 lessons
   回到主机，经主机的 CDF 写入，所以这个视图只需要在主机上。

## 3. 设计

### 3.1 显示哪些条目

**范围：只看主认知 markdown。** 项目 Brain 是它的四份文档；General Brain 是 CDF 写的 common DDD 文件
`Knowledge/Learned/_common/*.md` 和 `Knowledge/Learned/repos/*.md`（用户已确认放进 General Brain）。
参考资料（各 Brain 的 `2-understanding/knowledge/`、`Knowledge/Notes`、`Knowledge/Library` 等）不在这里：
它们在 DDD 的浏览树里看（用户 r2）。

**一条知识**是范围内同时满足下面三点的单元：
1. 身份是 `cand:<id>`，条目里确实有 `<!-- cand:<id>` 这个标记（`units.py` `_CAND` :25 会匹配正文里
   任何位置的 `cand:`，在这一层要求它在注释里；手写条目偶尔被认成 CDF 的，用户已接受）。
2. 不是旧格式撤回留下的墓碑。按**墓碑行的格式**认（`- [redacted] **Withdrawn** … | redacted:<hash> -->`），
   不按"单元里含 `redacted:`"认：EVOLUTION 的单元是整个 `###` 块（`units.py` :121–:137），旧墓碑是一行
   bullet，会落在上一块里面（现有那条就在 K001 块里），按"含"来判会把正常的 K001 当成墓碑。§3.5 之后不再
   产生新墓碑。
3. 没有被替换：不是某条有效 `supersedes` 关系的对象（取头用 `units.active_head` :279）。refine 后
   旧条目还在文件里，不再单独显示，新条目带"细化了〈旧标题〉"。

挪进 `*-archive.md` 的条目不在范围里；`ref:`/`last:` 元数据更新不改身份。人工编辑的、手写的条目不显示。

### 3.2 按三层七类分组

页面从上到下三层，层内按类型标签区分，最新的在前：

| 层 | 给用户看的名字 | 一句说明 | 类型 |
|---|---|---|---|
| meta | 元认知 · 怎么想 | 判断和纠错，不会随时间淡出 | 原则、纠错 |
| cognitive | 认知 · 怎么判断 | 做过的决定、对系统怎么运作的理解 | 决定、心智模型 |
| operational | 操作 · 怎么做 | 具体经验、踩过的坑、做事的步骤，不用会慢慢淡出 | 经验、坑、流程 |
| — | 待跟进 | AI 记下的未完成事项，不属于七类 | GapUnit |

层的顺序固定（元认知最重要，放最前）；当前时间范围里没有条目的层只显示一行"这段时间没有新的"，
不留大块空白。

### 3.3 时间范围，不设已看状态

不再记录"看过没有"（没有按钮，也没有状态可维护）。页面顶部选时间范围：**本周 / 本月 / 全部**，默认本周。
条目的时间按 §3.4 取；只有"全部"里会出现时间不详的条目，排在最后。Brain Hub 首页卡片和 Brain 页文档上
的计数统一是"本周新增 N"，有明确的参照。

### 3.4 每条带什么

| 字段 | 来自 | 缺失时 |
|---|---|---|
| `anchor`、`doc`、`section`、`type`、`layer`、`title`、`text` | `parse_units` 解析文件；`layer` 由 `type` 查表 | 不会缺 |
| `written_at` | 写入行的 `updated_at` | 换成 `ingestion_eval_log.at`，标"收集于"；都没有显示"时间不详" |
| `source` | `ingestion_inbox.source` 及其对话 / run / 文章（经 `candidate_id`） | 换成 `ingestion_eval_log.source`；都没有显示"来源记录已过期" |
| `refines` | `ingestion_relations` 中以它为主体的 `supersedes`，旧条目仍在文件里时取旧标题 | 不显示 |

所有来自条目的文字在前端按纯文本显示（转义 `<`、`>`、`&`），条目里常有 `<task_id>` 这类写法。

### 3.5 撤回 = 删除

`redact` 改为**删除这一条**，不再写墓碑：
- 删除的范围同现在的 `_redaction_span`（:389）：条目本身，加紧跟着的 `<!-- ref:... -->` 元数据行。拼接
  复用 writer 已有的 `_remove_unit_bytes`（`writer.py` :484），它已处理元数据行和空行，不另写一份。
- 一个文件里没有可解析的条目时，整份文档是一个单元（`units.py` :249–:255）；删它会留下空文件。这种单元
  **拒绝撤回**（400，"整份文档请在浏览树里处理"）。
- 原文照旧存进 `state/redacted/<hash>.bin`（备份，preserve 不 destroy）；替换关系、证据支持置为失效、
  `redactions` 一行、写入记录里的 `user_redact` 一行都照旧。
- 这是 `redact` 本身的行为，对所有受管文件一致（MEMORY、EVOLUTION、DDD、Knowledge），不为 DDD 单开一条
  路径：同一个动作在不同文件里结果不同，用户无法预期。
- 已有的墓碑（目前 1 条，在 `.context/EVOLUTION.md` 的 K001 块里）在落地时清掉一次：按墓碑行的格式删掉
  **这一行**，不走 `redact(anchor)`（它的 anchor 解析不到独立单元，会报错）；K001 本身一个字节都不动。
- **撤回的是"细化后的版本"时，旧版本会回来**：撤回把这条的所有 `supersedes` 关系置为失效（:463），refine
  保留了旧条目的字节，于是旧条目重新成为有效的一条，回到视图和注入里。这是现有行为；确认框里写明
  "撤回后会恢复它细化之前的版本"。

**不拦截再学。** CDF 之后如果从新的证据里再学到同样的知识，照常写入，重新出现在这里；这是自主流程的
正常结果，不是缺陷。撤回只决定"现在这条不要了"。

**不做恢复界面。** 原文备份在 `state/redacted/`，需要时手工找回；确认框里如实说明。

**落盘前在锁内重新定位。** 现在 `redact` 在拿文件锁之前就算好了"删掉后的文件"（:506–:513），锁内只比对
哈希；如果这期间 CDF 往同一个文件写了一条，落盘时抛 `StaleRevision`。它不是 `ValueError`，路由
（`routers/ingestion_redact.py` :52）接不住，用户看到 500，备份已写、条目还在、日志停在 `archive_applied`。
改为：在 `md_lock` 内重新读文件、按身份重新定位这一条、重新算出要写的内容；**先把新的 `before_hash`、
`intended_bytes`、`after_hash`、原文写回这条日志并提交，再写文件**。恢复流程只认日志里的哈希（:454–:456），
顺序反过来的话，写完文件、推进步骤前一崩溃，恢复会把它当作过期计划放弃（`writer.py` :893–:900）：条目没了，
关系、证据支持、撤回存档却都没写。条目在锁内已经找不到（被别的操作删了或改了身份），返回 409"这条已经变了，
请刷新"，不写备份、不留半截日志。

### 3.5b 撤回存档（r3）

被用户撤回的条目，就是 CDF 判断错、不该写进来的知识：误写（false positive）的现成案例。每一次撤回存一份
**不过期**的完整记录，给两个读者用：Brain 页上的撤回统计，和 CDF 的批量评测。

**存在哪：扩展现有的 `redactions` 表，不新建表，也不升库版本（见 §9.3）。** 它现在只有 anchor、时间、原因、备份路径
（`api_v2.py` :81；`governed_edit.py` :467 写入），**没有任何读者**。加这些列，在撤回的同一个事务里写入：

| 列 | 内容 | 来自 |
|---|---|---|
| `unit_id` | CDF 单元 id（anchor 里的 `cand:<id>`；不是 CDF 写的条目为空） | anchor |
| `brain` | 所属 Brain：项目名、`General`，或 `.context`（AI 自己的记忆） | anchor 的路径 |
| `target_path`、`section` | 写在哪 | anchor |
| `entry_type`、`layer` | 七类之一和它的层 | 解析条目 |
| `title`、`text` | 条目原文（完整，不截断） | 撤回前的条目 |
| `written_at` | 写入时间 | 写入记录 / eval 记录（§3.4） |
| `judge_record` | CDF 当初为什么写它：来源、证据、各卡答案、去向 | 撤回时从 live 表取；已过 30 天的从 `ingestion_eval_log` 取；都没有为空 |
| `evolution_id` | EVOLUTION 条目的编号（如 C071），其他文件为空 | 解析条目 |
| `reason` | 用户填的原因（已有） | 撤回时 |

写入用 `INSERT … ON CONFLICT(anchor) DO UPDATE`，只更新自己的列；不用 `INSERT OR REPLACE`（它先删整行，
会抹掉已存的存档字段）。

判官记录要在撤回时就复制下来：live 表 30 天、eval 记录 180 天后都会被清理（`worker.py` :608，
`eval_log.py` :23），不复制，半年后就不知道它为什么被写进来。`redactions` 不参与任何清理；撤回是少数
用户动作，量很小。`ingestion.db` 已纳入备份（CDF 设计 §17.10）。

**读者 1：Brain 页的撤回统计。** "学到了什么"页底部一行灰字："撤回过 N 条（共写入 M 条）· 查看"。
点开是只读列表：标题、类型、写入时间、撤回时间、原因、来自哪次对话。没有恢复按钮。

**读者 2：CDF 评测。** `eval_log.export` 增加一类记录 `{"stage": "judge", "origin": "withdrawn",
"label": "user_withdrawn", ...}`，带判官记录和用户原因；`scripts/cdf_batch_eval.py` 把它当作一个独立层
抽样，用来看判官的误写率和误写的模式。**不自动生成 golden case**（`s_golden-case`："Nothing creates
cases automatically"）：要固化成回归用例时由人挑选，经 `s_golden-case` 加入。

**同一个"撤回"只有一个来源。** Flow 健康里的"被用户撤回的比例"（`queries.py` `review_health` :531）现在
读写入记录里 `user_redact` 的 `relations`；改为读 `redactions`，与 Brain 页、评测导出用同一份数据（P8）。

### 3.6 接口

- `GET /api/ddd/brains/{name}/knowledge?since=week|month|all` →
  `{"layers": [{"layer": "meta", "items": [...]}, ...], "gaps": [...], "counts": {"week": N}}`。
  `items` 每条是 §3.4 的字段。
- 撤回：前端调用现有的 `POST /api/ingestion/redact`，reason 默认"在 Brain 页撤回"，成功后刷新。
- 计数：Brain Hub 首页和 Brain 页从同一个响应的 `counts.week` 取。

旧的 `/review`、`/review/approve`、`/review/reject` 在前端切换的同一个 commit 里删除（前后端一起发）。

### 3.7 界面（mockup：`Knowledge/Mockups/2026-10-30-brain-change-feed.html`）

- 顶部一句结论"SwarmAI 本周学到 N 条"，右侧时间范围切换，没有批量按钮。
- 三层依次排开，层名加一句说明；每条：类型标签、标题、两行原文（超出才"展开"）、一行灰字"写进 TECH ·
  某节 · 时间 · 来自对话「…」"，右下角淡显"撤回"。
- 点"撤回"就地展开确认："这条会从 SwarmAI 的 TECH 里删除，AI 不再使用它。原文另存一份备份。以后的对话里
  如果再出现同样的知识，AI 可能会重新学到。" 确认后这条淡出。
- 写 UI 前读 `s_frontend-design` 的 `data/design-judgment.md`（R15）。

## 4. 考虑过、不采用的

| 方案 | 为什么不 |
|---|---|
| 所有 Brain 进 git | 客户数据进 git；工作区曾有公开 remote（STEERING #19） |
| 非 git 的 Brain 隐藏 review | 用户自己的 Brain 照样看不到学到了什么 |
| 只读 `ingestion_writes` | 30 天后被清理，条目从视图里消失 |
| 保留"知道了"和已看集合（r1） | 知识自动进来、已经生效，"知道了"不改变任何东西，只是在维护一个状态（用户 r2） |
| 撤回后拦截再学（读 `redactions`） | 干预自主流程；同一知识有新证据时本该被学到（用户 r2） |
| 撤回保留墓碑 | 墓碑没有读者，还留在文件里被注入、被召回；用户要的是删除 |
| 撤回存档另建一张表 | `redactions` 已经是撤回的记录且没有读者，扩展它即可，避免两份撤回事实 |
| 撤回自动变成 golden case | 产品规则：不自动生成用例；由人从导出里挑 |

## 5. 删除

- 后端 `routers/ddd_brain.py`：`get_review`、`approve_review`、`reject_review`、`_scoped_diff_hunks`、
  `_parse_hunks`、`_hunk_signature`、`_default_watermark`、`_resolve_watermark`、`_read_watermark`、
  `_write_watermark`、`DiffIncompleteError`、`_HUNK_TAG`、`RejectHunkBody`，以及只为它们服务的 git 辅助
  函数（落地时按调用方逐个确认，R27）。
- 前端：`ReviewHunk`、`ReviewData`、`approveReview`、`rejectReviewHunk`、`BrainHub.tsx` 的 `hunkSummary`
  和代码块渲染、`dddOverview.ts` 的 `hunkMatchesDoc`。
- `governed_edit.redact` 里写墓碑的那段。
- 测试：`tests/test_ddd_brain_review.py` 改写为新接口的测试。
- 工作区：各 Brain 的 `.artifacts/.last-reviewed-sha`。

## 6. 非目标

- 不显示人工编辑；不做 Hive 上的视图；不追溯 CDF 之前的条目（没有 `cand:` 身份）。
- 不显示参考资料（`knowledge/`、`Knowledge/Notes`、`Knowledge/Library`），包括 CDF 写的 ReferenceUnit；它们在 DDD 浏览树里看。
- 不拦截再学；不做撤回的恢复界面。

## 7. 验证计划（在动手前定好）

**读的一方先于写的一方**：`knowledge` 接口是 `redact` 新结果（无墓碑）的读者，二者的测试先写。

**最终要成立的不变量（最后跑的测试集）**：
1. 一个不在 git 里的 Brain，CDF 写入一条后，`knowledge` 里有它，层和类型正确，本周计数 +1。
2. 30 天清理删掉写入记录之后，条目仍在"全部"里，时间换成"收集于"；两者都没有时排在最后，标"时间不详"。
3. 撤回后：文件里没有这一条，也没有墓碑；元数据行一起删掉，没有多出空行；`state/redacted/` 有原文；
   `knowledge` 里没有它；"被撤回比例"统计照旧算到它。
4. 撤回对 MEMORY / EVOLUTION 的条目同样是删除。
5. refine 后，旧条目不单独出现，新条目带"细化了〈旧标题〉"；replace 后旧条目消失。
6. 旧墓碑不出现在 `knowledge` 里。
7. General Brain：CDF 写进 `Knowledge/Learned/_common` 的一条出现在 General Brain 里。
8. 一个文件不是 UTF-8 的 Brain，`knowledge` 不失败（`parse_units` 严格按 UTF-8 读，`units.py` :224；逐个
   文件捕获解码错误，跳过并记日志）。
9. 前端：视图、文档计数、Brain Map 计数都从 `knowledge` 来；条目文字被转义；`tsc` 通过。
10. 撤回时 CDF 恰好写了同一个文件：撤回仍然成功（锁内重新定位），或返回 409；不会 500，不留半截日志。
11. 撤回存档：`redactions` 一行带齐 §3.5b 的列；判官记录在 live 表和 eval 记录都过期后仍在；Brain 页
    统计和 Flow 的撤回比例数字一致。
12. `eval-export` 输出里有这条 `origin=withdrawn` 的记录，带用户原因；golden set 没有新增用例。
13. 撤回 EVOLUTION 里编号最大的一条后，CDF 写入的下一条纠错编号不与它重复。
14. 新代码打开的 `ingestion.db` 版本号仍是 8，旧代码（v8）能打开并照常写入；`redactions` 新列齐全。
15. 文件没变时，第二次请求不重新解析（缓存命中）；首页一次请求返回所有 Brain 的计数。
16. 清理 EVOLUTION 里现有的墓碑：只删墓碑那一行，K001 字节不变；EVOLUTION 的 `###` 块里出现墓碑行时，
    视图仍把这一块当作正常条目。
17. 撤回时在"写完文件、推进步骤之前"注入崩溃：恢复后条目已删、关系失效、`redactions` 一行齐全，不被放弃。
18. 用只读连接、对一个缺新列的 `redactions`（旧备份）读取：不报错，新字段为空。
19. 同一 anchor 再写一次 `redactions`：已有的存档字段不被清掉。
20. 整份文档单元的撤回返回 400，文件不动。
21. 撤回一条 refine 的新版本后，被它细化的旧条目重新出现在视图里。

每条修复用 `scripts.mutation_check` 验证：去掉修复测试变红。

**审查**：设计已用完两轮（r1 correctness 审前提；r4 integration 审对现有系统的影响），之后的问题靠上面的测试
发现。代码落地前一轮 correctness + api-contract 专家审查（新接口、删掉旧接口、`redact` 行为变化），最多两轮。
UI 按 design-judgment 自查。

**上线后实测**：build 重启后打开 SwarmAI 和 General Brain，条目数与文件里的 `cand:` 条目一致；撤回一条，
确认文件里没有了、备份在。其余 Brain 等第一个相关任务跑完再看。

## 8. 写设计时发现的、不在这次范围里的问题

- **CDF 有写入用了小写路径 `Projects/swarmai/`**（8 条）。macOS 不分大小写，写进的就是
  `Projects/SwarmAI/`；在 Linux 的 Hive 上会建出另一个目录。要单独查项目名在哪一步变成小写。
- **`units.py` 的 `_CAND`（:25）匹配正文里任何位置的 `cand:`。** 用户已接受；这一层按 §3.1 第 1 点过滤。
- **用户编辑删掉 EVOLUTION 编号最大的一块，今天就会让编号被重复使用**（`_next_evolution_id` 只看文件）。根治要
  一个持久的"已用到第几号"，单独评估。
- **`ingestion.db` 过去的几次迁移有删列和重建表（`store.py` :53–:54、:99–:102），迁移前没有备份。** 与这次无关
  （这次只加列），但按 AGENT § Safety，删除和覆盖类迁移要先备份，单独评估。

## 9. 对现有系统的影响（r4，逐条对源码核实）

### 9.1 聊天和会话：不在路径上

- 新接口和撤回都跑在线程池（`executors.run_nav` / `run_in("io")`），不在 streaming 路径上。
- 撤回只在一个文件上短暂持有 `md_lock`；CDF 同时写这个文件会等它释放（毫秒级），不影响对话。
- 撤回 MEMORY / EVOLUTION 里的一条：已开着的对话沿用开始时的 system prompt；下一次新开的对话起不再注入，
  那一次的 prompt 缓存重新写一次，和 CDF 任何一次写入的代价相同。
- 检索每次现读文件（`addressing.find` :396 对单元现算 BM25），删掉的条目立刻搜不到；`Knowledge/` 下的
  撤回会同步 `knowledge_fts`（`governed_edit.py` :482 已有）。

### 9.2 撤回改成删除

- **删除后的文件形状不是新情况**：用户编辑删掉一条条目（`apply_user_edit`）是早已在用的路径，本周整理
  Brain 时做过几十次（`ingestion_writes` 里的 `user_edit` 行）。
- **墓碑没有读者**（§2.6），换成删除不影响任何读取方；Flow 视图读的是统计字段
  `agentSelfWithdrawn` / `agentSelfWithdrawnRate`（`CognitiveDistillationFlow.tsx` :207，`services/ingestion.ts`
  :116），数据源改成 `redactions` 后字段名和含义不变。
- **EVOLUTION 编号会被重复使用（必须修）**：新编号 = 文件里同一节现有最大编号 + 1（`writer.py`
  `_next_evolution_id` :458）。墓碑原来占着编号；删掉最新的一条（如 C071）之后，下一条纠错又会叫 C071，
  而别处引用的是编号（如 MEMORY 里的"(C052)"）。改为：最大编号同时取文件里的和 `redactions.evolution_id`
  里该节记录过的。用户编辑（`apply_user_edit`）删掉最大一条时今天就会重复，这是现有问题，记在 §8。
- **回滚到旧二进制**：旧版本的撤回仍写墓碑；§3.1 第 2 点已过滤墓碑，视图不受影响。

### 9.3 `ingestion.db`：不升版本号

`ingestion.db` 是不可替代的状态。打开时版本号必须正好等于代码里的值，否则抛 `unsupported ingestion schema`
（`store.py` :289）。如果为加列把版本升到 9，退回旧二进制时它打不开这个库，CDF 整个停摆，而产品有多版本
回滚（`runtime/<v>/`）。所以：
- **不升版本**。`api_v2.py` :81 的 `CREATE TABLE redactions` 加上新列（新建的库直接有）；已有的库在启动时经
  `connect_rw` 检查 `redactions` 的列，缺的用 `ALTER TABLE redactions ADD COLUMN ...` 补上，同一个事务，只加
  不删、不重建表，在任何路由开始服务之前完成。旧代码写 `redactions` 用具名列（`governed_edit.py` :467），
  新列可空，新旧二进制都能打开同一个库。
- **读取方不假设新列一定在**：大部分读取用 `connect_ro`（只读，从不迁移，`store.py` :348）；从旧备份恢复的
  库也没有新列。读 `redactions` 的地方先看 `PRAGMA table_info`，缺的列按空值处理，不报错。
- **回滚期间的撤回**：旧二进制用 `INSERT OR REPLACE` 写同一个 anchor 时会清掉这一行的存档字段，这些字段只能
  丢失；写明为已知限制。
- 只加可空列，不删除、不覆盖任何数据，所以不需要先备份（AGENT § Safety 的备份要求针对删除和覆盖）。
- 已有的撤回行（目前 1 行）没有新列的值：`unit_id`、`brain`、`target_path` 从 anchor 推出来回填；原文和
  判官记录拿不到的保持为空，不编造。

### 9.4 计算量和事件循环

实测：9 个 Brain 加 General Brain，45 个文件、2306 个单元全部解析 84 ms，关联写入记录和替换关系 10 ms
（2026-10-30）。P0 待办里记着"后台线程占 GIL 会让事件循环卡顿（streaming 冻结）"，所以：
- 只在用户打开 Brain Hub / Brain 页时计算，不放进任何定时任务。
- 按 `(路径, mtime, size)` 缓存每个文件的解析结果，文件没变就不重算；Brain Hub 首页一次请求拿所有 Brain
  的计数，不按 Brain 各发一次。

### 9.5 删除旧 review：使用方是封闭的

git review 的三个接口（`/review`、`/review/approve`、`/review/reject`）的使用方只有：前端 `BrainHub.tsx`
（:35、:504、:1139–:1197）、`BrainMapView.tsx`（:29、:71）、`dddOverview.ts`、`services/ddd.ts`（:522–:617）；
后端只有它自己的测试 `test_ddd_brain_review.py`、`test_ddd_brain_router.py`。没有 hook、job、eval 或其他
路由调用它们（2026-10-30 全仓 grep）。前后端在同一个 commit 里切换。审查之后又补查了整个仓库（含 `docs/`、`templates/`、`skills/`）和工作区的
`.claude/skills/`：除 todos 自己的 `/todos/{id}/review` 外没有别的使用方。

### 9.6 Hive

`.artifacts/` 和 `redactions` 都只在主机；主机上撤回后，Brain 下次同步到 Hive 时 Hive 上的副本随之更新
（`brain_sync`）。Hive 上不提供这个视图。
