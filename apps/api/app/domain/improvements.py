"""Provider-neutral contracts for supervised local improvement and ERP governance.

These records are deliberately separate from ``MeetingState``. A reviewed signal may
propose a declarative profile change, but it can never become transcript evidence or
grant connector write authority.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.context import ConnectorKind
from app.domain.evidence import DocumentKind


class _ImprovementContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImprovementSignalKind(str, Enum):
    citation_relevance = "citation_relevance"
    ask_now_usefulness = "ask_now_usefulness"
    assurance_finding_review = "assurance_finding_review"
    governance_review = "governance_review"
    draft_edit = "draft_edit"
    document_classification_review = "document_classification_review"
    routing_review = "routing_review"
    netsuite_finding_review = "netsuite_finding_review"


class ImprovementProposalKind(str, Enum):
    retrieval_weights = "retrieval_weights"
    document_classification = "document_classification"
    assurance_rule = "assurance_rule"
    glossary_alias = "glossary_alias"
    routing_mapping = "routing_mapping"
    netsuite_mapping = "netsuite_mapping"


class ImprovementProposalStatus(str, Enum):
    pending_evaluation = "pending_evaluation"
    evaluated = "evaluated"
    approved_shadow = "approved_shadow"
    active = "active"
    rejected = "rejected"
    blocked = "blocked"
    retired = "retired"


class ImprovementProfileStatus(str, Enum):
    shadow = "shadow"
    active = "active"
    previous = "previous"
    needs_revalidation = "needs_revalidation"
    blocked_shadow = "blocked_shadow"
    retired = "retired"


class ImprovementSignal(_ImprovementContract):
    signal_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: ImprovementSignalKind
    action: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    outcome: Literal[-1, 0, 1]
    source_kind: ConnectorKind | None = None
    entity_type: str | None = Field(default=None, max_length=64)
    language: Literal["ja", "en", "ko"] | None = None
    rank: int | None = Field(default=None, ge=1, le=20)
    features: dict[str, float] = Field(default_factory=dict, max_length=32)
    attributes: dict[str, str] = Field(default_factory=dict, max_length=16)
    local_excerpt: str | None = Field(default=None, max_length=600)
    remote_source: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = Field(
        default_factory=lambda: datetime.now(UTC) + timedelta(days=90)
    )

    @field_validator("features")
    @classmethod
    def features_are_finite_and_bounded(cls, values: dict[str, float]) -> dict[str, float]:
        if any(
            not key.replace("_", "").isalnum()
            or len(key) > 64
            or not math.isfinite(value)
            or abs(value) > 1_000_000
            for key, value in values.items()
        ):
            raise ValueError("improvement features are invalid")
        return values

    @field_validator("attributes")
    @classmethod
    def attributes_are_safe(cls, values: dict[str, str]) -> dict[str, str]:
        forbidden = {"token", "transcript", "prompt", "exception", "path", "record_id"}
        if any(
            key in forbidden
            or not key.replace("_", "").isalnum()
            or len(key) > 64
            or not value
            or len(value) > 160
            or any(character in value for character in "\r\n\0")
            for key, value in values.items()
        ):
            raise ValueError("improvement attributes are not privacy-safe")
        return values

    @model_validator(mode="after")
    def remote_content_is_never_retained(self) -> ImprovementSignal:
        if self.remote_source and self.local_excerpt is not None:
            raise ValueError("remote improvement signals cannot retain excerpts")
        return self


class ImprovementMetrics(_ImprovementContract):
    sample_count: int = Field(ge=0)
    train_count: int = Field(default=0, ge=0)
    holdout_count: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(default=0.0, ge=0.0, le=1.0)
    baseline_ndcg_at_5: float = Field(ge=0.0, le=1.0)
    candidate_ndcg_at_5: float = Field(ge=0.0, le=1.0)
    ndcg_improvement: float = Field(ge=-1.0, le=1.0)
    maximum_cohort_regression: float = Field(ge=0.0, le=1.0)
    unauthorized_results: int = Field(ge=0)


class ImprovementCohortMetric(_ImprovementContract):
    cohort: str = Field(pattern=r"^[a-z0-9_.:-]{1,120}$")
    sample_count: int = Field(ge=0)
    baseline_score: float = Field(ge=0.0, le=1.0)
    candidate_score: float = Field(ge=0.0, le=1.0)
    regression: float = Field(ge=0.0, le=1.0)


class ImprovementEvaluation(_ImprovementContract):
    evaluation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposal_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    corpus_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    train_snapshot_hash: str = Field(default="0" * 64, pattern=r"^[a-f0-9]{64}$")
    holdout_snapshot_hash: str = Field(default="0" * 64, pattern=r"^[a-f0-9]{64}$")
    active_profile_hash: str = Field(default="0" * 64, pattern=r"^[a-f0-9]{64}$")
    independent_corpus_hash: str = Field(default="0" * 64, pattern=r"^[a-f0-9]{64}$")
    metrics: ImprovementMetrics
    cohort_metrics: list[ImprovementCohortMetric] = Field(default_factory=list, max_length=128)
    independent_case_count: int = Field(default=0, ge=0)
    independent_pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    blocking_reasons: list[str] = Field(default_factory=list, max_length=32)
    eligible: bool
    detail_code: str = Field(max_length=80)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ImprovementProposal(_ImprovementContract):
    proposal_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: ImprovementProposalKind
    title: str = Field(min_length=1, max_length=240)
    rationale: str = Field(min_length=1, max_length=1_000)
    changes: dict[str, int | float | str | bool | list[str] | dict[str, int | float]] = Field(
        default_factory=dict, max_length=32
    )
    signal_count: int = Field(ge=1)
    signal_snapshot_hash: str = Field(default="0" * 64, pattern=r"^[a-f0-9]{64}$")
    train_snapshot_hash: str = Field(default="0" * 64, pattern=r"^[a-f0-9]{64}$")
    holdout_snapshot_hash: str = Field(default="0" * 64, pattern=r"^[a-f0-9]{64}$")
    independent_corpus_hash: str = Field(default="0" * 64, pattern=r"^[a-f0-9]{64}$")
    base_profile_revision: int = Field(default=0, ge=0)
    status: ImprovementProposalStatus = ImprovementProposalStatus.pending_evaluation
    revision: int = Field(default=1, ge=1)
    evaluation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    rollback_profile_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ImprovementProfile(_ImprovementContract):
    profile_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposal_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: ImprovementProposalKind
    revision: int = Field(ge=1)
    status: ImprovementProfileStatus
    configuration: dict[
        str, int | float | str | bool | list[str] | dict[str, int | float]
    ] = Field(default_factory=dict, max_length=32)
    shadow_cycles_completed: int = Field(default=0, ge=0)
    activated_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ShadowComparison(_ImprovementContract):
    applicable_cases: int = Field(ge=0)
    active_score: float = Field(ge=0.0, le=1.0)
    candidate_score: float = Field(ge=0.0, le=1.0)
    latency_regression_ms: float = Field(ge=0.0)
    privacy_violations: int = Field(ge=0)
    authorization_violations: int = Field(ge=0)
    invariant_violations: int = Field(ge=0)
    passed: bool


class ImprovementShadowRun(_ImprovementContract):
    run_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposal_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    cycle: int = Field(ge=1, le=3)
    corpus_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    comparison: ShadowComparison
    status: Literal["passed", "blocked"]
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ImprovementSettings(_ImprovementContract):
    paused: bool = False
    excerpt_retention_days: Literal[30, 90, 365, -1] = 90
    revision: int = Field(default=0, ge=0)


class ImprovementProposalReview(_ImprovementContract):
    expected_revision: int = Field(ge=1)
    action: Literal["approve", "reject"]


class ImprovementPackManifest(_ImprovementContract):
    schema_version: Literal[2] = 2
    compatibility_api_version: Literal[13] = 13
    pack_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    issuer_public_key: str = Field(min_length=40, max_length=128)
    proposal_count: int = Field(ge=0, le=1_000)
    profile_count: int = Field(ge=0, le=1_000)
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DocumentLifecycleState(str, Enum):
    unclassified = "unclassified"
    proposed = "proposed"
    reviewed = "reviewed"
    approved = "approved"
    superseded = "superseded"
    archived = "archived"


class DocumentClassification(_ImprovementContract):
    classification_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_reference: str = Field(min_length=1, max_length=512)
    document_kind: DocumentKind
    labels: dict[str, list[str]] = Field(default_factory=dict, max_length=16)
    confidence: int = Field(ge=0, le=100)
    reason_codes: list[str] = Field(default_factory=list, max_length=16)
    lifecycle: DocumentLifecycleState = DocumentLifecycleState.proposed
    revision: int = Field(default=1, ge=1)
    reviewed_at: datetime | None = None

    @field_validator("labels")
    @classmethod
    def labels_are_bounded(cls, values: dict[str, list[str]]) -> dict[str, list[str]]:
        allowed = {"partner", "project", "system", "process", "region", "environment", "tag"}
        if any(
            key not in allowed
            or len(items) > 20
            or any(not item or len(item) > 120 for item in items)
            for key, items in values.items()
        ):
            raise ValueError("document labels are invalid")
        return {key: list(dict.fromkeys(items)) for key, items in values.items()}


class DocumentClassificationReview(_ImprovementContract):
    expected_revision: int = Field(ge=1)
    action: Literal["approve", "correct", "archive"]
    document_kind: DocumentKind | None = None
    labels: dict[str, list[str]] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def correction_has_kind(self) -> DocumentClassificationReview:
        if self.action == "correct" and self.document_kind is None:
            raise ValueError("classification corrections require a document kind")
        return self


class DuplicateDocumentGroup(_ImprovementContract):
    group_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_references: list[str] = Field(min_length=2, max_length=100)
    relation: Literal["exact_duplicate", "possible_revision"]
    confidence: int = Field(ge=0, le=100)
    reviewed: bool = False


class DocumentDispositionProposal(_ImprovementContract):
    disposition_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    classification_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_reference: str = Field(min_length=1, max_length=512)
    safe_filename: str = Field(min_length=1, max_length=240)
    collection: str = Field(min_length=1, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=20)
    destination_ref: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$"
    )
    routing_action: Literal["copy", "move"] = "copy"
    status: Literal[
        "proposed", "reviewed", "executing", "completed", "failed", "cancelled"
    ] = "proposed"
    revision: int = Field(default=1, ge=1)


class DocumentDispositionReview(_ImprovementContract):
    expected_revision: int = Field(ge=1)
    action: Literal["approve", "cancel"]
    routing_action: Literal["copy", "move"] = "copy"


class DocumentInbox(_ImprovementContract):
    classifications: list[DocumentClassification] = Field(default_factory=list, max_length=5_000)
    duplicate_groups: list[DuplicateDocumentGroup] = Field(default_factory=list, max_length=1_000)
    dispositions: list[DocumentDispositionProposal] = Field(default_factory=list, max_length=5_000)


class DocumentScanPhase(str, Enum):
    idle = "idle"
    scanning = "scanning"
    ready = "ready"
    degraded = "degraded"
    unavailable = "unavailable"


class DocumentScanStatus(_ImprovementContract):
    phase: DocumentScanPhase = DocumentScanPhase.idle
    scanned_sources: int = Field(default=0, ge=0)
    indexed_revisions: int = Field(default=0, ge=0)
    failed_sources: int = Field(default=0, ge=0)
    extraction_methods: list[Literal["native", "ocr"]] = Field(default_factory=list, max_length=2)
    ocr_status: Literal["disabled", "ready", "degraded", "unavailable"] = "disabled"
    detail_code: str | None = Field(default=None, max_length=80)
    last_success_at: datetime | None = None


class DocumentOperationPlan(_ImprovementContract):
    operation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    disposition_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_reference: str = Field(min_length=1, max_length=512)
    destination_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    action: Literal["copy", "move"] = "copy"
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["ready", "completed", "failed", "rolled_back"] = "ready"
    revision: int = Field(default=1, ge=1)
    completed_at: datetime | None = None


class ERPProcess(str, Enum):
    order_to_cash = "order_to_cash"
    procure_to_pay = "procure_to_pay"
    inventory = "inventory"
    project_to_cash = "project_to_cash"
    financial_close = "financial_close"


class ERPControlTemplate(_ImprovementContract):
    template_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    process: ERPProcess
    title: str = Field(min_length=1, max_length=240)
    required_record_types: list[str] = Field(min_length=1, max_length=20)
    control_codes: list[str] = Field(min_length=1, max_length=32)


class NetSuiteMappingIssue(_ImprovementContract):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    record_type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    field: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    severity: Literal["info", "warning", "error"]


class NetSuiteMappingValidation(_ImprovementContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    status: Literal["valid", "invalid", "unavailable"]
    metadata_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    active_templates: list[str] = Field(default_factory=list, max_length=20)
    issues: list[NetSuiteMappingIssue] = Field(default_factory=list, max_length=1_000)
    persisted_remote_records: Literal[0] = 0
    validated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ERPMetadataBaseline(_ImprovementContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    metadata_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    prior_metadata_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    changed_record_types: list[str] = Field(default_factory=list, max_length=200)
    status: Literal["initial", "unchanged", "changed"]
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ERPGovernanceRecommendation(_ImprovementContract):
    recommendation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["risk", "action", "issue_draft", "mapping_change"]
    title: str = Field(min_length=1, max_length=240)
    source_reference: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=1_000)
    requires_review: Literal[True] = True


class ERPGovernanceRunRequest(_ImprovementContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    processes: list[ERPProcess] = Field(default_factory=list, max_length=5)
    changed_only: bool = True


class ERPGovernanceRun(_ImprovementContract):
    run_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    status: Literal["completed", "degraded"]
    active_templates: list[str] = Field(default_factory=list, max_length=20)
    inactive_templates: list[str] = Field(default_factory=list, max_length=20)
    finding_count: int = Field(default=0, ge=0)
    recommendations: list[ERPGovernanceRecommendation] = Field(
        default_factory=list, max_length=500
    )
    detail_code: str | None = Field(default=None, max_length=80)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ERPGovernanceDashboard(_ImprovementContract):
    runs: list[ERPGovernanceRun] = Field(default_factory=list, max_length=100)
    findings_by_severity: dict[str, int] = Field(default_factory=dict)
    findings_by_status: dict[str, int] = Field(default_factory=dict)
    active_processes: list[ERPProcess] = Field(default_factory=list, max_length=5)
    ownerless_findings: int = Field(default=0, ge=0)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
