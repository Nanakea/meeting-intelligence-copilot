"""mTLS/OIDC composition edge for company-managed read-only connectors."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode, urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.domain.context import ConnectorKind, ContextDocument
from app.domain.enterprise import (
    AccessLease,
    GatewaySearchRequest,
    SourceChangeEvent,
    SourceSelection,
    SyncCursor,
)


class _GatewayContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LeaseRequest(_GatewayContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    selections: list[SourceSelection] = Field(default_factory=list, max_length=200)


class DeltaRequest(_GatewayContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    cursor: SyncCursor | None = None


class DeltaResponse(_GatewayContract):
    events: list[SourceChangeEvent] = Field(default_factory=list, max_length=2_000)
    cursor: SyncCursor


class RevokeRequest(_GatewayContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")


class GatewayAuthenticator(Protocol):
    async def authenticate(self, request: Request) -> str: ...


class GatewayRepository(Protocol):
    def acl_digest(
        self, connector_id: str, principal_hash: str, selections: list[SourceSelection]
    ) -> str: ...

    def search(self, request: GatewaySearchRequest) -> list[ContextDocument]: ...

    def changes(
        self, connector_id: str, cursor: SyncCursor | None
    ) -> tuple[list[SourceChangeEvent], SyncCursor]: ...

    def ingest_change(self, kind: ConnectorKind, payload: bytes) -> SourceChangeEvent: ...


class DenyAllAuthenticator:
    async def authenticate(self, request: Request) -> str:
        del request
        raise HTTPException(status_code=401, detail="gateway identity is not configured")


class OidcIntrospectionAuthenticator:
    """Validate delegated OIDC tokens; the listener independently requires mTLS."""

    def __init__(
        self,
        *,
        introspection_url: str,
        client_id: str,
        client_secret: str,
        expected_issuer: str,
        expected_audience: str,
    ) -> None:
        for value in (introspection_url, expected_issuer):
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("OIDC endpoints must be credential-free HTTPS URLs")
        self._url = introspection_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._issuer = expected_issuer.rstrip("/")
        self._audience = expected_audience
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    @classmethod
    def from_env(cls) -> OidcIntrospectionAuthenticator:
        values = {
            "introspection_url": os.environ.get(
                "MEETING_INTELLIGENCE_GATEWAY_OIDC_INTROSPECTION"
            ),
            "client_id": os.environ.get("MEETING_INTELLIGENCE_GATEWAY_OIDC_CLIENT_ID"),
            "client_secret": os.environ.get(
                "MEETING_INTELLIGENCE_GATEWAY_OIDC_CLIENT_SECRET"
            ),
            "expected_issuer": os.environ.get(
                "MEETING_INTELLIGENCE_GATEWAY_OIDC_ISSUER"
            ),
            "expected_audience": os.environ.get(
                "MEETING_INTELLIGENCE_GATEWAY_OIDC_AUDIENCE"
            ),
        }
        if any(value is None for value in values.values()):
            raise ValueError("gateway OIDC introspection configuration is incomplete")
        return cls(**values)  # type: ignore[arg-type]

    def _introspect(self, token: str) -> dict[str, object]:
        import base64

        credentials = f"{self._client_id}:{self._client_secret}".encode()
        request = urllib.request.Request(
            self._url,
            data=urlencode({"token": token}).encode(),
            headers={
                "Authorization": (
                    f"Basic {base64.b64encode(credentials).decode('ascii')}"
                ),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=5.0) as response:
                payload = response.read(64_001)
                if len(payload) > 64_000:
                    raise ValueError("OIDC introspection response exceeds its bound")
                value = json.loads(payload)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise PermissionError("OIDC token validation is unavailable") from error
        if not isinstance(value, dict):
            raise PermissionError("OIDC token response is invalid")
        return value

    async def authenticate(self, request: Request) -> str:
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not token or len(token) > 8_192:
            raise HTTPException(status_code=401, detail="OIDC bearer token required")
        try:
            value = await asyncio.to_thread(self._introspect, token)
        except PermissionError as error:
            raise HTTPException(
                status_code=401, detail="OIDC token validation failed"
            ) from error
        audience = value.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        subject = value.get("sub")
        issuer = str(value.get("iss", "")).rstrip("/")
        if (
            value.get("active") is not True
            or issuer != self._issuer
            or self._audience not in audiences
            or not isinstance(subject, str)
            or not subject
        ):
            raise HTTPException(status_code=401, detail="OIDC token is not active")
        return hashlib.sha256(f"{issuer}\0{subject}".encode()).hexdigest()


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "redirect blocked", headers, fp)


class EmptyGatewayRepository:
    """Fail-closed default; company deployments compose approved connector hosts."""

    def acl_digest(
        self, connector_id: str, principal_hash: str, selections: list[SourceSelection]
    ) -> str:
        del connector_id, principal_hash, selections
        raise PermissionError("gateway connector repository is not configured")

    def search(self, request: GatewaySearchRequest) -> list[ContextDocument]:
        del request
        raise PermissionError("gateway connector repository is not configured")

    def changes(
        self, connector_id: str, cursor: SyncCursor | None
    ) -> tuple[list[SourceChangeEvent], SyncCursor]:
        del connector_id, cursor
        raise PermissionError("gateway connector repository is not configured")

    def ingest_change(self, kind: ConnectorKind, payload: bytes) -> SourceChangeEvent:
        del kind, payload
        raise PermissionError("gateway connector repository is not configured")


def create_gateway_app(
    *,
    authenticator: GatewayAuthenticator | None = None,
    repository: GatewayRepository | None = None,
) -> FastAPI:
    identity = authenticator or DenyAllAuthenticator()
    storage = repository or EmptyGatewayRepository()
    leases: dict[tuple[str, str], AccessLease] = {}
    seen_webhooks: dict[str, datetime] = {}
    rate_windows: dict[str, list[datetime]] = defaultdict(list)

    app = FastAPI(
        title="Meeting Intelligence Enterprise Connector Gateway",
        version="0.6.1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    async def require_identity(request: Request) -> str:
        principal_hash = await identity.authenticate(request)
        if len(principal_hash) != 64 or any(
            value not in "0123456789abcdef" for value in principal_hash
        ):
            raise HTTPException(status_code=401, detail="gateway identity is invalid")
        now = datetime.now(UTC)
        window = [
            value
            for value in rate_windows[principal_hash]
            if now - value < timedelta(minutes=1)
        ]
        if len(window) >= 120:
            raise HTTPException(status_code=429, detail="gateway rate limit exceeded")
        window.append(now)
        rate_windows[principal_hash] = window
        return principal_hash

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "product": "meeting-intelligence-gateway", "api_version": 13}

    @app.post("/v1/leases", response_model=AccessLease)
    async def issue_lease(
        body: LeaseRequest, principal_hash: str = Depends(require_identity)
    ) -> AccessLease:
        try:
            digest = storage.acl_digest(
                body.connector_id, principal_hash, body.selections
            )
        except PermissionError as error:
            raise HTTPException(status_code=403, detail="source access denied") from error
        now = datetime.now(UTC)
        lease = AccessLease(
            lease_id=secrets.token_hex(16),
            connector_id=body.connector_id,
            principal_hash=principal_hash,
            acl_digest=digest,
            selection_ids=[value.selection_id for value in body.selections if value.enabled],
            issued_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        leases[(body.connector_id, principal_hash)] = lease
        return lease

    @app.post("/v1/search", response_model=list[ContextDocument])
    async def search(
        body: GatewaySearchRequest,
        principal_hash: str = Depends(require_identity),
    ) -> list[ContextDocument]:
        if not hmac.compare_digest(body.principal_hash, principal_hash):
            raise HTTPException(status_code=403, detail="principal binding mismatch")
        lease = leases.get((body.connector_id, principal_hash))
        if lease is None or not lease.is_active():
            raise HTTPException(status_code=401, detail="active access lease required")
        try:
            documents = storage.search(body)
        except PermissionError as error:
            leases.pop((body.connector_id, principal_hash), None)
            raise HTTPException(status_code=403, detail="source access revoked") from error
        return [
            document
            for document in documents[: body.limit]
            if principal_hash in document.allowed_principals
            and document.connector_id == body.connector_id
        ]

    @app.post("/v1/deltas", response_model=DeltaResponse)
    async def deltas(
        body: DeltaRequest, principal_hash: str = Depends(require_identity)
    ) -> DeltaResponse:
        lease = leases.get((body.connector_id, principal_hash))
        if lease is None or not lease.is_active():
            raise HTTPException(status_code=401, detail="active access lease required")
        try:
            events, cursor = storage.changes(body.connector_id, body.cursor)
        except PermissionError as error:
            leases.pop((body.connector_id, principal_hash), None)
            raise HTTPException(status_code=403, detail="source access revoked") from error
        return DeltaResponse(events=events, cursor=cursor)

    @app.post("/v1/revoke")
    async def revoke(
        body: RevokeRequest, principal_hash: str = Depends(require_identity)
    ) -> dict[str, bool]:
        return {"revoked": leases.pop((body.connector_id, principal_hash), None) is not None}

    @app.post("/v1/webhooks/{kind}", response_model=SourceChangeEvent)
    async def webhook(
        kind: ConnectorKind,
        request: Request,
        signature: str | None = Header(default=None, alias="X-Connector-Signature"),
        delivery_id: str | None = Header(default=None, alias="X-Connector-Delivery"),
    ) -> SourceChangeEvent:
        if kind not in {
            ConnectorKind.github,
            ConnectorKind.gitlab,
            ConnectorKind.jira,
            ConnectorKind.confluence,
            ConnectorKind.azure_devops,
            ConnectorKind.servicenow,
        }:
            raise HTTPException(status_code=404, detail="webhooks are not supported")
        payload = await request.body()
        if len(payload) > 2_000_000:
            raise HTTPException(status_code=413, detail="webhook payload is too large")
        secret = os.environ.get(f"MEETING_INTELLIGENCE_{kind.value.upper()}_WEBHOOK_SECRET")
        if not secret or not signature or not delivery_id:
            raise HTTPException(status_code=401, detail="signed webhook required")
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        supplied = signature.removeprefix("sha256=")
        if not hmac.compare_digest(expected, supplied):
            raise HTTPException(status_code=401, detail="webhook signature is invalid")
        now = datetime.now(UTC)
        if delivery_id in seen_webhooks:
            raise HTTPException(status_code=409, detail="duplicate webhook delivery")
        seen_webhooks[delivery_id] = now
        for key, value in list(seen_webhooks.items()):
            if now - value > timedelta(hours=24):
                del seen_webhooks[key]
        try:
            return storage.ingest_change(kind, payload)
        except PermissionError as error:
            raise HTTPException(
                status_code=503, detail="connector repository unavailable"
            ) from error

    return app


app = create_gateway_app()
