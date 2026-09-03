"""Provider-neutral solution-governance contracts.

Governance is deliberately separate from ``MeetingState``. Transcript-backed
candidates remain proposals until a user confirms them; external context can be
attached as a reference but can never confirm a record or become meeting evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.context import PublicContextCitation


class _GovernanceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GovernanceKind(str, Enum):
    decision = "decision"
    risk = "risk"
    dependency = "dependency"
    action = "action"
    assumption = "assumption"
    constraint = "constraint"
    architecture_impact = "architecture_impact"
    responsibility = "responsibility"


class GovernanceCandidateStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    dismissed = "dismissed"


class GovernanceRecordStatus(str, Enum):
    open = "open"
    monitoring = "monitoring"
    resolved = "resolved"
    superseded = "superseded"


class FieldProvenance(str, Enum):
    transcript = "transcript"
    user = "user"


class DecisionPayload(_GovernanceContract):
    payload_type: Literal["decision"] = "decision"
    statement: str = Field(min_length=1, max_length=2_000)
    rationale: str | None = Field(default=None, max_length=2_000)
    alternatives: list[str] = Field(default_factory=list, max_length=20)
    approver: str | None = Field(default=None, max_length=200)


class RiskPayload(_GovernanceContract):
    payload_type: Literal["risk"] = "risk"
    statement: str = Field(min_length=1, max_length=2_000)
    cause: str | None = Field(default=None, max_length=1_000)
    impact: str | None = Field(default=None, max_length=1_000)
    likelihood: Literal["low", "medium", "high"] | None = None
    severity: Literal["low", "medium", "high", "critical"] | None = None
    mitigation: str | None = Field(default=None, max_length=2_000)
    owner: str | None = Field(default=None, max_length=200)
    target_date: str | None = Field(default=None, max_length=40)


class DependencyPayload(_GovernanceContract):
    payload_type: Literal["dependency"] = "dependency"
    statement: str = Field(min_length=1, max_length=2_000)
    provider: str | None = Field(default=None, max_length=200)
    consumer: str | None = Field(default=None, max_length=200)
    deliverable: str | None = Field(default=None, max_length=500)
    needed_by: str | None = Field(default=None, max_length=40)
    owner: str | None = Field(default=None, max_length=200)
    fallback: str | None = Field(default=None, max_length=1_000)


class ActionPayload(_GovernanceContract):
    payload_type: Literal["action"] = "action"
    task: str = Field(min_length=1, max_length=2_000)
    owner: str | None = Field(default=None, max_length=200)
    due_date: str | None = Field(default=None, max_length=40)


class AssumptionPayload(_GovernanceContract):
    payload_type: Literal["assumption"] = "assumption"
    statement: str = Field(min_length=1, max_length=2_000)
    validation_owner: str | None = Field(default=None, max_length=200)
    validation_due: str | None = Field(default=None, max_length=40)


class ConstraintPayload(_GovernanceContract):
    payload_type: Literal["constraint"] = "constraint"
    statement: str = Field(min_length=1, max_length=2_000)
    source: str | None = Field(default=None, max_length=500)
    affected_scope: list[str] = Field(default_factory=list, max_length=40)
    exception_path: str | None = Field(default=None, max_length=1_000)


class ArchitectureImpactPayload(_GovernanceContract):
    payload_type: Literal["architecture_impact"] = "architecture_impact"
    change: str = Field(min_length=1, max_length=2_000)
    affected_entities: list[str] = Field(default_factory=list, max_length=40)
    impact_areas: list[str] = Field(default_factory=list, max_length=20)
    decision_required: bool = False


class ResponsibilityPayload(_GovernanceContract):
    payload_type: Literal["responsibility"] = "responsibility"
    subject: str = Field(min_length=1, max_length=1_000)
    responsible: list[str] = Field(default_factory=list, max_length=20)
    accountable: list[str] = Field(default_factory=list, max_length=20)
    consulted: list[str] = Field(default_factory=list, max_length=40)
    informed: list[str] = Field(default_factory=list, max_length=40)


GovernancePayload = Annotated[
    DecisionPayload
    | RiskPayload
    | DependencyPayload
    | ActionPayload
    | AssumptionPayload
    | ConstraintPayload
    | ArchitectureImpactPayload
    | ResponsibilityPayload,
    Field(discriminator="payload_type"),
]


class GovernanceEvidenceRef(_GovernanceContract):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    evidence_event_ids: list[str] = Field(min_length=1, max_length=64)


class GovernanceCandidate(_GovernanceContract):
    candidate_id: str
    workspace_id: str
    session_id: str
    state_version: int = Field(ge=0)
    kind: GovernanceKind
    title: str = Field(min_length=1, max_length=240)
    payload: GovernancePayload
    language: Literal["ja", "en", "ko"]
    fingerprint: str
    evidence_event_ids: list[str] = Field(min_length=1, max_length=64)
    linked_pain_ids: list[str] = Field(default_factory=list, max_length=20)
    linked_entity_ids: list[str] = Field(default_factory=list, max_length=40)
    status: GovernanceCandidateStatus = GovernanceCandidateStatus.pending
    created_seq: int = Field(ge=0)

    @model_validator(mode="after")
    def payload_matches_kind(self) -> GovernanceCandidate:
        if self.payload.payload_type != self.kind.value:
            raise ValueError("governance payload does not match candidate kind")
        return self


class GovernanceRecord(_GovernanceContract):
    record_id: str
    workspace_id: str
    kind: GovernanceKind
    title: str = Field(min_length=1, max_length=240)
    payload: GovernancePayload
    status: GovernanceRecordStatus = GovernanceRecordStatus.open
    revision: int = Field(default=1, ge=1)
    field_provenance: dict[str, FieldProvenance] = Field(default_factory=dict, max_length=64)
    evidence: list[GovernanceEvidenceRef] = Field(default_factory=list, max_length=100)
    source_session_ids: list[str] = Field(default_factory=list, max_length=100)
    linked_pain_ids: list[str] = Field(default_factory=list, max_length=100)
    linked_entity_ids: list[str] = Field(default_factory=list, max_length=100)
    superseded_by: str | None = None
    needs_provenance_review: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def payload_matches_kind(self) -> GovernanceRecord:
        if self.payload.payload_type != self.kind.value:
            raise ValueError("governance payload does not match record kind")
        return self


class GovernanceEvent(_GovernanceContract):
    event_id: str
    record_id: str
    revision: int = Field(ge=1)
    action: Literal[
        "created",
        "updated",
        "status_changed",
        "linked",
        "unlinked",
        "superseded",
        "provenance_removed",
    ]
    changed_fields: list[str] = Field(default_factory=list, max_length=64)
    provenance: FieldProvenance
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GovernanceAlert(_GovernanceContract):
    alert_id: str
    record_id: str
    kind: Literal[
        "ownerless", "overdue", "aging_decision", "critical_dependency", "provenance_review"
    ]
    severity: Literal["info", "warning", "critical"]
    label: str = Field(min_length=1, max_length=240)


class SystemEntityKind(str, Enum):
    system = "system"
    interface = "interface"
    process = "process"
    data_object = "data_object"
    business_capability = "business_capability"
    team = "team"
    region = "region"
    environment = "environment"
    standard = "standard"
    architecture_pattern = "architecture_pattern"


class SystemEntity(_GovernanceContract):
    entity_id: str
    kind: SystemEntityKind
    label: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=32)
    confirmed: bool = True


class SystemRelationship(_GovernanceContract):
    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    relation: Literal[
        "integrates_with",
        "depends_on",
        "owns",
        "source_of_truth",
        "exchanges",
        "supports",
        "governed_by",
    ]
    confirmed: bool = False
    evidence: list[GovernanceEvidenceRef] = Field(default_factory=list, max_length=50)


class SolutionPortfolio(_GovernanceContract):
    records: list[GovernanceRecord] = Field(default_factory=list, max_length=500)
    alerts: list[GovernanceAlert] = Field(default_factory=list, max_length=100)
    counts: dict[str, int] = Field(default_factory=dict)


class SolutionBrief(_GovernanceContract):
    language: Literal["ja", "en", "ko"]
    records: list[GovernanceRecord] = Field(default_factory=list, max_length=100)
    alerts: list[GovernanceAlert] = Field(default_factory=list, max_length=50)
    context_citations: list[PublicContextCitation] = Field(default_factory=list, max_length=20)
    context_status: Literal["not_requested", "current", "unavailable", "timeout"] = "not_requested"


class ImpactPath(_GovernanceContract):
    entity_ids: list[str] = Field(min_length=1, max_length=3)
    relationship_ids: list[str] = Field(default_factory=list, max_length=2)


class ImpactAnalysis(_GovernanceContract):
    root_entity_id: str
    entities: list[SystemEntity] = Field(default_factory=list, max_length=100)
    relationships: list[SystemRelationship] = Field(default_factory=list, max_length=200)
    paths: list[ImpactPath] = Field(default_factory=list, max_length=100)
    context_citations: list[PublicContextCitation] = Field(default_factory=list, max_length=20)
    context_status: Literal["not_requested", "current", "unavailable", "timeout"] = "not_requested"


class GovernanceExportOptions(_GovernanceContract):
    include_transcript_excerpts: bool = False
    include_connected_excerpts: bool = False
    jira_work_type: str = Field(default="Task", min_length=1, max_length=80)
    azure_work_item_type: str = Field(default="Task", min_length=1, max_length=80)
