"""Tests for s_translate/scripts/md_freeze.py — the Freeze-Translate-Stitch tool.

Methodology: verifies the deterministic guarantees that make document translation
safe — frozen code fences are byte-identical across freeze→stitch, the pipeline is
deterministic (call-twice safe), and `verify` detects structural drift. Tests run
the script both as importable functions and as a CLI subprocess (the real usage).

Key invariants under test:
- Round-trip with identity translation reproduces source byte-for-byte (AC1)
- Frozen fenced blocks are byte-identical in stitched output (AC2)
- verify exits 1 and names the discrepancy when a block is dropped/altered (AC3)
- Re-running freeze is deterministic (AC4)
- Unclosed fence does not hang; EOF closes it with a warning (AC5)
- Sentinel collision in source aborts with a clear error (AC6)
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# Load the script as a module (it lives in skills/, outside the package tree).
_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills" / "s_translate" / "scripts" / "md_freeze.py"
)
_spec = importlib.util.spec_from_file_location("md_freeze", _SCRIPT)
md_freeze = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(md_freeze)


# ── Fixtures ──────────────────────────────────────────────────────────────

MULTI_FENCE = """# Title

Some prose paragraph here.

```json
{"key": "value", "n": 1}
```

More prose between blocks.

## Section

| Col A | Col B |
|-------|-------|
| 1     | 2     |

~~~
plain tilde-fenced block
with two lines
~~~

Closing prose.
"""

# A doc whose fenced block CONTAINS markdown (the appendix-template case from the
# real morning file). Inner headings/tables must be frozen, not counted as structure.
NESTED_MARKDOWN = """# Outer

Intro prose.

```markdown
# Inner Template
## Quick Start
| a | b |
|---|---|
| 1 | 2 |
<!-- user: keep this -->
```

Outro prose.
"""


def _run_cli(*args, cwd=None):
    """Invoke the script as a subprocess — the real usage path."""
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, cwd=cwd,
    )


# ── AC1: round-trip byte identity (tracer bullet) ───────────────────────────

def test_freeze_stitch_roundtrip_identity(tmp_path):
    """freeze → (no translation) → stitch reproduces the source byte-for-byte."""
    src = tmp_path / "doc.md"
    src.write_text(MULTI_FENCE, encoding="utf-8")
    skeleton = tmp_path / "doc.skeleton.md"
    blocks = tmp_path / "doc.blocks.json"

    r = _run_cli("freeze", str(src), "--skeleton", str(skeleton), "--blocks", str(blocks))
    assert r.returncode == 0, r.stderr

    # Identity "translation": skeleton passes through unchanged.
    out = tmp_path / "doc.out.md"
    r2 = _run_cli("stitch", str(skeleton), str(blocks), "-o", str(out))
    assert r2.returncode == 0, r2.stderr

    assert out.read_text(encoding="utf-8") == MULTI_FENCE


# ── AC2: frozen blocks are byte-identical (incl tilde + lang tags) ──────────

def test_frozen_blocks_byte_identical():
    skeleton, payload, unclosed = md_freeze.freeze_text(MULTI_FENCE)
    assert not unclosed
    # Both a ```json block and a ~~~ block must be captured verbatim.
    assert payload["blocks"]["0"] == '```json\n{"key": "value", "n": 1}\n```\n'
    assert payload["blocks"]["1"] == "~~~\nplain tilde-fenced block\nwith two lines\n~~~\n"
    # Skeleton replaced each block with one sentinel line and dropped no prose.
    assert "⟦FROZEN_0⟧" in skeleton and "⟦FROZEN_1⟧" in skeleton
    assert "{\"key\"" not in skeleton  # code content is gone from the skeleton


def test_nested_markdown_block_is_frozen_not_counted():
    """A ```markdown template's inner headings/tables must be frozen, not counted."""
    skeleton, payload, _ = md_freeze.freeze_text(NESTED_MARKDOWN)
    # Exactly one fenced block — the whole ```markdown ... ``` template.
    assert list(payload["blocks"]) == ["0"]
    assert "## Quick Start" in payload["blocks"]["0"]      # inner heading frozen
    assert "<!-- user: keep this -->" in payload["blocks"]["0"]
    # Skeleton's only headings are the OUTER ones, not the template's inner ones.
    assert "## Quick Start" not in skeleton
    headings, tables = md_freeze._count_structure(skeleton)
    assert headings == 1   # only "# Outer"
    assert tables == 0     # the inner table is frozen


# ── AC4: determinism (call-twice safe) ──────────────────────────────────────

def test_freeze_is_deterministic():
    s1, b1, _ = md_freeze.freeze_text(MULTI_FENCE)
    s2, b2, _ = md_freeze.freeze_text(MULTI_FENCE)
    assert s1 == s2
    assert b1 == b2


# ── AC5: unclosed fence does not hang; EOF closes it with a warning ─────────

def test_unclosed_fence_closed_at_eof(tmp_path):
    text = "# Title\n\nprose\n\n```python\nx = 1\n"  # never closed
    skeleton, payload, unclosed = md_freeze.freeze_text(text)
    assert unclosed is True
    assert payload["blocks"]["0"] == "```python\nx = 1\n"
    # CLI surfaces the warning on stderr.
    src = tmp_path / "u.md"
    src.write_text(text, encoding="utf-8")
    r = _run_cli("freeze", str(src), "--skeleton", str(tmp_path / "u.sk.md"),
                 "--blocks", str(tmp_path / "u.bl.json"))
    assert r.returncode == 0
    assert "unclosed code fence" in r.stderr.lower()


