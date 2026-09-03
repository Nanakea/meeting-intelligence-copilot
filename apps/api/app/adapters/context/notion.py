"""Selected-page, read-only Notion live context adapter."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.adapters.context.windows_security import SecretStore
from app.domain.context import (
    ConnectorHealth,
    ConnectorKind,
    ConnectorPhase,
    ContextDocument,
    ContextSearchQuery,
    NotionConnectorConfig,
)
from app.domain.enterprise import SourceSelection, SourceSelectionKind

_NOTION_API_ORIGIN = "https://api.notion.com"
_NOTION_VERSION = "2026-03-11"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_CONTENT_CHARACTERS = 100_000
_PAGE_ID = re.compile(
    r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


def normalize_notion_page_id(value: str) -> str:
    cleaned = value.strip()
    if not _PAGE_ID.fullmatch(cleaned):
        raise ValueError("Notion page identifiers must be UUIDs")
    compact = cleaned.replace("-", "").lower()
    return (
        f"{compact[:8]}-{compact[8:12]}-{compact[12:16]}-"
        f"{compact[16:20]}-{compact[20:]}"
    )


class NotionPageReader(Protocol):
    """Narrow read capability; no generic HTTP method is exposed."""

    def read_markdown(self, page_id: str, token: str) -> tuple[str, bool]: ...


class UrllibNotionPageReader:
    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, file_pointer, code, message, headers, new_url):
            return None

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self._timeout = timeout_seconds
        self._opener = build_opener(self._NoRedirect())

    def read_markdown(self, page_id: str, token: str) -> tuple[str, bool]:
        normalized = normalize_notion_page_id(page_id)
        if not token or len(token) < 20 or any(character.isspace() for character in token):
            raise PermissionError("Notion credential is invalid")
        request = Request(
            f"{_NOTION_API_ORIGIN}/v1/pages/{normalized}/markdown?include_transcript=false",
            method="GET",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": _NOTION_VERSION,
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                payload = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            if error.code in {401, 403, 404}:
                raise PermissionError("Notion page access is unavailable") from None
            raise OSError("Notion read failed") from None
        except (TimeoutError, URLError):
            raise OSError("Notion read failed") from None
        if len(payload) > _MAX_RESPONSE_BYTES:
            raise ValueError("Notion response exceeds the local bound")
        decoded = json.loads(payload or b"{}")
        if not isinstance(decoded, dict) or not isinstance(decoded.get("markdown"), str):
            raise ValueError("Notion markdown response is invalid")
        return decoded["markdown"], decoded.get("truncated") is True


def _safe_markdown(value: str) -> str:
    return value.replace("\0", "")[:_MAX_CONTENT_CHARACTERS]


class NotionContextProvider:
    """Reads only explicitly selected Notion pages and retains no remote content."""

    def __init__(
        self,
        *,
        connector_id: str,
        display_name: str,
        principal_id: str,
        configuration: NotionConnectorConfig,
        selections: list[SourceSelection],
        external_page_ids: dict[str, str],
        credential_target: str,
        secret_store: SecretStore,
        reader: NotionPageReader | None = None,
    ) -> None:
        self._connector_id = connector_id
        self._display_name = display_name
        self._principal_id = principal_id
        self.configuration = configuration
        self._selections = {
            selection.selection_id: selection
            for selection in selections
            if selection.enabled and selection.kind is SourceSelectionKind.space
        }
        self._external_page_ids = external_page_ids
        self._credential_target = credential_target
        self._secret_store = secret_store
        self._reader = reader or UrllibNotionPageReader()

    @property
    def connector_id(self) -> str:
        return self._connector_id

    @property
    def kind(self) -> ConnectorKind:
        return ConnectorKind.notion

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def auth_enabled(self) -> bool:
        return self._secret_store.get(self._credential_target) is not None

    def _token(self) -> str:
        token = self._secret_store.get(self._credential_target)
        if token is None:
            raise PermissionError("Notion authorization is unavailable")
        if len(token) < 20 or any(character.isspace() for character in token):
            raise ValueError("Notion credential is invalid")
        return token

    def _documents_sync(self) -> list[ContextDocument]:
        token = self._token()
        retrieved_at = datetime.now(UTC)
        documents: list[ContextDocument] = []
        for selection_id in sorted(self._selections):
            selection = self._selections[selection_id]
            raw_page_id = self._external_page_ids.get(selection_id)
            if raw_page_id is None:
                continue
            page_id = normalize_notion_page_id(raw_page_id)
            markdown, truncated = self._reader.read_markdown(page_id, token)
            content = _safe_markdown(markdown)
            if not content.strip():
                continue
            page_fingerprint = hashlib.sha256(page_id.encode()).hexdigest()
            documents.append(
                ContextDocument(
                    document_id=hashlib.sha256(
                        f"{self.connector_id}\0{page_fingerprint}".encode()
                    ).hexdigest(),
                    connector_id=self.connector_id,
                    source_kind=self.kind,
                    record_id=page_fingerprint,
                    title=selection.label,
                    content=content,
                    entity_type="notion_page",
                    source_reference=selection.label,
                    structured_values={"content_status": "truncated" if truncated else "complete"},
                    allowed_principals=[self._principal_id],
                    retrieved_at=retrieved_at,
                )
            )
        return documents

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(
            connector_id=self.connector_id,
            kind=self.kind,
            display_name=self.display_name,
            phase=ConnectorPhase.ready if self.auth_enabled else ConnectorPhase.auth_required,
            auth_enabled=self.auth_enabled,
            detail_code=None if self.auth_enabled else "notion_authorization_required",
            scope_summary=f"{len(self._selections)} selected Notion pages",
        )

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        del query
        import asyncio

        return await asyncio.to_thread(self._documents_sync)

    async def assurance_documents(self) -> list[ContextDocument]:
        import asyncio

        return await asyncio.to_thread(self._documents_sync)

    async def sync(self) -> int:
        if not self.auth_enabled:
            raise PermissionError("Notion authorization is unavailable")
        return 0

    async def close(self) -> None:
        return None
