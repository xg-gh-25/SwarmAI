---
title: Brain 的变化流 —— 用 CDF 写入的条目取代 git diff review
created: 2026-10-30
updated: 2026-10-30
project: SwarmAI
status: 待确认
follows: 2026-10-26-swarm-cognitive-distillation-flow.md（知识只由 CDF 写入）；2026-10-29-cdf-post-launch.md
---

# Brain 的变化流

## 0. 结论

Brain 上的"自上次看过之后它长了什么"改成读 **CDF 写进这个 Brain 的条目**，不再读 git diff。
一条变化就是一条知识：条目原文、写在哪份文档的哪一节、为什么写（来源）、什么时候写。用户能对它做
两件事：**知道了**（标为已看）和**撤回**（走现有的 redact）。对每个用户、每个 Brain 都一样，不管
Brain 在不在 git 里。git diff 的整套 review 删掉。

## 1. 问题

**出厂设置下，用户自己的 Brain 都不在 git 里。** 新工作区的 `.gitignore` 由
`SwarmWorkspaceManager` 写入，内容含 `Projects/*` 和 `!Projects/SwarmAI/`
（`core/swarm_workspace_manager.py` 中 `GITIGNORE_CONTENT`，约 :751–:800）。这是有意的：Brain
里有客户数据，工作区曾挂过公开 remote（STEERING #19）。

**review 的每一处都依赖 git。**
- 后端：`GET /brains/{name}/review` 用 `git diff <watermark>..HEAD -- Projects/<name>` 算代码块
  （`routers/ddd_brain.py` `_scoped_diff_hunks` :1340，`get_review` :1390）；"知道了"把
  `.artifacts/.last-reviewed-sha` 写成 HEAD（`approve_review` :1427，`_write_watermark` :1205）；
  "拒绝"用 `git apply -R` 反向应用一个代码块（`reject_review` :1447）。
- 前端：review 视图（`BrainHub.tsx` `ReviewView` :1135）、每份文档的"自上次 review 新增"
  （`dddOverview.ts` :72–:80，按代码块的文件名归到文档）、Brain Map 的"written since review"
  （`brainMapModel.ts` :168，数 `tag === 'cultivation·auto-applied'` 的代码块）。

所以对每个用户自己的 Brain，这些永远是 0，或者像 AIDLC 那样显示"把它移出 git 的那个 commit"
（已修的 500 就是这个：`8a77e5a04`）。用户最想知道的"我的 Brain 自己长了什么"，产品看不到。

## 2. 现有的、可以直接用的东西（都已对源码核实）

1. **条目自带永久身份。** CDF 写进 DDD 文档的每个条目，行尾带 `<!-- cand:<unit_id> -->`
   （`core/ingestion/writer.py` `_render_ddd` :345、通用分支 :442）。`core.ingestion.units.parse_units`
   （:186）把文件解析成单元，带了这个标记的单元 `identity` 就是 `cand:<unit_id>`（:106），`anchor`
   是 `<path>#<section>#<identity>`（:49）。不带标记的条目（手写的、CDF 之前的）身份是内容摘要。
2. **知识只由 CDF 写入；用户编辑、撤回和格式整理也会改这些文件。** CDF 在一个 Brain 里写的是
   `Projects/*/2-understanding/{PROJECT,PRODUCT,TECH,IMPROVEMENT}.md`；它还把跨项目的知识写进
   `Knowledge/Learned/_common/*.md` 和 `Knowledge/Learned/repos/*.md`（common DDD，`writer.py`
   `_ensure_common_ddd` :190）。同样会改这些文件的还有 `apply_user_edit`、`redact` 和
   `ddd_format.normalize`（`is_governed` 的范围见 `core/ingestion/api.py` :241）。实测 10-29 以来
   CDF 的写入：SwarmAI 四份文档 86 条，`Knowledge/Learned` 约 50 条，其余 Brain 0 条（还没有处理过
   与它们相关的任务）。
