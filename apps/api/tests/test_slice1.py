"""Slice 1 verification: replay normalization, deterministic analysis/reduction,
question selection, and WebSocket streaming for the JA inventory-mismatch scenario."""

from __future__ import annotations

import asyncio
import pathlib

from fastapi.testclient import TestClient

from app.adapters.transcript.replay import ReplayTranscriptSource
from app.api.main import create_app
from app.domain.analysis import AnalysisResult, DetectedPain, ProposedFact
from app.domain.contracts import MeetingState, TranscriptEvent
from app.domain.reducer import apply_analysis, compute_gaps
from app.services.meeting_engine import MeetingEngine

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
FIXTURE = REPO_ROOT / "evals" / "fixtures" / "inventory-mismatch-ja.jsonl"
MEETING = "demo"


def _replay_events() -> list[TranscriptEvent]:
    async def collect() -> list[TranscriptEvent]:
        src = ReplayTranscriptSource(FIXTURE, MEETING, speed_factor=0.0)
        return [ev async for ev in src.events()]

    return asyncio.run(collect())


def _snapshots() -> dict[int, MeetingState]:
    events = _replay_events()
    engine = MeetingEngine(MEETING)
    out: dict[int, MeetingState] = {}
    for ev in events:
        engine.apply(ev)
        out[ev.seq] = engine.state
    return out


def _active_facts(state: MeetingState) -> dict[str, str]:
    return {f.slot: f.value for f in state.facts if f.status == "active"}


def _open_slots(state: MeetingState) -> set[str]:
    return {g.slot for g in state.gaps if g.status == "open"}


def _ask_now_slot(state: MeetingState) -> str | None:
    slot_of = {g.gap_id: g.slot for g in state.gaps}
    for s in state.suggestions:
        if s.role == "ask_now" and s.status == "active":
            return slot_of.get(s.gap_id)
    return None


def _active_suggestion_slots(state: MeetingState) -> list[str]:
    slot_of = {g.gap_id: g.slot for g in state.gaps}
    return [slot_of[s.gap_id] for s in state.suggestions if s.status == "active"]


# --- replay normalization --------------------------------------------------

def test_replay_normalizes_fixture_fields() -> None:
    events = _replay_events()
    assert len(events) == 7
    first = events[0]
    assert first.meeting_id == MEETING
    assert first.speaker.id == "EC担当"
    assert "在庫が合わない" in first.text
    assert first.lang == "ja"
    assert first.is_final is True
    assert first.source == "replay"


def test_event_ids_are_stable_and_seq_monotonic() -> None:
    a = _replay_events()
    b = _replay_events()
    assert [e.event_id for e in a] == [e.event_id for e in b]  # stable
    assert [e.seq for e in a] == list(range(7))  # monotonic 0..6


def test_duplicate_event_id_ignored() -> None:
    events = _replay_events()
    engine = MeetingEngine(MEETING)
    assert engine.apply(events[0]) is True
    v = engine.state.version
    assert engine.apply(events[0]) is False  # duplicate
    assert engine.state.version == v


def test_engine_keeps_prior_transcript_snapshots_unchanged() -> None:
    events = _replay_events()
    engine = MeetingEngine(MEETING)
    assert engine.apply(events[0]) is True
    first_snapshot = engine.state
    transcript = first_snapshot.transcript

    assert engine.apply(events[1]) is True

    assert engine.state is not first_snapshot
    assert engine.state.transcript is not transcript
    assert [event.seq for event in transcript] == [0]
    assert [event.seq for event in engine.state.transcript] == [0, 1]


# --- extraction + evidence -------------------------------------------------

def test_pain_and_fact_evidence_ids_valid() -> None:
    snaps = _snapshots()
    emitted = {e.event_id for e in _replay_events()}
    final = snaps[6]
    assert final.pain_points
    for p in final.pain_points:
        assert p.evidence_event_ids and set(p.evidence_event_ids) <= emitted
    for f in final.facts:
        assert f.evidence_event_ids and set(f.evidence_event_ids) <= emitted


def test_correction_process_extraction() -> None:
    assert _active_facts(_snapshots()[2]).get("correction_process") == "手動修正"


def test_frequency_extraction() -> None:
    assert _active_facts(_snapshots()[4]).get("frequency") == "ほぼ毎朝"


def test_source_of_truth_extraction() -> None:
    assert _active_facts(_snapshots()[6]).get("source_of_truth") == "倉庫側"


def test_facts_are_evidence_kind() -> None:
    for f in _snapshots()[6].facts:
        assert f.kind == "evidence"


# --- gaps + selection ------------------------------------------------------

def test_answered_gap_removed_from_open_gaps() -> None:
    s = _snapshots()[4]
    opens = _open_slots(s)
    assert "frequency" not in opens
    assert "correction_process" not in opens
    assert "source_of_truth" in opens


def test_frequency_not_resuggested_after_answer() -> None:
    s = _snapshots()[4]
    assert "frequency" not in _active_suggestion_slots(s)


def test_source_of_truth_is_primary_at_checkpoint() -> None:
    assert _ask_now_slot(_snapshots()[4]) == "source_of_truth"


def test_source_of_truth_suggestion_retracts_after_answer() -> None:
    s = _snapshots()[6]
    assert "source_of_truth" not in _active_suggestion_slots(s)


