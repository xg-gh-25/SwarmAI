"""The benchmark's review harness: one expert review of one sealed case, as a native review runs it.

Design: docs/design/2026-11-18-expert-review-benchmark.md §4. ``pipeline-expert`` is a
sub-agent type (``core.pipeline_expert_agent``): its tool-call cap is a PreToolUse hook
that acts on hook input with ``agent_type == "pipeline-expert"`` and an ``agent_id``,
which the CLI only sends for a sub-agent's own tool calls. So a review is a parent
session opened with ``agents=agent_definitions()``, the budget hook on the matcher
``hook_builder`` registers it on, an explicit model and the sealed case as working
directory. Its prompt spawns exactly one ``pipeline-expert`` with the prompt
``s_expert-review`` §2 writes; the reply scored is the sub-agent's own, taken from the
Agent tool's result (word for word, whatever the parent then says). The parent's tokens
and cost are kept apart from the expert's: the tokens from the CLI's own transcripts of
the two (in the review's temporary config directory; a streamed message only carries its
first chunk's usage), the session's cost split between them by those tokens' prices.

What a review cannot reach:
- the answer: the case is a fresh one-commit repository (``sealed.build``), B's change
  uncommitted on top, nothing later; the prompt file is a copy next to it, so no path
  in the prompt leads back to the repository the case came from;
- the person's machine state: a temporary HOME (and Claude config directory), no
  filesystem settings (``setting_sources=[]``: the case's own ``.claude`` hooks and
  CLAUDE.md never load), no skills, no MCP servers;
- the cognition tools: every ``SWARM_*`` variable is emptied (``SWARM_BACKEND_BIN`` is
  how ``cognition find`` is reached) and PATH loses the directory holding that binary.
  ``core.code_intel.changed`` is not run for either arm (the code graph is of today's
  checkout: its callers would be future code).
A tool call naming an absolute path outside the review's temporary directory (other than
system directories) is recorded in ``outside_paths``, never blocked.

**Run it only on a repository you trust.** The review is a model with Bash and
``bypassPermissions`` reading the repository's content, as a native review is; nothing
here is a filesystem or network boundary. A planted instruction in an untrusted history
could make it read or send anything this user can. A run on an external corpus (S2)
needs a sandbox first (Claude Code's bash sandbox, which needs bubblewrap and socat on
Linux, or a container with no network but the model's endpoint); the ``run`` command
refuses to start without ``--trusted``.

Both arms go through ``review``; they differ only in the prompt file (and the EXPERT-ID
naming it). Each review's directory is removed when it ends, also on error or timeout.

Public: ``BASELINE_PROMPT``, ``CAP_MATCHER``, ``sha``, ``expert_prompt``, ``parent_prompt``,
``guidance``, ``sealed_env``, ``options``, ``review``, ``run_all``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from core.expert_bench import roster as _roster
from core.expert_bench import sealed

BASELINE_PROMPT = Path(__file__).with_name("baseline.md")
# the matcher hook_builder registers expert_budget_gate on
CAP_MATCHER = "Read|Grep|Glob|Bash"
PARENT_MAX_TURNS = 4
DEFAULT_TIMEOUT = 1800        # a hang guard per review; a slow review is normal
# system directories a review may read without it being recorded (``/proc`` and ``/tmp``
# are recorded: another process's command line or directory can name the source repository)
_SYSTEM_DIRS = ("/usr/", "/bin/", "/sbin/", "/lib/", "/lib64/", "/dev/", "/etc/")
_ABS = re.compile(r"(?<![\w.~$-])/[\w.@+-][^\s'\"`;|&<>(){}]*")
# this process's own session: a review is not part of the session that started it
_SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET",
                 "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_CHILD_SESSION",
                 "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expert_prompt(expert_id: str, prompt_file: str | Path, root: str | Path, goal: str,
                  guidance: str = "") -> str:
    """The prompt ``s_expert-review`` §2 gives one expert (its example, line for line),
    without the ``core.code_intel.changed`` output (§4: left out for both arms)."""
    lines = [f"EXPERT-ID: {expert_id}", f"Prompt file: {prompt_file}",
             f"Repository: {root}   Diff: git -C {root} diff HEAD",
             f"Goal of the change: {goal}"]
    if guidance:
        lines.append(guidance)
    lines.append("Report findings as: SEVERITY | file:line | what breaks | fix. "
                 "Native review, no pipeline run.")
    return "\n".join(lines)


def parent_prompt(expert_text: str) -> str:
    return ("Use the Agent tool exactly once, with subagent_type \"pipeline-expert\", "
            "description \"review\", run_in_background false, and as its prompt the text "
            "between the two ===== lines below, exactly as it is. Use no other tool. When "
            "it returns, reply with its reply word for word and nothing else.\n"
            "=====\n" + expert_text + "\n=====")


def guidance(repo: Path | str, case: Mapping) -> str:
    """The rows of the repository's ``.swarmai/review.md`` (at B's parent) that apply to
    B's change, for the prompt (§2: "anything in .swarmai/review.md"); '' without one."""
    from core.expert_bench.cases import _changed, _git
    from core.review_order import role
    from core.run_protocol import review_md
    text = review_md.read_at(repo, str(case["base"]))
    if not text:
        return ""
    paths = [p for s, p in _changed(repo, str(case["head"])) if s != "D" and role(p) == "source"]
    diff = _git(repo, "diff", "--no-renames", "--no-ext-diff", "--no-textconv", "--no-color",
                str(case["base"]), str(case["head"]))
    head = (f"The repository's review guidance is {review_md.PATH} in the repository.")
    try:
        hits = review_md.select(review_md.parse(text), paths, diff)
    except review_md.GuideError as e:
        return f"{head} Its pattern table cannot be parsed ({e}); read it and apply the rows whose trigger is present."
    if not hits:
        return head + " None of its pattern rows apply to this change."
    return (f"{head} These of its pattern rows apply to this change; check each against the "
            "diff and report a violated row as a finding naming its id:\n" + review_md.compact(hits))


