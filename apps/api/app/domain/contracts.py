"""Domain contracts for the Meeting Intelligence Copilot.

Pure Pydantic types — no I/O, no vendor SDKs, no logic. These mirror
``docs/DOMAIN_MODEL.md`` §1 and are the single source of truth for the wire
protocol; the frontend ``src/types/contracts.ts`` mirrors them and a schema-diff
check guards against drift.

Two orthogonal axes on a fact (DOMAIN_MODEL §3, §4):
  * ``kind``   — provenance: ``evidence`` (stated) | ``inference`` (derived).
  * ``status`` — lifecycle:  ``active`` | ``superseded`` | ``contradicted``.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Contract(BaseModel):
    """Base for all wire contracts: reject unknown fields to keep the schema tight."""

    model_config = ConfigDict(extra="forbid")


# --- enumerations ----------------------------------------------------------

class Lang(str, Enum):
    ja = "ja"
    en = "en"
    ko = "ko"


class FactKind(str, Enum):
    """Provenance axis."""

    evidence = "evidence"
    inference = "inference"


class FactStatus(str, Enum):
    """Lifecycle axis."""

    active = "active"
    superseded = "superseded"
    contradicted = "contradicted"


class ExtractionOrigin(str, Enum):
    deterministic = "deterministic"
    semantic_confirmed = "semantic_confirmed"


class PainStatus(str, Enum):
    open = "open"
    mitigated = "mitigated"


class PainIdentityStatus(str, Enum):
    anchored = "anchored"
    provisional = "provisional"


class GapStatus(str, Enum):
    open = "open"
    answered = "answered"


class SuggestionRole(str, Enum):
    ask_now = "ask_now"
    follow_up = "follow_up"


class SuggestionStatus(str, Enum):
    active = "active"
    retracted = "retracted"


# --- entities --------------------------------------------------------------

class Speaker(_Contract):
    id: str
    display_name: str | None = None
    role: str | None = None


class TranscriptEvent(_Contract):
    """The only input the domain understands. Adapters translate platform payloads into this."""

    event_id: str
    meeting_id: str
    seq: int
    speaker: Speaker
    text: str
    lang: Lang
    ts_start: float
    ts_end: float
    is_final: bool = True
    source: str


class MeetingFact(_Contract):
    fact_id: str
    meeting_id: str
    pain_id: str
    slot: str
    value: str
    kind: FactKind
    extraction_origin: ExtractionOrigin = ExtractionOrigin.deterministic
    status: FactStatus = FactStatus.active
    # Invariant I1: every fact preserves the transcript evidence it derives from.
    evidence_event_ids: list[str] = Field(min_length=1)
    supersedes: str | None = None
    superseded_by: str | None = None
    contradicts: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    created_seq: int


class PainPoint(_Contract):
    pain_id: str
    meeting_id: str
    template_id: str
    title: str
    instance_key: str = "legacy"
    identity_aliases: list[str] = Field(default_factory=list)
    identity_revision: int = Field(default=0, ge=0)
    identity_status: PainIdentityStatus = PainIdentityStatus.provisional
    subject: str | None = None
    merged_into_pain_id: str | None = None
    status: PainStatus = PainStatus.open
    kind: FactKind
    # Invariant I1: pain points also preserve their evidence.
    evidence_event_ids: list[str] = Field(min_length=1)
    created_seq: int


class InformationGap(_Contract):
    # gap_id is a pure function of (pain_id, slot) — invariant I6.
    gap_id: str
    meeting_id: str
    pain_id: str
    slot: str
    status: GapStatus = GapStatus.open
    resolved_by_fact_id: str | None = None
    base_priority: int
    reopen_count: int = 0


class QuestionSuggestion(_Contract):
    suggestion_id: str
    gap_id: str
    role: SuggestionRole
    text: str
    reason: str
    status: SuggestionStatus = SuggestionStatus.active
    priority: int


class ProblemIdentityDecision(_Contract):
    decision_id: str
    action: Literal["merge", "keep_separate"]
    pain_ids: list[str] = Field(min_length=2, max_length=2)
    survivor_pain_id: str | None = None
    created_version: int = Field(ge=0)


class MeetingState(_Contract):
    """Versioned aggregate pushed to the frontend as a full snapshot."""

    meeting_id: str
    version: int = 0
    transcript: list[TranscriptEvent] = Field(default_factory=list)
    pain_points: list[PainPoint] = Field(default_factory=list)
    facts: list[MeetingFact] = Field(default_factory=list)
    gaps: list[InformationGap] = Field(default_factory=list)
    suggestions: list[QuestionSuggestion] = Field(default_factory=list)
    last_event_seq: int = -1
    ambiguous_routing_count: int = Field(default=0, ge=0)
    identity_decisions: list[ProblemIdentityDecision] = Field(default_factory=list)
    confirmed_semantic_hint_ids: list[str] = Field(default_factory=list)


def empty_meeting_state(meeting_id: str) -> MeetingState:
    """A fresh, empty snapshot for a meeting (used by the Slice 0 WS endpoint)."""

    return MeetingState(meeting_id=meeting_id)
