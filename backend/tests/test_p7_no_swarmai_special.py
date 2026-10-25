"""Product code does not look for SwarmAI's own checkout, machine or project.

Design: docs/design/2026-10-25-usable-by-anyone-closed-world-guards.md §3.1 K1, which
turns the one-time inventory of usable-by-anyone §3.9 into a check. Rule P7 there:
SwarmAI's repo, project and machine use exactly the product every user gets, so no
product code may find "the SwarmAI checkout" by a guessed location, a dev-only
environment variable, or the existence of SwarmAI's own build script.

What is scanned: every git-tracked ``backend/**/*.py`` and ``*.sh`` that ships, i.e.
everything except test files. Python is parsed: string literals that are not
docstrings, plus function, name and import identifiers. Comments and docstrings may
mention SwarmAI's paths as examples; code may not use them. Shell files: every
non-comment line.

A hit is allowed only by a reviewed line in ``tests/p7_exceptions.txt``. Two checks keep
this test from going quietly blind: every exception must still match a hit (an unused
exception is deleted), and the scan must actually have read the product tree (a
broken file list would otherwise pass over nothing).
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXCEPTIONS = Path(__file__).with_name("p7_exceptions.txt")

#: Text in a code string that only makes sense on SwarmAI's development machine.
STRING_RULES = re.compile(
    r"SwarmAI-Workspace"                       # the owner's checkout folder
    r"|prod\.sh"                               # SwarmAI's own build script
    r"|Projects/SwarmAI"                       # one project's path, written as a literal
    r"|SWARMAI_CODEBASE_DIR|SWARMAI_DIR\b|SWARMAI_PROJECT_ROOT"  # env vars that point at it
    r"|/(?:Users|home)/[A-Za-z][\w.-]*"         # an absolute path naming one account
)
#: A string that is exactly this is the ``Path.home() / "Desktop" / ...`` guess.
DESKTOP = "Desktop"
#: Identifiers whose only job is to find SwarmAI's checkout.
NAME_RULES = re.compile(r"_find_swarmai_(?:root|repo)\b")

_TEST_FILE = re.compile(r"(?:^|/)tests?/|(?:^|/)test_[^/]*\.py$|_test\.py$|(?:^|/)conftest\.py$")
#: Directories the product tree must contain; a scan missing one read the wrong files.
_EXPECTED_DIRS = ("backend/core/", "backend/hooks/", "backend/routers/", "backend/jobs/",
                  "backend/scripts/", "backend/channels/", "backend/skills/")
_MIN_FILES = 400


def _shipped_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "--", "backend/*.py", "backend/*.sh"],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    return [f for f in out if f and not _TEST_FILE.search(f)]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _python_hits(rel: str, text: str) -> list[tuple[str, int, str]]:
    tree = ast.parse(text, filename=rel)
    docs = _docstring_nodes(tree)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            hits += [(rel, node.lineno, m.group(0)) for m in STRING_RULES.finditer(node.value)]
            if node.value == DESKTOP:
                hits.append((rel, node.lineno, DESKTOP))
        else:
            ident = (node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.alias))
                     else node.id if isinstance(node, ast.Name) else None)
            if ident and NAME_RULES.search(ident):
                hits.append((rel, getattr(node, "lineno", 0), NAME_RULES.search(ident).group(0)))
    return hits


def _shell_hits(rel: str, text: str) -> list[tuple[str, int, str]]:
    hits = []
    for no, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        hits += [(rel, no, m.group(0)) for m in STRING_RULES.finditer(line)]
    return hits


def scan(files: list[str]) -> list[tuple[str, int, str]]:
    hits = []
    for rel in files:
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        hits += _shell_hits(rel, text) if rel.endswith(".sh") else _python_hits(rel, text)
    return hits


def _exceptions() -> set[tuple[str, str]]:
    allowed = set()
    for line in EXCEPTIONS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        path, token, reason = (part.strip() for part in line.split("|", 2))
        assert reason, f"exception without a reason: {line!r}"
        allowed.add((path, token))
    return allowed


def test_scan_reads_the_product_tree():
    files = _shipped_files()
    assert len(files) >= _MIN_FILES, f"only {len(files)} files scanned: the file list is broken"
    for prefix in _EXPECTED_DIRS:
        assert any(f.startswith(prefix) for f in files), f"nothing scanned under {prefix}"
    assert not any(_TEST_FILE.search(f) for f in files)


def test_product_code_does_not_look_for_swarmai():
    allowed = _exceptions()
    unexplained = sorted(f"{p}:{n}: {t}" for p, n, t in scan(_shipped_files()) if (p, t) not in allowed)
    assert not unexplained, (
        "product code finds SwarmAI's own checkout, machine or project (usable-by-anyone P7). "
        "Use what any install has (bindings.yaml, the session's working repo, runtime paths), "
        "or add a reviewed line to tests/p7_exceptions.txt:\n" + "\n".join(unexplained))


def test_every_exception_is_still_needed():
    found = {(p, t) for p, _n, t in scan(_shipped_files())}
    stale = sorted(f"{p} | {t}" for p, t in _exceptions() - found)
    assert not stale, "exceptions that match nothing; delete them:\n" + "\n".join(stale)


def test_the_rules_catch_what_they_name():
    """Each rule fires on its shape and not on a comment or docstring (guards the scanner)."""
    code = ('"""Example: ~/Desktop/SwarmAI-Workspace/swarmai."""\n'
            '# prod.sh is mentioned in a comment\n'
            'from pathlib import Path\n'
            'def _find_swarmai_repo():\n'
            '    return Path.home() / "Desktop" / "SwarmAI-Workspace" / "swarmai"\n'
            'ROOT = "/Users/someone/x"\n'
            'BUILD = "prod.sh"\n')
    tokens = sorted(t for _p, _n, t in _python_hits("x.py", code))
    assert tokens == sorted(["_find_swarmai_repo", "Desktop", "SwarmAI-Workspace",
                             "/Users/someone", "prod.sh"])
    assert [t for _p, _n, t in _shell_hits("x.sh", "# prod.sh\n./prod.sh build\n")] == ["prod.sh"]