3. **写入记录 `ingestion_writes`**（`api.py` :83）：每个单元最多一行（`op_id = ingestion:<unit_id>`），
   含 `unit_id`、`target_path`、`created_at`/`updated_at`，没有来源字段。来源在
   `ingestion_inbox.source`，经 `ingestion_units.candidate_id` 关联。**这两张表都会被清理**：每日清理
   按候选的**收集时间**（`ingestion_inbox.created_at`）过 30 天，就把候选和它的写入行一起删掉
   （`core/ingestion/worker.py` 截止 :608、删除 :662），所以在队列里等过的候选，写入行不到 30 天就没了。
   删之前，单元的来源和判官记录复制进 `ingestion_eval_log`，保留 180 天（`eval_log.py`
   `archive_candidates` :56，`RETENTION_DAYS` :23）；它的 `at` 是收集时间，不是写入时间。
   单元之间的替换关系在 `ingestion_relations`（`api.py` :107，只有 `supersedes` 一种），没有清理。
   `core/ingestion/queries.py` `item_detail`（:269）已经按 `unit_id` 把单元、写入、关系和来源连在
   一起，变化流复用这段关联，不另写查询。按 `unit_id` 关联，不按路径：实测有写入记的路径是
   `Projects/swarmai/`（见 §8）。
4. **撤回已经有了。** `governed_edit.redact(anchor, reason, actor="user")`（:491）把单元换成
   墓碑 `- [redacted] **Withdrawn** [...] <!-- cand:<原 id> | redacted:<hash> -->`（:511），墓碑
   **沿用原来的身份**，原文存进 `state/redacted/`；路由 `POST /api/ingestion/redact`（`routers/ingestion_redact.py`），
   只接受本机且带 `X-Swarm-User-Action: redact` 的请求；前端 `services/ingestion.ts` :173 已在调用。
5. **`.artifacts/` 不随 Brain 同步到 Hive**（`core/brain_sync.py` :14）。Hive 上的新知识以
   lessons 回到主机，再经主机的 CDF 写入，所以变化流只需要在主机上。

## 3. 设计

### 3.1 什么算一条"变化"

**范围**：一个项目 Brain 是它的四份文档；General Brain 是 CDF 写的跨项目文件
`Knowledge/Learned/**/*.md`，加上它自己的 `Knowledge/Library/**/*.md`。这一条是我的判断：
`Learned` 下的 common DDD 现在是 CDF 第二大的写入去处，却不属于任何一个 Brain，放进 General Brain
用户才看得到。

**一条变化**是范围内同时满足下面四点的单元：
1. 身份是 `cand:<id>`，而且条目里确实有 `<!-- cand:<id>` 这个标记。`units.py` 的 `_CAND`（:25）
   会匹配正文里任何位置的 `cand:`，手写条目在正文里提到一个 id 也会拿到这个身份；变化流在自己这一层
   要求标记出现在注释里（§8 记了 `units.py` 本身的问题）。
2. 不是撤回留下的墓碑（条目里没有 `redacted:` 标记）。
3. 没有被替换：不是某条有效 `supersedes` 关系的对象（`ingestion_relations`，取头用
   `units.active_head` :279）。`refine` 会保留旧条目的字节（`writer.py` :545），新条目带着旧条目的
   内容；只看身份的话，用户会把旧内容再看一遍。
4. 身份不在已看集合里。

身份读自文件本身，所以不受 30 天清理影响。挪进 `*-archive.md` 的条目不在范围里，自然消失；
`ref:`/`last:` 元数据更新不改身份，不会冒出假变化。人工编辑（`user_edit`、手写条目）不进变化流：
review 是为了让人看 AI 自己写了什么。

### 3.2 每条变化带什么

| 字段 | 来自 | 缺失时 |
|---|---|---|
| `anchor`、`doc`、`section`、`title`、`text` | `parse_units` 解析文件 | 不会缺 |
| `written_at` | 写入行的 `updated_at`（写入完成的时间） | 换成 `ingestion_eval_log.at`，标明是"收集于"；都没有显示"时间不详" |
| `source` | `ingestion_inbox.source`（经 `candidate_id`） | 换成 `ingestion_eval_log.source`；都没有显示"来源记录已过期" |
| `refines` / `replaces` | `ingestion_relations` 里以它为主体的 `supersedes`；refine 时旧条目还在文件里，取它的标题 | replace 的旧条目已移到 `.archive/`（`writer.py` :550），只显示"替换了一条旧条目" |

