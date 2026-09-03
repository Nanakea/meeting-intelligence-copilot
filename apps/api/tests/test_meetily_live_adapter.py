"""Phase 2 production adapter tests: MeetilyLiveNormalizer + LiveMeetingRegistry.

Payload shapes below are taken directly from real Meetily TranscriptUpdate JSON
captured during the CDP-driven real-ingress acceptance runs (English/Parakeet
and Japanese/Whisper), not invented -- see
docs/MEETILY_INTEGRATION_RESEARCH.md for the full real-ingress evidence.
"""

from __future__ import annotations

import importlib
import json
import logging
import sqlite3
from pathlib import Path

import pytest

from app.adapters.transcript.live_registry import (
    STORE_PURGE_INTERVAL_SECONDS,
    LiveIngestStatus,
    LiveMeetingRegistry,
)
from app.adapters.transcript.live_session_store import (
    CACHE_DIR_ENV,
    DEFAULT_STALE_TTL_SECONDS,
    LiveSessionStore,
    LiveSessionStoreCapacityError,
    default_live_cache_dir,
)
from app.adapters.transcript.meetily_live import MeetilyLiveNormalizer
from app.domain.contracts import Lang, Speaker, TranscriptEvent
from app.services.meeting_engine import MeetingEngine

# Real captured English (Parakeet) TranscriptUpdate.
_REAL_EN_PAYLOAD = {
    "text": "Every morning one person downloads the order file and copies the rows "
    "into another spreadsheet.",
    "timestamp": "15:44:06",
    "source": "Audio",
    "sequence_id": 0,
    "chunk_start_time": 10.68,
    "is_partial": False,
    "confidence": 0.85,
    "audio_start_time": 10.68,
    "audio_end_time": 16.36,
    "duration": 5.68,
}

# Real captured Japanese (Whisper small) TranscriptUpdate -- note is_partial=True.
# whisper_engine.rs sets is_partial = duration_seconds < 15.0 (a chunk-length
# annotation, not a genuine interim/final signal); this must still be accepted.
_REAL_JA_PAYLOAD = {
    "text": "倉庫システム側を制としています",
    "timestamp": "15:51:53",
    "source": "Audio",
    "sequence_id": 9,
    "chunk_start_time": 28.71,
    "is_partial": True,
    "confidence": 0.55,
    "audio_start_time": 28.71,
    "audio_end_time": 31.81,
    "duration": 3.1,
}


def test_default_live_cache_uses_current_user_local_app_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CACHE_DIR_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert default_live_cache_dir() == tmp_path / "Meetily/meeting-intelligence-cache"


def test_live_session_store_close_releases_sqlite_connection(tmp_path: Path) -> None:
    store = LiveSessionStore(cache_dir=tmp_path)

    store.session_cache_bytes("meeting-intel-unused")
    store.close()
    store.close()
    moved_database = tmp_path / "closed.sqlite3"
    store.database_path.replace(moved_database)

    assert moved_database.is_file()
    with pytest.raises(sqlite3.ProgrammingError, match="store is closed"):
        store.session_cache_bytes("meeting-intel-unused")


def test_normalizer_accepts_real_whisper_partial_flagged_content() -> None:
    # The core Phase 2 correction: Whisper's is_partial=True must NOT be rejected.
    n = MeetilyLiveNormalizer(meeting_id="m1", lang=Lang.ja)
    ev = n.normalize(_REAL_JA_PAYLOAD)
    assert ev is not None
    assert ev.text == _REAL_JA_PAYLOAD["text"]
    assert ev.is_final is True
    assert ev.lang == Lang.ja


def test_normalizer_dedupes_by_sequence_id_not_is_partial() -> None:
    n = MeetilyLiveNormalizer(meeting_id="m1", lang=Lang.en)
    first = n.normalize(_REAL_EN_PAYLOAD)
    again = n.normalize(_REAL_EN_PAYLOAD)  # same sequence_id, real duplicate push
    assert first is not None
    assert again is None


