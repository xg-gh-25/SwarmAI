"""An abandoned run whose change landed outside the pipeline (design
docs/design/2026-10-31-pipeline-review-close-and-landing-route.md §3 change 4).

- ``landed-as`` records the commit only when it is on the integration branch and
  carries the change REVIEW completed on; anything else is refused (T11);
- once recorded, the run no longer needs anyone (``pipeline_metrics.needs_you``), it
  cannot be recorded twice, and nothing is left to restore.
"""
from __future__ import annotations

import json

import pytest

from tests.pipeline_v1_rig import Rig, _git, write

pytestmark = pytest.mark.timeout(300)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    yield r
    if r.run_id:
        assert r.v1("status").get("ok") is True, "the event log no longer folds"
    r.close()


def _abandoned(r: Rig) -> None:
    r.create()
    r.drive(until="deliver")
    assert r.v1("abandon", "--reason", "landed by hand").get("ok") is True


def _run_json(r: Rig) -> dict:
    engine = r.engine()
    return json.loads((engine.run_dir / "run.json").read_text(encoding="utf-8"))


def test_landed_as_takes_only_the_run_s_own_change_on_main(rig):
    from core import pipeline_metrics as pm
    r = rig
    _abandoned(r)
    assert pm.needs_you("abandoned", None, _run_json(r).get("reason"), _run_json(r).get("landed_as"))
    other = r.commit_on_main({"docs/elsewhere.md": "not the run's change\n"})
    refused = r.v1("landed-as", other)
    assert refused.get("ok") is False and "does not carry the reviewed change" in refused["error"]
    _git(r.repo, "checkout", "-q", "-b", "side")
    side = r.commit_on_main(dict(Rig.CHANGE), "the change, on a side branch")
    _git(r.repo, "checkout", "-q", "main")
    off = r.v1("landed-as", side)
    assert off.get("ok") is False and "is not on" in off["error"], off
    write(r.repo, dict(Rig.CHANGE))
    _git(r.repo, "add", "-A")
    _git(r.repo, "commit", "-q", "-m", "fix: swap the operands")
    landed = _git(r.repo, "rev-parse", "HEAD")
    # main moved before the hand landing (docs/elsewhere.md): only the run's own change counts
    out = r.v1("landed-as", landed)
    assert out.get("ok") is True and out["landed_as"] == {"repo": landed}, out
    raw = _run_json(r)
    assert raw["landed_as"] == {"repo": landed} and raw["status"] == "abandoned"
    assert pm.needs_you(raw["status"], None, raw.get("reason"), raw.get("landed_as")) is False
    again = r.v1("landed-as", landed)
    assert again.get("ok") is False and "already recorded" in again["error"], again
    restore = r.v1("restore")
    assert restore.get("ok") is False, restore


def test_abandon_with_landed_as_records_both(rig):
    r = rig
    r.create()
    r.drive(until="deliver")
    write(r.repo, dict(Rig.CHANGE))
    _git(r.repo, "add", "-A")
    _git(r.repo, "commit", "-q", "-m", "fix: swap the operands")
    landed = _git(r.repo, "rev-parse", "HEAD")
    out = r.v1("abandon", "--reason", "landed natively", "--landed-as", f"repo={landed}")
    assert out.get("ok") is True and out["landed_as"] == {"repo": landed}, out
    assert _run_json(r)["landed_as"] == {"repo": landed}
