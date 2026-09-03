"""Portable digital solution-thread contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.assurance import AssuranceFinding
from app.domain.governance import GovernanceAlert, GovernanceRecord


class _ThreadContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SolutionNodeKind(str, Enum):
    requirement = "requirement"
    document = "document"
    revision = "revision"
    decision = "decision"
    risk = "risk"
    problem = "problem"
    system = "system"
    interface = "interface"
    data_object = "data_object"
    erp_entity = "erp_entity"
    nfr = "nfr"
    test = "test"
    cutover_control = "cutover_control"
    action = "action"
    governance_record = "governance_record"


class SolutionEdgeKind(str, Enum):
    documents = "documents"
    implements = "implements"
    implemented_by = "implemented_by"
    deployed_as = "deployed_as"
    depends_on = "depends_on"
    validated_by = "validated_by"
    conflicts_with = "conflicts_with"
    supersedes = "supersedes"
    affects = "affects"
    owned_by = "owned_by"
    references = "references"


class SolutionThreadNode(_ThreadContract):
    node_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: SolutionNodeKind
    label: str = Field(min_length=1, max_length=300)
    source_reference: str = Field(min_length=1, max_length=512)
    source_revision: str | None = Field(default=None, max_length=128)
    confirmed: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SolutionThreadEdge(_ThreadContract):
    edge_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_node_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_node_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    relation: SolutionEdgeKind
    source_revision: str | None = Field(default=None, max_length=128)
    provenance_fields: list[str] = Field(default_factory=list, max_length=64)
    confirmed: bool = False
    review_status: Literal["suggested", "confirmed", "dismissed"] = "suggested"
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def cannot_link_node_to_itself(self) -> SolutionThreadEdge:
        if self.source_node_id == self.target_node_id:
            raise ValueError("solution-thread self links are not allowed")
        return self


class SolutionThreadEdgeReviewRequest(_ThreadContract):
    expected_revision: int = Field(ge=1)
    action: Literal["confirm", "dismiss"]


class SolutionThreadEdgeProposal(_ThreadContract):
    edge_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_label: str = Field(min_length=1, max_length=300)
    target_label: str = Field(min_length=1, max_length=300)
    source_reference: str = Field(min_length=1, max_length=512)
    relation: SolutionEdgeKind
    revision: int = Field(ge=1)


class TraceabilityRow(_ThreadContract):
    requirement_node_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    design_node_ids: list[str] = Field(default_factory=list, max_length=100)
    test_node_ids: list[str] = Field(default_factory=list, max_length=100)
    finding_ids: list[str] = Field(default_factory=list, max_length=100)


class TraceabilityMatrix(_ThreadContract):
    rows: list[TraceabilityRow] = Field(default_factory=list, max_length=10_000)
    missing_design_count: int = Field(default=0, ge=0)
    missing_test_count: int = Field(default=0, ge=0)


class ThreadImpactPath(_ThreadContract):
    node_ids: list[str] = Field(min_length=1, max_length=6)
    edge_ids: list[str] = Field(default_factory=list, max_length=5)


class SolutionThreadImpact(_ThreadContract):
    root_node_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    depth: int = Field(default=2, ge=1, le=5)
    nodes: list[SolutionThreadNode] = Field(default_factory=list, max_length=2_000)
    edges: list[SolutionThreadEdge] = Field(default_factory=list, max_length=5_000)
    paths: list[ThreadImpactPath] = Field(default_factory=list, max_length=2_000)


class SolutionThreadImpactRequest(_ThreadContract):
    root_node_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    depth: int = Field(default=2, ge=1, le=5)


class SolutionMindMapRequest(_ThreadContract):
    focus: str | None = Field(default=None, max_length=200)
    depth: int = Field(default=2, ge=1, le=3)
    include_suggested: bool = True
    kinds: list[SolutionNodeKind] = Field(default_factory=list, max_length=16)
    max_nodes: int = Field(default=150, ge=10, le=500)


class SolutionMindMapNode(_ThreadContract):
    public_id: str = Field(pattern=r"^m_[a-f0-9]{16}$")
    kind: SolutionNodeKind
    label: str = Field(min_length=1, max_length=300)
    source_reference: str = Field(min_length=1, max_length=512)
    source_revision: str | None = Field(default=None, max_length=128)
    confirmed: bool
    root: bool = False
    degree: int = Field(default=0, ge=0, le=10_000)


class SolutionMindMapEdge(_ThreadContract):
    public_id: str = Field(pattern=r"^e_[a-f0-9]{16}$")
    source_public_id: str = Field(pattern=r"^m_[a-f0-9]{16}$")
    target_public_id: str = Field(pattern=r"^m_[a-f0-9]{16}$")
    relation: SolutionEdgeKind
    status: Literal["confirmed", "suggested"]


class SolutionMindMap(_ThreadContract):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    focus: str | None = Field(default=None, max_length=200)
    root_public_ids: list[str] = Field(default_factory=list, max_length=64)
    nodes: list[SolutionMindMapNode] = Field(default_factory=list, max_length=500)
    edges: list[SolutionMindMapEdge] = Field(default_factory=list, max_length=2_000)
    counts_by_kind: dict[str, int] = Field(default_factory=dict)
    query_hints: list[str] = Field(default_factory=list, max_length=32)
    outline: list[str] = Field(default_factory=list, max_length=500)
    mermaid: str = Field(max_length=100_000)
    suggested_edge_count: int = Field(default=0, ge=0)
    truncated: bool = False


class SolutionLeadDashboard(_ThreadContract):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    new_findings: list[AssuranceFinding] = Field(default_factory=list, max_length=200)
    governance_alerts: list[GovernanceAlert] = Field(default_factory=list, max_length=100)
    governance_records: list[GovernanceRecord] = Field(default_factory=list, max_length=500)
    missing_traceability: TraceabilityMatrix = Field(default_factory=TraceabilityMatrix)
    erp_schema_drift_count: int = Field(default=0, ge=0)
    architecture_conflict_count: int = Field(default=0, ge=0)
    meetings_requiring_review: int = Field(default=0, ge=0)
    assurance_status: Literal["ready", "degraded", "unavailable"] = "ready"
