#!/usr/bin/env python3
"""Post-build verification: ensures the PyInstaller binary has all capabilities.

Run this AFTER `build-backend.sh` and BEFORE cutting a release.
It spawns the built binary, hits the health endpoint, and checks every
capability that can silently degrade in production.

Usage:
    python scripts/verify_build.py                          # verify daemon binary
    python scripts/verify_build.py /path/to/python-backend  # verify specific binary

Exit codes:
    0 = all capabilities verified
    1 = one or more capabilities missing (DO NOT RELEASE)

This script exists because of the sqlite_vec incident (2026-04-15):
18 modules worked in dev but were missing from the PyInstaller binary
for 5 days. Graceful degradation meant no crash, no error — just
silently broken vector search in production.

The principle: if two modes can diverge, verify both before shipping.
"""

from __future__ import annotations

import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Force UTF-8 stdout/stderr on Windows (cp1252 can't encode emoji)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Capability Manifest ──────────────────────────────────────────────
# Every capability that can silently degrade MUST be listed here.
# Format: (name, import_path_or_check, severity)
#   severity: "critical" = blocks release, "important" = warning

CAPABILITY_MANIFEST = [
    # ── Core modules (try/except imports) ──
    ("sqlite_vec",          "sqlite_vec",                    "critical"),
    ("psutil",              "psutil",                        "critical"),
    ("slack_bolt",          "slack_bolt",                    "important"),
    ("slack_sdk",           "slack_sdk",                     "important"),
    ("requests",            "requests",                      "important"),
    ("bcrypt",              "bcrypt",                        "critical"),
    ("cryptography",        "cryptography",                  "critical"),
    ("yaml",                "yaml",                          "critical"),
    ("httpx",               "httpx",                         "critical"),
    ("numpy",               "numpy",                         "important"),
    ("amazon_transcribe",   "amazon_transcribe",             "critical"),
    ("awscrt",              "awscrt",                        "critical"),
    # Bare-import checks stay "important": import success != AST functional. The
    # get_parser old-ABI bug (run_2e46f2af) had BOTH imports succeeding while the
    # AST path was dead. The CRITICAL gate is the FUNCTIONAL probe (__native__ below).
    ("tree_sitter",         "tree_sitter",                   "important"),
    ("tree_sitter_langs",   "tree_sitter_language_pack",     "important"),

    # ── Local modules (must be bundled) ──
    ("vec_db",              "core.vec_db",                   "critical"),
    ("recall_engine",       "core.recall_engine",            "critical"),
    ("embedding_client",    "core.embedding_client",         "critical"),
    ("knowledge_store",     "core.knowledge_store",          "critical"),
    ("memory_embeddings",   "core.memory_embeddings",        "critical"),
    ("transcript_indexer",  "core.transcript_indexer",       "critical"),
    ("memory_index",        "core.memory_index",             "critical"),
    ("manifest_loader",     "core.manifest_loader",          "critical"),
    ("llm_optimizer",       "core.llm_optimizer",            "critical"),
    ("memory_validation",   "core.memory_validation",        "critical"),
    ("locked_write",        "scripts.locked_write",          "critical"),
    ("session_router",      "core.session_router",           "critical"),
    ("session_unit",        "core.session_unit",             "critical"),
    ("prompt_builder",      "core.prompt_builder",           "critical"),
    ("security_hooks",      "core.security_hooks",           "critical"),
    ("evolution_optimizer",  "core.evolution_optimizer",     "critical"),
    ("skill_fitness",       "core.skill_fitness",            "critical"),
    ("session_miner",       "core.session_miner",            "critical"),
    ("skill_registry",      "core.skill_registry",           "critical"),
    ("voice_transcribe",    "core.voice_transcribe",         "critical"),
    ("voice_synthesize",    "core.voice_synthesize",         "critical"),
    ("distillation_hook",   "hooks.distillation_hook",       "critical"),
    ("evolution_hook",      "hooks.evolution_maintenance_hook", "critical"),
    ("install_daemon",      "channels.install_backend_daemon", "important"),
    ("jobs_bedrock",        "jobs.bedrock",                  "important"),
    ("estimation_learner",  "jobs.estimation_learner",       "important"),

    # ── Data files (must be bundled or deployed) ──
    ("skills_dir",          "__data__:skills",               "critical"),
    ("context_dir",         "__data__:context",              "critical"),
    ("templates_dir",       "__data__:templates",            "critical"),
    ("mcp_catalog",         "__data__:mcp-catalog.json",     "critical"),
    ("cli_tools",           "__data__:required-cli-tools.json", "critical"),

    # ── Native extensions ──
    # vec0_dylib: CI runners often bundle sqlite3 without enable_load_extension.
    # App gracefully degrades (VEC_AVAILABLE guards), so "important" not "critical".
    ("vec0_dylib",          "__native__:sqlite_vec/vec0",    "important"),
    # tree_sitter/parse: FUNCTIONAL AST probe (constructs Parser, parses bytes,
    # asserts a node) — CRITICAL because code-intel is now source-level AST and a
    # build where the grammar imports but fails to parse would silently revert the
    # whole indexer to the regex fallback (run_2e46f2af). The parser DOES fall back
    # to regex (parser.py _tree_sitter_live gate), so nothing crashes — but that
    # degrade is a SILENT quality regression (approximate symbols, no precise spans),
    # not a benign one like vec0's CI-runner sqlite limitation. tree_sitter needs
    # only pure-Python wheels + the bundled grammar (no env-specific fragility), so
    # a failing probe means a genuinely broken build, not a benign CI difference →
    # critical is safe (won't false-block) and warranted (catches the silent revert).
    ("tree_sitter_ast",     "__native__:tree_sitter/parse",  "critical"),
]


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill process and children. Works on macOS, Linux, and Windows."""
    if platform.system() != "Windows":
        # Unix: kill entire process group (catches MCP child processes)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError, AttributeError):
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError, AttributeError):
                proc.kill()
    else:
        # Windows: taskkill /T kills the process tree
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
        except Exception:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def find_free_port() -> int:
    """Find an available port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def verify_binary(binary_path: str) -> tuple[list[str], list[str], list[str]]:
    """Launch the binary and verify all capabilities.

    Returns: (passed, failed_critical, failed_important)
    """
    port = find_free_port()
    env = {
        **os.environ,
        "PORT": str(port),
        "SWARMAI_MODE": "daemon",
        "DATABASE_TYPE": "sqlite",
        # Gates verify-import/verify-data/verify-native endpoints
        "SWARMAI_VERIFY_BUILD": "1",
    }

    print(f"\n{'='*60}")
    print(f"  SwarmAI Build Verification")
    print(f"  Binary: {binary_path}")
    print(f"  Port:   {port}")
    print(f"{'='*60}\n")

    # Start the binary
    # start_new_session=True → own process group (Unix killpg); harmless on Windows
    proc = subprocess.Popen(
        [binary_path, "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    # Drain stdout in background thread to prevent pipe buffer deadlock.
    # macOS pipe buffer is 64KB — if binary writes more than that during
    # startup (common with 86 skills + code intel + hooks logging), it
    # blocks on write() and never reaches the health endpoint.
    import threading
    captured_output: list[bytes] = []

    def _drain_stdout():
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            captured_output.append(chunk)

    drain_thread = threading.Thread(target=_drain_stdout, daemon=True)
    drain_thread.start()

    try:
        # Wait for health endpoint
        # 180s: PyInstaller one-file mode extracts 200MB+ to temp on first run.
        # On macOS, uvicorn import alone takes ~88s during extraction (measured 2026-06-07).
        # On CI runners with slow I/O, this can be even longer.
        if not _wait_for_health(port, timeout=180):
            print("❌ Binary failed to start within 180s")
            # Dump captured stdout for diagnosis
            if proc.poll() is not None:
                print(f"  Process exited with code {proc.returncode}")
            drain_thread.join(timeout=2)
            out = b"".join(captured_output)
            if out:
                print(f"  Last output:\n{out.decode('utf-8', errors='replace')[-2000:]}")
            return [], ["binary_startup"], []

        # Verify capabilities via the binary's Python environment
        passed, failed_critical, failed_important = _verify_capabilities(port)

        # Guard the --thinking-display CLI flag (Opus 4.8 thinking-summary fix
        # depends on it; flag is .hideHelp() hidden and silently droppable).
        cli = _find_bundled_claude_cli(binary_path)
        if cli is None:
            failed_important.append("thinking_display_flag")
            print("  🟡 thinking_display_flag       bundled claude CLI not found (dev?)")
        else:
            ok, detail = _check_thinking_display_flag(str(cli))
            bucket = passed if ok else failed_critical
            bucket.append("thinking_display_flag")
            print(f"  {'✅' if ok else '🔴'} thinking_display_flag       {detail}")

        return passed, failed_critical, failed_important

    finally:
        # Cross-platform process cleanup
        _kill_process_tree(proc)


def _wait_for_health(port: int, timeout: int = 30) -> bool:
    """Poll health endpoint until ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            url = f"http://127.0.0.1:{port}/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read())
                if data.get("status") == "healthy":
                    print(f"✅ Health endpoint OK (v{data.get('version', '?')})\n")
                    return True
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            pass
        time.sleep(0.5)
    return False


def _verify_capabilities(port: int) -> tuple[list[str], list[str], list[str]]:
    """Check each capability against the running binary."""
    passed = []
    failed_critical = []
    failed_important = []

    for name, check, severity in CAPABILITY_MANIFEST:
        ok = False
        detail = ""

        if check.startswith("__data__:"):
            # Data file check — verify via /health or filesystem
            data_path = check.split(":", 1)[1]
            ok, detail = _check_data_via_health(port, data_path)
        elif check.startswith("__native__:"):
            # Native extension — verify via import + load
            native_path = check.split(":", 1)[1]
            ok, detail = _check_native_via_import(port, native_path)
        else:
            # Module import check
            ok, detail = _check_module_via_endpoint(port, check)

        bucket = passed if ok else (failed_critical if severity == "critical" else failed_important)
        bucket.append(name)

        status = "✅" if ok else ("🔴" if severity == "critical" else "🟡")
        print(f"  {status} {name:<25} {detail}")

    return passed, failed_critical, failed_important


def _check_module_via_endpoint(port: int, module_path: str) -> tuple[bool, str]:
    """Ask the running binary to import a module."""
    try:
        # Use the verify endpoint to check imports
        url = f"http://127.0.0.1:{port}/api/system/verify-import?module={module_path}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            if data.get("available"):
                return True, ""
            return False, data.get("error", "not found")
    except Exception:
        # Fallback: try to import in current process (for data checks)
        try:
            __import__(module_path)
            return True, "(verified via direct import)"
        except ImportError as e:
            return False, str(e)


def _check_data_via_health(port: int, data_path: str) -> tuple[bool, str]:
    """Check if a data file/directory exists in the binary's bundle."""
    try:
        url = f"http://127.0.0.1:{port}/api/system/verify-data?path={data_path}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("exists", False), data.get("detail", "")
    except Exception:
        return False, "endpoint unavailable"


def _check_native_via_import(port: int, native_path: str) -> tuple[bool, str]:
    """Check if a native extension is loadable."""
    try:
        url = f"http://127.0.0.1:{port}/api/system/verify-native?path={native_path}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("loadable", False), data.get("detail", "")
    except Exception:
        return False, "endpoint unavailable"


def _find_bundled_claude_cli(binary_path: str) -> Path | None:
    """Locate the bundled `claude` CLI shipped inside the SDK in the frozen bundle.

    PyInstaller lays the onedir bundle out as `{binary_dir}/_internal/...`, so the
    CLI lives at `{binary_dir}/_internal/claude_agent_sdk/_bundled/claude`. In a
    dev/test context (no frozen binary), fall back to the installed SDK package.
    """
    binary_dir = Path(binary_path).resolve().parent
    frozen = binary_dir / "_internal" / "claude_agent_sdk" / "_bundled" / "claude"
    if frozen.exists():
        return frozen
    # Dev fallback: the SDK package's own bundled CLI.
    try:
        import claude_agent_sdk

        candidate = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
        if candidate.exists():
            return candidate
    except Exception:
        pass
    return None


def _check_thinking_display_flag(cli_path: str) -> tuple[bool, str]:
    """Guard the `--thinking-display` CLI flag the Opus 4.8 thinking fix depends on.

    The flag is `.hideHelp()` hidden, so we cannot grep `--help`. The Claude CLI
    also *silently tolerates unknown flags* (exit 0 + version output) and exits 0
    even on an enum-validation error — so neither a positive probe nor the exit
    code can distinguish flag-present from flag-absent.

    The only falsifiable signal is a NEGATIVE probe: pass a bogus value and look
    for the enum-validation error that names the allowed choices. That error is
    emitted ONLY when the flag exists and validates its enum. If the flag is gone,
    the bogus value rides along on a tolerated unknown flag → no choices in output.

    Returns (ok, detail). ok=False means the contract the Opus 4.8 thinking fix
    relies on is broken — the build would silently regress to blank thinking.
    See run_4108aeef (the fix) + run_a972318c (this guard).
    """
    if not Path(cli_path).exists():
        return False, f"bundled claude CLI not found at {cli_path}"
    try:
        proc = subprocess.run(
            [cli_path, "--thinking-display", "__verify_build_bogus__", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "bundled CLI timed out on --thinking-display probe"
    except OSError as e:
        return False, f"could not spawn bundled CLI: {e}"

    output = (proc.stdout or "") + (proc.stderr or "")
    # The enum-validation error names BOTH choices when the flag is recognized.
    if "summarized" in output and "omitted" in output:
        return True, "flag validates enum (summarized, omitted)"
    return False, (
        "--thinking-display NOT recognized by bundled CLI — Opus 4.8 thinking "
        "summary would silently regress to blank. Probe output: "
        f"{output.strip()[:200]!r}"
    )


def main():
    # Determine binary path
    if len(sys.argv) > 1:
        binary = sys.argv[1]
    else:
        # Default: daemon binary
        binary = str(Path.home() / ".swarm-ai" / "daemon" / "python-backend")

    if not Path(binary).exists():
        print(f"❌ Binary not found: {binary}")
        sys.exit(1)

    passed, failed_critical, failed_important = verify_binary(binary)

    # Summary
    total = len(passed) + len(failed_critical) + len(failed_important)
    print(f"\n{'='*60}")
    print(f"  Results: {len(passed)}/{total} passed")
    if failed_critical:
        print(f"  🔴 CRITICAL failures ({len(failed_critical)}): {', '.join(failed_critical)}")
    if failed_important:
        print(f"  🟡 Important warnings ({len(failed_important)}): {', '.join(failed_important)}")
    print(f"{'='*60}\n")

    if failed_critical:
        print("❌ DO NOT RELEASE — critical capabilities missing from build")
        sys.exit(1)
    elif failed_important:
        print("⚠️  Release OK but with degraded capabilities")
        sys.exit(0)
    else:
        print("✅ All capabilities verified — safe to release")
        sys.exit(0)


if __name__ == "__main__":
    main()
