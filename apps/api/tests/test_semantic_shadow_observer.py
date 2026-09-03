from app.domain.contracts import Lang, Speaker, TranscriptEvent
from app.domain.semantic_candidates import (
    CandidateKind,
    FactCandidate,
    PainCandidate,
    SemanticObservationBatch,
)
from app.services.semantic_shadow_observer import SemanticShadowObserver


def _event(text: str) -> TranscriptEvent:
    return TranscriptEvent(
        event_id="shadow:event-0",
        meeting_id="shadow",
        seq=0,
        speaker=Speaker(id="synthetic"),
        text=text,
        lang=Lang.en,
        ts_start=0.0,
        ts_end=1.0,
        is_final=True,
        source="shadow-test",
    )


def test_shadow_observer_reports_only_aggregate_candidate_agreement() -> None:
    event = _event(
        "Every morning one person manually copies order rows into a spreadsheet."
    )

    class CandidateProvider:
        def observe(self, events: list[TranscriptEvent]) -> SemanticObservationBatch:
            assert events == [event]
            return SemanticObservationBatch(
                pains=[
                    PainCandidate(
                        category="manual_work",
                        title="Manual work",
                        kind=CandidateKind.evidence,
                        evidence_event_ids=[event.event_id],
                        confidence=0.9,
                        reason="candidate rationale must not leave this provider boundary",
                    )
                ],
                facts=[
                    FactCandidate(
                        slot="frequency",
                        value="every morning",
                        kind=CandidateKind.evidence,
                        evidence_event_ids=[event.event_id],
                        pain_category_hint="manual_work",
                        confidence=0.9,
                        reason="candidate rationale must not leave this provider boundary",
                    )
                ],
            )

    counters = SemanticShadowObserver(CandidateProvider()).evaluate([event])

    assert counters.windows_evaluated == 1
    assert counters.pain_candidates == 1
    assert counters.fact_candidates == 1
    assert counters.pain_agreements == 1
    assert counters.fact_agreements == 1
    assert not hasattr(counters, "prompt")
    assert not hasattr(counters, "candidates")


def test_shadow_observer_contains_provider_failure() -> None:
    class BrokenProvider:
        def observe(self, events: list[TranscriptEvent]) -> SemanticObservationBatch:
            del events
            raise RuntimeError("provider output that must not escape")

    counters = SemanticShadowObserver(BrokenProvider()).evaluate(
        [_event("The current workflow is documented.")]
    )

    assert counters.provider_failures == 1
    assert counters.pain_candidates == 0
    assert counters.fact_candidates == 0


def test_shadow_observer_rejects_ungrounded_candidate_batch() -> None:
    event = _event("The current workflow is documented.")

    class UngroundedProvider:
        def observe(self, events: list[TranscriptEvent]) -> SemanticObservationBatch:
            del events
            return SemanticObservationBatch(
                pains=[
                    PainCandidate(
                        category="manual_work",
                        title="Manual work",
                        kind=CandidateKind.inference,
                        evidence_event_ids=["missing:event"],
                        confidence=0.5,
                        reason="ungrounded",
                    )
                ]
            )

    counters = SemanticShadowObserver(UngroundedProvider()).evaluate([event])

    assert counters.validation_failures == 1
    assert counters.pain_candidates == 0
