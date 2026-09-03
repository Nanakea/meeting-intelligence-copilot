from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.adapters.context import service as context_service
from app.adapters.context import wms
from app.adapters.context.wms import WmsContextProvider
from app.adapters.integrations.workspace import IntegrationWorkspace
from app.api.main import CAPABILITY_TOKEN_HEADER, create_app
from app.domain.context import (
    ConnectorDefinition,
    ConnectorKind,
    ContextSearchQuery,
    ERPAnalyticsMapping,
    ERPMetricAggregation,
    ERPMetricKind,
    WmsConnectorConfig,
    WmsEntityMapping,
)
from app.domain.erp_analytics import ERPAnalyticsBucket, ERPAnalyticsRequest
from app.domain.integrations import DataQualityRunRequest
from app.services.erp_analytics import ERPAnalyticsSource, analyze_erp_records

TOKEN = "erp-analytics-capability-token-123456789"


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def put(self, target: str, secret: str) -> None:
        self.values[target] = secret

    def get(self, target: str) -> str | None:
        return self.values.get(target)

    def delete(self, target: str) -> bool:
        return self.values.pop(target, None) is not None


class FakeWmsTransport:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def get_json(self, path: str) -> dict[str, object]:
        self.paths.append(path)
        if "cursor=page-2" in path:
            return {
                "items": [
                    {
                        "sku": "SKU-2",
                        "name": "Second item",
                        "quantity": 4,
                        "snapshot_at": "2026-08-02T00:00:00Z",
                        "warehouse": "TOKYO",
                    }
                ]
            }
        return {
            "items": [
                {
                    "sku": "SKU-1",
                    "name": "First item",
                    "quantity": 8,
                    "snapshot_at": "2026-08-01T00:00:00Z",
                    "warehouse": "TOKYO",
                }
            ],
            "next_cursor": "page-2",
        }


def inventory_mapping() -> WmsEntityMapping:
    return WmsEntityMapping(
        entity_name="inventory",
        endpoint_path="api/v1/inventory",
        key_field="sku",
        display_field="name",
        searchable_fields=["sku", "name"],
        projected_fields=["sku", "name", "quantity", "snapshot_at", "warehouse"],
        field_types={
            "sku": "string",
            "name": "string",
            "quantity": "number",
            "snapshot_at": "datetime",
            "warehouse": "string",
        },
        max_pages=2,
    )


def quantity_metric() -> ERPAnalyticsMapping:
    return ERPAnalyticsMapping(
        metric_id="inventory.quantity_on_hand",
        metric_kind=ERPMetricKind.quantity_on_hand,
        entity_name="inventory",
        value_field="quantity",
        timestamp_field="snapshot_at",
        aggregation=ERPMetricAggregation.latest,
        unit="units",
        dimension_fields=["warehouse"],
    )


def wms_definition() -> ConnectorDefinition:
    return ConnectorDefinition(
        connector_id="warehouse",
        kind=ConnectorKind.wms,
        display_name="Warehouse system",
        principal_id="windows-user:alice",
        configuration=WmsConnectorConfig(
            kind="wms",
            base_url="https://wms.example.test/api",
            environment="production",
            entity_mappings=[inventory_mapping()],
            analytics_mappings=[quantity_metric()],
        ),
    )


def test_wms_configuration_blocks_unsafe_origins_paths_and_unprojected_metrics() -> None:
    with pytest.raises(ValidationError):
        WmsConnectorConfig(
            kind="wms",
            base_url="http://wms.example.test",
            environment="production",
            entity_mappings=[inventory_mapping()],
        )
    with pytest.raises(ValidationError):
        inventory_mapping().model_copy(update={"endpoint_path": "../admin"}).model_validate(
            {
                **inventory_mapping().model_dump(),
                "endpoint_path": "../admin",
            }
        )
    with pytest.raises(ValidationError):
        WmsConnectorConfig(
            kind="wms",
            base_url="https://wms.example.test/api",
            environment="production",
            entity_mappings=[inventory_mapping()],
            analytics_mappings=[
                quantity_metric().model_copy(update={"value_field": "secret_cost"})
            ],
        )


def test_wms_public_reference_hides_opaque_provider_ids() -> None:
    assert wms._safe_reference("a" * 64, "Warehouse item") == "Warehouse item"
    assert wms._safe_reference("SKU-123", "Warehouse item") == "SKU-123"


