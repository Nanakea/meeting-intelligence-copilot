"""Analyzer output types (internal, not wire contracts).

The analyzer *proposes*; the reducer *decides* and is the source of truth
(CLAUDE rules 8-9). These carry proposed facts/pains with the evidence they were
drawn from so the reducer can build lifecycle-correct MeetingFacts."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DetectedProblemCandidate:
    template_id: str
    title: str
    evidence_event_id: str
    instance_key: str = "legacy"
    identity_status: str = "provisional"
    subject: str | None = None


@dataclass(frozen=True)
class ProposedFact:
    template_id: str
    slot: str
    value: str
    evidence_event_id: str
    instance_key: str | None = None
    # provenance is always 'evidence' in Slice 1 (rule analyzer extracts stated facts)
    kind: str = "evidence"
    relation: str = "supersede"
    extraction_origin: str = "deterministic"


@dataclass(frozen=True)
class AnalysisResult:
    detected_pains: list[DetectedProblemCandidate] = field(default_factory=list)
    facts: list[ProposedFact] = field(default_factory=list)
    detected_pain: DetectedProblemCandidate | None = None

    def __post_init__(self) -> None:
        """Keep the v4 single-candidate constructor usable during cache/test migration."""

        if self.detected_pain is not None and not self.detected_pains:
            object.__setattr__(self, "detected_pains", [self.detected_pain])
        elif self.detected_pain is None and self.detected_pains:
            object.__setattr__(self, "detected_pain", self.detected_pains[0])


# Transitional internal alias for existing pure analyzer tests and call sites.
DetectedPain = DetectedProblemCandidate
