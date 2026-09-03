"""Strict read-only HTTP adapters for Graph and OData-compatible systems."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

from app.adapters.context.windows_security import SecretStore
from app.adapters.evidence.remote_documents import (
    MAXIMUM_REMOTE_FILE_BYTES,
    SUPPORTED_REMOTE_EXTENSIONS,
    parse_remote_document,
)
from app.domain.assurance import (
    ERPEntityMetadata,
    ERPFieldMetadata,
    ERPMetadataSnapshot,
)
from app.domain.context import (
    ConnectorHealth,
    ConnectorKind,
    ConnectorPhase,
    ContextDocument,
    ContextSearchQuery,
    EntityMapping,
    EntitySearchMode,
)

_MAXIMUM_RESPONSE_BYTES = 2 * 1024 * 1024
_MAXIMUM_ODATA_PAGES_PER_SEARCH = 10
_ODATA_PAGE_SIZE = 50
_MAXIMUM_ODATA_FILTER_CLAUSES = 16
_MAXIMUM_ODATA_TERM_CHARACTERS = 64
_MAXIMUM_GRAPH_ASSURANCE_DOCUMENTS = 500
_MAXIMUM_GRAPH_ASSURANCE_PAGES = 40
_TRANSIENT_HTTP_STATUSES = {429, 502, 503, 504}
_RETRY_DELAYS_SECONDS = (0.25, 0.5)
_OPAQUE_IDENTIFIER = re.compile(
    r"^(?:[0-9a-f]{32,}|[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})$",
    re.IGNORECASE,
)


def _odata_string_literal(value: str) -> str:
    return value.replace("'", "''")


def _safe_business_reference(record: str, display: str) -> str:
    if len(record) <= 64 and not _OPAQUE_IDENTIFIER.fullmatch(record):
        return record
    return display[:256]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def validate_remote_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("connector base URL must be an HTTPS origin/path without credentials")
    return value.rstrip("/") + "/"


class ReadOnlyJsonClient:
    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 5.0) -> None:
        self._base_url = validate_remote_base_url(base_url)
        self._origin = urlsplit(self._base_url).netloc.casefold()
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._opener = build_opener(_NoRedirect())

    def request_json(self, path: str, *, method: str = "GET") -> dict[str, object]:
        if method != "GET":
            raise ValueError("context HTTP client permits GET only")
        url = urljoin(self._base_url, path.lstrip("/"))
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc.casefold() != self._origin:
            raise ValueError("connector request escaped its configured HTTPS origin")
        request = Request(
            url,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
        )
        payload: bytes | None = None
        for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
            try:
                with self._opener.open(request, timeout=self._timeout_seconds) as response:
                    if response.status != 200:
                        raise OSError("connector returned a non-success status")
                    content_type = response.headers.get_content_type()
                    if content_type != "application/json":
                        raise ValueError("connector response is not JSON")
                    payload = response.read(_MAXIMUM_RESPONSE_BYTES + 1)
                break
            except HTTPError as error:
                if error.code not in _TRANSIENT_HTTP_STATUSES or attempt >= len(
                    _RETRY_DELAYS_SECONDS
                ):
                    raise OSError(
                        f"connector request failed with status {error.code}"
                    ) from None
                retry_after = error.headers.get("Retry-After") if error.headers else None
                delay = _RETRY_DELAYS_SECONDS[attempt]
                if retry_after and retry_after.isdigit():
                    delay = min(float(retry_after), 1.0)
                time.sleep(delay)
        assert payload is not None
        if len(payload) > _MAXIMUM_RESPONSE_BYTES:
            raise ValueError("connector response exceeds the local size limit")
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("connector response must be a JSON object")
        return decoded

    def request_xml(self, path: str, *, method: str = "GET") -> bytes:
        if method != "GET":
            raise ValueError("context HTTP client permits GET only")
        url = urljoin(self._base_url, path.lstrip("/"))
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc.casefold() != self._origin:
            raise ValueError("connector request escaped its configured HTTPS origin")
        request = Request(
            url,
            method=method,
            headers={
                "Accept": "application/xml, text/xml",
                "Authorization": f"Bearer {self._token}",
            },
        )
        with self._opener.open(request, timeout=self._timeout_seconds) as response:
            if response.status != 200:
                raise OSError("connector returned a non-success status")
            content_type = response.headers.get_content_type()
            if content_type not in {"application/xml", "text/xml", "application/atom+xml"}:
                raise ValueError("connector response is not XML metadata")
            payload = response.read(_MAXIMUM_RESPONSE_BYTES + 1)
        if len(payload) > _MAXIMUM_RESPONSE_BYTES:
            raise ValueError("connector metadata exceeds the local size limit")
        return payload


def _download_graph_content(url: str) -> bytes:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    allowed = host.endswith(
        (".sharepoint.com", ".sharepointonline.com", ".onedrive.com", ".1drv.com")
    )
    if parsed.scheme != "https" or not allowed or parsed.username or parsed.password:
        raise ValueError("Graph download URL is outside approved Microsoft storage hosts")
    request = Request(url, method="GET", headers={"Accept": "application/octet-stream"})
    opener = build_opener(_NoRedirect())
    with opener.open(request, timeout=10.0) as response:
        if response.status != 200:
            raise OSError("Graph document download failed")
        payload = response.read(MAXIMUM_REMOTE_FILE_BYTES + 1)
    if len(payload) > MAXIMUM_REMOTE_FILE_BYTES:
        raise ValueError("Graph document exceeds the file size limit")
    return payload


def _parse_odata_metadata(
    payload: bytes,
    *,
    connector_id: str,
    source_reference: str,
    allowed_entities: set[str],
) -> ERPMetadataSnapshot:
    root = ElementTree.fromstring(payload)
    entity_types: dict[str, ERPEntityMetadata] = {}
    for element in root.iter():
        if not element.tag.endswith("}EntityType"):
            continue
        name = element.attrib.get("Name", "")
        if not name or len(entity_types) >= 2_000:
            continue
        keys = {
            child.attrib.get("Name", "")
            for key in element
            if key.tag.endswith("}Key")
            for child in key
            if child.tag.endswith("}PropertyRef")
        }
        fields: list[ERPFieldMetadata] = []
        relationships: list[str] = []
        for child in element:
            if child.tag.endswith("}Property") and len(fields) < 2_000:
                field_name = child.attrib.get("Name", "")
                data_type = child.attrib.get("Type", "unknown")
                if field_name and field_name.replace("_", "").isalnum():
                    fields.append(
                        ERPFieldMetadata(
                            name=field_name,
                            data_type=data_type,
                            required=child.attrib.get("Nullable", "true").casefold() == "false",
                            key=field_name in keys,
                        )
                    )
            elif child.tag.endswith("}NavigationProperty"):
                relation = child.attrib.get("Name", "")
                if relation:
                    relationships.append(relation[:128])
        entity_types[name] = ERPEntityMetadata(
            name=name,
            fields=fields,
            relationships=relationships[:500],
        )
    entity_set_types: dict[str, str] = {}
    for element in root.iter():
        if element.tag.endswith("}EntitySet"):
            name = element.attrib.get("Name", "")
            entity_type = element.attrib.get("EntityType", "").rsplit(".", 1)[-1]
            if name and entity_type:
                entity_set_types[name] = entity_type
    entities: list[ERPEntityMetadata] = []
    for entity_name in sorted(allowed_entities):
        type_name = entity_set_types.get(entity_name, entity_name)
        metadata = entity_types.get(type_name)
        if metadata is not None:
            entities.append(metadata.model_copy(update={"name": entity_name}))
    metadata_hash = hashlib.sha256(payload).hexdigest()
    snapshot_id = hashlib.sha256(
        f"{connector_id}\0{metadata_hash}".encode()
    ).hexdigest()
    return ERPMetadataSnapshot(
        snapshot_id=snapshot_id,
        connector_id=connector_id,
        source_reference=source_reference,
        metadata_hash=metadata_hash,
        entities=entities,
    )


class ODataContextProvider:
    def __init__(
        self,
        *,
        connector_id: str,
        display_name: str,
        kind: ConnectorKind,
        base_url: str,
        principal_id: str,
        credential_target: str,
        secret_store: SecretStore,
        entity_mappings: list[EntityMapping],
    ) -> None:
        if kind not in {ConnectorKind.odata, ConnectorKind.sap, ConnectorKind.dynamics365}:
            raise ValueError("OData provider kind is invalid")
        if not entity_mappings or len(entity_mappings) > 20:
            raise ValueError("OData provider requires 1-20 allow-listed entities")
        self._connector_id = connector_id
        self._display_name = display_name
        self._kind = kind
        self._base_url = validate_remote_base_url(base_url)
        self._principal_id = principal_id
        self._credential_target = credential_target
        self._secret_store = secret_store
        self._entity_mappings = entity_mappings
        self._last_success_at: datetime | None = None

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
        return self._secret_store.get(self._credential_target) is not None

    async def health(self) -> ConnectorHealth:
        enabled = self.auth_enabled
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
            scope_summary=f"{len(self._entity_mappings)} allow-listed ERP entities",
        )

    def _search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        token = self._secret_store.get(self._credential_target)
        if token is None:
            raise PermissionError("connector credential is unavailable")
        client = ReadOnlyJsonClient(self._base_url, token)
        documents: list[ContextDocument] = []
        seen_documents: set[str] = set()
        pages_remaining = _MAXIMUM_ODATA_PAGES_PER_SEARCH
        selected_mappings = [
            mapping
            for mapping in self._entity_mappings
            if not query.entity_types or mapping.entity_name in query.entity_types
        ]
        terms = list(
            dict.fromkeys(
                term.casefold()[:_MAXIMUM_ODATA_TERM_CHARACTERS]
                for term in query.text.split()
                if len(term.strip()) > 1
            )
        )[:4]
        for mapping in selected_mappings:
            fields = mapping.projected_fields
            parameters = {
                "$top": str(_ODATA_PAGE_SIZE),
                "$select": ",".join(fields),
            }
            if mapping.search_mode is EntitySearchMode.server_filter and terms:
                clauses = [
                    f"contains(tolower({field}),'{_odata_string_literal(term)}')"
                    for field in mapping.searchable_fields
                    for term in terms
                ][:_MAXIMUM_ODATA_FILTER_CLAUSES]
                parameters["$filter"] = " or ".join(clauses)
            params = urlencode(parameters)
            entity = mapping.entity_name
            next_page: str | None = f"{quote(entity)}?{params}"
            visited: set[str] = set()
            while next_page is not None and pages_remaining > 0:
                if next_page in visited:
                    raise ValueError("OData pagination contains a cycle")
                visited.add(next_page)
                pages_remaining -= 1
                payload = client.request_json(next_page)
                values = payload.get("value", [])
                if not isinstance(values, list):
                    raise ValueError("OData response value must be an array")
                for row in values:
                    if not isinstance(row, dict):
                        continue
                    projected = {
                        field: row.get(field) for field in fields if field in row
                    }
                    content = json.dumps(projected, ensure_ascii=False, sort_keys=True)
                    if query.text.casefold() not in content.casefold() and not any(
                        term.casefold() in content.casefold() for term in query.text.split()
                    ):
                        continue
                    record = str(row.get(mapping.key_field, "")) or hashlib.sha256(
                        content.encode()
                    ).hexdigest()[:24]
                    display = str(row.get(mapping.display_field, "")) or record
                    document_id = hashlib.sha256(
                        f"{entity}\0{record}".encode()
                    ).hexdigest()
                    if document_id in seen_documents:
                        continue
                    seen_documents.add(document_id)
                    documents.append(
                        ContextDocument(
                            document_id=document_id,
                            connector_id=self.connector_id,
                            source_kind=self.kind,
                            record_id=record,
                            title=f"{entity}: {display}",
                            content=content,
                            entity_type=entity,
                            source_reference=_safe_business_reference(record, display),
                            structured_values={
                                field: str(value)
                                for field, value in projected.items()
                                if value is not None
                            },
                            allowed_principals=[self._principal_id],
                        )
                    )
                raw_next_page = payload.get("@odata.nextLink")
                if raw_next_page is not None and not isinstance(raw_next_page, str):
                    raise ValueError("OData next link must be a string")
                next_page = raw_next_page
        self._last_success_at = datetime.now(UTC)
        return documents

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        if query.principal_id != self._principal_id:
            return []
        return await asyncio.to_thread(self._search, query)

    async def sync(self) -> int:
        token = self._secret_store.get(self._credential_target)
        if token is None:
            raise PermissionError("connector credential is unavailable")
        client = ReadOnlyJsonClient(self._base_url, token)
        await asyncio.to_thread(client.request_json, "")
        self._last_success_at = datetime.now(UTC)
        return 0

    async def metadata_snapshot(self) -> ERPMetadataSnapshot:
        token = self._secret_store.get(self._credential_target)
        if token is None:
            raise PermissionError("connector credential is unavailable")
        client = ReadOnlyJsonClient(self._base_url, token)
        payload = await asyncio.to_thread(client.request_xml, "$metadata")
        host = urlsplit(self._base_url).hostname or "configured-odata-source"
        snapshot = _parse_odata_metadata(
            payload,
            connector_id=self.connector_id,
            source_reference=host,
            allowed_entities={mapping.entity_name for mapping in self._entity_mappings},
        )
        self._last_success_at = datetime.now(UTC)
        return snapshot

    async def close(self) -> None:
        return None


class MicrosoftGraphContextProvider:
    _BASE_URL = "https://graph.microsoft.com/v1.0/"

    def __init__(
        self,
        *,
        connector_id: str,
        display_name: str,
        principal_id: str,
        credential_target: str,
        secret_store: SecretStore,
        drive_ids: list[str] | None = None,
    ) -> None:
        self._connector_id = connector_id
        self._display_name = display_name
        self._principal_id = principal_id
        self._credential_target = credential_target
        self._secret_store = secret_store
        self._last_success_at: datetime | None = None
        self._auth_enabled = False
        self._drive_ids = drive_ids or []

    @property
    def connector_id(self) -> str:
        return self._connector_id

    @property
    def kind(self) -> ConnectorKind:
        return ConnectorKind.microsoft_graph

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def auth_enabled(self) -> bool:
        return self._auth_enabled

    async def health(self) -> ConnectorHealth:
        enabled = (
            await asyncio.to_thread(self._secret_store.get, self._credential_target)
            is not None
        )
        self._auth_enabled = enabled
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
            scope_summary=(
                f"{len(self._drive_ids)} selected SharePoint drive(s)"
                if self._drive_ids
                else "Current user's OneDrive"
            ),
        )

    def _document_from_resource(
        self,
        client: ReadOnlyJsonClient,
        resource: dict[str, object],
        *,
        allow_title_fallback: bool,
    ) -> ContextDocument | None:
        record_id = str(resource.get("id", ""))
        title = str(resource.get("name", ""))
        if not record_id or not title:
            return None
        supported = Path(title).suffix.casefold() in SUPPORTED_REMOTE_EXTENSIONS
        parent = resource.get("parentReference")
        drive_id = str(parent.get("driveId", "")) if isinstance(parent, dict) else ""
        detail = resource
        if "@microsoft.graph.downloadUrl" not in detail and drive_id:
            detail = client.request_json(
                f"drives/{quote(drive_id, safe='')}/items/{quote(record_id, safe='')}?"
                "$select=id,name,webUrl,lastModifiedDateTime,parentReference,file,size"
            )
        download_url = detail.get("@microsoft.graph.downloadUrl") if supported else None
        if isinstance(download_url, str):
            payload = _download_graph_content(download_url)
            content = parse_remote_document(title, payload)
        elif allow_title_fallback:
            content = title
        else:
            return None
        if not content.strip():
            return None
        return ContextDocument(
            document_id=hashlib.sha256(record_id.encode()).hexdigest(),
            connector_id=self.connector_id,
            source_kind=self.kind,
            record_id=record_id,
            title=title,
            content=content,
            uri=detail.get("webUrl") if isinstance(detail.get("webUrl"), str) else None,
            entity_type="knowledge_document",
            source_reference=title,
            allowed_principals=[self._principal_id],
            updated_at=_parse_graph_datetime(detail.get("lastModifiedDateTime")),
        )

    def _search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        token = self._secret_store.get(self._credential_target)
        if token is None:
            raise PermissionError("Microsoft Graph credential is unavailable")
        self._auth_enabled = True
        client = ReadOnlyJsonClient(self._BASE_URL, token)
        documents: list[ContextDocument] = []
        escaped_query = quote(query.text.replace("'", "''"), safe="")
        roots = [f"drives/{quote(drive_id, safe='')}/root" for drive_id in self._drive_ids]
        if not roots:
            roots = ["me/drive/root"]
        pages_remaining = _MAXIMUM_ODATA_PAGES_PER_SEARCH
        for root in roots:
            next_page: str | None = (
                f"{root}/search(q=%27{escaped_query}%27)?"
                f"$top={min(query.limit * 4, 50)}&"
                "$select=id,name,webUrl,lastModifiedDateTime,parentReference"
            )
            while next_page and pages_remaining > 0:
                pages_remaining -= 1
                payload = client.request_json(next_page)
                for resource in payload.get("value", []):
                    if not isinstance(resource, dict):
                        continue
                    try:
                        document = self._document_from_resource(
                            client, resource, allow_title_fallback=True
                        )
                    except (
                        OSError,
                        ValueError,
                        KeyError,
                        ElementTree.ParseError,
                        zipfile.BadZipFile,
                    ):
                        document = None
                    if document is not None:
                        documents.append(document)
                    if len(documents) >= query.limit:
                        break
                raw_next_page = payload.get("@odata.nextLink")
                if raw_next_page is not None and not isinstance(raw_next_page, str):
                    raise ValueError("Graph next link must be a string")
                next_page = raw_next_page
        self._last_success_at = datetime.now(UTC)
        return documents

    def _assurance_documents(self) -> list[ContextDocument]:
        token = self._secret_store.get(self._credential_target)
        if token is None:
            raise PermissionError("Microsoft Graph credential is unavailable")
        client = ReadOnlyJsonClient(self._BASE_URL, token)
        drive_ids = list(self._drive_ids)
        if not drive_ids:
            drive = client.request_json("me/drive?$select=id")
            drive_id = str(drive.get("id", ""))
            if not drive_id:
                raise ValueError("OneDrive scope did not return a drive")
            drive_ids = [drive_id]
        queue = [
            f"drives/{quote(drive_id, safe='')}/root/children?"
            "$top=100&$select=id,name,webUrl,lastModifiedDateTime,parentReference,file,folder,size"
            for drive_id in drive_ids
        ]
        documents: list[ContextDocument] = []
        pages_remaining = _MAXIMUM_GRAPH_ASSURANCE_PAGES
        visited: set[str] = set()
        while queue and pages_remaining > 0 and len(documents) < _MAXIMUM_GRAPH_ASSURANCE_DOCUMENTS:
            path = queue.pop(0)
            if path in visited:
                continue
            visited.add(path)
            pages_remaining -= 1
            payload = client.request_json(path)
            for resource in payload.get("value", []):
                if not isinstance(resource, dict):
                    continue
                item_id = str(resource.get("id", ""))
                parent = resource.get("parentReference")
                drive_id = (
                    str(parent.get("driveId", "")) if isinstance(parent, dict) else ""
                )
                if isinstance(resource.get("folder"), dict) and item_id and drive_id:
                    queue.append(
                        f"drives/{quote(drive_id, safe='')}/items/"
                        f"{quote(item_id, safe='')}/children?$top=100&"
                        "$select=id,name,webUrl,lastModifiedDateTime,"
                        "parentReference,file,folder,size"
                    )
                    continue
                try:
                    document = self._document_from_resource(
                        client, resource, allow_title_fallback=False
                    )
                except (OSError, ValueError, KeyError, ElementTree.ParseError, zipfile.BadZipFile):
                    document = None
                if document is not None:
                    documents.append(document)
                if len(documents) >= _MAXIMUM_GRAPH_ASSURANCE_DOCUMENTS:
                    break
            next_page = payload.get("@odata.nextLink")
            if isinstance(next_page, str):
                queue.append(next_page)
        self._last_success_at = datetime.now(UTC)
        return documents

    async def assurance_documents(self) -> list[ContextDocument]:
        return await asyncio.to_thread(self._assurance_documents)

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        if query.principal_id != self._principal_id:
            return []
        return await asyncio.to_thread(self._search, query)

    async def sync(self) -> int:
        token = await asyncio.to_thread(
            self._secret_store.get, self._credential_target
        )
        if token is None:
            raise PermissionError("Microsoft Graph credential is unavailable")
        client = ReadOnlyJsonClient(self._BASE_URL, token)
        path = (
            f"drives/{quote(self._drive_ids[0], safe='')}?$select=id"
            if self._drive_ids
            else "me/drive?$select=id"
        )
        await asyncio.to_thread(client.request_json, path)
        self._last_success_at = datetime.now(UTC)
        return 0

    async def close(self) -> None:
        return None


def _parse_graph_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
