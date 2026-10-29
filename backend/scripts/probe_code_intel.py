"""probe_code_intel — the code graph answers from the repo's current HEAD.

``impact``: a person confirms a local checkout for a Brain, its graph is built, a
commit then adds a new caller of a function, and ``impact`` (asked from outside the
daemon, as an agent asks it) lists that caller without anyone re-indexing — the
graph is brought up to HEAD when it is read. The negative asks the same question
with the refresh switched off: the answer then comes from the graph as built and
misses the new caller.

    "$SWARM_BACKEND_BIN" py -m scripts.probe_code_intel impact [negative]
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from scripts._probe_kit import run, tempdir, verdict

_STORE = "def save(x):\n    return x\n"
_OLD_CALLER = "from store import save\n\ndef post(x):\n    return save(x)\n"
_NEW_CALLER = "from store import save\n\ndef export_all(rows):\n    return [save(r) for r in rows]\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=probe@example.com",
                    "-c", "user.name=probe", *args], check=True, capture_output=True)


def _impact_holds(refresh: bool) -> tuple[bool, str]:
    from core.code_intel import invalidate_cache
    from core.code_intel.build import ensure_graph
    from core.code_intel.impact import impact
    from core.code_intel.local_roots import confirm, propose
    from jobs.paths import PROJECTS_DIR

    with tempdir() as d:
        repo = d / "app"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
        (repo / "store.py").write_text(_STORE, encoding="utf-8")
        (repo / "api.py").write_text(_OLD_CALLER, encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        project_dir = PROJECTS_DIR / "ProbeBrain"
        project_dir.mkdir(parents=True, exist_ok=True)
        propose("ProbeBrain", repo)
        confirm("ProbeBrain", repo)                       # what the Brain Hub card records
        invalidate_cache("ProbeBrain")
        built = ensure_graph(project_dir)
        if [r["action"] for r in built] != ["built"]:
            return False, f"the confirmed checkout's graph was not built: {built}"
        (repo / "export.py").write_text(_NEW_CALLER, encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "a new caller")
        results = impact("ProbeBrain", "store.py::save", refresh=refresh) or []
        invalidate_cache("ProbeBrain")
    callers = {(c.file, c.symbol) for r in results for c in r.callers}
    if ("api.py", "post") not in callers:
        return False, f"impact lost the caller the graph was built with: {sorted(callers)}"
    if ("export.py", "export_all") not in callers:
        return False, "impact missed the caller a commit added after the build"
    return True, ""


def impact_fresh(negative: bool) -> int:
    return verdict("CI_IMPACT_FRESH", negative, lambda: _impact_holds(True),
                   lambda: _impact_holds(False))


if __name__ == "__main__":
    raise SystemExit(run({"impact": impact_fresh}))
