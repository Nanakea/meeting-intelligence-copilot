"""Encrypted SQLite/WAL repositories for assurance and solution-thread state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.adapters.context.windows_security import DataProtector
from app.domain.assurance import (
    AssuranceFinding,
    AssuranceRulePack,
    AssuranceRun,
    AssuranceSchedule,
    FindingReview,
    TrustedRuleSigningKey,
)
from app.domain.consistency import (
    ApprovedException,
    AuthorityPolicy,
    ConsistencyFinding,
    ConsistencyFindingStatus,
    ConsistencyReviewEvent,
    ConsistencyRun,
    DocumentClaim,
)
from app.domain.solution_thread import SolutionThreadEdge, SolutionThreadNode


class EncryptedAssuranceRepository:
    def __init__(self, path: Path, protector: DataProtector) -> None:
        self._path = path
        self._protector = protector
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = NORMAL;
                CREATE TABLE IF NOT EXISTS assurance_rule_packs (
                    pack_key TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_runs (
                    run_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_findings (
                    finding_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS assurance_finding_fingerprint
                    ON assurance_findings(fingerprint, revision_id);
                CREATE TABLE IF NOT EXISTS assurance_finding_reviews (
                    review_id TEXT PRIMARY KEY,
                    finding_id TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_evaluated_revisions (
                    revision_id TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS assurance_trusted_keys (
                    key_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS solution_thread_nodes (
                    node_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS solution_thread_edges (
                    edge_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consistency_runs (
                    run_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consistency_evaluated_sources (
                    source_fingerprint TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consistency_claims (
                    claim_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS consistency_claim_fingerprint
                    ON consistency_claims(fingerprint);
                CREATE TABLE IF NOT EXISTS consistency_findings (
                    finding_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS consistency_finding_fingerprint
                    ON consistency_findings(fingerprint);
                CREATE TABLE IF NOT EXISTS consistency_reviews (
                    event_id TEXT PRIMARY KEY,
                    finding_id TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consistency_authority_policies (
                    policy_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consistency_exceptions (
                    exception_id TEXT PRIMARY KEY,
                    finding_fingerprint TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)

    def _protect(self, value: object) -> bytes:
        return self._protector.protect(value.model_dump_json().encode())  # type: ignore[attr-defined]

    def save_rule_pack(self, rule_pack: AssuranceRulePack) -> None:
        key = f"{rule_pack.pack_id}@{rule_pack.version}"
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO assurance_rule_packs(pack_key, payload) VALUES (?, ?) "
                "ON CONFLICT(pack_key) DO UPDATE SET payload = excluded.payload",
                (key, self._protect(rule_pack)),
            )

    def rule_packs(self) -> list[AssuranceRulePack]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM assurance_rule_packs ORDER BY pack_key"
            ).fetchall()
        return [
            AssuranceRulePack.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def save_run(self, run: AssuranceRun) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO assurance_runs(run_id, payload) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET payload = excluded.payload",
                (run.run_id, self._protect(run)),
            )

    def run(self, run_id: str) -> AssuranceRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM assurance_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return AssuranceRun.model_validate_json(self._protector.unprotect(row[0]))

    def save_findings(self, findings: list[AssuranceFinding]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for finding in findings:
                old_rows = connection.execute(
                    "SELECT finding_id, payload FROM assurance_findings "
                    "WHERE fingerprint = ? AND revision_id <> ?",
                    (finding.fingerprint, finding.revision_id),
                ).fetchall()
                for old_id, payload in old_rows:
                    old = AssuranceFinding.model_validate_json(
                        self._protector.unprotect(payload)
                    )
                    if old.status.value == "open":
                        old = old.model_copy(update={"status": "superseded"})
                        connection.execute(
                            "UPDATE assurance_findings SET payload = ? WHERE finding_id = ?",
                            (self._protect(old), old_id),
                        )
                connection.execute(
                    "INSERT OR IGNORE INTO assurance_findings(finding_id, fingerprint, "
                    "revision_id, payload) VALUES (?, ?, ?, ?)",
                    (
                        finding.finding_id,
                        finding.fingerprint,
                        finding.revision_id,
                        self._protect(finding),
                    ),
                )

    def findings(self) -> list[AssuranceFinding]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM assurance_findings ORDER BY finding_id"
            ).fetchall()
        return [
            AssuranceFinding.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def review_finding(self, finding: AssuranceFinding, review: FindingReview) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT payload FROM assurance_findings WHERE finding_id = ?",
                (finding.finding_id,),
            ).fetchone()
            if current is None:
                raise KeyError(finding.finding_id)
            persisted = AssuranceFinding.model_validate_json(
                self._protector.unprotect(current[0])
            )
            if persisted.revision + 1 != finding.revision:
                raise RuntimeError("stale_finding")
            connection.execute(
                "UPDATE assurance_findings SET payload = ? WHERE finding_id = ?",
                (self._protect(finding), finding.finding_id),
            )
            connection.execute(
                "INSERT INTO assurance_finding_reviews(review_id, finding_id, payload) "
                "VALUES (?, ?, ?)",
                (review.review_id, finding.finding_id, self._protect(review)),
            )

    def save_schedule(self, schedule: AssuranceSchedule) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO assurance_schedules(schedule_id, payload) VALUES (?, ?) "
                "ON CONFLICT(schedule_id) DO UPDATE SET payload = excluded.payload",
                (schedule.schedule_id, self._protect(schedule)),
            )

    def schedules(self) -> list[AssuranceSchedule]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM assurance_schedules ORDER BY schedule_id"
            ).fetchall()
        return [
            AssuranceSchedule.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def evaluated_revision_ids(self) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT revision_id FROM assurance_evaluated_revisions"
            ).fetchall()
        return {str(row[0]) for row in rows}

    def mark_revisions_evaluated(self, revision_ids: list[str]) -> None:
        with self._connect() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO assurance_evaluated_revisions(revision_id) VALUES (?)",
                [(revision_id,) for revision_id in revision_ids],
            )

    def save_trusted_key(self, key: TrustedRuleSigningKey) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO assurance_trusted_keys(key_id, payload) VALUES (?, ?) "
                "ON CONFLICT(key_id) DO UPDATE SET payload = excluded.payload",
                (key.key_id, self._protect(key)),
            )

    def trusted_keys(self) -> list[TrustedRuleSigningKey]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM assurance_trusted_keys ORDER BY key_id"
            ).fetchall()
        return [
            TrustedRuleSigningKey.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def save_consistency_run(self, run: ConsistencyRun) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO consistency_runs(run_id, payload) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET payload = excluded.payload",
                (run.run_id, self._protect(run)),
            )

    def consistency_run(self, run_id: str) -> ConsistencyRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM consistency_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return (
            ConsistencyRun.model_validate_json(self._protector.unprotect(row[0]))
            if row is not None
            else None
        )

    def consistency_run_for_source_fingerprint(
        self, source_fingerprint: str
    ) -> ConsistencyRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM consistency_evaluated_sources "
                "WHERE source_fingerprint = ?",
                (source_fingerprint,),
            ).fetchone()
        return self.consistency_run(str(row[0])) if row is not None else None

    def mark_consistency_source_evaluated(
        self, source_fingerprint: str, run_id: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO consistency_evaluated_sources(source_fingerprint, run_id) "
                "VALUES (?, ?) ON CONFLICT(source_fingerprint) DO UPDATE SET "
                "run_id = excluded.run_id",
                (source_fingerprint, run_id),
            )

    def save_consistency_claims(self, claims: list[DocumentClaim]) -> None:
        with self._connect() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO consistency_claims(claim_id, fingerprint, payload) "
                "VALUES (?, ?, ?)",
                [
                    (claim.claim_id, claim.fingerprint, self._protect(claim))
                    for claim in claims
                ],
            )

    def consistency_claims(self) -> list[DocumentClaim]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM consistency_claims ORDER BY claim_id"
            ).fetchall()
        return [
            DocumentClaim.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def review_consistency_claim(self, claim: DocumentClaim) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM consistency_claims WHERE claim_id = ?",
                (claim.claim_id,),
            ).fetchone()
            if row is None:
                raise KeyError(claim.claim_id)
            current = DocumentClaim.model_validate_json(self._protector.unprotect(row[0]))
            if claim.revision != current.revision + 1:
                raise RuntimeError("stale_consistency_claim")
            connection.execute(
                "UPDATE consistency_claims SET payload = ? WHERE claim_id = ?",
                (self._protect(claim), claim.claim_id),
            )

    def save_consistency_findings(self, findings: list[ConsistencyFinding]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for finding in findings:
                old_rows = connection.execute(
                    "SELECT finding_id, payload FROM consistency_findings "
                    "WHERE fingerprint = ? AND finding_id <> ?",
                    (finding.fingerprint, finding.finding_id),
                ).fetchall()
                for old_id, payload in old_rows:
                    old = ConsistencyFinding.model_validate_json(
                        self._protector.unprotect(payload)
                    )
                    if old.status is ConsistencyFindingStatus.open:
                        old = old.model_copy(
                            update={
                                "status": ConsistencyFindingStatus.superseded,
                                "revision": old.revision + 1,
                            }
                        )
                        connection.execute(
                            "UPDATE consistency_findings SET payload = ? WHERE finding_id = ?",
                            (self._protect(old), old_id),
                        )
                connection.execute(
                    "INSERT OR IGNORE INTO consistency_findings"
                    "(finding_id, fingerprint, payload) VALUES (?, ?, ?)",
                    (finding.finding_id, finding.fingerprint, self._protect(finding)),
                )

    def consistency_finding(self, finding_id: str) -> ConsistencyFinding | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM consistency_findings WHERE finding_id = ?",
                (finding_id,),
            ).fetchone()
        return (
            ConsistencyFinding.model_validate_json(self._protector.unprotect(row[0]))
            if row is not None
            else None
        )

    def consistency_findings(self) -> list[ConsistencyFinding]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM consistency_findings ORDER BY finding_id"
            ).fetchall()
        return [
            ConsistencyFinding.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def review_consistency_finding(
        self, finding: ConsistencyFinding, event: ConsistencyReviewEvent
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM consistency_findings WHERE finding_id = ?",
                (finding.finding_id,),
            ).fetchone()
            if row is None:
                raise KeyError(finding.finding_id)
            current = ConsistencyFinding.model_validate_json(
                self._protector.unprotect(row[0])
            )
            if (
                current.revision != event.expected_revision
                or finding.revision != event.resulting_revision
            ):
                raise RuntimeError("stale_consistency_finding")
            connection.execute(
                "UPDATE consistency_findings SET payload = ? WHERE finding_id = ?",
                (self._protect(finding), finding.finding_id),
            )
            connection.execute(
                "INSERT INTO consistency_reviews(event_id, finding_id, payload) "
                "VALUES (?, ?, ?)",
                (event.event_id, finding.finding_id, self._protect(event)),
            )

    def save_authority_policy(self, policy: AuthorityPolicy) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM consistency_authority_policies WHERE policy_id = ?",
                (policy.policy_id,),
            ).fetchone()
            if row is not None:
                current = AuthorityPolicy.model_validate_json(
                    self._protector.unprotect(row[0])
                )
                if policy.revision != current.revision + 1:
                    raise RuntimeError("stale_authority_policy")
            elif policy.revision != 1:
                raise RuntimeError("stale_authority_policy")
            connection.execute(
                "INSERT INTO consistency_authority_policies(policy_id, payload) VALUES (?, ?) "
                "ON CONFLICT(policy_id) DO UPDATE SET payload = excluded.payload",
                (policy.policy_id, self._protect(policy)),
            )

    def authority_policies(self) -> list[AuthorityPolicy]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM consistency_authority_policies ORDER BY policy_id"
            ).fetchall()
        return [
            AuthorityPolicy.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def save_approved_exception(self, exception: ApprovedException) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO consistency_exceptions"
                "(exception_id, finding_fingerprint, payload) VALUES (?, ?, ?)",
                (
                    exception.exception_id,
                    exception.finding_fingerprint,
                    self._protect(exception),
                ),
            )

    def approved_exceptions(self) -> list[ApprovedException]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM consistency_exceptions ORDER BY exception_id"
            ).fetchall()
        return [
            ApprovedException.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def save_node(self, node: SolutionThreadNode) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM solution_thread_nodes WHERE node_id = ?",
                (node.node_id,),
            ).fetchone()
            if row is not None:
                current = SolutionThreadNode.model_validate_json(
                    self._protector.unprotect(row[0])
                )
                if current.confirmed and not node.confirmed:
                    return
            connection.execute(
                "INSERT INTO solution_thread_nodes(node_id, payload) VALUES (?, ?) "
                "ON CONFLICT(node_id) DO UPDATE SET payload = excluded.payload",
                (node.node_id, self._protect(node)),
            )

    def save_edge(self, edge: SolutionThreadEdge) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM solution_thread_edges WHERE edge_id = ?",
                (edge.edge_id,),
            ).fetchone()
            if row is not None:
                current = SolutionThreadEdge.model_validate_json(
                    self._protector.unprotect(row[0])
                )
                if current.revision >= edge.revision:
                    return
            connection.execute(
                "INSERT INTO solution_thread_edges(edge_id, payload) VALUES (?, ?) "
                "ON CONFLICT(edge_id) DO UPDATE SET payload = excluded.payload",
                (edge.edge_id, self._protect(edge)),
            )

    def review_edge(self, edge: SolutionThreadEdge, expected_revision: int) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM solution_thread_edges WHERE edge_id = ?",
                (edge.edge_id,),
            ).fetchone()
            if row is None:
                raise KeyError(edge.edge_id)
            current = SolutionThreadEdge.model_validate_json(
                self._protector.unprotect(row[0])
            )
            if current.revision != expected_revision or edge.revision != expected_revision + 1:
                raise RuntimeError("stale_solution_edge")
            connection.execute(
                "UPDATE solution_thread_edges SET payload = ? WHERE edge_id = ?",
                (self._protect(edge), edge.edge_id),
            )

    def nodes(self) -> list[SolutionThreadNode]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM solution_thread_nodes ORDER BY node_id"
            ).fetchall()
        return [
            SolutionThreadNode.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def edges(self) -> list[SolutionThreadEdge]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM solution_thread_edges ORDER BY edge_id"
            ).fetchall()
        return [
            SolutionThreadEdge.model_validate_json(self._protector.unprotect(row[0]))
            for row in rows
        ]

    def assert_no_plaintext(self, marker: str) -> None:
        if marker.encode() in self._path.read_bytes():
            raise ValueError("assurance repository contains plaintext content")
