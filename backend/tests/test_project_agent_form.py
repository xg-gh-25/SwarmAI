"""The Project Agent standard template and the Brain Hub form (design
docs/design/2026-10-29-project-agent-standard-template.md §5, §6, §10).

Questions these tests answer: is TEMPLATE the one field list (STANDARD.md, the
proposal format and the key order are generated from it); does the form show every
field in the template's order with its kind, and only true facts (a desktop-only skill
does not reach a Hive, a clone URL never carries a credential); does showing the form
leave the definition and its revision alone; is every path the form gives out one the
edit accepts; does a new Brain get a skeleton, which P6 fails until it is drafted;
does a redraft keep what a person changed on the form; does binding a repo give a
draft its code-change mode without losing the judged fields.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import yaml

from core import project_agent_draft as pad
from core import project_agent_standard as std
from core.project_agent import load_project_agent, revision
from core.project_agent_form import _public_url, build_form
from core.project_agent_review import edit

STANDARD_MD = Path(__file__).resolve().parents[1] / "templates/project-agents/STANDARD.md"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root,
                   check=True, capture_output=True)


def _repo(tmp: Path) -> Path:
    r = tmp / "repo"
    r.mkdir()
    (r / "validations.json").write_text(json.dumps({"schema": 1, "entries": [
        {"id": "unit", "paths": ["**"], "cwd": ".", "argv": ["python", "-m", "pytest", "-q"]}]}))
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def _brain(tmp: Path) -> Path:
    b = tmp / "Projects" / "Lib"
    (b / "2-understanding").mkdir(parents=True)
    for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
        (b / "2-understanding" / doc).write_text(f"# {doc}\n")
    for name, platform in (("s_lib-release", "desktop"), ("s_lib-check", None)):
        (b / "4-capabilities" / name).mkdir(parents=True)
        head = (f"---\nname: {name}\ndescription: \"Does {name}. TRIGGER: x\"\n"
                + (f"platform: {platform}\n" if platform else "") + "---\n")
        (b / "4-capabilities" / name / "SKILL.md").write_text(head)
    return b


def _bind(b: Path, repo: Path, clone: str = "https://github.com/acme/lib.git") -> None:
    (b / "bindings.yaml").write_text(
        "bindings:\n  - repo: lib\n    kind: external\n"
        f'    clone: "{clone}"\n    worktree: {repo}\n'
        "    delivery_contract: {remote_kind: github-pr, branch: main, review_path: github-pr, auto_send: manual-push}\n")


# ── the template is the one field list ───────────────────────────────────────

def test_standard_md_is_generated_from_the_template():
    text = STANDARD_MD.read_text()
    a = text.index("<!-- TEMPLATE:BEGIN")
    a = text.index("\n", a) + 1
    b = text.index("<!-- TEMPLATE:END -->")
    assert text[a:b].strip() == std.standard_md_table()
    for _sid, title, _purpose in std.SECTIONS:
        assert f"**{title}**" in text


def test_the_proposal_format_and_key_order_come_from_the_template():
    keys = pad.PROPOSAL_CONTRACT["keys"]
    assert {k for k in keys if k != "modes"} == set(std.proposal_keys())
    assert set(std.mode_proposal_keys()) <= set(keys["modes"][0])
    for f in std.TEMPLATE:
        if f.store and not f.store.startswith("modes[]."):
            assert f.store.split(".")[0] in std.SHARED_ORDER, f.key
        elif f.store:
            assert f.store[len("modes[]."):] in std.MODE_ORDER, f.key


# ── the form ─────────────────────────────────────────────────────────────────

def test_the_form_shows_every_field_in_order_with_true_facts(tmp_path):
    b = _brain(tmp_path)
    _bind(b, _repo(tmp_path), clone="https://user:s3cret@github.com/acme/lib.git")
    pad.apply(b, {"identity": {"summary": "A lib", "source": "PRODUCT.md"}})
    form = load_project_agent(b)["form"]
    assert [s["id"] for s in form["sections"]] == [s[0] for s in std.SECTIONS]
    assert [f["key"] for s in form["sections"] for f in s["fields"]] == [f.key for f in std.TEMPLATE]
    fields = {f["key"]: f for s in form["sections"] for f in s["fields"]}
    assert fields["summary"]["value"] == "A lib" and fields["summary"]["kindLabel"] == "Editable"
    repo = fields["repos"]["value"][0]
    assert repo["remote"] == "https://github.com/acme/lib.git" and "s3cret" not in json.dumps(form)
    skills = {s["name"]: s for s in fields["skills"]["value"]}
    assert skills["s_lib-release"]["hive"].startswith("no") and skills["s_lib-check"]["hive"] == "yes"
    assert skills["s_lib-check"]["description"] == "Does s_lib-check."
    assert fields["when"]["paths"]["code-change"] == "modes[code-change].when"
    assert "enforced when the agent runs on a Hive" in form["enforced"]
    assert [p["value"] for p in fields["placements"]["choices"]] == ["chat-tab", "hive", "slack"]
    assert not next(p for p in fields["placements"]["choices"] if p["value"] == "slack")["available"]


def test_showing_the_form_writes_nothing(tmp_path):
    b = _brain(tmp_path)
    pad.apply(b, {})
    before, rev = (b / "project-agent.yaml").read_bytes(), revision(b)
    load_project_agent(b)
    assert (b / "project-agent.yaml").read_bytes() == before and revision(b) == rev


def test_every_path_the_form_gives_out_is_one_edit_accepts(tmp_path):
    b = _brain(tmp_path)
    _bind(b, _repo(tmp_path))
    pad.apply(b, {"identity": {"summary": "A lib", "source": "PRODUCT.md"},
                  "handles": [{"text": "fix a bug", "source": "PRODUCT.md"}]})
    form = build_form(yaml.safe_load((b / "project-agent.yaml").read_text()), b)
    for s in form["sections"]:
        for f in s["fields"]:
            if not f["editable"] or (f["kind"] == "brain" and f["shape"] != "names"):
                continue
            pairs = f["paths"].items() if f["scope"] == "mode" else [(None, f["path"])]
            for mid, path in pairs:
                value = f["values"][mid] if mid else f["value"]
                if value is None:
                    continue
                edit(b, seen_revision=revision(b), changes=[{"path": path, "value": value}])


def test_a_clone_url_never_shows_a_credential():
    assert _public_url("https://tok@github.com/a/b.git") == "https://github.com/a/b.git"
    assert _public_url("git@github.com:a/b.git") is None
    assert _public_url("https://github.com:8443/a/b") == "https://github.com:8443/a/b"


# ── a new Brain has a skeleton ───────────────────────────────────────────────

def _p6(b: Path):
    p = Path(__file__).resolve().parents[1] / "skills/s_project-manager/scripts/verify_ddd_complete.py"
    spec = importlib.util.spec_from_file_location("verify_ddd_complete_form", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod._check_project_agent(b)[1], mod


def test_a_registered_brain_gets_a_skeleton_that_p6_fails_until_drafted(tmp_path):
    from core.swarm_workspace_manager import ensure_project_metadata
    b = tmp_path / "Projects" / "Fresh"
    assert ensure_project_metadata(b, "Fresh")
    doc = yaml.safe_load((b / "project-agent.yaml").read_text())
    assert doc["status"] == "draft" and std.is_skeleton(doc)
    assert [m["id"] for m in doc["modes"]] == ["research", "write"]
    status, mod = _p6(b)
    assert status == mod.STATUS_FAIL
    pad.apply(b, {"identity": {"summary": "Fresh work", "source": ".project.json"}})
    assert _p6(b)[0] == mod.STATUS_PENDING
    assert pad.ensure_skeleton(b) is None                   # one is there: nothing is written


def test_a_redraft_keeps_what_a_person_changed(tmp_path):
    b = _brain(tmp_path)
    _bind(b, _repo(tmp_path))
    pad.apply(b, {"identity": {"summary": "A lib", "source": "PRODUCT.md"},
                  "handles": [{"text": "fix a bug", "source": "PRODUCT.md"}]})
    edit(b, seen_revision=revision(b), changes=[
        {"path": "identity.summary", "value": "The signing library"},
        {"path": "placements", "value": ["chat-tab", "hive"]},
        {"path": "reserved_for_human", "value": ["open_pr", "push"]},
        {"path": "modes[code-change].validations", "remove": 0}])
    pad.apply(b, {"watches": [{"area": "timing", "why": "constant time", "source": "PRODUCT.md"}]})
    doc = yaml.safe_load((b / "project-agent.yaml").read_text())
    assert doc["identity"]["summary"] == "The signing library"
    assert doc["intake"]["handles"] == ["fix a bug"] and doc["watches"][0]["area"] == "timing"
    assert doc["placements"] == ["chat-tab", "hive"] and doc["reserved_for_human"] == ["open_pr", "push"]
    code = next(m for m in doc["modes"] if m["id"] == "code-change")
    assert code["validations"] == []                       # the dropped check stays dropped
    pad.apply(b, {}, merge=False)                          # --fresh starts over
    assert std.is_skeleton(yaml.safe_load((b / "project-agent.yaml").read_text()))


def test_binding_a_repo_gives_a_draft_its_code_change_mode(tmp_path):
    b = _brain(tmp_path)
    pad.apply(b, {"identity": {"summary": "A lib", "source": "PRODUCT.md"}})
    assert [m["id"] for m in yaml.safe_load((b / "project-agent.yaml").read_text())["modes"]] == ["research", "write"]
    _bind(b, _repo(tmp_path))
    r = pad.refresh_draft(b)
    assert r["problems"] == [] and r["written"]
    doc = yaml.safe_load((b / "project-agent.yaml").read_text())
    ids = [m["id"] for m in doc["modes"]]
    assert "code-change" in ids and doc["identity"]["summary"] == "A lib"
    code = next(m for m in doc["modes"] if m["id"] == "code-change")
    assert code["validations"][0]["command"] == "python -m pytest -q"


def test_the_front_end_holds_no_field_list():
    """The form is drawn from what the backend sends: the component builds no stored
    path itself (it sends back the path each field came with), so a field added to the
    template needs no front-end change to be shown and saved."""
    tsx = (Path(__file__).resolve().parents[2] / "desktop/src/components/layout/ProjectAgentForm.tsx").read_text()
    assert "`modes[" not in tsx and "'modes[" not in tsx and "\"modes[" not in tsx
    for f in std.TEMPLATE:
        if f.store and not f.store.startswith("modes[]."):
            for q in ("'", '"', "`"):
                assert f"{q}{f.store}{q}" not in tsx, f.store


# ── review round 1 ───────────────────────────────────────────────────────────

def test_a_redraft_takes_in_gates_and_skills_added_since(tmp_path):
    b = _brain(tmp_path)
    pad.apply(b, {"identity": {"summary": "A lib", "source": "PRODUCT.md"}})
    (b / "3-gates").mkdir(exist_ok=True)
    (b / "3-gates" / "newgate.py").write_text("import sys\nsys.exit(0)\n")
    (b / "4-capabilities" / "s_new").mkdir()
    (b / "4-capabilities" / "s_new" / "SKILL.md").write_text("---\nname: s_new\n---\n")
    pad.apply(b, {})
    doc = yaml.safe_load((b / "project-agent.yaml").read_text())
    assert "newgate" in doc["gates"]["enforce"]
    assert any("s_new" in (m.get("skills") or []) for m in doc["modes"])


def test_a_bad_port_in_a_clone_url_does_not_break_the_form(tmp_path):
    assert _public_url("https://host:99999/x") is None
    b = _brain(tmp_path)
    _bind(b, _repo(tmp_path), clone="https://github.com:99999/acme/lib.git")
    pad.apply(b, {"identity": {"summary": "A lib", "source": "PRODUCT.md"}})
    assert load_project_agent(b)["form"] is not None


def test_a_write_drops_the_retired_keys(tmp_path):
    """A definition written before landing / drafted_at were removed loses them on its
    next write, and the rest is unchanged."""
    b = _brain(tmp_path)
    pad.apply(b, {"identity": {"summary": "A lib", "source": "PRODUCT.md"}})
    path = b / "project-agent.yaml"
    old = yaml.safe_load(path.read_text())
    path.write_text(yaml.safe_dump({**old, "drafted_at": "2026-10-01T00:00:00Z",
                                    "landing": {"approver": "you"}}, sort_keys=False))
    edit(b, seen_revision=revision(b), changes=[{"path": "identity.summary", "value": "The lib"}])
    new = yaml.safe_load(path.read_text())
    assert "landing" not in new and "drafted_at" not in new
    assert new["identity"]["summary"] == "The lib" and new["modes"] == old["modes"]
    assert set(std.RETIRED_KEYS).isdisjoint(std.SHARED_ORDER)
