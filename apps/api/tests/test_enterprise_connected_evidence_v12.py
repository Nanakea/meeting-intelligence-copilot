from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.adapters.context.encrypted_index import EncryptedContextIndex
from app.adapters.context.enterprise_gateway import (
    GatewayBackedContextProvider,
    capability_manifest,
    enterprise_connector_catalog,
    validate_public_https_origin,
)
from app.adapters.context.enterprise_oidc import EnterpriseOidcPkceManager
from app.adapters.evidence.code_chunks import (
    artifact_sections_from_code,
    chunk_repository_text,
    repository_path_is_indexable,
)
from app.domain.context import (
    ConnectorKind,
    ContextDocument,
    ContextSearchQuery,
    RepositoryConnectorConfig,
)
from app.domain.enterprise import (
    AccessLease,
    GatewaySearchRequest,
    SourceChangeEvent,
    SourceSelection,
    SourceSelectionKind,
    SyncCursor,
)
from app.domain.integrations import ExternalActionDraft, ExternalActionKind
from app.gateway.connector_plans import build_search_plan
from app.gateway.main import GatewayRepository, create_gateway_app
from app.gateway.repository import (
    ConfiguredGatewayRepository,
    GatewayConnectorBinding,
    GatewayRepositoryConfiguration,
    GatewaySourceBinding,
)
from app.services.external_action_exports import render_external_action_export


class XorProtector:
    def protect(self, value: bytes) -> bytes:
        return bytes(item ^ 0x5A for item in value)

    def unprotect(self, value: bytes) -> bytes:
        return bytes(item ^ 0x5A for item in value)


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def put(self, target: str, secret: str) -> None:
        self.values[target] = secret

    def get(self, target: str) -> str | None:
        return self.values.get(target)

    def delete(self, target: str) -> bool:
        return self.values.pop(target, None) is not None

def _selection() -> SourceSelection:
    return SourceSelection(
        selection_id="orders-api",
        connector_id="github-work",
        kind=SourceSelectionKind.repository,
        label="Architecture / Orders API",
    )


def _lease(*, expired: bool = False) -> AccessLease:
    issued = datetime.now(UTC) - (timedelta(minutes=16) if expired else timedelta())
    return AccessLease(
        lease_id="a" * 32,
        connector_id="github-work",
        principal_hash=hashlib.sha256(b"windows-user:alice").hexdigest(),
        acl_digest="b" * 64,
        selection_ids=["orders-api"],
        issued_at=issued,
        expires_at=issued + timedelta(minutes=15),
    )


def _document(principal: str) -> ContextDocument:
    return ContextDocument(
        document_id="repo:orders:src/order.ts:abc",
        connector_id="github-work",
        source_kind=ConnectorKind.github,
        record_id="opaque-record",
        title="OrderService validates subsidiary and currency",
        content="OrderService rejects a currency outside the subsidiary code list.",
        uri="https://github.example/architecture/orders/blob/abc/src/order.ts",
        entity_type="repository_symbol",
        source_reference="architecture/orders@abc:src/order.ts#L10-L24",
        allowed_principals=[principal],
    )


class FakeGatewayTransport:
    def __init__(self) -> None:
        self.search_calls = 0

    async def issue_lease(self, connector_id, principal_hash, selections):
        assert connector_id == "github-work"
        assert [value.selection_id for value in selections] == ["orders-api"]
        now = datetime.now(UTC)
        return AccessLease(
            lease_id="c" * 32,
            connector_id=connector_id,
            principal_hash=principal_hash,
            acl_digest="d" * 64,
            selection_ids=["orders-api"],
            issued_at=now,
            expires_at=now + timedelta(minutes=15),
        )

    async def search(self, request: GatewaySearchRequest):
        self.search_calls += 1
        assert "OrderService" in request.canonical_terms
        return [_document(request.principal_hash)]

    async def changes(self, connector_id, cursor):
        return [], SyncCursor(
            connector_id=connector_id,
            source_selection_id="orders-api",
            revision=(cursor.revision + 1 if cursor else 1),
        )

    async def revoke(self, connector_id, principal_hash):
        assert connector_id == "github-work"
        assert len(principal_hash) == 64


class RevokedGatewayTransport(FakeGatewayTransport):
    async def search(self, request):
        del request
        raise PermissionError("revoked")


def _provider(index: EncryptedContextIndex, transport: FakeGatewayTransport):
    return GatewayBackedContextProvider(
        connector_id="github-work",
        display_name="GitHub Architecture",
        kind=ConnectorKind.github,
        configuration=RepositoryConnectorConfig(
            kind="github",
            base_url="https://github.example/api/v3",
            client_id="github-app-client",
            gateway_url="https://gateway.example",
            selected_source_ids=["orders-api"],
        ),
        principal_id="windows-user:alice",
        index=index,
        transport=transport,
    )


