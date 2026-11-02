"""A Hive task gets the tools its repository's CI jobs get from their setup actions
(design docs/design/2026-11-02-hive-task-toolchain.md).

Questions:
(1) does ``validate`` refuse a ``tools`` row that is not ``<tool>@<version>``, or a tool
    listed twice, and does the mode view keep only well-formed ones;
(2) does the drafter write a proposed mode's ``tools`` with a source in the repo, and
    drop a bad one;
(3) does a Hive task's setup start with one ``task-tools`` line when the mode lists
    tools (and only then, and only on a Hive), so a checks-only mode is asked to run
    setup too;
(4) does ``task_checks`` give setup the package's ``hive/bin``, so that line is found;
(5) does ``hive/bin/task-tools`` install through mise with a clean environment, write
    the tools to ``$GITHUB_PATH`` / ``$GITHUB_ENV`` in the order task_checks expects,
    make a python venv in the task's own directory, and refuse a bad argument before
    running mise; and is its pattern the same as ``TOOL_RE``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core import project_agent as pa
from core import project_agent_draft as pad
from core import task_spec as ts
from scripts import task_checks
from tests.test_hive_workspace import W1, _repo as _hw_repo, _ship, world  # noqa: F401
from tests.test_project_agent_draft import brain  # noqa: F401 — the fixture
from tests.test_task_spec import DEF, _brain, _build, ws  # noqa: F401 — the fixture

SCRIPT = Path(__file__).resolve().parents[2] / "hive" / "bin" / "task-tools"


def _mode(**over):
    return {"id": "code-change", "when": "a change", **over}


# ── (1) validate and the view ────────────────────────────────────────────────

def test_validate_names_bad_and_repeated_tools():
    doc = {"schema": 2, "default_mode": "code-change", "modes": [_mode(tools=[
        {"tool": "python@3.11", "source": "ci.yml"},
        {"tool": "python@3.12", "source": "ci.yml"},
        {"tool": "node 22", "source": "ci.yml"},
        {"tool": "rust@$(id)", "source": "ci.yml"},
        "bun@1.4.4",
    ])]}
    problems = pa.validate(doc)
    assert any("python is listed twice" in p for p in problems)
    assert sum("is not a <tool>@<version> row" in p for p in problems) == 3
    assert pa.validate({**doc, "modes": [_mode(tools="python@3.11")]}) == ["modes[code-change].tools is not a list"]
    assert pa.mode_tools(doc["modes"][0]) == ["python@3.11", "python@3.12"]


# ── (2) the drafter ──────────────────────────────────────────────────────────

def test_the_drafter_writes_tools_with_a_repo_source(brain):  # noqa: F811
    proposal = {"modes": [
        {"id": "code-change", "tools": [
            {"tool": "python@3.11", "source": "lib:.github/workflows/ci.yml"},
            {"tool": "python@3.12", "source": "lib:.github/workflows/ci.yml"},   # a second python
            {"tool": "node@22", "source": "lib:missing.yml"},                    # no such file
            {"tool": "node", "source": "lib:.github/workflows/ci.yml"},          # no version
        ]},
        {"id": "research", "tools": [{"tool": "bun@1", "source": "TECH.md"}]},
    ]}
    doc, dropped = pad.build(brain, proposal)
    modes = {m["id"]: m for m in doc["modes"]}
    assert modes["code-change"]["tools"] == [{"tool": "python@3.11", "source": ".github/workflows/ci.yml"}]
    assert "tools" not in modes["research"]
    assert len([d for d in dropped if ".tools" in d]) == 4
    assert pa.validate(doc, brain) == []
    assert list(modes["code-change"]).index("tools") < list(modes["code-change"]).index("validations")


# ── (3) the Host's setup line ────────────────────────────────────────────────

def _doc_with(tools, setup=True):
    doc = json.loads(json.dumps(DEF))
    m = doc["modes"][0]
    m["tools"] = [{"tool": t, "source": "ci.yml"} for t in tools]
    if not setup:
        m.pop("setup")
    return doc


def test_a_hive_task_starts_setup_with_task_tools(ws):  # noqa: F811
    _brain(ws, doc=_doc_with(["python@3.11", "node@22"]))
    spec = _build(ws, "Lib", "x", "y", placement="hive")
    assert spec["setup"][0] == {"command": "task-tools python@3.11 node@22", "source": "project-agent.yaml",
                                "repo": "lib", "cwd": ""}
    assert [s["command"] for s in spec["setup"][1:]] == ["uv sync"]
    local = _build(ws, "Lib", "x", "y", placement="chat-tab")
    assert [s["command"] for s in local["setup"]] == ["uv sync"]          # only a Hive has task-tools


def test_no_tools_no_line(ws):  # noqa: F811
    _brain(ws)
    assert [s["command"] for s in _build(ws, "Lib", "x", "y", placement="hive")["setup"]] == ["uv sync"]


def test_a_checks_only_mode_is_asked_to_run_setup(ws):  # noqa: F811
    _brain(ws, doc=_doc_with(["rust@stable"], setup=False))
    spec = _build(ws, "Lib", "x", "y", placement="hive")
    assert [s["command"] for s in spec["setup"]] == ["task-tools rust@stable"]
    assert f"scripts.task_checks setup {W1}" in ts.segment(spec, None, work_id=W1)


# ── (4) task_checks finds it ─────────────────────────────────────────────────

@pytest.fixture
def order(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(task_checks, "_workspace", lambda: world["ws"])
    sha = _ship(world)

    def make(setup):
        from core import hive_workspace as hw
        return hw.prepare(world["ws"], W1, [_hw_repo(sha)],
                          {"setup": setup, "validations": [
                              {"id": "finds-it", "command": "command -v task-tools", "repo": "lib", "cwd": ""}]})
    return make


def test_task_checks_puts_the_package_bin_on_path(world, order):  # noqa: F811
    assert (task_checks.PACKAGE_BIN / "task-tools").is_file()
    order([{"command": "command -v task-tools", "repo": "lib", "cwd": ""}])
    assert task_checks.main(["setup", W1]) == 0
    assert task_checks.main(["run", W1, "finds-it"]) == 0       # and the checks after it


# ── (5) the script ───────────────────────────────────────────────────────────

FAKE_MISE = r"""#!/bin/bash
# records how it was called; answers `env --json` and `where` like mise
log="$(dirname "$0")/calls.jsonl"
/usr/bin/env python3 -I - "$log" "$PWD" "$@" <<'EOF'
import json, os, sys
log, cwd, *args = sys.argv[1:]
with open(log, "a") as f:
    f.write(json.dumps({"args": args, "cwd": cwd,
                        "mise": {k: v for k, v in os.environ.items() if k.startswith("MISE_")}}) + "\n")
