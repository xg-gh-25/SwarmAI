"""The backend program's subcommands and the one way product code runs Python (design
docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.2).

- desktop_main.py dispatches ``py`` / ``pipeline-v1`` / ``daemon`` before FastAPI is imported;
  ``py`` behaves like ``python`` for a script, ``-m`` and ``-c`` (argv, ``__name__``, exit
  code); ``pipeline-v1`` says "not supported" on Windows.
- the dev shim backend/bin/python-backend runs the same entry point.
- utils/backend_bin: packaged -> ``[<binary>, "py"]``; dev -> ``sys.executable``; the shim is
  ``SWARM_BACKEND_BIN`` in dev.
- the channel MCP script, the self-upgrader and the system script jobs use it: none of them
  names a system ``python`` or the source checkout any more.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SHIM = BACKEND / "bin" / "python-backend"


def _shim(*args, **kw):
    # run the shim with the main checkout's venv python (a worktree has none)
    return subprocess.run([sys.executable, str(BACKEND / "desktop_main.py"), *args],
                          capture_output=True, text=True, timeout=60, **kw)


def test_py_runs_a_script_a_module_and_code_like_python(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("import sys, json\nprint(json.dumps([sys.argv, __name__]))\nsys.exit(4)\n")
    r = _shim("py", str(script), "a", "b")
    assert r.returncode == 4
    assert json.loads(r.stdout) == [[str(script), "a", "b"], "__main__"]
    r = _shim("py", "-m", "json.tool", input='{"k": 1}')
    assert r.returncode == 0 and '"k": 1' in r.stdout
    r = _shim("py", "-c", "import sys; print(sys.argv)", "x")
    assert r.stdout.strip() == "['-c', 'x']"
    assert _shim("py", str(tmp_path / "missing.py")).returncode == 2
    assert _shim("py").returncode == 64


def test_subcommands_do_not_start_the_server():
    r = _shim("py", "-c", "import sys; print('fastapi' in sys.modules, 'main' in sys.modules)")
    assert r.stdout.strip() == "False False", r.stderr


def test_pipeline_v1_is_unsupported_on_windows(monkeypatch, capsys):
    sys.path.insert(0, str(BACKEND))
    import desktop_main
    monkeypatch.setattr(desktop_main.sys, "platform", "win32")
    assert desktop_main.main(["pipeline-v1", "status"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"] == "unsupported_platform"
    assert "native" in out["message"]


def test_dev_shim_runs_the_same_entry_point():
    text = SHIM.read_text()
    assert SHIM.stat().st_mode & 0o111
    assert '"$here/desktop_main.py" "$@"' in text


def _shim_copy(tmp_path, venv: bool) -> Path:
    """A backend dir with the real shim, a desktop_main.py that reports which python ran it,
    and (optionally) a fake .venv python."""
    import shutil
    b = tmp_path / "backend"
    (b / "bin").mkdir(parents=True)
    shutil.copy2(SHIM, b / "bin" / "python-backend")
    (b / "desktop_main.py").write_text("print('ran')\n")
    if venv:
        vpy = b / ".venv" / "bin" / "python"
        vpy.parent.mkdir(parents=True)
        vpy.write_text('#!/bin/sh\necho "venv $*"\n')
        vpy.chmod(0o755)
    return b / "bin" / "python-backend"


def test_dev_shim_uses_the_checkout_venv(tmp_path):
    shim = _shim_copy(tmp_path, venv=True)
    r = subprocess.run([str(shim), "py", "-c", "x"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert r.stdout.strip() == f"venv {shim.parents[1] / 'desktop_main.py'} py -c x"


def test_dev_shim_without_a_venv_stops_instead_of_using_another_python(tmp_path):
    # python3 is on PATH here; the shim must still refuse rather than run with it
    shim = _shim_copy(tmp_path, venv=False)
    r = subprocess.run([str(shim), "py", "-c", "x"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 127
    assert "ran" not in r.stdout
    assert ".venv" in r.stderr and "uv venv" in r.stderr


def test_dev_shim_in_a_git_worktree_uses_the_main_checkouts_venv(tmp_path):
    """A worktree has no venv of its own: the shim uses the main checkout's (found from
    the shared git dir) and still runs the worktree's own desktop_main.py."""
    main = tmp_path / "main"
    shim_main = _shim_copy(main, venv=True)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", str(main)], check=True)
    (main / ".gitignore").write_text(".venv/\n")
    subprocess.run([*git, "-C", str(main), "add", "-A"], check=True)
    subprocess.run([*git, "-C", str(main), "commit", "-qm", "x"], check=True)
    wt = tmp_path / "wt"
    subprocess.run([*git, "-C", str(main), "worktree", "add", "-q", str(wt)], check=True)
    shim = wt / "backend" / "bin" / "python-backend"
    assert not (wt / "backend" / ".venv").exists() and shim.exists()
    r = subprocess.run([str(shim), "py", "-c", "x"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"venv {wt / 'backend' / 'desktop_main.py'} py -c x"
    assert shim_main.exists()

def test_backend_bin_packaged_and_dev(monkeypatch, tmp_path):
    from utils import backend_bin as bb
    assert bb.python_argv() == [sys.executable]
    assert bb.backend_bin() == str(SHIM)
    assert bb.py_command("-m", "x") == [str(SHIM), "py", "-m", "x"]
    exe = tmp_path / "runtime" / "2.4.0" / "python-backend"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(bb.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bb.sys, "executable", str(exe))
    monkeypatch.setattr(bb.sys, "_MEIPASS", str(exe.parent / "_internal"), raising=False)
    assert bb.python_argv() == [str(exe.resolve()), "py"]
    assert bb.bundled_path("skills") == exe.parent / "_internal" / "skills"
    monkeypatch.delenv(bb.ENV, raising=False)
    assert bb.export_backend_bin() == str(exe.resolve())
    import os
    assert os.environ[bb.ENV] == str(exe.resolve())


def test_channel_mcp_runs_with_the_bundled_python(monkeypatch, tmp_path):
    from utils import backend_bin as bb
    from core import mcp_config_loader as m
    exe = tmp_path / "python-backend"
    exe.write_text("")
    monkeypatch.setattr(bb.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bb.sys, "executable", str(exe))
    out = m.inject_channel_mcp({}, {"channel_type": "slack", "bot_token": "", "chat_id": "c"}, str(tmp_path))
    entry = out.get("channel-tools")
    assert entry, out
    assert entry["command"] == str(exe.resolve())
    assert entry["args"][0] == "py" and entry["args"][1].endswith("channel_file_sender.py")


def test_system_script_jobs_need_no_system_python_or_checkout():
    from jobs.system_jobs import SYSTEM_JOBS
    from utils.backend_bin import backend_bin
    scripts = [j for j in SYSTEM_JOBS if j.type == "script" and "command" in j.config]
    py_jobs = [j for j in scripts if " py " in j.config["command"]]
    assert {j.id for j in py_jobs} >= {"loops-health", "judge-telemetry-report", "code-intel-reindex",
                                        "code-intel-full-reindex"}
    for j in scripts:
        cmd = j.config["command"]
        assert not cmd.startswith("python"), (j.id, cmd)
        assert "backend/" not in cmd.replace(str(BACKEND), ""), (j.id, cmd)
    for j in py_jobs:
        assert j.config["command"].startswith(backend_bin()), j.id
        # no cwd: the executor runs it in config.get_app_data_dir(), the one source
        assert "cwd" not in j.config, j.id


def test_no_product_code_looks_for_a_system_python():
    srcs = {p: p.read_text(encoding="utf-8") for p in
            [BACKEND / "main.py", BACKEND / "core" / "mcp_config_loader.py",
             BACKEND / "utils" / "bundle_paths.py"]}
    for p, s in srcs.items():
        assert "get_python_executable" not in s, p
        assert 'which("python3")' not in s, p
    build = (BACKEND.parent / "desktop" / "scripts" / "build-backend.sh").read_text()
    assert 'cat > "$BUILD_DIR/desktop_main.py"' not in build   # one entry file, tracked


@pytest.mark.parametrize("name", ["run_evolution"])
def test_job_modules_import_under_their_new_names(name):
    import importlib
    importlib.import_module(f"jobs.{name}")