def test_wms_provider_is_get_only_bounded_and_live_only() -> None:
    secrets = MemorySecretStore()
    secrets.put("wms-secret", "read-only-token")
    transport = FakeWmsTransport()
    provider = WmsContextProvider(
        connector_id="warehouse",
        display_name="Warehouse system",
        principal_id="windows-user:alice",
        configuration=WmsConnectorConfig(
            kind="wms",
            base_url="https://wms.example.test/api",
            environment="production",
            entity_mappings=[inventory_mapping()],
            analytics_mappings=[quantity_metric()],
        ),
        credential_target="wms-secret",
        secret_store=secrets,
        transport=transport,
    )

    documents = asyncio.run(
        provider.search(
            ContextSearchQuery(text="SKU", principal_id="windows-user:alice")
        )
    )
    records = asyncio.run(provider.quality_records())
    metadata = asyncio.run(provider.metadata_snapshot())

    assert [document.source_reference for document in documents] == ["SKU-1", "SKU-2"]
    assert len(records["inventory"]) == 2
    assert metadata.environment == "production"
    assert len(transport.paths) == 4
    source = inspect.getsource(wms)
    assert 'method="POST"' not in source
    assert 'method="PATCH"' not in source
    assert 'method="DELETE"' not in source
    assert "persist" not in source.casefold()


def price_mapping() -> ERPAnalyticsMapping:
    return ERPAnalyticsMapping(
        metric_id="item.unit_price",
        metric_kind=ERPMetricKind.unit_price,
        entity_name="itemPrice",
        value_field="price",
        timestamp_field="effective_at",
        aggregation=ERPMetricAggregation.weighted_average,
        unit="currency/unit",
        currency_field="currency",
        weight_field="quantity",
        dimension_fields=["item"],
    )


def test_wms_gets_builtin_required_and_duplicate_key_rules() -> None:
    definition = wms_definition()

    class StubContext:
        def connector_definition(self, connector_id: str) -> ConnectorDefinition:
            assert connector_id == definition.connector_id
            return definition

    workspace = object.__new__(IntegrationWorkspace)
    workspace._context = StubContext()
    workspace._rules = {}
    workspace._improvement_profiles = {}

    rules = workspace._selected_rules(
        DataQualityRunRequest(connector_id=definition.connector_id)
    )

    assert {(rule.kind.value, rule.record_type) for rule in rules} == {
        ("required_value", "inventory"),
        ("duplicate_key", "inventory"),
    }
    assert {rule.pack_id for rule in rules} == {"builtin-wms-safety"}


def test_price_trends_keep_currency_separate_and_flag_cross_system_disagreement() -> None:
    mapping = price_mapping()
    netsuite = ERPAnalyticsSource(
        connector_id="netsuite",
        source_kind=ConnectorKind.netsuite,
        source_label="NetSuite",
        mappings=[mapping],
        records={
            "itemPrice": [
                {
                    "item": "WIDGET",
                    "price": "10",
                    "quantity": "1",
                    "currency": "USD",
                    "effective_at": "2026-01-05T00:00:00Z",
                },
                {
                    "item": "WIDGET",
                    "price": "20",
                    "quantity": "3",
                    "currency": "USD",
                    "effective_at": "2026-01-20T00:00:00Z",
                },
                {
                    "item": "WIDGET",
                    "price": "20",
                    "quantity": "2",
                    "currency": "USD",
                    "effective_at": "2026-02-10T00:00:00Z",
                },
                {
                    "item": "WIDGET",
                    "price": "2400",
                    "quantity": "1",
                    "currency": "JPY",
                    "effective_at": "2026-02-10T00:00:00Z",
                },
            ]
        },
    )
    warehouse = ERPAnalyticsSource(
        connector_id="warehouse",
        source_kind=ConnectorKind.wms,
        source_label="WMS",
        mappings=[mapping],
        records={
            "itemPrice": [
                {
                    "item": "WIDGET",
                    "price": "15",
                    "quantity": "1",
                    "currency": "USD",
                    "effective_at": "2026-01-10T00:00:00Z",
                },
                {
                    "item": "WIDGET",
                    "price": "30",
                    "quantity": "1",
                    "currency": "USD",
                    "effective_at": "2026-02-10T00:00:00Z",
                },
            ]
        },
    )
    request = ERPAnalyticsRequest(
        connector_ids=["netsuite", "warehouse"],
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=datetime(2026, 3, 1, tzinfo=UTC),
        bucket=ERPAnalyticsBucket.month,
    )

    result = analyze_erp_records(request, [netsuite, warehouse])

    assert result.status == "current"
    assert len(result.series) == 3
    netsuite_usd = next(
        series
        for series in result.series
        if series.connector_id == "netsuite" and series.currency == "USD"
    )
    assert [point.value for point in netsuite_usd.points] == [17.5, 20.0]
    assert netsuite_usd.summary.direction.value == "increasing"
    assert {series.currency for series in result.series} == {"USD", "JPY"}
    assert len(result.discrepancies) == 2
    assert all(value.currency == "USD" for value in result.discrepancies)
    assert result.persisted_remote_records == 0


