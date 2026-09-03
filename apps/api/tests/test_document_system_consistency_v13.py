from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.adapters.analysis.ollama_consistency import OllamaConsistencyClaimSuggester
from app.adapters.assurance.consistency_workspace import ConsistencyWorkspace
from app.adapters.assurance.store import EncryptedAssuranceRepository
from app.adapters.evidence.artifacts import build_artifact
from app.adapters.evidence.repository import EncryptedEvidenceRepository
from app.api.main import CAPABILITY_TOKEN_HEADER, create_app
from app.domain.assurance import ERPEntityMetadata, ERPFieldMetadata, ERPMetadataSnapshot
from app.domain.consistency import (
    AuthorityPolicy,
    AuthoritySide,
    ConsistencyAttribution,
    ConsistencyCitation,
    ConsistencyFindingReviewRequest,
    ConsistencyFindingStatus,
    ConsistencyMismatchKind,
    ConsistencyRunRequest,
    DocumentClaim,
    ObservationPersistence,
    ObservedSystemFact,
)
from app.domain.context import ConnectorKind, ContextDocument
from app.domain.evidence import ArtifactLocator, DocumentKind
from app.domain.solution_thread import SolutionNodeKind, SolutionThreadNode
from app.services.consistency import (
    compare_claims_to_observations,
    extract_document_claims,
    facts_from_erp_snapshot,
)

TOKEN = "v13-document-system-consistency-token"


class XorProtector:
    def protect(self, payload: bytes) -> bytes:
        return bytes(value ^ 0xA5 for value in payload)

    def unprotect(self, payload: bytes) -> bytes:
        return bytes(value ^ 0xA5 for value in payload)


class StaticTransport:
    def __init__(self, response: dict) -> None:
        self.response = response

    def post_json(self, url: str, payload: dict, timeout: float) -> dict:
        assert url == "http://127.0.0.1:11434/api/generate"
        assert timeout == 5.0
        assert "section_id" in payload["prompt"]
        return self.response


def context_document(
    *,
    connector_id: str,
    source_kind: ConnectorKind,
    document_id: str,
    title: str,
    content: str,
    document_kind: DocumentKind = DocumentKind.interface,
) -> ContextDocument:
    return ContextDocument(
        document_id=document_id,
        connector_id=connector_id,
        source_kind=source_kind,
        record_id=document_id,
        title=title,
        content=content,
        entity_type=document_kind.value,
        source_reference=title,
        allowed_principals=["local-current-user"],
    )


def netsuite_snapshot(*, data_type: str = "Integer", required: bool = False) -> ERPMetadataSnapshot:
    return ERPMetadataSnapshot(
        snapshot_id="a" * 64,
        connector_id="netsuite",
        source_reference="NetSuite metadata catalog",
        api_version="2026.2",
        environment="production",
        metadata_hash="b" * 64,
        entities=[
            ERPEntityMetadata(
                name="Customer",
                fields=[
                    ERPFieldMetadata(
                        name="email",
                        data_type=data_type,
                        required=required,
                        key=False,
                    )
                ],
            )
        ],
    )


def extracted_claims(content: str, *, language: str = "en") -> list[DocumentClaim]:
    artifact, _, sections = build_artifact(
        context_document(
            connector_id="docs",
            source_kind=ConnectorKind.local_files,
            document_id="design",
            title="Approved integration design",
            content=content,
        )
    )
    return extract_document_claims(
        artifacts=[artifact],
        sections=sections,
        connector_labels={"docs": "Approved documents"},
        glossary_entries=[],
        language=language,
    )


