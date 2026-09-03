"""Application composition for assurance runs and the digital solution thread."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime

from app.adapters.evidence.artifacts import build_artifact
from app.domain.assurance import (
    AssuranceFinding,
    AssuranceRun,
    AssuranceRunRequest,
    AssuranceRunStatus,
    AssuranceSchedule,
    ERPMetadataSnapshot,
    FindingReview,
    FindingReviewRequest,
    MetadataValidationResult,
    TrustedRuleSigningKey,
)
from app.domain.context import ContextDocument
from app.domain.ports import AssuranceRepository, EvidenceRepository, SolutionThreadRepository
from app.domain.solution_thread import (
    SolutionLeadDashboard,
    SolutionMindMap,
    SolutionMindMapRequest,
    SolutionThreadEdge,
    SolutionThreadEdgeProposal,
    SolutionThreadEdgeReviewRequest,
    SolutionThreadImpact,
    SolutionThreadImpactRequest,
)
from app.services.assurance import builtin_rule_pack, evaluate_artifact, review_finding
from app.services.erp_assurance import validate_erp_metadata
from app.services.solution_thread import (
    artifact_nodes,
    artifact_revision_edge,
    bounded_impact,
    review_edge,
    solution_mind_map,
    traceability_candidates,
    traceability_matrix,
)


class AssuranceWorkspace:
    def __init__(
        self,
        *,
        evidence_repository: EvidenceRepository,
        repository: AssuranceRepository,
        solution_thread_repository: SolutionThreadRepository,
    ) -> None:
        self._evidence = evidence_repository
        self._repository = repository
        self._thread = solution_thread_repository
        self._improvement_profiles: dict[str, dict[str, object]] = {}
        if not any(
            pack.pack_id == "meeting-intelligence-builtins"
            for pack in repository.rule_packs()
        ):
            repository.save_rule_pack(builtin_rule_pack())

    def set_improvement_profiles(self, profiles: dict[str, dict[str, object]]) -> None:
        self._improvement_profiles = {
            key: dict(value) for key, value in profiles.items() if len(value) <= 32
        }

    def rule_packs(self):
        return self._repository.rule_packs()

    def save_rule_pack(self, rule_pack):
        if not rule_pack.trusted:
            raise PermissionError("untrusted_rule_pack")
        self._repository.save_rule_pack(rule_pack)
        return rule_pack

    def save_trusted_key(self, key: TrustedRuleSigningKey) -> TrustedRuleSigningKey:
        self._repository.save_trusted_key(key)
        return key

    def trusted_keys(self) -> list[TrustedRuleSigningKey]:
        return self._repository.trusted_keys()

    async def run(
        self,
        request: AssuranceRunRequest,
        transient_documents: list[ContextDocument] | None = None,
        unavailable_connector_ids: list[str] | None = None,
    ) -> AssuranceRun:
        run_id = hashlib.sha256(
            f"{datetime.now(UTC).isoformat()}\0{secrets.token_hex(32)}".encode()
        ).hexdigest()
        run = AssuranceRun(
            run_id=run_id,
            connector_ids=request.connector_ids,
            rule_pack_ids=request.rule_pack_ids,
            status=AssuranceRunStatus.running,
        )
        await asyncio.to_thread(self._repository.save_run, run)
        try:
            artifacts = await asyncio.to_thread(
                self._evidence.artifacts, request.connector_ids
            )
            sections = await asyncio.to_thread(
                self._evidence.sections, request.connector_ids
            )
            sections_by_artifact: dict[str, list] = {}
            transient_revisions = {}
            for section in sections:
                sections_by_artifact.setdefault(section.artifact_id, []).append(section)
            for document in transient_documents or []:
                artifact, revision, transient_sections = build_artifact(document)
                artifacts.append(artifact)
                sections_by_artifact[artifact.artifact_id] = transient_sections
                transient_revisions[revision.revision_id] = revision
            evaluated = (
                await asyncio.to_thread(self._repository.evaluated_revision_ids)
                if request.changed_only
                else set()
            )
            selected = [
                artifact
                for artifact in artifacts
                if artifact.current_revision_id not in evaluated
            ]
            packs = [pack for pack in self._repository.rule_packs() if pack.trusted]
            if request.rule_pack_ids:
                packs = [pack for pack in packs if pack.pack_id in request.rule_pack_ids]
            rules = [rule for pack in packs for rule in pack.rules]
            preferred = self._improvement_profiles.get("assurance_rule", {}).get(
                "reviewed_values", []
            )
            preferred_ids = set(preferred) if isinstance(preferred, list) else set()
            rules.sort(key=lambda rule: (rule.rule_id not in preferred_ids, rule.rule_id))
            findings: list[AssuranceFinding] = []
            for artifact in selected:
                revision = transient_revisions.get(artifact.current_revision_id)
                if revision is None:
                    revision = await asyncio.to_thread(
                        self._evidence.revision, artifact.current_revision_id
                    )
                if revision is None:
                    continue
                findings.extend(
                    evaluate_artifact(
                        run_id=run_id,
                        artifact=artifact,
                        revision=revision,
                        sections=sections_by_artifact.get(artifact.artifact_id, []),
                        rules=rules,
                    )
                )
                for node in artifact_nodes(artifact):
                    await asyncio.to_thread(self._thread.save_node, node)
                await asyncio.to_thread(
                    self._thread.save_edge, artifact_revision_edge(artifact)
                )
                trace_nodes, trace_edges = traceability_candidates(
                    artifact, sections_by_artifact.get(artifact.artifact_id, [])
                )
                for node in trace_nodes:
                    await asyncio.to_thread(self._thread.save_node, node)
                for edge in trace_edges:
                    await asyncio.to_thread(self._thread.save_edge, edge)
            await asyncio.to_thread(self._repository.save_findings, findings)
            await asyncio.to_thread(
                self._repository.mark_revisions_evaluated,
                [artifact.current_revision_id for artifact in selected],
            )
            completed = run.model_copy(
                update={
                    "status": (
                        AssuranceRunStatus.degraded
                        if unavailable_connector_ids
                        else AssuranceRunStatus.completed
                    ),
                    "completed_at": datetime.now(UTC),
                    "artifact_count": len(artifacts),
                    "changed_revision_count": len(selected),
                    "finding_count": len(findings),
                    "detail_code": (
                        "connector_unavailable" if unavailable_connector_ids else None
                    ),
                }
            )
            await asyncio.to_thread(self._repository.save_run, completed)
            return completed
        except (OSError, RuntimeError, ValueError):
            failed = run.model_copy(
                update={
                    "status": AssuranceRunStatus.degraded,
                    "completed_at": datetime.now(UTC),
                    "detail_code": "assurance_run_degraded",
                }
            )
            await asyncio.to_thread(self._repository.save_run, failed)
            return failed

    def run_result(self, run_id: str) -> AssuranceRun | None:
        return self._repository.run(run_id)

    def findings(self) -> list[AssuranceFinding]:
        return self._repository.findings()

    def review(
        self, finding_id: str, request: FindingReviewRequest
    ) -> AssuranceFinding:
        finding = next(
            (value for value in self._repository.findings() if value.finding_id == finding_id),
            None,
        )
        if finding is None:
            raise KeyError(finding_id)
        review = FindingReview(
            review_id=hashlib.sha256(
                f"{finding_id}\0{request.expected_revision}\0{request.action}\0{secrets.token_hex(16)}".encode()
            ).hexdigest(),
            finding_id=finding_id,
            expected_revision=request.expected_revision,
            action=request.action,
            note=request.note,
        )
        updated = review_finding(finding, review)
        self._repository.review_finding(updated, review)
        return updated

    def save_schedule(self, schedule: AssuranceSchedule) -> AssuranceSchedule:
        self._repository.save_schedule(schedule)
        return schedule

    def schedules(self) -> list[AssuranceSchedule]:
        return self._repository.schedules()

    def impact(self, request: SolutionThreadImpactRequest) -> SolutionThreadImpact:
        return bounded_impact(
            request.root_node_id,
            request.depth,
            self._thread.nodes(),
            self._thread.edges(),
        )

    def mind_map(self, request: SolutionMindMapRequest) -> SolutionMindMap:
        return solution_mind_map(
            request,
            self._thread.nodes(),
            self._thread.edges(),
        )

    def suggested_edges(self) -> list[SolutionThreadEdgeProposal]:
        nodes = {node.node_id: node for node in self._thread.nodes()}
        proposals: list[SolutionThreadEdgeProposal] = []
        for edge in self._thread.edges():
            source = nodes.get(edge.source_node_id)
            target = nodes.get(edge.target_node_id)
            if edge.review_status != "suggested" or source is None or target is None:
                continue
            proposals.append(
                SolutionThreadEdgeProposal(
                    edge_id=edge.edge_id,
                    source_label=source.label,
                    target_label=target.label,
                    source_reference=source.source_reference,
                    relation=edge.relation,
                    revision=edge.revision,
                )
            )
        return proposals

    def review_edge(
        self, edge_id: str, request: SolutionThreadEdgeReviewRequest
    ) -> SolutionThreadEdge:
        edge = next(
            (value for value in self._thread.edges() if value.edge_id == edge_id),
            None,
        )
        if edge is None:
            raise KeyError(edge_id)
        updated = review_edge(edge, request)
        self._thread.review_edge(updated, request.expected_revision)
        return updated

    def dashboard(
        self,
        *,
        governance_records: list | None = None,
        governance_alerts: list | None = None,
        meetings_requiring_review: int = 0,
    ) -> SolutionLeadDashboard:
        findings = [
            finding
            for finding in self._repository.findings()
            if finding.status.value == "open"
        ]
        matrix = traceability_matrix(self._thread.nodes(), self._thread.edges())
        return SolutionLeadDashboard(
            new_findings=findings[:200],
            governance_records=(governance_records or [])[:500],
            governance_alerts=(governance_alerts or [])[:100],
            missing_traceability=matrix,
            erp_schema_drift_count=sum(
                finding.rule_id.endswith("erp-metadata") for finding in findings
            ),
            architecture_conflict_count=sum(
                finding.rule_id.endswith("architecture-standard") for finding in findings
            ),
            meetings_requiring_review=meetings_requiring_review,
        )

    def validate_erp(
        self, snapshot: ERPMetadataSnapshot, artifact_ids: list[str]
    ) -> MetadataValidationResult:
        artifacts = self._evidence.artifacts()
        if artifact_ids:
            artifacts = [
                artifact for artifact in artifacts if artifact.artifact_id in artifact_ids
            ]
        allowed = {artifact.artifact_id for artifact in artifacts}
        sections = [
            section
            for section in self._evidence.sections()
            if section.artifact_id in allowed
        ]
        run_id = hashlib.sha256(
            f"erp\0{snapshot.snapshot_id}\0{secrets.token_hex(16)}".encode()
        ).hexdigest()
        result = validate_erp_metadata(
            run_id=run_id,
            snapshot=snapshot,
            artifacts=artifacts,
            sections=sections,
        )
        self._repository.save_findings(result.findings)
        return result
