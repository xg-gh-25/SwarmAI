---
title: 审批弹窗误报 — dangerous_command_gate 为什么让人批准不危险的命令
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r2 — 已实现（本地 main，未打包）
---

# 审批弹窗误报

调研 run：`run_872ffe42`（research profile）。要求原话："调研下 SwarmAI 最近的几个 sessions 为什么老是给我弹一些
permission approve request，我感觉那些都不是 dangerous command 啊，出一个 design spec"。

## 0. 结论

- 最近弹窗的全部来自 `dangerous_command_gate`（Bash）。`external_approval_gate` 这段时间一次也没弹。
- 2026-10-22 到 10-26 一共 15 次（§1.2 逐条）。其中 10 次能在存档里找到完整命令，另外 5 次只有日志里的前 50 个字符。
  用今天的代码重判：
  - 5 次今天已经不会再弹（全部有完整命令）。规则在之前的提交里已经收窄，只是当时 daemon 跑的还是旧代码，最近两次打包后才生效。
  - 3 次是设计如此：在 git 仓库里丢弃未提交的改动（`reset --hard`、`worktree remove --force`）。现在只有另一个会话在那里
    还有未提交的改动时才问，多数情况会直接放行。（其中 2 次有完整命令，1 次只看到开头。）
  - **4 次仍然是误报**，分两类（§2 A、B），都是"判断不了就问人"的兜底规则把无害的命令也拦了下来。（3 次有完整命令；
    1 次只有开头，但日志里有 `[DISCARD] gated`，和 B 类那条完整的出自同一个会话、同一个目录。）
  - 3 次只有开头，无法判断：一次多半已修，一次多半是 B 类，一次不知道。
- 另外有两个问题让"为什么弹"很难查：弹窗上只写一句"Matches dangerous command pattern"，不说是哪条规则、命令里哪一段；
  `gate_decisions.jsonl` 不记被人批准的弹窗（§2 C）。

## 1. 证据

### 1.1 数据来源

| 来源 | 内容 | 局限 |
|---|---|---|
| `~/.swarm-ai/logs/backend-daemon.log` 的 `[PERMISSION_REQUEST]` 行 | 每次弹窗一行，带 request id，是唯一完整的记录 | 命令只记前 50 个字符 |
| `~/.swarm-ai/pending_approvals.json` | 按 request id 存完整命令、reason、结果 | 不是每一条都在；还留着约 195 条 10-22 之前测试写进来的假记录（会话 id `s`、`sess-smoke`，`_isolate_permission_queues` 在 10-22 修好）。按规则不清理，统计时按会话 id 排除 |
| `~/.swarm-ai/logs/gate_decisions.jsonl` | 只有 1 条 dangerous_command_gate | gate 自己弹窗、人批准后返回 approve，这类不会被记下来（§2 C） |

方法：用 request id 把日志和存档对上，取出完整命令，再交给今天 `core/security_hooks.py` 的各条判断函数
（glob 列表、`_is_dangerous_rm`、`_is_irreversible_external_op`、`_targets_irreplaceable_store`、`_discards_work`）重判。
只读解析字符串，不执行任何命令。没碰 `data.db`。

### 1.2 最近 15 次弹窗

| 时间 | 命令（摘要） | 今天会怎样 | 归类 |
|---|---|---|---|
| 10-22 18:32 | `rm -rf /tmp/v1audit.Jy0d && echo removed` | 不弹 | 已修（按单条命令判断 rm） |
| 10-22 19:53 | `find … -exec ls -la {} \;` + `grep` 日志 | 不弹 | 已修 |
| 10-22 22:07 | `mkdir -p /tmp/xgrev && cd /tmp/xgrev && rm -rf x && …` | **弹** | A：`rm -rf` 相对路径，看不到前面的 `cd /tmp/…` |
| 10-24 16:06 | worktree 里 `git add / commit / rebase / merge --ff-only` | 不弹 | 已修（§3.8 r17：ref 移动不再问） |
| 10-24 21:01 | `git worktree remove --force <自己 run 的 worktree> && git branch -D …` | 只在别的会话有未提交改动时弹 | 设计如此 |
| 10-24 22:15 | worktree 里 `git a…`（日志截断，存档没有） | 多半不弹 | 同 16:06，未核实 |
| 10-24 22:40 | worktree 里 `git reset -q --…`（日志截断） | 只在别的会话有未提交改动时弹 | 设计如此 |
| 10-24 22:49 | worktree 里 `git add / commit / rebase / merge / worktree remove / branch -d` | 不弹 | 已修 |
| 10-24 23:17 | 同上 | 不弹 | 已修 |
| 10-25 00:20 | 自己 worktree 里 `reset` 到 main | 只在别的会话有未提交改动时弹 | 设计如此 |
| 10-25 12:01 | `cd ~/.swarm-ai/SwarmWS && cp .context/USER.md /tmp…`（截断） | 无法核对 | 未知 |
| 10-25 12:12 | session worktree 里 `python3 - <<'EOF'` 改文件（截断） | 多半弹 | 同 12:34，未核实 |
| 10-25 12:19 | 同上（截断），日志有 `[DISCARD] gated` | **弹** | B（按日志判断） |
| 10-25 12:34 | 同上，reason 是"could not check whether another session has uncommitted work there" | **弹** | B |
| 10-26 00:41 | `T=$(mktemp -d); …; rm -rf $T` | **弹** | A：`rm -rf` 一个变量，看不到它来自 `mktemp -d` |

