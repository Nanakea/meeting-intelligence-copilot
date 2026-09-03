"""Deterministic, state-bound query composition for supplemental context."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.context import ConnectedQuestionContextRequest, ContextSearchQuery
from app.domain.contracts import (
    FactStatus,
    GapStatus,
    MeetingState,
    QuestionSuggestion,
    SuggestionRole,
    SuggestionStatus,
)

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,63}")
_EN_STOP_WORDS = {
    "about",
    "could",
    "currently",
    "does",
    "from",
    "have",
    "information",
    "please",
    "question",
    "should",
    "still",
    "that",
    "their",
    "there",
    "this",
    "what",
    "which",
    "with",
    "would",
}
_ENTITY_HINTS: dict[tuple[str, str], list[str]] = {
    ("data_mismatch", "source_of_truth"): ["Items", "products"],
    ("data_mismatch", "affected_scope"): ["Items", "products"],
    ("integration_failure", "affected_scope"): ["Orders", "salesorders"],
    ("manual_work", "owner"): ["BusinessPartners", "contacts"],
    ("process_delay", "business_impact"): ["Orders", "salesorders"],
    ("unclear_ownership", "owner"): ["BusinessPartners", "contacts"],
}


@dataclass(frozen=True)
class QuestionContextResolution:
    suggestion: QuestionSuggestion
    query: ContextSearchQuery


def _fragments(text: str, *, lang: str) -> list[str]:
    latin = [token for token in _TOKEN.findall(text) if token.casefold() not in _EN_STOP_WORDS]
    if lang == "en":
        return latin
    clauses = [
        clause.strip()
        for clause in re.split(r"[、。！？?\s]+", text)
        if 1 < len(clause.strip()) <= 48
    ]
    return [*latin, *clauses]


def resolve_question_context(
    state: MeetingState | None,
    request: ConnectedQuestionContextRequest,
    *,
    principal_id: str,
) -> QuestionContextResolution | None:
    if state is None or state.version != request.state_version:
        return None
    suggestion = next(
        (
            candidate
            for candidate in state.suggestions
            if candidate.suggestion_id == request.suggestion_id
            and candidate.role is SuggestionRole.ask_now
            and candidate.status is SuggestionStatus.active
        ),
        None,
    )
    if suggestion is None:
        return None
    gap = next(
        (
            candidate
            for candidate in state.gaps
            if candidate.gap_id == suggestion.gap_id and candidate.status is GapStatus.open
        ),
        None,
    )
    if gap is None:
        return None
    pain = next(
        (candidate for candidate in state.pain_points if candidate.pain_id == gap.pain_id),
        None,
    )
    if pain is None:
        return None

    active_facts = [
        fact
        for fact in state.facts
        if fact.pain_id == pain.pain_id and fact.status is FactStatus.active
    ]
    evidence_languages = {
        event.event_id: event.lang.value for event in state.transcript
    }
    lang = next(
        (
            evidence_languages[event_id]
            for event_id in pain.evidence_event_ids
            if event_id in evidence_languages
        ),
        state.transcript[-1].lang.value if state.transcript else "en",
    )
    terms: list[str] = []
    for value in [suggestion.text, pain.title, *(fact.value for fact in active_facts)]:
        terms.extend(_fragments(value, lang=lang))
    query_text = " ".join(dict.fromkeys(terms)).strip()[:2_000]
    if not query_text:
        query_text = suggestion.text[:2_000]
    known_values: dict[str, list[str]] = {}
    for fact in active_facts:
        known_values.setdefault(fact.slot, []).append(fact.value)
    return QuestionContextResolution(
        suggestion=suggestion,
        query=ContextSearchQuery(
            text=query_text,
            principal_id=principal_id,
            connector_ids=request.connector_ids,
            preferred_entity_types=_ENTITY_HINTS.get((pain.template_id, gap.slot), []),
            target_slot=gap.slot,
            known_values=known_values,
            limit=request.limit,
        ),
    )
