"""Static enforcement for governed corpus writes through the CDF materializer.

``scripts/cdf_write_scan.py`` finds every function in a module that names a governed
location and writes a file, directly or through a helper writer in another module.
Each one is either migrated (and so gone from the scan) or listed in
``cdf_materializer_allowlist.txt`` with its reason. Synthetic modules prove every
sink family, ``Path.open``, an unreadable mode, ``os.open`` flags and a helper chain
across modules are detected, and that reads and ``str.replace`` are not.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.cdf_write_scan import EXEMPT, MARKER, scan_tree, sink_kind

_BACKEND = Path(__file__).resolve().parents[1]
_ALLOWLIST = Path(__file__).with_name("cdf_materializer_allowlist.txt")


def _allowed() -> set[str]:
    values: set[str] = set()
    for raw in _ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, reason = line.partition("  # ")
        assert separator and reason.strip(), f"allowlist entry needs a reason: {raw!r}"
        values.add(key.strip())
    return values


def test_production_governed_writes_are_materialized_or_in_inventory() -> None:
    """Every current writer is either migrated or explicitly inventoried."""
    actual = {hit.key for hit in scan_tree(_BACKEND)}
    allowed = _allowed()
    assert actual == allowed, (
        "CDF materializer inventory drift. Add real legacy writers with a reason; "
        "migrated writers must be removed.\n"
        f"unlisted={sorted(actual - allowed)!r}\n"
        f"stale={sorted(allowed - actual)!r}"
    )


def test_the_sanctioned_writers_exist() -> None:
    for rel in EXEMPT:
        assert (_BACKEND / rel).is_file(), rel


def _kinds(statement: str) -> set[str]:
    tree = ast.parse(f"def f(p=None, a=None, b=None, m=None, fd=None):\n    {statement}\n")
    return {k for c in ast.walk(tree) if isinstance(c, ast.Call) and (k := sink_kind(c))}


@pytest.mark.parametrize("statement, expected", [
    ("p.write_text('x')", "write_text"),
    ("p.write_bytes(b'x')", "write_bytes"),
    ("open(p, 'w')", "open"),
    ("open(p, mode='a')", "open"),
    ("open(p, 'x')", "open"),
    ("open(p, 'r+')", "open"),
    ("open(p, m)", "open"),                      # unreadable mode: fail closed
    ("p.open('a')", "open"),                     # Path.open
    ("p.open(mode='w')", "open"),
    ("io.open(p, 'w')", "open"),
    ("codecs.open(p, 'w', 'utf-8')", "open"),
    ("os.fdopen(fd, 'w')", "open"),
    ("os.open(p, os.O_WRONLY | os.O_CREAT)", "os.open"),
    ("os.open(p, m)", "os.open"),                # unreadable flags: fail closed
    ("os.replace(a, b)", "os.replace"),
    ("os.rename(a, b)", "os.rename"),
    ("os.remove(p)", "os.remove"),
    ("os.unlink(p)", "os.unlink"),
    ("p.rename(b)", "rename"),
    ("p.replace(b)", "Path.replace"),
    ("p.touch()", "touch"),
    ("shutil.move(a, b)", "shutil.move"),
    ("shutil.copy2(a, b)", "shutil.copy2"),
    ("p.unlink()", "unlink"),
    ("shutil.rmtree(p)", "rmtree"),
])
def test_scanner_catches_each_sink_kind(statement: str, expected: str) -> None:
    assert expected in _kinds(statement)


@pytest.mark.parametrize("statement", [
    "open(p)", "open(p, 'r')", "open(p, 'rb')", "p.open()", "p.open('r')",
    "os.open(p, os.O_RDONLY | os.O_NOFOLLOW)", "p.read_text()", "'a'.replace('a', 'b')",
])
def test_reads_and_string_replace_are_not_sinks(statement: str) -> None:
    assert not _kinds(statement)


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def test_a_helper_writer_in_another_module_is_found(tmp_path: Path) -> None:
    """The blind spot this scanner closes: a module that names MEMORY.md and writes
    through a helper in a module that names nothing governed."""
    root = _tree(tmp_path, {
        "core/__init__.py": "",
        "core/fileio.py": ("def deep(p, t):\n    p.write_text(t)\n\n"
                           "def atomic(p, t):\n    deep(p, t)\n"),
        "core/memory_job.py": ("from core.fileio import atomic\nfrom core import fileio\n"
                               "PATH = 'MEMORY.md'\n\n"
                               "def via_name(p):\n    atomic(p, 'x')\n\n"
                               "def via_module(p):\n    fileio.atomic(p, 'x')\n\n"
                               "def reads(p):\n    return p.read_text()\n\n"
                               "class Job:\n    def run(self, p):\n        self._w(p)\n\n"
                               "    def _w(self, p):\n        with p.open('a') as f:\n"
                               "            f.write('x')\n"),
    })
    hits = {h.key: h.via for h in scan_tree(root)}
    assert hits["core/memory_job.py:via_name"] == "via core.fileio:atomic"
    assert hits["core/memory_job.py:via_module"] == "via core.fileio:atomic"
    assert hits["core/memory_job.py:Job._w"] == "open"
    assert "core/memory_job.py:reads" not in hits
    assert "core/memory_job.py:Job.run" not in hits      # its callee is the hit
    assert not any(k.startswith("core/fileio.py") for k in hits)   # no marker there


def test_a_sanctioned_writer_is_not_a_helper(tmp_path: Path) -> None:
    root = _tree(tmp_path, {
        "core/__init__.py": "", "core/ingestion/__init__.py": "",
        "core/ingestion/writer.py": "def write(p):\n    p.write_text('x')\n",
        "core/caller.py": ("from core.ingestion.writer import write\nX = 'MEMORY.md'\n\n"
                           "def go(p):\n    write(p)\n"),
    })
    assert not scan_tree(root)


def test_marker_covers_every_governed_file_name() -> None:
    for name in ("MEMORY.md", "EVOLUTION.md", "STEERING.md", "USER.md", "TOOLS.md",
                 "TIMELINE.md", "PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md",
                 "Knowledge/", "ddd_path"):
        assert MARKER.search(name), name
