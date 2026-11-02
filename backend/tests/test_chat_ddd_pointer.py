"""Chat's [DDD:<project>] lines say where to read (knowledge addressing §2.6, §6.3 test
10): an entry by its title and file:line, a section by its heading line -- not a bare
"TECH.md § Architecture" naming a 110K section in a file the cwd may not contain."""
from pathlib import Path

from core.session_router import _ddd_hit_line

TECH = "# TECH\n\n## Architecture\n\nintro\n\n- [pitfall] **Quokka rule** — never feed quokkas after midnight.\n"


def test_entry_hit_gives_title_and_path_line(tmp_path):
    base = tmp_path / "Projects" / "P"
    (base / "2-understanding").mkdir(parents=True)
    (base / "2-understanding" / "TECH.md").write_text(TECH, encoding="utf-8")
    hit = {"doc": "TECH.md", "section": "Architecture",
           "content": "- [pitfall] **Quokka rule** — never feed quokkas after midnight."}
    line = _ddd_hit_line("P", base, {"TECH.md": TECH}, hit)
    path = base / "2-understanding" / "TECH.md"
    assert line.startswith("- ") and "Quokka rule" in line and f"{path}:7 (§ Architecture)" in line


def test_section_hit_gives_heading_line(tmp_path):
    base = tmp_path / "Projects" / "P"
    (base / "2-understanding").mkdir(parents=True)
    (base / "2-understanding" / "TECH.md").write_text(TECH, encoding="utf-8")
    line = _ddd_hit_line("P", base, {"TECH.md": TECH}, {"doc": "TECH.md", "section": "Architecture"})
    assert line == f"- § Architecture — {base / '2-understanding' / 'TECH.md'}:3 (§ Architecture)"


def test_unknown_file_falls_back_to_doc_and_section():
    assert _ddd_hit_line("P", None, {}, {"doc": "TECH.md", "section": "X"}) == "- **TECH.md** § X"


def test_whitespace_or_comment_first_content_never_crashes(tmp_path):
    base = tmp_path / "Projects" / "P"
    (base / "2-understanding").mkdir(parents=True)
    (base / "2-understanding" / "TECH.md").write_text(TECH, encoding="utf-8")
    assert "§ Architecture" in _ddd_hit_line("P", base, {"TECH.md": TECH},
                                             {"doc": "TECH.md", "section": "Architecture", "content": "  \n "})
    line = _ddd_hit_line("P", base, {"TECH.md": TECH}, {"doc": "TECH.md", "section": "Architecture",
                                                        "content": "<!-- ref:0 -->\nplain words"})
    assert "<!--" not in line


def test_locator_finds_a_repeated_line_in_its_own_section():
    from core.recall_multi import _locator
    text = ("# T\n\n## A\n\n```\nalpha code\n```\n\n---\n\n## B\n\n```\nbeta code\n```\n\n---\n\n"
            "- **dup** — same\n- **dup** — same\n\nlast block\n")
    loc = _locator("T.md", Path("/p/T.md"), text)
    assert loc("B", "```\nbeta code\n```") == "/p/T.md:13 (§ B)"
    assert loc("B", "---") == "/p/T.md:17 (§ B)"          # not section A's ---
    assert loc("B", "") == "/p/T.md:11 (§ B)"              # nothing to match: the heading


BIG = ("# TECH\n\n## Architecture\n\nintro paragraph\n\n"
       "- [decision] **Wombat cache** — the wombat cache is flushed hourly.\n"
       "  <!-- ref:0 | last:none | decay:active -->\n"
       "- [pitfall] **Quokka rule** — never feed quokkas after midnight.\n"
       "- plain bullet about platypus burrows\n")


def _ddd(tmp_path):
    base = tmp_path / "Projects" / "P"
    (base / "2-understanding").mkdir(parents=True)
    (base / "2-understanding" / "TECH.md").write_text(BIG, encoding="utf-8")
    return base


def test_a_section_hit_points_at_its_best_entry(tmp_path):
    """Recall returns most chat hits as a whole section (no content); the pointer named
    the section heading -- in SwarmAI a 100K section (E2E after deploy, §2.6)."""
    from core.session_router import _best_entry_of_section
    base = _ddd(tmp_path)
    hit = _best_entry_of_section({"doc": "TECH.md", "section": "Architecture"}, base,
                                 {"TECH.md": BIG}, "quokkas midnight")
    line = _ddd_hit_line("P", base, {"TECH.md": BIG}, hit)
    assert line == f"- Quokka rule — {base / '2-understanding' / 'TECH.md'}:9 (§ Architecture)"


def test_a_plain_bullet_title_has_no_doubled_marker(tmp_path):
    from core.session_router import _best_entry_of_section
    base = _ddd(tmp_path)
    hit = _best_entry_of_section({"doc": "TECH.md", "section": "Architecture"}, base,
                                 {"TECH.md": BIG}, "platypus burrows")
    assert _ddd_hit_line("P", base, {"TECH.md": BIG}, hit).startswith("- plain bullet about platypus")


def test_no_matching_entry_keeps_the_heading_pointer(tmp_path):
    from core.session_router import _best_entry_of_section
    base = _ddd(tmp_path)
    hit = {"doc": "TECH.md", "section": "Architecture"}
    assert _best_entry_of_section(hit, base, {"TECH.md": BIG}, "zebra") is hit
    assert _best_entry_of_section({"doc": "x.md", "section": "(whole file)"}, base, {}, "q")["doc"] == "x.md"