## 2. 原因

### A. `rm -rf` 的目标是相对路径或变量时，一律算危险

`_rm_segment_is_dangerous` 对每个操作数的判断（`core/security_hooks.py`）：

- 操作数里有 `$`、`~` 或 `..`，直接算危险；
- 相对路径一律算危险；
- 只有绝对路径落在 `/tmp/`、`/var/folders/`、`/private/tmp/`、`/private/var/folders/` 下才放行。

这条规则只看 `rm` 这一段，不知道同一条命令里前面做过什么：

- `mkdir -p /tmp/xgrev && cd /tmp/xgrev && rm -rf x`：`x` 实际在 `/tmp/xgrev` 下，但 `cd` 被忽略了，hook 输入里的 `cwd` 也没用；
- `T=$(mktemp -d); …; rm -rf $T`：`$T` 就是刚建的临时目录，但赋值被忽略了。

"不认识就问"本身没错。问题是这两种情况，命令里已经写着答案。

### B. 判断"会不会丢掉未提交的改动"时，规则拿错了范围

两个问题叠在一起：

1. **签名匹配到了一段英文。** `_DISCARD_SIG_RE` 是
   `\bgit\b[\s\S]*?\b(?:reset\W+--(?:hard|merge)|checkout|restore|switch|clean\W+-|worktree\W+remove)\b`，
   它会用在 heredoc 或解释器脚本的内容上（`_judge_with_bodies`）。中间的 `[\s\S]*?` 能跨任意长度。10-25 12:34 那条命令是一段
   Python，在改一个 docstring，正文里有 "git identities … a guessed checkout location"，于是被当成了 `git checkout`。
2. **目录不知道时就去查所有仓库。** 脚本内容里看不出目录，`_discard_dirs` 返回 None。这时 `_foreign_uncommitted` 会去查
   "所有别的会话写过的仓库"，在 6 秒的期限里一个个跑 `git status`。仓库一多就超时，抛 `GitUnknown`，结果是
   "could not check whether another session has uncommitted work there"，于是问人。

### C. 弹窗说不清原因，事后也查不到

- 除了丢弃类（discard）有具体说明，其他弹窗的 reason 都是同一句 "Matches dangerous command pattern"。不说是哪条规则，
  也不说是命令里的哪一段。一条命令几十行时，人无法判断该不该批。
- `HookRegistry` 只记 hook 返回的 deny 或 ask。dangerous_command_gate 是自己弹窗、自己等结果，人批准后返回 approve，
  所以被批准的弹窗不进 `gate_decisions.jsonl`。`gate_audit.py` 里这个 gate 只有 1 条。
  AGENTS.md 规定"一个 gate 是留是删，看这份记录"，但这份记录看不见它最常见的情况。

## 3. 设计

原则：命令里**写明了**答案，就用它；写不出来的，照旧问。不靠猜，不读 agent 进程的环境变量，因为 gate 运行在 daemon
进程里，看不到 agent 的环境。

### 3.1 A：rm 的目标按命令里写明的东西解析

- **A1 相对路径**：在同一条命令里，取 `rm` 之前最后一个 `cd <绝对路径>`（追踪方式和 `_discard_dirs` 对 `cd` 的处理一样，
  目标里不能有 `$`、glob 或 `..`）；没有这样的 `cd`，就用 hook 输入里的 `cwd`。拼出绝对路径后 normpath，落在安全前缀下就放行。
  以下情况仍算危险：中间有相对的 `cd`、`pushd` 或子 shell；`cwd` 不知道；拼出来的路径不在安全前缀下（比如仓库里的 `rm -rf src`）。
