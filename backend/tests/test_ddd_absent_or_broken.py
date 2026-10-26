"""No DDD, a partial DDD or a broken DDD never crashes recall, chat or a pipeline run.

run-knowledge §3.6.5 (H1-H5). One matrix of DDD states, each checked on every reader:

- the platform recall (``recall_ddd``, every purpose) and its loader: a doc that cannot be
  read is skipped ALONE, the other docs still answer (H1; red on main, where one
  non-UTF-8 doc raised and took the whole project's DDD recall down);
- chat injection (``_inject_ddd_for_active_project``) never raises and still injects
  what the readable docs hold;
- the pipeline's readers in-process: ``run_knowledge.build`` / ``expert_block`` /
  ``reflect_job.candidates_for``, including a recall that raises (H3: ``unavailable``
  with the reason, never ``none``, never blank);
- the real CLI: full, bugfix and direct runs reach ``landed`` in every state, with a
  knowledge.md (and, after EVALUATE, a knowledge_design.md and its knowledge_recalled
  event) whose status matches the state, and a REPORT (H4).

Fixtures build the workspace from scratch under tmp_path; no one's real DDD is read.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.pipeline_v1_rig import PROJECT, Rig

TRAP = "- **Operands trap**: swapping the operands of add needs the commutativity test.\n"
STATES = ("no_project", "no_docs", "tech_only", "product_only", "one_doc_not_utf8",
          "headings_only")
# whether any purpose recalls something (the platform view)
EXPECTED = {"no_project": "none", "no_docs": "none", "tech_only": "recalled",
            "product_only": "recalled", "one_doc_not_utf8": "recalled",
            "headings_only": "none"}
# per purpose, for the rig's REQUIREMENT (no data.db, no lessons in the rig, so only the
# DDD leg can recall anything): requirement reads PRODUCT / PROJECT, design TECH /
# IMPROVEMENT (run-knowledge §3.6.3)
BY_PURPOSE = {
    "requirement": {**{k: "none" for k in STATES}, "product_only": "recalled"},
    "design": {**{k: "none" for k in STATES}, "tech_only": "recalled",
               "one_doc_not_utf8": "recalled"},
}


def _make(ws: Path, state: str) -> None:
    """Put Projects/<PROJECT> into *state* (the rig creates the empty dir)."""
    base = ws / "Projects" / PROJECT
    und = base / "2-understanding"
    if state == "no_project":
        shutil.rmtree(base, ignore_errors=True)
        return
    und.mkdir(parents=True, exist_ok=True)
    if state == "tech_only":
        (und / "TECH.md").write_text(f"# TECH\n\n## Traps\n\n{TRAP}", encoding="utf-8")
    elif state == "product_only":
        (und / "PRODUCT.md").write_text(f"# PRODUCT\n\n## Principles\n\n{TRAP}",
                                        encoding="utf-8")
    elif state == "one_doc_not_utf8":
        (und / "TECH.md").write_text(f"# TECH\n\n## Traps\n\n{TRAP}", encoding="utf-8")
        (und / "PROJECT.md").write_bytes(b"\xff\xfe## Recent Decisions\n\n- \xff broken\n")
    elif state == "headings_only":
        (und / "TECH.md").write_text("# TECH\n\n## Traps\n\n## Conventions\n", encoding="utf-8")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    (root / "Projects" / PROJECT).mkdir(parents=True)
    monkeypatch.setenv("SWARM_WORKSPACE", str(root))
    return root


# ── platform recall + its loader (H1, H2) ───────────────────────────────────

@pytest.mark.parametrize("state", STATES)
def test_platform_recall_never_raises(ws, state):
    from core.recall_multi import DDD_PURPOSES, recall_ddd
    _make(ws, state)
    found = []
    for purpose in DDD_PURPOSES:
        hits = recall_ddd("swap operands add", PROJECT, purpose)
        assert isinstance(hits, list)
        found += [h for h in hits if h["kind"] in ("entry", "section")]
    assert bool(found) == (EXPECTED[state] == "recalled"), found


def test_one_unreadable_doc_is_skipped_alone(ws, caplog):
    from core.recall_multi import _load_ddd_docs_text
    _make(ws, "one_doc_not_utf8")
    _base, docs = _load_ddd_docs_text(PROJECT)
    assert "TECH.md" in docs and "PROJECT.md" not in docs
    assert any("PROJECT.md" in r.getMessage() for r in caplog.records), \
        "a skipped doc is logged, not hidden"


# ── chat injection ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("state", STATES)
def test_chat_injection_never_raises(ws, state):
    from types import SimpleNamespace
    from core.session_router import _inject_ddd_for_active_project
    _make(ws, state)
    o = SimpleNamespace(system_prompt="")
    _inject_ddd_for_active_project(o, "swap the operands of add", (PROJECT, "signal1_project_path"))
    if state in ("tech_only", "one_doc_not_utf8"):
        assert "Operands trap" in o.system_prompt or "Traps" in o.system_prompt, o.system_prompt


# ── pipeline readers in-process (H3) ────────────────────────────────────────

@pytest.mark.parametrize("state", STATES)
def test_pipeline_readers_never_raise(ws, state, tmp_path):
    from core.run_protocol import run_knowledge as rk
    from core.run_protocol.project_binding import has_ddd_docs
    from core.run_protocol.reflect_job import candidates_for
    _make(ws, state)
    has = has_ddd_docs(PROJECT)
    for purpose, expected in BY_PURPOSE.items():
        k = rk.build("Swap the operands of core.mod.add.", project=PROJECT, has_ddd=has,
                     workspace=ws, repo_roots={}, purpose=purpose)
        assert k.status == expected[state], (purpose, k.status, k.errors)
        assert k.markdown.strip() and f"Status: {k.status}" in k.markdown
    text, ids, status = rk.expert_block(project=PROJECT, has_ddd=has,
                                        paths=["backend/core/mod.py"],
                                        requirement="swap the operands of add")
    assert status in ("recalled", "none")
    assert (status == "recalled") == bool(ids)
    out = candidates_for([{"id": "L1", "statement": "operands of add"}], PROJECT)
    assert isinstance(out.get("L1"), list)


def test_a_recall_that_raises_is_unavailable_with_its_reason(ws):
    from core.run_protocol import run_knowledge as rk
    _make(ws, "tech_only")

    def boom(*_a, **_k):
        raise RuntimeError("index exploded")
    k = rk.build("Swap the operands of core.mod.add.", project=PROJECT, has_ddd=True,
                 workspace=ws, repo_roots={}, ddd=boom)
    assert k.status == "unavailable" and "index exploded" in " ".join(k.errors)
    assert "index exploded" in k.markdown
    text, ids, status = rk.expert_block(project=PROJECT, has_ddd=True,
                                        paths=["backend/core/mod.py"],
                                        requirement="swap the operands of add", ddd=boom)
    assert status == "unavailable" and not ids and "failed" in text


# ── the real CLI: every profile lands in every state (H4) ───────────────────

@pytest.mark.parametrize("profile", ("full", "bugfix", "direct"))
@pytest.mark.parametrize("state", STATES)
def test_run_lands_in_every_ddd_state(tmp_path, monkeypatch, state, profile):
    r = Rig(tmp_path, monkeypatch, profile=profile)
    _make(r.ws, state)
    out = r.create()
    first = "design" if profile == "direct" else "requirement"   # direct has no EVALUATE
    want = BY_PURPOSE[first][state]
    assert out["knowledge"]["status"] == want, out["knowledge"]
    text = Path(out["knowledge"]["file"]).read_text(encoding="utf-8")
    assert f"Status: {want}" in text
    state_out = r.drive()
    if profile != "direct":         # EVALUATE's completion wrote the design recall
        design = Path(out["knowledge"]["file"]).with_name("knowledge_design.md")
        assert f"Status: {BY_PURPOSE['design'][state]}" in design.read_text(encoding="utf-8")
        recalled = [e for e in r.events() if e["kind"] == "knowledge_recalled"]
        assert [e["payload"]["status"] for e in recalled] == [BY_PURPOSE["design"][state]]
    assert state_out["status"] == "completed" and state_out["landing"] == "landed", state_out
    report = r.repo / ".aidlc" / "deliverables" / r.run_id / "REPORT.md"
    assert report.is_file() and "Delivered:" in report.read_text(encoding="utf-8")
