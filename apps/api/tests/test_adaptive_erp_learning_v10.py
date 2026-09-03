from __future__ import annotations

import asyncio
import base64
import hashlib
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.adapters.evidence.artifacts import build_artifact
from app.adapters.evidence.repository import EncryptedEvidenceRepository
from app.adapters.evidence.tesseract_ocr import TesseractOcrProvider
from app.adapters.improvements.store import EncryptedImprovementRepository
from app.adapters.improvements.workspace import ImprovementWorkspace
from app.adapters.transcript.live_registry import LiveMeetingRegistry
from app.api.main import CAPABILITY_TOKEN_HEADER, create_app
from app.domain.assurance import ERPEntityMetadata, ERPFieldMetadata, ERPMetadataSnapshot
from app.domain.context import (
    ConnectorKind,
    ContextDocument,
    NetSuiteConnectorConfig,
    NetSuiteEntityMapping,
)
from app.domain.improvements import (
    DocumentClassificationReview,
    ERPGovernanceRunRequest,
    ImprovementProfileStatus,
    ImprovementProposalReview,
    ImprovementProposalStatus,
    ImprovementSettings,
    ImprovementSignal,
    ImprovementSignalKind,
)
from app.domain.integrations import DataQualityRun

TOKEN = "v10-local-capability-token-1234567890"


class XorProtector:
    def protect(self, payload: bytes) -> bytes:
        return bytes(value ^ 0xA5 for value in payload)

    def unprotect(self, payload: bytes) -> bytes:
        return bytes(value ^ 0xA5 for value in payload)


def make_workspace(tmp_path: Path) -> tuple[ImprovementWorkspace, EncryptedEvidenceRepository]:
    protector = XorProtector()
    evidence = EncryptedEvidenceRepository(tmp_path / "evidence.sqlite3", protector)
    repository = EncryptedImprovementRepository(
        tmp_path / "improvements.sqlite3", protector
    )
    return ImprovementWorkspace(repository=repository, evidence_repository=evidence), evidence


def local_document(document_id: str, title: str, content: str) -> ContextDocument:
    return ContextDocument(
        document_id=document_id,
        connector_id="local",
        source_kind=ConnectorKind.local_files,
        record_id=document_id,
        title=title,
        content=content,
        entity_type="knowledge_document",
        source_reference=title,
        allowed_principals=["local-user"],
    )


def test_remote_examples_are_rejected_and_expired_encrypted_signals_are_purged(
    tmp_path: Path,
) -> None:
    workspace, _ = make_workspace(tmp_path)
    with pytest.raises(ValidationError):
        ImprovementSignal(
            signal_id="1" * 64,
            fingerprint="2" * 64,
            kind=ImprovementSignalKind.citation_relevance,
            action="relevant",
            outcome=1,
            remote_source=True,
            local_excerpt="remote NetSuite content",
        )

    marker = "reviewed local requirement excerpt"
    signal = workspace.record_review(
        kind=ImprovementSignalKind.document_classification_review,
        target="classification-one",
        action="approve",
        outcome=1,
        attributes={"document_kind": "requirement"},
        local_excerpt=marker,
    )
    assert signal is not None
    workspace.repository.assert_no_plaintext(marker)

    expired = signal.model_copy(
        update={
            "signal_id": "3" * 64,
            "fingerprint": "4" * 64,
            "expires_at": datetime.now(UTC) - timedelta(seconds=1),
        }
    )
    workspace.repository.record_signal(expired)
    assert workspace.repository.purge_expired_signals() == 1


def test_pause_stops_collection_without_deleting_history(tmp_path: Path) -> None:
    workspace, _ = make_workspace(tmp_path)
    workspace.record_review(
        kind=ImprovementSignalKind.ask_now_usefulness,
        target="question-one",
        action="useful",
        outcome=1,
    )
    updated = workspace.update_settings(
        ImprovementSettings(paused=True, revision=0), expected_revision=0
    )
    assert updated.revision == 1
    assert len(workspace.repository.signals()) == 1
    assert (
        workspace.record_review(
            kind=ImprovementSignalKind.ask_now_usefulness,
            target="question-two",
            action="dismiss",
            outcome=-1,
        )
        is None
    )
    assert len(workspace.repository.signals()) == 1