# ── AC6: sentinel collision aborts with a clear error ───────────────────────

def test_sentinel_collision_aborts(tmp_path):
    text = "# Title\n\nThis text already has ⟦FROZEN_0⟧ in it.\n"
    with pytest.raises(ValueError, match="sentinel"):
        md_freeze.freeze_text(text)
    # CLI returns nonzero with a clear message.
    src = tmp_path / "c.md"
    src.write_text(text, encoding="utf-8")
    r = _run_cli("freeze", str(src))
    assert r.returncode == 2
    assert "sentinel" in r.stderr.lower()


# ── AC3: verify catches dropped/altered blocks ──────────────────────────────

def test_verify_passes_on_identity():
    ok, report = md_freeze.verify_texts(MULTI_FENCE, MULTI_FENCE)
    assert ok, report


def test_verify_catches_dropped_block(tmp_path):
    # Output where the json block was deleted entirely.
    mutilated = MULTI_FENCE.replace('```json\n{"key": "value", "n": 1}\n```\n\n', "")
    ok, report = md_freeze.verify_texts(MULTI_FENCE, mutilated)
    assert not ok
    assert any("FENCE COUNT" in line for line in report)
    # CLI exits 1.
    src = tmp_path / "s.md"; out = tmp_path / "o.md"
    src.write_text(MULTI_FENCE, encoding="utf-8")
    out.write_text(mutilated, encoding="utf-8")
    r = _run_cli("verify", str(src), str(out))
    assert r.returncode == 1


def test_verify_catches_altered_block():
    # Same block count, but the code content was changed (e.g. a value translated).
    altered = MULTI_FENCE.replace('"value"', '"价值"')
    ok, report = md_freeze.verify_texts(MULTI_FENCE, altered)
    assert not ok
    assert any("BLOCK 0 altered" in line for line in report)


def test_stitch_detects_missing_sentinel():
    """If the translated skeleton drops a placeholder, stitch must fail loud."""
    _, payload, _ = md_freeze.freeze_text(MULTI_FENCE)
    broken_skeleton = "# Title\n\nonly prose, no sentinels\n"
    with pytest.raises(ValueError, match="never reinserted"):
        md_freeze.stitch_text(broken_skeleton, payload)


# ── AC7: INSTRUCTIONS.md has the mandatory decision gate ────────────────────

