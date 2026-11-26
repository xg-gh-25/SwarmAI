---
title: 审批弹窗误报 r2 — gate 能证明安全的命令不再问人
created: 2026-11-25
updated: 2026-11-25
project: SwarmAI
status: 已实现
follows: docs/design/2026-10-26-approval-prompt-false-positives.md
---

# 审批弹窗误报 r2

## 0. 结论

主人机器 14 天里批准了 41 次 Bash 审批卡（`~/.swarm-ai/logs/gate_decisions.jsonl` 里 `prompted: true`
的行），真正需要人的大约 5 次：`gh api` 的删除/新增、永久清空废纸篓、删除 Hive 身份目录。其余都来自七个原因，
每个都在它产生的那一层修掉；判断不了的照旧问（fail closed），没有新的审批模式，没有按仓库或用户的白名单。

| 规则 | 次数 | 原因 | 修法 |
|---|---|---|---|
| `discard_unchecked` | 20 | A：已删除的 worktree 被当成“git 说不清”；B：命令里写明的 `$W` 没被用上；D：`checkout --detach` 被当成丢弃；E：Python 正文里的英文被当成 git 调用 | §A、§B、§D、§E |
| `discard_foreign_uncommitted` | 2 | B：目录不知道时去查所有仓库，碰上别的会话在另一个仓库的未提交文件 | §B |
| `rm_unresolved_target` | 14 | B、C：`$(mktemp -d)/sub`、工具缓存目录、`2>/dev/null` 的 `2` 被当成操作数 | §B、§C |
| `irreplaceable_store` | 2 | B、F：`$W` 没解析；`mv` 进项目的新文件被当成破坏 | §B、§F |
| `irreversible_external` | 2 | 真需要人 | 不变 |
| `v1_authorization` | 1 | 设计如此 | 不变 |

另外 `external_approval_gate`（MCP 发消息等非 Bash 的对外调用）以前不写 `gate_decisions.jsonl`，它弹了多少次无从统计（§G）。

## 1. 原因与修法

### A. 不存在的仓库没有未提交的改动

丢弃命令的目录不知道时，`_foreign_uncommitted` 会去查所有兄弟会话写过的仓库（`shared_checkout.sibling_repos`）。
兄弟会话不停地建、删 session worktree，它们的 `_files_written` 里留着已删 worktree 里的路径，`repo_guidance._repo_root`
又把这些根目录缓存着、从不复查。于是 `git -C <已删目录> status` 失败 → `_git_status` 返回 None →
`sibling_uncommitted(strict=True)` 抛 `GitUnknown` → `discard_unchecked` → 问人。

修法在唯一把“失败”变成 `GitUnknown` 的地方：`sibling_uncommitted` 在 git 失败之后检查根目录是不是还是 git 工作树
（`_is_work_tree`：目录不在、没有 `.git`、或 `.git` 文件指向的 gitdir 已不存在，都算不是）。不是 → 没有未提交的改动，
返回 `[]`；还是 → 照旧抛 `GitUnknown`。只在 git 失败后才查，正常路径零成本；存在的工作树上 git 超时或出错仍然问人。
没有改 `_repo_root` 的缓存：它还有别的读者，在消费处判断是最小的改动。

### B. 命令自己写明的变量值，就按写明的用

`W=/abs; git -C $W checkout -q br` 以前目录未知（`['']`），而 `git -C /abs checkout -q br` 是 `[]`。rm 目标
（`S=/x; rm -rf "$S"`）和 cp/mv 操作数（`W=/tmp/...; cp -R a $W/Projects/n/`）同样没解析。

一个共用的解析器 `_known_vars(command)`（按命令文本缓存）给每个分段算出“此处已确定的变量”，丢弃目录、rm 目标、
store 操作数三处都先用 `_subst_vars` 代入再判断。原来只给 rm 用的 `N=$(mktemp -d)`（`temp_vars`）并进来了。

变量只有同时满足下面全部条件才算确定，其余一律未知，调用方保持原来 fail closed 的判断：

