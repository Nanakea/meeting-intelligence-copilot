"""Deterministic golden evaluation runner (Slice 2, multi-fixture).

Discovers every ``evals/golden/*.json`` (expectations live in JSON, never here),
replays its fixture through the real engine, and asserts the state trajectory at
meaningful checkpoints. Prints PASS/FAIL per fixture and a summary, and exits
nonzero if any fixture fails. This is exact deterministic evaluation — no metrics.

    python scripts/eval_golden.py             # all golden fixtures
    python scripts/eval_golden.py <golden.json>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.adapters.transcript.replay import ReplayTranscriptSource
from app.domain.contracts import FactStatus, GapStatus, MeetingState, SuggestionStatus
from app.services.meeting_engine import MeetingEngine

# scripts/eval_golden.py -> scripts -> apps/api -> apps -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN_DIR = _REPO_ROOT / "evals" / "golden"


async def _collect(
    fixture: Path, meeting_id: str, lang: str
) -> tuple[dict[int, MeetingState], set[str]]:
    engine = MeetingEngine(meeting_id)
    source = ReplayTranscriptSource(fixture, meeting_id, lang=lang, speed_factor=0.0)
    snapshots: dict[int, MeetingState] = {}
    emitted: set[str] = set()
    async for event in source.events():
        emitted.add(event.event_id)
        if engine.apply(event):
            snapshots[event.seq] = engine.state
    return snapshots, emitted


def _active_facts(state: MeetingState) -> dict[str, str]:
    return {f.slot: f.value for f in state.facts if f.status == FactStatus.active}


def _slots_by_status(state: MeetingState, status: GapStatus) -> set[str]:
    return {g.slot for g in state.gaps if g.status == status}


def _ask_now_slot(state: MeetingState) -> str | None:
    slot_of = {g.gap_id: g.slot for g in state.gaps}
    for s in state.suggestions:
        if s.role == "ask_now" and s.status == SuggestionStatus.active:
            return slot_of.get(s.gap_id)
    return None


def _active_suggestion_slots(state: MeetingState) -> set[str]:
    slot_of = {g.gap_id: g.slot for g in state.gaps}
    return {
        slot_of.get(s.gap_id, "")
        for s in state.suggestions
        if s.status == SuggestionStatus.active
    }


def _evaluate(golden: dict, failures: list[str]) -> None:
    name = golden["meeting_id"]
    fixture = _REPO_ROOT / golden["fixture"]
    lang = golden.get("lang", "ja")
    snapshots, emitted = asyncio.run(_collect(fixture, name, lang))

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(f"[{name}] {msg}")

    # Evidence traceability: every fact/pain cites emitted event ids (all fixtures).
    if snapshots:
        final = snapshots[max(snapshots)]
        for f in final.facts:
            check(bool(f.evidence_event_ids) and set(f.evidence_event_ids) <= emitted,
                  f"fact {f.fact_id} evidence not traceable")
        for p in final.pain_points:
            check(bool(p.evidence_event_ids) and set(p.evidence_event_ids) <= emitted,
                  f"pain {p.pain_id} evidence not traceable")

    for cp in golden["checkpoints"]:
        cn = cp["name"]
        state = snapshots[cp["after_event_seq"]]
        facts = _active_facts(state)
        opens = _slots_by_status(state, GapStatus.open)
        answered = _slots_by_status(state, GapStatus.answered)
        active_sugg = _active_suggestion_slots(state)

        if cp.get("expect_empty"):
            check(not state.pain_points, f"{cn}: expected no pain point")
            check(not state.facts, f"{cn}: expected no facts")
            check(not state.gaps, f"{cn}: expected no gaps")
            check(not [s for s in state.suggestions if s.status == "active"],
                  f"{cn}: expected no active suggestions")
            continue

        if "pain_category" in cp:
            check(any(p.template_id == cp["pain_category"] for p in state.pain_points),
                  f"{cn}: expected pain category {cp['pain_category']}")
        for slot, value in cp.get("known_facts", {}).items():
            check(facts.get(slot) == value, f"{cn}: fact {slot}={value!r}, got {facts.get(slot)!r}")
        for slot in cp.get("known_facts_absent", []):
            check(slot not in facts, f"{cn}: fact {slot} must be absent, got {facts.get(slot)!r}")
        for hist in cp.get("facts_history", []):
            # Assert a historical (e.g. superseded) fact exists, distinct from active facts.
            match = any(
                f.slot == hist["slot"]
                and f.value == hist["value"]
                and f.status == hist["status"]
                for f in state.facts
            )
            check(
                match,
                f"{cn}: expected historical fact {hist['slot']}={hist['value']!r} "
                f"status={hist['status']}",
            )
        for slot in cp.get("open_gaps_include", []):
            check(slot in opens, f"{cn}: expected open gap {slot}")
        for slot in cp.get("open_gaps_exclude", []):
            check(slot not in opens, f"{cn}: gap {slot} must not be open")
        for slot in cp.get("answered_gaps", []):
            check(slot in answered, f"{cn}: expected answered gap {slot}")
        if "ask_now_gap" in cp:
            check(_ask_now_slot(state) == cp["ask_now_gap"],
                  f"{cn}: ASK NOW {cp['ask_now_gap']}, got {_ask_now_slot(state)}")
        for slot in cp.get("no_active_suggestion_gaps", []):
            check(slot not in active_sugg, f"{cn}: forbidden active suggestion for {slot}")

        ask = [s for s in state.suggestions if s.role == "ask_now" and s.status == "active"]
        follow = [s for s in state.suggestions if s.role == "follow_up" and s.status == "active"]
        check(len(ask) <= 1, f"{cn}: more than one ASK NOW")
        check(len(follow) <= 2, f"{cn}: more than two FOLLOW UP")


def run(golden_paths: list[Path]) -> int:
    passed = 0
    failed = 0
    for path in golden_paths:
        golden = json.loads(path.read_text(encoding="utf-8"))
        fixture_failures: list[str] = []
        _evaluate(golden, fixture_failures)
        if fixture_failures:
            failed += 1
            print(f"FAIL  {golden['meeting_id']}")
            for f in fixture_failures:
                print(f"      - {f}")
        else:
            passed += 1
            print(f"PASS  {golden['meeting_id']} ({len(golden['checkpoints'])} checkpoints)")
    print("\nGolden evaluation:")
    print(f"{passed} passed")
    print(f"{failed} failed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        paths = [Path(a) for a in argv]
    else:
        paths = sorted(_GOLDEN_DIR.glob("*.json"))
    return run(paths)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    raise SystemExit(main())
