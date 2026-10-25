"""Fail-closed evaluation of retained clean-room App product-proof evidence.

Key public symbols:
- ``ProductProofReport`` — evidence emitted by the external clean-room harness.
- ``evaluate_product_proof`` — computes catalog-only/development/product-proven.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CleanEnvironment(_StrictModel):
    """One independently identified clean builder or runtime."""

    environment_id: str = Field(min_length=1)
    empty_home: bool
    empty_app_data: bool
    source_checkout_unavailable: bool
    global_skills_absent: bool
    ambient_credentials_absent: bool


class ArtifactEvidence(_StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def relative_artifact_path(cls, value: str) -> str:
        path = value.replace("\\", "/")
        if path.startswith("/") or ".." in path.split("/") or not path:
            raise ValueError("artifact path must be relative and contained")
        return path


class ProductProofReport(_StrictModel):
    """Retained proof facts; no field is inferred from a developer machine."""

    schema_version: Literal[1]
    app_id: str
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    host_profile: Literal["desktop-daemon", "desktop-subprocess", "hive"]
    host_contract_version: str
    host_build_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    activation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: list[ArtifactEvidence] = Field(min_length=1)
    tracked_source_only: bool
    generated_inputs_recreated: bool
    untracked_inputs_absent: bool
    builders: list[CleanEnvironment]
    runtimes: list[CleanEnvironment]
    registry_identity_verified: bool
    execution_attestation_verified: bool
    install_verified: bool
    enable_disable_reenable_verified: bool
    retained_data_verified: bool
    public_api_ui_verified: bool
    positive_fixtures_passed: bool
    negative_fixtures_passed: bool
    ablation_fixtures_passed: bool
    artifact_digests_reproduced: bool
    package_logs_records_scanned: bool
    absolute_path_findings: list[str]
    undeclared_input_findings: list[str]
    secret_findings: list[str]
    omissions: list[str]
    failures: list[str]
    proof_principal: str
    proof_principal_least_privilege: bool


class ProductProofResult(_StrictModel):
    evidence_state: Literal["catalog-only", "development", "product-proven"]
    blockers: tuple[str, ...]


def _environment_blockers(kind: str, rows: list[CleanEnvironment]) -> list[str]:
    blockers: list[str] = []
    if len({row.environment_id for row in rows}) < 2:
        blockers.append(f"two-clean-{kind}-required")
    checks = (
        ("empty_home", "nonempty-home"),
        ("empty_app_data", "preexisting-app-data"),
        ("source_checkout_unavailable", "source-checkout-present"),
        ("global_skills_absent", "global-skills-present"),
        ("ambient_credentials_absent", "ambient-credentials-present"),
    )
    for field, label in checks:
        if any(not getattr(row, field) for row in rows):
            blockers.append(label)
    return blockers


def evaluate_product_proof(report: ProductProofReport | None) -> ProductProofResult:
    """Compute the evidence label. Missing or incomplete proof never warns green."""

    if report is None:
        return ProductProofResult(
            evidence_state="catalog-only", blockers=("proof-not-run",)
        )

    blockers = [
        *_environment_blockers("builders", report.builders),
        *_environment_blockers("runtimes", report.runtimes),
    ]
    boolean_checks = (
        (report.tracked_source_only, "untracked-source-inputs-used"),
        (report.generated_inputs_recreated, "generated-inputs-not-recreated"),
        (report.untracked_inputs_absent, "untracked-inputs-present"),
        (report.registry_identity_verified, "registry-identity-not-verified"),
        (report.execution_attestation_verified, "execution-attestation-not-verified"),
        (report.install_verified, "install-not-verified"),
        (report.enable_disable_reenable_verified, "lifecycle-not-verified"),
        (report.retained_data_verified, "retained-data-not-verified"),
        (report.public_api_ui_verified, "public-api-ui-not-verified"),
        (report.positive_fixtures_passed, "positive-fixtures-failed"),
        (report.negative_fixtures_passed, "negative-fixtures-failed"),
        (report.ablation_fixtures_passed, "ablation-fixtures-failed"),
        (report.artifact_digests_reproduced, "artifact-digests-not-reproduced"),
        (report.package_logs_records_scanned, "package-log-record-scan-not-run"),
        (report.proof_principal_least_privilege, "dedicated-proof-principal-required"),
    )
    blockers.extend(label for passed, label in boolean_checks if not passed)
    if report.absolute_path_findings:
        blockers.append("absolute-developer-paths-found")
    if report.undeclared_input_findings:
        blockers.append("undeclared-inputs-found")
    if report.secret_findings:
        blockers.append("secret-values-found")
    if report.failures:
        blockers.append("proof-failures-recorded")
    if not report.proof_principal.startswith("vault-ref:"):
        if "dedicated-proof-principal-required" not in blockers:
            blockers.append("dedicated-proof-principal-required")

    unique = tuple(dict.fromkeys(blockers))
    return ProductProofResult(
        evidence_state="development" if unique else "product-proven",
        blockers=unique,
    )
