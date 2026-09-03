"""Selected-space Microsoft Teams and Slack live-only context adapters."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.adapters.context.windows_security import SecretStore
from app.domain.context import (
    ConnectorHealth,
    ConnectorKind,
    ConnectorPhase,
    ContextDocument,
    ContextSearchQuery,
    ExternalSpaceKind,
    ExternalSpaceSelection,
    MicrosoftTeamsConnectorConfig,
    SlackConnectorConfig,
)

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TRANSIENT = {429, 502, 503, 504}
_HTML_TAG = re.compile(r"<[^>]+>")


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def build_microsoft_client_assertion(
    config: MicrosoftTeamsConnectorConfig,
    private_key_pem: str,
    *,
    now: int | None = None,
) -> str:
    issued = int(time.time()) if now is None else now
    audience = f"https://login.microsoftonline.com/{config.tenant_id}/oauth2/v2.0/token"
    header = {"alg": "RS256", "kid": config.certificate_id, "typ": "JWT"}
    claims = {
        "aud": audience,
        "iss": config.client_id,
        "sub": config.client_id,
        "jti": secrets.token_urlsafe(24),
        "iat": issued,
        "nbf": issued,
        "exp": issued + 300,
    }
    encoded = ".".join(
        _b64url(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        for value in (header, claims)
    )
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("Teams requires an RSA private key of at least 2048 bits")
    signature = key.sign(encoded.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{encoded}.{_b64url(signature)}"


class CollaborationTransport(Protocol):
    def request_json(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, dict[str, object]]: ...


class UrllibCollaborationTransport:
    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, file_pointer, code, message, headers, new_url):
            return None

    def __init__(self, allowed_hosts: set[str], timeout_seconds: float = 5.0) -> None:
        self._allowed_hosts = {host.casefold() for host in allowed_hosts}
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
            or (parsed.hostname or "").casefold() not in self._allowed_hosts
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("collaboration request escaped its fixed origin")
        request = Request(url, method=method, headers=headers, data=body)
        for attempt in range(3):
            try:
                with self._opener.open(request, timeout=self._timeout) as response:
                    payload = response.read(_MAX_RESPONSE_BYTES + 1)
                    if len(payload) > _MAX_RESPONSE_BYTES:
                        raise ValueError("collaboration response exceeds the local bound")
                    decoded = json.loads(payload or b"{}")
                    if not isinstance(decoded, dict):
                        raise ValueError("collaboration response must be an object")
                    return int(response.status), decoded
            except HTTPError as error:
                if error.code not in _TRANSIENT or attempt == 2:
                    raise OSError("collaboration request failed") from None
                retry_after = error.headers.get("Retry-After", "") if error.headers else ""
                delay = (
                    min(float(retry_after), 1.0)
                    if retry_after.isdigit()
                    else 0.25 * (2**attempt)
                )
                time.sleep(delay)
            except URLError as error:
                raise OSError("collaboration source is unavailable") from error
        raise OSError("collaboration source is unavailable")


def _safe_message(value: str, limit: int = 20_000) -> str:
    return " ".join(html.unescape(_HTML_TAG.sub(" ", value)).split())[:limit]


class MicrosoftTeamsContextProvider:
    def __init__(
        self,
        *,
        connector_id: str,
        display_name: str,
        principal_id: str,
        configuration: MicrosoftTeamsConnectorConfig,
        spaces: list[ExternalSpaceSelection],
        external_space_ids: dict[str, str],
        credential_target: str,
        secret_store: SecretStore,
        transport: CollaborationTransport | None = None,
    ) -> None:
        self._connector_id = connector_id
        self._display_name = display_name
        self._principal_id = principal_id
        self.configuration = configuration
        self._spaces = {space.selection_id: space for space in spaces if space.enabled}
        self._external = external_space_ids
        self._credential_target = credential_target
        self._secret_store = secret_store
        self._transport = transport or UrllibCollaborationTransport(
            {"graph.microsoft.com", "login.microsoftonline.com"}
        )
        self._access_token: str | None = None
        self._expires_at = 0.0

    @property
    def connector_id(self) -> str:
        return self._connector_id

    @property
    def kind(self) -> ConnectorKind:
        return ConnectorKind.microsoft_teams

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def auth_enabled(self) -> bool:
        return self._secret_store.get(self._credential_target) is not None

    def _credential(self) -> str:
        value = self._secret_store.get(self._credential_target)
        if value is None:
            raise PermissionError("Teams certificate is unavailable")
        decoded = json.loads(value)
        key = decoded.get("private_key_pem") if isinstance(decoded, dict) else None
        if not isinstance(key, str):
            raise ValueError("Teams credential is invalid")
        return key

    def _token(self) -> str:
        if self._access_token and time.monotonic() < self._expires_at:
            return self._access_token
        token_url = (
            f"https://login.microsoftonline.com/{self.configuration.tenant_id}"
            "/oauth2/v2.0/token"
        )
        assertion = build_microsoft_client_assertion(
            self.configuration, self._credential()
        )
        body = urlencode(
            {
                "client_id": self.configuration.client_id,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
            }
        ).encode()
        status, response = self._transport.request_json(
            token_url,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=body,
        )
        token = response.get("access_token")
        if status != 200 or not isinstance(token, str):
            raise PermissionError("Teams token exchange failed")
        self._access_token = token
        self._expires_at = time.monotonic() + max(
            30, min(int(response.get("expires_in", 300)) - 30, 3_570)
        )
        return token

    def _space_path(self, selection: ExternalSpaceSelection) -> str:
        external = self._external.get(selection.selection_id, "")
        if selection.kind is ExternalSpaceKind.teams_channel:
            parts = external.split("/", 1)
            if len(parts) != 2 or not all(parts):
                raise ValueError("Teams channel selection is invalid")
            return f"teams/{parts[0]}/channels/{parts[1]}/messages?$top=50"
        if selection.kind is ExternalSpaceKind.teams_meeting_chat and external:
            return f"chats/{external}/messages?$top=50"
        raise ValueError("Teams selection kind is invalid")

    def _search_sync(self, query: ContextSearchQuery) -> list[ContextDocument]:
        token = self._token()
        documents: list[ContextDocument] = []
        for selection_id in self.configuration.selected_space_ids:
            selection = self._spaces.get(selection_id)
            if selection is None:
                continue
            path = self._space_path(selection)
            next_url: str | None = f"https://graph.microsoft.com/v1.0/{path}"
            for _ in range(3):
                if next_url is None:
                    break
                status, response = self._transport.request_json(
                    next_url,
                    method="GET",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
                if status != 200:
                    raise OSError("Teams message retrieval failed")
                values = response.get("value", [])
                if not isinstance(values, list):
                    break
                for value in values[:50]:
                    if not isinstance(value, dict):
                        continue
                    body = value.get("body", {})
                    content = (
                        _safe_message(str(body.get("content", "")))
                        if isinstance(body, dict)
                        else ""
                    )
                    message_id = str(value.get("id", ""))
                    if not message_id or not content:
                        continue
                    documents.append(
                        ContextDocument(
                            document_id=hashlib.sha256(
                                f"{self.connector_id}\0{selection_id}\0{message_id}".encode()
                            ).hexdigest(),
                            connector_id=self.connector_id,
                            source_kind=self.kind,
                            record_id=hashlib.sha256(message_id.encode()).hexdigest(),
                            title=f"{self.display_name}: {selection.label}",
                            content=content,
                            uri=str(value.get("webUrl")) if value.get("webUrl") else None,
                            entity_type="teams_message",
                            source_reference=selection.label,
                            allowed_principals=[self._principal_id],
                            updated_at=_parse_time(value.get("lastModifiedDateTime")),
                        )
                    )
                candidate = response.get("@odata.nextLink")
                next_url = candidate if isinstance(candidate, str) else None
        return documents

    async def health(self) -> ConnectorHealth:
        phase = ConnectorPhase.auth_required
        detail = "certificate_required"
        if self.auth_enabled:
            try:
                await asyncio.to_thread(self._token)
                phase, detail = ConnectorPhase.ready, None
            except (OSError, PermissionError, ValueError):
                phase, detail = ConnectorPhase.unavailable, "teams_unavailable"
        return ConnectorHealth(
            connector_id=self.connector_id,
            kind=self.kind,
            display_name=self.display_name,
            phase=phase,
            auth_enabled=self.auth_enabled,
            detail_code=detail,
            scope_summary=f"{len(self.configuration.selected_space_ids)} selected Teams spaces",
        )

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        return await asyncio.to_thread(self._search_sync, query)

    async def sync(self) -> int:
        await asyncio.to_thread(self._token)
        return 0

    async def close(self) -> None:
        self._access_token = None
        self._expires_at = 0.0

    def destination_path(self, destination_ref: str) -> str:
        selection = self._spaces.get(destination_ref)
        if selection is None:
            raise KeyError(destination_ref)
        path = self._space_path(selection).split("?", 1)[0]
        return path


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class SlackContextProvider:
    def __init__(
        self,
        *,
        connector_id: str,
        display_name: str,
        principal_id: str,
        configuration: SlackConnectorConfig,
        spaces: list[ExternalSpaceSelection],
        external_space_ids: dict[str, str],
        credential_target: str,
        secret_store: SecretStore,
        transport: CollaborationTransport | None = None,
    ) -> None:
        self._connector_id = connector_id
        self._display_name = display_name
        self._principal_id = principal_id
        self.configuration = configuration
        self._spaces = {space.selection_id: space for space in spaces if space.enabled}
        self._external = external_space_ids
        self._credential_target = credential_target
        self._secret_store = secret_store
        self._transport = transport or UrllibCollaborationTransport({"slack.com"})

    @property
    def connector_id(self) -> str:
        return self._connector_id

    @property
    def kind(self) -> ConnectorKind:
        return ConnectorKind.slack

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def auth_enabled(self) -> bool:
        return self._secret_store.get(self._credential_target) is not None

    def credentials(self) -> dict[str, str]:
        value = self._secret_store.get(self._credential_target)
        if value is None:
            raise PermissionError("Slack authorization is unavailable")
        decoded = json.loads(value)
        if not isinstance(decoded, dict) or not isinstance(decoded.get("user_token"), str):
            raise ValueError("Slack credential is invalid")
        credentials = {
            key: str(decoded[key])
            for key in (
                "user_token",
                "user_refresh_token",
                "user_expires_at",
                "team_id",
            )
            if isinstance(decoded.get(key), (str, int))
        }
        changed = False
        refresh_token = credentials.get("user_refresh_token")
        expires_at = int(credentials.get("user_expires_at", "0"))
        if refresh_token and expires_at <= int(time.time()) + 60:
            refreshed = self._refresh_token(refresh_token)
            credentials["user_token"] = refreshed["access_token"]
            credentials["user_refresh_token"] = refreshed["refresh_token"]
            credentials["user_expires_at"] = str(
                int(time.time()) + int(refreshed["expires_in"])
            )
            changed = True
        if changed:
            self._secret_store.put(
                self._credential_target,
                json.dumps(credentials, sort_keys=True, separators=(",", ":")),
            )
        return credentials

    def _refresh_token(self, refresh_token: str) -> dict[str, str]:
        request = {
            "grant_type": "refresh_token",
            "client_id": self.configuration.client_id,
            "refresh_token": refresh_token,
        }
        status, response = self._transport.request_json(
            "https://slack.com/api/oauth.v2.access",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode(request).encode(),
        )
        values = {
            key: response.get(key) for key in ("access_token", "refresh_token", "expires_in")
        }
        if (
            status != 200
            or response.get("ok") is not True
            or not isinstance(values["access_token"], str)
            or not isinstance(values["refresh_token"], str)
            or not isinstance(values["expires_in"], int)
        ):
            raise PermissionError("Slack token rotation failed")
        return {key: str(value) for key, value in values.items()}

    def _search_sync(self, query: ContextSearchQuery) -> list[ContextDocument]:
        token = self.credentials()["user_token"]
        documents: list[ContextDocument] = []
        for selection_id in self.configuration.selected_space_ids:
            selection = self._spaces.get(selection_id)
            channel_id = self._external.get(selection_id)
            if selection is None or not channel_id:
                continue
            request_body = {
                "query": query.text,
                "channel_types": [
                    "private_channel"
                    if selection.kind is ExternalSpaceKind.slack_private_channel
                    else "public_channel"
                ],
                "content_types": ["messages"],
                "context_channel_id": channel_id,
                "limit": min(query.limit, 20),
                "disable_semantic_search": True,
            }
            for _ in range(3):
                status, response = self._transport.request_json(
                    "https://slack.com/api/assistant.search.context",
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    body=json.dumps(request_body, separators=(",", ":")).encode(),
                )
                if status != 200 or response.get("ok") is not True:
                    raise OSError("Slack search failed")
                results = response.get("results", {})
                messages = results.get("messages", []) if isinstance(results, dict) else []
                if not isinstance(messages, list):
                    break
                for message in messages[:20]:
                    if not isinstance(message, dict):
                        continue
                    message_channel = str(message.get("channel_id", channel_id))
                    if message_channel != channel_id:
                        continue
                    content = _safe_message(
                        str(message.get("text", message.get("content", "")))
                    )
                    timestamp = str(message.get("ts", message.get("timestamp", "")))
                    if not content or not timestamp:
                        continue
                    documents.append(
                        ContextDocument(
                            document_id=hashlib.sha256(
                                f"{self.connector_id}\0{selection_id}\0{timestamp}".encode()
                            ).hexdigest(),
                            connector_id=self.connector_id,
                            source_kind=self.kind,
                            record_id=hashlib.sha256(timestamp.encode()).hexdigest(),
                            title=f"{self.display_name}: {selection.label}",
                            content=content,
                            uri=(
                                str(message.get("permalink"))
                                if message.get("permalink")
                                else None
                            ),
                            entity_type="slack_message",
                            source_reference=selection.label,
                            allowed_principals=[self._principal_id],
                        )
                    )
                cursor = response.get("next_cursor")
                if not isinstance(cursor, str) or not cursor:
                    break
                request_body["cursor"] = cursor
        return documents

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(
            connector_id=self.connector_id,
            kind=self.kind,
            display_name=self.display_name,
            phase=ConnectorPhase.ready if self.auth_enabled else ConnectorPhase.auth_required,
            auth_enabled=self.auth_enabled,
            detail_code=None if self.auth_enabled else "slack_authorization_required",
            scope_summary=f"{len(self.configuration.selected_space_ids)} selected Slack channels",
        )

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        return await asyncio.to_thread(self._search_sync, query)

    async def sync(self) -> int:
        if not self.auth_enabled:
            raise PermissionError("Slack authorization is unavailable")
        return 0

    async def close(self) -> None:
        return None

@dataclass(frozen=True)
class SlackPkceAuthorization:
    authorization_url: str
    state: str
    expires_at: datetime


class SlackPkceManager:
    """In-memory PKCE verifier storage; authorization secrets are never persisted."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[str, datetime]] = {}

    def start(
        self, connector_id: str, client_id: str, redirect_uri: str
    ) -> SlackPkceAuthorization:
        if redirect_uri != "meeting-intelligence://oauth/slack":
            raise ValueError("Slack redirect must use the registered Meetily deep link")
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        state = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(minutes=10)
        self._pending[connector_id] = (f"{state}\0{verifier}", expires_at)
        query = urlencode(
            {
                "client_id": client_id,
                # Slack rejects bot scopes for desktop/custom-URI PKCE. Search uses
                # a rotating user token; posting credentials are enrolled separately.
                "scope": "",
                "user_scope": "search:read.public,search:read.private",
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return SlackPkceAuthorization(
            authorization_url=f"https://slack.com/oauth/v2/authorize?{query}",
            state=state,
            expires_at=expires_at,
        )

    def consume(self, connector_id: str, state: str) -> str:
        pending = self._pending.pop(connector_id, None)
        if pending is None or pending[1] <= datetime.now(UTC):
            raise ValueError("Slack authorization expired")
        expected, verifier = pending[0].split("\0", 1)
        if not secrets.compare_digest(expected, state):
            raise ValueError("Slack authorization state is invalid")
        return verifier

    def exchange(
        self,
        *,
        connector_id: str,
        client_id: str,
        state: str,
        code: str,
        redirect_uri: str,
        transport: CollaborationTransport | None = None,
    ) -> dict[str, str]:
        if redirect_uri != "meeting-intelligence://oauth/slack":
            raise ValueError("Slack redirect must use the registered Meetily deep link")
        verifier = self.consume(connector_id, state)
        client = transport or UrllibCollaborationTransport({"slack.com"})
        status, response = client.request_json(
            "https://slack.com/api/oauth.v2.access",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode(
                {
                    "client_id": client_id,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": redirect_uri,
                }
            ).encode(),
        )
        authenticated_user = response.get("authed_user", {})
        user_token = (
            authenticated_user.get("access_token")
            if isinstance(authenticated_user, dict)
            else None
        )
        team = response.get("team", {})
        team_id = team.get("id") if isinstance(team, dict) else None
        if (
            status != 200
            or response.get("ok") is not True
            or not isinstance(user_token, str)
            or not isinstance(team_id, str)
        ):
            raise PermissionError("Slack authorization failed")
        now = int(time.time())
        credentials = {"user_token": user_token, "team_id": team_id}
        user_refresh = authenticated_user.get("refresh_token")
        user_expires = authenticated_user.get("expires_in")
        if isinstance(user_refresh, str) and isinstance(user_expires, int):
            credentials["user_refresh_token"] = user_refresh
            credentials["user_expires_at"] = str(now + user_expires)
        return credentials
