"""Tests for gate_scaffold — the ②→③ last mile (run_90b8aeed).

Context: when a human ACCEPTS a proposal_kind='gate' in the governance dashboard,
the loop used to only set tracker state (register_gate) — an approved gate never
became a file, so the human hand-wrote it from a blank page. scaffold_gate_stub
closes that last mile by writing an INERT, fail-open GATE_<cls>.py stub the human
completes. P7-compliant: the human already approved AND must still write the match
logic + wire it into a hook chain; the scaffold only removes blank-page friction.

Safety invariants under test:
  - fail-open: the scaffolded stub exits 0 (allows every tool) until a human completes it
  - skip-if-exists: never clobber a human-completed gate
  - canonical filename: derived from canonical_class_key (matches tracker state key)
  - convention markers present (shebang, PreToolUse contract, fail-open, class cited)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from core.evolution.gate_scaffold import scaffold_gate_stub  # RED until implemented


class TestGateScaffold:
    def test_scaffolds_stub_file(self, tmp_path):
        """DoD#1: a GATE_<cls>.py is written into the gates dir."""
        gates = tmp_path / "gates"
        p = scaffold_gate_stub(gates, "CLASS A: Confidence → Skip Process", "GATE_CLASS_A")
        assert p is not None and p.exists(), "stub file must be written"
        assert p.name == "GATE_CLASS_A.py", f"filename from canonical key, got {p.name}"

    def test_stub_is_fail_open(self, tmp_path):
        """DoD#2: an un-completed scaffolded stub exits 0 (allows all) — never blocks a tool.

        Runs the stub as the real PreToolUse contract would (event JSON on stdin).
        """
        gates = tmp_path / "gates"
        p = scaffold_gate_stub(gates, "CLASS_B", "GATE_CLASS_B")
        r = subprocess.run(
            [sys.executable, str(p)],
            input='{"tool_name":"Bash","tool_input":{"command":"anything"}}',
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, (
            f"an incomplete auto-scaffolded gate MUST be fail-open (exit 0), got {r.returncode}: {r.stderr}")

    def test_skip_if_exists_never_clobbers(self, tmp_path):
        """DoD#3: a second scaffold (re-accept / re-run) does NOT overwrite a
        human-completed gate — returns None and leaves content byte-identical."""
        gates = tmp_path / "gates"
        p1 = scaffold_gate_stub(gates, "CLASS_C", "GATE_CLASS_C")
        # Simulate a human completing the gate:
        human_content = "#!/usr/bin/env python3\n# human-completed real match logic\nimport sys\nsys.exit(0)\n"
        p1.write_text(human_content, encoding="utf-8")

        p2 = scaffold_gate_stub(gates, "CLASS_C", "GATE_CLASS_C")
        assert p2 is None, "skip-if-exists must return None on a pre-existing gate"
        assert p1.read_text(encoding="utf-8") == human_content, "must NOT clobber the human-completed gate"

    def test_filename_uses_canonical_key(self, tmp_path):
        """DoD#4: the same logical class under a drifted spelling maps to the SAME file
        (canonical_class_key), so a re-accept doesn't orphan the first gate."""
        gates = tmp_path / "gates"
        p1 = scaffold_gate_stub(gates, "CLASS A: Confidence → Skip Process", "GATE_CLASS_A")
        # A drifted spelling of the same class → same canonical file → skip (not a dup).
        p2 = scaffold_gate_stub(gates, "class a", "GATE_CLASS_A")
        assert p1.name == "GATE_CLASS_A.py"
        assert p2 is None, "a drifted spelling of the same canonical class must not write a duplicate"
        assert len(list(gates.glob("GATE_*.py"))) == 1, "exactly one gate file for the logical class"

    def test_stub_has_convention_markers(self, tmp_path):
        """DoD#4: the stub follows the no_git_push convention — shebang, PreToolUse
        contract, fail-open note, the correction class cited, and a TODO for the human."""
        gates = tmp_path / "gates"
        p = scaffold_gate_stub(gates, "CLASS A: Confidence → Skip Process", "GATE_CLASS_A")
        text = p.read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env python3"), "shebang"
        assert "PreToolUse" in text, "declares the PreToolUse hook contract"
        assert "return 2" in text or "exit(2)" in text or "exit 2" in text, (
            "documents the exit-2 BLOCK contract (main() returns 2 → sys.exit(main()) blocks)")
        assert "fail-open" in text.lower() or "fail open" in text.lower(), "fail-open note"
        assert "CLASS_A" in text or "CLASS A" in text, "cites the correction class it enforces"
        assert "TODO" in text, "carries a TODO for the human to complete the match logic"

    def test_creates_gates_dir_if_absent(self, tmp_path):
        """The scaffold mkdir(parents=True) — works even if gates/ doesn't exist yet."""
        gates = tmp_path / "deep" / "gates"  # nonexistent parent chain
        p = scaffold_gate_stub(gates, "CLASS_D", "GATE_CLASS_D")
        assert p is not None and p.exists()

    def test_path_traversal_is_refused(self, tmp_path):
        """SECURITY (Gate-2 HIGH): a class label with path separators / .. must NOT
        write outside gates_dir. source_class is free-form (untrusted)."""
        gates = tmp_path / "gates"
        gates.mkdir()
        for evil in ("A/../../PWNED", "/etc/passwd", "foo/../../bar", "..", "a/b/c"):
            p = scaffold_gate_stub(gates, evil, f"GATE_{evil}")
            if p is not None:
                # If it wrote anything, it MUST be directly inside gates/ (no escape).
                assert p.resolve().parent == gates.resolve(), f"{evil!r} escaped to {p}"
                assert p.name.startswith("GATE_") and p.name.endswith(".py")
        # Nothing was written outside gates/ (no stray .py in tmp_path or its parents).
        assert not list(tmp_path.glob("*.py")), "no file may be written outside gates/"
        assert not list(tmp_path.glob("PWNED*")), "traversal target must not exist"

    def test_evidence_injection_cannot_break_the_stub(self, tmp_path):
        """SECURITY (Gate-2 MED): a triple-quote / backslash in evidence must NOT break
        out of the docstring — the generated stub must always be valid Python."""
        import py_compile
        gates = tmp_path / "gates"
        evil_ev = 'x"""\nimport os\nos.system("boom")\n"""'
        p = scaffold_gate_stub(gates, "CLASS_E", "GATE_CLASS_E", evidence=evil_ev)
        assert p is not None
        # The generated file must compile (no docstring breakout / injected code).
        py_compile.compile(str(p), doraise=True)