# what the review's CLI keeps of this process's environment: the locale and terminal, and
# what the model backend needs (its region, endpoint and credentials); every other
# inherited variable is emptied (an allowlist: a secret added later stays out)
_KEEP_ENV = re.compile(r"^(PATH|LANG|LANGUAGE|LC_[A-Z]+|TERM|TZ|TMPDIR|USER|LOGNAME|SHELL|"
                       r"AWS_[A-Z0-9_]+|ANTHROPIC_[A-Z0-9_]+|CLAUDE_CODE_[A-Z0-9_]+|"
                       r"CLAUDE_CODE_USE_[A-Z]+|CLOUD_ML_REGION|VERTEX_REGION_[A-Z0-9_]+|"
                       r"ENABLE_PROMPT_CACHING_[A-Z0-9_]+|DISABLE_[A-Z0-9_]+|"
                       r"HTTPS?_PROXY|NO_PROXY|https?_proxy|no_proxy|SSL_CERT_FILE|SSL_CERT_DIR|"
                       r"NODE_EXTRA_CA_CERTS|REQUESTS_CA_BUNDLE)$")


def sealed_env(home: Path) -> dict[str, str]:
    """Environment overrides for the review's CLI. The SDK lays them over os.environ, so
    a variable is removed by emptying it: every inherited one outside ``_KEEP_ENV``, this
    session's own links and ``SWARM_*`` (``SWARM_BACKEND_BIN`` is how ``cognition`` is
    reached). PWD / OLDPWD are outside the allowlist: they name the directory the run
    started in."""
    env = {k: "" for k in os.environ
           if not _KEEP_ENV.match(k) or k in _SESSION_VARS or k.startswith("SWARM_")}
    env.update({"HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_CACHE_HOME": str(home / ".cache"), "SWARM_BACKEND_BIN": ""})
    backend = os.environ.get("SWARM_BACKEND_BIN")
    holder = os.path.dirname(os.path.realpath(backend)) if backend else None
    keep = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        if backend and (os.path.realpath(d) == holder
                        or os.path.exists(os.path.join(d, os.path.basename(backend)))):
            continue
        keep.append(d)
    env["PATH"] = os.pathsep.join(keep)
    return env


