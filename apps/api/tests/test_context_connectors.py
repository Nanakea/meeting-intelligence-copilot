from __future__ import annotations

import asyncio
import zipfile
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.adapters.context import local_files, microsoft_oauth
from app.adapters.context.broker import ContextBroker
from app.adapters.context.encrypted_index import EncryptedContextIndex
from app.adapters.context.local_files import LocalFilesContextProvider
from app.adapters.context.microsoft_oauth import (
    MicrosoftDeviceCodeManager,
    MicrosoftOAuthCredentialStore,
)
from app.adapters.context.question_context import resolve_question_context
from app.adapters.context.remote import (
    MicrosoftGraphContextProvider,
    ODataContextProvider,
    ReadOnlyJsonClient,
    _safe_business_reference,
    validate_remote_base_url,
)
from app.adapters.context.service import ContextConnectorService
from app.adapters.context.windows_security import (
    WindowsCredentialStore,
    WindowsDpapiProtector,
)
from app.api.main import CAPABILITY_TOKEN_HEADER, create_app
from app.domain.context import (
    ConnectedQuestionContextRequest,
    ConnectorDefinition,
    ConnectorHealth,
    ConnectorKind,
    ConnectorPhase,
    ContextDocument,
    ContextRelation,
    ContextSearchQuery,
    ContextSearchResult,
    EntityMapping,
    EntitySearchMode,
    IssueExportOptions,
)
from app.domain.contracts import (
    FactKind,
    GapStatus,
    InformationGap,
    Lang,
    MeetingState,
    PainPoint,
    QuestionSuggestion,
    Speaker,
    SuggestionRole,
    SuggestionStatus,
    TranscriptEvent,
)

TOKEN = "context-test-capability-token-000000000000"


def test_issue_export_options_are_trimmed_and_single_line() -> None:
    options = IssueExportOptions(
        jira_work_type="  Task  ",
        azure_work_item_type="Product Backlog Item",
    )

    assert options.jira_work_type == "Task"
    assert options.azure_work_item_type == "Product Backlog Item"
    assert options.include_connected_excerpts is False
    with pytest.raises(ValidationError):
        IssueExportOptions(
            jira_work_type="Task\nDescription,Injected",
            azure_work_item_type="Task",
        )


class XorProtector:
    def protect(self, value: bytes) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value)

    def unprotect(self, value: bytes) -> bytes:
        return self.protect(value)


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def put(self, target: str, secret: str) -> None:
        self.values[target] = secret

    def get(self, target: str) -> str | None:
        return self.values.get(target)

    def delete(self, target: str) -> bool:
        return self.values.pop(target, None) is not None


class ToggleDeleteSecretStore(MemorySecretStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_deletes = False

    def delete(self, target: str) -> bool:
        if self.fail_deletes:
            raise OSError("simulated credential manager failure")
        return super().delete(target)


class ToggleSaveIndex(EncryptedContextIndex):
    def __init__(self, path: Path) -> None:
        super().__init__(path, XorProtector())
        self.fail_saves = False

    def save_connector(self, definition: ConnectorDefinition) -> None:
        if self.fail_saves:
            raise OSError("simulated connector database failure")
        super().save_connector(definition)


class StaticProvider:
    kind = ConnectorKind.odata
    display_name = "Static ERP"
    auth_enabled = True

    def __init__(
        self,
        connector_id: str,
        documents: list[ContextDocument] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self._connector_id = connector_id
        self._documents = documents or []
        self._fail = fail

    @property
    def connector_id(self) -> str:
        return self._connector_id

    async def health(self) -> ConnectorHealth:
        if self._fail:
            raise OSError("private provider failure")
        return ConnectorHealth(
            connector_id=self.connector_id,
            kind=self.kind,
            display_name=self.display_name,
            phase=ConnectorPhase.ready,
            auth_enabled=True,
        )

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        if self._fail:
            raise OSError("private provider failure")
        return self._documents

    async def sync(self) -> int:
        return len(self._documents)

    async def close(self) -> None:
        return None


def document(
    connector_id: str,
    *,
    principal: str,
    content: str = "SAP delivery lead time is fourteen days",
    retrieved_at: datetime | None = None,
) -> ContextDocument:
    return ContextDocument(
        document_id=f"{connector_id}-document",
        connector_id=connector_id,
        source_kind=ConnectorKind.odata,
        record_id="record-17",
        title="Delivery policy",
        content=content,
        entity_type="policy",
        source_reference="POLICY-17",
        allowed_principals=[principal],
        retrieved_at=retrieved_at or datetime.now(UTC),
    )


def entity_mapping(
    entity_name: str = "Orders",
    key_field: str = "OrderId",
    display_field: str = "Customer",
) -> EntityMapping:
    fields = [key_field, display_field]
    return EntityMapping(
        entity_name=entity_name,
        key_field=key_field,
        display_field=display_field,
        searchable_fields=fields,
        projected_fields=fields,
        search_mode=EntitySearchMode.bounded_scan,
    )


def test_broker_enforces_acl_and_contains_provider_failure() -> None:
    broker = ContextBroker(
        [
            StaticProvider("allowed", [document("allowed", principal="alice")]),
            StaticProvider("denied", [document("denied", principal="bob")]),
            StaticProvider("offline", fail=True),
        ]
    )
    result = asyncio.run(
        broker.search(ContextSearchQuery(text="delivery lead time", principal_id="alice"))
    )

    assert [citation.connector_id for citation in result.citations] == ["allowed"]
    assert result.unavailable_connector_ids == ["offline"]
    assert "private provider failure" not in result.model_dump_json()
    assert "record-17" not in result.model_dump_json()
    assert result.citations[0].source_reference == "POLICY-17"


def test_broker_marks_stale_context_without_promoting_it_to_meeting_state() -> None:
    stale = document(
        "erp",
        principal="alice",
        retrieved_at=datetime.now(UTC) - timedelta(days=2),
    )
    result = asyncio.run(
        ContextBroker([StaticProvider("erp", [stale])]).search(
            ContextSearchQuery(text="lead time", principal_id="alice")
        )
    )

    assert result.citations[0].stale is True
    assert set(MeetingState.model_fields).isdisjoint(
        {"context_citations", "connector_health", "action_draft"}
    )


def test_preferred_entity_boost_does_not_make_unrelated_records_relevant() -> None:
    unrelated = document(
        "erp",
        principal="alice",
        content="Quarterly office seating allocation",
    ).model_copy(update={"entity_type": "Orders"})
    result = asyncio.run(
        ContextBroker([StaticProvider("erp", [unrelated])]).search(
            ContextSearchQuery(
                text="SAP delivery lead time",
                principal_id="alice",
                preferred_entity_types=["Orders"],
            )
        )
    )

    assert result.citations == []


@pytest.mark.parametrize(
    ("actual", "expected_relation"),
    [
        ("SAP", ContextRelation.supporting),
        ("EC", ContextRelation.conflicting),
    ],
)
def test_relation_requires_deterministic_structured_value_comparison(
    actual: str,
    expected_relation: ContextRelation,
) -> None:
    matched = document("erp", principal="alice").model_copy(
        update={"structured_values": {"source_of_truth": actual}}
    )
    result = asyncio.run(
        ContextBroker([StaticProvider("erp", [matched])]).search(
            ContextSearchQuery(
                text="delivery lead time",
                principal_id="alice",
                known_values={"source_of_truth": ["SAP"]},
            )
        )
    )

    assert result.citations[0].relation is expected_relation


def test_connector_definition_rejects_unsafe_or_ambiguous_configuration() -> None:
    with pytest.raises(ValidationError):
        ConnectorDefinition(
            connector_id="graph",
            kind=ConnectorKind.microsoft_graph,
            display_name="Graph",
            principal_id="alice",
            base_url="https://evil.example",
        )
    with pytest.raises(ValidationError):
        ConnectorDefinition(
            connector_id="odata",
            kind=ConnectorKind.odata,
            display_name="ERP",
            principal_id="alice",
            base_url="https://erp.example/odata",
        )
    with pytest.raises(ValidationError):
        ConnectorDefinition(
            connector_id="graph",
            kind=ConnectorKind.microsoft_graph,
            display_name="Graph",
            principal_id="alice",
            tenant_id="organizations",
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://erp.example/odata",
        "https://user:secret@erp.example/odata",
        "https://erp.example/odata?token=secret",
        "https://erp.example/odata#fragment",
    ],
)
def test_remote_base_url_is_https_and_credential_free(url: str) -> None:
    with pytest.raises(ValueError):
        validate_remote_base_url(url)
    assert validate_remote_base_url("https://erp.example/odata") == (
        "https://erp.example/odata/"
    )


def test_read_only_client_rejects_absolute_cross_origin_request() -> None:
    client = ReadOnlyJsonClient("https://erp.example/odata", "private-token")
    with pytest.raises(ValueError):
        client.request_json("https://attacker.example/collect")
    with pytest.raises(ValueError):
        client.request_json("Orders", method="POST")


def test_read_only_client_retries_only_bounded_transient_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_headers = Message()
    response_headers["Content-Type"] = "application/json"
    retry_headers = Message()
    retry_headers["Retry-After"] = "5"

    class Response:
        status = 200
        headers = response_headers

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int) -> bytes:
            return b'{"value": []}'

    class Opener:
        calls = 0

        def open(self, request, *, timeout: float):
            self.calls += 1
            if self.calls == 1:
                raise HTTPError(request.full_url, 429, "limited", retry_headers, None)
            if self.calls == 2:
                raise HTTPError(request.full_url, 503, "offline", Message(), None)
            assert timeout == 5.0
            return Response()

    sleeps: list[float] = []
    monkeypatch.setattr("app.adapters.context.remote.time.sleep", sleeps.append)
    client = ReadOnlyJsonClient("https://erp.example/odata", "private-token")
    opener = Opener()
    client._opener = opener

    assert client.request_json("Orders") == {"value": []}
    assert opener.calls == 3
    assert sleeps == [1.0, 0.5]


