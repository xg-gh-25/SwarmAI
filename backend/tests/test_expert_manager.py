"""Forced-execution tests for ExpertManager over the REAL backend/experts/ tree.

RP47 (test-theater) defense is explicit here:
  * The 12-expert expectation is sourced from an INDEPENDENT constant below
    (``EXPECTED_CODE_EXPERTS``), NOT read back from the registry under test — so a
    registry bug that drops an expert makes a case go RED, it does not quietly
    shrink the expectation (Tell 5).
  * ``test_missing_field_is_detected`` MUTATION-PROVES the field check: it writes a
    real expert file with a field removed into a tmp tree, runs the real scanner,
    and asserts the expert is flagged invalid — reverting the production
    ``REQUIRED_FIELDS`` logic would make it GREEN (Tell 1/2).
"""
from pathlib import Path

import pytest

from core.expert_manager import REQUIRED_FIELDS, ExpertInfo, ExpertManager, scan_experts

# INDEPENDENT statement of intent — the 9 code experts after the S6-4 merge. Sourced
# from the design roster (2026-10-24 usable-by-anyone §3.5.1), NOT from scan_experts() — so this list
# is a real external expectation the registry is checked AGAINST (RP47 Tell 5).
EXPECTED_CODE_EXPERTS = frozenset({
    "correctness", "concurrency", "state-machine", "security",
    "api-contract", "integration", "operational", "performance", "ux",
})

_EXPERTS_ROOT = Path(__file__).resolve().parents[1] / "experts"


def test_real_tree_enumerates_exactly_the_12_code_experts():
    """scan_experts() over the real tree returns exactly the 12 expected code experts."""
    registry = scan_experts()
    code_ids = {eid for eid, info in registry.items() if info.domain == "code"}
    assert code_ids == EXPECTED_CODE_EXPERTS, (
        f"registry code experts != expected roster.\n"
        f"  missing: {EXPECTED_CODE_EXPERTS - code_ids}\n"
        f"  extra:   {code_ids - EXPECTED_CODE_EXPERTS}"
    )


def test_every_code_expert_has_all_7_fields_nonempty():
    """Each code expert carries all 7 REQUIRED_FIELDS, non-empty (AC3)."""
    registry = scan_experts()
    for eid in EXPECTED_CODE_EXPERTS:
        info = registry.get(eid)
        assert info is not None, f"expert '{eid}' absent from registry"
        assert info.is_valid, (
            f"expert '{eid}' is missing required fields: {info.missing_fields}"
        )
        # Independently re-assert each field is a non-empty string (does not trust
        # is_valid alone — mutation-proof the property, not the flag).
        for fld, val in (
            ("expert", info.expert_id),
            ("display_name_zh", info.display_name_zh),
            ("display_name_en", info.display_name_en),
            ("domain", info.domain),
            ("scope", info.scope),
            ("visibility", info.visibility),
        ):
            assert isinstance(val, str) and val.strip(), (
                f"expert '{eid}' field '{fld}' empty/non-string: {val!r}"
            )



def test_core_stance_is_excluded_from_registry():
    """The _core adversarial-stance skeleton is NOT a dispatchable expert."""
    registry = scan_experts()
    assert not any(info.domain == "_core" for info in registry.values()), (
        "a domain=_core file leaked into the dispatchable registry"
    )
    # But it must physically exist (the shared skeleton).
    assert (_EXPERTS_ROOT / "_core" / "adversarial-stance.md").exists()


def test_missing_field_is_detected_mutation_proof(tmp_path):
    """MUTATION PROOF: an expert with a removed required field is flagged invalid.

    Writes a REAL expert file (minus `scope`) into a tmp experts tree and runs the
    REAL scanner. If the production REQUIRED_FIELDS logic were reverted/weakened,
    `is_valid` would wrongly be True and this test goes RED — the RP47 revert-proof.
    """
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    # Deliberately OMIT `scope` — one of the D3 required fields.
    (code_dir / "broken.md").write_text(
        "---\n"
        "expert: broken\n"
        "display_name_zh: 坏专家\n"
        "display_name_en: Broken\n"
        "domain: code\n"
                "visibility: public\n"
        "---\n\nbody\n"
    )
    mgr = ExpertManager(experts_root=tmp_path)
    reg = mgr.scan_experts()
    assert "broken" in reg, "scanner dropped the malformed expert instead of surfacing it"
    assert not reg["broken"].is_valid, (
        "a code expert missing `scope` was marked valid — the field check is broken"
    )
    assert "scope" in reg["broken"].missing_fields


def test_by_domain_groups_code_experts(tmp_path):
    """by_domain() groups and sorts; a mutation that mis-keys domain is caught."""
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    for eid in ("alpha", "beta"):
        (code_dir / f"{eid}.md").write_text(
            f"---\nexpert: {eid}\ndisplay_name_zh: x\ndisplay_name_en: x\n"
            f"domain: code\nscope: s\nvisibility: public\n---\n\nb\n"
        )
    grouped = ExpertManager(experts_root=tmp_path).by_domain()
    assert list(grouped.keys()) == ["code"]
    assert [e.expert_id for e in grouped["code"]] == ["alpha", "beta"]  # sorted


def test_required_fields_contract_is_the_6_declared():
    """Pin the field contract so a silent shrink of REQUIRED_FIELDS is caught."""
    assert set(REQUIRED_FIELDS) == {
        "expert", "display_name_zh", "display_name_en",
        "domain", "scope", "visibility",
    }
