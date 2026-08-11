"""Tests for the --thinking-display flag guard in verify_build.py.

Background (run_4108aeef, 2026-06-20): Opus 4.8 thinking summaries went blank
because the model defaults `display` to "omitted". The fix passes the CLI flag
`--thinking-display summarized` via extra_args. That flag is `.hideHelp()` hidden
in the Claude CLI — if a future SDK/CLI bump silently drops it, the fix regresses
to blank thinking with NO error (the CLI tolerates unknown flags). This guard
pins the contract at build time.

Detection design (verified empirically):
- POSITIVE probe (`--thinking-display summarized`) gives a FALSE PASS — the CLI
  silently tolerates unknown flags (prints the version instead of complaining),
  so it can't tell flag-present from flag-absent.
- EXIT CODE is not the signal: CLI 2.1.190 exits 1 on the enum-validation error,
  but an older note here claimed it always exits 0. Read the OUTPUT; the exit
  code is recorded only to make failures diagnosable.
- NEGATIVE probe is the falsifiable signal: `--thinking-display <bogus>` triggers
  "Allowed choices are summarized, omitted" ONLY when the flag exists and
  validates its enum.

Three-state verdict (2026-08-11): a negative result is only trustworthy if the
instrument works. Two consecutive real builds failed this gate — once with empty
probe output, once with a 30s timeout — while the flag was demonstrably present in
the exact file being probed. The guard was reporting "could not measure" as
"flag is gone" and blocking a good release. It now runs a plain `--version`
CONTROL before declaring a regression, and only PROBE_MISSING blocks.
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


def _stub(tmp_path: Path, name: str, body: str) -> Path:
    """Write an executable /bin/sh stub standing in for the bundled CLI."""
    path = tmp_path / name
    path.write_text(body)
    path.chmod(0o755)
    return path


class TestThinkingDisplayGuard:
    def test_real_bundled_cli_passes(self):
        """AC1: against the real bundled CLI, the guard confirms the flag exists."""
        cli = _find_bundled_cli()
        if cli is None:
            pytest.skip("bundled claude CLI not present in this environment")
        verdict, detail = verify_build._check_thinking_display_flag(str(cli))
        assert verdict == verify_build.PROBE_OK, f"should pass real CLI, got: {detail}"

    def test_missing_flag_is_critical(self, tmp_path):
        """AC2: a CLI that silently tolerates the flag (like a future build with it
        removed) must be reported as a CONFIRMED regression, which blocks release."""
        # Mimics unknown-flag tolerance: any args → print version, exit 0. The
        # plain --version control also answers, so the negative result is trusted.
        stub = _stub(tmp_path, "claude_tolerant", "#!/bin/sh\necho '2.1.183 (Claude Code)'\nexit 0\n")
        verdict, detail = verify_build._check_thinking_display_flag(str(stub))
        assert verdict == verify_build.PROBE_MISSING
        assert "NOT recognized" in detail  # explains WHY it failed (regression risk)


class TestProbeInconclusive:
    """The 2026-08-11 false-critical class: the probe could not be measured, which
    says NOTHING about the flag. These must NOT be reported as a regression."""

    def test_nonexistent_cli_is_inconclusive(self):
        """AC3: a missing CLI path is reported (not a crash, not 'flag removed')."""
        verdict, detail = verify_build._check_thinking_display_flag("/no/such/claude")
        assert verdict == verify_build.PROBE_INCONCLUSIVE
        assert detail  # non-empty explanation

    def test_silent_cli_is_inconclusive_not_missing(self, tmp_path):
        """AC4 (regression, 2026-08-11): a CLI that exits without writing to either
        stream — the SIGKILL/GK-scan shape observed in a real build — used to be
        reported as '--thinking-display NOT recognized' and blocked the release."""
        stub = _stub(tmp_path, "claude_silent", "#!/bin/sh\nexit 137\n")
        verdict, detail = verify_build._check_thinking_display_flag(str(stub))
        assert verdict == verify_build.PROBE_INCONCLUSIVE
        assert "NOT recognized" not in detail
        assert "UNKNOWN" in detail  # states honestly that it could not be verified

    def test_hanging_cli_is_inconclusive(self, tmp_path, monkeypatch):
        """AC5 (regression, 2026-08-11): the second failing build hit the timeout
        path. A timeout is an unmeasured probe, not a removed flag."""
        monkeypatch.setattr(verify_build, "_PROBE_TIMEOUT_S", 2)
        # sleep's stdio goes to /dev/null so it does not hold the captured pipes
        # open after the shell is killed (otherwise run() blocks for the full sleep).
        stub = _stub(tmp_path, "claude_hang", "#!/bin/sh\nsleep 10 >/dev/null 2>&1\n")
        verdict, detail = verify_build._check_thinking_display_flag(str(stub))
        assert verdict == verify_build.PROBE_INCONCLUSIVE
        assert "NOT recognized" not in detail

    def test_control_works_but_probe_silent_is_inconclusive(self, tmp_path):
        """AC6: asymmetric case — the CLI answers a plain --version but goes silent
        on the flag probe. Ambiguous, so warn rather than claim a regression."""
        stub = _stub(
            tmp_path,
            "claude_asym",
            "#!/bin/sh\n"
            'case "$*" in *thinking-display*) exit 137 ;; esac\n'
            "echo '2.1.190 (Claude Code)'\n",
        )
        verdict, detail = verify_build._check_thinking_display_flag(str(stub))
        assert verdict == verify_build.PROBE_INCONCLUSIVE
        assert "UNKNOWN" in detail


class TestVerdictRouting:
    """AC7: only a confirmed regression may block a release."""

    def test_ok_passes(self):
        assert verify_build._thinking_probe_bucket(verify_build.PROBE_OK)[1] == "passed"

    def test_missing_is_the_only_release_blocker(self):
        assert (
            verify_build._thinking_probe_bucket(verify_build.PROBE_MISSING)[1]
            == "critical"
        )

    def test_inconclusive_never_blocks(self):
        assert (
            verify_build._thinking_probe_bucket(verify_build.PROBE_INCONCLUSIVE)[1]
            == "important"
        )

    def test_verdicts_are_distinct(self):
        verdicts = {
            verify_build.PROBE_OK,
            verify_build.PROBE_MISSING,
            verify_build.PROBE_INCONCLUSIVE,
        }
        assert len(verdicts) == 3