def test_public_reference_hides_opaque_provider_identifiers() -> None:
    opaque = "01234567-89ab-cdef-0123-456789abcdef"
    assert _safe_business_reference(opaque, "Acme order") == "Acme order"
    assert _safe_business_reference("ORDER-17", "Acme order") == "ORDER-17"


def test_odata_provider_projects_only_allowlisted_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, object]] = []

    def fake_request(self, path: str, *, method: str = "GET", body=None):
        calls.append((path, method, body))
        return {
            "value": [
                {
                    "OrderId": "ORDER-17",
                    "Customer": "Acme",
                    "InternalSecret": "must-not-leave-provider",
                }
            ]
        }

    monkeypatch.setattr(ReadOnlyJsonClient, "request_json", fake_request)
    secrets = MemorySecretStore()
    secrets.put("erp-target", "read-only-token")
    provider = ODataContextProvider(
        connector_id="erp",
        display_name="ERP",
        kind=ConnectorKind.odata,
        base_url="https://erp.example/odata",
        principal_id="alice",
        credential_target="erp-target",
        secret_store=secrets,
        entity_mappings=[entity_mapping()],
    )

    results = asyncio.run(
        provider.search(ContextSearchQuery(text="Acme", principal_id="alice"))
    )
    assert len(results) == 1
    assert results[0].record_id == "ORDER-17"
    assert "InternalSecret" not in results[0].content
    assert results[0].allowed_principals == ["alice"]
    assert calls[0][1:] == ("GET", None)
    assert "%24select=OrderId%2CCustomer" in calls[0][0]


def test_odata_provider_follows_bounded_same_origin_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_request(self, path: str, *, method: str = "GET", body=None):
        calls.append(path)
        if len(calls) == 1:
            return {
                "value": [{"OrderId": "ORDER-73", "Customer": "Acme"}],
                "@odata.nextLink": "https://erp.example/odata/Orders?$skiptoken=next",
            }
        return {
            "value": [
                {"OrderId": "ORDER-73", "Customer": "Acme"},
                {"OrderId": "ORDER-74", "Customer": "Acme"},
            ]
        }

    monkeypatch.setattr(ReadOnlyJsonClient, "request_json", fake_request)
    secrets = MemorySecretStore()
    secrets.put("erp-target", "read-only-token")
    provider = ODataContextProvider(
        connector_id="erp",
        display_name="ERP",
        kind=ConnectorKind.odata,
        base_url="https://erp.example/odata",
        principal_id="alice",
        credential_target="erp-target",
        secret_store=secrets,
        entity_mappings=[entity_mapping()],
    )

    results = asyncio.run(
        provider.search(ContextSearchQuery(text="Acme", principal_id="alice"))
    )

    assert [result.record_id for result in results] == ["ORDER-73", "ORDER-74"]
    assert len(calls) == 2
    assert calls[1] == "https://erp.example/odata/Orders?$skiptoken=next"


