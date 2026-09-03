"""Vendor-neutral, allow-listed, read-only WMS context adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlencode

from app.adapters.context.remote import ReadOnlyJsonClient, validate_remote_base_url
from app.adapters.context.windows_security import SecretStore
from app.domain.assurance import ERPEntityMetadata, ERPFieldMetadata, ERPMetadataSnapshot
from app.domain.context import (
    ConnectorHealth,
    ConnectorKind,
    ConnectorPhase,
    ContextDocument,
    ContextSearchQuery,
    WmsConnectorConfig,
    WmsEntityMapping,
)

_MAX_CURSOR_CHARACTERS = 512
_MAX_QUERY_CHARACTERS = 240
_OPAQUE_IDENTIFIER = re.compile(
    r"^(?:[0-9a-f]{32,}|[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})$",
    re.IGNORECASE,
)


class WmsReadTransport(Protocol):
    """Narrow GET-only transport; it intentionally has no method argument."""

    def get_json(self, path: str) -> dict[str, object]: ...


class ReadOnlyWmsTransport:
    def __init__(self, base_url: str, token: str) -> None:
        self._client = ReadOnlyJsonClient(base_url, token)

    def get_json(self, path: str) -> dict[str, object]:
        return self._client.request_json(path)


def _safe_reference(key: str, display: str) -> str:
    safe_characters = all(
        character.isalnum() or character in "._-/" for character in key
    )
    if (
        key
        and len(key) <= 128
        and safe_characters
        and not _OPAQUE_IDENTIFIER.fullmatch(key)
    ):
        return key
    return display[:256]


class WmsContextProvider:
    """Reads bounded records from explicitly mapped WMS endpoints and retains no rows."""

    def __init__(
        self,
        *,
        connector_id: str,
        display_name: str,
        principal_id: str,
        configuration: WmsConnectorConfig,
        credential_target: str,
        secret_store: SecretStore,
        transport: WmsReadTransport | None = None,
    ) -> None:
        self._connector_id = connector_id
        self._display_name = display_name
        self._principal_id = principal_id
        self.configuration = configuration
        self._base_url = validate_remote_base_url(configuration.base_url)
        self._credential_target = credential_target
        self._secret_store = secret_store
        self._transport = transport
        self._last_success_at: datetime | None = None

    @property
    def connector_id(self) -> str:
        return self._connector_id

    @property
    def kind(self) -> ConnectorKind:
        return ConnectorKind.wms

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def auth_enabled(self) -> bool:
        return self._secret_store.get(self._credential_target) is not None

    def _reader(self) -> WmsReadTransport:
        if self._transport is not None:
            return self._transport
        token = self._secret_store.get(self._credential_target)
        if token is None or not token.strip() or any(character.isspace() for character in token):
            raise PermissionError("WMS credential is unavailable")
        return ReadOnlyWmsTransport(self._base_url, token)

    def _records_sync(
        self,
        mapping: WmsEntityMapping,
        *,
        query_text: str | None = None,
        modified_since: datetime | None = None,
    ) -> list[dict[str, object]]:
        reader = self._reader()
        records: list[dict[str, object]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(mapping.max_pages):
            parameters = {mapping.limit_parameter: str(mapping.page_size)}
            if cursor is not None:
                parameters[mapping.cursor_parameter] = cursor
            if query_text and mapping.search_parameter:
                parameters[mapping.search_parameter] = query_text[:_MAX_QUERY_CHARACTERS]
            if modified_since is not None and mapping.modified_since_parameter:
                parameters[mapping.modified_since_parameter] = (
                    modified_since.astimezone(UTC).isoformat().replace("+00:00", "Z")
                )
            payload = reader.get_json(f"{mapping.endpoint_path}?{urlencode(parameters)}")
            values = payload.get(mapping.items_field, [])
            if not isinstance(values, list):
                raise ValueError("WMS response items must be an array")
            for value in values[: mapping.page_size]:
                if not isinstance(value, dict):
                    continue
                records.append(
                    {
                        field: value[field]
                        for field in mapping.projected_fields
                        if field in value
                    }
                )
            raw_cursor = payload.get(mapping.next_cursor_field)
            if raw_cursor is None or raw_cursor == "":
                break
            if not isinstance(raw_cursor, (str, int)):
                raise ValueError("WMS cursor must be a bounded scalar")
            cursor = str(raw_cursor)
            if len(cursor) > _MAX_CURSOR_CHARACTERS or cursor in seen_cursors:
                raise ValueError("WMS pagination cursor is invalid")
            seen_cursors.add(cursor)
        self._last_success_at = datetime.now(UTC)
        return records

    async def health(self) -> ConnectorHealth:
        enabled = self.auth_enabled or self._transport is not None
        return ConnectorHealth(
            connector_id=self.connector_id,
            kind=self.kind,
            display_name=self.display_name,
            phase=(
                ConnectorPhase.ready
                if enabled and self._last_success_at is not None
                else ConnectorPhase.degraded
                if enabled
                else ConnectorPhase.auth_required
            ),
            auth_enabled=enabled,
            last_success_at=self._last_success_at,
            detail_code=(
                None
                if enabled and self._last_success_at is not None
                else "connection_not_checked"
                if enabled
                else "credential_required"
            ),
            scope_summary=f"{len(self.configuration.entity_mappings)} allow-listed WMS entities",
        )

    def _search_sync(self, query: ContextSearchQuery) -> list[ContextDocument]:
        documents: list[ContextDocument] = []
        terms = [term.casefold() for term in query.text.split() if len(term) > 1][:8]
        for mapping in self.configuration.entity_mappings:
            if query.entity_types and mapping.entity_name not in query.entity_types:
                continue
            records = self._records_sync(mapping, query_text=" ".join(terms))
            for record in records:
                searchable = " ".join(
                    str(record.get(field, "")) for field in mapping.searchable_fields
                ).casefold()
                if terms and not any(term in searchable for term in terms):
                    continue
                key = str(record.get(mapping.key_field, "")).strip()
                display = str(record.get(mapping.display_field, key)).strip() or key
                if not key:
                    continue
                values = {
                    field: str(record[field])[:500]
                    for field in mapping.projected_fields
                    if field in record and record[field] is not None
                }
                documents.append(
                    ContextDocument(
                        document_id=hashlib.sha256(
                            f"{self.connector_id}\0{mapping.entity_name}\0{key}".encode()
                        ).hexdigest(),
                        connector_id=self.connector_id,
                        source_kind=self.kind,
                        record_id=hashlib.sha256(key.encode()).hexdigest(),
                        title=f"{self.display_name}: {display}"[:500],
                        content="\n".join(
                            f"{field}: {value}" for field, value in values.items()
                        ),
                        entity_type=mapping.entity_name,
                        source_reference=_safe_reference(key, display),
                        structured_values=values,
                        allowed_principals=[self._principal_id],
                    )
                )
        return documents[: max(query.limit * 4, query.limit)]

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        return await asyncio.to_thread(self._search_sync, query)

    async def sync(self) -> int:
        self._reader()
        return 0

    async def close(self) -> None:
        return None

    async def metadata_snapshot(self) -> ERPMetadataSnapshot:
        entities = [
            ERPEntityMetadata(
                name=mapping.entity_name,
                fields=[
                    ERPFieldMetadata(
                        name=field,
                        data_type=mapping.field_types.get(field, "unknown"),
                        required=field in {mapping.key_field, mapping.display_field},
                        key=field == mapping.key_field,
                    )
                    for field in mapping.projected_fields
                ],
            )
            for mapping in self.configuration.entity_mappings
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
            source_reference=f"{self.display_name} configured contract",
            metadata_hash=digest,
            retrieved_at=datetime.now(UTC),
            entities=entities,
            environment=self.configuration.environment,
            api_version="configured-read-contract-v1",
        )

    async def quality_records(
        self, *, changed_after: datetime | None = None
    ) -> dict[str, list[dict[str, object]]]:
        values: dict[str, list[dict[str, object]]] = {}
        for mapping in self.configuration.entity_mappings:
            values[mapping.entity_name] = await asyncio.to_thread(
                self._records_sync, mapping, modified_since=changed_after
            )
        return values
