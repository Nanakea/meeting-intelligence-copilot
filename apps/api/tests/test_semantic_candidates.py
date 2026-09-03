"""Phase 4A semantic candidate boundary tests (no model or network runtime)."""

from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import ValidationError

from app.domain.contracts import Lang, Speaker, TranscriptEvent
from app.domain.semantic_candidates import (
    CandidateKind,
    FactCandidate,
    PainCandidate,
    SemanticCandidateValidationError,
    SemanticObservationBatch,
    validate_semantic_observations,
)
from app.domain.templates import TEMPLATES
from app.services.meeting_engine import MeetingEngine
from app.services.semantic_candidate_analyzer import NullSemanticCandidateAnalyzer

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ANNOTATIONS = REPO_ROOT / "evals" / "semantic" / "local-semantic-paraphrases.json"
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check.ps1"


def _event(event_id: str = "e1", text: str = "EC is authoritative") -> TranscriptEvent:
    return TranscriptEvent(
        event_id=event_id,
        meeting_id="m1",
        seq=0,
        speaker=Speaker(id="speaker-1"),
        text=text,
        lang=Lang.en,
        ts_start=0.0,
        ts_end=1.0,
        source="semantic-test",
    )


def _fact(**updates) -> FactCandidate:
    values = {
        "slot": "source_of_truth",
        "value": "EC",
        "kind": CandidateKind.evidence,
        "evidence_event_ids": ["e1"],
        "pain_category_hint": "data_mismatch",
        "confidence": 0.95,
        "reason": "Explicit source-of-truth statement.",
    }
    values.update(updates)
    return FactCandidate(**values)


def _pain(**updates) -> PainCandidate:
    values = {
        "category": "data_mismatch",
        "title": "Data mismatch",
        "kind": CandidateKind.evidence,
        "evidence_event_ids": ["e1"],
        "confidence": 0.9,
        "reason": "The systems are described as disagreeing.",
    }
    values.update(updates)
    return PainCandidate(**values)


def test_valid_fact_and_pain_candidates_pass_structural_validation() -> None:
    batch = SemanticObservationBatch(pains=[_pain()], facts=[_fact()])
    assert validate_semantic_observations(batch, [_event()]) is batch


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        (_pain(category="not_registered"), "unknown category"),
        (_fact(pain_category_hint="not_registered"), "unknown category"),
        (_fact(slot="not_a_slot"), "unknown slot"),
        (_fact(pain_category_hint=None), "pain_category_hint is required"),
    ],
)
def test_unknown_category_or_slot_is_rejected(candidate, message: str) -> None:
    batch = (
        SemanticObservationBatch(pains=[candidate])
        if isinstance(candidate, PainCandidate)
        else SemanticObservationBatch(facts=[candidate])
    )
    with pytest.raises(SemanticCandidateValidationError, match=message):
        validate_semantic_observations(batch, [_event()])


def test_missing_or_unknown_evidence_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        FactCandidate(
            slot="frequency",
            value="daily",
            kind="evidence",
            evidence_event_ids=[],
            pain_category_hint="manual_work",
            confidence=1.0,
            reason="Explicit frequency.",
        )

    with pytest.raises(SemanticCandidateValidationError, match="not present"):
        validate_semantic_observations(
            SemanticObservationBatch(facts=[_fact(evidence_event_ids=["invented"])]),
            [_event()],
        )


def test_blank_fact_value_and_invalid_kind_are_rejected_by_candidate_schema() -> None:
    with pytest.raises(ValidationError):
        _fact(value="   ")
    with pytest.raises(ValidationError):
        _fact(kind="guess")


def test_inference_provenance_remains_inference_after_validation() -> None:
    batch = SemanticObservationBatch(facts=[_fact(kind=CandidateKind.inference)])
    validated = validate_semantic_observations(batch, [_event()])
    assert validated.facts[0].kind is CandidateKind.inference


def test_invalid_batch_cannot_mutate_meeting_state() -> None:
    engine = MeetingEngine("m1")
    event = _event(text="Small talk only.")
    assert engine.apply(event) is True
    before = engine.state.model_copy(deep=True)

    invalid = SemanticObservationBatch(facts=[_fact(slot="invented_slot")])
    with pytest.raises(SemanticCandidateValidationError):
        validate_semantic_observations(invalid, [event])

    assert engine.state == before


class _StaticSemanticCandidateAnalyzer:
    """Test-only in-memory provider; deliberately has no I/O capabilities."""

    def __init__(self, batch: SemanticObservationBatch) -> None:
        self.batch = batch

    def observe(self, events: list[TranscriptEvent]) -> SemanticObservationBatch:
        del events
        return self.batch


def test_null_and_static_test_providers_require_no_network() -> None:
    event = _event()
    assert NullSemanticCandidateAnalyzer().observe([event]) == SemanticObservationBatch()

    expected = SemanticObservationBatch(facts=[_fact()])
    observed = _StaticSemanticCandidateAnalyzer(expected).observe([event])
    assert validate_semantic_observations(observed, [event]) == expected

    engine = MeetingEngine("m1")
    assert engine.apply(event) is True
    assert engine.state.version == 1


def test_annotation_only_semantic_expectations_are_representable() -> None:
    payload = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    assert payload["status"] == "annotation-only"

    for seq, case in enumerate(payload["cases"]):
        event = TranscriptEvent(
            event_id=case["event_id"],
            meeting_id="semantic-annotations",
            seq=seq,
            speaker=Speaker(id="annotator"),
            text=case["text"],
            lang=case["lang"],
            ts_start=float(seq),
            ts_end=float(seq + 1),
            source="annotation-only",
        )
        pains = []
        if case["expected_pain_template"] is not None:
            category = case["expected_pain_template"]
            pains.append(
                PainCandidate(
                    category=category,
                    title=TEMPLATES[category].title_for(case["lang"]),
                    kind=CandidateKind.evidence,
                    evidence_event_ids=[case["event_id"]],
                    confidence=1.0,
                    reason="Expected pain from the annotation-only semantic eval.",
                )
            )
        facts = [
            FactCandidate(
                slot=fact["slot"],
                value=fact["value"],
                kind=fact["kind"],
                evidence_event_ids=fact["evidence_event_ids"],
                pain_category_hint=fact["template_id"],
                confidence=1.0,
                reason="Expected fact from the annotation-only semantic eval.",
            )
            for fact in case["expected_facts"]
        ]
        validate_semantic_observations(
            SemanticObservationBatch(pains=pains, facts=facts),
            [event],
        )


def test_repository_check_has_no_ollama_runtime_gate() -> None:
    assert "ollama" not in CHECK_SCRIPT.read_text(encoding="utf-8").lower()
