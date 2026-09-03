"""Live meeting registry (Phase 2 production adapter wiring).

Holds one ``MeetingEngine`` + ``MeetilyLiveNormalizer`` per live opaque session_id, fed
by real Meetily TranscriptUpdate payloads pushed over HTTP (see
``app/api/main.py``'s ingestion route). Broadcasts each accepted ``MeetingState``
to every subscribed WebSocket via a per-subscriber asyncio.Queue.

Lives in app/adapters (not app/services): it depends on the MeetilyLiveNormalizer
adapter, and domain/services may not import adapters (docs/ARCHITECTURE.md §4).
No FastAPI/vendor SDK import here either, so this stays testable without a
running web server.

Resilience (Phase 9): if a payload fails to normalize or the engine raises, the
error is contained per-event -- it never propagates to Meetily's own recording
pipeline, and other subscribers/meetings are unaffected.

CORRECTED (independent review findings, both confirmed real):

1. Ordering. Network retries, replay repair, or older clients can deliver
   requests out of the order Meetily produced them in.
   Each meeting now tracks the next expected ``sequence_id``; an event that
   arrives early is buffered (``_pending_events``) and only actually applied to
   the engine once every earlier sequence_id has been applied, draining any
   contiguous run that becomes available. This guarantees the engine only ever
   sees true chronological order regardless of network/scheduling races.

2. Language provenance. The registry no longer creates a meeting from a
   WebSocket subscription's (possibly-default/guessed) ``lang``. Only a real
   ``ingest()`` call -- which always carries the session's actual configured
   language -- may create a meeting, so the *first real transcript push* is
   authoritative. A WebSocket that subscribes before any push has arrived is
   queued as a pending subscriber and attached once the meeting is created for
   real.

3. Thread-safety. This class holds shared, unsynchronized state
   (``_meetings``, per-meeting buffers, subscriber lists) and is only safe to
   call from a single asyncio event loop thread. ``app/api/main.py`` MUST
   invoke ``ingest()`` from an ``async def`` route (not a plain ``def``, which
   FastAPI would run in a worker thread) so all mutation stays on one thread.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.adapters.transcript.live_adapters import (
    LiveAdapterName,
    UnsupportedLiveAdapter,
    build_live_session_normalizer,
)
from app.adapters.transcript.live_session_store import LiveSessionStore
from app.domain.context import EntityGlossaryEntry
from app.domain.contracts import Lang, MeetingState, TranscriptEvent
from app.domain.semantic_candidates import SemanticProblemHint
from app.services.meeting_engine import MeetingEngine

logger = logging.getLogger(__name__)
MAX_PENDING_SEQUENCE_GAP = 1024
SUBSCRIBER_QUEUE_SIZE = 1
COMPLETED_SESSION_TTL_SECONDS = 24 * 60 * 60
MAX_COMPLETED_SESSIONS = 10_000
STORE_PURGE_INTERVAL_SECONDS = 60 * 60


class LiveIngestStatus(str, Enum):
    applied = "applied"
    buffered = "buffered"
    duplicate = "duplicate"
    rejected = "rejected"


@dataclass(frozen=True)
class LiveIngestResult:
    status: LiveIngestStatus
    received_sequence_id: int | None
    next_expected_sequence_id: int
    state_version: int
    state: MeetingState | None = None


@dataclass
class _LiveMeeting:
    adapter: LiveAdapterName
    engine: MeetingEngine
    normalizer: Any
    subscribers: list[asyncio.Queue[MeetingState]] = field(default_factory=list)
    next_expected_seq: int = 0
    pending_events: dict[int, TranscriptEvent] = field(default_factory=dict)
    rejected_sequences: set[int] = field(default_factory=set)
    persistence_disabled: bool = False


class LiveMeetingRegistry:
    def __init__(
        self,
        session_store: LiveSessionStore | None = None,
        glossary_provider: Callable[[], list[EntityGlossaryEntry]] | None = None,
    ) -> None:
        self._meetings: dict[str, _LiveMeeting] = {}
        self._pending_subscribers: dict[str, list[asyncio.Queue[MeetingState]]] = {}
        self._session_store = session_store
        self._completed_sessions: OrderedDict[str, float] = OrderedDict()
        self._pending_store_deletes: set[str] = set()
        self._last_store_purge_at = time.monotonic()
        self._glossary_provider = glossary_provider

    def set_glossary_provider(
        self, provider: Callable[[], list[EntityGlossaryEntry]] | None
    ) -> None:
        self._glossary_provider = provider

    def _glossary(self) -> list[EntityGlossaryEntry]:
        if self._glossary_provider is None:
            return []
        try:
            return self._glossary_provider()
        except Exception:
            logger.warning("live_meeting: entity glossary unavailable")
            return []

    def _attach_pending_subscribers(self, session_id: str, live: _LiveMeeting) -> None:
        live.subscribers.extend(self._pending_subscribers.pop(session_id, []))

    def _restore_meeting(self, session_id: str) -> _LiveMeeting | None:
        if self._session_store is None:
            return None
        try:
            recovered = self._session_store.recover(session_id)
        except Exception:
            logger.warning("live_meeting: session recovery unavailable for %s", session_id)
            return None
        if recovered is None:
            return None
        try:
            normalizer = build_live_session_normalizer(
                recovered.adapter, session_id, recovered.lang
            )
        except Exception:
            logger.warning("live_meeting: recovered adapter unavailable for %s", session_id)
            try:
                self._session_store.delete(session_id)
            except Exception:
                pass
            return None
        if hasattr(normalizer, "seed_seen_sequence_ids"):
            normalizer.seed_seen_sequence_ids(event.seq for event in recovered.state.transcript)
        live = _LiveMeeting(
            adapter=recovered.adapter,
            engine=MeetingEngine.from_state(recovered.state, self._glossary()),
            normalizer=normalizer,
            next_expected_seq=max(recovered.state.last_event_seq + 1, 0),
        )
        self._attach_pending_subscribers(session_id, live)
        self._meetings[session_id] = live
        return live

    def _get_live_meeting(self, session_id: str) -> _LiveMeeting | None:
        if session_id in self._completed_sessions:
            return None
        return self._meetings.get(session_id) or self._restore_meeting(session_id)

    def ingest(
        self,
        session_id: str,
        lang: str,
        payload: dict[str, Any],
        *,
        adapter: LiveAdapterName = "meetily",
    ) -> MeetingState | None:
        """Compatibility wrapper returning state only for an applied update."""

        return self.ingest_with_ack(
            session_id, lang, payload, adapter=adapter
        ).state

    def ingest_with_ack(
        self,
        session_id: str,
        lang: str,
        payload: dict[str, Any],
        *,
        adapter: LiveAdapterName = "meetily",
        _persist: bool = True,
    ) -> LiveIngestResult:
        """Normalize + apply one real Meetily TranscriptUpdate. Never raises --
        a malformed payload or engine error is logged and skipped so a bad event
        cannot break the live session (Phase 9 resilience).

        Must be called from the single asyncio event loop thread only (see
        module docstring, point 3)."""
        raw_sequence_id = payload.get("sequence_id")
        received_sequence_id = (
            raw_sequence_id
            if isinstance(raw_sequence_id, int) and not isinstance(raw_sequence_id, bool)
            else None
        )
        self._maintain_session_store()
        self._purge_completed_sessions()
        if session_id in self._completed_sessions:
            return LiveIngestResult(
                status=LiveIngestStatus.rejected,
                received_sequence_id=received_sequence_id,
                next_expected_sequence_id=0,
                state_version=0,
            )
        live: _LiveMeeting | None = None
        try:
            live = self._get_live_meeting(session_id)
            if live is None:
                # First real ingest is authoritative for language. Keep creation in
                # the containment boundary so an invalid language never escapes.
                live = _LiveMeeting(
                    adapter=adapter,
                    engine=MeetingEngine(session_id, self._glossary()),
                    normalizer=build_live_session_normalizer(
                        adapter, session_id, Lang(lang)
                    ),
                )
                self._meetings[session_id] = live
                self._attach_pending_subscribers(session_id, live)
            elif live.adapter != adapter or live.normalizer.lang != Lang(lang):
                logger.warning(
                    "live_meeting: rejected session metadata mismatch for %s", session_id
                )
                return self._result(
                    LiveIngestStatus.rejected, received_sequence_id, live
                )

        except UnsupportedLiveAdapter:
            logger.warning("live_meeting: adapter unavailable for %s via %s", session_id, adapter)
            return self._result(
                LiveIngestStatus.rejected, received_sequence_id, live
            )
        except Exception:
            logger.warning("live_meeting: session metadata rejected for %s", session_id)
            return self._result(
                LiveIngestStatus.rejected, received_sequence_id, live
            )

        if (
            isinstance(raw_sequence_id, bool)
            or not isinstance(raw_sequence_id, int)
            or raw_sequence_id < 0
            or raw_sequence_id > live.next_expected_seq + MAX_PENDING_SEQUENCE_GAP
        ):
            logger.warning("live_meeting: rejected invalid sequence for %s", session_id)
            return self._result(
                LiveIngestStatus.rejected, received_sequence_id, live
            )
        if (
            raw_sequence_id < live.next_expected_seq
            or raw_sequence_id in live.pending_events
            or raw_sequence_id in live.rejected_sequences
        ):
            return self._result(
                LiveIngestStatus.duplicate, received_sequence_id, live
            )

        try:
            event = live.normalizer.normalize(payload)
        except Exception:
            logger.warning("live_meeting: event normalization rejected for %s", session_id)
            live.rejected_sequences.add(raw_sequence_id)
            self._drain_ready(session_id, live, persist=_persist)
            return self._result(
                LiveIngestStatus.rejected, received_sequence_id, live
            )
        if event is None:
            live.rejected_sequences.add(raw_sequence_id)
            self._drain_ready(session_id, live, persist=_persist)
            return self._result(
                LiveIngestStatus.rejected, received_sequence_id, live
            )

        live.pending_events[event.seq] = event
        if event.seq > live.next_expected_seq:
            return self._result(
                LiveIngestStatus.buffered, received_sequence_id, live
            )

        final_state, rejected_sequences = self._drain_ready(
            session_id, live, persist=_persist
        )
        if event.seq in rejected_sequences:
            return self._result(
                LiveIngestStatus.rejected, received_sequence_id, live
            )
        if final_state is None:
            return self._result(
                LiveIngestStatus.duplicate, received_sequence_id, live
            )
        return self._result(
            LiveIngestStatus.applied, received_sequence_id, live, final_state
        )

    def _drain_ready(
        self, session_id: str, live: _LiveMeeting, *, persist: bool = True
    ) -> tuple[MeetingState | None, set[int]]:
        """Consume a contiguous run of accepted or explicitly rejected sequences."""

        final_state: MeetingState | None = None
        rejected_sequences: set[int] = set()
        while True:
            if live.next_expected_seq in live.rejected_sequences:
                rejected_sequences.add(live.next_expected_seq)
                live.rejected_sequences.remove(live.next_expected_seq)
                live.next_expected_seq += 1
                continue
            if live.next_expected_seq not in live.pending_events:
                break

            sequence_id = live.next_expected_seq
            next_event = live.pending_events.pop(sequence_id)
            live.next_expected_seq += 1
            try:
                changed = live.engine.apply(next_event)
            except Exception:
                rejected_sequences.add(sequence_id)
                logger.warning("live_meeting: engine rejected event for %s", session_id)
                continue
            if not changed:
                continue

            final_state = live.engine.state
            if (
                persist
                and self._session_store is not None
                and not live.persistence_disabled
            ):
                try:
                    self._session_store.persist(
                        session_id,
                        live.adapter,
                        live.normalizer.lang,
                        next_event,
                        final_state,
                    )
                except Exception:
                    # Never write a newer compact snapshot after losing an event
                    # row: its facts could cite evidence absent from recovery.
                    # Keep the last transactional prefix and let client replay
                    # repair it after restart.
                    live.persistence_disabled = True
                    logger.warning(
                        "live_meeting: session cache unavailable for %s", session_id
                    )
            for q in live.subscribers:
                if q.full():
                    # Snapshots are complete, so a slow subscriber only needs
                    # the newest state; retaining stale states is unbounded debt.
                    q.get_nowait()
                q.put_nowait(final_state)
        return final_state, rejected_sequences

    def batch_persistence_marker(self, session_id: str) -> int:
        """Return the transcript prefix already represented by durable state."""

        live = self._get_live_meeting(session_id)
        return len(live.engine.state.transcript) if live else 0

    def ingest_batch_item_with_ack(
        self,
        session_id: str,
        lang: str,
        payload: dict[str, Any],
        *,
        adapter: LiveAdapterName = "meetily",
    ) -> LiveIngestResult:
        return self.ingest_with_ack(
            session_id,
            lang,
            payload,
            adapter=adapter,
            _persist=False,
        )

    def persist_batch_from(self, session_id: str, transcript_index: int) -> None:
        """Persist all events applied since a batch marker in one transaction."""

        live = self._meetings.get(session_id)
        if (
            live is None
            or self._session_store is None
            or live.persistence_disabled
        ):
            return
        events = live.engine.state.transcript[transcript_index:]
        if not events:
            return
        try:
            self._session_store.persist_batch(
                session_id,
                live.adapter,
                live.normalizer.lang,
                events,
                live.engine.state,
            )
        except Exception:
            live.persistence_disabled = True
            logger.warning("live_meeting: session cache unavailable for %s", session_id)

    @staticmethod
    def _result(
        status: LiveIngestStatus,
        received_sequence_id: int | None,
        live: _LiveMeeting | None,
        state: MeetingState | None = None,
    ) -> LiveIngestResult:
        return LiveIngestResult(
            status=status,
            received_sequence_id=received_sequence_id,
            next_expected_sequence_id=live.next_expected_seq if live else 0,
            state_version=live.engine.state.version if live else 0,
            state=state,
        )

    def current_state(self, session_id: str) -> MeetingState | None:
        live = self._get_live_meeting(session_id)
        return live.engine.state if live else None

    def decide_identity(
        self,
        session_id: str,
        action: str,
        pain_ids: list[str],
        survivor_pain_id: str | None = None,
    ) -> MeetingState:
        live = self._get_live_meeting(session_id)
        if live is None:
            raise KeyError(session_id)
        live.engine.decide_identity(action, pain_ids, survivor_pain_id)
        self._persist_decision_snapshot(session_id, live)
        self._broadcast(live, live.engine.state)
        return live.engine.state

    def confirm_semantic_hint(
        self, session_id: str, hint: SemanticProblemHint
    ) -> MeetingState:
        live = self._get_live_meeting(session_id)
        if live is None:
            raise KeyError(session_id)
        live.engine.confirm_semantic_hint(hint)
        self._persist_decision_snapshot(session_id, live)
        self._broadcast(live, live.engine.state)
        return live.engine.state

    def publish_current(self, session_id: str) -> None:
        live = self._get_live_meeting(session_id)
        if live is not None:
            self._broadcast(live, live.engine.state)

    def record_semantic_hint_decision(
        self, session_id: str, hint_id: str, action: str
    ) -> None:
        if self._session_store is None:
            return
        try:
            self._session_store.record_semantic_hint_decision(
                session_id, hint_id, action
            )
        except Exception:
            logger.warning(
                "live_meeting: semantic hint decision cache unavailable for %s",
                session_id,
            )

    def dismissed_semantic_hint_ids(self, session_id: str) -> set[str]:
        if self._session_store is None:
            return set()
        try:
            return {
                hint_id
                for hint_id, action in self._session_store.semantic_hint_decisions(
                    session_id
                ).items()
                if action == "dismiss"
            }
        except Exception:
            return set()

    def _persist_decision_snapshot(self, session_id: str, live: _LiveMeeting) -> None:
        if self._session_store is None or live.persistence_disabled:
            return
        try:
            self._session_store.persist_snapshot(
                session_id,
                live.adapter,
                live.normalizer.lang,
                live.engine.state,
            )
        except Exception:
            live.persistence_disabled = True
            logger.warning("live_meeting: decision cache unavailable for %s", session_id)

    @staticmethod
    def _broadcast(live: _LiveMeeting, state: MeetingState) -> None:
        for queue in live.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(state)

    def subscribe(self, session_id: str) -> asyncio.Queue[MeetingState]:
        """Subscribe to an opaque session's live state. Does NOT create it --
        only a real ingest() call is authoritative for the meeting's language
        (see module docstring, point 2). If the meeting doesn't exist yet, the
        queue is held as a pending subscriber and attached once it is."""
        q: asyncio.Queue[MeetingState] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        live = self._meetings.get(session_id)
        if live is not None:
            live.subscribers.append(q)
        else:
            self._pending_subscribers.setdefault(session_id, []).append(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue[MeetingState]) -> None:
        live = self._meetings.get(session_id)
        if live and q in live.subscribers:
            live.subscribers.remove(q)
        pending = self._pending_subscribers.get(session_id)
        if pending and q in pending:
            pending.remove(q)
            if not pending:
                self._pending_subscribers.pop(session_id, None)

    def reset(self, session_id: str) -> bool:
        """Drop live state and report whether transient cache cleanup completed."""
        self._meetings.pop(session_id, None)
        self._pending_subscribers.pop(session_id, None)
        self._completed_sessions[session_id] = time.monotonic()
        self._completed_sessions.move_to_end(session_id)
        self._purge_completed_sessions()
        if self._session_store is None:
            return True
        try:
            self._session_store.delete(session_id)
            self._pending_store_deletes.discard(session_id)
        except Exception:
            self._pending_store_deletes.add(session_id)
            logger.warning(
                "live_meeting: session cache cleanup unavailable for %s", session_id
            )
            return False
        return True

    def reset_all(self) -> bool:
        """Drop every live/recovery session for the explicit local delete-all action."""

        session_ids = {*self._meetings, *self._pending_subscribers}
        self._meetings.clear()
        self._pending_subscribers.clear()
        completed_at = time.monotonic()
        for session_id in session_ids:
            self._completed_sessions[session_id] = completed_at
            self._completed_sessions.move_to_end(session_id)
        self._purge_completed_sessions()
        self._pending_store_deletes.clear()
        if self._session_store is None:
            return True
        try:
            self._session_store.delete_all()
        except Exception:
            logger.warning("live_meeting: full recovery cache cleanup unavailable")
            return False
        return True

    def _retry_pending_store_deletes(self) -> None:
        if self._session_store is None:
            return
        for session_id in tuple(self._pending_store_deletes):
            try:
                self._session_store.delete(session_id)
            except Exception:
                continue
            self._pending_store_deletes.remove(session_id)

    def _maintain_session_store(self) -> None:
        self._retry_pending_store_deletes()
        if self._session_store is None:
            return
        now = time.monotonic()
        if now - self._last_store_purge_at < STORE_PURGE_INTERVAL_SECONDS:
            return
        self._last_store_purge_at = now
        try:
            self._session_store.purge_stale(
                exclude_session_ids=set(self._meetings)
            )
        except Exception:
            logger.warning("live_meeting: stale recovery cache cleanup unavailable")

    def _purge_completed_sessions(self) -> None:
        cutoff = time.monotonic() - COMPLETED_SESSION_TTL_SECONDS
        while self._completed_sessions:
            _, completed_at = next(iter(self._completed_sessions.items()))
            if (
                completed_at >= cutoff
                and len(self._completed_sessions) <= MAX_COMPLETED_SESSIONS
            ):
                break
            self._completed_sessions.popitem(last=False)

    def close(self) -> None:
        """Release adapter-owned resources during backend shutdown."""

        if self._session_store is not None:
            self._session_store.close()


def _default_session_store() -> LiveSessionStore | None:
    try:
        return LiveSessionStore()
    except Exception:
        # Recovery durability is subordinate to recording and live intelligence.
        # Avoid logging paths, payloads, or raw storage exceptions.
        logger.warning("live_meeting: recovery cache unavailable; continuing in memory")
        return None


live_registry = LiveMeetingRegistry(session_store=_default_session_store())
