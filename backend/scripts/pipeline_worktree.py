"""pipeline_worktree — artifact_cli's side of the per-run worktree
(docs/design/pipeline-run-worktree.md). artifact_cli keeps only call sites; the
policy lives here, the git mechanics in ``core.run_worktree``.

Key public symbols:
- ``refuse_if_worktree_copy`` — a CLI started from inside a run worktree refuses (§6.2).
- ``lock_command`` — one run lock per run-writing command (§4.1).
- ``on_stage_start`` — create the worktree at the first plan/build/goal_cycle start;
  at deliver: block un-added files, sync, record the reviewed tree (§5, §6.2, §7.2).
- ``derive_files_touched`` — files_touched from the change set (§6.2).
- ``land_deliver`` — deliver stage-complete landing (§7.4, §7.5).
- ``run_commit_isolated`` — run-commit is an idempotent confirmation (§7.1).
- ``completion_block`` / ``after_status_write`` — completion gate + release (§8.2, §8.4).
- ``reap_note`` — what a non-owner terminal writer records (``release_requested``).
- ``cmd_run_diff`` — ``run-diff --mark/--since`` (§6.3).
- ``sweep_for_cli`` — run-create's sweep (only this session's own runs).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from core import hook_token
from core import run_worktree as rw
from core.run_lock import RunLockTimeout, acquire_for_process, atomic_write_json, update_run

# Commands that write run.json for ONE run: the whole command is a critical section.
LOCKED_COMMANDS = frozenset({
    "run-update", "run-commit", "run-checkpoint", "run-resume", "run-report",
    "stage-start", "stage-complete", "publish", "run-observe", "run-diff",
    "run-surface-changes", "run-repo-add",
})
WORKTREE_STAGES = ("plan", "build", "goal_cycle")
HOOKLESS_LIMIT = 3
TERMINAL = ("completed", "complete", "abandoned", "failed", "cancelled")
RULE = ("This run's code lives in its own worktrees: edit files under {paths} (NOT the main "
        "checkout). New files need `git add <path>` in the worktree; do not commit there. "
        "A write into another git repo is refused once, with the `run-repo-add` command that "
        "gives that repo a worktree too. Run the pipeline tools with the main checkout's "
        "absolute paths, each as ONE simple command.")
# The stage cards are written for the shared checkout and have no room left (§6.3), so
# the steps that differ for an isolated run are overridden here, in the tool output.
CARD_OVERRIDES = {
    "build": "files_touched is derived from the worktree's change set: skip registering it "
             "by hand; `git add` only NEW files.",
    "test": "fixes stay uncommitted in the worktree: no per-fix commits.",
    "goal_cycle": "use `artifact_cli.py run-diff --mark/--since` for anchors and diffs, not "
                  "`git rev-parse HEAD` / `<commit>..HEAD` in the main checkout.",
    "deliver": "skip the card's staging / unstaging and run-commit steps: stage-complete "
               "lands the change, PUSH-READY or not (run-commit afterwards only confirms).",
}


def _emit_fail(payload: dict, code: int = 2) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))
    sys.exit(code)


def refuse_if_worktree_copy(script: str) -> None:
    """A tool copy inside a run worktree must never run against the workspace that owns
    that worktree (it could be half-edited, and it would write real runs). Run against
    another workspace — a test's temp SwarmWS — it is the code under test and may run.
    $SWARM_WORKSPACE is read as artifact_cli._get_workspace reads it (``~`` expanded), so
    a literal "~/.swarm-ai/SwarmWS" is the owning workspace here too."""
    hit = rw.run_for_path(script)
    if hit is None:
        return
    rid, repo = hit
    ws = os.environ.get("SWARM_WORKSPACE")
    if ws and not rw.owned_by(rid, Path(ws).expanduser()):
        return
    main_hint = "<main checkout>/" + os.path.relpath(os.path.realpath(script),
                                                     str(rw.wt_path(rid, repo)))
    _emit_fail({"ok": False, "error": "this tool copy lives inside run worktree "
                f"{rw.wt_path(rid, repo)}; run the main checkout's copy instead",
                "use": main_hint})


def lock_command(command: str, project, run_id, run_dir_fn) -> None:
    """Hold the run lock until the process exits (released by the kernel on exit)."""
    if command not in LOCKED_COMMANDS or not project or not run_id:
        return
    try:
        rd = run_dir_fn(project, run_id)
    except ValueError:
        return  # the handler reports the unsafe identifier
    if not (rd / "run.json").exists():
        return
    try:
        acquire_for_process(rd, timeout=60.0)
    except RunLockTimeout:
        _emit_fail({"ok": False, "code": "busy", "error": f"run {run_id} is locked by "
                    "another command for 60s; retry it"})
    apply_cancel_request(rd / "run.json")


CANCEL_SIDECAR = ".cancel_requested"


def apply_cancel_request(run_file: Path) -> bool:
    """A cancel that could not wait for the run lock left ``.cancel_requested`` (written
    lock-free by the router). The next lock holder applies it before doing anything, so
    a running command can never write ``running`` back over it."""
    side = run_file.parent / CANCEL_SIDECAR
    if not side.exists():
        return False

    def m(d: dict) -> None:
        if d.get("status") in ("running", "paused"):
            d["status"] = "cancelled"
            reap_note(d)
    try:
        _save(run_file, m)
        side.unlink(missing_ok=True)
    except (OSError, ValueError, RunLockTimeout):
        return False
    return True


# ── helpers ──────────────────────────────────────────────────────────────────
class _Mains(dict):
    """repo key → main checkout, plus ``errors``: key → why git did not confirm it."""

    def __init__(self):
        super().__init__()
        self.errors: "dict[str, str]" = {}


def mains_for(run_state: dict, workspace, self_repo, run_id: str) -> "_Mains":
    """repo key → main checkout of each worktree entry, named by GIT (rw.entry_main:
    the worktree's own registration, cross-checked with the pinned path and the
    recorded common dir) — never by a path from run.json alone. Bindings only name the
    main of a legacy entry (no pinned main) whose worktree is no longer on disk."""
    hints = dict(rw.code_repos(workspace, str(run_state.get("project") or ""), self_repo))
    out = _Mains()
    for e in _entries(run_state):
        key = str(e.get("repo_name"))
        try:
            out[key] = rw.entry_main(run_id, e, hint=hints.get(key))
        except rw.WorktreeError as err:
            out.errors[key] = err.message
    return out


def cli_record() -> dict:
    """This CLI's own interpreter and script: tool outputs and the gates print commands
    from it (the packaged daemon cannot know where the source checkout is)."""
    return {"python": sys.executable,
            "script": os.path.realpath(os.path.join(os.path.dirname(__file__), "artifact_cli.py"))}


def _rerun(run_state: dict, run_id: str, sub: str, stage: str, extra: str = "") -> str:
    import shlex
    proj = shlex.quote(str(run_state.get("project") or "<project>"))
    return self_cmd(f"{sub} --project {proj} --run-id {run_id} --stage {stage} {extra}".strip())


def self_cmd(args: str) -> str:
    """``<python> <artifact_cli.py> <args>``: ONE simple command with absolute paths."""
    import shlex
    c = cli_record()
    return f"{shlex.quote(c['python'])} {shlex.quote(c['script'])} {args}"


def is_isolated(run_state: dict) -> bool:
    return run_state.get("worktree_mode") == "isolated"


_TOKEN_VERDICTS: "dict[tuple, tuple[bool, str]]" = {}


def _token(run_dir: Path, run_state: dict, run_id: str, sub: str) -> "tuple[bool, str]":
    """The command's token, burned at most ONCE per process: one command may pass
    several worktree steps (a rebuild from the ledger, then the deliver sync)."""
    tok = os.environ.get(hook_token.TOKEN_ENV)
    key = (str(run_dir), tok, run_id, sub, run_state.get("session_id"))
    if tok and key in _TOKEN_VERDICTS:
        return _TOKEN_VERDICTS[key]
    verdict = hook_token.consume(run_dir, tok, run_state.get("session_id"), run_id, sub)
    if tok:
        _TOKEN_VERDICTS[key] = verdict
    return verdict


def _save(run_file: Path, mutate) -> dict:
    return update_run(run_file, mutate)


def _entries(run_state: dict) -> "list[dict]":
    return [e for e in run_state.get("worktrees") or [] if isinstance(e, dict)]


def _validated(run_id: str, run_state: dict, mains: dict) -> "list[tuple[dict, Path, Path]]":
    """(entry, worktree, main) for every ACTIVE entry; raises WorktreeError."""
    out = []
    for e in _entries(run_state):
        if e.get("state") != "active":
            continue
        main = mains.get(e.get("repo_name"))
        if main is None:
            why = getattr(mains, "errors", {}).get(e.get("repo_name"), "main checkout unknown")
            raise rw.WorktreeError("worktree_invalid", f"repo {e.get('repo_name')!r}: {why}")
        out.append((e, rw.validate(run_id, e, main), main))
    return out


_CTX: dict = {}   # project / run_id of the command being served (for re-run commands)


def stage_fail(stage: str, err: "rw.WorktreeError", next_step: str) -> None:
    """One structured stage-boundary failure carrying the worktree error code (§7.7),
    plus the re-run commands it may ask for, each ONE absolute simple command."""
    extra = {}
    if _CTX.get("run_id"):
        st = {"project": _CTX.get("project")}
        extra = {"rerun_stage_start": _rerun(st, _CTX["run_id"], "stage-start", stage),
                 "rerun_stage_complete": _rerun(st, _CTX["run_id"], "stage-complete", stage,
                                                "--data @<file>")}
    _emit_fail({"ok": False, "stage": stage, "code": err.code, "error": err.message,
                "next": next_step, **extra,
                **{k: v for k, v in err.data.items() if v is not None}}, 1)


# ── stage-start (§5.1, §7.2) ─────────────────────────────────────────────────
def _persist(run_file: Path, run_state: dict, **extra) -> None:
    def m(d):
        d["worktrees"] = run_state.get("worktrees") or []
        d["worktree_mode"] = run_state.get("worktree_mode")
        d.update(extra)
    _save(run_file, m)


def _decide_and_create(*, run_id, stage, run_file, run_state, run_dir, workspace,
                       self_repo, extra: dict) -> str:
    """First plan/build/goal_cycle start: decide isolated vs shared once per run.

    Switch on → isolated (proven by the hook token). Worktrees are made for the
    project's bindings repos now (a hint: known up front); any other repo gets one on
    its first write (worktree_write_gate denies it and prints ``run-repo-add``, §16.9).
    A run with no bindings repo and no token falls back to shared: it has nothing
    isolated yet, and a hookless background executor must not fail every run."""
    if not rw.mode_enabled():
        run_state["worktree_mode"] = "shared"
        _persist(run_file, run_state)
        return "shared"
    repos = rw.code_repos(workspace, str(run_state.get("project") or ""), self_repo)
    if not repos and not os.environ.get(hook_token.TOKEN_ENV) \
            and not os.environ.get("SWARM_APP_SESSION_ID"):
        # truly hookless executor (no app session): nothing isolated yet, stay shared;
        # a hooked session whose gate failed open takes the refuse-and-retry path below
        run_state["worktree_mode"] = "shared"
        extra.setdefault("warnings", []).append(
            "worktree mode is on but this command carried no hook token, so this run uses "
            "the shared checkout (run stage-start as ONE command from a SwarmAI chat session "
            "to isolate it)")
        _persist(run_file, run_state)
        return "shared"
    ok, why = _token(run_dir, run_state, run_id, "stage-start")
    if not ok:
        # A hooked session's gate can fail open once (lock contention, a slow decision):
        # refuse and let it retry. A hookless executor never gets a token, so after
        # HOOKLESS_LIMIT refusals in a row the run fails with the reason (section 4.2).
        n = int(run_state.get("hookless_refusals") or 0) + 1
        reason = f"isolated run needs a session with hooks ({why})"
        if n >= HOOKLESS_LIMIT:
            _save(run_file, lambda d: d.update(status="failed", fail_reason=reason,
                                               worktree_mode="shared", hookless_refusals=n))
            _emit_fail({"ok": False, "stage": stage, "code": "hookless_session", "error": reason,
                        "next": "this run is failed; start it from a SwarmAI chat session"}, 1)
        _save(run_file, lambda d: d.update(hookless_refusals=n))
        _emit_fail({"ok": False, "stage": stage, "code": "hookless_session", "error": reason,
                    "next": "rerun this stage-start as ONE command (no `;`, `&&`, `|`): "
                            + _rerun(run_state, run_id, "stage-start", stage)
                            + f" — after {HOOKLESS_LIMIT} refusals the run fails"}, 1)
    created: list[dict] = []
    try:
        for _name, main in repos:
            created.append(rw.create(run_id, rw.repo_key(main, created), main,
                                     workspace=workspace))
    except rw.WorktreeError as err:
        for e in created:
            try:
                rw._remove(run_id, e, Path(e["main"]), merged=False)
            except rw.WorktreeError as rm_err:  # a fresh, empty worktree: report, never rmtree
                extra.setdefault("warnings", []).append(f"cleanup: {rm_err.message}")
        run_state["worktree_mode"] = "shared"
        extra.setdefault("warnings", []).append(
            f"worktree not created ({err.code}: {err.message}); this run uses the shared "
            "main checkout as before")
        _persist(run_file, run_state)
        return "shared"
    run_state["worktrees"] = created
    run_state["worktree_mode"] = "isolated"
    rw.stamp_workspace(run_id, workspace)  # WT_ROOT/<run_id>/ also arms the write gate
    _persist(run_file, run_state, cli=cli_record())
    return "isolated"


def on_stage_start(*, project: str, run_id: str, stage: str, run_file: Path,
                   run_state: dict, workspace, self_repo) -> dict:
    """Extra stage-start output for the worktree (may be {}); exits on a refusal."""
    _CTX.update(project=project, run_id=run_id)
    extra: dict = {}
    run_dir = run_file.parent
    if run_state.get("cli") != cli_record():   # the gates print re-run commands from it
        run_state["cli"] = cli_record()
        _save(run_file, lambda d: d.update(cli=run_state["cli"]))
    mode = run_state.get("worktree_mode")
    if mode == "isolated" and run_state.get("status") in TERMINAL:
        # Ended by someone else (cancel, reaper): refuse work; the owner releases (§8.2).
        res = None
        ok, _why = _token(run_dir, run_state, run_id, "stage-start")
        mains = mains_for(run_state, workspace, self_repo, run_id)
        if ok and mains:
            holder: dict = {}

            def m(d):
                holder["res"] = rw.release(run_id, d, mains, f"run {d.get('status')}")
                if all(e.get("state") == "released" for e in _entries(d)):
                    d.pop("release_requested", None)
            _save(run_file, m)
            res = holder.get("res")
        _emit_fail({"ok": False, "stage": stage, "code": "run_ended",
                    "error": f"run {run_id} is {run_state.get('status')}; no further work",
                    "worktree_release": res or "requested"}, 1)
    if mode is None and stage in WORKTREE_STAGES:
        mode = _decide_and_create(run_id=run_id, stage=stage, run_file=run_file,
                                  run_state=run_state, run_dir=run_dir, workspace=workspace,
                                  self_repo=self_repo, extra=extra)
    if mode != "isolated":
        return extra
    if run_state.get("status") == "running" and not _entries(run_state):
        rw.stamp_workspace(run_id, workspace)   # re-arm the write gate (idle runs are disarmed)
    if run_state.get("worktree_released_stale"):
        extra.update(_restore_released(run_id, stage, run_file, run_state, workspace, self_repo))
    try:
        wts = _validated(run_id, run_state, mains_for(run_state, workspace, self_repo, run_id))
    except rw.WorktreeError as err:
        stage_fail(stage, err, "the worktree no longer matches its git registration; tell the user")
    paths = [str(w) for _e, w, _m in wts]
    if stage in ("build", "test", "review", "goal_cycle", "deliver"):
        extra["worktrees"] = paths
        extra["worktree_rule"] = RULE.format(paths=", ".join(paths))
        if stage in CARD_OVERRIDES:
            extra["card_override"] = ("This overrides the stage card: "
                                      + CARD_OVERRIDES[stage])
    if stage in ("build", "test", "goal_cycle", "review"):
        left = {e["repo_name"]: rw.untracked_new_files(w, run_id) for e, w, _m in wts}
        left = {k: v for k, v in left.items() if v}
        if left:
            extra["untracked_new_files"] = left
            extra["untracked_note"] = ("un-added new files; before DELIVER each must be "
                                       "`git add`-ed (joins the run) or deleted")
    if stage == "deliver":
        extra.update(_deliver_sync(run_id, run_file, run_state, wts, run_dir))
    return extra


def _restore_released(run_id: str, stage: str, run_file: Path, run_state: dict,
                      workspace, self_repo) -> dict:
    """The sweep released this LIVE run's worktree after a long idle period (§8.3):
    the owner's next stage-start rebuilds it from the verified ledger snapshot."""
    ok, why = _token(run_file.parent, run_state, run_id, "stage-start")
    if not ok:
        stage_fail(stage, rw.WorktreeError("hookless_session", why),
                   "rerun this stage-start as ONE command from the session that owns the run: "
                   + _rerun(run_state, run_id, "stage-start", stage))
    mains = mains_for(run_state, workspace, self_repo, run_id)
    restored = []
    for e in _entries(run_state):
        if e.get("state") != "released" or not e.get("ledger"):
            continue  # integrated before the release: nothing to rebuild
        name = e.get("repo_name")
        try:
            if name not in mains:
                raise rw.WorktreeError("restore_failed", f"repo {name!r} is no longer a "
                                       "code-repo binding of this project", ledger=e["ledger"])
            new = rw.restore_from_ledger(run_id, e, mains[name], workspace=workspace)
        except rw.WorktreeError as err:
            _persist(run_file, run_state)
            stage_fail(stage, err, "tell the user: restore it by hand from the ledger README "
                                   f"({e.get('ledger')}), or abandon this run")
        run_state["worktrees"][run_state["worktrees"].index(e)] = new
        restored.append(name)
        _persist(run_file, run_state)
    run_state.pop("worktree_released_stale", None)
    _save(run_file, lambda d: d.pop("worktree_released_stale", None))
    return {"worktree_restored": restored,
            "restore_note": "this run's worktree was released after a long idle period and "
                            "has been rebuilt from its ledger snapshot (un-added files are "
                            "still un-added); rerun the test runner before deliver"} \
        if restored else {}


