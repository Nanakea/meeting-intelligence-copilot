"""Deterministic question selection (pure, category-generic).

Operates only on InformationGap objects (never raw transcript strings). Uses the
template-specific priority (slot order) and the template's localized question
wording for the meeting language. Emits at most one ASK NOW and at most two
FOLLOW UP active suggestions. Suggestion identity is (pain_id, slot) so a gap maps
to a single suggestion — no duplicates for the same pain+gap, and a resolved gap
produces no active suggestion (retraction). Question wording is never used as
identity."""

from __future__ import annotations

from app.domain.contracts import (
    GapStatus,
    InformationGap,
    MeetingFact,
    PainPoint,
    QuestionSuggestion,
    SuggestionRole,
    SuggestionStatus,
)
from app.domain.priority import solution_lead_priority
from app.domain.templates import TEMPLATES

_MAX_SUGGESTIONS = 3  # 1 ASK NOW + 2 FOLLOW UP
_DEFAULT_LANG = "ja"


def select_suggestions(
    pains: list[PainPoint],
    gaps: list[InformationGap],
    lang: str = _DEFAULT_LANG,
    facts: list[MeetingFact] | None = None,
) -> list[QuestionSuggestion]:
    template_of = {p.pain_id: p.template_id for p in pains}

    open_gaps = [g for g in gaps if g.status == GapStatus.open]
    ranked_gaps = solution_lead_priority.rank(open_gaps, pains, facts or [])

    suggestions: list[QuestionSuggestion] = []
    for rank, ranked_gap in enumerate(ranked_gaps[:_MAX_SUGGESTIONS]):
        gap = ranked_gap.gap
        template = TEMPLATES.get(template_of.get(gap.pain_id, ""))
        if template is None:
            continue
        # No cross-language fallback: a missing translation must not silently render
        # the wrong language (DOMAIN_MODEL §1.5). Skip rather than mislocalize.
        question = template.question(lang, gap.slot)
        if question is None:
            continue
        suggestions.append(
            QuestionSuggestion(
                suggestion_id=f"{gap.pain_id}::{gap.slot}",
                gap_id=gap.gap_id,
                role=SuggestionRole.ask_now if rank == 0 else SuggestionRole.follow_up,
                text=question.text,
                reason=(
                    f"{question.reason} Priority signal: {ranked_gap.signal.replace('_', ' ')}."
                    if ranked_gap.signal and lang == "en"
                    else f"{question.reason} 優先シグナル: {ranked_gap.signal}."
                    if ranked_gap.signal
                    else question.reason
                ),
                status=SuggestionStatus.active,
                priority=ranked_gap.effective_priority,
            )
        )
    return suggestions
