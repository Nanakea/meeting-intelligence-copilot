"""Provider-neutral semantic observations and fail-closed structural validation.

These models are an internal candidate boundary, not wire contracts and not
MeetingState. Validation only proves that a candidate is structurally eligible
for a future adapter: it does not prove that the cited transcript semantically
supports the proposed value. Nothing in this module applies candidates to the
reducer or creates gaps or questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.contracts import TranscriptEvent
from app.domain.templates import TEMPLATES


class CandidateKind(str, Enum):
    """Candidate provenance; it must survive future adaptation unchanged."""

    evidence = "evidence"
    inference = "inference"


class _CandidateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PainCandidate(_CandidateModel):
    category: str = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: CandidateKind
    evidence_event_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)


class FactCandidate(_CandidateModel):
    slot: str = Field(min_length=1)
    value: str = Field(min_length=1)
    kind: CandidateKind
    evidence_event_ids: list[str] = Field(min_length=1)
    pain_category_hint: str | None = Field(default=None, min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)


class SemanticHintFact(_CandidateModel):
    slot: str
    value: str
    kind: CandidateKind
    evidence_event_ids: list[str] = Field(min_length=1, max_length=20)


class SemanticProblemHint(_CandidateModel):
    hint_id: str = Field(pattern=r"^hint-[a-f0-9]{24}$")
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    category: str
    subject: str = Field(min_length=1, max_length=160)
    language: str = Field(pattern=r"^(ja|en|ko)$")
    evidence_event_ids: list[str] = Field(min_length=1, max_length=20)
    facts: list[SemanticHintFact] = Field(default_factory=list, max_length=32)
    confidence: float = Field(ge=0.0, le=1.0)
    state_version: int = Field(ge=0)


class SemanticHintDecision(_CandidateModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    state_version: int = Field(ge=0)
    action: str = Field(pattern=r"^(confirm|dismiss)$")


class SemanticObservationBatch(_CandidateModel):
    pains: list[PainCandidate] = Field(default_factory=list)
    facts: list[FactCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_ids_are_nonblank(self) -> Self:
        for candidate in [*self.pains, *self.facts]:
            if any(not event_id for event_id in candidate.evidence_event_ids):
                raise ValueError("candidate evidence_event_ids must not contain blank IDs")
        return self


@dataclass(frozen=True)
class SemanticCandidateValidationIssue:
    location: str
    message: str


class SemanticCandidateValidationError(ValueError):
    """All structural errors found while validating one observation batch."""

    def __init__(self, issues: list[SemanticCandidateValidationIssue]) -> None:
        self.issues = tuple(issues)
        detail = "; ".join(f"{issue.location}: {issue.message}" for issue in issues)
        super().__init__(f"invalid semantic observation batch: {detail}")


def validate_semantic_observations(
    batch: SemanticObservationBatch,
    events: list[TranscriptEvent],
) -> SemanticObservationBatch:
    """Fail closed unless every candidate is structurally grounded.

    This checks registry membership and evidence references only. It deliberately
    does not compare candidate values with transcript meaning; that is a later
    semantic-quality gate.
    """

    supplied_event_ids = {event.event_id for event in events}
    issues: list[SemanticCandidateValidationIssue] = []

    for index, pain in enumerate(batch.pains):
        location = f"pains[{index}]"
        if pain.category not in TEMPLATES:
            issues.append(
                SemanticCandidateValidationIssue(location, f"unknown category {pain.category!r}")
            )
        _validate_evidence_ids(
            location,
            pain.evidence_event_ids,
            supplied_event_ids,
            issues,
        )

    for index, fact in enumerate(batch.facts):
        location = f"facts[{index}]"
        category = fact.pain_category_hint
        if category is None:
            issues.append(
                SemanticCandidateValidationIssue(
                    location,
                    "pain_category_hint is required for slot validation",
                )
            )
        else:
            template = TEMPLATES.get(category)
            if template is None:
                issues.append(
                    SemanticCandidateValidationIssue(location, f"unknown category {category!r}")
                )
            elif fact.slot not in template.slots:
                issues.append(
                    SemanticCandidateValidationIssue(
                        location,
                        f"unknown slot {fact.slot!r} for category {category!r}",
                    )
                )
        _validate_evidence_ids(
            location,
            fact.evidence_event_ids,
            supplied_event_ids,
            issues,
        )

    if issues:
        raise SemanticCandidateValidationError(issues)
    return batch


def _validate_evidence_ids(
    location: str,
    evidence_event_ids: list[str],
    supplied_event_ids: set[str],
    issues: list[SemanticCandidateValidationIssue],
) -> None:
    unknown_ids = sorted(set(evidence_event_ids) - supplied_event_ids)
    if unknown_ids:
        issues.append(
            SemanticCandidateValidationIssue(
                location,
                f"evidence IDs are not present in supplied transcript events: {unknown_ids!r}",
            )
        )