def _main_overlap(run_id: str, entry: dict, wt: Path, main: Path, since: str) -> "list[str]":
    """§7.2 step 1 (report only): main-checkout files with uncommitted changes newer
    than the run that the run also changes."""
    from datetime import datetime
    try:
        mine = set(rw.changed_paths(wt, entry["base"], rw.change_tree(wt, run_id)))
        out = rw._out(main, "status", "--porcelain", "-z", "--untracked-files=all")
        t0 = datetime.fromisoformat(str(since).replace("Z", "+00:00")).timestamp()
    except (rw.WorktreeError, ValueError, TypeError):
        return []
    hits = []
    for rec in out.split("\0"):
        rel = rec[3:] if len(rec) > 3 else ""
        if rel in mine:
            try:
                if os.lstat(main / rel).st_mtime > t0:
                    hits.append(rel)
            except OSError:
                continue
    return sorted(hits)


def _deliver_sync(run_id: str, run_file: Path, run_state: dict, wts, run_dir: Path) -> dict:
    ok, why = _token(run_dir, run_state, run_id, "stage-start")
    if not ok:
        stage_fail("deliver", rw.WorktreeError("hookless_session", why),
                   "run stage-start as ONE command from the session that owns the run: "
                   + _rerun(run_state, run_id, "stage-start", "deliver"))
    left = {e["repo_name"]: rw.untracked_new_files(w, run_id) for e, w, _m in wts}
    left = {k: v for k, v in left.items() if v}
    if left:
        stage_fail("deliver", rw.WorktreeError(
            "untracked_new_files", "un-added new files in the worktree", files=left),
            "for EACH file: `git add <path>` in the worktree if the run needs it (tests then "
            "rerun), or delete it; then rerun deliver stage-start")
    out: dict = {"sync": {}}
    overlap = {e["repo_name"]: _main_overlap(run_id, e, w, m, run_state.get("created_at"))
               for e, w, m in wts}
    overlap = {k: v for k, v in overlap.items() if v}
    if overlap:
        out["main_checkout_overlap"] = overlap
        out["main_checkout_overlap_note"] = (
            "the main checkout has uncommitted changes to these files, made after this run "
            "started — maybe writes the gate could not see, maybe another session's work. "
            "Do not move, copy or restore them yourself: landing merges them with the run's "
            "change when that merges cleanly (their bytes are backed up first, and the "
            "landing output names the files), and refuses otherwise.")
    rebased_any, changed_any, rd_files = False, False, []
    for e, w, m in wts:
        old_base = e.get("base")
        try:
            res = rw.sync(run_id, e, m)
        except rw.WorktreeError as err:
            _persist(run_file, run_state)
            nxt = ("resolve the conflicts in " + str(w) + ": edit each listed file, then "
                   "`git add` / `git rm` it there; rerun deliver stage-start"
                   if err.code == "conflict" else
                   "git add or delete the listed un-added files, then rerun deliver stage-start"
                   if err.code == "untracked_collision" else "tell the user")
            stage_fail("deliver", err, nxt)
        out["sync"][e["repo_name"]] = res["status"]
        if res["status"] == "rebased":
            rebased_any = True
            if res.get("patch_changed") and res.get("pre_sync_tip"):
                changed_any = True
                text = rw.range_diff(w, old_base, res["pre_sync_tip"], res["base"], run_id)
                f = run_dir / "evidence" / "prompts" / f"sync-range-diff-{e['repo_name']}.txt"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8")
                rd_files.append(str(f))
    _persist(run_file, run_state)
    # The reviewed tree + base, and WHEN it was recorded: landing requires every Gate-2
    # expert to have completed after that moment. A new tree gets a new moment (a
    # post-review fix always asks for a re-review) — unless it only carries the same
    # change onto a moved base (rw.carries_over: the run's files byte-identical, main's
    # new commits elsewhere), which keeps the moment; a stage-start rerun with no new
    # code changes nothing.
    import time as _time
    trees = {e["repo_name"]: rw.change_tree(w, run_id) for e, w, _m in wts}
    bases = {e["repo_name"]: e.get("base") for e, _w, _m in wts}
    rt = run_dir / "evidence" / "prompts" / "review-tree.json"
    rt.parent.mkdir(parents=True, exist_ok=True)
    rec = _review_record(run_dir)
    if rec.get("trees") != trees:
        if _review_carries(run_id, rec, wts, trees):
            # a rebase that left the run's own patch alone, onto commits touching none
            # of its files: the review and its timestamp still hold
            atomic_write_json(rt, {"run_id": run_id, "trees": trees, "bases": bases,
                                   "at": rec.get("at")})
            out["review_note"] = ("rebased with the run's patch unchanged: the Gate-2 "
                                  "review still holds")
        else:
            atomic_write_json(rt, {"run_id": run_id, "trees": trees, "bases": bases,
                                   "at": _time.time()})
            out["review_note"] = ("the change differs from the last reviewed tree: every "
                                  "Gate-2 expert must review it (again) before stage-complete")
    elif rec.get("bases") != bases:
        atomic_write_json(rt, {**rec, "bases": bases})
    if rebased_any and not changed_any and _tests_carry(run_id, run_dir, wts):
        out["sync_note"] = ("the integration branch moved and the change was rebased onto it; "
                            "the run's patch and files are untouched, so the last test run "
                            "still holds")
    elif rebased_any:
        out["sync_note"] = ("the integration branch moved and the change was rebased onto it: "
                            "rerun the pipeline test runner (verify) before stage-complete")
    if changed_any:
        out["sync_note"] += ("; the rebase changed the run's own hunks — the experts that "
                             "already reviewed must re-review the parts in " + ", ".join(rd_files))
    return out


