"""Skills run Python with the backend program's own interpreter (design
docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.2, S3c).

- ``py -`` reads the program from stdin (the heredoc form skill docs use);
- a module the build does not carry fails with a message naming it, not a bare traceback;
- every skill doc invokes Python as ``"$SWARM_BACKEND_BIN" py``, except the listed lines that
  run a repository's own code in that repository's own environment (or are prose);
- every third-party module a skill script imports is declared in that skill's SKILL.md
  (``python-deps`` bundled, ``python-optional-deps`` not), and every bundled one is a backend
  dependency, so the build venv has it;
- the PyInstaller spec collects the declared modules and the standard library.
"""
import ast
import importlib.metadata as md
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
SKILLS = BACKEND / "skills"
DOC_ROOTS = [SKILLS, BACKEND / "templates" / "ddd-skills"]
sys.path.insert(0, str(BACKEND))
from scripts.skill_python_deps import bundled_imports, declared  # noqa: E402


def _py(*args, **kw):
    return subprocess.run([sys.executable, str(BACKEND / "desktop_main.py"), "py", *args],
                          capture_output=True, text=True, timeout=60, **kw)


def test_py_reads_the_program_from_stdin():
    r = _py("-", "a1", input="import sys\nprint(6 * 7, sys.argv, __name__)\n")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "42 ['-', 'a1'] __main__"


def test_a_missing_module_is_named():
    r = _py("-c", "import swarm_no_such_module_xyz")
    assert r.returncode == 1
    assert "swarm_no_such_module_xyz" in r.stderr
    assert "not in this SwarmAI build" in r.stderr


# --- skill docs ----------------------------------------------------------------------------

INVOCATION = re.compile(
    r'(?<![\w/.$-])python3?(?=\s+(?:-[cm]\b|-\s|<<|["\']?\$|["\']?\{|["\']?[^\s"\'`]*\.py\b'
    r'|\S*scripts/))')
# (file under the docs root, text on the line): a repository's own code in its own environment,
# or prose. Everything else runs in SwarmAI's environment and must use py.
EXEMPT = [
    ("s_mcp-builder/INSTRUCTIONS.md", "python -m py_compile"),            # the user's own server
    ("s_mcp-builder/reference/python_mcp_server.md", "python your_server.py"),
    ("s_chat-brain-check/INSTRUCTIONS.md", "python -m pytest"),           # SwarmAI checkout venv
    ("s_code-review/INSTRUCTIONS.md", "python3 -m ruff"),                 # the reviewed repo's tool
    ("s_memory-distill/SKILL.md", "never use `python3 locked_write.py`"),  # prose
    ("s_self-evolution/SKILL.md", "Never `python3 locked_write.py`"),     # prose
    ("s_health-check/INSTRUCTIONS.md", "backend/.venv/bin/activate && python3"),
    ("s_swarm-release/INSTRUCTIONS.md", "cd backend && python scripts/ci_eval_gate.py"),
]


def test_skill_docs_run_python_through_py():
    bad = []
    for root in DOC_ROOTS:
        for p in sorted(root.rglob("*.md")):
            if "node_modules" in p.parts:
                continue
            rel = str(p.relative_to(root))
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if INVOCATION.search(line) and not any(rel == f and s in line for f, s in EXEMPT):
                    bad.append(f"{rel}:{n}: {line.strip()[:120]}")
    assert not bad, "use \"$SWARM_BACKEND_BIN\" py:\n" + "\n".join(bad)


def test_every_exemption_still_matches_a_line():
    for f, s in EXEMPT:
        assert s in (SKILLS / f).read_text(encoding="utf-8"), (f, s)


# --- dependencies --------------------------------------------------------------------------

_DISTS = md.packages_distributions()


def _third_party_imports(script: Path) -> set[str]:
    """Top-level names the script imports that an installed distribution provides and that are
    not a sibling module (Python puts the script's directory first on sys.path)."""
    siblings = {q.stem for q in script.parent.glob("*.py")} | {
        d.name for d in script.parent.iterdir() if (d / "__init__.py").exists()}
    names = set()
    for node in ast.walk(ast.parse(script.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods = [node.module]
        else:
            continue
        for m in mods:
            top = m.split(".")[0]
            if top in sys.stdlib_module_names or top in siblings:
                continue
            if top in _DISTS or top in _OPTIONAL_KNOWN:
                names.add(top)
    return names


# not installed in the dev venv, so packages_distributions() cannot name them
_OPTIONAL_KNOWN = {"playwright", "pypdf", "pdf2image", "edge_tts"}


def _is_test(p: Path) -> bool:
    return "tests" in p.parts or p.name.startswith("test_") or p.name == "conftest.py"


def test_skill_scripts_declare_their_third_party_imports():
    decl = declared(SKILLS)
    missing = []
    for skill in sorted(d for d in SKILLS.iterdir() if (d / "SKILL.md").exists()):
        deps, optional = decl.get(skill.name, ([], []))
        for script in skill.rglob("*.py"):
            if "node_modules" in script.parts or _is_test(script):
                continue
            for name in _third_party_imports(script) - set(deps) - set(optional):
                missing.append(f"{skill.name}: {name} ({script.relative_to(skill)})")
    assert not missing, "declare in SKILL.md python-deps / python-optional-deps:\n" + "\n".join(
        sorted(set(missing)))


def _pyproject_dists() -> set[str]:
    text = (BACKEND / "pyproject.toml").read_text()
    block = text[text.index("dependencies = ["):]
    block = block[:block.index("\n]")]
    names = re.findall(r'^\s*"([A-Za-z0-9_.\-]+)', block, re.M)
    return {n.lower().replace("_", "-") for n in names}


def test_bundled_deps_are_backend_dependencies():
    deps = _pyproject_dists()
    for name in bundled_imports(SKILLS):
        dists = {d.lower().replace("_", "-") for d in _DISTS.get(name, [])}
        assert dists & deps, f"python-deps {name!r} must come from a pyproject dependency"


def test_the_build_collects_declared_deps_and_the_standard_library():
    spec = (REPO / "desktop" / "scripts" / "build-backend.sh").read_text()
    assert "skill_python_deps" in spec and "bundled_imports" in spec
    assert "stdlib_module_names" in spec