def _observer(log: list) -> Callable:
    async def observe(input_data: dict, tool_use_id: Optional[str], context: Any) -> dict:
        ti = input_data.get("tool_input") or {}
        paths = [str(ti[k]) for k in ("file_path", "path", "notebook_path") if ti.get(k)]
        if input_data.get("tool_name") == "Bash":
            paths += _ABS.findall(str(ti.get("command") or ""))
        log.append({"agent_id": input_data.get("agent_id"), "agent_type": input_data.get("agent_type"),
                    "tool": input_data.get("tool_name"), "paths": paths})
        return {}
    return observe


def options(*, model: str, cwd: Path, home: Path, observer: Optional[Callable] = None):
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
    from core.pipeline_expert_agent import EXPERT_TOOLS, agent_definitions, create_expert_budget_gate
    pre = []
    if observer is not None:
        pre.append(HookMatcher(matcher=CAP_MATCHER, hooks=[observer]))
    pre.append(HookMatcher(matcher=CAP_MATCHER, hooks=[create_expert_budget_gate()]))
    return ClaudeAgentOptions(
        model=model, cwd=str(cwd), agents=agent_definitions(),
        tools=["Agent", *EXPERT_TOOLS], setting_sources=[], skills=[], mcp_servers={},
        permission_mode="bypassPermissions", max_turns=PARENT_MAX_TURNS,
        env=sealed_env(home), hooks={"PreToolUse": pre})


