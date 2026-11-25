"""The pinned runtime a replay runs on: Node, @playwright/test and Chromium (headless shell).

Installed by the product on first use into the app data folder, never relying on a Node
or a browser the person happens to have (Node is optional on the desktop). One
versioned folder per pin: ``<app data>/feature_map/runtime/node-<v>-pw-<v>/``; on a
machine with the replay user's shared location (a Hive: ``replay_user.shared_dir``) it is
``<shared>/runtime/node-<v>-pw-<v>/`` instead, outside the Hive user's home, which the
replay user has no way into.

- **Node**: the official archive for the platform, checked against the SHA-256 constant
  below (from nodejs.org SHASUMS256 of the pinned version). Same pattern as the Hive's
  pinned Caddy and mise: pinned, checksummed, downloaded on first use, not bundled.
- **@playwright/test**: ``runtime_lock/package.json`` + ``package-lock.json`` ship with the
  backend; the pinned Node's npm runs ``npm ci --ignore-scripts``, so every package is
  checked against the lockfile's sha512 integrity.
- **Chromium**: the pinned Playwright's own installer (``install --only-shell chromium``)
  into the runtime folder. Its archive has no checksum held here (Playwright pins the
  revision; integrity rests on its installer over HTTPS).

A failure (no network, a checksum mismatch, an unsupported platform) raises
``RuntimeUnavailable`` with the reason; a replay reports it as ``not_run``. A finished
install writes ``ready.json`` last, so a half-written folder is never used.

On Linux the headless shell also needs system libraries the runtime cannot carry (on Amazon
Linux 2023: libatk, libX11, libgbm, ...; ``hive/replay-host.sh`` installs them on a Hive).
``ensure`` checks them with ``ldd`` every time it hands out the runtime, and raises
``RuntimeUnavailable`` naming the missing ones: a browser that cannot start is an environment
problem (walks ``not_run``), never N failed walks. ``launch_check`` (``python -m
core.feature_map.runtime --launch``, run by ``hive/smoke_package.sh``) starts the shell and
renders text: without fonts it starts but lays out every glyph at zero width.
``SWARM_FEATURE_MAP_RUNTIME`` may point at an existing runtime folder of the same layout
(used by tests and by a machine that pre-provisions it).

Exports: NODE_VERSION, PLAYWRIGHT_VERSION, Runtime, RuntimeUnavailable, ensure, launch_check,
missing_libraries, runtime_dir.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

NODE_VERSION = "22.21.1"
PLAYWRIGHT_VERSION = "1.63.0"
NODE_SHA256 = {
    ("darwin", "arm64"): ("node-v22.21.1-darwin-arm64.tar.gz",
                          "c170d6554fba83d41d25a76cdbad85487c077e51fa73519e41ac885aa429d8af"),  # pragma: allowlist secret  (public nodejs.org SHASUMS256)
    ("darwin", "x64"): ("node-v22.21.1-darwin-x64.tar.gz",
                        "8e3dc89614debe66c2a6ad2313a1adb06eb37db6cd6c40d7de6f7d987f7d1afd"),  # pragma: allowlist secret  (public nodejs.org SHASUMS256)
    ("linux", "arm64"): ("node-v22.21.1-linux-arm64.tar.gz",
                         "c86830dedf77f8941faa6c5a9c863bdfdd1927a336a46943decc06a38f80bfb2"),  # pragma: allowlist secret  (public nodejs.org SHASUMS256)
    ("linux", "x64"): ("node-v22.21.1-linux-x64.tar.gz",
                       "219a152ea859861d75adea578bdec3dce8143853c13c5187f40c40e77b0143b2"),  # pragma: allowlist secret  (public nodejs.org SHASUMS256)
    ("win32", "x64"): ("node-v22.21.1-win-x64.zip",
                       "3c624e9fbe07e3217552ec52a0f84e2bdc2e6ffa7348f3fdfb9fbf8f42e23fcf"),  # pragma: allowlist secret  (public nodejs.org SHASUMS256)
}
NODE_BASE_URL = f"https://nodejs.org/dist/v{NODE_VERSION}/"
LOCK_DIR = Path(__file__).resolve().parent / "runtime_lock"
INSTALL_TIMEOUT_S = 1200        # a hang guard for a download or an npm ci that never ends


class RuntimeUnavailable(RuntimeError):
    """The pinned runtime could not be installed or used; ``reason`` says why."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Runtime:
    folder: Path
    node: Path
    playwright_cli: Path          # node_modules/@playwright/test/cli.js
    playwright_pkg: Path          # node_modules/@playwright/test (linked into sandboxes)
    browsers: Path                # PLAYWRIGHT_BROWSERS_PATH

    def env(self) -> dict[str, str]:
        return {"PLAYWRIGHT_BROWSERS_PATH": str(self.browsers),
                "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1"}