def test_odata_pagination_budget_is_global_across_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_request(self, path: str, *, method: str = "GET", body=None):
        calls.append(path)
        return {
            "value": [],
            "@odata.nextLink": f"https://erp.example/odata/page/{len(calls)}",
        }

    monkeypatch.setattr(ReadOnlyJsonClient, "request_json", fake_request)
    secrets = MemorySecretStore()
    secrets.put("erp-target", "read-only-token")
    provider = ODataContextProvider(
        connector_id="erp",
        display_name="ERP",
        kind=ConnectorKind.odata,
        base_url="https://erp.example/odata",
        principal_id="alice",
        credential_target="erp-target",
        secret_store=secrets,
        entity_mappings=[entity_mapping(), entity_mapping("Items", "ItemId", "Name")],
    )

    assert (
        asyncio.run(
            provider.search(ContextSearchQuery(text="Acme", principal_id="alice"))
        )
        == []
    )
    assert len(calls) == 10


def test_odata_server_filter_is_bounded_and_escapes_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_request(self, path: str, *, method: str = "GET", body=None):
        calls.append(path)
        return {"value": []}

    monkeypatch.setattr(ReadOnlyJsonClient, "request_json", fake_request)
    secrets = MemorySecretStore()
    secrets.put("erp-target", "read-only-token")
    fields = [f"Field{index}" for index in range(20)]
    provider = ODataContextProvider(
        connector_id="erp",
        display_name="ERP",
        kind=ConnectorKind.odata,
        base_url="https://erp.example/odata",
        principal_id="alice",
        credential_target="erp-target",
        secret_store=secrets,
        entity_mappings=[
            EntityMapping(
                entity_name="Orders",
                key_field=fields[0],
                display_field=fields[1],
                searchable_fields=fields,
                projected_fields=fields,
                search_mode=EntitySearchMode.server_filter,
            )
        ],
    )

    asyncio.run(
        provider.search(
            ContextSearchQuery(text="customer's delayed order", principal_id="alice")
        )
    )

    decoded = unquote(calls[0])
    assert decoded.count("contains(") == 16
    assert "customer''s" in decoded


def test_graph_provider_uses_search_only_and_preserves_source_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, object]] = []

    def fake_request(self, path: str, *, method: str = "GET", body=None):
        calls.append((path, method, body))
        return {
            "value": [
                {
                    "id": "drive-record-17",
                    "name": "Inventory policy",
                    "webUrl": "https://tenant.sharepoint.com/policy",
                    "lastModifiedDateTime": "2026-08-20T10:30:00Z",
                }
            ]
        }

    monkeypatch.setattr(ReadOnlyJsonClient, "request_json", fake_request)
    secrets = MemorySecretStore()
    secrets.put("graph-target", "delegated-read-token")
    provider = MicrosoftGraphContextProvider(
        connector_id="graph",
        display_name="Microsoft knowledge",
        principal_id="alice",
        credential_target="graph-target",
        secret_store=secrets,
    )

    results = asyncio.run(
        provider.search(ContextSearchQuery(text="Acme inventory", principal_id="alice"))
    )
    assert len(results) == 1
    assert results[0].record_id == "drive-record-17"
    assert results[0].allowed_principals == ["alice"]
    assert results[0].updated_at == datetime(2026, 8, 20, 10, 30, tzinfo=UTC)
    assert calls[0][0].startswith("me/drive/root/search")
    assert calls[0][1:] == ("GET", None)


def test_graph_selected_scope_ids_remain_backend_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_request(self, path: str, *, method: str = "GET", body=None):
        calls.append(path)
        return {"value": []}

    monkeypatch.setattr(ReadOnlyJsonClient, "request_json", fake_request)
    secrets = MemorySecretStore()
    secrets.put("graph-target", "delegated-read-token")
    private_drive_id = "b!private-graph-drive-record-id"
    provider = MicrosoftGraphContextProvider(
        connector_id="graph",
        display_name="Microsoft knowledge",
        principal_id="alice",
        credential_target="graph-target",
        secret_store=secrets,
        drive_ids=[private_drive_id],
    )

    assert (
        asyncio.run(
            provider.search(ContextSearchQuery(text="inventory policy", principal_id="alice"))
        )
        == []
    )
    health = asyncio.run(provider.health())
    assert calls[0].startswith("drives/b%21private-graph-drive-record-id/root/search")
    assert health.scope_summary == "1 selected SharePoint drive(s)"
    assert private_drive_id not in health.model_dump_json()


def test_encrypted_index_persists_without_plaintext(tmp_path: Path) -> None:
    index_path = tmp_path / "context.sqlite3"
    index = EncryptedContextIndex(index_path, XorProtector())
    marker = "PRIVATE-ERP-CUSTOMER-MARKER"
    definition = ConnectorDefinition(
        connector_id="local",
        kind=ConnectorKind.local_files,
        display_name="Local knowledge",
        principal_id="alice",
        root_path=str(tmp_path),
    )
    index.save_connector(definition)
    assert index.replace(
        "local", [document("local", principal="alice", content=marker)]
    ) == 1

    assert index.documents("local")[0].content == marker
    assert index.connector_definitions() == [definition]
    index.assert_no_plaintext(marker)
    assert index.delete_connector("local") is True
    assert index.documents("local") == []


