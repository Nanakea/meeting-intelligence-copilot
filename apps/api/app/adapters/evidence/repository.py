"""DPAPI-protected artifact storage with append-only document revisions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.adapters.context.windows_security import DataProtector
from app.domain.evidence import ArtifactSection, DocumentArtifact, DocumentRevision


class EncryptedEvidenceRepository:
    def __init__(
        self,
        path: Path,
        protector: DataProtector,
        *,
        maximum_revisions_per_artifact: int = 20,
    ) -> None:
        self._path = path
        self._protector = protector
        self._maximum_revisions = maximum_revisions_per_artifact
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = NORMAL;
                CREATE TABLE IF NOT EXISTS evidence_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    connector_id TEXT NOT NULL,
                    current_revision_id TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS evidence_artifact_connector
                    ON evidence_artifacts(connector_id, artifact_id);
                CREATE TABLE IF NOT EXISTS evidence_revisions (
                    artifact_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    created_order INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload BLOB NOT NULL,
                    UNIQUE (artifact_id, revision_id)
                );
                CREATE TABLE IF NOT EXISTS evidence_sections (
                    artifact_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    section_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (artifact_id, revision_id, section_id)
                );
                CREATE INDEX IF NOT EXISTS evidence_section_revision
                    ON evidence_sections(artifact_id, revision_id, ordinal);
                CREATE TABLE IF NOT EXISTS evidence_embeddings (
                    section_id TEXT NOT NULL,
                    model_sha256 TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (section_id, model_sha256)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)

    def replace_artifact(
        self,
        artifact: DocumentArtifact,
        revision: DocumentRevision,
        sections: list[ArtifactSection],
    ) -> bool:
        if artifact.artifact_id != revision.artifact_id:
            raise ValueError("artifact and revision identity mismatch")
        if artifact.current_revision_id != revision.revision_id:
            raise ValueError("artifact current revision mismatch")
        if any(
            section.artifact_id != artifact.artifact_id
            or section.revision_id != revision.revision_id
            for section in sections
        ):
            raise ValueError("artifact section identity mismatch")
        if revision.section_count != len(sections):
            raise ValueError("revision section count mismatch")

        artifact_payload = self._protector.protect(artifact.model_dump_json().encode())
        revision_payload = self._protector.protect(revision.model_dump_json().encode())
        section_rows = [
            (
                section.artifact_id,
                section.revision_id,
                section.section_id,
                section.ordinal,
                self._protector.protect(section.model_dump_json().encode()),
            )
            for section in sections
        ]
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT current_revision_id FROM evidence_artifacts WHERE artifact_id = ?",
                (artifact.artifact_id,),
            ).fetchone()
            changed = existing is None or existing[0] != revision.revision_id
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO evidence_artifacts(artifact_id, connector_id, "
                "current_revision_id, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(artifact_id) DO UPDATE SET connector_id = excluded.connector_id, "
                "current_revision_id = excluded.current_revision_id, payload = excluded.payload",
                (
                    artifact.artifact_id,
                    artifact.connector_id,
                    revision.revision_id,
                    artifact_payload,
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO evidence_revisions(artifact_id, revision_id, payload) "
                "VALUES (?, ?, ?)",
                (artifact.artifact_id, revision.revision_id, revision_payload),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO evidence_sections(artifact_id, revision_id, section_id, "
                "ordinal, payload) VALUES (?, ?, ?, ?, ?)",
                section_rows,
            )
            obsolete = connection.execute(
                "SELECT revision_id FROM evidence_revisions WHERE artifact_id = ? "
                "ORDER BY created_order DESC LIMIT -1 OFFSET ?",
                (artifact.artifact_id, self._maximum_revisions),
            ).fetchall()
            for (revision_id,) in obsolete:
                section_ids = connection.execute(
                    "SELECT section_id FROM evidence_sections WHERE artifact_id = ? "
                    "AND revision_id = ?",
                    (artifact.artifact_id, revision_id),
                ).fetchall()
                connection.executemany(
                    "DELETE FROM evidence_embeddings WHERE section_id = ?",
                    section_ids,
                )
                connection.execute(
                    "DELETE FROM evidence_sections WHERE artifact_id = ? AND revision_id = ?",
                    (artifact.artifact_id, revision_id),
                )
                connection.execute(
                    "DELETE FROM evidence_revisions WHERE artifact_id = ? AND revision_id = ?",
                    (artifact.artifact_id, revision_id),
                )
        return changed

    def artifacts(self, connector_ids: list[str] | None = None) -> list[DocumentArtifact]:
        with self._connect() as connection:
            if connector_ids:
                placeholders = ",".join("?" for _ in connector_ids)
                rows = connection.execute(
                    "SELECT payload FROM evidence_artifacts WHERE connector_id IN "
                    f"({placeholders}) "
                    "ORDER BY artifact_id",
                    connector_ids,
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM evidence_artifacts ORDER BY artifact_id"
                ).fetchall()
        return [
            DocumentArtifact.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def sections(self, connector_ids: list[str] | None = None) -> list[ArtifactSection]:
        query = (
            "SELECT s.payload FROM evidence_sections s "
            "JOIN evidence_artifacts a ON a.artifact_id = s.artifact_id "
            "AND a.current_revision_id = s.revision_id"
        )
        parameters: list[str] = []
        if connector_ids:
            placeholders = ",".join("?" for _ in connector_ids)
            query += f" WHERE a.connector_id IN ({placeholders})"
            parameters.extend(connector_ids)
        query += " ORDER BY s.artifact_id, s.ordinal"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            ArtifactSection.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def revision(self, revision_id: str) -> DocumentRevision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM evidence_revisions WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()
        if row is None:
            return None
        return DocumentRevision.model_validate_json(self._protector.unprotect(row[0]))

    def save_embeddings(
        self, model_sha256: str, values: dict[str, list[float]]
    ) -> None:
        import json

        rows = [
            (
                section_id,
                model_sha256,
                self._protector.protect(
                    json.dumps(vector, separators=(",", ":")).encode()
                ),
            )
            for section_id, vector in values.items()
        ]
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO evidence_embeddings(section_id, model_sha256, payload) "
                "VALUES (?, ?, ?) ON CONFLICT(section_id, model_sha256) "
                "DO UPDATE SET payload = excluded.payload",
                rows,
            )

    def embeddings(
        self, model_sha256: str, section_ids: list[str]
    ) -> dict[str, list[float]]:
        import json

        if not section_ids:
            return {}
        placeholders = ",".join("?" for _ in section_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT section_id, payload FROM evidence_embeddings "
                f"WHERE model_sha256 = ? AND section_id IN ({placeholders})",
                [model_sha256, *section_ids],
            ).fetchall()
        return {
            section_id: json.loads(self._protector.unprotect(payload))
            for section_id, payload in rows
        }

    def purge_connector_artifacts(self, connector_id: str) -> int:
        return self.reconcile_connector_artifacts(connector_id, set())

    def reconcile_connector_artifacts(
        self, connector_id: str, retained_artifact_ids: set[str]
    ) -> int:
        with self._connect() as connection:
            artifact_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT artifact_id FROM evidence_artifacts WHERE connector_id = ?",
                    (connector_id,),
                ).fetchall()
            ]
            connection.execute("BEGIN IMMEDIATE")
            count = 0
            for artifact_id in artifact_ids:
                if artifact_id in retained_artifact_ids:
                    continue
                section_ids = connection.execute(
                    "SELECT section_id FROM evidence_sections WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchall()
                connection.executemany(
                    "DELETE FROM evidence_embeddings WHERE section_id = ?", section_ids
                )
                connection.execute(
                    "DELETE FROM evidence_sections WHERE artifact_id = ?", (artifact_id,)
                )
                connection.execute(
                    "DELETE FROM evidence_revisions WHERE artifact_id = ?", (artifact_id,)
                )
                count += connection.execute(
                    "DELETE FROM evidence_artifacts WHERE artifact_id = ?", (artifact_id,)
                ).rowcount
        return count

    def assert_no_plaintext(self, marker: str) -> None:
        if marker.encode() in self._path.read_bytes():
            raise ValueError("evidence repository contains plaintext content")
