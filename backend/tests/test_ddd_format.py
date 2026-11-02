"""The shape CDF writes a DDD doc in, and the lossless move into it (core.ddd_format).

Questions: does ``check`` name every way a doc is out of shape; after ``normalize`` is
a doc in shape, with every line of the input still in the doc or in a reference file
(only a type tag added), and does a second run change nothing; is an entry's type
and section the one CDF's matrix gives it; is the user's own section left alone; does
``--apply`` write the reference files and the doc and leave nothing to do; does the P6
gate report the shape; do the skeleton's section types agree with CDF's matrix.
"""
from __future__ import annotations

import collections
import importlib.util
import re
from pathlib import Path

from core import ddd_format as F
from core import ddd_skeleton as sk

MESSY_TECH = """# Lib — Technical

Some overview prose before any section.

## Architecture

- [decision] **One process** everything runs in one process (2026-01-02, run_x).
- [pitfall] **Clock skew** the cache breaks when the clock jumps.
  <!-- ref:3 | last:2026-02-01 | decay:active -->
- **Uses sqlite** the store is one sqlite file.

| col | col |
|---|---|
| a | b |

~~~
- **Fenced in architecture** not an entry either
~~~

## Dev Commands

- [process] **Run tests** `pytest -q`
- a plain bullet with no title
```bash
## not a heading
- not an entry
- **Looks like an entry** inside a fence
```

## Runtime Traps

- [x] **Done thing** it was done.
"""


def _norm(line: str) -> str:
    line = re.sub(r"^- \[\w+\] ", "- ", line.rstrip())
    return re.sub(r"^(- \*\*.+?\*\*) \(\w+\)", r"\1", line)


def _covered(original: str, res: F.Result) -> list[str]:
    out = collections.Counter(_norm(ln) for ln in (res.text + "\n" + "\n".join(res.moved.values())).splitlines())
    missing = []
    for ln in original.splitlines():
        if not ln.strip() or ln.startswith("## "):
            continue
        k = _norm(ln)
        if out[k]:
            out[k] -= 1
        else:
            missing.append(ln)
    return missing


def test_check_names_each_way_a_doc_is_out_of_shape():
    kinds = {p.kind for p in F.check("TECH.md", MESSY_TECH)}
    found = F.check("TECH.md", MESSY_TECH)
    assert kinds >= {"extra_section", "not_an_entry", "untyped", "no_meta", "wrong_type"}
    assert any("CDF never writes a [pitfall] here" in p.detail for p in found)   # a real type, wrong section


def test_normalize_is_in_shape_loses_nothing_and_is_idempotent():
    res = F.normalize("TECH.md", MESSY_TECH, brain="Lib", today="2026-10-29")
    assert F.check("TECH.md", res.text) == []
    assert _covered(MESSY_TECH, res) == []
    again = F.normalize("TECH.md", res.text, brain="Lib", today="2026-10-29")
    assert again.text == res.text and again.moved == {}
    assert sk.headings(res.text) == sk.skeleton_sections("TECH.md")


def test_an_entry_goes_where_cdf_routes_its_type():
    res = F.normalize("TECH.md", MESSY_TECH, brain="Lib", today="2026-10-29")
    secs = {s.heading: "\n".join(s.lines) for s in F._sections(res.text) if s.heading}
    assert "**Clock skew**" in secs["Runtime Traps"]            # a pitfall never sits in Architecture
    assert "<!-- ref:3 | last:2026-02-01 | decay:active -->" in secs["Runtime Traps"]  # metadata kept
    assert "- [decision] **One process**" in secs["Architecture"]
    assert "<!-- source:manual -->" in secs["Architecture"]     # an entry without metadata gets it
    assert "- [process] **Run tests**" in secs["Conventions"]  # a typed entry leaves its own section
    assert "- [pitfall] **Done thing** (x) it was done." in secs["Runtime Traps"]
    assert re.search(r"- \[(decision|principle)\] \*\*Uses sqlite\*\*", secs["Architecture"])


