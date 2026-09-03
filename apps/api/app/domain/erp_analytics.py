"""Provider-neutral contracts for read-only ERP and WMS descriptive analytics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.context import ConnectorKind, ERPMetricKind


class _AnalyticsContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ERPAnalyticsBucket(str, Enum):
    day = "day"
    week = "week"
    month = "month"


class ERPTrendDirection(str, Enum):
    increasing = "increasing"
    decreasing = "decreasing"
    stable = "stable"
    insufficient_data = "insufficient_data"


class ERPAnalyticsRequest(_AnalyticsContract):
    connector_ids: list[str] = Field(min_length=1, max_length=8)
    metric_ids: list[str] = Field(default_factory=list, max_length=80)
    period_start: datetime = Field(
        default_factory=lambda: datetime.now(UTC) - timedelta(days=90)
    )
    period_end: datetime = Field(default_factory=lambda: datetime.now(UTC))
    bucket: ERPAnalyticsBucket = ERPAnalyticsBucket.week
    stable_threshold_percent: float = Field(default=1.0, ge=0.0, le=25.0)
    discrepancy_threshold_percent: float = Field(default=5.0, ge=0.1, le=100.0)

    @model_validator(mode="after")
    def window_and_identifiers_are_bounded(self) -> ERPAnalyticsRequest:
        if self.period_start.tzinfo is None or self.period_end.tzinfo is None:
            raise ValueError("analytics periods must include an explicit timezone")
        if self.period_end <= self.period_start:
            raise ValueError("analytics period end must be after its start")
        if self.period_end - self.period_start > timedelta(days=731):
            raise ValueError("analytics periods cannot exceed 731 days")
        if len(set(self.connector_ids)) != len(self.connector_ids):
            raise ValueError("analytics connector IDs must be unique")
        if len(set(self.metric_ids)) != len(self.metric_ids):
            raise ValueError("analytics metric IDs must be unique")
        if any(
            not value
            or len(value) > 128
            or not all(character.isalnum() or character in "_.-" for character in value)
            for value in self.metric_ids
        ):
            raise ValueError("analytics metric IDs are invalid")
        return self


class ERPTrendPoint(_AnalyticsContract):
    period_start: datetime
    period_end: datetime
    value: float
    sample_count: int = Field(ge=1)


class ERPTrendSummary(_AnalyticsContract):
    direction: ERPTrendDirection
    first_value: float | None = None
    last_value: float | None = None
    absolute_change: float | None = None
    percent_change: float | None = None
    point_count: int = Field(ge=0)
    sample_count: int = Field(ge=0)


class ERPTrendSeries(_AnalyticsContract):
    series_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    source_kind: ConnectorKind
    source_label: str = Field(min_length=1, max_length=160)
    source_reference: str = Field(min_length=1, max_length=256)
    metric_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    metric_kind: ERPMetricKind
    entity_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    unit: str = Field(min_length=1, max_length=32)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    dimensions: dict[str, str] = Field(default_factory=dict, max_length=4)
    bucket: ERPAnalyticsBucket
    points: list[ERPTrendPoint] = Field(default_factory=list, max_length=731)
    summary: ERPTrendSummary
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    persisted_remote_records: Literal[0] = 0


class ERPMetricDiscrepancy(_AnalyticsContract):
    discrepancy_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    metric_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    left_connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    right_connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    period_start: datetime
    unit: str = Field(min_length=1, max_length=32)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    dimensions: dict[str, str] = Field(default_factory=dict, max_length=4)
    left_value: float
    right_value: float
    absolute_difference: float
    difference_percent: float = Field(ge=0.0)
    severity: Literal["low", "medium", "high"]
    explanation: str = Field(min_length=1, max_length=500)
    requires_review: Literal[True] = True


class ERPAnalyticsWarning(_AnalyticsContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    metric_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$"
    )
    code: Literal[
        "connector_unavailable",
        "metric_not_configured",
        "invalid_timestamp",
        "out_of_range",
        "invalid_value",
        "invalid_weight",
        "missing_currency",
        "invalid_currency",
        "series_limit_reached",
    ]
    skipped_records: int = Field(default=0, ge=0)


class ERPAnalyticsRun(_AnalyticsContract):
    run_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["current", "partial", "unavailable"]
    period_start: datetime
    period_end: datetime
    bucket: ERPAnalyticsBucket
    series: list[ERPTrendSeries] = Field(default_factory=list, max_length=200)
    discrepancies: list[ERPMetricDiscrepancy] = Field(default_factory=list, max_length=1_000)
    warnings: list[ERPAnalyticsWarning] = Field(default_factory=list, max_length=1_000)
    unavailable_connector_ids: list[str] = Field(default_factory=list, max_length=8)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    persisted_remote_records: Literal[0] = 0
