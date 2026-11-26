"""The dispatching agent records that it landed a run's commits by hand, checked by git here.

When ``return-land`` answers a conflict, the agent lands the change itself in a session
worktree; ``swarm_out landed-by-hand`` then records it (route
``POST /api/swarm-out/runs/{id}/landed-by-hand``, core/swarm_out_settle.set_landed_by_hand,
core/hive_return.verify_hand_landing). Each test answers one question, with real git
repositories:

- does a hand landing whose commits are on the prepared branch, made after the run's base,
  and cover the returned files settle the run as accepted, recorded as the agent's, with
  the commits per repository and the why, as one decision entry, and does the funnel say so;
- is it refused, with nothing written, for a commit not on the branch, a commit older than
  the base, a commit touching none of the returned files, a returned file left uncovered
  (unless the agent names it dropped), a repository left out, a run not prepared here, a
  Swarm Run's child, another session, the person, an errored check, a malformed commit id;
- is a run whose returned files hold a person-only path (CI, git hooks, agent configuration)
  refused to the agent's hand landing as return-land refuses it, whether the agent names the
  path dropped, lands it in a commit it does not name, or the capped review does not show
  it, while the person's button still settles it;
- does the person's manual-landed button still settle what it settled before.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import (hive_bundle, hive_return, swarm_out, swarm_out_record as rc, swarm_out_results,
                  swarm_out_settle as st)

DID = "so_aaaaaaaaaaaa"
SID = "s-abc123"
ENDED = "2026-11-24T07:00:00Z"
URL = "https://github.com/acme/lib.git"
DIR = "Hive/work/x/repos/lib"
URL2 = "https://github.com/acme/app.git"
DIR2 = "Hive/work/x/repos/app"
AGENT = {"by": "agent", "session": SID, "why": "resolved the conflict in src/a.py and landed it"}
#: the run's src/a.py, and the agent's resolution of the conflict with main's edit: it keeps
#: the run's lines
RUN_A = "a = 1\n\ndef signer(key):\n    return key.strip()\n"
HAND_A = "a = 10\n\ndef signer(key):\n    return key.strip()\n"


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo: Path, files: dict, msg: str) -> str:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, name: str, did_dir: str, url: str, state: Path) -> dict:
    """A host checkout on main whose run returned commits: the Hive's commits at
    refs/hive/<dispatch>/<key>, made on a branch from the run's base, and main moved on
    since (so the commits would not land as they are); its return-prepare state saved."""
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    first = _commit(repo, {"src/a.py": "a = 0\n"}, "first")
    base = _commit(repo, {"src/a.py": "a = 1\n", "src/b.py": "b = 1\n"}, "start")
    git(repo, "checkout", "-q", "-b", "hive", base)
    tip = _commit(repo, {"src/a.py": RUN_A, "src/c.py": "c = 1\n"}, "the run's change")
    key = hive_bundle.key(did_dir)
    ref = f"refs/hive/{DID}/{key}"
    git(repo, "update-ref", ref, tip)
    git(repo, "checkout", "-q", "main")
    git(repo, "branch", "-q", "-D", "hive")
    other = _commit(repo, {"src/a.py": "a = 10\n"}, "other work on main")
    hive_return._save(DID, key, {
        "dispatch_id": DID, "url": url, "key": key, "repo": str(repo), "branch": "refs/heads/main",
        "ref": ref, "tip": tip, "base": base,
        "files": [{"path": "src/a.py", "added": 1, "deleted": 1, "protected": False},
                  {"path": "src/c.py", "added": 1, "deleted": 0, "protected": False}],
        "files_total": 2, "protected_files": [], "commits": [], "checkout_dirty": False,
        "prepared_at": ENDED, "status": "prepared"})
    return {"repo": repo, "first": first, "base": base, "tip": tip, "key": key, "url": url, "dir": did_dir,
            "other": other, "ref": ref}


@pytest.fixture
def world(tmp_path, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HOME", str(tmp_path))
    state = tmp_path / "state"
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: state)
    monkeypatch.setattr(swarm_out, "_refresh_soon", lambda: None)
    lib = _repo(tmp_path, "lib", DIR, URL, state)
    row = {"dispatch_id": DID, "hive_id": "h1", "hive": "explorer", "session_id": "hs1",
           "dispatched_from": SID, "title": "fix the signer",
           "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "repos": [{"url": URL, "ref": "main", "dir": DIR}],
           "project_agent": {"brain": "Lib", "returns": "commits", "delivery": "bundle"},
           "state": "stopped", "state_reason": "idle",
           "result": {"ended": "completed", "provisional": False, "ended_at": ENDED, "lessons": None,
                      "checks": [],
                      "work": {"repos": [{"dir": DIR, "url": URL, "commits_total": 1, "head": lib["tip"],
                                          "bundle": {"available": True}}]}}}
    w = {"row": row, "lib": lib, "tmp": tmp_path, "state": state, "checks": []}

    def log_row(dispatch_id):
        if dispatch_id != DID:
            raise swarm_out.SwarmOutError("no dispatch")
        return w["row"]
    monkeypatch.setattr(swarm_out, "_log_row_of", log_row)
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {
        "records": {"closed": True, "last_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "result": {**w["row"]["result"], "checks": w["checks"]}})

    async def run_row(dispatch_id):
        # the row as /runs has it: its stage and the actions it offers now
        r = dict(w["row"])
        r.update(st.derive(r, swarm_out_results.load(DID)))
        return r
    monkeypatch.setattr(swarm_out, "run_row", run_row)
    from routers.swarm_out import router
    app = FastAPI()
    app.include_router(router, prefix="/api/swarm-out")
    w["client"] = TestClient(app)
    return w


def _hand_land(w, files: dict, repo_key: str = "lib") -> str:
    """The agent's own landing on main: a commit after the base."""
    return _commit(w[repo_key]["repo"], files, "land the run by hand")