_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _sum_usage(usages: Iterable[Mapping]) -> dict[str, int]:
    out = {k: 0 for k in _USAGE_KEYS}
    out["cache_creation_1h"] = 0
    for u in usages:
        for k in _USAGE_KEYS:
            out[k] += int(u.get(k) or 0)
        out["cache_creation_1h"] += int((u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0)
    return out


def _transcript_usage(config_dir: Path, agent_id: Optional[str]) -> Optional[tuple[list, list]]:
    """(parent usages, expert usages) from the CLI's own session transcripts under
    *config_dir*: one usage per API message, its last record (a streamed message's usage
    is its first chunk's, with almost no output tokens). None without a transcript."""
    projects = config_dir / "projects"
    if not projects.is_dir():
        return None

    def read(files: Iterable[Path]) -> list:
        last: dict[str, Mapping] = {}
        for f in files:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                m = d.get("message") if isinstance(d, dict) else None
                if d.get("type") == "assistant" and isinstance(m, dict) and m.get("usage"):
                    last[str(m.get("id") or len(last))] = m["usage"]
        return list(last.values())

    parent = read(p for p in projects.glob("*/*.jsonl"))
    expert = read(projects.glob(f"*/*/subagents/agent-{agent_id}.jsonl")) if agent_id else []
    return (parent, expert) if parent else None


def _weight(u: Mapping[str, int]) -> float:
    """Tokens weighted by their price relative to an input token (Anthropic's price
    ratios, the same for every model): used only to split one session's cost between
    the parent and the expert, which run on the same model."""
    c1h = u.get("cache_creation_1h", 0)
    c5m = max(0, u.get("cache_creation_input_tokens", 0) - c1h)
    return (u.get("input_tokens", 0) + 0.1 * u.get("cache_read_input_tokens", 0)
            + 1.25 * c5m + 2.0 * c1h + 5.0 * u.get("output_tokens", 0))


def _text(content: Any) -> Optional[str]:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    parts = [c.get("text", "") for c in content if isinstance(c, Mapping) and c.get("type") == "text"]
    return "\n".join(parts) if parts else None


def _outside(paths: Iterable[str], tmp: Path) -> list[str]:
    real_tmp = os.path.realpath(tmp)
    out = []
    for p in paths:
        if not p.startswith("/"):
            continue
        rp = os.path.realpath(p)
        if rp == real_tmp or rp.startswith(real_tmp + os.sep):
            continue
        if any(rp.startswith(d) or rp == d.rstrip("/") for d in _SYSTEM_DIRS) or rp == "/dev/null":
            continue
        if not os.path.exists(rp):     # a word in a command that only looks like a path
            continue
        out.append(p)
    return sorted(set(out))


async def review(case: Mapping, *, repo: Path | str, arm: str, expert: str,
                 prompt_file: Path | str, model: str, k: int, query: Optional[Callable] = None,
                 tmp_root: Optional[Path | str] = None, timeout: float = DEFAULT_TIMEOUT,
                 guidance_text: Optional[str] = None) -> dict:
    """One review of *case* by *expert* (*arm* ``expert`` or ``baseline``), reply *k*.
    Returns the result row; a failed review is a row with ``error`` set."""
    from claude_agent_sdk.types import (AssistantMessage, ResultMessage, TextBlock, ToolUseBlock,
                                        UserMessage)
    from core.pipeline_expert_agent import EXPERT_TOOL_CAP, PIPELINE_EXPERT
    if query is None:
        from claude_agent_sdk import query
    prompt_bytes = await asyncio.to_thread(Path(prompt_file).read_bytes)
    row: dict[str, Any] = {
        "case": case["id"], "arm": arm, "expert": expert, "k": k, "model": model,
        "prompt_file": str(prompt_file), "prompt_sha": sha(prompt_bytes),
        "gate_force": os.environ.get("SWARM_GATE_FORCE") or None,
        "reply": None, "parent_reply": None, "spawns": 0, "agent_type": None, "error": None}
    tmp = await asyncio.to_thread(_workdir, tmp_root)
    started = time.monotonic()
    log: list[dict] = []
    try:
        root, local_prompt, home = await asyncio.to_thread(
            _seal, repo, case, tmp, Path(prompt_file).name, prompt_bytes)
        if guidance_text is None:
            guidance_text = await asyncio.to_thread(guidance, repo, case)
        text = expert_prompt(expert, local_prompt, root, str(case.get("goal") or ""), guidance_text)
        row["root"] = str(root)
        row["expert_prompt"] = text
        opts = options(model=model, cwd=root, home=home, observer=_observer(log))
        spawn_ids: list[str] = []
        parent_u: dict[str, Mapping] = {}
        expert_u: dict[str, Mapping] = {}
        result = None
        parent_text: Optional[str] = None
        tool_result: dict = {}
        stream = query(prompt=parent_prompt(text), options=opts)
        async with contextlib.aclosing(stream):
            async with asyncio.timeout(timeout):
                async for m in stream:
                    if isinstance(m, AssistantMessage):
                        key = m.message_id or f"anon-{len(parent_u) + len(expert_u)}"
                        if m.parent_tool_use_id is None:
                            parent_u[key] = m.usage or {}
                            for b in m.content:
                                if isinstance(b, TextBlock) and b.text.strip():
                                    parent_text = b.text
                                if isinstance(b, ToolUseBlock) and b.name in ("Agent", "Task"):
                                    spawn_ids.append(b.id)
                                    row["spawn_type"] = (b.input or {}).get("subagent_type")
                        elif m.parent_tool_use_id in spawn_ids:
                            expert_u[key] = m.usage or {}
                    elif isinstance(m, UserMessage) and m.parent_tool_use_id is None and m.tool_use_result:
                        ids = {getattr(b, "tool_use_id", None) for b in (m.content or []) if not isinstance(m.content, str)}
                        if ids & set(spawn_ids) or (not ids and spawn_ids):
                            tool_result = m.tool_use_result
                    elif isinstance(m, ResultMessage):
                        result = m
        row["spawns"] = len(spawn_ids)
        row["agent_type"] = tool_result.get("agentType")
        row["reply"] = _text(tool_result.get("content"))
        row["expert_status"] = tool_result.get("status")
        row["expert_total_tokens"] = tool_result.get("totalTokens")
        row["expert_duration_ms"] = tool_result.get("totalDurationMs")
        row["expert_tool_uses"] = tool_result.get("totalToolUseCount")
        row["parent_usage"] = _sum_usage(parent_u.values())
        row["expert_usage"] = _sum_usage(expert_u.values())
        row["usage_source"] = "stream"
        if not any(row["expert_usage"].values()) and tool_result.get("usage"):
            # the sub-agent's turns were not streamed: its last turn's usage, as reported
            row["expert_usage"] = _sum_usage([tool_result["usage"]])
            row["usage_source"] = "tool_result"
        found = await asyncio.to_thread(_transcript_usage, home / ".claude", tool_result.get("agentId"))
        if found and found[1]:
            row["parent_usage"], row["expert_usage"] = _sum_usage(found[0]), _sum_usage(found[1])
            row["usage_source"] = "transcript"
        row["parent_reply"] = parent_text
        if result is not None:
            if result.result is not None:
                row["parent_reply"] = result.result
            row["total_cost_usd"] = result.total_cost_usd
            row["duration_ms"] = result.duration_ms
            row["num_turns"] = result.num_turns
            row["model_usage"] = result.model_usage
            total = float(result.total_cost_usd or 0.0)
            wp, we = _weight(row["parent_usage"]), _weight(row["expert_usage"])
            share = we / (we + wp) if we + wp else 0.0
            row["expert_cost_usd"] = total * share
            row["parent_cost_usd"] = total - row["expert_cost_usd"]
            if result.is_error:
                row["error"] = f"session ended with {result.subtype}"
        if row["error"] is None:
            if len(spawn_ids) != 1:
                row["error"] = f"{len(spawn_ids)} spawns, expected exactly one"
            elif row["agent_type"] != PIPELINE_EXPERT:
                row["error"] = f"the spawn ran as {row['agent_type']!r}, not {PIPELINE_EXPERT}"
            elif row["reply"] is None:
                row["error"] = "no reply from the expert"
            elif row["expert_status"] not in (None, "completed"):
                row["error"] = f"expert ended {row['expert_status']}"
    except TimeoutError:
        row["error"] = f"timed out after {timeout:.0f}s"
    except Exception as e:  # noqa: BLE001 -- one failed review is a row, never the run's end
        row["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        row["wall_s"] = round(time.monotonic() - started, 3)
        per_agent: dict[str, int] = {}
        for entry in log:
            if entry["agent_type"] == PIPELINE_EXPERT and entry["agent_id"]:
                per_agent[entry["agent_id"]] = per_agent.get(entry["agent_id"], 0) + 1
        row["expert_tool_calls"] = sum(per_agent.values())
        row["capped"] = any(n > EXPERT_TOOL_CAP for n in per_agent.values())
        row["parent_tool_calls"] = sum(1 for e in log if not e["agent_id"])
        row["outside_paths"] = await asyncio.to_thread(_outside, [p for e in log for p in e["paths"]], tmp)
        await asyncio.to_thread(shutil.rmtree, tmp, True)
    return row


def _workdir(tmp_root: Optional[Path | str]) -> Path:
    if tmp_root is not None:
        Path(tmp_root).mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="expert-bench-", dir=tmp_root))