EOF
case "$1" in
  env) printf '%s\n' '{"PATH": "/tools/node/bin:/tools/python/bin:/usr/local/bin:/usr/bin:/bin", "JAVA_HOME": "/tools/java", "MISE_SHELL": "bash", "LD_PRELOAD": "/x.so", "BASH_ENV": "/x.sh", "SPLIT": "a\rB=c", "9BAD": "x"}' ;;
  where) echo "$(dirname "$0")/py" ;;
esac
"""


def _bin(d: Path, name: str, body: str) -> None:
    (d / name).write_text("#!/bin/sh\n" + body)
    (d / name).chmod(0o755)


def _sha(f: Path) -> str:
    import hashlib
    return hashlib.sha256(f.read_bytes()).hexdigest()


@pytest.fixture
def kit(tmp_path):
    """task-tools on a (faked) Linux arm64 machine whose pinned mise is a fake one: the
    copy of the script pins the fake's sha256, as the real script pins the real build.
    A python launcher where mise would have installed python; a checks directory with
    the two GitHub files; a repository directory holding a json.py and a mise.toml."""
    home = tmp_path / "home"
    cache = home / ".local" / "share" / "task-tools"
    cache.mkdir(parents=True)
    script = SCRIPT.read_text()
    version = re.search(r'^MISE_VERSION="([^"]+)"', script, re.M).group(1)
    mise = cache / f"mise-{version}"
    mise.write_text(FAKE_MISE)
    mise.chmod(0o755)
    script = re.sub(r'^MISE_SHA256_LINUX_ARM64=".*"$', f'MISE_SHA256_LINUX_ARM64="{_sha(mise)}"', script, flags=re.M)
    (tmp_path / "task-tools").write_text(script)
    (tmp_path / "task-tools").chmod(0o755)
    (cache / "py" / "bin").mkdir(parents=True)
    (cache / "py" / "bin" / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (cache / "py" / "bin" / "python3").chmod(0o755)
    fake = tmp_path / "fake"
    fake.mkdir()
    _bin(fake, "uname", '[ "$1" = "-s" ] && echo Linux || echo aarch64\n')
    _bin(fake, "curl", f'touch "{tmp_path}/fetched"; exit 22\n')
    checks = tmp_path / "work" / "checks"
    checks.mkdir(parents=True)
    (checks / "setup-1.path").write_text("")
    (checks / "setup-1.env").write_text("")
    repo = tmp_path / "work" / "repo"
    repo.mkdir()
    (repo / "mise.toml").write_text('[env]\nPLANTED = "1"\n')
    (repo / "json.py").write_text(f'open("{tmp_path}/repo_code_ran", "w").close()\n')

    def run(*args, **env):
        e = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "HOME": str(home),
             "GITHUB_PATH": str(checks / "setup-1.path"), "GITHUB_ENV": str(checks / "setup-1.env"), **env}
        return subprocess.run([str(tmp_path / "task-tools"), *args], capture_output=True, text=True,
                              env=e, cwd=repo, timeout=60)

    def calls():
        f = cache / "calls.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
    return {"run": run, "calls": calls, "checks": checks, "mise": mise, "tmp": tmp_path}


def test_it_installs_and_writes_the_tools_for_task_checks(kit):
    r = kit["run"]("node@22", "python@3.11", MISE_GLOBAL_CONFIG_FILE="/evil.toml", MISE_EVIL="1")
    assert r.returncode == 0, r.stderr
    calls = kit["calls"]()
    assert [c["args"][0] for c in calls] == ["install", "env", "where"]
    assert calls[0]["args"] == ["install", "node@22", "python@3.11"]
    checks = kit["checks"]
    for c in calls:
        assert "MISE_EVIL" not in c["mise"]
        assert c["mise"]["MISE_GLOBAL_CONFIG_FILE"] == str(checks / "mise-empty" / "config.toml")
        assert c["mise"]["MISE_OVERRIDE_CONFIG_FILENAMES"] == "task-tools-none.toml"
        assert c["mise"]["MISE_DISABLE_BACKENDS"] == "asdf,vfox"
        assert Path(c["cwd"]).resolve() == (checks / "mise-empty").resolve()
    assert not (kit["tmp"] / "fetched").exists()                  # the pinned file was used
    assert not (kit["tmp"] / "repo_code_ran").exists()            # the repo's json.py was not imported
    # task_checks puts the LAST $GITHUB_PATH line first: venv, then mise's order
    from scripts.task_checks import _read_github_env, _read_github_path
    got = list(reversed(_read_github_path(checks / "setup-1.path", checks)))
    assert got[1:] == ["/tools/node/bin", "/tools/python/bin"]
    assert Path(got[0]).resolve() == (checks / "python" / "bin").resolve()
    assert (checks / "python" / "bin" / "python").exists()          # the task's own venv
    # only plain names, no loader / shell-startup variables, no value the reader would split
    assert _read_github_env(checks / "setup-1.env") == {"JAVA_HOME": "/tools/java"}


def test_without_python_there_is_no_venv(kit):
    assert kit["run"]("node@22").returncode == 0
    assert [c["args"][0] for c in kit["calls"]()] == ["install", "env"]
    assert not (kit["checks"] / "python").exists()


@pytest.mark.parametrize("bad", ["node", "node@22;id", "--version@1", "Node@22", "node@-1"])
def test_a_bad_argument_runs_nothing(kit, bad):
    r = kit["run"]("python@3.11", bad)
    assert r.returncode == 2 and kit["calls"]() == []


def test_a_changed_cached_mise_is_never_run(kit):
    """The cached mise is checked on every use: a file that is not the pinned one is
    fetched again (here the fetch fails), and it never runs."""
    kit["mise"].write_text(FAKE_MISE + "# changed\n")
    r = kit["run"]("node@22")
    assert r.returncode != 0 and (kit["tmp"] / "fetched").exists()
    assert kit["calls"]() == []


@pytest.mark.parametrize("machine, fetched", [("aarch64", "linux-arm64"), ("x86_64", "linux-x64")])
def test_a_fetched_mise_with_another_hash_is_not_kept(tmp_path, machine, fetched):
    """With no cached mise, task-tools fetches the pinned build for the machine and
    keeps it only when its sha256 is the pinned one."""
    shutil.copy(SCRIPT, tmp_path / "task-tools")
    fake = tmp_path / "fake"
    fake.mkdir()
    _bin(fake, "uname", f'[ "$1" = "-s" ] && echo Linux || echo {machine}\n')
    _bin(fake, "curl", 'while [ "$#" -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; last="$1"; shift; done\n'
                       f'echo "$last" > "{tmp_path}/url"; echo not-mise > "$out"\n')
    home = tmp_path / "home"
    r = subprocess.run(["bash", str(tmp_path / "task-tools"), "node@22"], capture_output=True, text=True,
                       timeout=30, env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}",
                                        "HOME": str(home), "GITHUB_PATH": str(tmp_path / "p"),
                                        "GITHUB_ENV": str(tmp_path / "e")})
    assert r.returncode == 1 and "expected" in r.stderr
    assert (tmp_path / "url").read_text().strip().endswith(f"-{fetched}")
    assert not [p for p in (home / ".local/share/task-tools").iterdir()]     # nothing kept


def test_the_script_checks_the_same_pattern_as_the_host():
    m = re.search(r"^TOOL_RE='\^(.*)\$'$", SCRIPT.read_text(), re.M)
    assert m and m.group(1) == pa.TOOL_RE.pattern