- **名字只以两种形式出现**：唯一的那次赋值 `NAME=`，和以词尾或 `/` 结束的引用 `$NAME` / `${NAME}`。其他任何形式
  （`read W`、`builtin read W`、zsh 的 `print -v W` / `set -A W` / `${W::=/}` / `vared W`、`exec {W}>f`、`printf -vW`、
  `(( W = 1 ))`、`$W[1]`、`$W(:h)`）都让它未知。这是白名单：没列到的绑定方式也拦得住。绑定类内建拿到的是算出来的名字
  （`printf -v "$X" …`）时，所有变量都未知。
- **整条命令里只绑定一次**（不只是用之前）：任何分段里的赋值词（env 前缀、`export N=`、`eval N=`、trap 字符串）、
  绑定类内建（export / read / unset / declare / local / readonly / for / select / getopts / mapfile / let /
  `printf -v` / `wait -p`）、算术或复合赋值文本（`(( N = 1 ))`、`N+=x`、`N[0]=`）都算一次。只数“用之前”的话，
  `for …; do rm -rf $W; W=/; done` 第二轮就是 `rm -rf /`。
- **无条件的顶层独立赋值**：不在 `( )` 或复合命令（if / while / for / case / `{ }`）里；前面是开头或 `;`，不是 `&&` / `||`
  （`false && W=/tmp/a; rm -rf $W/etc` 里 W 没赋上，`$W/etc` 就是 `/etc`）；后面不是管道或 `&`（子 shell）；分段里只有赋值词
  （`W=x cmd` 是 env 前缀，不是 shell 变量）。
- **值是字面量**：`[A-Za-z0-9_./@%+:,=-]` 组成的一个词（绝对路径、相对词都行，代入后和 shell 展开完全一样，不会分词、
  不会 glob），或未加引号的 `~/...`（按赋值时的展开处理）；不含 `..` 段，不以 `-` 开头；或者是 `$(mktemp -d)[/固定后缀]`。
- 对它的引用没有出现在单引号里或反斜杠后（那是字面文本，shlex 已把引号去掉，看不出来）。
- 不是 shell 自己维护的变量（PWD、OLDPWD、REPLY、RANDOM、IFS，zsh 的 MATCH / match / reply 等：`[[ =~ ]]` 会改它们）；整条命令没有反引号、`IFS`、`$'…'` / `$"…"`
  （`$'\x57'` 不用字母就拼出 `W`）、zsh 参数标志 `${(…)`（`${(P)X::=/}` 经算出来的名字赋值）、`source` / `.` / `eval` /
  `trap` / `sched` / `autoload` / `fc`（`trap … DEBUG` 在下一条命令展开前先运行）、`declare -n`（这些都能悄悄改任何变量）。
- **按名字绑定的内建拿到展开时**，所有变量都未知：外部程序改不了本 shell 的变量，只有内建能；所以 bash 和 zsh（含模块）的
  内建全列出来，拿到 `$` 展开、又不在安全名单里的（`printf -v "$X"`、`zformat -a $X`、`read $X`、`export "$X=/"`）一律算可能绑定。
  这也是白名单：没列到的内建照样拦。`echo` / `cd` / `test` 这些不绑定的内建，`printf` / `print` 不带 `-v`、`set` 不带 `-A`、
  `stat` 不带 `-A` / `-H`（选项合写也算：`print -rv`、`wait -np`、`set -eA`；选项本身是展开的 `printf -$X` 也算）、`export PATH="$P:$PATH"` 这种名字是字面量的
  声明，都不影响；`declare -n` / `local -n`（nameref 绑到任意名字）让所有变量未知。
- **程序名本身是展开**时，只有“命令从没绑定过的变量的普通引用”（`"$SWARM_BACKEND_BIN" py …`，跑的是环境给的程序）
  不影响；`${C:-source}`、命令自己赋过值的 `$C`（可能就是 `source`）、花括号或通配的程序名（`{source,./x.sh}`、`sourc[e]`）
  让所有变量未知。

代入只认 `$NAME`、`${NAME}`（`"$NAME"` 去引号后就是 `$NAME`），可以出现在词的任何位置，但后面只能是词尾或 `/`：
zsh 把 `$W[1]` 读成 W 的第一个字符、`$W:h` 读成它的父目录（`W=/tmp/a/b; rm -rf $W:h:h` 就是 `rm -rf /tmp`，再多一层就是 `/`）；mktemp 目录不是路径，只在 rm
判断里当临时目录。示例 11 的目标 `$W/Projects/$n/` 需要 `n=SwarmAI` 这种相对词也能代入，所以值不限于绝对路径。

