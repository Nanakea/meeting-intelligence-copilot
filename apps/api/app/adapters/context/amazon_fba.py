"""Strict read-only Amazon Selling Partner API adapter for FBA reconciliation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.adapters.context.windows_security import SecretStore
from app.domain.assurance import ERPEntityMetadata, ERPFieldMetadata, ERPMetadataSnapshot
from app.domain.context import (
    AmazonFbaConnectorConfig,
    ConnectorHealth,
    ConnectorKind,
    ConnectorPhase,
    ContextDocument,
    ContextSearchQuery,
)

_FBA_HOSTS = {
    "na": "sellingpartnerapi-na.amazon.com",
    "eu": "sellingpartnerapi-eu.amazon.com",
    "fe": "sellingpartnerapi-fe.amazon.com",
}
_LWA_HOST = "api.amazon.com"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
_SHIPMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AmazonFbaReadTransport(Protocol):
    """Narrow transport: one exact auth POST and SP-API GET operations only."""

    def exchange_token(self, body: bytes) -> tuple[int, dict[str, object]]: ...

    def get_json(self, path: str, access_token: str) -> tuple[int, dict[str, object]]: ...


class UrllibAmazonFbaReadTransport:
    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, file_pointer, code, message, headers, new_url):
            return None

    def __init__(self, region: str, *, timeout_seconds: float = 5.0) -> None:
        self._sp_host = _FBA_HOSTS[region]
        self._timeout = timeout_seconds
        self._opener = build_opener(self._NoRedirect())

    def _request(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str],
        body: bytes | None = None,
        expected_host: str,
    ) -> tuple[int, dict[str, object]]:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").casefold() != expected_host.casefold()
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("Amazon request escaped its allow-listed HTTPS origin")
        request = Request(url, method=method, headers=headers, data=body)
        for attempt in range(3):
            try:
                with self._opener.open(request, timeout=self._timeout) as response:
                    payload = response.read(_MAX_RESPONSE_BYTES + 1)
                    if len(payload) > _MAX_RESPONSE_BYTES:
                        raise ValueError("Amazon response exceeds the local bound")
                    decoded = json.loads(payload or b"{}")
                    if not isinstance(decoded, dict):
                        raise ValueError("Amazon response must be an object")
                    return int(response.status), decoded
            except HTTPError as error:
                if error.code not in _TRANSIENT_STATUSES or attempt == 2:
                    raise OSError("Amazon request failed") from None
                retry_after = error.headers.get("Retry-After", "") if error.headers else ""
                delay = min(float(retry_after), 1.0) if retry_after.isdigit() else 0.25 * 2**attempt
                time.sleep(delay)
            except URLError as error:
                raise OSError("Amazon SP-API is unavailable") from error
        raise OSError("Amazon SP-API is unavailable")

    def exchange_token(self, body: bytes) -> tuple[int, dict[str, object]]:
        return self._request(
            "https://api.amazon.com/auth/o2/token",
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            },
            body=body,
            expected_host=_LWA_HOST,
        )

    def get_json(self, path: str, access_token: str) -> tuple[int, dict[str, object]]:
        if not path or path.startswith(("/", "http:")) or path.startswith("https:"):
            raise ValueError("Amazon SP-API path is invalid")
        return self._request(
            f"https://{self._sp_host}/{path}",
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": "MeetingIntelligenceCopilot/0.6 (Language=Python/3.12)",
                "x-amz-access-token": access_token,
                "x-amz-date": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            },
            expected_host=self._sp_host,
        )


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value))
    except ValueError:
        return None


def _nested_number(value: object, *path: str) -> int | float | None:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _number(current)


class AmazonFbaContextProvider:
    def __init__(
        self,
        *,
        connector_id: str,
        display_name: str,
        principal_id: str,
        configuration: AmazonFbaConnectorConfig,
        credential_target: str,
        secret_store: SecretStore,
        transport: AmazonFbaReadTransport | None = None,
    ) -> None:
        self._connector_id = connector_id
        self._display_name = display_name
        self._principal_id = principal_id
        self.configuration = configuration
        self._credential_target = credential_target
        self._secret_store = secret_store
        self._transport = transport or UrllibAmazonFbaReadTransport(configuration.region)
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._last_success_at: datetime | None = None

    @property
    def connector_id(self) -> str:
        return self._connector_id

    @property
    def kind(self) -> ConnectorKind:
        return ConnectorKind.amazon_fba

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def auth_enabled(self) -> bool:
        return self._secret_store.get(self._credential_target) is not None

    def _credential(self) -> dict[str, str]:
        raw = self._secret_store.get(self._credential_target)
        if raw is None:
            raise PermissionError("Amazon FBA credentials are unavailable")
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("Amazon FBA credential is invalid")
        client_secret = decoded.get("client_secret")
        refresh_token = decoded.get("refresh_token")
        if not isinstance(client_secret, str) or not isinstance(refresh_token, str):
            raise ValueError("Amazon FBA credential is invalid")
        if not client_secret or not refresh_token or any(
            character in client_secret + refresh_token for character in "\r\n\0"
        ):
            raise ValueError("Amazon FBA credential is invalid")
        return {"client_secret": client_secret, "refresh_token": refresh_token}

    def _token(self) -> str:
        if self._access_token and time.monotonic() < self._access_token_expires_at:
            return self._access_token
        credential = self._credential()
        status, response = self._transport.exchange_token(
            urlencode(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": credential["refresh_token"],
                    "client_id": self.configuration.lwa_client_id,
                    "client_secret": credential["client_secret"],
                }
            ).encode()
        )
        token = response.get("access_token")
        if status != 200 or not isinstance(token, str) or not token:
            raise PermissionError("Amazon LWA token exchange failed")
        expires_in = response.get("expires_in", 3600)
        try:
            lifetime = int(expires_in)
        except (TypeError, ValueError):
            lifetime = 300
        self._access_token = token
        self._access_token_expires_at = time.monotonic() + max(30, min(lifetime - 30, 3570))
        return token

    def _get(self, path: str) -> dict[str, object]:
        allowed = (
            "fba/inventory/v1/summaries?",
            "fba/inbound/v0/shipments?",
            "fba/inbound/v0/shipmentItems?",
        )
        item_prefix = "fba/inbound/v0/shipments/"
        item_path = path.split("?", 1)[0]
        if not path.startswith(allowed) and not (
            path.startswith(item_prefix) and item_path.endswith("/items")
        ):
            raise ValueError("Amazon FBA retrieval endpoint is not allow-listed")
        status, response = self._transport.get_json(path, self._token())
        if status != 200:
            raise OSError("Amazon FBA retrieval failed")
        return response

    @staticmethod
    def _payload(response: dict[str, object]) -> dict[str, object]:
        payload = response.get("payload", response)
        if not isinstance(payload, dict):
            raise ValueError("Amazon FBA payload is invalid")
        return payload

    def _inventory_records(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for marketplace_id in self.configuration.marketplace_ids:
            next_token: str | None = None
            for _ in range(self.configuration.max_pages):
                parameters = {
                    "details": "true",
                    "granularityType": "Marketplace",
                    "granularityId": marketplace_id,
                    "marketplaceIds": marketplace_id,
                }
                if next_token:
                    parameters["nextToken"] = next_token
                response = self._get(
                    f"fba/inventory/v1/summaries?{urlencode(parameters)}"
                )
                payload = self._payload(response)
                values = payload.get("inventorySummaries", [])
                if not isinstance(values, list):
                    raise ValueError("Amazon inventory summaries are invalid")
                for item in values[:250]:
                    if not isinstance(item, dict):
                        continue
                    details = item.get("inventoryDetails", {})
                    records.append(
                        {
                            "sku": item.get("sellerSku"),
                            "fnsku": item.get("fnSku"),
                            "asin": item.get("asin"),
                            "condition": item.get("condition"),
                            "marketplace_id": marketplace_id,
                            "fulfillable_quantity": _nested_number(
                                details, "fulfillableQuantity"
                            ),
                            "inbound_working_quantity": _nested_number(
                                details, "inboundWorkingQuantity"
                            ),
                            "inbound_shipped_quantity": _nested_number(
                                details, "inboundShippedQuantity"
                            ),
                            "inbound_receiving_quantity": _nested_number(
                                details, "inboundReceivingQuantity"
                            ),
                            "reserved_quantity": _nested_number(
                                details, "reservedQuantity", "totalReservedQuantity"
                            ),
                            "unfulfillable_quantity": _nested_number(
                                details, "unfulfillableQuantity", "totalUnfulfillableQuantity"
                            ),
                            "researching_quantity": _nested_number(
                                details, "researchingQuantity", "totalResearchingQuantity"
                            ),
                            "total_quantity": _number(item.get("totalQuantity")),
                            "last_updated_at": item.get("lastUpdatedTime"),
                        }
                    )
                pagination = response.get("pagination", {})
                raw_token = (
                    pagination.get("nextToken")
                    if isinstance(pagination, dict)
                    else None
                )
                if not isinstance(raw_token, str) or not raw_token:
                    break
                if len(raw_token) > 1024 or raw_token == next_token:
                    raise ValueError("Amazon inventory pagination token is invalid")
                next_token = raw_token
        return records

    def _inbound_records(self) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        shipments: list[dict[str, object]] = []
        next_token: str | None = None
        last_updated = datetime.now(UTC) - timedelta(
            days=self.configuration.inbound_lookback_days
        )
        for _ in range(self.configuration.max_pages):
            parameters = {
                "ShipmentStatusList": (
                    "WORKING,SHIPPED,IN_TRANSIT,DELIVERED,CHECKED_IN,"
                    "RECEIVING,CLOSED,ERROR"
                ),
                "LastUpdatedAfter": last_updated.isoformat().replace("+00:00", "Z"),
            }
            if next_token:
                parameters = {"NextToken": next_token}
            payload = self._payload(
                self._get(f"fba/inbound/v0/shipments?{urlencode(parameters)}")
            )
            values = payload.get("ShipmentData", [])
            if not isinstance(values, list):
                raise ValueError("Amazon inbound shipments are invalid")
            for item in values:
                if not isinstance(item, dict):
                    continue
                shipment_id = str(item.get("ShipmentId", "")).strip()
                if not _SHIPMENT_ID.fullmatch(shipment_id):
                    continue
                shipments.append(
                    {
                        "shipment_id": shipment_id,
                        "shipment_name": item.get("ShipmentName"),
                        "status": item.get("ShipmentStatus"),
                        "destination_fulfillment_center_id": item.get(
                            "DestinationFulfillmentCenterId"
                        ),
                        "label_prep_type": item.get("LabelPrepType"),
                        "last_updated_at": item.get("LastUpdatedDate"),
                    }
                )
                if len(shipments) >= self.configuration.max_inbound_shipments:
                    break
            if len(shipments) >= self.configuration.max_inbound_shipments:
                break
            raw_token = payload.get("NextToken")
            if not isinstance(raw_token, str) or not raw_token:
                break
            if len(raw_token) > 1024 or raw_token == next_token:
                raise ValueError("Amazon inbound pagination token is invalid")
            next_token = raw_token

        selected_shipments = {str(value["shipment_id"]) for value in shipments}
        items: list[dict[str, object]] = []
        next_token = None
        for _ in range(self.configuration.max_pages):
            parameters = {
                "LastUpdatedAfter": last_updated.isoformat().replace("+00:00", "Z")
            }
            if next_token:
                parameters = {"NextToken": next_token}
            payload = self._payload(
                self._get(f"fba/inbound/v0/shipmentItems?{urlencode(parameters)}")
            )
            values = payload.get("ItemData", [])
            if not isinstance(values, list):
                raise ValueError("Amazon inbound shipment items are invalid")
            for item in values[:1000]:
                if not isinstance(item, dict):
                    continue
                shipment_id = str(item.get("ShipmentId", "")).strip()
                if shipment_id not in selected_shipments:
                    continue
                items.append(
                    {
                        "shipment_id": shipment_id,
                        "sku": item.get("SellerSKU"),
                        "fnsku": item.get("FulfillmentNetworkSKU"),
                        "quantity_shipped": _number(item.get("QuantityShipped")),
                        "quantity_received": _number(item.get("QuantityReceived")),
                        "quantity_in_case": _number(item.get("QuantityInCase")),
                        "release_date": item.get("ReleaseDate"),
                    }
                )
            raw_token = payload.get("NextToken")
            if not isinstance(raw_token, str) or not raw_token:
                break
            if len(raw_token) > 1024 or raw_token == next_token:
                raise ValueError("Amazon shipment-item pagination token is invalid")
            next_token = raw_token
        return shipments, items

    def _records_sync(self) -> dict[str, list[dict[str, object]]]:
        records: dict[str, list[dict[str, object]]] = {}
        if self.configuration.include_inventory:
            records["fba_inventory"] = self._inventory_records()
        if self.configuration.include_inbound_shipments:
            shipments, items = self._inbound_records()
            records["fba_inbound_shipments"] = shipments
            records["fba_inbound_items"] = items
        self._last_success_at = datetime.now(UTC)
        return records

    async def health(self) -> ConnectorHealth:
        if not self.auth_enabled:
            return ConnectorHealth(
                connector_id=self.connector_id,
                kind=self.kind,
                display_name=self.display_name,
                phase=ConnectorPhase.auth_required,
                auth_enabled=False,
                detail_code="amazon_lwa_credentials_required",
                scope_summary="Selected FBA marketplaces; read-only",
            )
        try:
            await asyncio.to_thread(self._token)
            phase, detail = ConnectorPhase.ready, None
        except (OSError, PermissionError, ValueError):
            phase, detail = ConnectorPhase.unavailable, "amazon_fba_unavailable"
        return ConnectorHealth(
            connector_id=self.connector_id,
            kind=self.kind,
            display_name=self.display_name,
            phase=phase,
            auth_enabled=True,
            detail_code=detail,
            scope_summary=f"{len(self.configuration.marketplace_ids)} selected FBA marketplaces",
            last_success_at=self._last_success_at,
        )

    async def quality_records(
        self, *, changed_after: datetime | None = None
    ) -> dict[str, list[dict[str, object]]]:
        del changed_after
        return await asyncio.to_thread(self._records_sync)

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        records = await self.quality_records()
        terms = [value.casefold() for value in query.text.split() if len(value) > 1][:8]
        documents: list[ContextDocument] = []
        for entity, values in records.items():
            if query.entity_types and entity not in query.entity_types:
                continue
            for record in values:
                searchable = " ".join(str(value) for value in record.values()).casefold()
                if terms and not any(term in searchable for term in terms):
                    continue
                reference = str(
                    record.get("sku")
                    or record.get("shipment_name")
                    or record.get("shipment_id")
                    or "FBA record"
                )[:256]
                public_values = {
                    key: str(value)[:500]
                    for key, value in record.items()
                    if value is not None and key != "shipment_id"
                }
                documents.append(
                    ContextDocument(
                        document_id=hashlib.sha256(
                            f"{self.connector_id}\0{entity}\0{reference}".encode()
                        ).hexdigest(),
                        connector_id=self.connector_id,
                        source_kind=self.kind,
                        record_id=hashlib.sha256(reference.encode()).hexdigest(),
                        title=f"{self.display_name}: {reference}"[:500],
                        content="\n".join(
                            f"{key}: {value}" for key, value in public_values.items()
                        ),
                        entity_type=entity,
                        source_reference=reference,
                        structured_values=public_values,
                        allowed_principals=[self._principal_id],
                    )
                )
        return documents[: max(query.limit * 4, query.limit)]

    async def metadata_snapshot(self) -> ERPMetadataSnapshot:
        fields = {
            "fba_inventory": [
                "sku",
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
            ],
            "fba_inbound_shipments": ["shipment_id", "status", "last_updated_at"],
            "fba_inbound_items": [
                "shipment_id",
                "sku",
                "quantity_shipped",
                "quantity_received",
            ],
        }
        entities = [
            ERPEntityMetadata(
                name=entity,
                fields=[
                    ERPFieldMetadata(
                        name=field,
                        data_type=("number" if "quantity" in field else "string"),
                        required=field in {"sku", "shipment_id"},
                        key=field in {"sku", "shipment_id"},
                    )
                    for field in names
                ],
            )
            for entity, names in fields.items()
        ]
        canonical = json.dumps(
            [entity.model_dump(mode="json") for entity in entities],
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        return ERPMetadataSnapshot(
            snapshot_id=hashlib.sha256(
                f"{self.connector_id}\0{digest}".encode()
            ).hexdigest(),
            connector_id=self.connector_id,
            source_reference=f"{self.display_name} SP-API read contract",
            metadata_hash=digest,
            retrieved_at=datetime.now(UTC),
            entities=entities,
            environment="production",
            api_version="FBA Inventory v1 / Fulfillment Inbound v0 reads",
        )

    async def sync(self) -> int:
        await asyncio.to_thread(self._token)
        return 0

    async def close(self) -> None:
        self._access_token = None
        self._access_token_expires_at = 0.0
