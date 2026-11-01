"""Product code does not look for SwarmAI's own checkout, machine or project.

Design: docs/design/2026-10-25-usable-by-anyone-closed-world-guards.md §3.1 K1, which
turns the one-time inventory of usable-by-anyone §3.9 into a check. Rule P7 there:
SwarmAI's repo, project and machine use exactly the product every user gets, so no
product code may find "the SwarmAI checkout" by a guessed location, a dev-only
environment variable, or the existence of SwarmAI's own build script.

What is scanned: every git-tracked ``backend/**/*.py`` and ``*.sh`` that ships, i.e.
everything except test files. Python is parsed: str and bytes literals that are not
docstrings, ``Path.home() / <segment>`` chains, and function, class, name, attribute
and import identifiers. Comments and docstrings may mention SwarmAI's paths as
examples; code may not use them. Shell files: every non-comment line.

A hit is allowed only by a reviewed line in ``tests/p7_exceptions.txt``, which names the
file, the token and the exact number of hits: one more hit in an excepted file is a new
violation, one fewer means the line must shrink. The scan must also have read the
product tree (a broken file list would otherwise pass over nothing).

The same instance-term rule (one deployment's customer, internal-tool and sign-in names)
also runs over the shipped text: backend/context, skills, experts and templates
(``test_shipped_text_has_no_instance_terms``).

Known limit: a bare ``"SwarmAI"`` literal (the product name, also the seeded default
project's name) is not a rule; the default-project literals of usable-by-anyone §3.9
class B are a separate cleanup.
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
    # one deployment's customers, internal tools and sign-in command (the 2026-11-01
    # all-user audit): text only that deployment can use. mwinit is allowed where the code
    # runs only for an Amazon-internal (ADA / Midway) sign-in, by a reviewed exception.
    r"|(?i:\b(?:cmhk|ivt|sentral|highspot|mwinit)\b)"
)
#: A home-relative development location written as a string: ``~/Desktop/...``,
#: ``.../Desktop`` or the exact segment ``"Desktop"``, ``~/swarmai``, ``~/Projects/...``.
PATH_STRING_RULES = re.compile(r"(?:^|/)Desktop(?:/|$)|^~/(?:swarmai|Projects)(?:/|$)")
#: ``Path.home() / <first> / ...`` whose first segment is a place a developer keeps
#: checkouts. App data lives under ``Library`` / ``AppData`` / ``.local`` / ``.swarm-ai``.
HOME_DEV_DIRS = {"Desktop", "Projects", "swarmai", "SwarmAI-Workspace"}
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


def _home_chain_first_segment(node: ast.BinOp) -> str | None:
    """For ``Path.home() / "a" / "b"`` the first segment ``"a"``; None otherwise."""
    while isinstance(node.left, ast.BinOp) and isinstance(node.left.op, ast.Div):
        node = node.left
    left, right = node.left, node.right
    is_home = (isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute)
               and left.func.attr == "home")
    if is_home and isinstance(right, ast.Constant) and isinstance(right.value, str):
        return right.value
    return None


def _python_hits(rel: str, text: str) -> list[tuple[str, int, str]]:
    tree = ast.parse(text, filename=rel)
    docs = _docstring_nodes(tree)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)) \
                and id(node) not in docs:
            value = node.value if isinstance(node.value, str) else node.value.decode("latin-1")
            hits += [(rel, node.lineno, m.group(0)) for m in STRING_RULES.finditer(value)]
            hits += [(rel, node.lineno, m.group(0)) for m in PATH_STRING_RULES.finditer(value)]
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            first = _home_chain_first_segment(node)
            if first in HOME_DEV_DIRS and not (isinstance(node.left, ast.BinOp)):
                hits.append((rel, node.lineno, f"Path.home() / {first!r}"))
        else:
            ident = (node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                    ast.ClassDef, ast.alias))
                     else node.id if isinstance(node, ast.Name)
                     else node.attr if isinstance(node, ast.Attribute) else None)
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


def _exceptions() -> dict[tuple[str, str], int]:
    allowed: dict[tuple[str, str], int] = {}
    for line in EXCEPTIONS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        path, token, count, reason = (part.strip() for part in line.split("|", 3))
        assert reason, f"exception without a reason: {line!r}"
        assert (path, token) not in allowed, f"duplicate exception: {line!r}"
        allowed[(path, token)] = int(count)
    return allowed


def _counts(hits) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for path, _no, token in hits:
        counts[(path, token)] = counts.get((path, token), 0) + 1
    return counts


def test_scan_reads_the_product_tree():
    files = _shipped_files()
    assert len(files) >= _MIN_FILES, f"only {len(files)} files scanned: the file list is broken"
    for prefix in _EXPECTED_DIRS:
        assert any(f.startswith(prefix) for f in files), f"nothing scanned under {prefix}"
    assert not any(_TEST_FILE.search(f) for f in files)


def test_product_code_does_not_look_for_swarmai():
    allowed = _exceptions()
    hits = scan(_shipped_files())
    counts = _counts(hits)
    unexplained = sorted(f"{p}:{n}: {t}" for p, n, t in hits
                         if counts[(p, t)] > allowed.get((p, t), 0))
    assert not unexplained, (
        "product code finds SwarmAI's own checkout, machine or project (usable-by-anyone P7). "
        "Use what any install has (bindings.yaml, the session's working repo, runtime paths), "
        "or add a reviewed line to tests/p7_exceptions.txt:\n" + "\n".join(unexplained))


def test_every_exception_matches_exactly():
    counts = _counts(scan(_shipped_files()) + _text_hits(_shipped_text_files()))
    wrong = sorted(f"{p} | {t} | {n} (found {counts.get((p, t), 0)})"
                   for (p, t), n in _exceptions().items() if counts.get((p, t), 0) < n)
    assert not wrong, ("exceptions that allow more hits than exist; lower the count or "
                       "delete the line:\n" + "\n".join(wrong))


def test_the_rules_catch_what_they_name():
    """Each rule fires on its shape and not on a comment or docstring (guards the scanner)."""
    code = ('"""Example: ~/Desktop/SwarmAI-Workspace/swarmai."""\n'
            '# prod.sh is mentioned in a comment\n'
            'from pathlib import Path\n'
            'def _find_swarmai_repo():\n'
            '    return Path.home() / "Desktop" / "SwarmAI-Workspace" / "swarmai"\n'
            'ROOT = "/Users/someone/x"\n'
            'BUILD = "prod.sh"\n'
            'A = Path.home() / "swarmai"\n'
            'B = Path.home() / "Projects" / "swarmai"\n'
            'm._find_swarmai_root()\n'
            'C = b"/Users/bob/x"\n'
            'D = "~/Desktop/swarmai"\n'
            'LOGS = Path.home() / ".local" / "share" / "swarmai" / "logs"\n'
            'APP = Path.home() / "Library" / "Application Support" / "SwarmAI"\n')
    tokens = sorted(t for _p, _n, t in _python_hits("x.py", code))
    assert tokens == sorted([
        "_find_swarmai_repo", "Desktop", "Path.home() / 'Desktop'", "SwarmAI-Workspace",
        "/Users/someone", "prod.sh", "Path.home() / 'swarmai'", "Path.home() / 'Projects'",
        "_find_swarmai_root", "/Users/bob", "/Desktop/",
    ])
    assert [t for _p, _n, t in _shell_hits("x.sh", "# prod.sh\n./prod.sh build\n")] == ["prod.sh"]


#: Shipped text read by every user's agent or shown to them.
_TEXT_ROOTS = ("backend/context/", "backend/skills/", "backend/experts/", "backend/templates/")
_TEXT_SUFFIXES = (".md", ".yaml", ".yml", ".json", ".txt", ".toml")
INSTANCE_TERMS = re.compile(r"(?i:\b(?:cmhk|ivt|sentral|highspot|mwinit)\b)")


def _shipped_text_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "--", *_TEXT_ROOTS],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    return [f for f in out if f.endswith(_TEXT_SUFFIXES) and not _TEST_FILE.search(f)]


def _text_hits(files: list[str]) -> list[tuple[str, int, str]]:
    hits = []
    for rel in files:
        for no, line in enumerate((REPO / rel).read_text(encoding="utf-8", errors="replace")
                                  .splitlines(), 1):
            hits += [(rel, no, m.group(0)) for m in INSTANCE_TERMS.finditer(line)]
    return hits


def test_shipped_text_has_no_instance_terms():
    files = _shipped_text_files()
    assert len(files) >= 100 and any(f.startswith("backend/context/") for f in files)
    allowed = _exceptions()
    hits = _text_hits(files)
    counts = _counts(hits)
    unexplained = sorted(f"{p}:{n}: {t}" for p, n, t in hits
                         if counts[(p, t)] > allowed.get((p, t), 0))
    assert not unexplained, ("shipped text names one deployment's customer, internal tool "
                             "or sign-in command:\n" + "\n".join(unexplained))


def test_instance_terms_are_caught():
    code = 'A = "CMHK report"\nB = "run mwinit -f"\nC = "aws-sentral-mcp"\nD = "native"\n'
    assert sorted(t for _p, _n, t in _python_hits("x.py", code)) == ["CMHK", "mwinit", "sentral"]
