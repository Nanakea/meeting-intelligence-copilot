"""Transport-only contracts for the compact live intelligence protocol."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.transcript.live_registry import LiveIngestStatus
from app.domain.contracts import (
    InformationGap,
    MeetingFact,
    MeetingState,
    PainPoint,
    QuestionSuggestion,
)
from app.domain.governance import GovernanceAlert, GovernanceCandidate
from app.domain.semantic_candidates import SemanticProblemHint

MAX_LIVE_EVIDENCE_EVENT_IDS = 8


def _bounded_live_evidence(event_ids: list[str]) -> list[str]:
    """Keep origin and recent evidence in the UI projection.

    The authoritative MeetingState and append-only cache retain the complete
    evidence history. The live panel needs traceability, not an ever-growing
    duplicate-restatement payload on every WebSocket update.
    """

    if len(event_ids) <= MAX_LIVE_EVIDENCE_EVENT_IDS:
        return list(event_ids)
    return [event_ids[0], *event_ids[-(MAX_LIVE_EVIDENCE_EVENT_IDS - 1) :]]


class LiveIngestAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: LiveIngestStatus
    received_sequence_id: int | None
    next_expected_sequence_id: int
    state_version: int


class LiveBatchAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: LiveIngestStatus
    received_sequence_id: int | None
    next_expected_sequence_id: int
    state_version: int
    rejected_sequence_ids: list[int] = Field(default_factory=list)


class ProblemIdentityReviewCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    category: str
    first_pain_id: str
    second_pain_id: str
    first_subject: str
    second_subject: str


class LiveIntelligenceSnapshot(BaseModel):
    """Compact live projection; transcript remains owned by Meetily's transcript UI."""

    model_config = ConfigDict(extra="forbid")

    meeting_id: str
    version: int = 0
    pain_points: list[PainPoint] = Field(default_factory=list)
    facts: list[MeetingFact] = Field(default_factory=list)
    gaps: list[InformationGap] = Field(default_factory=list)
    suggestions: list[QuestionSuggestion] = Field(default_factory=list)
    last_event_seq: int = -1
    ambiguous_routing_count: int = Field(default=0, ge=0)
    semantic_hints: list[SemanticProblemHint] = Field(default_factory=list, max_length=8)
    identity_review_candidates: list[ProblemIdentityReviewCandidate] = Field(
        default_factory=list, max_length=8
    )
    governance_candidates: list[GovernanceCandidate] = Field(default_factory=list, max_length=12)
    governance_alerts: list[GovernanceAlert] = Field(default_factory=list, max_length=12)

    @classmethod
    def from_state(
        cls,
        state: MeetingState,
        semantic_hints: list[SemanticProblemHint] | None = None,
        governance_candidates: list[GovernanceCandidate] | None = None,
        governance_alerts: list[GovernanceAlert] | None = None,
    ) -> LiveIntelligenceSnapshot:
        return cls(
            meeting_id=state.meeting_id,
            version=state.version,
            pain_points=[
                pain.model_copy(
                    update={"evidence_event_ids": _bounded_live_evidence(pain.evidence_event_ids)}
                )
                for pain in state.pain_points
            ],
            facts=[
                fact.model_copy(
                    update={"evidence_event_ids": _bounded_live_evidence(fact.evidence_event_ids)}
                )
                for fact in state.facts
            ],
            gaps=state.gaps,
            suggestions=state.suggestions,
            last_event_seq=state.last_event_seq,
            ambiguous_routing_count=state.ambiguous_routing_count,
            semantic_hints=(semantic_hints or [])[:8],
            identity_review_candidates=_identity_review_candidates(state),
            governance_candidates=(governance_candidates or [])[:12],
            governance_alerts=(governance_alerts or [])[:12],
        )


def _identity_review_candidates(
    state: MeetingState,
) -> list[ProblemIdentityReviewCandidate]:
    separated = {
        tuple(sorted(decision.pain_ids))
        for decision in state.identity_decisions
        if decision.action == "keep_separate"
    }
    active = [
        pain
        for pain in state.pain_points
        if pain.status.value == "open" and pain.merged_into_pain_id is None
    ]
    candidates: list[ProblemIdentityReviewCandidate] = []
    for index, first in enumerate(active):
        for second in active[index + 1 :]:
            pair = tuple(sorted([first.pain_id, second.pain_id]))
            if first.template_id != second.template_id or pair in separated:
                continue
            if (
                first.identity_status.value == "anchored"
                and second.identity_status.value == "anchored"
                and first.instance_key != second.instance_key
            ):
                continue
            candidates.append(
                ProblemIdentityReviewCandidate(
                    review_id=f"identity-review-{len(candidates) + 1}",
                    category=first.template_id,
                    first_pain_id=first.pain_id,
                    second_pain_id=second.pain_id,
                    first_subject=first.subject or first.title,
                    second_subject=second.subject or second.title,
                )
            )
            if len(candidates) == 8:
                return candidates
    return candidates