def _post(w, commits, dropped=None, body=None):
    payload = {**(body or AGENT), "commits": commits}
    if dropped is not None:
        payload["dropped"] = dropped
    return w["client"].post(f"/api/swarm-out/runs/{DID}/landed-by-hand", json=payload)


def _nothing_recorded():
    rec = st.load(DID)
    assert not rec.get("manual_landed_at") and not rec.get("manual_landed")
    assert not [e for e in rc.entries(rec) if e.get("command") == "landed_by_hand"]


def _derived(w):
    return st.derive(w["row"], swarm_out_results.load(DID))


# ── recorded ────────────────────────────────────────────────────────────────

def test_the_agent_records_a_hand_landing_and_the_run_settles_accepted(world):
    assert _derived(world)["code"] == "waiting"                    # the conflict left it unlanded
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    r = _post(world, [sha[:12]])
    assert r.status_code == 200, r.text
    rec = st.load(DID)
    assert rec["manual_landed_at"] and rec["manual_landed_by"] == "agent"
    assert rec["manual_landed"]["why"] == AGENT["why"]
    assert rec["manual_landed"]["repos"] == [{"url": URL, "key": world["lib"]["key"],
                                              "commits": [sha], "dropped": []}]
    hand = [e for e in rc.entries(rec) if e.get("command") == "landed_by_hand"]
    assert len(hand) == 1
    e = hand[0]
    assert (e["by"], e["session"], e["decision"], e["kind"], e["commits"], e["why"]) == (
        "agent", SID, "land", "by_hand", [sha], AGENT["why"])
    assert rc.judgment_pending(rec) is False                       # like the person's: no accept after
    d = _derived(world)
    assert d["code"] == "landed" and d["verdict"] == "pass" and d["stage"] == "accepted"
    assert d["settles"] and d["why_not"] == []
    assert st.closed_state(rec) == "accepted"
    # recorded once: a second one is refused
    again = _post(world, [sha])
    assert again.status_code in (400, 403)


