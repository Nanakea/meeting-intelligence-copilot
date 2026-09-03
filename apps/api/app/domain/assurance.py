"""Document assurance contracts kept outside authoritative meeting state."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.evidence import ArtifactLocator, DocumentKind


class _AssuranceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssuranceRuleType(str, Enum):
    required_section = "required_section"
    forbidden_placeholder = "forbidden_placeholder"
    measurable_target = "measurable_target"
    required_owner = "required_owner"
    acceptance_criteria = "acceptance_criteria"
    rollback_required = "rollback_required"
    required_reference = "required_reference"
    terminology_consistency = "terminology_consistency"
    traceability = "traceability"
    erp_metadata = "erp_metadata"
    architecture_standard = "architecture_standard"
    vague_language = "vague_language"
    broken_reference = "broken_reference"
    conflicting_statement = "conflicting_statement"


class AssuranceSeverity(str, Enum):
    info = "info"
    warning = "warning"
    high = "high"
    critical = "critical"


class AssuranceRule(_AssuranceContract):
    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    rule_type: AssuranceRuleType
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=1_000)
    applies_to: list[DocumentKind] = Field(min_length=1, max_length=16)
    severity: AssuranceSeverity = AssuranceSeverity.warning
    terms: list[str] = Field(default_factory=list, max_length=64)
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=32)
    enabled: bool = True

    @field_validator("terms")
    @classmethod
    def terms_are_bounded(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(" ".join(value.split()) for value in values if value.strip()))
        if any(len(value) > 200 for value in cleaned):
            raise ValueError("assurance terms are too long")
        return cleaned


class AssuranceRulePack(_AssuranceContract):
    pack_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
    issuer: str = Field(min_length=1, max_length=200)
    rules: list[AssuranceRule] = Field(min_length=1, max_length=2_000)
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str | None = Field(default=None, max_length=512)
    trusted: bool = False
    installed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TrustedRuleSigningKey(_AssuranceContract):
    key_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    label: str = Field(min_length=1, max_length=200)
    public_key_base64: str = Field(min_length=40, max_length=128)
    fingerprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    trusted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RulePackInstallRequest(_AssuranceContract):
    package_base64: str = Field(min_length=1, max_length=4_000_000)


class AssuranceRunStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    degraded = "degraded"
    failed = "failed"


class AssuranceRun(_AssuranceContract):
    run_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    connector_ids: list[str] = Field(min_length=1, max_length=32)
    rule_pack_ids: list[str] = Field(default_factory=list, max_length=32)
    status: AssuranceRunStatus
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    artifact_count: int = Field(default=0, ge=0)
    changed_revision_count: int = Field(default=0, ge=0)
    finding_count: int = Field(default=0, ge=0)
    detail_code: str | None = Field(default=None, max_length=80)


class AssuranceFindingStatus(str, Enum):
    open = "open"
    confirmed = "confirmed"
    dismissed = "dismissed"
    resolved = "resolved"
    superseded = "superseded"


class AssuranceFinding(_AssuranceContract):
    finding_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    run_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    rule_id: str
    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_reference: str = Field(min_length=1, max_length=512)
    document_kind: DocumentKind
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=2_000)
    severity: AssuranceSeverity
    locator: ArtifactLocator = Field(default_factory=ArtifactLocator)
    status: AssuranceFindingStatus = AssuranceFindingStatus.open
    revision: int = Field(default=1, ge=1)
    suggested_governance_kind: Literal[
        "risk", "action", "dependency", "constraint", "architecture_impact"
    ] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FindingReview(_AssuranceContract):
    review_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    finding_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_revision: int = Field(ge=1)
    action: Literal["confirm", "dismiss", "resolve", "reopen", "propose_governance"]
    note: str | None = Field(default=None, max_length=2_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AssuranceSchedule(_AssuranceContract):
    schedule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    connector_ids: list[str] = Field(min_length=1, max_length=32)
    rule_pack_ids: list[str] = Field(default_factory=list, max_length=32)
    cadence: Literal["daily", "weekdays", "weekly"]
    local_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    enabled: bool = True
    run_if_missed: bool = True


class AssuranceRunRequest(_AssuranceContract):
    connector_ids: list[str] = Field(min_length=1, max_length=32)
    rule_pack_ids: list[str] = Field(default_factory=list, max_length=32)
    changed_only: bool = True


class FindingReviewRequest(_AssuranceContract):
    expected_revision: int = Field(ge=1)
    action: Literal["confirm", "dismiss", "resolve", "reopen", "propose_governance"]
    note: str | None = Field(default=None, max_length=2_000)


class ERPFieldMetadata(_AssuranceContract):
    name: str = Field(pattern=r"^[A-Za-z0-9_]{1,128}$")
    data_type: str = Field(min_length=1, max_length=120)
    required: bool = False
    key: bool = False
    code_values: list[str] = Field(default_factory=list, max_length=500)


class ERPEntityMetadata(_AssuranceContract):
    name: str = Field(pattern=r"^[A-Za-z0-9_]{1,128}$")
    fields: list[ERPFieldMetadata] = Field(default_factory=list, max_length=2_000)
    relationships: list[str] = Field(default_factory=list, max_length=500)


class ERPMetadataSnapshot(_AssuranceContract):
    snapshot_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    source_reference: str = Field(min_length=1, max_length=512)
    api_version: str | None = Field(default=None, max_length=120)
    environment: str | None = Field(default=None, max_length=120)
    metadata_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    entities: list[ERPEntityMetadata] = Field(default_factory=list, max_length=2_000)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MetadataValidationResult(_AssuranceContract):
    status: Literal["current", "unavailable", "timeout"]
    snapshot_id: str | None = None
    findings: list[AssuranceFinding] = Field(default_factory=list, max_length=2_000)
    persisted_remote_records: int = Field(default=0, ge=0, le=0)


class ERPMetadataCheckRequest(_AssuranceContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    artifact_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def artifact_ids_are_hashes(self) -> ERPMetadataCheckRequest:
        if any(
            len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            for value in self.artifact_ids
        ):
            raise ValueError("artifact ids must be opaque SHA-256 identifiers")
        return self
