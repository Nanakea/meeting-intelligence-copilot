from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

import app.api.main as api_main
from app.adapters.governance.store import MemoryGovernanceRepository
from app.adapters.governance.workspace import GovernanceWorkspace
from app.adapters.transcript.live_registry import LiveMeetingRegistry
from app.api.main import create_app
from app.domain.context import EntityGlossaryEntry
from app.domain.contracts import (
    FactKind,
    GapStatus,
    InformationGap,
    Lang,
    MeetingFact,
    PainPoint,
    TranscriptEvent,
)
from app.domain.selector import select_suggestions
from app.services.analyzer import analyze


class _SlowContextService:
    async def search(self, _query: object) -> object:
        await asyncio.sleep(5)
        raise AssertionError("the two-second governance deadline was not enforced")

    async def close(self) -> None:
        return None


def test_governance_factory_does_not_disable_opt_in_semantic_runtime(monkeypatch) -> None:
    monkeypatch.setenv(api_main.SEMANTIC_FEATURE_FLAG, "ollama")
    monkeypatch.setattr(api_main, "semantic_candidate_analyzer_from_env", lambda: object())
    assert api_main.load_default_semantic_runtime() is not None


def event(text: str, *, lang: Lang = Lang.en, seq: int = 0) -> TranscriptEvent:
    return TranscriptEvent(
        event_id=f"event-{seq}",
        meeting_id="meeting-intel-0123456789abcdef0123456789abcdef",
        seq=seq,
        speaker={"id": "speaker"},
        text=text,
        lang=lang,
        ts_start=float(seq),
        ts_end=float(seq + 1),
        source="test",
    )


def payload(sequence_id: int, text: str) -> dict[str, object]:
    return {
        "sequence_id": sequence_id,
        "text": text,
        "timestamp": sequence_id,
        "duration": 1.0,
        "speaker": "Speaker",
        "is_partial": True,
    }


def test_new_solution_templates_detect_bilingual_pains_and_facts() -> None:
    cases = [
        (
            "The performance requirement is unclear. Response time must be under 2 seconds.",
            Lang.en,
            "nonfunctional_requirement",
            "measurable_target",
        ),
        (
            "アーキテクチャ制約はSAPの利用です。社内規程で必須です。",
            Lang.ja,
            "architecture_constraint",
            "constraint",
        ),
        (
            "The security control is missing for customer data required by GDPR.",
            Lang.en,
            "security_control_gap",
            "affected_asset_data",
        ),
        (
            "基盤チームの対応待ちです。金曜日までに必要です。",
            Lang.ja,
            "dependency_blocker",
            "current_status",
        ),
    ]
    for text, lang, category, slot in cases:
        result = analyze(event(text, lang=lang))
        assert any(pain.template_id == category for pain in result.detected_pains)
        assert any(fact.template_id == category and fact.slot == slot for fact in result.facts)


