"""Contracts for transient NetSuite -> WMS -> Amazon FBA reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _ReconciliationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FulfillmentStage(str, Enum):
    netsuite = "netsuite"
    wms = "wms"
    amazon_fba = "amazon_fba"


class FulfillmentGapKind(str, Enum):
    missing_downstream_record = "missing_downstream_record"
    missing_upstream_record = "missing_upstream_record"
    quantity_mismatch = "quantity_mismatch"
    duplicate_handoff_key = "duplicate_handoff_key"


class FulfillmentReconciliationRequest(_ReconciliationContract):
    netsuite_connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    wms_connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    amazon_fba_connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    flow_ids: list[str] = Field(default_factory=list, max_length=20)
    absolute_quantity_tolerance: float = Field(default=0.0, ge=0.0, le=1_000_000)
    relative_quantity_tolerance_percent: float = Field(default=0.0, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def connectors_and_flows_are_unique(self) -> FulfillmentReconciliationRequest:
        connectors = {
            self.netsuite_connector_id,
            self.wms_connector_id,
            self.amazon_fba_connector_id,
        }
        if len(connectors) != 3:
            raise ValueError("reconciliation requires three distinct connectors")
        if len(set(self.flow_ids)) != len(self.flow_ids) or any(
            not value
            or len(value) > 128
            or not all(character.isalnum() or character in "_.-" for character in value)
            for value in self.flow_ids
        ):
            raise ValueError("fulfillment flow IDs are invalid")
        return self


class FulfillmentReconciliationFinding(_ReconciliationContract):
    finding_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: FulfillmentGapKind
    flow_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    transition: Literal["netsuite_to_wms", "wms_to_amazon_fba"]
    severity: Literal["low", "medium", "high"]
    sku_reference: str = Field(min_length=1, max_length=256)
    handoff_reference: str | None = Field(default=None, max_length=256)
    dimensions: list[str] = Field(default_factory=list, max_length=4)
    upstream_source_label: str = Field(min_length=1, max_length=160)
    downstream_source_label: str = Field(min_length=1, max_length=160)
    upstream_quantity: float | None = None
    downstream_quantity: float | None = None
    absolute_difference: float | None = Field(default=None, ge=0.0)
    difference_percent: float | None = Field(default=None, ge=0.0)
    upstream_updated_at: datetime | None = None
    downstream_updated_at: datetime | None = None
    explanation: str = Field(min_length=1, max_length=700)
    recommended_check: str = Field(min_length=1, max_length=500)
    requires_review: Literal[True] = True


class FulfillmentReconciliationWarning(_ReconciliationContract):
    connector_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$"
    )
    flow_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$"
    )
    code: Literal[
        "connector_unavailable",
        "mapping_not_configured",
        "flow_not_shared",
        "invalid_record",
        "finding_limit_reached",
    ]
    count: int = Field(default=1, ge=1)


class FulfillmentReconciliationRun(_ReconciliationContract):
    run_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["current", "partial", "unavailable"]
    findings: list[FulfillmentReconciliationFinding] = Field(
        default_factory=list, max_length=1_000
    )
    warnings: list[FulfillmentReconciliationWarning] = Field(
        default_factory=list, max_length=200
    )
    inspected_records: int = Field(default=0, ge=0)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    persisted_remote_records: Literal[0] = 0
