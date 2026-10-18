# Autonomous Pipeline Report: pipeline_test.py：单文件测试拆到多个 shard 并行

**Run ID:** run_1f81be36 | **Project:** SwarmAI | **Profile:** full
**Date:** 2026-10-18 07:34 UTC | **Duration:** 55.2 min (in progress) | **Tokens:** 462,163 (measured, $13.88, cache read 26,683,854; 不含当前一轮 / excludes the turn in flight; 含建 run 前的同一轮 / includes the pre-run part of the creating turn)
**Gate 1:** WARN | **Files:** 3 | **Tests:** not recorded

## TL;DR
**解决了什么问题**
- 原来：runner 按文件数分 shard，一个文件只能整体进一个 shard 串行跑：test_pipeline_test.py 一个文件就是整个 run 的长杆（负载下 5 分钟以上，按真实历史回放是 143 秒对其余 shard 不到 4 秒），机器一忙就超时，只能反复重跑
- 现在：runner 记录每个测试的耗时，按耗时分 shard；超过一个份额的重文件按测试族拆成几块分到不同 shard，每个测试正好跑一次。同一组目标按真实历史回放：最慢 shard 143.3 秒 → 18.4 秒（3 个 shard → 9 个）。没有历史时分法和以前完全一样

**怎么做的**
- events 插件在每条 report 里带上耗时，并新增按文件生效的精确 keep 列表（PIPELINE_TEST_KEEP），与原来的精确 deselect 列表并存；插件目录按源码 hash 分版本，不同 commit 的 runner 不会互相覆盖
- _parse_pytest 汇总每个测试的耗时；只有 main run 把真正跑过、通过/跳过/超时的叶子测试写进 <cache>/durations.json（原子写，2 万条上限，30 天过期，失败的测试和 NaN/Infinity 不记）
- _cost_bins 按优先级顺序把目标切成耗时约一份的连续组（run 自己的文件在最前面）；_split_file 把重文件按测试族拆块：除最后一块外只跑自己的 keep id，最后一块跑剩下的（含历史里没有的新测试），每块都带 AC lead 的 deselect
- baseline、mutant、flaky 重跑和 requeue 的分法不用历史；拆块文件的 collect_error 合并为一条

**遗留问题**（11 条，见 §10）

## 1. Requirement
单文件测试拆多进程并行：pipeline_test.py runner 目前一个测试文件只能进一个 shard 串行跑（test_pipeline_test.py 约 40 个端到端测试要 5 分钟以上，test_pipeline_run.py 负载下 170 秒只跑完 39 个），机器负载高时整文件超时、拿不到干净证据。目标：把大文件的测试按 nodeid 拆到多个 worker 并行跑，结果按 nodeid 合并回证据；serial suite 保持串行；不破坏现有的超时重排、recheck 覆盖、cache_key、AC 先行。

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> 起 run：单文件测试拆多进程并行

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | user-story | user-stated: 拆多进程需要知道每个测试多重 | confirmed | ● | Given a verify run, when it finishes, then the cache holds each executed nodeid's last duration |
| FR-2 | user-story | user-stated: 把大文件的测试按 nodeid 拆到多个 worker 并行跑 | confirmed | ● | Given a file whose recorded cost exceeds the per-shard target, when shards are planned, then its tests land in more than one shard and each runs once |
| FR-3 | constraint | user-stated: serial suite 保持串行 | confirmed | ● | Given no duration history, when shards are planned, then the plan equals the count-based plan |
| FR-4 | constraint | user-stated: 不破坏现有的超时重排、recheck 覆盖、cache_key、AC 先行 | confirmed | ● | Given the existing runner suite, when it runs after the change, then it passes unchanged |

### 澄清问答 (Clarifications — who decided what)
_(无澄清记录 / no clarifications)_

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
✅ 无未闭合问题 (no leftover — all requirements closed, no AI assumptions)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 8.00 | |
| Priority | 8.00 | |
| Feasibility | 8.00 | |

**Acceptance Criteria:**
1. AC1: every pytest report event the runner records carries the test's duration, and a run of verify/regression/tdd updates a per-nodeid duration history in the runner cache (bounded, keyed by nodeid, newest wins).
2. AC2: with duration history present, _shards packs non-serial targets into shards by recorded cost (longest first onto the least-loaded shard) instead of by file count, keeping shard start order by target priority.
3. AC3: a non-serial whole-file target whose recorded cost exceeds the per-shard target is split into nodeid chunks across shards; the last chunk is the whole file with --deselect of every nodeid placed in other chunks, so tests missing from history still run; every test of the file runs exactly once.
4. AC4: serial suites (_SERIAL_SUITES) are never split and keep a shard of their own; with no duration history the shard plan is identical to today's count-based plan.
5. AC5: AC-lead shards, per-test-timeout requeue, recheck overlay and cache_key behave as before: the change only alters how targets are grouped, and results still merge by nodeid with no duplicates.


