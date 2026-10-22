---
title: Pipeline v1 cycle 1 复核修复规范
created: 2026-10-22
updated: 2026-10-22
project: SwarmAI
status: DONE — 三轮独立复核完成；最终 8 项已修复；rebase eb9331b9 后 334 tests green；等待 XG cycle-1 review
related: pipeline-v1-plan.md；pipeline-v1-deviations.md；v13 FINAL §A、§D4
---
# Cycle 1 复核修复规范

## 1. 根因

第一版把“纯函数”误解为“接收调用者传来的布尔事实”：`stage_checks_passed`、`authorized`、`touches_finding`、`old/new` 等都能由任意 dict 伪造。测试只证明这些 dict 会得到预期分支，没有证明 v13 的事件协议。

正确边界：**事件写入者产出 typed event；事件模块按 kind 校验 producer、ckey、payload 和发生键；projection 只折叠 validated events。** lifecycle、checks、findings 不再接受能改变判定的裸布尔自报。cycle 2/4 接入真实 hook/CLI/runner 和封印；cycle 1 先把可接受的 typed event 形状冻结。

## 2. 四个修复 cluster

### C1 Typed event + projection authority

覆盖 review findings：event per-kind contract、run_create 缺失、lifecycle 伪造 landing/attempt/RF、finding 伪造 proof/auth/canonical ID、已损坏 chain 仍能 append/replay。

- `events.py`：每个 kind 一张 `EventSpec(producer, occurrence_fields, required_ckey, payload_schema)`；增加 run_create、stage_check_failed、land、run_land、proof、auth_consumed 等 cycle-1 需要的全部 kind。`make_event` 返回 validated envelope；append 前先 verify 现有 chain，再校验新 event。
- `lifecycle.py`：对 event stream 做 `fold(events)`；attempt 从已有 `stage_attempt` 数量推出；RF 例外从 failed check IDs 的非空子集 `RF-1..4` 推出；landing 只来自 land / run_land / not-landed 路径；严格按 `(status, landing, stage, profile, event)` 表转移。
- `findings.py`：canonical ID 从 validated `finding_occ` 事件推出；关闭只折叠 runner `proof`、R2 `finding_status`、CLI `auth_consumed`；不再接受 `authorized=True` 或 proof outcome 字面量的公开入口。

### C2 Real check evaluators + schema cross-checks

覆盖 review findings：placeholder `{passed, detail}`、coverage 只看 profile、DS-9 不调用 probe、test refs/path traversal、DS-1/DS-4/RQ-8/DS-6/security_boundaries 缺失。

- `checks.py` 每条检查注册真实 evaluator；registry coverage 枚举 `profile × check × stage × variant`，条件豁免显式有 reason。
- `schemas.py` 提供纯 evaluator：全局 ID/refs、repo-relative file/nodeid、DS-1 需求→task+exit、DS-4 unchanged→regression test、RQ-8 executable acceptance→test/probe exit、DS-6 flow failure branch/states 生命周期。
- DS-9a 直接调用 `probe.validate`；full DESIGN_SPEC 强制 `security_boundaries`。

### C3 Resource identity and confinement

覆盖 review findings：`run_id=".."` traversal、reserved dir 非 REPORT 文件被隐藏、probe 无 cwd、混合 pathspec 扩成 glob。

- run ID 统一正则 `^run_[A-Za-z0-9_-]+$`。
- tree writer 前先调用 injected `list_reserved(root, reserved_dir)`；非空立即拒绝。delivery overlay 之后 LG-3 再验证唯一 REPORT。
- probe execution spec 必须带绝对 repo root `cwd`；pathspec 含 `?[]` 一律拒绝，只允许 `*` 触发 glob，其他 path 在默认 pathspec 语义下不含元字符。

### C4 Authorization and exact booleans

覆盖 review findings：字符串 `"false"` 被当 true、run_land 邻近状态非法、completed(not_landed) 不计 human decision。

