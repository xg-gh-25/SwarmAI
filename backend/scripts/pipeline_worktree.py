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
    "run-surface-changes",
})
WORKTREE_STAGES = ("plan", "build", "goal_cycle")
HOOKLESS_LIMIT = 3
TERMINAL = ("completed", "complete", "abandoned", "failed", "cancelled")
RULE = ("This run's code lives in its own worktree: edit files under {paths} (NOT the main "
        "checkout). New files need `git add <path>` in the worktree; do not commit there. "
        "Run the pipeline tools with the main checkout's absolute paths.")


def _emit_fail(payload: dict, code: int = 2) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))
    sys.exit(code)


def refuse_if_worktree_copy(script: str) -> None:
    """A tool copy inside a run worktree must never run (it could be half-edited)."""
    hit = rw.run_for_path(script)
    if hit is None:
        return
    rid, repo = hit
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
def mains_for(run_state: dict, workspace, self_repo) -> "dict[str, Path]":
    """repo_name → main checkout, re-derived from bindings (never from run.json)."""
    return dict(rw.code_repos(workspace, str(run_state.get("project") or ""), self_repo))


def is_isolated(run_state: dict) -> bool:
    return run_state.get("worktree_mode") == "isolated"


def _token(run_dir: Path, run_state: dict, run_id: str, sub: str) -> "tuple[bool, str]":
    return hook_token.consume(run_dir, os.environ.get(hook_token.TOKEN_ENV),
                              run_state.get("session_id"), run_id, sub)


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
            raise rw.WorktreeError("worktree_invalid", f"repo {e.get('repo_name')!r} is no "
                                   "longer a code-repo binding of this project")
        out.append((e, rw.validate(run_id, e, main), main))
    return out


def stage_fail(stage: str, err: "rw.WorktreeError", next_step: str) -> None:
    """One structured stage-boundary failure carrying the worktree error code (§7.7)."""
    _emit_fail({"ok": False, "stage": stage, "code": err.code, "error": err.message,
                "next": next_step, **{k: v for k, v in err.data.items() if v is not None}}, 1)


# ── stage-start (§5.1, §7.2) ─────────────────────────────────────────────────
def _persist(run_file: Path, run_state: dict, **extra) -> None:
    def m(d):
        d["worktrees"] = run_state.get("worktrees") or []
        d["worktree_mode"] = run_state.get("worktree_mode")
        d.update(extra)
    _save(run_file, m)


def _decide_and_create(*, run_id, stage, run_file, run_state, run_dir, workspace,
                       self_repo, extra: dict) -> str:
    """First plan/build/goal_cycle start: decide isolated vs shared once per run."""
    repos = rw.code_repos(workspace, str(run_state.get("project") or ""), self_repo) \
        if rw.mode_enabled() else []
    if not repos:
        run_state["worktree_mode"] = "shared"
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
                    "next": "rerun this stage-start as ONE command (no `;`, `&&`, `|`); "
                            f"after {HOOKLESS_LIMIT} refusals the run fails"}, 1)
    created: list[dict] = []
    try:
        for name, main in repos:
            created.append(rw.create(run_id, name, main, workspace=workspace))
    except rw.WorktreeError as err:
        mains = dict(repos)
        for e in created:
            try:
                rw._remove(run_id, e, mains[e["repo_name"]], merged=False)
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
    _persist(run_file, run_state)
    return "isolated"


def on_stage_start(*, project: str, run_id: str, stage: str, run_file: Path,
                   run_state: dict, workspace, self_repo) -> dict:
    """Extra stage-start output for the worktree (may be {}); exits on a refusal."""
    extra: dict = {}
    run_dir = run_file.parent
    mode = run_state.get("worktree_mode")
    if mode == "isolated" and run_state.get("status") in TERMINAL:
        # Ended by someone else (cancel, reaper): refuse work; the owner releases (§8.2).
        res = None
        ok, _why = _token(run_dir, run_state, run_id, "stage-start")
        mains = mains_for(run_state, workspace, self_repo)
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
    if run_state.get("worktree_released_stale"):
        stage_fail(stage, rw.WorktreeError(
            "worktree_released", "this run's worktree was released after a long idle period "
            "and saved to the ledger", ledger=[e.get("ledger") for e in _entries(run_state)]),
            "tell the user: restore it from the ledger README, or abandon this run")
    try:
        wts = _validated(run_id, run_state, mains_for(run_state, workspace, self_repo))
    except rw.WorktreeError as err:
        stage_fail(stage, err, "the worktree no longer matches its git registration; tell the user")
    paths = [str(w) for _e, w, _m in wts]
    if stage in ("build", "goal_cycle", "deliver"):
        extra["worktrees"] = paths
        extra["worktree_rule"] = RULE.format(paths=", ".join(paths))
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
                   "run stage-start as ONE command from the session that owns the run")
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
            "REPORT ONLY: do not move, copy or restore them; the user decides.")
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
    # The reviewed tree, and WHEN it was recorded: landing requires every Gate-2 expert
    # to have completed after that moment. Rewritten only when the tree changed, so a
    # stage-start rerun with no new code never asks for a re-review, and one WITH new
    # code (a post-review fix) always does.
    import time as _time
    trees = {e["repo_name"]: rw.change_tree(w, run_id) for e, w, _m in wts}
    rt = run_dir / "evidence" / "prompts" / "review-tree.json"
    rt.parent.mkdir(parents=True, exist_ok=True)
    if _review_record(run_dir).get("trees") != trees:
        atomic_write_json(rt, {"run_id": run_id, "trees": trees, "at": _time.time()})
        out["review_note"] = ("the change differs from the last reviewed tree: every Gate-2 "
                              "expert must review it (again) before stage-complete")
    if rebased_any:
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
    mains = mains_for(run_state, workspace, self_repo)
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