def test_governance_candidates_are_review_gated_revisioned_and_deletable() -> None:
    repository = MemoryGovernanceRepository()
    workspace = GovernanceWorkspace(repository)
    registry = LiveMeetingRegistry()
    client = TestClient(
        create_app(
            capability_token=None,
            live_registry_instance=registry,
            governance_workspace_instance=workspace,
        )
    )
    session_id = "meeting-intel-0123456789abcdef0123456789abcdef"
    response = client.post(
        f"/ingest/meetily/{session_id}",
        json={"lang": "en", "payload": payload(0, "We decided to use SAP for order processing.")},
    )
    assert response.status_code == 200
    state_version = response.json()["state_version"]

    candidates = client.get(f"/governance/sessions/{session_id}/candidates").json()
    assert len(candidates) == 1
    assert candidates[0]["kind"] == "decision"
    assert candidates[0]["evidence_event_ids"] == [f"{session_id}:seg0"]
    assert client.get("/governance/records").json()["records"] == []

    stale = client.post(
        f"/governance/candidates/{candidates[0]['candidate_id']}/review",
        json={"session_id": session_id, "state_version": state_version - 1, "action": "confirm"},
    )
    assert stale.status_code == 409

    confirmed = client.post(
        f"/governance/candidates/{candidates[0]['candidate_id']}/review",
        json={
            "session_id": session_id,
            "state_version": state_version,
            "action": "confirm",
            "edits": {"rationale": "Approved enterprise platform"},
        },
    )
    assert confirmed.status_code == 200
    record = confirmed.json()
    assert record["revision"] == 1
    assert record["field_provenance"]["rationale"] == "user"

    updated = client.post(
        f"/governance/records/{record['record_id']}/events",
        json={"expected_revision": 1, "action": "status", "changes": {"status": "resolved"}},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["status"] == "resolved"
    stale_update = client.post(
        f"/governance/records/{record['record_id']}/events",
        json={"expected_revision": 1, "action": "status", "changes": {"status": "open"}},
    )
    assert stale_update.status_code == 409

    # Live cleanup retains confirmed governance for post-meeting review.
    assert client.delete(f"/meeting/{session_id}").json() == {"reset": True}
    assert len(client.get("/governance/records").json()["records"]) == 1
    assert client.delete(f"/governance/meetings/{session_id}").json() == {"deleted": True}
    assert client.get("/governance/records").json()["records"] == []


def test_delete_all_governance_is_separate_from_live_cleanup() -> None:
    workspace = GovernanceWorkspace(MemoryGovernanceRepository())
    client = TestClient(create_app(capability_token=None, governance_workspace_instance=workspace))
    session_id = "meeting-intel-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    response = client.post(
        f"/ingest/meetily/{session_id}",
        json={"lang": "en", "payload": payload(0, "We decided to use SAP.")},
    )
    candidate = client.get(f"/governance/sessions/{session_id}/candidates").json()[0]
    assert (
        client.post(
            f"/governance/candidates/{candidate['candidate_id']}/review",
            json={
                "session_id": session_id,
                "state_version": response.json()["state_version"],
                "action": "confirm",
            },
        ).status_code
        == 200
    )
    assert client.delete("/meetings").json() == {"reset": True}
    assert len(client.get("/governance/records").json()["records"]) == 1
    assert client.delete("/governance").json() == {"deleted": True}
    assert client.get("/governance/records").json()["records"] == []


def test_identical_confirmed_record_accumulates_sources_and_survives_one_deletion() -> None:
    workspace = GovernanceWorkspace(MemoryGovernanceRepository())
    registry = LiveMeetingRegistry()
    client = TestClient(
        create_app(
            capability_token=None,
            live_registry_instance=registry,
            governance_workspace_instance=workspace,
        )
    )
    sessions = [
        "meeting-intel-11111111111111111111111111111111",
        "meeting-intel-22222222222222222222222222222222",
    ]
    for session_id in sessions:
        response = client.post(
            f"/ingest/meetily/{session_id}",
            json={"lang": "en", "payload": payload(0, "We decided to use SAP.")},
        )
        candidate = client.get(f"/governance/sessions/{session_id}/candidates").json()[0]
        assert (
            client.post(
                f"/governance/candidates/{candidate['candidate_id']}/review",
                json={
                    "session_id": session_id,
                    "state_version": response.json()["state_version"],
                    "action": "confirm",
                },
            ).status_code
            == 200
        )

    records = client.get("/governance/records").json()["records"]
    assert len(records) == 1
    assert records[0]["revision"] == 2
    assert records[0]["source_session_ids"] == sessions

    assert client.delete(f"/governance/meetings/{sessions[0]}").status_code == 200
    surviving = client.get("/governance/records").json()["records"][0]
    assert surviving["revision"] == 3
    assert surviving["source_session_ids"] == [sessions[1]]
    assert surviving["needs_provenance_review"] is True
    assert client.delete(f"/governance/meetings/{sessions[1]}").status_code == 200
    assert client.get("/governance/records").json()["records"] == []