def test_catalog_and_manifests_are_read_only() -> None:
    kinds = {entry.kind for entry in enterprise_connector_catalog()}
    assert kinds == {
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
    for kind in kinds:
        manifest = capability_manifest(kind)
        assert set(manifest.read_operations) <= {"GET", "HEAD"}


def test_origins_and_request_plans_block_unsafe_network_access() -> None:
    for value in (
        "http://github.example",
        "https://127.0.0.1",
        "https://user:secret@example.com",
    ):
        with pytest.raises(ValueError):
            validate_public_https_origin(value)
    plans = build_search_plan(
        kind=ConnectorKind.github,
        base_url="https://api.github.com",
        terms=["OrderService", "currency"],
        selected_external_ids=["architecture/orders"],
        limit=10,
    )
    assert {value.method for value in plans} == {"GET"}
    assert all(value.url.startswith("https://api.github.com/") for value in plans)


def test_scope_and_lease_are_encrypted_and_revocation_purges(tmp_path: Path) -> None:
    index = EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector())
    selection = _selection()
    index.save_source_selection(selection, "architecture/orders")
    assert index.source_selections("github-work") == [selection]
    assert index.resolve_source_selections("github-work") == {
        "orders-api": "architecture/orders"
    }
    index.save_access_lease(_lease())
    index.upsert_documents("github-work", [_document("windows-user:alice")])
    raw = (tmp_path / "context.sqlite3").read_bytes()
    assert b"architecture/orders" not in raw
    assert b"OrderService rejects" not in raw
    assert index.revoke_access_lease("github-work") is True
    assert index.access_lease("github-work") is None
    assert index.documents("github-work") == []


def test_provider_requires_lease_and_caches_only_after_acl_check(tmp_path: Path) -> None:
    index = EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector())
    index.save_source_selection(_selection(), "architecture/orders")
    transport = FakeGatewayTransport()
    provider = _provider(index, transport)
    query = ContextSearchQuery(
        text="OrderService currency", principal_id="windows-user:alice"
    )
    with pytest.raises(PermissionError):
        asyncio.run(provider.search(query))
    assert asyncio.run(provider.refresh_lease()).is_active()
    documents = asyncio.run(provider.search(query))
    assert documents[0].allowed_principals == ["windows-user:alice"]
    assert index.document_count("github-work") == 1
    index.save_access_lease(_lease(expired=True))
    with pytest.raises(PermissionError):
        asyncio.run(provider.search(query))
    assert transport.search_calls == 1


def test_gateway_revocation_immediately_purges_encrypted_index(tmp_path: Path) -> None:
    index = EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector())
    index.save_source_selection(_selection(), "architecture/orders")
    index.save_access_lease(_lease())
    index.upsert_documents(
        "github-work", [_document(hashlib.sha256(b"windows-user:alice").hexdigest())]
    )
    provider = _provider(index, RevokedGatewayTransport())
    with pytest.raises(PermissionError):
        asyncio.run(
            provider.search(
                ContextSearchQuery(
                    text="OrderService currency",
                    principal_id="windows-user:alice",
                )
            )
        )
    assert index.document_count("github-work") == 0
    assert index.access_lease("github-work") is None


class StaticAuthenticator:
    principal = "e" * 64

    async def authenticate(self, request: Request) -> str:
        assert request.headers["Authorization"] == "Bearer opaque"
        return self.principal


class FakeGatewayRepository(GatewayRepository):
    def __init__(self) -> None:
        self.webhook_calls = 0

    def acl_digest(self, connector_id, principal_hash, selections):
        assert connector_id == "github-work"
        assert principal_hash == StaticAuthenticator.principal
        assert selections[0].selection_id == "orders-api"
        return "f" * 64

    def search(self, request):
        return [_document(request.principal_hash)]

    def changes(self, connector_id, cursor):
        return [], SyncCursor(
            connector_id=connector_id,
            source_selection_id="orders-api",
            revision=1,
        )

    def ingest_change(self, kind, payload):
        self.webhook_calls += 1
        digest = hashlib.sha256(payload).hexdigest()
        return SourceChangeEvent(
            event_id=digest[:32],
            connector_id="github-work",
            source_selection_id="orders-api",
            operation="upsert",
            resource_hash=digest,
            occurred_at=datetime.now(UTC),
        )


def test_gateway_binds_identity_and_rejects_webhook_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeGatewayRepository()
    client = TestClient(
        create_gateway_app(authenticator=StaticAuthenticator(), repository=repository)
    )
    headers = {"Authorization": "Bearer opaque"}
    lease_response = client.post(
        "/v1/leases",
        headers=headers,
        json={
            "connector_id": "github-work",
            "selections": [_selection().model_dump(mode="json")],
        },
    )
    assert lease_response.status_code == 200
    search_response = client.post(
        "/v1/search",
        headers=headers,
        json={
            "principal_hash": StaticAuthenticator.principal,
            "connector_id": "github-work",
            "canonical_terms": ["OrderService"],
            "business_keys": [],
            "filters": {},
            "limit": 5,
        },
    )
    assert search_response.status_code == 200
    payload = json.dumps({"repository": "architecture/orders"}).encode()
    secret = "webhook-secret"
    monkeypatch.setenv("MEETING_INTELLIGENCE_GITHUB_WEBHOOK_SECRET", secret)
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    webhook_headers = {
        "X-Connector-Signature": f"sha256={signature}",
        "X-Connector-Delivery": "delivery-1",
        "Content-Type": "application/json",
    }
    route = "/v1/webhooks/github"
    assert client.post(route, content=payload, headers=webhook_headers).status_code == 200
    assert client.post(route, content=payload, headers=webhook_headers).status_code == 409
    assert repository.webhook_calls == 1