def _seal(repo: Path | str, case: Mapping, tmp: Path, prompt_name: str,
          prompt_bytes: bytes) -> tuple[Path, Path, Path]:
    """The sealed case, the prompt file's copy and the temporary HOME, inside *tmp*."""
    root = tmp / "repo"
    sealed.build(repo, case, root)
    prompts = tmp / "prompts"
    prompts.mkdir()
    local_prompt = prompts / prompt_name
    local_prompt.write_bytes(prompt_bytes)
    home = tmp / "home"
    home.mkdir()
    return root, local_prompt, home


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _append(path: Path, row: Mapping) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _key(case_id: str, arm: str, expert: str, k: int, model: str, prompt_sha: str) -> tuple:
    return (case_id, arm, expert, k, model, prompt_sha)


async def run_all(cases: Iterable[Mapping], *, repo: Path | str, model: str, k: int, out: Path | str,
                  arms: Iterable[str] = ("expert", "baseline"), concurrency: int = 4,
                  query: Optional[Callable] = None, tmp_root: Optional[Path | str] = None,
                  timeout: float = DEFAULT_TIMEOUT, baseline: Path | str = BASELINE_PROMPT,
                  catalog: Optional[Mapping[str, str]] = None,
                  on_row: Optional[Callable[[dict], None]] = None,
                  start_window: Optional[float] = None) -> dict[str, int]:
    """Every (case, arm, expert, reply) review of *cases*, *concurrency* at a time, each
    row appended to *out* as it ends. A review already in *out* without an error (same
    case, arm, expert, k, model and prompt) is skipped, so a stopped run resumes; a
    failed one is run again. The expert arm calls the case's recorded ``roster`` (decided
    from B's diff by ``roster.for_case`` when the case has none). With *start_window*
    (seconds), no review starts later than that after the call began: the ones running
    finish, the rest are counted ``deferred`` for the next call, so a long run can go in
    slices that each end on their own."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cat = dict(catalog or _roster.catalog())
    arms = list(arms)
    shas = await asyncio.to_thread(
        lambda: {p: sha(Path(p).read_bytes()) for p in [*cat.values(), str(baseline)]})
    done = {_key(r["case"], r["arm"], r["expert"], r["k"], r["model"], r["prompt_sha"])
            for r in await asyncio.to_thread(_rows, out) if not r.get("error")}
    counts = {"done": 0, "skipped": 0, "errors": 0, "deferred": 0}
    began = time.monotonic()
    jobs = []
    cases = list(cases)
    guides: dict[str, str] = {}
    for case in cases:
        names = (case.get("roster") or await asyncio.to_thread(_roster.for_case, repo, case))["roster"]
        try:
            guides[case["id"]] = await asyncio.to_thread(guidance, repo, case)
        except (OSError, RuntimeError) as e:   # the review still runs; its prompt says so
            guides[case["id"]] = f"The repository's review guidance could not be read ({e})."
        for kk in range(k):
            todo = []
            if "expert" in arms:
                todo += [("expert", e, cat[e]) for e in names]
            if "baseline" in arms:
                todo.append(("baseline", "baseline", str(baseline)))
            for arm, expert, pf in todo:
                if _key(case["id"], arm, expert, kk, model, shas[str(pf)]) in done:
                    counts["skipped"] += 1
                else:
                    jobs.append((kk, case, arm, expert, pf))
    jobs.sort(key=lambda j: (j[0], j[1]["id"], j[2], j[3]))   # arms side by side in time
    sem = asyncio.Semaphore(max(1, concurrency))
    lock = asyncio.Lock()

    async def one(kk, case, arm, expert, pf):
        async with sem:
            if start_window is not None and time.monotonic() - began > start_window:
                counts["deferred"] += 1
                return
            row = await review(case, repo=repo, arm=arm, expert=expert, prompt_file=pf, model=model,
                               k=kk, query=query, tmp_root=tmp_root, timeout=timeout,
                               guidance_text=guides[case["id"]])
        async with lock:
            await asyncio.to_thread(_append, out, row)
            counts["errors" if row.get("error") else "done"] += 1
            if on_row:
                on_row(row)

    await asyncio.gather(*(one(*j) for j in jobs))
    return counts
