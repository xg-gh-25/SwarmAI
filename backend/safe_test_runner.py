#!/usr/bin/env python3
"""Memory-safe test runner for SwarmAI backend.

Splits the test suite into batches and runs each in a subprocess to prevent
the 9GB+ RSS spike that occurs when 1759 tests run in a single process.

Usage:
    python safe_test_runner.py                    # Run all tests in batches
    python safe_test_runner.py -k "chat or sse"   # Run filtered tests in batches
    python safe_test_runner.py --batch-size 300    # Custom batch size
    python safe_test_runner.py -v                  # Verbose output
    python safe_test_runner.py --no-split          # Force single process (bypass safety)

How it works:
    1. Collect test node IDs via `pytest --collect-only -q`
    2. Split into batches of N tests (default: 200)
    3. Run each batch in a subprocess: `pytest <node_ids>`
    4. Aggregate results and report

Each subprocess starts with fresh memory — peak RSS per batch stays ~1-2GB
instead of accumulating to 9GB+ across all 1759 tests.

If pytest-xdist is installed, this script is unnecessary — xdist handles
the splitting natively. The conftest.py auto-injects `-n auto` when xdist
is detected.
"""
import subprocess
import sys
import os
import time
import json
from pathlib import Path

BACKEND_DIR = Path(__file__).parent
PYTHON = sys.executable
DEFAULT_BATCH_SIZE = 200


def collect_tests(extra_args: list[str], filter_expr: str | None = None) -> list[str]:
    """Collect test node IDs without running them."""
    cmd = [PYTHON, "-m", "pytest", "--collect-only", "-q", "--no-header"]

    if filter_expr:
        cmd.extend(["-k", filter_expr])

    # Pass through test paths and other args
    for arg in extra_args:
        if arg.startswith("tests/") or arg.startswith("--"):
            cmd.append(arg)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
        env=_clean_env(),
    )

    if result.returncode not in (0, 5):  # 5 = no tests collected
        print(f"Collection failed (exit {result.returncode}):")
        print(result.stderr)
        sys.exit(1)

    # Parse node IDs (lines like "tests/test_foo.py::TestClass::test_method")
    node_ids = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line and "::" in line and not line.startswith(("=", "-", " ")):
            node_ids.append(line)

    return node_ids


def run_batch(batch_num: int, total_batches: int, node_ids: list[str],
              extra_args: list[str]) -> tuple[int, float, str]:
    """Run a batch of tests in a subprocess. Returns (exit_code, duration, output)."""
    cmd = [PYTHON, "-m", "pytest"]
    # Disable xdist auto-inject for batches (we're already splitting)
    try:
        import xdist  # noqa: F401
        cmd.append("-n0")
    except ImportError:
        pass
    cmd.extend(node_ids)

    # Pass through display args
    for arg in extra_args:
        if arg in ("-v", "-vv", "--tb=short", "--tb=long", "--tb=no", "-x", "-s"):
            cmd.append(arg)

    print(f"\n{'='*60}")
    print(f"Batch {batch_num}/{total_batches} — {len(node_ids)} tests")
    print(f"{'='*60}")

    start = time.monotonic()
    result = subprocess.run(
        cmd,
        capture_output=False,  # Stream output in real-time
        cwd=str(BACKEND_DIR),
        env=_clean_env(),
    )
    duration = time.monotonic() - start

    return result.returncode, duration, ""


def _clean_env() -> dict:
    """Return env with proxy vars stripped (Claude sandbox sets them)."""
    env = os.environ.copy()
    for key in list(env.keys()):
        if "proxy" in key.lower():
            del env[key]
    return env


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Memory-safe test runner")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Tests per batch (default: {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--no-split", action="store_true",
                        help="Run all tests in single process (bypass safety)")
    parser.add_argument("-k", dest="filter_expr", help="pytest -k filter expression")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-x", "--exitfirst", action="store_true",
                        help="Stop on first batch failure")
    parser.add_argument("--tb", default="short", help="Traceback style")
    parser.add_argument("test_paths", nargs="*", help="Specific test files/dirs")

    args, unknown = parser.parse_known_args()

    # Check if xdist is available
    try:
        import xdist  # noqa: F401
        print("pytest-xdist detected — you can also use: pytest -n auto")
        print("(this script is a fallback for when xdist isn't installed)\n")
    except ImportError:
        pass

    if args.no_split:
        # Bypass: run everything in one process
        cmd = [PYTHON, "-m", "pytest"]
        if args.filter_expr:
            cmd.extend(["-k", args.filter_expr])
        if args.verbose:
            cmd.append("-v")
        cmd.append(f"--tb={args.tb}")
        cmd.extend(args.test_paths)
        cmd.extend(unknown)
        result = subprocess.run(cmd, cwd=str(BACKEND_DIR), env=_clean_env())
        sys.exit(result.returncode)

    # Collect tests
    extra_args = []
    if args.verbose:
        extra_args.append("-v")
    extra_args.append(f"--tb={args.tb}")
    extra_args.extend(args.test_paths)
    extra_args.extend(unknown)

    print("Collecting tests...")
    node_ids = collect_tests(extra_args, filter_expr=args.filter_expr)
    total = len(node_ids)

    if total == 0:
        print("No tests collected.")
        sys.exit(0)

    if total <= args.batch_size:
        print(f"{total} tests — small enough for single process (< {args.batch_size})")
        # Run directly, still in subprocess for memory isolation
        batches = [node_ids]
    else:
        # Split into batches
        batches = [
            node_ids[i:i + args.batch_size]
            for i in range(0, total, args.batch_size)
        ]
        print(f"{total} tests → {len(batches)} batches of ~{args.batch_size}")

    # Run batches
    results = []
    total_start = time.monotonic()

    for i, batch in enumerate(batches, 1):
        exit_code, duration, _ = run_batch(i, len(batches), batch, extra_args)
        results.append((i, len(batch), exit_code, duration))

        if exit_code != 0 and args.exitfirst:
            print(f"\nBatch {i} failed — stopping (-x flag)")
            break

    total_duration = time.monotonic() - total_start

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY — {total} tests in {len(batches)} batches ({total_duration:.1f}s)")
    print(f"{'='*60}")

    failed_batches = []
    for batch_num, count, exit_code, duration in results:
        status = "PASS" if exit_code == 0 else f"FAIL (exit {exit_code})"
        if exit_code != 0:
            failed_batches.append(batch_num)
        print(f"  Batch {batch_num}: {count:4d} tests — {status} ({duration:.1f}s)")

    if failed_batches:
        print(f"\n{len(failed_batches)} batch(es) failed: {failed_batches}")
        sys.exit(1)
    else:
        print(f"\nAll {total} tests passed across {len(batches)} batches.")
        sys.exit(0)


if __name__ == "__main__":
    main()