def test_solution_lead_priority_is_bounded_and_keeps_suggestion_limits() -> None:
    pains = [
        PainPoint(
            pain_id="security-pain",
            meeting_id="meeting",
            template_id="security_control_gap",
            title="Security gap",
            kind=FactKind.evidence,
            evidence_event_ids=["event-0"],
            created_seq=0,
        ),
        PainPoint(
            pain_id="ordinary-pain",
            meeting_id="meeting",
            template_id="manual_work",
            title="Manual work",
            kind=FactKind.evidence,
            evidence_event_ids=["event-1"],
            created_seq=1,
        ),
    ]
    gaps = [
        InformationGap(
            gap_id="security-gap",
            meeting_id="meeting",
            pain_id="security-pain",
            slot="owner",
            status=GapStatus.open,
            base_priority=2,
        ),
        InformationGap(
            gap_id="ordinary-gap",
            meeting_id="meeting",
            pain_id="ordinary-pain",
            slot="business_impact",
            status=GapStatus.open,
            base_priority=1,
        ),
    ]
    facts = [
        MeetingFact(
            fact_id="security-fact",
            meeting_id="meeting",
            pain_id="security-pain",
            slot="affected_asset_data",
            value="Production customer data has a regulatory security gap",
            kind=FactKind.evidence,
            evidence_event_ids=["event-0"],
            created_seq=0,
        )
    ]
    suggestions = select_suggestions(pains, gaps, "en", facts)
    assert suggestions[0].gap_id == "security-gap"
    assert "security or regulatory" in suggestions[0].reason
    assert len([value for value in suggestions if value.role.value == "ask_now"]) == 1
    assert len([value for value in suggestions if value.role.value == "follow_up"]) <= 2


def test_impact_analysis_uses_only_confirmed_dependency_records_and_two_hops() -> None:
    glossary = [
        EntityGlossaryEntry(entry_id="erp", kind="system", canonical_name="ERP"),
        EntityGlossaryEntry(entry_id="crm", kind="system", canonical_name="CRM"),
        EntityGlossaryEntry(entry_id="portal", kind="system", canonical_name="Portal"),
    ]
    workspace = GovernanceWorkspace(
        MemoryGovernanceRepository(), glossary_provider=lambda: glossary
    )
    registry = LiveMeetingRegistry()
    client = TestClient(
        create_app(
            capability_token=None,
            live_registry_instance=registry,
            governance_workspace_instance=workspace,
        )
    )
    session_id = "meeting-intel-cccccccccccccccccccccccccccccccc"
    statements = [
        ("CRM depends on ERP.", "ERP", "CRM"),
        ("Portal depends on CRM.", "CRM", "Portal"),
    ]
    for sequence_id, (text, provider, consumer) in enumerate(statements):
        response = client.post(
            f"/ingest/meetily/{session_id}",
            json={"lang": "en", "payload": payload(sequence_id, text)},
        )
        candidate = client.get(f"/governance/sessions/{session_id}/candidates").json()[-1]
        assert (
            client.post(
                f"/governance/candidates/{candidate['candidate_id']}/review",
                json={
                    "session_id": session_id,
                    "state_version": response.json()["state_version"],
                    "action": "confirm",
                    "edits": {"provider": provider, "consumer": consumer},
                },
            ).status_code
            == 200
        )
    result = client.post("/governance/impact-analysis", json={"root_entity_id": "erp"}).json()
    assert {entity["entity_id"] for entity in result["entities"]} == {"erp", "crm", "portal"}
    assert len(result["relationships"]) == 2
    assert max(len(path["relationship_ids"]) for path in result["paths"]) == 2