def _review_trees(run_dir: Path) -> dict:
    return _review_record(run_dir).get("trees") or {}


def _evidence_worktree(run_dir: Path, run_id: str) -> "tuple[bool, dict | None]":
    """(any evidence, its worktree block) of the NEWEST runner report of this run."""
    import pipeline_validator as pv
    ev = [e for e in pv.load_test_evidence(run_dir)
          if isinstance(e, dict) and e.get("run_id") in (None, run_id)]
    if not ev:
        return False, None
    return True, (ev[0].get("worktree") if isinstance(ev[0].get("worktree"), dict) else None)


def _check_bound_trees(run_id: str, run_dir: Path, wts) -> None:
    """§7.3: the tree to land == the reviewed tree == the tested tree, and the tests
    ran with no un-added file in the worktree."""
    reviewed = _review_trees(run_dir)
    have_ev, ev = _evidence_worktree(run_dir, run_id)
    if have_ev and ev is None:
        raise rw.WorktreeError("changed_since_review", "the latest test evidence records no "
                               "worktree tree (unreadable or produced outside the worktree) "
                               "— rerun the test runner")
    for e, w, _m in wts:
        name = e["repo_name"]
        now = rw.change_tree(w, run_id)
        if reviewed.get(name) != now:
            raise rw.WorktreeError("changed_since_review", f"{name}: the change differs from "
                                   "the tree recorded when Gate-2 prompts were written")
        tested = (ev or {}).get(name) if isinstance((ev or {}).get(name), dict) else None
        if tested is None:
            continue  # not the runner's target repo (multi-repo): the review binds it
        if tested.get("tree") != now:
            raise rw.WorktreeError("changed_since_review", f"{name}: the latest test evidence "
                                   "was produced on a different tree — rerun the test runner")
        if tested.get("untracked"):
            raise rw.WorktreeError("changed_since_review", f"{name}: the latest tests ran with "
                                   "un-added files present: " + ", ".join(tested["untracked"][:8]))


def _current_entry(run_state: dict, name: str) -> "dict | None":
    return next((e for e in _entries(run_state) if e.get("repo_name") == name), None)


def land_deliver(*, run_id: str, run_file: Path, run_state: dict, push_ready: bool,
                 workspace, self_repo, gen_b, requirement: str,
                 precheck=None) -> "dict | None":
    """Deliver stage-complete landing; None when nothing to do (not isolated / not
    push-ready / already integrated). Exits with a §7.7 error on refusal.
    *precheck(review_at)* → error strings (D7 coverage, experts that have not reviewed
    since the reviewed tree was recorded), checked before anything is committed."""
    if not is_isolated(run_state) or not push_ready:
        return None
    run_dir = run_file.parent
    mains = mains_for(run_state, workspace, self_repo)
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
                   "run stage-complete as ONE command from the session that owns the run")
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
    msg = (f"{(requirement or 'pipeline changes')[:72]}\n\n(auto local-commit, run {run_id}; "
           f"not pushed)\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
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
        try:
            res = rw.land(run_id, e, m, msg, tree, still_live=lambda: _still_live(run_file),
                          write_b=(lambda wt, _f=files: gen_b(wt, _f)) if name == b_repo else None)
        except rw.WorktreeError as err:
            _persist(run_file, run_state, **({"integration": "partial"} if landed else {}))
            nxt = _NEXT.get(err.code, "tell the user")
            if landed:
                nxt = f"{', '.join(landed)} already landed; for {name}: {nxt}"
            stage_fail("deliver", err, nxt)
        landed[name] = res
        _record_landing(run_file, run_state, run_id, name, m, res)
    return {"landed": {k: {"sha": v["sha"], "files": len(v["files"]), "commit_s": v["commit_s"],
                           "merge_s": v["merge_s"]} for k, v in landed.items()}}


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
                         "stage-start wrote their prompts), then rerun stage-complete",
    "run_not_live": "the run was cancelled; stop",
}


def _record_landing(run_file: Path, run_state: dict, run_id: str, name: str, main: Path,
                    res: dict) -> None:
    """§7.4 step 6: commits[] + files_touched worktree→main + entry integrated."""
    wt_prefix = str(rw.wt_path(run_id, name)) + os.sep

    def m(d: dict) -> None:
        d["worktrees"] = run_state.get("worktrees") or []
        commits = d.get("commits") or []
        if not any(isinstance(c, dict) and c.get("sha") == res["sha"][:12] for c in commits):
            commits.append({"repo": str(main), "sha": res["sha"][:12], "files": res["files"],
                            "landed": True, "commit_s": res["commit_s"], "merge_s": res["merge_s"]})
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
    """Why ``--status completed`` is refused (an active worktree with a change)."""
    if not is_isolated(run_state):
        return None
    for e in _entries(run_state):
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
                    "stage-complete with push_ready) or end the run as failed/abandoned with "
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
        return None
    ok, why = _token(run_file.parent, data, run_id, "run-update")
    mains = mains_for(data, workspace, self_repo)
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
    mains = mains_for(data, workspace, self_repo)
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


def sweep_for_cli(workspace) -> "dict | None":
    """run-create's sweep: a CLI cannot see other sessions, so only runs owned by THIS
    session (or with no owner) are treated as having an inactive owner."""
    me = os.environ.get("SWARM_APP_SESSION_ID") or None
    try:
        return rw.sweep(workspace, owner_active=lambda sid: bool(sid) and sid != me,
                        release_orphans=False)
    except Exception as exc:  # noqa: BLE001 — housekeeping must never fail run-create
        return {"errors": [f"{type(exc).__name__}: {exc}"]}
