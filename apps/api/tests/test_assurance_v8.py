from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app.adapters.assurance.rule_packs import signing_key, verify_rule_pack
from app.adapters.assurance.store import EncryptedAssuranceRepository
from app.adapters.assurance.workspace import AssuranceWorkspace
from app.adapters.context.encrypted_index import EncryptedContextIndex
from app.adapters.context.local_files import LocalFilesContextProvider
from app.adapters.context.remote import _parse_odata_metadata
from app.adapters.evidence.artifacts import build_artifact
from app.adapters.evidence.remote_documents import parse_remote_document
from app.adapters.evidence.repository import EncryptedEvidenceRepository
from app.adapters.evidence.retrieval import LocalHybridRetrievalEngine
from app.api.main import CAPABILITY_TOKEN_HEADER, create_app
from app.domain.assurance import (
    AssuranceRunRequest,
    ERPEntityMetadata,
    ERPFieldMetadata,
    ERPMetadataSnapshot,
    FindingReviewRequest,
)
from app.domain.context import ConnectorKind, ContextDocument
from app.domain.evidence import EmbeddingProfile, RetrievalRequest
from app.domain.solution_thread import (
    SolutionEdgeKind,
    SolutionMindMapRequest,
    SolutionNodeKind,
    SolutionThreadEdge,
    SolutionThreadEdgeReviewRequest,
    SolutionThreadImpactRequest,
    SolutionThreadNode,
)


class XorProtector:
    def protect(self, payload: bytes) -> bytes:
        return bytes(value ^ 0xA5 for value in payload)

    def unprotect(self, payload: bytes) -> bytes:
        return bytes(value ^ 0xA5 for value in payload)


def document(
    *,
    document_id: str,
    title: str,
    content: str,
    connector_id: str = "local",
) -> ContextDocument:
    return ContextDocument(
        document_id=document_id,
        connector_id=connector_id,
        source_kind=ConnectorKind.local_files,
        record_id=document_id,
        title=title,
        content=content,
        entity_type="knowledge_document",
        source_reference=title,
        allowed_principals=["alice"],
    )


def workspace(tmp_path: Path) -> tuple[AssuranceWorkspace, EncryptedEvidenceRepository]:
    protector = XorProtector()
    evidence = EncryptedEvidenceRepository(tmp_path / "evidence.sqlite3", protector)
    assurance = EncryptedAssuranceRepository(tmp_path / "assurance.sqlite3", protector)
    return (
        AssuranceWorkspace(
            evidence_repository=evidence,
            repository=assurance,
            solution_thread_repository=assurance,
        ),
        evidence,
    )


def test_revisioned_encrypted_artifacts_and_bilingual_fts_retrieval(tmp_path: Path) -> None:
    evidence = EncryptedEvidenceRepository(tmp_path / "evidence.sqlite3", XorProtector())
    documents = [
        document(
            document_id="orders",
            title="Order integration design",
            content="# Interface\nSAP sends customer orders to Dynamics every five minutes.",
        ),
        document(
            document_id="security",
            title="セキュリティ非機能要件",
            content="# 暗号化\n顧客データは保存時に暗号化する。応答時間は2秒以内。",
        ),
    ]
    for value in documents:
        artifact, revision, sections = build_artifact(value)
        assert evidence.replace_artifact(artifact, revision, sections) is True
        assert evidence.replace_artifact(artifact, revision, sections) is False

    engine = LocalHybridRetrievalEngine(evidence, source_labels={"local": "Design library"})
    english = asyncio.run(
        engine.retrieve(RetrievalRequest(text="SAP customer orders", principal_id="alice"))
    )
    japanese = asyncio.run(
        engine.retrieve(RetrievalRequest(text="顧客データ 暗号化", principal_id="alice"))
    )
    assert english.citations[0].source_reference == "Order integration design"
    assert japanese.citations[0].source_reference == "セキュリティ非機能要件"
    assert english.lexical_only is True
    evidence.assert_no_plaintext("SAP sends customer orders")


