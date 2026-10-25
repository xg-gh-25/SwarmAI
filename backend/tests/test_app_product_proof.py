"""Tests for clean-room App product-proof evidence validation.

A local checkout result must never produce `product-proven`; two clean builders and
runtimes, public-surface evidence, ablation, and clean scans are mandatory.
"""
from __future__ import annotations

from copy import deepcopy

from core.app_product_proof import ProductProofReport, evaluate_product_proof


def _report() -> dict:
    clean = {
        "empty_home": True,
        "empty_app_data": True,
        "source_checkout_unavailable": True,
        "global_skills_absent": True,
        "ambient_credentials_absent": True,
    }
    return {
        "schema_version": 1,
        "app_id": "pollinate",
        "source_commit": "a" * 40,
        "host_profile": "desktop-daemon",
        "host_contract_version": "1",
        "host_build_digest": "e" * 64,
        "package_release_digest": "b" * 64,
        "execution_release_digest": "c" * 64,
        "activation_digest": "d" * 64,
        "artifacts": [{"path": "SwarmAI.app", "sha256": "f" * 64}],
        "tracked_source_only": True,
        "generated_inputs_recreated": True,
        "untracked_inputs_absent": True,
        "builders": [{"environment_id": "builder-a", **clean}, {"environment_id": "builder-b", **clean}],
        "runtimes": [{"environment_id": "runtime-a", **clean}, {"environment_id": "runtime-b", **clean}],
        "registry_identity_verified": True,
        "execution_attestation_verified": True,
        "install_verified": True,
        "enable_disable_reenable_verified": True,
        "retained_data_verified": True,
        "public_api_ui_verified": True,
        "positive_fixtures_passed": True,
        "negative_fixtures_passed": True,
        "ablation_fixtures_passed": True,
        "artifact_digests_reproduced": True,
        "package_logs_records_scanned": True,
        "absolute_path_findings": [],
        "undeclared_input_findings": [],
        "secret_findings": [],
        "omissions": [],
        "failures": [],
        "proof_principal": "vault-ref:app-proof/pollinate",
        "proof_principal_least_privilege": True,
    }


def test_complete_clean_room_report_is_product_proven() -> None:
    report = ProductProofReport.model_validate(_report())
    result = evaluate_product_proof(report)
    assert result.evidence_state == "product-proven"
    assert result.blockers == ()


def test_source_checkout_or_single_builder_blocks_product_claim() -> None:
    raw = _report()
    raw["runtimes"][0]["source_checkout_unavailable"] = False
    raw["builders"] = raw["builders"][:1]
    result = evaluate_product_proof(ProductProofReport.model_validate(raw))
    assert result.evidence_state == "development"
    assert "source-checkout-present" in result.blockers
    assert "two-clean-builders-required" in result.blockers


def test_path_secret_or_ambient_identity_blocks_product_claim() -> None:
    raw = deepcopy(_report())
    raw["absolute_path_findings"] = ["/Users/author/project"]
    raw["secret_findings"] = ["token in log"]
    raw["proof_principal"] = "default"
    raw["proof_principal_least_privilege"] = False
    result = evaluate_product_proof(ProductProofReport.model_validate(raw))
    assert result.evidence_state == "development"
    assert "absolute-developer-paths-found" in result.blockers
    assert "secret-values-found" in result.blockers
    assert "dedicated-proof-principal-required" in result.blockers


def test_catalog_only_is_the_only_state_without_a_proof_report() -> None:
    result = evaluate_product_proof(None)
    assert result.evidence_state == "catalog-only"
    assert result.blockers == ("proof-not-run",)


def test_product_proof_cli_exit_contract(tmp_path) -> None:
    import json
    from scripts.check_app_product_proof import main

    path = tmp_path / "proof.json"
    path.write_text(json.dumps(_report()), encoding="utf-8")
    assert main(["check_app_product_proof.py", str(path)]) == 0

    blocked = _report()
    blocked["artifact_digests_reproduced"] = False
    path.write_text(json.dumps(blocked), encoding="utf-8")
    assert main(["check_app_product_proof.py", str(path)]) == 1

    path.write_text("{}", encoding="utf-8")
    assert main(["check_app_product_proof.py", str(path)]) == 64
