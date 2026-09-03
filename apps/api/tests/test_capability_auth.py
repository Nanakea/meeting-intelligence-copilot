"""Local capability-token transport contract."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.adapters.transcript.live_registry import (
    LiveIngestResult,
    LiveIngestStatus,
    LiveMeetingRegistry,
)
from app.adapters.transcript.live_session_store import LiveSessionStore
from app.api.main import (
    CAPABILITY_TOKEN_HEADER,
    WEBSOCKET_PROTOCOL,
    WEBSOCKET_TOKEN_PREFIX,
    create_app,
    websocket_capability_token,
    websocket_subprotocols,
)

TOKEN = "a" * 64
OTHER_TOKEN = "b" * 64
SESSION_ID = "meeting-intel-auth-test"
INGEST_BODY = {
    "lang": "en",
    "payload": {
        "text": "Every morning one person copies rows manually.",
        "source": "Audio",
        "sequence_id": 0,
    },
}


def _headers(token: str) -> dict[str, str]:
    return {CAPABILITY_TOKEN_HEADER: token}


def _protocols(token: str) -> list[str]:
    return [WEBSOCKET_PROTOCOL, f"{WEBSOCKET_TOKEN_PREFIX}{token}"]


def test_comma_delimited_asgi_subprotocol_scope_is_normalized() -> None:
    async def receive() -> dict[str, str]:
        return {"type": "websocket.disconnect"}

    async def send(_: dict[str, object]) -> None:
        return None

    websocket = WebSocket(
        {
            "type": "websocket",
            "subprotocols": [f"{WEBSOCKET_PROTOCOL}, {WEBSOCKET_TOKEN_PREFIX}{TOKEN}"],
        },
        receive,
        send,
    )

    assert websocket_subprotocols(websocket) == _protocols(TOKEN)
    assert websocket_capability_token(websocket) == TOKEN


def test_auth_enabled_accepts_valid_http_and_websocket_token() -> None:
    client = TestClient(create_app(capability_token=TOKEN))

    compatibility = client.get("/health/compatibility", headers=_headers(TOKEN))
    ingest = client.post(
        f"/ingest/meetily/{SESSION_ID}",
        headers=_headers(TOKEN),
        json=INGEST_BODY,
    )

    assert compatibility.status_code == 200
    assert compatibility.json()["capability_auth"] is True
    assert ingest.status_code == 200
    generic = client.post(
        f"/ingest/live/{SESSION_ID}",
        headers=_headers(TOKEN),
        json={**INGEST_BODY, "adapter": "meetily"},
    )
    assert generic.status_code == 200
    with client.websocket_connect(
        f"/ws/meeting/{SESSION_ID}", subprotocols=_protocols(TOKEN)
    ) as websocket:
        assert websocket.accepted_subprotocol == WEBSOCKET_PROTOCOL
        assert websocket.receive_json()["version"] == 0


@pytest.mark.parametrize("token", [None, "", OTHER_TOKEN, "short"])
def test_auth_enabled_rejects_missing_empty_and_invalid_http_token(
    token: str | None,
) -> None:
    client = TestClient(create_app(capability_token=TOKEN))
    headers = {} if token is None else _headers(token)

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/compatibility", headers=headers).status_code == 401
    assert (
        client.post(
            f"/ingest/meetily/{SESSION_ID}", headers=headers, json=INGEST_BODY
        ).status_code
        == 401
    )
    assert client.delete(f"/meeting/{SESSION_ID}", headers=headers).status_code == 401


@pytest.mark.parametrize("protocols", [[], [WEBSOCKET_PROTOCOL], _protocols(OTHER_TOKEN)])
def test_auth_enabled_rejects_missing_or_invalid_websocket_token(
    protocols: list[str],
) -> None:
    client = TestClient(create_app(capability_token=TOKEN))

    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect(
            f"/ws/meeting/{SESSION_ID}", subprotocols=protocols
        ):
            pass

    assert rejected.value.code == 1008


def test_auth_disabled_is_explicit_and_health_never_leaks_token() -> None:
    client = TestClient(create_app(capability_token=None))

    assert client.get("/health").json() == {"status": "ok"}
    compatibility = client.get("/health/compatibility")
    ingest = client.post(f"/ingest/meetily/{SESSION_ID}", json=INGEST_BODY)
    generic = client.post(
        f"/ingest/live/{SESSION_ID}",
        json={**INGEST_BODY, "adapter": "meetily"},
    )

    assert compatibility.status_code == 200
    assert compatibility.json()["capability_auth"] is False
    assert ingest.status_code == 200
    assert generic.status_code == 200
    assert TOKEN not in client.get("/health").text


def test_auth_enabled_cleanup_resets_live_session_state_idempotently() -> None:
    client = TestClient(create_app(capability_token=TOKEN))

    assert (
        client.post(
            f"/ingest/meetily/{SESSION_ID}",
            headers=_headers(TOKEN),
            json=INGEST_BODY,
        ).status_code
        == 200
    )

    first = client.delete(f"/meeting/{SESSION_ID}", headers=_headers(TOKEN))
    second = client.delete(f"/meeting/{SESSION_ID}", headers=_headers(TOKEN))

    assert first.json() == {"reset": True}
    assert second.json() == {"reset": True}

    with client.websocket_connect(
        f"/ws/meeting/{SESSION_ID}", subprotocols=_protocols(TOKEN)
    ) as websocket:
        assert websocket.accepted_subprotocol == WEBSOCKET_PROTOCOL
        assert websocket.receive_json()["version"] == 0


def test_cleanup_rejects_late_delivery_without_recreating_session() -> None:
    registry = LiveMeetingRegistry()
    client = TestClient(
        create_app(capability_token=TOKEN, live_registry_instance=registry)
    )
    assert (
        client.delete(f"/meeting/{SESSION_ID}", headers=_headers(TOKEN)).status_code
        == 200
    )

    late = client.post(
        f"/ingest/live/{SESSION_ID}",
        headers=_headers(TOKEN),
        json={**INGEST_BODY, "adapter": "meetily"},
    )

    assert late.status_code == 200
    assert late.json()["status"] == "rejected"
    assert registry.current_state(SESSION_ID) is None


def test_cleanup_reports_incomplete_transient_cache_deletion() -> None:
    class BrokenDeleteStore:
        def delete(self, session_id: str) -> None:
            raise OSError("simulated cleanup failure")

    registry = LiveMeetingRegistry(session_store=BrokenDeleteStore())
    client = TestClient(
        create_app(capability_token=TOKEN, live_registry_instance=registry)
    )

    response = client.delete(f"/meeting/{SESSION_ID}", headers=_headers(TOKEN))

    assert response.status_code == 200
    assert response.json() == {"reset": False}
    assert registry.current_state(SESSION_ID) is None


def test_authenticated_delete_all_purges_live_and_recovery_state(tmp_path: Path) -> None:
    store = LiveSessionStore(cache_dir=tmp_path)
    registry = LiveMeetingRegistry(session_store=store)
    client = TestClient(
        create_app(capability_token=TOKEN, live_registry_instance=registry)
    )
    second_session = "meeting-intel-fedcba9876543210fedcba9876543210"
    for session_id in (SESSION_ID, second_session):
        response = client.post(
            f"/ingest/live/{session_id}",
            headers=_headers(TOKEN),
            json={**INGEST_BODY, "adapter": "meetily"},
        )
        assert response.json()["status"] == "applied"

    unauthorized = client.delete("/meetings")
    deleted = client.delete("/meetings", headers=_headers(TOKEN))
    late = client.post(
        f"/ingest/live/{SESSION_ID}",
        headers=_headers(TOKEN),
        json={**INGEST_BODY, "adapter": "meetily"},
    )

    assert unauthorized.status_code == 401
    assert deleted.json() == {"reset": True}
    assert store.load_logged_events(SESSION_ID) == []
    assert store.load_logged_events(second_session) == []
    assert late.json()["status"] == "rejected"


@pytest.mark.parametrize("token", ["", "too-short", "x" * 257, "é" * 64])
def test_invalid_configured_token_fails_closed_at_startup(token: str) -> None:
    with pytest.raises(ValueError, match="32-256 URL-safe ASCII"):
        create_app(capability_token=token)


def test_rejected_token_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(create_app(capability_token=TOKEN))

    with caplog.at_level(logging.DEBUG):
        response = client.get("/health/compatibility", headers=_headers(OTHER_TOKEN))

    assert response.status_code == 401
    assert TOKEN not in caplog.text
    assert OTHER_TOKEN not in caplog.text


def test_generic_live_ingest_and_compatibility_alias_match_versions(tmp_path: Path) -> None:
    registry = LiveMeetingRegistry(session_store=LiveSessionStore(cache_dir=tmp_path))
    client = TestClient(
        create_app(capability_token=TOKEN, live_registry_instance=registry)
    )

    generic = client.post(
        f"/ingest/live/{SESSION_ID}",
        headers=_headers(TOKEN),
        json={**INGEST_BODY, "adapter": "meetily"},
    )
    alias = client.post(
        f"/ingest/meetily/{SESSION_ID}",
        headers=_headers(TOKEN),
        json={
            "lang": "en",
            "payload": {
                "text": "It delays the morning shipping report by about an hour.",
                "source": "Audio",
                "sequence_id": 1,
            },
        },
    )

    assert generic.json() == {
        "status": "applied",
        "received_sequence_id": 0,
        "next_expected_sequence_id": 1,
        "state_version": 1,
    }
    assert alias.json() == {
        "status": "applied",
        "received_sequence_id": 1,
        "next_expected_sequence_id": 2,
        "state_version": 2,
    }


def test_live_ingest_ack_reports_buffer_duplicate_and_gap_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LiveSessionStore(cache_dir=tmp_path)
    persist_calls: list[int] = []
    batch_sizes: list[int] = []
    real_persist = store.persist
    real_persist_batch = store.persist_batch

    def tracked_persist(*args: object, **kwargs: object) -> None:
        persist_calls.append(1)
        real_persist(*args, **kwargs)  # type: ignore[arg-type]

    def tracked_persist_batch(
        session_id: str,
        adapter: str,
        lang: object,
        events: list[object],
        state: object,
    ) -> None:
        batch_sizes.append(len(events))
        real_persist_batch(  # type: ignore[arg-type]
            session_id, adapter, lang, events, state
        )

    monkeypatch.setattr(store, "persist", tracked_persist)
    monkeypatch.setattr(store, "persist_batch", tracked_persist_batch)
    registry = LiveMeetingRegistry(session_store=store)
    client = TestClient(
        create_app(capability_token=TOKEN, live_registry_instance=registry)
    )

    buffered = client.post(
        f"/ingest/live/{SESSION_ID}",
        headers=_headers(TOKEN),
        json={
            **INGEST_BODY,
            "adapter": "meetily",
            "payload": {**INGEST_BODY["payload"], "sequence_id": 2},
        },
    )
    repaired = client.post(
        f"/ingest/live/{SESSION_ID}/batch",
        headers=_headers(TOKEN),
        json={
            "adapter": "meetily",
            "lang": "en",
            "payloads": [
                INGEST_BODY["payload"],
                {
                    **INGEST_BODY["payload"],
                    "sequence_id": 1,
                    "text": "It takes about an hour each morning.",
                },
            ],
        },
    )
    duplicate = client.post(
        f"/ingest/live/{SESSION_ID}",
        headers=_headers(TOKEN),
        json={**INGEST_BODY, "adapter": "meetily"},
    )

    assert buffered.json()["status"] == "buffered"
    assert buffered.json()["next_expected_sequence_id"] == 0
    assert repaired.json() == {
        "status": "applied",
        "received_sequence_id": 1,
        "next_expected_sequence_id": 3,
        "state_version": 3,
        "rejected_sequence_ids": [],
    }
    assert duplicate.json()["status"] == "duplicate"
    assert duplicate.json()["next_expected_sequence_id"] == 3
    assert persist_calls == []
    assert batch_sizes == [3]
    assert [event.seq for event in store.recover(SESSION_ID).state.transcript] == [0, 1, 2]


def test_live_batch_reports_rejected_sequence_without_stalling_following_event() -> None:
    client = TestClient(create_app(capability_token=TOKEN))

    response = client.post(
        f"/ingest/live/{SESSION_ID}/batch",
        headers=_headers(TOKEN),
        json={
            "adapter": "meetily",
            "lang": "en",
            "payloads": [
                {**INGEST_BODY["payload"], "text": ""},
                {
                    **INGEST_BODY["payload"],
                    "sequence_id": 1,
                    "text": "The report is delayed.",
                },
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "applied",
        "received_sequence_id": 1,
        "next_expected_sequence_id": 2,
        "state_version": 1,
        "rejected_sequence_ids": [0],
    }


def test_live_batch_rejects_unsorted_or_oversized_event_lists() -> None:
    client = TestClient(create_app(capability_token=TOKEN))
    unsorted = client.post(
        f"/ingest/live/{SESSION_ID}/batch",
        headers=_headers(TOKEN),
        json={
            "adapter": "meetily",
            "lang": "en",
            "payloads": [
                {**INGEST_BODY["payload"], "sequence_id": 1},
                INGEST_BODY["payload"],
            ],
        },
    )
    oversized = client.post(
        f"/ingest/live/{SESSION_ID}/batch",
        headers=_headers(TOKEN),
        json={
            "adapter": "meetily",
            "lang": "en",
            "payloads": [INGEST_BODY["payload"]] * 201,
        },
    )

    assert unsorted.status_code == 422
    assert oversized.status_code == 422


def test_live_batch_yields_to_compatibility_health_between_events() -> None:
    class SlowRegistry:
        def __init__(self) -> None:
            self.started = threading.Event()

        def ingest_with_ack(
            self,
            session_id: str,
            lang: str,
            payload: dict[str, object],
            *,
            adapter: str,
        ) -> LiveIngestResult:
            del session_id, lang, adapter
            self.started.set()
            time.sleep(0.025)
            sequence_id = int(payload["sequence_id"])
            return LiveIngestResult(
                status=LiveIngestStatus.applied,
                received_sequence_id=sequence_id,
                next_expected_sequence_id=sequence_id + 1,
                state_version=sequence_id + 1,
            )

        def batch_persistence_marker(self, session_id: str) -> int:
            del session_id
            return 0

        def ingest_batch_item_with_ack(
            self,
            session_id: str,
            lang: str,
            payload: dict[str, object],
            *,
            adapter: str,
        ) -> LiveIngestResult:
            return self.ingest_with_ack(
                session_id, lang, payload, adapter=adapter
            )

        def persist_batch_from(self, session_id: str, transcript_index: int) -> None:
            del session_id, transcript_index

    registry = SlowRegistry()
    batch_responses = []
    app = create_app(
        capability_token=TOKEN,
        live_registry_instance=registry,  # type: ignore[arg-type]
    )
    payloads = [
        {**INGEST_BODY["payload"], "sequence_id": sequence_id}
        for sequence_id in range(200)
    ]

    with TestClient(app) as client:
        worker = threading.Thread(
            target=lambda: batch_responses.append(
                client.post(
                    f"/ingest/live/{SESSION_ID}/batch",
                    headers=_headers(TOKEN),
                    json={"adapter": "meetily", "lang": "en", "payloads": payloads},
                )
            ),
            daemon=True,
        )
        worker.start()
        assert registry.started.wait(timeout=1)

        started = time.perf_counter()
        health = client.get("/health/compatibility", headers=_headers(TOKEN))
        health_elapsed = time.perf_counter() - started
        worker.join(timeout=10)

    assert health.status_code == 200
    assert health_elapsed < 1
    assert not worker.is_alive()
    assert batch_responses[0].status_code == 200


def test_generic_live_ingest_recovers_cached_session_after_restart(tmp_path: Path) -> None:
    store = LiveSessionStore(cache_dir=tmp_path)
    first_client = TestClient(
        create_app(
            capability_token=TOKEN,
            live_registry_instance=LiveMeetingRegistry(session_store=store),
        )
    )
    assert (
        first_client.post(
            f"/ingest/live/{SESSION_ID}",
            headers=_headers(TOKEN),
            json={**INGEST_BODY, "adapter": "meetily"},
        ).status_code
        == 200
    )

    recovered_client = TestClient(
        create_app(
            capability_token=TOKEN,
            live_registry_instance=LiveMeetingRegistry(session_store=store),
        )
    )
    with recovered_client.websocket_connect(
        f"/ws/meeting/{SESSION_ID}", subprotocols=_protocols(TOKEN)
    ) as websocket:
        assert websocket.receive_json()["version"] == 0
        assert websocket.receive_json()["version"] == 1


def test_app_shutdown_closes_live_session_store(tmp_path: Path) -> None:
    store = LiveSessionStore(cache_dir=tmp_path)
    app = create_app(
        capability_token=TOKEN,
        live_registry_instance=LiveMeetingRegistry(session_store=store),
    )

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    with pytest.raises(sqlite3.ProgrammingError, match="store is closed"):
        store.session_cache_bytes(SESSION_ID)


def test_live_websocket_handshake_waits_for_durable_recovery() -> None:
    class BlockingRecoveryRegistry:
        def __init__(self) -> None:
            self.recovery_started = threading.Event()
            self.allow_recovery = threading.Event()

        def subscribe(self, session_id: str) -> asyncio.Queue[object]:
            del session_id
            return asyncio.Queue(maxsize=1)

        def current_state(self, session_id: str) -> None:
            del session_id
            self.recovery_started.set()
            assert self.allow_recovery.wait(timeout=2)
            return None

        def unsubscribe(self, session_id: str, queue: asyncio.Queue[object]) -> None:
            del session_id, queue

    registry = BlockingRecoveryRegistry()
    client = TestClient(
        create_app(
            capability_token=TOKEN,
            live_registry_instance=registry,  # type: ignore[arg-type]
        )
    )
    connected = threading.Event()
    received_versions: list[int] = []

    def connect() -> None:
        with client.websocket_connect(
            f"/ws/meeting/{SESSION_ID}", subprotocols=_protocols(TOKEN)
        ) as websocket:
            connected.set()
            received_versions.append(websocket.receive_json()["version"])

    worker = threading.Thread(target=connect, daemon=True)
    worker.start()
    assert registry.recovery_started.wait(timeout=1)
    assert not connected.wait(timeout=0.1)
    registry.allow_recovery.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert received_versions == [0]
