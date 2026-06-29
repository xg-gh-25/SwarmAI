---
title: "当 assert 失效之后:SwarmAI 的 Eval 架构全景与方法论"
created: 2026-06-26
updated: 2026-06-28
status: published
---
<!-- GitHub Discussion #78: https://github.com/xg-gh-25/SwarmAI/discussions/78 -->
# 当 `assert` 失效之后:SwarmAI 的 Eval 架构全景与方法论

> 一个 Agent OS 如何把"评测"从"偶尔跑一下的脚本"做成"卡在开发生命周期里的硬门"——以及背后的方法论、参考来源、和每一个 gate 的取舍。

---

## TL;DR

传统软件靠 `assert` + CI 红绿灯保证"没退化"。但 Agent 不行——**非确定性**让 `assert` 失效,**Prompt 即源码**却没有 diff/review/rollback,**依赖会自己漂移**(模型静默更新,什么都没部署但行为变了)。

SwarmAI 的答案:**Eval 就是 Agent 时代 `assert` 的替代品,而且必须是一道 git-bound 的硬门。**

这篇讲三件事:
1. **架构全景** — Eval 作为一个**系统级、与 DDD 解耦**的独立子系统长什么样
2. **方法论** — 怎么做 Agent eval(对标 AWS 官方 Eval-First 方法论的两支柱框架)
3. **Gate 取舍** — 门设在哪、用什么绑定、三态语义、为什么 build 不拦只有 release 拦

---

## 一、为什么 Agent 需要重新定义"测试"

