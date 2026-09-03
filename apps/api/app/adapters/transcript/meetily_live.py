"""Production adapter: real Meetily ``TranscriptUpdate`` -> ``TranscriptEvent``.

Meetily pushes each TranscriptUpdate over HTTP as it is emitted by its real
capture/STT pipeline (see the Meetily-side integration point in
``docs/MEETILY_INTEGRATION_RESEARCH.md``). This adapter holds the pure
normalization step; the transport (FastAPI route) lives in ``app/api``.

Design corrections from real-ingress evidence (both Parakeet and Whisper engines,
English and Japanese, verified via the real CDP-driven acceptance harness):

- Raw ``is_partial`` is NOT a reliable interim/final streaming signal to gate on.
  Parakeet reports it correctly as False for finals, but Whisper's own engine
  (whisper_engine.rs) sets ``is_partial = duration_seconds < 15.0`` -- a pure
  chunk-length annotation. Real conversational VAD segments are almost always
  under 15 seconds, so Whisper marks nearly everything partial even though each
  ``sequence_id`` is observed to be emitted exactly once (no later revision).
- Meetily's own frontend (``TranscriptContext.tsx``) does not gate display on
  ``is_partial`` either -- it dedupes purely by ``sequence_id``.
  This adapter matches that real behavior: dedupe by ``sequence_id``, accept
  content once, regardless of ``is_partial``.
- Language is never inferred from the text (no Unicode sniffing). It is supplied
  once per live session as the meeting's immutable configured language -- the
  same value the caller passed to Meetily's ``set_language_preference`` command.

Requests can arrive out of order through network retries, replay repair, or
older clients. ``seq``/``event_id`` are therefore derived directly from
Meetily's own ``sequence_id`` (not an arrival counter) so ordering is meaningful
regardless of transport scheduling. Enforcing that events are actually
*applied* to the engine in that order (buffering ones that arrive early) is
``LiveMeetingRegistry``'s job, not this per-session normalizer's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.domain.contracts import Lang, Speaker, TranscriptEvent

_SPEAKER_DISPLAY = {"Microphone": "You", "Audio": "Remote"}


def _audio_timestamp(payload: dict[str, Any], field_name: str) -> float:
    raw = payload.get(field_name, 0.0)
    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be a finite non-negative number")
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return value


@dataclass
class MeetilyLiveNormalizer:
    """Stateful per-session normalizer. One instance per opaque live session id."""

    meeting_id: str  # contract field; live value is the opaque recording session id
    lang: Lang  # meeting-level configured language (immutable for the session)
    _seen_sequence_ids: set[int] = field(default_factory=set)

    def seed_seen_sequence_ids(self, sequence_ids: Iterable[int]) -> None:
        """Rehydrate sequence-id dedupe after loading a cached MeetingState."""

        self._seen_sequence_ids.update(
            sequence_id
            for sequence_id in sequence_ids
            if isinstance(sequence_id, int)
            and not isinstance(sequence_id, bool)
            and sequence_id >= 0
        )

    def normalize(self, payload: dict[str, Any]) -> TranscriptEvent | None:
        """Return a TranscriptEvent for genuinely new content, else None.

        ``payload`` is the raw Meetily TranscriptUpdate JSON: text, timestamp,
        source, sequence_id, chunk_start_time, is_partial, confidence,
        audio_start_time, audio_end_time, duration.

        ``TranscriptEvent.seq`` is Meetily's own ``sequence_id`` verbatim (not an
        arrival-order counter) so the caller can enforce true chronological order
        even if pushes arrive out of order.
        """
        text = str(payload.get("text", "")).strip()
        if not text:
            return None
        sequence_id = payload.get("sequence_id")
        if (
            isinstance(sequence_id, bool)
            or not isinstance(sequence_id, int)
            or sequence_id < 0
            or sequence_id in self._seen_sequence_ids
        ):
            return None  # dedupe by Meetily's own real segment identity

        seq = sequence_id
        source = str(payload.get("source", ""))
        display_name = _SPEAKER_DISPLAY.get(source, source or "Unknown")
        ts_start = _audio_timestamp(payload, "audio_start_time")
        ts_end = _audio_timestamp(payload, "audio_end_time")
        if ts_end < ts_start:
            raise ValueError("audio_end_time must not precede audio_start_time")

        event = TranscriptEvent(
            event_id=f"{self.meeting_id}:seg{seq}",  # DERIVED-STABLE from sequence_id
            meeting_id=self.meeting_id,
            seq=seq,
            speaker=Speaker(id=source or "unknown", display_name=display_name, role=None),
            text=text,
            lang=self.lang,  # DERIVED from session configuration, never Unicode-sniffed
            ts_start=ts_start,
            ts_end=ts_end,
            is_final=True,  # see module docstring: sequence_id dedupe is the real gate
            source="meetily",
        )
        self._seen_sequence_ids.add(sequence_id)
        return event
