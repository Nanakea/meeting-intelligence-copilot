"""Asynchronous, opt-in semantic hints that never block authoritative analysis."""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from collections.abc import Callable

from app.domain.contracts import MeetingState, TranscriptEvent
from app.domain.semantic_candidates import (
    SemanticHintFact,
    SemanticObservationBatch,
    SemanticProblemHint,
)
from app.domain.templates import TEMPLATES
from app.services.semantic_candidate_analyzer import SemanticCandidateAnalyzer

MINIMUM_HINT_CONFIDENCE = 0.90
_QUESTION_RE = re.compile(
    r"(?:\?|？|でしょうか|ますか|どなた|どちら|\b(?:what|who|which|how|why|could|would)\b)",
    re.IGNORECASE,
)
_HYPOTHETICAL_RE = re.compile(
    r"(?:もし|仮に|例えば|たとえば|\b(?:if|suppose|hypothetically|for example)\b)",
    re.IGNORECASE,
)
_QUOTED_RE = re.compile(r"(?:^[\s]*[\"'“‘「『]|[\"'”’」』][\s]*$)")
_NEGATION_RE = re.compile(
    r"(?:問題(?:は)?ない|発生していない|遅れていない|"
    r"\b(?:no issue|not failing|does not fail|isn't delayed|not delayed)\b)",
    re.IGNORECASE,
)


class SemanticHintRuntime:
    def __init__(self, analyzer: SemanticCandidateAnalyzer) -> None:
        self._analyzer = analyzer
        self._hints: dict[str, dict[str, SemanticProblemHint]] = {}
        self._dismissed: dict[str, set[str]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._pending: dict[str, tuple[MeetingState, Callable[[str], None]]] = {}
        self._last_observed_version: dict[str, int] = {}

    def schedule(
        self,
        state: MeetingState,
        on_ready: Callable[[str], None],
    ) -> None:
        if (
            not state.transcript
            or self._last_observed_version.get(state.meeting_id) == state.version
        ):
            return
        self._pending[state.meeting_id] = (state, on_ready)
        current = self._tasks.get(state.meeting_id)
        if current is not None and not current.done():
            return
        self._tasks[state.meeting_id] = asyncio.create_task(
            self._drain(state.meeting_id)
        )

    async def _drain(self, session_id: str) -> None:
        try:
            while pending := self._pending.pop(session_id, None):
                state, on_ready = pending
                try:
                    batch = await asyncio.wait_for(
                        asyncio.to_thread(self._analyzer.observe, state.transcript),
                        timeout=5.0,
                    )
                    hints = _validated_hints(state, batch)
                    dismissed = self._dismissed.get(session_id, set())
                    confirmed = set(state.confirmed_semantic_hint_ids)
                    self._hints[session_id] = {
                        hint.hint_id: hint
                        for hint in hints
                        if hint.hint_id not in dismissed and hint.hint_id not in confirmed
                    }
                    self._last_observed_version[session_id] = state.version
                    on_ready(session_id)
                except TimeoutError:
                    continue
                except Exception:
                    continue
        except asyncio.CancelledError:
            return
        finally:
            self._tasks.pop(session_id, None)

    def hints(self, session_id: str) -> list[SemanticProblemHint]:
        return sorted(
            self._hints.get(session_id, {}).values(),
            key=lambda hint: (-hint.confidence, hint.hint_id),
        )

    def seed_dismissed(self, session_id: str, hint_ids: set[str]) -> None:
        if hint_ids:
            self._dismissed.setdefault(session_id, set()).update(hint_ids)

    def decide(
        self,
        session_id: str,
        hint_id: str,
        action: str,
    ) -> SemanticProblemHint | None:
        hint = self._hints.get(session_id, {}).pop(hint_id, None)
        if hint is None:
            raise KeyError(hint_id)
        if action == "dismiss":
            self._dismissed.setdefault(session_id, set()).add(hint_id)
            return None
        if action != "confirm":
            raise ValueError("unsupported semantic hint decision")
        return hint

    def reset(self, session_id: str) -> None:
        task = self._tasks.pop(session_id, None)
        if task is not None:
            task.cancel()
        self._hints.pop(session_id, None)
        self._dismissed.pop(session_id, None)
        self._pending.pop(session_id, None)
        self._last_observed_version.pop(session_id, None)

    def reset_all(self) -> None:
        for session_id in list(
            {*self._tasks, *self._hints, *self._pending, *self._dismissed}
        ):
            self.reset(session_id)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._pending.clear()


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _eligible_event(event: TranscriptEvent) -> bool:
    text = event.text.strip()
    return bool(text) and not any(
        pattern.search(text)
        for pattern in (_QUESTION_RE, _HYPOTHETICAL_RE, _QUOTED_RE, _NEGATION_RE)
    )


def _validated_hints(
    state: MeetingState,
    batch: SemanticObservationBatch,
) -> list[SemanticProblemHint]:
    events = {event.event_id: event for event in state.transcript}
    hints: list[SemanticProblemHint] = []
    for pain in batch.pains:
        if pain.confidence < MINIMUM_HINT_CONFIDENCE or pain.category not in TEMPLATES:
            continue
        evidence = [events.get(event_id) for event_id in pain.evidence_event_ids]
        if not evidence or any(event is None or not _eligible_event(event) for event in evidence):
            continue
        languages = {event.lang.value for event in evidence if event is not None}
        if len(languages) != 1:
            continue
        language = next(iter(languages))
        facts = []
        fact_confidences = [pain.confidence]
        for fact in batch.facts:
            if (
                fact.pain_category_hint != pain.category
                or fact.confidence < MINIMUM_HINT_CONFIDENCE
            ):
                continue
            cited = [events.get(event_id) for event_id in fact.evidence_event_ids]
            if not cited or any(event is None or not _eligible_event(event) for event in cited):
                continue
            if any(
                event is None or event.lang.value != language
                for event in cited
            ):
                continue
            if not any(
                fact.value.strip() in event.text
                for event in cited
                if event is not None
            ):
                continue
            facts.append(
                SemanticHintFact(
                    slot=fact.slot,
                    value=fact.value,
                    kind=fact.kind,
                    evidence_event_ids=fact.evidence_event_ids,
                )
            )
            fact_confidences.append(fact.confidence)
        evidence_ids = list(
            dict.fromkeys(
                [
                    *pain.evidence_event_ids,
                    *(item for fact in facts for item in fact.evidence_event_ids),
                ]
            )
        )
        fingerprint = "\0".join(
            [
                state.meeting_id,
                pain.category,
                *sorted(evidence_ids),
                *(f"{fact.slot}:{_normalized(fact.value)}" for fact in facts),
            ]
        )
        hint_id = f"hint-{hashlib.sha256(fingerprint.encode()).hexdigest()[:24]}"
        hints.append(
            SemanticProblemHint(
                hint_id=hint_id,
                session_id=state.meeting_id,
                category=pain.category,
                subject=TEMPLATES[pain.category].title_for(language),
                language=language,
                evidence_event_ids=evidence_ids,
                facts=facts,
                confidence=min(fact_confidences),
                state_version=state.version,
            )
        )
    return hints