## 3. Design & Approach
**Research Findings (THINK):**
- _shards (pipeline_test.py:872-915) puts each whole-file target in one args entry and packs files into about 2*workers groups by count (size = ceil(len(normal)/(workers*2))); a file is never split and cost is never considered.
- The events plugin (pipeline_test.py:657-666) emits start/report without report.duration; result dicts (_res, :1017) carry no duration, so the runner has no cost data.
- cache_key (pipeline_test.py:1838-1860) digests the target nodeid sets, not the shard plan, so changing how targets are grouped cannot change cache hits.
- The plugin's --deselect is exact-match and env-passed (PIPELINE_TEST_DESELECT), and _target_gaps (:1124-1155) already skips a whole-file target whose tests were all deselected, so a chunk = whole file + deselect of the other chunks' nodeids is tolerated by the existing gap logic.
- _write_json (:160-165) is atomic (tmp + os.replace); a lost concurrent update only loses hint data.
- Heavy files test_pipeline_test.py and test_pipeline_run.py isolate every test via tmp_path and SWARM_TEST_CACHE_DIR, so running their tests in parallel processes is safe. Two existing tests pin _shards behaviour (test_pipeline_test.py:862, :978).

**Alternatives Evaluated:**
- SIMPLICITY — effort: S (0.2 session)
- QUALITY — effort: M (1 session)
- FLEXIBILITY — effort: M (1 session)

**Chosen Approach:** Add duration to the events plugin's report record and an exact-match, per-file KEEP list (PIPELINE_TEST_KEEP) next to the existing DESELECT list: an item whose file has a keep list is kept only if its nodeid matches one (exact, or its [param]/::method). _parse_pytest sums phase durations into result['duration'] (a started-but-unfinished test = PER_TEST_TIMEOUT_S). After the MAIN run only, _save_durations merges ran results into <cache>/durations.json ({backend/-prefixed nodeid: [seconds, epoch]}, capped, oldest evicted, tmp+os.replace; concurrent writers = last wins, best-effort). _Ctx loads it once; only the main _pytest_jobs call passes it to _shards. _shards keeps the AC lead and serial handling unchanged; with non-empty history it LPT-packs non-serial targets by cost over 2*workers bins, splitting heavy whole-file targets into keep-chunks plus one complement-deselect last chunk, siblings never sharing a bin; keep-chunk files go into the job's 'deselected' meta so an emptied chunk is not flagged by _target_gaps. Empty history -> old code path unchanged.

**Success Criteria:**
- all new unit tests and test_pipeline_test.py pass through the runner
- a second verify of this run, with history recorded by the first, shows the slowest shard closer to the mean than before (shard walls from the .out summary lines)

**Always:**
- fall back to the exact old plan when history is empty
- never pass history nodeids as pytest targets (only as exact-match deselects)
- keep chunks of one file in different shards
- history only for the main run

