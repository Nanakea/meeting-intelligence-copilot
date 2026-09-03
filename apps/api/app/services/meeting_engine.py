"""Backend-owned meeting engine — the single source of truth (CLAUDE §Architecture).

Holds the append-only transcript log and the derived MeetingState. For every
accepted final TranscriptEvent it: dedupes by event_id, appends the event,
analyzes it, reduces facts/pains, recomputes gaps, reselects suggestions, and
bumps ``version``. Duplicate or non-final events are ignored and do not bump."""

from __future__ import annotations

import hashlib

from app.domain.analysis import AnalysisResult, DetectedProblemCandidate, ProposedFact
from app.domain.context import EntityGlossaryEntry
from app.domain.contracts import (
    MeetingState,
    PainStatus,
    ProblemIdentityDecision,
    TranscriptEvent,
    empty_meeting_state,
)
from app.domain.reducer import (
    ambiguous_identity_promotion_keys,
    apply_analysis,
    apply_identity_decision,
    compute_gaps,
    count_ambiguous_fact_routes,
)
from app.domain.selector import select_suggestions
from app.domain.semantic_candidates import SemanticProblemHint
from app.services.analyzer import analyze
from app.services.problem_instances import identify_problem

_PAIN_CONTEXT_MAX_EVENTS = 4
_PAIN_CONTEXT_MAX_SECONDS = 30.0


