"""Feature-flagged stub for a future direct Microsoft Teams live adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.contracts import Lang, TranscriptEvent

FEATURE_FLAG_ENV = "MEETING_INTELLIGENCE_TEAMS_LIVE_ADAPTER"


@dataclass
class TeamsLiveNormalizer:
    """Non-composed placeholder for a future direct Teams payload normalizer."""

    meeting_id: str
    lang: Lang

    def normalize(self, payload: dict[str, Any]) -> TranscriptEvent | None:
        raise NotImplementedError("teams live adapter is a feature-flagged stub")