def test_code_chunking_excludes_generated_binary_and_secret_content() -> None:
    assert not repository_path_is_indexable("node_modules/a/index.ts", 100)
    assert not repository_path_is_indexable("src/tool.exe", 100)
    assert chunk_repository_text("src/secret.py", "api_key = 'abcdefghijklmnop'") == []
    chunks = chunk_repository_text(
        "src/orders.py",
        "def validate_currency(order):\n    return order.currency in {'JPY', 'USD'}\n",
    )
    assert chunks[0].title.endswith("validate_currency")
    sections = artifact_sections_from_code(
        artifact_id="1" * 64,
        revision_id="2" * 64,
        path="src/orders.py",
        text="def validate_currency(order):\n    return True\n",
        principal_id="windows-user:alice",
    )
    assert sections[0].locator.start_line == 1


def test_reviewed_exports_neutralize_csv_and_never_submit() -> None:
    action = ExternalActionDraft(
        action_id="action",
        fingerprint="1" * 64,
        kind=ExternalActionKind.github_issue,
        connector_id="github-work",
        destination_ref="orders-api",
        destination_label="Orders API",
        finding_id="finding",
        finding_revision=1,
        severity="high",
        source_reference="ORDER-1",
        rendered_preview="=cmd|' /C calc'!A0\nReview the currency validation gap.",
    )
    filename, media_type, payload = render_external_action_export(action, "jira_csv")
    assert filename.endswith("-jira.csv")
    assert media_type.startswith("text/csv")
    assert payload.startswith(b"\xef\xbb\xbf")
    assert b"'=cmd" in payload
    _, _, canonical = render_external_action_export(action, "canonical_json")
    assert json.loads(canonical)["submission"] == "disabled"


def test_enterprise_oidc_pkce_binds_state_and_rotates_tokens() -> None:
    secrets_store = MemorySecretStore()
    exchanges: list[dict[str, str]] = []

    def exchange(_url: str, values: dict[str, str]):
        exchanges.append(values)
        return 200, {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 600,
        }

    manager = EnterpriseOidcPkceManager(
        secrets_store,
        authorization_url="https://identity.example/authorize",
        token_url="https://identity.example/token",
        client_id="meeting-intelligence-desktop",
        scopes=["openid", "profile"],
        exchange=exchange,
    )
    authorization = manager.start("github-work", "credential-target")
    assert "code_challenge_method=S256" in authorization.authorization_url
    manager.complete(
        "github-work", code="authorization-code", state=authorization.state
    )
    assert exchanges[0]["code_verifier"]
    assert manager.access_token("credential-target") == "access-token"
    assert "authorization-code" not in secrets_store.values["credential-target"]


def test_configured_gateway_repository_enforces_acl_and_read_only_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = "d" * 64
    seen_methods: list[str] = []

    def execute(request, credential):
        seen_methods.append(request.method)
        assert credential == "gateway-token"
        return {
            "items": [
                {
                    "name": "orders.py",
                    "path": "src/orders.py",
                    "text": "validates subsidiary and currency",
                }
            ]
        }

    repository = ConfiguredGatewayRepository(
        GatewayRepositoryConfiguration(
            connectors=[
                GatewayConnectorBinding(
                    connector_id="github-work",
                    kind="github",
                    base_url="https://api.github.com",
                    credential_environment_variable="TEST_GITHUB_TOKEN",
                    sources=[
                        GatewaySourceBinding(
                            selection_id="orders-api",
                            external_id="architecture/orders",
                            label="Orders API",
                            allowed_principal_hashes=[principal],
                        )
                    ],
                )
            ]
        ),
        executor=execute,
    )
    monkeypatch.setenv("TEST_GITHUB_TOKEN", "gateway-token")
    digest = repository.acl_digest("github-work", principal, [_selection()])
    assert len(digest) == 64
    documents = repository.search(
        GatewaySearchRequest(
            principal_hash=principal,
            connector_id="github-work",
            canonical_terms=["subsidiary", "currency"],
        )
    )
    assert documents[0].source_reference == "src/orders.py"
    assert documents[0].allowed_principals == [principal]
    assert seen_methods and set(seen_methods) == {"GET"}
    assert repository.search(
        GatewaySearchRequest(
            principal_hash=principal,
            connector_id="github-work",
            canonical_terms=["currency"],
            filters={"source_selection_ids": "not-authorized"},
        )
    ) == []
    with pytest.raises(PermissionError):
        repository.acl_digest("github-work", "e" * 64, [_selection()])