def test_instructions_has_decision_gate():
    skill_dir = _SCRIPT.parent.parent
    text = (skill_dir / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "Decision Gate" in text
    assert "Freeze-Translate-Stitch" in text
    # The gate must route code-dense / large docs through the freeze path.
    assert "300 lines" in text
    assert "md_freeze.py" in text


# ── AC8: manifest declares the script ───────────────────────────────────────

def test_manifest_declares_script():
    import yaml  # available in backend deps
    skill_dir = _SCRIPT.parent.parent
    manifest = yaml.safe_load((skill_dir / "manifest.yaml").read_text(encoding="utf-8"))
    paths = [s["path"] for s in manifest.get("scripts", [])]
    assert "scripts/md_freeze.py" in paths


# ── Byte-identity edge cases (adversarial surface from REVIEW) ───────────────

def test_roundtrip_crlf_preserved():
    """CRLF line endings survive freeze→stitch byte-for-byte."""
    text = "# Title\r\n\r\nprose\r\n\r\n```json\r\n{\"a\": 1}\r\n```\r\nafter\r\n"
    skeleton, payload, _ = md_freeze.freeze_text(text)
    out = md_freeze.stitch_text(skeleton, payload)
    assert out == text


def test_roundtrip_no_trailing_newline():
    """A file whose final line has no newline reassembles exactly."""
    text = "# Title\n\nprose\n\n```\ncode\n```"  # no trailing \n
    skeleton, payload, _ = md_freeze.freeze_text(text)
    out = md_freeze.stitch_text(skeleton, payload)
    assert out == text


def test_longer_inner_fence_does_not_falsely_close():
    """A longer ``` line inside a ~~~ block must NOT close the ~~~ block."""
    text = "~~~\nhere is a fenced example:\n```\nnested\n```\nstill inside tilde\n~~~\n"
    skeleton, payload, _ = md_freeze.freeze_text(text)
    # The entire thing is ONE tilde block (the inner ``` are content, not closers).
    assert list(payload["blocks"]) == ["0"]
    assert payload["blocks"]["0"] == text
    assert md_freeze.stitch_text(skeleton, payload) == text


# ── Unicode/control line-separator safety (adversarial HIGH) ────────────────

@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x85", "\x0c", "\x0b", "\x1c"])
def test_unicode_line_separators_do_not_split_blocks(sep):
    """Chars that str.splitlines() breaks on, but Markdown does NOT, must stay inside the block.

    Regression for the splitlines() over-split bug: a fenced block containing a
    Unicode/control separator followed by a fence marker must remain ONE block,
    not leak its code into the translatable skeleton.
    """
    text = f"intro\n\n```\ncode-a{sep}```\nmore code\n```\n\nouter\n"
    skeleton, payload, unclosed = md_freeze.freeze_text(text)
    assert not unclosed, f"sep {sep!r} caused a spurious unclosed fence"
    assert list(payload["blocks"]) == ["0"], f"sep {sep!r} split one block into many"
    assert "more code" not in skeleton, f"sep {sep!r} leaked code into the skeleton"
    # And the round-trip is byte-identical.
    assert md_freeze.stitch_text(skeleton, payload) == text


def test_unicode_separator_roundtrip_byte_identical():
    text = "# T\n\n```json\n{\"a\": 1}\n```\nend\n"
    skeleton, payload, _ = md_freeze.freeze_text(text)
    assert md_freeze.stitch_text(skeleton, payload) == text


# ── stitch fails loud on corrupt sidecar (adversarial MED/LOW) ──────────────

def test_stitch_rejects_non_dict_payload():
    with pytest.raises(ValueError, match="must be a JSON object"):
        md_freeze.stitch_text("# T\n", [1, 2, 3])


def test_stitch_rejects_non_dict_blocks():
    with pytest.raises(ValueError, match="must be a JSON object"):
        md_freeze.stitch_text("# T\n", {"blocks": "not a dict"})


def test_stitch_rejects_non_string_block_value():
    skeleton = "intro\n⟦FROZEN_0⟧\nouter\n"
    with pytest.raises(ValueError, match="must be a string"):
        md_freeze.stitch_text(skeleton, {"blocks": {"0": 42}})


def test_cli_stitch_malformed_json_clean_error(tmp_path):
    sk = tmp_path / "s.md"; bl = tmp_path / "b.json"
    sk.write_text("# T\nprose\n", encoding="utf-8")
    bl.write_text("{not valid json", encoding="utf-8")
    r = _run_cli("stitch", str(sk), str(bl))
    assert r.returncode == 2
    assert "not valid JSON" in r.stderr


def test_cli_freeze_non_utf8_clean_error(tmp_path):
    """A non-UTF-8 source fails loud with a clear message, not a raw traceback."""
    src = tmp_path / "latin1.md"
    src.write_bytes("# Café\n\nré sumé\n".encode("latin-1"))  # invalid UTF-8
    r = _run_cli("freeze", str(src))
    assert r.returncode == 2
    assert "not valid UTF-8" in r.stderr
