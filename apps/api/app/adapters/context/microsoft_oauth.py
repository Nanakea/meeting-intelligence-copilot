"""Microsoft delegated device-code authentication with refresh secrets in CredMan."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.adapters.context.windows_security import SecretStore

_GRAPH_SCOPES = "offline_access Files.Read User.Read"
_MAXIMUM_AUTH_RESPONSE_BYTES = 256 * 1024


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _token_url(tenant_id: str, endpoint: str) -> str:
    if not tenant_id.replace("-", "").replace(".", "").isalnum():
        raise ValueError("Microsoft tenant id is invalid")
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/{endpoint}"


def _post_form(url: str, values: dict[str, str]) -> tuple[int, dict[str, Any]]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "login.microsoftonline.com":
        raise ValueError("Microsoft authentication URL is invalid")
    request = Request(
        url,
        data=urlencode(values).encode(),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    opener = build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=10.0)
        status = response.status
    except HTTPError as error:
        response = error
        status = error.code
    with response:
        payload = response.read(_MAXIMUM_AUTH_RESPONSE_BYTES + 1)
    if len(payload) > _MAXIMUM_AUTH_RESPONSE_BYTES:
        raise ValueError("Microsoft authentication response is too large")
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("Microsoft authentication response is invalid")
    return status, decoded


@dataclass(frozen=True)
class DeviceAuthorization:
    connector_id: str
    verification_uri: str
    user_code: str
    expires_at: datetime
    interval_seconds: int


@dataclass(frozen=True)
class _PendingFlow:
    definition_tenant_id: str
    definition_client_id: str
    device_code: str
    authorization: DeviceAuthorization
    poll_interval_seconds: int
    next_poll_at: datetime


class MicrosoftOAuthCredentialStore:
    """Returns access tokens while persisting only refresh material in CredMan."""

    def __init__(self, raw: SecretStore) -> None:
        self._raw = raw
        self._cache: dict[str, tuple[str, datetime]] = {}
        # Refreshing writes the rotated refresh token while get() is still
        # serialized, so this must permit same-thread re-entry.
        self._lock = threading.RLock()

    def put(self, target: str, secret: str) -> None:
        with self._lock:
            self._cache.pop(target, None)
            self._raw.put(target, secret)

    def put_graph_refresh(
        self,
        target: str,
        *,
        tenant_id: str,
        client_id: str,
        refresh_token: str,
        access_token: str,
        expires_in: int,
    ) -> None:
        stored = json.dumps(
            {
                "kind": "microsoft_graph_refresh_v1",
                "tenant_id": tenant_id,
                "client_id": client_id,
                "refresh_token": refresh_token,
            },
            separators=(",", ":"),
        )
        with self._lock:
            self._raw.put(target, stored)
            self._cache[target] = (
                access_token,
                _utcnow() + timedelta(seconds=max(1, expires_in - 60)),
            )

    def get(self, target: str) -> str | None:
        with self._lock:
            cached = self._cache.get(target)
            if cached is not None and cached[1] > _utcnow():
                return cached[0]
            raw = self._raw.get(target)
            if raw is None:
                return None
            try:
                stored = json.loads(raw)
            except json.JSONDecodeError:
                return raw
            if not isinstance(stored, dict) or stored.get("kind") != "microsoft_graph_refresh_v1":
                return raw
            tenant_id = stored.get("tenant_id")
            client_id = stored.get("client_id")
            refresh_secret = stored.get("refresh_token")
            if not all(
                isinstance(value, str) and value
                for value in (tenant_id, client_id, refresh_secret)
            ):
                self._raw.delete(target)
                return None
            status, payload = _post_form(
                _token_url(tenant_id, "token"),
                {
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_secret,
                    "scope": _GRAPH_SCOPES,
                },
            )
            if status != 200 or not isinstance(payload.get("access_token"), str):
                if payload.get("error") == "invalid_grant":
                    self._raw.delete(target)
                return None
            refresh_token = str(payload.get("refresh_token", refresh_secret))
            expires_in = int(payload.get("expires_in", 3600))
            self.put_graph_refresh(
                target,
                tenant_id=tenant_id,
                client_id=client_id,
                refresh_token=refresh_token,
                access_token=payload["access_token"],
                expires_in=expires_in,
            )
            return payload["access_token"]

    def delete(self, target: str) -> bool:
        with self._lock:
            self._cache.pop(target, None)
            return self._raw.delete(target)


class MicrosoftDeviceCodeManager:
    def __init__(self, credential_store: MicrosoftOAuthCredentialStore) -> None:
        self._credential_store = credential_store
        self._flows: dict[str, _PendingFlow] = {}
        self._lock = threading.Lock()

    def start(
        self, *, connector_id: str, tenant_id: str, client_id: str
    ) -> DeviceAuthorization:
        status, payload = _post_form(
            _token_url(tenant_id, "devicecode"),
            {"client_id": client_id, "scope": _GRAPH_SCOPES},
        )
        if status != 200:
            raise OSError("Microsoft device authorization is unavailable")
        device_code = payload.get("device_code")
        user_code = payload.get("user_code")
        verification_uri = payload.get("verification_uri")
        expires_in = payload.get("expires_in")
        interval = payload.get("interval", 5)
        if (
            not isinstance(device_code, str)
            or not isinstance(user_code, str)
            or not isinstance(verification_uri, str)
            or not isinstance(expires_in, int)
            or isinstance(expires_in, bool)
            or not isinstance(interval, int)
            or isinstance(interval, bool)
            or expires_in <= 0
            or interval <= 0
        ):
            raise ValueError("Microsoft device authorization response is invalid")
        verification = urlsplit(verification_uri)
        if verification.scheme != "https" or verification.hostname not in {
            "microsoft.com",
            "www.microsoft.com",
        }:
            raise ValueError("Microsoft verification URL is invalid")
        poll_interval = min(300, max(5, interval))
        now = _utcnow()
        authorization = DeviceAuthorization(
            connector_id=connector_id,
            verification_uri=verification_uri,
            user_code=user_code,
            expires_at=now + timedelta(seconds=expires_in),
            interval_seconds=poll_interval,
        )
        with self._lock:
            self._flows[connector_id] = _PendingFlow(
                definition_tenant_id=tenant_id,
                definition_client_id=client_id,
                device_code=device_code,
                authorization=authorization,
                poll_interval_seconds=poll_interval,
                next_poll_at=now + timedelta(seconds=poll_interval),
            )
        return authorization

    def poll(self, connector_id: str, credential_target: str) -> str:
        now = _utcnow()
        with self._lock:
            flow = self._flows.get(connector_id)
            if flow is None:
                return "not_started"
            if flow.authorization.expires_at <= now:
                self._flows.pop(connector_id, None)
                return "expired"
            if flow.next_poll_at > now:
                return "pending"
            # Claim the next interval before releasing the lock so concurrent
            # loopback poll requests cannot duplicate token endpoint calls.
            claimed = replace(
                flow,
                next_poll_at=now + timedelta(seconds=flow.poll_interval_seconds),
            )
            self._flows[connector_id] = claimed
        status, payload = _post_form(
            _token_url(flow.definition_tenant_id, "token"),
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": flow.definition_client_id,
                "device_code": flow.device_code,
            },
        )
        if status != 200:
            error = payload.get("error")
            if error in {"authorization_pending", "slow_down"}:
                if error == "slow_down":
                    with self._lock:
                        current = self._flows.get(connector_id)
                        if current is claimed:
                            slower = min(300, current.poll_interval_seconds + 5)
                            self._flows[connector_id] = replace(
                                current,
                                poll_interval_seconds=slower,
                                next_poll_at=now + timedelta(seconds=slower),
                            )
                return "pending"
            with self._lock:
                if self._flows.get(connector_id) is claimed:
                    self._flows.pop(connector_id, None)
            return "declined" if error == "authorization_declined" else "expired"
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            raise ValueError("Microsoft token response is incomplete")
        with self._lock:
            if self._flows.get(connector_id) is not claimed:
                return "pending"
        self._credential_store.put_graph_refresh(
            credential_target,
            tenant_id=flow.definition_tenant_id,
            client_id=flow.definition_client_id,
            refresh_token=refresh_token,
            access_token=access_token,
            expires_in=int(payload.get("expires_in", 3600)),
        )
        with self._lock:
            if self._flows.get(connector_id) is claimed:
                self._flows.pop(connector_id, None)
        return "complete"

    def cancel(self, connector_id: str) -> None:
        with self._lock:
            self._flows.pop(connector_id, None)
