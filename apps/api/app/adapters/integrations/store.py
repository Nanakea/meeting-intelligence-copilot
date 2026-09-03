"""DPAPI-protected SQLite/WAL storage for v9 integration workflows."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.adapters.context.windows_security import DataProtector
from app.domain.integrations import (
    DataQualityFinding,
    DataQualityRulePack,
    DataQualityRun,
    DocumentRoutingDestination,
    DocumentRoutingProposal,
    ExternalActionDraft,
    ExternalActionEvent,
    NotificationPolicy,
)


class EncryptedIntegrationRepository:
    def __init__(self, path: Path, protector: DataProtector) -> None:
        self._path = path
        self._protector = protector
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = NORMAL;
                CREATE TABLE IF NOT EXISTS data_quality_runs (
                    run_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS data_quality_findings (
                    finding_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS data_quality_rule_packs (
                    pack_key TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS data_quality_fingerprint
                    ON data_quality_findings(fingerprint);
                CREATE TABLE IF NOT EXISTS external_actions (
                    action_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS external_action_fingerprint
                    ON external_actions(fingerprint);
                CREATE TABLE IF NOT EXISTS external_action_events (
                    event_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    UNIQUE(action_id, revision)
                );
                CREATE TABLE IF NOT EXISTS notification_policies (
                    policy_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_routing_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_routing_destinations (
                    destination_ref TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_routing_events (
                    proposal_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (proposal_id, revision)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)

    def _protect(self, value) -> bytes:
        return self._protector.protect(value.model_dump_json().encode())

    def save_run(self, run: DataQualityRun) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO data_quality_runs(run_id, payload) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET payload = excluded.payload",
                (run.run_id, self._protect(run)),
            )

    def run(self, run_id: str) -> DataQualityRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM data_quality_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return (
            DataQualityRun.model_validate_json(self._protector.unprotect(row[0]))
            if row
            else None
        )

    def save_findings(self, findings: list[DataQualityFinding]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for finding in findings:
                existing = connection.execute(
                    "SELECT payload FROM data_quality_findings WHERE fingerprint = ? "
                    "ORDER BY finding_id LIMIT 1",
                    (finding.fingerprint,),
                ).fetchone()
                if existing is not None:
                    current = DataQualityFinding.model_validate_json(
                        self._protector.unprotect(existing[0])
                    )
                    if current.status != "open":
                        continue
                connection.execute(
                    "INSERT INTO data_quality_findings(finding_id, fingerprint, payload) "
                    "VALUES (?, ?, ?) ON CONFLICT(finding_id) DO NOTHING",
                    (finding.finding_id, finding.fingerprint, self._protect(finding)),
                )

    def save_rule_pack(self, pack: DataQualityRulePack) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO data_quality_rule_packs(pack_key, payload) VALUES (?, ?) "
                "ON CONFLICT(pack_key) DO UPDATE SET payload = excluded.payload",
                (f"{pack.pack_id}@{pack.version}", self._protect(pack)),
            )

    def rule_packs(self) -> list[DataQualityRulePack]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM data_quality_rule_packs ORDER BY pack_key"
            ).fetchall()
        return [
            DataQualityRulePack.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def findings(self) -> list[DataQualityFinding]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM data_quality_findings ORDER BY finding_id"
            ).fetchall()
        return [
            DataQualityFinding.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def review_finding(self, updated: DataQualityFinding, expected_revision: int) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM data_quality_findings WHERE finding_id = ?",
                (updated.finding_id,),
            ).fetchone()
            if row is None:
                raise KeyError(updated.finding_id)
            current = DataQualityFinding.model_validate_json(
                self._protector.unprotect(row[0])
            )
            if current.revision != expected_revision or updated.revision != expected_revision + 1:
                raise RuntimeError("stale_finding")
            connection.execute(
                "UPDATE data_quality_findings SET payload = ? WHERE finding_id = ?",
                (self._protect(updated), updated.finding_id),
            )

    def create_action(self, action: ExternalActionDraft) -> ExternalActionDraft:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload FROM external_actions WHERE fingerprint = ? "
                "ORDER BY action_id LIMIT 1",
                (action.fingerprint,),
            ).fetchone()
            if existing:
                return ExternalActionDraft.model_validate_json(
                    self._protector.unprotect(existing[0])
                )
            connection.execute(
                "INSERT INTO external_actions(action_id, fingerprint, payload) VALUES (?, ?, ?)",
                (action.action_id, action.fingerprint, self._protect(action)),
            )
        return action

    def actions(self) -> list[ExternalActionDraft]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM external_actions ORDER BY action_id"
            ).fetchall()
        return [
            ExternalActionDraft.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def action(self, action_id: str) -> ExternalActionDraft | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM external_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
        return (
            ExternalActionDraft.model_validate_json(self._protector.unprotect(row[0]))
            if row
            else None
        )

    def transition_action(
        self,
        action: ExternalActionDraft,
        event: ExternalActionEvent,
        expected_revision: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM external_actions WHERE action_id = ?",
                (action.action_id,),
            ).fetchone()
            if row is None:
                raise KeyError(action.action_id)
            current = ExternalActionDraft.model_validate_json(
                self._protector.unprotect(row[0])
            )
            if current.revision != expected_revision or action.revision != expected_revision + 1:
                raise RuntimeError("stale_action")
            connection.execute(
                "UPDATE external_actions SET payload = ? WHERE action_id = ?",
                (self._protect(action), action.action_id),
            )
            connection.execute(
                "INSERT INTO external_action_events(event_id, action_id, revision, payload) "
                "VALUES (?, ?, ?, ?)",
                (event.event_id, action.action_id, event.revision, self._protect(event)),
            )

    def save_policy(self, policy: NotificationPolicy, expected_revision: int | None) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM notification_policies WHERE policy_id = ?",
                (policy.policy_id,),
            ).fetchone()
            if row is None:
                if expected_revision not in {None, 0} or policy.revision != 0:
                    raise RuntimeError("stale_policy")
                connection.execute(
                    "INSERT INTO notification_policies(policy_id, payload) VALUES (?, ?)",
                    (policy.policy_id, self._protect(policy)),
                )
                return
            current = NotificationPolicy.model_validate_json(
                self._protector.unprotect(row[0])
            )
            if expected_revision != current.revision or policy.revision != current.revision + 1:
                raise RuntimeError("stale_policy")
            connection.execute(
                "UPDATE notification_policies SET payload = ? WHERE policy_id = ?",
                (self._protect(policy), policy.policy_id),
            )

    def policies(self) -> list[NotificationPolicy]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM notification_policies ORDER BY policy_id"
            ).fetchall()
        return [
            NotificationPolicy.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def save_routing_proposal(self, proposal: DocumentRoutingProposal) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO document_routing_proposals(proposal_id, payload) VALUES (?, ?) "
                "ON CONFLICT(proposal_id) DO UPDATE SET payload = excluded.payload",
                (proposal.proposal_id, self._protect(proposal)),
            )

    def routing_proposals(self) -> list[DocumentRoutingProposal]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM document_routing_proposals ORDER BY proposal_id"
            ).fetchall()
        return [
            DocumentRoutingProposal.model_validate_json(
                self._protector.unprotect(row[0])
            )
            for row in rows
        ]

    def save_routing_destination(
        self, destination: DocumentRoutingDestination, root_path: str
    ) -> DocumentRoutingDestination:
        if not root_path or len(root_path) > 2_048 or "\0" in root_path:
            raise ValueError("routing root is invalid")
        payload = self._protector.protect(
            json.dumps(
                {
                    "destination": destination.model_dump(mode="json"),
                    "root_path": root_path,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO document_routing_destinations(destination_ref, payload) "
                "VALUES (?, ?) ON CONFLICT(destination_ref) DO UPDATE SET "
                "payload = excluded.payload",
                (destination.destination_ref, payload),
            )
        return destination

    def routing_destinations(self) -> list[DocumentRoutingDestination]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM document_routing_destinations ORDER BY destination_ref"
            ).fetchall()
        return [
            DocumentRoutingDestination.model_validate(
                json.loads(self._protector.unprotect(row[0]))["destination"]
            )
            for row in rows
        ]

    def routing_root(self, destination_ref: str) -> Path | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM document_routing_destinations WHERE destination_ref = ?",
                (destination_ref,),
            ).fetchone()
        if row is None:
            return None
        return Path(json.loads(self._protector.unprotect(row[0]))["root_path"])

    def record_routing_operation(
        self,
        proposal: DocumentRoutingProposal,
        *,
        source_path: str,
        destination_path: str,
        content_hash: str,
    ) -> None:
        payload = self._protector.protect(
            json.dumps(
                {
                    "proposal_id": proposal.proposal_id,
                    "revision": proposal.revision,
                    "action": proposal.action.value,
                    "source_path": source_path,
                    "destination_path": destination_path,
                    "content_hash": content_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO document_routing_events(proposal_id, revision, payload) "
                "VALUES (?, ?, ?)",
                (proposal.proposal_id, proposal.revision, payload),
            )

    def assert_no_plaintext(self, marker: str) -> None:
        if marker.encode() in self._path.read_bytes():
            raise ValueError("integration repository contains plaintext content")
