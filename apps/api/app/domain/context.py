"""Provider-neutral contracts for read-only external context.

External context is deliberately separate from ``MeetingState``. It can support
or challenge a question, but it cannot become transcript evidence or mutate the
deterministic fact/gap lifecycle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _ContextContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectorKind(str, Enum):
    local_files = "local_files"
    microsoft_graph = "microsoft_graph"
    odata = "odata"
    sap = "sap"
    dynamics365 = "dynamics365"
    netsuite = "netsuite"
    microsoft_teams = "microsoft_teams"
    slack = "slack"
    notion = "notion"
    wms = "wms"
    amazon_fba = "amazon_fba"
    github = "github"
    gitlab = "gitlab"
    jira = "jira"
    confluence = "confluence"
    azure_devops = "azure_devops"
    servicenow = "servicenow"
    sql = "sql"
    sftp = "sftp"
    openapi = "openapi"


class ConnectorPhase(str, Enum):
    disconnected = "disconnected"
    connecting = "connecting"
    ready = "ready"
    degraded = "degraded"
    auth_required = "auth_required"
    unavailable = "unavailable"


class ContextRelation(str, Enum):
    supporting = "supporting"
    conflicting = "conflicting"
    reference = "reference"


class EntitySearchMode(str, Enum):
    server_filter = "server_filter"
    bounded_scan = "bounded_scan"


class ERPMetricKind(str, Enum):
    unit_price = "unit_price"
    extended_amount = "extended_amount"
    quantity_on_hand = "quantity_on_hand"
    inventory_value = "inventory_value"
    order_volume = "order_volume"
    fulfillment_lead_time = "fulfillment_lead_time"
    pick_cycle_time = "pick_cycle_time"
    fill_rate = "fill_rate"
    stockout_rate = "stockout_rate"
    custom = "custom"


class ERPMetricAggregation(str, Enum):
    average = "average"
    weighted_average = "weighted_average"
    sum = "sum"
    minimum = "minimum"
    maximum = "maximum"
    latest = "latest"


class ERPAnalyticsMapping(_ContextContract):
    metric_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    metric_kind: ERPMetricKind
    entity_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    value_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    timestamp_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    aggregation: ERPMetricAggregation
    unit: str = Field(min_length=1, max_length=32)
    currency_field: str | None = Field(
        default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$"
    )
    fixed_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    weight_field: str | None = Field(
        default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$"
    )
    dimension_fields: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("unit")
    @classmethod
    def unit_is_safe(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or any(character in value for character in "\r\n\0"):
            raise ValueError("analytics units must be single-line text")
        return cleaned

    @field_validator("dimension_fields")
    @classmethod
    def dimensions_are_identifiers(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(values))
        if any(
            not value
            or len(value) > 128
            or not value.replace("_", "").isalnum()
            for value in cleaned
        ):
            raise ValueError("analytics dimensions must be allow-listed identifiers")
        return cleaned

    @model_validator(mode="after")
    def aggregation_and_currency_are_consistent(self) -> ERPAnalyticsMapping:
        if self.aggregation is ERPMetricAggregation.weighted_average and not self.weight_field:
            raise ValueError("weighted averages require a weight field")
        if self.aggregation is not ERPMetricAggregation.weighted_average and self.weight_field:
            raise ValueError("weight fields are only valid for weighted averages")
        if self.currency_field and self.fixed_currency:
            raise ValueError("analytics mappings use a field or fixed currency, not both")
        if self.metric_kind in {
            ERPMetricKind.unit_price,
            ERPMetricKind.extended_amount,
            ERPMetricKind.inventory_value,
        } and not (self.currency_field or self.fixed_currency):
            raise ValueError("monetary metrics require an explicit currency")
        return self


class FulfillmentRecordMapping(_ContextContract):
    flow_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    entity_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    sku_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    quantity_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    reference_field: str | None = Field(
        default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$"
    )
    timestamp_field: str | None = Field(
        default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$"
    )
    dimension_fields: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("dimension_fields")
    @classmethod
    def dimensions_are_identifiers(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(values))
        if any(
            not value
            or len(value) > 128
            or not value.replace("_", "").isalnum()
            for value in cleaned
        ):
            raise ValueError("fulfillment dimensions must be allow-listed identifiers")
        return cleaned


class EntityGlossaryKind(str, Enum):
    system = "system"
    business_object = "business_object"
    process = "process"
    interface = "interface"
    business_capability = "business_capability"
    team = "team"
    region = "region"
    environment = "environment"
    standard = "standard"
    architecture_pattern = "architecture_pattern"


class EntityGlossaryEntry(_ContextContract):
    entry_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    kind: EntityGlossaryKind
    canonical_name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=32)
    language: Literal["ja", "en", "ko"] | None = None
    enabled: bool = True

    @field_validator("canonical_name")
    @classmethod
    def canonical_name_is_single_line(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or any(character in value for character in "\r\n\0"):
            raise ValueError("canonical glossary names must be non-empty single-line text")
        return cleaned

    @field_validator("aliases")
    @classmethod
    def aliases_are_bounded_and_unique(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(" ".join(value.split()) for value in values))
        if any(
            not value or len(value) > 120 or any(character in value for character in "\r\n\0")
            for value in cleaned
        ):
            raise ValueError("glossary aliases must be non-empty single-line text")
        return cleaned


class EntityGlossary(_ContextContract):
    revision: int = Field(default=0, ge=0)
    entries: list[EntityGlossaryEntry] = Field(default_factory=list, max_length=2_000)

    @model_validator(mode="after")
    def entry_ids_are_unique(self) -> EntityGlossary:
        if len({entry.entry_id for entry in self.entries}) != len(self.entries):
            raise ValueError("glossary entry ids must be unique")
        return self


class EntityMapping(_ContextContract):
    entity_name: str = Field(pattern=r"^[A-Za-z0-9_]{1,128}$")
    key_field: str = Field(pattern=r"^[A-Za-z0-9_]{1,128}$")
    display_field: str = Field(pattern=r"^[A-Za-z0-9_]{1,128}$")
    searchable_fields: list[str] = Field(min_length=1, max_length=32)
    projected_fields: list[str] = Field(min_length=1, max_length=64)
    search_mode: EntitySearchMode = EntitySearchMode.server_filter

    @field_validator("searchable_fields", "projected_fields")
    @classmethod
    def fields_are_safe_identifiers(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(values))
        if not cleaned or any(
            not value or len(value) > 128 or not value.replace("_", "").isalnum()
            for value in cleaned
        ):
            raise ValueError("entity fields must be allow-listed identifiers")
        return cleaned

    @model_validator(mode="after")
    def key_and_display_are_projected(self) -> EntityMapping:
        required = {self.key_field, self.display_field, *self.searchable_fields}
        if not required.issubset(self.projected_fields):
            raise ValueError("key, display, and searchable fields must be projected")
        return self


class ExternalSpaceKind(str, Enum):
    teams_channel = "teams_channel"
    teams_meeting_chat = "teams_meeting_chat"
    slack_public_channel = "slack_public_channel"
    slack_private_channel = "slack_private_channel"


class ExternalSpaceSelection(_ContextContract):
    """UI-safe selected collaboration space; provider IDs remain encrypted."""

    selection_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    kind: ExternalSpaceKind
    enabled: bool = True

    @field_validator("label")
    @classmethod
    def label_is_safe(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or any(character in value for character in "\r\n\0"):
            raise ValueError("space labels must be single-line text")
        return cleaned


class NetSuiteEntityMapping(_ContextContract):
    record_type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    key_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    display_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    searchable_fields: list[str] = Field(min_length=1, max_length=24)
    projected_fields: list[str] = Field(min_length=1, max_length=48)
    modified_field: str | None = Field(
        default="lastmodifieddate", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$"
    )

    @field_validator("searchable_fields", "projected_fields")
    @classmethod
    def fields_are_identifiers(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(values))
        if any(
            not value
            or len(value) > 128
            or not value.replace("_", "").isalnum()
            for value in cleaned
        ):
            raise ValueError("NetSuite fields must be allow-listed identifiers")
        return cleaned

    @model_validator(mode="after")
    def required_fields_are_projected(self) -> NetSuiteEntityMapping:
        required = {self.key_field, self.display_field, *self.searchable_fields}
        if self.modified_field:
            required.add(self.modified_field)
        if not required.issubset(self.projected_fields):
            raise ValueError("NetSuite key, display, search, and modified fields must be projected")
        return self


class NetSuiteReviewTargetMapping(_ContextContract):
    record_type: str = Field(pattern=r"^customrecord_[a-z0-9_]{1,110}$")
    external_id_field: str = Field(pattern=r"^custrecord_[a-z0-9_]{1,110}$")
    title_field: str = Field(pattern=r"^custrecord_[a-z0-9_]{1,110}$")
    severity_field: str = Field(pattern=r"^custrecord_[a-z0-9_]{1,110}$")
    source_reference_field: str = Field(pattern=r"^custrecord_[a-z0-9_]{1,110}$")
    status_field: str | None = Field(
        default=None, pattern=r"^custrecord_[a-z0-9_]{1,110}$"
    )


class LocalFilesConnectorConfig(_ContextContract):
    kind: Literal["local_files"]
    root_path: str = Field(min_length=1, max_length=2_048)


class MicrosoftGraphConnectorConfig(_ContextContract):
    kind: Literal["microsoft_graph"]
    tenant_id: str = Field(default="organizations", pattern=r"^[A-Za-z0-9.-]{1,128}$")
    client_id: str | None = Field(
        default=None,
        pattern=(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        )
    )
    drive_ids: list[str] = Field(default_factory=list, max_length=20)


class ODataConnectorConfig(_ContextContract):
    kind: Literal["odata", "sap", "dynamics365"]
    base_url: str = Field(min_length=1, max_length=2_048)
    entity_mappings: list[EntityMapping] = Field(default_factory=list, max_length=20)


class NetSuiteConnectorConfig(_ContextContract):
    kind: Literal["netsuite"]
    account_id: str = Field(pattern=r"^[A-Za-z0-9_-]{2,64}$")
    client_id: str = Field(min_length=8, max_length=256)
    certificate_id: str = Field(min_length=1, max_length=256)
    entity_mappings: list[NetSuiteEntityMapping] = Field(min_length=1, max_length=40)
    analytics_mappings: list[ERPAnalyticsMapping] = Field(default_factory=list, max_length=80)
    fulfillment_mappings: list[FulfillmentRecordMapping] = Field(
        default_factory=list, max_length=20
    )
    review_target: NetSuiteReviewTargetMapping | None = None

    @model_validator(mode="after")
    def analytics_fields_are_projected(self) -> NetSuiteConnectorConfig:
        entities = {
            mapping.record_type.casefold(): set(mapping.projected_fields)
            for mapping in self.entity_mappings
        }
        metric_ids: set[str] = set()
        for analytics in self.analytics_mappings:
            if analytics.metric_id in metric_ids:
                raise ValueError("analytics metric IDs must be unique")
            metric_ids.add(analytics.metric_id)
            projected = entities.get(analytics.entity_name.casefold())
            required = {
                analytics.value_field,
                analytics.timestamp_field,
                *analytics.dimension_fields,
            }
            if analytics.currency_field:
                required.add(analytics.currency_field)
            if analytics.weight_field:
                required.add(analytics.weight_field)
            if projected is None or not required.issubset(projected):
                raise ValueError("NetSuite analytics fields must be projected by their entity")
        flow_ids: set[str] = set()
        for flow in self.fulfillment_mappings:
            if flow.flow_id in flow_ids:
                raise ValueError("fulfillment flow IDs must be unique per connector")
            flow_ids.add(flow.flow_id)
            projected = entities.get(flow.entity_name.casefold())
            required = {
                flow.sku_field,
                flow.quantity_field,
                *flow.dimension_fields,
            }
            if flow.reference_field:
                required.add(flow.reference_field)
            if flow.timestamp_field:
                required.add(flow.timestamp_field)
            if projected is None or not required.issubset(projected):
                raise ValueError("NetSuite fulfillment fields must be projected by their entity")
        return self


class MicrosoftTeamsConnectorConfig(_ContextContract):
    kind: Literal["microsoft_teams"]
    tenant_id: str = Field(pattern=r"^[A-Za-z0-9.-]{1,128}$")
    client_id: str = Field(
        pattern=(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        )
    )
    certificate_id: str = Field(min_length=1, max_length=256)
    selected_space_ids: list[str] = Field(default_factory=list, max_length=50)


class SlackConnectorConfig(_ContextContract):
    kind: Literal["slack"]
    client_id: str = Field(min_length=1, max_length=128)
    team_id: str | None = Field(default=None, pattern=r"^[A-Z0-9]{5,32}$")
    selected_space_ids: list[str] = Field(default_factory=list, max_length=50)


class NotionConnectorConfig(_ContextContract):
    """Read-only Notion connection; selected page IDs stay in encrypted storage."""

    kind: Literal["notion"]
    workspace_label: str = Field(min_length=1, max_length=160)

    @field_validator("workspace_label")
    @classmethod
    def label_is_safe(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or any(character in value for character in "\r\n\0"):
            raise ValueError("Notion workspace labels must be single-line text")
        return cleaned


def _validate_https_origin(value: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("enterprise connector origins must be credential-free HTTPS URLs")
    return value.rstrip("/")


class WmsEntityMapping(_ContextContract):
    entity_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    endpoint_path: str = Field(min_length=1, max_length=256)
    key_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    display_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    searchable_fields: list[str] = Field(min_length=1, max_length=24)
    projected_fields: list[str] = Field(min_length=1, max_length=64)
    field_types: dict[str, str] = Field(default_factory=dict, max_length=64)
    items_field: str = Field(default="items", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    next_cursor_field: str = Field(
        default="next_cursor", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$"
    )
    cursor_parameter: str = Field(
        default="cursor", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$"
    )
    limit_parameter: str = Field(
        default="limit", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$"
    )
    search_parameter: str | None = Field(
        default="query", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$"
    )
    modified_since_parameter: str | None = Field(
        default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$"
    )
    page_size: int = Field(default=100, ge=1, le=200)
    max_pages: int = Field(default=5, ge=1, le=10)

    @field_validator("endpoint_path")
    @classmethod
    def endpoint_is_relative_and_safe(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or not segments
            or any(segment in {".", ".."} for segment in segments)
            or any(
                not segment.replace("-", "").replace("_", "").isalnum()
                for segment in segments
            )
        ):
            raise ValueError("WMS endpoint paths must be safe relative API paths")
        return "/".join(segments)

    @field_validator("searchable_fields", "projected_fields")
    @classmethod
    def fields_are_identifiers(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(values))
        if any(
            not value
            or len(value) > 128
            or not value.replace("_", "").isalnum()
            for value in cleaned
        ):
            raise ValueError("WMS fields must be allow-listed identifiers")
        return cleaned

    @field_validator("field_types")
    @classmethod
    def field_types_are_bounded(cls, values: dict[str, str]) -> dict[str, str]:
        if any(
            not key.replace("_", "").isalnum()
            or len(key) > 128
            or not value
            or len(value) > 64
            or any(character in value for character in "\r\n\0")
            for key, value in values.items()
        ):
            raise ValueError("WMS field types are invalid")
        return values

    @model_validator(mode="after")
    def required_fields_are_projected(self) -> WmsEntityMapping:
        required = {self.key_field, self.display_field, *self.searchable_fields}
        if not required.issubset(self.projected_fields):
            raise ValueError("WMS key, display, and search fields must be projected")
        if not set(self.field_types).issubset(self.projected_fields):
            raise ValueError("WMS field types must describe projected fields")
        return self


class WmsConnectorConfig(_ContextContract):
    kind: Literal["wms"]
    base_url: str = Field(min_length=1, max_length=2_048)
    environment: str = Field(min_length=1, max_length=64)
    entity_mappings: list[WmsEntityMapping] = Field(min_length=1, max_length=40)
    analytics_mappings: list[ERPAnalyticsMapping] = Field(default_factory=list, max_length=80)
    fulfillment_mappings: list[FulfillmentRecordMapping] = Field(
        default_factory=list, max_length=20
    )

    @field_validator("base_url")
    @classmethod
    def origin_is_https(cls, value: str) -> str:
        return _validate_https_origin(value)

    @field_validator("environment")
    @classmethod
    def environment_is_safe(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or any(character in value for character in "\r\n\0"):
            raise ValueError("WMS environment must be single-line text")
        return cleaned

    @model_validator(mode="after")
    def analytics_fields_are_projected(self) -> WmsConnectorConfig:
        entities = {
            mapping.entity_name.casefold(): set(mapping.projected_fields)
            for mapping in self.entity_mappings
        }
        metric_ids: set[str] = set()
        for analytics in self.analytics_mappings:
            if analytics.metric_id in metric_ids:
                raise ValueError("analytics metric IDs must be unique")
            metric_ids.add(analytics.metric_id)
            projected = entities.get(analytics.entity_name.casefold())
            required = {
                analytics.value_field,
                analytics.timestamp_field,
                *analytics.dimension_fields,
            }
            if analytics.currency_field:
                required.add(analytics.currency_field)
            if analytics.weight_field:
                required.add(analytics.weight_field)
            if projected is None or not required.issubset(projected):
                raise ValueError("WMS analytics fields must be projected by their entity")
        flow_ids: set[str] = set()
        for flow in self.fulfillment_mappings:
            if flow.flow_id in flow_ids:
                raise ValueError("fulfillment flow IDs must be unique per connector")
            flow_ids.add(flow.flow_id)
            projected = entities.get(flow.entity_name.casefold())
            required = {
                flow.sku_field,
                flow.quantity_field,
                *flow.dimension_fields,
            }
            if flow.reference_field:
                required.add(flow.reference_field)
            if flow.timestamp_field:
                required.add(flow.timestamp_field)
            if projected is None or not required.issubset(projected):
                raise ValueError("WMS fulfillment fields must be projected by their entity")
        return self


_FBA_CANONICAL_FIELDS = {
    "fba_inventory": {
        "sku",
        "fnsku",
        "asin",
        "condition",
        "marketplace_id",
        "fulfillable_quantity",
        "inbound_working_quantity",
        "inbound_shipped_quantity",
        "inbound_receiving_quantity",
        "reserved_quantity",
        "unfulfillable_quantity",
        "researching_quantity",
        "total_quantity",
        "last_updated_at",
    },
    "fba_inbound_shipments": {
        "shipment_id",
        "shipment_name",
        "status",
        "destination_fulfillment_center_id",
        "label_prep_type",
        "last_updated_at",
    },
    "fba_inbound_items": {
        "shipment_id",
        "sku",
        "fnsku",
        "quantity_shipped",
        "quantity_received",
        "quantity_in_case",
        "release_date",
    },
}


class AmazonFbaConnectorConfig(_ContextContract):
    kind: Literal["amazon_fba"]
    region: Literal["na", "eu", "fe"]
    lwa_client_id: str = Field(min_length=16, max_length=256)
    marketplace_ids: list[str] = Field(min_length=1, max_length=20)
    include_inventory: bool = True
    include_inbound_shipments: bool = True
    inbound_lookback_days: int = Field(default=90, ge=1, le=365)
    max_pages: int = Field(default=5, ge=1, le=10)
    max_inbound_shipments: int = Field(default=25, ge=1, le=50)
    analytics_mappings: list[ERPAnalyticsMapping] = Field(default_factory=list, max_length=40)
    fulfillment_mappings: list[FulfillmentRecordMapping] = Field(
        default_factory=list, max_length=20
    )

    @field_validator("marketplace_ids")
    @classmethod
    def marketplaces_are_bounded(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(values))
        if any(
            not value
            or len(value) > 32
            or not value.replace("-", "").replace("_", "").isalnum()
            for value in cleaned
        ):
            raise ValueError("Amazon marketplace IDs are invalid")
        return cleaned

    @model_validator(mode="after")
    def mappings_use_canonical_fields(self) -> AmazonFbaConnectorConfig:
        metric_ids: set[str] = set()
        flow_ids: set[str] = set()
        for mapping in [*self.analytics_mappings, *self.fulfillment_mappings]:
            mapping_id = (
                mapping.metric_id
                if isinstance(mapping, ERPAnalyticsMapping)
                else mapping.flow_id
            )
            ids = metric_ids if isinstance(mapping, ERPAnalyticsMapping) else flow_ids
            if mapping_id in ids:
                raise ValueError("Amazon FBA mapping IDs must be unique")
            ids.add(mapping_id)
            projected = _FBA_CANONICAL_FIELDS.get(mapping.entity_name)
            if projected is None:
                raise ValueError("Amazon FBA mappings require a canonical entity")
            required = (
                {
                    mapping.value_field,
                    mapping.timestamp_field,
                    *mapping.dimension_fields,
                }
                if isinstance(mapping, ERPAnalyticsMapping)
                else {
                    mapping.sku_field,
                    mapping.quantity_field,
                    *mapping.dimension_fields,
                }
            )
            if isinstance(mapping, ERPAnalyticsMapping):
                required.update(
                    value
                    for value in (mapping.currency_field, mapping.weight_field)
                    if value
                )
            else:
                required.update(
                    value
                    for value in (mapping.reference_field, mapping.timestamp_field)
                    if value
                )
            if not required.issubset(projected):
                raise ValueError("Amazon FBA mappings use unsupported canonical fields")
        return self


class RepositoryConnectorConfig(_ContextContract):
    kind: Literal["github", "gitlab"]
    base_url: str = Field(min_length=1, max_length=2_048)
    client_id: str = Field(min_length=1, max_length=256)
    gateway_url: str | None = Field(default=None, min_length=1, max_length=2_048)
    selected_source_ids: list[str] = Field(default_factory=list, max_length=200)
    include_code: bool = True
    include_delivery_data: bool = True

    @field_validator("base_url", "gateway_url")
    @classmethod
    def origins_are_https(cls, value: str | None) -> str | None:
        return _validate_https_origin(value) if value is not None else None


class WorkManagementConnectorConfig(_ContextContract):
    kind: Literal["jira", "confluence", "azure_devops", "servicenow"]
    base_url: str = Field(min_length=1, max_length=2_048)
    client_id: str = Field(min_length=1, max_length=256)
    gateway_url: str | None = Field(default=None, min_length=1, max_length=2_048)
    selected_source_ids: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("base_url", "gateway_url")
    @classmethod
    def origins_are_https(cls, value: str | None) -> str | None:
        return _validate_https_origin(value) if value is not None else None


class SqlConnectorConfig(_ContextContract):
    kind: Literal["sql"]
    server_label: str = Field(min_length=1, max_length=160)
    approved_views: list[str] = Field(min_length=1, max_length=100)
    query_template_ids: list[str] = Field(default_factory=list, max_length=100)
    gateway_url: str | None = Field(default=None, min_length=1, max_length=2_048)

    @field_validator("approved_views", "query_template_ids")
    @classmethod
    def identifiers_are_allow_listed(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(values))
        if any(
            not value
            or len(value) > 128
            or not value.replace("_", "").replace(".", "").isalnum()
            for value in cleaned
        ):
            raise ValueError("SQL views and templates must be allow-listed identifiers")
        return cleaned

    @field_validator("gateway_url")
    @classmethod
    def gateway_is_https(cls, value: str | None) -> str | None:
        return _validate_https_origin(value) if value is not None else None


class SftpConnectorConfig(_ContextContract):
    kind: Literal["sftp"]
    host: str = Field(pattern=r"^[A-Za-z0-9.-]{1,253}$")
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=128)
    host_key_sha256: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{20,88}={0,2}$")
    approved_roots: list[str] = Field(min_length=1, max_length=50)
    gateway_url: str | None = Field(default=None, min_length=1, max_length=2_048)

    @field_validator("approved_roots")
    @classmethod
    def roots_are_absolute_and_safe(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(value.rstrip("/") for value in values))
        if any(not value.startswith("/") or ".." in value.split("/") for value in cleaned):
            raise ValueError("SFTP roots must be absolute paths without traversal")
        return cleaned

    @field_validator("gateway_url")
    @classmethod
    def gateway_is_https(cls, value: str | None) -> str | None:
        return _validate_https_origin(value) if value is not None else None


class OpenApiConnectorConfig(_ContextContract):
    kind: Literal["openapi"]
    base_url: str = Field(min_length=1, max_length=2_048)
    specification_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_operation_ids: list[str] = Field(min_length=1, max_length=100)
    gateway_url: str | None = Field(default=None, min_length=1, max_length=2_048)

    @field_validator("base_url", "gateway_url")
    @classmethod
    def origins_are_https(cls, value: str | None) -> str | None:
        return _validate_https_origin(value) if value is not None else None


ConnectorConfiguration = Annotated[
    LocalFilesConnectorConfig
    | MicrosoftGraphConnectorConfig
    | ODataConnectorConfig
    | NetSuiteConnectorConfig
    | MicrosoftTeamsConnectorConfig
    | SlackConnectorConfig
    | NotionConnectorConfig
    | WmsConnectorConfig
    | AmazonFbaConnectorConfig
    | RepositoryConnectorConfig
    | WorkManagementConnectorConfig
    | SqlConnectorConfig
    | SftpConnectorConfig
    | OpenApiConnectorConfig,
    Field(discriminator="kind"),
]


class ContextDocument(_ContextContract):
    """Adapter-internal retrieved content with an explicit access boundary."""

    document_id: str = Field(min_length=1, max_length=256)
    connector_id: str = Field(min_length=1, max_length=64)
    source_kind: ConnectorKind
    record_id: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=100_000)
    uri: str | None = Field(default=None, max_length=2_048)
    entity_type: str = Field(min_length=1, max_length=64)
    source_reference: str | None = Field(default=None, max_length=256)
    structured_values: dict[str, str] = Field(default_factory=dict, max_length=64)
    allowed_principals: list[str] = Field(min_length=1, max_length=64)
    updated_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("allowed_principals")
    @classmethod
    def principals_are_explicit(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not cleaned:
            raise ValueError("context documents require at least one explicit principal")
        return cleaned


class ContextSearchQuery(_ContextContract):
    text: str = Field(min_length=1, max_length=2_000)
    principal_id: str = Field(min_length=1, max_length=256)
    connector_ids: list[str] = Field(default_factory=list, max_length=32)
    entity_types: list[str] = Field(default_factory=list, max_length=32)
    source_selection_ids: list[str] = Field(default_factory=list, max_length=200)
    preferred_entity_types: list[str] = Field(default_factory=list, max_length=32)
    target_slot: str | None = Field(default=None, max_length=64)
    known_values: dict[str, list[str]] = Field(default_factory=dict, max_length=32)
    limit: int = Field(default=8, ge=1, le=20)


class PublicContextCitation(_ContextContract):
    citation_id: str
    connector_id: str
    source_kind: ConnectorKind
    source_label: str
    source_reference: str
    entity_type: str
    excerpt: str = Field(max_length=600)
    uri: str | None = Field(default=None, max_length=2_048)
    retrieved_at: datetime
    updated_at: datetime | None = None
    stale: bool = False
    relation: ContextRelation = ContextRelation.reference
    rank: int = Field(ge=1, le=20)


# Existing backend imports keep working while the public wire shape no longer
# exposes provider-internal record identifiers.
ContextCitation = PublicContextCitation


class ContextSearchResult(_ContextContract):
    citations: list[PublicContextCitation] = Field(default_factory=list, max_length=20)
    unavailable_connector_ids: list[str] = Field(default_factory=list, max_length=32)


class ConnectedQuestionContextRequest(_ContextContract):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    suggestion_id: str = Field(min_length=1, max_length=256)
    state_version: int = Field(ge=0)
    connector_ids: list[str] = Field(default_factory=list, max_length=32)
    limit: int = Field(default=5, ge=1, le=8)


class ConnectedQuestionBrief(_ContextContract):
    status: Literal["current", "stale", "unavailable"]
    state_version: int = Field(ge=0)
    suggestion_id: str
    citations: list[PublicContextCitation] = Field(default_factory=list, max_length=8)
    unavailable_connector_ids: list[str] = Field(default_factory=list, max_length=32)


class ConnectedProblemContextRequest(_ContextContract):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    pain_id: str = Field(min_length=1, max_length=512)
    state_version: int = Field(ge=0)
    connector_ids: list[str] = Field(default_factory=list, max_length=32)
    limit: int = Field(default=8, ge=1, le=20)


class ConnectedProblemBrief(_ContextContract):
    status: Literal["current", "stale", "unavailable", "timeout", "problem_not_found"]
    state_version: int = Field(ge=0)
    pain_id: str
    citations: list[PublicContextCitation] = Field(default_factory=list, max_length=20)
    unavailable_connector_ids: list[str] = Field(default_factory=list, max_length=32)


class ConnectorHealth(_ContextContract):
    connector_id: str
    kind: ConnectorKind
    display_name: str
    phase: ConnectorPhase
    auth_enabled: bool
    last_success_at: datetime | None = None
    detail_code: str | None = Field(default=None, max_length=64)
    scope_summary: str | None = Field(default=None, max_length=200)
    retry_at: datetime | None = None
    indexed_documents: int = Field(default=0, ge=0)


class ContextDiagnostics(_ContextContract):
    search_requests: int = Field(ge=0)
    citation_results: int = Field(ge=0)
    unavailable_provider_results: int = Field(ge=0)
    circuit_opened: int = Field(ge=0)
    max_search_latency_ms: int = Field(ge=0)
    local_index_documents: int = Field(ge=0)
    citation_feedback_records: int = Field(ge=0)


class ConnectorDefinition(_ContextContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    kind: ConnectorKind
    display_name: str = Field(min_length=1, max_length=100)
    source_authority: int = Field(default=50, ge=0, le=100)
    principal_id: str = Field(min_length=1, max_length=256)
    configuration: ConnectorConfiguration | None = None
    # Accepted only for migration from encrypted API v8 rows and clients.
    root_path: str | None = Field(default=None, max_length=2_048, exclude=True)
    base_url: str | None = Field(default=None, max_length=2_048, exclude=True)
    entities: dict[str, list[str]] = Field(default_factory=dict, max_length=20, exclude=True)
    entity_mappings: list[EntityMapping] = Field(default_factory=list, max_length=20, exclude=True)
    graph_drive_ids: list[str] = Field(default_factory=list, max_length=20, exclude=True)
    tenant_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9.-]{1,128}$", exclude=True
    )
    client_id: str | None = Field(
        default=None,
        pattern=(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        ),
        exclude=True,
    )

    @field_validator("graph_drive_ids")
    @classmethod
    def graph_scopes_are_bounded(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(
            len(value) > 256 or any(character.isspace() for character in value) for value in cleaned
        ):
            raise ValueError("Graph drive identifiers are invalid")
        return cleaned

    def resolved_entity_mappings(self) -> list[EntityMapping]:
        configuration = self.configuration
        if isinstance(configuration, ODataConnectorConfig):
            return configuration.entity_mappings
        if self.entity_mappings:
            return self.entity_mappings
        return [
            EntityMapping(
                entity_name=entity,
                key_field=fields[0],
                display_field=fields[0],
                searchable_fields=fields,
                projected_fields=fields,
                search_mode=EntitySearchMode.bounded_scan,
            )
            for entity, fields in self.entities.items()
            if fields
        ]

    @model_validator(mode="before")
    @classmethod
    def migrate_v8_configuration(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("configuration") is not None:
            return value
        migrated = dict(value)
        kind = migrated.get("kind")
        if kind == "local_files" and migrated.get("root_path"):
            migrated["configuration"] = {
                "kind": kind,
                "root_path": migrated["root_path"],
            }
        elif kind == "microsoft_graph":
            migrated["configuration"] = {
                "kind": kind,
                "tenant_id": migrated.get("tenant_id") or "organizations",
                "client_id": migrated.get("client_id"),
                "drive_ids": migrated.get("graph_drive_ids", []),
            }
        elif kind in {"odata", "sap", "dynamics365"} and migrated.get("base_url"):
            mappings = migrated.get("entity_mappings", [])
            if not mappings:
                mappings = [
                    {
                        "entity_name": entity,
                        "key_field": fields[0],
                        "display_field": fields[0],
                        "searchable_fields": fields,
                        "projected_fields": fields,
                        "search_mode": "bounded_scan",
                    }
                    for entity, fields in migrated.get("entities", {}).items()
                    if fields
                ]
            migrated["configuration"] = {
                "kind": kind,
                "base_url": migrated["base_url"],
                "entity_mappings": mappings,
            }
        return migrated

    @model_validator(mode="after")
    def validate_kind_configuration(self) -> ConnectorDefinition:
        if self.configuration is None or self.configuration.kind != self.kind.value:
            raise ValueError("connector kind and configuration must match")
        if isinstance(self.configuration, LocalFilesConnectorConfig):
            self.root_path = self.configuration.root_path
        elif isinstance(self.configuration, MicrosoftGraphConnectorConfig):
            if self.base_url is not None or self.root_path is not None:
                raise ValueError("Graph connectors cannot configure file or OData paths")
            if "tenant_id" in self.model_fields_set and not self.configuration.client_id:
                raise ValueError("Graph tenant configuration requires a client ID")
            self.tenant_id = self.configuration.tenant_id
            self.client_id = self.configuration.client_id
            self.graph_drive_ids = self.configuration.drive_ids
        elif isinstance(self.configuration, ODataConnectorConfig):
            if self.configuration.kind == "odata" and not self.configuration.entity_mappings:
                raise ValueError("generic OData connectors require allow-listed entities")
            self.base_url = self.configuration.base_url
            self.entity_mappings = self.configuration.entity_mappings
        elif isinstance(self.configuration, WmsConnectorConfig):
            self.base_url = self.configuration.base_url
        return self

    def resolved_configuration(self) -> ConnectorConfiguration:
        assert self.configuration is not None
        return self.configuration


class IssueFact(_ContextContract):
    slot: str
    value: str
    kind: Literal["evidence", "inference"]
    extraction_origin: Literal["deterministic", "semantic_confirmed"] = "deterministic"
    evidence_event_ids: list[str] = Field(min_length=1, max_length=64)


class IssueHistoryWarning(_ContextContract):
    slot: str
    value: str
    status: Literal["superseded", "contradicted"]
    evidence_event_ids: list[str] = Field(min_length=1, max_length=64)


class IssueQuestion(_ContextContract):
    slot: str
    question: str
    priority: int = Field(ge=0)


class IssueReadiness(str, Enum):
    needs_clarification = "needs_clarification"
    ready_for_review = "ready_for_review"


class IssueExportOptions(_ContextContract):
    jira_work_type: str = Field(default="Task", min_length=1, max_length=80)
    azure_work_item_type: str = Field(default="Task", min_length=1, max_length=80)
    include_connected_excerpts: bool = False

    @field_validator("jira_work_type", "azure_work_item_type")
    @classmethod
    def work_item_types_are_single_line(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(character in cleaned for character in "\r\n\0"):
            raise ValueError("work item types must be non-empty single-line values")
        return cleaned


class StructuredIssueDraft(_ContextContract):
    draft_id: str
    session_id: str
    pain_id: str
    state_version: int = Field(ge=0)
    template_id: str
    language: Literal["ja", "en", "ko"]
    title: str
    problem: list[str] = Field(default_factory=list)
    impact: list[str] = Field(default_factory=list)
    affected_systems: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    owner: str | None = None
    workaround: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    facts: list[IssueFact] = Field(default_factory=list)
    history_warnings: list[IssueHistoryWarning] = Field(default_factory=list)
    unresolved_questions: list[IssueQuestion] = Field(default_factory=list)
    context_citations: list[PublicContextCitation] = Field(default_factory=list, max_length=20)
    context_status: Literal["not_requested", "current", "unavailable", "timeout"]
    readiness: IssueReadiness
    missing_required_fields: list[str] = Field(default_factory=list)


class StructuredIssueDraftBatch(_ContextContract):
    state_version: int = Field(ge=0)
    drafts: list[StructuredIssueDraft] = Field(max_length=50)
