"""Provider-neutral contracts for enterprise connector search and synchronization."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.context import ConnectorKind, ContextRelation


class _EnterpriseContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceSelectionKind(str, Enum):
    repository = "repository"
    project = "project"
    space = "space"
    table = "table"
    folder = "folder"
    service = "service"


class AccessLeaseStatus(str, Enum):
    active = "active"
    expired = "expired"
    revoked = "revoked"


class ConnectorSyncPhase(str, Enum):
    idle = "idle"
    synchronizing = "synchronizing"
    reconciling = "reconciling"
    ready = "ready"
    degraded = "degraded"
    revoked = "revoked"


class SourceSelection(_EnterpriseContract):
    """UI-safe source scope; provider identifiers remain encrypted."""

    selection_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    kind: SourceSelectionKind
    label: str = Field(min_length=1, max_length=160)
    enabled: bool = True

    @field_validator("label")
    @classmethod
    def label_is_safe(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or any(character in value for character in "\r\n\0"):
            raise ValueError("source labels must be bounded single-line text")
        return cleaned


class AccessLease(_EnterpriseContract):
    lease_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    principal_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    acl_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    selection_ids: list[str] = Field(default_factory=list, max_length=200)
    issued_at: datetime
    expires_at: datetime
    status: AccessLeaseStatus = AccessLeaseStatus.active

    @model_validator(mode="after")
    def duration_is_bounded(self) -> AccessLease:
        if self.expires_at <= self.issued_at:
            raise ValueError("access leases must expire after issuance")
        if (self.expires_at - self.issued_at).total_seconds() > 15 * 60:
            raise ValueError("access leases cannot exceed fifteen minutes")
        return self

    def is_active(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        return self.status is AccessLeaseStatus.active and current < self.expires_at


class SyncCursor(_EnterpriseContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    source_selection_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    revision: int = Field(default=0, ge=0)
    cursor_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    last_success_at: datetime | None = None
    phase: ConnectorSyncPhase = ConnectorSyncPhase.idle
    detail_code: str | None = Field(default=None, max_length=64)


class SourceChangeEvent(_EnterpriseContract):
    event_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    source_selection_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    operation: Literal["upsert", "delete", "reconcile"]
    resource_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_revision: str | None = Field(default=None, max_length=160)
    occurred_at: datetime


class ConnectorCapabilityManifest(_EnterpriseContract):
    connector_kind: ConnectorKind
    manifest_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    read_operations: list[Literal["GET", "HEAD"]] = Field(default_factory=lambda: ["GET"])
    source_types: list[SourceSelectionKind] = Field(min_length=1, max_length=12)
    supports_webhooks: bool = False
    supports_incremental_sync: bool = True
    content_persistence: Literal["encrypted_lease", "metadata_only", "live_only"]
    maximum_response_bytes: int = Field(default=2_000_000, ge=1_024, le=10_000_000)
    signed_manifest_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ConnectorCatalogEntry(_EnterpriseContract):
    kind: ConnectorKind
    label: str = Field(min_length=1, max_length=100)
    availability: Literal["built_in", "gateway", "signed_extension"]
    authentication: Literal[
        "none",
        "oauth_pkce",
        "github_app",
        "oidc",
        "certificate",
        "integrated",
        "secret",
    ]
    persistence: Literal["encrypted_lease", "metadata_only", "live_only"]
    supports_webhooks: bool
    read_only: Literal[True] = True


class RepositoryArtifact(_EnterpriseContract):
    source_reference: str = Field(min_length=1, max_length=300)
    repository_label: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=160)
    path: str = Field(min_length=1, max_length=1_024)
    symbol: str | None = Field(default=None, max_length=256)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    language: str | None = Field(default=None, max_length=40)


class WorkItemArtifact(_EnterpriseContract):
    source_reference: str = Field(min_length=1, max_length=300)
    project_label: str = Field(min_length=1, max_length=200)
    item_type: str = Field(min_length=1, max_length=80)
    status: str | None = Field(default=None, max_length=80)
    updated_at: datetime | None = None


class EnterpriseSearchRequest(_EnterpriseContract):
    text: str = Field(min_length=1, max_length=2_000)
    connector_ids: list[str] = Field(default_factory=list, max_length=64)
    source_kinds: list[ConnectorKind] = Field(default_factory=list, max_length=32)
    source_selection_ids: list[str] = Field(default_factory=list, max_length=200)
    entity_types: list[str] = Field(default_factory=list, max_length=32)
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = Field(default=20, ge=1, le=50)
    deadline_ms: int = Field(default=10_000, ge=250, le=10_000)

    @model_validator(mode="after")
    def date_range_is_ordered(self) -> EnterpriseSearchRequest:
        if any(
            value is not None and value.tzinfo is None
            for value in (self.date_from, self.date_to)
        ):
            raise ValueError("enterprise search dates require a timezone")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("enterprise search date range is invalid")
        return self


class EnterpriseSearchCitation(_EnterpriseContract):
    citation_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    source_kind: ConnectorKind
    source_label: str = Field(min_length=1, max_length=500)
    source_reference: str = Field(min_length=1, max_length=300)
    entity_type: str = Field(min_length=1, max_length=64)
    excerpt: str = Field(max_length=600)
    uri: str | None = Field(default=None, max_length=2_048)
    relation: ContextRelation = ContextRelation.reference
    freshness: Literal["current", "stale", "unknown"] = "unknown"
    retrieved_at: datetime
    source_updated_at: datetime | None = None
    rank: int = Field(ge=1, le=50)
    score_reasons: list[str] = Field(default_factory=list, max_length=8)
    access_expires_at: datetime | None = None
    local_indexed: bool = False
    not_stated_in_meeting: Literal[True] = True


class EnterpriseSearchResult(_EnterpriseContract):
    status: Literal["current", "partial", "timeout", "unavailable"]
    citations: list[EnterpriseSearchCitation] = Field(default_factory=list, max_length=50)
    unavailable_connector_ids: list[str] = Field(default_factory=list, max_length=64)
    expired_lease_connector_ids: list[str] = Field(default_factory=list, max_length=64)
    elapsed_ms: int = Field(default=0, ge=0)


class GatewaySearchRequest(_EnterpriseContract):
    principal_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    canonical_terms: list[str] = Field(min_length=1, max_length=32)
    business_keys: list[str] = Field(default_factory=list, max_length=32)
    filters: dict[str, str] = Field(default_factory=dict, max_length=32)
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("canonical_terms", "business_keys")
    @classmethod
    def query_values_are_bounded(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(" ".join(value.split()) for value in values))
        if any(not value or len(value) > 160 for value in cleaned):
            raise ValueError("gateway queries require bounded canonical values")
        return cleaned
