#!/usr/bin/env python3
"""bvt_probe.py — positive and negative checks for BVT golden cases.

A BVT case proves something only if its check can fail. Each subcommand runs
one check; a trailing ``negative`` argument runs the SAME check on input where
it must fail (an unknown project, an empty graph, a repo that does not exist,
a noise-heavy document), so the runtime teeth check
(``eval_runner._verify_canary_teeth``) can see the probe go red.

Output contract (same as eval_spine_probe.py):
  positive: ``<NAME>_OK`` exit 0, or ``<NAME>_FAIL <why>`` exit 1
  negative: ``<NAME>_TEETH`` exit 0 when the check failed as it should,
            ``<NAME>_FAIL <why>`` exit 1 when it passed on broken input
The negative never prints ``<NAME>_OK``.

Subcommands (PROJECT defaults to SwarmAI):
  route   [ROUTE]       code-intel hook answers a route query with its handler
  load    [PROJECT]     load_project_graph returns a graph for the project
  summary [PROJECT]     the project graph has more than 100 nodes and edges
  fresh   [PROJECT]     check_freshness reads the graph's repo (current_head set)
  detect  [PROJECT]     a file in this repo resolves to the project
  noise   DOC           ddd-noise gives DOC a PASS verdict
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from argparse import Namespace
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

_DEFAULT_PROJECT = "SwarmAI"
_NO_PROJECT = "__bvt_probe_no_such_project__"


# ── checks: each returns (holds, why) and is shared by both modes ──────────────

def _route_holds(route: str) -> tuple[bool, str]:
    from core.code_intel.code_intel_hook import create_code_intel_hook
    result = create_code_intel_hook()("Grep", {"pattern": route, "path": ""}) or {}
    ctx = (result.get("hookSpecificOutput") or {}).get("additionalContext", "") or ""
    if "Route Match" in ctx and route in ctx:
        return True, ""
    return False, f"no route match for {route}: {ctx[:120]!r}"


def _load_holds(project: str) -> tuple[bool, str]:
    from core.code_intel import load_project_graph
    graph = load_project_graph(project)
    if graph is not None and hasattr(graph, "get_codebase_summary"):
        return True, ""
    return False, f"no graph for {project}"


def _summary_holds(graph) -> tuple[bool, str]:
    s = graph.get_codebase_summary()
    nodes, edges = s.get("total_nodes", 0), s.get("total_edges", 0)
    if nodes > 100 and edges > 100:
        return True, ""
    return False, f"nodes={nodes} edges={edges}"


def _fresh_holds(graph) -> tuple[bool, str]:
    from core.code_intel.freshness import FreshnessResult, check_freshness
    r = check_freshness(graph)
    if isinstance(r, FreshnessResult) and r.current_head:
        return True, ""
    return False, f"git HEAD not read: {getattr(r, 'reason', '')!r}"


def _detect_holds(path: str, project: str) -> tuple[bool, str]:
    from core.code_intel import detect_project_from_path
    got = detect_project_from_path(path)
    if got == project:
        return True, ""
    return False, f"{path} -> {got!r}, expected {project!r}"


def _noise_report(doc: str) -> str:
    from scripts.artifact_cli import cmd_ddd_noise
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cmd_ddd_noise(Namespace(doc=doc, project=None), None)
    return out.getvalue()


def _noise_holds(doc: str) -> tuple[bool, str]:
    text = _noise_report(doc)
    if '"verdict": "PASS"' in text:
        return True, ""
    return False, f"verdict not PASS: {text[:160]!r}"


def _noise_fails_noisy_doc(doc: str) -> tuple[bool, str]:
    """For the negative: the check 'holds' (= is broken) unless the noisy doc
    gets an explicit FAIL verdict. A missing doc or an error is not teeth."""
    text = _noise_report(doc)
    if '"verdict": "FAIL"' in text:
        return False, ""
    return True, f"noisy doc not judged FAIL: {text[:160]!r}"


def _empty_graph(tmp: Path):
    from core.code_intel.graph_store import GraphStore
    return GraphStore(tmp / "empty.db")


_NOISY_ENTRY = (
    "- [guideline] **stale operational note {i}** body text. (2020-01-0{i})\n"
    "  <!-- ref:0 | last:none | decay:dormant | source:manual -->\n"
)


# ── subcommands ────────────────────────────────────────────────────────────────

def _run(name: str, negative: bool, positive, broken) -> int:
    """Run the positive check, or in negative mode the same check on broken
    input (``broken`` is a zero-arg callable returning (holds, why))."""
    if not negative:
        holds, why = positive()
        if holds:
            print(f"{name}_OK")
            return 0
        print(f"{name}_FAIL {why}")
        return 1
    holds, why = broken()
    if holds:
        print(f"{name}_FAIL negative passed on broken input")
        return 1
    print(f"{name}_TEETH")
    return 0


def main(argv: list[str]) -> int:
    args = list(argv)
    negative = bool(args) and args[-1] == "negative"
    if negative:
        args = args[:-1]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, rest = args[0], args[1:]
    arg = rest[0] if rest else None
    project = arg or _DEFAULT_PROJECT

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if cmd == "route":
            route = arg or "/api/chat/sessions"
            return _run("ROUTE", negative, lambda: _route_holds(route),
                        lambda: _route_holds("/api/__bvt_probe_no_such_route__"))
        if cmd == "load":
            return _run("GRAPH_LOAD", negative, lambda: _load_holds(project),
                        lambda: _load_holds(_NO_PROJECT))
        if cmd == "summary":
            def positive():
                ok, why = _load_holds(project)
                if not ok:
                    return ok, why
                from core.code_intel import load_project_graph
                return _summary_holds(load_project_graph(project))
            return _run("GRAPH_SUMMARY", negative, positive,
                        lambda: _summary_holds(_empty_graph(tmp)))
        if cmd == "fresh":
            def positive():
                ok, why = _load_holds(project)
                if not ok:
                    return ok, why
                from core.code_intel import load_project_graph
                return _fresh_holds(load_project_graph(project))

            def broken():
                graph = _empty_graph(tmp)
                graph.set_meta("repo_root", str(tmp / "no-such-repo"))
                return _fresh_holds(graph)
            return _run("FRESHNESS", negative, positive, broken)
        if cmd == "detect":
            here = str(_BACKEND / "core" / "session_unit.py")
            return _run("DETECT", negative, lambda: _detect_holds(here, project),
                        lambda: _detect_holds(str(tmp / "outside" / "x.py"), project))
        if cmd == "noise":
            if not arg:
                print("noise needs a DOC path", file=sys.stderr)
                return 2

            def broken():
                from core.ddd_paths import ddd_write_path
                doc = ddd_write_path(tmp / "NoisyProject", "IMPROVEMENT.md")
                doc.write_text("## Lessons\n\n" + "".join(
                    _NOISY_ENTRY.format(i=i) for i in range(1, 5)), encoding="utf-8")
                return _noise_fails_noisy_doc(str(doc))
            return _run("DDD_NOISE", negative, lambda: _noise_holds(arg), broken)
    print(f"unknown subcommand {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
