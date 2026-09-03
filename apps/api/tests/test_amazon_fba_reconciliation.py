from __future__ import annotations

import asyncio
import inspect
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.adapters.context import amazon_fba
from app.adapters.context.amazon_fba import AmazonFbaContextProvider
from app.adapters.context.service import ContextConnectorService
from app.adapters.integrations.workspace import IntegrationWorkspace
from app.api.main import CAPABILITY_TOKEN_HEADER, create_app
from app.domain.context import (
    AmazonFbaConnectorConfig,
    ConnectorDefinition,
    ConnectorKind,
    ContextSearchQuery,
    ERPAnalyticsMapping,
    ERPMetricAggregation,
    ERPMetricKind,
    FulfillmentRecordMapping,
)
from app.domain.fulfillment_reconciliation import (
    FulfillmentReconciliationRequest,
    FulfillmentStage,
)
from app.domain.integrations import DataQualityRunRequest
from app.services.fulfillment_reconciliation import (
    FulfillmentSource,
    reconcile_fulfillment_records,
)

TOKEN = "fba-reconciliation-capability-token-123456789"


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def put(self, target: str, secret: str) -> None:
        self.values[target] = secret

    def get(self, target: str) -> str | None:
        return self.values.get(target)

    def delete(self, target: str) -> bool:
        return self.values.pop(target, None) is not None


class FakeAmazonTransport:
    def __init__(self) -> None:
        self.token_bodies: list[bytes] = []
        self.paths: list[str] = []

    def exchange_token(self, body: bytes) -> tuple[int, dict[str, object]]:
        self.token_bodies.append(body)
        return 200, {"access_token": "short-lived-token", "expires_in": 3600}

    def get_json(
        self, path: str, access_token: str
    ) -> tuple[int, dict[str, object]]:
        assert access_token == "short-lived-token"
        self.paths.append(path)
        if path.startswith("fba/inventory/v1/summaries?"):
            if "nextToken=inventory-page-2" in path:
                return 200, {
                    "payload": {
                        "inventorySummaries": [
                            {
                                "sellerSku": "SKU-2",
                                "fnSku": "FNSKU-2",
                                "asin": "ASIN000002",
                                "condition": "NewItem",
                                "totalQuantity": 3,
                                "lastUpdatedTime": "2026-09-01T01:00:00Z",
                                "inventoryDetails": {"fulfillableQuantity": 3},
                            }
                        ]
                    }
                }
            return 200, {
                "payload": {
                    "inventorySummaries": [
                        {
                            "sellerSku": "SKU-1",
                            "fnSku": "FNSKU-1",
                            "asin": "ASIN000001",
                            "condition": "NewItem",
                            "totalQuantity": 8,
                            "lastUpdatedTime": "2026-09-01T00:00:00Z",
                            "inventoryDetails": {
                                "fulfillableQuantity": 5,
                                "inboundShippedQuantity": 3,
                            },
                        }
                    ],
                },
                "pagination": {"nextToken": "inventory-page-2"},
            }
        if path.startswith("fba/inbound/v0/shipments?"):
            return 200, {
                "payload": {
                    "ShipmentData": [
                        {
                            "ShipmentId": "FBA-SHIPMENT-1",
                            "ShipmentName": "Tokyo replenishment 1",
                            "ShipmentStatus": "RECEIVING",
                            "DestinationFulfillmentCenterId": "NRT1",
                            "LabelPrepType": "SELLER_LABEL",
                            "LastUpdatedDate": "2026-09-01T02:00:00Z",
                        }
                    ]
                }
            }
        if path.startswith("fba/inbound/v0/shipmentItems?") and "NextToken=" not in path:
            return 200, {
                "payload": {
                    "ItemData": [
                        {
                            "ShipmentId": "FBA-SHIPMENT-1",
                            "SellerSKU": "SKU-1",
                            "FulfillmentNetworkSKU": "FNSKU-1",
                            "QuantityShipped": 10,
                            "QuantityReceived": 8,
                        }
                    ],
                    "NextToken": "item-page-2",
                }
            }
        if "NextToken=item-page-2" in path:
            return 200, {
                "payload": {
                    "ItemData": [
                        {
                            "ShipmentId": "FBA-SHIPMENT-1",
                            "SellerSKU": "SKU-2",
                            "FulfillmentNetworkSKU": "FNSKU-2",
                            "QuantityShipped": 3,
                            "QuantityReceived": 3,
                        }
                    ]
                }
            }
        raise AssertionError(f"unexpected Amazon path: {path}")


