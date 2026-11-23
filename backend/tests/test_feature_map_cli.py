"""Feature Map CLI: exit codes with quarantine, errors said as JSON, evidence containment."""
from __future__ import annotations

import json
from pathlib import Path

from core.feature_map import ledger
from core.feature_map.replay import contained
from scripts import feature_map as cli
from tests.feature_map_fixtures import app_data, make_repo  # noqa: F401 — fixture

SPEC = "import { test } from '@playwright/test';\ntest('t', async () => {});\n"
FEATURES = ("schema: 1\nfeatures:\n"
            "  - {id: title-a1, title: Title, target: web, spec: specs/title.spec.ts}\n")


def _r(feature, walk, project, result):
    return {"feature": feature, "walk": walk, "project": project, "result": result}


def test_exit_code_ignores_quarantined_walks_by_exact_key():
    q = {ledger.walk_key("a", "opens", "compact")}
    # a quarantined walk's fail never sets the exit code; the same title elsewhere does
    assert cli._exit_code([_r("a", "opens", "compact", "fail"), _r("a", "opens", "regular", "pass")], q) == 0
    assert cli._exit_code([_r("a", "opens", "regular", "fail")], q) == 1
    assert cli._exit_code([_r("b", "opens", "compact", "fail")], q) == 1
    assert cli._exit_code([_r("a", "opens", "compact", "fail")], q) == 2, "nothing else judged"


def test_replay_error_is_json_and_exit_two(tmp_path, app_data, monkeypatch, capsys):  # noqa: F811
    root = make_repo(tmp_path / "r", specs={"title.spec.ts": SPEC}, features=FEATURES)

    def boom(*_a, **_kw):
        raise OSError("no space left")
    monkeypatch.setattr(cli, "replay", boom)
    code = cli.main(["replay", "--repo-root", str(root), "--project", "B", "--repo", "app"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 2 and out["ok"] is False and "no space left" in out["error"]


def test_evidence_only_from_inside_the_sandbox(tmp_path):
    sb = tmp_path / "sb"
    (sb / "out").mkdir(parents=True)
    inside = sb / "out" / "shot.png"
    inside.write_bytes(b"x")
    outside = tmp_path / "credentials"
    outside.write_text("secret")
    link = sb / "out" / "link.png"
    link.symlink_to(outside)
    assert contained([str(inside), str(outside), str(link), str(sb / "missing.png")], sb) == [str(inside.resolve())]


def test_brain_key_is_shared():
    assert ledger.brain_key("") == ledger.brain_key(None) == ledger.UNBOUND
    assert ledger.brain_key("B") == "B"


def test_run_writes_the_ledger_the_engine_reads(tmp_path, app_data, monkeypatch, capsys):  # noqa: F811
    """Under --run the CLI takes the engine's own project (empty here), never a binding's."""
    from core.feature_map import pipeline
    root = make_repo(tmp_path / "r", specs={}, features=(
        "schema: 1\nfeatures:\n  - {id: cmd-a1, title: Cmd, target: web, run: [\"true\"]}\n"))
    tree = "a" * 40
    monkeypatch.setattr(cli, "_run_facts", lambda run, r: {
        "repo": "app", "project": "", "project_dir": "", "code_tree": tree,
        "base": "HEAD", "changed": ["site/index.html"], "since": None})
    import core.repo_changes as repo_changes
    monkeypatch.setattr(repo_changes, "_bindings", lambda: {str(root.resolve()): ("app", "Other")},
                        raising=False)
    monkeypatch.setattr(cli, "replay", lambda fmap, r, sel, keep=False: ([
        {"feature": "cmd-a1", "walk": "Cmd", "project": "command", "result": "pass",
         "reason": "", "error": "", "duration_s": 0.1, "evidence": []}], None))
    code = cli.main(["replay", "--affected", "--run", "run_x", "--repo-root", str(root)])
    capsys.readouterr()
    assert code == 0
    assert ledger.read("Other", "app") == [], "a binding's project is not the run's"
    rows = ledger.read(ledger.brain_key(""), "app")
    assert rows and rows[0]["run"] == "run_x" and rows[0]["code_tree"] == tree
    facts = pipeline.facts(project_dir=None, project=ledger.brain_key(""), repo="app",
                           root=root, base="HEAD", code_tree=tree, run_id="run_x",
                           changed=["site/index.html"], blocking=True)
    assert [r["result"] for r in facts["rows"]] == ["pass"] and not facts["missing"]


def test_replay_leaves_no_sandbox_behind_on_errors(tmp_path, monkeypatch):
    import tempfile

    from tests.feature_map_fixtures import no_metadata_service
    no_metadata_service(monkeypatch)

    import pytest
    from core.feature_map import files, sandbox
    from core.feature_map.replay import Selection, replay
    root = make_repo(tmp_path / "r", specs={}, features=(
        "schema: 1\nfeatures:\n  - {id: cmd-a1, title: Cmd, target: web, run: [\"true\"]}\n"))
    fmap = files.load_folder(root / ".swarmai/features")
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))

    # the checkout copy fails after the sandbox folder was made: not_run, folder removed
    def no_copy(*_a, **_kw):
        raise OSError("disk full")
    monkeypatch.setattr(sandbox, "_copy_checkout", no_copy)
    results, sbdir = replay(fmap, root, Selection(list(fmap.features)))
    assert sbdir is None and results[0]["result"] == "not_run"
    assert list(temp.iterdir()) == []
    monkeypatch.undo()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    no_metadata_service(monkeypatch)

    # an error while replaying: raised, and the folder (returned to nobody) removed
    def broken_start(self, fmap, target):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(sandbox.Sandbox, "start", broken_start)
    with pytest.raises(RuntimeError):
        replay(fmap, root, Selection(list(fmap.features)))
    assert list(temp.iterdir()) == []
    # the caller's --keep keeps it, for a look at what broke
    with pytest.raises(RuntimeError) as err:
        replay(fmap, root, Selection(list(fmap.features)), keep=True)
    assert [p.name.startswith("swarm-feature-map-") for p in temp.iterdir()] == [True]
    assert err.value.sandbox_dir == str(next(temp.iterdir()).resolve()), "named for the caller"