def test_invalid_values_are_skipped_with_aggregate_warnings() -> None:
    source = ERPAnalyticsSource(
        connector_id="warehouse",
        source_kind=ConnectorKind.wms,
        source_label="WMS",
        mappings=[price_mapping()],
        records={
            "itemPrice": [
                {
                    "item": "WIDGET",
                    "price": "not-a-number",
                    "quantity": "1",
                    "currency": "USD",
                    "effective_at": "2026-01-01T00:00:00Z",
                },
                {
                    "item": "WIDGET",
                    "price": "10",
                    "quantity": "1",
                    "currency": "US D",
                    "effective_at": "2026-01-01T00:00:00Z",
                },
            ]
        },
    )
    result = analyze_erp_records(
        ERPAnalyticsRequest(
            connector_ids=["warehouse"],
            period_start=datetime(2026, 1, 1, tzinfo=UTC),
            period_end=datetime(2026, 2, 1, tzinfo=UTC),
        ),
        [source],
    )
    assert result.series == []
    assert {(warning.code, warning.skipped_records) for warning in result.warnings} == {
        ("invalid_value", 1),
        ("invalid_currency", 1),
    }


def test_analytics_uses_one_total_deadline_and_keeps_completed_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fast = wms_definition().model_copy(update={"connector_id": "warehouse-fast"})
    slow = wms_definition().model_copy(update={"connector_id": "warehouse-slow"})
    service = object.__new__(context_service.ContextConnectorService)
    service._definitions = {
        fast.connector_id: fast,
        slow.connector_id: slow,
    }

    async def quality_records(
        connector_id: str, *, changed_after: datetime | None = None
    ) -> dict[str, list[dict[str, object]]]:
        del changed_after
        if connector_id == slow.connector_id:
            await asyncio.sleep(0.1)
        return {
            "inventory": [
                {
                    "sku": "SKU-1",
                    "name": "Item",
                    "quantity": 12,
                    "snapshot_at": "2026-08-01T00:00:00Z",
                    "warehouse": "TOKYO",
                }
            ]
        }

    service.quality_records = quality_records
    monkeypatch.setattr(context_service, "_ERP_ANALYTICS_DEADLINE_SECONDS", 0.02)

    result = asyncio.run(
        service.erp_analytics(
            ERPAnalyticsRequest(
                connector_ids=[fast.connector_id, slow.connector_id],
                period_start=datetime(2026, 8, 1, tzinfo=UTC),
                period_end=datetime(2026, 9, 1, tzinfo=UTC),
            )
        )
    )

    assert result.status == "partial"
    assert result.unavailable_connector_ids == [slow.connector_id]
    assert [series.connector_id for series in result.series] == [fast.connector_id]


class FakeAnalyticsService:
    async def erp_analytics(self, request: ERPAnalyticsRequest):
        return analyze_erp_records(request, [])


def test_analytics_endpoint_requires_capability_authentication() -> None:
    client = TestClient(
        create_app(
            capability_token=TOKEN,
            context_service_instance=FakeAnalyticsService(),  # type: ignore[arg-type]
        )
    )
    body = {
        "connector_ids": ["netsuite"],
        "period_start": "2026-01-01T00:00:00Z",
        "period_end": "2026-02-01T00:00:00Z",
        "bucket": "week",
    }
    assert client.post("/erp/analytics/runs", json=body).status_code == 401
    response = client.post(
        "/erp/analytics/runs",
        headers={CAPABILITY_TOKEN_HEADER: TOKEN},
        json=body,
    )
    assert response.status_code == 200
    assert response.json()["persisted_remote_records"] == 0
