"""verify / replay: sandbox → Playwright (or commands) → per-walk results (design §6).

For each selected feature's target the sandbox prepares and starts the target (and the
targets it needs); then, per start state, the pinned Playwright runs the selected specs
with a generated config — never the repo's own config — that sets:

- two viewport projects, ``regular`` 1440×900 and ``compact`` 1024×640, so a dialog or
  popover that only fits a large window fails ``toBeInViewport({ ratio: 1 })`` in
  ``compact``;
- ``retries: 1``: Playwright's own ``flaky`` outcome (failed, then passed on the retry) is
  reported as ``flaky``, never as a pass;
- ``screenshot: 'on'``, ``trace: 'retain-on-failure'``, the JSON reporter, the locale;
- a proxy that refuses every connection with loopback bypassed: a page reaches no other
  host, by name or by IP (Playwright proxies loopback too unless it is bypassed — seen in
  the design's spike, where every page failed until the bypass was set).

The sandbox copy of the feature folder gets ``node_modules/@playwright/test`` linked to
the pinned package, so a spec's ``import '@playwright/test'`` resolves the product's copy
even when the repo has its own (Node takes the nearest ``node_modules``; the link
realpaths to the same module instance the CLI runs).

A spec's start state is its tag ``@start:<name>`` (none = ``default``). A command walk
(``run:``) runs its argv in the sandbox; exit 0 = pass. Results:

    {"feature", "walk", "project", "result": pass|fail|flaky|not_run, "reason",
     "duration_s", "evidence": [paths], "error"}

Exports: replay, Selection.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.feature_map.files import Feature, FeatureMap
from core.feature_map.runtime import Runtime, RuntimeUnavailable, ensure
from core.feature_map.sandbox import Sandbox, SandboxError, _kill_group, loopback_url

PLAYWRIGHT_TIMEOUT_S = 1800     # a hang guard for one Playwright run, never a budget
VIEWPORTS = (("regular", 1440, 900), ("compact", 1024, 640))
_STATE_TAG = re.compile(r"@start:([a-z0-9_-]+)")


@dataclass
class Selection:
    """The features to replay. A quarantined walk is replayed like any other (so it can
    earn its release); the ledger's keys, not this selection, keep it from blocking."""
    features: list[Feature] = field(default_factory=list)


def _not_run(features: list[Feature], reason: str, detail: str = "") -> list[dict]:
    out = []
    for f in features:
        out.append({"feature": f.id, "walk": f.title if f.run else "*",
                    "project": "command" if f.run else "*", "result": "not_run",
                    "reason": reason, "error": detail[:600], "duration_s": 0.0, "evidence": []})
    return out


def _link_pinned(folder_copy: Path, rt: Runtime) -> None:
    nm = folder_copy / "node_modules" / "@playwright"
    nm.mkdir(parents=True, exist_ok=True)
    link = nm / "test"
    if link.exists() or link.is_symlink():
        if link.is_dir() and not link.is_symlink():
            shutil.rmtree(link)
        else:
            link.unlink()
    try:
        os.symlink(rt.playwright_pkg, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(rt.playwright_pkg)],
                       capture_output=True, check=True, timeout=30)


def _config(sb: Sandbox, folder_copy: Path, rt: Runtime, base_url: str, locale: str,
            out_dir: Path, report: Path) -> Path:
    from urllib.request import pathname2url
    index = (rt.playwright_pkg / "index.mjs").resolve()
    url = "file:" + pathname2url(str(index)) if sys.platform != "win32" else index.as_uri()
    cfg = {
        "testDir": str(folder_copy), "outputDir": str(out_dir), "retries": 1, "workers": 1,
        "timeout": 60_000, "reporter": [["json", {"outputFile": str(report)}]],
        "use": {"baseURL": base_url, "screenshot": "on", "trace": "retain-on-failure",
                "locale": locale,
                "proxy": {"server": "http://127.0.0.1:9", "bypass": "127.0.0.1,localhost,[::1]"}},
        "projects": [{"name": n, "use": {"viewport": {"width": w, "height": h}}}
                     for n, w, h in VIEWPORTS],
    }
    path = sb.dir / f"playwright.config.{int(time.time() * 1000)}.mjs"
    path.write_text(f"import {{ defineConfig }} from {json.dumps(url)};\n"
                    f"export default defineConfig({json.dumps(cfg)});\n", encoding="utf-8")
    return path


