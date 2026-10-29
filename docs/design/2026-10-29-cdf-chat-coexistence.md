---
title: CDF 和 chat 共处 —— 不抢 chat 的资源，也不被 chat 饿死
created: 2026-10-29
updated: 2026-10-29
project: SwarmAI
status: 待确认
follows: 2026-10-29-cdf-post-launch.md §1（上线当天发现）
---

# CDF 和 chat 共处

## 0. 问题

CDF 在 daemon 进程里跑三条线程：调度、run lane、会话任务。为了不拖慢 chat，准入规则是：只要有任何一个 tab 在 streaming，所有要调模型的 lane（run lane 除外）都等着（`worker.ingestion_admission`，原设计 §6.3）。这条规则两头都出了问题：

- **CDF 被饿死**（上线当天实测）：4 个会话任务 16:00 到期，到 16:06 一个都没开始，因为期间一直有 tab 在 streaming。你多 tab 一直在用，白天 CDF 几乎没有机会跑。
- **chat 的变慢没有被这条门挡住**：run lane（`RunLaneExecutor._drain`）本来就不检查准入，streaming 时照样在做判官和解析。chat 自己的路径也在做同样的解析：MEMORY 注入选取（`memory_injection`）、召回（`recall_multi`、`knowledge_store`）、Read/Grep 之后的使用统计（`usage`），都调用 `parse_units`。

## 1. 根因：一个平方级的函数，不是"解析本来就慢"

`core/ingestion/units.py` 的 `_byte_offset` 把文件开头到当前位置的整段文本重新编码一遍，用来算字节偏移。每个单元算 3 次，一个约 1 MB 的文件因此是平方级。实测（线上 DDD 文件，2026-10-29）：

| | 现在 | 改成递增计算之后 |
|---|---|---|
| IMPROVEMENT.md（946K，863 条） | 999 ms，其中 822 ms 花在 `str.encode` | 23 ms，偏移和锚点完全一致 |
| TECH.md（785K，616 条） | 563 ms | 同一个原因 |
| 解析期间，同进程另一个线程的唤醒间隔 p99 | 35 ms | 解析本身已经短到可以忽略 |

而且 `card3.load_corpus` 每做一次检索，就把所有 DDD 文档和 `Learned/` 重新解析一遍，所以这 1.5 秒会在每个单元上重复。

结论：要修的是这个函数和重复解析。修好之后，chat 和 CDF 抢的那部分 CPU 基本就没有了，streaming 门也就没有存在的理由。

## 2. 方案对比

| | 做法 | 改动 | 风险 | 取舍 |
|---|---|---|---|---|
| A | **修根因**：字节偏移改成递增计算；解析结果按（路径, mtime_ns, size）缓存；去掉 streaming 门 | 小 | 去掉门之后，CDF 的模型调用和 chat 同时进行；对 chat 的影响要在真机上测 | 修了平方函数，chat 自己的注入、召回、Read 统计也一起变快；CDF 白天可以跑 |
| B | CDF 搬进独立子进程，daemon 看护（上一版的推荐） | 中 | 评审核实了几处会断：S7 复盘找 SDK 会话 id 要读 daemon 内存里的 session router（`s7_review.current_sdk_session_id`）；`spawn_budget` 需要 daemon 的活跃会话数和 OOM 冷却，子进程拿不到；重启重叠会误判 worker Hive 启动失败；关闭时 S7 fork 出来的 CLI 孙进程会变孤儿 | chat 路径自己的解析不会因此变快，平方函数还在 daemon 里 |
| C | 只在所有 tab 空闲 N 分钟后跑 | 小 | 知识晚几小时才生效；忙的日子照样饿死 | 推迟问题 |

**推荐 A**，B 降为后备：A 落地后，如果真机测量仍然显示 chat 变差，再做 B。B 的设计和评审意见留在 §5。

## 3. 方案 A