def test_assurance_run_is_incremental_and_findings_require_review(tmp_path: Path) -> None:
    assurance, evidence = workspace(tmp_path)
    artifact, revision, sections = build_artifact(
        document(
            document_id="requirements",
            title="Requirements specification",
            content="# Requirement\nOwner: Platform team\nThe acceptance remains TBD.",
        )
    )
    evidence.replace_artifact(artifact, revision, sections)

    first = asyncio.run(assurance.run(AssuranceRunRequest(connector_ids=["local"])))
    assert first.status.value == "completed"
    assert first.changed_revision_count == 1
    findings = assurance.findings()
    assert {finding.rule_id for finding in findings} >= {
        "builtin.unresolved-placeholder",
        "builtin.requirement-acceptance",
    }
    assert all(finding.status.value == "open" for finding in findings)

    second = asyncio.run(assurance.run(AssuranceRunRequest(connector_ids=["local"])))
    assert second.changed_revision_count == 0
    selected = findings[0]
    reviewed = assurance.review(
        selected.finding_id,
        FindingReviewRequest(expected_revision=1, action="confirm"),
    )
    assert reviewed.status.value == "confirmed"
    assert reviewed.revision == 2
    try:
        assurance.review(
            selected.finding_id,
            FindingReviewRequest(expected_revision=1, action="dismiss"),
        )
    except RuntimeError as error:
        assert str(error) == "stale_finding"
    else:
        raise AssertionError("stale assurance review was accepted")


def test_signed_company_rule_pack_requires_enrolled_key() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key = signing_key(
        key_id="company-architecture",
        label="Company Architecture Office",
        public_key_base64=base64.b64encode(public_bytes).decode(),
    )
    rules = [
        {
            "rule_id": "company.required-owner",
            "rule_type": "required_owner",
            "title": "Owner required",
            "description": "Company documents require an owner.",
            "applies_to": ["architecture"],
            "severity": "high",
        }
    ]
    rules_bytes = json.dumps(rules, separators=(",", ":")).encode()
    manifest = {
        "pack_id": "company-architecture",
        "version": "1.0.0",
        "issuer": "Architecture Office",
        "key_id": key.key_id,
        "rules_sha256": hashlib.sha256(rules_bytes).hexdigest(),
    }
    manifest_bytes = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode()
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("rules.json", rules_bytes)
        archive.writestr(
            "signature.ed25519",
            base64.b64encode(private_key.sign(manifest_bytes)),
        )
    verified = verify_rule_pack(package.getvalue(), [key])
    assert verified.trusted is True
    assert verified.pack_id == "company-architecture"


def test_odata_metadata_is_bounded_and_detects_document_mismatch(tmp_path: Path) -> None:
    xml = b"""<?xml version="1.0"?>
    <edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
      <edmx:DataServices>
        <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="Demo">
          <EntityType Name="Account"><Key><PropertyRef Name="accountid"/></Key>
            <Property Name="accountid" Type="Edm.Guid" Nullable="false"/>
            <Property Name="name" Type="Edm.String"/>
          </EntityType>
          <EntityContainer Name="Default"><EntitySet Name="accounts" EntityType="Demo.Account"/>
          </EntityContainer>
        </Schema>
      </edmx:DataServices>
    </edmx:Edmx>"""
    snapshot = _parse_odata_metadata(
        xml,
        connector_id="dynamics",
        source_reference="example.crm.dynamics.com",
        allowed_entities={"accounts"},
    )
    assert snapshot.entities[0].fields[0].key is True

    assurance, evidence = workspace(tmp_path)
    artifact, revision, sections = build_artifact(
        document(
            document_id="mapping",
            title="Data mapping",
            content="Map accounts.missing_field to the warehouse record.",
        )
    )
    evidence.replace_artifact(artifact, revision, sections)
    result = assurance.validate_erp(snapshot, [artifact.artifact_id])
    assert result.persisted_remote_records == 0
    assert result.findings[0].rule_id == "builtin.erp-metadata"


