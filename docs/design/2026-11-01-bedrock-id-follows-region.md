---
created: 2026-11-01
updated: 2026-11-01
title: "Bedrock 模型 ID 跟着主区域走"
status: r1 — 实现中（XG 2026-11-01 定规则：eu-* 用 eu.，ap-* 用 apac.，其他用 global.）
related: docs/design/2026-11-01-settings-account-merge.md
---

# Bedrock 模型 ID 跟着主区域走

## 1. 问题

每个模型的 Bedrock ID 都是跨区 inference profile，写死成 `us.`（`model_registry.MODEL_REGISTRY`，
以及从它复制进 `config.json` 的 `bedrock_model_map`）。`us.` profile 只能从美国 region 调用。
Settings → Swarm AWS Account 的主区域下拉框却提供 eu-west-1、eu-central-1、ap-northeast-1、
ap-southeast-1：选了其中任何一个，聊天、后台任务、CDF 都会调用一个这个 region 调不了的 ID。
默认 region 是 us-east-1，所以作者的机器上看不到（P7）。

## 2. 规则（一处定义）

`model_registry.geo_for_region(region)`：

| 主区域 | 前缀 |
|---|---|
| `us-gov-*` | `us-gov.` |
| `us-*` | `us.` |
| `eu-*` | `eu.` |
| `ap-*` | `apac.` |
| 其他（ca-、sa-、me-、af-、il-、mx-…） | `global.` |

`model_registry.regional_id(model_id, region)` 只改**带地区前缀的 profile ID** 的前缀：

- `us.anthropic.x` / `eu.anthropic.x` / `apac.anthropic.x` … → `<geo_for_region>.anthropic.x`，后缀（`-v1`、`[1m]`、`:0`）原样保留；
- `global.anthropic.x` 不改：`global.` 在大多数 region 都能调用，是用户（或发现流程）有意选的；
- `anthropic.x`（基础模型 ID）、`arn:…`、短名、其他任何字符串原样返回；
- region 为空或不合法时原样返回（不猜）。

registry 里的 ID 继续写 `us.`：它们是"这个模型的 profile 名"，前缀在发出调用时换。不改存储的
`bedrock_model_map`，不做数据迁移（只保证新的调用正确）。

## 3. 在哪里换（模型 ID 离开本进程的地方）

| 出口 | 谁走它 | region 从哪来 |
|---|---|---|
| `config.get_bedrock_model_id` | 聊天（`prompt_builder`）、后台 CLI 任务（`jobs/model_resolve`）、`aws_account._bedrock_model_id`、`routers/system` 的凭证校验 | 主区域 `aws_account.region()` |
| `jobs.bedrock.converse_with_retry` | 每一次直接的模型调用（structured / text，CDF、summarization、eval judge…） | 这次调用 client 用的 region（传入的 `region`，否则 `_load_config` 的 region） |

CLI 的 `AWS_REGION` 由 `claude_environment` 设置。它今天读 `config["aws_region"]`，而模型 ID 读
`aws_account.region()`（多一个账号记录的 region 作为兜底，并校验格式）。两者不同时，ID 和调用
region 会对不上。所以 `claude_environment` 也改读 `aws_account.region()`：发出调用的 region 和
换前缀用的 region 是同一个值。

`jobs.bedrock.get_model_id` 的兜底 `f"us.anthropic.{key}"` 违反 registry 的"不要拼 ID"规则：改成
`resolve_bedrock_id(key) or key`，前缀在出口换。

## 4. 只认 `us.` / `global.` 的判断

这些地方把 `eu.` / `apac.` 开头的完整 ID 当成"不认识的短名"，会拒掉或改写它，统一改用
`model_registry.is_full_bedrock_id`（`arn:` 或任意地区前缀 + `anthropic.`）：

- `routers/settings.py` 的 judge 校验、`_short_name`；
- `jobs/model_resolve.py` 的完整 ID 判断；
- `scripts/eval_runner.py` 的 judge 解析；
- `prompt_builder` 判断 1M 窗口前剥前缀（改用 `normalize_short_name`）。

模型发现（`_list_bedrock_inference_profiles`）同一个短名有多个 profile 时，优先主区域的前缀，
其次 `global.`，不再固定优先 `us.`。

`usage_prices.FALLBACK` 只有 `us.` 的 sonnet 价格：没有学到价格的模型按短名找兜底价，
`eu.` / `apac.` 的同一模型也能估价（学到的价格仍按真实 ID 分开）。

## 5. 不做

- 不按账号调用 `ListInferenceProfiles` 去确认 profile 存在：`global.` 不是每个模型都有，没有时
  Bedrock 返回的错误照常显示给用户。以后可以加。
- 不改 Hive：Hive 是同一份代码，读它自己 config 的主区域，走同样的出口。

## 6. 验收

- `geo_for_region` / `regional_id` 的表驱动测试，覆盖上面每一行和不改的情况；
- `get_bedrock_model_id` 在 eu-west-1 / ap-northeast-1 / ca-central-1 / us-east-1 下的结果；
  `bedrock_model_map` 里存的 `us.` 值同样被换；
- `converse_with_retry` 发出的 `modelId` 跟着调用 region；
- `claude_environment` 设置的 `AWS_REGION` 等于 `aws_account.region()`；
- 判断完整 ID 的地方接受 `eu.anthropic.*`；
- 每个修复做变异检查：撤掉修复，对应测试变红。