def test_normalizer_produces_deterministic_stable_event_ids() -> None:
    n = MeetilyLiveNormalizer(meeting_id="my-meeting", lang=Lang.en)
    ev = n.normalize(_REAL_EN_PAYLOAD)
    assert ev is not None
    assert ev.event_id == "my-meeting:seg0"
    assert ev.meeting_id == "my-meeting"


def test_normalizer_preserves_timestamp_source_confidence_provenance() -> None:
    n = MeetilyLiveNormalizer(meeting_id="m1", lang=Lang.en)
    ev = n.normalize(_REAL_EN_PAYLOAD)
    assert ev is not None
    assert ev.ts_start == _REAL_EN_PAYLOAD["audio_start_time"]
    assert ev.ts_end == _REAL_EN_PAYLOAD["audio_end_time"]
    assert ev.speaker.display_name == "Remote"  # source="Audio" -> Remote


@pytest.mark.parametrize(
    "overrides",
    [
        {"audio_start_time": float("nan")},
        {"audio_end_time": float("inf")},
        {"audio_start_time": -1.0},
        {"audio_start_time": 3.0, "audio_end_time": 2.0},
    ],
)
def test_normalizer_rejects_invalid_audio_timestamp_ranges(
    overrides: dict[str, float],
) -> None:
    normalizer = MeetilyLiveNormalizer(meeting_id="m1", lang=Lang.en)

    with pytest.raises(ValueError, match="audio_"):
        normalizer.normalize({**_REAL_EN_PAYLOAD, **overrides})


def test_normalizer_ignores_empty_text() -> None:
    n = MeetilyLiveNormalizer(meeting_id="m1", lang=Lang.en)
    assert n.normalize({**_REAL_EN_PAYLOAD, "text": "   ", "sequence_id": 1}) is None


def test_normalizer_language_is_never_derived_from_text() -> None:
    # A JA-configured session normalizes English-looking text as JA -- language
    # comes from session config, never from the text's own characters.
    n = MeetilyLiveNormalizer(meeting_id="m1", lang=Lang.ja)
    ev = n.normalize({**_REAL_EN_PAYLOAD, "sequence_id": 5, "text": "pure ASCII text"})
    assert ev is not None and ev.lang == Lang.ja


def test_registry_ingest_broadcasts_only_on_accepted_state_change() -> None:
    reg = LiveMeetingRegistry()
    reg.ingest("m2", "en", _REAL_EN_PAYLOAD)  # creates the meeting, real lang="en"
    q = reg.subscribe("m2")
    state = reg.ingest("m2", "en", {**_REAL_EN_PAYLOAD, "sequence_id": 1, "text": "How often?"})
    assert state is not None and state.version == 2
    assert q.get_nowait().version == 2

    # A malformed/duplicate payload must not raise and must not double-broadcast.
    assert reg.ingest("m2", "en", _REAL_EN_PAYLOAD) is None
    assert q.empty()


def test_registry_ingest_never_raises_on_malformed_payload() -> None:
    reg = LiveMeetingRegistry()
    # Missing every expected field -- must be swallowed, not raised, per Phase 9
    # resilience (a bad event must never break Meetily's own recording pipeline).
    result = reg.ingest("m3", "en", {})
    assert result is None

    # Invalid language is contained by the same never-raise boundary.
    assert reg.ingest("m-invalid-lang", "remote", _REAL_EN_PAYLOAD) is None
    assert reg.current_state("m-invalid-lang") is None


def test_registry_logs_neither_transcript_text_nor_raw_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transcript_secret = "PRIVATE TRANSCRIPT CONTENT 91f2"
    exception_secret = "PRIVATE STORAGE ERROR 73ac"

    class PrivateFailureStore:
        def recover(self, session_id: str) -> None:
            return None

        def persist(self, *args: object) -> None:
            raise OSError(exception_secret)

    registry = LiveMeetingRegistry(session_store=PrivateFailureStore())
    with caplog.at_level(logging.WARNING):
        registry.ingest_with_ack(
            "meeting-intel-private-log-0123456789abcdef",
            "en",
            {**_REAL_EN_PAYLOAD, "text": transcript_secret},
        )

    assert transcript_secret not in caplog.text
    assert exception_secret not in caplog.text


