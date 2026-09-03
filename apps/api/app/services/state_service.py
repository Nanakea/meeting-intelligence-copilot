"""Application service layer.

Services orchestrate the domain for the transport layer. They may import
``app.domain`` but never ``app.adapters`` or vendor SDKs — the import-boundary
test enforces this (docs/ARCHITECTURE.md). In Slice 0 the only service is the
initial snapshot; the real fold-over-events lives in later slices.
"""

from __future__ import annotations

from app.domain.contracts import MeetingState, empty_meeting_state


def initial_snapshot(meeting_id: str) -> MeetingState:
    """The first snapshot a client receives: an empty, versioned MeetingState."""

    return empty_meeting_state(meeting_id)
