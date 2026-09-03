"""Review-gated documentation-to-system consistency contracts.

These records are separate from ``MeetingState``. A consistency finding describes
a disagreement between two cited sources; it is never transcript evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.assurance import AssuranceSeverity
from app.domain.context import ConnectorKind
from app.domain.evidence import ArtifactLocator, DocumentKind


class _ConsistencyContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimOrigin(str, Enum):
    deterministic = "deterministic"
    semantic_suggested = "semantic_suggested"
    semantic_confirmed = "semantic_confirmed"


class ClaimStatus(str, Enum):
    proposed = "proposed"
    confirmed = "confirmed"
    dismissed = "dismissed"


class ObservationPersistence(str, Enum):
    metadata_safe = "metadata_safe"
    encrypted_lease = "encrypted_lease"
    live_only = "live_only"


class ConsistencyMismatchKind(str, Enum):
    missing_entity = "missing_entity"
    missing_field = "missing_field"
    undocumented_implementation = "undocumented_implementation"
    value_mismatch = "value_mismatch"
    type_mismatch = "type_mismatch"
    requiredness_mismatch = "requiredness_mismatch"
    key_mismatch = "key_mismatch"
    code_list_mismatch = "code_list_mismatch"
    api_version_drift = "api_version_drift"
    schema_version_drift = "schema_version_drift"
    environment_drift = "environment_drift"
    configuration_drift = "configuration_drift"
    missing_test = "missing_test"
    missing_deployment = "missing_deployment"
    deployment_lag = "deployment_lag"
    ownership_conflict = "ownership_conflict"
    control_conflict = "control_conflict"
    ambiguous_match = "ambiguous_match"


class ConsistencyAttribution(str, Enum):
    neutral = "neutral"
    documentation_drift = "documentation_drift"
    implementation_drift = "implementation_drift"
    configuration_drift = "configuration_drift"
    release_drift = "release_drift"
    approved_exception = "approved_exception"
    ambiguous = "ambiguous"
    access_incomplete = "access_incomplete"


class AuthoritySide(str, Enum):
    neutral = "neutral"
    documentation = "documentation"
    observed_system = "observed_system"


class ConsistencyFindingStatus(str, Enum):
    open = "open"
    confirmed = "confirmed"
    dismissed = "dismissed"
    resolved = "resolved"
    approved_exception = "approved_exception"
    superseded = "superseded"


class ConsistencyRunStatus(str, Enum):
    running = "running"
    completed = "completed"
    degraded = "degraded"
    failed = "failed"


class ConsistencyCitation(_ConsistencyContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    source_kind: ConnectorKind
    source_label: str = Field(min_length=1, max_length=200)
    source_reference: str = Field(min_length=1, max_length=512)
    source_revision: str = Field(min_length=1, max_length=160)
    locator: ArtifactLocator = Field(default_factory=ArtifactLocator)
    environment: str | None = Field(default=None, max_length=120)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    freshness: Literal["current", "stale", "unknown"] = "unknown"


class DocumentClaim(_ConsistencyContract):
    claim_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    canonical_subject: str = Field(min_length=1, max_length=300)
    canonical_property: str = Field(min_length=1, max_length=160)
    expected_value: str | None = Field(default=None, max_length=1_000)
    expected_value_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    value_type: str | None = Field(default=None, max_length=120)
    environment: str | None = Field(default=None, max_length=120)
    version: str | None = Field(default=None, max_length=120)
    language: Literal["ja", "en", "ko"] = "en"
    document_kind: DocumentKind
    origin: ClaimOrigin = ClaimOrigin.deterministic
    status: ClaimStatus = ClaimStatus.confirmed
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    impact_flags: list[
        Literal["production", "security", "regulatory", "customer", "financial_close"]
    ] = Field(default_factory=list, max_length=5)
    citation: ConsistencyCitation
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def semantic_claims_require_review(self) -> DocumentClaim:
        if (
            self.origin is ClaimOrigin.semantic_suggested
            and self.status is not ClaimStatus.proposed
        ):
            raise ValueError("semantic suggestions must remain proposed until reviewed")
        return self


class ObservedSystemFact(_ConsistencyContract):
    fact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    canonical_subject: str = Field(min_length=1, max_length=300)
    canonical_property: str = Field(min_length=1, max_length=160)
    observed_value: str | None = Field(default=None, max_length=1_000)
    observed_value_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    value_type: str | None = Field(default=None, max_length=120)
    environment: str | None = Field(default=None, max_length=120)
    version: str | None = Field(default=None, max_length=120)
    persistence: ObservationPersistence = ObservationPersistence.metadata_safe
    scope_complete: bool = False
    citation: ConsistencyCitation


class AuthorityPolicy(_ConsistencyContract):
    policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    label: str = Field(min_length=1, max_length=200)
    subject_pattern: str = Field(min_length=1, max_length=300)
    property_pattern: str = Field(min_length=1, max_length=160)
    environment: str | None = Field(default=None, max_length=120)
    authority: AuthoritySide = AuthoritySide.neutral
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True
    revision: int = Field(default=1, ge=1)
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def patterns_are_bounded(self) -> AuthorityPolicy:
        for value in (self.subject_pattern, self.property_pattern):
            if value.count("*") > 4 or "?" in value or "[" in value:
                raise ValueError("authority policies support at most four '*' wildcards")
        return self


class ApprovedException(_ConsistencyContract):
    exception_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    finding_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=2_000)
    owner_role: str | None = Field(default=None, max_length=160)
    expires_at: datetime
    approved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def expiry_is_after_approval(self) -> ApprovedException:
        if self.expires_at <= self.approved_at:
            raise ValueError("approved exceptions require a future expiry")
        return self


class ConsistencyComparison(_ConsistencyContract):
    expected_value: str | None = Field(default=None, max_length=1_000)
    expected_value_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_value: str | None = Field(default=None, max_length=1_000)
    observed_value_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    comparison_rule: str = Field(min_length=1, max_length=160)
    authority_policy_id: str | None = Field(default=None, max_length=128)
    attribution: ConsistencyAttribution = ConsistencyAttribution.neutral
    explanation: str = Field(min_length=1, max_length=2_000)
    likely_impact: str = Field(min_length=1, max_length=1_000)
    recommended_verification: str = Field(min_length=1, max_length=1_000)


class ConsistencyFinding(_ConsistencyContract):
    finding_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    run_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    mismatch_kind: ConsistencyMismatchKind
    severity: AssuranceSeverity
    status: ConsistencyFindingStatus = ConsistencyFindingStatus.open
    subject: str = Field(min_length=1, max_length=300)
    property_name: str = Field(min_length=1, max_length=160)
    document_claim: DocumentClaim
    observed_fact: ObservedSystemFact
    comparison: ConsistencyComparison
    affected_solution_node_ids: list[str] = Field(default_factory=list, max_length=100)
    owner_role: str | None = Field(default=None, max_length=160)
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConsistencyRun(_ConsistencyContract):
    run_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    document_connector_ids: list[str] = Field(min_length=1, max_length=32)
    observed_connector_ids: list[str] = Field(min_length=1, max_length=32)
    status: ConsistencyRunStatus
    language: Literal["ja", "en", "ko"] = "en"
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    claim_count: int = Field(default=0, ge=0)
    observation_count: int = Field(default=0, ge=0)
    finding_count: int = Field(default=0, ge=0)
    ambiguous_count: int = Field(default=0, ge=0)
    source_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reused_run_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    unavailable_connector_ids: list[str] = Field(default_factory=list, max_length=64)
    detail_code: str | None = Field(default=None, max_length=80)


class ConsistencyRunRequest(_ConsistencyContract):
    document_connector_ids: list[str] = Field(min_length=1, max_length=32)
    observed_connector_ids: list[str] = Field(min_length=1, max_length=32)
    artifact_ids: list[str] = Field(default_factory=list, max_length=200)
    language: Literal["ja", "en", "ko"] = "en"
    environment: str | None = Field(default=None, max_length=120)
    changed_only: bool = True
    detect_undocumented: bool = True

    @model_validator(mode="after")
    def connector_roles_are_separate(self) -> ConsistencyRunRequest:
        if set(self.document_connector_ids) & set(self.observed_connector_ids):
            raise ValueError("documentation and observed connector roles must be separate")
        return self


class ConsistencyFindingReviewRequest(_ConsistencyContract):
    expected_revision: int = Field(ge=1)
    action: Literal[
        "confirm",
        "dismiss",
        "resolve",
        "reopen",
        "classify",
        "assign",
        "request_refresh",
        "mark_exception",
    ]
    attribution: ConsistencyAttribution | None = None
    owner_role: str | None = Field(default=None, max_length=160)
    note: str | None = Field(default=None, max_length=2_000)
    exception_reason: str | None = Field(default=None, max_length=2_000)
    exception_expires_at: datetime | None = None

    @model_validator(mode="after")
    def action_fields_are_consistent(self) -> ConsistencyFindingReviewRequest:
        if self.action == "classify" and self.attribution is None:
            raise ValueError("classification requires an attribution")
        if self.action == "mark_exception" and (
            not self.exception_reason or self.exception_expires_at is None
        ):
            raise ValueError("approved exceptions require a reason and expiry")
        if self.action == "assign" and not self.owner_role:
            raise ValueError("role assignment requires an owner role")
        return self


class ConsistencyClaimReviewRequest(_ConsistencyContract):
    expected_revision: int = Field(ge=1)
    action: Literal["confirm", "dismiss"]


class ConsistencyReviewEvent(_ConsistencyContract):
    event_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    finding_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_revision: int = Field(ge=1)
    resulting_revision: int = Field(ge=2)
    action: Literal[
        "confirm",
        "dismiss",
        "resolve",
        "reopen",
        "classify",
        "assign",
        "request_refresh",
        "mark_exception",
    ]
    note: str | None = Field(default=None, max_length=2_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConsistencyFindingFilter(_ConsistencyContract):
    system: str | None = Field(default=None, max_length=160)
    project: str | None = Field(default=None, max_length=160)
    repository: str | None = Field(default=None, max_length=200)
    document: str | None = Field(default=None, max_length=300)
    environment: str | None = Field(default=None, max_length=120)
    document_kind: str | None = Field(default=None, max_length=80)
    mismatch_kind: ConsistencyMismatchKind | None = None
    severity: AssuranceSeverity | None = None
    status: ConsistencyFindingStatus | None = None
    owner_role: str | None = Field(default=None, max_length=160)
    limit: int = Field(default=200, ge=1, le=1_000)


class ConsistencyDashboard(_ConsistencyContract):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    open_findings: int = Field(default=0, ge=0)
    confirmed_findings: int = Field(default=0, ge=0)
    approved_exceptions: int = Field(default=0, ge=0)
    ambiguous_findings: int = Field(default=0, ge=0)
    by_mismatch_kind: dict[str, int] = Field(default_factory=dict, max_length=32)
    by_severity: dict[str, int] = Field(default_factory=dict, max_length=8)
    by_attribution: dict[str, int] = Field(default_factory=dict, max_length=16)
    proposed_semantic_claims: list[DocumentClaim] = Field(default_factory=list, max_length=100)
    recent_findings: list[ConsistencyFinding] = Field(default_factory=list, max_length=200)


class ConsistencyExportRequest(_ConsistencyContract):
    format: Literal["markdown", "canonical_json", "jira_csv", "azure_boards_csv"]
    draft_kind: Literal["issue", "risk", "action", "adr", "mapping_change"] = "issue"


class ConsistencyExport(_ConsistencyContract):
    filename: str = Field(min_length=1, max_length=240)
    media_type: str = Field(min_length=1, max_length=120)
    payload_base64: str = Field(min_length=1, max_length=8_000_000)
    direct_submission_enabled: Literal[False] = False