def test_next_gap_promotion_is_deterministic() -> None:
    # After source_of_truth is answered, template priority promotes business_impact.
    assert _ask_now_slot(_snapshots()[6]) == "business_impact"


def test_at_most_one_ask_now_and_two_follow_up() -> None:
    for s in _snapshots().values():
        ask = [x for x in s.suggestions if x.role == "ask_now" and x.status == "active"]
        follow = [x for x in s.suggestions if x.role == "follow_up" and x.status == "active"]
        assert len(ask) <= 1
        assert len(follow) <= 2


def test_no_duplicate_pain_gap_active_suggestion() -> None:
    for s in _snapshots().values():
        keys = [
            (sg.gap_id,)
            for sg in s.suggestions
            if sg.status == "active"
        ]
        assert len(keys) == len(set(keys))


# --- WebSocket integration -------------------------------------------------

# The demo fixture yields 7 accepted events; with the empty v0 snapshot that is
# 8 messages ending at version 7. Read a bounded stream up to the final version
# rather than draining until disconnect (avoids TestClient close-semantics flakiness).
_FINAL_VERSION = 7


def _read_until_final(ws, final_version: int = _FINAL_VERSION, limit: int = 32) -> list[dict]:
    messages: list[dict] = []
    for _ in range(limit):
        msg = ws.receive_json()
        messages.append(msg)
        if msg["version"] >= final_version:
            break
    return messages


def test_ws_streams_increasing_versioned_snapshots() -> None:
    client = TestClient(create_app(replay_speed=0.0))
    with client.websocket_connect(f"/ws/meeting/{MEETING}") as ws:
        messages = _read_until_final(ws)
    versions = [m["version"] for m in messages]
    assert versions[0] == 0  # empty v0 first (Slice 0 contract preserved)
    # Strictly increasing — a duplicate/plateau version would be a real regression
    # (frontend ignores non-greater versions; DOMAIN_MODEL: version increments per change).
    assert all(b > a for a, b in zip(versions, versions[1:]))
    assert versions[-1] >= 7


def test_ws_final_snapshot_has_source_of_truth_and_no_active_suggestion() -> None:
    client = TestClient(create_app(replay_speed=0.0))
    with client.websocket_connect(f"/ws/meeting/{MEETING}") as ws:
        messages = _read_until_final(ws)
    final = MeetingState.model_validate(messages[-1])
    assert _active_facts(final).get("source_of_truth") == "倉庫側"
    assert "source_of_truth" not in _active_suggestion_slots(final)


# --- lifecycle + guards (reviewer-driven regression tests) -----------------

def test_question_utterances_do_not_self_answer() -> None:
    # The PM's own questions (seq 3 = 頻度 question, seq 5 = 正 question) must not
    # fill the slot they ask about. Guards analyzer._is_question.
    snaps = _snapshots()
    assert "frequency" not in _active_facts(snaps[3])
    assert "frequency" in _open_slots(snaps[3])
    assert "source_of_truth" not in _active_facts(snaps[5])
    assert "source_of_truth" in _open_slots(snaps[5])


def _pain_result(event_id: str) -> AnalysisResult:
    return AnalysisResult(
        detected_pain=DetectedPain("data_mismatch", "EC・倉庫間の在庫差異", event_id),
    )


def _fact_result(slot: str, value: str, event_id: str) -> AnalysisResult:
    return AnalysisResult(facts=[ProposedFact("data_mismatch", slot, value, event_id)])


def test_supersede_preserves_lifecycle_fields() -> None:
    # A newer differing value supersedes without destroying history (user rule:
    # "the reducer must not destroy or bypass the lifecycle fields"; DOMAIN_MODEL §4).
    pains, facts = apply_analysis("m", 0, "e0", [], [], _pain_result("e0"))
    r1 = _fact_result("frequency", "毎日", "e1")
    pains, facts = apply_analysis("m", 1, "e1", pains, facts, r1)
    r2 = _fact_result("frequency", "月末", "e2")
    pains, facts = apply_analysis("m", 2, "e2", pains, facts, r2)

    freq = [f for f in facts if f.slot == "frequency"]
    assert len(freq) == 2  # history retained, nothing deleted (I5)
    old = next(f for f in freq if f.value == "毎日")
    new = next(f for f in freq if f.value == "月末")
    assert old.status == "superseded"
    assert new.status == "active"
    assert old.superseded_by == new.fact_id
    assert new.supersedes == old.fact_id
    assert old.evidence_event_ids == ["e1"]  # evidence preserved on the old fact

    # Slot stays filled by the active fact → its gap remains answered (no reopen).
    gaps = compute_gaps("m", pains, facts)
    freq_gap = next(g for g in gaps if g.slot == "frequency")
    assert freq_gap.status == "answered"
    assert freq_gap.resolved_by_fact_id == new.fact_id


def test_reopen_count_is_carried_forward_not_reset() -> None:
    # compute_gaps must not reset a gap's reopen_count to 0 each event (invariant I8).
    pains, facts = apply_analysis("m", 0, "e0", [], [], _pain_result("e0"))
    gaps = compute_gaps("m", pains, facts)
    bumped = [
        g.model_copy(update={"reopen_count": 2}) if g.slot == "owner" else g for g in gaps
    ]
    recomputed = compute_gaps("m", pains, facts, prior_gaps=bumped)
    assert next(g for g in recomputed if g.slot == "owner").reopen_count == 2
