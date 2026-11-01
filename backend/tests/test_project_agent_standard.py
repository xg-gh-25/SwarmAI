"""The Project Agent standard and the two built-in Brains (design
docs/design/2026-10-29-default-brains-and-project-agent-standard.md §6, S1-S6).

Questions these tests answer: are the two shipped definitions written to the standard
(valid, keys in the standard order, the standard modes of their shape, none of the keys
a definition no longer has: design 2026-11-01-project-agent-simple); does the real
generator start every draft from the same skeleton and drop retired keys; can no
Projects/ Brain carry the General Brain's name on any path that makes one (create,
rename, register, a Hive's install); does the Hub list carry the General Brain with
what it really takes to a Hive.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from core import project_agent_draft as pad
from core import project_agent_standard as std
from core.project_agent import normalize, validate
from core.project_agent_seed import SHIPPED, SHIPPED_DIR
from tests.test_project_agent_draft import _brain, _repo


def _workspace(tmp: Path) -> Path:
    seed = Path(SHIPPED_DIR).parent / "swarmai-seed"
    swarm = tmp / "Projects" / "SwarmAI" / "2-understanding"
    swarm.mkdir(parents=True)
    for doc in seed.iterdir():
        (swarm / doc.name).write_bytes(doc.read_bytes())
    (tmp / "Knowledge" / "Library").mkdir(parents=True)
    return tmp


# ── S1: the shipped definitions are the standard ─────────────────────────────

@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_a_shipped_definition_is_written_to_the_standard(tmp_path, name):
    ws = _workspace(tmp_path)
    raw = (SHIPPED_DIR / name).read_text()
    doc = yaml.safe_load(raw)
    brain_dir = SHIPPED[name](ws)
    assert raw.startswith(std.HEADER), name
    assert validate(doc, brain_dir) == [], name
    assert list(doc) == list(std.ordered(doc)), name                 # keys in the standard order
    for m in doc["modes"]:
        assert list(m) == list(std.ordered({"modes": [m]})["modes"][0]), (name, m["id"])
    assert [{k: m[k] for k in ("id", "when")} for m in doc["modes"]] == \
        [{k: m[k] for k in ("id", "when")} for m in std.standard_modes(False)]
    assert all("profile" not in m and "profile_rules" not in m for m in doc["modes"]), name
    assert doc["default_mode"] == std.default_mode(False)
    assert 3 <= len(doc["intake"]["handles"]) <= 5
    assert not set(doc) & set(std.RETIRED_KEYS), name


def test_the_standard_modes_of_one_shape_never_share_a_when():
    for has_repo in (True, False):
        whens = [m["when"] for m in std.standard_modes(has_repo)]
        assert len(set(whens)) == len(whens)
    assert [m["id"] for m in std.standard_modes(True)] == ["code-change", "research"]
    assert [m["id"] for m in std.standard_modes(False)] == ["research", "write"]


# ── S2: the generator starts from the same skeleton ──────────────────────────

def _skeleton(doc):
    return [{k: m[k] for k in ("id", "when")} for m in doc["modes"]]


def test_a_draft_without_a_repo_has_the_runner_agents_modes(tmp_path):
    b = _brain(tmp_path, None)
    doc, _ = pad.build(b, {"identity": {"summary": "x", "source": "PRODUCT.md"},
                           "handles": [{"text": "a task", "source": "PRODUCT.md"}]})
    runner = yaml.safe_load((SHIPPED_DIR / "runner.yaml").read_text())
    assert _skeleton(doc) == _skeleton(runner) and doc["default_mode"] == runner["default_mode"]
    assert list(doc) == list(std.ordered(doc))
    assert validate(doc, b) == [] and not set(doc) & set(std.RETIRED_KEYS)


def test_a_draft_with_a_repo_has_code_change_and_research(tmp_path):
    b = _brain(tmp_path, _repo(tmp_path))
    proposal = {"identity": {"summary": "x", "source": "PRODUCT.md"},
                "handles": [{"text": "a task", "source": "PRODUCT.md"}],
                "modes": [{"id": "code-change", "profile": "full", "source": "TECH.md"}]}
    doc, _ = pad.build(b, proposal)
    assert [m["id"] for m in doc["modes"]] == ["code-change", "research"]
    want = {m["id"]: m for m in std.standard_modes(True)}
    assert all(m["when"] == want[m["id"]]["when"] for m in doc["modes"])
    assert doc["default_mode"] == "code-change"
    for m in doc["modes"]:                                         # each mode's keys in the standard order
        assert list(m) == [k for k in std.MODE_ORDER if k in m], m["id"]
    # a proposed profile is not written (a dispatch picks how work runs)
    assert all("profile" not in m for m in doc["modes"])


def test_an_older_definitions_retired_keys_are_dropped_on_the_next_write():
    old = {"schema": 2, "status": "reviewed", "reviewed_by": "x", "placements": ["chat-tab"],
           "reserved_for_human": ["push"], "confidence": "c", "sources": ["a"],
           "identity": {"summary": "s", "in_scope": ["a"], "out_of_scope": ["b"]},
           "intake": {"handles": ["h"], "proposed_aliases": [{"name": "n"}]},
           "modes": [{"id": "a", "when": "w", "delivery": "bundle", "placements": ["hive"],
                      "reserved_for_human": [], "pr_remote": "fork", "profile": "full"}],
           "default_mode": "a"}
    d = std.ordered(old)
    assert set(d) == {"schema", "identity", "intake", "modes", "default_mode"}
    assert d["identity"] == {"summary": "s"} and d["intake"] == {"handles": ["h"]}
    assert d["modes"] == [{"id": "a", "when": "w"}]


def test_the_written_draft_starts_with_the_standard_header(tmp_path):
    b = _brain(tmp_path, None)
    rep = pad.apply(b, {"identity": {"summary": "x", "source": "PRODUCT.md"}})
    assert rep["written"] and (b / "project-agent.yaml").read_text().startswith(std.HEADER)


# ── S4: the General Brain's name ─────────────────────────────────────────────

@pytest.mark.parametrize("name", ["General", "general", "GENERAL"])
def test_no_project_may_be_created_or_renamed_to_the_general_brains_name(tmp_path, name):
    from core.swarm_workspace_manager import SwarmWorkspaceManager
    mgr = SwarmWorkspaceManager()
    (tmp_path / "Projects").mkdir()
    with pytest.raises(ValueError, match="built-in"):
        asyncio.run(mgr.create_project(name, str(tmp_path)))
    assert not (tmp_path / "Projects" / name).exists()
    made = asyncio.run(mgr.create_project("Mine", str(tmp_path)))
    with pytest.raises(ValueError, match="built-in"):
        asyncio.run(mgr.update_project(made["id"], {"name": name}, workspace_path=str(tmp_path)))
    renamed = asyncio.run(mgr.update_project(made["id"], {"name": "Other"}, workspace_path=str(tmp_path)))
    assert renamed["name"] == "Other"


def test_the_general_brains_name_is_never_registered(tmp_path):
    from core.swarm_workspace_manager import ensure_project_metadata
    d = tmp_path / "Projects" / "general"
    d.mkdir(parents=True)
    with pytest.raises(ValueError, match="built-in"):
        ensure_project_metadata(d, "general")
    assert not (d / ".project.json").exists()
    (d / ".project.json").write_text("{}")                          # registered before the rule
    assert ensure_project_metadata(d, "general") is False          # left as it is


def test_the_pipeline_cli_never_mints_a_general_project(tmp_path, monkeypatch):
    from scripts import artifact_cli
    monkeypatch.setattr(artifact_cli, "_get_workspace", lambda: tmp_path)
    with pytest.raises(ValueError, match="built-in"):
        artifact_cli._ensure_project_dir("General")
    assert not (tmp_path / "Projects" / "General").exists()


def test_a_hive_installs_the_general_brain_only_under_its_exact_name(tmp_path):
    from core import brain_sync
    from core.project_agent import general_brain_dir
    assert brain_sync.brain_root(tmp_path, "General") == (general_brain_dir(tmp_path), True)
    for name in ("general", "GENERAL"):
        with pytest.raises(brain_sync.SyncError):
            brain_sync.brain_root(tmp_path, name)


def test_the_host_never_pushes_a_projects_brain_named_general(tmp_path):
    from core import brain_sync_host
    for name in ("general", "Mine"):
        d = tmp_path / "Projects" / name
        d.mkdir(parents=True)
        (d / ".project.json").write_text("{}")
    (tmp_path / "Knowledge" / "Library").mkdir(parents=True)
    names = [r["brain"] for r in brain_sync_host.local_brains(tmp_path)]
    assert names.count("General") == 1 and "general" not in names and "Mine" in names


# ── S5: the Hub list carries the General Brain ───────────────────────────────

def test_the_hub_list_carries_the_general_brain(tmp_path, monkeypatch):
    import routers.ddd_brain as m
    from core.project_agent_seed import seed_shipped
    ws = _workspace(tmp_path)
    lib = ws / "Knowledge" / "Library"
    (lib / "a.md").write_text("# a\n")
    (lib / "sub").mkdir()
    (lib / "sub" / "b.md").write_text("# bb\n")
    (lib / "old-archive.md").write_text("# gone\n")                # not taken to a Hive
    (lib / "data.csv").write_text("x\n")
    seed_shipped(ws)
    monkeypatch.setattr(m, "_workspace_root", lambda: ws)
    g = m._general_summary()
    assert g["name"] == "General" and g["library"] == {"files": 2, "bytes": len("# a\n") + len("# bb\n")}
    runner = yaml.safe_load((SHIPPED_DIR / "runner.yaml").read_text())
    assert g["projectAgentStatus"] == "ready" and g["description"] == runner["identity"]["summary"]
    assert g["shadowed"] is False
    shadow = ws / "Projects" / "general"
    shadow.mkdir()
    (shadow / ".project.json").write_text("{}")
    assert m._general_summary()["shadowed"] is True
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(m.router, prefix="/api/ddd")
    body = TestClient(app).get("/api/ddd/brains").json()
    assert body["general"]["name"] == "General" and "General" not in [b["name"] for b in body["brains"]]
    assert "general" in [b["name"] for b in body["brains"]]         # the shadow stays listed


# ── S6: an unchanged shipped definition follows the new standard ─────────────

def test_an_unchanged_runner_agent_is_replaced_by_the_new_standard(tmp_path):
    from core.project_agent_seed import STATE_FILE, place
    import hashlib
    import json
    lib = tmp_path / "Knowledge" / "Library"
    lib.mkdir(parents=True)
    old = b"schema: 2\nstatus: draft\nmodes: [{id: research, when: old, profile: research}]\n"
    (lib / "project-agent.yaml").write_bytes(old)
    (lib / STATE_FILE).write_text(json.dumps({"sha256": hashlib.sha256(old).hexdigest()}))
    new = (SHIPPED_DIR / "runner.yaml").read_bytes()
    assert place(new, lib) == "updated" and (lib / "project-agent.yaml").read_bytes() == new
    assert normalize(yaml.safe_load(new))["modes"][1]["id"] == "write"


def test_a_standard_mode_of_the_other_shape_is_never_proposed_in(tmp_path):
    with_repo = _brain(tmp_path / "a", _repo(tmp_path))
    doc, dropped = pad.build(with_repo, {"modes": [{"id": "write", "when": "docs", "profile": "direct",
                                                     "source": "TECH.md"}]})
    assert "write" not in [m["id"] for m in doc["modes"]]
    assert any("no write mode" in d for d in dropped)
    without = _brain(tmp_path / "b", None)
    doc, dropped = pad.build(without, {"modes": [{"id": "code-change", "when": "x", "source": "TECH.md"}]})
    assert "code-change" not in [m["id"] for m in doc["modes"]] and any("no bound repo" in d for d in dropped)



def test_a_shipped_copy_differing_only_by_retired_keys_is_replaced(tmp_path):
    """zero-touch R4.2: the retired confirmation rewrote the shipped Runner with a status
    and a new header; the seeder treats that copy as still the product's."""
    from core.project_agent_seed import place
    shipped = b"schema: 2\nidentity: {summary: General}\nintake: {handles: [x]}\nmodes:\n- {id: research, when: q}\n"
    (tmp_path / "project-agent.yaml").write_bytes(b"# Confirmed on the Brain Hub review card\nstatus: reviewed\n" + shipped)
    assert place(shipped, tmp_path) == "updated"
    assert (tmp_path / "project-agent.yaml").read_bytes() == shipped
