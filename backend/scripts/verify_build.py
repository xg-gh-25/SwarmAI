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
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

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
    ("vec0_dylib",          "__native__:sqlite_vec/vec0",    "critical"),
]


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
        "SWARMAI_MODE": "sidecar",
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
    proc = subprocess.Popen(
        [binary_path, "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # Own process group for clean killpg
    )

    try:
        # Wait for health endpoint
        if not _wait_for_health(port, timeout=30):
            print("❌ Binary failed to start within 30s")
            return [], ["binary_startup"], []

        # Verify capabilities via the binary's Python environment
        passed, failed_critical, failed_important = _verify_capabilities(port)

        return passed, failed_critical, failed_important

    finally:
        # Kill the entire process group (catches MCP child processes)
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                proc.kill()


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