def test_optional_ocr_is_bounded_and_supports_images_and_scanned_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "tesseract.exe"
    renderer = tmp_path / "pdftoppm.exe"
    executable.write_bytes(b"local executable")
    renderer.write_bytes(b"local renderer")
    image = tmp_path / "scan.png"
    image.write_bytes(b"image")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"pdf")

    def fake_run(arguments, **_kwargs):
        if "--list-langs" in arguments:
            return subprocess.CompletedProcess(arguments, 0, "eng\njpn\n", "")
        if str(arguments[0]).endswith("pdftoppm.exe"):
            Path(f"{arguments[-1]}-1.png").write_bytes(b"rendered")
            return subprocess.CompletedProcess(arguments, 0, b"", b"")
        return subprocess.CompletedProcess(arguments, 0, "受注 order text", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = TesseractOcrProvider(executable, pdf_renderer=renderer)
    assert provider.preflight() is True
    assert provider.extract(image, 6) == "受注 ord"
    assert provider.extract(pdf, 100) == "受注 order text"


def _target_for_holdout(index: int, holdout: bool) -> str:
    attempt = 0
    while True:
        target = f"citation-{index}-{attempt}"
        fingerprint = hashlib.sha256(
            f"citation_relevance\0{target}".encode()
        ).hexdigest()
        if (int(fingerprint[:8], 16) % 5 == 0) is holdout:
            return target
        attempt += 1


class SourceAuthorityContext:
    def __init__(self) -> None:
        self.deltas: dict[str, int | float] = {}

    def set_improvement_source_authority_deltas(
        self, values: dict[str, int | float]
    ) -> None:
        self.deltas = values


def test_supervised_profile_requires_evaluation_shadow_cycle_and_supports_pack(
    tmp_path: Path,
) -> None:
    initial, evidence = make_workspace(tmp_path)
    context = SourceAuthorityContext()
    workspace = ImprovementWorkspace(
        repository=initial.repository,
        evidence_repository=evidence,
        context_service=context,
    )
    for index in range(100):
        holdout = index < 20
        # The candidate must learn negative source feedback from training data;
        # the holdout negative is evaluation-only.
        negative = index == 0 or 20 <= index < 30
        workspace.record_review(
            kind=ImprovementSignalKind.citation_relevance,
            target=_target_for_holdout(index, holdout),
            action="not_relevant" if negative else "relevant",
            outcome=-1 if negative else 1,
            source_kind=(ConnectorKind.local_files if negative else ConnectorKind.netsuite),
            rank=2 if negative else 3,
            attributes={"query_group": "reviewed-query-cohort"},
            remote_source=not negative,
        )

    proposal = workspace.proposals()[0]
    evaluation = workspace.evaluate(proposal.proposal_id)
    assert evaluation.eligible is True
    assert evaluation.metrics.precision >= 0.95
    assert evaluation.metrics.ndcg_improvement >= 0.03

    evaluated = workspace.repository.proposal(proposal.proposal_id)
    assert evaluated is not None
    approved = workspace.review(
        proposal.proposal_id,
        ImprovementProposalReview(
            expected_revision=evaluated.revision, action="approve"
        ),
    )
    assert approved.status is ImprovementProposalStatus.approved_shadow
    assert workspace.profiles()[0].status is ImprovementProfileStatus.shadow
    workspace.run_shadow_cycle(proposal.proposal_id)
    workspace.run_shadow_cycle(proposal.proposal_id)
    workspace.run_shadow_cycle(proposal.proposal_id)
    assert workspace.profiles()[0].status is ImprovementProfileStatus.active
    assert context.deltas == {"local_files": -10.0, "netsuite": 10.0}

    restarted_context = SourceAuthorityContext()
    ImprovementWorkspace(
        repository=workspace.repository,
        evidence_repository=evidence,
        context_service=restarted_context,
    )
    assert restarted_context.deltas == context.deltas

    package = workspace.export_pack()
    imported_workspace, _ = make_workspace(tmp_path / "imported")
    manifest = imported_workspace.import_pack(package)
    assert manifest.compatibility_api_version == 13
    assert imported_workspace.proposals()[0].status is ImprovementProposalStatus.pending_evaluation

    tampered = bytearray(package)
    tampered[-10] ^= 0xFF
    with pytest.raises(ValueError):
        imported_workspace.import_pack(bytes(tampered))


def test_reviewed_classification_exemplars_can_pass_their_own_precision_gate(
    tmp_path: Path,
) -> None:
    workspace, _ = make_workspace(tmp_path)
    for index in range(50):
        workspace.record_review(
            kind=ImprovementSignalKind.document_classification_review,
            target=f"reviewed-classification-{index}",
            action="correct",
            outcome=1,
            attributes={"document_kind": "requirement"},
            local_excerpt=f"Reviewed requirement example {index}",
        )
    proposal = workspace.proposals()[0]
    evaluation = workspace.evaluate(proposal.proposal_id)
    assert evaluation.eligible is True
    assert evaluation.metrics.precision == 1.0


def test_document_inbox_tracks_revisions_duplicates_and_reviewed_dispositions(
    tmp_path: Path,
) -> None:
    workspace, evidence = make_workspace(tmp_path)
    for document_id, title in (("one", "O2C requirements.md"), ("two", "O2C copy.md")):
        artifact, revision, sections = build_artifact(
            local_document(
                document_id,
                title,
                "# Requirement\nOrder-to-cash production owner is the ERP team.",
            )
        )
        evidence.replace_artifact(artifact, revision, sections)

    inbox = workspace.document_inbox()
    assert len(inbox.classifications) == 2
    assert any(group.relation == "exact_duplicate" for group in inbox.duplicate_groups)
    selected = inbox.classifications[0]
    reviewed = workspace.review_classification(
        selected.classification_id,
        DocumentClassificationReview(
            expected_revision=1,
            action="correct",
            document_kind="requirement",
            labels={"process": ["order-to-cash"]},
        ),
    )
    assert reviewed.lifecycle.value == "approved"
    disposition = workspace.document_inbox().dispositions[0]
    assert disposition.routing_action == "copy"
    assert disposition.status == "proposed"


class FakeDefinition:
    connector_id = "netsuite"
    display_name = "NetSuite sandbox"

    def __init__(self, configuration: NetSuiteConnectorConfig) -> None:
        self._configuration = configuration

    def resolved_configuration(self) -> NetSuiteConnectorConfig:
        return self._configuration


class FakeContext:
    def __init__(self, configuration: NetSuiteConnectorConfig) -> None:
        self._definition = FakeDefinition(configuration)

    def connector_definition(self, connector_id: str) -> FakeDefinition:
        if connector_id != "netsuite":
            raise KeyError(connector_id)
        return self._definition

    async def erp_metadata_snapshot(self, connector_id: str) -> ERPMetadataSnapshot:
        if connector_id != "netsuite":
            raise KeyError(connector_id)
        entities = [
            ERPEntityMetadata(
                name=mapping.record_type,
                fields=[
                    ERPFieldMetadata(
                        name=field,
                        data_type="string",
                        key=field == mapping.key_field,
                    )
                    for field in dict.fromkeys(
                        [mapping.key_field, *mapping.projected_fields]
                    )
                ],
            )
            for mapping in self._definition.resolved_configuration().entity_mappings
        ]
        digest = hashlib.sha256(b"synthetic-v11-metadata").hexdigest()
        return ERPMetadataSnapshot(
            snapshot_id=digest,
            connector_id=connector_id,
            source_reference="Synthetic NetSuite metadata",
            metadata_hash=digest,
            entities=entities,
        )


class FakeIntegrations:
    async def run_data_quality(self, request) -> DataQualityRun:
        return DataQualityRun(
            run_id="quality-run",
            connector_id=request.connector_id,
            status="completed",
        )

    def findings(self) -> list:
        return []


def test_netsuite_templates_activate_only_for_validated_mappings(tmp_path: Path) -> None:
    workspace, evidence = make_workspace(tmp_path)
    records = ["customer", "salesOrder", "invoice"]
    mappings = [
        NetSuiteEntityMapping(
            record_type=record,
            key_field="internalid",
            display_field="tranid",
            searchable_fields=["tranid"],
            projected_fields=["internalid", "tranid", "lastmodifieddate"],
        )
        for record in records
    ]
    configuration = NetSuiteConnectorConfig(
        kind="netsuite",
        account_id="sandbox_1",
        client_id="client-id",
        certificate_id="certificate-id",
        entity_mappings=mappings,
    )
    governed = ImprovementWorkspace(
        repository=workspace.repository,
        evidence_repository=evidence,
        context_service=FakeContext(configuration),
        integration_workspace=FakeIntegrations(),
    )
    run = asyncio.run(
        governed.run_erp_governance(
            ERPGovernanceRunRequest(
                connector_id="netsuite", processes=["order_to_cash"]
            )
        )
    )
    assert run.status == "completed"
    assert run.active_templates == ["netsuite.order-to-cash"]
    assert run.recommendations == []


def test_v10_api_is_authenticated_and_redacts_document_storage_ids(tmp_path: Path) -> None:
    workspace, evidence = make_workspace(tmp_path)
    artifact, revision, sections = build_artifact(
        local_document("api", "Architecture.md", "# Architecture\nNetSuite interface")
    )
    evidence.replace_artifact(artifact, revision, sections)
    app = create_app(
        capability_token=TOKEN,
        improvement_workspace_instance=workspace,
    )
    with TestClient(app) as client:
        assert client.get("/documents/inbox").status_code == 401
        response = client.get(
            "/documents/inbox", headers={CAPABILITY_TOKEN_HEADER: TOKEN}
        )
        assert response.status_code == 200
        body = response.json()
        assert "artifact_id" not in body["classifications"][0]
        assert "revision_id" not in body["classifications"][0]

        settings = client.get(
            "/improvements/settings", headers={CAPABILITY_TOKEN_HEADER: TOKEN}
        )
        assert settings.status_code == 200
        stale = client.put(
            "/improvements/settings",
            headers={CAPABILITY_TOKEN_HEADER: TOKEN},
            json={
                "expected_revision": 3,
                "paused": True,
                "excerpt_retention_days": 90,
            },
        )
        assert stale.status_code == 409

        exported = client.post(
            "/improvements/packs/export", headers={CAPABILITY_TOKEN_HEADER: TOKEN}
        )
        assert exported.status_code == 200
        assert base64.b64decode(exported.json()["content_base64"]).startswith(b"PK")


def test_ask_now_feedback_is_resolved_from_authoritative_state(tmp_path: Path) -> None:
    workspace, _ = make_workspace(tmp_path)
    registry = LiveMeetingRegistry()
    app = create_app(
        capability_token=TOKEN,
        live_registry_instance=registry,
        improvement_workspace_instance=workspace,
    )
    headers = {CAPABILITY_TOKEN_HEADER: TOKEN}
    session_id = "meeting-intel-v10-feedback"
    with TestClient(app) as client:
        ingested = client.post(
            f"/ingest/live/{session_id}",
            headers=headers,
            json={
                "adapter": "meetily",
                "lang": "en",
                "payload": {
                    "text": "Every morning one person copies rows manually.",
                    "source": "Audio",
                    "sequence_id": 0,
                },
            },
        )
        assert ingested.status_code == 200
        state = registry.current_state(session_id)
        assert state is not None
        suggestion = next(value for value in state.suggestions if value.role.value == "ask_now")
        gap = next(value for value in state.gaps if value.gap_id == suggestion.gap_id)
        response = client.post(
            "/improvements/ask-now-feedback",
            headers=headers,
            json={
                "session_id": session_id,
                "suggestion_id": suggestion.suggestion_id,
                "state_version": state.version,
                "reopen_count": gap.reopen_count,
                "action": "useful",
            },
        )
        assert response.status_code == 204
    signals = workspace.repository.signals()
    assert signals[-1].kind is ImprovementSignalKind.ask_now_usefulness
    assert signals[-1].attributes["template"] == "manual_work"
    assert signals[-1].attributes["slot"] == gap.slot