def _pw(rt: Runtime, sb: Sandbox, target, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {**sb.env(target), **rt.env()}
    env["PATH"] = str(rt.node.parent) + os.pathsep + env.get("PATH", "")
    kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
              if sys.platform == "win32" else {"start_new_session": True})
    proc = subprocess.Popen([str(rt.node), str(rt.playwright_cli), "test", *args], cwd=str(cwd),
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            **kwargs)
    try:
        out, _ = proc.communicate(timeout=PLAYWRIGHT_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        _kill_group(proc)
        raise SandboxError("not ready", f"Playwright did not finish in {PLAYWRIGHT_TIMEOUT_S}s") from exc
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, "")


def _walk_specs(report: dict) -> list[tuple[str, str, list[str], dict]]:
    """``(file, title, tags, test)`` for every test of a Playwright JSON report. The file
    is the top-level suite's (relative to testDir); a spec's own ``file`` is relative to
    the config's folder and is not used."""
    out = []

    def visit(suite: dict, titles: list[str], file: str) -> None:
        here = titles + ([suite["title"]] if suite.get("title") and suite.get("title") != file else [])
        for spec in suite.get("specs") or []:
            title = " › ".join([*here, spec["title"]])
            tags = list(spec.get("tags") or []) + _STATE_TAG.findall(spec.get("title", ""))
            for t in spec.get("tests") or []:
                out.append((file, title, tags, t))
        for child in suite.get("suites") or []:
            visit(child, here, file)
    for s in report.get("suites") or []:
        visit(s, [], s.get("file") or s.get("title") or "")
    return out


def _state_of(tags: list[str]) -> str:
    for tag in tags:
        m = _STATE_TAG.search(tag if tag.startswith("@") else "@" + tag)
        if m:
            return m.group(1)
    return "default"


_RESULT = {"expected": "pass", "unexpected": "fail", "flaky": "flaky", "skipped": "not_run"}


def contained(paths: list[str], folder: Path) -> list[str]:
    """The paths that are files inside *folder* (a spec may name any file as an
    attachment; only what the replay itself wrote is evidence)."""
    root = Path(folder).resolve()
    out = []
    for raw in paths:
        p = Path(raw).resolve()
        if p.is_file() and p.is_relative_to(root):
            out.append(str(p))
    return out


