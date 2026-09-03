"""Release thresholds for the Korean synthetic pilot corpus."""

from app.domain.contracts import (
    FactStatus,
    GapStatus,
    Lang,
    Speaker,
    TranscriptEvent,
)
from app.evals.korean_pilot_corpus import (
    evaluate_korean_pilot_corpus,
    generate_korean_pilot_corpus,
)
from app.services.meeting_engine import MeetingEngine


def event(text: str, *, meeting_id: str, seq: int = 0) -> TranscriptEvent:
    return TranscriptEvent(
        event_id=f"{meeting_id}:event-{seq}",
        meeting_id=meeting_id,
        seq=seq,
        speaker=Speaker(id="synthetic"),
        text=text,
        lang=Lang.ko,
        ts_start=float(seq),
        ts_end=float(seq + 1),
        source="korean-pilot-corpus",
    )


def test_korean_corpus_shape_and_independent_annotations() -> None:
    corpus = generate_korean_pilot_corpus()
    assert len(corpus) == 300
    assert sum(case.hard_negative for case in corpus) == 90
    assert len({(case.case_id, case.events) for case in corpus}) == 300
    assert {case.category for case in corpus} == {
        "data_mismatch",
        "integration_failure",
        "manual_work",
        "process_delay",
        "vague_requirement",
        "unclear_ownership",
        "nonfunctional_requirement",
        "architecture_constraint",
        "security_control_gap",
        "dependency_blocker",
    }


def test_korean_corpus_meets_synthetic_candidate_thresholds() -> None:
    report = evaluate_korean_pilot_corpus()
    assert report.invariant_failures == 0
    assert report.trajectory_failures == 0
    assert report.pain_precision >= 0.95
    assert report.pain_recall >= 0.90
    assert report.fact_precision >= 0.95
    assert report.fact_recall >= 0.85
    assert report.ask_now_agreement >= 0.95
    assert report.no_question_false_positive_rate < 0.02


def test_korean_multiple_problems_remain_separate_in_one_event() -> None:
    engine = MeetingEngine("meeting-intel-korean-multiple")
    engine.apply(
        event(
            "WMS와 NetSuite의 재고 데이터가 일치하지 않고 매일 CSV 데이터를 수작업으로 복사합니다.",
            meeting_id=engine.state.meeting_id,
        )
    )
    assert {pain.template_id for pain in engine.state.pain_points} == {
        "data_mismatch",
        "manual_work",
    }
    assert len({pain.pain_id for pain in engine.state.pain_points}) == 2


def test_korean_supersession_contradiction_and_gap_reopening_are_traceable() -> None:
    engine = MeetingEngine("meeting-intel-korean-lifecycle")
    statements = [
        "WMS와 NetSuite의 재고 데이터가 일치하지 않습니다.",
        "현재 WMS 데이터를 기준 정보로 사용합니다.",
        "정확히는 NetSuite 데이터를 기준 정보로 사용합니다.",
        "SAP 데이터를 기준 정보로 사용합니다.",
    ]
    for seq, text in enumerate(statements):
        engine.apply(
            event(text, meeting_id=engine.state.meeting_id, seq=seq)
        )

    source_facts = [fact for fact in engine.state.facts if fact.slot == "source_of_truth"]
    assert [fact.status for fact in source_facts] == [
        FactStatus.superseded,
        FactStatus.contradicted,
        FactStatus.contradicted,
    ]
    assert all(fact.evidence_event_ids for fact in source_facts)
    source_gap = next(gap for gap in engine.state.gaps if gap.slot == "source_of_truth")
    assert source_gap.status is GapStatus.open
    assert source_gap.reopen_count == 1
