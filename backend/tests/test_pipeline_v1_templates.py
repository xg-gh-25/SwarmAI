"""Every document template goes through the real entry point (design 2026-11-01-
pipeline-delivery-friction D7): ``pipeline-v1 template <kind>`` prints it; with its
placeholders filled by a run's own values it is accepted where the run asks for it.
A schema change that a template no longer meets turns this test red."""
from __future__ import annotations

import pytest

from core.run_protocol import templates
from tests.pipeline_v1_rig import PROJECT, TEST_REF, Rig

FINDING = ("{\"severity\":\"HIGH\",\"kind\":\"behavior\",\"file\":\"backend/core/mod.py\",\"line\":2,"
           "\"claim\":\"wrong order\",\"fix\":\"swap\"}")

pytestmark = pytest.mark.timeout(400)

VALUES = {"<FILE>": "backend/core/mod.py", "<SYMBOL>": "add",
          "<TEST_FILE>": "backend/tests/test_x.py", "<TEST_REF>": TEST_REF, "<REPO>": "repo",
          "<COMMAND>": "python -m pytest backend/tests/test_x.py", "<PROJECT>": PROJECT}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    rigs: list[Rig] = []

    def make(profile: str) -> Rig:
        r = Rig(tmp_path / f"case{len(rigs)}", monkeypatch, profile=profile)
        rigs.append(r)
        return r
    yield make
    for r in rigs:
        r.close()


def _doc(r: Rig, kind: str, **extra: str) -> str:
    out = r.v1("template", kind)
    assert out.get("ok") is True and out["kind"] == kind, out
    assert set(out["placeholders"]) <= set(templates.PLACEHOLDERS)
    return r.artifact(kind, templates.fill(out["document"], {**VALUES, **extra}))


def test_every_kind_has_a_template_and_only_known_tokens():
    for kind in templates.KINDS:
        row = templates.template(kind)
        assert row["document"] and row["kind"] == kind
    with pytest.raises(ValueError):
        templates.template("nope")


def test_full_run_documents_are_accepted_from_their_templates(rig):
    r = rig("full")
    r.create()
    out = r.v1("stage-complete", "--artifact", _doc(r, "requirements"))
    assert out.get("ok") is True and r.state()["stage"] == "think", out
    r.v1("stage-complete")
    r.edit(r.CHANGE)
    out = r.v1("stage-complete", "--artifact", _doc(r, "design_spec"))
    assert out.get("ok") is True and r.state()["stage"] == "build", out
    out = r.v1("stage-complete", "--artifact", _doc(r, "validation_report"))
    assert out.get("ok") is True and r.state()["stage"] == "review", out
    started = r.v1("review-start")
    for i, handout in enumerate(started["handouts"]):
        r.spawn_reviewer(handout["prompt"], FINDING if i == 0 else "NO FINDINGS", f"r1-{i}")
    from core.run_protocol import review
    engine = r.engine()
    finding = review.ReviewOrchestrator(engine.store, engine.run_dir).findings()[0]["canonical_id"]
    out = r.v1("review-address", "--artifact", _doc(r, "review_address", **{"<FINDING_ID>": finding}))
    assert out.get("ok") is True, out


def test_trivial_plan_and_reflect_are_accepted_from_their_templates(rig):
    r = rig("trivial")
    r.create()
    r.drive(until="plan")
    r.edit(r.CHANGE)
    out = r.v1("stage-complete", "--artifact", _doc(r, "design_spec-lite"))
    assert out.get("ok") is True and r.state()["stage"] == "build", out
    r.drive(until="deliver")
    from core.run_protocol.provenance import run_roots
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect0", {
        "delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}))
    anchor = sorted(run_roots(r.run_id))[0]
    out = r.v1("reflect-prepare", "--artifact", _doc(r, "reflect", **{"<ANCHOR>": anchor}))
    assert out.get("ok") is True and not out["reflect"].get("dropped"), out
    assert len(out["reflect"]["sections"]["delivered"]) == 1


def test_a_refused_report_names_its_template(rig):
    r = rig("direct")
    r.create()
    r.drive(until="build")
    r.edit(r.CHANGE)
    out = r.v1("stage-complete", "--artifact", r.artifact("bad", {"reports": [{"repo": "repo"}]}))
    assert out.get("ok") is False and out.get("shape") == "pipeline-v1 template validation_report", out
