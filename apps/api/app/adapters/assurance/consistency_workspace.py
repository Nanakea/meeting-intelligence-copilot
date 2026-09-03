"""Application workspace for review-gated system/document consistency runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections import Counter
from datetime import UTC, datetime

from app.adapters.evidence.artifacts import build_artifact
from app.domain.assurance import ERPMetadataSnapshot
from app.domain.consistency import (
    AuthorityPolicy,
    ConsistencyClaimReviewRequest,
    ConsistencyDashboard,
    ConsistencyExport,
    ConsistencyExportRequest,
    ConsistencyFinding,
    ConsistencyFindingFilter,
    ConsistencyFindingReviewRequest,
    ConsistencyFindingStatus,
    ConsistencyReviewEvent,
    ConsistencyRun,
    ConsistencyRunRequest,
    ConsistencyRunStatus,
    DocumentClaim,
)
from app.domain.context import ConnectorKind, ContextDocument, EntityGlossaryEntry
from app.domain.ports import (
    ConsistencyClaimSuggester,
    ConsistencyRepository,
    EvidenceRepository,
    SolutionThreadRepository,
)
from app.domain.solution_thread import (
    SolutionEdgeKind,
    SolutionNodeKind,
    SolutionThreadEdge,
    SolutionThreadNode,
)
from app.services.consistency import (
    compare_claims_to_observations,
    extract_document_claims,
    facts_from_erp_snapshot,
    facts_from_observed_artifacts,
    render_consistency_export,
    review_claim,
    review_finding,
)


class ConsistencyWorkspace:
    def __init__(
        self,
        *,
        evidence_repository: EvidenceRepository,
        repository: ConsistencyRepository,
        claim_suggester: ConsistencyClaimSuggester | None = None,
        solution_thread_repository: SolutionThreadRepository | None = None,
    ) -> None:
        self._evidence = evidence_repository
        self._repository = repository
        self._claim_suggester = claim_suggester
        self._thread = solution_thread_repository

    async def run(
        self,
        request: ConsistencyRunRequest,
        *,
        connector_labels: dict[str, str],
        connector_kinds: dict[str, ConnectorKind],
        glossary_entries: list[EntityGlossaryEntry],
        erp_snapshots: list[ERPMetadataSnapshot],
        transient_documents: list[ContextDocument],
        leased_connector_ids: set[str],
        unavailable_connector_ids: list[str],
    ) -> ConsistencyRun:
        run_id = hashlib.sha256(
            f"{datetime.now(UTC).isoformat()}\0{secrets.token_hex(32)}".encode()
        ).hexdigest()
        run = ConsistencyRun(
            run_id=run_id,
            document_connector_ids=request.document_connector_ids,
            observed_connector_ids=request.observed_connector_ids,
            status=ConsistencyRunStatus.running,
            language=request.language,
        )
        await asyncio.to_thread(self._repository.save_consistency_run, run)
        try:
            document_artifacts = await asyncio.to_thread(
                self._evidence.artifacts, request.document_connector_ids
            )
            observed_artifacts = await asyncio.to_thread(
                self._evidence.artifacts, request.observed_connector_ids
            )
            if request.artifact_ids:
                selected_ids = set(request.artifact_ids)
                document_artifacts = [
                    value for value in document_artifacts if value.artifact_id in selected_ids
                ]
            document_ids = {value.artifact_id for value in document_artifacts}
            observed_ids = {value.artifact_id for value in observed_artifacts}
            sections = await asyncio.to_thread(self._evidence.sections)
            document_sections = [
                value for value in sections if value.artifact_id in document_ids
            ]
            for document in transient_documents:
                artifact, _, transient_sections = build_artifact(document)
                if request.artifact_ids and artifact.artifact_id not in selected_ids:
                    continue
                document_artifacts.append(artifact)
                document_sections.extend(transient_sections)
            observed_sections = [
                value for value in sections if value.artifact_id in observed_ids
            ]
            policies = await asyncio.to_thread(self._repository.authority_policies)
            persisted_claims = await asyncio.to_thread(
                self._repository.consistency_claims
            )
            fingerprint_parts = [
                json.dumps(
                    request.model_dump(exclude={"changed_only"}, mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                *sorted(
                    f"document:{value.artifact_id}:{value.current_revision_id}"
                    for value in document_artifacts
                ),
                *sorted(
                    f"observed:{value.artifact_id}:{value.current_revision_id}"
                    for value in observed_artifacts
                ),
                *sorted(
                    f"erp:{value.connector_id}:{value.metadata_hash}"
                    for value in erp_snapshots
                ),
                *sorted(
                    f"policy:{value.policy_id}:{value.revision}:{value.enabled}"
                    for value in policies
                ),
                *sorted(
                    f"claim:{value.claim_id}:{value.revision}:{value.status.value}"
                    for value in persisted_claims
                    if value.origin.value == "semantic_confirmed"
                ),
            ]
            source_fingerprint = hashlib.sha256(
                "\0".join(fingerprint_parts).encode()
            ).hexdigest()
            if request.changed_only:
                previous = await asyncio.to_thread(
                    self._repository.consistency_run_for_source_fingerprint,
                    source_fingerprint,
                )
                if previous is not None:
                    unchanged = run.model_copy(
                        update={
                            "status": ConsistencyRunStatus.completed,
                            "completed_at": datetime.now(UTC),
                            "claim_count": previous.claim_count,
                            "observation_count": previous.observation_count,
                            "finding_count": previous.finding_count,
                            "ambiguous_count": previous.ambiguous_count,
                            "source_fingerprint": source_fingerprint,
                            "reused_run_id": previous.run_id,
                            "detail_code": "unchanged_sources",
                        }
                    )
                    await asyncio.to_thread(
                        self._repository.save_consistency_run, unchanged
                    )
                    return unchanged
            claims = extract_document_claims(
                artifacts=document_artifacts,
                sections=document_sections,
                connector_labels=connector_labels,
                glossary_entries=glossary_entries,
                language=request.language,
                environment_filter=request.environment,
            )
            current_revisions = {
                artifact.current_revision_id for artifact in document_artifacts
            }
            persisted_semantic = [
                value
                for value in persisted_claims
                if value.origin.value == "semantic_confirmed"
                and value.citation.source_revision in current_revisions
            ]
            proposals = (
                await asyncio.to_thread(
                    self._claim_suggester.suggest,
                    document_artifacts,
                    document_sections,
                    connector_labels,
                    request.language,
                )
                if self._claim_suggester is not None
                else []
            )
            claims = list(
                {
                    value.claim_id: value
                    for value in [*claims, *persisted_semantic, *proposals]
                }.values()
            )
            observations = facts_from_observed_artifacts(
                artifacts=observed_artifacts,
                sections=observed_sections,
                connector_labels=connector_labels,
                glossary_entries=glossary_entries,
                language=request.language,
                environment_filter=request.environment,
                leased_connector_ids=leased_connector_ids,
            )
            for snapshot in erp_snapshots:
                observations.extend(
                    facts_from_erp_snapshot(
                        snapshot,
                        connector_kind=connector_kinds[snapshot.connector_id],
                        connector_label=connector_labels[snapshot.connector_id],
                        glossary_entries=glossary_entries,
                    )
                )
            observed_scope = []
            seen_scope: set[tuple[str, str]] = set()
            for observation in observations:
                key = (
                    observation.citation.connector_id,
                    observation.citation.source_revision,
                )
                if key not in seen_scope:
                    observed_scope.append(observation.citation)
                    seen_scope.add(key)
            findings = compare_claims_to_observations(
                run_id=run_id,
                claims=claims,
                observations=observations,
                policies=policies,
                exceptions=await asyncio.to_thread(self._repository.approved_exceptions),
                observed_scope_citations=observed_scope,
                detect_undocumented=request.detect_undocumented,
            )
            if self._thread is not None:
                linked_findings: list[ConsistencyFinding] = []
                for finding in findings:
                    document_node = SolutionThreadNode(
                        node_id=hashlib.sha256(
                            f"consistency-document\0"
                            f"{finding.document_claim.citation.source_reference}".encode()
                        ).hexdigest(),
                        kind=SolutionNodeKind.document,
                        label=finding.document_claim.citation.source_label,
                        source_reference=finding.document_claim.citation.source_reference,
                        source_revision=finding.document_claim.citation.source_revision,
                    )
                    system_node = SolutionThreadNode(
                        node_id=hashlib.sha256(
                            f"consistency-system\0{finding.subject}\0"
                            f"{finding.observed_fact.citation.source_reference}".encode()
                        ).hexdigest(),
                        kind=SolutionNodeKind.system,
                        label=finding.subject,
                        source_reference=finding.observed_fact.citation.source_reference,
                        source_revision=finding.observed_fact.citation.source_revision,
                    )
                    await asyncio.to_thread(self._thread.save_node, document_node)
                    await asyncio.to_thread(self._thread.save_node, system_node)
                    for relation in (
                        SolutionEdgeKind.documents,
                        SolutionEdgeKind.conflicts_with,
                    ):
                        await asyncio.to_thread(
                            self._thread.save_edge,
                            SolutionThreadEdge(
                                edge_id=hashlib.sha256(
                                    f"{finding.fingerprint}\0{relation.value}".encode()
                                ).hexdigest(),
                                source_node_id=document_node.node_id,
                                target_node_id=system_node.node_id,
                                relation=relation,
                                source_revision=finding.document_claim.citation.source_revision,
                                provenance_fields=["document_claim", "observed_fact"],
                            ),
                        )
                    linked_findings.append(
                        finding.model_copy(
                            update={
                                "affected_solution_node_ids": [
                                    document_node.node_id,
                                    system_node.node_id,
                                ]
                            }
                        )
                    )
                findings = linked_findings
            await asyncio.to_thread(self._repository.save_consistency_claims, claims)
            await asyncio.to_thread(self._repository.save_consistency_findings, findings)
            completed = run.model_copy(
                update={
                    "status": (
                        ConsistencyRunStatus.degraded
                        if unavailable_connector_ids
                        else ConsistencyRunStatus.completed
                    ),
                    "completed_at": datetime.now(UTC),
                    "claim_count": len(claims),
                    "observation_count": len(observations),
                    "finding_count": len(findings),
                    "ambiguous_count": sum(
                        value.mismatch_kind.value == "ambiguous_match"
                        for value in findings
                    ),
                    "source_fingerprint": source_fingerprint,
                    "unavailable_connector_ids": unavailable_connector_ids,
                    "detail_code": (
                        "connector_unavailable" if unavailable_connector_ids else None
                    ),
                }
            )
            await asyncio.to_thread(self._repository.save_consistency_run, completed)
            if completed.status is ConsistencyRunStatus.completed:
                await asyncio.to_thread(
                    self._repository.mark_consistency_source_evaluated,
                    source_fingerprint,
                    completed.run_id,
                )
            return completed
        except (KeyError, OSError, RuntimeError, ValueError):
            degraded = run.model_copy(
                update={
                    "status": ConsistencyRunStatus.degraded,
                    "completed_at": datetime.now(UTC),
                    "unavailable_connector_ids": unavailable_connector_ids,
                    "detail_code": "consistency_run_degraded",
                }
            )
            await asyncio.to_thread(self._repository.save_consistency_run, degraded)
            return degraded

    def run_result(self, run_id: str) -> ConsistencyRun | None:
        return self._repository.consistency_run(run_id)

    def findings(
        self, filters: ConsistencyFindingFilter | None = None
    ) -> list[ConsistencyFinding]:
        values = self._repository.consistency_findings()
        if filters is None:
            return values
        if filters.system:
            term = filters.system.casefold()
            values = [value for value in values if term in value.subject.casefold()]
        if filters.environment:
            values = [
                value
                for value in values
                if value.document_claim.environment == filters.environment
                or value.observed_fact.environment == filters.environment
            ]
        if filters.repository:
            term = filters.repository.casefold()
            values = [
                value
                for value in values
                if term in value.document_claim.citation.source_reference.casefold()
                or term in value.observed_fact.citation.source_reference.casefold()
            ]
        if filters.document:
            term = filters.document.casefold()
            values = [
                value
                for value in values
                if term in value.document_claim.citation.source_label.casefold()
                or term in value.document_claim.citation.source_reference.casefold()
            ]
        if filters.project:
            term = filters.project.casefold()
            values = [
                value
                for value in values
                if term in value.document_claim.citation.source_label.casefold()
                or term in value.observed_fact.citation.source_label.casefold()
            ]
        if filters.mismatch_kind:
            values = [
                value for value in values if value.mismatch_kind is filters.mismatch_kind
            ]
        if filters.severity:
            values = [value for value in values if value.severity is filters.severity]
        if filters.status:
            values = [value for value in values if value.status is filters.status]
        if filters.owner_role:
            values = [value for value in values if value.owner_role == filters.owner_role]
        if filters.document_kind:
            values = [
                value
                for value in values
                if value.document_claim.document_kind.value == filters.document_kind
            ]
        return values[: filters.limit]

    def finding(self, finding_id: str) -> ConsistencyFinding | None:
        return self._repository.consistency_finding(finding_id)

    def review_finding(
        self, finding_id: str, request: ConsistencyFindingReviewRequest
    ) -> ConsistencyFinding:
        current = self._repository.consistency_finding(finding_id)
        if current is None:
            raise KeyError(finding_id)
        updated, exception = review_finding(current, request)
        event = ConsistencyReviewEvent(
            event_id=hashlib.sha256(
                f"{finding_id}\0{request.expected_revision}\0{request.action}\0"
                f"{secrets.token_hex(16)}".encode()
            ).hexdigest(),
            finding_id=finding_id,
            expected_revision=request.expected_revision,
            resulting_revision=updated.revision,
            action=request.action,
            note=request.note,
        )
        self._repository.review_consistency_finding(updated, event)
        if exception is not None:
            self._repository.save_approved_exception(exception)
        return updated

    def review_claim(
        self, claim_id: str, request: ConsistencyClaimReviewRequest
    ) -> DocumentClaim:
        current = next(
            (
                value
                for value in self._repository.consistency_claims()
                if value.claim_id == claim_id
            ),
            None,
        )
        if current is None:
            raise KeyError(claim_id)
        updated = review_claim(current, request.expected_revision, request.action)
        self._repository.review_consistency_claim(updated)
        return updated

    def policies(self) -> list[AuthorityPolicy]:
        return self._repository.authority_policies()

    def save_policy(self, requested: AuthorityPolicy) -> AuthorityPolicy:
        current = next(
            (value for value in self.policies() if value.policy_id == requested.policy_id),
            None,
        )
        if current is None:
            if requested.revision != 1:
                raise RuntimeError("stale_authority_policy")
            saved = requested
        else:
            if requested.revision != current.revision:
                raise RuntimeError("stale_authority_policy")
            saved = requested.model_copy(
                update={
                    "revision": current.revision + 1,
                    "reviewed_at": datetime.now(UTC),
                }
            )
        self._repository.save_authority_policy(saved)
        return saved

    def dashboard(self) -> ConsistencyDashboard:
        findings = self._repository.consistency_findings()
        proposed = [
            value
            for value in self._repository.consistency_claims()
            if value.status.value == "proposed"
        ]
        return ConsistencyDashboard(
            open_findings=sum(
                value.status is ConsistencyFindingStatus.open for value in findings
            ),
            confirmed_findings=sum(
                value.status is ConsistencyFindingStatus.confirmed for value in findings
            ),
            approved_exceptions=sum(
                value.status is ConsistencyFindingStatus.approved_exception
                for value in findings
            ),
            ambiguous_findings=sum(
                value.mismatch_kind.value == "ambiguous_match" for value in findings
            ),
            by_mismatch_kind=dict(Counter(value.mismatch_kind.value for value in findings)),
            by_severity=dict(Counter(value.severity.value for value in findings)),
            by_attribution=dict(
                Counter(value.comparison.attribution.value for value in findings)
            ),
            proposed_semantic_claims=proposed[:100],
            recent_findings=list(reversed(findings))[:200],
        )

    def export(
        self, finding_id: str, request: ConsistencyExportRequest
    ) -> ConsistencyExport:
        finding = self._repository.consistency_finding(finding_id)
        if finding is None:
            raise KeyError(finding_id)
        return render_consistency_export(finding, request)
