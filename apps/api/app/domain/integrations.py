"""Provider-neutral v9 assurance, routing, and governed action contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _IntegrationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataQualityRuleKind(str, Enum):
    required_value = "required_value"
    duplicate_key = "duplicate_key"
    code_list = "code_list"
    broken_reference = "broken_reference"
    type_mismatch = "type_mismatch"
    currency_mismatch = "currency_mismatch"
    subsidiary_mismatch = "subsidiary_mismatch"
    document_record_conflict = "document_record_conflict"
    schema_drift = "schema_drift"
    threshold = "threshold"


class DataQualityRule(_IntegrationContract):
    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    pack_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    kind: DataQualityRuleKind
    severity: Literal["low", "medium", "high", "critical"]
    record_type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    fields: list[str] = Field(default_factory=list, max_length=16)
    parameters: dict[str, str | int | float | bool | list[str]] = Field(
        default_factory=dict, max_length=24
    )
    trusted: bool = False

    @field_validator("fields")
    @classmethod
    def fields_are_identifiers(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(values))
        if any(not value.replace("_", "").isalnum() for value in cleaned):
            raise ValueError("data-quality fields must be allow-listed identifiers")
        return cleaned


class DataQualityRulePack(_IntegrationContract):
    pack_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
    issuer: str = Field(min_length=1, max_length=200)
    rules: list[DataQualityRule] = Field(min_length=1, max_length=2_000)
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str = Field(min_length=40, max_length=512)
    trusted: Literal[True] = True
    installed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DataQualityRunRequest(_IntegrationContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    rule_pack_ids: list[str] = Field(default_factory=list, max_length=16)
    changed_only: bool = True


class DataQualityRun(_IntegrationContract):
    run_id: str
    connector_id: str
    status: Literal["running", "completed", "degraded"]
    inspected_records: int = Field(default=0, ge=0)
    finding_count: int = Field(default=0, ge=0)
    detail_code: str | None = Field(default=None, max_length=64)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class DataQualityFinding(_IntegrationContract):
    finding_id: str
    fingerprint: str
    run_id: str
    connector_id: str
    rule_id: str
    severity: Literal["low", "medium", "high", "critical"]
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=1_200)
    source_reference: str = Field(min_length=1, max_length=256)
    status: Literal["open", "confirmed", "dismissed"] = "open"
    revision: int = Field(default=0, ge=0)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DataQualityFindingReview(_IntegrationContract):
    expected_revision: int = Field(ge=0)
    action: Literal["confirm", "dismiss"]


class PartnerClassification(_IntegrationContract):
    classification_id: str
    artifact_id: str
    partner_reference: str = Field(min_length=1, max_length=256)
    partner_label: str = Field(min_length=1, max_length=160)
    confidence: int = Field(ge=0, le=100)
    matched_values: list[str] = Field(default_factory=list, max_length=16)
    ambiguous: bool = False


class DocumentRoutingAction(str, Enum):
    copy = "copy"
    move = "move"


class DocumentRoutingProposal(_IntegrationContract):
    proposal_id: str
    artifact_id: str
    source_reference: str = Field(min_length=1, max_length=256)
    partner_reference: str = Field(min_length=1, max_length=256)
    partner_label: str = Field(min_length=1, max_length=160)
    destination_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    destination_label: str = Field(min_length=1, max_length=256)
    action: DocumentRoutingAction = DocumentRoutingAction.copy
    confidence: int = Field(ge=0, le=100)
    status: Literal["proposed", "completed", "failed", "cancelled"] = "proposed"
    revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DocumentRoutingReview(_IntegrationContract):
    expected_revision: int = Field(ge=0)
    action: Literal["execute", "cancel"]
    routing_action: DocumentRoutingAction = DocumentRoutingAction.copy


class DocumentRoutingDestination(_IntegrationContract):
    destination_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=160)
    partner_references: list[str] = Field(default_factory=list, min_length=1, max_length=50)
    aliases: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("partner_references", "aliases")
    @classmethod
    def routing_values_are_safe(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(" ".join(value.split()) for value in values))
        if any(not value or len(value) > 160 for value in cleaned):
            raise ValueError("routing business references and aliases must be bounded")
        return cleaned

    @model_validator(mode="after")
    def requires_partner_business_keys(self) -> DocumentRoutingDestination:
        if not self.partner_references:
            raise ValueError("routing destinations require approved partner business keys")
        return self


class ExternalActionKind(str, Enum):
    teams_notification = "teams_notification"
    slack_notification = "slack_notification"
    netsuite_review_record = "netsuite_review_record"
    github_issue = "github_issue"
    gitlab_issue = "gitlab_issue"
    jira_work_item = "jira_work_item"
    azure_boards_work_item = "azure_boards_work_item"
    servicenow_change = "servicenow_change"


class DeliveryStatus(str, Enum):
    drafted = "drafted"
    pending_confirmation = "pending_confirmation"
    approved = "approved"
    executing = "executing"
    succeeded = "succeeded"
    failed = "failed"
    delivery_unknown = "delivery_unknown"
    cancelled = "cancelled"


class ExternalActionDraft(_IntegrationContract):
    action_id: str
    fingerprint: str
    kind: ExternalActionKind
    connector_id: str
    destination_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    destination_label: str = Field(min_length=1, max_length=160)
    finding_id: str
    finding_revision: int = Field(ge=0)
    severity: Literal["low", "medium", "high", "critical"]
    source_reference: str = Field(min_length=1, max_length=256)
    rendered_preview: str = Field(min_length=1, max_length=4_000)
    contains_sensitive_content: bool = False
    status: DeliveryStatus = DeliveryStatus.pending_confirmation
    approval_origin: Literal["user", "trusted_rule"] | None = None
    revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExternalActionEvent(_IntegrationContract):
    event_id: str
    action_id: str
    from_status: DeliveryStatus
    to_status: DeliveryStatus
    revision: int = Field(ge=1)
    approval_origin: Literal["user", "trusted_rule"] | None = None
    detail_code: str | None = Field(default=None, max_length=64)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExternalDeliveryResult(_IntegrationContract):
    status: Literal["succeeded", "failed", "delivery_unknown"]
    provider_reference: str | None = Field(default=None, max_length=256)
    detail_code: str | None = Field(default=None, max_length=64)


class ExternalActionCreateRequest(_IntegrationContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    destination_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    finding_id: str


class ExternalActionDecision(_IntegrationContract):
    expected_revision: int = Field(ge=0)


class NotificationPolicy(_IntegrationContract):
    policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    destination_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    trusted_rule_ids: list[str] = Field(default_factory=list, max_length=100)
    minimum_severity: Literal["high", "critical"] = "high"
    enabled: bool = False
    cooldown_minutes: int = Field(default=60, ge=5, le=10_080)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def enabled_policy_has_rules(self) -> NotificationPolicy:
        if self.enabled and not self.trusted_rule_ids:
            raise ValueError("automatic notification policies require trusted rules")
        return self
