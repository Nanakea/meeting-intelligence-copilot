"""Feature-flagged stub for a future direct Google Meet live adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.contracts import Lang, TranscriptEvent

FEATURE_FLAG_ENV = "MEETING_INTELLIGENCE_GOOGLE_MEET_LIVE_ADAPTER"


@dataclass
class GoogleMeetLiveNormalizer:
    """Non-composed placeholder for a future direct Google Meet normalizer."""

    meeting_id: str
    lang: Lang

    def normalize(self, payload: dict[str, Any]) -> TranscriptEvent | None:
        raise NotImplementedError("google_meet live adapter is a feature-flagged stub")