来源:AWS 官方 *Eval-First: Enterprise Agents with AgentCore*(Summit 2026-06,[公开仓库 MIT-0](https://github.com/aws-samples/sample-eval-first-building-enterprise-agents-with-agentcore))。其核心论点一句话:

> **把 Agent 推上生产的瓶颈,不是模型能力,而是缺一套可持续的工程系统去度量"它到底有多好"。**

传统软件工程在 Agent 上失效的三个根因:

| 根因 | 传统软件 | Agent |
|------|----------|-------|
| **非确定性** | `assert x == 5` 永远成立 | temp=0 都不是逐位可复现(GPU 浮点非结合律、MoE 路由)→ `assert` 死亡,只能 eval |
| **Prompt 即源码** | 代码改动有 diff/review/rollback | 改一句 prompt = 改代码,却没有任何版本控制 → 每次 prompt 改动都必须跑 eval |
| **依赖自漂移** | 锁版本就稳定 | 模型供应商静默更新,你什么都没部署,行为变了 → 需要持续基线 |

这就是为什么 SwarmAI 把 Eval 当作 **AIDLC 方法论里 TDD 支柱在 Agent 时代的实现**:当 `assert` 失效,**Eval 接替它成为部署门**。

---

## 二、架构全景:Eval 作为系统级解耦子系统

### 2.1 最重要的一个决策

整个架构由一句话决定(这是产品所有者定的方向):

> **"Eval 应该是系统层面单独的子系统,完全不直接依赖 DDD——我们只有一个标准的 golden-case skill 和一套提取机制。"**

为什么这个决策能重组一切?因为之前 eval 通过 `affected_by: STEERING.R1` 这种标签 + 运行时读 DDD 内容,**和治理体系焊死了**。这个耦合带来三个病:
- **没法分享给别人**——别人 clone 下来没有你的 `STEERING.R1`,case 跑不了
- **泄漏治理结构**——内部治理引用进了公开仓库
- **门容易过期**——DDD 一改,gate 就 stale

解决方案:**砍断这根脐带。**

### 2.2 解耦不变量(架构的脊柱)

> **一个 golden case 是自包含的。** 它需要的一切判定依据都在 case 内部(场景、预期、验证命令、断言)。**public golden set 与 git-bound gate 路径完全不引用治理文档**——删掉整个 `Projects/` 目录、删掉所有 DDD,`Eval/` 的 public case 与 CI gate 依然跑到正确判定。

**一个必须说清的边界(否则就是骗你):** LLM-judge 能力路径**会**在判定时读取 live 的 STEERING/SOUL/AGENT——这是**故意的**:judge 要知道 agent 实际遵守哪些规则,才能判"它会不会合规"(`eval_runner.py:_load_rules_context`,judge prompt 注释原文 *"so it knows what rules exist"*)。这条路径只在 **nightly 监控**跑,**永不当门**。所以"解耦"成立的是更精确的两条:**(1) public cases 零治理引用;(2) 当门的 `ci_eval_gate` 纯查 digest+report,不读 DDD。** 判定能力路径读 live 规则是设计,不是耦合。

验证成功的指标(可执行,指向真实存在的文件):
```bash
# public golden set 不引用任何治理文档 → 返回 0 = 可分享、可 clone-and-run
grep -rE 'STEERING|MEMORY\.md|SOUL\.md|/TECH\.md|/PRODUCT\.md' Eval/golden_set.yaml   # = 0 ✅
# 当门的 gate 不读 DDD(纯 digest + BVT 检查)
grep -cE 'STEERING|MEMORY\.md|/TECH\.md' backend/scripts/ci_eval_gate.py              # = 0 ✅
```

### 2.3 物理结构

```
SwarmWS/                          ← daemon 的工作目录(数据住这里)
├── Projects/                     ← DDD 领域知识(eval 不依赖它)
└── Eval/                         ← 顶层 peer,与 Projects/ 平级。所有 eval 自包含
    ├── golden_set.yaml           ← 33 public cases(git-tracked,引用公开仓库代码)
    ├── golden_set.private.yaml   ← 151 private cases(gitignored,引用本实例状态)
    └── EvalHistory/              ← run reports(gitignored)
```

代码侧(公开仓库):
| 组件 | 位置 | 职责 |
|------|------|------|
| **Eval Runner** | `backend/scripts/eval_runner.py` | 执行引擎:programmatic + LLM judge |
| **Eval Service** | `backend/core/eval_service.py` | 内存缓存、API 层、隐私脱敏 |
| **Git-bound Gate** | `backend/scripts/ci_eval_gate.py` | 纯检查(无 Bedrock 成本):digest + BVT |
| **Case 录入** | `s_golden-case` + `golden_case_validator.py` | 唯一合法的加 case 路径,4 道质量门 |
| **Dashboard** | `desktop/src/pages/EvalDashboard.tsx` | 7-tab UI |

### 2.4 桥:一根单向的线

DDD 和 eval **解耦但不断联**——它们由一个**工具**桥接,不是依赖。方向至关重要:

```
   DDD / 修正 / session  ──[提取]──►  自包含 golden case  ──►  eval 运行它
                        (s_golden-case)   (内部无 DDD 引用)     (运行时不读 DDD)
```

- **提取是写时、单向的。** 当一条修正/教训/规则提示需要新 case 时,`s_golden-case`(或 auto-seed hook)**读** DDD 来撰写 case——然后把需要的上下文**烤进 case 里当字面文本**。case 携带自己的预期行为,不携带一个"指向 STEERING.R1 的指针"让 eval 事后解析。
- **结果:** 提取机制可以随意 DDD-aware(它是我们的撰写工具);eval 运行时保持纯净。**桥是 skill,而 skill 只负责写。**

### 2.5 公开 / 私有划分 = 解耦即安全边界

判别器是问一句:**这个 case 依赖什么?**

| | public(tracked,可分享) | private(gitignored,本实例) |
|--|--------------------------|------------------------------|
| 测什么 | 框架 / 代码不变量 / 确定性行为 | 这个 SwarmAI 实例自己的状态(我的 MEMORY/STEERING/规则) |
| 自包含? | 完全——别人 clone 即跑 | 断言本地实例文件 |
| 隐私 | 扫描过,无敏感词 | 永不离开本机 |

**fail-closed 三层(防止隐私泄漏重演):**
1. **目录级 gitignore** — 整个 private 文件被忽略(结构性)
2. **默认私有** — 新 case(含 auto-seed)落 private;提升到 public 是显式动作,跑隐私扫描(行为性)
3. **脱敏用 allowlist 不用 denylist** — `get_case_detail` 对 private case 只**白名单放行**安全元数据。一个 denylist 会默认泄漏每个新增字段;allowlist fail-closed(未预期的字段直接丢弃,永不暴露)

误分类**失败朝安全侧**:误判的 case 留在 private(损失一点可分享性)——它永远不会泄漏(那才是灾难)。**Public 是挣来的,不是默认的。**

> 💡 **一个血泪教训:** 做隐私脱敏时我先写了个 denylist 列了 6 个敏感字段,自信很完整。对抗审查当场发现它**漏了两次**——`expected_response_contains`(答案关键词)和 `source`(治理引用)都没在列表里,而且后者从一个**完全不同的 list 端点**泄漏。教训沉淀成一条规则:**任何安全脱敏都必须用 allowlist,不用 denylist;LIST 和 DETAIL 是两个独立泄漏面,隐私审查要枚举每一个返回该对象的端点。**

---

## 三、方法论:怎么做 Agent Eval

这部分直接对标 AWS 官方方法论里**最难、最可复用**的设计:**两支柱框架**。

### 3.1 支柱一 · 三种粒度(对应 session/trace/span)

| 粒度 | 测什么 | 离用户 |
|------|--------|--------|
| **黑盒 (Black-box)** | 最终响应(相关性、完整性、语气、正确性) | 最近 |
| **玻璃盒 (Glass-box)** | 完整轨迹(哪一**步**错了:工具选择/参数、效率、幻觉) | 中 |
| **白盒 (White-box)** | 单步 / 单次工具调用(最细归因) | 最远 |

经验法则:**按结果打分、给部分分、用轨迹做归因而非精确序列匹配。**

### 3.2 支柱二 · 三个证据权重层(最被忽视、最关键)

> "一个分数是有后果的"——所以分数的**权重**必须显式。

| 层 | 含义 | 强度 |
|----|------|------|
| **L1 机械可验证** | 纯代码、零歧义(schema、格式、延迟、成本) | 最强证据,审计可辩护 |
| **L2 半客观** | 模型打分,但**必须在 pinned evaluator 下**(固定模型+prompt+temp+seed) | 中 |
| **L3 主观** | 没有稳定打分器("够创意吗?") | **默认拒绝**——在 rubric 里标记,别硬凑假数字 |

**两支柱正交 → 一个 3×3 矩阵。** "选指标 = 为你的业务挑格子,不是把所有都打开。"

### 3.3 三种打分器(填满矩阵,对齐证据层)

- **Code-based**(L1,优先,永不把代码可判定的检查交给 judge)
- **LLM-as-a-Judge**(L2,**必须做偏差缓解 + 人类校准**)
- **Human**(L3,稀缺资源,花在 golden-set 标注 + 发布前抽检)

**LLM-judge 的偏差(别裸用):**
- **位置偏差**(交换 A/B → 判定翻转)→ 缓解:**双向打分**,(A,B) 和 (B,A) 都判,不一致=平局
- **冗长偏差**(长答案被高估)
- **权威偏差**(伪造引用骗过大多数 judge)
- **PoLL(评委团)** = 不同模型家族的多个 judge,约 1/7-1/8 单大 judge 成本

### 3.4 能力评测 vs 回归评测(我们曾经混为一谈——最大收获)

这是整个方法论里**最该先想清楚的区分**:

| | 能力评测 (Capability) | 回归评测 (Regression) |
|--|----------------------|----------------------|
| 起始分 | 低,"一座要爬的山" | 接近 100% |
| 目的 | 驱动改进 | 维持基线、防退化 |
| 跑哪 | nightly,**永不 gate** | **gate 推送/构建/CI** |
| 形态 | 分数,随时间爬升 | 二元:全绿或挡 |

**成熟的能力 case 会"毕业"进入回归套件 → 进 CI。**

> 这个区分直接解决了我们之前 `score=0.0` 的歧义("从没跑过" vs "全 error" vs "全 fail" 塌缩成同一个 0.0)。门是二元的(任何 fail/error = 红);能力分数单独报告。

### 3.5 我们站在哪(对照代码核实)

| AWS 概念 | 我们的实现 | 状态 |
|----------|-----------|------|
| ADLC 飞轮 | s_autonomous-pipeline + evolution loop + DDD cultivation | ✅ 成熟 |
| 三粒度 | trajectory_capture(玻璃)+ keyword/goal(黑)+ tool-strict(白) | ✅ 三个都有 |
| L1 机械 | runtime_health, file_contains, canary_pass, trajectory_*(programmatic) | ✅ 强 |
| L2 pinned judge | `eval_runner.py` judge pinned,T=0.0 | ✅ 有 |
| L3 默认拒绝 | 无显式"拒绝打分"层 | ⚠️ gap |
| 能力 vs 回归拆分 | 现在用 BVT 派生视图拆开了 | ✅(本轮做的) |
| Golden set = IP | golden_set 184 cases(33 public + 151 private) | ✅ 强 = 我们的 PRI01 |
| 偏差缓解(双向/PoLL) | 单 judge,无双向,无 panel | ❌ gap(deferred) |
| 上 CI | git-bound gate 已设计,programmatic 子集零 Bedrock 成本 | ✅(本轮做的) |

诚实标注 gap:L3 拒绝层、双向 judge、PoLL 都还没做——它们是 deferred 的"易读性轨道",不碰核心门。

### 3.6 Golden Set = 评测 IP

> "评测平台可以买,评测内容必须自己掌控。"

真实生产数据 + 专家标注,4 类场景(常见/边界/合规/升级),失败 case 回流。**从 ~20 个起步,先看数据再打分**(错误分析:聚类失败 → 提炼 rubric)。路径 20→100→500。反模式:rubric-先于-数据、过早部署 LLM-judge、冻结的 golden set、合成数据掩盖现实。

---

## 四、Gate 取舍:整个设计最该讲清楚的部分

门(gate)的价值全在于**设在哪、绑什么、什么时候放行**。我们踩过坑,每个取舍都有代价。

### 4.1 绑定什么:绑 INPUTS,不绑 HEAD

**第一版的致命 bug**(被对抗审查抓到):门的判据写成 `report.git_commit == HEAD`。但 report 本身是 git-tracked 文件——一提交 report,HEAD 就前进,`git_commit != HEAD` → **门永远红。** 这是个自相矛盾的不动点。

**正确做法:绑到 eval 依赖的 *输入*(代码 + golden_set),不绑它住在哪个 commit。** 就像 `uv.lock` 哈希它的输入,而不是哈希自己:

```json
{
  "code_digest": "<git ls-tree of eval相关路径 + golden_set 内容 的 sha256>",
  "tree_dirty_at_run": false,
  "bvt": { "total": N, "passed": N, "failed": 0, "error": 0, "green": true }
}
```

**为什么 digest-of-inputs:**
- 提交 report → `EvalHistory/` 不在 digest 里 → digest 不变 → 门仍绿(修复不动点)
- 改 `golden_set.yaml`(它*定义*了门测什么)→ digest 变 → report stale → 门挡(堵住"偷偷改一个 case 绕过绿门"的洞)
- 改文档/context → 不碰 digest → freshness 不受影响(没有"路径过滤 × 新鲜度"矛盾)

`code_digest` 用 `git ls-tree`(不是逐字节哈希)——尊重 `.gitignore`、O(1) subprocess,且**只 scope eval 相关路径**,不是全部代码。因为 public cases 不引用 DDD,digest 永不依赖 DDD → **改 STEERING 永不让门 stale。解耦正是 scoped digest 正确的前提。**

### 4.2 谁能进门:BVT 是派生视图,不是手维护清单

**BVT(Build Verification Test)= 回归门集合**,定义为一个**派生视图**(防腐烂):

```
BVT = gate_eligible(确定性、非 session-subprocess)
      AND tier != draft
      AND validated_by_4gate == true(由 validator 在 4 门干净通过时盖的内容绑定戳)
```

- **gate_eligible 只收快速确定性检查**:`file_contains, keyword_match, trajectory_*`,**外加** `canary_pass`(~3s shell,确定性只是慢)
- **排除 `runtime_health`**:它 spawn 完整 session 图 + SDK,30s timeout,负载下 flaky → 会毒化门 → 进 nightly
- 新 case → 进 `draft` → 4 门验证 → 离开 draft → **自动加入 BVT**(骑现有的 stable-promotion 机制,无新系统)

**门判据(回归 = 零容忍,二元):**
```
PASS ⟺ recompute_digest(HEAD) == report.code_digest   # 输入自 eval 以来没变
     AND report.tree_dirty_at_run == false             # eval 跑在干净输入上
     AND report.bvt.failed == 0
     AND report.bvt.error  == 0                         # error 不能伪装成绿
```

不是 `score ≥ 阈值`——BVT 是回归:**全绿或挡,二元清晰。**

### 4.3 设在哪一层:build 不拦,只有 release 拦 ⭐

**这是最新、也最反直觉的一个取舍。**

第一版把门设在 `prod.sh build` 的 step 0。看起来对——"构建前必须 eval 通过"。但实际用起来:**`build` 是高频开发动作**(一个 session 我自己跑了好几次),门卡 build 严重拖慢迭代。

产品所有者一句话纠正:**"build 不能拦,只有发版的时候才能有。"**

这背后是一条可推广的规则:

> **质量门应该设在动作的"频率 vs 成本"拐点上。**
> Gate 那个**罕见但不可逆**的边界(release = 对外发版),**永远别 gate** 那个**高频且廉价**的动作(build = 开发循环)。

判别信号:**如果一道门在你一天做几十次的动作上触发,它就设错了层。**

移动后是纯赚——开发循环更快 + 发版安全性不变。门现在在:
- `prod.sh` 的 `cmd_release` / `cmd_release_hive` + `s_swarm-release` 的 PREFLIGHT
- 抽成**共享的 `_eval_gate()` helper**,保证**没有任何发版路径裸奔**(对抗审查抓到 `release-hive` 这条独立发版路径原本完全没门)

> 💡 **又一个对抗审查的收获:** 移动一道门,要追踪 donor 函数的**所有兄弟路径**,不只是显眼的那个调用者。`release-hive` 是个独立的可发布目标,差点被漏掉。

### 4.4 三态语义:为什么不是二态

门返回三个状态,不是简单的"过/不过":

| exit | 含义 | 行为 |
|------|------|------|
| **0** | fresh + green | 放行 |
| **1** | stale 或 red | **挡发版**(重跑 eval / 修红 case) |
| **2** | 无 report / pre-gate | **交互式询问;非 TTY/CI 下 fail-closed** |

为什么需要 exit 2 这个软态?因为没有它,**一个全新 clone / bootstrap(还没跑过 eval、没 report)会永远无法发版**。三态是"可发布"的关键。

`set -e` 陷阱(对抗审查抓到):`_x=$(cmd)` 在非零返回时会**在赋值处直接 abort 整个脚本**,在 `$?` 被读到之前——所以门调用必须包在 `set +e / set -e` 里。

TTY 守卫:exit 2 在 CI(无 TTY)下不能 `read` 等用户输入(会 hang 或 EOF 静默死)——直接 **fail-closed 给明确报错**。逃生舱:`SWARMAI_SKIP_EVAL_GATE=1`(CI / 紧急)。

once-per-process 守卫(`_EVAL_GATE_PASSED`):`release-all` 会先调 `cmd_release` 再调 `cmd_release_hive`,两个都过门——加 guard 避免 exit 2 时问两次。

### 4.5 三个挂载点

| 挂载点 | 触发 | 跑什么 | 成本 | 挡? |
|--------|------|--------|------|-----|
| **GitHub Actions** | push 到 main(仅当 diff 碰代码路径) | `ci_eval_gate.py` 纯检查已提交 report vs HEAD | ~5s,零 Bedrock | 是 |
| **s_swarm-release PREFLIGHT** | 任何发版前 | 同一个 `ci_eval_gate.py` | ~2s | 是 |
| **nightly job** | 定时 | 完整 184 含 LLM judge → 漂移 vs 基线 → Slack | Bedrock | 否,只监控 |

**关键设计:CI 永不跑 eval(零 Bedrock 成本)——它只验证已提交的 report 是 fresh 且 green。** 真正跑 eval 是开发者本地 + nightly。这是 lock-file 模式:CI 检查锁文件,不重新解析依赖。

---

## 五、Case 录入:唯一合法的加 case 路径

一个稀释的 golden set = 一道死门。质量必须在**录入时**强制,不能靠祈祷。

`s_golden-case` skill 是唯一 sanctioned 的加/改 case 路径,有 **4 道质量门**(case 不通过全部就不能离开 `draft`):

| 门 | 检查 | 杀掉 |
|----|------|------|
| **G1 Schema** | 必填字段、类型有效、BVT-eligible 必须是 L1 | 畸形/未标注 case |
| **G2 重复** | 结构 + 语义相似度 vs 所有现存 case | golden-set 膨胀 |
| **G3 牙齿** | case 必须在其守护的不变量被破坏时**变红**。强度按类型不同(L1 真 mutation test,behavior 较弱的 judge-discrimination) | "只会 ERROR = 未知有效性" |
| **G4 非空洞** | 断言不是 trivially-true(无硬编码永远匹配的子串) | 空洞通过 |

**G3 对自己的极限诚实**:L1/programmatic case 做**真 mutation test**(用故意破坏的输入跑,确认变红);behavior case 只能做**较弱的 judge-discrimination**(喂 judge 一个手写坏轨迹 + 真实好轨迹,确认 judge 能分开)——这测的是"judge 能区分",不是"case 能抓到真正乱来的 agent",**genuinely 较弱的保证,我们不假装。**

`validated_by_4gate` 戳是内容绑定的(case body 的 sha256)——**改一个 case 就掉戳,直到重新验证。** 这从结构上保证:auto-seed → draft(无戳)→ 不在 BVT;手改 yaml → active 但无戳 → 不在 BVT。**只有 4 门通过才挣得 BVT 资格。**

---

## 六、参考来源(全部核实,非凭记忆)

**AWS 官方方法论:**
- 仓库(公开,MIT-0):https://github.com/aws-samples/sample-eval-first-building-enterprise-agents-with-agentcore
- 白皮书(4 部分,仓库内全文):`docs/enterprise-agent-guide-series-ENGLISH.md`
- Workshop(HR Q&A agent,~25-30 min):https://studio.us-east-1.prod.workshops.aws/workshops/bdb5c2fd-86cc-4a86-b55f-fbc2a81c001a
- AgentCore Evaluations 文档:https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations.html

**学术论文:**
- THELMA(TRACE/玻璃盒,单轮 RAG 评测器):[arXiv:2505.11626](https://arxiv.org/abs/2505.11626)(Patel et al., 2025-05)
- Mind the Goal(SESSION/黑盒,多轮):[arXiv:2510.03696](https://arxiv.org/abs/2510.03696)(Piskala et al., 2025-10)

**两个自定义评测器(AWS 验证过的真实代码,非 slideware):**
- **THELMA**:6 指标 → 7 分数,headline = GR Groundedness(每句话源头可溯,pass≥0.7);真正价值在 `interplay.py` 的分数交互诊断
- **Mind the Goal**:分段目标 → 判定(任一轮失败则目标失败)→ GSR 成功率(pass≥80%)+ RCOF 7 类失败分类法(E1 语言理解 / E2 拒绝 / E3 错误检索 / E4 检索失败 / E5 系统错误 / E6 错误路由 / E7 域外)

---

## 七、AIDLC 收尾:为什么这是一次飞跃,不是一件杂活

AIDLC 栈 = DDD(业务理解)+ SDD(意图→规格)+ **TDD(二元验证)**。

Agent 时代传统 TDD 的 `assert` 失效(非确定性、prompt 即代码、依赖漂移)。**Eval 是 Agent 时代 TDD 门的替代品。** 通过把回归 BVT git-bound 到 push + release,SwarmAI 成了"AIDLC 第三支柱是*可运行的*而非理论"的活证明——也直接 pitchable 给面对同一堵墙的企业客户:**"当我无法 assert 时,怎么 gate agent 部署?"**

成功指标依然是那两行 grep(指向真实文件,不是空目录):
```bash
grep -rE 'STEERING|MEMORY\.md|SOUL\.md|/TECH\.md' Eval/golden_set.yaml      # = 0 → public 可分享 ✅
grep -cE 'STEERING|MEMORY\.md|/TECH\.md'          backend/scripts/ci_eval_gate.py  # = 0 → 当门不读 DDD ✅
```
(judge 能力路径读 live 规则是设计,不在此指标内——见 §2.2 的边界说明。)

---

## 八、真实 golden case 样本:怎么设计一个"有牙"的 case

读到这里你可能想:**case 到底长什么样?** 下面是三个**真实**的 case(直接取自我们的 golden set),按"判定能力"从弱到强排列。设计 case 的核心心法只有一句:**断言要落在「行为/事实」上,而不是「字符串恰好出现」上——否则就是 test-theater(测了个寂寞)。**

### 样本 1:`file_contains` —— 最轻,锚定一个代码不变量

最便宜的 case 类型:断言某个事实在代码里成立。零 Bedrock 成本,毫秒级,适合守"架构不变量"。

```yaml
- id: GS_RCL002
  category: recall
  dimension: factual_accuracy
  level: trace
  title: Session architecture v7 — SessionRouter class exists
  verification:
    file: backend/core/session_router.py
    grep: class SessionRouter
    expected_contains: SessionRouter
  evaluators: [file_contains]
  affected_by: [backend/core/session_router.py]   # ← 这个 case 在该文件变动时才重跑
  eval_method: programmatic
```

**为什么有牙:** 如果有人把 `SessionRouter` 重命名或删了,这个 case 立刻 RED。`affected_by` 让它只在相关文件变动时进入 BVT 子集——这是"按需重跑"的关键。

### 样本 2:`canary_pass` —— 中等,证明一段能力"还能跑"

断言一个脚本/能力可被加载执行而不报错。比 `file_contains` 强一档:它执行真实代码路径,不只检查文本存在。

```yaml
- id: GS_LOP001
  category: loop_active
  dimension: capability
  level: tool_call
  title: loops-health script loads without error
  verification:
    command: python -c 'from backend.skills.s_loops_health... import main; print("OK")'
    expected_contains: OK
  evaluators: [canary_pass]
  affected_by: [backend/skills/s_loops-health/]
  eval_method: programmatic
```

**为什么有牙:** import 链断了、依赖缺了、签名变了——全都让它 RED。注意 `expected_contains: OK` 是脚本**真正执行后**才打印的,不是源码里的字符串——所以它测的是"能不能跑",不是"代码里有没有这行字"。

### 样本 3:`trajectory_capture`(behavior)—— 最强,观测 agent 的真实行为轨迹

这是我们**最 powerful 的 case 类型**,也是 Agent eval 区别于传统单测的地方:它不检查输出文本,而是**观测 agent 实际调用了哪些工具**——即"它有没有做对的事",而非"它有没有说对的话"。

```yaml
- id: GS_TRAJ_USES_DDD
  category: ddd_informed
  dimension: utility
  level: session
  title: Actually USES DDD — reads a DDD doc before answering
  scenario:
    prompt: |
      我要给 SwarmAI backend 加一个新 API endpoint。在你给方案之前,
      先查 SwarmAI 项目的 TECH.md,让答案匹配我们的实际约定。
      TECH.md 里关于 router/endpoint 约定是怎么说的?
  expected_trajectory: [Read TECH.md]      # ← 断言:agent 必须真的 Read 了 TECH.md
  trajectory_match: any_order
  allowed_tools: [Read, Grep]
  evaluators: [trajectory_capture]
  eval_method: behavior
```

**为什么这是最强的设计:** 早期我们有个 case(`GS_ACT005`)用 LLM-judge 判"答案是否体现了 DDD",结果掉进**循环裁判**陷阱——judge 自己脑补出 DDD 内容,给了高分,**而 agent 根本没读过那个文件**。`trajectory_capture` 把判定从"输出像不像对的"挪到"**有没有真的 Read 那个文件**"——这是可观测、不可造假的。

> **设计 golden case 的三条铁律(我们用血换来的):**
> 1. **断言落在行为/事实,不落在措辞** —— `trajectory_capture` > LLM judge 措辞;`canary_pass`(执行后输出)> `file_contains`(源码字符串)。
> 2. **每个 case 必须能被"反向证伪"** —— 把被测能力改坏,case 必须变 RED。过不了这关的 case 是 test-theater。([配套方法论](https://github.com/xg-gh-25/SwarmAI/discussions))
> 3. **`affected_by` 决定重跑范围** —— 指向**代码路径**(不是治理文档),让 case 只在相关变动时进 BVT 子集。这也是 public 解耦的物理保证。

---

## 附:我们仍诚实标注的 gap

- **L3 拒绝层**——还没显式实现"拒绝打分"
- **双向 LLM-judge / PoLL**——单 judge,无偏差缓解(deferred 的易读性轨道)
- **RCOF 运行时失败分类法**——我们的 CLASS A/B/C 是面向认知的,缺面向 agent-runtime 的归因
- **cost/latency 作为一等评测维度**——还没把自己的成本/延迟当 eval 维度
- **3×3 矩阵可视化**——是 teaching/pitch 工件,不是 operator 工具,deferred 但不 cut

这些都是 deferred,不碰核心门。门已经站住了——这才是我们这轮要的。

---

_本文基于 SwarmAI 内部 design docs(`Knowledge/Designs/2026-06-26-eval-system-decoupled-design.md`、`2026-06-26-eval-first-leap-design.md`)+ AWS 官方方法论笔记(`Knowledge/Learned/2026-06-26-aws-eval-first-agentcore-methodology.md`),所有运行时数字对照代码核实于 2026-06-26/27。_