排序按 `written_at`，两种时间分开显示，不混成一个字段。

条目本身永远完整；时间和来源是补充信息，过期了就如实说过期，不猜。

### 3.3 已看集合

- **存在哪**：`Projects/<name>/.artifacts/review-seen.json`，替代现在的 `.last-reviewed-sha`。
  跟着 Brain 走：改名时一起搬，删除时一起进废纸篓，不同步到 Hive。
- **形状**：`{"version": 1, "seen": ["cand:…", …]}`，存的是身份，不是 anchor：`ddd_format.normalize`
  会把条目挪到别的节，anchor 跟着变，身份不变。写入用临时文件加 `os.replace`（同现在的
  `_write_watermark`）。
- **有界**：每次写入时只保留仍在四份文档里的身份（`seen ∩ 当前身份`），大小不超过这个 Brain
  的条目数。
- **"知道了"按身份标，不按时间**：前端把它显示过的那批身份发回来，只标这些。用户点"知道了"
  的同时 CDF 新写了一条，那一条仍是未看（R3(8)：同一条由 id 判定）。

### 3.4 第一次读到一个 Brain（迁移）

没有 `review-seen.json` 时，先建基线，再出变化流：
- 有旧的 `.last-reviewed-sha` 且那个 commit 还在：取它的提交时间 T。写入时间晚于 T 的单元保持未看；
  **写入时间查不到的也保持未看**（宁可多显示，不把没看过的悄悄标成已看）；其余算已看。然后删掉旧文件。
  CDF 10-29 才上线，现在每个 `cand:` 单元都还有写入行，所以这一步今天是精确的。
- 否则：当前所有 `cand:` 单元都算已看。

这样升级后不会一下冒出上千条"新变化"，SwarmAI 现在还没看的那几条也不会丢。

### 3.5 接口

- `GET /api/ddd/brains/{name}/changes` → `{"items": [...], "total": N}`，按 `written_at` 新的在前，
  时间不详的排最后。`items` 里每条是 §3.2 的字段。
- `POST /api/ddd/brains/{name}/changes/seen`，body `{"identities": [...]}` → 把这些身份加入已看集合。
  只接受当前确实在这个 Brain 变化流里的身份，其余忽略并在响应里列出。
- 撤回：前端直接调用现有的 `POST /api/ingestion/redact`，reason 默认"在 Brain review 中撤回"，
  成功后刷新变化流。不新建撤回接口。

旧的 `/review`、`/review/approve`、`/review/reject` 在前端切到新接口的同一个 commit 里删除（前后端
一起发，没有旧客户端需要兼容）。

### 3.6 界面

- **review 视图**（Brain 页的"reflect"）：每条一张卡，标题加原文，下面一行"写进 TECH · 某节 ·
  10-29 · 来自会话 xxx"，右侧两个动作"知道了""撤回"。顶部一个"全部知道了"，作用于当前显示的这批。
  空的时候显示"自上次看过之后没有新内容"。
- **每份文档的"新增"计数**：直接按 `doc` 字段数，不再从代码块的文件名去猜。
- **Brain Map 的"written since review"**：数 `total`。
- 写 UI 前先读 `s_frontend-design` 的 `data/design-judgment.md`（R15）：这张卡要回答的决定是
  "这条该不该留在我的 Brain 里"，所以原文和来源是主体，计数和时间是辅助。

## 4. 考虑过、不采用的

| 方案 | 为什么不 |
|---|---|
| 所有 Brain 进 git | 客户数据进 git；工作区曾有公开 remote（STEERING #19） |
| 非 git 的 Brain 隐藏 review | 用户自己的 Brain 照样看不到变化，只是不再显示 0 |
| 只读 `ingestion_writes` | 30 天后被清理；用户一个月没打开某个 Brain，变化就静默消失 |
| 新建一张永久的"变化表" | 文件里的 `cand:` 身份已经是永久记录，再建一张表是两份事实，会互相漂移 |
| 已看位置用时间戳 | 文件里没有条目的写入时间（元数据里的 `last:` 是最近被引用的日期，会变）；30 天后写入记录也没了，时间戳无从比较 |

## 5. 删除

