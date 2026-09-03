"""Evaluation-only semantic observer with aggregate, content-free counters.

This service never returns provider candidates to the meeting engine. It only
compares structurally valid observations with deterministic analyzer output and
returns counters suitable for local pilot evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.contracts import TranscriptEvent
from app.domain.semantic_candidates import validate_semantic_observations
from app.services.analyzer import analyze
from app.services.semantic_candidate_analyzer import SemanticCandidateAnalyzer


@dataclass(frozen=True)
class SemanticShadowCounters:
    windows_evaluated: int = 0
    provider_failures: int = 0
    validation_failures: int = 0
    pain_candidates: int = 0
    fact_candidates: int = 0
    pain_agreements: int = 0
    fact_agreements: int = 0


class SemanticShadowObserver:
    """Compare candidate output without exposing it or changing domain truth."""

    def __init__(self, analyzer: SemanticCandidateAnalyzer) -> None:
        self._analyzer = analyzer

    def evaluate(self, events: list[TranscriptEvent]) -> SemanticShadowCounters:
        final_events = [event for event in events if event.is_final]
        if not final_events:
            return SemanticShadowCounters()
        try:
            batch = self._analyzer.observe(final_events)
        except Exception:
            return SemanticShadowCounters(
                windows_evaluated=1,
                provider_failures=1,
            )
        try:
            validated = validate_semantic_observations(batch, final_events)
        except Exception:
            return SemanticShadowCounters(
                windows_evaluated=1,
                validation_failures=1,
            )

        deterministic_pains: set[str] = set()
        deterministic_facts: set[tuple[str, str, str]] = set()
        for event in final_events:
            result = analyze(event)
            if result.detected_pain is not None:
                deterministic_pains.add(result.detected_pain.template_id)
            deterministic_facts.update(
                (fact.template_id, fact.slot, fact.value) for fact in result.facts
            )

        pain_agreements = sum(
            candidate.category in deterministic_pains for candidate in validated.pains
        )
        fact_agreements = sum(
            (
                candidate.pain_category_hint,
                candidate.slot,
                candidate.value,
            )
            in deterministic_facts
            for candidate in validated.facts
        )
        return SemanticShadowCounters(
            windows_evaluated=1,
            pain_candidates=len(validated.pains),
            fact_candidates=len(validated.facts),
            pain_agreements=pain_agreements,
            fact_agreements=fact_agreements,
        )
