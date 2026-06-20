"""Tests for the --thinking-display flag guard in verify_build.py.

Background (run_4108aeef, 2026-06-20): Opus 4.8 thinking summaries went blank
because the model defaults `display` to "omitted". The fix passes the CLI flag
`--thinking-display summarized` via extra_args. That flag is `.hideHelp()` hidden
in the Claude CLI — if a future SDK/CLI bump silently drops it, the fix regresses
to blank thinking with NO error (the CLI tolerates unknown flags). This guard
pins the contract at build time.

Detection design (verified empirically before coding):
- POSITIVE probe (`--thinking-display summarized`) gives a FALSE PASS — the CLI
  silently tolerates unknown flags (exit 0 + version output), so it can't tell
  flag-present from flag-absent.
- EXIT CODE is useless — the CLI exits 0 even on an enum-validation error.
- NEGATIVE probe is the only falsifiable signal: `--thinking-display <bogus>`
  triggers "Allowed choices are summarized, omitted" ONLY when the flag exists
  and validates its enum. If the flag is gone, the bogus value rides along on a
  tolerated unknown flag → no choices in output → guard fails.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Load verify_build.py as a module (it lives in scripts/, not an importable pkg)
_VB_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_build.py"
_spec = importlib.util.spec_from_file_location("verify_build", _VB_PATH)
verify_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_build)


# The real bundled CLI shipped inside the installed SDK (exists in dev + frozen).
def _find_bundled_cli() -> Path | None:
    """Locate the bundled claude CLI via the installed SDK package."""
    try:
        import claude_agent_sdk

        sdk_dir = Path(claude_agent_sdk.__file__).parent
        candidate = sdk_dir / "_bundled" / "claude"
        return candidate if candidate.exists() else None
    except Exception:
        return None


class TestThinkingDisplayGuard:
    def test_real_bundled_cli_passes(self):
        """AC1: against the real bundled CLI, the guard confirms the flag exists."""
        cli = _find_bundled_cli()
        if cli is None:
            pytest.skip("bundled claude CLI not present in this environment")
        ok, detail = verify_build._check_thinking_display_flag(str(cli))
        assert ok is True, f"guard should pass against real CLI, got: {detail}"

    def test_missing_flag_fails(self, tmp_path):
        """AC2: a CLI stub that does NOT validate the flag (silently tolerates it,
        like a future build with the flag removed) must FAIL the guard."""
        # Stub mimics the unknown-flag tolerance: any args → print version, exit 0.
        stub = tmp_path / "claude_stub"
        stub.write_text("#!/bin/sh\necho '2.1.183 (Claude Code)'\nexit 0\n")
        stub.chmod(0o755)
        ok, detail = verify_build._check_thinking_display_flag(str(stub))
        assert ok is False, "guard must fail when CLI output lacks the enum choices"
        assert "NOT recognized" in detail  # explains WHY it failed (regression risk)

    def test_nonexistent_cli_returns_false(self):
        """AC3: a missing CLI path is reported (not crash) — caller decides severity."""
        ok, detail = verify_build._check_thinking_display_flag("/no/such/claude")
        assert ok is False
        assert detail  # non-empty explanation
