"""desktop/scripts/normalize_bundle_rpaths.py — the Linux bundle's RUNPATHs before linuxdeploy.

The tools (patchelf, ldd) are replaced by a fake that keeps each file's RUNPATH and
resolves dependencies the way the loader does, so the checks run on any platform:
- a library PyInstaller copied away from the library it needs gets an entry to it;
- every file ends with ``$ORIGIN``, the value linuxdeploy's own call then leaves unchanged;
- a second run changes nothing (what linuxdeploy's call will see);
- a dependency the bundle does not hold is reported, not silently passed.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "normalize_bundle_rpaths", ROOT / "desktop" / "scripts" / "normalize_bundle_rpaths.py")
nbr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nbr)


class FakeTools:
    """patchelf + ldd over a dict: file -> (needed, runpath)."""

    def __init__(self, elves: dict[Path, tuple[list[str], str]], system: set[str] = frozenset()):
        self.needed = {p: n for p, (n, _) in elves.items()}
        self.rpath = {p: r for p, (_, r) in elves.items()}
        self.system = set(system)
        self.sets: list[tuple[Path, str]] = []

    def _dirs(self, f: Path) -> list[Path]:
        out = []
        for e in self.rpath[f].split(":"):
            if e.startswith("$ORIGIN"):
                out.append(Path(os.path.normpath(f.parent / e[len("$ORIGIN"):].lstrip("/"))))
        return out

    def __call__(self, cmd: list) -> str:
        tool, f = cmd[0], Path(cmd[-1])
        if cmd[:2] == ["patchelf", "--print-needed"]:
            return "\n".join(self.needed[f]) + "\n"
        if cmd[:2] == ["patchelf", "--print-rpath"]:
            return self.rpath[f] + "\n"
        if cmd[:2] == ["patchelf", "--set-rpath"]:
            self.sets.append((f, cmd[2]))
            self.rpath[f] = cmd[2]
            return ""
        if tool == "ldd":
            lines = []
            for name in self.needed[f]:
                hit = name in self.system or any((d / name).exists() for d in self._dirs(f))
                lines.append(f"\t{name} => {'/x/' + name if hit else 'not found'}")
            return "\n".join(lines) + "\n"
        raise AssertionError(cmd)


def _elf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x7fELF" + b"\0" * 12)
    return path


def _bundle(tmp_path: Path):
    """The shape that failed the v2.4.0 AppImage: Pillow's freetype copied to _internal/."""
    b = tmp_path / "python-backend"
    exe = _elf(b / "python-backend")
    freetype = _elf(b / "_internal" / "libfreetype-d2d71ad9.so.6")
    png = _elf(b / "_internal" / "pillow.libs" / "libpng16-65c953c0.so.16")
    ft = _elf(b / "_internal" / "PIL" / "_imagingft.so")
    (b / "_internal" / "base_library.zip").write_bytes(b"PK\3\4")
    elves = {
        exe: (["libc.so.6"], ""),
        freetype: (["libpng16-65c953c0.so.16", "libc.so.6"], ""),
        png: (["libc.so.6"], "$ORIGIN"),
        ft: (["libfreetype-d2d71ad9.so.6", "libc.so.6"], "$ORIGIN/../pillow.libs"),
    }
    # the copy _imagingft resolves
    _elf(b / "_internal" / "pillow.libs" / "libfreetype-d2d71ad9.so.6")
    elves[b / "_internal" / "pillow.libs" / "libfreetype-d2d71ad9.so.6"] = (
        ["libpng16-65c953c0.so.16"], "$ORIGIN")
    return b, FakeTools(elves, system={"libc.so.6"}), freetype, ft, exe


def test_a_copied_library_gets_an_entry_to_what_it_needs(tmp_path):
    b, tools, freetype, ft, exe = _bundle(tmp_path)
    assert nbr.normalize(b, tools) == []
    assert tools.rpath[freetype] == "$ORIGIN/pillow.libs:$ORIGIN"
    assert tools.rpath[ft] == "$ORIGIN/../pillow.libs:$ORIGIN"  # kept, $ORIGIN appended
    assert tools.rpath[exe] == "$ORIGIN"
    assert "not found" not in tools(["ldd", str(freetype)])


def test_every_file_ends_with_the_value_linuxdeploy_computes(tmp_path):
    """linuxdeploy keeps a RUNPATH that lists $ORIGIN and appends $ORIGIN otherwise."""
    b, tools, *_ = _bundle(tmp_path)
    nbr.normalize(b, tools)
    for f, value in tools.rpath.items():
        entries = value.split(":")
        assert "$ORIGIN" in entries, f
        linuxdeploy = value if "$ORIGIN" in entries else value + ":$ORIGIN"
        assert linuxdeploy == value


def test_a_second_run_sets_the_same_values(tmp_path):
    b, tools, *_ = _bundle(tmp_path)
    nbr.normalize(b, tools)
    first = dict(tools.rpath)
    tools.sets.clear()
    assert nbr.normalize(b, tools) == []
    assert tools.rpath == first
    assert all(value == first[f] for f, value in tools.sets)


def test_only_elf_files_are_touched(tmp_path):
    b, tools, *_ = _bundle(tmp_path)
    nbr.normalize(b, tools)
    assert {f.name for f, _ in tools.sets} == {f.name for f in tools.rpath}
    assert all(f.suffix != ".zip" for f, _ in tools.sets)


def test_a_dependency_the_bundle_lacks_is_reported(tmp_path):
    b = tmp_path / "python-backend"
    lib = _elf(b / "_internal" / "libvec.so")
    tools = FakeTools({lib: (["libgone.so.1"], "")})
    problems = nbr.normalize(b, tools)
    assert len(problems) == 1 and "libgone.so.1" in problems[0]


def test_ldd_on_a_static_file_is_not_a_problem(tmp_path):
    b = tmp_path / "python-backend"
    lib = _elf(b / "static-tool")
    tools = FakeTools({lib: ([], "")})

    def run(cmd):
        if cmd[0] == "ldd":
            raise subprocess.CalledProcessError(1, cmd, stderr="not a dynamic executable")
        return tools(cmd)

    assert nbr.normalize(b, run) == []
    assert tools.rpath[lib] == "$ORIGIN"


def test_not_found_parses_ldd_output():
    out = ("\tlinux-vdso.so.1 (0x00007ffd)\n"
           "\tlibpng16-65c953c0.so.16 => not found\n"
           "\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f)\n")
    assert nbr.not_found(out) == {"libpng16-65c953c0.so.16"}


@pytest.mark.parametrize("found, expected", [
    ("_internal", "$ORIGIN"),
    ("_internal/pillow.libs", "$ORIGIN/pillow.libs"),
    ("lib", "$ORIGIN/../lib"),
])
def test_rpath_entry_is_relative_to_the_file(tmp_path, found, expected):
    f = tmp_path / "_internal" / "libx.so"
    assert nbr.rpath_entry(f, tmp_path / found) == expected
