"""code_intel_replay — what the callers report would have said about a past commit.

Replays one commit of any git repo: builds a throwaway code graph of the commit's
parent, then reports the commit's change against it, exactly as a review before that
commit landed would have seen it. Nothing in the repo or in any Brain is touched (two
temporary worktrees and a temporary graph, all removed afterwards).

    "$SWARM_BACKEND_BIN" py -m scripts.code_intel_replay --repo <checkout> --commit <sha> [--json]

Use it to check the report against a breakage that really happened: the commit that
broke a caller should list that caller under "will break".
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    from core.code_intel.local_roots import GIT_SAFE
    return subprocess.run(["git", *GIT_SAFE, "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


def replay(repo: Path, commit: str):
    from core.code_intel import CodeIntelDb, changed
    from core.code_intel.build import rebuild
    from core.code_intel.graph_store import GraphStore

    from core.code_intel.changed import filter_programs
    if commit.startswith("-"):
        raise SystemExit("a commit, not an option")
    if filter_programs(repo):
        raise SystemExit("the repo's config names a filter program; a checkout would run it")
    parent = _git(repo, "rev-parse", "--verify", "--end-of-options", f"{commit}~1").strip()
    sha = _git(repo, "rev-parse", "--verify", "--end-of-options", commit).strip()
    with tempfile.TemporaryDirectory(prefix="ci-replay-") as tmp:
        tmp_path = Path(tmp)
        before, after = tmp_path / "before", tmp_path / "after"
        try:
            _git(repo, "worktree", "add", "--detach", "-q", str(before), parent)
            _git(repo, "worktree", "add", "--detach", "-q", str(after), sha)
            graph = GraphStore(tmp_path / "g.db")
            rebuild(graph, before, head=parent)
            ref = CodeIntelDb(db_path=tmp_path / "g.db", repo=repo.name, repo_root=before)
            saved = changed._project_graph_for
            changed._project_graph_for = lambda checkout, project=None: (
                "replay", tmp_path, ref, graph)
            try:
                rep = changed._report(after, base=parent, refresh=False, tree=None,
                                      project=None, budget_s=None)
            finally:
                changed._project_graph_for = saved
                graph.close()
            return rep
        finally:
            for wt in (before, after):        # the two worktrees this made, nothing else
                if wt.exists():
                    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force",
                                    str(wt)], capture_output=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.code_intel_replay")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    from core.code_intel import changed
    rep = replay(Path(args.repo).resolve(), args.commit)
    print(json.dumps(asdict(rep), ensure_ascii=False, indent=1) if args.json
          else changed.render(rep))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
