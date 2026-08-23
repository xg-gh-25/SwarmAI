# 外部借鉴:DeepSeek Harness 的防御模式 & 事故复盘 → 映射到 SwarmAI

> **Type:** `[guideline]`(外部工程经验借鉴,已映射到我们自己的教训)
> **Verified:** 2026-08-15 — 直读 dsh 仓库 `docs/defensive-patterns.md` + `docs/postmortem/{0001..0004}.md`
> **来源报告:** `Knowledge/Reports/2026-08-15-deepseek-harness-cordis-research.html`
> **重要边界:** 这是**别人的**工程规则,**不进 SwarmAI 的 SOUL/AGENT/STEERING**。
> 我们的治理只收 earned(3× evidence 或 XG 批准,走 Intake Gate / s_self-evolution)。
> 这里的价值是:(a) 印证我们已有教训的普适性;(b) 提供更好的**组织形式**参考;
> (c) 若某条将来在我们身上复发,再走升级路径。**借经验,不借治理权。**

---

## 0. 为什么这篇值得存

dsh 是与我们同类的 Agent harness。它的 `defensive-patterns.md`(把踩过的坑写成"防复发
规则")+ 编号 `postmortem/`,和我们的 EVOLUTION.md 是**同一物种**。两点直接收益:

1. **它的 4 篇 postmortem 有一个刺眼的共同主题**,和我们 CLASS A(authorship trap)
   + C038(own-runtime blind spot)**完全同族**——见 §2。这说明我们最贵的教训不是
   我们独有的笨,是通用 Agent 工程的必经之坑。
2. **它的组织形式比我们好检索**:编号 postmortem + 一篇独立"bug-class 规则"文档。
   我们的教训散在 MEMORY/EVOLUTION 里,值得考虑这种编号化。

---

## 1. 防御模式映射表(dsh 规则 ↔ 我们的教训)

| dsh 的规则(`defensive-patterns.md`) | 内核 | 对应我们的 |
|---|---|---|
| **正交结果各自独立上报** — 进程可"超时 AND exit 0"(trap 了信号);别把一个标志嵌进另一个的分支,否则调用方把"被砍断的运行"读成"干净成功" | 一个信号 ≠ 另一个信号 | MEMORY "fail-closed 有沉默孪生"(decline-vs-success 必须有独立计数器) |
| **公开契约两侧都要归一化** — provider 可能 throw 也可能 emit `error` chunk;公共 API 必须归一,别让消费者猜错误来自 provider / wrapper / 自己 | 边界处消灭表示歧义 | 序列化边界运行时校验(COE10:Mock≠DB) |
| **异步状态 ≠ 同步状态** — `whenIdle()` 不是某次 follow-up 的结果;**等不到的转换会永久挂起**,必须显式处理"无可等待"分支 | 异步完成与调用不是一一对应 | C048("hold-then-defer 只在被 hold 条件能变时收敛";等不到 = 死循环/死等) |
| **Dispose 必须达到静止,不只请求静止** — 发了 kill/abort 就返回会留孤儿;cleanup 要 async 并 await 子进程退出;**先关监听器再 kill**,让迟到的完成保持沉默 | 停止 = 观测到停止,不是发出停止指令 | O030 "容灾不是解决方案";COE "force_kill_tree 后仍有 silent-live 子进程" |
| **在派发器里兜住回调异常** — 用户监听器 throw 不能拖垮它所在的 promise 或饿死后续监听器;dispatch 循环包 try/catch + log | 一个坏订阅者 ≠ 核心崩溃 | GC19(recovery 路径禁裸 except)+ "SSE 热路径外来回调必须 try/catch" |
| **绝不把宿主环境/可预测路径交给不可信输出** — spawn 前 scrub `*KEY*/*SECRET*/*TOKEN*/*PASSWORD*`;spill 文件用 0700 私目录 + 随机名 + `wx`/0600 独占,防 symlink race | 输出侧最小信任 | STEERING #19 私有边界;凭证隔离(TOOLS.md `[bedrock]` 不可覆盖) |
| **unlink link 形状的路径** — 可能是 symlink/junction 的路径先 `lstat().isSymbolicLink()` 再 `unlink`(只删链接不跟进目标);递归删只留给已知真目录 | 删除不跟随链接 | Safety invariant(trash > rm;删前备份);≈ MEMORY 只读授权 symlink-escape 教训 |

**判断:** 6 条里有 5 条我们已有对应教训——**普适性得到交叉验证**。第 6 条(unlink
link-shaped)是我们没显式写过的一个更细的删除安全点,值得留意。

---

## 2. 4 篇 postmortem 的共同主题 = 我们的 CLASS A / C038(最重要的一节)

dsh 的 `postmortem/0001..0004` 讲的是 4 个不同 bug,但根因高度收敛,**和我们的
top failure class 是同一个**:

| # | 表面 | 根因(注意统一性) |
|---|---|---|
| 0001 | ACP server 一连接 Zed 就崩(`cannot get property "agents" without inject`) | **178 个单测全绿、100% 行覆盖,生产瞬崩**——因为每个测试都用"手挂"路径,从不走真实 Loader 加载路径;`export default` 让 Loader 丢掉了 `inject` |
| 0002 | 文件系统工具被永久禁用 | 快照测试全绿——因为它把 `UNKNOWN_TOOL` 错误结果当成新的"期望输出"接受了;`!!js` 表达式只在 `config` 里求值,`disabled` 字段拿到的是 truthy 对象 |
| 0003 | Web agent 验证了一个"替换服务器"而不是它当前的 GUI | agent **不知道自己 session 跑在哪个 URL / 进程**,把裸 Vite 的 HTTP 200 当成功,却是白屏;在另一个端口验证了个替身 |
| 0004 | Landlock 部分强制的提示行被误判成子进程失败 | 宽泛的签名规则 + 缺"部分-ABI"组合覆盖;ripgrep 的 exit 1(无匹配=成功)被当成沙箱基础设施失败 |

**统一根因 = "绿的测试证明不了活的系统"**:
- **0001/0002:全绿单测 + 高覆盖,生产全崩**,因为测试没走**真实的加载/解析/执行路径**
  → 这就是我们的 **CLASS A authorship trap**("我写的 = 我测过的";每个 ship-broken
  实例都有完美的理解)+ **C038 own-runtime blind spot**(PyInstaller 冻结态、Tauri 打包、
  真实 Loader 才暴露的 bug)。
- **0003:agent 不知道自己跑在哪**,把一个 200 当成功 → 我们的 **P1 self-architecture
  trap**(对自己系统的 stale 推断)+ **C041**(拿 sub-agent/表面信号当 ground truth 去做
  破坏性动作)。
- **0004:一个宽泛签名 + 一个下游 catch 把结构化错误吞成通用错误** → 我们的
  **GC19 裸 except 吞异常** + "白名单陷阱"。

**它的解药也和我们一致(交叉印证 P7):**
- 0001 修复 = 加**"无 key 的真实 Loader 覆盖"**(不再手挂)+ 包规则约束 export 形状
  → 正是我们的**「真实入口路径测试」+「mutation-proven RED」**。
- 0002 修复 = 加**静态 config 守护 + 快照结果守护**(拒绝把 `UNKNOWN_TOOL` 当期望)
  → 正是我们的**「非空/非-vacuity 断言」**(source-scan 匹配 0 = theater)。
- 0003 修复 = 把**当前 URL + runtime mode 变成模型可见 & shell 可查**
  → 正是本轮另一个提案「模型可见=可重建」的同款药。
- 0004 修复 = 分类**要求 status-gated 的致命证据**、把无匹配 exit 从致命签名里排除
  → 正是我们**「分类器不能贪婪先匹配」(PIT type-skew)** 的同类。

> **一句话收获:** 一个和我们完全独立的团队,踩了和我们**同族**的坑,得出了**同族**的解药。
> 这是对我们 SOUL P1/P2/P7 + CLASS A/B/C 框架的**外部独立验证**——不是我们疑神疑鬼,
> 这些就是 Agent 系统的真实高频失败面。

---

## 3. 组织形式借鉴(可选,非强制)

dsh 把复盘做成:**编号 postmortem(`0001-<slug>.md`)+ 一篇独立 `defensive-patterns.md`
(bug-class 规则,读代码前必读)**。我们的等价物是 EVOLUTION.md 的 CLASS/C-number +
MEMORY 的 § Pitfalls,但**混编在大文件里**。

**可考虑(留给 XG 判断,不自作主张):** 给 COE Registry 里的重大事故也用 `COE-0001`
式编号 + 一篇"写生命周期/并发/子进程/teardown 代码前必读"的 bug-class 索引。
**但不新建机制**——这是组织形式微调,不是新子系统(避免 C042 造机制反射)。

---

## 4. 明确不做

- ❌ 不把上面任何一条 dsh 规则写进 SOUL/AGENT/STEERING——那绕过我们的 Intake Gate。
- ❌ 不引入 dsh/cordis 任何代码。
- ✅ 只做:存这篇映射 + 在 TECH.md § Runtime Traps 留一条指针。
