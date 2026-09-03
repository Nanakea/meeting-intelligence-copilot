"""Pure authoritative projection of MeetingState into reviewable issue drafts."""

from __future__ import annotations

import hashlib

from app.domain.context import (
    ContextSearchQuery,
    IssueFact,
    IssueHistoryWarning,
    IssueQuestion,
    StructuredIssueDraft,
)
from app.domain.contracts import FactStatus, GapStatus, MeetingState
from app.domain.templates import ISSUE_FIELD_MAPS, ISSUE_REQUIRED_SLOTS, TEMPLATES


def _values(active_by_slot: dict[str, str], slots: tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(active_by_slot[slot] for slot in slots if slot in active_by_slot))


def build_issue_draft(state: MeetingState, pain_id: str) -> StructuredIssueDraft | None:
    pain = next((item for item in state.pain_points if item.pain_id == pain_id), None)
    if pain is None:
        return None
    template = TEMPLATES.get(pain.template_id)
    mapping = ISSUE_FIELD_MAPS.get(pain.template_id)
    if template is None or mapping is None:
        return None
    evidence_lang = {
        event.event_id: event.lang.value for event in state.transcript
    }
    language = next(
        (
            evidence_lang[event_id]
            for event_id in pain.evidence_event_ids
            if event_id in evidence_lang
        ),
        state.transcript[-1].lang.value if state.transcript else "en",
    )
    lineage_ids = {
        item.pain_id
        for item in state.pain_points
        if item.pain_id == pain_id or item.merged_into_pain_id == pain_id
    }
    related = [fact for fact in state.facts if fact.pain_id in lineage_ids]
    active = [fact for fact in related if fact.status is FactStatus.active]
    active_by_slot = {fact.slot: fact.value for fact in active}
    open_gaps = [
        gap
        for gap in state.gaps
        if gap.pain_id == pain_id and gap.status is GapStatus.open
    ]
    missing_required = [
        slot
        for slot in ISSUE_REQUIRED_SLOTS.get(pain.template_id, ())
        if slot not in active_by_slot
    ]
    identity = "\0".join([state.meeting_id, pain_id, str(state.version)])
    return StructuredIssueDraft(
        draft_id=f"draft-{hashlib.sha256(identity.encode()).hexdigest()[:24]}",
        session_id=state.meeting_id,
        pain_id=pain_id,
        state_version=state.version,
        template_id=pain.template_id,
        language=language,
        title=pain.subject or pain.title,
        problem=_values(active_by_slot, mapping["problem"]),
        impact=_values(active_by_slot, mapping["impact"]),
        affected_systems=_values(active_by_slot, mapping["systems"]),
        scope=_values(active_by_slot, mapping["scope"]),
        owner=next(iter(_values(active_by_slot, mapping["owner"])), None),
        workaround=_values(active_by_slot, mapping["workaround"]),
        acceptance_criteria=_values(active_by_slot, mapping["acceptance"]),
        facts=[
            IssueFact(
                slot=fact.slot,
                value=fact.value,
                kind=fact.kind.value,
                extraction_origin=fact.extraction_origin.value,
                evidence_event_ids=fact.evidence_event_ids,
            )
            for fact in active
        ],
        history_warnings=[
            IssueHistoryWarning(
                slot=fact.slot,
                value=fact.value,
                status=fact.status.value,
                evidence_event_ids=fact.evidence_event_ids,
            )
            for fact in related
            if fact.status is not FactStatus.active
        ],
        unresolved_questions=[
            IssueQuestion(
                slot=gap.slot,
                question=(
                    template.question(language, gap.slot).text
                    if template.question(language, gap.slot) is not None
                    else gap.slot
                ),
                priority=gap.base_priority,
            )
            for gap in sorted(open_gaps, key=lambda item: item.base_priority)
        ],
        context_status="not_requested",
        readiness=(
            "needs_clarification" if missing_required else "ready_for_review"
        ),
        missing_required_fields=missing_required,
    )


def context_query_for_draft(
    draft: StructuredIssueDraft,
    connector_ids: list[str],
) -> ContextSearchQuery:
    text = " ".join(
        dict.fromkeys(
            [
                draft.title,
                *draft.problem,
                *draft.affected_systems,
                *draft.scope,
                *draft.impact,
            ]
        )
    )[:2_000]
    known_values: dict[str, list[str]] = {}
    for fact in draft.facts:
        known_values.setdefault(fact.slot, []).append(fact.value)
    return ContextSearchQuery(
        text=text,
        principal_id="backend-owned",
        connector_ids=connector_ids,
        target_slot=(
            draft.missing_required_fields[0]
            if draft.missing_required_fields
            else None
        ),
        known_values=known_values,
        limit=8,
    )
