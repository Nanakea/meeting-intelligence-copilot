"""Pure provider seam for future semantic candidate observation.

Phase 4A supplies only the protocol and a null implementation. No production
composition root invokes this seam, and no network, subprocess, or model runtime
is used.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.contracts import TranscriptEvent
from app.domain.semantic_candidates import SemanticObservationBatch


class SemanticCandidateAnalyzer(Protocol):
    def observe(self, events: list[TranscriptEvent]) -> SemanticObservationBatch:
        """Return untrusted candidate observations for a bounded event window."""


class NullSemanticCandidateAnalyzer:
    """Default-safe provider that performs no semantic observation."""

    def observe(self, events: list[TranscriptEvent]) -> SemanticObservationBatch:
        del events
        return SemanticObservationBatch()
