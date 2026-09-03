from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from app.adapters.context import collaboration, netsuite, notion
from app.adapters.context.encrypted_index import EncryptedContextIndex
from app.adapters.context.notion import NotionContextProvider, normalize_notion_page_id
from app.adapters.context.service import ContextConnectorService
from app.api.main import create_app
from app.domain.context import (
    ConnectorDefinition,
    ConnectorKind,
    ContextSearchQuery,
    NotionConnectorConfig,
)
from app.domain.enterprise import SourceSelection, SourceSelectionKind


class XorProtector:
    def protect(self, value: bytes) -> bytes:
        return bytes(item ^ 0x6D for item in value)

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


class FakeNotionReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def read_markdown(self, page_id: str, token: str) -> tuple[str, bool]:
        self.calls.append((page_id, token))
        return "# Order integration\nThe approved flow uses NetSuite.", False


def selection(connector_id: str = "notion-work") -> SourceSelection:
    return SourceSelection(
        selection_id="order-design",
        connector_id=connector_id,
        kind=SourceSelectionKind.space,
        label="Order integration design",
    )


def notion_definition() -> ConnectorDefinition:
    return ConnectorDefinition(
        connector_id="notion-work",
        kind=ConnectorKind.notion,
        display_name="Selected Notion pages",
        principal_id="windows-user:alice",
        configuration=NotionConnectorConfig(
            kind="notion", workspace_label="Architecture workspace"
        ),
    )


def test_notion_provider_reads_only_enrolled_pages_and_exposes_safe_references() -> None:
    store = MemorySecretStore()
    store.put("notion-secret", "ntn_test_read_only_token_123456789")
    reader = FakeNotionReader()
    provider = NotionContextProvider(
        connector_id="notion-work",
        display_name="Selected Notion pages",
        principal_id="windows-user:alice",
        configuration=NotionConnectorConfig(
            kind="notion", workspace_label="Architecture workspace"
        ),
        selections=[
            selection(),
            SourceSelection(
                selection_id="not-enrolled",
                connector_id="notion-work",
                kind=SourceSelectionKind.space,
                label="Not enrolled",
            ),
        ],
        external_page_ids={
            "order-design": "0123456789abcdef0123456789abcdef",
        },
        credential_target="notion-secret",
        secret_store=store,
        reader=reader,
    )

    documents = asyncio.run(
        provider.search(
            ContextSearchQuery(
                text="NetSuite order flow", principal_id="windows-user:alice"
            )
        )
    )

    assert len(documents) == 1
    assert documents[0].source_kind is ConnectorKind.notion
    assert documents[0].source_reference == "Order integration design"
    assert documents[0].uri is None
    assert "01234567" not in documents[0].model_dump_json()
    assert reader.calls == [
        ("01234567-89ab-cdef-0123-456789abcdef", "ntn_test_read_only_token_123456789")
    ]


def test_notion_source_identifier_and_token_are_not_persisted_in_plaintext(
    tmp_path: Path,
) -> None:
    index = EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector())
    secrets = MemorySecretStore()
    service = ContextConnectorService(
        index=index,
        secret_store=secrets,
        principal_id="windows-user:alice",
    )
    raw_page_id = "0123456789abcdef0123456789abcdef"
    token = "ntn_test_read_only_token_123456789"

    async def scenario() -> None:
        await service.upsert(notion_definition(), token)
        await service.save_source_selection(
            "notion-work", selection(), raw_page_id
        )
        assert service.source_selections("notion-work") == [selection()]
        await service.close()

    asyncio.run(scenario())

    database = (tmp_path / "context.sqlite3").read_bytes()
    assert raw_page_id.encode() not in database
    assert token.encode() not in database
    assert token in secrets.values.values()


def test_external_connectors_have_no_composed_write_or_delete_primitive() -> None:
    collaboration_source = inspect.getsource(collaboration)
    netsuite_source = inspect.getsource(netsuite)
    notion_source = inspect.getsource(notion)
    application_source = inspect.getsource(create_app)

    assert "chat.postMessage" not in collaboration_source
    assert "NotificationExecutor" not in collaboration_source
    assert "ReviewRecordExecutor" not in netsuite_source
    assert 'method="PATCH"' not in collaboration_source + netsuite_source + notion_source
    assert 'method="DELETE"' not in collaboration_source + netsuite_source + notion_source
    assert "/slack/bot-credential" not in application_source
    assert "direct_writes_disabled" in application_source
    assert 'method="GET"' in notion_source


def test_notion_page_ids_are_strict() -> None:
    assert normalize_notion_page_id("0123456789abcdef0123456789abcdef") == (
        "01234567-89ab-cdef-0123-456789abcdef"
    )
    for value in (
        "https://api.notion.com/v1/pages/0123",
        "../../workspace",
        "01234567-89ab-cdef-0123-456789abcdeg",
    ):
        with pytest.raises(ValueError):
            normalize_notion_page_id(value)