def test_the_funnel_shows_the_hand_landing_and_its_commit(world):
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    assert _post(world, [sha]).status_code == 200
    gates = _derived(world)["funnel"]["gates"]
    land = next(g for g in gates if g["key"] == "land")
    assert (land["state"], land["by"], land["ref"], land.get("hand")) == ("done", "agent", sha, True)


def test_a_file_the_agent_says_it_dropped_need_not_be_touched(world):
    sha = _hand_land(world, {"src/a.py": HAND_A})
    r = _post(world, [sha])
    assert r.status_code == 400 and "src/c.py" in r.json()["detail"] and "--dropped" in r.json()["detail"]
    _nothing_recorded()
    # a dropped path the run did not return is refused
    r = _post(world, [sha], dropped=["src/nope.py"])
    assert r.status_code == 400 and "src/nope.py" in r.json()["detail"]
    _nothing_recorded()
    r = _post(world, [sha], dropped=["src/c.py"])
    assert r.status_code == 200, r.text
    assert st.load(DID)["manual_landed"]["repos"][0]["dropped"] == ["src/c.py"]


def test_several_commits_together_cover_the_returned_files(world):
    one = _hand_land(world, {"src/a.py": HAND_A})
    two = _hand_land(world, {"src/c.py": "c = 1\n"})
    r = _post(world, [one, two])
    assert r.status_code == 200, r.text
    assert st.load(DID)["manual_landed"]["repos"][0]["commits"] == [one, two]


# ── refused, nothing written ────────────────────────────────────────────────