def test_solution_thread_impact_uses_confirmed_edges_only(tmp_path: Path) -> None:
    assurance, _ = workspace(tmp_path)
    repository = assurance._thread  # Test the repository seam used by the workspace.
    nodes = [
        SolutionThreadNode(
            node_id=hashlib.sha256(label.encode()).hexdigest(),
            kind=SolutionNodeKind.system,
            label=label,
            source_reference=label,
            confirmed=True,
        )
        for label in ("ERP", "CRM", "Portal")
    ]
    for node in nodes:
        repository.save_node(node)
    for index, confirmed in enumerate((True, False)):
        repository.save_edge(
            SolutionThreadEdge(
                edge_id=hashlib.sha256(f"edge-{index}".encode()).hexdigest(),
                source_node_id=nodes[index].node_id,
                target_node_id=nodes[index + 1].node_id,
                relation=SolutionEdgeKind.depends_on,
                confirmed=confirmed,
            )
        )
    impact = assurance.impact(
        SolutionThreadImpactRequest(root_node_id=nodes[0].node_id, depth=2)
    )
    assert {node.label for node in impact.nodes} == {"ERP", "CRM"}


def test_solution_mind_map_is_bounded_safe_and_marks_suggested_edges(
    tmp_path: Path,
) -> None:
    assurance, _ = workspace(tmp_path)
    repository = assurance._thread
    nodes = [
        SolutionThreadNode(
            node_id=hashlib.sha256(label.encode()).hexdigest(),
            kind=(
                SolutionNodeKind.system
                if label != "Order API"
                else SolutionNodeKind.interface
            ),
            label=label,
            source_reference=f"{label}.md",
            source_revision="revision-1",
            confirmed=True,
        )
        for label in ("NetSuite", "Salesforce", "Order API")
    ]
    for node in nodes:
        repository.save_node(node)
    for index, confirmed in enumerate((True, False)):
        repository.save_edge(
            SolutionThreadEdge(
                edge_id=hashlib.sha256(f"mind-map-edge-{index}".encode()).hexdigest(),
                source_node_id=nodes[index].node_id,
                target_node_id=nodes[index + 1].node_id,
                relation=SolutionEdgeKind.depends_on,
                confirmed=confirmed,
                review_status="confirmed" if confirmed else "suggested",
            )
        )

    confirmed_only = assurance.mind_map(
        SolutionMindMapRequest(
            focus="NetSuite",
            depth=2,
            include_suggested=False,
        )
    )
    assert {node.label for node in confirmed_only.nodes} == {"NetSuite", "Salesforce"}
    assert [edge.status for edge in confirmed_only.edges] == ["confirmed"]
    assert nodes[0].node_id not in confirmed_only.mermaid
    assert confirmed_only.source_fingerprint == assurance.mind_map(
        SolutionMindMapRequest(
            focus="NetSuite",
            depth=2,
            include_suggested=False,
        )
    ).source_fingerprint

    with_suggestions = assurance.mind_map(
        SolutionMindMapRequest(focus="NetSuite", depth=2)
    )
    assert {node.label for node in with_suggestions.nodes} == {
        "NetSuite",
        "Salesforce",
        "Order API",
    }
    assert with_suggestions.suggested_edge_count == 1
    assert "-.->|depends_on|" in with_suggestions.mermaid
    assert with_suggestions.query_hints[0] == "NetSuite"


def test_solution_mind_map_reports_truncation(tmp_path: Path) -> None:
    assurance, _ = workspace(tmp_path)
    for index in range(11):
        assurance._thread.save_node(
            SolutionThreadNode(
                node_id=hashlib.sha256(f"document-{index}".encode()).hexdigest(),
                kind=SolutionNodeKind.document,
                label=f"Document {index}",
                source_reference=f"document-{index}.md",
                confirmed=True,
            )
        )

    mind_map = assurance.mind_map(SolutionMindMapRequest(max_nodes=10))

    assert len(mind_map.nodes) == 10
    assert mind_map.truncated is True