def test_run_takes_the_engines_project_folder(tmp_path, app_data, monkeypatch, capsys):  # noqa: F811
    """Under --run the feature folder is looked up in the engine's own project folder (its
    workspace), never one rebuilt from the CLI's own workspace setting."""
    import shutil
    from types import SimpleNamespace

    import core.pipeline_human as pipeline_human
    from core.feature_map import files
    root = make_repo(tmp_path / "r", specs={}, features="schema: 1\nfeatures: []\n")
    shutil.rmtree(root / ".swarmai/features")
    engine_projects = tmp_path / "engine-ws" / "Projects" / "P"      # not the CLI's workspace
    folder = files.brain_folder(engine_projects, "app")
    (folder / "targets").mkdir(parents=True)
    (folder / "targets" / "web.yaml").write_text("id: web\nkind: command\n")
    (folder / "features.yaml").write_text(
        "schema: 1\nfeatures:\n  - {id: cmd-a1, title: Cmd, target: web, run: [\"true\"]}\n")
    tree = "a" * 40
    engine = SimpleNamespace(
        contract={"project": "P", "created_at": "2026-11-19T00:00:00+00:00"},
        effective_repos=lambda: {"app": str(root)}, _project_dir=lambda: engine_projects,
        _current_code=lambda names: ({"app": str(root)}, {"app": tree}, {"app": "HEAD"}, {},
                                     {"app": ["README.md"]}))
    monkeypatch.setattr(pipeline_human, "load_engine", lambda run_id: engine)
    assert cli._run_facts("run_x", root)["project_dir"] == str(engine_projects)
    monkeypatch.setattr(cli, "replay", lambda fmap, r, sel, keep=False: ([
        {"feature": f.id, "walk": f.title, "project": "command", "result": "pass", "reason": "",
         "error": "", "duration_s": 0.1, "evidence": []} for f in sel.features], None))
    code = cli.main(["replay", "--affected", "--run", "run_x", "--repo-root", str(root)])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and out["mode"] == "brain", out
    assert [r["feature"] for r in ledger.read("P", "app")] == ["cmd-a1"]