- **A2 变量**：`$N`、`"$N"`、`${N}`、`$N/子路径`，只有同时满足以下几点才放行：
  - 同一条命令里，`rm` 之前对 `N` 的赋值**只有一次**；
  - 这次赋值是 `N=$(mktemp -d)` 或 `N=$(mktemp -d -t <模板>)`，不带指向别处的路径模板；
  - 中间没有 `export`、`read`、`unset`，也没有对 `N` 的第二次赋值。

  其他变量（`$HOME`、`$TMPDIR`、`$KIROCREW_SCRATCH`、函数参数等）照旧算危险。
- 改动集中在 `_rm_segment_is_dangerous` 的调用方：`_is_dangerous_rm` 把整条命令的分段一起交给它，让它能看到前面的段，
  不改单段规则本身。

### 3.2 B：丢弃签名只认 git 调用的样子；目录尽量从命令里拿

- **B1 收紧签名**：只在 git **调用**的形状上匹配。
  - shell 写法：子命令必须是 `git` 后面第一个非选项词，可以带 `-C <dir>` 这类选项，且在同一行；
  - Python 的参数列表写法：`'git', ['-C', '<dir>',] 'checkout'` 这样相邻的字符串元素。

  这样英文句子就不会再命中，因为中间夹着的是普通单词，不是选项。shell 无法解析时的兜底照旧用签名，只是换成收紧后的版本。
- **B2 目录**：判定为丢弃、但目录来自脚本内容时，如果脚本里没有 `cwd=`、`chdir`、`-C`，就按"这段脚本在命令的工作目录里运行"
  处理（用 A1 的同一套 `cd`/`cwd` 解析），不再去查所有仓库；脚本里自己切了目录的，照旧查所有仓库。
- 期限超时（`GitUnknown`）仍然问人：这是真的判断不了。B1、B2 之后，不该走到这一步的命令不会再走到这里。

### 3.3 C：弹窗写明原因，每次弹窗都留记录

- **C1**：每条判断函数返回"规则 id + 命中的那一段命令"，不再只返回 True/False。
  弹窗的 reason 按规则写一句人能看懂的话，例如：
  - "`rm -rf $T`：删除目标是一个 gate 解析不了的变量"；
  - "`git checkout -- a.py`：会丢掉这个目录里未提交的改动"；
  - "`sudo …`：以管理员身份运行"。

  卡片上同时显示那一段命令。daemon 日志的 `[PERMISSION_REQUEST]` 行也带上规则 id。
- **C2**：dangerous_command_gate 每弹一次窗，就往 `gate_decisions.jsonl` 写一行，带 gate 名、规则 id、命中的那一段、
  结果（approve / deny / timeout）和会话 id，和其他 gate 用同一个写入口。`gate_audit.py` 按规则统计这些行。
  这些写入都放在批准之后、不在决定的关键路径上，写失败也不影响命令本身。

### 3.4 不做的事

- 会话内的批准仍然按命令全文的 hash 记（`permission_manager.approve_command`），不改成"本会话放行这条规则"。
  先用 C2 的数据看重复弹窗有多少，再决定要不要改。
- "自己的 worktree 里随便 reset"：不加例外。已有的"只有别的会话有未提交改动才问"已经覆盖。
- 仓库里的 `rm -rf <相对路径>` 仍然问：它可能删掉还没提交的东西。
- `external_approval_gate`、glob 列表（`sudo`、`kill -9`、`curl | sh` 等）不动。
- 不清理 `pending_approvals.json` 里的历史假记录。

## 4. 测试（测试先行，每条在 main 上应为红；变异检查）

- A（`_is_dangerous_rm`）：
  - 不危险：`cd /tmp/x && rm -rf y`；`cwd=/var/folders/…` 时的 `rm -rf y`；`T=$(mktemp -d); rm -rf $T`；
    `T=$(mktemp -d); rm -rf "$T/sub"`。
  - 仍然危险：`cd /tmp/x && cd .. && rm -rf etc`；仓库里 `cwd` 下的 `rm -rf src`；`cwd` 不知道时的 `rm -rf y`；
    `T=$(mktemp -d); T=/; rm -rf $T`；`T=$(mktemp -d ~/x.XXX); rm -rf $T`；`rm -rf $HOME/x`；`rm -rf $TMPDIR/x`。
  - 变异：去掉 `cd` 追踪，第一条变红；接受任意一次赋值，`T=/` 那条变红。
