#!/usr/bin/env python3
"""⑥ Code-Intel Refresher CLI (DDD-native, portable).

Regenerates `code-intel.json` from a bound repo's REAL import graph — the runnable
mechanism the s_ai-ready-repo SKILL describes. Pure stdlib + git; no SwarmAI backend.

Narrow ⑥ refresh mode (spec §3.6): produces ONLY the derived projection
(`code-intel.json`), NEVER touches the 4 DDD docs (OWNed cognition). The projection is
gitignored / regenerated at each consumer end — it is not a bindings.yaml member.

Usage:
    python refresh_code_intel.py <repo_path> [--out <file>] [--project <name>]

    # default output: <repo>/.ai-ready/code-intel.json (or $SWARM_WORKSPACE/.artifacts/…)
    python refresh_code_intel.py /path/to/bound/repo
    python refresh_code_intel.py . --out code-intel.json

Exit 0 = written. Exit 1 = error (e.g. not a git repo). Exit 2 = zero edges found
(likely a language-detection miss — surfaced loud, not silently shipped).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling helper
from ai_ready_helpers import extract_import_graph, resolve_output_path


def build_code_intel(repo_path: Path, project_name: str | None) -> dict:
    """Assemble the code-intel.json v2 projection from the REAL import graph.

    depends_on / depended_by come ONLY from extract_import_graph (file:line-cited
    edges) — never guessed (the derived-projection contract).
    """
    graph = extract_import_graph(repo_path)
    stats = graph.get("stats", {})
    return {
        "schema_version": 2,
        "project": project_name or repo_path.name,
        "generated_from": "import-graph (real, file:line-cited — not guessed)",
        "stats": stats,
        "modules": graph.get("modules", []),
        "edges": graph.get("edges", []),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="⑥ regenerate code-intel.json from a repo")
    ap.add_argument("repo_path", help="path to the ⑤-bound repo to index")
    ap.add_argument("--out", default=None, help="output file (default: <resolved>/code-intel.json)")
    ap.add_argument("--project", default=None, help="project name (default: repo dir name)")
    args = ap.parse_args()

    repo = Path(args.repo_path).resolve()
    # Robust git-repo check: ask git to walk up to the real root (handles a path nested
    # any depth below the .git dir — a fixed 2-level parent check would false-fail).
    import subprocess as _sp
    try:
        _rc = _sp.run(["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
                      capture_output=True, text=True, timeout=10)
        _in_git = _rc.returncode == 0 and _rc.stdout.strip() == "true"
    except (OSError, _sp.SubprocessError):
        _in_git = False
    if not _in_git:
        print(f"ERROR: {repo} is not inside a git work tree (extract_import_graph uses `git ls-files`)",
              file=sys.stderr)
        sys.exit(1)

    intel = build_code_intel(repo, args.project)

    if args.out:
        out_file = Path(args.out).resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
    else:
        # DDD-local by default (resolve_output_path honors $SWARM_WORKSPACE, else repo-adjacent)
        out_dir = resolve_output_path(repo, project_name=args.project)
        out_file = out_dir / "code-intel.json"

    out_file.write_text(json.dumps(intel, indent=2), encoding="utf-8")
    edges = intel["stats"].get("edges_found", 0)
    print(json.dumps({
        "written": str(out_file),
        "modules": len(intel["modules"]),
        "edges": edges,
        "files_scanned": intel["stats"].get("files_scanned", 0),
    }, indent=2))

    if edges == 0:
        # A repo with code but 0 edges = language-detection miss; fail LOUD, don't ship a
        # silently-empty projection that reads as "no dependencies".
        print("WARNING: 0 edges found — check language detection (is this repo empty/unsupported?)",
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