def _platform() -> tuple[str, str]:
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "win32"}.get(platform.system(), "")
    machine = platform.machine().lower()
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(machine, machine)
    return system, arch


def runtime_dir() -> Path:
    from config import get_app_data_dir
    from core.feature_map.replay_user import shared_dir
    shared = shared_dir()
    base = shared if shared is not None else Path(get_app_data_dir()) / "feature_map"
    return base / "runtime" / f"node-{NODE_VERSION}-pw-{PLAYWRIGHT_VERSION}"


def _layout(folder: Path) -> Runtime:
    system, _arch = _platform()
    node_dir = folder / "node"
    node = node_dir / ("node.exe" if system == "win32" else "bin/node")
    pkg = folder / "pw" / "node_modules" / "@playwright" / "test"
    return Runtime(folder, node, pkg / "cli.js", pkg, folder / "browsers")


def headless_shell(rt: Runtime) -> Path | None:
    """The pinned Chromium headless shell binary of *rt* (None when it is not there)."""
    name = "chrome-headless-shell.exe" if _platform()[0] == "win32" else "chrome-headless-shell"
    hits = sorted(rt.browsers.glob(f"chromium_headless_shell-*/*/{name}"))
    return hits[-1] if hits else None


def missing_libraries(rt: Runtime) -> list[str]:
    """On Linux, the shared libraries the headless shell cannot load (``ldd`` "not found");
    [] elsewhere, and when ldd cannot tell (no ldd, no shell, not an ELF binary)."""
    if _platform()[0] != "linux":
        return []
    shell, ldd = headless_shell(rt), shutil.which("ldd")
    if shell is None or ldd is None:
        return []
    try:
        proc = subprocess.run([ldd, str(shell)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return sorted({line.split("=>")[0].strip() for line in proc.stdout.splitlines()
                   if "=> not found" in line})


def _checked(rt: Runtime) -> Runtime:
    missing = missing_libraries(rt)
    if missing:
        raise RuntimeUnavailable(
            f"the pinned Chromium cannot load {', '.join(missing)}: install the system packages "
            "that provide them (Amazon Linux 2023: the list in hive/replay-host.sh; Debian or "
            f"Ubuntu: npx playwright@{PLAYWRIGHT_VERSION} install-deps chromium)")
    return rt


# Starts the headless shell and lays out text; prints {"version", "text_width"}. The pinned
# package's index.mjs comes in FM_PLAYWRIGHT (a file: URL).
_LAUNCH_JS = r"""
const { chromium } = await import(process.env.FM_PLAYWRIGHT);
const browser = await chromium.launch({ timeout: 60000 });
try {
  const page = await browser.newPage();
  await page.setContent('<span id="t" style="font-family:sans-serif">Feature Map replay ✓</span>');
  const width = await page.evaluate(() => document.getElementById('t').getBoundingClientRect().width);
  console.log(JSON.stringify({ version: browser.version(), text_width: width }));
} finally {
  await browser.close();
}
"""


def launch_check(rt: Runtime, timeout_s: int = 180) -> dict:
    """Start *rt*'s headless shell, render a line of text, and return ``{"version",
    "text_width"}``. Raises ``RuntimeUnavailable`` when it does not start or lays the text
    out at zero width (no fonts)."""
    from urllib.request import pathname2url
    _checked(rt)
    index = (rt.playwright_pkg / "index.mjs").resolve()
    url = "file:" + pathname2url(str(index)) if _platform()[0] != "win32" else index.as_uri()
    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "HOME", "USERPROFILE", "TMPDIR", "TEMP", "TMP", "SystemRoot", "LANG")}
    env.update(rt.env())
    env["FM_PLAYWRIGHT"] = url
    try:
        proc = subprocess.run([str(rt.node), "--input-type=module", "-e", _LAUNCH_JS],
                              capture_output=True, text=True, timeout=timeout_s, env=env)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeUnavailable(f"the pinned Chromium did not start in {timeout_s}s") from exc
    except OSError as exc:
        raise RuntimeUnavailable(f"the pinned Node could not start: {exc}") from exc
    if proc.returncode != 0:
        tail = [ln for ln in (proc.stderr or proc.stdout or "").strip().splitlines()
                if ln.strip()][-4:]
        raise RuntimeUnavailable(f"the pinned Chromium did not start (exit {proc.returncode}): "
                                 + " | ".join(tail)[:800])
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise RuntimeUnavailable(f"the launch check printed no result: {proc.stdout[-200:]}") from exc
    if not out.get("text_width"):
        raise RuntimeUnavailable("the pinned Chromium starts but renders no text (no fonts: "
                                 "install fontconfig and a font)")
    return out


