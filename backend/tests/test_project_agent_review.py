"""Brain Hub review card: confirming a Project Agent (design
docs/design/2026-10-28-project-agent-flow.md §5, step 3).

Questions these tests answer: does confirm refuse a definition that changed since the
card showed it; does it apply exactly the removals sent (several in one list, highest
index first) and refuse an unknown one; can the default mode be removed (no); does
allowing / withdrawing the Hive change every placement list; do accepted aliases move
into aim.json and the rest disappear; is a result that does not validate refused with
nothing written; is the reviewed file stamped and its source check stored for the new
revision; is confirm refused on a Hive; do the endpoints answer with the right codes,
including the Runner Agent's; does the Hub detail carry what the card needs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from core.project_agent import load_project_agent, revision, source_check_current
from core.project_agent_review import ReviewError, confirm

DOC = {
    "schema": 2, "status": "draft",
    "identity": {"summary": "lib", "source": "PRODUCT.md"},
    "intake": {"handles": ["a", "b", "c"], "repos": [],
               "proposed_aliases": [{"name": "signer", "source": "PRODUCT.md"},
                                    {"name": "tok", "source": "PRODUCT.md"}]},
    "confidence": "research first",
    "watches": [{"area": "x", "why": "y", "source": "PRODUCT.md"}, {"area": "z", "why": "w", "source": "PRODUCT.md"}],
    "gates": {"enforce": ["no_git_push"]},
    "reserved_for_human": ["open_pr", "publish_release", "push", "send_external_message"],
    "placements": ["chat-tab"],
    "landing": {"approval": "human"},
    "modes": [
        {"id": "research", "when": "a question", "profile": "research",
         "profile_rules": [{"when": "deep", "profile": "research"}], "skills": ["s_a", "s_b"]},
        {"id": "release", "when": "build it", "profile": "direct", "placements": ["chat-tab"],
         "gates": {"enforce": ["no_git_push"]}},
    ],
    "default_mode": "research",
}


def _brain(tmp: Path, doc: dict | None = None) -> Path:
    b = tmp / "Brain"
    (b / "2-understanding").mkdir(parents=True)
    (b / "2-understanding" / "PRODUCT.md").write_text("# PRODUCT\n")
    for s in ("s_a", "s_b"):
        (b / "4-capabilities" / s).mkdir(parents=True)
        (b / "4-capabilities" / s / "SKILL.md").write_text(f"---\nname: {s}\n---\n")
    (b / "3-gates").mkdir()
    (b / "3-gates" / "no_git_push.py").write_text("import sys\nsys.exit(0)\n")
    (b / "aim.json").write_text(json.dumps({"name": "Brain", "aliases": ["existing"]}))
    (b / "project-agent.yaml").write_text(yaml.safe_dump(doc or DOC, allow_unicode=True))
    return b


def _ok(b: Path, **kw):
    return confirm(b, seen_revision=revision(b), reviewer="tester", now="2026-10-29T00:00:00Z", **kw)


def test_confirm_stamps_the_file_and_stores_the_source_check(tmp_path):
    b = _brain(tmp_path)
    r = _ok(b)
    doc = yaml.safe_load((b / "project-agent.yaml").read_text())
    assert doc["status"] == "reviewed" and doc["reviewed_by"] == "tester"
    assert doc["reviewed_at"] == "2026-10-29T00:00:00Z"
    assert r["revision"] == revision(b) and source_check_current(b) is not None
    assert "proposed_aliases" not in doc["intake"]          # every proposal was decided


def test_a_changed_definition_is_not_confirmed(tmp_path):
    b = _brain(tmp_path)
    seen = revision(b)
    (b / "4-capabilities" / "s_a" / "run.sh").write_text("echo\n")   # a used skill changed
    with pytest.raises(ReviewError) as e:
        confirm(b, seen_revision=seen, reviewer="t")
    assert e.value.status == 409
    with pytest.raises(ReviewError) as e:
        confirm(b, seen_revision="", reviewer="t")
    assert e.value.status == 409
    assert yaml.safe_load((b / "project-agent.yaml").read_text())["status"] == "draft"


def test_removals_apply_exactly(tmp_path):
    b = _brain(tmp_path)
    _ok(b, remove=["intake.handles[0]", "intake.handles[2]", "watches[1]", "confidence",
                   "modes[research].skills[s_b]", "modes[research].profile_rules[0]",
                   "modes[release]", "gates[no_git_push]", "reserved_for_human[send_external_message]"])
    doc = yaml.safe_load((b / "project-agent.yaml").read_text())
    assert doc["intake"]["handles"] == ["b"]
    assert [w["area"] for w in doc["watches"]] == ["x"]
    assert "confidence" not in doc
    assert [m["id"] for m in doc["modes"]] == ["research"]
    assert doc["modes"][0]["skills"] == ["s_a"] and doc["modes"][0]["profile_rules"] == []
    assert doc["gates"]["enforce"] == []
    assert doc["reserved_for_human"] == ["open_pr", "publish_release", "push"]


def test_a_shared_gate_removed_is_removed_from_every_mode(tmp_path):
    b = _brain(tmp_path)
    _ok(b, remove=["gates[no_git_push]"])
    doc = yaml.safe_load((b / "project-agent.yaml").read_text())
    assert doc["modes"][1]["gates"]["enforce"] == []


@pytest.mark.parametrize("path", ["intake.handles[9]", "watches[5]", "modes[ghost]", "gates[other]",
                                  "modes[research].validations[0]", "modes[research].skills[s_z]",
                                  "../etc", "identity.summary", 7])
def test_an_unknown_removal_is_refused_and_nothing_is_written(tmp_path, path):
    b = _brain(tmp_path)
    before = (b / "project-agent.yaml").read_bytes()
    with pytest.raises(ReviewError) as e:
        _ok(b, remove=[path])
    assert e.value.status == 400 and (b / "project-agent.yaml").read_bytes() == before


def test_the_default_mode_cannot_be_removed(tmp_path):
    b = _brain(tmp_path)
    with pytest.raises(ReviewError) as e:
        _ok(b, remove=["modes[research]"])
    assert e.value.status == 400 and "default mode" in str(e.value)


def test_allowing_and_withdrawing_the_hive(tmp_path):
    b = _brain(tmp_path)
    _ok(b, allow_hive=True)
    doc = yaml.safe_load((b / "project-agent.yaml").read_text())
    assert doc["placements"] == ["chat-tab", "hive"]
    assert doc["modes"][1]["placements"] == ["chat-tab"]           # a narrower mode stays narrower
    doc["modes"][1]["placements"] = ["chat-tab", "hive"]
    doc["status"] = "draft"
    (b / "project-agent.yaml").write_text(yaml.safe_dump(doc))
    _ok(b, allow_hive=False)
    doc = yaml.safe_load((b / "project-agent.yaml").read_text())
    assert doc["placements"] == ["chat-tab"] and doc["modes"][1]["placements"] == ["chat-tab"]


def test_accepted_aliases_move_into_aim_json(tmp_path):
    b = _brain(tmp_path)
    r = _ok(b, aliases=["signer", "signer"])
    assert r["aliasesAdded"] == ["signer"]
    assert json.loads((b / "aim.json").read_text())["aliases"] == ["existing", "signer"]
    b2 = _brain(tmp_path / "two")
    with pytest.raises(ReviewError) as e:
        _ok(b2, aliases=["invented"])
    assert e.value.status == 400
    assert json.loads((b2 / "aim.json").read_text())["aliases"] == ["existing"]


def test_an_invalid_result_is_refused_and_nothing_is_written(tmp_path):
    bad_mode = {**DOC["modes"][1], "skills": ["s_missing"]}
    b = _brain(tmp_path, {**DOC, "modes": [DOC["modes"][0], bad_mode]})
    before = (b / "project-agent.yaml").read_bytes()
    with pytest.raises(ReviewError) as e:
        _ok(b, aliases=["signer"])
    assert e.value.status == 422 and any("s_missing" in p for p in e.value.problems)
    assert (b / "project-agent.yaml").read_bytes() == before
    assert json.loads((b / "aim.json").read_text())["aliases"] == ["existing"]
    _ok(b, remove=["modes[release]"])              # removing the bad mode makes it valid


def test_no_definition_is_404(tmp_path):
    b = tmp_path / "B"
    b.mkdir()
    with pytest.raises(ReviewError) as e:
        confirm(b, seen_revision="x", reviewer="t")
    assert e.value.status == 404


def test_the_hub_detail_carries_what_the_card_needs(tmp_path):
    b = _brain(tmp_path)
    pa = load_project_agent(b)
    assert pa["intake"]["proposedAliases"] == [{"name": "signer", "source": "PRODUCT.md"},
                                               {"name": "tok", "source": "PRODUCT.md"}]
    assert pa["sourceCheck"] is None
    assert pa["sourcePaths"]["PRODUCT.md"] == str(b / "2-understanding" / "PRODUCT.md")
    _ok(b)
    assert load_project_agent(b)["sourceCheck"]["ok"] is True


# ── endpoints ────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    import routers.ddd_brain as rb
    from fastapi import FastAPI
    ws = tmp_path / "ws"
    (ws / "Projects").mkdir(parents=True)
    b = _brain(tmp_path)
    (b / ".project.json").write_text("{}")
    b.rename(ws / "Projects" / "Lib")
    (ws / "Knowledge" / "Library").mkdir(parents=True)
    from core.project_agent_seed import SHIPPED_DIR
    (ws / "Knowledge" / "Library" / "project-agent.yaml").write_bytes((SHIPPED_DIR / "runner.yaml").read_bytes())
    monkeypatch.setattr(rb, "_workspace_root", lambda: ws)
    monkeypatch.setattr(rb, "_projects_root", lambda: ws / "Projects")
    app = FastAPI()
    app.include_router(rb.router, prefix="/api/ddd")
    return TestClient(app), ws


def test_confirm_endpoint(client, monkeypatch):
    c, ws = client
    rev = revision(ws / "Projects" / "Lib")
    r = c.post("/api/ddd/brains/Lib/project-agent/confirm", json={"revision": "stale"})
    assert r.status_code == 409
    r = c.post("/api/ddd/brains/Lib/project-agent/confirm", json={"revision": rev, "remove": ["nope"]})
    assert r.status_code == 400
    r = c.post("/api/ddd/brains/Nope/project-agent/confirm", json={"revision": rev})
    assert r.status_code == 404
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    assert c.post("/api/ddd/brains/Lib/project-agent/confirm", json={"revision": rev}).status_code == 403
    monkeypatch.delenv("SWARMAI_MODE")
    r = c.post("/api/ddd/brains/Lib/project-agent/confirm",
               json={"revision": rev, "allow_hive": True, "aliases": ["tok"]})
    assert r.status_code == 200 and r.json()["aliasesAdded"] == ["tok"]
    assert yaml.safe_load((ws / "Projects" / "Lib" / "project-agent.yaml").read_text())["status"] == "reviewed"


def test_runner_agent_endpoints(client):
    c, ws = client
    pa = c.get("/api/ddd/runner-agent").json()["projectAgent"]
    assert pa["status"] == "draft" and [m["id"] for m in pa["modes"]] == ["research", "write"]
    assert pa["problems"] == []
    r = c.post("/api/ddd/runner-agent/confirm", json={"revision": pa["revision"]})
    assert r.status_code == 200
    assert c.get("/api/ddd/runner-agent").json()["projectAgent"]["status"] == "reviewed"
    (ws / "Knowledge" / "Library" / "project-agent.yaml").unlink()
    assert c.get("/api/ddd/runner-agent").json()["projectAgent"] is None


# ── review round 1 fixes ─────────────────────────────────────────────────────

def test_what_a_mode_adds_can_be_removed_and_a_shared_name_is_not_removed_through_a_mode(tmp_path):
    doc = yaml.safe_load(yaml.safe_dump(DOC))
    doc["modes"][1]["reserved_for_human"] = DOC["reserved_for_human"] + []
    doc["modes"][1]["gates"] = {"enforce": ["no_git_push", "no_git_push_extra"]}
    (tmp_path / "x").mkdir()
    b = _brain(tmp_path, doc)
    (b / "3-gates" / "no_git_push_extra.py").write_text("import sys\nsys.exit(0)\n")
    with pytest.raises(ReviewError):
        _ok(b, remove=["modes[release].gates[no_git_push]"])        # shared: remove it with gates[...]
    with pytest.raises(ReviewError):
        _ok(b, remove=["modes[release].reserved_for_human[push]"])
    _ok(b, remove=["modes[release].gates[no_git_push_extra]"])
    out = yaml.safe_load((b / "project-agent.yaml").read_text())
    assert out["modes"][1]["gates"]["enforce"] == ["no_git_push"]


def test_an_index_counts_the_rows_the_card_shows(tmp_path):
    b = _brain(tmp_path, {**DOC, "watches": ["loose note", {"area": "A", "why": "a", "source": "PRODUCT.md"},
                                              {"area": "B", "why": "b", "source": "PRODUCT.md"}]})
    shown = [w["area"] for w in load_project_agent(b)["reviewFocus"]]
    assert shown == ["A", "B"]
    _ok(b, remove=["watches[0]"])                                     # the card's first row: A
    out = yaml.safe_load((b / "project-agent.yaml").read_text())
    assert out["watches"] == ["loose note", {"area": "B", "why": "b", "source": "PRODUCT.md"}]


def test_an_unreadable_aim_json_stops_before_anything_is_written(tmp_path):
    b = _brain(tmp_path)
    (b / "aim.json").write_text("{not json")
    before = (b / "project-agent.yaml").read_bytes()
    with pytest.raises(ReviewError) as e:
        _ok(b, aliases=["signer"])
    assert e.value.status == 409 and (b / "project-agent.yaml").read_bytes() == before
    _ok(b)                                                            # no alias chosen: aim.json is not needed


def test_an_alias_is_compared_as_text_and_bounded(tmp_path):
    intake = {**DOC["intake"], "proposed_aliases": [{"name": 2024, "source": "PRODUCT.md"},
                                                     {"name": "x" * 41, "source": "PRODUCT.md"}]}
    b = _brain(tmp_path, {**DOC, "intake": intake})
    with pytest.raises(ReviewError) as e:
        _ok(b, aliases=["x" * 41])
    assert e.value.status == 400
    assert _ok(b, aliases=["2024"])["aliasesAdded"] == ["2024"]


def test_a_write_between_the_check_and_the_read_is_refused(tmp_path, monkeypatch):
    import core.project_agent_review as pr
    b = _brain(tmp_path)
    seen = revision(b)

    def racing(project_dir):
        (b / "project-agent.yaml").write_text((b / "project-agent.yaml").read_text() + "# agent edit\n")
        return seen
    monkeypatch.setattr(pr, "revision", racing)
    with pytest.raises(ReviewError) as e:
        confirm(b, seen_revision=seen, reviewer="t")
    assert e.value.status == 409


def test_a_path_with_a_trailing_newline_is_refused(tmp_path):
    b = _brain(tmp_path)
    with pytest.raises(ReviewError):
        _ok(b, remove=["confidence\n"])


def test_p6_without_the_validator_reads_a_v2_definition(tmp_path, monkeypatch):
    import importlib.util
    p = Path(__file__).resolve().parents[1] / "skills/s_project-manager/scripts/verify_ddd_complete.py"
    spec = importlib.util.spec_from_file_location("verify_ddd_complete_fallback", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    def unavailable(doc, d):
        raise ImportError("core.ddd_skill_registry")
    monkeypatch.setattr(mod, "_validate_project_agent", unavailable)
    b = _brain(tmp_path)
    assert mod._check_project_agent(b)[1] == mod.STATUS_PENDING
    (b / "project-agent.yaml").write_text(yaml.safe_dump({**DOC, "status": "reviewed"}))
    assert mod._check_project_agent(b)[1] == mod.STATUS_PASS
    bad = {**DOC, "modes": [{**DOC["modes"][0], "profile": "turbo"}]}
    (b / "project-agent.yaml").write_text(yaml.safe_dump(bad))
    assert mod._check_project_agent(b)[1] == mod.STATUS_FAIL