def test_exact_two_sided_metadata_mismatches_are_neutral_by_default() -> None:
    claims = extracted_claims(
        "# Mapping\nsystem: NetSuite\nenvironment: production\n"
        "Customer.email type: String\nCustomer.email required"
    )
    observations = facts_from_erp_snapshot(
        netsuite_snapshot(),
        connector_kind=ConnectorKind.netsuite,
        connector_label="NetSuite",
        glossary_entries=[],
    )
    findings = compare_claims_to_observations(
        run_id="c" * 64,
        claims=claims,
        observations=observations,
        policies=[],
        exceptions=[],
        observed_scope_citations=[observations[0].citation],
        detect_undocumented=False,
    )
    kinds = {value.mismatch_kind for value in findings}
    assert ConsistencyMismatchKind.type_mismatch in kinds
    assert ConsistencyMismatchKind.requiredness_mismatch in kinds
    assert all(
        value.comparison.attribution is ConsistencyAttribution.neutral
        for value in findings
    )
    assert all(value.document_claim.citation.source_revision for value in findings)
    assert all(value.observed_fact.citation.source_revision for value in findings)


def test_reviewed_authority_policy_attributes_documentation_drift() -> None:
    claims = extracted_claims("Customer.email type: String")
    observations = facts_from_erp_snapshot(
        netsuite_snapshot(),
        connector_kind=ConnectorKind.netsuite,
        connector_label="NetSuite",
        glossary_entries=[],
    )
    findings = compare_claims_to_observations(
        run_id="d" * 64,
        claims=claims,
        observations=observations,
        policies=[
            AuthorityPolicy(
                policy_id="netsuite-metadata",
                label="NetSuite metadata is authoritative",
                subject_pattern="customer",
                property_pattern="field.*",
                authority=AuthoritySide.observed_system,
            )
        ],
        exceptions=[],
        observed_scope_citations=[observations[0].citation],
        detect_undocumented=False,
    )
    mismatch = next(
        value
        for value in findings
        if value.mismatch_kind is ConsistencyMismatchKind.type_mismatch
    )
    assert mismatch.comparison.attribution is ConsistencyAttribution.documentation_drift
    assert mismatch.comparison.authority_policy_id == "netsuite-metadata"


def test_missing_field_environment_drift_and_undocumented_system_values() -> None:
    claims = extracted_claims(
        "system: NetSuite\nenvironment: staging\nschema complete: Customer\n"
        "Customer.legacyCode type: String"
    )
    observations = facts_from_erp_snapshot(
        netsuite_snapshot(),
        connector_kind=ConnectorKind.netsuite,
        connector_label="NetSuite",
        glossary_entries=[],
    )
    findings = compare_claims_to_observations(
        run_id="e" * 64,
        claims=claims,
        observations=observations,
        policies=[],
        exceptions=[],
        observed_scope_citations=[observations[0].citation],
    )
    kinds = {value.mismatch_kind for value in findings}
    assert ConsistencyMismatchKind.environment_drift in kinds
    assert ConsistencyMismatchKind.missing_field in kinds
    assert ConsistencyMismatchKind.undocumented_implementation in kinds


def test_semantic_document_claims_require_literal_span_and_review() -> None:
    artifact, _, sections = build_artifact(
        context_document(
            connector_id="docs",
            source_kind=ConnectorKind.local_files,
            document_id="semantic",
            title="Narrative design",
            content="The production order API must return within 300 ms.",
        )
    )
    valid = {
        "section_id": sections[0].section_id,
        "canonical_subject": "order api",
        "canonical_property": "latency_target",
        "exact_value": "300 ms",
        "value_type": "duration",
        "environment": "production",
        "confidence": 0.97,
    }
    transport = StaticTransport(
        {"response": json.dumps({"claims": [valid, {**valid, "exact_value": "20 ms"}]})}
    )
    proposals = OllamaConsistencyClaimSuggester(transport=transport).suggest(
        [artifact], sections, {"docs": "Approved documents"}, "en"
    )
    assert len(proposals) == 1
    assert proposals[0].status.value == "proposed"
    assert proposals[0].origin.value == "semantic_suggested"
    assert compare_claims_to_observations(
        run_id="f" * 64,
        claims=proposals,
        observations=[],
        policies=[],
        exceptions=[],
        observed_scope_citations=[],
    ) == []


