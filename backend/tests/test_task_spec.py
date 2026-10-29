"""From an Intent to a dispatch under a Project Agent (design
docs/design/2026-10-28-project-agent-flow.md §7 ①–④, ⑥; core/task_spec, the
dispatch in core/swarm_out, routes and CLI).

Questions: does routing use the deterministic signals first and ask (with "not part
of any Brain") otherwise; does the spec take mode, profile, repositories and rules from
the definition and refuse a choice that does not fit; does admission name each rule
it refuses on; does the project section tell the Hive how to reach the Brain's
knowledge and what done is; does a dispatch sync a Hive's out-of-date copy, pin the
session and record the spec; do the route and the CLI require a Brain.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from core import task_spec as ts

DEF = {
    "schema": 2,
    "status": "reviewed",
    "identity": {"summary": "Signing library"},
    "intake": {"handles": ["fix a signer bug"], "repos": ["lib"]},
    "watches": [{"area": "security", "why": "constant-time compare"}],
    "gates": {"enforce": ["no_git_push"]},
    "reserved_for_human": ["push", "open_pr", "publish_release", "send_external_message"],
    "placements": ["chat-tab", "hive"],
    "landing": {"approval": "human"},
    "modes": [
        {"id": "code-change", "when": "change the library code", "profile": "bugfix",
         "profile_rules": [{"when": "a public API change", "profile": "full"}],
         "placements": ["chat-tab", "hive"], "repos": ["lib"],
         "setup": [{"command": "uv sync", "source": "ci.yml", "repo": "lib"}],
         "validations": [{"id": "tests", "command": "pytest -q", "source": "ci.yml", "repo": "lib"}],
         "skills": ["s_lib-release", "s_lib-desk"], "delivery": "bundle"},
        {"id": "research", "when": "a question with no code change", "profile": "research"},
        {"id": "release", "when": "cut a release", "profile": "direct", "placements": ["chat-tab"]},
    ],
    "default_mode": "code-change",
}

BINDINGS = """\
bindings:
  - repo: lib
    kind: external
    clone: "https://github.com/acme/lib.git"
    delivery_contract: {remote_kind: github-pr, branch: main, review_path: github-pr, auto_send: manual-push}