def test_encrypted_index_retains_only_encrypted_credential_cleanup_handles(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "context.sqlite3"
    index = EncryptedContextIndex(index_path, XorProtector())
    target = "MeetingIntelligenceCopilot/context/erp/private-fingerprint"

    index.remember_credential_cleanup("erp", [target, target])

    assert index.pending_credential_cleanup() == [("erp", target)]
    index.assert_no_plaintext(target)
    assert index.clear_credential_cleanup("erp", target) is True
    assert index.pending_credential_cleanup() == []


def test_windows_dpapi_round_trip_is_user_scoped() -> None:
    protector = WindowsDpapiProtector()
    clear = b"private connector payload"
    encrypted = protector.protect(clear)

    assert encrypted != clear
    assert protector.unprotect(encrypted) == clear


def test_windows_credential_manager_round_trip_is_deleted() -> None:
    store = WindowsCredentialStore()
    target = f"MeetingIntelligenceCopilot/test/{uuid4().hex}"
    try:
        assert store.get(target) is None
        store.put(target, "temporary-private-token")
        assert store.get(target) == "temporary-private-token"
    finally:
        store.delete(target)
    assert store.get(target) is None


def test_local_file_provider_indexes_only_bounded_text_files(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "policy.md").write_text("Inventory policy uses SAP as source", encoding="utf-8")
    (root / "oversized.md").write_bytes(b"S" * 65)
    (root / "ignored.exe").write_bytes(b"SAP")
    index = EncryptedContextIndex(tmp_path / "index.sqlite3", XorProtector())
    provider = LocalFilesContextProvider(
        connector_id="files",
        display_name="Files",
        root=root,
        principal_id="alice",
        index=index,
        maximum_file_bytes=64,
    )

    assert asyncio.run(provider.sync()) == 1
    results = asyncio.run(
        provider.search(ContextSearchQuery(text="SAP", principal_id="alice"))
    )
    assert [result.title for result in results] == ["policy.md"]
    assert (
        asyncio.run(provider.search(ContextSearchQuery(text="SAP", principal_id="bob")))
        == []
    )


def test_local_file_provider_stops_at_document_and_total_text_limits(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    for name in ["a.txt", "b.txt", "c.txt"]:
        (root / name).write_text("12345", encoding="utf-8")
    index = EncryptedContextIndex(tmp_path / "index.sqlite3", XorProtector())
    provider = LocalFilesContextProvider(
        connector_id="files",
        display_name="Files",
        root=root,
        principal_id="alice",
        index=index,
        maximum_documents=3,
        maximum_total_characters=7,
    )

    assert asyncio.run(provider.sync()) == 2
    documents = sorted(index.documents("files"), key=lambda document: document.title)
    assert [document.title for document in documents] == ["a.txt", "b.txt"]
    assert [document.content for document in documents] == ["12345", "12"]


class FakeContextService:
    def __init__(self) -> None:
        self.received_credential: str | None = None

    async def upsert(self, definition, credential):
        self.received_credential = credential
        return ConnectorHealth(
            connector_id=definition.connector_id,
            kind=definition.kind,
            display_name=definition.display_name,
            phase=ConnectorPhase.ready,
            auth_enabled=credential is not None,
        )

    async def health(self):
        return []

    async def sync(self, connector_id):
        if connector_id == "missing":
            raise KeyError(connector_id)
        return 3

    async def start_microsoft_authorization(self, connector_id):
        raise KeyError(connector_id)

    async def poll_microsoft_authorization(self, connector_id):
        raise KeyError(connector_id)

    async def search(self, query):
        return ContextSearchResult()

    async def remove(self, connector_id):
        return connector_id == "graph"

    async def delete_all(self):
        return None

    async def close(self):
        return None


class FailingContextStorageService(FakeContextService):
    async def upsert(self, definition, credential):
        raise OSError("private storage path and failure")

    async def remove(self, connector_id):
        raise OSError("private credential cleanup failure")


def test_context_api_is_authenticated_and_never_echoes_credentials() -> None:
    service = FakeContextService()
    app = create_app(capability_token=TOKEN, context_service_instance=service)
    body = {
        "definition": {
            "connector_id": "graph",
            "kind": "microsoft_graph",
            "display_name": "Microsoft knowledge",
            "principal_id": "alice",
        },
        "credential": "private-bearer-token",
    }
    with TestClient(app) as client:
        assert client.post("/context/connectors", json=body).status_code == 401
        response = client.post(
            "/context/connectors",
            headers={CAPABILITY_TOKEN_HEADER: TOKEN},
            json=body,
        )
        assert response.status_code == 200
        assert service.received_credential == "private-bearer-token"
        assert "private-bearer-token" not in response.text


def test_context_api_contains_storage_and_credential_cleanup_failures() -> None:
    service = FailingContextStorageService()
    app = create_app(capability_token=TOKEN, context_service_instance=service)
    headers = {CAPABILITY_TOKEN_HEADER: TOKEN}
    body = {
        "definition": {
            "connector_id": "graph",
            "kind": "microsoft_graph",
            "display_name": "Microsoft knowledge",
            "principal_id": "alice",
        }
    }
    with TestClient(app) as client:
        configured = client.post("/context/connectors", headers=headers, json=body)
        assert configured.status_code == 503
        assert configured.json() == {"detail": "connector storage is unavailable"}
        deleted = client.delete("/context/connectors/graph", headers=headers)
        assert deleted.status_code == 503
        assert deleted.json() == {
            "detail": "connector credential cleanup is incomplete"
        }
        assert "private" not in configured.text
        assert "private" not in deleted.text


def test_microsoft_device_flow_keeps_device_and_refresh_secrets_backend_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_post(url: str, values: dict[str, str]):
        calls.append(url)
        if url.endswith("/devicecode"):
            return 200, {
                "device_code": "backend-only-device-secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://microsoft.com/devicelogin",
                "expires_in": 900,
                "interval": 5,
            }
        return 200, {
            "access_token": "memory-only-access-token",
            "refresh_token": "credential-manager-refresh-token",
            "expires_in": 3600,
        }

    clock = [datetime(2026, 8, 22, tzinfo=UTC)]
    monkeypatch.setattr(microsoft_oauth, "_post_form", fake_post)
    monkeypatch.setattr(microsoft_oauth, "_utcnow", lambda: clock[0])
    raw = MemorySecretStore()
    credentials = MicrosoftOAuthCredentialStore(raw)
    manager = MicrosoftDeviceCodeManager(credentials)

    authorization = manager.start(
        connector_id="graph",
        tenant_id="organizations",
        client_id="00000000-0000-0000-0000-000000000001",
    )
    public = {
        "verification_uri": authorization.verification_uri,
        "user_code": authorization.user_code,
        "expires_at": authorization.expires_at.isoformat(),
        "interval_seconds": authorization.interval_seconds,
    }
    assert "device" not in public
    assert "backend-only-device-secret" not in str(public)
    clock[0] += timedelta(seconds=5)
    assert manager.poll("graph", "graph-target") == "complete"
    assert credentials.get("graph-target") == "memory-only-access-token"
    assert "credential-manager-refresh-token" in raw.values["graph-target"]
    assert "memory-only-access-token" not in raw.values["graph-target"]
    assert calls[0].endswith("/devicecode")
    assert calls[1].endswith("/token")


def test_microsoft_refresh_rotates_without_locking_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = MemorySecretStore()
    raw.put(
        "graph-target",
        '{"kind":"microsoft_graph_refresh_v1","tenant_id":"organizations",'
        '"client_id":"00000000-0000-0000-0000-000000000001",'
        '"refresh_token":"old-refresh"}',
    )
    monkeypatch.setattr(
        microsoft_oauth,
        "_post_form",
        lambda _url, _values: (
            200,
            {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
        ),
    )

    credentials = MicrosoftOAuthCredentialStore(raw)
    assert credentials.get("graph-target") == "new-access"
    assert "new-refresh" in raw.values["graph-target"]


def test_microsoft_refresh_invalid_grant_removes_expired_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = MemorySecretStore()
    raw.put(
        "graph-target",
        '{"kind":"microsoft_graph_refresh_v1","tenant_id":"organizations",'
        '"client_id":"00000000-0000-0000-0000-000000000001",'
        '"refresh_token":"expired-refresh"}',
    )
    monkeypatch.setattr(
        microsoft_oauth,
        "_post_form",
        lambda _url, _values: (400, {"error": "invalid_grant"}),
    )

    credentials = MicrosoftOAuthCredentialStore(raw)
    assert credentials.get("graph-target") is None
    assert "graph-target" not in raw.values


def test_microsoft_device_poll_enforces_interval_and_slow_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [datetime(2026, 8, 22, tzinfo=UTC)]
    token_calls = 0

    def fake_post(url: str, _values: dict[str, str]):
        nonlocal token_calls
        if url.endswith("/devicecode"):
            return 200, {
                "device_code": "private-device-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://microsoft.com/devicelogin",
                "expires_in": 900,
                "interval": 5,
            }
        token_calls += 1
        if token_calls == 1:
            return 400, {"error": "slow_down"}
        return 200, {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
        }

    monkeypatch.setattr(microsoft_oauth, "_post_form", fake_post)
    monkeypatch.setattr(microsoft_oauth, "_utcnow", lambda: clock[0])
    manager = MicrosoftDeviceCodeManager(
        MicrosoftOAuthCredentialStore(MemorySecretStore())
    )
    manager.start(
        connector_id="graph",
        tenant_id="organizations",
        client_id="00000000-0000-0000-0000-000000000001",
    )

    assert manager.poll("graph", "graph-target") == "pending"
    assert token_calls == 0
    clock[0] += timedelta(seconds=5)
    assert manager.poll("graph", "graph-target") == "pending"
    assert token_calls == 1
    clock[0] += timedelta(seconds=5)
    assert manager.poll("graph", "graph-target") == "pending"
    assert token_calls == 1
    clock[0] += timedelta(seconds=5)
    assert manager.poll("graph", "graph-target") == "complete"
    assert token_calls == 2


def test_authenticated_microsoft_device_api_never_returns_backend_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_post(url: str, _values: dict[str, str]):
        if url.endswith("/devicecode"):
            return 200, {
                "device_code": "private-device-code",
                "user_code": "WXYZ-1234",
                "verification_uri": "https://microsoft.com/devicelogin",
                "expires_in": 900,
                "interval": 5,
            }
        return 200, {
            "access_token": "private-access-token",
            "refresh_token": "private-refresh-token",
            "expires_in": 3600,
        }

    clock = [datetime(2026, 8, 22, tzinfo=UTC)]
    monkeypatch.setattr(microsoft_oauth, "_post_form", fake_post)
    monkeypatch.setattr(microsoft_oauth, "_utcnow", lambda: clock[0])
    secrets = MemorySecretStore()
    service = ContextConnectorService(
        index=EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector()),
        secret_store=secrets,
        principal_id="windows-user:alice",
    )
    app = create_app(capability_token=TOKEN, context_service_instance=service)
    headers = {CAPABILITY_TOKEN_HEADER: TOKEN}
    with TestClient(app) as client:
        configured = client.post(
            "/context/connectors",
            headers=headers,
            json={
                "definition": {
                    "connector_id": "graph",
                    "kind": "microsoft_graph",
                    "display_name": "Microsoft knowledge",
                    "principal_id": "ignored",
                    "tenant_id": "organizations",
                    "client_id": "00000000-0000-0000-0000-000000000001",
                }
            },
        )
        assert configured.status_code == 200
        assert configured.json()["phase"] == "auth_required"

        started = client.post(
            "/context/connectors/graph/microsoft-device-code", headers=headers
        )
        assert started.status_code == 200
        assert started.json()["user_code"] == "WXYZ-1234"
        assert "private-device-code" not in started.text
        assert "device_code" not in started.text

        clock[0] += timedelta(seconds=5)
        polled = client.post(
            "/context/connectors/graph/microsoft-device-code/poll", headers=headers
        )
        assert polled.json() == {"status": "complete"}
        assert "private-refresh-token" in next(iter(secrets.values.values()))
        assert "private-access-token" not in next(iter(secrets.values.values()))


def test_remote_credentials_are_bound_to_kind_and_https_base_url(tmp_path: Path) -> None:
    secrets = MemorySecretStore()
    service = ContextConnectorService(
        index=EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector()),
        secret_store=secrets,
        principal_id="windows-user:alice",
    )

    async def scenario() -> None:
        first = ConnectorDefinition(
            connector_id="erp",
            kind=ConnectorKind.odata,
            display_name="ERP A",
            principal_id="ignored",
            base_url="https://erp-a.example/odata",
            entities={"Orders": ["OrderId"]},
        )
        await service.upsert(first, "token-for-a")
        first_targets = set(secrets.values)
        assert len(first_targets) == 1

        second = first.model_copy(
            update={
                "display_name": "ERP B",
                "base_url": "https://erp-b.example/odata",
            }
        )
        health = await service.upsert(second, None)
        assert health.phase is ConnectorPhase.auth_required
        assert secrets.values == {}

        await service.upsert(second, "token-for-b")
        second_targets = set(secrets.values)
        assert len(second_targets) == 1
        assert first_targets.isdisjoint(second_targets)
        assert next(iter(secrets.values.values())) == "token-for-b"

    asyncio.run(scenario())


def test_reconfiguration_persistence_failure_cannot_rebind_old_credential(
    tmp_path: Path,
) -> None:
    secrets = MemorySecretStore()
    index = ToggleSaveIndex(tmp_path / "context.sqlite3")
    service = ContextConnectorService(
        index=index,
        secret_store=secrets,
        principal_id="windows-user:alice",
    )

    async def scenario() -> None:
        first = ConnectorDefinition(
            connector_id="erp",
            kind=ConnectorKind.odata,
            display_name="ERP A",
            principal_id="ignored",
            base_url="https://erp-a.example/odata",
            entities={"Orders": ["OrderId"]},
        )
        await service.upsert(first, "token-for-a")
        index.fail_saves = True
        second = first.model_copy(
            update={
                "display_name": "ERP B",
                "base_url": "https://erp-b.example/odata",
            }
        )

        with pytest.raises(OSError):
            await service.upsert(second, "token-for-b")
        assert secrets.values == {}
        persisted = index.connector_definition("erp")
        assert persisted is not None
        assert persisted.display_name == "ERP A"
        assert persisted.principal_id == "windows-user:alice"
        assert persisted.resolved_configuration() == first.resolved_configuration()
        assert await service.health() == []

    asyncio.run(scenario())


def test_delete_all_purges_encrypted_index_when_credential_delete_fails(
    tmp_path: Path,
) -> None:
    secrets = ToggleDeleteSecretStore()
    index = EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector())
    service = ContextConnectorService(
        index=index,
        secret_store=secrets,
        principal_id="windows-user:alice",
    )

    async def scenario() -> None:
        definition = ConnectorDefinition(
            connector_id="erp",
            kind=ConnectorKind.odata,
            display_name="ERP",
            principal_id="ignored",
            base_url="https://erp.example/odata",
            entities={"Orders": ["OrderId"]},
        )
        await service.upsert(definition, "read-only-token")
        secrets.fail_deletes = True
        with pytest.raises(OSError):
            await service.delete_all()
        assert index.connector_definitions() == []
        assert len(index.pending_credential_cleanup("erp")) == 2
        assert len(secrets.values) == 1

        secrets.fail_deletes = False
        await service.delete_all()
        assert index.pending_credential_cleanup() == []
        assert secrets.values == {}

    asyncio.run(scenario())


