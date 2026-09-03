"""Registry for live transcript adapter names and session normalizers."""

from __future__ import annotations

import os
from typing import Literal, Protocol, get_args

from app.adapters.transcript.google_meet_live import FEATURE_FLAG_ENV as GOOGLE_MEET_FLAG
from app.adapters.transcript.meetily_live import MeetilyLiveNormalizer
from app.adapters.transcript.teams_live import FEATURE_FLAG_ENV as TEAMS_FLAG
from app.adapters.transcript.zoom_live import FEATURE_FLAG_ENV as ZOOM_FLAG
from app.domain.contracts import Lang, TranscriptEvent

LiveAdapterName = Literal["meetily", "zoom", "google_meet", "teams"]
SUPPORTED_LIVE_ADAPTERS = get_args(LiveAdapterName)


class LiveSessionNormalizer(Protocol):
    meeting_id: str
    lang: Lang

    def normalize(self, payload: dict[str, object]) -> TranscriptEvent | None:
        ...


class UnsupportedLiveAdapter(ValueError):
    """Raised when a live adapter name is reserved but unavailable in this build."""


_FEATURE_FLAG_ENVS: dict[LiveAdapterName, str | None] = {
    "meetily": None,
    "zoom": ZOOM_FLAG,
    "google_meet": GOOGLE_MEET_FLAG,
    "teams": TEAMS_FLAG,
}


def feature_flag_env(adapter: LiveAdapterName) -> str | None:
    return _FEATURE_FLAG_ENVS[adapter]


def build_live_session_normalizer(
    adapter: LiveAdapterName, session_id: str, lang: Lang
) -> LiveSessionNormalizer:
    if adapter == "meetily":
        return MeetilyLiveNormalizer(meeting_id=session_id, lang=lang)

    flag_env = feature_flag_env(adapter)
    if flag_env is not None and os.environ.get(flag_env) != "1":
        raise UnsupportedLiveAdapter(
            f"{adapter} live adapter is reserved but disabled in this build"
        )
    raise UnsupportedLiveAdapter(
        f"{adapter} live adapter is feature-flagged but not implemented in this build"
    )
