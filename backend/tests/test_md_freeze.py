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
import json
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
