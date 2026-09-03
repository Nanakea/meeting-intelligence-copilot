"""Deterministic 10,000-event live-ingest recovery stress test.

This provider-free harness exercises reordering, duplicate delivery, simulated
missing POST repair, backend reconstruction, durable-prefix replay repair,
corrupt-session quarantine, subscriber reconnect, and idempotent cleanup. It is
not a substitute for the four-hour routed-audio soak, real sidecar crash,
occupied-port, or process-level WebSocket disconnect runs in the pilot checklist.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.adapters.transcript.live_registry import (  # noqa: E402
    LiveIngestStatus,
    LiveMeetingRegistry,
)
from app.adapters.transcript.live_session_store import LiveSessionStore  # noqa: E402


def payload(sequence_id: int) -> dict[str, object]:
    return {
        "text": f"Routine checkpoint {sequence_id}.",
        "source": "Audio",
        "sequence_id": sequence_id,
        "is_partial": sequence_id % 2 == 0,
        "confidence": 0.9,
        "audio_start_time": sequence_id * 0.25,
        "audio_end_time": sequence_id * 0.25 + 0.2,
        "duration": 0.2,
    }


class FailAfterPrefixStore:
    """Inject a permanent cache-write failure after a durable event prefix."""

    def __init__(self, store: LiveSessionStore, fail_at_sequence: int) -> None:
        self._store = store
        self._fail_at_sequence = fail_at_sequence

    def recover(self, session_id: str) -> Any:
        return self._store.recover(session_id)

    def persist(
        self, session_id: str, adapter: str, lang: Any, event: Any, state: Any
    ) -> None:
        if event.seq >= self._fail_at_sequence:
            raise OSError("simulated cache capacity failure")
        self._store.persist(session_id, adapter, lang, event, state)

    def purge_stale(self, **kwargs: Any) -> int:
        return self._store.purge_stale(**kwargs)

    def delete(self, session_id: str) -> None:
        self._store.delete(session_id)

    def delete_all(self) -> None:
        self._store.delete_all()


def exercise_durable_prefix_repair(cache_dir: Path) -> dict[str, int | float]:
    session_id = "meeting-intel-prefix-repair-0123456789abcdef"
    event_count = 250
    durable_prefix = 73
    with LiveSessionStore(cache_dir=cache_dir) as store:
        failing_store = FailAfterPrefixStore(store, durable_prefix)
        registry = LiveMeetingRegistry(session_store=failing_store)  # type: ignore[arg-type]

        for sequence_id in range(event_count):
            result = registry.ingest_with_ack(session_id, "en", payload(sequence_id))
            assert result.status is LiveIngestStatus.applied
            assert result.next_expected_sequence_id == sequence_id + 1

        recovered = LiveMeetingRegistry(session_store=store)
        prefix_state = recovered.current_state(session_id)
        assert prefix_state is not None
        assert len(prefix_state.transcript) == durable_prefix

        repair_started = time.perf_counter()
        statuses: dict[LiveIngestStatus, int] = {
            status: 0 for status in LiveIngestStatus
        }
        for sequence_id in range(event_count):
            result = recovered.ingest_with_ack(session_id, "en", payload(sequence_id))
            statuses[result.status] += 1
        repair_seconds = time.perf_counter() - repair_started

        final = recovered.current_state(session_id)
        assert final is not None
        assert [event.seq for event in final.transcript] == list(range(event_count))
        assert statuses[LiveIngestStatus.duplicate] == durable_prefix
        assert statuses[LiveIngestStatus.applied] == event_count - durable_prefix
        assert repair_seconds < 30
        return {
            "durable_prefix_events": durable_prefix,
            "replayed_suffix_events": event_count - durable_prefix,
            "durable_repair_seconds": round(repair_seconds, 3),
        }


def exercise_corrupt_cache_quarantine(cache_dir: Path) -> int:
    session_id = "meeting-intel-corrupt-session-0123456789abcdef"
    with LiveSessionStore(cache_dir=cache_dir) as store:
        registry = LiveMeetingRegistry(session_store=store)
        assert (
            registry.ingest_with_ack(session_id, "en", payload(0)).status
            is LiveIngestStatus.applied
        )

        with closing(sqlite3.connect(store.database_path)) as connection:
            with connection:
                connection.execute(
                    "UPDATE live_sessions SET state_json = ? WHERE session_id = ?",
                    ("{not-valid-json", session_id),
                )

        recovered = LiveMeetingRegistry(session_store=store)
        assert recovered.current_state(session_id) is None
        assert store.recover(session_id) is None
        with closing(sqlite3.connect(store.database_path)) as connection:
            quarantined = connection.execute(
                "SELECT COUNT(*) FROM quarantined_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        assert quarantined == 1
        return quarantined


def exercise_disconnect_and_cleanup(cache_dir: Path) -> dict[str, int]:
    session_id = "meeting-intel-reconnect-cleanup-0123456789abcdef"
    with LiveSessionStore(cache_dir=cache_dir) as store:
        registry = LiveMeetingRegistry(session_store=store)

        first_subscriber = registry.subscribe(session_id)
        first = registry.ingest_with_ack(session_id, "en", payload(0))
        assert first.status is LiveIngestStatus.applied
        assert first_subscriber.get_nowait().last_event_seq == 0
        registry.unsubscribe(session_id, first_subscriber)

        assert (
            registry.ingest_with_ack(session_id, "en", payload(1)).status
            is LiveIngestStatus.applied
        )
        reconnected = registry.subscribe(session_id)
        current = registry.current_state(session_id)
        assert current is not None and current.last_event_seq == 1
        assert (
            registry.ingest_with_ack(session_id, "en", payload(2)).status
            is LiveIngestStatus.applied
        )
        assert reconnected.get_nowait().last_event_seq == 2
        registry.unsubscribe(session_id, reconnected)

        assert registry.reset(session_id)
        assert registry.reset(session_id)
        assert store.recover(session_id) is None
        late = registry.ingest_with_ack(session_id, "en", payload(3))
        assert late.status is LiveIngestStatus.rejected
        assert registry.current_state(session_id) is None
        return {"reconnect_last_sequence": 2, "late_rejections": 1}


def run(event_count: int, seed: int) -> dict[str, int | float]:
    if event_count < 1:
        raise ValueError("event_count must be positive")
    rng = random.Random(seed)
    session_id = "meeting-intel-fault-0123456789abcdef"
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="meeting-intelligence-fault-") as directory:
        with LiveSessionStore(cache_dir=Path(directory)) as store:
            registry = LiveMeetingRegistry(session_store=store)
            processing_ms: list[float] = []
            recovery_seconds: list[float] = []

            def deliver(sequence_id: int) -> None:
                event_started = time.perf_counter()
                registry.ingest_with_ack(session_id, "en", payload(sequence_id))
                processing_ms.append((time.perf_counter() - event_started) * 1000)

            for window_start in range(0, event_count, 100):
                window = list(range(window_start, min(window_start + 100, event_count)))
                missing = window.pop(len(window) // 3)
                rng.shuffle(window)
                for sequence_id in window:
                    deliver(sequence_id)
                    if sequence_id % 17 == 0:
                        deliver(sequence_id)
                deliver(missing)

                if window_start and window_start % 1_000 == 0:
                    recovery_started = time.perf_counter()
                    registry = LiveMeetingRegistry(session_store=store)
                    assert registry.current_state(session_id) is not None
                    recovery_elapsed = time.perf_counter() - recovery_started
                    recovery_seconds.append(recovery_elapsed)
                    assert recovery_elapsed < 30

            recovery_started = time.perf_counter()
            registry = LiveMeetingRegistry(session_store=store)
            final = registry.current_state(session_id)
            recovery_elapsed = time.perf_counter() - recovery_started
            recovery_seconds.append(recovery_elapsed)
            assert recovery_elapsed < 30
            assert final is not None
            assert [event.seq for event in final.transcript] == list(range(event_count))
            assert len({event.event_id for event in final.transcript}) == event_count
            assert store.session_cache_bytes(session_id) < 64 * 1024 * 1024
            ordered_latencies = sorted(processing_ms)
            p95 = ordered_latencies[int(len(ordered_latencies) * 0.95)]
            assert p95 < 100
            prefix_repair = exercise_durable_prefix_repair(Path(directory) / "prefix")
            corrupt_quarantine = exercise_corrupt_cache_quarantine(
                Path(directory) / "corrupt"
            )
            reconnect_cleanup = exercise_disconnect_and_cleanup(
                Path(directory) / "reconnect"
            )
            return {
                "event_count": event_count,
                "seed": seed,
                "processing_p95_ms": round(p95, 3),
                "max_recovery_seconds": round(max(recovery_seconds), 3),
                "cache_bytes": store.session_cache_bytes(session_id),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                **prefix_repair,
                "quarantined_corrupt_sessions": corrupt_quarantine,
                **reconnect_cleanup,
            }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260724)
    args = parser.parse_args()
    print(json.dumps(run(args.events, args.seed), indent=2, sort_keys=True))