def corpus_claim(index: int, *, language: str) -> DocumentClaim:
    value = f"type-{index % 11}"
    citation = ConsistencyCitation(
        connector_id="docs",
        source_kind=ConnectorKind.local_files,
        source_label="Approved documents",
        source_reference=f"design-{index}.md",
        source_revision=hashlib.sha256(f"doc-{index}".encode()).hexdigest(),
        locator=ArtifactLocator(start_line=index + 1, end_line=index + 1),
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        freshness="current",
    )
    fingerprint = hashlib.sha256(f"claim-{index}".encode()).hexdigest()
    return DocumentClaim(
        claim_id=hashlib.sha256(f"claim-revision-{index}".encode()).hexdigest(),
        fingerprint=fingerprint,
        canonical_subject=f"entity-{index}",
        canonical_property="field.value.type",
        expected_value=value,
        expected_value_sha256=hashlib.sha256(value.encode()).hexdigest(),
        value_type="type",
        language=language,
        document_kind=DocumentKind.interface,
        citation=citation,
    )


def corpus_fact(claim: DocumentClaim, index: int, *, mismatched: bool) -> ObservedSystemFact:
    value = "different-type" if mismatched else claim.expected_value
    assert value is not None
    return ObservedSystemFact(
        fact_id=hashlib.sha256(f"fact-{index}".encode()).hexdigest(),
        canonical_subject=claim.canonical_subject,
        canonical_property=claim.canonical_property,
        observed_value=value,
        observed_value_sha256=hashlib.sha256(value.encode()).hexdigest(),
        value_type="type",
        persistence=ObservationPersistence.metadata_safe,
        scope_complete=True,
        citation=ConsistencyCitation(
            connector_id="system",
            source_kind=ConnectorKind.openapi,
            source_label="Observed API",
            source_reference=f"openapi/service-{index}",
            source_revision=hashlib.sha256(f"system-{index}".encode()).hexdigest(),
            retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
            freshness="current",
        ),
    )


def test_fixed_500_case_bilingual_corpus_with_30_percent_hard_negatives() -> None:
    findings = []
    for index in range(500):
        claim = corpus_claim(index, language="ja" if index % 2 else "en")
        # The final 150 cases are exact matches and must never be flagged.
        fact = corpus_fact(claim, index, mismatched=index < 350)
        findings.extend(
            compare_claims_to_observations(
                run_id=hashlib.sha256(f"run-{index}".encode()).hexdigest(),
                claims=[claim],
                observations=[fact],
                policies=[],
                exceptions=[],
                observed_scope_citations=[fact.citation],
                detect_undocumented=False,
            )
        )
    assert len(findings) == 350
    assert all(value.mismatch_kind is ConsistencyMismatchKind.type_mismatch for value in findings)
    assert all(value.document_claim.citation and value.observed_fact.citation for value in findings)


