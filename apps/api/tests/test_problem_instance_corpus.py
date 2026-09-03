from __future__ import annotations

import json
from pathlib import Path

from app.domain.contracts import Lang, Speaker, TranscriptEvent
from app.services.meeting_engine import MeetingEngine

SPEC = Path(__file__).resolve().parents[3] / "evals" / "pilot" / "problem-instance-corpus.json"


def _run(case_id: str, lang: str, texts: list[str]) -> MeetingEngine:
    engine = MeetingEngine(case_id)
    for seq, text in enumerate(texts):
        assert engine.apply(
            TranscriptEvent(
                event_id=f"{case_id}:event-{seq}",
                meeting_id=case_id,
                seq=seq,
                speaker=Speaker(id="speaker"),
                text=text,
                lang=Lang(lang),
                ts_start=float(seq),
                ts_end=float(seq + 1),
                source="problem-instance-corpus",
            )
        )
    return engine


def test_bilingual_multi_problem_collision_corpus() -> None:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    cases = 0
    hard_negatives = 0
    for group_index, group in enumerate(spec["groups"]):
        for case_index in range(spec["cases_per_group"]):
            case_id = f"problem-corpus-{group_index:02}-{case_index:03}"
            is_negative = case_index < spec["hard_negative_cases_per_group"]
            texts = (
                group["negative_events"]
                if is_negative
                else [
                    f"Reference {case_index}.",
                    *group["problems"],
                ]
            )
            first = _run(case_id, group["lang"], texts)
            replay = _run(case_id, group["lang"], texts)
            cases += 1
            hard_negatives += int(is_negative)

            if is_negative:
                assert first.state.pain_points == []
                assert first.state.facts == []
                assert first.state.suggestions == []
                continue

            pains = [
                pain
                for pain in first.state.pain_points
                if pain.template_id == group["category"]
            ]
            assert len(pains) == 2
            assert len({pain.instance_key for pain in pains}) == 2
            assert all(pain.identity_status.value == "anchored" for pain in pains)
            assert [pain.pain_id for pain in pains] == [
                pain.pain_id
                for pain in replay.state.pain_points
                if pain.template_id == group["category"]
            ]
            evidence_by_pain = {
                pain.pain_id: set(pain.evidence_event_ids) for pain in pains
            }
            assert all(len(evidence) == 1 for evidence in evidence_by_pain.values())
            assert len(set.union(*evidence_by_pain.values())) == 2
            for fact in first.state.facts:
                if fact.pain_id in evidence_by_pain:
                    assert set(fact.evidence_event_ids) <= evidence_by_pain[fact.pain_id]

    assert cases == 240
    assert hard_negatives == 72