def test_the_injected_block_names_entries_not_sections(tmp_path, monkeypatch):
    """Through _inject_ddd_for_active_project with a section-level recall hit."""
    import types
    from core import recall_multi, session_router as sr
    base = _ddd(tmp_path)
    monkeypatch.setattr(recall_multi, "recall_all", lambda *a, **k: types.SimpleNamespace(
        buckets={"ddd": [{"doc": "TECH.md", "section": "Architecture", "score": 1.0}]}))
    monkeypatch.setattr(recall_multi, "_load_ddd_docs_text", lambda p: (base, {"TECH.md": BIG}))
    out: list = []
    sr._inject_ddd_for_active_project(types.SimpleNamespace(system_prompt=""),
                                      "why never feed quokkas after midnight", ("P", "signal3_keyword"),
                                      deliver=out.append)
    block = "".join(out)
    assert "Quokka rule" in block and "TECH.md:9 (§ Architecture)" in block
    assert "- § Architecture" not in block


def test_the_entry_is_taken_from_the_hit_section_only(tmp_path):
    from core.session_router import _best_entry_of_section
    text = BIG + "\n## Other\n\n- quokkas midnight quokkas midnight quokkas\n"
    base = _ddd(tmp_path)
    (base / "2-understanding" / "TECH.md").write_text(text, encoding="utf-8")
    hit = _best_entry_of_section({"doc": "TECH.md", "section": "Architecture"}, base,
                                 {"TECH.md": text}, "quokkas midnight")
    assert "Quokka rule" in hit["content"]


def test_the_ddd_pointer_says_where_the_code_is_and_where_to_write_it(tmp_path, monkeypatch):
    # fixing-spec 2026-11-02-new-brain-setup G1: an agent coding natively learns the
    # repository's checkout and the rule (a session worktree of it), not only the Brain
    import subprocess
    from types import SimpleNamespace
    from core import session_router as sr
    repo = tmp_path / "Repos" / "app"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    base = tmp_path / "Projects" / "P"
    base.mkdir(parents=True)
    (base / "bindings.yaml").write_text(
        "bindings:\n  - repo: app\n    kind: external\n    clone: https://github.com/acme/app.git\n"
        f"    worktree: {repo}\n    delivery_contract: {{remote_kind: github-pr, branch: main, "
        "review_path: local, auto_send: manual-push}\n")
    monkeypatch.setattr("core.repo_placement.repos_dir", lambda: tmp_path / "Repos")
    monkeypatch.setattr("core.addressing.project_root", lambda project: base)
    monkeypatch.setattr(sr, "_record_ddd_inject", lambda *a: None)
    got: list[str] = []
    sr._inject_ddd_for_active_project(SimpleNamespace(system_prompt=""), "继续",
                                      ("P", "signal1_project_path"), deliver=got.append)
    assert len(got) == 1 and "[DDD:P]" in got[0]
    assert f"checkout {repo.resolve()}" in got[0] and "session_worktree create" in got[0]


def test_a_brain_without_a_repository_gets_no_repository_lines(tmp_path):
    from core.session_router import _ddd_repo_lines
    (tmp_path / "P").mkdir()
    assert _ddd_repo_lines(tmp_path / "P") == ""
    assert _ddd_repo_lines(None) == ""


def test_the_ddd_block_with_hits_also_says_where_the_code_is(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from core import session_router as sr
    repo = tmp_path / "Repos" / "app"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    base = tmp_path / "Projects" / "P"
    (base / "2-understanding").mkdir(parents=True)
    (base / "2-understanding" / "TECH.md").write_text(TECH, encoding="utf-8")
    (base / "bindings.yaml").write_text(
        "bindings:\n  - repo: app\n    kind: external\n    clone: https://github.com/acme/app.git\n"
        f"    worktree: {repo}\n    delivery_contract: {{remote_kind: github-pr, branch: main, "
        "review_path: local, auto_send: manual-push}\n")
    monkeypatch.setattr("core.repo_placement.repos_dir", lambda: tmp_path / "Repos")
    monkeypatch.setattr(sr, "_record_ddd_inject", lambda *a: None)
    hit = {"doc": "TECH.md", "section": "Architecture",
           "content": "- [pitfall] **Quokka rule** — never feed quokkas after midnight."}
    monkeypatch.setattr("core.recall_multi.recall_all",
                        lambda *a, **k: SimpleNamespace(buckets={"ddd": [hit]}))
    monkeypatch.setattr("core.recall_multi._load_ddd_docs_text", lambda project: (base, {"TECH.md": TECH}))
    got: list[str] = []
    sr._inject_ddd_for_active_project(SimpleNamespace(system_prompt=""), "quokka rule after midnight",
                                      ("P", "signal1_project_path"), deliver=got.append)
    assert len(got) == 1 and "Quokka rule" in got[0]
    assert f"checkout {repo.resolve()}" in got[0]


def test_a_hive_gets_no_host_repository_lines(tmp_path, monkeypatch):
    # a Hive works in the task clones its workspace paragraph names; telling it to bind
    # into ~/SwarmAI-Repos would be the wrong advice
    from core.session_router import _ddd_repo_lines
    monkeypatch.setattr("core.code_intel.local_roots.hive_mode", lambda: True)
    monkeypatch.setattr("core.repo_placement.describe", lambda d: ["- app: checkout /x"])
    assert _ddd_repo_lines(tmp_path) == ""
    monkeypatch.setattr("core.code_intel.local_roots.hive_mode", lambda: False)
    assert "checkout /x" in _ddd_repo_lines(tmp_path)