def test_registry_bounds_reorder_buffer_and_allows_later_replay() -> None:
    reg = LiveMeetingRegistry()
    far_future = {**_REAL_EN_PAYLOAD, "sequence_id": 2048}

    assert reg.ingest("m-bounded", "en", far_future) is None
    assert reg.current_state("m-bounded").version == 0

    assert reg.ingest("m-bounded", "en", _REAL_EN_PAYLOAD) is not None


def test_registry_coalesces_slow_subscriber_to_latest_full_snapshot() -> None:
    reg = LiveMeetingRegistry()
    q = reg.subscribe("m-coalesce")

    first = reg.ingest("m-coalesce", "en", _REAL_EN_PAYLOAD)
    second = reg.ingest(
        "m-coalesce",
        "en",
        {**_REAL_EN_PAYLOAD, "sequence_id": 1, "text": "It takes two hours."},
    )

    assert first is not None and second is not None
    assert q.qsize() == 1
    assert q.get_nowait().version == second.version


def test_unsubscribe_removes_empty_pending_session_bucket() -> None:
    reg = LiveMeetingRegistry()
    q = reg.subscribe("m-pending")

    reg.unsubscribe("m-pending", q)

    assert "m-pending" not in reg._pending_subscribers


def test_registry_applies_events_in_sequence_id_order_despite_arrival_order() -> None:
    # Retry and replay traffic can still reach ingest out of order. The registry
    # must apply it to the engine in true sequence_id order.
    reg = LiveMeetingRegistry()
    seg0 = {**_REAL_EN_PAYLOAD, "sequence_id": 0,
            "text": "Every morning one person copies rows into a spreadsheet manually."}
    seg1 = {**_REAL_EN_PAYLOAD, "sequence_id": 1, "text": "How often is that required?"}
    seg2 = {**_REAL_EN_PAYLOAD, "sequence_id": 2,
            "text": "It delays our morning shipping report by about an hour."}

    # seg1 arrives before seg0 (out of order) -- must be buffered, not applied yet.
    assert reg.ingest("m4", "en", seg1) is None
    state = reg.current_state("m4")
    assert state is None or state.version == 0

    # seg0 now arrives: both seg0 and the buffered seg1 should apply, in order.
    state_after_seg0 = reg.ingest("m4", "en", seg0)
    assert state_after_seg0 is not None
    # seg0 alone (manual_work) doesn't trigger business_impact; seg1 is a question.
    assert state_after_seg0.transcript[-1].seq == 1  # seg1 was the last applied
    assert [e.seq for e in state_after_seg0.transcript] == [0, 1]

    # seg2 arrives in order -- applies immediately, no gap.
    state_after_seg2 = reg.ingest("m4", "en", seg2)
    assert state_after_seg2 is not None
    assert [e.seq for e in state_after_seg2.transcript] == [0, 1, 2]


def test_registry_rejected_expected_sequence_does_not_stall_later_events() -> None:
    reg = LiveMeetingRegistry()

    rejected = reg.ingest_with_ack(
        "m-rejected-gap",
        "en",
        {**_REAL_EN_PAYLOAD, "text": "   "},
    )
    applied = reg.ingest_with_ack(
        "m-rejected-gap",
        "en",
        {**_REAL_EN_PAYLOAD, "sequence_id": 1, "text": "The report is delayed."},
    )

    assert rejected.status == LiveIngestStatus.rejected
    assert rejected.next_expected_sequence_id == 1
    assert applied.status == LiveIngestStatus.applied
    assert applied.next_expected_sequence_id == 2
    assert [event.seq for event in applied.state.transcript] == [1]


def test_registry_skips_out_of_order_rejection_when_gap_arrives() -> None:
    reg = LiveMeetingRegistry()

    rejected = reg.ingest_with_ack(
        "m-rejected-buffer",
        "en",
        {**_REAL_EN_PAYLOAD, "sequence_id": 1, "text": ""},
    )
    applied = reg.ingest_with_ack(
        "m-rejected-buffer",
        "en",
        _REAL_EN_PAYLOAD,
    )

    assert rejected.status == LiveIngestStatus.rejected
    assert rejected.next_expected_sequence_id == 0
    assert applied.status == LiveIngestStatus.applied
    assert applied.next_expected_sequence_id == 2
    assert [event.seq for event in applied.state.transcript] == [0]