def test_disconnect_retries_orphaned_credential_cleanup_after_restart(
    tmp_path: Path,
) -> None:
    secrets = ToggleDeleteSecretStore()
    index = EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector())
    service = ContextConnectorService(
        index=index,
        secret_store=secrets,
        principal_id="windows-user:alice",
    )

    async def configure_and_fail_cleanup() -> str:
        definition = ConnectorDefinition(
            connector_id="erp",
            kind=ConnectorKind.odata,
            display_name="ERP",
            principal_id="ignored",
            base_url="https://erp.example/odata",
            entities={"Orders": ["OrderId"]},
        )
        await service.upsert(definition, "read-only-token")
        target = next(iter(secrets.values))
        secrets.fail_deletes = True
        with pytest.raises(OSError):
            await service.remove("erp")
        assert index.connector_definition("erp") is None
        assert index.pending_credential_cleanup("erp")
        return target

    target = asyncio.run(configure_and_fail_cleanup())
    secrets.fail_deletes = False

    ContextConnectorService(
        index=index,
        secret_store=secrets,
        principal_id="windows-user:alice",
    )

    assert target not in secrets.values
    assert index.pending_credential_cleanup() == []


def test_action_draft_is_authoritative_deterministic_and_has_no_write_endpoint(
    tmp_path: Path,
) -> None:
    state = _question_state()

    class Registry:
        def current_state(self, session_id: str):
            return state if session_id == state.meeting_id else None

        def close(self):
            return None

    service = ContextConnectorService(
        index=EncryptedContextIndex(tmp_path / "draft-context.sqlite3", XorProtector()),
        secret_store=MemorySecretStore(),
        principal_id="alice",
    )
    app = create_app(
        capability_token=TOKEN,
        live_registry_instance=Registry(),
        context_service_instance=service,
    )
    payload = {
        "session_id": state.meeting_id,
        "pain_id": state.pain_points[0].pain_id,
        "state_version": state.version,
        "include_context": False,
    }
    with TestClient(app) as client:
        first = client.post(
            "/issues/drafts",
            headers={CAPABILITY_TOKEN_HEADER: TOKEN},
            json=payload,
        )
        second = client.post(
            "/issues/drafts",
            headers={CAPABILITY_TOKEN_HEADER: TOKEN},
            json=payload,
        )
        assert first.status_code == 200
        assert first.json() == second.json()
        assert first.json()["draft_id"].startswith("draft-")
        assert first.json()["facts"] == []
        assert first.json()["missing_required_fields"] == [
            "source_of_truth",
            "business_impact",
            "owner",
            "affected_scope",
        ]
        stale = client.post(
            "/issues/drafts",
            headers={CAPABILITY_TOKEN_HEADER: TOKEN},
            json={**payload, "state_version": state.version - 1},
        )
        assert stale.status_code == 409
        assert stale.json() == {
            "status": "stale",
            "latest_state_version": state.version,
        }
        missing = client.post(
            "/issues/drafts",
            headers={CAPABILITY_TOKEN_HEADER: TOKEN},
            json={**payload, "pain_id": "another-session::data_mismatch::pi-missing"},
        )
        assert missing.status_code == 404
        assert missing.json() == {"status": "problem_not_found"}
        batch = client.post(
            "/issues/drafts/batch",
            headers={CAPABILITY_TOKEN_HEADER: TOKEN},
            json={
                "session_id": state.meeting_id,
                "pain_ids": [state.pain_points[0].pain_id],
                "state_version": state.version,
                "include_context": False,
            },
        )
        assert batch.status_code == 200
        assert batch.json()["state_version"] == state.version
        assert batch.json()["drafts"] == [first.json()]
        final = client.post(
            "/issues/drafts/final",
            headers={CAPABILITY_TOKEN_HEADER: TOKEN},
            json={"session_id": state.meeting_id},
        )
        assert final.status_code == 200
        assert final.json()["drafts"] == [first.json()]
        assert client.post(
            "/issues/drafts/final",
            json={"session_id": state.meeting_id},
        ).status_code == 401
        assert client.post("/issues/drafts/submit").status_code == 404
        assert client.post("/context/action-drafts", json={}).status_code == 404

    meeting_only_app = create_app(
        capability_token=TOKEN,
        live_registry_instance=Registry(),
    )
    with TestClient(meeting_only_app) as client:
        meeting_only = client.post(
            "/issues/drafts",
            headers={CAPABILITY_TOKEN_HEADER: TOKEN},
            json=payload,
        )
        assert meeting_only.status_code == 200
        assert meeting_only.json()["context_status"] == "not_requested"
        unavailable_context = client.post(
            "/issues/drafts",
            headers={CAPABILITY_TOKEN_HEADER: TOKEN},
            json={**payload, "include_context": True},
        )
        assert unavailable_context.status_code == 200
        assert unavailable_context.json()["context_status"] == "unavailable"
        assert unavailable_context.json()["missing_required_fields"] == first.json()[
            "missing_required_fields"
        ]


