"""Strict read-only NetSuite SuiteTalk REST retrieval adapter."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
from datetime import UTC, datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.adapters.context.windows_security import SecretStore
from app.domain.assurance import ERPEntityMetadata, ERPFieldMetadata, ERPMetadataSnapshot
from app.domain.context import (
    ConnectorHealth,
    ConnectorKind,
    ConnectorPhase,
    ContextDocument,
    ContextSearchQuery,
    NetSuiteConnectorConfig,
    NetSuiteEntityMapping,
)

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_TRANSIENT_STATUSES = {429, 502, 503, 504}
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def netsuite_account_host(account_id: str) -> str:
    normalized = account_id.strip().replace("_", "-").lower()
    if not re.fullmatch(r"[a-z0-9-]{2,64}", normalized):
        raise ValueError("NetSuite account identifier is invalid")
    return f"{normalized}.suitetalk.api.netsuite.com"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def build_client_assertion(
    config: NetSuiteConnectorConfig,
    private_key_pem: str,
    *,
    now: int | None = None,
) -> str:
    issued = int(time.time()) if now is None else now
    audience = (
        f"https://{netsuite_account_host(config.account_id)}"
        "/services/rest/auth/oauth2/v1/token"
    )
    header = {"alg": "PS256", "kid": config.certificate_id, "typ": "JWT"}
    claims = {
        "iss": config.client_id,
        "scope": ["rest_webservices"],
        "aud": audience,
        "iat": issued,
        "exp": issued + 300,
        "jti": secrets.token_urlsafe(24),
    }
    encoded = ".".join(
        _base64url(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        for value in (header, claims)
    )
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("NetSuite requires an RSA private key of at least 2048 bits")
    signature = key.sign(
        encoded.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    return f"{encoded}.{_base64url(signature)}"


def suiteql_literal(value: str) -> str:
    bounded = " ".join(value.split())[:64]
    return bounded.replace("'", "''")


def build_suiteql(mapping: NetSuiteEntityMapping, terms: list[str]) -> str:
    identifiers = [
        mapping.record_type,
        mapping.key_field,
        mapping.display_field,
        *mapping.searchable_fields,
        *mapping.projected_fields,
    ]
    if any(not _IDENTIFIER.fullmatch(value) for value in identifiers):
        raise ValueError("SuiteQL identifiers must be allow-listed")
    projected = ", ".join(mapping.projected_fields)
    clauses = [
        "(" + " OR ".join(
            f"LOWER({field}) LIKE '%{suiteql_literal(term.casefold())}%'"
            for field in mapping.searchable_fields
        ) + ")"
        for term in terms[:4]
        if suiteql_literal(term)
    ]
    where = " AND ".join(clauses) if clauses else "1 = 0"
    return f"SELECT {projected} FROM {mapping.record_type} WHERE {where}"


class NetSuiteTransport(Protocol):
    def request_json(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, dict[str, object]]: ...


class UrllibNetSuiteTransport:
    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, file_pointer, code, message, headers, new_url):
            return None

    def __init__(self, expected_host: str, timeout_seconds: float = 5.0) -> None:
        self._expected_host = expected_host.casefold()
        self._timeout = timeout_seconds
        self._opener = build_opener(self._NoRedirect())

    def request_json(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, dict[str, object]]:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").casefold() != self._expected_host
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("NetSuite request escaped the account-specific origin")
        request = Request(url, method=method, headers=headers, data=body)
        for attempt in range(3):
            try:
                with self._opener.open(request, timeout=self._timeout) as response:
                    payload = response.read(_MAX_RESPONSE_BYTES + 1)
                    if len(payload) > _MAX_RESPONSE_BYTES:
                        raise ValueError("NetSuite response exceeds the local bound")
                    decoded = json.loads(payload or b"{}")
                    if not isinstance(decoded, dict):
                        raise ValueError("NetSuite response must be an object")
                    return int(response.status), decoded
            except HTTPError as error:
                if error.code not in _TRANSIENT_STATUSES or attempt == 2:
                    raise OSError("NetSuite request failed") from None
                retry_after = error.headers.get("Retry-After", "") if error.headers else ""
                delay = (
                    min(float(retry_after), 1.0)
                    if retry_after.isdigit()
                    else 0.25 * (2**attempt)
                )
                time.sleep(delay)
            except URLError as error:
                raise OSError("NetSuite is unavailable") from error
        raise OSError("NetSuite is unavailable")


class NetSuiteContextProvider:
    def __init__(
        self,
        *,
        connector_id: str,
        display_name: str,
        principal_id: str,
        configuration: NetSuiteConnectorConfig,
        credential_target: str,
        secret_store: SecretStore,
        transport: NetSuiteTransport | None = None,
    ) -> None:
        self._connector_id = connector_id
        self._display_name = display_name
        self._principal_id = principal_id
        self.configuration = configuration
        self._credential_target = credential_target
        self._secret_store = secret_store
        self._host = netsuite_account_host(configuration.account_id)
        self._transport = transport or UrllibNetSuiteTransport(self._host)
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0

    @property
    def connector_id(self) -> str:
        return self._connector_id

    @property
    def kind(self) -> ConnectorKind:
        return ConnectorKind.netsuite

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def auth_enabled(self) -> bool:
        return self._secret_store.get(self._credential_target) is not None

    def _credential(self) -> dict[str, str]:
        value = self._secret_store.get(self._credential_target)
        if value is None:
            raise PermissionError("NetSuite credentials are unavailable")
        decoded = json.loads(value)
        if not isinstance(decoded, dict) or not isinstance(decoded.get("private_key_pem"), str):
            raise ValueError("NetSuite credential is invalid")
        return {"private_key_pem": decoded["private_key_pem"]}

    def _token(self) -> str:
        if self._access_token and time.monotonic() < self._access_token_expires_at:
            return self._access_token
        url = f"https://{self._host}/services/rest/auth/oauth2/v1/token"
        assertion = build_client_assertion(
            self.configuration, self._credential()["private_key_pem"]
        )
        body = urlencode(
            {
                "grant_type": "client_credentials",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
            }
        ).encode()
        status, response = self._transport.request_json(
            url,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=body,
        )
        token = response.get("access_token")
        if status != 200 or not isinstance(token, str) or not token:
            raise PermissionError("NetSuite token exchange failed")
        expires_in = response.get("expires_in", 300)
        self._access_token = token
        self._access_token_expires_at = time.monotonic() + max(
            30, min(int(expires_in) - 30, 3_570)
        )
        return token

    def _request(self, path: str, *, method: str = "GET", body: dict | None = None):
        allowed_prefixes = (
            "services/rest/query/v1/suiteql",
            "services/rest/record/v1/metadata-catalog",
        )
        if not path.startswith(allowed_prefixes):
            raise ValueError("NetSuite retrieval endpoint is not allow-listed")
        payload = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}
        if body is not None:
            headers.update({"Content-Type": "application/json", "Prefer": "transient"})
        return self._transport.request_json(
            f"https://{self._host}/{path}", method=method, headers=headers, body=payload
        )

    async def health(self) -> ConnectorHealth:
        if not self.auth_enabled:
            return ConnectorHealth(
                connector_id=self.connector_id,
                kind=self.kind,
                display_name=self.display_name,
                phase=ConnectorPhase.auth_required,
                auth_enabled=False,
                detail_code="certificate_required",
                scope_summary="Read-only NetSuite role",
            )
        try:
            await asyncio.to_thread(self._token)
            phase, detail = ConnectorPhase.ready, None
        except (OSError, PermissionError, ValueError):
            phase, detail = ConnectorPhase.unavailable, "netsuite_unavailable"
        return ConnectorHealth(
            connector_id=self.connector_id,
            kind=self.kind,
            display_name=self.display_name,
            phase=phase,
            auth_enabled=True,
            detail_code=detail,
            scope_summary=f"{len(self.configuration.entity_mappings)} allow-listed record types",
            last_success_at=datetime.now(UTC) if phase is ConnectorPhase.ready else None,
        )

    def _search_sync(self, query: ContextSearchQuery) -> list[ContextDocument]:
        terms = [
            term
            for term in re.findall(r"[\w.-]+", query.text, re.UNICODE)
            if len(term) > 1
        ][:4]
        documents: list[ContextDocument] = []
        for mapping in self.configuration.entity_mappings:
            suiteql = build_suiteql(mapping, terms)
            status, response = self._request(
                "services/rest/query/v1/suiteql?limit=20&offset=0",
                method="POST",
                body={"q": suiteql},
            )
            if status != 200:
                raise OSError("NetSuite query failed")
            items = response.get("items", [])
            if not isinstance(items, list):
                raise ValueError("NetSuite query result is invalid")
            for item in items[:20]:
                if not isinstance(item, dict):
                    continue
                key = str(item.get(mapping.key_field, "")).strip()
                display = str(item.get(mapping.display_field, key)).strip() or key
                if not key:
                    continue
                values = {
                    field: str(item.get(field, ""))[:500]
                    for field in mapping.projected_fields
                    if item.get(field) is not None
                }
                modified = None
                if mapping.modified_field and item.get(mapping.modified_field):
                    try:
                        modified = datetime.fromisoformat(
                            str(item[mapping.modified_field]).replace("Z", "+00:00")
                        )
                    except ValueError:
                        modified = None
                documents.append(
                    ContextDocument(
                        document_id=hashlib.sha256(
                            f"{self.connector_id}\0{mapping.record_type}\0{key}".encode()
                        ).hexdigest(),
                        connector_id=self.connector_id,
                        source_kind=self.kind,
                        record_id=hashlib.sha256(key.encode()).hexdigest(),
                        title=f"{self.display_name}: {display}"[:500],
                        content="\n".join(f"{name}: {value}" for name, value in values.items()),
                        entity_type=mapping.record_type,
                        source_reference=key[:256],
                        structured_values=values,
                        allowed_principals=[self._principal_id],
                        updated_at=modified,
                    )
                )
        return documents[: max(query.limit * 4, query.limit)]

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        return await asyncio.to_thread(self._search_sync, query)

    async def sync(self) -> int:
        # Business records are intentionally live-only.
        await asyncio.to_thread(self._token)
        return 0

    async def close(self) -> None:
        self._access_token = None
        self._access_token_expires_at = 0.0

    async def metadata_snapshot(self) -> ERPMetadataSnapshot:
        selected = ",".join(mapping.record_type for mapping in self.configuration.entity_mappings)
        status, response = await asyncio.to_thread(
            self._request,
            f"services/rest/record/v1/metadata-catalog?select={selected}",
        )
        if status != 200:
            raise OSError("NetSuite metadata is unavailable")
        entities: list[ERPEntityMetadata] = []
        schemas = response.get("components", {})
        schemas = schemas.get("schemas", {}) if isinstance(schemas, dict) else {}
        for mapping in self.configuration.entity_mappings:
            schema = schemas.get(mapping.record_type, {}) if isinstance(schemas, dict) else {}
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
            fields = []
            if isinstance(properties, dict):
                for name, definition in list(properties.items())[:2_000]:
                    if not _IDENTIFIER.fullmatch(str(name)) or not isinstance(definition, dict):
                        continue
                    fields.append(
                        ERPFieldMetadata(
                            name=str(name),
                            data_type=str(definition.get("type", "unknown"))[:120],
                            required=str(name) in required,
                            key=str(name) == mapping.key_field,
                            code_values=[
                                str(value)[:120]
                                for value in definition.get("enum", [])[:500]
                            ],
                        )
                    )
            entities.append(ERPEntityMetadata(name=mapping.record_type, fields=fields))
        digest = hashlib.sha256(json.dumps(response, sort_keys=True).encode()).hexdigest()
        return ERPMetadataSnapshot(
            snapshot_id=hashlib.sha256(f"{self.connector_id}\0{digest}".encode()).hexdigest(),
            connector_id=self.connector_id,
            source_reference=f"{self.display_name} metadata",
            metadata_hash=digest,
            retrieved_at=datetime.now(UTC),
            entities=entities,
            api_version="SuiteTalk REST v1",
        )

    def _quality_records_sync(
        self, mapping: NetSuiteEntityMapping, *, changed_after: datetime | None = None
    ) -> list[dict[str, object]]:
        projected = ", ".join(mapping.projected_fields)
        if any(not _IDENTIFIER.fullmatch(value) for value in mapping.projected_fields):
            raise ValueError("NetSuite quality fields must be allow-listed")
        query = f"SELECT {projected} FROM {mapping.record_type}"
        if changed_after is not None and mapping.modified_field:
            timestamp = changed_after.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            query += (
                f" WHERE {mapping.modified_field} > "
                f"TO_TIMESTAMP_TZ('{suiteql_literal(timestamp)}')"
            )
        records: list[dict[str, object]] = []
        for offset in (0, 200, 400):
            status, response = self._request(
                f"services/rest/query/v1/suiteql?limit=200&offset={offset}",
                method="POST",
                body={"q": query},
            )
            items = response.get("items", [])
            if status != 200 or not isinstance(items, list):
                raise OSError("NetSuite quality query failed")
            records.extend(value for value in items[:200] if isinstance(value, dict))
            if response.get("hasMore") is not True:
                break
        return records

    async def quality_records(
        self, *, changed_after: datetime | None = None
    ) -> dict[str, list[dict[str, object]]]:
        values: dict[str, list[dict[str, object]]] = {}
        for mapping in self.configuration.entity_mappings:
            values[mapping.record_type] = await asyncio.to_thread(
                self._quality_records_sync, mapping, changed_after=changed_after
            )
        return values