def test_a_commit_not_on_the_branch_is_refused(world):
    repo = world["lib"]["repo"]
    git(repo, "checkout", "-q", "-b", "side")
    sha = _commit(repo, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"}, "not on main")
    git(repo, "checkout", "-q", "main")
    r = _post(world, [sha])
    assert r.status_code == 400 and "not on main" in r.json()["detail"]
    _nothing_recorded()
    # nor a commit this repository does not have
    r = _post(world, ["0123456789abcdef0123456789abcdef01234567"])
    assert r.status_code == 400 and "not a commit" in r.json()["detail"]
    _nothing_recorded()


def test_a_commit_older_than_the_base_is_refused(world):
    for old in (world["lib"]["base"], world["lib"]["first"]):
        r = _post(world, [old])
        assert r.status_code == 400 and "older than the run's base" in r.json()["detail"], r.text
    _nothing_recorded()


def test_a_commit_touching_none_of_the_returned_files_is_refused(world):
    unrelated = _hand_land(world, {"docs/notes.md": "unrelated\n"})
    r = _post(world, [unrelated])
    assert r.status_code == 400 and "none of the files" in r.json()["detail"]
    _nothing_recorded()
    # named next to a real landing, the unrelated one is still refused
    real = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    r = _post(world, [real, unrelated])
    assert r.status_code == 400 and unrelated[:12] in r.json()["detail"]
    _nothing_recorded()


def test_a_commit_that_edits_a_returned_file_without_the_runs_change_is_refused(world):
    # main's own edit to src/a.py, the one the conflict was with: on main, after the base,
    # touching a returned file, but it is not the run's change
    r = _post(world, [world["lib"]["other"]], dropped=["src/c.py"])
    assert r.status_code == 400 and "leaves none of them holding the run's whole change" in r.json()["detail"]
    _nothing_recorded()
    # nor a later edit of the same file that drops the run's lines
    sha = _hand_land(world, {"src/a.py": "a = 11\n", "src/c.py": "c = 1\n"})
    r = _post(world, [sha])
    assert r.status_code == 400 and "src/a.py" in r.json()["detail"]
    assert "leaves the run's whole change" in r.json()["detail"]
    _nothing_recorded()


def _new_run_change(world, files: dict) -> str:
    """The run's change is *files* from the base instead (its state and ref follow)."""
    lib, repo = world["lib"], world["lib"]["repo"]
    git(repo, "checkout", "-q", "--detach", lib["base"])
    tip = _commit(repo, files, "the run's change")
    git(repo, "checkout", "-q", "main")
    git(repo, "update-ref", lib["ref"], tip)
    hive_return._save(DID, lib["key"], {**hive_return.load(DID, lib["key"]), "tip": tip})
    world["row"]["result"]["work"]["repos"][0]["head"] = tip         # the result names the run's tip
    return tip


def test_a_commit_sharing_only_a_common_line_with_the_run_is_refused(world):
    # the run and main's own edit of the same function share only "else:" and "return v"
    _new_run_change(world, {"src/a.py": "def f(v):\n    if v:\n        return v.strip()\n    else:\n"
                                         "        return None\n"})
    other = _hand_land(world, {"src/a.py": "def f(v):\n    if v is None:\n        return ''\n    else:\n"
                                           "        return v\n"})
    r = _post(world, [other])
    assert r.status_code == 400 and "src/a.py" in r.json()["detail"], r.text
    _nothing_recorded()
    # the resolution that keeps the run's lines (main's guard kept too) is the landing
    sha = _hand_land(world, {"src/a.py": "def f(v):\n    if v is None:\n        return ''\n    if v:\n"
                                         "        return v.strip()\n    else:\n        return None\n"})
    # named next to it, main's own edit is still not part of the landing
    r = _post(world, [other, sha])
    assert r.status_code == 400 and other[:12] in r.json()["detail"], r.text
    _nothing_recorded()
    # nor is a later edit of the file that only keeps the landed lines
    later = _hand_land(world, {"src/a.py": "def f(v):\n    if v is None:\n        return ''\n    if v:\n"
                                           "        return v.strip()\n    else:\n        return None\n# done\n"})
    r = _post(world, [sha, later])
    assert r.status_code == 400 and later[:12] in r.json()["detail"], r.text
    assert "already held the run's whole change before" in r.json()["detail"]
    _nothing_recorded()
    assert _post(world, [sha]).status_code == 200
    assert st.load(DID)["manual_landed"]["repos"][0]["commits"] == [sha]


def test_a_merge_of_main_into_the_runs_side_is_the_landing(world):
    # the agent merges main into the run's commits, resolves, and fast-forwards main to it:
    # the merge's first parent (the run's tip) already holds the change, main does not
    lib, repo = world["lib"], world["lib"]["repo"]
    git(repo, "checkout", "-q", "-b", "landing", lib["tip"])
    p = subprocess.run(["git", "merge", "-q", "--no-edit", "main"], cwd=str(repo), capture_output=True, text=True)
    assert p.returncode != 0                                     # the conflict
    (repo / "src" / "a.py").write_text(HAND_A)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-edit")
    merge = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--ff-only", "landing")
    r = _post(world, [merge])
    assert r.status_code == 200, r.text
    assert st.load(DID)["manual_landed"]["repos"][0]["commits"] == [merge]


def test_a_line_the_base_already_had_does_not_count_for_the_run(world):
    # the run writes a second "b = 1" into src/b.py, which has one at the base
    _new_run_change(world, {"src/b.py": "b = 1\nb = 1\n"})
    other = _hand_land(world, {"src/b.py": "b = 1\nz = 0\n"})       # keeps only the base's copy
    r = _post(world, [other])
    assert r.status_code == 400 and "src/b.py" in r.json()["detail"], r.text
    _nothing_recorded()
    sha = _hand_land(world, {"src/b.py": "b = 1\nb = 1\nz = 0\n"})
    assert _post(world, [sha]).status_code == 200


def test_a_submodule_bump_is_compared_by_its_commit(world):
    lib, repo = world["lib"], world["lib"]["repo"]

    def bump(to: str, msg: str) -> str:
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{to},vendor/sub")
        git(repo, "commit", "-q", "-m", msg)
        return git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "--detach", lib["base"])
    tip = bump(lib["first"], "the run bumps the submodule")
    git(repo, "checkout", "-q", "main")
    git(repo, "update-ref", lib["ref"], tip)
    hive_return._save(DID, lib["key"], {**hive_return.load(DID, lib["key"]), "tip": tip})
    world["row"]["result"]["work"]["repos"][0]["head"] = tip         # the result names the run's tip
    wrong = bump(lib["base"], "main points it elsewhere")
    r = _post(world, [wrong])
    assert r.status_code == 400 and "vendor/sub" in r.json()["detail"], r.text
    _nothing_recorded()
    right = bump(lib["first"], "the run's bump, by hand")
    r = _post(world, [right])
    assert r.status_code == 200, r.text