### 3.1 字节偏移递增计算
- `parse_units` 和各个 `_parse_*` 在解析开始时为整段文本建一个偏移计算器：记住上一次的（字符位置, 字节位置），下一次只编码两者之间的那一段；位置往回走时从头重算（不会发生，留作兜底的正确性保证）。
- 计算器作为参数传给 `_unit`，不放在模块级变量里，因为 daemon 和 worker 线程会同时调用 `parse_units`。
- `byte_end = byte_start + len(raw)`，`raw` 已经算过了，不再编码一次。
- **测试**：对线上 TECH / IMPROVEMENT / MEMORY / EVOLUTION / TIMELINE 的副本，以及含中文、emoji 的构造文本，新旧实现给出的（byte_start, byte_end, identity）完全一致。再加一条耗时上限的测试：1 MB 文本在 200 ms 内解析完（现在约 1 秒）。撤掉修复，这条必须变红。

### 3.2 解析缓存
- `parse_units` 按（解析后的路径, `st_mtime_ns`, `st_size`, baseline 版本）缓存，有界 LRU，写法和 `usage._unit_spans` 一样。
- `Unit` 是 frozen dataclass（已核实，`units.py:26`），缓存时返回 list 的副本，调用方改不到缓存内容。
- **测试**：文件改了，下一次解析拿到新内容；没改，第二次解析不再读文件。

### 3.3 准入
- 删掉 `ingestion_admission` 里的 streaming 判断，以及 maintenance 等待 streaming 的那段。内存压力 warning / critical 时只做 immediate 和 maintenance，这条规则不变。
- S7 复盘 fork CLI 之前用 `resource_monitor.spawn_budget(<活跃会话数>)` 判断，和 chat tab 的准入用同一条规则；CDF 在 daemon 进程里，活跃会话数和 OOM 冷却都拿得到。不能起就把任务延后。
- **测试**：streaming 时会话任务照样被领走（今天失败的场景，写成测试）；`spawn_budget` 说不行时 S7 不 fork。

### 3.4 Bedrock
chat 和 CDF 同时调模型会不会互相影响，现在没有数据，不预设。上线后统计 CDF 日志里的 ThrottlingException：出现了，就按已有的「判官不可用」退避处理（B8）。

## 4. 验证计划

- **单元测试**：§3.1–§3.3 的测试先写、先红，撤掉修复必须变红（mutation）。
- **测试范围**：`test_cdf*`，加上调用 `parse_units` 的 chat 路径测试（memory_injection、recall_multi、knowledge_store、usage）。
- **真机**（决定要不要做 B 的依据）：重启后，在 CDF 跑会话任务的同时保持一个 tab 持续 streaming。比较有 CDF 在跑和没有 CDF 在跑的两段时间：
  - `event-loop starvation` 日志行数；
  - TTFT（取数方法见 MEMORY Open Threads 里的那条 TTFT 记录）；
  - 一个会话任务从到期到被领走等了多久。

  如果有 CDF 的时段更差，就做 B。
- **审查**：concurrency 一位专家，看这次改动的 diff。
- **结束时逐行报告**：§3.1、§3.2、§3.3 和真机测量，各自完成还是缺失。

## 5. 后备：方案 B 要先解决的问题（评审核实）

只有 A 测下来仍然不够时才做 B，做之前先解决这些：
- S7 找 SDK 会话 id 读的是 daemon 内存里的 session router，子进程里会拿到 None，复盘会悄悄变成 "unresumable"：要在任务行里带上 sdk id，或者由 daemon 写到 DB。
- `spawn_budget` 的活跃会话数和 OOM 冷却在 daemon 内存里：要发布到文件或 DB。
- worker Hive 的启动判定：要区分「子进程崩了」和「锁被另一个活着的 owner 拿着」（后者是重启重叠时的正常情况）。owner 的 pid 记在 `ingestion_owner` 表里，不在锁文件里。
- 关闭：子进程要装 SIGTERM 处理（默认的 SIGTERM 不会运行 `stop_and_join`）；按进程组结束，带上 S7 fork 出来的 CLI 孙进程；等待时间不短于三个组件 join 的总时长。Windows 上 terminate 是硬杀，只能靠 `writer.recover`。

## 6. 现在怎么办

A 落地之前，会话任务只在所有 tab 都不 streaming 时跑。今天的 4 个任务会在你停下来的空档里跑完，不会丢，只是会晚。
