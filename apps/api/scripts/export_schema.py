"""Export (or verify) the domain contracts as one JSON Schema document.

``contracts/meeting_state.schema.json`` at the repository root is the shared
contract of record between api and web. The web mirrors these types in
``apps/web/src/types/contracts.ts`` and a Vitest test asserts its field sets match
this schema; an api test asserts this file matches the live models.

    python scripts/export_schema.py           # write the schema
    python scripts/export_schema.py --check    # exit 1 if the file is out of date
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from pydantic.json_schema import models_json_schema

# Direct script execution places ``scripts`` rather than ``apps/api`` on sys.path.
_API_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

from app.domain.assurance import (  # noqa: E402
    AssuranceFinding,
    AssuranceRule,
    AssuranceRulePack,
    AssuranceRun,
    AssuranceRunRequest,
    AssuranceSchedule,
    ERPMetadataCheckRequest,
    ERPMetadataSnapshot,
    FindingReview,
    FindingReviewRequest,
    MetadataValidationResult,
    TrustedRuleSigningKey,
)
from app.domain.consistency import (  # noqa: E402
    ApprovedException,
    AuthorityPolicy,
    ConsistencyClaimReviewRequest,
    ConsistencyComparison,
    ConsistencyDashboard,
    ConsistencyExport,
    ConsistencyExportRequest,
    ConsistencyFinding,
    ConsistencyFindingFilter,
    ConsistencyFindingReviewRequest,
    ConsistencyReviewEvent,
    ConsistencyRun,
    ConsistencyRunRequest,
    DocumentClaim,
    ObservedSystemFact,
)
from app.domain.context import (  # noqa: E402
    ConnectedQuestionBrief,
    ConnectedQuestionContextRequest,
    ConnectorDefinition,
    ConnectorHealth,
    ContextDiagnostics,
    ContextDocument,
    ContextSearchQuery,
    ContextSearchResult,
    EntityMapping,
    IssueExportOptions,
    IssueFact,
    IssueHistoryWarning,
    IssueQuestion,
    PublicContextCitation,
    StructuredIssueDraft,
    StructuredIssueDraftBatch,
)
from app.domain.contracts import (  # noqa: E402
    InformationGap,
    MeetingFact,
    MeetingState,
    PainPoint,
    QuestionSuggestion,
    Speaker,
    TranscriptEvent,
)
from app.domain.enterprise import (  # noqa: E402
    AccessLease,
    ConnectorCapabilityManifest,
    ConnectorCatalogEntry,
    EnterpriseSearchCitation,
    EnterpriseSearchRequest,
    EnterpriseSearchResult,
    RepositoryArtifact,
    SourceChangeEvent,
    SourceSelection,
    SyncCursor,
    WorkItemArtifact,
)
from app.domain.erp_analytics import (  # noqa: E402
    ERPAnalyticsRequest,
    ERPAnalyticsRun,
    ERPAnalyticsWarning,
    ERPMetricDiscrepancy,
    ERPTrendPoint,
    ERPTrendSeries,
    ERPTrendSummary,
)
from app.domain.evidence import (  # noqa: E402
    ArtifactSection,
    DocumentArtifact,
    DocumentRevision,
    EmbeddingProfile,
    EvidenceBundle,
    EvidenceCitation,
    RetrievalRequest,
)
from app.domain.fulfillment_reconciliation import (  # noqa: E402
    FulfillmentReconciliationFinding,
    FulfillmentReconciliationRequest,
    FulfillmentReconciliationRun,
    FulfillmentReconciliationWarning,
)
from app.domain.governance import (  # noqa: E402
    GovernanceAlert,
    GovernanceCandidate,
    GovernanceEvent,
    GovernanceExportOptions,
    GovernanceRecord,
    ImpactAnalysis,
    SolutionBrief,
    SolutionPortfolio,
    SystemEntity,
    SystemRelationship,
)
from app.domain.improvements import (  # noqa: E402
    DocumentClassification,
    DocumentClassificationReview,
    DocumentDispositionProposal,
    DocumentDispositionReview,
    DocumentInbox,
    DocumentOperationPlan,
    DocumentScanStatus,
    DuplicateDocumentGroup,
    ERPControlTemplate,
    ERPGovernanceDashboard,
    ERPGovernanceRun,
    ERPGovernanceRunRequest,
    ERPMetadataBaseline,
    ImprovementEvaluation,
    ImprovementPackManifest,
    ImprovementProfile,
    ImprovementProposal,
    ImprovementProposalReview,
    ImprovementSettings,
    ImprovementShadowRun,
    NetSuiteMappingValidation,
)
from app.domain.integrations import (  # noqa: E402
    DataQualityFinding,
    DataQualityFindingReview,
    DataQualityRule,
    DataQualityRulePack,
    DataQualityRun,
    DataQualityRunRequest,
    DocumentRoutingDestination,
    DocumentRoutingProposal,
    DocumentRoutingReview,
    ExternalActionCreateRequest,
    ExternalActionDraft,
    ExternalActionEvent,
    NotificationPolicy,
    PartnerClassification,
)
from app.domain.solution_thread import (  # noqa: E402
    SolutionLeadDashboard,
    SolutionMindMap,
    SolutionMindMapEdge,
    SolutionMindMapNode,
    SolutionMindMapRequest,
    SolutionThreadEdge,
    SolutionThreadEdgeProposal,
    SolutionThreadEdgeReviewRequest,
    SolutionThreadImpact,
    SolutionThreadImpactRequest,
    SolutionThreadNode,
    TraceabilityMatrix,
)

_MODELS = [
    Speaker,
    TranscriptEvent,
    MeetingFact,
    PainPoint,
    InformationGap,
    QuestionSuggestion,
    MeetingState,
]
_CONTEXT_MODELS = [
    ContextDocument,
    ContextSearchQuery,
    PublicContextCitation,
    ContextSearchResult,
    ConnectedQuestionContextRequest,
    ConnectedQuestionBrief,
    ContextDiagnostics,
    ConnectorHealth,
    ConnectorDefinition,
    EntityMapping,
    IssueExportOptions,
    IssueFact,
    IssueHistoryWarning,
    IssueQuestion,
    StructuredIssueDraft,
    StructuredIssueDraftBatch,
    SourceSelection,
    AccessLease,
    SyncCursor,
    SourceChangeEvent,
    ConnectorCapabilityManifest,
    ConnectorCatalogEntry,
    RepositoryArtifact,
    WorkItemArtifact,
    EnterpriseSearchRequest,
    EnterpriseSearchCitation,
    EnterpriseSearchResult,
    ERPAnalyticsRequest,
    ERPTrendPoint,
    ERPTrendSummary,
    ERPTrendSeries,
    ERPMetricDiscrepancy,
    ERPAnalyticsWarning,
    ERPAnalyticsRun,
    FulfillmentReconciliationRequest,
    FulfillmentReconciliationFinding,
    FulfillmentReconciliationWarning,
    FulfillmentReconciliationRun,
]
_GOVERNANCE_MODELS = [
    GovernanceCandidate,
    GovernanceRecord,
    GovernanceEvent,
    GovernanceAlert,
    SystemEntity,
    SystemRelationship,
    SolutionBrief,
    SolutionPortfolio,
    ImpactAnalysis,
    GovernanceExportOptions,
]
_EVIDENCE_MODELS = [
    DocumentArtifact,
    DocumentRevision,
    ArtifactSection,
    RetrievalRequest,
    EvidenceCitation,
    EvidenceBundle,
    EmbeddingProfile,
]
_ASSURANCE_MODELS = [
    AssuranceRule,
    AssuranceRulePack,
    TrustedRuleSigningKey,
    AssuranceRun,
    AssuranceRunRequest,
    AssuranceFinding,
    FindingReview,
    FindingReviewRequest,
    AssuranceSchedule,
    ERPMetadataSnapshot,
    ERPMetadataCheckRequest,
    MetadataValidationResult,
]
_SOLUTION_THREAD_MODELS = [
    SolutionThreadNode,
    SolutionThreadEdge,
    SolutionMindMapNode,
    SolutionMindMapEdge,
    SolutionMindMapRequest,
    SolutionMindMap,
    SolutionThreadEdgeProposal,
    SolutionThreadEdgeReviewRequest,
    TraceabilityMatrix,
    SolutionThreadImpact,
    SolutionThreadImpactRequest,
    SolutionLeadDashboard,
]
_INTEGRATION_MODELS = [
    DataQualityRule,
    DataQualityRulePack,
    DataQualityRunRequest,
    DataQualityRun,
    DataQualityFinding,
    DataQualityFindingReview,
    PartnerClassification,
    DocumentRoutingDestination,
    DocumentRoutingProposal,
    DocumentRoutingReview,
    ExternalActionCreateRequest,
    ExternalActionDraft,
    ExternalActionEvent,
    NotificationPolicy,
]
_IMPROVEMENT_MODELS = [
    ImprovementProposal,
    ImprovementEvaluation,
    ImprovementProfile,
    ImprovementProposalReview,
    ImprovementSettings,
    ImprovementPackManifest,
    DocumentClassification,
    DocumentClassificationReview,
    DuplicateDocumentGroup,
    DocumentDispositionProposal,
    DocumentDispositionReview,
    DocumentInbox,
    DocumentScanStatus,
    DocumentOperationPlan,
    ERPControlTemplate,
    ERPGovernanceRunRequest,
    ERPGovernanceRun,
    ERPGovernanceDashboard,
    NetSuiteMappingValidation,
    ERPMetadataBaseline,
    ImprovementShadowRun,
]
_CONSISTENCY_MODELS = [
    DocumentClaim,
    ObservedSystemFact,
    ConsistencyComparison,
    ConsistencyFinding,
    AuthorityPolicy,
    ApprovedException,
    ConsistencyRun,
    ConsistencyRunRequest,
    ConsistencyFindingFilter,
    ConsistencyFindingReviewRequest,
    ConsistencyClaimReviewRequest,
    ConsistencyReviewEvent,
    ConsistencyDashboard,
    ConsistencyExportRequest,
    ConsistencyExport,
]

# scripts/export_schema.py -> scripts -> api -> apps -> <repo root>
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCHEMA_PATH = _REPO_ROOT / "contracts" / "meeting_state.schema.json"
CONTEXT_SCHEMA_PATH = _REPO_ROOT / "contracts" / "context.schema.json"
GOVERNANCE_SCHEMA_PATH = _REPO_ROOT / "contracts" / "governance.schema.json"
EVIDENCE_SCHEMA_PATH = _REPO_ROOT / "contracts" / "evidence.schema.json"
ASSURANCE_SCHEMA_PATH = _REPO_ROOT / "contracts" / "assurance.schema.json"
SOLUTION_THREAD_SCHEMA_PATH = _REPO_ROOT / "contracts" / "solution_thread.schema.json"
INTEGRATION_SCHEMA_PATH = _REPO_ROOT / "contracts" / "integrations.schema.json"
IMPROVEMENT_SCHEMA_PATH = _REPO_ROOT / "contracts" / "improvements.schema.json"
CONSISTENCY_SCHEMA_PATH = _REPO_ROOT / "contracts" / "consistency.schema.json"


def build_schema() -> dict:
    """Bundle every top-level contract into one schema with shared ``$defs``."""

    _, top = models_json_schema(
        [(model, "validation") for model in _MODELS],
        ref_template="#/$defs/{model}",
        title="MeetingIntelligenceCopilotContracts",
    )
    return top


def build_context_schema() -> dict:
    """Bundle non-authoritative connector contracts separately from MeetingState."""

    _, top = models_json_schema(
        [(model, "validation") for model in _CONTEXT_MODELS],
        ref_template="#/$defs/{model}",
        title="MeetingIntelligenceContextContracts",
    )
    return top


def build_governance_schema() -> dict:
    """Bundle user-confirmed governance separately from meeting truth."""

    _, top = models_json_schema(
        [(model, "validation") for model in _GOVERNANCE_MODELS],
        ref_template="#/$defs/{model}",
        title="MeetingIntelligenceGovernanceContracts",
    )
    return top


def _build_contract_schema(models: list[type], title: str) -> dict:
    _, top = models_json_schema(
        [(model, "validation") for model in models],
        ref_template="#/$defs/{model}",
        title=title,
    )
    return top


def _serialize(schema: dict) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args(argv)

    rendered = _serialize(build_schema())
    rendered_context = _serialize(build_context_schema())
    rendered_governance = _serialize(build_governance_schema())
    rendered_evidence = _serialize(
        _build_contract_schema(_EVIDENCE_MODELS, "MeetingIntelligenceEvidenceContracts")
    )
    rendered_assurance = _serialize(
        _build_contract_schema(_ASSURANCE_MODELS, "MeetingIntelligenceAssuranceContracts")
    )
    rendered_solution_thread = _serialize(
        _build_contract_schema(
            _SOLUTION_THREAD_MODELS, "MeetingIntelligenceSolutionThreadContracts"
        )
    )
    rendered_integrations = _serialize(
        _build_contract_schema(
            _INTEGRATION_MODELS, "MeetingIntelligenceIntegrationContracts"
        )
    )
    rendered_improvements = _serialize(
        _build_contract_schema(
            _IMPROVEMENT_MODELS, "MeetingIntelligenceImprovementContracts"
        )
    )
    rendered_consistency = _serialize(
        _build_contract_schema(
            _CONSISTENCY_MODELS, "MeetingIntelligenceConsistencyContracts"
        )
    )
    if args.check:
        current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
        current_context = (
            CONTEXT_SCHEMA_PATH.read_text(encoding="utf-8") if CONTEXT_SCHEMA_PATH.exists() else ""
        )
        current_governance = (
            GOVERNANCE_SCHEMA_PATH.read_text(encoding="utf-8")
            if GOVERNANCE_SCHEMA_PATH.exists()
            else ""
        )
        current_evidence = (
            EVIDENCE_SCHEMA_PATH.read_text(encoding="utf-8")
            if EVIDENCE_SCHEMA_PATH.exists()
            else ""
        )
        current_assurance = (
            ASSURANCE_SCHEMA_PATH.read_text(encoding="utf-8")
            if ASSURANCE_SCHEMA_PATH.exists()
            else ""
        )
        current_solution_thread = (
            SOLUTION_THREAD_SCHEMA_PATH.read_text(encoding="utf-8")
            if SOLUTION_THREAD_SCHEMA_PATH.exists()
            else ""
        )
        current_integrations = (
            INTEGRATION_SCHEMA_PATH.read_text(encoding="utf-8")
            if INTEGRATION_SCHEMA_PATH.exists()
            else ""
        )
        current_improvements = (
            IMPROVEMENT_SCHEMA_PATH.read_text(encoding="utf-8")
            if IMPROVEMENT_SCHEMA_PATH.exists()
            else ""
        )
        current_consistency = (
            CONSISTENCY_SCHEMA_PATH.read_text(encoding="utf-8")
            if CONSISTENCY_SCHEMA_PATH.exists()
            else ""
        )
        if (
            current != rendered
            or current_context != rendered_context
            or current_governance != rendered_governance
            or current_evidence != rendered_evidence
            or current_assurance != rendered_assurance
            or current_solution_thread != rendered_solution_thread
            or current_integrations != rendered_integrations
            or current_improvements != rendered_improvements
            or current_consistency != rendered_consistency
        ):
            print(
                "contract schema drift: contracts/*.schema.json is out of date — "
                "run `python scripts/export_schema.py`",
                file=sys.stderr,
            )
            return 1
        print("contract schema up to date")
        return 0

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(rendered, encoding="utf-8")
    CONTEXT_SCHEMA_PATH.write_text(rendered_context, encoding="utf-8")
    GOVERNANCE_SCHEMA_PATH.write_text(rendered_governance, encoding="utf-8")
    EVIDENCE_SCHEMA_PATH.write_text(rendered_evidence, encoding="utf-8")
    ASSURANCE_SCHEMA_PATH.write_text(rendered_assurance, encoding="utf-8")
    SOLUTION_THREAD_SCHEMA_PATH.write_text(rendered_solution_thread, encoding="utf-8")
    INTEGRATION_SCHEMA_PATH.write_text(rendered_integrations, encoding="utf-8")
    IMPROVEMENT_SCHEMA_PATH.write_text(rendered_improvements, encoding="utf-8")
    CONSISTENCY_SCHEMA_PATH.write_text(rendered_consistency, encoding="utf-8")
    print(
        "wrote meeting, context, governance, evidence, assurance, solution-thread, "
        "integration, improvement, and consistency schemas"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
