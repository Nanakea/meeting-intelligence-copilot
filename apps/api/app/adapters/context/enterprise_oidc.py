"""OIDC authorization-code/PKCE for the optional enterprise gateway."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.adapters.context.windows_security import SecretStore

_REDIRECT_URI = "meeting-intelligence://oauth/enterprise"
_MAXIMUM_RESPONSE_BYTES = 256 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _https_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("enterprise OIDC endpoints must use credential-free HTTPS")
    return value


class TokenExchange(Protocol):
    def __call__(self, url: str, values: dict[str, str]) -> tuple[int, dict[str, Any]]: ...


def _post_form(url: str, values: dict[str, str]) -> tuple[int, dict[str, Any]]:
    _https_url(url)
    request = Request(
        url,
        data=urlencode(values).encode(),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    opener = build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=10.0)
        status = response.status
    except HTTPError as error:
        response = error
        status = error.code
    except (URLError, TimeoutError) as error:
        raise OSError("enterprise OIDC token endpoint is unavailable") from error
    with response:
        payload = response.read(_MAXIMUM_RESPONSE_BYTES + 1)
    if len(payload) > _MAXIMUM_RESPONSE_BYTES:
        raise ValueError("enterprise OIDC response exceeds its bound")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("enterprise OIDC response is invalid")
    return status, value


@dataclass(frozen=True)
class EnterpriseAuthorization:
    authorization_url: str
    state: str
    expires_at: datetime


@dataclass(frozen=True)
class _PendingAuthorization:
    connector_id: str
    credential_target: str
    state: str
    verifier: str
    expires_at: datetime


class EnterpriseOidcPkceManager:
    """Keep PKCE verifiers in memory and delegated tokens in Credential Manager."""

    def __init__(
        self,
        secret_store: SecretStore,
        *,
        authorization_url: str,
        token_url: str,
        client_id: str,
        scopes: list[str],
        exchange: TokenExchange = _post_form,
    ) -> None:
        self._secret_store = secret_store
        self._authorization_url = _https_url(authorization_url)
        self._token_url = _https_url(token_url)
        if not client_id or len(client_id) > 256:
            raise ValueError("enterprise OIDC client ID is invalid")
        cleaned_scopes = list(dict.fromkeys(value.strip() for value in scopes if value.strip()))
        if not cleaned_scopes or any(len(value) > 128 for value in cleaned_scopes):
            raise ValueError("enterprise OIDC scopes are invalid")
        self._client_id = client_id
        self._scope = " ".join(cleaned_scopes)
        self._exchange = exchange
        self._pending: dict[str, _PendingAuthorization] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_env(cls, secret_store: SecretStore) -> EnterpriseOidcPkceManager | None:
        authorization_url = os.environ.get("MEETING_INTELLIGENCE_GATEWAY_OIDC_AUTHORIZATION")
        token_url = os.environ.get("MEETING_INTELLIGENCE_GATEWAY_OIDC_TOKEN")
        client_id = os.environ.get("MEETING_INTELLIGENCE_GATEWAY_OIDC_PUBLIC_CLIENT_ID")
        scopes = os.environ.get("MEETING_INTELLIGENCE_GATEWAY_OIDC_SCOPES", "openid profile")
        if not authorization_url and not token_url and not client_id:
            return None
        if not authorization_url or not token_url or not client_id:
            raise ValueError("enterprise gateway OIDC desktop configuration is incomplete")
        return cls(
            secret_store,
            authorization_url=authorization_url,
            token_url=token_url,
            client_id=client_id,
            scopes=scopes.split(),
        )

    def start(self, connector_id: str, credential_target: str) -> EnterpriseAuthorization:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        expires_at = _now() + timedelta(minutes=10)
        with self._lock:
            self._pending[connector_id] = _PendingAuthorization(
                connector_id=connector_id,
                credential_target=credential_target,
                state=state,
                verifier=verifier,
                expires_at=expires_at,
            )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": _REDIRECT_URI,
                "scope": self._scope,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if urlsplit(self._authorization_url).query else "?"
        return EnterpriseAuthorization(
            authorization_url=f"{self._authorization_url}{separator}{query}",
            state=state,
            expires_at=expires_at,
        )

    def complete(self, connector_id: str, *, code: str, state: str) -> None:
        if not code or len(code) > 8_192 or not state or len(state) > 512:
            raise ValueError("enterprise OIDC callback is invalid")
        with self._lock:
            pending = self._pending.pop(connector_id, None)
        if pending is None or pending.expires_at <= _now():
            raise PermissionError("enterprise OIDC authorization expired")
        if not secrets.compare_digest(pending.state, state):
            raise PermissionError("enterprise OIDC state mismatch")
        status, payload = self._exchange(
            self._token_url,
            {
                "grant_type": "authorization_code",
                "client_id": self._client_id,
                "code": code,
                "code_verifier": pending.verifier,
                "redirect_uri": _REDIRECT_URI,
            },
        )
        if status != 200:
            raise PermissionError("enterprise OIDC authorization was rejected")
        self._store_tokens(pending.credential_target, payload)

    def access_token(self, credential_target: str) -> str | None:
        raw = self._secret_store.get(credential_target)
        if raw is None:
            return None
        try:
            stored = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(stored, dict) or stored.get("kind") != "enterprise_oidc_v1":
            return None
        access_token = stored.get("access_token")
        expires_at = stored.get("expires_at")
        if not isinstance(access_token, str) or not isinstance(expires_at, str):
            return None
        if datetime.fromisoformat(expires_at) > _now() + timedelta(seconds=30):
            return access_token
        refresh_token = stored.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return None
        status, payload = self._exchange(
            self._token_url,
            {
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "refresh_token": refresh_token,
                "scope": self._scope,
            },
        )
        if status != 200:
            if payload.get("error") == "invalid_grant":
                self._secret_store.delete(credential_target)
            return None
        payload.setdefault("refresh_token", refresh_token)
        self._store_tokens(credential_target, payload)
        return str(payload["access_token"])

    def cancel(self, connector_id: str) -> None:
        with self._lock:
            self._pending.pop(connector_id, None)

    def _store_tokens(self, target: str, payload: dict[str, Any]) -> None:
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token or len(access_token) > 16_384:
            raise ValueError("enterprise OIDC access token is invalid")
        refresh_token = payload.get("refresh_token")
        if refresh_token is not None and (
            not isinstance(refresh_token, str) or len(refresh_token) > 16_384
        ):
            raise ValueError("enterprise OIDC refresh token is invalid")
        expires_in = int(payload.get("expires_in", 900))
        expires_in = max(60, min(expires_in, 24 * 60 * 60))
        self._secret_store.put(
            target,
            json.dumps(
                {
                    "kind": "enterprise_oidc_v1",
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_at": (_now() + timedelta(seconds=expires_in)).isoformat(),
                },
                separators=(",", ":"),
            ),
        )
