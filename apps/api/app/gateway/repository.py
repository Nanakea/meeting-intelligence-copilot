"""Administrator-configured, read-only gateway connector repository."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import socket
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.adapters.context.enterprise_gateway import validate_public_https_origin
from app.domain.context import ConnectorKind, ContextDocument
from app.domain.enterprise import (
    GatewaySearchRequest,
    SourceChangeEvent,
    SourceSelection,
    SyncCursor,
)
from app.gateway.connector_plans import ReadOnlyRequest, build_search_plan

_HTTP_KINDS = {
    ConnectorKind.github,
    ConnectorKind.gitlab,
    ConnectorKind.jira,
    ConnectorKind.confluence,
    ConnectorKind.azure_devops,
    ConnectorKind.servicenow,
    ConnectorKind.openapi,
}


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GatewaySourceBinding(_Config):
    selection_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    external_id: str = Field(min_length=1, max_length=512)
    label: str = Field(min_length=1, max_length=160)
    allowed_principal_hashes: list[str] = Field(min_length=1, max_length=200)

    @field_validator("allowed_principal_hashes")
    @classmethod
    def principals_are_hashes(cls, values: list[str]) -> list[str]:
        if any(
            value != "*"
            and (
                len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            )
            for value in values
        ):
            raise ValueError("gateway source principals must be SHA-256 hashes")
        return list(dict.fromkeys(values))


class GatewayConnectorBinding(_Config):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    kind: ConnectorKind
    base_url: str = Field(min_length=1, max_length=2_048)
    credential_environment_variable: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    sources: list[GatewaySourceBinding] = Field(min_length=1, max_length=200)
    maximum_requests_per_search: int = Field(default=20, ge=1, le=50)

    @field_validator("base_url")
    @classmethod
    def base_url_is_public_https(cls, value: str) -> str:
        return validate_public_https_origin(value)

    @model_validator(mode="after")
    def connector_is_supported(self) -> GatewayConnectorBinding:
        if self.kind not in _HTTP_KINDS:
            raise ValueError("connector requires an approved out-of-process adapter")
        identifiers = [source.selection_id for source in self.sources]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("gateway source selection IDs must be unique")
        return self


class GatewayRepositoryConfiguration(_Config):
    schema_version: int = Field(default=12, ge=12, le=12)
    connectors: list[GatewayConnectorBinding] = Field(default_factory=list, max_length=200)


class ReadExecutor(Protocol):
    def __call__(self, request: ReadOnlyRequest, credential: str) -> object: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "redirect blocked", headers, fp)


def execute_read(request: ReadOnlyRequest, credential: str) -> object:
    if request.method not in {"GET", "HEAD"}:
        raise PermissionError("gateway connector operation is not read-only")
    host = urlsplit(request.url).hostname
    if not host:
        raise ValueError("gateway connector host is unavailable")
    try:
        addresses = {
            ipaddress.ip_address(result[4][0])
            for result in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as error:
        raise OSError("gateway connector host could not be resolved") from error
    if not addresses or any(not address.is_global for address in addresses):
        raise PermissionError("gateway connector resolved to a non-public network")
    opener = urllib.request.build_opener(_NoRedirect())
    wire_request = urllib.request.Request(
        request.url,
        method=request.method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {credential}",
            "User-Agent": "meeting-intelligence-gateway/0.6.1",
        },
    )
    try:
        with opener.open(wire_request, timeout=8.0) as response:
            payload = response.read(request.maximum_response_bytes + 1)
    except (urllib.error.URLError, TimeoutError) as error:
        raise OSError("gateway connector request failed") from error
    if len(payload) > request.maximum_response_bytes:
        raise ValueError("gateway connector response exceeds its bound")
    value = json.loads(payload)
    if not isinstance(value, (dict, list)):
        raise ValueError("gateway connector response must be JSON data")
    return value


def _records(value: object) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("items", "values", "results", "result", "issues"):
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return [value]


def _string(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
        fields = record.get("fields")
        if isinstance(fields, dict):
            value = fields.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())
    return None


class ConfiguredGatewayRepository:
    """Federates built-in read plans without loading arbitrary connector code."""

    def __init__(
        self,
        configuration: GatewayRepositoryConfiguration,
        *,
        executor: ReadExecutor = execute_read,
    ) -> None:
        self._connectors = {value.connector_id: value for value in configuration.connectors}
        if len(self._connectors) != len(configuration.connectors):
            raise ValueError("gateway connector IDs must be unique")
        self._executor = executor
        self._active_sources: dict[tuple[str, str], list[GatewaySourceBinding]] = {}
        self._events: dict[str, list[SourceChangeEvent]] = {
            connector_id: [] for connector_id in self._connectors
        }
        self._lock = threading.RLock()

    @classmethod
    def from_file(cls, path: Path) -> ConfiguredGatewayRepository:
        value = json.loads(path.read_text(encoding="utf-8"))
        return cls(GatewayRepositoryConfiguration.model_validate(value))

    def acl_digest(
        self, connector_id: str, principal_hash: str, selections: list[SourceSelection]
    ) -> str:
        connector = self._connectors.get(connector_id)
        if connector is None:
            raise PermissionError("gateway connector is not configured")
        configured = {source.selection_id: source for source in connector.sources}
        active: list[GatewaySourceBinding] = []
        for selection in selections:
            if selection.connector_id != connector_id or not selection.enabled:
                continue
            source = configured.get(selection.selection_id)
            if source is None or (
                "*" not in source.allowed_principal_hashes
                and principal_hash not in source.allowed_principal_hashes
            ):
                raise PermissionError("gateway source is not authorized")
            active.append(source)
        if not active:
            raise PermissionError("gateway requires at least one selected source")
        active.sort(key=lambda value: value.selection_id)
        with self._lock:
            self._active_sources[(connector_id, principal_hash)] = active
        return hashlib.sha256(
            json.dumps(
                [(value.selection_id, value.external_id) for value in active],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    def search(self, request: GatewaySearchRequest) -> list[ContextDocument]:
        connector = self._connectors.get(request.connector_id)
        if connector is None:
            raise PermissionError("gateway connector is not configured")
        with self._lock:
            sources = list(
                self._active_sources.get((request.connector_id, request.principal_hash), [])
            )
        requested_selection_ids = {
            value
            for value in request.filters.get("source_selection_ids", "").split(",")
            if value
        }
        if requested_selection_ids:
            sources = [
                source for source in sources if source.selection_id in requested_selection_ids
            ]
            if not sources:
                return []
        if not sources:
            raise PermissionError("gateway source authorization was revoked")
        credential = os.environ.get(connector.credential_environment_variable)
        if not credential or len(credential) > 16_384:
            raise PermissionError("gateway connector credential is unavailable")
        plans = build_search_plan(
            kind=connector.kind,
            base_url=connector.base_url,
            terms=request.canonical_terms,
            selected_external_ids=[source.external_id for source in sources],
            limit=request.limit,
        )[: connector.maximum_requests_per_search]
        documents: list[ContextDocument] = []
        for plan in plans:
            payload = self._executor(plan, credential)
            for record in _records(payload):
                document = self._document(connector, request.principal_hash, plan, record)
                if document is not None:
                    documents.append(document)
                if len(documents) >= request.limit:
                    return documents
        return documents

    def _document(
        self,
        connector: GatewayConnectorBinding,
        principal_hash: str,
        plan: ReadOnlyRequest,
        record: dict[str, Any],
    ) -> ContextDocument | None:
        title = _string(record, "title", "name", "summary", "path", "filename")
        content = _string(record, "body", "text", "content", "description", "message")
        if title is None and content is None:
            return None
        title = (title or "Enterprise record")[:500]
        content = (content or title)[:100_000]
        raw_reference = _string(record, "number", "key", "path", "title", "name") or title
        source_reference = raw_reference[:256]
        canonical = json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
        record_hash = hashlib.sha256(canonical.encode()).hexdigest()
        uri = _string(record, "html_url", "web_url", "url", "self")
        if uri:
            parsed = urlsplit(uri)
            origin = urlsplit(connector.base_url)
            if parsed.scheme != "https" or parsed.netloc.casefold() != origin.netloc.casefold():
                uri = None
        return ContextDocument(
            document_id=record_hash[:32],
            connector_id=connector.connector_id,
            source_kind=connector.kind,
            record_id=record_hash,
            title=title,
            content=content,
            uri=uri,
            entity_type=plan.operation_id[:64],
            source_reference=source_reference,
            structured_values={"operation": plan.operation_id},
            allowed_principals=[principal_hash],
        )

    def changes(
        self, connector_id: str, cursor: SyncCursor | None
    ) -> tuple[list[SourceChangeEvent], SyncCursor]:
        connector = self._connectors.get(connector_id)
        if connector is None:
            raise PermissionError("gateway connector is not configured")
        revision = cursor.revision if cursor is not None else 0
        with self._lock:
            events = list(self._events.get(connector_id, []))[revision : revision + 2_000]
        updated_revision = revision + len(events)
        selection_id = (
            cursor.source_selection_id
            if cursor is not None
            else connector.sources[0].selection_id
        )
        now = datetime.now(UTC)
        return events, SyncCursor(
            connector_id=connector_id,
            source_selection_id=selection_id,
            revision=updated_revision,
            cursor_hash=hashlib.sha256(f"{connector_id}\0{updated_revision}".encode()).hexdigest(),
            last_success_at=now,
            phase="ready",
        )

    def ingest_change(self, kind: ConnectorKind, payload: bytes) -> SourceChangeEvent:
        matches = [value for value in self._connectors.values() if value.kind is kind]
        if len(matches) != 1:
            raise PermissionError("webhook connector is ambiguous or unavailable")
        connector = matches[0]
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise PermissionError("webhook payload is invalid") from error
        if not isinstance(value, dict):
            raise PermissionError("webhook payload is invalid")
        digest = hashlib.sha256(payload).hexdigest()
        event = SourceChangeEvent(
            event_id=secrets.token_hex(16),
            connector_id=connector.connector_id,
            source_selection_id=connector.sources[0].selection_id,
            operation="reconcile",
            resource_hash=digest,
            source_revision=digest[:32],
            occurred_at=datetime.now(UTC),
        )
        with self._lock:
            self._events[connector.connector_id].append(event)
            self._events[connector.connector_id] = self._events[connector.connector_id][-10_000:]
        return event