def _second_repo(world, landed=False):
    app = _repo(world["tmp"], "app", DIR2, URL2, world["state"])
    world["app"] = app
    if landed:
        st2 = hive_return.load(DID, app["key"])
        hive_return._save(DID, app["key"], {**st2, "status": "landed", "landed": app["tip"],
                                            "landed_tip": app["tip"]})
    world["row"]["repos"].append({"url": URL2, "ref": "main", "dir": DIR2})
    world["row"]["result"]["work"]["repos"].append({"dir": DIR2, "url": URL2, "commits_total": 1,
                                                     "head": app["tip"], "bundle": {"available": True}})
    return app


def test_a_dropped_path_several_repositories_returned_names_its_repository(world):
    _second_repo(world)
    one = _hand_land(world, {"src/a.py": HAND_A})
    two = _hand_land(world, {"src/a.py": HAND_A + "# app\n"}, repo_key="app")
    r = _post(world, [one, two], dropped=["src/c.py"])
    assert r.status_code == 400 and "more than one repository" in r.json()["detail"]
    _nothing_recorded()
    r = _post(world, [one, two], dropped=[f"{URL}::src/c.py"])
    assert r.status_code == 400 and URL2 in r.json()["detail"]          # app's c.py is not dropped
    _nothing_recorded()
    r = _post(world, [one, two], dropped=[f"{URL}::src/c.py", f"{URL2}::src/c.py"])
    assert r.status_code == 200, r.text
    assert [x["dropped"] for x in st.load(DID)["manual_landed"]["repos"]] == [["src/c.py"], ["src/c.py"]]


def test_a_path_is_kept_as_git_names_it(world):
    # the run also returned a file whose name has two spaces in a row
    lib, repo = world["lib"], world["lib"]["repo"]
    git(repo, "checkout", "-q", "--detach", lib["tip"])
    tip = _commit(repo, {"src/two  spaces.py": "x = 1\n", "src/a::b.py": "y = 1\n"}, "one more")
    git(repo, "checkout", "-q", "main")
    git(repo, "update-ref", lib["ref"], tip)
    state = hive_return.load(DID, lib["key"])
    hive_return._save(DID, lib["key"], {**state, "tip": tip})
    world["row"]["result"]["work"]["repos"][0]["head"] = tip
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    r = _post(world, [sha])
    assert r.status_code == 400 and "src/two  spaces.py" in r.json()["detail"]
    # a name that holds "::" is a path, or scoped by the repository's url before it
    r = _post(world, [sha], dropped=["src/two  spaces.py", f"{URL}::src/a::b.py"])
    assert r.status_code == 200, r.text
    assert st.load(DID)["manual_landed"]["repos"][0]["dropped"] == ["src/a::b.py", "src/two  spaces.py"]


def test_an_unscoped_path_holding_the_scope_mark_is_still_a_path(world):
    lib, repo = world["lib"], world["lib"]["repo"]
    git(repo, "checkout", "-q", "--detach", lib["tip"])
    tip = _commit(repo, {"src/a::b.py": "y = 1\n"}, "one more")
    git(repo, "checkout", "-q", "main")
    git(repo, "update-ref", lib["ref"], tip)
    hive_return._save(DID, lib["key"], {**hive_return.load(DID, lib["key"]), "tip": tip})
    world["row"]["result"]["work"]["repos"][0]["head"] = tip         # the result names the run's tip
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    r = _post(world, [sha], dropped=["src/a::b.py"])
    assert r.status_code == 200, r.text


