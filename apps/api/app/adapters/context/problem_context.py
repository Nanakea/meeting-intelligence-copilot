"""Authoritative, deterministic context query composition for one problem."""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.context.question_context import _ENTITY_HINTS, _fragments
from app.domain.context import ConnectedProblemContextRequest, ContextSearchQuery
from app.domain.contracts import FactStatus, GapStatus, MeetingState, PainStatus


@dataclass(frozen=True)
class ProblemContextResolution:
    query: ContextSearchQuery


def resolve_problem_context(
    state: MeetingState | None,
    request: ConnectedProblemContextRequest,
    *,
    principal_id: str,
) -> ProblemContextResolution | None:
    if state is None or state.version != request.state_version:
        return None
    pain = next(
        (
            candidate
            for candidate in state.pain_points
            if candidate.pain_id == request.pain_id
            and candidate.status is PainStatus.open
            and candidate.merged_into_pain_id is None
        ),
        None,
    )
    if pain is None:
        return None
    lineage_ids = {
        candidate.pain_id
        for candidate in state.pain_points
        if candidate.pain_id == pain.pain_id
        or candidate.merged_into_pain_id == pain.pain_id
    }
    active_facts = [
        fact
        for fact in state.facts
        if fact.pain_id in lineage_ids and fact.status is FactStatus.active
    ]
    open_gaps = sorted(
        (
            gap
            for gap in state.gaps
            if gap.pain_id == pain.pain_id and gap.status is GapStatus.open
        ),
        key=lambda gap: gap.base_priority,
    )
    event_languages = {event.event_id: event.lang.value for event in state.transcript}
    language = next(
        (
            event_languages[event_id]
            for event_id in pain.evidence_event_ids
            if event_id in event_languages
        ),
        state.transcript[-1].lang.value if state.transcript else "en",
    )
    terms: list[str] = []
    for value in [
        pain.subject or pain.title,
        *(fact.value for fact in active_facts),
        *(gap.slot.replace("_", " ") for gap in open_gaps[:3]),
    ]:
        terms.extend(_fragments(value, lang=language))
    query_text = " ".join(dict.fromkeys(terms)).strip()[:2_000]
    if not query_text:
        query_text = pain.title[:2_000]
    known_values: dict[str, list[str]] = {}
    for fact in active_facts:
        known_values.setdefault(fact.slot, []).append(fact.value)
    target_slot = open_gaps[0].slot if open_gaps else None
    return ProblemContextResolution(
        query=ContextSearchQuery(
            text=query_text,
            principal_id=principal_id,
            connector_ids=request.connector_ids,
            preferred_entity_types=(
                _ENTITY_HINTS.get((pain.template_id, target_slot), [])
                if target_slot is not None
                else []
            ),
            target_slot=target_slot,
            known_values=known_values,
            limit=request.limit,
        )
    )
