"""Transactional local recovery cache for active live meetings."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from app.adapters.transcript.live_adapters import (
    SUPPORTED_LIVE_ADAPTERS,
    LiveAdapterName,
)
from app.domain.contracts import (
    Lang,
    MeetingState,
    ProblemIdentityDecision,
    TranscriptEvent,
)
from app.services.meeting_engine import MeetingEngine

CACHE_DIR_ENV = "MEETING_INTELLIGENCE_CACHE_DIR"
DEFAULT_CACHE_SUBDIR = "Meetily/meeting-intelligence-cache"
DATABASE_FILE = "live-sessions.sqlite3"
DEFAULT_STALE_TTL_SECONDS = 24 * 60 * 60
MAX_SESSION_EVENT_BYTES = 64 * 1024 * 1024
CURRENT_STATE_SCHEMA_VERSION = 6


class LiveSessionStoreCapacityError(RuntimeError):
    """The bounded recovery cache cannot accept more transcript data."""


@dataclass(frozen=True)
class RecoveredLiveSession:
    adapter: LiveAdapterName
    lang: Lang
    state: MeetingState


def default_live_cache_dir() -> Path:
    configured = os.environ.get(CACHE_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / DEFAULT_CACHE_SUBDIR
    if os.name == "nt":
        return Path.home() / "AppData/Local" / DEFAULT_CACHE_SUBDIR
    return Path(tempfile.gettempdir()) / "meeting-intelligence-copilot/live-sessions"


class LiveSessionStore:
    """SQLite/WAL cache containing compact snapshots and append-only events.

    The snapshot deliberately omits ``MeetingState.transcript``. Recovery
    reconstructs it from the event table, avoiding an O(n) full-state rewrite
    for every utterance while retaining the domain's append-only ground truth.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        max_session_event_bytes: int = MAX_SESSION_EVENT_BYTES,
        stale_ttl_seconds: int = DEFAULT_STALE_TTL_SECONDS,
    ) -> None:
        if max_session_event_bytes < 1:
            raise ValueError("max_session_event_bytes must be positive")
        if stale_ttl_seconds < 1:
            raise ValueError("stale_ttl_seconds must be positive")
        self._cache_dir = cache_dir or default_live_cache_dir()
        self._database_path = self._cache_dir / DATABASE_FILE
        self._max_session_event_bytes = max_session_event_bytes
        self._stale_ttl_seconds = stale_ttl_seconds
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._connection_lock = threading.RLock()
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            self._database_path,
            timeout=2.0,
            check_same_thread=False,
        )
        self._initialize()
        self.purge_stale()

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def __enter__(self) -> LiveSessionStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def database_path(self) -> Path:
        return self._database_path

    def persist(
        self,
        session_id: str,
        adapter: LiveAdapterName,
        lang: Lang,
        event: TranscriptEvent,
        state: MeetingState,
    ) -> None:
        self.persist_batch(session_id, adapter, lang, [event], state)

    def persist_batch(
        self,
        session_id: str,
        adapter: LiveAdapterName,
        lang: Lang,
        events: list[TranscriptEvent],
        state: MeetingState,
    ) -> None:
        """Atomically append a replay run and its final compact snapshot."""

        if not events:
            return
        event_rows = [(event.seq, event.model_dump_json()) for event in events]
        compact_state = state.model_copy(update={"transcript": []}).model_dump_json()
        compact_state_bytes = len(compact_state.encode("utf-8"))
        now = time.time()

        with self._connect() as connection:
            existing_bytes = connection.execute(
                "SELECT COALESCE(SUM(LENGTH(CAST(event_json AS BLOB))), 0) "
                "FROM transcript_events "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            placeholders = ",".join("?" for _ in event_rows)
            existing_sequences = {
                row[0]
                for row in connection.execute(
                    "SELECT seq FROM transcript_events "
                    f"WHERE session_id = ? AND seq IN ({placeholders})",
                    (session_id, *(seq for seq, _ in event_rows)),
                )
            }
            additional_bytes = sum(
                len(event_json.encode("utf-8"))
                for seq, event_json in event_rows
                if seq not in existing_sequences
            )
            if (
                existing_bytes + additional_bytes + compact_state_bytes
                >= self._max_session_event_bytes
            ):
                raise LiveSessionStoreCapacityError("live session recovery cache limit reached")

            connection.execute(
                """
                INSERT INTO live_sessions(
                    session_id, adapter, lang, state_json, updated_at, state_schema_version
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    adapter = excluded.adapter,
                    lang = excluded.lang,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at,
                    state_schema_version = excluded.state_schema_version
                """,
                (
                    session_id,
                    adapter,
                    lang.value,
                    compact_state,
                    now,
                    CURRENT_STATE_SCHEMA_VERSION,
                ),
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO transcript_events(
                    session_id, seq, event_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                ((session_id, seq, event_json, now) for seq, event_json in event_rows),
            )
            self._append_identity_decisions(connection, session_id, state, now)

    def recover(self, session_id: str) -> RecoveredLiveSession | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT adapter, lang, state_json, state_schema_version "
                    "FROM live_sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    return None
                events = [
                    TranscriptEvent.model_validate_json(event_row[0])
                    for event_row in connection.execute(
                        "SELECT event_json FROM transcript_events "
                        "WHERE session_id = ? ORDER BY seq",
                        (session_id,),
                    )
                ]
                identity_decisions = [
                    ProblemIdentityDecision.model_validate_json(decision_row[0])
                    for decision_row in connection.execute(
                        "SELECT decision_json FROM problem_identity_events "
                        "WHERE session_id = ? ORDER BY ordinal",
                        (session_id,),
                    )
                ]
            if any(event.meeting_id != session_id for event in events):
                raise ValueError("session identity mismatch")
            if not events or [event.seq for event in events] != list(range(len(events))):
                raise ValueError("event log sequence gap")
            stored_schema_version = int(row[3])
            if stored_schema_version >= CURRENT_STATE_SCHEMA_VERSION:
                compact = MeetingState.model_validate_json(row[2])
                event_ids = {event.event_id for event in events}
                if compact.meeting_id != session_id:
                    raise ValueError("snapshot identity mismatch")
                if any(
                    not set(item.evidence_event_ids).issubset(event_ids)
                    for item in [*compact.pain_points, *compact.facts]
                ):
                    raise ValueError("snapshot evidence is absent from transcript log")
                if compact.identity_decisions != identity_decisions:
                    raise ValueError("snapshot identity decisions do not match event log")
                state = compact.model_copy(update={"transcript": events})
            else:
                # v4/v5 snapshots are deliberately ignored and replayed through
                # the current deterministic engine.
                engine = MeetingEngine(
                    session_id,
                    allow_identity_promotion=stored_schema_version != 5,
                )
                for event in events:
                    if not engine.apply(event):
                        raise ValueError("event log could not be replayed")
                state = engine.state
            adapter = row[0]
            if adapter not in SUPPORTED_LIVE_ADAPTERS:
                raise ValueError("snapshot adapter is not registered")
            return RecoveredLiveSession(
                adapter=adapter,
                lang=Lang(row[1]),
                state=state,
            )
        except (ValidationError, ValueError, KeyError, TypeError):
            try:
                self._quarantine_session(session_id, "invalid_session_cache")
            except (OSError, sqlite3.DatabaseError):
                pass
            return None
        except (OSError, sqlite3.DatabaseError):
            return None

    def delete(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM semantic_hint_decisions WHERE session_id = ?", (session_id,)
            )
            connection.execute(
                "DELETE FROM problem_identity_events WHERE session_id = ?", (session_id,)
            )
            connection.execute("DELETE FROM transcript_events WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM live_sessions WHERE session_id = ?", (session_id,))
            connection.execute(
                "DELETE FROM quarantined_sessions WHERE session_id = ?", (session_id,)
            )

    def persist_snapshot(
        self,
        session_id: str,
        adapter: LiveAdapterName,
        lang: Lang,
        state: MeetingState,
    ) -> None:
        """Persist a decision-only state revision without rewriting transcript rows."""

        compact_state = state.model_copy(update={"transcript": []}).model_dump_json()
        with self._connect() as connection:
            event_bytes = int(
                connection.execute(
                    "SELECT COALESCE(SUM(LENGTH(CAST(event_json AS BLOB))), 0) "
                    "FROM transcript_events WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
            )
            if event_bytes + len(compact_state.encode("utf-8")) >= self._max_session_event_bytes:
                raise LiveSessionStoreCapacityError("live session recovery cache limit reached")
            cursor = connection.execute(
                "UPDATE live_sessions SET adapter = ?, lang = ?, state_json = ?, "
                "updated_at = ?, state_schema_version = ? WHERE session_id = ?",
                (
                    adapter,
                    lang.value,
                    compact_state,
                    time.time(),
                    CURRENT_STATE_SCHEMA_VERSION,
                    session_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("live session snapshot has no transcript prefix")
            self._append_identity_decisions(connection, session_id, state, time.time())

    @staticmethod
    def _append_identity_decisions(
        connection: sqlite3.Connection,
        session_id: str,
        state: MeetingState,
        created_at: float,
    ) -> None:
        connection.executemany(
            "INSERT OR IGNORE INTO problem_identity_events "
            "(session_id, decision_id, ordinal, decision_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                (
                    session_id,
                    decision.decision_id,
                    ordinal,
                    decision.model_dump_json(),
                    created_at,
                )
                for ordinal, decision in enumerate(state.identity_decisions)
            ),
        )

    def delete_all(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM semantic_hint_decisions")
            connection.execute("DELETE FROM problem_identity_events")
            connection.execute("DELETE FROM transcript_events")
            connection.execute("DELETE FROM live_sessions")
            connection.execute("DELETE FROM quarantined_sessions")

    def load_logged_events(self, session_id: str) -> list[TranscriptEvent]:
        with self._connect() as connection:
            return [
                TranscriptEvent.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT event_json FROM transcript_events WHERE session_id = ? ORDER BY seq",
                    (session_id,),
                )
            ]

    def record_semantic_hint_decision(
        self, session_id: str, hint_id: str, action: str
    ) -> None:
        if action not in {"confirm", "dismiss"}:
            raise ValueError("semantic hint decision is invalid")
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO semantic_hint_decisions "
                "(session_id, hint_id, action, created_at) "
                "SELECT ?, ?, ?, ? FROM live_sessions WHERE session_id = ?",
                (session_id, hint_id, action, time.time(), session_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("semantic hint decision has no live session")

    def semantic_hint_decisions(self, session_id: str) -> dict[str, str]:
        with self._connect() as connection:
            return dict(
                connection.execute(
                    "SELECT hint_id, action FROM semantic_hint_decisions "
                    "WHERE session_id = ? ORDER BY created_at, hint_id",
                    (session_id,),
                )
            )

    def purge_stale(
        self,
        *,
        now: float | None = None,
        exclude_session_ids: set[str] | None = None,
    ) -> int:
        cutoff = (time.time() if now is None else now) - self._stale_ttl_seconds
        excluded = exclude_session_ids or set()
        with self._connect() as connection:
            stale_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT session_id FROM live_sessions WHERE updated_at < ?", (cutoff,)
                )
                if row[0] not in excluded
            ]
            for session_id in stale_ids:
                connection.execute(
                    "DELETE FROM semantic_hint_decisions WHERE session_id = ?", (session_id,)
                )
                connection.execute(
                    "DELETE FROM problem_identity_events WHERE session_id = ?", (session_id,)
                )
                connection.execute(
                    "DELETE FROM transcript_events WHERE session_id = ?", (session_id,)
                )
                connection.execute("DELETE FROM live_sessions WHERE session_id = ?", (session_id,))
            connection.execute(
                "DELETE FROM quarantined_sessions WHERE quarantined_at < ?",
                (cutoff,),
            )
        return len(stale_ids)

    def session_event_bytes(self, session_id: str) -> int:
        with self._connect() as connection:
            return int(
                connection.execute(
                    "SELECT COALESCE(SUM(LENGTH(CAST(event_json AS BLOB))), 0) "
                    "FROM transcript_events WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
            )

    def session_cache_bytes(self, session_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(LENGTH(CAST(state_json AS BLOB)), 0), "
                "(SELECT COALESCE(SUM(LENGTH(CAST(event_json AS BLOB))), 0) "
                "FROM transcript_events WHERE session_id = ?) "
                "FROM live_sessions WHERE session_id = ?",
                (session_id, session_id),
            ).fetchone()
        return int(row[0] + row[1]) if row is not None else 0

    def _initialize(self) -> None:
        try:
            self._cache_dir.chmod(0o700)
        except OSError:
            pass
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS live_sessions(
                    session_id TEXT PRIMARY KEY,
                    adapter TEXT NOT NULL,
                    lang TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    state_schema_version INTEGER NOT NULL DEFAULT 5
                );
                CREATE TABLE IF NOT EXISTS transcript_events(
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(session_id, seq),
                    FOREIGN KEY(session_id) REFERENCES live_sessions(session_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS problem_identity_events(
                    session_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    decision_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(session_id, decision_id),
                    UNIQUE(session_id, ordinal),
                    FOREIGN KEY(session_id) REFERENCES live_sessions(session_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS semantic_hint_decisions(
                    session_id TEXT NOT NULL,
                    hint_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('confirm', 'dismiss')),
                    created_at REAL NOT NULL,
                    PRIMARY KEY(session_id, hint_id),
                    FOREIGN KEY(session_id) REFERENCES live_sessions(session_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS quarantined_sessions(
                    session_id TEXT PRIMARY KEY,
                    reason_code TEXT NOT NULL,
                    quarantined_at REAL NOT NULL
                );
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(live_sessions)")
            }
            if "state_schema_version" not in columns:
                connection.execute(
                    "ALTER TABLE live_sessions ADD COLUMN state_schema_version "
                    "INTEGER NOT NULL DEFAULT 5"
                )
        try:
            self._database_path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Keep one connection open for the store lifetime. Closing the final
        # SQLite connection after every event can force WAL checkpoint work,
        # making live acknowledgements depend on slow-storage latency.
        with self._connection_lock:
            connection = self._connection
            if connection is None:
                raise sqlite3.ProgrammingError("live session store is closed")
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection

    def close(self) -> None:
        """Close the owned database connection; safe to call repeatedly."""

        with self._connection_lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                connection.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _quarantine_session(self, session_id: str, reason_code: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO quarantined_sessions(session_id, reason_code, quarantined_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    reason_code = excluded.reason_code,
                    quarantined_at = excluded.quarantined_at
                """,
                (session_id, reason_code, time.time()),
            )
            connection.execute("DELETE FROM transcript_events WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM live_sessions WHERE session_id = ?", (session_id,))