# ── files_touched (§6.2) ─────────────────────────────────────────────────────
def derive_files_touched(run_id: str, run_state: dict, workspace, self_repo) -> "list[str] | None":
    """files_touched = the change set as worktree absolute paths + every recorded entry
    outside the run's worktrees (SwarmWS docs, unbound repos). None = not isolated."""
    if not is_isolated(run_state):
        return None
    mains = mains_for(run_state, workspace, self_repo, run_id)
    roots, derived = [], []
    for e in _entries(run_state):
        if e.get("state") != "active":
            continue
        wt = rw.wt_path(run_id, e["repo_name"])
        roots.append(str(wt) + os.sep)
        main = mains.get(e["repo_name"])
        if main is None or not wt.is_dir():
            continue
        try:
            rw.validate(run_id, e, main)
            derived += [str(wt / p) for p in rw.changed_paths(wt, e["base"], rw.change_tree(wt, run_id))]
        except rw.WorktreeError:
            continue
    keep = [f for f in run_state.get("files_touched") or []
            if isinstance(f, str) and not any(f.startswith(r) for r in roots)]
    return sorted(dict.fromkeys(keep + derived))


# ── landing (§7.3–§7.5) ──────────────────────────────────────────────────────
def _review_record(run_dir: Path) -> dict:
    try:
        d = json.loads((run_dir / "evidence" / "prompts" / "review-tree.json").read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _evidence_worktree(run_dir: Path, run_id: str) -> "tuple[bool, dict | None]":
    """(any evidence, its worktree block) of the NEWEST runner report of this run."""
    import pipeline_validator as pv
    ev = [e for e in pv.load_test_evidence(run_dir)
          if isinstance(e, dict) and e.get("run_id") in (None, run_id)]
    if not ev:
        return False, None
    return True, (ev[0].get("worktree") if isinstance(ev[0].get("worktree"), dict) else None)


def _review_carries(run_id: str, rec: dict, wts, trees: dict) -> bool:
    """The recorded review (tree + base per repo) still holds for the current trees."""
    rt, rb = rec.get("trees") or {}, rec.get("bases") or {}
    if not rec.get("at"):
        return False
    for e, w, _m in wts:
        name = e["repo_name"]
        if not rw.carries_over(w, run_id, rb.get(name), rt.get(name), e.get("base"), trees[name]):
            return False
    return True


def _tests_carry(run_id: str, run_dir: Path, wts) -> bool:
    """The newest test evidence still holds for every repo it tested."""
    have_ev, ev = _evidence_worktree(run_dir, run_id)
    if not have_ev or not ev:
        return False
    for e, w, _m in wts:
        t = ev.get(e["repo_name"])
        if not isinstance(t, dict):
            continue
        if t.get("untracked") or not rw.carries_over(
                w, run_id, t.get("base"), t.get("tree"), e.get("base"), rw.change_tree(w, run_id)):
            return False
    return True


def _check_bound_trees(run_id: str, run_dir: Path, wts) -> None:
    """§7.3: the tree to land == the reviewed tree == the tested tree — or carries the
    same change onto a moved base (rw.carries_over) — and the tests ran with no
    un-added file in the worktree."""
    rec = _review_record(run_dir)
    reviewed, rbases = rec.get("trees") or {}, rec.get("bases") or {}
    have_ev, ev = _evidence_worktree(run_dir, run_id)
    if have_ev and ev is None:
        raise rw.WorktreeError("changed_since_review", "the latest test evidence records no "
                               "worktree tree (unreadable or produced outside the worktree) "
                               "— rerun the test runner")
    for e, w, _m in wts:
        name = e["repo_name"]
        now = rw.change_tree(w, run_id)
        if reviewed.get(name) != now and not rw.carries_over(
                w, run_id, rbases.get(name), reviewed.get(name), e.get("base"), now):
            raise rw.WorktreeError("changed_since_review", f"{name}: the change differs from "
                                   "the tree recorded when Gate-2 prompts were written")
        tested = (ev or {}).get(name) if isinstance((ev or {}).get(name), dict) else None
        if tested is None:
            continue  # not the runner's target repo (multi-repo): the review binds it
        if tested.get("tree") != now and not rw.carries_over(
                w, run_id, tested.get("base"), tested.get("tree"), e.get("base"), now):
            raise rw.WorktreeError("changed_since_review", f"{name}: the latest test evidence "
                                   "was produced on a different tree — rerun the test runner")
        if tested.get("untracked"):
            raise rw.WorktreeError("changed_since_review", f"{name}: the latest tests ran with "
                                   "un-added files present: " + ", ".join(tested["untracked"][:8]))


def _current_entry(run_state: dict, name: str) -> "dict | None":
    return next((e for e in _entries(run_state) if e.get("repo_name") == name), None)


def land_deliver(*, run_id: str, run_file: Path, run_state: dict, push_ready: bool,
                 workspace, self_repo, gen_b, requirement: str,
                 precheck=None, message: "str | None" = None,
                 review_not_converged: bool = False,
                 not_ready: "list | str | None" = None) -> "dict | None":
    """Deliver stage-complete landing; None when nothing to do (not isolated / not
    push-ready / already integrated). Exits with a §7.7 error on refusal.
    *precheck(review_at)* → error strings (D7 coverage, experts that have not reviewed
    since the reviewed tree was recorded), checked before anything is committed.
    *review_not_converged*: the caller proved (artifact_cli._only_review_not_converged)
    that the ONLY thing keeping DELIVER from PUSH-READY is a Gate-2 loop that did not
    converge in 3 rounds with a SOUND root-cause verdict. The run is autonomous, so it
    lands anyway, the same as run-commit in shared mode (f2ffe9c2); every landed commit
    is marked review_not_converged and push_ready itself stays false (push is gated).
    Since 2026-10-19 every completed DELIVER lands (commit-ready is not push-ready):
    *not_ready* (the push_ready reason; a one-item list when *precheck* may still
    append to it) marks each landed commit push_ready: false."""
    if not is_isolated(run_state):
        return None
    _CTX.update(project=run_state.get("project"), run_id=run_id)
    run_dir = run_file.parent
    if run_state.get("worktree_released_stale"):
        stage_fail("deliver", rw.WorktreeError(
            "worktree_released", "the worktree was released to the ledger after a long idle "
            "period; nothing can land until it is rebuilt"), _NEXT["worktree_released"])
    _save(run_file, lambda d: d.update(landing_attempt_at=rw._now_iso()))
    mains = mains_for(run_state, workspace, self_repo, run_id)
    for e in _entries(run_state):   # a crash after merge, before the record: finish it
        m = mains.get(e.get("repo_name"))
        n = rw.landed_already(run_id, e, m) if e.get("state") == "active" and m else None
        if n:
            files = rw.changed_paths(rw.wt_path(run_id, e["repo_name"]), e["base"], n)
            e.update(state="integrated", integrated={"sha": n, "at": rw._now_iso(),
                                                     "recovered": True}, base=n)
            _record_landing(run_file, run_state, run_id, e["repo_name"], m,
                            {"sha": n, "files": files, "commit_s": None, "merge_s": None})
    try:
        wts = _validated(run_id, run_state, mains)
    except rw.WorktreeError as err:
        stage_fail("deliver", err, "the worktree no longer matches git; tell the user")
    if not wts:
        return None
    ok, why = _token(run_dir, run_state, run_id, "stage-complete")
    if not ok:
        stage_fail("deliver", rw.WorktreeError("hookless_session", why),
                   "run stage-complete as ONE command from the session that owns the run: "
                   + _rerun(run_state, run_id, "stage-complete", "deliver",
                            "--data @<file>"))
    try:
        for e, w, _m in wts:
            left = rw.untracked_new_files(w, run_id)
            if left:
                raise rw.WorktreeError("untracked_new_files", "un-added new files", files=left)
        _check_bound_trees(run_id, run_dir, wts)
        errs = precheck(float(_review_record(run_dir).get("at") or 0)) if precheck else []
        if errs:
            raise rw.WorktreeError("review_incomplete", "; ".join(errs)[:600])
    except rw.WorktreeError as err:
        stage_fail("deliver", err, _NEXT.get(err.code, "rerun deliver stage-start"))
    b_repo = run_state.get("deliverable_repo")
    if b_repo is None:  # chosen once, so a retry after a partial landing never writes B twice
        sizes = {e["repo_name"]: len(rw.changed_paths(w, e["base"], rw.change_tree(w, run_id)))
                 for e, w, _m in wts}
        b_repo = max(sizes, key=lambda k: (sizes[k], k)) if sizes else None
        _save(run_file, lambda d: d.update(deliverable_repo=b_repo))
        run_state["deliverable_repo"] = b_repo
    # One builder for both commit paths: artifact_cli passes the DELIVER-title message;
    # a caller without one gets the same builder's requirement fallback.
    if not message:
        import commit_message
        message = commit_message.build(run_id, requirement, None)
    msg = message
    landed = {}
    for name in sorted(e["repo_name"] for e, _w, _m in wts):
        e = _current_entry(run_state, name)   # run_state is reloaded after every landing
        m = mains[name]
        w = rw.validate(run_id, e, m)
        tree = rw.change_tree(w, run_id)
        files = rw.changed_paths(w, e["base"], tree)
        if not files:  # nothing changed in this repo: nothing to land, release later
            e.update(state="integrated", integrated={"sha": None, "at": rw._now_iso(),
                                                     "noop": True})
            _persist(run_file, run_state)
            continue
        res, resyncs = None, 0
        while res is None:
            try:
                res = rw.land(run_id, e, m, msg, tree, still_live=lambda: _still_live(run_file),
                              write_b=(lambda wt, _f=files: gen_b(wt, _f)) if name == b_repo else None)
            except rw.WorktreeError as err:
                if err.code == "needs_resync" and resyncs < LAND_RESYNC_TRIES:
                    resyncs += 1
                    try:
                        tree = _resync_for_landing(run_id, run_file, run_state, run_dir, e, w, m,
                                                   tree)
                    except rw.WorktreeError as err2:
                        err = err2
                    else:
                        files = rw.changed_paths(w, e["base"], tree)
                        continue
                _persist(run_file, run_state, **({"integration": "partial"} if landed else {}))
                nxt = _NEXT.get(err.code, "tell the user")
                if landed:
                    nxt = f"{', '.join(landed)} already landed; for {name}: {nxt}"
                stage_fail("deliver", err, nxt)
        if resyncs:
            res["resyncs"] = resyncs
        if review_not_converged and not push_ready:
            res["review_not_converged"] = True
        if not push_ready:
            res["push_ready"] = False
            _why = not_ready[0] if isinstance(not_ready, list) and not_ready else not_ready
            res["push_ready_reason"] = str(_why or "no reason recorded")[:500]
        landed[name] = res
        _record_landing(run_file, run_state, run_id, name, m, res)
    return {"landed": {k: {"sha": v["sha"], "files": len(v["files"]), "commit_s": v["commit_s"],
                           "merge_s": v["merge_s"],
                           **({"carried_uncommitted": v["carried_uncommitted"],
                               "carried_backup": v.get("carried_backup")}
                              if v.get("carried_uncommitted") else {}),
                           **({"resyncs": v["resyncs"]} if v.get("resyncs") else {}),
                           **({"review_not_converged": True}
                              if v.get("review_not_converged") else {})}
                       for k, v in landed.items()}}


LAND_RESYNC_TRIES = 5   # main moves every few minutes; each try is one rebase + ff


def _resync_for_landing(run_id: str, run_file: Path, run_state: dict, run_dir: Path,
                        e: dict, w: Path, m: Path, checked_tree: str) -> str:
    """The integration branch moved between the checks and the merge: rebase onto it
    and land without a new round, when the review and the tests still hold (the run's
    files are byte-identical and main's new commits touch none of them —
    rw.carries_over). *checked_tree* is the tree the review/test check accepted: the
    worktree must still hold exactly it, so nothing edited after the check can ride
    along. Returns the new change tree. Raises WorktreeError: 'conflict' from the
    rebase, or 'changed_since_review' (retest / re-review via deliver stage-start)."""
    old_base = e.get("base")
    if rw.change_tree(w, run_id) != checked_tree:
        raise rw.WorktreeError("changed_since_review", "the worktree changed after the "
                               "review and test check")
    res = rw.sync(run_id, e, m)
    _persist(run_file, run_state)
    new_tree = rw.change_tree(w, run_id)
    if res.get("status") == "noop":
        if new_tree != checked_tree:
            raise rw.WorktreeError("changed_since_review", "the worktree changed after the "
                                   "review and test check")
        return new_tree
    if not rw.carries_over(w, run_id, old_base, checked_tree, e.get("base"), new_tree):
        raise rw.WorktreeError("changed_since_review", "the integration branch moved onto "
                               "this run's files: the review and tests must be redone")
    rec = _review_record(run_dir)
    rec.setdefault("trees", {})[e["repo_name"]] = new_tree
    rec.setdefault("bases", {})[e["repo_name"]] = e.get("base")
    atomic_write_json(run_dir / "evidence" / "prompts" / "review-tree.json", rec)
    return new_tree


def _still_live(run_file: Path) -> bool:
    """Not ended and no cancel requested (the cancel sidecar is written lock-free)."""
    if (run_file.parent / CANCEL_SIDECAR).exists():
        return False
    try:
        return json.loads(run_file.read_text("utf-8")).get("status") in ("running", "paused")
    except (OSError, ValueError):
        return False


_NEXT = {
    "needs_resync": "rerun deliver stage-start (it syncs and says whether to retest or re-review)",
    "changed_since_review": "rerun deliver stage-start",
    "untracked_new_files": "for each file: `git add` it in the worktree or delete it; rerun "
                           "deliver stage-start",
    "blocked_by_uncommitted": "back off and rerun stage-complete; after 10 minutes in total, "
                              "checkpoint with the reason 'blocked by uncommitted changes in "
                              "<files>'",
    "busy": "back off and rerun stage-complete (10 minutes in total, then checkpoint)",
    "main_checkout_busy": "back off and rerun stage-complete (10 minutes in total, then checkpoint)",
    "commit_slow": "back off and rerun stage-complete (10 minutes in total, then checkpoint)",
    "path_rejected": "change the refused paths, or explain them and let the user decide",
    "conflict": "rerun deliver stage-start and resolve the conflicts",
    "review_incomplete": "spawn the listed experts on the current change (deliver "
                         "stage-start wrote their prompts), then rerun stage-complete. "
                         "Genuinely not enforceable? rerun with `--force --reason '<why>'` "
                         "(recorded as a d7_force_bypass intervention)",
    "run_not_live": "the run was cancelled; stop",
    "worktree_released": "rerun deliver stage-start (it rebuilds the worktree from the ledger "
                         "and syncs it), then the test runner, then stage-complete",
}


def _record_landing(run_file: Path, run_state: dict, run_id: str, name: str, main: Path,
                    res: dict) -> None:
    """§7.4 step 6: commits[] + files_touched worktree→main + entry integrated."""
    wt_prefix = str(rw.wt_path(run_id, name)) + os.sep

    def m(d: dict) -> None:
        d["worktrees"] = run_state.get("worktrees") or []
        commits = d.get("commits") or []
        if not any(isinstance(c, dict) and c.get("sha") == res["sha"][:12] for c in commits):
            c = {"repo": str(main), "sha": res["sha"][:12], "files": res["files"],
                 "landed": True, "commit_s": res["commit_s"], "merge_s": res["merge_s"]}
            if res.get("carried_uncommitted"):
                # another session's uncommitted edits, merged in place (backup kept)
                c["carried_uncommitted"] = res["carried_uncommitted"]
                c["carried_backup"] = res.get("carried_backup")
            if res.get("resyncs"):
                c["resyncs"] = res["resyncs"]
            if res.get("review_not_converged"):
                c["review_not_converged"] = True   # the shared-mode run-commit's mark
            if res.get("push_ready") is False:
                c["push_ready"] = False
                c["push_ready_reason"] = res.get("push_ready_reason")
            commits.append(c)
        d["commits"] = commits
        ft = d.get("files_touched") or []
        ft = [str(main) + os.sep + f[len(wt_prefix):] if isinstance(f, str)
              and f.startswith(wt_prefix) else f for f in ft]
        b = rw.deliverable_prefix(run_id)
        ft += [str(main / p) for p in res["files"] if not p.startswith(b)]
        d["files_touched"] = list(dict.fromkeys(ft))
        if all(e.get("state") != "active" for e in _entries(d)):
            d.pop("integration", None)
    _save(run_file, m)
    run_state.update(json.loads(run_file.read_text("utf-8")))


# ── run-commit (§7.1) ────────────────────────────────────────────────────────
def run_commit_isolated(run_state: dict) -> "tuple[bool, list[str] | None]":
    """(handled, remaining files). handled=True: print nothing more, run-commit is
    the idempotent confirmation. remaining: files OUTSIDE the worktrees that the old
    pathspec commit should still handle (SwarmWS docs, unbound repos)."""
    if not is_isolated(run_state):
        return False, None
    active = [e for e in _entries(run_state) if e.get("state") == "active"]
    if active:
        _emit_fail({"blocked": True, "error": "run-commit refused: this run's change lives in its "
                    "worktree and lands at deliver stage-complete (push_ready). Run "
                    "stage-complete --stage deliver; run-commit then confirms."}, 2)
    landed_roots = [c.get("repo") for c in run_state.get("commits") or []
                    if isinstance(c, dict) and c.get("landed")]
    rest = [f for f in run_state.get("files_touched") or [] if isinstance(f, str)
            and not any(f.startswith(str(r) + os.sep) for r in landed_roots if r)
            and rw.run_for_path(f) is None]
    if not rest:
        print(json.dumps({"committed": [c for c in run_state.get("commits") or []
                                        if isinstance(c, dict) and c.get("landed")],
                          "warnings": [], "pushed": False,
                          "note": "already landed at deliver stage-complete (idempotent)"},
                         indent=2))
        return True, None
    return False, rest


# ── completion and release (§8.2, §8.4) ──────────────────────────────────────
def completion_block(run_id: str, run_state: dict) -> "str | None":
    """Why ``--status completed`` is refused: an active worktree with a change, or a
    change released to the ledger after a long idle period and never integrated."""
    if not is_isolated(run_state):
        return None
    for e in _entries(run_state):
        if e.get("state") == "released" and e.get("ledger") and not e.get("integrated"):
            return (f"the change of {e.get('repo_name')} was saved to the ledger "
                    f"({e.get('ledger')}) and never integrated: rerun deliver stage-start (it "
                    "rebuilds the worktree), or end the run as failed/abandoned with a reason")
        if e.get("state") != "active":
            continue
        wt = rw.wt_path(run_id, e["repo_name"])
        try:
            changed = rw.changed_paths(wt, e["base"], rw.change_tree(wt, run_id)) \
                or rw.untracked_new_files(wt, run_id)
        except rw.WorktreeError:
            changed = True
        if changed:
            return (f"worktree {wt} holds an unintegrated change: land it (deliver "
                    "stage-complete; it lands even when not PUSH-READY) or end the run as failed/abandoned with "
                    "a reason (the change is then saved to the ledger)")
    return None


def reap_note(data: dict) -> None:
    """A non-owner writer that ends an isolated run only requests the release."""
    if is_isolated(data) and any(e.get("state") in ("active", "integrated") for e in _entries(data)):
        data["release_requested"] = True


def after_status_write(*, run_id: str, run_file: Path, workspace, self_repo) -> "dict | None":
    """Owner wrote a terminal status: release now if the hook token proves it is the
    owner's own hooked call, else leave ``release_requested`` for the daemon sweep."""
    try:
        data = json.loads(run_file.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not is_isolated(data) or data.get("status") not in TERMINAL:
        return None
    if not any(e.get("state") in ("active", "integrated") for e in _entries(data)):
        rw.drop_stamp_only_dir(run_id, workspace)   # never got a worktree: disarm the gate
        return None
    ok, why = _token(run_file.parent, data, run_id, "run-update")
    mains = mains_for(data, workspace, self_repo, run_id)
    if not ok or not mains:
        _save(run_file, lambda d: d.update(release_requested=True))
        return {"release": "requested", "why": why or "no code-repo binding"}
    out = {}

    def m(d: dict) -> None:
        out["results"] = rw.release(run_id, d, mains, f"run {d.get('status')}")
        if all(e.get("state") == "released" for e in _entries(d)):
            d.pop("release_requested", None)
        else:
            d["release_requested"] = True
    _save(run_file, m)
    return {"release": out.get("results")}


# ── run-diff (§6.3) and run-create sweep (§8.3) ──────────────────────────────
def cmd_run_diff(args, run_file: Path, workspace, self_repo) -> None:
    """``run-diff --mark NAME`` records an anchor; ``run-diff [--since NAME] [--stat]``
    prints the change set since that anchor (default: since base), per repo."""
    data = json.loads(run_file.read_text("utf-8"))
    if not is_isolated(data):
        _emit_fail({"ok": False, "error": "run-diff needs an isolated run (worktree mode); "
                    "use git diff in the main checkout"}, 2)
    mains = mains_for(data, workspace, self_repo, args.run_id)
    out: dict = {"ok": True}
    try:
        for e, w, m in _validated(args.run_id, data, mains):
            name = e["repo_name"]
            if args.mark:
                out.setdefault("marks", {})[name] = rw.mark(args.run_id, e, m, args.mark)
            else:
                out.setdefault("diff", {})[name] = rw.diff_since(args.run_id, e, m, args.since,
                                                                stat=args.stat)
    except rw.WorktreeError as err:
        _emit_fail({"ok": False, **err.as_dict()}, 1)
    if "diff" in out and not args.json:
        for name, text in out["diff"].items():
            print(f"# {name}\n{text}")
        return
    print(json.dumps(out, ensure_ascii=False))


def cmd_run_repo_add(args, run_file: Path, workspace, self_repo) -> None:
    """``run-repo-add --path P``: give the repo containing P a worktree of this isolated
    run (§16.9). Owner-only (hook token), under the run lock (LOCKED_COMMANDS);
    idempotent for a repo that already has one. Prints the worktree and its main."""
    data = json.loads(run_file.read_text("utf-8"))
    rid = args.run_id
    if not is_isolated(data) or data.get("status") in TERMINAL:
        _emit_fail({"ok": False, "code": "not_isolated", "error": f"run {rid} is not a live "
                    "isolated run (worktree mode decides at the first plan/build stage-start)"}, 2)
    if data.get("worktree_released_stale") or any(
            e.get("state") in ("integrated", "released") for e in _entries(data)):
        _emit_fail({"ok": False, "code": "run_settled", "error": f"run {rid} has landed or "
                    "released a worktree: no new worktree can join it now",
                    "next": "if a worktree was released after a long idle period, run "
                            + _rerun(data, rid, "stage-start", "<current stage>")
                            + " (it restores it); after landing, write in place"}, 2)
    main = rw.repo_for_write(args.path, workspace)
    if main is None:
        _emit_fail({"ok": False, "code": "not_isolatable", "error": f"{args.path} is not in a "
                    "git repo that can get a run worktree (not a repo, SwarmWS, a submodule "
                    "setup, a Brazil package, or a detached / mid-rebase main checkout): "
                    "write it in place"}, 2)
    mains = mains_for(data, workspace, self_repo, rid)
    for e in _entries(data):
        if e.get("state") == "active" and mains.get(e.get("repo_name")) == main:
            print(json.dumps({"ok": True, "worktree": str(rw.wt_path(rid, e["repo_name"])),
                              "main": str(main), "created": False}))
            return
    ok, why = _token(run_file.parent, data, rid, "run-repo-add")
    if not ok:
        import shlex
        _emit_fail({"ok": False, "code": "hookless_session", "error": why,
                    "next": "run it as ONE command from the session that owns the run: "
                            + self_cmd(f"run-repo-add --project {shlex.quote(str(data.get('project')))} "
                                       f"--run-id {rid} --path {shlex.quote(str(args.path))}")}, 1)
    resolved = [dict(e, main=str(mains[e["repo_name"]])) if e.get("repo_name") in mains else e
                for e in _entries(data)]
    key = rw.repo_key(main, resolved)
    try:
        if rw.wt_path(rid, key).exists() and rw.is_registered(main, rid, key):
            entry = rw.adopt(rid, key, main)   # left by a call killed before it was recorded
        else:
            entry = rw.create(rid, key, main, workspace=workspace)
    except rw.WorktreeError as err:
        _emit_fail({"ok": False, **err.as_dict()}, 1)

    def m(d: dict) -> None:
        d["worktrees"] = (d.get("worktrees") or []) + [entry]
        d["cli"] = cli_record()
    _save(run_file, m)
    wt = rw.wt_path(rid, entry["repo_name"])
    print(json.dumps({"ok": True, "worktree": str(wt), "main": str(main), "created": True,
                      "rule": f"edit {wt}/<path> instead of {main}/<path> for this repo; "
                              "new files need `git add` there"}))


def sweep_for_cli(workspace) -> "dict | None":
    """run-create's sweep: a CLI cannot see other sessions, so only runs owned by THIS
    session (or with no owner) are treated as having an inactive owner."""
    me = os.environ.get("SWARM_APP_SESSION_ID") or None
    try:
        return rw.sweep(workspace, owner_active=lambda sid: bool(sid) and sid != me,
                        release_orphans=False)
    except Exception as exc:  # noqa: BLE001 — housekeeping must never fail run-create
        return {"errors": [f"{type(exc).__name__}: {exc}"]}