def test_actual_context_service_configures_syncs_searches_and_purges(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    marker = "PRIVATE-INVENTORY-POLICY-MARKER"
    (knowledge / "inventory.md").write_text(
        f"SAP is authoritative. {marker}", encoding="utf-8"
    )
    index = EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector())
    service = ContextConnectorService(
        index=index,
        secret_store=MemorySecretStore(),
        principal_id="windows-user:alice",
    )
    app = create_app(capability_token=TOKEN, context_service_instance=service)
    headers = {CAPABILITY_TOKEN_HEADER: TOKEN}
    with TestClient(app) as client:
        configured = client.post(
            "/context/connectors",
            headers=headers,
            json={
                "definition": {
                    "connector_id": "knowledge",
                    "kind": "local_files",
                    "display_name": "Knowledge",
                    # The service must replace this caller-controlled identity.
                    "principal_id": "windows-user:mallory",
                    "root_path": str(knowledge),
                    "entities": {},
                }
            },
        )
        assert configured.status_code == 200
        synced = client.post("/context/connectors/knowledge/sync", headers=headers)
        assert synced.json()["indexed_documents"] == 1
        searched = client.post(
            "/context/search",
            headers=headers,
            json={
                "text": "inventory policy",
                "principal_id": "windows-user:mallory",
                "limit": 4,
            },
        )
        assert searched.status_code == 200
        assert searched.json()["citations"][0]["source_label"] == "inventory.md"
        assert marker in searched.json()["citations"][0]["excerpt"]
        feedback = client.post(
            "/context/citation-feedback",
            headers=headers,
            json={
                "connector_kind": "local_files",
                "entity_type": "knowledge_document",
                "rank": 1,
                "action": "relevant",
                "state_version": 1,
                "response_ms": 25,
            },
        )
        assert feedback.status_code == 204
        diagnostics = client.get("/context/diagnostics", headers=headers).json()
        assert diagnostics["search_requests"] == 1
        assert diagnostics["citation_results"] == 1
        assert diagnostics["local_index_documents"] == 1
        assert diagnostics["citation_feedback_records"] == 1
        index.assert_no_plaintext(marker)
        assert client.delete("/context", headers=headers).json() == {"deleted": True}
        assert index.documents("knowledge") == []