def inventory_flow() -> FulfillmentRecordMapping:
    return FulfillmentRecordMapping(
        flow_id="inventory",
        entity_name="fba_inventory",
        sku_field="sku",
        quantity_field="total_quantity",
        timestamp_field="last_updated_at",
        dimension_fields=["marketplace_id"],
    )


def fba_config() -> AmazonFbaConnectorConfig:
    return AmazonFbaConnectorConfig(
        kind="amazon_fba",
        region="fe",
        lwa_client_id="amzn1.application-oa2-client.example",
        marketplace_ids=["A1VC38T7YXB528"],
        max_pages=2,
        fulfillment_mappings=[inventory_flow()],
        analytics_mappings=[
            ERPAnalyticsMapping(
                metric_id="fba.inventory.total",
                metric_kind=ERPMetricKind.quantity_on_hand,
                entity_name="fba_inventory",
                value_field="total_quantity",
                timestamp_field="last_updated_at",
                aggregation=ERPMetricAggregation.latest,
                unit="units",
                dimension_fields=["marketplace_id"],
            )
        ],
    )


def test_fba_configuration_rejects_unknown_fields_and_marketplaces() -> None:
    with pytest.raises(ValidationError):
        fba_config().model_copy(
            update={"marketplace_ids": ["unsafe marketplace"]}
        ).model_validate(
            {**fba_config().model_dump(), "marketplace_ids": ["unsafe marketplace"]}
        )
    with pytest.raises(ValidationError):
        AmazonFbaConnectorConfig(
            **{
                **fba_config().model_dump(),
                "fulfillment_mappings": [
                    inventory_flow().model_copy(update={"quantity_field": "secret_field"})
                ],
            }
        )


def test_fba_adapter_reads_inventory_and_all_bounded_inbound_item_pages() -> None:
    secrets = MemorySecretStore()
    secrets.put(
        "amazon-secret",
        json.dumps(
            {
                "client_secret": "client-secret-value",
                "refresh_token": "refresh-token-value",
            }
        ),
    )
    transport = FakeAmazonTransport()
    provider = AmazonFbaContextProvider(
        connector_id="amazon-fba",
        display_name="Amazon FBA Japan",
        principal_id="windows-user:alice",
        configuration=fba_config(),
        credential_target="amazon-secret",
        secret_store=secrets,
        transport=transport,
    )

    records = asyncio.run(provider.quality_records())
    documents = asyncio.run(
        provider.search(
            ContextSearchQuery(text="SKU-1", principal_id="windows-user:alice")
        )
    )
    metadata = asyncio.run(provider.metadata_snapshot())

    assert [value["sku"] for value in records["fba_inventory"]] == ["SKU-1", "SKU-2"]
    assert [value["sku"] for value in records["fba_inbound_items"]] == ["SKU-1", "SKU-2"]
    assert len(transport.token_bodies) == 1
    assert b"refresh_token=refresh-token-value" in transport.token_bodies[0]
    assert any("nextToken=inventory-page-2" in path for path in transport.paths)
    assert any("NextToken=item-page-2" in path for path in transport.paths)
    assert documents[0].source_kind is ConnectorKind.amazon_fba
    assert "FBA-SHIPMENT-1" not in documents[0].content
    assert {entity.name for entity in metadata.entities} == {
        "fba_inventory",
        "fba_inbound_shipments",
        "fba_inbound_items",
    }

    source = inspect.getsource(amazon_fba)
    assert "createInbound" not in source
    assert "updateShipment" not in source
    assert "deleteInventory" not in source
    assert 'method="PATCH"' not in source
    assert 'method="PUT"' not in source
    assert "persist" not in source.casefold()


def test_fba_quality_rules_do_not_treat_cross_market_skus_as_duplicates() -> None:
    definition = ConnectorDefinition(
        connector_id="amazon-fba",
        kind=ConnectorKind.amazon_fba,
        display_name="Amazon FBA Japan",
        principal_id="windows-user:alice",
        configuration=fba_config(),
    )

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

    assert ContextConnectorService.credential_target(definition).startswith(
        "MeetingIntelligenceCopilot/context/amazon-fba/"
    )

    assert {(rule.kind.value, rule.record_type) for rule in rules} == {
        ("required_value", "fba_inventory"),
        ("required_value", "fba_inbound_shipments"),
        ("duplicate_key", "fba_inbound_shipments"),
        ("required_value", "fba_inbound_items"),
    }


def _mapping(entity_name: str) -> FulfillmentRecordMapping:
    return FulfillmentRecordMapping(
        flow_id="inventory",
        entity_name=entity_name,
        sku_field="sku",
        quantity_field="quantity",
        dimension_fields=["marketplace"],
    )


