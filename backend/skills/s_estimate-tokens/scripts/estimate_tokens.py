#!/usr/bin/env python3
"""Token estimator — delegates to the CANONICAL ContextDirectoryLoader.estimate_tokens.

This REPLACES the old `wc -w * 1.8` shell heuristic, which
counted a whole CJK paragraph as ~1 word (massive under-count) and hardcoded a
200000-token window. It calls the SAME calibrated estimator the prompt assembly
uses (CJK 1.1 tok/char, Latin 2.2 tok/word — measured against the real opus-4-8
tokenizer), so the number this skill reports matches what actually enters the
context window.

It does NOT re-implement the estimator (a vendored copy would re-create exactly
the drift this change exists to kill — Gate-1 finding E). Instead it discovers
the repo root by walking up for `backend/core/context_directory_loader.py` and
imports the canonical. Works from the agent session (cwd under the repo /
workspace). If the canonical cannot be found, it FAILS LOUD (non-zero exit with a
clear message) rather than silently falling back to a wrong heuristic.

Usage:
    estimate_tokens.py [--window N] <file> [<file> ...]
    <command> | estimate_tokens.py [--window N]      # reads stdin

--window defaults to the effective context-file assembly budget for a 1M model,
derived at runtime from context_directory_loader.EFFECTIVE_1M_BUDGET (base budget −
ephemeral headroom; currently 133K). Run with --help to see the live value.
Override for other budgets.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Effective context-file budget for our default 1M-context models — DERIVED from
# the budget SoT (context_directory_loader.EFFECTIVE_1M_BUDGET = BUDGET_1M_MODEL −
# EPHEMERAL_HEADROOM), so a base-budget change propagates here automatically.
# Falls back to the current value only if the canonical module is unreachable
# (e.g. run from a frozen bundle with no source) — a fail-SAFE default, not a 2nd
# source of truth: when the module IS reachable, this always tracks it.
_DEFAULT_WINDOW_FALLBACK = 133_000


def _load_effective_1m_budget() -> int:
    """Resolve EFFECTIVE_1M_BUDGET from the canonical loader, reusing the same
    repo-discovery as the estimator. Returns the fallback if unreachable."""
    import os
    try:  # under "$SWARM_BACKEND_BIN" py the backend is importable as is
        from core.context_directory_loader import EFFECTIVE_1M_BUDGET  # type: ignore
        return int(EFFECTIVE_1M_BUDGET)
    except Exception:
        pass

    marker = Path("backend") / "core" / "context_directory_loader.py"
    candidates = []
    env_root = os.environ.get("SWARM_REPO_ROOT")
    if env_root:
        candidates.append(Path(env_root).resolve())
    candidates.extend(Path(__file__).resolve().parents)
    candidates.extend(Path.cwd().resolve().parents)
    candidates.append(Path.cwd().resolve())
    seen = set()
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        if (base / marker).is_file():
            backend = str(base / "backend")
            if backend not in sys.path:
                sys.path.insert(0, backend)
            try:
                from core.context_directory_loader import EFFECTIVE_1M_BUDGET  # type: ignore
                return int(EFFECTIVE_1M_BUDGET)
            except Exception:
                break
    return _DEFAULT_WINDOW_FALLBACK


DEFAULT_WINDOW = _load_effective_1m_budget()


def _load_canonical_estimator():
    """Walk up from this file AND cwd to find the repo, import the canonical.

    Returns the estimate_tokens callable, or raises RuntimeError (fail loud).
    """
    import os
    try:  # under "$SWARM_BACKEND_BIN" py the backend is importable as is
        from core.context_directory_loader import ContextDirectoryLoader  # type: ignore
        return ContextDirectoryLoader.estimate_tokens
    except ImportError:
        pass

    marker = Path("backend") / "core" / "context_directory_loader.py"
    candidates = []
    # 1. Explicit override — survives invocation from ANY cwd / a projected copy
    #    that has no backend/ alongside it (Gate-2 finding E: the projected
    #    .claude/skills/ copy and the agent workspace ~/.swarm-ai/SwarmWS have no
    #    backend/, so __file__/cwd discovery alone fails when run from there).
    env_root = os.environ.get("SWARM_REPO_ROOT")
    if env_root:
        candidates.append(Path(env_root).resolve())
    # 2. Walk up from the script location and the cwd (works in the dev checkout).
    here = Path(__file__).resolve()
    candidates.extend(here.parents)
    candidates.extend(Path.cwd().resolve().parents)
    candidates.append(Path.cwd().resolve())

    seen = set()
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        if (base / marker).is_file():
            backend = str(base / "backend")
            if backend not in sys.path:
                sys.path.insert(0, backend)
            from core.context_directory_loader import (  # type: ignore
                ContextDirectoryLoader,
            )
            return ContextDirectoryLoader.estimate_tokens

    raise RuntimeError(
        "Could not locate the canonical token estimator "
        "(backend/core/context_directory_loader.py) by walking up from "
        f"{here} or cwd {Path.cwd()}. This skill MUST run inside the SwarmAI "
        "repo/workspace so it can call the single calibrated estimator — it "
        "deliberately does NOT carry its own copy (which would drift)."
    )


def _report(name: str, text: str, estimate, window: int) -> None:
    tokens = estimate(text)
    pct = (tokens / window * 100) if window else 0.0
    print(f"File: {name}")
    print(f"Estimated tokens: {tokens:,}")
    print(f"Context usage: {pct:.2f}% of {window:,} tokens")
    print()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Estimate tokens via the canonical calibrated estimator."
    )
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                        help=f"Context window for the %% calc (default {DEFAULT_WINDOW}).")
    parser.add_argument("files", nargs="*", help="File path(s); omit to read stdin.")
    args = parser.parse_args(argv)

    try:
        estimate = _load_canonical_estimator()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.files:
        any_ok = False
        for fp in args.files:
            p = Path(fp)
            if not p.is_file():
                print(f"Error: File '{fp}' not found", file=sys.stderr)
                continue
            _report(p.name, p.read_text(encoding="utf-8", errors="replace"),
                     estimate, args.window)
            any_ok = True
        return 0 if any_ok else 1

    # stdin mode
    data = sys.stdin.read()
    if not data.strip():
        print("Usage: estimate_tokens.py [--window N] <file> ... | <cmd> | estimate_tokens.py",
              file=sys.stderr)
        return 1
    _report("(stdin)", data, estimate, args.window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