def test_a_hand_landing_does_not_judge_another_repositorys_return_land(world):
    _second_repo(world, landed=True)
    rc.append(DID, "agent", "land", "landed app with return-land", SID, "return_land")
    assert rc.judgment_pending(st.load(DID)) is True
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    assert _post(world, [sha]).status_code == 200
    rec = st.load(DID)
    assert rec["manual_landed_by"] == "agent"
    assert rc.judgment_pending(rec) is True             # app's landing still waits for accept
    d = _derived(world)
    assert d["code"] == "landed" and d["verdict"] is None and d["why"] == "judge"


def test_a_repository_left_out_is_refused(world):
    app = _repo(world["tmp"], "app", DIR2, URL2, world["state"])
    world["app"] = app
    world["row"]["repos"].append({"url": URL2, "ref": "main", "dir": DIR2})
    world["row"]["result"]["work"]["repos"].append({"dir": DIR2, "url": URL2, "commits_total": 1,
                                                     "head": app["tip"], "bundle": {"available": True}})
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    r = _post(world, [sha])
    assert r.status_code == 400 and "no commit you named landed " + URL2 in r.json()["detail"]
    _nothing_recorded()
    other = _hand_land(world, {"src/a.py": HAND_A + "# app\n", "src/c.py": "c = 1\n"}, repo_key="app")
    r = _post(world, [sha, other])
    assert r.status_code == 200, r.text
    assert [(x["url"], x["commits"]) for x in st.load(DID)["manual_landed"]["repos"]] == [
        (URL, [sha]), (URL2, [other])]


def test_a_repository_already_landed_by_return_land_needs_no_commit(world):
    app = _repo(world["tmp"], "app", DIR2, URL2, world["state"])
    st2 = hive_return.load(DID, app["key"])
    hive_return._save(DID, app["key"], {**st2, "status": "landed", "landed": app["tip"],
                                        "landed_tip": app["tip"]})
    world["row"]["repos"].append({"url": URL2, "ref": "main", "dir": DIR2})
    world["row"]["result"]["work"]["repos"].append({"dir": DIR2, "url": URL2, "commits_total": 1,
                                                     "head": app["tip"], "bundle": {"available": True}})
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    r = _post(world, [sha])
    assert r.status_code == 200, r.text
    assert [x["url"] for x in st.load(DID)["manual_landed"]["repos"]] == [URL]


def test_a_run_not_prepared_here_is_refused(world):
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    (world["state"] / "returns" / DID / f"{world['lib']['key']}.json").unlink()
    r = _post(world, [sha])
    assert r.status_code == 400 and "not prepared" in r.json()["detail"] and URL in r.json()["detail"]
    _nothing_recorded()
    # commits the Hive never sealed into a bundle are only there: not prepared here either
    world["row"]["result"]["work"]["repos"] = [{"dir": DIR, "url": URL, "commits_total": 2}]
    r = _post(world, [sha])
    assert r.status_code in (400, 403)
    assert "not prepared" in r.json()["detail"] or "does not offer" in r.json()["detail"]
    _nothing_recorded()


def _later_turn(world, repo_key="lib") -> str:
    """A re-run's later turn returned one more commit on top of the prepared tip: the
    result names it now, and nobody took it back."""
    x, repo = world[repo_key], world[repo_key]["repo"]
    git(repo, "checkout", "-q", "--detach", x["tip"])
    later = _commit(repo, {"src/d.py": "d = 1\n"}, "a later turn")
    git(repo, "checkout", "-q", "main")
    head = next(r for r in world["row"]["result"]["work"]["repos"] if r["dir"] == x["dir"])
    head["head"] = later
    return later