def test_traceability_links_require_revision_checked_review(tmp_path: Path) -> None:
    assurance, evidence = workspace(tmp_path)
    artifact, revision, sections = build_artifact(
        document(
            document_id="traceability",
            title="Traceability specification",
            content="# Coverage\nREQ-104 is validated by TEST-22.",
        )
    )
    evidence.replace_artifact(artifact, revision, sections)
    asyncio.run(assurance.run(AssuranceRunRequest(connector_ids=["local"])))
    suggested = assurance.suggested_edges()
    assert {edge.relation for edge in suggested} == {
        SolutionEdgeKind.implements,
        SolutionEdgeKind.validated_by,
    }
    requirement = next(
        node
        for node in assurance._thread.nodes()
        if node.kind is SolutionNodeKind.requirement
    )
    before = assurance.impact(
        SolutionThreadImpactRequest(root_node_id=requirement.node_id, depth=2)
    )
    assert before.edges == []
    test_edge = next(
        edge for edge in suggested if edge.relation is SolutionEdgeKind.validated_by
    )
    reviewed = assurance.review_edge(
        test_edge.edge_id,
        SolutionThreadEdgeReviewRequest(expected_revision=1, action="confirm"),
    )
    assert reviewed.confirmed is True
    assert reviewed.revision == 2
    after = assurance.impact(
        SolutionThreadImpactRequest(root_node_id=requirement.node_id, depth=2)
    )
    assert [edge.edge_id for edge in after.edges] == [test_edge.edge_id]
    try:
        assurance.review_edge(
            test_edge.edge_id,
            SolutionThreadEdgeReviewRequest(expected_revision=1, action="dismiss"),
        )
    except RuntimeError as error:
        assert str(error) == "stale_solution_edge"
    else:
        raise AssertionError("stale solution-thread review was accepted")
    assurance._thread.save_edge(test_edge)
    persisted = next(
        edge
        for edge in assurance._thread.edges()
        if edge.edge_id == test_edge.edge_id
    )
    assert persisted.review_status == "confirmed"
    assert persisted.revision == 2


def test_malformed_rule_pack_is_a_safe_validation_error() -> None:
    try:
        verify_rule_pack(b"not-a-zip", [])
    except ValueError as error:
        assert "archive" in str(error)
    else:
        raise AssertionError("malformed rule pack was accepted")


def test_remote_office_content_is_bounded_and_ignores_embedded_objects() -> None:
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="urn:test"><w:t>Approved architecture text</w:t>'
            "</w:document>",
        )
        archive.writestr("word/vbaProject.bin", b"PRIVATE-MACRO-CONTENT")
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<Relationship Target="https://untrusted.example/instructions"/>',
        )
    content = parse_remote_document("architecture.docx", package.getvalue())
    assert content == "Approved architecture text"
    assert "PRIVATE-MACRO-CONTENT" not in content
    assert "untrusted.example" not in content


def test_live_remote_assurance_content_is_not_added_to_local_evidence(
    tmp_path: Path,
) -> None:
    assurance, evidence = workspace(tmp_path)
    marker = "REMOTE-GRAPH-ONLY-TBD-MARKER"
    remote = ContextDocument(
        document_id="remote-document",
        connector_id="graph",
        source_kind=ConnectorKind.microsoft_graph,
        record_id="opaque-remote-id",
        title="Remote requirements",
        content=f"# Requirement\nAcceptance criteria: {marker}",
        entity_type="knowledge_document",
        source_reference="Remote requirements.docx",
        allowed_principals=["alice"],
    )
    result = asyncio.run(
        assurance.run(
            AssuranceRunRequest(connector_ids=["graph"], changed_only=False),
            transient_documents=[remote],
        )
    )
    assert result.artifact_count == 1
    assert evidence.artifacts() == []
    assert evidence.sections() == []
    assert any(
        finding.rule_id == "builtin.unresolved-placeholder"
        for finding in assurance.findings()
    )