def _ready(rt: Runtime) -> bool:
    return (rt.folder / "ready.json").is_file() and rt.node.is_file() and rt.playwright_cli.is_file()


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as out:  # noqa: S310 — pinned https URL
        shutil.copyfileobj(resp, out)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_extract(archive: Path, dest: Path) -> Path:
    """Extract the Node archive refusing any member outside *dest*; returns its top folder."""
    dest = dest.resolve()
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                target = (dest / name).resolve()
                if not target.is_relative_to(dest):
                    raise RuntimeUnavailable(f"node archive member escapes the folder: {name}")
            for name in zf.namelist():
                zf.extract(name, dest)        # one by one, each checked above
            tops = {Path(n).parts[0] for n in zf.namelist() if n}
    else:
        with tarfile.open(archive) as tf:
            for m in tf.getmembers():
                target = (dest / m.name).resolve()
                if not target.is_relative_to(dest) or m.isdev():
                    raise RuntimeUnavailable(f"node archive member escapes the folder: {m.name}")
                if (m.issym() or m.islnk()) and not (target.parent / m.linkname).resolve().is_relative_to(dest):
                    raise RuntimeUnavailable(f"node archive link escapes the folder: {m.name}")
            tf.extractall(dest, filter="data")   # the stdlib's own guard on top of ours
            tops = {Path(m.name).parts[0] for m in tf.getmembers() if m.name}
    if len(tops) != 1:
        raise RuntimeUnavailable("node archive has no single top folder")
    return dest / tops.pop()


