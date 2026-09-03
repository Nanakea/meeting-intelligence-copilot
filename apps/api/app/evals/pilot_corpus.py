"""Fixed-seed, provider-free internal-pilot corpus and quality scoring.

The corpus definition points at human-readable JSONL fixtures and golden state
trajectories. It never imports analyzer rules, regexes, or normalization helpers.
This keeps expected behavior independent from the implementation being scored.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.contracts import Lang, MeetingState, Speaker, TranscriptEvent
from app.services.meeting_engine import MeetingEngine

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SPEC = REPO_ROOT / "evals" / "pilot" / "corpus-spec.json"


@dataclass(frozen=True)
class PilotExpectedCheckpoint:
    after_event_index: int
    expected_facts: frozenset[tuple[str, str]]
    expected_ask_slot: str | None
    assert_ask_now: bool


@dataclass(frozen=True)
class PilotCorpusCase:
    case_id: str
    category: str
    lang: Lang
    events: tuple[str, ...]
    expected_pain: bool
    expected_facts: frozenset[tuple[str, str]]
    expected_ask_slot: str | None
    hard_negative: bool
    checkpoints: tuple[PilotExpectedCheckpoint, ...] = ()


@dataclass(frozen=True)
class PilotQualityReport:
    case_count: int
    hard_negative_count: int
    pain_precision: float
    pain_recall: float
    fact_precision: float
    fact_recall: float
    ask_now_agreement: float
    no_question_false_positive_rate: float
    invariant_failures: int
    trajectory_failures: int


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_texts(path: Path) -> tuple[str, ...]:
    return tuple(
        json.loads(line)["text"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def _golden_expectations(
    path: Path,
) -> tuple[
    frozenset[tuple[str, str]],
    str | None,
    tuple[PilotExpectedCheckpoint, ...],
]:
    golden = _load_json(path)
    active_facts: dict[str, str] = {}
    expected_checkpoints: list[PilotExpectedCheckpoint] = []
    for checkpoint in golden["checkpoints"]:
        checkpoint_facts = frozenset(
            (str(slot), str(value))
            for slot, value in checkpoint.get("known_facts", {}).items()
        )
        active_facts.update(checkpoint_facts)
        expected_checkpoints.append(
            PilotExpectedCheckpoint(
                # Generated positive cases insert one neutral turn before the
                # source fixture, so source event N is generated event N + 1.
                after_event_index=int(checkpoint["after_event_seq"]) + 1,
                expected_facts=checkpoint_facts,
                expected_ask_slot=checkpoint.get("ask_now_gap"),
                assert_ask_now="ask_now_gap" in checkpoint,
            )
        )
    final_checkpoint = golden["checkpoints"][-1]
    return (
        frozenset(active_facts.items()),
        final_checkpoint.get("ask_now_gap"),
        tuple(expected_checkpoints),
    )


def generate_pilot_corpus(spec_path: Path = DEFAULT_SPEC) -> tuple[PilotCorpusCase, ...]:
    spec = _load_json(spec_path)
    groups = spec["groups"]
    per_group = int(spec["cases_per_category_language"])
    positive_count = int(per_group * float(spec["positive_ratio"]))
    negative_count = per_group - positive_count
    rng = random.Random(int(spec["seed"]))
    neutral_context = spec["neutral_context"]
    cases: list[PilotCorpusCase] = []

    for group in groups:
        category = str(group["category"])
        lang = Lang(group["lang"])
        fixture = REPO_ROOT / group["fixture"]
        golden = REPO_ROOT / group["golden"]
        positive_events = _fixture_texts(fixture)
        pain_variants = tuple(str(text) for text in group["positive_pain_variants"])
        expected_facts, expected_ask_slot, checkpoints = _golden_expectations(golden)
        openers = tuple(str(text) for text in neutral_context[lang.value]["openers"])
        closers = tuple(str(text) for text in neutral_context[lang.value]["closers"])
        group_cases = [
            PilotCorpusCase(
                case_id=f"{category}-{lang.value}-positive-{index:03d}",
                category=category,
                lang=lang,
                events=(
                    openers[index % len(openers)],
                    pain_variants[index % len(pain_variants)],
                    *positive_events[1:],
                    closers[(index // len(openers)) % len(closers)],
                ),
                expected_pain=True,
                expected_facts=expected_facts,
                expected_ask_slot=expected_ask_slot,
                hard_negative=False,
                checkpoints=checkpoints,
            )
            for index in range(positive_count)
        ]
        negative_frames = tuple(
            str(frame) for frame in spec["negative_frames"][lang.value]
        )
        negatives = (
            *(str(text) for text in group["hard_negatives"]),
            *(frame.format(statement=pain_variants[0]) for frame in negative_frames),
        )
        group_cases.extend(
            PilotCorpusCase(
                case_id=f"{category}-{lang.value}-negative-{index:03d}",
                category=category,
                lang=lang,
                events=(
                    openers[(index // len(negatives)) % len(openers)],
                    negatives[index % len(negatives)],
                ),
                expected_pain=False,
                expected_facts=frozenset(),
                expected_ask_slot=None,
                hard_negative=True,
            )
            for index in range(negative_count)
        )
        rng.shuffle(group_cases)
        cases.extend(group_cases)

    return tuple(cases)


def _apply_case(
    case: PilotCorpusCase,
) -> tuple[MeetingEngine, tuple[MeetingState, ...]]:
    engine = MeetingEngine(case.case_id)
    states: list[MeetingState] = []
    for seq, text in enumerate(case.events):
        engine.apply(
            TranscriptEvent(
                event_id=f"{case.case_id}:event-{seq}",
                meeting_id=case.case_id,
                seq=seq,
                speaker=Speaker(id="synthetic", display_name="Synthetic"),
                text=text,
                lang=case.lang,
                ts_start=float(seq),
                ts_end=float(seq + 1),
                is_final=True,
                source="fixed-seed-pilot-corpus",
            )
        )
        states.append(engine.state)
    return engine, tuple(states)


def _active_ask_target(state: MeetingState) -> tuple[str, str] | None:
    ask_now = [
        suggestion
        for suggestion in state.suggestions
        if suggestion.status == "active" and suggestion.role == "ask_now"
    ]
    if not ask_now:
        return None
    gap_by_id = {gap.gap_id: gap for gap in state.gaps}
    category_by_pain_id = {
        pain.pain_id: pain.template_id for pain in state.pain_points
    }
    gap = gap_by_id.get(ask_now[0].gap_id)
    if gap is None:
        return None
    category = category_by_pain_id.get(gap.pain_id)
    return (category, gap.slot) if category is not None else None


def _invariant_failures(state: MeetingState) -> int:
    failures = 0
    event_ids = {event.event_id for event in state.transcript}
    if len(event_ids) != len(state.transcript):
        failures += 1
    if any(
        not item.evidence_event_ids
        or not set(item.evidence_event_ids).issubset(event_ids)
        for item in [*state.pain_points, *state.facts]
    ):
        failures += 1

    active_facts = {
        (fact.pain_id, fact.slot)
        for fact in state.facts
        if fact.status == "active"
    }
    for gap in state.gaps:
        should_be_answered = (gap.pain_id, gap.slot) in active_facts
        if (gap.status == "answered") != should_be_answered:
            failures += 1

    gap_by_id = {gap.gap_id: gap for gap in state.gaps}
    active_suggestions = [
        suggestion
        for suggestion in state.suggestions
        if suggestion.status == "active"
    ]
    if any(
        gap_by_id.get(suggestion.gap_id) is None
        or gap_by_id[suggestion.gap_id].status != "open"
        for suggestion in active_suggestions
    ):
        failures += 1

    facts_by_id = {fact.fact_id: fact for fact in state.facts}
    if any(
        fact.status == "superseded"
        and (
            fact.superseded_by is None
            or fact.superseded_by not in facts_by_id
        )
        for fact in state.facts
    ):
        failures += 1
    return failures


def evaluate_pilot_corpus(
    cases: tuple[PilotCorpusCase, ...] | None = None,
) -> PilotQualityReport:
    corpus = cases or generate_pilot_corpus()
    pain_true_positive = pain_false_positive = pain_false_negative = 0
    fact_true_positive = fact_false_positive = fact_false_negative = 0
    ask_matches = ask_total = no_question_false_positives = invariant_failures = 0
    trajectory_failures = 0

    for case in corpus:
        engine, states = _apply_case(case)
        state = engine.state
        detected = {pain.template_id for pain in state.pain_points}
        expected_pains = {case.category} if case.expected_pain else set()
        pain_true_positive += len(detected & expected_pains)
        pain_false_positive += len(detected - expected_pains)
        pain_false_negative += len(expected_pains - detected)

        category_by_pain_id = {
            pain.pain_id: pain.template_id for pain in state.pain_points
        }
        actual_facts = {
            (category_by_pain_id[fact.pain_id], fact.slot, fact.value)
            for fact in state.facts
            if fact.pain_id in category_by_pain_id and fact.status == "active"
        }
        expected_facts = {
            (case.category, slot, value) for slot, value in case.expected_facts
        }
        fact_true_positive += len(actual_facts & expected_facts)
        fact_false_positive += len(actual_facts - expected_facts)
        fact_false_negative += len(expected_facts - actual_facts)

        active_suggestions = [
            suggestion
            for suggestion in state.suggestions
            if suggestion.status == "active"
        ]
        ask_now = [
            suggestion for suggestion in active_suggestions if suggestion.role == "ask_now"
        ]
        follow_ups = [
            suggestion for suggestion in active_suggestions if suggestion.role == "follow_up"
        ]
        if len(ask_now) > 1 or len(follow_ups) > 2:
            invariant_failures += 1
        invariant_failures += _invariant_failures(state)

        actual_ask_target = _active_ask_target(state)
        if case.expected_pain:
            ask_total += 1
            expected_ask_target = (
                (case.category, case.expected_ask_slot)
                if case.expected_ask_slot is not None
                else None
            )
            ask_matches += int(actual_ask_target == expected_ask_target)
        elif active_suggestions:
            no_question_false_positives += 1

        for checkpoint in case.checkpoints:
            checkpoint_state = states[checkpoint.after_event_index]
            checkpoint_pains = {
                pain.pain_id
                for pain in checkpoint_state.pain_points
                if pain.template_id == case.category
            }
            checkpoint_facts = {
                (fact.slot, fact.value)
                for fact in checkpoint_state.facts
                if fact.pain_id in checkpoint_pains and fact.status == "active"
            }
            if not checkpoint_pains or not checkpoint.expected_facts.issubset(
                checkpoint_facts
            ):
                trajectory_failures += 1
            if (
                checkpoint.assert_ask_now
                and _active_ask_target(checkpoint_state)
                != (
                    (case.category, checkpoint.expected_ask_slot)
                    if checkpoint.expected_ask_slot is not None
                    else None
                )
            ):
                trajectory_failures += 1

    def ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 1.0

    negative_count = sum(case.hard_negative for case in corpus)
    return PilotQualityReport(
        case_count=len(corpus),
        hard_negative_count=negative_count,
        pain_precision=ratio(
            pain_true_positive, pain_true_positive + pain_false_positive
        ),
        pain_recall=ratio(
            pain_true_positive, pain_true_positive + pain_false_negative
        ),
        fact_precision=ratio(
            fact_true_positive, fact_true_positive + fact_false_positive
        ),
        fact_recall=ratio(
            fact_true_positive, fact_true_positive + fact_false_negative
        ),
        ask_now_agreement=ratio(ask_matches, ask_total),
        no_question_false_positive_rate=ratio(
            no_question_false_positives, negative_count
        ),
        invariant_failures=invariant_failures,
        trajectory_failures=trajectory_failures,
    )
