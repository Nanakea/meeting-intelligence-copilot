"""Transactional encrypted SQLite governance repository."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from app.adapters.context.windows_security import DataProtector
from app.domain.governance import (
    FieldProvenance,
    GovernanceCandidate,
    GovernanceEvent,
    GovernanceRecord,
)


class EncryptedGovernanceRepository:
    def __init__(self, path: Path, protector: DataProtector) -> None:
        self._path = path
        self._protector = protector
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = NORMAL;
                CREATE TABLE IF NOT EXISTS governance_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_governance_candidates_session
                    ON governance_candidates(session_id, status);
                CREATE TABLE IF NOT EXISTS governance_records (
                    record_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS governance_events (
                    event_id TEXT PRIMARY KEY,
                    record_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_governance_events_record
                    ON governance_events(record_id, revision);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)

    def _encode(self, value: GovernanceCandidate | GovernanceRecord | GovernanceEvent) -> bytes:
        return self._protector.protect(value.model_dump_json().encode())

    def save_candidate(self, candidate: GovernanceCandidate) -> GovernanceCandidate:
        existing = self.candidate(candidate.candidate_id)
        if existing is not None:
            if existing.status.value != "pending":
                return existing
            evidence = list(
                dict.fromkeys([*existing.evidence_event_ids, *candidate.evidence_event_ids])
            )
            candidate = candidate.model_copy(
                update={
                    "evidence_event_ids": evidence[:64],
                    "state_version": max(existing.state_version, candidate.state_version),
                }
            )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO governance_candidates(candidate_id, session_id, status, payload) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(candidate_id) DO UPDATE SET "
                "status = excluded.status, payload = excluded.payload",
                (
                    candidate.candidate_id,
                    candidate.session_id,
                    candidate.status.value,
                    self._encode(candidate),
                ),
            )
        return candidate

    def candidate(self, candidate_id: str) -> GovernanceCandidate | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM governance_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            return None
        return GovernanceCandidate.model_validate_json(self._protector.unprotect(row[0]))

    def candidates(self, session_id: str) -> list[GovernanceCandidate]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM governance_candidates WHERE session_id = ? "
                "ORDER BY candidate_id",
                (session_id,),
            ).fetchall()
        return [
            GovernanceCandidate.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def save_review(
        self,
        candidate: GovernanceCandidate,
        record: GovernanceRecord | None,
        event: GovernanceEvent | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE governance_candidates SET status = ?, payload = ? WHERE candidate_id = ?",
                (candidate.status.value, self._encode(candidate), candidate.candidate_id),
            )
            if record is not None and event is not None:
                current_row = connection.execute(
                    "SELECT payload FROM governance_records WHERE record_id = ?",
                    (record.record_id,),
                ).fetchone()
                if current_row is None:
                    connection.execute(
                        "INSERT INTO governance_records"
                        "(record_id, status, payload) VALUES (?, ?, ?)",
                        (record.record_id, record.status.value, self._encode(record)),
                    )
                else:
                    current = GovernanceRecord.model_validate_json(
                        self._protector.unprotect(current_row[0])
                    )
                    if current.revision + 1 != record.revision:
                        raise RuntimeError("stale governance record")
                    connection.execute(
                        "UPDATE governance_records SET status = ?, payload = ? WHERE record_id = ?",
                        (record.status.value, self._encode(record), record.record_id),
                    )
                connection.execute(
                    "INSERT INTO governance_events"
                    "(event_id, record_id, revision, payload) VALUES (?, ?, ?, ?)",
                    (event.event_id, event.record_id, event.revision, self._encode(event)),
                )

    def record(self, record_id: str) -> GovernanceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM governance_records WHERE record_id = ?", (record_id,)
            ).fetchone()
        if row is None:
            return None
        return GovernanceRecord.model_validate_json(self._protector.unprotect(row[0]))

    def records(self) -> list[GovernanceRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM governance_records ORDER BY record_id"
            ).fetchall()
        return [
            GovernanceRecord.model_validate_json(self._protector.unprotect(row[0])) for row in rows
        ]

    def save_record_event(self, record: GovernanceRecord, event: GovernanceEvent) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM governance_records WHERE record_id = ?", (record.record_id,)
            ).fetchone()
            if row is None:
                raise KeyError(record.record_id)
            current = GovernanceRecord.model_validate_json(self._protector.unprotect(row[0]))
            if current.revision + 1 != record.revision:
                raise RuntimeError("stale governance record")
            connection.execute(
                "UPDATE governance_records SET status = ?, payload = ? WHERE record_id = ?",
                (record.status.value, self._encode(record), record.record_id),
            )
            connection.execute(
                "INSERT INTO governance_events"
                "(event_id, record_id, revision, payload) VALUES (?, ?, ?, ?)",
                (event.event_id, event.record_id, event.revision, self._encode(event)),
            )

    def delete_meeting(self, session_id: str) -> None:
        records = self.records()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM governance_candidates WHERE session_id = ?", (session_id,)
            )
            for record in records:
                if session_id not in record.source_session_ids:
                    continue
                remaining_sessions = [
                    value for value in record.source_session_ids if value != session_id
                ]
                remaining_evidence = [
                    value for value in record.evidence if value.session_id != session_id
                ]
                if not remaining_sessions:
                    connection.execute(
                        "DELETE FROM governance_events WHERE record_id = ?", (record.record_id,)
                    )
                    connection.execute(
                        "DELETE FROM governance_records WHERE record_id = ?", (record.record_id,)
                    )
                    continue
                updated = record.model_copy(
                    update={
                        "source_session_ids": remaining_sessions,
                        "evidence": remaining_evidence,
                        "needs_provenance_review": True,
                        "revision": record.revision + 1,
                        "updated_at": datetime.now(UTC),
                    }
                )
                connection.execute(
                    "UPDATE governance_records SET payload = ? WHERE record_id = ?",
                    (self._encode(updated), record.record_id),
                )
                event = _provenance_removed_event(updated, session_id)
                connection.execute(
                    "INSERT INTO governance_events"
                    "(event_id, record_id, revision, payload) VALUES (?, ?, ?, ?)",
                    (event.event_id, event.record_id, event.revision, self._encode(event)),
                )

    def delete_all(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM governance_events")
            connection.execute("DELETE FROM governance_records")
            connection.execute("DELETE FROM governance_candidates")

    def close(self) -> None:
        return None


class MemoryGovernanceRepository:
    """Deterministic test/degraded-mode repository."""

    def __init__(self) -> None:
        self._candidates: dict[str, GovernanceCandidate] = {}
        self._records: dict[str, GovernanceRecord] = {}
        self._events: dict[str, GovernanceEvent] = {}

    def save_candidate(self, candidate: GovernanceCandidate) -> GovernanceCandidate:
        existing = self._candidates.get(candidate.candidate_id)
        if existing is not None:
            if existing.status.value != "pending":
                return existing
            candidate = candidate.model_copy(
                update={
                    "evidence_event_ids": list(
                        dict.fromkeys([*existing.evidence_event_ids, *candidate.evidence_event_ids])
                    )[:64],
                    "state_version": max(existing.state_version, candidate.state_version),
                }
            )
        self._candidates[candidate.candidate_id] = candidate
        return candidate

    def candidate(self, candidate_id: str) -> GovernanceCandidate | None:
        return self._candidates.get(candidate_id)

    def candidates(self, session_id: str) -> list[GovernanceCandidate]:
        return sorted(
            (value for value in self._candidates.values() if value.session_id == session_id),
            key=lambda value: (value.created_seq, value.candidate_id),
        )

    def save_review(
        self,
        candidate: GovernanceCandidate,
        record: GovernanceRecord | None,
        event: GovernanceEvent | None,
    ) -> None:
        self._candidates[candidate.candidate_id] = candidate
        if record is not None and event is not None:
            current = self._records.get(record.record_id)
            if current is not None and current.revision + 1 != record.revision:
                raise RuntimeError("stale governance record")
            self._records[record.record_id] = record
            self._events[event.event_id] = event

    def record(self, record_id: str) -> GovernanceRecord | None:
        return self._records.get(record_id)

    def records(self) -> list[GovernanceRecord]:
        return sorted(self._records.values(), key=lambda value: value.record_id)

    def save_record_event(self, record: GovernanceRecord, event: GovernanceEvent) -> None:
        current = self._records.get(record.record_id)
        if current is None:
            raise KeyError(record.record_id)
        if current.revision + 1 != record.revision:
            raise RuntimeError("stale governance record")
        self._records[record.record_id] = record
        self._events[event.event_id] = event

    def delete_meeting(self, session_id: str) -> None:
        self._candidates = {
            key: value for key, value in self._candidates.items() if value.session_id != session_id
        }
        for record_id, record in list(self._records.items()):
            if session_id not in record.source_session_ids:
                continue
            remaining = [value for value in record.source_session_ids if value != session_id]
            if not remaining:
                self._records.pop(record_id)
                self._events = {
                    key: value
                    for key, value in self._events.items()
                    if value.record_id != record_id
                }
            else:
                self._records[record_id] = record.model_copy(
                    update={
                        "source_session_ids": remaining,
                        "evidence": [
                            value for value in record.evidence if value.session_id != session_id
                        ],
                        "needs_provenance_review": True,
                        "revision": record.revision + 1,
                        "updated_at": datetime.now(UTC),
                    }
                )
                updated = self._records[record_id]
                event = _provenance_removed_event(updated, session_id)
                self._events[event.event_id] = event

    def delete_all(self) -> None:
        self._candidates.clear()
        self._records.clear()
        self._events.clear()

    def close(self) -> None:
        return None


def _provenance_removed_event(record: GovernanceRecord, session_id: str) -> GovernanceEvent:
    fingerprint = sha256(
        f"{record.record_id}\0provenance_removed\0{record.revision}\0{session_id}".encode()
    ).hexdigest()[:24]
    return GovernanceEvent(
        event_id=f"ge-{fingerprint}",
        record_id=record.record_id,
        revision=record.revision,
        action="provenance_removed",
        changed_fields=["source_session_ids", "evidence", "needs_provenance_review"],
        provenance=FieldProvenance.user,
    )