def _run(argv: list[str], *, cwd: Path, env: dict[str, str], what: str) -> None:
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True,
                              timeout=INSTALL_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeUnavailable(f"{what} did not finish in {INSTALL_TIMEOUT_S}s") from exc
    except OSError as exc:
        raise RuntimeUnavailable(f"{what} could not start: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise RuntimeUnavailable(f"{what} failed (exit {proc.returncode}): {' | '.join(tail)}")


def _install(folder: Path, download: Callable[[str, Path], None]) -> Runtime:
    system, arch = _platform()
    pin = NODE_SHA256.get((system, arch))
    if pin is None:
        raise RuntimeUnavailable(f"no pinned Node for {system}/{arch}")
    archive_name, expected = pin
    folder.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".install-", dir=str(folder.parent)))
    try:
        archive = work / archive_name
        try:
            download(NODE_BASE_URL + archive_name, archive)
        except OSError as exc:
            raise RuntimeUnavailable(f"node download failed: {exc}") from exc
        got = _sha256(archive)
        if got != expected:
            raise RuntimeUnavailable(f"node archive checksum mismatch: expected {expected[:12]}…, "
                                     f"got {got[:12]}…; nothing installed")
        stage = work / "stage"
        stage.mkdir()
        top = _safe_extract(archive, work / "x")
        top.rename(stage / "node")
        rt = _layout(stage)
        pw = stage / "pw"
        pw.mkdir()
        for name in ("package.json", "package-lock.json"):
            shutil.copy2(LOCK_DIR / name, pw / name)
        npm_cli = (stage / "node" / ("node_modules" if system == "win32" else "lib/node_modules")
                   / "npm" / "bin" / "npm-cli.js")
        base_env = {k: v for k, v in os.environ.items()
                    if k in ("PATH", "HOME", "USERPROFILE", "TMPDIR", "TEMP", "TMP", "SystemRoot",
                             "windir", "COMSPEC", "PATHEXT", "HTTPS_PROXY", "HTTP_PROXY",
                             "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
                             "npm_config_cache", "LANG")}
        env = {**base_env, "PATH": str(rt.node.parent) + os.pathsep + base_env.get("PATH", "")}
        _run([str(rt.node), str(npm_cli), "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
             cwd=pw, env=env, what="npm ci of the pinned @playwright/test")
        _run([str(rt.node), str(rt.playwright_cli), "install", "--only-shell", "chromium"],
             cwd=pw, env={**env, "PLAYWRIGHT_BROWSERS_PATH": str(stage / "browsers")},
             what="Chromium install")
        (stage / "ready.json").write_text(json.dumps(
            {"node": NODE_VERSION, "playwright": PLAYWRIGHT_VERSION, "platform": [system, arch],
             "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}), encoding="utf-8")
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)     # a half-written folder (no ready.json)
        stage.rename(folder)
        return _layout(folder)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _lock(folder: Path):
    """One installer at a time: an exclusive lock directory, waited on (an install
    takes minutes); a stale lock older than the install cap is taken over."""
    lock = folder.with_name(folder.name + ".lock")
    folder.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + INSTALL_TIMEOUT_S
    while True:
        try:
            lock.mkdir()
            return lock
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > INSTALL_TIMEOUT_S:
                    shutil.rmtree(lock, ignore_errors=True)
                    continue
            except OSError:
                continue
            if time.time() > deadline:
                raise RuntimeUnavailable("another install of the runtime did not finish")
            time.sleep(1)


def ensure(*, install: bool = True,
           download: Callable[[str, Path], None] = _download) -> Runtime:
    """The ready runtime; installed first when missing (and *install*). Raises
    ``RuntimeUnavailable`` with the reason when it cannot be had."""
    override = os.environ.get("SWARM_FEATURE_MAP_RUNTIME")
    if override:
        rt = _layout(Path(override))
        if not rt.node.is_file() or not rt.playwright_cli.is_file():
            raise RuntimeUnavailable(f"SWARM_FEATURE_MAP_RUNTIME={override} is not a runtime folder")
        return _checked(rt)
    folder = runtime_dir()
    rt = _layout(folder)
    if _ready(rt):
        return _checked(rt)
    if not install:
        raise RuntimeUnavailable("the pinned runtime is not installed")
    lock = _lock(folder)
    try:
        if _ready(rt):
            return _checked(rt)
        return _checked(_install(folder, download))
    finally:
        shutil.rmtree(lock, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover — manual provisioning, hive/smoke_package.sh
    # python -m core.feature_map.runtime [--launch]: install (when missing) and, with --launch,
    # start the browser and render text. JSON on stdout; exit 2 when unavailable.
    try:
        rt = ensure()
        result = {"ok": True, "folder": str(rt.folder)}
        if "--launch" in sys.argv[1:]:
            result.update(launch_check(rt))
        print(json.dumps(result))
    except RuntimeUnavailable as exc:
        print(json.dumps({"ok": False, "reason": exc.reason}))
        sys.exit(2)
