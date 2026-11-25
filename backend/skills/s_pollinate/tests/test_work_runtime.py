"""The renderer runs on SwarmAI's pinned browser runtime, never on Python Playwright.

* ``work.py render`` in a child process of the backend's own interpreter, with every
  ``playwright`` import refused, renders the 5-slide fixture through the runtime.
* No product file of the skill imports Python Playwright or tells the agent to install it.
* When the runtime cannot be had (``ensure`` raises, or ``core.feature_map.runtime``
  cannot be imported) ``work.py`` exits 2 with one line carrying the reason, and ``cut``
  leaves no staged version behind.
"""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_common import (  # noqa: E402
    FIXTURES, FONT_CSS_URL, SKILL, copy_fixture, env, need_browser, run, work, work_render,
)

__all__ = ["env"]

# Runs a script with some top-level packages refused, then reports what was imported.
# argv: <comma separated refused packages> <script> <script args...>
CHILD = r"""
import json, runpy, sys
refused = [p for p in sys.argv[1].split(",") if p]

class Refuse:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in refused:
            raise ModuleNotFoundError(f"No module named {name!r} (refused by the test)", name=name)
        return None

sys.meta_path.insert(0, Refuse())
for name in list(sys.modules):
    if name.split(".")[0] in refused:
        del sys.modules[name]
script = sys.argv[2]
sys.argv = [script] + sys.argv[3:]
code = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
try:
    import playwright  # noqa: F401
    importable = True
except ImportError:
    importable = False
mods = sorted(sys.modules)
sys.stderr.write("CHILD-REPORT " + json.dumps({
    "playwright_importable": importable,
    "playwright_loaded": [m for m in mods if m.split(".")[0] == "playwright"],
    "runtime_loaded": "core.feature_map.runtime" in sys.modules,
}) + "\n")
sys.exit(code)
"""


def _child(refused, script, args, env_vars, cwd):
    proc = subprocess.run([sys.executable, "-c", CHILD, ",".join(refused), str(script), *args],
                          capture_output=True, text=True, env=env_vars, cwd=str(cwd), timeout=90)
    lines = [ln for ln in proc.stderr.splitlines() if ln.startswith("CHILD-REPORT ")]
    report = json.loads(lines[-1][len("CHILD-REPORT "):]) if lines else None
    errors = [ln for ln in proc.stderr.splitlines() if not ln.startswith("CHILD-REPORT ")]
    return proc, report, errors


def _seed_font_cache(cache: Path) -> None:
    """The fixture's web font, already in the cache (the child has no fake download)."""
    entry = cache / hashlib.sha256(FONT_CSS_URL.encode("utf-8")).hexdigest()[:32]
    entry.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "fonts" / "test-blocks.ttf", entry / "000-000000000000.ttf")
    (entry / "style.css").write_text(
        "@font-face{font-family:'Pollinate Test Blocks';"
        "src:url(\"000-000000000000.ttf\") format('truetype');}", encoding="utf-8")


def _child_env(tmp: Path) -> dict:
    import os
    e = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    e["SWARM_WORKSPACE"] = str(tmp / "ws")
    e["POLLINATE_FONT_CACHE"] = str(tmp / "font-cache")
    return e


def test_render_without_python_playwright(env, capsys):
    need_browser()
    tmp = env["tmp"]
    d = copy_fixture(tmp / "deck")
    code, _ = run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "960x540"], capsys)
    assert code == 0
    _seed_font_cache(tmp / "font-cache")
    backend = SKILL.parent.parent
    proc, report, errors = _child(["playwright"], SKILL / "scripts" / "work.py",
                                  ["render", "--dir", str(d), "--formats", "html,pdf,png"],
                                  _child_env(tmp), backend)
    assert proc.returncode == 0, errors
    assert report == {"playwright_importable": False, "playwright_loaded": [], "runtime_loaded": True}
    out = json.loads(proc.stdout)
    assert out["failed_checks"] == []
    ids = json.loads((d / "work.json").read_text())["slides"]
    assert list(out["outputs"]["slides"]) == ids
    pngs = sorted((d / "out" / "slides").glob("*.png"))
    assert [p.name for p in pngs] == [f"{n:02d}-{i}.png" for n, i in enumerate(ids, 1)]
    assert all(Image.open(p).size == (960, 540) for p in pngs)
    from pypdf import PdfReader
    assert len(PdfReader(str(d / "out" / "deck.pdf")).pages) == 5
    assert (d / "review" / "sheet.jpg").is_file()
    assert env["http"].calls == []  # the in-process fake download was not what rendered


def _imports(path: Path) -> set:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_no_python_playwright_on_the_product_path():
    scripts = SKILL / "scripts"
    for path in sorted(scripts.glob("*.py")):
        assert "playwright" not in _imports(path), f"{path.name} imports Python Playwright"
    head = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    deps = next(ln for ln in head.splitlines() if ln.startswith("python-optional-deps:"))
    assert "playwright" not in deps
    for doc in [SKILL / "SKILL.md", SKILL / "INSTRUCTIONS.md", *sorted((SKILL / "kinds").glob("*.md"))]:
        text = doc.read_text(encoding="utf-8")
        assert "pip install --user playwright" not in text and "playwright install" not in text, doc.name