def test_governance_detection_rejects_questions_and_hypotheticals() -> None:
    workspace = GovernanceWorkspace(MemoryGovernanceRepository())
    registry = LiveMeetingRegistry()
    client = TestClient(
        create_app(
            capability_token=None,
            live_registry_instance=registry,
            governance_workspace_instance=workspace,
        )
    )
    session_id = "meeting-intel-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    texts = [
        "What if we decided to use SAP?",
        "Did we agree on the architecture?",
        "If the provider is late, we could be blocked by them.",
        "We did not decide to use SAP.",
        "There is no dependency on CRM.",
        "Alice said that we decided to replace ERP.",
        "SAPの利用は決定していません。",
        "ERPへの依存関係はありません。",
    ]
    for sequence_id, text in enumerate(texts):
        assert (
            client.post(
                f"/ingest/meetily/{session_id}",
                json={"lang": "en", "payload": payload(sequence_id, text)},
            ).status_code
            == 200
        )
    assert client.get(f"/governance/sessions/{session_id}/candidates").json() == []


def test_all_governance_kinds_have_ja_and_en_deterministic_candidates() -> None:
    cases = {
        "decision": ("We decided to use SAP.", "SAPを利用すると決定しました。"),
        "risk": ("The risk is a missed close.", "リスクは決算の遅延です。"),
        "dependency": ("CRM depends on ERP.", "CRMはERPに依存しています。"),
        "action": ("We will review the interface.", "インターフェースを確認します。"),
        "assumption": ("We assume the API is available.", "APIが利用可能という前提です。"),
        "constraint": ("We must use the approved gateway.", "承認済み基盤の利用が必須です。"),
        "architecture_impact": (
            "This impacts our systems and data flow.",
            "この変更はデータフローに影響します。",
        ),
        "responsibility": (
            "The platform team is responsible for the interface.",
            "基盤チームが責任者です。",
        ),
    }
    for expected_kind, statements in cases.items():
        for index, (language, text) in enumerate(zip(("en", "ja"), statements, strict=True)):
            workspace = GovernanceWorkspace(MemoryGovernanceRepository())
            registry = LiveMeetingRegistry()
            client = TestClient(
                create_app(
                    capability_token=None,
                    live_registry_instance=registry,
                    governance_workspace_instance=workspace,
                )
            )
            session_id = f"meeting-intel-{expected_kind.replace('_', '')}{language}{index}"
            assert (
                client.post(
                    f"/ingest/meetily/{session_id}",
                    json={"lang": language, "payload": payload(0, text)},
                ).status_code
                == 200
            )
            kinds = {
                value["kind"]
                for value in client.get(f"/governance/sessions/{session_id}/candidates").json()
            }
            assert expected_kind in kinds


def test_governance_brief_connector_timeout_returns_meeting_only_records() -> None:
    workspace = GovernanceWorkspace(MemoryGovernanceRepository())
    registry = LiveMeetingRegistry()
    client = TestClient(
        create_app(
            capability_token=None,
            live_registry_instance=registry,
            context_service_instance=_SlowContextService(),  # type: ignore[arg-type]
            governance_workspace_instance=workspace,
        )
    )
    session_id = "meeting-intel-dddddddddddddddddddddddddddddddd"
    response = client.post(
        f"/ingest/meetily/{session_id}",
        json={"lang": "en", "payload": payload(0, "The risk is a production outage.")},
    )
    candidate = client.get(f"/governance/sessions/{session_id}/candidates").json()[0]
    assert (
        client.post(
            f"/governance/candidates/{candidate['candidate_id']}/review",
            json={
                "session_id": session_id,
                "state_version": response.json()["state_version"],
                "action": "confirm",
            },
        ).status_code
        == 200
    )
    brief = client.post(
        "/governance/briefs",
        json={"language": "en", "connector_ids": ["slow-connector"]},
    ).json()
    assert brief["context_status"] == "timeout"
    assert len(brief["records"]) == 1
    assert brief["context_citations"] == []
