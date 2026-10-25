"""Per-profile behaviour reads one table (design
docs/design/2026-10-26-pipeline-two-ends.md §4).

What is tested: outside core/run_protocol/lifecycle.py, product code under backend/core
holds no comparison on a profile NAME (``profile == "x"``, ``profile in {"x", ...}``):
behaviour reads lifecycle.PROFILE_CAPS. KNOWN (per-file counts) may only shrink; a
fix that removes a comparison removes it from KNOWN in the same commit, and a new one
fails.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
NAMES = "research|direct|trivial|full|bugfix|goal"
# a comparison on a profile name: profile == "x" / "x" != profile / profile in {"x", ...}
PAT = re.compile(
    rf"profile[\w\"'\]\[.()]*\s*(==|!=)\s*[\"']({NAMES})[\"']"
    rf"|[\"']({NAMES})[\"']\s*(==|!=)\s*[\w.\[\]\"'()]*profile"
    rf"|profile[\w\"'\]\[.()]*\s+(not\s+)?in\s+[\(\{{\[][^\n]*[\"']({NAMES})[\"']")
OWNER = BACKEND / "core" / "run_protocol" / "lifecycle.py"

# file (relative to backend/) -> lines that still compare on a profile name
KNOWN: dict[str, int] = {}   # emptied in two-ends step 4 (lifecycle.PROFILE_CAPS)


def _counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted((BACKEND / "core").rglob("*.py")):
        if path == OWNER or "__pycache__" in path.parts:
            continue
        n = sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#") and PAT.search(line))
        if n:
            out[str(path.relative_to(BACKEND))] = n
    return out


def test_profile_names_are_compared_only_in_the_profile_table():
    assert _counts() == KNOWN