def test_legacy_entity_fields_migrate_to_explicit_bounded_mapping() -> None:
    definition = ConnectorDefinition(
        connector_id="erp",
        kind=ConnectorKind.odata,
        display_name="ERP",
        principal_id="alice",
        base_url="https://erp.example/odata",
        entities={"Orders": ["OrderId", "Customer"]},
    )

    mapping = definition.resolved_entity_mappings()[0]
    assert mapping.key_field == "OrderId"
    assert mapping.display_field == "OrderId"
    assert mapping.searchable_fields == ["OrderId", "Customer"]
    assert mapping.projected_fields == ["OrderId", "Customer"]
    assert mapping.search_mode is EntitySearchMode.bounded_scan


def test_odata_server_filter_is_bounded_and_escapes_literals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_request(self, path: str, *, method: str = "GET", body=None):
        calls.append(path)
        return {"value": []}

    monkeypatch.setattr(ReadOnlyJsonClient, "request_json", fake_request)
    secrets = MemorySecretStore()
    secrets.put("erp-target", "read-only-token")
    mapping = entity_mapping().model_copy(
        update={"search_mode": EntitySearchMode.server_filter}
    )
    provider = ODataContextProvider(
        connector_id="erp",
        display_name="ERP",
        kind=ConnectorKind.odata,
        base_url="https://erp.example/odata",
        principal_id="alice",
        credential_target="erp-target",
        secret_store=secrets,
        entity_mappings=[mapping],
    )

    asyncio.run(provider.search(ContextSearchQuery(text="O'Brien", principal_id="alice")))

    assert len(calls) == 1
    assert "%24filter=" in calls[0]
    assert "O%27%27Brien".casefold() in calls[0].casefold()


def test_broker_opens_circuit_after_three_failures() -> None:
    provider = StaticProvider("offline", fail=True)
    broker = ContextBroker([provider], circuit_cooldown_seconds=30)
    query = ContextSearchQuery(text="inventory", principal_id="alice")

    for _ in range(4):
        result = asyncio.run(broker.search(query))
        assert result.unavailable_connector_ids == ["offline"]

    circuit = broker._circuits["offline"]
    assert circuit.consecutive_failures == 3
    assert circuit.opened_until > 0


def test_broker_allows_one_successful_half_open_probe() -> None:
    provider = StaticProvider(
        "flaky", [document("flaky", principal="alice")], fail=True
    )
    broker = ContextBroker([provider], circuit_cooldown_seconds=30)
    query = ContextSearchQuery(text="delivery lead time", principal_id="alice")
    for _ in range(3):
        asyncio.run(broker.search(query))
    circuit = broker._circuits["flaky"]
    circuit.opened_until = 0.0001
    provider._fail = False

    result = asyncio.run(broker.search(query))

    assert [citation.connector_id for citation in result.citations] == ["flaky"]
    assert circuit.consecutive_failures == 0


def _question_state() -> MeetingState:
    event = TranscriptEvent(
        event_id="event-0",
        meeting_id="meeting-intel-abc",
        seq=0,
        speaker=Speaker(id="speaker-1"),
        text="SAP and EC inventory do not match.",
        lang=Lang.en,
        ts_start=0,
        ts_end=1,
        source="meetily",
    )
    pain = PainPoint(
        pain_id="pain-1",
        meeting_id=event.meeting_id,
        template_id="data_mismatch",
        title="SAP and EC inventory mismatch",
        kind=FactKind.evidence,
        evidence_event_ids=[event.event_id],
        created_seq=0,
    )
    gap = InformationGap(
        gap_id="pain-1::source_of_truth",
        meeting_id=event.meeting_id,
        pain_id=pain.pain_id,
        slot="source_of_truth",
        status=GapStatus.open,
        base_priority=0,
    )
    suggestion = QuestionSuggestion(
        suggestion_id="suggestion-1",
        gap_id=gap.gap_id,
        role=SuggestionRole.ask_now,
        text="Which system is authoritative for inventory?",
        reason="The source of truth is still unclear.",
        status=SuggestionStatus.active,
        priority=0,
    )
    return MeetingState(
        meeting_id=event.meeting_id,
        version=1,
        transcript=[event],
        pain_points=[pain],
        gaps=[gap],
        suggestions=[suggestion],
        last_event_seq=0,
    )


def test_question_context_query_is_derived_from_authoritative_state() -> None:
    state = _question_state()
    request = ConnectedQuestionContextRequest(
        session_id=state.meeting_id,
        suggestion_id="suggestion-1",
        state_version=1,
    )

    resolution = resolve_question_context(state, request, principal_id="alice")

    assert resolution is not None
    assert "SAP" in resolution.query.text
    assert resolution.query.target_slot == "source_of_truth"
    assert resolution.query.preferred_entity_types == ["Items", "products"]
    assert resolve_question_context(
        state, request.model_copy(update={"state_version": 0}), principal_id="alice"
    ) is None


