"""probe_code_intel — the callers of a change are right, without anyone re-indexing.

``impact``: a local checkout is declared for a Brain (``bind --local``'s
``declare``) and its graph is built; a commit then adds a new caller; a working-tree
change touches ``Store.save`` and ``save_all``. The callers report
(``core.code_intel.changed``, what every review gets) must:

1. list the caller the commit added (the graph is brought up to HEAD when read);
2. list a call on a value whose type the code states (``s: Store``) as a caller of
   ``Store.save``, and a call on a value of unknown type (``conn.save()``) only as a
   name-only lead, never as a caller;
3. list a function passed as a value (``Thread(target=save_all)``) and a test patch
   by dotted path (``patch("store.save_all")``) as references.

The negative breaks it two ways and must fail either way: the refresh off (the new
caller is missed), and the receiver typing off (``receivers.TYPED_LANGS`` empty: the
typed call becomes a guess).

    "$SWARM_BACKEND_BIN" py -m scripts.probe_code_intel impact [negative]
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from scripts._probe_kit import run, tempdir, verdict

_FILES = {
    "store.py": ("class Store:\n    def save(self, x):\n        return x\n\n\n"
                 "def save_all(rows):\n    return [Store().save(r) for r in rows]\n"),
    "api.py": ("from store import Store\n\n\ndef post(s: Store, x):\n    return s.save(x)\n\n\n"
               "def guess(conn, x):\n    return conn.save(x)\n"),
    "jobs.py": ("import threading\n\nfrom store import save_all\n\n\n"
                "def start(rows):\n    return threading.Thread(target=save_all, args=(rows,))\n"),
    "test_store.py": ("from unittest.mock import patch\n\n\n"
                      "def test_it():\n    with patch(\"store.save_all\"):\n        pass\n"),
}
_NEW_CALLER = ("from store import Store\n\n\n"
               "def export(s: Store, rows):\n    return [s.save(r) for r in rows]\n")
_CHANGE = _FILES["store.py"].replace("def save(self, x):", "def save(self, x, *, strict=False):") \
                            .replace("def save_all(rows):", "def save_all(rows, *, strict=False):")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=probe@example.com",
                    "-c", "user.name=probe", *args], check=True, capture_output=True)


def _report_holds(refresh: bool = True, typed: bool = True) -> tuple[bool, str]:
    from core.code_intel import invalidate_cache, receivers
    from core.code_intel.build import ensure_graph
    from core.code_intel.changed import report
    from core.code_intel.local_roots import declare
    from jobs.paths import PROJECTS_DIR

    saved = receivers.TYPED_LANGS
    if not typed:
        receivers.TYPED_LANGS = frozenset()
    try:
        with tempdir() as d:
            repo = d / "app"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
            for name, text in _FILES.items():
                (repo / name).write_text(text, encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "init")
            project_dir = PROJECTS_DIR / "ProbeBrain"
            project_dir.mkdir(parents=True, exist_ok=True)
            declare("ProbeBrain", repo)                      # what bind --local records
            invalidate_cache("ProbeBrain")
            built = ensure_graph(project_dir)
            if [r["action"] for r in built] != ["built"]:
                return False, f"the declared checkout's graph was not built: {built}"
            (repo / "export.py").write_text(_NEW_CALLER, encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "a new caller")
            (repo / "store.py").write_text(_CHANGE, encoding="utf-8")
            rep = report(repo, refresh=refresh, caller="probe")
            invalidate_cache("ProbeBrain")
    finally:
        receivers.TYPED_LANGS = saved
    by_id = {c.id: c for c in rep.symbols}
    save, save_all = by_id.get("store.py::Store.save"), by_id.get("store.py::save_all")
    if save is None or save_all is None:
        return False, f"the changed symbols are not listed: {sorted(by_id)} ({rep.note})"
    calls = {(x["file"], x["symbol"]) for x in save.outside}
    if ("api.py", "post") not in calls:
        return False, f"the call on a typed value is not a caller of Store.save: {sorted(calls)}"
    if ("export.py", "export") not in calls:
        return False, "the caller a commit added after the build is missing"
    if ("api.py", "guess") in calls or "api.py" not in save.guessed:
        return False, "the call on a value of unknown type is not kept apart as a name-only lead"
    refs = {(x["file"], x["how"]) for x in save_all.outside}
    if not {("jobs.py", "reference"), ("test_store.py", "reference")} <= refs:
        return False, f"save_all passed as a value or patched is not a reference: {sorted(refs)}"
    return True, ""


def _broken() -> tuple[bool, str]:
    """Holds only if the check still passes with either part switched off."""
    no_refresh = _report_holds(refresh=False)
    no_typing = _report_holds(typed=False)
    if no_refresh[0] or no_typing[0]:
        return True, "a part was switched off and the check still held"
    return False, f"refresh off: {no_refresh[1]}; typing off: {no_typing[1]}"


def impact(negative: bool) -> int:
    return verdict("CI_IMPACT", negative, _report_holds, _broken)


if __name__ == "__main__":
    raise SystemExit(run({"impact": impact}))