**Never:**
- split a serial suite
- change cache_key composition
- drop or duplicate a test to balance


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Profile full: bounded change to one module, not an open-ende | mechanical |  |
| evaluate | Balance by recorded duration rather than collecting nodeids  | taste |  |
| evaluate | The test_pipeline_run.py hang found by Gate-0 is out of scop | taste |  |
| think | Chunks pass the whole file plus a complement deselect list i | taste |  |
| think | History records only main-tree runs (not HEAD baseline or mu | taste |  |
| plan | Evidence result fields stay unchanged; duration lives only i | mechanical |  |
| plan | Unit tests go in a new small file so AC evidence does not de | taste |  |
| plan | Nodeid-set targets (requeue, recheck) are packed by cost but | taste |  |
| plan | Gate-1 BLOCK (complement-deselect chunks duplicate tests mis | mechanical |  |
| build | Family = file::first-segment without [params]; any two ids w | mechanical |  |
| build | History write is best-effort last-writer-wins (atomic replac | taste |  |
| review | Spec BLOCK fixed: LPT ties broken by family count + empty ch | mechanical |  |
| test | accept load-inflated wall time | judgment | per-test durations scale with machine load (5x bet |
| deliver | leave nested-worker capping to its own run | judgment | it changes how every nested test sizes itself and  |

**Summary:** 5 mechanical, 7 taste, 2 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**15 found · 12 fixed · 3 open**
- [MEDIUM] pipeline_test.py _cost_bins: LPT mixed top-ranked own files with low-priority im
- [MEDIUM] pipeline_test.py: every chunk of a split file re-emits its collect_error / colle
- [MEDIUM] pipeline_test.py _plugin_dir: one shared plugin file; an older runner (worktree)
- [MEDIUM] pipeline_test.py durations history: deleted / renamed ids never age out. Fixed: 
- [MEDIUM] pipeline_test.py _cost_bins: record shapes told apart by tuple length. Fixed: ex

### 7.6 Completion Audit
**Requirements met:** 5/5
**All green:** yes


## 8. Files Changed
- `backend/scripts/pipeline_test.py`
- `backend/tests/test_pipeline_test.py`
- `backend/tests/test_pipeline_test_balance.py`

## 9. Lessons
- [pitfall] Packing work by cost when a deadline drops the tail: longest-first (LPT) balancing spreads the top-priority items over every bin, so a priority item can land behind low-priority work and never start. Cut priority-ordered contiguous groups of about one cost share instead. A test whose items all cost the same cannot tell the two orders apart: make priority order and cost order disagree.
- [pitfall] Splitting one unit of work by 'each part excludes the other parts' known items' runs every item missing from the known list in EVERY part. Non-final parts need an explicit include list, and exactly one part may own the unknown items. Also guard the empty part: an include-list part with nothing in it must not be emitted, or it falls back to 'run everything'.
- [pitfall] A generated helper file in a shared cache that each version of a tool rewrites to its own content is a cross-version race: an older checkout running at the same time swaps its version in mid-run. Put the content hash in the path so versions never share the file.


## 10. Known Gaps & Attention Flags
- 未修复的 MEDIUM 发现：concurrency: split chunks of the nested-runner suite run concurrently, each nested runner sizing its own workers; oversubscription under load. Not fixed here: capping nested workers changes every nested test; measured and listed in attention_flags for its own run.
- 未修复的 LOW 发现：red-team: chunks of one file run in parallel with each other; only _SERIAL_SUITES are protected. Listed in attention_flags (no-split list).
- 未修复的 LOW 发现：code-quality: history loaded on every invocation, also on cache-hit paths. Accepted: one JSON read, under 2 MB.
- 本次提交用了 run-commit --force（XG 批准）：D7 找不到 concurrency 专家的审查记录，原因是 run-create 的 stdout 被重定向到文件，daemon 没能把 session 关联到 run_1f81be36，本 run 所有子 agent 的记录都写成了 run_39068a4a。审查确实做过：concurrency 15:16:51 的 adv marker（expert_id=concurrency，reviewed_paths 含 pipeline_test.py / test_pipeline_test.py / test_pipeline_test_balance.py），Gate-1、spec-compliance、Gate-2 其余 4 位和 meta-review 同样如此
- 嵌套 runner 并发会推高负载：test_pipeline_test.py 拆开后几块同时跑，每块里的测试又各自起带 worker 的嵌套 runner；本 run 负载 21 时这个文件 48 个测试累计 350 秒（负载 4.7 时 70 秒），整体墙钟 121 秒。修法是给 rig 里的嵌套 runner 限 1 个 worker（_worker_count 读一个 env），会影响所有嵌套测试，需单独一个 run
- 一个 AC 引用的测试被改名或删除时，pytest 返回 rc=4，整个 AC lead shard 的测试都报 error（本 run 发生过一次，18 个 AC 测试一起红）；runner 应把不存在的 AC nodeid 单独报出来而不是让整个 shard 失败
- 同一个文件的几块会互相并行，文件里依赖进程外共享状态（固定端口/路径、模块级全局）的测试可能互相干扰；目前只有 _SERIAL_SUITES 那几个名字受保护，可加一个不拆分名单
- 只有部分测试有历史的文件，成本按已记录的测试求和，未记录的算 0，会被低估；跑过一次完整 main run 之后自动校正
- `run-update --stage-json` 会整条替换阶段记录（我用它补 ac_coverage 时把 build 的 status 抹掉了，已重新提交恢复）；`--files-touched` 必须传 JSON 数组，传错时报错被 /dev/null 吞掉会让 REVIEW 和 Gate-2 拿不到 diff、少派专家（本 run 的 REVIEW 因此漏了 code-quality，已在 Gate-2 那一批补上）
- MED 风险：nested runners in split chunks oversubscribe the CPU; timeouts look flaky and inflate the recorded costs
- LOW 风险：a file with partial history is costed as the sum of its recorded tests only


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 07:34 UTC*
