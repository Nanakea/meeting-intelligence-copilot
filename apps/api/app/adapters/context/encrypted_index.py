"""Transactional DPAPI-encrypted local cache for connector documents."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.adapters.context.windows_security import DataProtector
from app.domain.context import (
    ConnectorDefinition,
    ContextDocument,
    EntityGlossary,
    ExternalSpaceSelection,
)
from app.domain.enterprise import AccessLease, SourceSelection, SyncCursor


class EncryptedContextIndex:
    def __init__(
        self,
        path: Path,
        protector: DataProtector,
        *,
        maximum_documents_per_connector: int = 5_000,
    ) -> None:
        self._path = path
        self._protector = protector
        self._maximum_documents = maximum_documents_per_connector
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = NORMAL;
                CREATE TABLE IF NOT EXISTS context_documents (
                    connector_id TEXT NOT NULL,
                    document_key TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (connector_id, document_key)
                );
                CREATE TABLE IF NOT EXISTS context_connectors (
                    connector_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_credential_cleanup (
                    connector_id TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (connector_id, target_key)
                );
                CREATE TABLE IF NOT EXISTS context_citation_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_settings (
                    setting_key TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_external_spaces (
                    connector_id TEXT NOT NULL,
                    selection_id TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (connector_id, selection_id)
                );
                CREATE TABLE IF NOT EXISTS context_source_selections (
                    connector_id TEXT NOT NULL,
                    selection_id TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (connector_id, selection_id)
                );
                CREATE TABLE IF NOT EXISTS context_access_leases (
                    connector_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_sync_cursors (
                    connector_id TEXT NOT NULL,
                    selection_id TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (connector_id, selection_id)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)

    @property
    def storage_path(self) -> Path:
        return self._path

    @property
    def protector(self) -> DataProtector:
        return self._protector

    def replace(self, connector_id: str, documents: list[ContextDocument]) -> int:
        bounded = documents[: self._maximum_documents]
        encrypted = [
            (
                connector_id,
                document.document_id,
                self._protector.protect(document.model_dump_json().encode()),
            )
            for document in bounded
        ]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM context_documents WHERE connector_id = ?", (connector_id,)
            )
            connection.executemany(
                "INSERT INTO context_documents(connector_id, document_key, payload) "
                "VALUES (?, ?, ?)",
                encrypted,
            )
        return len(encrypted)

    def upsert_documents(self, connector_id: str, documents: list[ContextDocument]) -> int:
        bounded = documents[: self._maximum_documents]
        rows = [
            (
                connector_id,
                document.document_id,
                self._protector.protect(document.model_dump_json().encode()),
            )
            for document in bounded
            if document.connector_id == connector_id
        ]
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO context_documents(connector_id, document_key, payload) "
                "VALUES (?, ?, ?) ON CONFLICT(connector_id, document_key) "
                "DO UPDATE SET payload = excluded.payload",
                rows,
            )
            connection.execute(
                "DELETE FROM context_documents WHERE connector_id = ? AND document_key NOT IN "
                "(SELECT document_key FROM context_documents WHERE connector_id = ? "
                "ORDER BY document_key LIMIT ?)",
                (connector_id, connector_id, self._maximum_documents),
            )
        return len(rows)

    def documents(self, connector_id: str) -> list[ContextDocument]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM context_documents WHERE connector_id = ? "
                "ORDER BY document_key",
                (connector_id,),
            ).fetchall()
        return [
            ContextDocument.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def document_count(self, connector_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM context_documents WHERE connector_id = ?",
                (connector_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def purge(self, connector_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM context_documents WHERE connector_id = ?", (connector_id,)
            )
        return cursor.rowcount > 0

    def save_connector(self, definition: ConnectorDefinition) -> None:
        payload = self._protector.protect(definition.model_dump_json().encode())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO context_connectors(connector_id, payload) VALUES (?, ?) "
                "ON CONFLICT(connector_id) DO UPDATE SET payload = excluded.payload",
                (definition.connector_id, payload),
            )

    def connector_definitions(self) -> list[ConnectorDefinition]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM context_connectors ORDER BY connector_id"
            ).fetchall()
        return [
            ConnectorDefinition.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def connector_definition(self, connector_id: str) -> ConnectorDefinition | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM context_connectors WHERE connector_id = ?",
                (connector_id,),
            ).fetchone()
        if row is None:
            return None
        return ConnectorDefinition.model_validate_json(self._protector.unprotect(row[0]))

    def delete_connector(self, connector_id: str) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            documents = connection.execute(
                "DELETE FROM context_documents WHERE connector_id = ?", (connector_id,)
            ).rowcount
            connector = connection.execute(
                "DELETE FROM context_connectors WHERE connector_id = ?", (connector_id,)
            ).rowcount
            spaces = connection.execute(
                "DELETE FROM context_external_spaces WHERE connector_id = ?",
                (connector_id,),
            ).rowcount
            selections = connection.execute(
                "DELETE FROM context_source_selections WHERE connector_id = ?",
                (connector_id,),
            ).rowcount
            connection.execute(
                "DELETE FROM context_access_leases WHERE connector_id = ?", (connector_id,)
            )
            connection.execute(
                "DELETE FROM context_sync_cursors WHERE connector_id = ?", (connector_id,)
            )
        return documents > 0 or connector > 0 or spaces > 0 or selections > 0

    def delete_all(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM context_documents")
            connection.execute("DELETE FROM context_connectors")
            connection.execute("DELETE FROM context_citation_feedback")
            connection.execute("DELETE FROM context_settings")
            connection.execute("DELETE FROM context_external_spaces")
            connection.execute("DELETE FROM context_source_selections")
            connection.execute("DELETE FROM context_access_leases")
            connection.execute("DELETE FROM context_sync_cursors")

    def save_source_selection(
        self, selection: SourceSelection, external_id: str
    ) -> SourceSelection:
        if not external_id or len(external_id) > 512 or any(
            character in external_id for character in "\r\n\0"
        ):
            raise ValueError("external source identifier is invalid")
        payload = self._protector.protect(
            json.dumps(
                {
                    "selection": selection.model_dump(mode="json"),
                    "external_id": external_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO context_source_selections(connector_id, selection_id, payload) "
                "VALUES (?, ?, ?) ON CONFLICT(connector_id, selection_id) "
                "DO UPDATE SET payload = excluded.payload",
                (selection.connector_id, selection.selection_id, payload),
            )
        return selection

    def source_selections(self, connector_id: str) -> list[SourceSelection]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM context_source_selections WHERE connector_id = ? "
                "ORDER BY selection_id",
                (connector_id,),
            ).fetchall()
        return [
            SourceSelection.model_validate(
                json.loads(self._protector.unprotect(row[0]))["selection"]
            )
            for row in rows
        ]

    def resolve_source_selections(self, connector_id: str) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT selection_id, payload FROM context_source_selections "
                "WHERE connector_id = ? ORDER BY selection_id",
                (connector_id,),
            ).fetchall()
        return {
            str(selection_id): str(
                json.loads(self._protector.unprotect(payload))["external_id"]
            )
            for selection_id, payload in rows
        }

    def delete_source_selection(self, connector_id: str, selection_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM context_source_selections WHERE connector_id = ? "
                "AND selection_id = ?",
                (connector_id, selection_id),
            )
        return cursor.rowcount > 0

    def save_access_lease(self, lease: AccessLease) -> None:
        payload = self._protector.protect(lease.model_dump_json().encode())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO context_access_leases(connector_id, payload) VALUES (?, ?) "
                "ON CONFLICT(connector_id) DO UPDATE SET payload = excluded.payload",
                (lease.connector_id, payload),
            )

    def access_lease(self, connector_id: str) -> AccessLease | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM context_access_leases WHERE connector_id = ?",
                (connector_id,),
            ).fetchone()
        if row is None:
            return None
        return AccessLease.model_validate_json(self._protector.unprotect(row[0]))

    def revoke_access_lease(self, connector_id: str) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                "DELETE FROM context_access_leases WHERE connector_id = ?", (connector_id,)
            ).rowcount
            connection.execute(
                "DELETE FROM context_documents WHERE connector_id = ?", (connector_id,)
            )
        return lease > 0

    def save_sync_cursor(self, cursor: SyncCursor) -> None:
        payload = self._protector.protect(cursor.model_dump_json().encode())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO context_sync_cursors(connector_id, selection_id, payload) "
                "VALUES (?, ?, ?) ON CONFLICT(connector_id, selection_id) "
                "DO UPDATE SET payload = excluded.payload",
                (cursor.connector_id, cursor.source_selection_id, payload),
            )

    def sync_cursors(self, connector_id: str) -> list[SyncCursor]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM context_sync_cursors WHERE connector_id = ? "
                "ORDER BY selection_id",
                (connector_id,),
            ).fetchall()
        return [
            SyncCursor.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def save_external_space(
        self,
        connector_id: str,
        selection: ExternalSpaceSelection,
        external_id: str,
    ) -> ExternalSpaceSelection:
        if not external_id or len(external_id) > 512 or any(
            character in external_id for character in "\r\n\0"
        ):
            raise ValueError("external space identifier is invalid")
        payload = self._protector.protect(
            json.dumps(
                {
                    "selection": selection.model_dump(mode="json"),
                    "external_id": external_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO context_external_spaces(connector_id, selection_id, payload) "
                "VALUES (?, ?, ?) ON CONFLICT(connector_id, selection_id) "
                "DO UPDATE SET payload = excluded.payload",
                (connector_id, selection.selection_id, payload),
            )
        return selection

    def external_spaces(self, connector_id: str) -> list[ExternalSpaceSelection]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM context_external_spaces WHERE connector_id = ? "
                "ORDER BY selection_id",
                (connector_id,),
            ).fetchall()
        values = [
            json.loads(self._protector.unprotect(row[0]))["selection"] for row in rows
        ]
        return [ExternalSpaceSelection.model_validate(value) for value in values]

    def resolve_external_spaces(self, connector_id: str) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT selection_id, payload FROM context_external_spaces "
                "WHERE connector_id = ? ORDER BY selection_id",
                (connector_id,),
            ).fetchall()
        return {
            str(selection_id): str(
                json.loads(self._protector.unprotect(payload))["external_id"]
            )
            for selection_id, payload in rows
        }

    def delete_external_space(self, connector_id: str, selection_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM context_external_spaces "
                "WHERE connector_id = ? AND selection_id = ?",
                (connector_id, selection_id),
            )
        return cursor.rowcount > 0

    def entity_glossary(self) -> EntityGlossary:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM context_settings WHERE setting_key = 'entity_glossary'"
            ).fetchone()
        if row is None:
            return EntityGlossary()
        return EntityGlossary.model_validate_json(self._protector.unprotect(row[0]))

    def save_entity_glossary(self, glossary: EntityGlossary) -> None:
        payload = self._protector.protect(glossary.model_dump_json().encode())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO context_settings(setting_key, payload) VALUES ('entity_glossary', ?) "
                "ON CONFLICT(setting_key) DO UPDATE SET payload = excluded.payload",
                (payload,),
            )

    def record_citation_feedback(self, values: dict[str, object]) -> None:
        payload = {
            **values,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        encrypted = self._protector.protect(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO context_citation_feedback(payload) VALUES (?)", (encrypted,)
            )
            connection.execute(
                "DELETE FROM context_citation_feedback WHERE id NOT IN "
                "(SELECT id FROM context_citation_feedback ORDER BY id DESC LIMIT 10000)"
            )

    def remember_credential_cleanup(
        self, connector_id: str, targets: list[str]
    ) -> None:
        """Retain only encrypted cleanup handles until CredMan confirms deletion."""

        rows = [
            (
                connector_id,
                hashlib.sha256(target.encode()).hexdigest(),
                self._protector.protect(target.encode()),
            )
            for target in dict.fromkeys(targets)
        ]
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO context_credential_cleanup(connector_id, target_key, payload) "
                "VALUES (?, ?, ?) ON CONFLICT(connector_id, target_key) "
                "DO UPDATE SET payload = excluded.payload",
                rows,
            )

    def pending_credential_cleanup(
        self, connector_id: str | None = None
    ) -> list[tuple[str, str]]:
        with self._connect() as connection:
            if connector_id is None:
                rows = connection.execute(
                    "SELECT connector_id, payload FROM context_credential_cleanup "
                    "ORDER BY connector_id, target_key"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT connector_id, payload FROM context_credential_cleanup "
                    "WHERE connector_id = ? ORDER BY target_key",
                    (connector_id,),
                ).fetchall()
        return [
            (row[0], self._protector.unprotect(row[1]).decode()) for row in rows
        ]

    def clear_credential_cleanup(self, connector_id: str, target: str) -> bool:
        target_key = hashlib.sha256(target.encode()).hexdigest()
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM context_credential_cleanup "
                "WHERE connector_id = ? AND target_key = ?",
                (connector_id, target_key),
            )
        return cursor.rowcount > 0

    def export_aggregate(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT connector_id, COUNT(*) FROM context_documents GROUP BY connector_id"
            ).fetchall()
        return {connector_id: count for connector_id, count in rows}

    def diagnostics(self) -> dict[str, int]:
        with self._connect() as connection:
            documents = connection.execute(
                "SELECT COUNT(*) FROM context_documents"
            ).fetchone()[0]
            feedback = connection.execute(
                "SELECT COUNT(*) FROM context_citation_feedback"
            ).fetchone()[0]
        return {
            "local_index_documents": int(documents),
            "citation_feedback_records": int(feedback),
        }

    def assert_no_plaintext(self, marker: str) -> None:
        """Acceptance helper: sensitive markers must not occur in SQLite bytes."""

        if marker.encode() in self._path.read_bytes():
            raise ValueError("context cache contains plaintext content")