def test_question_context_language_follows_target_pain_evidence() -> None:
    state = _question_state()
    japanese_evidence = state.transcript[0].model_copy(
        update={"text": "SAPとECの在庫データが一致しない。", "lang": Lang.ja}
    )
    later_english = TranscriptEvent(
        event_id="event-1",
        meeting_id=state.meeting_id,
        seq=1,
        speaker=Speaker(id="speaker-2"),
        text="Let us move to the next topic.",
        lang=Lang.en,
        ts_start=1,
        ts_end=2,
        source="meetily",
    )
    pain = state.pain_points[0].model_copy(
        update={"title": "SAPとECの在庫データが一致しない"}
    )
    mixed_state = state.model_copy(
        update={
            "transcript": [japanese_evidence, later_english],
            "pain_points": [pain],
            "last_event_seq": 1,
        }
    )

    resolution = resolve_question_context(
        mixed_state,
        ConnectedQuestionContextRequest(
            session_id=state.meeting_id,
            suggestion_id="suggestion-1",
            state_version=1,
        ),
        principal_id="alice",
    )

    assert resolution is not None
    assert "在庫データが一致しない" in resolution.query.text


def test_question_context_api_returns_typed_stale_response(tmp_path: Path) -> None:
    state = _question_state()

    class Registry:
        def current_state(self, session_id: str):
            return state if session_id == state.meeting_id else None

        def close(self):
            return None

    service = ContextConnectorService(
        index=EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector()),
        secret_store=MemorySecretStore(),
        principal_id="alice",
    )
    app = create_app(
        capability_token=TOKEN,
        live_registry_instance=Registry(),
        context_service_instance=service,
    )
    headers = {CAPABILITY_TOKEN_HEADER: TOKEN}
    body = {
        "session_id": state.meeting_id,
        "suggestion_id": "suggestion-1",
        "state_version": 0,
    }
    with TestClient(app) as client:
        response = client.post("/context/question-context", headers=headers, json=body)

    assert response.status_code == 409
    assert response.json() == {
        "status": "stale",
        "state_version": 1,
        "suggestion_id": "suggestion-1",
        "citations": [],
        "unavailable_connector_ids": [],
    }


def test_question_context_api_retrieves_from_backend_owned_ask_now(
    tmp_path: Path,
) -> None:
    state = _question_state()

    class Registry:
        def current_state(self, session_id: str):
            return state if session_id == state.meeting_id else None

        def close(self):
            return None

    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "inventory-policy.md").write_text(
        "SAP inventory source of truth and authoritative EC policy",
        encoding="utf-8",
    )
    service = ContextConnectorService(
        index=EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector()),
        secret_store=MemorySecretStore(),
        principal_id="alice",
    )

    async def configure() -> None:
        await service.upsert(
            ConnectorDefinition(
                connector_id="knowledge",
                kind=ConnectorKind.local_files,
                display_name="Knowledge",
                principal_id="ignored",
                root_path=str(root),
            ),
            None,
        )
        await service.sync("knowledge")

    asyncio.run(configure())
    app = create_app(
        capability_token=TOKEN,
        live_registry_instance=Registry(),
        context_service_instance=service,
    )
    body = {
        "session_id": state.meeting_id,
        "suggestion_id": "suggestion-1",
        "state_version": 1,
    }
    with TestClient(app) as client:
        assert client.post("/context/question-context", json=body).status_code == 401
        response = client.post(
            "/context/question-context",
            headers={CAPABILITY_TOKEN_HEADER: TOKEN},
            json=body,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "current"
    assert payload["citations"][0]["source_reference"] == "inventory-policy.md"
    assert "record_id" not in response.text
    assert state.model_dump() == _question_state().model_dump()


def test_local_office_documents_are_bounded_and_malformed_files_are_skipped(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    with zipfile.ZipFile(root / "policy.docx", "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="urn:w"><w:body><w:p><w:r>'
            "<w:t>SAP inventory policy</w:t></w:r></w:p></w:body></w:document>",
        )
        archive.writestr("word/vbaProject.bin", b"PRIVATE-MACRO-CONTENT")
        archive.writestr("word/embeddings/object.bin", b"PRIVATE-EMBEDDED-CONTENT")
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships><Relationship Target="https://external.example/private"/></Relationships>',
        )
    with zipfile.ZipFile(root / "inventory.xlsx", "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="urn:x"><si><t>Warehouse stock</t></si></sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="urn:x"><sheetData><row>'
            '<c t="s"><v>0</v></c><c><v>42</v></c>'
            "</row></sheetData></worksheet>",
        )
    (root / "broken.xlsx").write_bytes(b"not-a-zip")
    with zipfile.ZipFile(root / "broken.docx", "w") as archive:
        archive.writestr("word/document.xml", "<broken")
    (root / "broken.pdf").write_bytes(b"not-a-pdf")
    index = EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector())
    provider = LocalFilesContextProvider(
        connector_id="local",
        display_name="Knowledge",
        root=root,
        principal_id="alice",
        index=index,
    )

    assert asyncio.run(provider.sync()) == 2
    assert {document.content for document in index.documents("local")} == {
        "SAP inventory policy",
        "Warehouse stock\t42",
    }
    assert "PRIVATE" not in " ".join(
        document.content for document in index.documents("local")
    )


def test_office_archive_expansion_limit_rejects_compressed_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"x" * 1_024)
    monkeypatch.setattr(local_files, "_MAXIMUM_ARCHIVE_EXPANDED_BYTES", 128)

    with pytest.raises(ValueError, match="safe extraction limits"):
        local_files._read_document(path)


def test_local_provider_does_not_follow_symlinks_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    outside = tmp_path / "private.md"
    outside.write_text("SAP private record", encoding="utf-8")
    try:
        (root / "escape.md").symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlink creation is unavailable")
    index = EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector())
    provider = LocalFilesContextProvider(
        connector_id="local",
        display_name="Knowledge",
        root=root,
        principal_id="alice",
        index=index,
    )

    assert asyncio.run(provider.sync()) == 0
    assert index.documents("local") == []


def test_local_pdf_extraction_is_page_and_text_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Page:
        def extract_text(self) -> str:
            return "inventory policy"

    class Reader:
        is_encrypted = False
        pages = [Page()]

        def __init__(self, path: Path, *, strict: bool) -> None:
            assert path.suffix == ".pdf"
            assert strict is False

    monkeypatch.setattr(local_files, "PdfReader", Reader)
    path = tmp_path / "policy.pdf"
    path.write_bytes(b"bounded-pdf-fixture")

    assert local_files._read_document(path) == "inventory policy"