def _cut_ready_deck(env, capsys) -> Path:
    d = copy_fixture(env["tmp"] / "deck")
    code, _ = run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "960x540"], capsys)
    assert code == 0
    code, _ = run(["open", "--dir", str(d), "--scope", ""], capsys)
    assert code == 0
    return d


def test_runtime_unavailable_exits_2(env, capsys, monkeypatch):
    runtime = work_render._runtime_module()
    calls = []

    def unavailable(**kw):
        calls.append(kw)
        raise runtime.RuntimeUnavailable("node download failed: no network")

    monkeypatch.delenv("SWARM_FEATURE_MAP_RUNTIME", raising=False)
    monkeypatch.setattr(runtime, "runtime_dir", lambda: env["tmp"] / "no-runtime")
    monkeypatch.setattr(runtime, "ensure", unavailable)
    d = _cut_ready_deck(env, capsys)
    capsys.readouterr()
    assert work.main(["cut", "--dir", str(d), "--formats", "png"]) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert err == ["work_render: installing the pinned browser runtime (a one-time download on first use)",
                   "error: the pinned browser runtime is not available: node download failed: no network"]
    assert calls == [{}]                       # one ensure, which installs on first use
    assert sorted(p.name for p in (d / ".versions").iterdir()) == ["v001"]   # nothing staged
    assert not list(d.glob(".render-tmp-*"))
    code = work.main(["render", "--dir", str(d)])
    assert code == 2


def test_installed_runtime_problem_says_no_install(env, capsys, monkeypatch, tmp_path):
    """An installed runtime that cannot run (missing system libraries) gives its reason only."""
    runtime = work_render._runtime_module()
    folder = tmp_path / "rt"
    folder.mkdir()
    (folder / "ready.json").write_text("{}")

    def missing_libs(**kw):
        raise runtime.RuntimeUnavailable("the pinned Chromium cannot load libgbm.so.1")

    monkeypatch.delenv("SWARM_FEATURE_MAP_RUNTIME", raising=False)
    monkeypatch.setattr(runtime, "runtime_dir", lambda: folder)
    monkeypatch.setattr(runtime, "ensure", missing_libs)
    src = tmp_path / "d.html"
    src.write_text("<section>x</section>")
    capsys.readouterr()
    assert work_render.main(["--src", str(src), "--out", str(tmp_path / "o")]) == 2
    assert capsys.readouterr().err.strip().splitlines() == [
        "error: the pinned browser runtime is not available: the pinned Chromium cannot load libgbm.so.1"]


def test_driver_env_is_an_allowlist(monkeypatch, tmp_path):
    """Cloud settings and variables that change Node or Playwright never reach the driver."""
    class Rt:
        playwright_pkg = tmp_path / "pw"

        def env(self):
            return {"PLAYWRIGHT_BROWSERS_PATH": "/b", "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1"}

    for k, v in {"AWS_PROFILE": "sentinel-profile", "NODE_OPTIONS": "--require x",
                 "NODE_TLS_REJECT_UNAUTHORIZED": "0", "DEBUG": "pw:*",
                 "FONTCONFIG_FILE": "/f/fonts.conf", "TZ": "Asia/Tokyo"}.items():
        monkeypatch.setenv(k, v)
    e = work_render.driver_env(Rt())
    assert "sentinel-profile" not in e.values()
    assert not {"AWS_PROFILE", "NODE_OPTIONS", "NODE_TLS_REJECT_UNAUTHORIZED", "DEBUG"} & set(e)
    assert e["FONTCONFIG_FILE"] == "/f/fonts.conf" and e["TZ"] == "Asia/Tokyo"
    assert e["PLAYWRIGHT_BROWSERS_PATH"] == "/b"
    assert e["POLLINATE_PLAYWRIGHT"] == (tmp_path / "pw" / "index.mjs").resolve().as_uri()


def test_thumbnails_exit_2_when_a_browser_request_fails(env, monkeypatch, capsys):
    import runpy

    class Failing:
        def __init__(self):
            raise work_render.RenderError("the browser driver stopped during 'open'")

    monkeypatch.setattr(work_render, "_Browser", Failing)
    monkeypatch.setattr(sys, "argv", ["render_style_thumbnails.py", "--force"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SKILL / "scripts" / "render_style_thumbnails.py"), run_name="__main__")
    assert exc.value.code == 2
    assert capsys.readouterr().err.strip().splitlines()[-1] == (
        "error: the browser driver stopped during 'open'")


def test_runtime_module_missing_exits_2(tmp_path):
    """Outside a SwarmAI backend (no core package, no backend above the script) it exits 2."""
    scripts = tmp_path / "skill" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("work_render.py", "work_render_driver.mjs"):
        shutil.copyfile(SKILL / "scripts" / name, scripts / name)
    src = tmp_path / "d.html"
    src.write_text("<section>x</section>", encoding="utf-8")
    proc, report, errors = _child(["core", "playwright"], scripts / "work_render.py",
                                  ["--src", str(src), "--out", str(tmp_path / "o")],
                                  _child_env(tmp_path), tmp_path)
    assert proc.returncode == 2
    assert errors == ["error: the renderer needs SwarmAI's pinned browser runtime "
                      "(core.feature_map.runtime), which cannot be imported here; run the script "
                      'with "$SWARM_BACKEND_BIN" py']
    assert report["runtime_loaded"] is False