def test_an_earlier_turns_take_back_does_not_record_a_later_turn_landed_by_hand(world):
    """Turn 1 was prepared and its return-land conflicted; turn 2 then returned more commits.
    Landing turn 1's change by hand does not settle the run: turn 2's were never reviewed."""
    _later_turn(world)
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    r = _post(world, [sha])
    assert r.status_code == 400 and "earlier turn" in r.json()["detail"] and URL in r.json()["detail"]
    _nothing_recorded()
    assert _derived(world)["code"] == "waiting"


def test_an_earlier_turns_return_land_is_not_counted_as_handled(world):
    """A repository landed by return-land in turn 1 whose later turn returned more is not
    handled already: the hand landing of the other repository does not settle the run."""
    _second_repo(world, landed=True)
    _later_turn(world, "app")
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    r = _post(world, [sha])
    assert r.status_code == 400 and "earlier turn" in r.json()["detail"] and URL2 in r.json()["detail"]
    _nothing_recorded()


def test_the_route_refuses_a_child_another_session_and_the_person(world, monkeypatch):
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    r = _post(world, [sha], body={**AGENT, "session": "s-other"})
    assert r.status_code == 403 and "another session" in r.json()["detail"]
    r = _post(world, [sha], body={"by": "person", "why": "w"})
    assert r.status_code == 403
    r = _post(world, [sha], body={**AGENT, "why": ""})
    assert r.status_code == 403 and "say why" in r.json()["detail"]
    _nothing_recorded()
    from core import swarm_run
    world["row"]["parent_run"] = "run_0123abcd"
    monkeypatch.setattr(swarm_run, "lead", lambda run_id: None)
    r = _post(world, [sha])
    assert r.status_code == 403 and "child of Swarm Run" in r.json()["detail"]
    _nothing_recorded()


def test_an_errored_check_refuses_a_hand_landing_as_it_refuses_return_land(world):
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    world["checks"] = [{"id": "tests", "status": "errored"}]
    r = _post(world, [sha])
    assert r.status_code == 403 and "errored" in r.json()["detail"]
    _nothing_recorded()


@pytest.mark.parametrize("bad", ["--output=/tmp/x", "HEAD", "main", "abc", "g" * 40, ""])
def test_a_commit_id_must_be_hex(world, bad):
    r = _post(world, [bad])
    assert r.status_code == 400 and "not a commit id" in r.json()["detail"] and "hex" in r.json()["detail"]
    _nothing_recorded()


def test_no_commit_named_is_refused(world):
    r = _post(world, [])
    assert r.status_code == 400 and "--commit" in r.json()["detail"]
    _nothing_recorded()


# ── person-only files ───────────────────────────────────────────────────────

CI = ".github/workflows/ci.yml"


def _ci_run(world) -> str:
    """The run's change also writes a CI pipeline (hive_return.PERSON_ONLY), and its
    return-prepare state lists it as protected, as ``prepare`` does."""
    tip = _new_run_change(world, {"src/a.py": RUN_A, "src/c.py": "c = 1\n", CI: "on: push\n"})
    key = world["lib"]["key"]
    state = hive_return.load(DID, key)
    hive_return._save(DID, key, {**state, "files_total": 3, "protected_files": [CI],
                                 "files": [*state["files"], {"path": CI, "added": 1, "deleted": 0,
                                                             "protected": True}]})
    return tip


def test_an_agent_may_not_land_person_only_files_by_hand_after_return_land_refused_them(world):
    # the release check's reproduction: the agent's land refuses the CI file, the agent
    # cherry-picks the run's commit onto main itself and records it as landed by hand
    tip = _ci_run(world)
    with pytest.raises(hive_return.ReturnError, match=r"\.github/workflows/ci\.yml"):
        hive_return.land(DID, world["lib"]["key"], tip, allow_git_exec=False)
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n", CI: "on: push\n"})
    r = _post(world, [sha])
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert CI in detail and "only a person" in detail and "land them by hand" not in detail
    _nothing_recorded()
    assert _derived(world)["code"] == "waiting"                    # still the person's to land


