"""Local-only transport guardrails for the normal intelligence path."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.main import (
    _CORS_ALLOWED_ORIGINS,
    MAX_INGEST_PAYLOAD_BYTES,
    create_app,
    is_allowed_websocket_origin,
    is_valid_session_id,
)


def test_cors_allowlist_has_no_wildcard_or_remote_origin() -> None:
    assert "*" not in _CORS_ALLOWED_ORIGINS
    assert all(
        origin.startswith(("http://localhost", "http://127.0.0.1", "tauri://"))
        or origin == "http://tauri.localhost"
        for origin in _CORS_ALLOWED_ORIGINS
    )


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        (None, True),
        ("http://localhost:3118", True),
        ("http://127.0.0.1:3118", True),
        ("tauri://localhost", True),
        ("http://tauri.localhost", True),
        ("https://example.com", False),
        ("http://192.168.1.20:3118", False),
    ],
)
def test_websocket_origin_allowlist(origin: str | None, allowed: bool) -> None:
    assert is_allowed_websocket_origin(origin) is allowed


def test_websocket_rejects_remote_origin() -> None:
    client = TestClient(create_app(replay_speed=0.0))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/meeting/small-talk-no-pain-en",
            headers={"origin": "https://example.com"},
        ):
            pass


def test_session_ids_are_bounded_and_url_safe() -> None:
    assert is_valid_session_id("meeting-intel-0123456789abcdef")
    for value in ["", "../meeting", "meeting/child", "x" * 129, "meeting?token=leak"]:
        assert not is_valid_session_id(value)


def test_live_ingest_rejects_invalid_language_and_oversized_payload() -> None:
    client = TestClient(create_app(replay_speed=0.0))
    payload = {"text": "hello", "source": "Audio", "sequence_id": 0}

    invalid_language = client.post(
        "/ingest/meetily/meeting-intel-valid",
        json={"lang": "remote", "payload": payload},
    )
    assert invalid_language.status_code == 422

    oversized = client.post(
        "/ingest/meetily/meeting-intel-valid",
        json={"lang": "en", "payload": {"text": "x" * MAX_INGEST_PAYLOAD_BYTES}},
    )
    assert oversized.status_code == 422

    generic_invalid_language = client.post(
        "/ingest/live/meeting-intel-valid",
        json={"adapter": "meetily", "lang": "remote", "payload": payload},
    )
    assert generic_invalid_language.status_code == 422

    generic_oversized = client.post(
        "/ingest/live/meeting-intel-valid",
        json={
            "adapter": "meetily",
            "lang": "en",
            "payload": {"text": "x" * MAX_INGEST_PAYLOAD_BYTES},
        },
    )
    assert generic_oversized.status_code == 422


def test_live_session_cleanup_is_idempotent_and_releases_state() -> None:
    client = TestClient(create_app(replay_speed=0.0))
    session_id = "meeting-intel-cleanup"
    body = {
        "lang": "en",
        "payload": {
            "text": "Every day we copy rows manually.",
            "source": "Audio",
            "sequence_id": 0,
        },
    }
    assert client.post(f"/ingest/meetily/{session_id}", json=body).status_code == 200

    first = client.delete(f"/meeting/{session_id}")
    second = client.delete(f"/meeting/{session_id}")

    assert first.json() == {"reset": True}
    assert second.json() == {"reset": True}


def test_generic_live_ingest_rejects_reserved_unimplemented_adapter() -> None:
    client = TestClient(create_app(replay_speed=0.0))
    response = client.post(
        "/ingest/live/meeting-intel-valid",
        json={
            "adapter": "zoom",
            "lang": "en",
            "payload": {"text": "hello", "source": "Audio", "sequence_id": 0},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "rejected",
        "received_sequence_id": 0,
        "next_expected_sequence_id": 0,
        "state_version": 0,
    }
