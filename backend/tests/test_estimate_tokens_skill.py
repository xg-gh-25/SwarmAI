"""Parity tests for the s_estimate-tokens skill (run_3f25a73a).

The skill MUST report the SAME number as the canonical
ContextDirectoryLoader.estimate_tokens — it delegates, never re-implements
(Gate-1 finding E: a vendored copy would re-create the drift this fixes).
"""
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1].parent  # repo root
PY_ENTRY = REPO / "backend/skills/s_estimate-tokens/scripts/estimate_tokens.py"
SH_ENTRY = REPO / "backend/skills/s_estimate-tokens/scripts/estimate-tokens.sh"


def _canonical(text: str) -> int:
    sys.path.insert(0, str(REPO / "backend"))
    from core.context_directory_loader import ContextDirectoryLoader
    return ContextDirectoryLoader.estimate_tokens(text)


def _parse_tokens(output: str) -> int:
    for line in output.splitlines():
        if line.startswith("Estimated tokens:"):
            return int(line.split(":", 1)[1].strip().replace(",", ""))
    raise AssertionError(f"no token line in output:\n{output}")


class TestSkillParity:
    def test_python_entry_matches_canonical_file(self, tmp_path):
        f = tmp_path / "sample.md"
        content = "SwarmAI 是自进化 Agent OS. The READ path is the differentiator. " * 40
        f.write_text(content, encoding="utf-8")
        out = subprocess.run(
            [sys.executable, str(PY_ENTRY), str(f)],
            capture_output=True, text=True, timeout=30,
        )
        assert out.returncode == 0, out.stderr
        assert _parse_tokens(out.stdout) == _canonical(content)

    def test_shell_wrapper_matches_canonical_stdin(self):
        content = "认知是操作系统知识是硬盘数据 The quick brown fox jumps"
        out = subprocess.run(
            ["bash", str(SH_ENTRY)],
            input=content, capture_output=True, text=True, timeout=30,
        )
        assert out.returncode == 0, out.stderr
        assert _parse_tokens(out.stdout) == _canonical(content)

    def test_window_parameterized_not_hardcoded_200k(self, tmp_path):
        f = tmp_path / "s.md"
        f.write_text("word " * 1000, encoding="utf-8")
        # default window (91K) gives a different % than 200K
        default = subprocess.run([sys.executable, str(PY_ENTRY), str(f)],
                                 capture_output=True, text=True, timeout=30).stdout
        windowed = subprocess.run([sys.executable, str(PY_ENTRY), "--window", "200000", str(f)],
                                  capture_output=True, text=True, timeout=30).stdout
        assert "91,000 tokens" in default
        assert "200,000 tokens" in windowed
        assert "91,000" not in windowed  # window actually applied

    def test_no_wc_w_heuristic_remains(self):
        """The old wc-w*1.8 + hardcoded 200000 must be gone from the shell script."""
        # Strip comment lines — the script legitimately documents WHAT was
        # removed (history) in comments, but no executable line may use it.
        code = "\n".join(
            ln for ln in SH_ENTRY.read_text().splitlines()
            if not ln.lstrip().startswith("#")
        )
        assert "wc -w" not in code
        assert "1.8" not in code
        assert "200000" not in code  # no hardcoded window in executable shell

    def test_discovery_works_from_outside_repo(self, tmp_path):
        """Gate-2 finding E: invoked from a cwd with NO backend/ (projected-copy /
        agent-workspace scenario), the entry must still find the canonical via the
        env override or known-path fallback — and FAIL LOUD, never wrong numbers."""
        f = tmp_path / "x.md"
        f.write_text("hello world test content here", encoding="utf-8")
        # Run with cwd = tmp_path (no backend/ anywhere up the tree)
        out = subprocess.run(
            [sys.executable, str(PY_ENTRY), str(f)],
            capture_output=True, text=True, timeout=30, cwd=str(tmp_path),
        )
        assert out.returncode == 0, f"discovery failed from outside repo: {out.stderr}"
        assert _parse_tokens(out.stdout) == _canonical(f.read_text())

    def test_env_var_discovery(self, tmp_path):
        """SWARM_REPO_ROOT override resolves the canonical even from an alien cwd."""
        import os
        f = tmp_path / "y.md"
        f.write_text("word " * 100, encoding="utf-8")
        env = {**os.environ, "SWARM_REPO_ROOT": str(REPO)}
        out = subprocess.run(
            [sys.executable, str(PY_ENTRY), str(f)],
            capture_output=True, text=True, timeout=30, cwd=str(tmp_path), env=env,
        )
        assert out.returncode == 0, out.stderr
        assert _parse_tokens(out.stdout) == _canonical(f.read_text())