def test_registry_subscribe_does_not_create_meeting_or_guess_language() -> None:
    # Independent review finding (confirmed real): a WebSocket subscribing before
    # any real transcript push must not create the meeting with a guessed/default
    # language. Only a real ingest() call may do that.
    reg = LiveMeetingRegistry()
    q = reg.subscribe("m5")
    assert reg.current_state("m5") is None  # not created yet

    # First real ingest is authoritative for language, and the pending
    # subscriber must be attached once the meeting is actually created.
    state = reg.ingest("m5", "ja", {**_REAL_JA_PAYLOAD, "sequence_id": 0})
    assert state is not None
    assert state.transcript[0].lang == Lang.ja
    assert q.get_nowait().version == state.version


def test_registry_uses_opaque_session_ids_not_reused_display_titles() -> None:
    """Two recordings with one display title must never share live state.

    The title is deliberately not part of the registry API: it stays Meetily
    display metadata, while the opaque session id is the only live identity.
    """
    reg = LiveMeetingRegistry()
    display_title = "Sales / 在庫差異 🎯"
    session_a = "meeting-intel-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    session_b = "meeting-intel-bbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    state_a = reg.ingest(session_a, "en", _REAL_EN_PAYLOAD)
    state_b = reg.ingest(session_b, "en", _REAL_EN_PAYLOAD)

    assert state_a is not None and state_a.meeting_id == session_a
    assert state_b is not None and state_b.meeting_id == session_b
    assert reg.current_state(display_title) is None

    next_a = reg.ingest(session_a, "en", {**_REAL_EN_PAYLOAD, "sequence_id": 1, "text": "Only A"})
    assert next_a is not None and next_a.meeting_id == session_a
    current_b = reg.current_state(session_b)
    assert current_b is not None and current_b.version == state_b.version


def test_registry_accepts_url_safe_opaque_session_id() -> None:
    session_id = "meeting-intel-0123456789abcdef0123456789abcdef"
    reg = LiveMeetingRegistry()

    state = reg.ingest(session_id, "ja", {**_REAL_JA_PAYLOAD, "sequence_id": 0})

    assert state is not None
    assert state.meeting_id == session_id
    assert reg.current_state(session_id) is state


def test_registry_rebuilds_ordered_state_for_a_new_recording_session() -> None:
    session_id = "meeting-intel-recovery-0123456789abcdef0123456789abcdef"
    replay_session_id = "meeting-intel-replay-0123456789abcdef0123456789abcdef"
    seg0 = {
        **_REAL_EN_PAYLOAD,
        "sequence_id": 0,
        "text": "Every morning one person copies rows into a spreadsheet manually.",
    }
    seg1 = {
        **_REAL_EN_PAYLOAD,
        "sequence_id": 1,
        "text": "It delays our morning shipping report by about an hour.",
    }

    reg = LiveMeetingRegistry()
    original = reg.ingest(session_id, "en", seg0)
    original = reg.ingest(session_id, "en", seg1)
    assert original is not None

    reg.reset(session_id)
    assert reg.current_state(session_id) is None
    assert reg.ingest(session_id, "en", seg0) is None

    assert reg.ingest(replay_session_id, "en", seg0) is not None
    replayed = reg.ingest(replay_session_id, "en", seg1)

    assert replayed is not None
    assert replayed.version == original.version
    assert [event.seq for event in replayed.transcript] == [0, 1]
    assert [pain.template_id for pain in replayed.pain_points] == [
        pain.template_id for pain in original.pain_points
    ]
    assert [(fact.slot, fact.value) for fact in replayed.facts] == [
        (fact.slot, fact.value) for fact in original.facts
    ]
    assert [suggestion.role for suggestion in replayed.suggestions] == [
        suggestion.role for suggestion in original.suggestions
    ]


