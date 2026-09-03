"""Korean meeting-intelligence coverage with explicit language provenance."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.contracts import Lang, MeetingState, Speaker, TranscriptEvent
from app.domain.templates import TEMPLATES
from app.services.analyzer import analyze
from app.services.governance import detect_governance_candidates
from app.services.meeting_engine import MeetingEngine


def event(text: str, *, lang: Lang = Lang.ko, seq: int = 0) -> TranscriptEvent:
    return TranscriptEvent(
        event_id=f"ko-event-{seq}",
        meeting_id="meeting-intel-korean-test",
        seq=seq,
        speaker=Speaker(id="speaker"),
        text=text,
        lang=lang,
        ts_start=float(seq),
        ts_end=float(seq + 1),
        source="test",
    )


def active_fact(state: MeetingState, slot: str) -> str | None:
    return next(
        (fact.value for fact in state.facts if fact.slot == slot and fact.status == "active"),
        None,
    )


def ask_now_slot(state: MeetingState) -> str | None:
    slots = {gap.gap_id: gap.slot for gap in state.gaps}
    return next(
        (
            slots[suggestion.gap_id]
            for suggestion in state.suggestions
            if suggestion.role == "ask_now" and suggestion.status == "active"
        ),
        None,
    )


def test_every_template_has_complete_korean_wording() -> None:
    for category, template in TEMPLATES.items():
        assert template.title_for("ko") == template.title["ko"]
        assert all(template.question("ko", slot) is not None for slot in template.slots), category
        assert all(
            "가 확인되지 않았습니다" not in template.question("ko", slot).text
            for slot in template.slots
        )


@pytest.mark.parametrize(
    ("text", "category", "fact_slot"),
    [
        ("WMS와 NetSuite의 재고 데이터가 일치하지 않습니다.", "data_mismatch", None),
        ("매일 CSV 데이터를 수작업으로 복사합니다.", "manual_work", "frequency"),
        (
            "NetSuite에서 주문을 WMS로 전송하는 단계에서 실패합니다.",
            "integration_failure",
            "failure_point",
        ),
        ("검색 화면을 더 쉽게 사용하게 해 주세요.", "vague_requirement", None),
        ("승인 프로세스가 지연되어 현재 8영업일이 걸립니다.", "process_delay", None),
        ("할인 승인 담당자가 정해지지 않았습니다.", "unclear_ownership", None),
        ("성능 요구사항이 불명확합니다.", "nonfunctional_requirement", "quality_attribute"),
        ("아키텍처 제약으로 SAP를 사용해야 합니다.", "architecture_constraint", "constraint"),
        (
            "고객 데이터가 암호화되지 않아 보안 통제가 없습니다.",
            "security_control_gap",
            "affected_asset_data",
        ),
        ("플랫폼 팀의 대응을 기다리는 중입니다.", "dependency_blocker", "provider"),
    ],
)
def test_korean_rules_cover_every_problem_template(
    text: str, category: str, fact_slot: str | None
) -> None:
    result = analyze(event(text))

    assert any(pain.template_id == category for pain in result.detected_pains)
    if fact_slot is not None:
        assert any(
            fact.template_id == category and fact.slot == fact_slot for fact in result.facts
        )
    assert all(pain.evidence_event_id == "ko-event-0" for pain in result.detected_pains)
    assert all(fact.evidence_event_id == "ko-event-0" for fact in result.facts)


def test_korean_dispatch_uses_explicit_language_not_text_shape() -> None:
    korean = "WMS와 NetSuite의 재고 데이터가 일치하지 않습니다."
    english = "The WMS and NetSuite inventory counts do not match."

    assert analyze(event(korean, lang=Lang.ko)).detected_pain is not None
    assert analyze(event(korean, lang=Lang.en)).detected_pain is None
    assert analyze(event(korean, lang=Lang.ja)).detected_pain is None
    assert analyze(event(english, lang=Lang.ko)).detected_pain is None


@pytest.mark.parametrize(
    "text",
    [
        "WMS와 NetSuite의 재고 데이터가 일치하지 않습니까?",
        "만약 WMS와 NetSuite의 재고가 일치하지 않으면 확인해야 합니다.",
        "WMS와 NetSuite의 재고 불일치가 없습니다.",
        '"WMS와 NetSuite의 재고가 일치하지 않는다"라고 들었습니다.',
    ],
)
def test_korean_questions_hypotheticals_negation_and_quotes_do_not_open_pain(text: str) -> None:
    assert analyze(event(text)).detected_pain is None


@pytest.mark.parametrize(
    "text",
    [
        "성능 요구사항이 명확합니다.",
        "아키텍처 제약이 없습니다.",
        "고객 데이터는 암호화되어 있으며 보안 통제가 있습니다.",
        "플랫폼 팀의 대응을 더 이상 대기하지 않습니다.",
        "사용자는 이 기능을 사용해야 합니다.",
        "버스를 기다리는 중입니다.",
    ],
)
def test_korean_solution_lead_rules_reject_resolved_or_unrelated_statements(text: str) -> None:
    assert analyze(event(text)).detected_pain is None


def test_korean_meeting_progresses_questions_without_losing_evidence() -> None:
    engine = MeetingEngine("meeting-intel-korean-test")
    statements = [
        "WMS와 NetSuite의 재고 데이터가 일치하지 않습니다.",
        "현재 WMS 데이터를 기준 정보로 사용합니다.",
        "이 차이 때문에 출하가 지연됩니다.",
        "재고 불일치 담당자는 물류팀입니다.",
    ]

    for seq, text in enumerate(statements):
        assert engine.apply(event(text, seq=seq))
        if seq == 0:
            assert ask_now_slot(engine.state) == "source_of_truth"
            assert engine.state.suggestions[0].text.startswith("현재 어떤 시스템")
        elif seq == 1:
            assert active_fact(engine.state, "source_of_truth") == "WMS"
            assert ask_now_slot(engine.state) == "business_impact"
        elif seq == 2:
            assert active_fact(engine.state, "business_impact") == statements[2]
            assert ask_now_slot(engine.state) == "owner"

    assert active_fact(engine.state, "owner") == "물류팀"
    emitted_ids = {item.event_id for item in engine.state.transcript}
    assert all(set(pain.evidence_event_ids) <= emitted_ids for pain in engine.state.pain_points)
    assert all(set(fact.evidence_event_ids) <= emitted_ids for fact in engine.state.facts)


def test_live_ingest_accepts_korean_and_preserves_configured_language() -> None:
    client = TestClient(create_app(capability_token=None))
    response = client.post(
        "/ingest/meetily/meeting-intel-korean-live",
        json={
            "lang": "ko",
            "payload": {
                "sequence_id": 0,
                "text": "WMS와 NetSuite의 재고 데이터가 일치하지 않습니다.",
                "timestamp": 0,
                "duration": 1.0,
                "speaker": "물류 담당자",
                "is_partial": True,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "applied"


def test_korean_governance_candidates_are_transcript_backed_and_review_only() -> None:
    source = event("운영팀이 주문 연계를 확인하기로 결정했습니다.")
    candidates = detect_governance_candidates(source, MeetingState(meeting_id=source.meeting_id))

    assert len(candidates) == 1
    assert candidates[0].kind == "decision"
    assert candidates[0].language == "ko"
    assert candidates[0].title.startswith("결정:")
    assert candidates[0].evidence_event_ids == [source.event_id]