def test_declaring_a_landed_person_only_file_dropped_does_not_let_the_agent_land_it(world):
    _ci_run(world)
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n", CI: "on: push\n"})
    r = _post(world, [sha], dropped=[CI])
    assert r.status_code == 400 and CI in r.json()["detail"] and "only a person" in r.json()["detail"], r.text
    _nothing_recorded()


def test_a_person_only_file_landed_in_a_commit_not_named_is_still_refused(world):
    # the CI file goes on main in a commit of its own the agent does not name, the run's
    # other files in the one it names, and it says it dropped the CI file
    _ci_run(world)
    _hand_land(world, {CI: "on: push\n"})
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    r = _post(world, [sha], dropped=[CI])
    assert r.status_code == 400 and CI in r.json()["detail"] and "only a person" in r.json()["detail"], r.text
    _nothing_recorded()


def test_a_person_only_file_left_out_on_purpose_still_leaves_the_run_to_the_person(world):
    # the agent lands only the ordinary files and drops the CI one: the run returned it, so
    # the run is still the person's to land (or a re-run without it), as return-land holds it
    _ci_run(world)
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n"})
    r = _post(world, [sha], dropped=[CI])
    assert r.status_code == 400 and CI in r.json()["detail"] and "only a person" in r.json()["detail"], r.text
    _nothing_recorded()


def test_a_person_only_file_the_capped_review_does_not_show_is_still_refused(world):
    # the review lists fewer files than the commits change (and this one is not marked):
    # the hand landing reads base..tip whole
    _ci_run(world)
    key = world["lib"]["key"]
    state = hive_return.load(DID, key)
    hive_return._save(DID, key, {**state, "protected_files": [],
                                 "files": [f for f in state["files"] if f["path"] != CI]})
    sha = _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n", CI: "on: push\n"})
    r = _post(world, [sha])
    assert r.status_code == 400 and CI in r.json()["detail"] and "only a person" in r.json()["detail"], r.text
    _nothing_recorded()


def test_the_person_lands_the_person_only_files_by_hand_from_the_run_card(world):
    _ci_run(world)
    _hand_land(world, {"src/a.py": HAND_A, "src/c.py": "c = 1\n", CI: "on: push\n"})
    r = world["client"].post(f"/api/swarm-out/runs/{DID}/manual-landed", json={"by": "person"})
    assert r.status_code == 200, r.text
    assert st.load(DID)["manual_landed_at"]
    assert _derived(world)["code"] == "landed"


# ── the person's button ─────────────────────────────────────────────────────

def test_the_persons_manual_landed_still_settles_and_refuses_nothing_new(world):
    # not prepared here, nothing landed on main: the person's word is enough, as before
    (world["state"] / "returns" / DID / f"{world['lib']['key']}.json").unlink()
    r = world["client"].post(f"/api/swarm-out/runs/{DID}/manual-landed", json={"by": "person"})
    assert r.status_code == 200, r.text
    rec = st.load(DID)
    assert rec["manual_landed_at"] and "manual_landed_by" not in rec and "manual_landed" not in rec
    d = _derived(world)
    assert d["code"] == "landed" and d["verdict"] == "pass"
    land = next(g for g in d["funnel"]["gates"] if g["key"] == "land")
    assert (land["by"], land.get("hand")) == ("person", True)


def test_the_agent_still_may_not_say_manual_landed(world):
    r = world["client"].post(f"/api/swarm-out/runs/{DID}/manual-landed", json=AGENT)
    assert r.status_code == 403 and "landed-by-hand" in r.json()["detail"]
    _nothing_recorded()


# ── the skill says it ───────────────────────────────────────────────────────

def test_the_skill_says_to_record_a_hand_landing_never_to_discard_it():
    text = (Path(__file__).resolve().parents[1] / "skills" / "s_swarm-out" / "SKILL.md").read_text()
    assert "landed-by-hand <dispatch_id> --commit" in text
    i = text.index("When `return-land` answers a conflict")
    para = text[i:i + 1500]
    assert "session_worktree" in para and "landed-by-hand" in para
    assert "never `discard` a run whose code is on main" in para