- B：
  - 不算丢弃：10-25 12:34 那条命令（只用它的形状做 fixture：一个 Python heredoc，正文里有 "git … checkout" 这样的英文）。
  - 仍算丢弃：Python 里 `subprocess.run(['git', 'checkout', '--', 'a.py'])`、`['git', '-C', d, 'reset', '--hard']`；
    shell 里 `git -C /r checkout -- a`；shell 无法解析时的 `git reset --hard`。
  - B2：脚本里有丢弃、没有切换目录时，dirs 等于命令的工作目录；脚本里有 `cwd=` 时，dirs 为 None（查所有仓库）。
  - 变异：把签名换回 `[\s\S]*?`，第一条变红。
- C：
  - reason 里有规则 id 和那一段命令；
  - 一次弹窗，不管结果是 approve、deny 还是 timeout，都在测试数据目录的 `gate_decisions.jsonl` 里留一行，`gate_audit.py` 能数到。
- 回归：现有的 `test_security_hooks.py` 和 dangerous gate 相关测试全部通过；测试里原来判为危险的样例，现在仍然危险。
- 验收：重放 §1.2 里有完整命令的 10 条，结果应为：A、B 的 3 条不弹；"设计如此"的 2 条照旧走丢弃检查；已修的 5 条不弹。

## 5. 实施

- 三个提交，各在自己的 worktree 里，按 A、B、C 的顺序；改动集中在 `backend/core/security_hooks.py` 和对应测试，
  C 还要动 `gate_audit.py`。
- 代价：A、B 都只是解析字符串，不多跑 git。B2 还会少跑 git，因为不再去查所有仓库。C2 每次弹窗多写一行文件，在批准之后、
  不在关键路径上。
- 生效要重新打包、重启 daemon（XG 手动）。

## 6. 实现记录（r2）

- 一个提交，改动在 `backend/core/security_hooks.py`、`backend/core/gate_decision_log.py`、`backend/scripts/gate_audit.py`；
  测试 `backend/tests/test_approval_prompt_false_positives.py`（§4 的全部条目，main 上全红）。
- A：`_dangerous_rm_segment` 返回命中的那段 rm（`_is_dangerous_rm` 是它的布尔版），按分隔符追踪 `cd <绝对路径> &&`
  和子 shell，变量只认顶层唯一一次 `N=$(mktemp -d)`（可带 `-t` 模板，不能带路径）；`export`、`read`、`for`、
  第二次赋值、env 前缀写法都让它失效。
- B：`_DISCARD_SIG_RE` 只认 git 调用形状（shell 同一行、Python argv 相邻元素）。脚本里有丢弃但没有 `cwd=`、`chdir`、
  `-C`、`GIT_DIR` 时，目录取读这段脚本的解释器所在目录；shell heredoc 仍返回 None。
- C：`_danger_match` 返回 `rule` / `match` / `why`；reason 形如 ``` `rm -rf $T`: … [rule: rm_unresolved_target] ```，
  存档请求带 `rule`，daemon 日志 `[PERMISSION_REQUEST]` 带规则 id。每次弹窗（以及关闭人工审批时的自动拒绝）由
  gate 自己写一行（`rule`、`match`、`prompted`），在 `pipeline_signal` 池里写；`gate_decision_log.mark_self_logged`
  让 `with_decision_log` 不再重复写 deny 行。`gate_audit.py` 在 gate 那一行下面按规则、按结果计数。
  `bash_misuse_report` 的 deny 文本识别加了 `[rule: `。
- 偏离：规则的说明是英文（和其他 gate 的 reason 一致），卡片前端没改，命中的那段在 reason 里显示。
- 验收：§1.2 里有完整命令的 10 条重放结果和 §4 预期一致（22:07、12:34、00:41 不再弹；21:01、00:20 走丢弃检查；
  其余 5 条不弹）。变异检查：去掉 cd 追踪、接受第二次赋值、签名换回 `[\s\S]*?`、去掉 B2 的目录，各自让测试变红。

## 修订

- r2（2026-10-26）：A、B、C 已实现，见 §6。§2 挪回 §1 之后。
- r1（2026-10-26）：初稿。依据 `run_872ffe42` 的调研：daemon 日志里的 15 次弹窗加上存档里的完整命令，用当时 main 的 gate 重判。