### C. rm：临时目录的子目录、工具缓存、重定向的 fd

- `N=$(mktemp -d)/sub`（固定后缀，不含 `$`、`..`、`~`）也是临时目录。未加引号时分词器把 `/sub` 切成单独一段，
  所以还要求原文里 `)` 和 `/sub` 紧挨着：`N=$(mktemp -d) /x` 是给命令 `/x` 的 env 前缀，不是 shell 变量。
- 最后一段是可再生的工具缓存（`__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`），或 `__pycache__/` 下直接的
  文件 glob，不危险。条件：不含 `$`、反引号、`..`，不以 `/` 结尾（rm 会顺着符号链接进去）；能在磁盘上找到时（绝对路径，
  或相对已知目录），真实路径也得是这个名字（指向别处的同名符号链接不算）；查不到的 glob 不放行。
- **重定向的 fd 不是操作数**。`_gate_shell_segments` 用 shlex 切词，`2>/dev/null` 会切成 `2`、`>`、`/dev/null`，
  `>` 和它的目标被丢掉，`2` 却留成了一个词，于是 `rm -rf /tmp/x 2>/dev/null` 多了一个相对路径操作数 `2`。在分词器这一层修：
  切词前去掉未加引号、位于词首、紧贴 `<` / `>` 的数字（`_drop_redirect_fds`）。`echo 2 > f` 的 `2` 中间有空格，照旧是词。
  这一层的修复对所有谓词都生效（例如 `git checkout -q main 2>/dev/null` 不再多出第二个 pathspec）。

rm 后面紧跟 `(`（`rm -rf /tmp/a/b(:h:h:h)`）在 bash 里是语法错误，在 zsh 里是 glob 修饰符（这个例子就是 `/`），照旧算危险。

顺带修了一个同函数里的漏判：rm 和 store 判断以前不跳过复合命令的关键字，`for x in 1; do rm -rf /; done`、
`if true; then rm .context/MEMORY.md; fi` 都没被看到（第一个词是 `do` / `then`）。现在开头的
`do` / `then` / `else` / `{` / `!`、`exec [-cl] [-a NAME]` 和 `sudo` / `env` / `timeout` 这类包装按任意顺序去掉再判断
（`do sudo rm -rf /`、`exec rm -rf /`、`exec -ca x rm -rf /`）；包装命令自己的选项也一起跳过（`env -i`、`nice -n 5`、
`command -p`、`time -p`、`sudo -u root`、`timeout -k 5 60`，合写的 `env -iu NAME`、`doas -nu root`，`env -S '…'` 取字符串里的词
并当作 env 自己的参数再读一遍，`doas`）；`exec` 的选项（含 `-ca NAME` 这种合写）和 `builtin` /
`noglob` / `nocorrect` 在所有谓词共用的 `_unwrap_program` 里也一样跳过，所以 exec 加选项再跑 git 的丢弃命令也照样被认出来。交给另一个 shell 的脚本（`sh -c '…'`、`bash <<EOF`）前面的这些前缀
也一样去掉：`for …; do sh -c 'rm -rf ~'; done`、`exec sh -c …`、`noglob bash <<EOF` 都照样判断里面的脚本。这是收紧，不是放宽。

### D. `checkout --detach` 是分支移动

`git checkout --detach <x>` 和 `git switch --detach <x>` 会带着本地改动走或拒绝，不丢东西；`_checkout_discards` 把
`--detach` 当成 `-b` 一样的分支移动，目录未知也不算丢弃。同时带 `-f` / `--force` / `-p` 或 `--` 后的路径，照旧算丢弃。

### E. Python 正文：只有真正调用 git 才算丢弃

`_DISCARD_SIG_RE` 以前在整个 Python 正文上搜，正文里改的字符串 `pytest.skip("not a git checkout")`、docstring 里的
``` ``git worktree remove`` ```、注释都会命中。现在 Python heredoc 和 `python -c CODE`（以前完全没判断，这是收紧）先过签名，
再用 `ast` 解析（`_python_discards`，按代码缓存）：

