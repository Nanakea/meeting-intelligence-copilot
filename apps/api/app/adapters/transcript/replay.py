"""JSONL transcript replay adapter (infrastructure).

Reads a synthetic meeting fixture and normalizes each line into a platform-neutral
``TranscriptEvent``. This is the only place JSONL is parsed — the domain and
service layers never touch the file format (docs/ARCHITECTURE.md §4).

Fixture line fields: ``speaker``, ``text``, ``offset_ms``.

``speed_factor`` scales real-time replay: 0 (default) means no delay so automated
tests run instantly; 1.0 replays at the fixture's original pace; 2.0 is twice as
fast."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from app.domain.contracts import Lang, Speaker, TranscriptEvent


class ReplayTranscriptSource:
    """A :class:`app.domain.ports.TranscriptSource` backed by a JSONL fixture."""

    def __init__(
        self,
        fixture_path: str | Path,
        meeting_id: str,
        *,
        lang: str | Lang = Lang.ja,
        speed_factor: float = 0.0,
        source_name: str = "replay",
    ) -> None:
        self._path = Path(fixture_path)
        self._meeting_id = meeting_id
        # Language is explicit source configuration (no auto-detection in the domain).
        self._lang = Lang(lang) if not isinstance(lang, Lang) else lang
        self._speed_factor = speed_factor
        self._source_name = source_name

    def _load(self) -> list[dict]:
        rows: list[dict] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        rows.sort(key=lambda r: int(r["offset_ms"]))  # chronological order
        return rows

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        rows = self._load()
        previous_offset = 0
        for seq, row in enumerate(rows):
            offset_ms = int(row["offset_ms"])
            if self._speed_factor and self._speed_factor > 0:
                delay = max(0.0, (offset_ms - previous_offset) / 1000.0 / self._speed_factor)
                if delay:
                    await asyncio.sleep(delay)
            previous_offset = offset_ms

            speaker_name = str(row["speaker"])
            yield TranscriptEvent(
                event_id=f"{self._meeting_id}-{seq:04d}",  # stable, deterministic
                meeting_id=self._meeting_id,
                seq=seq,
                speaker=Speaker(id=speaker_name, display_name=speaker_name),
                text=str(row["text"]),
                lang=self._lang,
                ts_start=offset_ms / 1000.0,
                ts_end=offset_ms / 1000.0,
                is_final=True,
                source=self._source_name,
            )
