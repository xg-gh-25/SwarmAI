"""Brain Hub form: changing and confirming a Project Agent (design
docs/design/2026-10-29-project-agent-standard-template.md §7).

Questions these tests answer: does edit refuse a definition that changed since the form
showed it, a path the form does not give out, a value of the wrong shape, a value for a
field that comes from the Brain; does it change exactly what was sent, write a draft
(a confirmed definition goes back to draft) and refuse a result that does not validate
with nothing written; do gates and reserved actions dropped on the shared part go from
every mode too, and does withdrawing the Hive narrow every mode; does confirm stamp the
file, move the proposed aliases still on the form into aim.json and store the source
check; are the endpoints JSON-from-the-app only, refused on a Hive, and do they answer
with the right codes, including the Runner Agent's.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from core.project_agent import load_project_agent, revision, source_check_current
from core.project_agent_review import ReviewError, confirm, edit

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


def _ok(b: Path):
    return confirm(b, seen_revision=revision(b), reviewer="tester", now="2026-10-29T00:00:00Z")


def _edit(b: Path, *changes):
    return edit(b, seen_revision=revision(b), changes=list(changes))


def _doc(b: Path) -> dict:
    return yaml.safe_load((b / "project-agent.yaml").read_text())


def test_confirm_stamps_the_file_and_stores_the_source_check(tmp_path):
    b = _brain(tmp_path)
    r = _ok(b)
    doc = _doc(b)
    assert doc["status"] == "reviewed" and doc["reviewed_by"] == "tester"
    assert doc["reviewed_at"] == "2026-10-29T00:00:00Z"
    assert r["revision"] == revision(b) and source_check_current(b) is not None
    assert "proposed_aliases" not in doc["intake"]


def test_a_changed_definition_is_not_confirmed_or_edited(tmp_path):
    b = _brain(tmp_path)
    seen = revision(b)
    (b / "4-capabilities" / "s_a" / "run.sh").write_text("echo\n")   # a used skill changed
    for call in (lambda: confirm(b, seen_revision=seen, reviewer="t"),
                 lambda: edit(b, seen_revision=seen, changes=[{"path": "confidence", "value": "x"}]),
                 lambda: confirm(b, seen_revision="", reviewer="t")):
        with pytest.raises(ReviewError) as e:
            call()
        assert e.value.status == 409
    assert _doc(b)["status"] == "draft" and _doc(b)["confidence"] == "research first"


def test_edit_changes_exactly_what_was_sent_and_writes_a_draft(tmp_path):
    b = _brain(tmp_path, {**DOC, "status": "reviewed", "reviewed_by": "x", "reviewed_at": "t"})
    r = _edit(b, {"path": "identity.summary", "value": "  a signing library  "},
              {"path": "intake.handles", "value": ["fix a signer bug", " ", "add a key"]},
              {"path": "watches", "value": [{"area": "timing", "why": "constant time", "source": "PRODUCT.md"}]},
              {"path": "modes[research].when", "value": "a question about signing"},
              {"path": "modes[research].profile_rules", "value": [{"when": "a design", "profile": "full"}]},
              {"path": "modes[release].profile", "value": "trivial"},
              {"path": "confidence", "value": ""})
    doc = _doc(b)
    assert r["status"] == "draft" and doc["status"] == "draft" and doc["reviewed_by"] is None
    assert doc["identity"]["summary"] == "a signing library" and doc["identity"]["source"] == "PRODUCT.md"
    assert doc["intake"]["handles"] == ["fix a signer bug", "add a key"]
    assert doc["watches"] == [{"area": "timing", "why": "constant time", "source": "PRODUCT.md"}]
    modes = {m["id"]: m for m in doc["modes"]}
    assert modes["research"]["when"] == "a question about signing"
    assert modes["research"]["profile_rules"] == [{"when": "a design", "profile": "full"}]
    assert modes["release"]["profile"] == "trivial" and "confidence" not in doc
    assert (b / "project-agent.yaml").read_text().startswith("# Project Agent definition")


@pytest.mark.parametrize("change", [
    {"path": "../etc", "value": "x"},
    {"path": "status", "value": "reviewed"},                      # not a form field
    {"path": "intake.repos", "value": []},                        # read from the Brain
    {"path": "modes[ghost].when", "value": "x"},
    {"path": "modes[research].repos", "value": []},               # from the Brain, not removable
    {"path": "modes[research].setup", "value": []},               # rows from the Brain: drop only
    {"path": "identity.summary", "value": "two\nlines"},
    {"path": "identity.summary", "value": 7},
    {"path": "modes[research].profile", "value": "turbo"},
    {"path": "modes[research].profile_rules", "value": [{"when": "x", "profile": "turbo"}]},
    {"path": "gates.enforce", "value": ["no_git_push", "another"]},   # a gate is only dropped here
    {"path": "placements", "value": ["hive"]},                    # a chat tab is always allowed
    {"path": "placements", "value": ["chat-tab", "slack"]},       # not a placement yet
    {"path": "reserved_for_human", "value": ["launch_missiles"]},
    {"path": "modes[research].skills", "value": ["s_missing"]},
    {"path": "intake.proposed_aliases", "remove": 5},
    {"path": "confidence", "remove": 0},
    {"path": "confidence"},
    "confidence",
])
def test_a_change_the_form_may_not_make_is_refused_and_nothing_is_written(tmp_path, change):
    b = _brain(tmp_path)
    before = (b / "project-agent.yaml").read_bytes()
    with pytest.raises(ReviewError) as e:
        _edit(b, change)
    assert e.value.status == 400
    assert (b / "project-agent.yaml").read_bytes() == before


def test_rows_from_the_brain_and_aliases_can_be_dropped(tmp_path):
    doc = yaml.safe_load(yaml.safe_dump(DOC))
    doc["modes"][0]["setup"] = [{"command": "npm ci", "source": "PRODUCT.md"}, {"command": "make", "source": "PRODUCT.md"}]
    b = _brain(tmp_path, doc)
    _edit(b, {"path": "modes[research].setup", "remove": 0}, {"path": "intake.proposed_aliases", "remove": 1})
    out = _doc(b)
    assert out["modes"][0]["setup"] == [{"command": "make", "source": "PRODUCT.md"}]
    assert out["intake"]["proposed_aliases"] == [{"name": "signer", "source": "PRODUCT.md"}]


def test_a_shared_gate_or_reserved_action_dropped_goes_from_every_mode(tmp_path):
    doc = yaml.safe_load(yaml.safe_dump(DOC))
    doc["modes"][1]["reserved_for_human"] = DOC["reserved_for_human"] + []
    b = _brain(tmp_path, doc)
    _edit(b, {"path": "gates.enforce", "value": []},
          {"path": "reserved_for_human", "value": ["open_pr", "push", "send_external_message"]})
    out = _doc(b)
    assert out["gates"]["enforce"] == [] and out["modes"][1]["gates"]["enforce"] == []
    assert out["reserved_for_human"] == out["modes"][1]["reserved_for_human"] == \
        ["open_pr", "push", "send_external_message"]


def test_allowing_and_withdrawing_the_hive(tmp_path):
    b = _brain(tmp_path)
    _edit(b, {"path": "placements", "value": ["chat-tab", "hive"]})
    out = _doc(b)
    assert out["placements"] == ["chat-tab", "hive"]
    assert out["modes"][1]["placements"] == ["chat-tab"]               # a mode that stays here stays
    doc = {**out, "modes": [out["modes"][0], {**out["modes"][1], "placements": ["chat-tab", "hive"]}]}
    (b / "project-agent.yaml").write_text(yaml.safe_dump(doc))
    _edit(b, {"path": "placements", "value": ["chat-tab"]})
    out = _doc(b)
    assert out["placements"] == ["chat-tab"] and out["modes"][1]["placements"] == ["chat-tab"]


def test_the_aliases_still_on_the_form_move_into_aim_json(tmp_path):
    b = _brain(tmp_path)
    _edit(b, {"path": "intake.proposed_aliases", "remove": 0})
    r = _ok(b)
    assert r["aliasesAdded"] == ["tok"]
    assert json.loads((b / "aim.json").read_text())["aliases"] == ["existing", "tok"]


def test_an_invalid_result_is_refused_and_nothing_is_written(tmp_path):
    bad_mode = {**DOC["modes"][1], "skills": ["s_missing"]}
    b = _brain(tmp_path, {**DOC, "modes": [DOC["modes"][0], bad_mode]})
    before = (b / "project-agent.yaml").read_bytes()
    for call in (lambda: _ok(b), lambda: _edit(b, {"path": "confidence", "value": "x"})):
        with pytest.raises(ReviewError) as e:
            call()
        assert e.value.status == 422 and any("s_missing" in p for p in e.value.problems)
    assert (b / "project-agent.yaml").read_bytes() == before
    assert json.loads((b / "aim.json").read_text())["aliases"] == ["existing"]
    _edit(b, {"path": "modes[release].skills", "value": []})          # the form fixes it
    assert _ok(b)["revision"] == revision(b)


def test_no_definition_is_404(tmp_path):
    b = tmp_path / "B"
    b.mkdir()
    with pytest.raises(ReviewError) as e:
        confirm(b, seen_revision="x", reviewer="t")
    assert e.value.status == 404


def test_an_unreadable_aim_json_stops_before_anything_is_written(tmp_path):
    b = _brain(tmp_path)
    (b / "aim.json").write_text("{not json")
    before = (b / "project-agent.yaml").read_bytes()
    with pytest.raises(ReviewError) as e:
        _ok(b)
    assert e.value.status == 409 and (b / "project-agent.yaml").read_bytes() == before
    _edit(b, {"path": "intake.proposed_aliases", "remove": 0}, {"path": "intake.proposed_aliases", "remove": 0})
    _ok(b)                                                            # no alias left: aim.json is not needed


def test_an_alias_is_bounded(tmp_path):
    intake = {**DOC["intake"], "proposed_aliases": [{"name": 2024, "source": "PRODUCT.md"},
                                                     {"name": "x" * 41, "source": "PRODUCT.md"}]}
    b = _brain(tmp_path, {**DOC, "intake": intake})
    assert _ok(b)["aliasesAdded"] == ["2024"]


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


H = {"X-Swarm-User-Action": "project-agent"}


def test_confirm_and_edit_endpoints(client, monkeypatch):
    c, ws = client
    rev = revision(ws / "Projects" / "Lib")
    assert c.post("/api/ddd/brains/Lib/project-agent/confirm", json={"revision": rev}).status_code == 415
    assert c.post("/api/ddd/brains/Lib/project-agent/confirm", json={"revision": "stale"}, headers=H).status_code == 409
    assert c.post("/api/ddd/brains/Nope/project-agent/confirm", json={"revision": rev}, headers=H).status_code == 404
    r = c.patch("/api/ddd/brains/Lib/project-agent", headers=H,
                json={"revision": rev, "changes": [{"path": "nope", "value": 1}]})
    assert r.status_code == 400
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    assert c.post("/api/ddd/brains/Lib/project-agent/confirm", json={"revision": rev}, headers=H).status_code == 403
    assert c.patch("/api/ddd/brains/Lib/project-agent", headers=H,
                   json={"revision": rev, "changes": [{"path": "confidence", "value": "x"}]}).status_code == 403
    monkeypatch.delenv("SWARMAI_MODE")
    r = c.patch("/api/ddd/brains/Lib/project-agent", headers=H,
                json={"revision": rev, "changes": [{"path": "placements", "value": ["chat-tab", "hive"]}]})
    assert r.status_code == 200 and r.json()["status"] == "draft"
    r = c.post("/api/ddd/brains/Lib/project-agent/confirm", json={"revision": r.json()["revision"]}, headers=H)
    assert r.status_code == 200 and r.json()["aliasesAdded"] == ["signer", "tok"]
    doc = yaml.safe_load((ws / "Projects" / "Lib" / "project-agent.yaml").read_text())
    assert doc["status"] == "reviewed" and doc["placements"] == ["chat-tab", "hive"]


def test_runner_agent_endpoints(client):
    c, ws = client
    pa = c.get("/api/ddd/runner-agent").json()["projectAgent"]
    assert pa["status"] == "draft" and [m["id"] for m in pa["modes"]] == ["research", "write"]
    assert pa["problems"] == [] and pa["form"]["sections"][0]["fields"][0]["value"] == "Runner Agent"
    r = c.patch("/api/ddd/runner-agent", headers=H,
                json={"revision": pa["revision"], "changes": [{"path": "confidence", "value": "ask first"}]})
    assert r.status_code == 200
    r = c.post("/api/ddd/runner-agent/confirm", json={"revision": r.json()["revision"]}, headers=H)
    assert r.status_code == 200
    assert c.get("/api/ddd/runner-agent").json()["projectAgent"]["status"] == "reviewed"
    (ws / "Knowledge" / "Library" / "project-agent.yaml").unlink()
    assert c.get("/api/ddd/runner-agent").json()["projectAgent"] is None


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
    skeleton = {**DOC, "identity": {}, "intake": {}}
    (b / "project-agent.yaml").write_text(yaml.safe_dump(skeleton))
    assert mod._check_project_agent(b)[1] == mod.STATUS_FAIL              # nobody drafted it yet
    (b / "project-agent.yaml").write_text(yaml.safe_dump({**DOC, "status": "reviewed"}))
    assert mod._check_project_agent(b)[1] == mod.STATUS_PASS
    bad = {**DOC, "modes": [{**DOC["modes"][0], "profile": "turbo"}]}
    (b / "project-agent.yaml").write_text(yaml.safe_dump(bad))
    assert mod._check_project_agent(b)[1] == mod.STATUS_FAIL
