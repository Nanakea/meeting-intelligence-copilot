"""v11 trust, synthetic NetSuite, and reviewed document-operation tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from tests.support.netsuite_simulator import SyntheticSuiteTalkTransport

from app.adapters.context.netsuite import NetSuiteContextProvider
from app.adapters.evidence.artifacts import build_artifact
from app.adapters.evidence.repository import EncryptedEvidenceRepository
from app.adapters.improvements.store import EncryptedImprovementRepository
from app.adapters.improvements.workspace import ImprovementWorkspace
from app.api.main import CAPABILITY_TOKEN_HEADER, create_app
from app.domain.context import (
    ConnectorKind,
    ContextDocument,
    NetSuiteConnectorConfig,
    NetSuiteEntityMapping,
)
from app.domain.improvements import (
    DocumentClassificationReview,
    DocumentDispositionReview,
    ImprovementProfile,
    ImprovementProfileStatus,
    ImprovementProposal,
    ImprovementProposalKind,
    ImprovementSignalKind,
)
from app.domain.integrations import DocumentRoutingDestination

TOKEN = "v11-local-capability-token-1234567890"


class XorProtector:
    def protect(self, payload: bytes) -> bytes:
        return bytes(value ^ 0xA5 for value in payload)

    def unprotect(self, payload: bytes) -> bytes:
        return self.protect(payload)


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def put(self, target: str, value: str) -> None:
        self.values[target] = value

    def get(self, target: str) -> str | None:
        return self.values.get(target)

    def delete(self, target: str) -> bool:
        return self.values.pop(target, None) is not None


def workspace_at(tmp_path: Path) -> tuple[ImprovementWorkspace, EncryptedEvidenceRepository]:
    protector = XorProtector()
    evidence = EncryptedEvidenceRepository(tmp_path / "evidence.sqlite3", protector)
    repository = EncryptedImprovementRepository(tmp_path / "improvements.sqlite3", protector)
    return ImprovementWorkspace(repository=repository, evidence_repository=evidence), evidence


def target_for_partition(index: int, *, holdout: bool) -> str:
    attempt = 0
    while True:
        target = f"v11-signal-{index}-{attempt}"
        fingerprint = hashlib.sha256(
            f"citation_relevance\0{target}".encode()
        ).hexdigest()
        if (int(fingerprint[:8], 16) % 5 == 0) is holdout:
            return target
        attempt += 1


def add_ranking_signals(
    workspace: ImprovementWorkspace, *, invert_holdout: bool = False
) -> None:
    for index in range(100):
        holdout = index < 20
        negative = index == 0 or 20 <= index < 30
        outcome = -1 if negative else 1
        if holdout and invert_holdout:
            outcome *= -1
        workspace.record_review(
            kind=ImprovementSignalKind.citation_relevance,
            target=target_for_partition(index, holdout=holdout),
            action="relevant" if outcome > 0 else "not_relevant",
            outcome=outcome,
            source_kind=ConnectorKind.local_files if negative else ConnectorKind.netsuite,
            rank=2 if negative else 3,
            language="ja" if index % 2 else "en",
            attributes={
                "query_group": "synthetic-ranking",
                "process": "order_to_cash",
            },
            remote_source=not negative,
        )


def test_holdout_cannot_change_generated_profile(tmp_path: Path) -> None:
    first, _ = workspace_at(tmp_path / "first")
    second, _ = workspace_at(tmp_path / "second")
    add_ranking_signals(first)
    add_ranking_signals(second, invert_holdout=True)
    first_proposal = first.proposals()[0]
    second_proposal = second.proposals()[0]
    assert first_proposal.changes == second_proposal.changes
    assert first_proposal.fingerprint == second_proposal.fingerprint
    assert first_proposal.train_snapshot_hash == second_proposal.train_snapshot_hash
    assert first_proposal.holdout_snapshot_hash != second_proposal.holdout_snapshot_hash


def test_changed_signal_snapshot_blocks_old_evaluation(tmp_path: Path) -> None:
    workspace, _ = workspace_at(tmp_path)
    add_ranking_signals(workspace)
    proposal = workspace.proposals()[0]
    workspace.record_review(
        kind=ImprovementSignalKind.citation_relevance,
        target="late-reviewed-signal",
        action="relevant",
        outcome=1,
        source_kind=ConnectorKind.local_files,
    )
    evaluation = workspace.evaluate(proposal.proposal_id)
    assert evaluation.eligible is False
    assert "evaluation_inputs_changed" in evaluation.blocking_reasons


def test_v10_active_profile_is_preserved_but_not_applied(tmp_path: Path) -> None:
    workspace, evidence = workspace_at(tmp_path)
    proposal = ImprovementProposal(
        proposal_id="1" * 64,
        fingerprint="2" * 64,
        kind=ImprovementProposalKind.retrieval_weights,
        title="Legacy profile",
        rationale="Historical v10 profile",
        changes={"source_authority_delta": {"netsuite": 10}},
        signal_count=1,
    )
    workspace.repository.save_proposal(proposal)
    workspace.repository.save_profile(
        ImprovementProfile(
            profile_id="3" * 64,
            proposal_id=proposal.proposal_id,
            kind=proposal.kind,
            revision=1,
            status=ImprovementProfileStatus.active,
            configuration=proposal.changes,
        )
    )
    restarted = ImprovementWorkspace(
        repository=workspace.repository, evidence_repository=evidence
    )
    assert restarted.profiles()[0].status is ImprovementProfileStatus.needs_revalidation


def netsuite_configuration() -> NetSuiteConnectorConfig:
    mappings = []
    for record_type, display, fields in (
        ("customer", "entityid", ["internalid", "entityid", "lastmodifieddate"]),
        (
            "salesOrder",
            "tranid",
            ["internalid", "tranid", "currency", "subsidiary", "lastmodifieddate"],
        ),
        (
            "invoice",
            "tranid",
            ["internalid", "tranid", "currency", "subsidiary", "lastmodifieddate"],
        ),
    ):
        mappings.append(
            NetSuiteEntityMapping(
                record_type=record_type,
                key_field="internalid",
                display_field=display,
                searchable_fields=[display],
                projected_fields=fields,
            )
        )
    return NetSuiteConnectorConfig(
        kind="netsuite",
        account_id="synthetic_1",
        client_id="synthetic-client",
        certificate_id="synthetic-certificate",
        entity_mappings=mappings,
    )


def netsuite_provider(
    mode: str = "ready",
) -> tuple[NetSuiteContextProvider, SyntheticSuiteTalkTransport]:
    key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    secrets = MemorySecrets()
    secrets.put("synthetic-secret", json.dumps({"private_key_pem": pem}))
    transport = SyntheticSuiteTalkTransport(mode=mode)
    return (
        NetSuiteContextProvider(
            connector_id="netsuite",
            display_name="Synthetic NetSuite",
            principal_id="local-user",
            configuration=netsuite_configuration(),
            credential_target="synthetic-secret",
            secret_store=secrets,
            transport=transport,
        ),
        transport,
    )


def test_synthetic_suitetalk_metadata_is_read_only_and_bounded() -> None:
    provider, transport = netsuite_provider()
    snapshot = asyncio.run(provider.metadata_snapshot())
    assert snapshot.metadata_hash
    assert {entity.name for entity in snapshot.entities} == {
        "customer",
        "salesOrder",
        "invoice",
    }
    assert transport.operational_writes == 0
    with pytest.raises(AssertionError):
        transport.request_json(
            "https://synthetic-1.suitetalk.api.netsuite.com/services/rest/record/v1/customer",
            method="PATCH",
            headers={},
            body=b"{}",
        )


@pytest.mark.parametrize("mode", ["expired_certificate", "revoked_role"])
def test_synthetic_suitetalk_rejects_invalid_integration_identity(mode: str) -> None:
    provider, _ = netsuite_provider(mode)
    health = asyncio.run(provider.health())
    assert health.phase.value == "unavailable"
    assert health.detail_code == "netsuite_unavailable"


def test_synthetic_suitetalk_recovers_after_throttling() -> None:
    provider, _ = netsuite_provider("throttled")
    with pytest.raises(OSError):
        asyncio.run(provider.metadata_snapshot())
    assert asyncio.run(provider.metadata_snapshot()).entities


def test_synthetic_suitetalk_handles_malformed_metadata_and_pagination() -> None:
    malformed, _ = netsuite_provider("malformed")
    assert all(not entity.fields for entity in asyncio.run(malformed.metadata_snapshot()).entities)

    provider, transport = netsuite_provider()
    transport.records["customer"] = [
        {
            "internalid": f"C-{index:03d}",
            "entityid": f"SYNTH-{index:03d}",
            "lastmodifieddate": "2026-01-01T00:00:00Z",
        }
        for index in range(401)
    ]
    records = asyncio.run(provider.quality_records())
    assert len(records["customer"]) == 401
    suiteql_paths = [path for method, path in transport.requests if path.endswith("/suiteql")]
    assert len(suiteql_paths) >= 5


def test_synthetic_suitetalk_marks_ambiguous_review_delivery() -> None:
    transport = SyntheticSuiteTalkTransport(mode="ambiguous_delivery")
    with pytest.raises(TimeoutError):
        transport.request_json(
            "https://synthetic-1.suitetalk.api.netsuite.com/services/rest/record/v1/"
            "customrecord_solution_review",
            method="POST",
            headers={},
            body=b"{}",
        )
    assert transport.review_writes == 0


class Definition:
    connector_id = "netsuite"
    display_name = "Synthetic NetSuite"

    def resolved_configuration(self) -> NetSuiteConnectorConfig:
        return netsuite_configuration()


class MappingContext:
    def __init__(self, provider: NetSuiteContextProvider) -> None:
        self.provider = provider

    def connector_definition(self, connector_id: str) -> Definition:
        if connector_id != "netsuite":
            raise KeyError(connector_id)
        return Definition()

    async def erp_metadata_snapshot(self, connector_id: str):
        assert connector_id == "netsuite"
        return await self.provider.metadata_snapshot()


def test_mapping_validation_records_hash_only_baseline(tmp_path: Path) -> None:
    workspace, evidence = workspace_at(tmp_path)
    provider, _ = netsuite_provider()
    governed = ImprovementWorkspace(
        repository=workspace.repository,
        evidence_repository=evidence,
        context_service=MappingContext(provider),
    )
    result = asyncio.run(governed.validate_netsuite_mapping("netsuite"))
    assert result.status == "valid"
    assert result.active_templates == ["netsuite.order-to-cash"]
    assert result.persisted_remote_records == 0
    baseline = governed.repository.metadata_baseline("netsuite")
    assert baseline is not None and baseline.status == "initial"


class ScanContext:
    async def prepare_assurance_sources(self, connector_ids: list[str]):
        return [], [value for value in connector_ids if value == "offline"]

    def ocr_status(self) -> str:
        return "ready"

    def set_improvement_source_authority_deltas(self, _values) -> None:
        return None

    def set_improvement_profiles(self, _values) -> None:
        return None


def test_document_scan_reports_degraded_without_interrupting_index(tmp_path: Path) -> None:
    workspace, evidence = workspace_at(tmp_path)
    scanning = ImprovementWorkspace(
        repository=workspace.repository,
        evidence_repository=evidence,
        context_service=ScanContext(),
    )
    status = asyncio.run(scanning.scan_documents(["local", "offline"]))
    assert status.phase.value == "degraded"
    assert status.failed_sources == 1
    assert status.ocr_status == "ready"


class FakeRouting:
    def __init__(self) -> None:
        self.saved = []

    def routing_destinations(self):
        return [
            DocumentRoutingDestination(
                destination_ref="approved-root",
                label="Approved root",
                partner_references=["requirement"],
            )
        ]

    def save_routing_proposal(self, proposal):
        self.saved.append(proposal)

    async def review_routing(self, proposal_id, review):
        proposal = self.saved[-1]
        assert proposal.proposal_id == proposal_id
        assert review.action == "execute"
        return proposal.model_copy(update={"status": "completed", "revision": 1})

    def set_improvement_profiles(self, _values) -> None:
        return None


def test_document_operation_requires_separate_review_then_execute(tmp_path: Path) -> None:
    workspace, evidence = workspace_at(tmp_path)
    document = ContextDocument(
        document_id="local-document",
        connector_id="local",
        source_kind=ConnectorKind.local_files,
        record_id="local-document",
        title="Requirement.md",
        content="# Requirement\nSynthetic reviewed requirement",
        entity_type="knowledge_document",
        source_reference="Requirement.md",
        allowed_principals=["local-user"],
    )
    artifact, revision, sections = build_artifact(document)
    evidence.replace_artifact(artifact, revision, sections)
    routed = ImprovementWorkspace(
        repository=workspace.repository,
        evidence_repository=evidence,
        integration_workspace=FakeRouting(),
    )
    classification = routed.document_inbox().classifications[0]
    routed.review_classification(
        classification.classification_id,
        DocumentClassificationReview(
            expected_revision=1,
            action="correct",
            document_kind="requirement",
        ),
    )
    disposition = routed.document_inbox().dispositions[0]
    with pytest.raises(PermissionError):
        asyncio.run(
            routed.execute_disposition(
                disposition.disposition_id,
                expected_revision=1,
                destination_ref="approved-root",
                routing_action="copy",
            )
        )
    reviewed = routed.review_disposition(
        disposition.disposition_id,
        DocumentDispositionReview(expected_revision=1, action="approve"),
    )
    result = asyncio.run(
        routed.execute_disposition(
            reviewed.disposition_id,
            expected_revision=reviewed.revision,
            destination_ref="approved-root",
            routing_action="copy",
        )
    )
    assert result.status == "completed"
    assert result.action == "copy"


def test_v11_shadow_and_document_status_routes_are_authenticated(tmp_path: Path) -> None:
    workspace, _ = workspace_at(tmp_path)
    add_ranking_signals(workspace)
    proposal = workspace.proposals()[0]
    app = create_app(capability_token=TOKEN, improvement_workspace_instance=workspace)
    headers = {CAPABILITY_TOKEN_HEADER: TOKEN}
    with TestClient(app) as client:
        assert client.get("/documents/scan/status").status_code == 401
        evaluation = client.post(
            f"/improvements/proposals/{proposal.proposal_id}/evaluate",
            headers=headers,
        )
        assert evaluation.status_code == 200
        assert evaluation.json()["eligible"] is True
        current = workspace.repository.proposal(proposal.proposal_id)
        assert current is not None
        approved = client.post(
            f"/improvements/proposals/{proposal.proposal_id}/review",
            headers=headers,
            json={"expected_revision": current.revision, "action": "approve"},
        )
        assert approved.status_code == 200
        for cycle in range(1, 4):
            response = client.post(
                f"/improvements/proposals/{proposal.proposal_id}/shadow-runs",
                headers=headers,
            )
            assert response.status_code == 200
            assert response.json()["cycle"] == cycle
        assert workspace.profiles()[0].status is ImprovementProfileStatus.active
        status = client.get("/documents/scan/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["phase"] == "idle"