def test_registry_recovers_persisted_live_session_and_continues_versions(
    tmp_path: Path,
) -> None:
    session_id = "meeting-intel-recover-cache-0123456789abcdef"
    store = LiveSessionStore(cache_dir=tmp_path)
    first = LiveMeetingRegistry(session_store=store)

    assert first.ingest(session_id, "en", _REAL_EN_PAYLOAD) is not None
    second_state = first.ingest(
        session_id,
        "en",
        {
            **_REAL_EN_PAYLOAD,
            "sequence_id": 1,
            "text": "It delays our morning shipping report by about an hour.",
        },
    )
    assert second_state is not None

    recovered = LiveMeetingRegistry(session_store=store)
    current = recovered.current_state(session_id)
    assert current is not None
    assert current.version == second_state.version
    assert [event.seq for event in current.transcript] == [0, 1]

    third_state = recovered.ingest(
        session_id,
        "en",
        {
            **_REAL_EN_PAYLOAD,
            "sequence_id": 2,
            "text": "We still do it by hand each business day.",
        },
    )
    assert third_state is not None
    assert third_state.version == second_state.version + 1
    assert [event.seq for event in third_state.transcript] == [0, 1, 2]
    assert [event.seq for event in store.load_logged_events(session_id)] == [0, 1, 2]


def test_registry_reset_purges_persisted_live_session_cache(tmp_path: Path) -> None:
    session_id = "meeting-intel-purge-cache-0123456789abcdef"
    store = LiveSessionStore(cache_dir=tmp_path)
    registry = LiveMeetingRegistry(session_store=store)

    assert registry.ingest(session_id, "ja", {**_REAL_JA_PAYLOAD, "sequence_id": 0}) is not None
    assert store.load_logged_events(session_id)

    registry.reset(session_id)

    recovered = LiveMeetingRegistry(session_store=store)
    assert recovered.current_state(session_id) is None
    assert store.load_logged_events(session_id) == []


def test_registry_reset_contains_cache_delete_failure() -> None:
    class BrokenDeleteStore:
        def delete(self, session_id: str) -> None:
            raise OSError("simulated storage failure")

    registry = LiveMeetingRegistry(session_store=BrokenDeleteStore())

    reset = registry.reset("meeting-intel-delete-failure")

    assert reset is False
    assert registry.current_state("meeting-intel-delete-failure") is None


def test_registry_retries_transient_cache_cleanup_without_reopening_session() -> None:
    class FlakyDeleteStore:
        def __init__(self) -> None:
            self.delete_calls = 0

        def delete(self, session_id: str) -> None:
            self.delete_calls += 1
            if self.delete_calls == 1:
                raise OSError("simulated transient failure")

    store = FlakyDeleteStore()
    registry = LiveMeetingRegistry(session_store=store)
    session_id = "meeting-intel-flaky-cleanup-0123456789abcdef"
    registry.reset(session_id)

    late = registry.ingest_with_ack(session_id, "en", _REAL_EN_PAYLOAD)

    assert late.status == LiveIngestStatus.rejected
    assert store.delete_calls == 2
    assert registry.current_state(session_id) is None


def test_registry_delete_all_reports_cache_purge_failure() -> None:
    class BrokenDeleteAllStore:
        def delete_all(self) -> None:
            raise OSError("simulated delete-all failure")

    registry = LiveMeetingRegistry(session_store=BrokenDeleteAllStore())

    assert registry.reset_all() is False


def test_long_running_registry_periodically_purges_stale_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LiveSessionStore(cache_dir=tmp_path)
    purge_calls = 0
    real_purge_stale = store.purge_stale

    def track_purge(*, exclude_session_ids: set[str]) -> int:
        nonlocal purge_calls
        purge_calls += 1
        assert exclude_session_ids == {
            "meeting-intel-periodic-purge-0123456789abcdef"
        }
        return real_purge_stale()

    monkeypatch.setattr(store, "purge_stale", track_purge)
    registry = LiveMeetingRegistry(session_store=store)
    session_id = "meeting-intel-periodic-purge-0123456789abcdef"
    assert registry.ingest(session_id, "en", _REAL_EN_PAYLOAD) is not None
    registry._last_store_purge_at -= STORE_PURGE_INTERVAL_SECONDS

    result = registry.ingest_with_ack(
        session_id,
        "en",
        {**_REAL_EN_PAYLOAD, "sequence_id": 1},
    )

    assert result.status == LiveIngestStatus.applied
    assert purge_calls == 1