def _web(fmap: FeatureMap, target, features: list[Feature], sel: Selection, sb: Sandbox,
         rt: Runtime, folder_copy: Path) -> list[dict]:
    by_spec = {f.spec: f for f in features if f.spec}
    base_url = loopback_url(sb.expand(target.base_url or "http://127.0.0.1/"), "base_url")
    files = sorted(by_spec)
    listing = sb.dir / f"list-{target.id}.json"
    cfg = _config(sb, folder_copy, rt, base_url, target.locale, sb.dir / "out-list", listing)
    _pw(rt, sb, target, ["--config", str(cfg), "--list", *files], folder_copy)
    try:
        listed = _walk_specs(json.loads(listing.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return _not_run(features, "bad file", "Playwright could not list the specs")
    states: dict[str, set[str]] = {}
    for file, _title, tags, _t in listed:
        states.setdefault(_state_of(tags), set()).add(file)
    results: list[dict] = []
    seen_specs = set()
    for state in sorted(states, key=lambda s: (s != "default", s)):
        try:
            sb.setup(fmap, target, state)
        except SandboxError as exc:
            hit = [by_spec[f] for f in sorted(states[state]) if f in by_spec]
            results += _not_run(hit, exc.reason, exc.detail)
            seen_specs |= states[state]
            continue
        report_path = sb.dir / f"report-{target.id}-{state}.json"
        out_dir = sb.dir / f"out-{target.id}-{state}"
        cfg = _config(sb, folder_copy, rt, base_url, target.locale, out_dir, report_path)
        args = ["--config", str(cfg)]
        # this state's tests only (by their @start tag)
        if state != "default":
            args += ["--grep", rf"@start:{re.escape(state)}\b"]
        else:
            args += ["--grep-invert", r"@start:(?!default\b)"]
        started = time.monotonic()
        _pw(rt, sb, target, [*args, *sorted(states[state])], folder_copy)
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            hit = [by_spec[f] for f in sorted(states[state]) if f in by_spec]
            results += _not_run(hit, "launch failed", "Playwright wrote no report")
            seen_specs |= states[state]
            continue
        elapsed = time.monotonic() - started
        for file, title, _tags, test in _walk_specs(report):
            feature = by_spec.get(file)
            if feature is None:
                continue
            seen_specs.add(file)
            runs = test.get("results") or []
            last = runs[-1] if runs else {}
            error = next(((r.get("error") or {}).get("message", "") for r in runs if r.get("error")), "")
            evidence = contained([a["path"] for r in runs for a in r.get("attachments") or []
                                  if a.get("path") and a.get("name") in ("screenshot", "trace")],
                                 sb.dir)
            status = test.get("status") or ""
            results.append({
                "feature": feature.id, "walk": title, "project": test.get("projectName") or "",
                "result": _RESULT.get(status, "fail"),
                "reason": "skipped by the spec" if status == "skipped" else "",
                "error": re.sub(r"\x1b\[[0-9;]*m", "", error)[:1200],
                "duration_s": round(sum((r.get("duration") or 0) for r in runs) / 1000, 2) or round(elapsed, 2),
                "evidence": evidence, "retries": len(runs) - 1, "last_status": last.get("status")})
    missing = [f for spec, f in by_spec.items() if spec not in seen_specs]
    results += _not_run(missing, "bad file", "the spec holds no test")
    return results


def _command(target, feature: Feature, sb: Sandbox) -> dict:
    argv = [sb.expand(a) for a in feature.run or ()]
    log = sb.dir / "logs" / f"{target.id}-walk-{feature.id}.log"   # where Sandbox._run writes it
    started = time.monotonic()
    try:
        sb._run(argv, target, "fail", f"walk-{feature.id}")
        result, error = "pass", ""
    except SandboxError as exc:
        if exc.reason.startswith("needs "):
            return {**_not_run([feature], exc.reason, exc.detail)[0]}
        result, error = "fail", exc.detail
    return {"feature": feature.id, "walk": feature.title, "project": "command", "result": result,
            "reason": "", "error": error[:1200], "duration_s": round(time.monotonic() - started, 2),
            "evidence": [str(log)] if log.is_file() else []}


def replay(fmap: FeatureMap, root: Path, sel: Selection, *,
           runtime: Runtime | None = None, keep: bool = False) -> tuple[list[dict], Path | None]:
    """Replay the selected features of *fmap* against a sandbox copy of *root*.
    Returns ``(results, sandbox_dir)``. Every service is stopped before it returns; the
    sandbox directory is left for the caller, because evidence paths point into it: the
    caller copies the evidence, then removes the directory (unless ``--keep``). When it
    raises, it removes the directory itself unless *keep* (the caller's ``--keep``)."""
    if not sel.features:
        return [], None
    root = Path(root)
    needs_web = any(f.spec for f in sel.features)
    rt = runtime
    if needs_web and rt is None:
        try:
            rt = ensure()
        except RuntimeUnavailable as exc:
            web = [f for f in sel.features if f.spec]
            rest = [f for f in sel.features if not f.spec]
            results = _not_run(web, "runtime unavailable", exc.reason)
            if rest:
                more, sbdir = replay(fmap, root, Selection(rest), runtime=None, keep=keep)
                return results + more, sbdir
            return results, None
    in_repo = fmap.folder.resolve().is_relative_to(root.resolve())
    results: list[dict] = []
    sb = Sandbox(root, keep=True)
    try:
        sb.__enter__()
    except (subprocess.SubprocessError, OSError) as exc:
        if sb.dir:
            shutil.rmtree(sb.dir, ignore_errors=True)
        return _not_run(sel.features, "launch failed", f"the checkout could not be copied: {exc}"), None
    try:
        if in_repo:
            folder_copy = sb.checkout / fmap.folder.resolve().relative_to(root.resolve())
        else:
            folder_copy = sb.dir / "features"
            shutil.copytree(fmap.folder, folder_copy, ignore=shutil.ignore_patterns("node_modules"))
        if needs_web:
            _link_pinned(folder_copy, rt)
        by_target: dict[str, list[Feature]] = {}
        for f in sel.features:
            by_target.setdefault(f.target, []).append(f)
        for tid in sorted(by_target):
            target = fmap.targets[tid]
            feats = by_target[tid]
            try:
                sb.start(fmap, target)
            except SandboxError as exc:
                results += _not_run(feats, exc.reason, exc.detail)
                continue
            web = [f for f in feats if f.spec]
            if web:
                try:
                    results += _web(fmap, target, web, sel, sb, rt, folder_copy)
                except SandboxError as exc:
                    results += _not_run(web, exc.reason, exc.detail)
            for f in feats:
                if f.run:
                    results.append(_command(target, f, sb))
    except BaseException as exc:
        # nothing is returned, so no caller can remove it: stop its services, then remove
        # it, or (keep) name it on the exception so the caller can report where it is
        for _sid, proc in reversed(sb.procs):
            _kill_group(proc)
        sb.procs.clear()
        if keep:
            exc.sandbox_dir = str(sb.dir)
        else:
            shutil.rmtree(sb.dir, ignore_errors=True)
        raise
    finally:
        for _sid, proc in reversed(sb.procs):
            _kill_group(proc)
        sb.procs.clear()
    return results, sb.dir