def test_reconciliation_pinpoints_each_manual_handoff_gap() -> None:
    sources = [
        FulfillmentSource(
            connector_id="netsuite",
            stage=FulfillmentStage.netsuite,
            source_label="NetSuite",
            mappings=[_mapping("inventory")],
            records={
                "inventory": [
                    {"sku": "SKU-A", "quantity": 10, "marketplace": "JP"},
                    {"sku": "SKU-B", "quantity": 5, "marketplace": "JP"},
                ]
            },
        ),
        FulfillmentSource(
            connector_id="warehouse",
            stage=FulfillmentStage.wms,
            source_label="WMS",
            mappings=[_mapping("inventory")],
            records={
                "inventory": [
                    {"sku": "SKU-A", "quantity": 8, "marketplace": "JP"},
                    {"sku": "SKU-C", "quantity": 2, "marketplace": "JP"},
                ]
            },
        ),
        FulfillmentSource(
            connector_id="amazon-fba",
            stage=FulfillmentStage.amazon_fba,
            source_label="Amazon FBA",
            mappings=[_mapping("inventory")],
            records={
                "inventory": [
                    {"sku": "SKU-A", "quantity": 7, "marketplace": "JP"},
                    {"sku": "SKU-C", "quantity": 2, "marketplace": "JP"},
                    {"sku": "SKU-D", "quantity": 1, "marketplace": "JP"},
                ]
            },
        ),
    ]
    request = FulfillmentReconciliationRequest(
        netsuite_connector_id="netsuite",
        wms_connector_id="warehouse",
        amazon_fba_connector_id="amazon-fba",
    )

    result = reconcile_fulfillment_records(request, sources)

    actual = {
        (finding.transition, finding.kind.value, finding.sku_reference)
        for finding in result.findings
    }
    assert actual == {
        ("netsuite_to_wms", "quantity_mismatch", "SKU-A"),
        ("netsuite_to_wms", "missing_downstream_record", "SKU-B"),
        ("netsuite_to_wms", "missing_upstream_record", "SKU-C"),
        ("wms_to_amazon_fba", "quantity_mismatch", "SKU-A"),
        ("wms_to_amazon_fba", "missing_upstream_record", "SKU-D"),
    }
    assert all(finding.requires_review for finding in result.findings)
    assert result.persisted_remote_records == 0


def test_reconciliation_keeps_references_and_dimensions_separate() -> None:
    mappings = [
        FulfillmentRecordMapping(
            flow_id="inbound",
            entity_name="shipments",
            sku_field="sku",
            quantity_field="quantity",
            reference_field="shipment",
            dimension_fields=["marketplace"],
        )
    ]
    records = {
        "shipments": [
            {"sku": "SKU-A", "quantity": 5, "shipment": "SHIP-1", "marketplace": "JP"},
            {"sku": "SKU-A", "quantity": 7, "shipment": "SHIP-2", "marketplace": "JP"},
        ]
    }
    sources = [
        FulfillmentSource("netsuite", FulfillmentStage.netsuite, "NetSuite", mappings, records),
        FulfillmentSource("warehouse", FulfillmentStage.wms, "WMS", mappings, records),
        FulfillmentSource("amazon-fba", FulfillmentStage.amazon_fba, "FBA", mappings, records),
    ]

    result = reconcile_fulfillment_records(
        FulfillmentReconciliationRequest(
            netsuite_connector_id="netsuite",
            wms_connector_id="warehouse",
            amazon_fba_connector_id="amazon-fba",
        ),
        sources,
    )

    assert result.findings == []


class FakeReconciliationService:
    async def fulfillment_reconciliation(self, request: FulfillmentReconciliationRequest):
        return reconcile_fulfillment_records(request, [])


def test_reconciliation_endpoint_requires_capability_authentication() -> None:
    client = TestClient(
        create_app(
            capability_token=TOKEN,
            context_service_instance=FakeReconciliationService(),  # type: ignore[arg-type]
        )
    )
    payload = {
        "netsuite_connector_id": "netsuite",
        "wms_connector_id": "warehouse",
        "amazon_fba_connector_id": "amazon-fba",
    }

    assert client.post("/erp/fulfillment-reconciliation/runs", json=payload).status_code == 401
    response = client.post(
        "/erp/fulfillment-reconciliation/runs",
        json=payload,
        headers={CAPABILITY_TOKEN_HEADER: TOKEN},
    )

    assert response.status_code == 200
    assert response.json()["persisted_remote_records"] == 0