- `AuthorizationOracle.consume` 返回不可由 JSON 构造的 `Consumption` 值对象；`auth_consumed` event 由 CLI producer 引用 request/action/binding digest。lifecycle/findings 只读这个 event，不读 `authorized`。
- 所有结构 flag 必须 `is True` / `is False`，不使用 truthiness。
- run_land 只允许 active/paused/awaiting_human/abandoned/reflecting(not_landed)/completed(not_landed)，且每次成功计 human decision；reflecting(landed)、completed(landed) 禁止。

## 3. 测试

每个复核 counterexample 先成为失败测试；再改实现。完成标准：

- 原 325 个测试经语义修订后全部通过，不保留 blessing reduced semantics 的断言。
- §D4 每条不变量有至少一个 negative test；邻近非法 lifecycle 状态表参数化覆盖。
- 新增：wrong producer/ckey/payload、corrupt-chain append/replay、interpreter/auth truthy string、run-id traversal、reserved-dir foreign file、unsafe probe in DESIGN_SPEC、probe cwd/mixed meta path、fake proof/auth event。
- 重新做两路独立复核；任何 HIGH 未关闭，不进入 cycle 2。

## 4. 第二轮复核与修复

第二轮设计 + 安全复核确认第一轮 10 类已关闭，剩余/新增问题归为四组并一次修复：

- **Authority**：producer 不再是字符串标签。`EventAuthority` 给每类 writer capability，事件带 authority ID + HMAC；projection 必须拿同一 authority 验证。另一个 authority 即使签出 `producer=runner` 也无效。`Consumption` 必须在签发 oracle 的 capability 注册表中存在，公开构造对象不能转 auth event。
- **Cross-event correlation**：stage attempt 的 check IDs 必须等于 registry roster；finding_occ 核对真实 parsed answer + expert/round；R2 status 核对该 expert 的 parsed R2 answer；test/probe proof 必须引用同 tree 的 passing test_call，并带完整旧/新结果；accept_finding 从 canonical ID + current state hash 重算 digest，不能把 A 的授权绑给 B。
- **Landing/final**：normal land 固定顺序 LG1/2/5 → land_intent → REPORT render → LG3/4/DV1，同一 attempt；human land 与 skip-tests 分别绑定 LG1–5 / LG3–4；TS/LG 要求 exact nonempty repo set 和每 repo base validation manifest；QX/MD 缺 provenance 时 fail closed；Gate-0 handout 无 parsed answer 不再 vacuous pass。
- **Confinement/schema**：统一 run ID；probe cwd 必须在 allowed run repos，PATH 固定，HOME 在 trusted temp root；DS-2a 查 file exists/planned creation；DS-4b 查 unchanged regression collection；DS-9b 包括 probe exits。

当前 targeted suite：326 tests pass。进入第三轮（最终）独立复核；按三轮上限，本轮只接受未关闭的具体协议缺陷，不再扩展设计范围。

## 5. 第三轮最终复核与收口

最终轮报告 4 HIGH + 4 MEDIUM，按根因一次修复，不再开启第四轮：

- `auth_consumed` 只能由 `AuthorizationOracle.consume()` 返回的 `Consumption` 创建；事件 authority 对完整消费绑定附加独立域 HMAC receipt，普通已签名 dict 也不能冒充 oracle 消费。
- break test/probe proof 绑定具体 target、旧/新 code tree 和 runner call event IDs；test 固定为旧树两次 `assert_fail` + 新树一次 `pass`，probe 固定为旧不满足 + 新满足；lint/build/typecheck 不能充当 proof。
- `run_create.payload.run_id` 必须等于 envelope run ID；normal/human land 都要求 40-hex landed commit；LG-4 对 malformed repo row fail closed。
- R2 handout 明确携带 canonical finding roster；缺状态只重开该 roster，R2 新 finding 可直接用 proof/accept 关闭。
- DS-1 只覆盖 R.m；C/E/U 仍参与引用合法性与 DS-4；DS-6 改为可解析的独立 `failure-condition -> outcome` edge，不再按任意关键词猜。

全部 8 个 reviewer counterexample 已加入回归测试。当前 cycle 1 targeted suite：334 passed。