class MeetingEngine:
    def __init__(
        self,
        meeting_id: str,
        glossary: list[EntityGlossaryEntry] | None = None,
        *,
        allow_identity_promotion: bool = True,
    ) -> None:
        self.meeting_id = meeting_id
        self.state = empty_meeting_state(meeting_id)
        self._seen: set[str] = set()
        self._glossary = glossary or []
        self._allow_identity_promotion = allow_identity_promotion

    @classmethod
    def from_state(
        cls,
        state: MeetingState,
        glossary: list[EntityGlossaryEntry] | None = None,
    ) -> "MeetingEngine":
        engine = cls(state.meeting_id, glossary)
        engine.state = state
        engine._seen = {event.event_id for event in state.transcript}
        return engine

    def apply(self, event: TranscriptEvent) -> bool:
        """Fold one event into the state. Returns True iff the state advanced."""

        if not event.is_final:
            return False
        if event.event_id in self._seen:
            return False  # dedupe by event_id
        self._seen.add(event.event_id)

        # Preserve prior snapshots while extending the append-only ground-truth
        # log. model_construct below prevents Pydantic from deep-copying and
        # revalidating this complete history a second time.
        transcript = [*self.state.transcript, event]
        result = analyze(event, self._glossary)
        ambiguous_identity_keys = (
            ambiguous_identity_promotion_keys(self.state.pain_points, result, event.seq)
            if self._allow_identity_promotion
            else set()
        )
        pains, facts = apply_analysis(
            self.meeting_id,
            event.seq,
            event.event_id,
            self.state.pain_points,
            self.state.facts,
            result,
            allow_identity_promotion=self._allow_identity_promotion,
        )
        ambiguous_routes = count_ambiguous_fact_routes(pains, result)
        ambiguous_routes += len(ambiguous_identity_keys)
        prior_pain_ids = {pain.pain_id for pain in self.state.pain_points}
        new_pain_ids = {
            pain.pain_id
            for pain in pains
            if pain.pain_id not in prior_pain_ids
            and pain.created_seq == event.seq
            and (pain.template_id, pain.instance_key) not in ambiguous_identity_keys
        }
        if new_pain_ids:
            pains, facts = self._backfill_new_pain_context(
                transcript,
                pains,
                facts,
                new_pain_ids,
            )
        gaps = compute_gaps(self.meeting_id, pains, facts, prior_gaps=self.state.gaps)
        # Questions are rendered in the meeting language (from the explicit event lang).
        suggestions = select_suggestions(pains, gaps, lang=str(event.lang.value), facts=facts)

        # All values below were produced by validated domain contracts and pure
        # reducers. model_construct avoids a second Pydantic copy and validation
        # pass while retaining an immutable prior versioned aggregate.
        self.state = MeetingState.model_construct(
            meeting_id=self.meeting_id,
            version=self.state.version + 1,
            transcript=transcript,
            pain_points=pains,
            facts=facts,
            gaps=gaps,
            suggestions=suggestions,
            last_event_seq=event.seq,
            ambiguous_routing_count=(self.state.ambiguous_routing_count + ambiguous_routes),
            identity_decisions=self.state.identity_decisions,
            confirmed_semantic_hint_ids=self.state.confirmed_semantic_hint_ids,
        )
        return True

    def decide_identity(
        self,
        action: str,
        pain_ids: list[str],
        survivor_pain_id: str | None = None,
    ) -> ProblemIdentityDecision:
        normalized_ids = sorted(dict.fromkeys(pain_ids))
        if len(normalized_ids) != 2:
            raise ValueError("identity decisions require two distinct problems")
        digest = hashlib.sha256(
            "\0".join([self.meeting_id, action, *normalized_ids]).encode()
        ).hexdigest()[:24]
        decision = ProblemIdentityDecision(
            decision_id=f"identity-{digest}",
            action=action,
            pain_ids=normalized_ids,
            survivor_pain_id=survivor_pain_id,
            created_version=self.state.version,
        )
        pains, decisions = apply_identity_decision(
            self.state.pain_points, self.state.identity_decisions, decision
        )
        gaps = compute_gaps(self.meeting_id, pains, self.state.facts, prior_gaps=self.state.gaps)
        lang = self.state.transcript[-1].lang.value if self.state.transcript else "en"
        self.state = self.state.model_copy(
            update={
                "version": self.state.version + 1,
                "pain_points": pains,
                "identity_decisions": decisions,
                "gaps": gaps,
                "suggestions": select_suggestions(pains, gaps, lang=lang, facts=self.state.facts),
            }
        )
        return decisions[-1]

    def confirm_semantic_hint(self, hint: SemanticProblemHint) -> bool:
        if hint.session_id != self.meeting_id:
            raise ValueError("semantic hint belongs to another session")
        if hint.hint_id in self.state.confirmed_semantic_hint_ids:
            return False
        event_by_id = {event.event_id: event for event in self.state.transcript}
        evidence = [event_by_id.get(event_id) for event_id in hint.evidence_event_ids]
        if not evidence or any(event is None for event in evidence):
            raise ValueError("semantic hint evidence is unavailable")
        event = max((item for item in evidence if item is not None), key=lambda item: item.seq)
        proposed = [
            ProposedFact(
                template_id=hint.category,
                slot=fact.slot,
                value=fact.value,
                evidence_event_id=fact.evidence_event_ids[0],
                kind=fact.kind.value,
                extraction_origin="semantic_confirmed",
            )
            for fact in hint.facts
        ]
        identity = identify_problem(hint.category, event, proposed, self._glossary)
        proposed = [
            ProposedFact(
                template_id=fact.template_id,
                slot=fact.slot,
                value=fact.value,
                evidence_event_id=fact.evidence_event_id,
                instance_key=identity.instance_key,
                kind=fact.kind,
                relation=fact.relation,
                extraction_origin=fact.extraction_origin,
            )
            for fact in proposed
        ]
        result = AnalysisResult(
            detected_pains=[
                DetectedProblemCandidate(
                    template_id=hint.category,
                    title=hint.subject,
                    evidence_event_id=hint.evidence_event_ids[0],
                    instance_key=identity.instance_key,
                    identity_status=identity.status,
                    subject=identity.subject or hint.subject,
                )
            ],
            facts=proposed,
        )
        pains, facts = apply_analysis(
            self.meeting_id,
            event.seq,
            event.event_id,
            self.state.pain_points,
            self.state.facts,
            result,
        )
        gaps = compute_gaps(self.meeting_id, pains, facts, prior_gaps=self.state.gaps)
        self.state = self.state.model_copy(
            update={
                "version": self.state.version + 1,
                "pain_points": pains,
                "facts": facts,
                "gaps": gaps,
                "suggestions": select_suggestions(pains, gaps, lang=event.lang.value, facts=facts),
                "confirmed_semantic_hint_ids": [
                    *self.state.confirmed_semantic_hint_ids,
                    hint.hint_id,
                ],
            }
        )
        return True

    def _backfill_new_pain_context(
        self,
        transcript: list[TranscriptEvent],
        pains,
        facts,
        new_pain_ids: set[str],
    ):
        """Attach nearby pre-pain evidence after a template first activates."""

        # The current event was already reduced once. Replaying the bounded
        # context in transcript order preserves supersede chronology.
        facts = [fact for fact in facts if fact.pain_id not in new_pain_ids]
        current = transcript[-1]
        context = [
            candidate
            for candidate in transcript[-_PAIN_CONTEXT_MAX_EVENTS:]
            if candidate is current
            or current.ts_start - candidate.ts_end <= _PAIN_CONTEXT_MAX_SECONDS
        ]
        for candidate in context:
            candidate_result = analyze(candidate, self._glossary)
            relevant_facts = []
            for fact in candidate_result.facts:
                compatible = [
                    pain
                    for pain in pains
                    if pain.template_id == fact.template_id and pain.status is PainStatus.open
                ]
                if fact.instance_key is None:
                    if len(compatible) != 1:
                        continue
                    target = compatible[0]
                else:
                    target = next(
                        (pain for pain in compatible if pain.instance_key == fact.instance_key),
                        None,
                    )
                if target is None or target.pain_id not in new_pain_ids:
                    continue
                relevant_facts.append(
                    ProposedFact(
                        template_id=fact.template_id,
                        slot=fact.slot,
                        value=fact.value,
                        evidence_event_id=fact.evidence_event_id,
                        instance_key=target.instance_key,
                        kind=fact.kind,
                        relation=fact.relation,
                        extraction_origin=fact.extraction_origin,
                    )
                )
            if not relevant_facts:
                continue
            pains, facts = apply_analysis(
                self.meeting_id,
                candidate.seq,
                candidate.event_id,
                pains,
                facts,
                AnalysisResult(facts=relevant_facts),
            )
        return pains, facts