def test_sqlite_store_keeps_compact_snapshot_and_rebuilds_transcript(tmp_path: Path) -> None:
    session_id = "meeting-intel-compact-cache-0123456789abcdef"
    store = LiveSessionStore(cache_dir=tmp_path)
    registry = LiveMeetingRegistry(session_store=store)

    state = registry.ingest(session_id, "en", _REAL_EN_PAYLOAD)

    assert state is not None
    with sqlite3.connect(store.database_path) as connection:
        stored_state = json.loads(
            connection.execute(
                "SELECT state_json FROM live_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        )
    assert stored_state["transcript"] == []
    assert store.recover(session_id).state.transcript == state.transcript


def test_v5_recovery_ignores_legacy_snapshot_and_replays_valid_log(tmp_path: Path) -> None:
    session_id = "meeting-intel-v4-replay-0123456789abcdef"
    store = LiveSessionStore(cache_dir=tmp_path)
    registry = LiveMeetingRegistry(session_store=store)
    state = registry.ingest(session_id, "en", _REAL_EN_PAYLOAD)
    assert state is not None

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE live_sessions SET state_json = ?, state_schema_version = 5 "
            "WHERE session_id = ?",
            ('{"version":999,"pain_points":[{"legacy":true}]}', session_id),
        )

    recovered = store.recover(session_id)
    assert recovered is not None
    assert recovered.state.transcript == state.transcript
    assert all("::pi-" in pain.pain_id for pain in recovered.state.pain_points)


def test_v5_recovery_preserves_pre_promotion_problem_ids(tmp_path: Path) -> None:
    session_id = "meeting-intel-v5-identity-0123456789abcdef"
    events = [
        TranscriptEvent(
            event_id=f"legacy-event-{seq}",
            meeting_id=session_id,
            seq=seq,
            speaker=Speaker(id="speaker"),
            text=text,
            lang=Lang.en,
            ts_start=float(seq),
            ts_end=float(seq + 1),
            source="test",
        )
        for seq, text in enumerate(
            [
                "Inventory figures do not match.",
                "SAP and EC inventory figures do not match.",
            ]
        )
    ]
    legacy_engine = MeetingEngine(
        session_id,
        allow_identity_promotion=False,
    )
    for transcript_event in events:
        assert legacy_engine.apply(transcript_event)
    legacy_ids = [pain.pain_id for pain in legacy_engine.state.pain_points]
    assert len(legacy_ids) == 2

    store = LiveSessionStore(cache_dir=tmp_path)
    store.persist_batch(
        session_id,
        "meetily",
        Lang.en,
        events,
        legacy_engine.state,
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE live_sessions SET state_schema_version = 5 WHERE session_id = ?",
            (session_id,),
        )

    recovered = store.recover(session_id)
    assert recovered is not None
    assert [pain.pain_id for pain in recovered.state.pain_points] == legacy_ids


def test_sqlite_store_counts_utf8_bytes_not_characters(tmp_path: Path) -> None:
    session_id = "meeting-intel-ja-cache-bytes-0123456789abcdef"
    store = LiveSessionStore(cache_dir=tmp_path)
    registry = LiveMeetingRegistry(session_store=store)

    state = registry.ingest(
        session_id,
        "ja",
        {**_REAL_JA_PAYLOAD, "sequence_id": 0},
    )

    assert state is not None
    expected_bytes = len(state.transcript[0].model_dump_json().encode("utf-8"))
    assert store.session_event_bytes(session_id) == expected_bytes


def test_sqlite_store_quarantines_snapshot_with_missing_evidence_row(
    tmp_path: Path,
) -> None:
    session_id = "meeting-intel-corrupt-evidence-0123456789abcdef"
    store = LiveSessionStore(cache_dir=tmp_path)
    registry = LiveMeetingRegistry(session_store=store)
    assert registry.ingest(session_id, "en", _REAL_EN_PAYLOAD) is not None

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "DELETE FROM transcript_events WHERE session_id = ?",
            (session_id,),
        )

    assert store.recover(session_id) is None
    with sqlite3.connect(store.database_path) as connection:
        quarantined = connection.execute(
            "SELECT reason_code FROM quarantined_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    assert quarantined == ("invalid_session_cache",)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE quarantined_sessions SET quarantined_at = 1 WHERE session_id = ?",
            (session_id,),
        )
    store.purge_stale(now=DEFAULT_STALE_TTL_SECONDS + 2)
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM quarantined_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone() is None


