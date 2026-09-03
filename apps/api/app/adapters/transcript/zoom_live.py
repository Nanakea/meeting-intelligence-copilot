"""Feature-flagged stub for a future direct Zoom live adapter.

The current live product path uses Meetily's local transcript bridge for any
meeting audio captured on the machine. This stub exists so the adapter registry
can reserve a stable name and feature flag without leaking Zoom-specific logic
into the pure domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.contracts import Lang, TranscriptEvent

FEATURE_FLAG_ENV = "MEETING_INTELLIGENCE_ZOOM_LIVE_ADAPTER"


@dataclass
class ZoomLiveNormalizer:
    """Non-composed placeholder for a future direct Zoom payload normalizer."""

    meeting_id: str
    lang: Lang

    def normalize(self, payload: dict[str, Any]) -> TranscriptEvent | None:
        raise NotImplementedError("zoom live adapter is a feature-flagged stub")