- 算丢弃：任何位置的 argv 列表 / 元组字面量（首元素是字符串），拼成 shell 行后 `_discards_work` 判为丢弃；或传给进程调用
  （按最后一个名字认：`subprocess.*`、`os.system` / `popen` / `exec*` / `spawn*`、`asyncio.create_subprocess_*`、
  `pty.spawn`，以及从它们 import 或赋值来的别名）的字符串 / f-string（`{…}` 当未知词）本身是一行会丢弃的 shell 命令。
- 是数据：其他字符串字面量、注释。
- 判断不了 → 照旧按签名算丢弃：解析失败、有动态执行（`exec` / `eval` / `getattr` …）、进程调用拿到的命令不是字面量
  （变量、函数调用、首元素不是字面量的列表）。

签名不命中的正文永远不算丢弃（也就不用解析）；命中签名的，只有解析后**每个调用都看得清**才放行：只导入不会自己执行代码的模块
（`_PY_DATA_MODULES`：re、json、pathlib、os、subprocess、shutil …；pickle、webbrowser、importlib、非标准库模块都不算，
`pickle.loads` 就能执行命令），每个被调用的对象要么是只处理数据的已知函数 / 方法（`_PY_DATA_CALLS`：`read_text`、`replace`、
`re.sub`、`json.dump` …），要么是正文自己定义的函数或类，要么是命令为字面量的进程调用（照上面判断）；进程调用跑的是 shell、解释器或一个路径
（它可能从 stdin 或正文刚写的文件读命令），或者带了 `input=`，都算看不清。进程函数被当成值使用
（`functools.partial(subprocess.run, …)`、`map(os.system, …)`、`Thread(target=os.system)`、`r = subprocess.run`）、
按名字字符串取到它（`vars(os)['system']`、`attrgetter('system')`、`methodcaller('run', …)`），或调用一个算出来的对象
（`d['k'](…)`、`f()(…)`）、调用一个变量或任何没列出的函数（`inspect.getmembers` 取到的函数）时，它会执行什么看不见，
按“判断不了”处理。

### F. `mv` 的目标和 `cp` 一样判断；临时目录里的 store 是草稿

- `mv` 的每个**源**照旧拦（把 store 挪走就毁了原位置）；**目标**只在覆盖 store 里已有的文件或目录时才拦（`_copy_overwrites`），
  和 `cp` 一样。所以把一个新设计文档挪进项目（示例 12）放行，`mv Projects/CMHK /tmp`、`mv .context/MEMORY.md x`、
  `mv new.md Projects/P/PRODUCT.md`（相对路径，覆盖不确定）照旧问。
- 代入变量后是完整绝对路径、且**真实路径**（解析符号链接）在系统临时目录下（rm 规则信任的同一组前缀）的操作数不是受管 store：
  把 DDD 复制进 `/tmp` 做隔离实验（示例 11，`iso2` 说明目标可能已经存在），不再当成破坏。从 `/tmp` 经符号链接指回真实
  store 的路径照旧拦。两个把 `tmp_path` 当真实工作区用的老测试相应地关掉了临时前缀。

### G. `external_approval_gate` 的每次弹窗都留一行

和 `dangerous_command_gate` 的 `_log_prompt` 一样：人做出决定后写一行 `gate: external_approval_gate`、`decision`、
`rule: external_tool`、`match: <工具名>`、`prompted: true`，在 `pipeline_signal` 线程池里写，不在事件循环上，写失败不影响调用；
`mark_self_logged` 让外层包装不再重复写 deny 行。

## 2. 照旧问人的（每条都有测试）

- `git push --force`、`gh api -X DELETE …`、`gh repo edit --visibility …`
- 别的活会话在该仓库有自己写的未提交文件时的丢弃，包括经过解析后的 `$W`
- 存在的工作树上 git 超时或失败（真正的 `GitUnknown`）
- `rm -rf ~/.Trash/x`、`rm -rf $HOME/x`、`S=~/.swarm-ai/runtime/2.5.5.diag; rm -rf "$S"`（解析了，但不是临时目录）、
  `rm -rf __pycache__/../src`、目录未知时的 `rm -rf build`