def test_what_is_not_an_entry_moves_verbatim_to_the_brains_knowledge():
    res = F.normalize("TECH.md", MESSY_TECH, brain="Lib", today="2026-10-29")
    assert set(res.moved) == {"tech/overview.md", "tech/architecture-notes.md", "tech/dev-commands.md"}
    dev = res.moved["tech/dev-commands.md"]
    assert dev.startswith("# Lib — Dev Commands")
    assert "- a plain bullet with no title" in dev
    assert "## not a heading\n- not an entry\n- **Looks like an entry** inside a fence\n```" in dev  # a fence moves whole
    assert "Looks like an entry" not in res.text
    assert "| a | b |" in res.moved["tech/architecture-notes.md"]
    assert "Fenced in architecture" in res.moved["tech/architecture-notes.md"]
    assert "Fenced in architecture" not in res.text


def test_an_untyped_entry_in_what_failed_is_a_failure():
    text = "# P\n\n## What Failed\n\n- **Shipped a broken build** nothing caught it.\n"
    res = F.normalize("IMPROVEMENT.md", text, brain="P", today="2026-10-29")
    assert re.search(r"- \[(pitfall|correction)\] \*\*Shipped a broken build\*\*", res.text)


def test_the_users_own_sections_are_left_alone():
    text = ("# P\n\n## Current Focus\n\nShip the thing.\n| a | b |\n\n## Recent Decisions\n\n"
            "## Open Items\n\n## Blocked By\n\nNothing.\n")
    res = F.normalize("PROJECT.md", text, brain="P", today="2026-10-29")
    secs = {s.heading: "\n".join(s.lines) for s in F._sections(res.text) if s.heading}
    assert "Ship the thing.\n| a | b |" in secs["Current Focus"] and "Nothing." in secs["Blocked By"]
    assert res.moved == {} and F.check("PROJECT.md", res.text) == []


def test_skeleton_section_types_agree_with_cdfs_matrix():
    from core.ingestion.contracts import DDD_MATRIX
    routed: dict[tuple[str, str], set[str]] = {}
    for by_type in DDD_MATRIX.values():
        for etype, where in by_type.items():
            routed.setdefault(where, set()).add(etype)
    assert {k: frozenset(v) for k, v in routed.items()} == sk.SECTION_TYPES
    for (doc, section), types in sk.SECTION_TYPES.items():
        for t in types:
            assert sk.home_section(doc, t) in sk.skeleton_sections(doc)
        assert sk.default_type(doc, section) in types


def test_a_fresh_skeleton_is_in_shape():
    for doc in sk.DOCS:
        assert F.check(doc, sk.render_skeleton(doc, "P")) == []