"""


def _brain(ws: Path, name: str = "Lib", doc: dict | None = None, check: bool = True) -> Path:
    from core.project_agent import check_sources, revision
    b = ws / "Projects" / name
    for s, plat in (("s_lib-release", None), ("s_lib-desk", "desktop")):
        (b / "4-capabilities" / s).mkdir(parents=True, exist_ok=True)
        (b / "4-capabilities" / s / "SKILL.md").write_text(
            f"---\nname: {s}\n" + (f"platform: {plat}\n" if plat else "") + "---\nx\n")
    (b / "3-gates").mkdir(exist_ok=True)
    (b / "3-gates" / "no_git_push.py").write_text("import sys\nsys.exit(0)\n")
    (b / "2-understanding").mkdir(exist_ok=True)
    (b / "2-understanding" / "PRODUCT.md").write_text("# PRODUCT\n")
    (b / "bindings.yaml").write_text(BINDINGS)
    (b / ".project.json").write_text('{"name": "%s"}' % name)
    (b / "project-agent.yaml").write_text(yaml.safe_dump(doc or DEF, allow_unicode=True))
    if check:
        (b / ".project-agent-check.json").write_text(json.dumps({"revision": revision(b), "ok": True,
                                                               "missing": []}))
    return b


@pytest.fixture
def ws(tmp_path, monkeypatch):
    import core.project_registry as pr
    w = tmp_path / "ws"
    (w / "Projects").mkdir(parents=True)
    lib = w / "Knowledge" / "Library"
    lib.mkdir(parents=True)
    general = {"schema": 2, "status": "reviewed", "identity": {"summary": "Runner"},
               "placements": ["chat-tab", "hive"], "landing": {"approval": "human"},
               "reserved_for_human": ["push", "open_pr", "publish_release", "send_external_message"],
               "modes": [{"id": "research", "when": "anything", "profile": "research"}],
               "default_mode": "research"}
    (lib / "project-agent.yaml").write_text(yaml.safe_dump(general))
    from core.project_agent import revision
    (lib / ".project-agent-check.json").write_text(json.dumps({"revision": revision(lib), "ok": True,
                                                             "missing": []}))
    monkeypatch.setattr(pr, "get_swarmws", lambda: w)
    return w


# ── ① route ──────────────────────────────────────────────────────────────────

def test_a_named_or_detected_brain_is_used_without_asking(ws):
    _brain(ws)
    assert ts.route(ws, "anything", brain="Lib") == {"brain": "Lib", "signal": "named",
                                                      "certain": True, "choices": []}
    r = ts.route(ws, "fix the Lib signer")
    assert r["certain"] and r["brain"] == "Lib" and r["choices"] == []


def test_no_signal_or_several_asks_with_every_brain_and_the_runner(ws):
    _brain(ws)
    _brain(ws, "Other")
    for text in ("summarise this paper", "compare Lib and Other"):
        r = ts.route(ws, text)
        assert not r["certain"] and r["brain"] is None
        assert [c["brain"] for c in r["choices"]] == ["Lib", "Other", "General"]
        assert r["choices"][0]["handles"] == ["fix a signer bug"]
        assert "Runner Agent" in r["choices"][-1]["summary"]
    with pytest.raises(ts.SpecError):
        ts.route(ws, "x", brain="Nope")


# ── ② spec ───────────────────────────────────────────────────────────────────

def test_the_spec_comes_from_the_definition(ws):
    _brain(ws)
    s = ts.build(ws, "Lib", "fix it", "the signer test passes")
    assert (s["mode"], s["mode_chosen"], s["profile"], s["profile_reason"]) == (
        "code-change", "the default mode", "bugfix", "the mode's profile")
    assert s["repos"] == [{"name": "lib", "url": "https://github.com/acme/lib.git", "ref": "main",
                           "sha": None, "dirty": None, "checkout": None, "error": None}]
    # every repository of the Brain goes, whatever the mode (research declares none)
    assert [r["name"] for r in ts.build(ws, "Lib", "why", "an answer", mode="research", profile="research")["repos"]] == ["lib"]
    assert s["delivery"] == "bundle" and s["revision"] and not s["problems"]
    assert s["skills"] == ["s_lib-release"] and s["skills_not_here"] == ["s_lib-desk"]
    s = ts.build(ws, "Lib", "x", "y", mode="code-change", profile="full")
    assert s["mode_chosen"] == "named" and s["profile_reason"] == "profile rule: a public API change"


def test_a_checkout_on_this_machine_gives_its_branch_and_commit(ws, tmp_path):
    import subprocess
    repo = tmp_path / "lib"
    subprocess.run(["git", "init", "-q", "-b", "work", str(repo)], check=True)
    (repo / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "a"], check=True)
    (repo / "a.txt").write_text("changed, not committed")
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    b = _brain(ws)
    (b / "bindings.yaml").write_text(BINDINGS.replace("    kind: external\n", f"    kind: external\n    worktree: {repo}\n"))
    r = ts.build(ws, "Lib", "why", "an answer", mode="research", profile="research")["repos"][0]
    assert (r["ref"], r["sha"], r["dirty"], r["checkout"]) == ("work", sha, 1, str(repo.resolve()))


@pytest.mark.parametrize("kw,match", [
    ({"mode": "nope"}, "no mode 'nope'"),
    ({"profile": "trivial"}, "neither"),
])
def test_a_choice_the_definition_does_not_allow_is_refused(ws, kw, match):
    _brain(ws)
    with pytest.raises(ts.SpecError, match=match):
        ts.build(ws, "Lib", "x", "y", **kw)


# ── ③ admission ──────────────────────────────────────────────────────────────

def test_admission_names_each_rule(ws):
    _brain(ws)
    assert ts.admit(ts.build(ws, "Lib", "x", "done")) == []
    assert any("no acceptance" in r for r in ts.admit(ts.build(ws, "Lib", "x", "  ")))
    _brain(ws, "Draft", doc={**DEF, "status": "draft"})
    assert any("draft" in r for r in ts.admit(ts.build(ws, "Draft", "x", "y")))
    _brain(ws, "Unchecked", check=False)
    assert any("changed after it was confirmed" in r for r in ts.admit(ts.build(ws, "Unchecked", "x", "y")))
    local = yaml.safe_load(yaml.safe_dump(DEF))
    b = _brain(ws, "Local", doc=local)
    (b / "bindings.yaml").write_text(BINDINGS.replace("https://github.com/acme/lib.git", "/Users/me/lib"))
    assert any("no remote a Hive can clone" in r for r in ts.admit(ts.build(ws, "Local", "x", "y")))
    pr = yaml.safe_load(yaml.safe_dump(DEF))
    pr["modes"][0]["delivery"] = "branch-pr"
    _brain(ws, "PR", doc=pr)
    assert any("branch-pr" in r for r in ts.admit(ts.build(ws, "PR", "x", "y")))
    # a mode that does not run on a Hive, reported with the other rules
    refusals = ts.admit(ts.build(ws, "Draft", "x", "", mode="release"))
    assert any("does not run on hive" in r for r in refusals) and any("draft" in r for r in refusals)
    assert refusals[-1].startswith("the task has no acceptance")       # the design's order
    # a source check that found commands with no source
    bad = _brain(ws, "NoSource")
    rec = json.loads((bad / ".project-agent-check.json").read_text())
    (bad / ".project-agent-check.json").write_text(json.dumps({**rec, "ok": False,
                                                              "missing": ["modes[code-change].setup: x"]}))
    assert any("no source in the repository" in r for r in ts.admit(ts.build(ws, "NoSource", "x", "y")))
    # the Runner Agent too: changed after it was confirmed
    lib = ws / "Knowledge" / "Library"
    (lib / "project-agent.yaml").write_text((lib / "project-agent.yaml").read_text() + "# edited\n")
    assert any("changed after it was confirmed" in r for r in ts.admit(ts.build(ws, "General", "x", "y")))
    # schema 1 never goes to a Hive (its repositories are not in a mode)
    v1 = {"status": "reviewed", "default_profile": "bugfix", "placements": ["chat-tab", "hive"],
          "landing": {"approval": "human"}}
    _brain(ws, "Old", doc=v1)
    assert any("schema 1" in r for r in ts.admit(ts.build(ws, "Old", "x", "y")))
    # a Brain named in another case is not the Brain on disk
    with pytest.raises(ts.SpecError):
        ts.build(ws, "lib", "x", "y")


# ── ⑥ the project section ────────────────────────────────────────────────────

def test_the_project_section_tells_the_hive_how_to_work(ws):
    _brain(ws)
    text = ts.segment(ts.build(ws, "Lib", "x", "the signer test passes"))
    for part in ('knowledge find --project Lib', "profile `bugfix`", "`uv sync`", "tests: `pytest -q`",
                 "constant-time compare", "s_lib-release", "do not push", "push, open_pr",
                 "no_git_push", "Done when: the signer test passes"):
        assert part in text, part
    assert "s_lib-desk" not in text and text.endswith("## Task\n")
    runner = ts.segment(ts.build(ws, "General", "x", "y"))
    assert "knowledge find --in library" in runner and "do not push" not in runner
    research = ts.segment(ts.build(ws, "Lib", "why", "an answer", mode="research", profile="research"))
    assert "there to read" in research and "commit on the declared branch" not in research


# ── dispatch ─────────────────────────────────────────────────────────────────

def _fake_hive(monkeypatch, remote_digest, supported=True, current=None, sync_error=None):
    import core.swarm_out as so
    from core import brain_sync_host as host
    calls = {"sync": [], "dispatch": []}

    async def instance(ref):
        return {"id": "i1", "name": ref}

    async def hive_brains(inst):
        return {"supported": supported, "brains": {"Lib": {"digest": remote_digest,
                                                           "current_digest": current or remote_digest,
                                                           "changed_files": 2}}}

    async def sync(inst, brain, ws=None):
        calls["sync"].append(brain)
        if sync_error:
            raise sync_error

    async def dispatch(hive, title, task, origin, repos, *, segment, agent):
        calls["dispatch"].append({"task": task, "repos": repos, "segment": segment, "agent": agent})
        return {"dispatch_id": "so_1"}
    monkeypatch.setattr(so, "_instance", instance)
    monkeypatch.setattr(host, "hive_brains", hive_brains)
    monkeypatch.setattr(host, "sync", sync)
    monkeypatch.setattr(so, "dispatch", dispatch)
    return calls


def test_a_dispatch_syncs_an_out_of_date_brain_and_pins_the_session(ws, monkeypatch):
    import core.swarm_out as so
    from core import brain_sync_host as host
    from core.project_agent import revision
    b = _brain(ws)
    rev = revision(b)
    calls = _fake_hive(monkeypatch, "0" * 64)
    out = asyncio.run(so.dispatch_spec("hive3", "t", "Lib", "fix it", "tests pass", seen_revision=rev))
    assert calls["sync"] == ["Lib"] and out["brain_sync"] == "synced"
    d = calls["dispatch"][0]
    assert d["repos"] == [{"url": "https://github.com/acme/lib.git", "ref": "main"}]
    assert d["agent"]["brain"] == "Lib" and d["agent"]["mode"] == "code-change" and d["agent"]["revision"]
    assert d["agent"]["acceptance"] == "tests pass" and "## Project Agent: Lib" in d["segment"]
    assert so._pin_request(d["agent"]) == {"brain": "Lib", "mode": "code-change",
                                           "revision": d["agent"]["revision"]}
    # up to date: no sync
    digest = host.local_brain(ws, "Lib")["digest"]
    calls = _fake_hive(monkeypatch, digest)
    out = asyncio.run(so.dispatch_spec("hive3", "t", "Lib", "fix it", "tests pass", seen_revision=rev))
    assert calls["sync"] == [] and len(calls["dispatch"]) == 1 and out["brain_sync"] == "up to date"
    # the same version, but the Hive changed files of it: restored
    calls = _fake_hive(monkeypatch, digest, current="1" * 64)
    out = asyncio.run(so.dispatch_spec("hive3", "t", "Lib", "fix it", "tests pass", seen_revision=rev))
    assert calls["sync"] == ["Lib"] and out["brain_sync"].startswith("restored 2")
    # a definition changed since the person was shown it
    calls = _fake_hive(monkeypatch, digest)
    with pytest.raises(so.SwarmOutError, match="changed since it was shown"):
        asyncio.run(so.dispatch_spec("hive3", "t", "Lib", "fix it", "tests pass", seen_revision="0" * 64))
    assert calls["dispatch"] == []


def test_a_refused_spec_or_an_old_hive_sends_nothing(ws, monkeypatch):
    import core.swarm_out as so
    import httpx
    from core.project_agent import revision
    d = _brain(ws, doc={**DEF, "status": "draft"})
    calls = _fake_hive(monkeypatch, None)
    with pytest.raises(so.SwarmOutError, match="draft"):
        asyncio.run(so.dispatch_spec("hive3", "t", "Lib", "x", "y", seen_revision=revision(d)))
    ok = _brain(ws, "Ok")
    calls = _fake_hive(monkeypatch, None, supported=False)
    with pytest.raises(so.SwarmOutError, match="update it once"):
        asyncio.run(so.dispatch_spec("hive3", "t", "Ok", "x", "y", seen_revision=revision(ok)))
    assert calls["dispatch"] == [] and calls["sync"] == []
    calls = _fake_hive(monkeypatch, None, sync_error=httpx.ReadTimeout("slow"))
    with pytest.raises(so.SwarmOutError, match="nothing was sent"):
        asyncio.run(so.dispatch_spec("hive3", "t", "Ok", "x", "y", seen_revision=revision(ok)))
    assert calls["dispatch"] == []


def test_the_hive_request_carries_the_pin_and_the_row_records_the_agent():
    import core.swarm_out as so
    agent = {"brain": "Lib", "mode": "m", "revision": "r" * 64, "profile": "bugfix",
             "delivery": "bundle", "acceptance": "a"}
    row = so._log_row({"id": "i1", "name": "h"}, "s1", "t", "swarm-out", [], agent)
    assert row["project_agent"] == agent
    assert "project_agent" not in so._log_row({"id": "i1", "name": "h"}, "s1", "t", "swarm-out")
    sent = {}

    class _Resp:
        status_code = 200

        async def aiter_lines(self):
            yield 'data: {"session_id": "s1"}'

    class _Stream:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *a):
            return False

    class _Client:
        def stream(self, method, url, json=None, timeout=None):
            sent.update(json)
            return _Stream()
    assert asyncio.run(so._start_session(_Client(), "m", None, so._pin_request(agent))) == "s1"
    assert sent["project_agent"] == {"brain": "Lib", "mode": "m", "revision": "r" * 64}


def test_the_real_dispatch_posts_the_pin_and_the_project_section(monkeypatch, tmp_path):
    import core.swarm_out as so
    import httpx
    monkeypatch.setattr(so, "_state_dir", lambda: tmp_path)
    posted = {}

    def handler(request):
        if request.url.path.endswith("/admission-state"):
            return httpx.Response(200, json={"saturated": False})
        posted.update(json.loads(request.content))
        if posted.get("project_agent", {}).get("revision") == "stale":
            return httpx.Response(400, json={"detail": "<b>hive text</b>"})
        return httpx.Response(200, text='data: {"session_id": "s1"}\n\n',
                              headers={"content-type": "text/event-stream"})

    async def instance(ref):
        return {"id": "i1", "name": ref}
    monkeypatch.setattr(so, "_instance", instance)
    monkeypatch.setattr(so, "_client", lambda inst, timeout=30: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://hive"))
    agent = {"brain": "Lib", "mode": "m", "revision": "r" * 64, "profile": "bugfix",
             "delivery": "bundle", "acceptance": "a"}
    row = asyncio.run(so.dispatch("h", "t", "the task", "swarm-out", [], segment="## Project Agent: Lib\n",
                                  agent=agent))
    assert row["session_id"] == "s1" and row["project_agent"] == agent
    assert posted["project_agent"] == {"brain": "Lib", "mode": "m", "revision": "r" * 64}
    assert "## Project Agent: Lib" in posted["message"] and posted["message"].endswith("the task")
    with pytest.raises(so.SwarmOutError, match="refused the Project Agent pin") as e:
        asyncio.run(so.dispatch("h", "t", "x", "swarm-out", [], segment="", agent={**agent, "revision": "stale"}))
    assert "hive text" not in str(e.value)


def test_the_general_brain_revision_is_the_same_on_a_hive(ws, tmp_path):
    from core import brain_sync as bs
    from core.project_agent import revision
    lib = ws / "Knowledge" / "Library"
    (lib / "3-gates").mkdir()
    (lib / "3-gates" / "g.py").write_text("x\n")
    (lib / "note.md").write_text("# n\n")
    files = bs.collect(lib, general=True)
    hive = tmp_path / "hive"
    bs.install(hive, "General", files, bs.content_digest(files))
    assert revision(hive / "Knowledge" / "Library") == revision(lib)


# ── route and CLI ────────────────────────────────────────────────────────────

def test_the_dispatch_route_needs_a_brain(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.swarm_out import router
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    app = FastAPI()
    app.include_router(router, prefix="/api/swarm-out")
    c = TestClient(app)
    r = c.post("/api/swarm-out/dispatch", json={"hive": "h", "title": "t", "task": "x"})
    assert r.status_code == 400 and "route it first" in r.json()["detail"]
    r = c.post("/api/swarm-out/dispatch", json={"hive": "h", "title": "t", "task": "x", "brain": "Lib"})
    assert r.status_code == 400 and "run spec first" in r.json()["detail"]
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    for path in ("/route", "/spec", "/dispatch"):
        assert c.post("/api/swarm-out" + path, json={}).status_code in (403, 422)


def test_the_cli_sends_brain_mode_profile_and_acceptance(monkeypatch):
    import scripts.swarm_out as cli
    seen = []
    monkeypatch.setattr(cli, "_call", lambda m, p, b=None, timeout=120: seen.append((m, p, b, timeout)) or 0)
    assert cli.main(["dispatch", "--hive", "h", "--title", "t", "--brain", "Lib", "--task", "x",
                     "--acceptance", "done", "--mode", "code-change", "--revision", "abc"]) == 0
    assert seen[-1] == ("POST", "/dispatch", {"brain": "Lib", "task": "x", "acceptance": "done",
                                              "mode": "code-change", "profile": None,
                                              "hive": "h", "title": "t", "revision": "abc"},
                        cli.DISPATCH_TIMEOUT_S)
    assert cli.DISPATCH_TIMEOUT_S > 2 * 120
    cli.main(["route", "--task", "fix it", "--bound-project", "Lib"])
    assert seen[-1][:3] == ("POST", "/route", {"text": "fix it", "brain": None, "editor_file_path": None,
                                               "session_bound_project": "Lib"})
    with pytest.raises(SystemExit):
        cli.main(["dispatch", "--hive", "h", "--title", "t", "--task", "x", "--acceptance", "a"])
    with pytest.raises(SystemExit):
        cli.main(["dispatch", "--hive", "h", "--title", "t", "--brain", "B", "--task", "x", "--acceptance", "a"])


def test_a_dispatch_the_cli_stops_waiting_for_says_it_may_be_running(monkeypatch, capsys):
    import urllib.request
    import scripts.swarm_out as cli

    def slow(req, timeout=None):
        raise TimeoutError("timed out")
    monkeypatch.setattr(urllib.request, "urlopen", slow)
    assert cli._call("POST", "/dispatch", {}, timeout=1) == 1
    assert "may be running" in capsys.readouterr().out


def test_a_linked_definition_has_no_revision_and_is_refused(ws, tmp_path):
    b = _brain(ws)
    real = tmp_path / "elsewhere.yaml"
    real.write_text((b / "project-agent.yaml").read_text())
    (b / "project-agent.yaml").unlink()
    (b / "project-agent.yaml").symlink_to(real)
    assert any("no revision" in r for r in ts.admit(ts.build(ws, "Lib", "x", "y")))


def test_the_route_endpoint_passes_the_deterministic_signals(ws, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.swarm_out import router
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    _brain(ws)
    _brain(ws, "Other")
    app = FastAPI()
    app.include_router(router, prefix="/api/swarm-out")
    r = TestClient(app).post("/api/swarm-out/route", json={"text": "fix the failing test",
                                                          "session_bound_project": "Other"})
    assert r.json()["brain"] == "Other" and r.json()["signal"] == "signal2_bound_repo"
