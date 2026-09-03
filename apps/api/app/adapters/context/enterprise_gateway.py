"""Lease-gated enterprise gateway adapter and built-in connector catalog."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from app.adapters.context.encrypted_index import EncryptedContextIndex
from app.domain.context import (
    ConnectorConfiguration,
    ConnectorHealth,
    ConnectorKind,
    ConnectorPhase,
    ContextDocument,
    ContextSearchQuery,
    OpenApiConnectorConfig,
    RepositoryConnectorConfig,
    SftpConnectorConfig,
    SqlConnectorConfig,
    WorkManagementConnectorConfig,
)
from app.domain.enterprise import (
    AccessLease,
    AccessLeaseStatus,
    ConnectorCapabilityManifest,
    ConnectorCatalogEntry,
    GatewaySearchRequest,
    SourceChangeEvent,
    SourceSelection,
    SourceSelectionKind,
    SyncCursor,
)

_TERM = re.compile(r"[\w./:#-]{2,160}", re.UNICODE)
_ENTERPRISE_KINDS = {
    ConnectorKind.github,
    ConnectorKind.gitlab,
    ConnectorKind.jira,
    ConnectorKind.confluence,
    ConnectorKind.azure_devops,
    ConnectorKind.servicenow,
    ConnectorKind.sql,
    ConnectorKind.sftp,
    ConnectorKind.openapi,
}
_LIVE_ONLY_KINDS = {ConnectorKind.sql, ConnectorKind.openapi}


def validate_public_https_origin(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or host in {"localhost", "localhost.localdomain", "0.0.0.0", "::1"}
        or host.startswith("127.")
        or host.startswith("169.254.")
        or host.startswith("10.")
        or host.startswith("192.168.")
        or host.endswith(".local")
    ):
        raise ValueError("enterprise gateway must use an allow-listed public HTTPS origin")
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) > 1 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            raise ValueError("enterprise gateway cannot use a private network literal")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("enterprise gateway cannot use a non-public network address")
    return value.rstrip("/")


def enterprise_connector_catalog() -> list[ConnectorCatalogEntry]:
    definitions = [
        (ConnectorKind.github, "GitHub", "github_app", "encrypted_lease", True),
        (ConnectorKind.gitlab, "GitLab", "oauth_pkce", "encrypted_lease", True),
        (ConnectorKind.jira, "Jira", "oauth_pkce", "encrypted_lease", True),
        (ConnectorKind.confluence, "Confluence", "oauth_pkce", "encrypted_lease", True),
        (ConnectorKind.azure_devops, "Azure DevOps", "oidc", "encrypted_lease", True),
        (ConnectorKind.servicenow, "ServiceNow", "oauth_pkce", "encrypted_lease", True),
        (ConnectorKind.sql, "Approved SQL views", "integrated", "live_only", False),
        (ConnectorKind.sftp, "SFTP document source", "secret", "encrypted_lease", False),
        (ConnectorKind.openapi, "Read-only OpenAPI", "oauth_pkce", "live_only", False),
    ]
    return [
        ConnectorCatalogEntry(
            kind=kind,
            label=label,
            availability="gateway",
            authentication=authentication,
            persistence=persistence,
            supports_webhooks=webhooks,
        )
        for kind, label, authentication, persistence, webhooks in definitions
    ]


def capability_manifest(kind: ConnectorKind) -> ConnectorCapabilityManifest:
    if kind not in _ENTERPRISE_KINDS:
        raise ValueError("connector does not use the enterprise gateway")
    source_kind = {
        ConnectorKind.github: SourceSelectionKind.repository,
        ConnectorKind.gitlab: SourceSelectionKind.repository,
        ConnectorKind.jira: SourceSelectionKind.project,
        ConnectorKind.confluence: SourceSelectionKind.space,
        ConnectorKind.azure_devops: SourceSelectionKind.project,
        ConnectorKind.servicenow: SourceSelectionKind.service,
        ConnectorKind.sql: SourceSelectionKind.table,
        ConnectorKind.sftp: SourceSelectionKind.folder,
        ConnectorKind.openapi: SourceSelectionKind.service,
    }[kind]
    return ConnectorCapabilityManifest(
        connector_kind=kind,
        manifest_version="12.0.0",
        source_types=[source_kind],
        supports_webhooks=kind
        in {
            ConnectorKind.github,
            ConnectorKind.gitlab,
            ConnectorKind.jira,
            ConnectorKind.confluence,
            ConnectorKind.azure_devops,
            ConnectorKind.servicenow,
        },
        content_persistence=("live_only" if kind in _LIVE_ONLY_KINDS else "encrypted_lease"),
    )


class EnterpriseGatewayTransport(Protocol):
    async def issue_lease(
        self, connector_id: str, principal_hash: str, selections: list[SourceSelection]
    ) -> AccessLease: ...

    async def search(self, request: GatewaySearchRequest) -> list[ContextDocument]: ...

    async def changes(
        self, connector_id: str, cursor: SyncCursor | None
    ) -> tuple[list[SourceChangeEvent], SyncCursor]: ...

    async def revoke(self, connector_id: str, principal_hash: str) -> None: ...


class HttpsEnterpriseGatewayTransport:
    """Small HTTPS client; mTLS material is supplied by the managed deployment."""

    def __init__(
        self,
        base_url: str,
        bearer_token: str | Callable[[], str | None] | None = None,
    ) -> None:
        self._base_url = validate_public_https_origin(base_url)
        self._bearer_token = bearer_token
        context = ssl.create_default_context()
        certificate = os.environ.get("MEETING_INTELLIGENCE_GATEWAY_CLIENT_CERT")
        private_key = os.environ.get("MEETING_INTELLIGENCE_GATEWAY_CLIENT_KEY")
        if certificate and private_key:
            context.load_cert_chain(certificate, private_key)
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context), _NoRedirectHandler()
        )

    def _request(self, path: str, body: dict[str, object]) -> object:
        url = urljoin(f"{self._base_url}/", path.lstrip("/"))
        if not url.startswith(f"{self._base_url}/"):
            raise ValueError("gateway request escaped its configured origin")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = self._bearer_token() if callable(self._bearer_token) else self._bearer_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            url,
            data=json.dumps(body, separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=10.0) as response:
                payload = response.read(2_000_001)
                if len(payload) > 2_000_000:
                    raise ValueError("gateway response exceeds the configured bound")
                return json.loads(payload)
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise PermissionError("enterprise gateway authorization was revoked") from error
            raise OSError("enterprise gateway is unavailable") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise OSError("enterprise gateway is unavailable") from error

    async def issue_lease(
        self, connector_id: str, principal_hash: str, selections: list[SourceSelection]
    ) -> AccessLease:
        payload = await asyncio.to_thread(
            self._request,
            "/v1/leases",
            {
                "connector_id": connector_id,
                "selections": [value.model_dump(mode="json") for value in selections],
            },
        )
        return AccessLease.model_validate(payload)

    async def search(self, request: GatewaySearchRequest) -> list[ContextDocument]:
        payload = await asyncio.to_thread(
            self._request, "/v1/search", request.model_dump(mode="json")
        )
        if not isinstance(payload, list):
            raise ValueError("gateway search response is invalid")
        return [ContextDocument.model_validate(value) for value in payload]

    async def changes(
        self, connector_id: str, cursor: SyncCursor | None
    ) -> tuple[list[SourceChangeEvent], SyncCursor]:
        payload = await asyncio.to_thread(
            self._request,
            "/v1/deltas",
            {
                "connector_id": connector_id,
                "cursor": cursor.model_dump(mode="json") if cursor else None,
            },
        )
        if not isinstance(payload, dict):
            raise ValueError("gateway delta response is invalid")
        return (
            [SourceChangeEvent.model_validate(value) for value in payload.get("events", [])],
            SyncCursor.model_validate(payload["cursor"]),
        )

    async def revoke(self, connector_id: str, principal_hash: str) -> None:
        await asyncio.to_thread(
            self._request,
            "/v1/revoke",
            {"connector_id": connector_id},
        )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "redirect blocked", headers, fp)


def _gateway_url(configuration: ConnectorConfiguration) -> str | None:
    if isinstance(
        configuration,
        (
            RepositoryConnectorConfig,
            WorkManagementConnectorConfig,
            SqlConnectorConfig,
            SftpConnectorConfig,
            OpenApiConnectorConfig,
        ),
    ):
        return configuration.gateway_url
    return None


class GatewayBackedContextProvider:
    def __init__(
        self,
        *,
        connector_id: str,
        display_name: str,
        kind: ConnectorKind,
        configuration: ConnectorConfiguration,
        principal_id: str,
        index: EncryptedContextIndex,
        transport: EnterpriseGatewayTransport | None = None,
        bearer_token: Callable[[], str | None] | None = None,
    ) -> None:
        if kind not in _ENTERPRISE_KINDS:
            raise ValueError("unsupported enterprise connector kind")
        self._connector_id = connector_id
        self._display_name = display_name
        self._kind = kind
        self._configuration = configuration
        self._principal_id = principal_id
        self._principal_hash = hashlib.sha256(principal_id.encode()).hexdigest()
        self._index = index
        gateway_url = _gateway_url(configuration)
        self._transport = transport or (
            HttpsEnterpriseGatewayTransport(gateway_url, bearer_token) if gateway_url else None
        )

    @property
    def connector_id(self) -> str:
        return self._connector_id

    @property
    def kind(self) -> ConnectorKind:
        return self._kind

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def auth_enabled(self) -> bool:
        return self._transport is not None

    async def health(self) -> ConnectorHealth:
        lease = await asyncio.to_thread(self._index.access_lease, self._connector_id)
        if self._transport is None:
            phase, detail = ConnectorPhase.auth_required, "gateway_not_configured"
        elif lease is None or not lease.is_active():
            phase, detail = ConnectorPhase.auth_required, "access_lease_expired"
        else:
            phase, detail = ConnectorPhase.ready, None
        return ConnectorHealth(
            connector_id=self._connector_id,
            kind=self._kind,
            display_name=self._display_name,
            phase=phase,
            auth_enabled=self.auth_enabled,
            detail_code=detail,
            scope_summary=(
                f"{len(self._index.source_selections(self._connector_id))} selected sources"
            ),
            indexed_documents=self._index.document_count(self._connector_id),
        )

    async def refresh_lease(self) -> AccessLease:
        if self._transport is None:
            raise PermissionError("enterprise gateway is not configured")
        selections = await asyncio.to_thread(
            self._index.source_selections, self._connector_id
        )
        lease = await self._transport.issue_lease(
            self._connector_id, self._principal_hash, selections
        )
        if lease.connector_id != self._connector_id:
            raise PermissionError("gateway issued a lease for another connector")
        await asyncio.to_thread(self._index.save_access_lease, lease)
        return lease

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        lease = await asyncio.to_thread(self._index.access_lease, self._connector_id)
        if lease is None or not lease.is_active():
            raise PermissionError("enterprise access lease expired")
        local = (
            []
            if self._kind in _LIVE_ONLY_KINDS or query.source_selection_ids
            else await asyncio.to_thread(self._index.documents, self._connector_id)
        )
        if self._transport is None:
            return local
        terms = list(dict.fromkeys(_TERM.findall(query.text)))[:32]
        if not terms:
            return local
        try:
            documents = await self._transport.search(
                GatewaySearchRequest(
                    principal_hash=lease.principal_hash,
                    connector_id=self._connector_id,
                    canonical_terms=terms,
                    business_keys=[
                        value
                        for values in query.known_values.values()
                        for value in values
                    ][:32],
                    filters={
                        "entity_type": ",".join(query.entity_types)[:160],
                        "source_selection_ids": ",".join(query.source_selection_ids)[:2_000],
                    },
                    limit=query.limit,
                )
            )
        except PermissionError:
            await asyncio.to_thread(self._index.revoke_access_lease, self._connector_id)
            raise
        safe = [
            document.model_copy(update={"allowed_principals": [self._principal_id]})
            for document in documents
            if document.connector_id == self._connector_id
            and document.source_kind is self._kind
            and (
                self._principal_id in document.allowed_principals
                or lease.principal_hash in document.allowed_principals
            )
        ]
        if self._kind not in _LIVE_ONLY_KINDS and safe:
            await asyncio.to_thread(self._index.upsert_documents, self._connector_id, safe)
        by_id = {document.document_id: document for document in [*safe, *local]}
        return list(by_id.values())

    async def sync(self) -> int:
        lease = await asyncio.to_thread(self._index.access_lease, self._connector_id)
        if lease is None or not lease.is_active() or self._transport is None:
            raise PermissionError("enterprise access lease expired")
        cursors = await asyncio.to_thread(self._index.sync_cursors, self._connector_id)
        cursor = cursors[0] if cursors else None
        events, updated = await self._transport.changes(self._connector_id, cursor)
        await asyncio.to_thread(self._index.save_sync_cursor, updated)
        return len(events)

    async def revoke(self) -> None:
        if self._transport is not None:
            await self._transport.revoke(self._connector_id, self._principal_hash)
        await asyncio.to_thread(self._index.revoke_access_lease, self._connector_id)

    async def close(self) -> None:
        return None


def revoked_lease(lease: AccessLease) -> AccessLease:
    return lease.model_copy(update={"status": AccessLeaseStatus.revoked})
