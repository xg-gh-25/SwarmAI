"""Knowledge Admission — Component B: the passed_adversarial_gate field + stamping.

The trust field is stamped at proposal creation by resolving the source run's
run.json and deriving the canonical gate2 outcome. Fail-closed: an unresolvable
run, a non-run source (session decision/correction), or a run without a canonical
gate2_outcome → 'n/a', NEVER 'passed'. (Design AC3.)
"""
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class TestTrustField:
    def test_field_exists_and_defaults_na(self):
        from core.ddd_cultivation import CultivationProposal
        p = CultivationProposal(
            target_doc="TECH.md", target_section="Architecture",
            content="x" * 40, source_run_id="run_abc", confidence=0.7,
        )
        assert p.passed_adversarial_gate == "n/a"  # fail-closed default

    def test_field_roundtrips_through_dict(self):
        from core.ddd_cultivation import CultivationProposal
        p = CultivationProposal(
            target_doc="TECH.md", target_section="Architecture",
            content="x" * 40, source_run_id="run_abc", confidence=0.7,
            passed_adversarial_gate="passed",
        )
        d = p.to_dict()
        assert d["passed_adversarial_gate"] == "passed"
        p2 = CultivationProposal.from_dict(d)
        assert p2.passed_adversarial_gate == "passed"

    def test_from_dict_missing_field_defaults_na(self):
        # backward-compat: an old proposal JSON with no trust field → n/a
        from core.ddd_cultivation import CultivationProposal
        d = {
            "id": "proposal_x", "target_doc": "TECH.md", "target_section": "Architecture",
            "content": "x" * 40, "source_run_id": "run_abc", "confidence": 0.7,
            "created_at": "2026-08-10T00:00:00+00:00",
        }
        p = CultivationProposal.from_dict(d)
        assert p.passed_adversarial_gate == "n/a"


class TestStampTrust:
    """stamp_trust_from_run(run_id, project_dir) resolves the run and derives trust."""

    def _fn(self):
        from core.ddd_cultivation import stamp_trust_from_run
        return stamp_trust_from_run

    def _make_run(self, tmp_path, project, run_id, stages):
        run_dir = tmp_path / "Projects" / project / ".artifacts" / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"stages": stages}))
        return tmp_path / "Projects" / project

    def test_gate2_passed_run_yields_passed(self, tmp_path):
        stamp = self._fn()
        proj = self._make_run(tmp_path, "SwarmAI", "run_ok", [
            {"stage": "adversarial", "status": "completed", "gate2_outcome": "pass"}
        ])
        assert stamp("run_ok", proj) == "passed"

    def test_blocked_run_yields_failed(self, tmp_path):
        stamp = self._fn()
        proj = self._make_run(tmp_path, "SwarmAI", "run_bad", [
            {"stage": "adversarial", "status": "completed", "gate2_outcome": "block"}
        ])
        assert stamp("run_bad", proj) == "failed"

    def test_docs_run_no_adversarial_yields_na(self, tmp_path):
        stamp = self._fn()
        proj = self._make_run(tmp_path, "SwarmAI", "run_docs", [
            {"stage": "deliver", "status": "completed"}
        ])
        assert stamp("run_docs", proj) == "n/a"

    def test_unresolvable_run_yields_na_fail_closed(self, tmp_path):
        # non-run source id (a session decision) or a run that isn't on disk → n/a
        stamp = self._fn()
        proj = tmp_path / "Projects" / "SwarmAI"
        proj.mkdir(parents=True)
        assert stamp("code_intel_drift:foo", proj) == "n/a"
        assert stamp("run_missing", proj) == "n/a"
        assert stamp("", proj) == "n/a"
        assert stamp(None, proj) == "n/a"

    def test_filter_lessons_stamps_trust_from_run(self, tmp_path):
        # E2E: a lesson cultivated from a Gate-2-passed run → proposal.passed_adversarial_gate=passed
        from core.ddd_cultivation import filter_lessons_for_ddd
        proj = self._make_run(tmp_path, "SwarmAI", "run_e2e", [
            {"stage": "adversarial", "status": "completed", "gate2_outcome": "pass"}
        ])
        # give the project the DDD docs so classify/append works; project_dir enables stamping
        lessons = ["A silent race drops a bubble when two writes interleave; a recurring "
                   "bug whenever a fallback path is added without a client_id."]
        props = filter_lessons_for_ddd(lessons, "run_e2e", "SwarmAI", project_dir=proj)
        assert props, "expected at least one proposal"
        assert props[0].passed_adversarial_gate == "passed"

    def test_filter_lessons_no_projectdir_stamps_na(self, tmp_path):
        # pure-classify caller (project_dir=None) → cannot resolve run → n/a (fail-closed)
        from core.ddd_cultivation import filter_lessons_for_ddd
        lessons = ["A silent race drops a bubble when two writes interleave; a recurring "
                   "bug whenever a fallback path is added without a client_id."]
        props = filter_lessons_for_ddd(lessons, "run_e2e", "SwarmAI", project_dir=None)
        assert props
        assert props[0].passed_adversarial_gate == "n/a"

    def test_path_traversal_run_id_cannot_forge_trust(self, tmp_path):
        # Gate-2 self-probe HOLE: a crafted run_id that climbs OUT of this project to
        # another project's Gate-2-PASSED run must NOT forge trust. Force the exploit:
        # the traversal target genuinely resolves to a passed run.json.
        stamp = self._fn()
        # victim: a DIFFERENT project with a passed run
        self._make_run(tmp_path, "Other", "run_win", [
            {"stage": "adversarial", "status": "completed", "gate2_outcome": "pass"}
        ])
        attacker = tmp_path / "Projects" / "SwarmAI"
        (attacker / ".artifacts" / "runs").mkdir(parents=True)
        for attack_id in (
            "run_x/../../../../Other/.artifacts/runs/run_win",
            "run_../../Other/.artifacts/runs/run_win",
            "run_win/../../../Other/.artifacts/runs/run_win",
        ):
            assert stamp(attack_id, attacker) == "n/a", f"traversal forged trust via {attack_id!r}"

    def test_prose_only_verdict_run_yields_na(self, tmp_path):
        # a run with only a free-text gate2_verdict (no canonical enum) → n/a
        stamp = self._fn()
        proj = self._make_run(tmp_path, "SwarmAI", "run_prose", [
            {"stage": "adversarial", "status": "completed",
             "gate2_verdict": "2 CRIT fixed + 3 adjudicated"}
        ])
        assert stamp("run_prose", proj) == "n/a"
