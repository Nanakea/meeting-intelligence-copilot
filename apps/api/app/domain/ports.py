"""Domain ports (protocols). The domain depends on these abstractions; concrete
adapters implement them (docs/ARCHITECTURE.md §4). Pure — no I/O, no vendors."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol, runtime_checkable

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
    ConsistencyReviewEvent,
    ConsistencyRun,
    DocumentClaim,
)
from app.domain.context import (
    ConnectorHealth,
    ConnectorKind,
    ContextDocument,
    ContextSearchQuery,
)
from app.domain.contracts import InformationGap, MeetingFact, PainPoint, TranscriptEvent
from app.domain.enterprise import (
    AccessLease,
    GatewaySearchRequest,
    SourceChangeEvent,
    SourceSelection,
    SyncCursor,
)
from app.domain.evidence import (
    ArtifactSection,
    DocumentArtifact,
    DocumentRevision,
    EmbeddingProfile,
    EvidenceBundle,
    RetrievalRequest,
)
from app.domain.governance import (
    GovernanceCandidate,
    GovernanceEvent,
    GovernanceRecord,
)
from app.domain.integrations import (
    DocumentRoutingProposal,
    ExternalActionDraft,
    ExternalDeliveryResult,
)
from app.domain.priority import RankedGap
from app.domain.solution_thread import SolutionThreadEdge, SolutionThreadNode


@runtime_checkable
class TranscriptSource(Protocol):
    """Yields platform-neutral TranscriptEvents in chronological order.

    Implementations (e.g. JSONL replay now; whisper.cpp / Vexa later) live under
    ``app/adapters`` and translate platform payloads into ``TranscriptEvent``.
    """

    def events(self) -> AsyncIterator[TranscriptEvent]: ...


@runtime_checkable
class PriorityStrategy(Protocol):
    def rank(
        self,
        gaps: list[InformationGap],
        pains: list[PainPoint],
        facts: list[MeetingFact],
    ) -> list[RankedGap]: ...


@runtime_checkable
class ContextProvider(Protocol):
    """Read-only external context boundary.

    Implementations live under ``app/adapters``. Nothing returned through this
    port is authoritative meeting evidence.
    """

    @property
    def connector_id(self) -> str: ...

    @property
    def kind(self) -> ConnectorKind: ...

    @property
    def display_name(self) -> str: ...

    @property
    def auth_enabled(self) -> bool: ...

    async def health(self) -> ConnectorHealth: ...

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]: ...

    async def sync(self) -> int: ...

    async def close(self) -> None: ...


@runtime_checkable
class GovernanceRepository(Protocol):
    """Append-only durable governance boundary."""

    def save_candidate(self, candidate: GovernanceCandidate) -> GovernanceCandidate: ...

    def candidate(self, candidate_id: str) -> GovernanceCandidate | None: ...

    def candidates(self, session_id: str) -> list[GovernanceCandidate]: ...

    def save_review(
        self,
        candidate: GovernanceCandidate,
        record: GovernanceRecord | None,
        event: GovernanceEvent | None,
    ) -> None: ...

    def record(self, record_id: str) -> GovernanceRecord | None: ...

    def records(self) -> list[GovernanceRecord]: ...

    def save_record_event(self, record: GovernanceRecord, event: GovernanceEvent) -> None: ...

    def delete_meeting(self, session_id: str) -> None: ...

    def delete_all(self) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class GovernanceQuery(Protocol):
    """Read-model seam for a future local or company-hosted workspace."""

    def records(self) -> list[GovernanceRecord]: ...


@runtime_checkable
class GovernanceExport(Protocol):
    """Reviewed export seam. Implementations must not submit external writes."""

    def export(self, record_ids: list[str], format_name: str) -> bytes: ...


@runtime_checkable
class EvidenceRepository(Protocol):
    """Encrypted-at-rest artifact and section repository."""

    def replace_artifact(
        self,
        artifact: DocumentArtifact,
        revision: DocumentRevision,
        sections: list[ArtifactSection],
    ) -> bool: ...

    def artifacts(self, connector_ids: list[str] | None = None) -> list[DocumentArtifact]: ...

    def sections(
        self, connector_ids: list[str] | None = None
    ) -> list[ArtifactSection]: ...

    def revision(self, revision_id: str) -> DocumentRevision | None: ...

    def purge_connector_artifacts(self, connector_id: str) -> int: ...

    def reconcile_connector_artifacts(
        self, connector_id: str, retained_artifact_ids: set[str]
    ) -> int: ...


@runtime_checkable
class RetrievalEngine(Protocol):
    async def retrieve(self, request: RetrievalRequest) -> EvidenceBundle: ...


@runtime_checkable
class ArtifactSource(Protocol):
    async def collect_artifacts(
        self,
    ) -> list[tuple[DocumentArtifact, DocumentRevision, list[ArtifactSection]]]: ...


@runtime_checkable
class ExternalActionExecutor(Protocol):
    """Narrow provider write boundary; retrieval adapters never implement it."""

    async def execute(self, action: ExternalActionDraft) -> ExternalDeliveryResult: ...


@runtime_checkable
class EnterpriseConnectorGateway(Protocol):
    """Company-managed ACL and connector boundary; it never receives transcripts."""

    async def issue_lease(
        self, connector_id: str, principal_hash: str, selections: list[SourceSelection]
    ) -> AccessLease: ...

    async def search(self, request: GatewaySearchRequest) -> list[ContextDocument]: ...

    async def changes(
        self, connector_id: str, cursor: SyncCursor | None
    ) -> tuple[list[SourceChangeEvent], SyncCursor]: ...

    async def revoke(self, connector_id: str, principal_hash: str) -> None: ...


@runtime_checkable
class DocumentRouter(Protocol):
    """Confirmed local-file routing boundary."""

    async def execute(self, proposal: DocumentRoutingProposal) -> DocumentRoutingProposal: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def profile(self) -> EmbeddingProfile: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class OcrProvider(Protocol):
    """Optional local-only extraction seam for scanned documents and images."""

    def extract(self, path: Path, maximum_characters: int) -> str: ...


@runtime_checkable
class AssuranceRepository(Protocol):
    def save_rule_pack(self, rule_pack: AssuranceRulePack) -> None: ...

    def rule_packs(self) -> list[AssuranceRulePack]: ...

    def save_run(self, run: AssuranceRun) -> None: ...

    def run(self, run_id: str) -> AssuranceRun | None: ...

    def save_findings(self, findings: list[AssuranceFinding]) -> None: ...

    def findings(self) -> list[AssuranceFinding]: ...

    def review_finding(
        self, finding: AssuranceFinding, review: FindingReview
    ) -> None: ...

    def save_schedule(self, schedule: AssuranceSchedule) -> None: ...

    def schedules(self) -> list[AssuranceSchedule]: ...

    def evaluated_revision_ids(self) -> set[str]: ...

    def mark_revisions_evaluated(self, revision_ids: list[str]) -> None: ...

    def save_trusted_key(self, key: TrustedRuleSigningKey) -> None: ...

    def trusted_keys(self) -> list[TrustedRuleSigningKey]: ...


@runtime_checkable
class ConsistencyRepository(Protocol):
    """Encrypted review store kept separate from authoritative meeting state."""

    def save_consistency_run(self, run: ConsistencyRun) -> None: ...

    def consistency_run(self, run_id: str) -> ConsistencyRun | None: ...

    def consistency_run_for_source_fingerprint(
        self, source_fingerprint: str
    ) -> ConsistencyRun | None: ...

    def mark_consistency_source_evaluated(
        self, source_fingerprint: str, run_id: str
    ) -> None: ...

    def save_consistency_claims(self, claims: list[DocumentClaim]) -> None: ...

    def consistency_claims(self) -> list[DocumentClaim]: ...

    def review_consistency_claim(self, claim: DocumentClaim) -> None: ...

    def save_consistency_findings(self, findings: list[ConsistencyFinding]) -> None: ...

    def consistency_finding(self, finding_id: str) -> ConsistencyFinding | None: ...

    def consistency_findings(self) -> list[ConsistencyFinding]: ...

    def review_consistency_finding(
        self, finding: ConsistencyFinding, event: ConsistencyReviewEvent
    ) -> None: ...

    def save_authority_policy(self, policy: AuthorityPolicy) -> None: ...

    def authority_policies(self) -> list[AuthorityPolicy]: ...

    def save_approved_exception(self, exception: ApprovedException) -> None: ...

    def approved_exceptions(self) -> list[ApprovedException]: ...


@runtime_checkable
class ConsistencyClaimSuggester(Protocol):
    """Optional local model seam; returned claims remain review-only proposals."""

    def suggest(
        self,
        artifacts: list[DocumentArtifact],
        sections: list[ArtifactSection],
        connector_labels: dict[str, str],
        language: str,
    ) -> list[DocumentClaim]: ...


@runtime_checkable
class SolutionThreadRepository(Protocol):
    def save_node(self, node: SolutionThreadNode) -> None: ...

    def save_edge(self, edge: SolutionThreadEdge) -> None: ...

    def review_edge(
        self, edge: SolutionThreadEdge, expected_revision: int
    ) -> None: ...

    def nodes(self) -> list[SolutionThreadNode]: ...

    def edges(self) -> list[SolutionThreadEdge]: ...
