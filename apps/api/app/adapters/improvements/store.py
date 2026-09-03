"""DPAPI-protected SQLite/WAL persistence for v11 supervised improvement."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.adapters.context.windows_security import DataProtector
from app.domain.improvements import (
    DocumentClassification,
    DocumentDispositionProposal,
    DocumentOperationPlan,
    DocumentScanStatus,
    ERPGovernanceRun,
    ERPMetadataBaseline,
    ImprovementEvaluation,
    ImprovementProfile,
    ImprovementProposal,
    ImprovementSettings,
    ImprovementShadowRun,
    ImprovementSignal,
)


class EncryptedImprovementRepository:
    def __init__(self, path: Path, protector: DataProtector) -> None:
        self._path = path
        self._protector = protector
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = NORMAL;
                CREATE TABLE IF NOT EXISTS improvement_signals (
                    signal_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    payload BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS improvement_signal_fingerprint
                    ON improvement_signals(fingerprint);
                CREATE TABLE IF NOT EXISTS improvement_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS improvement_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS improvement_profiles (
                    profile_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    UNIQUE(kind, revision)
                );
                CREATE TABLE IF NOT EXISTS improvement_settings (
                    setting_key TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS improvement_personal_keys (
                    key_name TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_classifications (
                    classification_id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    UNIQUE(artifact_id, revision_id)
                );
                CREATE TABLE IF NOT EXISTS document_dispositions (
                    disposition_id TEXT PRIMARY KEY,
                    classification_id TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS erp_governance_runs (
                    run_id TEXT PRIMARY KEY,
                    connector_id TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS improvement_shadow_runs (
                    run_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    cycle INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    UNIQUE(proposal_id, cycle)
                );
                CREATE TABLE IF NOT EXISTS document_operation_plans (
                    operation_id TEXT PRIMARY KEY,
                    disposition_id TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_scan_status (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS erp_metadata_baselines (
                    connector_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)

    def _protect(self, value: object) -> bytes:
        return self._protector.protect(value.model_dump_json().encode())  # type: ignore[attr-defined]

    def record_signal(self, signal: ImprovementSignal) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO improvement_signals"
                "(signal_id, fingerprint, created_at, expires_at, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    signal.signal_id,
                    signal.fingerprint,
                    signal.created_at.isoformat(),
                    signal.expires_at.isoformat() if signal.expires_at else None,
                    self._protect(signal),
                ),
            )
        return cursor.rowcount == 1

    def purge_expired_signals(self, now: datetime | None = None) -> int:
        current = (now or datetime.now(UTC)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM improvement_signals WHERE expires_at IS NOT NULL AND expires_at < ?",
                (current,),
            )
        return cursor.rowcount

    def signals(self) -> list[ImprovementSignal]:
        self.purge_expired_signals()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM improvement_signals ORDER BY created_at, signal_id"
            ).fetchall()
        return [
            ImprovementSignal.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def delete_signals(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM improvement_signals")
        return cursor.rowcount

    def save_proposal(self, proposal: ImprovementProposal) -> ImprovementProposal:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO improvement_proposals(proposal_id, fingerprint, payload) "
                "VALUES (?, ?, ?) ON CONFLICT(proposal_id) "
                "DO UPDATE SET payload = excluded.payload",
                (proposal.proposal_id, proposal.fingerprint, self._protect(proposal)),
            )
        return proposal

    def proposals(self) -> list[ImprovementProposal]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM improvement_proposals ORDER BY proposal_id"
            ).fetchall()
        return [
            ImprovementProposal.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def proposal(self, proposal_id: str) -> ImprovementProposal | None:
        return next((value for value in self.proposals() if value.proposal_id == proposal_id), None)

    def update_proposal(self, proposal: ImprovementProposal, expected_revision: int) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM improvement_proposals WHERE proposal_id = ?",
                (proposal.proposal_id,),
            ).fetchone()
            if row is None:
                raise KeyError(proposal.proposal_id)
            current = ImprovementProposal.model_validate_json(
                self._protector.unprotect(row[0])
            )
            if current.revision != expected_revision or proposal.revision != expected_revision + 1:
                raise RuntimeError("stale_proposal")
            connection.execute(
                "UPDATE improvement_proposals SET payload = ? WHERE proposal_id = ?",
                (self._protect(proposal), proposal.proposal_id),
            )

    def save_evaluation(self, evaluation: ImprovementEvaluation) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO improvement_evaluations"
                "(evaluation_id, proposal_id, payload) VALUES (?, ?, ?)",
                (evaluation.evaluation_id, evaluation.proposal_id, self._protect(evaluation)),
            )

    def evaluation_for_proposal(self, proposal_id: str) -> ImprovementEvaluation | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM improvement_evaluations WHERE proposal_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (proposal_id,),
            ).fetchone()
        return (
            ImprovementEvaluation.model_validate_json(self._protector.unprotect(row[0]))
            if row
            else None
        )

    def evaluation(self, evaluation_id: str) -> ImprovementEvaluation | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM improvement_evaluations WHERE evaluation_id = ?",
                (evaluation_id,),
            ).fetchone()
        return (
            ImprovementEvaluation.model_validate_json(self._protector.unprotect(row[0]))
            if row
            else None
        )

    def save_profile(self, profile: ImprovementProfile) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO improvement_profiles(profile_id, kind, revision, payload) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(profile_id) "
                "DO UPDATE SET payload = excluded.payload",
                (profile.profile_id, profile.kind.value, profile.revision, self._protect(profile)),
            )

    def profiles(self) -> list[ImprovementProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM improvement_profiles ORDER BY kind, revision"
            ).fetchall()
        return [
            ImprovementProfile.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def save_shadow_run(self, value: ImprovementShadowRun) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO improvement_shadow_runs(run_id, proposal_id, cycle, payload) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(proposal_id, cycle) "
                "DO UPDATE SET run_id = excluded.run_id, payload = excluded.payload",
                (value.run_id, value.proposal_id, value.cycle, self._protect(value)),
            )

    def shadow_runs(self, proposal_id: str | None = None) -> list[ImprovementShadowRun]:
        with self._connect() as connection:
            if proposal_id is None:
                rows = connection.execute(
                    "SELECT payload FROM improvement_shadow_runs ORDER BY proposal_id, cycle"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM improvement_shadow_runs WHERE proposal_id = ? "
                    "ORDER BY cycle",
                    (proposal_id,),
                ).fetchall()
        return [
            ImprovementShadowRun.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def settings(self) -> ImprovementSettings:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM improvement_settings WHERE setting_key = 'settings'"
            ).fetchone()
        return (
            ImprovementSettings.model_validate_json(self._protector.unprotect(row[0]))
            if row
            else ImprovementSettings()
        )

    def save_settings(self, settings: ImprovementSettings, expected_revision: int) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM improvement_settings WHERE setting_key = 'settings'"
            ).fetchone()
            current = (
                ImprovementSettings.model_validate_json(
                    self._protector.unprotect(row[0])
                )
                if row
                else ImprovementSettings()
            )
            if (
                current.revision != expected_revision
                or settings.revision != expected_revision + 1
            ):
                raise RuntimeError("stale_settings")
            connection.execute(
                "INSERT INTO improvement_settings(setting_key, payload) VALUES ('settings', ?) "
                "ON CONFLICT(setting_key) DO UPDATE SET payload = excluded.payload",
                (self._protect(settings),),
            )

    def personal_key(self) -> bytes | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM improvement_personal_keys WHERE key_name = 'ed25519'"
            ).fetchone()
        return self._protector.unprotect(row[0]) if row else None

    def save_personal_key(self, private_key: bytes) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO improvement_personal_keys(key_name, payload) "
                "VALUES ('ed25519', ?)",
                (self._protector.protect(private_key),),
            )

    def save_classification(self, value: DocumentClassification) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO document_classifications"
                "(classification_id, artifact_id, revision_id, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(classification_id) DO UPDATE SET payload = excluded.payload",
                (
                    value.classification_id,
                    value.artifact_id,
                    value.revision_id,
                    self._protect(value),
                ),
            )

    def classifications(self) -> list[DocumentClassification]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM document_classifications ORDER BY classification_id"
            ).fetchall()
        return [
            DocumentClassification.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def update_classification(
        self, value: DocumentClassification, expected_revision: int
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM document_classifications WHERE classification_id = ?",
                (value.classification_id,),
            ).fetchone()
            if row is None:
                raise KeyError(value.classification_id)
            current = DocumentClassification.model_validate_json(
                self._protector.unprotect(row[0])
            )
            if (
                current.revision != expected_revision
                or value.revision != expected_revision + 1
            ):
                raise RuntimeError("stale_classification")
            connection.execute(
                "UPDATE document_classifications SET payload = ? "
                "WHERE classification_id = ?",
                (self._protect(value), value.classification_id),
            )

    def save_disposition(self, value: DocumentDispositionProposal) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO document_dispositions(disposition_id, classification_id, payload) "
                "VALUES (?, ?, ?) ON CONFLICT(disposition_id) "
                "DO UPDATE SET payload = excluded.payload",
                (value.disposition_id, value.classification_id, self._protect(value)),
            )

    def dispositions(self) -> list[DocumentDispositionProposal]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM document_dispositions ORDER BY disposition_id"
            ).fetchall()
        return [
            DocumentDispositionProposal.model_validate_json(
                self._protector.unprotect(row[0])
            )
            for row in rows
        ]

    def save_operation_plan(self, value: DocumentOperationPlan) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO document_operation_plans(operation_id, disposition_id, payload) "
                "VALUES (?, ?, ?) ON CONFLICT(operation_id) "
                "DO UPDATE SET payload = excluded.payload",
                (value.operation_id, value.disposition_id, self._protect(value)),
            )

    def operation_plans(self) -> list[DocumentOperationPlan]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM document_operation_plans ORDER BY operation_id"
            ).fetchall()
        return [
            DocumentOperationPlan.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def save_scan_status(self, value: DocumentScanStatus) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO document_scan_status(singleton, payload) VALUES (1, ?) "
                "ON CONFLICT(singleton) DO UPDATE SET payload = excluded.payload",
                (self._protect(value),),
            )

    def scan_status(self) -> DocumentScanStatus:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM document_scan_status WHERE singleton = 1"
            ).fetchone()
        return (
            DocumentScanStatus.model_validate_json(self._protector.unprotect(row[0]))
            if row
            else DocumentScanStatus()
        )

    def update_disposition(
        self, value: DocumentDispositionProposal, expected_revision: int
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM document_dispositions WHERE disposition_id = ?",
                (value.disposition_id,),
            ).fetchone()
            if row is None:
                raise KeyError(value.disposition_id)
            current = DocumentDispositionProposal.model_validate_json(
                self._protector.unprotect(row[0])
            )
            if (
                current.revision != expected_revision
                or value.revision != expected_revision + 1
            ):
                raise RuntimeError("stale_disposition")
            connection.execute(
                "UPDATE document_dispositions SET payload = ? WHERE disposition_id = ?",
                (self._protect(value), value.disposition_id),
            )

    def save_erp_run(self, value: ERPGovernanceRun) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO erp_governance_runs"
                "(run_id, connector_id, completed_at, payload) VALUES (?, ?, ?, ?)",
                (
                    value.run_id,
                    value.connector_id,
                    value.completed_at.isoformat(),
                    self._protect(value),
                ),
            )

    def erp_runs(self) -> list[ERPGovernanceRun]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM erp_governance_runs ORDER BY completed_at DESC LIMIT 100"
            ).fetchall()
        return [
            ERPGovernanceRun.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def save_metadata_baseline(self, value: ERPMetadataBaseline) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO erp_metadata_baselines(connector_id, payload) VALUES (?, ?) "
                "ON CONFLICT(connector_id) DO UPDATE SET payload = excluded.payload",
                (value.connector_id, self._protect(value)),
            )

    def metadata_baseline(self, connector_id: str) -> ERPMetadataBaseline | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM erp_metadata_baselines WHERE connector_id = ?",
                (connector_id,),
            ).fetchone()
        return (
            ERPMetadataBaseline.model_validate_json(self._protector.unprotect(row[0]))
            if row
            else None
        )

    def delete_all(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                DELETE FROM improvement_signals;
                DELETE FROM improvement_proposals;
                DELETE FROM improvement_evaluations;
                DELETE FROM improvement_profiles;
                DELETE FROM improvement_settings;
                DELETE FROM improvement_personal_keys;
                DELETE FROM document_classifications;
                DELETE FROM document_dispositions;
                DELETE FROM erp_governance_runs;
                DELETE FROM improvement_shadow_runs;
                DELETE FROM document_operation_plans;
                DELETE FROM document_scan_status;
                DELETE FROM erp_metadata_baselines;
                """
            )

    def assert_no_plaintext(self, marker: str) -> None:
        if marker.encode() in self._path.read_bytes():
            raise ValueError("improvement repository contains plaintext content")
