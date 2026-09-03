"""FastAPI application — transport edge.

A WebSocket endpoint that streams versioned snapshots (no delta patches) for
either of two sources, both server-selected from the URL's opaque session_id
alone (the client never supplies a filesystem path or otherwise influences
source selection):

- a known demo/fixture id (Slice 0-2 registry) -> replay mode
- any other session_id -> a compact live projection without transcript, fed by
  the production ingestion route below as Meetily emits TranscriptUpdate events.
  The backend retains the full deterministic MeetingState and remains the single
  source of truth in both modes.

The first message is always the empty v0 snapshot (preserving the Slice 0
contract), then one snapshot per accepted event.

This module is the transport edge (``app/api``). It may import FastAPI, the service
layer, and adapters; the pure ``app/domain`` and ``app/services`` layers may not
import a web/vendor SDK (enforced by tests/test_slice0.py).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel, Field, field_validator, model_validator

from app.adapters.analysis.ollama_consistency import consistency_claim_suggester_from_env
from app.adapters.analysis.ollama_semantic import (
    FEATURE_FLAG as SEMANTIC_FEATURE_FLAG,
)
from app.adapters.analysis.ollama_semantic import (
    semantic_candidate_analyzer_from_env,
)
from app.adapters.analysis.semantic_runtime import SemanticHintRuntime
from app.adapters.assurance.consistency_workspace import ConsistencyWorkspace
from app.adapters.assurance.rule_packs import signing_key, verify_rule_pack
from app.adapters.assurance.store import EncryptedAssuranceRepository
from app.adapters.assurance.workspace import AssuranceWorkspace
from app.adapters.context.service import ContextConnectorService
from app.adapters.context.windows_security import WindowsDpapiProtector
from app.adapters.governance.store import EncryptedGovernanceRepository
from app.adapters.governance.workspace import GovernanceWorkspace
from app.adapters.improvements.store import EncryptedImprovementRepository
from app.adapters.improvements.workspace import ImprovementWorkspace
from app.adapters.integrations.rule_packs import verify_data_quality_rule_pack
from app.adapters.integrations.store import EncryptedIntegrationRepository
from app.adapters.integrations.workspace import IntegrationWorkspace
from app.adapters.transcript.live_adapters import LiveAdapterName
from app.adapters.transcript.live_registry import LiveMeetingRegistry, live_registry
from app.adapters.transcript.replay import ReplayTranscriptSource
from app.api.assurance_contracts import TrustedRuleKeyRequest
from app.api.context_contracts import (
    ConnectorSyncResponse,
    ConnectorUpsertRequest,
    ContextCitationFeedbackRequest,
    FinalIssueDraftBatchRequest,
    MicrosoftDeviceCodeResponse,
    MicrosoftOAuthPollResponse,
    ProblemIdentityDecisionRequest,
    StructuredIssueDraftBatchRequest,
    StructuredIssueDraftRequest,
)
from app.api.demo_registry import resolve_demo
from app.api.enterprise_contracts import (
    EnterpriseAuthorizationCallback,
    EnterpriseAuthorizationStart,
    EnterpriseSourceEnrollmentRequest,
    ReviewedExternalActionExport,
    ReviewedExternalActionExportRequest,
)
from app.api.governance_contracts import (
    GovernanceCandidateReviewRequest,
    GovernanceRecordEventRequest,
    ImpactAnalysisRequest,
    SolutionBriefRequest,
)
from app.api.improvement_contracts import (
    AskNowFeedbackRequest,
    DocumentClassificationBatchRequest,
    DocumentClassificationBatchResponse,
    DocumentDispositionExecuteRequest,
    DocumentScanRequest,
    ImprovementDataDeleteResponse,
    ImprovementPackExportResponse,
    ImprovementPackImportRequest,
    ImprovementSettingsUpdate,
    NetSuiteMappingValidationRequest,
)
from app.api.integration_contracts import (
    CertificateCredentialRequest,
    ExternalSpaceEnrollmentRequest,
    NotificationPolicyUpsertRequest,
    RoutingDestinationUpsertRequest,
    SlackAuthorizationCallbackRequest,
    SlackAuthorizationStartRequest,
    SlackAuthorizationStartResponse,
)
from app.api.live_contracts import (
    LiveBatchAcknowledgement,
    LiveIngestAcknowledgement,
    LiveIngestStatus,
    LiveIntelligenceSnapshot,
)
from app.domain.assurance import (
    AssuranceFinding,
    AssuranceRulePack,
    AssuranceRun,
    AssuranceRunRequest,
    AssuranceSchedule,
    AssuranceSeverity,
    ERPMetadataCheckRequest,
    FindingReviewRequest,
    MetadataValidationResult,
    RulePackInstallRequest,
    TrustedRuleSigningKey,
)
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
    ConsistencyMismatchKind,
    ConsistencyRun,
    ConsistencyRunRequest,
    DocumentClaim,
)
from app.domain.context import (
    ConnectedProblemBrief,
    ConnectedProblemContextRequest,
    ConnectedQuestionBrief,
    ConnectedQuestionContextRequest,
    ConnectorHealth,
    ConnectorKind,
    ContextDiagnostics,
    ContextSearchQuery,
    ContextSearchResult,
    EntityGlossary,
    ExternalSpaceSelection,
    StructuredIssueDraft,
    StructuredIssueDraftBatch,
)
from app.domain.enterprise import (
    AccessLease,
    ConnectorCatalogEntry,
    EnterpriseSearchRequest,
    EnterpriseSearchResult,
    SourceSelection,
)
from app.domain.erp_analytics import ERPAnalyticsRequest, ERPAnalyticsRun
from app.domain.evidence import EvidenceBundle, RetrievalRequest
from app.domain.fulfillment_reconciliation import (
    FulfillmentReconciliationRequest,
    FulfillmentReconciliationRun,
)
from app.domain.governance import (
    GovernanceCandidate,
    GovernanceKind,
    GovernanceRecord,
    GovernanceRecordStatus,
    ImpactAnalysis,
    SolutionBrief,
    SolutionPortfolio,
)
from app.domain.improvements import (
    DocumentClassification,
    DocumentClassificationReview,
    DocumentDispositionReview,
    DocumentInbox,
    DocumentOperationPlan,
    DocumentScanStatus,
    ERPControlTemplate,
    ERPGovernanceDashboard,
    ERPGovernanceRun,
    ERPGovernanceRunRequest,
    ImprovementEvaluation,
    ImprovementPackManifest,
    ImprovementProfile,
    ImprovementProposal,
    ImprovementProposalReview,
    ImprovementSettings,
    ImprovementShadowRun,
    ImprovementSignalKind,
    NetSuiteMappingValidation,
)
from app.domain.improvements import (
    DocumentDispositionProposal as ImprovementDocumentDispositionProposal,
)
from app.domain.integrations import (
    DataQualityFinding,
    DataQualityFindingReview,
    DataQualityRulePack,
    DataQualityRun,
    DataQualityRunRequest,
    DocumentRoutingDestination,
    DocumentRoutingProposal,
    DocumentRoutingReview,
    ExternalActionCreateRequest,
    ExternalActionDecision,
    ExternalActionDraft,
    NotificationPolicy,
)
from app.domain.semantic_candidates import SemanticHintDecision
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
from app.services.external_action_exports import render_external_action_export
from app.services.issue_drafts import build_issue_draft
from app.services.meeting_engine import MeetingEngine
from app.services.state_service import initial_snapshot

DEFAULT_REPLAY_SPEED = 0.0  # 0.0 = stream instantly (keeps WS tests quick).
BACKEND_API_VERSION = 13
BACKEND_PRODUCT = "meeting-intelligence-copilot"
BACKEND_VERSION = "0.6.1"
CAPABILITY_TOKEN_ENV = "MEETING_INTELLIGENCE_TOKEN"
CAPABILITY_TOKEN_HEADER = "X-Meeting-Intelligence-Token"
WEBSOCKET_PROTOCOL = "meeting-intelligence-v1"
WEBSOCKET_TOKEN_PREFIX = "token."
MAX_INGEST_PAYLOAD_BYTES = 64 * 1024
MAX_BATCH_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_BATCH_EVENTS = 200
MAX_TRANSCRIPT_TEXT_CHARS = 20_000
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_CAPABILITY_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{32,256}$")
_USE_ENV_TOKEN = object()

# The Meetily webview serves its dev/prod UI from an origin distinct from this
# loopback-only backend (e.g. http://localhost:3118 vs 127.0.0.1:8000, and
# Tauri's own tauri://localhost / http://tauri.localhost in production), so a
# plain fetch()/WebSocket from that UI is cross-origin and needs CORS headers
# to not be silently rejected by the browser. This service is loopback-only by
# design (docs/MEETILY_INTEGRATION_RESEARCH.md), and the explicit allow-list
# prevents an arbitrary browser origin from reading local meeting state.
_CORS_ALLOWED_ORIGINS = [
    "http://localhost:3118",
    "http://127.0.0.1:3118",
    "tauri://localhost",
    "http://tauri.localhost",
]


def is_allowed_websocket_origin(origin: str | None) -> bool:
    """Allow the documented local Meetily webview origins for WS upgrades.

    Browsers send an Origin header for WebSocket upgrades, but native/local
    clients and the test client may omit it. CORS middleware does not protect
    WebSocket upgrades, so the route checks this allow-list separately.
    """

    return origin is None or origin in _CORS_ALLOWED_ORIGINS


def is_valid_session_id(session_id: str) -> bool:
    return _SESSION_ID_PATTERN.fullmatch(session_id) is not None


def is_valid_capability_token(token: str) -> bool:
    return _CAPABILITY_TOKEN_PATTERN.fullmatch(token) is not None


class CapabilityAuth:
    """Fail-closed local transport capability authentication."""

    def __init__(self, token: str | None) -> None:
        if token is not None and not is_valid_capability_token(token):
            raise ValueError(f"{CAPABILITY_TOKEN_ENV} must be 32-256 URL-safe ASCII characters")
        self._token = token

    @property
    def enabled(self) -> bool:
        return self._token is not None

    def accepts(self, provided: str | None) -> bool:
        if self._token is None:
            return True
        if provided is None or not is_valid_capability_token(provided):
            return False
        return secrets.compare_digest(self._token, provided)


def websocket_subprotocols(websocket: WebSocket) -> list[str]:
    """Normalize ASGI-server differences in offered protocol parsing.

    Uvicorn's WebSocket implementations do not all expose the scope identically:
    some split a comma-delimited header while others leave it as one list item.
    Browser and .NET clients legitimately send both forms.
    """

    return [
        protocol.strip()
        for offered in websocket.scope.get("subprotocols", [])
        for protocol in offered.split(",")
        if protocol.strip()
    ]


def websocket_capability_token(websocket: WebSocket) -> str | None:
    for protocol in websocket_subprotocols(websocket):
        if protocol.startswith(WEBSOCKET_TOKEN_PREFIX):
            return protocol.removeprefix(WEBSOCKET_TOKEN_PREFIX)
    return None


class MeetilyIngestRequest(BaseModel):
    """Wire shape for a real Meetily TranscriptUpdate push.

    ``lang`` is the meeting's immutable configured language (the same value
    passed to Meetily's set_language_preference) -- never inferred from text.
    ``payload`` is Meetily's own TranscriptUpdate JSON, unmodified.
    """

    lang: Literal["ja", "en", "ko"]
    payload: dict[str, Any]

    @field_validator("payload")
    @classmethod
    def validate_payload_size_and_text(cls, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > MAX_INGEST_PAYLOAD_BYTES:
            raise ValueError("transcript payload exceeds the local ingest limit")
        text = payload.get("text")
        if text is not None and (
            not isinstance(text, str) or len(text) > MAX_TRANSCRIPT_TEXT_CHARS
        ):
            raise ValueError("transcript text is invalid or exceeds the local ingest limit")
        return payload


class LiveIngestRequest(MeetilyIngestRequest):
    adapter: LiveAdapterName


class LiveBatchIngestRequest(BaseModel):
    adapter: LiveAdapterName
    lang: Literal["ja", "en", "ko"]
    payloads: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_BATCH_EVENTS)

    @model_validator(mode="after")
    def validate_batch(self) -> LiveBatchIngestRequest:
        encoded = json.dumps(self.payloads, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > MAX_BATCH_PAYLOAD_BYTES:
            raise ValueError("transcript batch exceeds the local ingest limit")
        sequence_ids: list[int] = []
        for payload in self.payloads:
            MeetilyIngestRequest.validate_payload_size_and_text(payload)
            sequence_id = payload.get("sequence_id")
            if isinstance(sequence_id, bool) or not isinstance(sequence_id, int):
                raise ValueError("every batch payload requires an integer sequence_id")
            sequence_ids.append(sequence_id)
        if sequence_ids != sorted(set(sequence_ids)):
            raise ValueError("batch sequence_ids must be unique and ordered")
        return self


def create_app(
    replay_speed: float = DEFAULT_REPLAY_SPEED,
    capability_token: str | None | object = _USE_ENV_TOKEN,
    live_registry_instance: LiveMeetingRegistry | None = None,
    context_service_instance: ContextConnectorService | None = None,
    semantic_runtime_instance: SemanticHintRuntime | None = None,
    governance_workspace_instance: GovernanceWorkspace | None = None,
    assurance_workspace_instance: AssuranceWorkspace | None = None,
    integration_workspace_instance: IntegrationWorkspace | None = None,
    improvement_workspace_instance: ImprovementWorkspace | None = None,
    consistency_workspace_instance: ConsistencyWorkspace | None = None,
) -> FastAPI:
    configured_token = (
        os.environ.get(CAPABILITY_TOKEN_ENV)
        if capability_token is _USE_ENV_TOKEN
        else capability_token
    )
    if configured_token is not None and not isinstance(configured_token, str):
        raise TypeError("capability_token must be a string or None")
    auth = CapabilityAuth(configured_token)
    registry = live_registry_instance or LiveMeetingRegistry()
    context_service = context_service_instance
    semantic_runtime = semantic_runtime_instance
    governance_workspace = governance_workspace_instance
    assurance_workspace = assurance_workspace_instance
    integration_workspace = integration_workspace_instance
    improvement_workspace = improvement_workspace_instance
    consistency_workspace = consistency_workspace_instance
    set_glossary_provider = getattr(registry, "set_glossary_provider", None)
    glossary_provider = getattr(context_service, "entity_glossary_with_mappings", None)
    if set_glossary_provider is not None:
        set_glossary_provider(glossary_provider)
    if governance_workspace is not None and glossary_provider is not None:
        governance_workspace.set_glossary_provider(glossary_provider)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            if context_service is not None:
                await context_service.close()
            if semantic_runtime is not None:
                await semantic_runtime.close()
            if governance_workspace is not None:
                governance_workspace.close()
            close = getattr(registry, "close", None)
            if close is not None:
                close()

    app = FastAPI(
        title="Meeting Intelligence Copilot",
        version=BACKEND_VERSION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ALLOWED_ORIGINS,
        allow_methods=["DELETE", "GET", "POST", "PUT"],
        allow_headers=["Content-Type", CAPABILITY_TOKEN_HEADER],
    )

    def require_capability_token(
        provided: str | None = Header(default=None, alias=CAPABILITY_TOKEN_HEADER),
    ) -> None:
        if not auth.accepts(provided):
            raise HTTPException(status_code=401, detail="local capability token required")

    def require_context_service() -> ContextConnectorService:
        if context_service is None:
            raise HTTPException(status_code=503, detail="context connectors are unavailable")
        return context_service

    def require_assurance_workspace() -> AssuranceWorkspace:
        if assurance_workspace is None:
            raise HTTPException(status_code=503, detail="assurance workspace is unavailable")
        return assurance_workspace

    def require_integration_workspace() -> IntegrationWorkspace:
        if integration_workspace is None:
            raise HTTPException(status_code=503, detail="integration workspace is unavailable")
        return integration_workspace

    def require_improvement_workspace() -> ImprovementWorkspace:
        if improvement_workspace is None:
            raise HTTPException(status_code=503, detail="improvement workspace is unavailable")
        return improvement_workspace

    def require_consistency_workspace() -> ConsistencyWorkspace:
        if consistency_workspace is None:
            raise HTTPException(status_code=503, detail="consistency workspace is unavailable")
        return consistency_workspace

    async def _enrich_governance_context(
        value: SolutionBrief | ImpactAnalysis,
        query_text: str,
        connector_ids: list[str],
        limit: int,
    ) -> SolutionBrief | ImpactAnalysis:
        """Attach supplemental citations without changing governance or meeting truth."""

        if context_service is None:
            return value.model_copy(update={"context_status": "unavailable"})
        query_text = " ".join(query_text.split())[:2_000]
        if not query_text:
            return value.model_copy(update={"context_status": "current"})
        try:
            result = await asyncio.wait_for(
                context_service.search(
                    ContextSearchQuery(
                        text=query_text,
                        principal_id="local-current-user",
                        connector_ids=connector_ids,
                        limit=limit,
                    )
                ),
                timeout=2.0,
            )
        except TimeoutError:
            return value.model_copy(update={"context_status": "timeout"})
        status = (
            "unavailable"
            if result.unavailable_connector_ids and not result.citations
            else "current"
        )
        return value.model_copy(
            update={"context_status": status, "context_citations": result.citations}
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/compatibility", dependencies=[Depends(require_capability_token)])
    def compatibility_health() -> dict[str, str | int | bool]:
        """Versioned lifecycle handshake; the basic health contract stays minimal."""

        return {
            "status": "ok",
            "product": BACKEND_PRODUCT,
            "api_version": BACKEND_API_VERSION,
            "backend_version": app.version,
            "capability_auth": auth.enabled,
        }

    async def ingest_live_transcript_update(
        session_id: str,
        adapter: LiveAdapterName,
        lang: Literal["ja", "en", "ko"],
        payload: dict[str, Any],
    ) -> LiveIngestAcknowledgement:
        if not is_valid_session_id(session_id):
            raise HTTPException(status_code=422, detail="invalid meeting session id")
        result = registry.ingest_with_ack(session_id, lang, payload, adapter=adapter)
        if governance_workspace is not None and result.state is not None:
            governance_workspace.observe_state(result.state)
        if semantic_runtime is not None and result.state is not None:
            semantic_runtime.seed_dismissed(
                session_id, registry.dismissed_semantic_hint_ids(session_id)
            )
            semantic_runtime.schedule(result.state, registry.publish_current)
        return LiveIngestAcknowledgement(
            status=result.status,
            received_sequence_id=result.received_sequence_id,
            next_expected_sequence_id=result.next_expected_sequence_id,
            state_version=result.state_version,
        )

    @app.post(
        "/ingest/live/{session_id}",
        dependencies=[Depends(require_capability_token)],
    )
    async def ingest_live_transcript(
        session_id: str, body: LiveIngestRequest
    ) -> LiveIngestAcknowledgement:
        """Source-neutral live ingest.

        ``adapter`` reserves stable names for future direct platform bridges
        while keeping Meetily's current local transcript path unchanged.
        """

        return await ingest_live_transcript_update(
            session_id, body.adapter, body.lang, body.payload
        )

    @app.post(
        "/ingest/live/{session_id}/batch",
        dependencies=[Depends(require_capability_token)],
    )
    async def ingest_live_transcript_batch(
        session_id: str, body: LiveBatchIngestRequest
    ) -> LiveBatchAcknowledgement:
        """Apply an ordered, bounded replay batch using the normal ingest path."""

        if not is_valid_session_id(session_id):
            raise HTTPException(status_code=422, detail="invalid meeting session id")
        rejected: list[int] = []
        final = None
        persistence_marker = registry.batch_persistence_marker(session_id)
        for payload in body.payloads:
            final = registry.ingest_batch_item_with_ack(
                session_id, body.lang, payload, adapter=body.adapter
            )
            if final.status is LiveIngestStatus.rejected:
                rejected.append(payload["sequence_id"])
            if governance_workspace is not None and final.state is not None:
                governance_workspace.observe_state(final.state)
            # Replay can fold 200 CPU-bound events. Yield between folds so the
            # local compatibility probe and WebSocket delivery are not starved.
            await asyncio.sleep(0)
        registry.persist_batch_from(session_id, persistence_marker)
        assert final is not None
        if semantic_runtime is not None:
            state = registry.current_state(session_id)
            if state is not None:
                semantic_runtime.seed_dismissed(
                    session_id, registry.dismissed_semantic_hint_ids(session_id)
                )
                semantic_runtime.schedule(state, registry.publish_current)
        return LiveBatchAcknowledgement(
            status=final.status,
            received_sequence_id=final.received_sequence_id,
            next_expected_sequence_id=final.next_expected_sequence_id,
            state_version=final.state_version,
            rejected_sequence_ids=rejected,
        )

    @app.post(
        "/ingest/meetily/{session_id}",
        dependencies=[Depends(require_capability_token)],
    )
    async def ingest_meetily_transcript_update(
        session_id: str, body: MeetilyIngestRequest
    ) -> LiveIngestAcknowledgement:
        """Real production ingestion point. Meetily pushes each TranscriptUpdate
        here as it is emitted by its real capture/STT pipeline. The path value is
        the opaque recording session id; the human-readable meeting title is not
        used as live identity. A schema-valid event that fails normalization or
        analysis is acknowledged as rejected rather than raising; request
        validation and capability authentication still fail closed with 4xx.
        Recording and transcription in Meetily must remain independent.

        MUST stay ``async def`` (not a plain ``def``): FastAPI/Starlette runs a
        plain ``def`` route in a worker threadpool, and ``live_registry`` holds
        shared, unsynchronized state that is only safe to mutate from the single
        asyncio event loop thread (independent review finding, confirmed real --
        concurrent plain-``def`` calls could race on registry mutation and on
        ``asyncio.Queue.put_nowait()``, which is not thread-safe)."""
        return await ingest_live_transcript_update(session_id, "meetily", body.lang, body.payload)

    @app.delete(
        "/meeting/{session_id}",
        dependencies=[Depends(require_capability_token)],
    )
    async def reset_live_meeting(session_id: str) -> dict[str, bool]:
        """End a session; ``reset`` reports whether cache deletion completed."""

        if not is_valid_session_id(session_id):
            raise HTTPException(status_code=422, detail="invalid meeting session id")
        if semantic_runtime is not None:
            semantic_runtime.reset(session_id)
        return {"reset": registry.reset(session_id)}

    @app.delete(
        "/meetings",
        dependencies=[Depends(require_capability_token)],
    )
    async def reset_all_live_meetings() -> dict[str, bool]:
        """Purge all transient live recovery state for local delete-all."""

        if semantic_runtime is not None:
            semantic_runtime.reset_all()
        return {"reset": registry.reset_all()}

    @app.get(
        "/governance/sessions/{session_id}/candidates",
        response_model=list[GovernanceCandidate],
        dependencies=[Depends(require_capability_token)],
    )
    async def governance_candidates(session_id: str) -> list[GovernanceCandidate]:
        if not is_valid_session_id(session_id):
            raise HTTPException(status_code=422, detail="invalid meeting session id")
        if governance_workspace is None:
            return []
        state = registry.current_state(session_id)
        if state is not None:
            governance_workspace.observe_state(state)
        return governance_workspace.candidates(session_id)

    @app.post(
        "/governance/candidates/{candidate_id}/review",
        response_model=GovernanceRecord | GovernanceCandidate,
        dependencies=[Depends(require_capability_token)],
        responses={404: {}, 409: {}, 422: {}},
    )
    async def review_governance_candidate(
        candidate_id: str, body: GovernanceCandidateReviewRequest
    ) -> GovernanceRecord | GovernanceCandidate | JSONResponse:
        if governance_workspace is None:
            return JSONResponse(status_code=404, content={"status": "candidate_not_found"})
        state = registry.current_state(body.session_id)
        latest_version = state.version if state is not None else body.state_version
        if state is not None and state.version != body.state_version:
            return JSONResponse(
                status_code=409, content={"status": "stale", "latest_state_version": latest_version}
            )
        try:
            candidate, record = governance_workspace.review_candidate(
                candidate_id, body.session_id, body.state_version, body.action, body.edits
            )
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "candidate_not_found"})
        except RuntimeError:
            return JSONResponse(
                status_code=409, content={"status": "stale", "latest_state_version": latest_version}
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="governance review is invalid") from error
        if improvement_workspace is not None:
            improvement_workspace.record_review(
                kind=ImprovementSignalKind.governance_review,
                target=f"{candidate_id}:{body.state_version}",
                action=body.action,
                outcome=1 if body.action == "confirm" else -1,
                attributes={"governance_kind": candidate.kind.value},
            )
        registry.publish_current(body.session_id)
        return record or candidate

    @app.get(
        "/governance/records",
        response_model=SolutionPortfolio,
        dependencies=[Depends(require_capability_token)],
    )
    async def governance_records(
        kind: GovernanceKind | None = None,
        status: GovernanceRecordStatus | None = None,
        entity_id: str | None = None,
    ) -> SolutionPortfolio:
        if governance_workspace is None:
            return SolutionPortfolio()
        records = governance_workspace.records(
            kind=kind.value if kind else None,
            status=status.value if status else None,
            entity_id=entity_id,
        )
        from app.services.governance import build_portfolio

        return build_portfolio(records)

    @app.post(
        "/governance/records/{record_id}/events",
        response_model=GovernanceRecord,
        dependencies=[Depends(require_capability_token)],
        responses={404: {}, 409: {}, 422: {}},
    )
    async def change_governance_record(
        record_id: str, body: GovernanceRecordEventRequest
    ) -> GovernanceRecord | JSONResponse:
        if governance_workspace is None:
            return JSONResponse(status_code=404, content={"status": "record_not_found"})
        try:
            return governance_workspace.change_record(
                record_id, body.expected_revision, body.action, body.changes
            )
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "record_not_found"})
        except RuntimeError:
            current = next(
                (value for value in governance_workspace.records() if value.record_id == record_id),
                None,
            )
            return JSONResponse(
                status_code=409,
                content={"status": "stale", "latest_revision": current.revision if current else 0},
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="governance event is invalid") from error

    @app.post(
        "/governance/briefs",
        response_model=SolutionBrief,
        dependencies=[Depends(require_capability_token)],
    )
    async def governance_brief(body: SolutionBriefRequest) -> SolutionBrief:
        if governance_workspace is None:
            return SolutionBrief(language=body.language)
        brief = governance_workspace.brief(body.language, body.entity_ids, body.limit)
        if body.connector_ids:
            brief = await _enrich_governance_context(
                brief,
                " ".join(record.title for record in brief.records[:10]),
                body.connector_ids,
                min(body.limit, 20),
            )
        return brief

    @app.post(
        "/governance/impact-analysis",
        response_model=ImpactAnalysis,
        dependencies=[Depends(require_capability_token)],
        responses={404: {}},
    )
    async def governance_impact(body: ImpactAnalysisRequest) -> ImpactAnalysis | JSONResponse:
        if governance_workspace is None:
            return JSONResponse(status_code=404, content={"status": "entity_not_found"})
        try:
            result = governance_workspace.impact_analysis(body.root_entity_id)
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "entity_not_found"})
        if body.connector_ids:
            result = await _enrich_governance_context(
                result,
                " ".join(entity.label for entity in result.entities),
                body.connector_ids,
                20,
            )
        return result

    @app.delete(
        "/governance/meetings/{session_id}",
        dependencies=[Depends(require_capability_token)],
    )
    async def delete_governance_meeting(session_id: str) -> dict[str, bool]:
        if not is_valid_session_id(session_id):
            raise HTTPException(status_code=422, detail="invalid meeting session id")
        if governance_workspace is not None:
            governance_workspace.delete_meeting(session_id)
        return {"deleted": True}

    @app.delete(
        "/governance",
        dependencies=[Depends(require_capability_token)],
    )
    async def delete_all_governance() -> dict[str, bool]:
        if governance_workspace is not None:
            governance_workspace.delete_all()
        return {"deleted": True}

    @app.get(
        "/configuration/entity-glossary",
        response_model=EntityGlossary,
        dependencies=[Depends(require_capability_token)],
    )
    async def get_entity_glossary(
        service: ContextConnectorService = Depends(require_context_service),
    ) -> EntityGlossary:
        return service.entity_glossary()

    @app.put(
        "/configuration/entity-glossary",
        response_model=EntityGlossary,
        responses={409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def put_entity_glossary(
        body: EntityGlossary,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> EntityGlossary | JSONResponse:
        try:
            return await service.update_entity_glossary(body)
        except RuntimeError:
            return JSONResponse(
                status_code=409,
                content={
                    "status": "stale",
                    "latest_revision": service.entity_glossary().revision,
                },
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="entity glossary is invalid") from error

    @app.post(
        "/context/connectors",
        response_model=ConnectorHealth,
        dependencies=[Depends(require_capability_token)],
    )
    async def upsert_context_connector(
        body: ConnectorUpsertRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> ConnectorHealth:
        credential = body.credential.get_secret_value() if body.credential else None
        try:
            return await service.upsert(body.definition, credential)
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail="connector configuration is invalid"
            ) from error
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="connector storage is unavailable"
            ) from error

    @app.get(
        "/context/connectors",
        response_model=list[ConnectorHealth],
        dependencies=[Depends(require_capability_token)],
    )
    async def context_connector_health(
        service: ContextConnectorService = Depends(require_context_service),
    ) -> list[ConnectorHealth]:
        return await service.health()

    @app.put(
        "/context/connectors/{connector_id}/certificate",
        response_model=ConnectorHealth,
        dependencies=[Depends(require_capability_token)],
    )
    async def save_connector_certificate(
        connector_id: str,
        body: CertificateCredentialRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> ConnectorHealth:
        try:
            definition = service.connector_definition(connector_id)
            credential = json.dumps(
                {"private_key_pem": body.private_key_pem.get_secret_value()}
            )
            return await service.upsert(definition, credential)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=422, detail="connector certificate is invalid"
            ) from error

    @app.get(
        "/context/connectors/{connector_id}/spaces",
        response_model=list[ExternalSpaceSelection],
        dependencies=[Depends(require_capability_token)],
    )
    async def selected_external_spaces(
        connector_id: str,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> list[ExternalSpaceSelection]:
        try:
            return service.external_spaces(connector_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error

    @app.put(
        "/context/connectors/{connector_id}/spaces",
        response_model=ExternalSpaceSelection,
        dependencies=[Depends(require_capability_token)],
    )
    async def save_external_space(
        connector_id: str,
        body: ExternalSpaceEnrollmentRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> ExternalSpaceSelection:
        try:
            return await service.save_external_space(
                connector_id,
                body.selection,
                body.external_id.get_secret_value(),
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="space selection is invalid") from error

    @app.delete(
        "/context/connectors/{connector_id}/spaces/{selection_id}",
        dependencies=[Depends(require_capability_token)],
    )
    async def delete_external_space(
        connector_id: str,
        selection_id: str,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> dict[str, bool]:
        try:
            return {
                "deleted": await service.delete_external_space(
                    connector_id, selection_id
                )
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="space selection is invalid") from error

    @app.post(
        "/context/connectors/{connector_id}/slack/authorize",
        response_model=SlackAuthorizationStartResponse,
        dependencies=[Depends(require_capability_token)],
    )
    async def start_slack_authorization(
        connector_id: str,
        body: SlackAuthorizationStartRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> SlackAuthorizationStartResponse:
        try:
            authorization = service.start_slack_authorization(
                connector_id, body.redirect_uri
            )
            return SlackAuthorizationStartResponse(
                authorization_url=authorization.authorization_url,
                state=authorization.state,
                expires_at=authorization.expires_at,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Slack setup is invalid") from error

    @app.post(
        "/context/connectors/{connector_id}/slack/callback",
        response_model=ConnectorHealth,
        dependencies=[Depends(require_capability_token)],
    )
    async def complete_slack_authorization(
        connector_id: str,
        body: SlackAuthorizationCallbackRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> ConnectorHealth:
        try:
            return await service.complete_slack_authorization(
                connector_id,
                state=body.state.get_secret_value(),
                code=body.code.get_secret_value(),
                redirect_uri=body.redirect_uri,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except PermissionError as error:
            raise HTTPException(status_code=401, detail="Slack authorization failed") from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Slack callback is invalid") from error

    @app.post(
        "/context/connectors/{connector_id}/microsoft-device-code",
        response_model=MicrosoftDeviceCodeResponse,
        dependencies=[Depends(require_capability_token)],
    )
    async def start_microsoft_device_code(
        connector_id: str,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> MicrosoftDeviceCodeResponse:
        try:
            authorization = await service.start_microsoft_authorization(connector_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=503, detail="Microsoft authorization is unavailable"
            ) from error
        return MicrosoftDeviceCodeResponse(
            verification_uri=authorization.verification_uri,
            user_code=authorization.user_code,
            expires_at=authorization.expires_at,
            interval_seconds=authorization.interval_seconds,
        )

    @app.post(
        "/context/connectors/{connector_id}/microsoft-device-code/poll",
        response_model=MicrosoftOAuthPollResponse,
        dependencies=[Depends(require_capability_token)],
    )
    async def poll_microsoft_device_code(
        connector_id: str,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> MicrosoftOAuthPollResponse:
        try:
            status = await service.poll_microsoft_authorization(connector_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=503, detail="Microsoft authorization is unavailable"
            ) from error
        return MicrosoftOAuthPollResponse(status=status)

    @app.post(
        "/context/connectors/{connector_id}/sync",
        response_model=ConnectorSyncResponse,
        dependencies=[Depends(require_capability_token)],
    )
    async def sync_context_connector(
        connector_id: str,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> ConnectorSyncResponse:
        try:
            count = await service.sync(connector_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except PermissionError as error:
            raise HTTPException(
                status_code=401, detail="connector authorization required"
            ) from error
        except (OSError, sqlite3.Error, ValueError, TimeoutError) as error:
            raise HTTPException(status_code=503, detail="connector sync is unavailable") from error
        return ConnectorSyncResponse(connector_id=connector_id, indexed_documents=count)

    @app.get(
        "/connectors/catalog",
        response_model=list[ConnectorCatalogEntry],
        dependencies=[Depends(require_capability_token)],
    )
    async def connector_catalog(
        service: ContextConnectorService = Depends(require_context_service),
    ) -> list[ConnectorCatalogEntry]:
        return service.enterprise_catalog()

    @app.post(
        "/context/connectors/{connector_id}/enterprise/authorize",
        response_model=EnterpriseAuthorizationStart,
        dependencies=[Depends(require_capability_token)],
    )
    async def start_enterprise_authorization(
        connector_id: str,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> EnterpriseAuthorizationStart:
        try:
            authorization = service.start_enterprise_authorization(connector_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail="connector does not use enterprise authorization"
            ) from error
        except OSError as error:
            raise HTTPException(
                status_code=503, detail="enterprise authorization is unavailable"
            ) from error
        return EnterpriseAuthorizationStart(
            authorization_url=authorization.authorization_url,
            state=authorization.state,
            expires_at=authorization.expires_at,
        )

    @app.post(
        "/context/connectors/{connector_id}/enterprise/callback",
        response_model=ConnectorHealth,
        dependencies=[Depends(require_capability_token)],
    )
    async def complete_enterprise_authorization(
        connector_id: str,
        body: EnterpriseAuthorizationCallback,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> ConnectorHealth:
        try:
            return await service.complete_enterprise_authorization(
                connector_id,
                code=body.code.get_secret_value(),
                state=body.state,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except PermissionError as error:
            raise HTTPException(status_code=401, detail="authorization rejected") from error
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail="authorization callback is invalid"
            ) from error
        except OSError as error:
            raise HTTPException(
                status_code=503, detail="enterprise authorization is unavailable"
            ) from error

    @app.get(
        "/context/connectors/{connector_id}/sources",
        response_model=list[SourceSelection],
        dependencies=[Depends(require_capability_token)],
    )
    async def enterprise_sources(
        connector_id: str,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> list[SourceSelection]:
        try:
            return service.source_selections(connector_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="connector has no source scopes") from error

    @app.put(
        "/context/connectors/{connector_id}/sources",
        response_model=SourceSelection,
        dependencies=[Depends(require_capability_token)],
    )
    async def save_enterprise_source(
        connector_id: str,
        body: EnterpriseSourceEnrollmentRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> SourceSelection:
        try:
            return await service.save_source_selection(
                connector_id, body.selection, body.external_id.get_secret_value()
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="source selection is invalid") from error

    @app.delete(
        "/context/connectors/{connector_id}/sources/{selection_id}",
        dependencies=[Depends(require_capability_token)],
    )
    async def delete_enterprise_source(
        connector_id: str,
        selection_id: str,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> dict[str, bool]:
        try:
            return {
                "deleted": await service.delete_source_selection(
                    connector_id, selection_id
                )
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error

    @app.post(
        "/context/connectors/{connector_id}/lease/refresh",
        response_model=AccessLease,
        dependencies=[Depends(require_capability_token)],
    )
    async def refresh_connector_lease(
        connector_id: str,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> AccessLease:
        try:
            return await service.refresh_access_lease(connector_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except PermissionError as error:
            raise HTTPException(
                status_code=401, detail="connector authorization required"
            ) from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=503, detail="connector lease unavailable") from error

    @app.post(
        "/context/connectors/{connector_id}/revoke",
        dependencies=[Depends(require_capability_token)],
    )
    async def revoke_connector_access(
        connector_id: str,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> dict[str, bool]:
        try:
            return {"revoked": await service.revoke_access(connector_id)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="connector does not use leases") from error

    @app.delete(
        "/context/connectors/{connector_id}",
        dependencies=[Depends(require_capability_token)],
    )
    async def delete_context_connector(
        connector_id: str,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> dict[str, bool]:
        try:
            return {"deleted": await service.remove(connector_id)}
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="connector credential cleanup is incomplete"
            ) from error

    @app.delete(
        "/context",
        dependencies=[Depends(require_capability_token)],
    )
    async def delete_all_context(
        service: ContextConnectorService = Depends(require_context_service),
    ) -> dict[str, bool]:
        try:
            await service.delete_all()
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="connector credential cleanup is incomplete"
            ) from error
        return {"deleted": True}

    @app.post(
        "/context/search",
        response_model=ContextSearchResult,
        dependencies=[Depends(require_capability_token)],
    )
    async def search_context(
        body: ContextSearchQuery,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> ContextSearchResult:
        return await service.search(body)

    @app.post(
        "/evidence/search",
        response_model=EnterpriseSearchResult,
        dependencies=[Depends(require_capability_token)],
    )
    async def search_enterprise_evidence(
        body: EnterpriseSearchRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> EnterpriseSearchResult:
        return await service.enterprise_search(body)

    @app.post(
        "/context/question-context",
        response_model=ConnectedQuestionBrief,
        responses={409: {"model": ConnectedQuestionBrief}},
        dependencies=[Depends(require_capability_token)],
    )
    async def question_context(
        body: ConnectedQuestionContextRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> ConnectedQuestionBrief | JSONResponse:
        """Retrieve supplemental context only for the backend-owned ASK NOW."""

        state = registry.current_state(body.session_id)
        brief = await service.question_context(state, body)
        if brief.status == "stale":
            return JSONResponse(status_code=409, content=brief.model_dump(mode="json"))
        return brief

    @app.post(
        "/context/problem-context",
        response_model=ConnectedProblemBrief,
        responses={404: {"model": ConnectedProblemBrief}, 409: {"model": ConnectedProblemBrief}},
        dependencies=[Depends(require_capability_token)],
    )
    async def problem_context(
        body: ConnectedProblemContextRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> ConnectedProblemBrief | JSONResponse:
        brief = await service.problem_context(registry.current_state(body.session_id), body)
        if brief.status == "stale":
            return JSONResponse(status_code=409, content=brief.model_dump(mode="json"))
        if brief.status == "problem_not_found":
            return JSONResponse(status_code=404, content=brief.model_dump(mode="json"))
        return brief

    @app.post(
        "/problems/identity-decisions",
        response_model=LiveIntelligenceSnapshot,
        responses={404: {}, 409: {}, 422: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def decide_problem_identity(
        body: ProblemIdentityDecisionRequest,
    ) -> LiveIntelligenceSnapshot | JSONResponse:
        state = registry.current_state(body.session_id)
        latest_version = state.version if state is not None else 0
        if state is None:
            return JSONResponse(status_code=404, content={"status": "session_not_found"})
        if state.version != body.state_version:
            return JSONResponse(
                status_code=409,
                content={"status": "stale", "latest_state_version": latest_version},
            )
        try:
            updated = registry.decide_identity(
                body.session_id,
                body.action,
                body.pain_ids,
                body.survivor_pain_id,
            )
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=422, detail="identity decision is invalid") from error
        hints = semantic_runtime.hints(body.session_id) if semantic_runtime else []
        return LiveIntelligenceSnapshot.from_state(updated, hints)

    @app.post(
        "/problems/hints/{hint_id}/decisions",
        response_model=LiveIntelligenceSnapshot,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def decide_semantic_hint(
        hint_id: str,
        body: SemanticHintDecision,
    ) -> LiveIntelligenceSnapshot | JSONResponse:
        state = registry.current_state(body.session_id)
        latest_version = state.version if state is not None else 0
        if state is None or semantic_runtime is None:
            return JSONResponse(status_code=404, content={"status": "hint_not_found"})
        if state.version != body.state_version:
            return JSONResponse(
                status_code=409,
                content={"status": "stale", "latest_state_version": latest_version},
            )
        try:
            hint = semantic_runtime.decide(body.session_id, hint_id, body.action)
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "hint_not_found"})
        if hint is not None:
            updated = registry.confirm_semantic_hint(body.session_id, hint)
        else:
            updated = state
            registry.publish_current(body.session_id)
        registry.record_semantic_hint_decision(body.session_id, hint_id, body.action)
        return LiveIntelligenceSnapshot.from_state(updated, semantic_runtime.hints(body.session_id))

    @app.post(
        "/context/citation-feedback",
        status_code=204,
        dependencies=[Depends(require_capability_token)],
    )
    async def citation_feedback(
        body: ContextCitationFeedbackRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> None:
        await service.record_citation_feedback(body.model_dump(mode="json"))
        if improvement_workspace is not None:
            improvement_workspace.record_citation_feedback(body)

    @app.get(
        "/context/diagnostics",
        response_model=ContextDiagnostics,
        dependencies=[Depends(require_capability_token)],
    )
    async def context_diagnostics(
        service: ContextConnectorService = Depends(require_context_service),
    ) -> ContextDiagnostics:
        return await service.diagnostics()

    @app.post(
        "/evidence/query",
        response_model=EvidenceBundle,
        dependencies=[Depends(require_capability_token)],
    )
    async def query_evidence(
        body: RetrievalRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> EvidenceBundle:
        return await service.retrieve_evidence(body)

    @app.post(
        "/assurance/runs",
        response_model=AssuranceRun,
        dependencies=[Depends(require_capability_token)],
    )
    async def start_assurance_run(
        body: AssuranceRunRequest,
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> AssuranceRun:
        transient_documents = []
        unavailable_connector_ids = []
        if context_service is not None:
            transient_documents, unavailable_connector_ids = (
                await context_service.prepare_assurance_sources(body.connector_ids)
            )
        run = await workspace.run(
            body,
            transient_documents=transient_documents,
            unavailable_connector_ids=unavailable_connector_ids,
        )
        return run

    @app.get(
        "/assurance/runs/{run_id}",
        response_model=AssuranceRun,
        dependencies=[Depends(require_capability_token)],
    )
    async def get_assurance_run(
        run_id: str,
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> AssuranceRun:
        value = workspace.run_result(run_id)
        if value is None:
            raise HTTPException(status_code=404, detail="assurance run not found")
        return value

    @app.get(
        "/assurance/findings",
        response_model=list[AssuranceFinding],
        dependencies=[Depends(require_capability_token)],
    )
    async def list_assurance_findings(
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> list[AssuranceFinding]:
        return workspace.findings()

    @app.post(
        "/assurance/findings/{finding_id}/review",
        response_model=AssuranceFinding,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def review_assurance_finding(
        finding_id: str,
        body: FindingReviewRequest,
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> AssuranceFinding | JSONResponse:
        try:
            finding = workspace.review(finding_id, body)
            if improvement_workspace is not None:
                improvement_workspace.record_review(
                    kind=ImprovementSignalKind.assurance_finding_review,
                    target=f"{finding_id}:{finding.revision}",
                    action=body.action,
                    outcome=(
                        1
                        if body.action in {"confirm", "resolve", "propose_governance"}
                        else (-1 if body.action == "dismiss" else 0)
                    ),
                    attributes={"rule_id": finding.rule_id},
                )
            return finding
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "finding_not_found"})
        except RuntimeError:
            return JSONResponse(status_code=409, content={"status": "stale"})

    @app.get(
        "/assurance/rule-packs",
        response_model=list[AssuranceRulePack],
        dependencies=[Depends(require_capability_token)],
    )
    async def list_assurance_rule_packs(
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> list[AssuranceRulePack]:
        return workspace.rule_packs()

    @app.put(
        "/assurance/rule-packs",
        response_model=AssuranceRulePack,
        dependencies=[Depends(require_capability_token)],
    )
    async def install_assurance_rule_pack(
        body: RulePackInstallRequest,
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> AssuranceRulePack:
        try:
            package = base64.b64decode(body.package_base64, validate=True)
            rule_pack = verify_rule_pack(package, workspace.trusted_keys())
            return workspace.save_rule_pack(rule_pack)
        except (ValueError, PermissionError) as error:
            raise HTTPException(status_code=422, detail="rule pack is invalid") from error

    @app.get(
        "/assurance/trusted-keys",
        response_model=list[TrustedRuleSigningKey],
        dependencies=[Depends(require_capability_token)],
    )
    async def list_assurance_trusted_keys(
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> list[TrustedRuleSigningKey]:
        return workspace.trusted_keys()

    @app.put(
        "/assurance/trusted-keys",
        response_model=TrustedRuleSigningKey,
        dependencies=[Depends(require_capability_token)],
    )
    async def trust_assurance_signing_key(
        body: TrustedRuleKeyRequest,
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> TrustedRuleSigningKey:
        try:
            value = signing_key(
                key_id=body.key_id,
                label=body.label,
                public_key_base64=body.public_key_base64,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="signing key is invalid") from error
        return workspace.save_trusted_key(value)

    @app.get(
        "/assurance/schedules",
        response_model=list[AssuranceSchedule],
        dependencies=[Depends(require_capability_token)],
    )
    async def list_assurance_schedules(
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> list[AssuranceSchedule]:
        return workspace.schedules()

    @app.put(
        "/assurance/schedules",
        response_model=AssuranceSchedule,
        dependencies=[Depends(require_capability_token)],
    )
    async def save_assurance_schedule(
        body: AssuranceSchedule,
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> AssuranceSchedule:
        return workspace.save_schedule(body)

    @app.post(
        "/data-quality/runs",
        response_model=DataQualityRun,
        dependencies=[Depends(require_capability_token)],
    )
    async def run_data_quality(
        body: DataQualityRunRequest,
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> DataQualityRun:
        return await workspace.run_data_quality(body)

    @app.get(
        "/data-quality/rule-packs",
        response_model=list[DataQualityRulePack],
        dependencies=[Depends(require_capability_token)],
    )
    async def data_quality_rule_packs(
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> list[DataQualityRulePack]:
        return workspace.rule_packs()

    @app.put(
        "/data-quality/rule-packs",
        response_model=DataQualityRulePack,
        dependencies=[Depends(require_capability_token)],
    )
    async def install_data_quality_rule_pack(
        body: RulePackInstallRequest,
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
        assurance: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> DataQualityRulePack:
        try:
            package = base64.b64decode(body.package_base64, validate=True)
            pack = verify_data_quality_rule_pack(package, assurance.trusted_keys())
            return workspace.save_rule_pack(pack)
        except (ValueError, PermissionError) as error:
            raise HTTPException(
                status_code=422, detail="data-quality rule pack is invalid"
            ) from error

    @app.get(
        "/data-quality/findings",
        response_model=list[DataQualityFinding],
        dependencies=[Depends(require_capability_token)],
    )
    async def data_quality_findings(
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> list[DataQualityFinding]:
        return workspace.findings()

    @app.post(
        "/data-quality/findings/{finding_id}/review",
        response_model=DataQualityFinding,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def review_data_quality_finding(
        finding_id: str,
        body: DataQualityFindingReview,
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> DataQualityFinding | JSONResponse:
        try:
            finding = workspace.review_finding(finding_id, body)
            if improvement_workspace is not None:
                improvement_workspace.record_review(
                    kind=ImprovementSignalKind.netsuite_finding_review,
                    target=f"{finding_id}:{finding.revision}",
                    action=body.action,
                    outcome=1 if body.action == "confirm" else -1,
                    source_kind=ConnectorKind.netsuite,
                    attributes={"rule_id": finding.rule_id},
                    remote_source=True,
                )
            return finding
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "finding_not_found"})
        except RuntimeError:
            return JSONResponse(status_code=409, content={"status": "stale"})

    @app.get(
        "/external-actions",
        response_model=list[ExternalActionDraft],
        dependencies=[Depends(require_capability_token)],
    )
    async def external_actions(
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> list[ExternalActionDraft]:
        return workspace.actions()

    @app.post(
        "/external-actions",
        response_model=ExternalActionDraft,
        dependencies=[Depends(require_capability_token)],
    )
    async def create_external_action(
        body: ExternalActionCreateRequest,
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> ExternalActionDraft:
        try:
            return workspace.create_action(body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="finding not found") from error
        except PermissionError as error:
            raise HTTPException(status_code=409, detail="finding is not confirmed") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="destination is invalid") from error

    @app.post(
        "/external-actions/{action_id}/approve",
        response_model=ExternalActionDraft,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def approve_external_action(
        action_id: str,
        body: ExternalActionDecision,
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> ExternalActionDraft | JSONResponse:
        del action_id, body, workspace
        return JSONResponse(
            status_code=409,
            content={
                "status": "direct_writes_disabled",
                "detail": "Export the reviewed draft and submit it manually.",
            },
        )

    @app.post(
        "/external-actions/{action_id}/export",
        response_model=ReviewedExternalActionExport,
        dependencies=[Depends(require_capability_token)],
    )
    async def export_external_action(
        action_id: str,
        body: ReviewedExternalActionExportRequest,
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> ReviewedExternalActionExport:
        action = next(
            (value for value in workspace.actions() if value.action_id == action_id), None
        )
        if action is None:
            raise HTTPException(status_code=404, detail="action not found")
        filename, media_type, payload = render_external_action_export(action, body.format)
        return ReviewedExternalActionExport(
            filename=filename,
            media_type=media_type,
            payload_base64=base64.b64encode(payload).decode("ascii"),
        )

    @app.post(
        "/external-actions/{action_id}/cancel",
        response_model=ExternalActionDraft,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def cancel_external_action(
        action_id: str,
        body: ExternalActionDecision,
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> ExternalActionDraft | JSONResponse:
        try:
            return await workspace.cancel_action(action_id, body.expected_revision)
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "action_not_found"})
        except RuntimeError:
            return JSONResponse(status_code=409, content={"status": "stale"})

    @app.get(
        "/notification-policies",
        response_model=list[NotificationPolicy],
        dependencies=[Depends(require_capability_token)],
    )
    async def notification_policies(
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> list[NotificationPolicy]:
        return workspace.policies()

    @app.put(
        "/notification-policies",
        response_model=NotificationPolicy,
        dependencies=[Depends(require_capability_token)],
    )
    async def save_notification_policy(
        body: NotificationPolicyUpsertRequest,
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> NotificationPolicy:
        try:
            return workspace.save_policy(body.policy, body.expected_revision)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail="policy rule is not trusted") from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail="policy is stale") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="policy is invalid") from error

    @app.get(
        "/document-routing/destinations",
        response_model=list[DocumentRoutingDestination],
        dependencies=[Depends(require_capability_token)],
    )
    async def routing_destinations(
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> list[DocumentRoutingDestination]:
        return workspace.routing_destinations()

    @app.put(
        "/document-routing/destinations",
        response_model=DocumentRoutingDestination,
        dependencies=[Depends(require_capability_token)],
    )
    async def save_routing_destination(
        body: RoutingDestinationUpsertRequest,
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> DocumentRoutingDestination:
        try:
            return workspace.save_routing_destination(
                body.destination, body.root_path.get_secret_value()
            )
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail="destination is invalid") from error

    @app.get(
        "/document-routing/proposals",
        response_model=list[DocumentRoutingProposal],
        dependencies=[Depends(require_capability_token)],
    )
    async def document_routing_proposals(
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> list[DocumentRoutingProposal]:
        return workspace.routing_proposals()

    @app.post(
        "/document-routing/proposals/{proposal_id}/review",
        response_model=DocumentRoutingProposal,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def review_document_routing(
        proposal_id: str,
        body: DocumentRoutingReview,
        workspace: IntegrationWorkspace = Depends(require_integration_workspace),
    ) -> DocumentRoutingProposal | JSONResponse:
        try:
            proposal = await workspace.review_routing(proposal_id, body)
            if improvement_workspace is not None:
                improvement_workspace.record_review(
                    kind=ImprovementSignalKind.routing_review,
                    target=f"{proposal_id}:{proposal.revision}",
                    action=body.action,
                    outcome=1 if body.action == "execute" else -1,
                    attributes={"routing_action": body.routing_action.value},
                )
            return proposal
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "proposal_not_found"})
        except RuntimeError:
            return JSONResponse(status_code=409, content={"status": "stale"})

    @app.get(
        "/improvements/proposals",
        response_model=list[ImprovementProposal],
        dependencies=[Depends(require_capability_token)],
    )
    async def improvement_proposals(
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> list[ImprovementProposal]:
        return workspace.proposals()

    @app.post(
        "/improvements/ask-now-feedback",
        status_code=204,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def ask_now_feedback(
        body: AskNowFeedbackRequest,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> Response:
        state = registry.current_state(body.session_id)
        if state is None:
            raise HTTPException(status_code=404, detail="session not found")
        if state.version != body.state_version:
            raise HTTPException(status_code=409, detail="meeting state is stale")
        suggestion = next(
            (
                value
                for value in state.suggestions
                if value.suggestion_id == body.suggestion_id
                and value.role.value == "ask_now"
            ),
            None,
        )
        if suggestion is None:
            raise HTTPException(status_code=404, detail="suggestion not found")
        gap = next((value for value in state.gaps if value.gap_id == suggestion.gap_id), None)
        pain = next(
            (
                value
                for value in state.pain_points
                if gap is not None and value.pain_id == gap.pain_id
            ),
            None,
        )
        if gap is None or pain is None or gap.reopen_count != body.reopen_count:
            raise HTTPException(status_code=409, detail="suggestion occurrence is stale")
        workspace.record_review(
            kind=ImprovementSignalKind.ask_now_usefulness,
            target=f"{body.session_id}:{body.suggestion_id}:{body.reopen_count}",
            action=body.action,
            outcome=1 if body.action == "useful" else -1,
            language=next(
                (
                    event.lang.value
                    for event in state.transcript
                    if event.event_id in pain.evidence_event_ids
                ),
                None,
            ),
            attributes={"template": pain.template_id, "slot": gap.slot},
        )
        return Response(status_code=204)

    @app.post(
        "/improvements/proposals/{proposal_id}/evaluate",
        response_model=ImprovementEvaluation,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def evaluate_improvement_proposal(
        proposal_id: str,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ImprovementEvaluation | JSONResponse:
        try:
            return workspace.evaluate(proposal_id)
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "proposal_not_found"})
        except RuntimeError:
            return JSONResponse(status_code=409, content={"status": "not_evaluable"})

    @app.get(
        "/improvements/proposals/{proposal_id}/evaluation",
        response_model=ImprovementEvaluation,
        responses={404: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def get_improvement_evaluation(
        proposal_id: str,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ImprovementEvaluation:
        value = workspace.evaluation_for(proposal_id)
        if value is None:
            raise HTTPException(status_code=404, detail="evaluation not found")
        return value

    @app.post(
        "/improvements/proposals/{proposal_id}/shadow-runs",
        response_model=ImprovementShadowRun,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def run_improvement_shadow_cycle(
        proposal_id: str,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ImprovementShadowRun | JSONResponse:
        try:
            return workspace.run_shadow_cycle(proposal_id)
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "proposal_not_found"})
        except RuntimeError:
            return JSONResponse(status_code=409, content={"status": "shadow_not_ready"})

    @app.post(
        "/improvements/proposals/{proposal_id}/review",
        response_model=ImprovementProposal,
        responses={404: {}, 409: {}, 422: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def review_improvement_proposal(
        proposal_id: str,
        body: ImprovementProposalReview,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ImprovementProposal | JSONResponse:
        try:
            return workspace.review(proposal_id, body)
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "proposal_not_found"})
        except RuntimeError:
            return JSONResponse(status_code=409, content={"status": "stale"})
        except PermissionError:
            return JSONResponse(status_code=422, content={"status": "evaluation_required"})

    @app.get(
        "/improvements/profiles",
        response_model=list[ImprovementProfile],
        dependencies=[Depends(require_capability_token)],
    )
    async def improvement_profiles(
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> list[ImprovementProfile]:
        return workspace.profiles()

    @app.post(
        "/improvements/profiles/{profile_id}/rollback",
        response_model=ImprovementProfile,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def rollback_improvement_profile(
        profile_id: str,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ImprovementProfile | JSONResponse:
        try:
            return workspace.rollback(profile_id)
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "profile_not_found"})
        except RuntimeError:
            return JSONResponse(status_code=409, content={"status": "invalid_rollback"})

    @app.get(
        "/improvements/settings",
        response_model=ImprovementSettings,
        dependencies=[Depends(require_capability_token)],
    )
    async def improvement_settings(
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ImprovementSettings:
        return workspace.settings()

    @app.put(
        "/improvements/settings",
        response_model=ImprovementSettings,
        responses={409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def update_improvement_settings(
        body: ImprovementSettingsUpdate,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ImprovementSettings | JSONResponse:
        requested = ImprovementSettings(
            paused=body.paused,
            excerpt_retention_days=body.excerpt_retention_days,
            revision=body.expected_revision,
        )
        try:
            return workspace.update_settings(requested, body.expected_revision)
        except RuntimeError:
            return JSONResponse(status_code=409, content={"status": "stale"})

    @app.delete(
        "/improvements/data",
        response_model=ImprovementDataDeleteResponse,
        dependencies=[Depends(require_capability_token)],
    )
    async def delete_improvement_data(
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ImprovementDataDeleteResponse:
        workspace.repository.delete_all()
        return ImprovementDataDeleteResponse()

    @app.post(
        "/improvements/packs/export",
        response_model=ImprovementPackExportResponse,
        dependencies=[Depends(require_capability_token)],
    )
    async def export_improvement_pack(
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ImprovementPackExportResponse:
        package = workspace.export_pack()
        return ImprovementPackExportResponse(
            filename="meeting-intelligence-improvements-v11.zip",
            sha256=hashlib.sha256(package).hexdigest(),
            content_base64=base64.b64encode(package).decode("ascii"),
        )

    @app.post(
        "/improvements/packs/import",
        response_model=ImprovementPackManifest,
        responses={422: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def import_improvement_pack(
        body: ImprovementPackImportRequest,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ImprovementPackManifest:
        try:
            package = base64.b64decode(body.content_base64, validate=True)
            return workspace.import_pack(package)
        except (ValueError, TypeError) as error:
            raise HTTPException(status_code=422, detail="improvement pack is invalid") from error

    @app.get(
        "/documents/inbox",
        response_model=DocumentInbox,
        response_model_exclude={
            "classifications": {"__all__": {"artifact_id", "revision_id"}},
            "dispositions": {"__all__": {"classification_id"}},
        },
        dependencies=[Depends(require_capability_token)],
    )
    async def document_inbox(
        lifecycle: str | None = Query(default=None, max_length=32),
        document_kind: str | None = Query(default=None, max_length=64),
        text: str | None = Query(default=None, max_length=120),
        offset: int = Query(default=0, ge=0, le=5_000),
        limit: int = Query(default=100, ge=1, le=500),
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> DocumentInbox:
        inbox = workspace.document_inbox()
        values = [
            value
            for value in inbox.classifications
            if (lifecycle is None or value.lifecycle.value == lifecycle)
            and (document_kind is None or value.document_kind.value == document_kind)
            and (text is None or text.casefold() in value.source_reference.casefold())
        ]
        selected_ids = {value.classification_id for value in values[offset : offset + limit]}
        return DocumentInbox(
            classifications=values[offset : offset + limit],
            duplicate_groups=inbox.duplicate_groups[:limit],
            dispositions=[
                value for value in inbox.dispositions if value.classification_id in selected_ids
            ],
        )

    @app.post(
        "/documents/scan",
        response_model=DocumentScanStatus,
        dependencies=[Depends(require_capability_token)],
    )
    async def scan_documents(
        body: DocumentScanRequest,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> DocumentScanStatus:
        return await workspace.scan_documents(body.connector_ids)

    @app.get(
        "/documents/scan/status",
        response_model=DocumentScanStatus,
        dependencies=[Depends(require_capability_token)],
    )
    async def document_scan_status(
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> DocumentScanStatus:
        return workspace.scan_status()

    @app.post(
        "/documents/classifications/{classification_id}/review",
        response_model=DocumentClassification,
        response_model_exclude={"artifact_id", "revision_id"},
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def review_document_classification(
        classification_id: str,
        body: DocumentClassificationReview,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> DocumentClassification | JSONResponse:
        try:
            return workspace.review_classification(classification_id, body)
        except KeyError:
            return JSONResponse(
                status_code=404, content={"status": "classification_not_found"}
            )
        except RuntimeError:
            return JSONResponse(status_code=409, content={"status": "stale"})

    @app.post(
        "/documents/classifications/batch-review",
        response_model=DocumentClassificationBatchResponse,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def review_document_classifications_batch(
        body: DocumentClassificationBatchRequest,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> DocumentClassificationBatchResponse | JSONResponse:
        current = {
            value.classification_id: value
            for value in workspace.document_inbox().classifications
        }
        if any(item.classification_id not in current for item in body.items):
            return JSONResponse(status_code=404, content={"status": "classification_not_found"})
        if any(
            current[item.classification_id].revision != item.expected_revision
            for item in body.items
        ):
            return JSONResponse(status_code=409, content={"status": "stale"})
        for item in body.items:
            workspace.review_classification(
                item.classification_id,
                DocumentClassificationReview(
                    expected_revision=item.expected_revision,
                    action=item.action,
                ),
            )
        return DocumentClassificationBatchResponse(reviewed=len(body.items))

    @app.post(
        "/documents/dispositions/{disposition_id}/review",
        response_model=ImprovementDocumentDispositionProposal,
        response_model_exclude={"classification_id"},
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def review_document_disposition(
        disposition_id: str,
        body: DocumentDispositionReview,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ImprovementDocumentDispositionProposal | JSONResponse:
        try:
            return workspace.review_disposition(disposition_id, body)
        except KeyError:
            return JSONResponse(
                status_code=404, content={"status": "disposition_not_found"}
            )
        except RuntimeError:
            return JSONResponse(status_code=409, content={"status": "stale"})

    @app.post(
        "/documents/dispositions/{disposition_id}/execute",
        response_model=DocumentOperationPlan,
        responses={404: {}, 409: {}, 422: {}, 503: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def execute_document_disposition(
        disposition_id: str,
        body: DocumentDispositionExecuteRequest,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> DocumentOperationPlan | JSONResponse:
        try:
            return await workspace.execute_disposition(
                disposition_id,
                expected_revision=body.expected_revision,
                destination_ref=body.destination_ref,
                routing_action=body.routing_action,
            )
        except KeyError:
            return JSONResponse(status_code=404, content={"status": "not_found"})
        except RuntimeError:
            return JSONResponse(status_code=409, content={"status": "stale_or_unavailable"})
        except PermissionError:
            return JSONResponse(status_code=422, content={"status": "review_required"})

    @app.get(
        "/erp/governance/templates",
        response_model=list[ERPControlTemplate],
        dependencies=[Depends(require_capability_token)],
    )
    async def erp_governance_templates(
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> list[ERPControlTemplate]:
        return workspace.erp_templates()

    @app.post(
        "/erp/governance/runs",
        response_model=ERPGovernanceRun,
        responses={422: {}, 503: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def run_erp_governance(
        body: ERPGovernanceRunRequest,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ERPGovernanceRun:
        try:
            return await workspace.run_erp_governance(body)
        except KeyError as error:
            raise HTTPException(status_code=422, detail="connector is unavailable") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="connector is not NetSuite") from error
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail="ERP governance is unavailable") from error

    @app.post(
        "/erp/governance/mappings/validate",
        response_model=NetSuiteMappingValidation,
        dependencies=[Depends(require_capability_token)],
    )
    async def validate_netsuite_mapping(
        body: NetSuiteMappingValidationRequest,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> NetSuiteMappingValidation:
        try:
            return await workspace.validate_netsuite_mapping(body.connector_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="connector is not NetSuite") from error

    @app.get(
        "/erp/governance/runs/{run_id}",
        response_model=ERPGovernanceRun,
        dependencies=[Depends(require_capability_token)],
    )
    async def get_erp_governance_run(
        run_id: str,
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ERPGovernanceRun:
        value = workspace.erp_run(run_id)
        if value is None:
            raise HTTPException(status_code=404, detail="ERP governance run not found")
        return value

    @app.get(
        "/erp/governance/dashboard",
        response_model=ERPGovernanceDashboard,
        dependencies=[Depends(require_capability_token)],
    )
    async def erp_governance_dashboard(
        workspace: ImprovementWorkspace = Depends(require_improvement_workspace),
    ) -> ERPGovernanceDashboard:
        return workspace.erp_dashboard()

    @app.post(
        "/erp/analytics/runs",
        response_model=ERPAnalyticsRun,
        dependencies=[Depends(require_capability_token)],
    )
    async def run_erp_analytics(
        body: ERPAnalyticsRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> ERPAnalyticsRun:
        return await service.erp_analytics(body)

    @app.post(
        "/erp/fulfillment-reconciliation/runs",
        response_model=FulfillmentReconciliationRun,
        dependencies=[Depends(require_capability_token)],
    )
    async def run_fulfillment_reconciliation(
        body: FulfillmentReconciliationRequest,
        service: ContextConnectorService = Depends(require_context_service),
    ) -> FulfillmentReconciliationRun:
        return await service.fulfillment_reconciliation(body)

    @app.post(
        "/erp/metadata/check",
        response_model=MetadataValidationResult,
        dependencies=[Depends(require_capability_token)],
    )
    async def check_erp_metadata(
        body: ERPMetadataCheckRequest,
        service: ContextConnectorService = Depends(require_context_service),
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> MetadataValidationResult:
        try:
            snapshot = await service.erp_metadata_snapshot(body.connector_id)
            return workspace.validate_erp(snapshot, body.artifact_ids)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error
        except (OSError, TimeoutError, ValueError) as error:
            raise HTTPException(status_code=503, detail="ERP metadata is unavailable") from error

    @app.post(
        "/solution-thread/impact-analysis",
        response_model=SolutionThreadImpact,
        dependencies=[Depends(require_capability_token)],
    )
    async def solution_thread_impact_analysis(
        body: SolutionThreadImpactRequest,
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> SolutionThreadImpact:
        try:
            return workspace.impact(body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="solution node not found") from error

    @app.post(
        "/solution-thread/mind-map",
        response_model=SolutionMindMap,
        dependencies=[Depends(require_capability_token)],
    )
    async def solution_thread_mind_map(
        body: SolutionMindMapRequest,
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> SolutionMindMap:
        return workspace.mind_map(body)

    @app.get(
        "/solution-thread/edges/suggested",
        response_model=list[SolutionThreadEdgeProposal],
        dependencies=[Depends(require_capability_token)],
    )
    async def suggested_solution_thread_edges(
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> list[SolutionThreadEdgeProposal]:
        return workspace.suggested_edges()

    @app.post(
        "/solution-thread/edges/{edge_id}/review",
        response_model=SolutionThreadEdge,
        dependencies=[Depends(require_capability_token)],
    )
    async def review_solution_thread_edge(
        edge_id: str,
        body: SolutionThreadEdgeReviewRequest,
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> SolutionThreadEdge:
        try:
            return workspace.review_edge(edge_id, body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="solution edge not found") from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail="stale solution edge") from error

    @app.post(
        "/consistency/runs",
        response_model=ConsistencyRun,
        dependencies=[Depends(require_capability_token)],
    )
    async def start_consistency_run(
        body: ConsistencyRunRequest,
        service: ContextConnectorService = Depends(require_context_service),
        workspace: ConsistencyWorkspace = Depends(require_consistency_workspace),
    ) -> ConsistencyRun:
        connector_ids = list(
            dict.fromkeys([*body.document_connector_ids, *body.observed_connector_ids])
        )
        try:
            definitions = {
                connector_id: service.connector_definition(connector_id)
                for connector_id in connector_ids
            }
            transient, snapshots, unavailable, leased = (
                await service.prepare_consistency_sources(
                    body.document_connector_ids, body.observed_connector_ids
                )
            )
            return await workspace.run(
                body,
                connector_labels={
                    key: value.display_name for key, value in definitions.items()
                },
                connector_kinds={key: value.kind for key, value in definitions.items()},
                glossary_entries=service.entity_glossary_with_mappings(),
                erp_snapshots=snapshots,
                transient_documents=transient,
                leased_connector_ids=leased,
                unavailable_connector_ids=unavailable,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="connector not found") from error

    @app.get(
        "/consistency/runs/{run_id}",
        response_model=ConsistencyRun,
        dependencies=[Depends(require_capability_token)],
    )
    async def get_consistency_run(
        run_id: str,
        workspace: ConsistencyWorkspace = Depends(require_consistency_workspace),
    ) -> ConsistencyRun:
        run = workspace.run_result(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="consistency run not found")
        return run

    @app.get(
        "/consistency/findings",
        response_model=list[ConsistencyFinding],
        dependencies=[Depends(require_capability_token)],
    )
    async def list_consistency_findings(
        system: str | None = Query(default=None, max_length=160),
        project: str | None = Query(default=None, max_length=160),
        repository: str | None = Query(default=None, max_length=200),
        document: str | None = Query(default=None, max_length=300),
        environment: str | None = Query(default=None, max_length=120),
        document_kind: str | None = Query(default=None, max_length=80),
        mismatch_kind: ConsistencyMismatchKind | None = Query(default=None),
        severity: AssuranceSeverity | None = Query(default=None),
        status: ConsistencyFindingStatus | None = Query(default=None),
        owner_role: str | None = Query(default=None, max_length=160),
        limit: int = Query(default=200, ge=1, le=1_000),
        workspace: ConsistencyWorkspace = Depends(require_consistency_workspace),
    ) -> list[ConsistencyFinding]:
        return workspace.findings(
            ConsistencyFindingFilter(
                system=system,
                project=project,
                repository=repository,
                document=document,
                environment=environment,
                document_kind=document_kind,
                mismatch_kind=mismatch_kind,
                severity=severity,
                status=status,
                owner_role=owner_role,
                limit=limit,
            )
        )

    @app.post(
        "/consistency/findings/{finding_id}/review",
        response_model=ConsistencyFinding,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def review_consistency_finding(
        finding_id: str,
        body: ConsistencyFindingReviewRequest,
        workspace: ConsistencyWorkspace = Depends(require_consistency_workspace),
    ) -> ConsistencyFinding:
        if body.action == "request_refresh":
            current = workspace.finding(finding_id)
            if current is None:
                raise HTTPException(status_code=404, detail="consistency finding not found")
            service = require_context_service()
            try:
                await service.prepare_consistency_sources(
                    [current.document_claim.citation.connector_id],
                    [current.observed_fact.citation.connector_id],
                )
            except (KeyError, OSError, PermissionError, TimeoutError) as error:
                raise HTTPException(
                    status_code=503, detail="source refresh unavailable"
                ) from error
        try:
            return workspace.review_finding(finding_id, body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="consistency finding not found") from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail="stale consistency finding") from error

    @app.post(
        "/consistency/claims/{claim_id}/review",
        response_model=DocumentClaim,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def review_consistency_claim(
        claim_id: str,
        body: ConsistencyClaimReviewRequest,
        workspace: ConsistencyWorkspace = Depends(require_consistency_workspace),
    ) -> DocumentClaim:
        try:
            return workspace.review_claim(claim_id, body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="consistency claim not found") from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail="stale consistency claim") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="claim does not require review") from error

    @app.get(
        "/consistency/authority-policies",
        response_model=list[AuthorityPolicy],
        dependencies=[Depends(require_capability_token)],
    )
    async def list_consistency_authority_policies(
        workspace: ConsistencyWorkspace = Depends(require_consistency_workspace),
    ) -> list[AuthorityPolicy]:
        return workspace.policies()

    @app.put(
        "/consistency/authority-policies",
        response_model=AuthorityPolicy,
        responses={409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def save_consistency_authority_policy(
        body: AuthorityPolicy,
        workspace: ConsistencyWorkspace = Depends(require_consistency_workspace),
    ) -> AuthorityPolicy:
        try:
            return workspace.save_policy(body)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail="stale authority policy") from error

    @app.get(
        "/consistency/dashboard",
        response_model=ConsistencyDashboard,
        dependencies=[Depends(require_capability_token)],
    )
    async def consistency_dashboard(
        workspace: ConsistencyWorkspace = Depends(require_consistency_workspace),
    ) -> ConsistencyDashboard:
        return workspace.dashboard()

    @app.post(
        "/consistency/findings/{finding_id}/export",
        response_model=ConsistencyExport,
        responses={404: {}, 409: {}},
        dependencies=[Depends(require_capability_token)],
    )
    async def export_consistency_finding(
        finding_id: str,
        body: ConsistencyExportRequest,
        workspace: ConsistencyWorkspace = Depends(require_consistency_workspace),
    ) -> ConsistencyExport:
        try:
            return workspace.export(finding_id, body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="consistency finding not found") from error
        except PermissionError as error:
            raise HTTPException(status_code=409, detail="finding review required") from error

    @app.get(
        "/solution-lead/dashboard",
        response_model=SolutionLeadDashboard,
        dependencies=[Depends(require_capability_token)],
    )
    async def solution_lead_dashboard(
        workspace: AssuranceWorkspace = Depends(require_assurance_workspace),
    ) -> SolutionLeadDashboard:
        portfolio = governance_workspace.portfolio() if governance_workspace else None
        return workspace.dashboard(
            governance_records=portfolio.records if portfolio else [],
            governance_alerts=portfolio.alerts if portfolio else [],
        )

    async def enrich_issue_draft(
        draft: StructuredIssueDraft,
        state,
        connector_ids: list[str],
        service: ContextConnectorService,
    ) -> StructuredIssueDraft:
        result = await service.problem_context(
            state,
            ConnectedProblemContextRequest(
                session_id=draft.session_id,
                pain_id=draft.pain_id,
                state_version=draft.state_version,
                connector_ids=connector_ids,
                limit=8,
            ),
        )
        status = (
            result.status
            if result.status in {"current", "unavailable", "timeout"}
            else "unavailable"
        )
        return draft.model_copy(
            update={
                "context_citations": result.citations,
                "context_status": status,
            }
        )

    @app.post(
        "/issues/drafts",
        response_model=StructuredIssueDraft,
        dependencies=[Depends(require_capability_token)],
        responses={404: {}, 409: {}},
    )
    async def create_issue_draft(
        body: StructuredIssueDraftRequest,
    ) -> StructuredIssueDraft | JSONResponse:
        state = registry.current_state(body.session_id)
        latest_version = state.version if state is not None else 0
        if state is None or state.version != body.state_version:
            return JSONResponse(
                status_code=409,
                content={"status": "stale", "latest_state_version": latest_version},
            )
        draft = build_issue_draft(state, body.pain_id)
        if draft is None:
            return JSONResponse(status_code=404, content={"status": "problem_not_found"})
        if body.include_context:
            if context_service is None:
                draft = draft.model_copy(update={"context_status": "unavailable"})
            else:
                draft = await enrich_issue_draft(draft, state, body.connector_ids, context_service)
        return draft

    @app.post(
        "/issues/drafts/batch",
        response_model=StructuredIssueDraftBatch,
        dependencies=[Depends(require_capability_token)],
        responses={404: {}, 409: {}},
    )
    async def create_issue_draft_batch(
        body: StructuredIssueDraftBatchRequest,
    ) -> StructuredIssueDraftBatch | JSONResponse:
        state = registry.current_state(body.session_id)
        latest_version = state.version if state is not None else 0
        if state is None or state.version != body.state_version:
            return JSONResponse(
                status_code=409,
                content={"status": "stale", "latest_state_version": latest_version},
            )
        drafts: list[StructuredIssueDraft] = []
        for pain_id in dict.fromkeys(body.pain_ids):
            draft = build_issue_draft(state, pain_id)
            if draft is None:
                return JSONResponse(
                    status_code=404,
                    content={"status": "problem_not_found"},
                )
            drafts.append(draft)
        if body.include_context:
            if context_service is None:
                drafts = [
                    draft.model_copy(update={"context_status": "unavailable"}) for draft in drafts
                ]
            else:
                drafts = await asyncio.gather(
                    *(
                        enrich_issue_draft(draft, state, body.connector_ids, context_service)
                        for draft in drafts
                    )
                )
        return StructuredIssueDraftBatch(state_version=state.version, drafts=drafts)

    @app.post(
        "/issues/drafts/final",
        response_model=StructuredIssueDraftBatch,
        dependencies=[Depends(require_capability_token)],
    )
    async def create_final_issue_draft_batch(
        body: FinalIssueDraftBatchRequest,
    ) -> StructuredIssueDraftBatch:
        """Internal stop-time projection before the transient session is purged."""

        state = registry.current_state(body.session_id)
        if state is None:
            return StructuredIssueDraftBatch(state_version=0, drafts=[])
        drafts = [
            draft
            for pain in state.pain_points[:50]
            if (draft := build_issue_draft(state, pain.pain_id)) is not None
        ]
        return StructuredIssueDraftBatch(state_version=state.version, drafts=drafts)

    @app.websocket("/ws/meeting/{session_id}")
    async def meeting_ws(websocket: WebSocket, session_id: str) -> None:
        if not is_valid_session_id(session_id):
            await websocket.close(code=1008, reason="Meeting session id is invalid")
            return
        if not is_allowed_websocket_origin(websocket.headers.get("origin")):
            await websocket.close(code=1008, reason="WebSocket origin is not allowed")
            return
        protocols = websocket_subprotocols(websocket)
        if auth.enabled and (
            WEBSOCKET_PROTOCOL not in protocols
            or not auth.accepts(websocket_capability_token(websocket))
        ):
            await websocket.close(code=1008, reason="Local capability token is invalid")
            return
        selected_protocol = WEBSOCKET_PROTOCOL if WEBSOCKET_PROTOCOL in protocols else None
        queue: asyncio.Queue[Any] | None = None
        try:
            demo = resolve_demo(session_id)
            if demo is not None:
                # Known fixture id: replay mode (existing Slice 0-2 contract).
                await websocket.accept(subprotocol=selected_protocol)
                await websocket.send_json(initial_snapshot(session_id).model_dump(mode="json"))
                engine = MeetingEngine(session_id)
                source = ReplayTranscriptSource(
                    demo.fixture_path, session_id, lang=demo.lang, speed_factor=replay_speed
                )
                async for event in source.events():
                    if engine.apply(event):
                        await websocket.send_json(engine.state.model_dump(mode="json"))
                return

            # Otherwise: a live meeting fed by real Meetily ingestion (Phase 2).
            # Restore durable state before accepting the socket. If acceptance
            # happens first, the client observes a false empty reset while the
            # event loop rebuilds a long session and unnecessarily replays its
            # complete canonical transcript history.
            queue = registry.subscribe(session_id)
            current = registry.current_state(session_id)
            await websocket.accept(subprotocol=selected_protocol)
            await websocket.send_json(
                LiveIntelligenceSnapshot(meeting_id=session_id).model_dump(mode="json")
            )
            # session_id is opaque and never used as a filesystem path -- unlike
            # the demo registry there is no fixture lookup, so no traversal surface.
            # Note: subscribing does NOT set the meeting's language -- only a real
            # ingest() call is authoritative for that (independent review finding;
            # a subscriber's own ?lang= query param used to be able to race an
            # actual push and silently mis-configure the session language).
            # Only catch up a late/reconnecting subscriber -- a fresh meeting's
            # current_state is the same trivial v0 already sent above.
            if current is not None and current.version > 0:
                await websocket.send_json(
                    LiveIntelligenceSnapshot.from_state(
                        current,
                        semantic_runtime.hints(session_id) if semantic_runtime else [],
                        governance_workspace.candidates(session_id, pending_only=True)
                        if governance_workspace
                        else [],
                        governance_workspace.portfolio().alerts if governance_workspace else [],
                    ).model_dump(mode="json")
                )
            while True:
                state = await queue.get()
                await websocket.send_json(
                    LiveIntelligenceSnapshot.from_state(
                        state,
                        semantic_runtime.hints(session_id) if semantic_runtime else [],
                        governance_workspace.candidates(session_id, pending_only=True)
                        if governance_workspace
                        else [],
                        governance_workspace.portfolio().alerts if governance_workspace else [],
                    ).model_dump(mode="json")
                )
        except WebSocketDisconnect:
            return
        finally:
            if queue is not None:
                registry.unsubscribe(session_id, queue)

    return app


def load_default_context_service() -> ContextConnectorService | None:
    """Keep optional connector storage failures isolated from live recording."""

    try:
        return ContextConnectorService.windows_default()
    except Exception:
        return None


def load_default_semantic_runtime() -> SemanticHintRuntime | None:
    """Semantic hints are strictly opt-in and remain isolated from startup."""

    if os.environ.get(SEMANTIC_FEATURE_FLAG, "off").strip().lower() != "ollama":
        return None
    try:
        return SemanticHintRuntime(semantic_candidate_analyzer_from_env())
    except Exception:
        return None


def load_default_governance_workspace() -> GovernanceWorkspace | None:
    """Open the user-scoped encrypted governance store without blocking startup."""

    try:
        root = Path(
            os.environ.get("MEETING_INTELLIGENCE_CACHE_DIR")
            or Path(os.environ["LOCALAPPDATA"]) / "MeetingIntelligenceCopilot"
        )
        repository = EncryptedGovernanceRepository(
            root / "governance" / "governance-v7.sqlite3",
            WindowsDpapiProtector(),
        )
        return GovernanceWorkspace(repository)
    except Exception:
        return None


def load_default_assurance_workspace(
    context_service: ContextConnectorService | None,
) -> AssuranceWorkspace | None:
    """Open v8 assurance storage without making it a recording dependency."""

    if context_service is None:
        return None
    try:
        root = Path(
            os.environ.get("MEETING_INTELLIGENCE_CACHE_DIR")
            or Path(os.environ["LOCALAPPDATA"]) / "MeetingIntelligenceCopilot"
        )
        repository = EncryptedAssuranceRepository(
            root / "assurance" / "assurance-v8.sqlite3",
            WindowsDpapiProtector(),
        )
        return AssuranceWorkspace(
            evidence_repository=context_service.evidence_repository,
            repository=repository,
            solution_thread_repository=repository,
        )
    except Exception:
        return None


def load_default_consistency_workspace(
    context_service: ContextConnectorService | None,
) -> ConsistencyWorkspace | None:
    """Replay-migrate v12 assurance storage with isolated v13 consistency tables."""

    if context_service is None:
        return None
    try:
        root = Path(
            os.environ.get("MEETING_INTELLIGENCE_CACHE_DIR")
            or Path(os.environ["LOCALAPPDATA"]) / "MeetingIntelligenceCopilot"
        )
        repository = EncryptedAssuranceRepository(
            root / "assurance" / "assurance-v8.sqlite3",
            WindowsDpapiProtector(),
        )
        return ConsistencyWorkspace(
            evidence_repository=context_service.evidence_repository,
            repository=repository,
            claim_suggester=consistency_claim_suggester_from_env(),
            solution_thread_repository=repository,
        )
    except Exception:
        return None


def load_default_integration_workspace(
    context_service: ContextConnectorService | None,
) -> IntegrationWorkspace | None:
    """Open v9 workflow storage without making it a recording dependency."""

    if context_service is None:
        return None
    try:
        root = Path(
            os.environ.get("MEETING_INTELLIGENCE_CACHE_DIR")
            or Path(os.environ["LOCALAPPDATA"]) / "MeetingIntelligenceCopilot"
        )
        repository = EncryptedIntegrationRepository(
            root / "integrations" / "integrations-v9.sqlite3",
            WindowsDpapiProtector(),
        )
        return IntegrationWorkspace(context=context_service, repository=repository)
    except Exception:
        return None


def load_default_improvement_workspace(
    context_service: ContextConnectorService | None,
    integration_workspace: IntegrationWorkspace | None,
    assurance_workspace: AssuranceWorkspace | None,
) -> ImprovementWorkspace | None:
    """Open v11 learning storage while replay-migrating the v10 database in place."""

    if context_service is None:
        return None
    try:
        root = Path(
            os.environ.get("MEETING_INTELLIGENCE_CACHE_DIR")
            or Path(os.environ["LOCALAPPDATA"]) / "MeetingIntelligenceCopilot"
        )
        repository = EncryptedImprovementRepository(
            root / "improvements" / "improvements-v10.sqlite3",
            WindowsDpapiProtector(),
        )
        return ImprovementWorkspace(
            repository=repository,
            evidence_repository=context_service.evidence_repository,
            context_service=context_service,
            integration_workspace=integration_workspace,
            assurance_workspace=assurance_workspace,
        )
    except Exception:
        return None


_default_context_service = load_default_context_service()
_default_integration_workspace = load_default_integration_workspace(
    _default_context_service
)
_default_assurance_workspace = load_default_assurance_workspace(_default_context_service)
_default_consistency_workspace = load_default_consistency_workspace(_default_context_service)
app = create_app(
    live_registry_instance=live_registry,
    context_service_instance=_default_context_service,
    semantic_runtime_instance=load_default_semantic_runtime(),
    governance_workspace_instance=load_default_governance_workspace(),
    assurance_workspace_instance=_default_assurance_workspace,
    consistency_workspace_instance=_default_consistency_workspace,
    integration_workspace_instance=_default_integration_workspace,
    improvement_workspace_instance=load_default_improvement_workspace(
        _default_context_service,
        _default_integration_workspace,
        _default_assurance_workspace,
    ),
)