def test_sqlite_store_enforces_per_session_event_budget(tmp_path: Path) -> None:
    session_id = "meeting-intel-bounded-cache-0123456789abcdef"
    store = LiveSessionStore(cache_dir=tmp_path, max_session_event_bytes=1)
    registry = LiveMeetingRegistry()
    state = registry.ingest(session_id, "en", _REAL_EN_PAYLOAD)
    assert state is not None

    with pytest.raises(LiveSessionStoreCapacityError):
        store.persist(
            session_id,
            "meetily",
            Lang.en,
            state.transcript[0],
            state,
        )


def test_sqlite_store_purges_only_expired_crash_recovery_sessions(tmp_path: Path) -> None:
    expired_id = "meeting-intel-expired-cache-0123456789abcdef"
    active_id = "meeting-intel-active-cache-0123456789abcdef"
    store = LiveSessionStore(cache_dir=tmp_path, stale_ttl_seconds=60)
    registry = LiveMeetingRegistry(session_store=store)
    assert registry.ingest(expired_id, "en", _REAL_EN_PAYLOAD) is not None
    assert registry.ingest(active_id, "en", _REAL_EN_PAYLOAD) is not None

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE live_sessions SET updated_at = 1",
        )
    assert store.purge_stale(now=62, exclude_session_ids={active_id}) == 1
    assert store.recover(expired_id) is None
    assert store.recover(active_id) is not None


def test_default_store_failure_falls_back_to_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("app.adapters.transcript.live_registry")

    class BrokenStore:
        def __init__(self) -> None:
            raise OSError("simulated disk full")

    monkeypatch.setattr(module, "LiveSessionStore", BrokenStore)
    assert module._default_session_store() is None


def test_registry_continues_in_memory_when_cache_becomes_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LiveSessionStore(cache_dir=tmp_path)

    def fail_connection() -> None:
        raise sqlite3.DatabaseError("simulated corrupt cache")

    monkeypatch.setattr(store, "_connect", fail_connection)
    registry = LiveMeetingRegistry(session_store=store)

    result = registry.ingest_with_ack(
        "meeting-intel-corrupt-cache-0123456789abcdef",
        "en",
        _REAL_EN_PAYLOAD,
    )

    assert result.status == LiveIngestStatus.applied
    assert result.state is not None


def test_registry_never_writes_newer_snapshot_after_event_cache_failure() -> None:
    class FailingPrefixStore:
        def __init__(self) -> None:
            self.persist_calls = 0

        def recover(self, session_id: str) -> None:
            return None

        def persist(self, *args: object) -> None:
            self.persist_calls += 1
            raise OSError("simulated disk full")

    store = FailingPrefixStore()
    registry = LiveMeetingRegistry(session_store=store)
    session_id = "meeting-intel-cache-prefix-0123456789abcdef"

    first = registry.ingest_with_ack(session_id, "en", _REAL_EN_PAYLOAD)
    second = registry.ingest_with_ack(
        session_id,
        "en",
        {**_REAL_EN_PAYLOAD, "sequence_id": 1, "text": "The report is delayed."},
    )

    assert first.status == LiveIngestStatus.applied
    assert second.status == LiveIngestStatus.applied
    assert store.persist_calls == 1