def test_encrypted_review_exception_and_stale_revision(tmp_path: Path) -> None:
    protector = XorProtector()
    repository = EncryptedAssuranceRepository(tmp_path / "assurance.sqlite3", protector)
    evidence = EncryptedEvidenceRepository(tmp_path / "evidence.sqlite3", protector)
    workspace = ConsistencyWorkspace(evidence_repository=evidence, repository=repository)
    claims = extracted_claims("Customer.email type: String")
    observations = facts_from_erp_snapshot(
        netsuite_snapshot(),
        connector_kind=ConnectorKind.netsuite,
        connector_label="NetSuite",
        glossary_entries=[],
    )
    finding = compare_claims_to_observations(
        run_id="1" * 64,
        claims=claims,
        observations=observations,
        policies=[],
        exceptions=[],
        observed_scope_citations=[observations[0].citation],
        detect_undocumented=False,
    )[0]
    repository.save_consistency_findings([finding])
    request = ConsistencyFindingReviewRequest(
        expected_revision=1,
        action="mark_exception",
        exception_reason="Approved transition window",
        exception_expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    updated = workspace.review_finding(finding.finding_id, request)
    assert updated.status is ConsistencyFindingStatus.approved_exception
    with pytest.raises(RuntimeError, match="stale"):
        workspace.review_finding(finding.finding_id, request)
    repository.assert_no_plaintext("Approved transition window")


class FakeContextService:
    def __init__(self, snapshot: ERPMetadataSnapshot) -> None:
        self.snapshot = snapshot
        self.prepare_calls = 0

    def connector_definition(self, connector_id: str):
        values = {
            "docs": SimpleNamespace(
                display_name="Approved documents", kind=ConnectorKind.local_files
            ),
            "netsuite": SimpleNamespace(display_name="NetSuite", kind=ConnectorKind.netsuite),
        }
        if connector_id not in values:
            raise KeyError(connector_id)
        return values[connector_id]

    async def prepare_consistency_sources(self, document_ids, observed_ids):
        assert document_ids == ["docs"]
        assert observed_ids == ["netsuite"]
        self.prepare_calls += 1
        return [], [self.snapshot], [], set()

    def entity_glossary_with_mappings(self):
        return []

    async def close(self) -> None:
        return None


def test_authenticated_consistency_api_run_review_policy_and_export(tmp_path: Path) -> None:
    protector = XorProtector()
    evidence = EncryptedEvidenceRepository(tmp_path / "evidence.sqlite3", protector)
    repository = EncryptedAssuranceRepository(tmp_path / "assurance.sqlite3", protector)
    artifact, revision, sections = build_artifact(
        context_document(
            connector_id="docs",
            source_kind=ConnectorKind.local_files,
            document_id="api-design",
            title="Approved API design",
            content="Customer.email type: String",
        )
    )
    evidence.replace_artifact(artifact, revision, sections)
    workspace = ConsistencyWorkspace(evidence_repository=evidence, repository=repository)
    context_service = FakeContextService(netsuite_snapshot())
    client = TestClient(
        create_app(
            capability_token=TOKEN,
            context_service_instance=context_service,  # type: ignore[arg-type]
            consistency_workspace_instance=workspace,
        )
    )
    headers = {CAPABILITY_TOKEN_HEADER: TOKEN}
    unauthenticated = client.post(
        "/consistency/runs",
        json={
            "document_connector_ids": ["docs"],
            "observed_connector_ids": ["netsuite"],
        },
    )
    assert unauthenticated.status_code == 401
    run = client.post(
        "/consistency/runs",
        headers=headers,
        json={
            "document_connector_ids": ["docs"],
            "observed_connector_ids": ["netsuite"],
            "detect_undocumented": False,
        },
    )
    assert run.status_code == 200
    assert run.json()["status"] == "completed"
    findings = client.get("/consistency/findings", headers=headers).json()
    assert findings
    finding = findings[0]
    reviewed = client.post(
        f"/consistency/findings/{finding['finding_id']}/review",
        headers=headers,
        json={"expected_revision": 1, "action": "confirm"},
    )
    assert reviewed.status_code == 200
    exported = client.post(
        f"/consistency/findings/{finding['finding_id']}/export",
        headers=headers,
        json={"format": "markdown", "draft_kind": "issue"},
    )
    assert exported.status_code == 200
    assert exported.json()["direct_submission_enabled"] is False
    assigned = client.post(
        f"/consistency/findings/{finding['finding_id']}/review",
        headers=headers,
        json={
            "expected_revision": reviewed.json()["revision"],
            "action": "assign",
            "owner_role": "ERP platform team",
        },
    )
    assert assigned.status_code == 200
    assert assigned.json()["owner_role"] == "ERP platform team"
    refreshed = client.post(
        f"/consistency/findings/{finding['finding_id']}/review",
        headers=headers,
        json={
            "expected_revision": assigned.json()["revision"],
            "action": "request_refresh",
        },
    )
    assert refreshed.status_code == 200
    assert context_service.prepare_calls == 2
    filtered = client.get(
        "/consistency/findings",
        headers=headers,
        params={"document": "Approved API design", "owner_role": "ERP platform team"},
    )
    assert filtered.status_code == 200
    assert len(filtered.json()) == 1
    policy = client.put(
        "/consistency/authority-policies",
        headers=headers,
        json={
            "policy_id": "netsuite-authority",
            "label": "NetSuite metadata",
            "subject_pattern": "customer",
            "property_pattern": "field.*",
            "authority": "observed_system",
        },
    )
    assert policy.status_code == 200
    assert client.get("/consistency/dashboard", headers=headers).status_code == 200


def test_changed_only_reuses_source_result_and_preserves_confirmed_thread_node(
    tmp_path: Path,
) -> None:
    protector = XorProtector()
    evidence = EncryptedEvidenceRepository(tmp_path / "evidence.sqlite3", protector)
    repository = EncryptedAssuranceRepository(tmp_path / "assurance.sqlite3", protector)
    artifact, revision, sections = build_artifact(
        context_document(
            connector_id="docs",
            source_kind=ConnectorKind.local_files,
            document_id="incremental-design",
            title="Approved design",
            content="Customer.email type: String",
        )
    )
    evidence.replace_artifact(artifact, revision, sections)
    workspace = ConsistencyWorkspace(
        evidence_repository=evidence,
        repository=repository,
        solution_thread_repository=repository,
    )
    request = ConsistencyRunRequest(
        document_connector_ids=["docs"],
        observed_connector_ids=["netsuite"],
        changed_only=True,
        detect_undocumented=False,
    )

    async def execute():
        return await workspace.run(
            request,
            connector_labels={"docs": "Approved documents", "netsuite": "NetSuite"},
            connector_kinds={
                "docs": ConnectorKind.local_files,
                "netsuite": ConnectorKind.netsuite,
            },
            glossary_entries=[],
            erp_snapshots=[netsuite_snapshot()],
            transient_documents=[],
            leased_connector_ids=set(),
            unavailable_connector_ids=[],
        )

    first = asyncio.run(execute())
    assert first.finding_count == 1
    assert first.source_fingerprint
    second = asyncio.run(execute())
    assert second.detail_code == "unchanged_sources"
    assert second.reused_run_id == first.run_id
    assert second.finding_count == first.finding_count

    document_node = next(
        node for node in repository.nodes() if node.kind is SolutionNodeKind.document
    )
    repository.save_node(document_node.model_copy(update={"confirmed": True}))
    repository.save_node(
        SolutionThreadNode(
            node_id=document_node.node_id,
            kind=document_node.kind,
            label="A later scan label",
            source_reference=document_node.source_reference,
            source_revision=document_node.source_revision,
        )
    )
    preserved = next(
        node for node in repository.nodes() if node.node_id == document_node.node_id
    )
    assert preserved.confirmed is True
    assert preserved.label == document_node.label


def test_version_properties_are_drift_not_ambiguous_scope() -> None:
    claims = extracted_claims("system: NetSuite\nAPI version: 2026.1")
    observations = facts_from_erp_snapshot(
        netsuite_snapshot(),
        connector_kind=ConnectorKind.netsuite,
        connector_label="NetSuite",
        glossary_entries=[],
    )
    finding = next(
        value
        for value in compare_claims_to_observations(
            run_id="f" * 64,
            claims=claims,
            observations=observations,
            policies=[],
            exceptions=[],
            observed_scope_citations=[observations[0].citation],
            detect_undocumented=False,
        )
        if value.property_name == "api_version"
    )
    assert finding.mismatch_kind is ConsistencyMismatchKind.api_version_drift
