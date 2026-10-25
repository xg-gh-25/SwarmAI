"""Per-profile behaviour reads one table (design
docs/design/2026-10-26-profile-capabilities.md §3.3).

What is tested: outside core/run_protocol/lifecycle.py, product code under backend/core
holds no line that compares or switches on a profile NAME (a quoted profile name on a
line that names ``profile``). KNOWN is today's count per file: it may only shrink; a
fix that removes a comparison removes it from KNOWN in the same commit, and a new one
fails. checks.py keeps its per-profile exemption reasons by design (each names why a
check does not apply), so its entry is the exemption table's.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
NAMES = "research|direct|trivial|full|bugfix|goal"
PAT = re.compile(rf"profile.*[\"']({NAMES})[\"']|[\"']({NAMES})[\"'].*profile")
OWNER = BACKEND / "core" / "run_protocol" / "lifecycle.py"

# file (relative to backend/) -> lines that still compare on a profile name
KNOWN: dict[str, int] = {
    "core/pipeline_profiles.py": 6,
    "core/run_protocol/checks.py": 6,
    "core/run_protocol/cli_commands.py": 2,
    "core/run_protocol/engine.py": 22,
    "core/run_protocol/next_step.py": 2,
    "core/run_protocol/schemas.py": 3,
}


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