def test_deterministic_document_checks_cover_explicit_quality_failures(
    tmp_path: Path,
) -> None:
    assurance, evidence = workspace(tmp_path)
    artifact, revision, sections = build_artifact(
        document(
            document_id="quality",
            title="Requirements specification",
            content=(
                "# Scope\nREQ-42 must be fast and user-friendly.\n"
                "REQ-42 must not be enabled.\n"
                "CRM: Customer relationship manager\n"
                "CRM: Change request module\n"
                "See [security controls](#missing-security).\n"
                "Owner: Platform team\nAcceptance criteria: reviewed"
            ),
        )
    )
    evidence.replace_artifact(artifact, revision, sections)
    asyncio.run(assurance.run(AssuranceRunRequest(connector_ids=["local"])))
    rule_ids = {finding.rule_id for finding in assurance.findings()}
    assert {
        "builtin.requirement-vague-language",
        "builtin.broken-internal-reference",
        "builtin.terminology-definition-conflict",
        "builtin.keyed-requirement-conflict",
    } <= rule_ids


class RecordingEmbeddingProvider:
    profile = EmbeddingProfile(
        model_id="test/e5",
        model_sha256="a" * 64,
        dimensions=2,
    )

    def __init__(self) -> None:
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls.append(texts)
        return [[1.0, 0.0] for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return [1.0, 0.0]


def test_hybrid_retrieval_distinguishes_query_and_passage_embeddings(
    tmp_path: Path,
) -> None:
    evidence = EncryptedEvidenceRepository(tmp_path / "evidence.sqlite3", XorProtector())
    artifact, revision, sections = build_artifact(
        document(
            document_id="e5",
            title="ERP interface design",
            content="Customer order interface",
        )
    )
    evidence.replace_artifact(artifact, revision, sections)
    provider = RecordingEmbeddingProvider()
    engine = LocalHybridRetrievalEngine(
        evidence,
        embedding_provider=provider,
        embedding_store=evidence,
    )
    result = asyncio.run(
        engine.retrieve(RetrievalRequest(text="customer order", principal_id="alice"))
    )
    assert result.lexical_only is False
    assert provider.document_calls == [["Customer order interface"]]
    assert provider.query_calls == ["customer order"]


def test_retrieval_source_authority_is_bounded_and_deterministic(tmp_path: Path) -> None:
    evidence = EncryptedEvidenceRepository(tmp_path / "evidence.sqlite3", XorProtector())
    for connector_id, title in (("low", "Draft design"), ("high", "Approved standard")):
        artifact, revision, sections = build_artifact(
            document(
                document_id=connector_id,
                connector_id=connector_id,
                title=title,
                content="Customer order retention control",
            )
        )
        evidence.replace_artifact(artifact, revision, sections)
    engine = LocalHybridRetrievalEngine(
        evidence,
        source_authority={"low": 0, "high": 100},
    )
    result = asyncio.run(
        engine.retrieve(RetrievalRequest(text="customer order", principal_id="alice"))
    )
    assert result.citations[0].source_reference == "Approved standard"
    assert result.citations[0].score.source_authority_boost == 1.0


def test_removed_local_file_is_purged_from_revisioned_evidence(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    source = root / "retired-design.md"
    source.write_text("# Retired design\nREQ-1 old interface", encoding="utf-8")
    protector = XorProtector()
    evidence = EncryptedEvidenceRepository(tmp_path / "evidence.sqlite3", protector)
    provider = LocalFilesContextProvider(
        connector_id="local",
        display_name="Local knowledge",
        root=root,
        principal_id="alice",
        index=EncryptedContextIndex(tmp_path / "context.sqlite3", protector),
        evidence_repository=evidence,
    )
    assert asyncio.run(provider.sync()) == 1
    assert len(evidence.artifacts()) == 1
    source.unlink()
    assert asyncio.run(provider.sync()) == 0
    assert evidence.artifacts() == []
    assert evidence.sections() == []


def test_erp_validation_checks_type_required_key_code_version_and_environment(
    tmp_path: Path,
) -> None:
    assurance, evidence = workspace(tmp_path)
    artifact, revision, sections = build_artifact(
        document(
            document_id="erp-claims",
            title="Data mapping",
            content=(
                "Account.name type: Edm.Int32\n"
                "Account.name required\n"
                "Business key: Account.name\n"
                "Account.status code: RETIRED\n"
                "API version: v1\nEnvironment: production"
            ),
        )
    )
    evidence.replace_artifact(artifact, revision, sections)
    snapshot = ERPMetadataSnapshot(
        snapshot_id="b" * 64,
        connector_id="dynamics",
        source_reference="Dynamics metadata",
        api_version="v2",
        environment="sandbox",
        metadata_hash="c" * 64,
        entities=[
            ERPEntityMetadata(
                name="Account",
                fields=[
                    ERPFieldMetadata(
                        name="name",
                        data_type="Edm.String",
                        required=False,
                        key=False,
                    ),
                    ERPFieldMetadata(
                        name="status",
                        data_type="Edm.String",
                        code_values=["ACTIVE", "INACTIVE"],
                    ),
                ],
            )
        ],
    )
    result = assurance.validate_erp(snapshot, [artifact.artifact_id])
    descriptions = "\n".join(finding.description for finding in result.findings)
    assert "not Edm.Int32" in descriptions
    assert "Required-field claim" in descriptions
    assert "is not an ERP key" in descriptions
    assert "RETIRED is not valid" in descriptions
    assert "API version" in descriptions
    assert "environment" in descriptions


def test_v8_api_is_authenticated_and_reviews_are_stale_safe(tmp_path: Path) -> None:
    assurance, evidence = workspace(tmp_path)
    artifact, revision, sections = build_artifact(
        document(
            document_id="cutover",
            title="Cutover plan",
            content="Deployment starts Friday. Owner: Release team.",
        )
    )
    evidence.replace_artifact(artifact, revision, sections)
    token = "v8-capability-token-0123456789abcdef"
    client = TestClient(
        create_app(capability_token=token, assurance_workspace_instance=assurance)
    )
    headers = {CAPABILITY_TOKEN_HEADER: token}
    assert client.get("/health/compatibility", headers=headers).json()["api_version"] == 13
    assert client.post(
        "/assurance/runs",
        json={"connector_ids": ["local"]},
    ).status_code == 401
    run = client.post(
        "/assurance/runs",
        json={"connector_ids": ["local"]},
        headers=headers,
    )
    assert run.status_code == 200
    assert client.post(
        "/solution-thread/mind-map",
        json={"focus": "Cutover", "depth": 2},
    ).status_code == 401
    mind_map = client.post(
        "/solution-thread/mind-map",
        json={"focus": "Cutover", "depth": 2},
        headers=headers,
    )
    assert mind_map.status_code == 200
    assert mind_map.json()["nodes"][0]["label"] == "Cutover plan"
    assert mind_map.json()["source_fingerprint"]
    findings = client.get("/assurance/findings", headers=headers).json()
    assert findings[0]["rule_id"] == "builtin.cutover-rollback"
    first = client.post(
        f"/assurance/findings/{findings[0]['finding_id']}/review",
        json={"expected_revision": 1, "action": "confirm"},
        headers=headers,
    )
    assert first.status_code == 200
    stale = client.post(
        f"/assurance/findings/{findings[0]['finding_id']}/review",
        json={"expected_revision": 1, "action": "dismiss"},
        headers=headers,
    )
    assert stale.status_code == 409


def test_unavailable_connector_degrades_but_does_not_block_local_assurance(
    tmp_path: Path,
) -> None:
    assurance, evidence = workspace(tmp_path)
    artifact, revision, sections = build_artifact(
        document(
            document_id="local-fallback",
            title="Cutover plan",
            content="Owner: Release team. Cutover begins Friday.",
        )
    )
    evidence.replace_artifact(artifact, revision, sections)

    class DegradedContextService:
        async def prepare_assurance_sources(self, connector_ids: list[str]):
            assert connector_ids == ["local", "graph"]
            return [], ["graph"]

    token = "v8-degraded-token-0123456789abcdef"
    client = TestClient(
        create_app(
            capability_token=token,
            assurance_workspace_instance=assurance,
            context_service_instance=DegradedContextService(),  # type: ignore[arg-type]
        )
    )
    response = client.post(
        "/assurance/runs",
        json={"connector_ids": ["local", "graph"]},
        headers={CAPABILITY_TOKEN_HEADER: token},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["artifact_count"] == 1
    assert response.json()["detail_code"] == "connector_unavailable"