- `T=$(mktemp -d); T=/; rm -rf $T`、`W=/tmp/a; export W=/; rm -rf $W`、`(W=/tmp/a); rm -rf $W`、
  `W=/abs; W=$(cat f); git -C $W reset --hard`
- `mv Projects/CMHK /tmp`、`rm .context/MEMORY.md`、`cp /dev/null Knowledge/Library/x.md`
- 目录未知且某仓库有兄弟会话未提交文件时的 `git checkout -f --detach x`、`git checkout -- .`、`git checkout HEAD -- path`
- 运行 `subprocess.run(['git','reset','--hard'])` 的 Python 正文

## 3. 仍然接受的残留

这个 gate 防的是粗心或被注入的命令里的误伤，不是对抗性沙箱（`python3 -c "import shutil; shutil.rmtree(...)"` 本来就不经过
rm 规则）。评审找到的经由转义、trap、zsh 参数标志、按名字取函数、合写的 `exec` 选项的改写都已关上，而且关的方式是白名单（名字的出现形式、
内建、Python 的被调用对象和导入），没列到的写法一律当作判断不了；下面这些照旧：

- 命令没写明值的变量（`W=$(pwd)`、继承来的环境变量）照旧未知，这类命令照旧问。
- 写进文件再执行的脚本（`open('x.sh','w').write(...)` 然后 `bash x.sh`）看不见，和 shell 里的 `bash x.sh` 一样。
- 签名本身就不命中的 Python 写法（例如 GitPython 的 `repo.git.reset('--hard')`）和以前一样看不见。
- 通配符源的 `cp`（`cp a/*.md $W/.context/`）在目标不在临时目录时，覆盖与否算不清，照旧问。

## 4. 测试

- `backend/tests/test_approval_prompt_false_positives_r2.py`：七个原因各自的放行与照旧问的用例，12 条代表命令走真实 gate
  （真实 git 仓库、假的会话路由，审批等待固定为 deny）；A 的复现（建仓库 + `git worktree add`，兄弟会话写进去，删掉 worktree
  后 `_foreign_uncommitted([""], mine)` 返回 `''`，main 上抛 `GitUnknown`）。
- `backend/tests/test_shared_checkout.py`：已删 worktree、没有 `.git`、gitdir 悬空的目录都读作干净；存在的工作树上 git 失败照旧抛。
- 变异检查（`python -m scripts.mutation_check`）：每个修复各撤一处，共 63 处（后几轮把被白名单覆盖、测试无法单独验证的旧分支删掉了），全部让测试变红，包括“绑定次数不数”
  “接受条件赋值”“引号里的引用也代入”“算术赋值不数”“mktemp 后缀不要求紧挨”“缓存名不查真实路径”“临时目录不解析符号链接”
  “引用后跟 zsh 下标或修饰符也代入”这几处保护性条件。
- 回归：`test_security_hooks.py`、`test_discard_gate.py`、`test_approval_prompt_false_positives.py`、`test_shared_checkout.py`
  以及其余调用这些谓词的测试文件全部通过。

## 5. 代价（测量）

gate 在事件循环上同步判断每条 Bash 命令。每个谓词以前各自重新切一遍命令，现在切分（`_split_segments`）、变量解析
（`_known_vars`）、Python 解析（`_python_discards`）都按文本缓存，同一次 gate 调用里只算一次。冷缓存下一次完整判断
（`_danger_match` + `_discard_dirs` + `_discards_work`）：

| 命令 | 之前 | 之后 |
|---|---|---|
| 普通链式命令（cd、git status、`rm -rf /tmp/x 2>/dev/null`） | 0.58 ms | 0.56 ms |
| 200 个赋值加 rm 的长命令（约 10 KB） | 43 ms | 43 ms |
| 命中签名的 38 KB Python 编辑脚本 | 27 ms | 49 ms |

只有命中签名的大 Python 正文多出 `ast.parse` 的时间；不命中签名的正文不解析。

## 修订

- r2（2026-11-25）：初稿即实现。