def test_keep_reaches_replay_and_a_kept_sandbox_is_named(tmp_path, app_data, monkeypatch, capsys):  # noqa: F811
    root = make_repo(tmp_path / "r", specs={"title.spec.ts": SPEC}, features=FEATURES)
    seen = []

    def crash(fmap, r, sel, keep=False):
        seen.append(keep)
        exc = RuntimeError("broke")
        if keep:
            exc.sandbox_dir = str(tmp_path / "kept-sandbox")
        raise exc
    monkeypatch.setattr(cli, "replay", crash)
    code = cli.main(["replay", "--repo-root", str(root), "--project", "B", "--repo", "app", "--keep"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert seen == [True], "the caller's --keep reaches replay"
    assert code == 2 and out["sandbox"] == str(tmp_path / "kept-sandbox")


def test_base_replay_error_is_json_too(tmp_path, app_data, monkeypatch, capsys):  # noqa: F811
    """A walk failed at head, and the replay at the run's base raised: still JSON, exit 2."""
    root = make_repo(tmp_path / "r", specs={}, features=(
        "schema: 1\nfeatures:\n  - {id: cmd-a1, title: Cmd, target: web, run: [\"true\"]}\n"))
    monkeypatch.setattr(cli, "_run_facts", lambda run, r: {
        "repo": "app", "project": "", "project_dir": "", "code_tree": "a" * 40,
        "base": "HEAD", "changed": ["site/index.html"], "since": None})
    calls = []
    head_sb = tmp_path / "head-sandbox"
    head_sb.mkdir()

    def head_fails_base_breaks(fmap, r, sel, keep=False):
        calls.append(r)
        if len(calls) > 1:
            exc = OSError("base replay broke")
            exc.sandbox_dir = str(tmp_path / "base-sandbox")
            raise exc
        return ([{"feature": "cmd-a1", "walk": "Cmd", "project": "command", "result": "fail",
                  "reason": "", "error": "", "duration_s": 0.1, "evidence": []}], head_sb)
    monkeypatch.setattr(cli, "replay", head_fails_base_breaks)
    code = cli.main(["replay", "--affected", "--run", "run_x", "--repo-root", str(root), "--keep"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert len(calls) == 2 and code == 2 and "base replay broke" in out["error"]
    # --keep: the head sandbox (the failing walk) is named, and the base one too
    assert out["sandbox"] == str(head_sb) and head_sb.is_dir()
    assert out["base_sandbox"] == str(tmp_path / "base-sandbox")


def test_a_wall_clock_cap_stops_the_sandbox_services(tmp_path, app_data):  # noqa: F811
    """The CLI under `perl -e 'alarm N; exec ...'` gets SIGALRM: its services must not outlive it."""
    import os
    import signal
    import socket
    import subprocess
    import sys
    import time
    from pathlib import Path
    port = 20000 + os.getpid() % 10000
    target = (f"id: web\nkind: command\nservices:\n"
              f"  - {{id: s, argv: [{sys.executable}, -m, http.server, \"{port}\", --bind, 127.0.0.1], "
              f"ready: \"http://127.0.0.1:{port}/\"}}\n")
    root = make_repo(tmp_path / "r", specs={}, target=target, features=(
        "schema: 1\nfeatures:\n  - {id: wait-a1, title: Wait, target: web, run: "
        f"[{sys.executable}, -c, \"import time; time.sleep(60)\"]}}\n"))

    def answers():
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
            return True
        except OSError:
            return False
    backend = Path(__file__).resolve().parents[1]
    # the CLI as `-m scripts.feature_map`, on a machine taken as one that does not need the
    # replay user (as app_data does in-process), so it replays as the person on a Hive too
    main = ("import runpy, sys; sys.path.insert(0, '.'); import core.feature_map.replay_user as r; "
            "r.needs_replay_user = lambda: False; "
            "runpy.run_module('scripts.feature_map', run_name='__main__')")
    proc = subprocess.Popen([sys.executable, "-c", main, "replay", "--repo-root", str(root),
                             "--project", "B", "--repo", "app"], cwd=str(backend),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=os.environ)
    deadline = time.monotonic() + 30
    while not answers() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert answers(), "the service came up"
    proc.send_signal(signal.SIGALRM)
    proc.wait(timeout=30)
    deadline = time.monotonic() + 10
    while answers() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not answers(), "the sandbox's service outlived the capped CLI"


def test_an_interrupted_playwright_run_takes_its_group_with_it(tmp_path):
    import os
    import signal
    import sys
    import time
    from types import SimpleNamespace

    import pytest
    from core.feature_map import replay
    script = tmp_path / "pw.py"
    script.write_text("import os, time\nopen(os.environ['PID_FILE'], 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
    pid_file = tmp_path / "pid"
    rt = SimpleNamespace(node=Path(sys.executable), playwright_cli=script, env=lambda: {})
    sb = SimpleNamespace(env=lambda target: {"PATH": os.environ["PATH"], "PID_FILE": str(pid_file)},
                         user=None)
    from core.feature_map.sandbox import Sandbox
    sb.popen = lambda argv, **kw: Sandbox.popen(sb, argv, **kw)   # the sandbox's own start

    def interrupt(*_a):
        raise KeyboardInterrupt
    old = signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, 1.5)
    try:
        with pytest.raises(KeyboardInterrupt):
            replay._pw(rt, sb, None, [], tmp_path)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    alive = True
    while alive and time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            alive = False
    assert not alive, "the Playwright process outlived the interrupted replay"


def test_base_record_error_names_the_kept_base_sandbox(tmp_path, app_data, monkeypatch, capsys):  # noqa: F811
    """The base replay succeeded, then recording its results raised, with --keep: the kept
    base sandbox is named as base_sandbox and left in place."""
    root = make_repo(tmp_path / "r", specs={}, features=(
        "schema: 1\nfeatures:\n  - {id: cmd-a1, title: Cmd, target: web, run: [\"true\"]}\n"))
    monkeypatch.setattr(cli, "_run_facts", lambda run, r: {
        "repo": "app", "project": "", "project_dir": "", "code_tree": "a" * 40,
        "base": "HEAD", "changed": ["site/index.html"], "since": None})
    head_sb, base_sb = tmp_path / "head-sandbox", tmp_path / "base-sandbox"
    head_sb.mkdir()
    base_sb.mkdir()
    calls = []

    def head_fails(fmap, r, sel, keep=False):
        calls.append(r)
        return ([{"feature": "cmd-a1", "walk": "Cmd", "project": "command", "result": "fail",
                  "reason": "", "error": "", "duration_s": 0.1, "evidence": []}],
                head_sb if len(calls) == 1 else base_sb)
    real_record = cli._record

    def record(*a, **kw):
        if kw["trigger"] == "base":
            raise OSError("ledger write broke")
        return real_record(*a, **kw)
    monkeypatch.setattr(cli, "replay", head_fails)
    monkeypatch.setattr(cli, "_record", record)
    code = cli.main(["replay", "--affected", "--run", "run_x", "--repo-root", str(root), "--keep"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert len(calls) == 2 and code == 2 and "ledger write broke" in out["error"]
    assert out["sandbox"] == str(head_sb)
    assert out["base_sandbox"] == str(base_sb) and base_sb.is_dir()


def test_a_bad_command_line_exits_2_with_its_usage(capsys):
    # the CLI shares its parser with the commit notice; a refusal stays argparse's exit 2
    import pytest
    from scripts import feature_map
    with pytest.raises(SystemExit) as e:
        feature_map.main(["frobnicate"])
    assert e.value.code == 2 and "usage: feature_map" in capsys.readouterr().err
