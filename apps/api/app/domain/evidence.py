"""Provider-neutral evidence retrieval contracts.

Retrieved evidence is supplemental. It never becomes transcript evidence and
cannot mutate ``MeetingState`` or durable governance without explicit review.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.context import ConnectorKind


class _EvidenceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentKind(str, Enum):
    requirement = "requirement"
    architecture = "architecture"
    adr = "adr"
    interface = "interface"
    data_mapping = "data_mapping"
    nfr_security = "nfr_security"
    test = "test"
    cutover = "cutover"
    operations = "operations"
    general = "general"


class ArtifactLocator(_EvidenceContract):
    heading: str | None = Field(default=None, max_length=300)
    page: int | None = Field(default=None, ge=1, le=100_000)
    sheet: str | None = Field(default=None, max_length=120)
    cell_range: str | None = Field(default=None, max_length=80)
    paragraph: int | None = Field(default=None, ge=1, le=1_000_000)
    start_line: int | None = Field(default=None, ge=1, le=10_000_000)
    end_line: int | None = Field(default=None, ge=1, le=10_000_000)


class DocumentArtifact(_EvidenceContract):
    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    source_kind: ConnectorKind
    title: str = Field(min_length=1, max_length=500)
    source_reference: str = Field(min_length=1, max_length=512)
    document_kind: DocumentKind = DocumentKind.general
    current_revision_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    updated_at: datetime | None = None
    indexed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DocumentRevision(_EvidenceContract):
    revision_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    section_count: int = Field(ge=0, le=100_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ArtifactSection(_EvidenceContract):
    section_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    ordinal: int = Field(ge=0, le=100_000)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=20_000)
    locator: ArtifactLocator = Field(default_factory=ArtifactLocator)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    allowed_principals: list[str] = Field(min_length=1, max_length=64)

    @field_validator("allowed_principals")
    @classmethod
    def principals_are_explicit(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not cleaned:
            raise ValueError("artifact sections require an explicit principal")
        return cleaned


class RetrievalRequest(_EvidenceContract):
    text: str = Field(min_length=1, max_length=2_000)
    principal_id: str = Field(min_length=1, max_length=256)
    connector_ids: list[str] = Field(default_factory=list, max_length=32)
    document_kinds: list[DocumentKind] = Field(default_factory=list, max_length=16)
    entity_hints: list[str] = Field(default_factory=list, max_length=64)
    business_keys: list[str] = Field(default_factory=list, max_length=32)
    limit: int = Field(default=8, ge=1, le=20)


class RetrievalScore(_EvidenceContract):
    lexical_rank: int | None = Field(default=None, ge=1)
    semantic_rank: int | None = Field(default=None, ge=1)
    entity_matches: int = Field(default=0, ge=0, le=100)
    business_key_matches: int = Field(default=0, ge=0, le=100)
    title_match: bool = False
    freshness_boost: float = Field(default=0.0, ge=0.0, le=1.0)
    source_authority_boost: float = Field(default=0.0, ge=0.0, le=1.0)
    reciprocal_rank_score: float = Field(ge=0.0)


class EvidenceCitation(_EvidenceContract):
    citation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    connector_id: str = Field(exclude=True)
    source_kind: ConnectorKind
    source_label: str = Field(min_length=1, max_length=100)
    source_reference: str = Field(min_length=1, max_length=512)
    document_kind: DocumentKind
    excerpt: str = Field(max_length=600)
    uri: str | None = Field(default=None, max_length=2_048)
    locator: ArtifactLocator = Field(default_factory=ArtifactLocator)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    relation: Literal["supporting", "conflicting", "reference"] = "reference"
    rank: int = Field(ge=1, le=20)
    score: RetrievalScore


class EvidenceBundle(_EvidenceContract):
    status: Literal["current", "unavailable", "timeout"] = "current"
    citations: list[EvidenceCitation] = Field(default_factory=list, max_length=20)
    unavailable_connector_ids: list[str] = Field(default_factory=list, max_length=32)
    lexical_only: bool = True
    elapsed_ms: int = Field(default=0, ge=0)


class EmbeddingProfile(_EvidenceContract):
    model_id: str = Field(min_length=1, max_length=200)
    model_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dimensions: int = Field(ge=1, le=16_384)
    runtime: Literal["onnx-cpu"] = "onnx-cpu"