- 后端 `routers/ddd_brain.py`：`get_review`、`approve_review`、`reject_review`、
  `_scoped_diff_hunks`、`_parse_hunks`、`_hunk_signature`、`_default_watermark`、`_resolve_watermark`、
  `_read_watermark`、`_write_watermark`、`DiffIncompleteError`、`_HUNK_TAG`、`RejectHunkBody`，以及
  只为它们服务的 git 辅助函数（落地时按调用方逐个确认，R27）。
- 前端：`ReviewHunk`、`ReviewData` 的代码块字段、`approveReview`、`rejectReviewHunk`、
  `BrainHub.tsx` 的 `hunkSummary` 和代码块渲染、`dddOverview.ts` 的 `hunkMatchesDoc`。
- 测试：`tests/test_ddd_brain_review.py` 改写为新接口的测试。
- 工作区：迁移时删掉各 Brain 的 `.artifacts/.last-reviewed-sha`。

## 6. 非目标

- 人工编辑（界面里的、编辑器里直接改的）不进变化流。
- 不做 Hive 上的变化流（§2.5）。
- 不追溯 CDF 之前写的条目（它们没有 `cand:` 身份）。

## 7. 验证计划（在动手前定好）

**读的一方先于写的一方**：`review-seen.json` 是新的持久化形状，读它的 `changes` 接口和迁移与写它的
`seen` 接口在同一个 commit 落地，先写读的测试（R3(7)）。

**最终要成立的不变量（就是最后跑的测试集）**：
1. 一个不在 git 里的 Brain，CDF 写入一条后，`changes` 里有它；标为已看后没有它。
2. 30 天清理删掉写入记录之后，条目仍在 `changes` 里，时间换成"收集于"，来源取自 eval 记录；两者都没有时如实显示为过期。
3. "知道了"只标发回来的身份：标记期间新写入的一条仍是未看。
4. 条目挪进 archive、被撤回、被替换后，不在 `changes` 里；已看集合不再保留它的身份。
5. 迁移：有旧 watermark 时，晚于它的写入保持未看，其余已看，旧文件被删；没有时全部已看。
6. `seen` 接口拒收不在这个 Brain 变化流里的身份（不能借它改别的 Brain）；一条被 normalize 挪到别的节之后，按身份标记仍然生效。
7. 一个文件不是 UTF-8 的 Brain，`changes` 不失败：`parse_units` 严格按 UTF-8 读（`units.py` :224），
   变化流逐个文件捕获解码错误，跳过那个文件并记日志。
9. 撤回一条之后，它的墓碑不在 `changes` 里。
10. refine 之后，被细化的旧条目不在 `changes` 里，新条目带"细化了〈旧标题〉"。
11. General Brain：CDF 写进 `Knowledge/Learned/_common` 的一条出现在 General Brain 的 `changes` 里。
8. 前端：review 视图、文档计数、Brain Map 计数都从 `changes` 来；`tsc` 通过。

每条修复用 `scripts.mutation_check` 验证：去掉修复测试变红。

**审查**：落地前做一轮 correctness + api-contract 专家审查（新接口加删掉旧接口），最多两轮。UI 按
design-judgment 自查。

**上线后实测**：build 重启后，打开 SwarmAI 和 General Brain 的 review，确认条目和计数与文件里的
`cand:` 条目一致。其余 Brain 现在还没有 CDF 写入，显示为空是正确的；等第一个与它们相关的任务跑完，
再打开一次确认它出现。

## 8. 写设计时发现的、不在这次范围里的问题

- **CDF 有写入用了小写路径 `Projects/swarmai/`**（8 条，`ingestion_writes.target_path`）。macOS 磁盘不分
  大小写，所以它写进的就是 `Projects/SwarmAI/`；在 Linux 的 Hive 上它会建出另一个目录。要单独查项目名
  是在哪一步变成小写的。
- **`units.py` 的 `_CAND`（:25）匹配正文里任何位置的 `cand:`**，手写条目正文里提到一个 id 就会拿到
  `cand:` 身份。应该只认注释里的标记；但改它会改变已有单元的身份（anchor 被 `claim_support` 等处保存），
  要单独评估。变化流先在自己这一层过滤（§3.1 第 1 点）。