def _workspace_project(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    proj = tmp_path / "ws" / "Projects" / "Lib"
    (proj / "2-understanding").mkdir(parents=True)
    for doc in sk.DOCS:
        (proj / "2-understanding" / doc).write_text(sk.render_skeleton(doc, "Lib"), encoding="utf-8")
    (proj / "2-understanding" / "TECH.md").write_text(MESSY_TECH, encoding="utf-8")
    return proj


def test_apply_writes_reference_files_then_the_doc_and_leaves_nothing_to_do(tmp_path, monkeypatch):
    proj = _workspace_project(tmp_path, monkeypatch)
    tech = proj / "2-understanding" / "TECH.md"
    dry = F.apply_project(proj)
    assert tech.read_text(encoding="utf-8") == MESSY_TECH and not (proj / "2-understanding" / "knowledge").exists()
    assert dry["docs"]["TECH.md"]["changed"] and dry["docs"]["TECH.md"]["after"] == []
    F.apply_project(proj, apply=True)
    assert F.check("TECH.md", tech.read_text(encoding="utf-8")) == []
    k = proj / "2-understanding" / "knowledge" / "tech"
    assert "- a plain bullet with no title" in (k / "dev-commands.md").read_text(encoding="utf-8")
    again = F.apply_project(proj, apply=True)
    assert not any(v.get("changed") for v in again["docs"].values())


def test_p6_reports_the_shape(tmp_path, monkeypatch):
    proj = _workspace_project(tmp_path, monkeypatch)
    p = Path(__file__).resolve().parents[1] / "skills/s_project-manager/scripts/verify_ddd_complete.py"
    spec = importlib.util.spec_from_file_location("verify_ddd_complete_fmt", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    checks = {n: (st, dt) for n, st, dt in mod._check_knowledge(proj)}
    status, detail = checks["② Knowledge/format"]
    assert status == mod.STATUS_FAIL and "TECH.md" in detail and "ddd-format --project Lib --apply" in detail
    F.apply_project(proj, apply=True)
    checks = {n: st for n, st, _dt in mod._check_knowledge(proj)}
    assert checks["② Knowledge/format"] == mod.STATUS_PASS


def test_an_interrupted_apply_does_not_move_a_block_twice(tmp_path, monkeypatch):
    """The reference files are written before the doc; when the doc write fails, the
    next run must not add the same block to them again."""
    proj = _workspace_project(tmp_path, monkeypatch)
    from core.ingestion import governed_edit

    real = governed_edit.apply_user_edit

    def fail(path, *a, **k):
        if Path(path).name == "TECH.md":
            raise RuntimeError("disk full")
        return real(path, *a, **k)

    monkeypatch.setattr(governed_edit, "apply_user_edit", fail)
    try:
        F.apply_project(proj, apply=True)
    except RuntimeError:
        pass
    monkeypatch.setattr(governed_edit, "apply_user_edit", real)
    F.apply_project(proj, apply=True)
    dev = (proj / "2-understanding" / "knowledge" / "tech" / "dev-commands.md").read_text(encoding="utf-8")
    assert dev.count("- a plain bullet with no title") == 1


def test_a_fence_with_a_blank_line_inside_an_entry_stays_in_the_entry():
    text = ("# T\n\n## Conventions\n\n- [process] **T1** run this:\n  ```bash\n  a\n\n  b\n  ```\n"
            "  <!-- ref:4 | last:2026-01-01 | decay:active -->\n- [guideline] **T2** keep it.\n"
            "  <!-- ref:0 | last:2026-01-01 | decay:active -->\n")
    res = F.normalize("TECH.md", text, brain="P", today="2026-10-29")
    assert res.moved == {} and F.check("TECH.md", res.text) == []
    assert "<!-- ref:4 | last:2026-01-01" in res.text and "**T2**" in res.text
    again = F.normalize("TECH.md", res.text, brain="P", today="2026-10-29")
    assert again.text == res.text and again.moved == {}


def test_an_unreadable_metadata_line_gets_a_readable_one():
    text = "# T\n\n## Conventions\n\n- [guideline] **G** x.\n  <!-- ref:3 | last:2026-02-02 | decay:active | weird:1 -->\n"
    res = F.normalize("TECH.md", text, brain="P", today="2026-10-29")
    assert "\n  <!-- source:manual -->\n" in res.text


def test_a_moved_section_keeps_its_heading_even_when_two_share_a_file():
    text = "# T\n\n## Stack Notes\n\nalpha\n\n## Stack   notes\n\nbeta\n"
    res = F.normalize("TECH.md", text, brain="P", today="2026-10-29")
    body = res.moved["tech/stack-notes.md"]
    assert "## Stack Notes\n\nalpha" in body and "## Stack   notes\n\nbeta" in body


def test_a_short_block_already_in_the_reference_file_is_still_moved(tmp_path, monkeypatch):
    """Skipping is by the block's digest, never by its text occurring in the file."""
    proj = _workspace_project(tmp_path, monkeypatch)
    (proj / "2-understanding" / "TECH.md").write_text("# T\n\n## Architecture\n\nUses X\n", encoding="utf-8")
    k = proj / "2-understanding" / "knowledge" / "tech"
    k.mkdir(parents=True)
    (k / "architecture-notes.md").write_text("# Lib — Architecture (notes)\n\nUses Xavier for builds.\n",
                                             encoding="utf-8")
    F.apply_project(proj, apply=True)
    assert "\nUses X\n" in (k / "architecture-notes.md").read_text(encoding="utf-8") + "\n"
    assert "Uses X" not in (proj / "2-understanding" / "TECH.md").read_text(encoding="utf-8")


def test_one_doc_failing_is_reported_and_the_others_are_written(tmp_path, monkeypatch):
    proj = _workspace_project(tmp_path, monkeypatch)
    (proj / "2-understanding" / "PROJECT.md").write_text(
        "# P\n\n## Recent Decisions\n\n- **Untyped** decision.\n", encoding="utf-8")
    from core.ingestion import governed_edit
    real = governed_edit.apply_user_edit

    def fail(path, *a, **k):
        if Path(path).name == "TECH.md":
            raise RuntimeError("disk full")
        return real(path, *a, **k)

    monkeypatch.setattr(governed_edit, "apply_user_edit", fail)
    report = F.apply_project(proj, apply=True)
    assert "disk full" in report["docs"]["TECH.md"]["error"]
    assert re.search(r"- \[\w+\] \*\*Untyped\*\* decision\.\n  <!-- source:manual -->", (proj / "2-understanding" / "PROJECT.md").read_text(encoding="utf-8"))


def test_dangling_references_reports_only_paths_it_can_place(tmp_path, monkeypatch):
    """A Brain path, a workspace path and a home path that do not exist are reported;
    a path under an unknown base, a .git path and an existing file are not."""
    proj = _workspace_project(tmp_path, monkeypatch)
    ws = proj.parent.parent
    (proj / "2-understanding" / "knowledge").mkdir()
    (proj / "2-understanding" / "knowledge" / "here.md").write_text("x\n")
    (proj / "2-understanding" / "TECH.md").write_text(
        "# T\n\n## Architecture\n\n- [decision] **Refs** see `knowledge/here.md`, `knowledge/gone.md`, "
        "`Knowledge/Notes/gone.md`, `core/unknown_base.py`, `.git/index.lock`, `~/no-such-dir-xyz/a.md`.\n"
        "  <!-- ref:0 | last:2026-10-29 | decay:active -->\n", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    from core.code_intel import local_roots
    monkeypatch.setattr(local_roots, "confirmed", lambda project: [str(repo)])
    found = {ref for _f, _line, ref in F.dangling_references(proj, ws)}
    assert found == {"knowledge/gone.md", "Knowledge/Notes/gone.md", "~/no-such-dir-xyz/a.md"}


def test_p6_lists_dangling_references_without_failing(tmp_path, monkeypatch):
    proj = _workspace_project(tmp_path, monkeypatch)
    (proj / "AGENTS.md").write_text("Members: `3-gates/missing.py`\n", encoding="utf-8")
    p = Path(__file__).resolve().parents[1] / "skills/s_project-manager/scripts/verify_ddd_complete.py"
    spec = importlib.util.spec_from_file_location("verify_ddd_complete_refs", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    checks = {n: (st, dt) for n, st, dt in mod._check_knowledge(proj)}
    status, detail = checks["② Knowledge/references"]
    assert status == mod.STATUS_PENDING and "AGENTS.md:1 3-gates/missing.py" in detail


def test_the_daily_health_check_reports_shape_and_references(tmp_path, monkeypatch):
    proj = _workspace_project(tmp_path, monkeypatch)
    (proj / "AGENTS.md").write_text("Members: `3-gates/missing.py`\n", encoding="utf-8")
    from hooks.context_health_hook import ContextHealthHook
    hook = ContextHealthHook.__new__(ContextHealthHook)
    found = hook._check_ddd_shape(proj.parent.parent)
    assert any("DDD-FORMAT: Brain 'Lib'" in f for f in found)             # MESSY_TECH is out of shape
    assert any("DDD-REFS: Brain 'Lib'" in f and "3-gates/missing.py" in f for f in found)
