"""Independent fixed-seed Korean pilot corpus definitions."""

from __future__ import annotations

import json
import random
from pathlib import Path

from app.domain.contracts import Lang
from app.evals.pilot_corpus import PilotCorpusCase, PilotQualityReport, evaluate_pilot_corpus

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SPEC = REPO_ROOT / "evals" / "pilot" / "korean-corpus-spec.json"


def generate_korean_pilot_corpus(
    spec_path: Path = DEFAULT_SPEC,
) -> tuple[PilotCorpusCase, ...]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    cases_per_category = int(spec["cases_per_category"])
    positive_count = int(spec["positive_cases_per_category"])
    negative_count = cases_per_category - positive_count
    openers = tuple(str(value) for value in spec["neutral_openers"])
    closers = tuple(str(value) for value in spec["neutral_closers"])
    negative_frames = tuple(str(value) for value in spec["negative_frames"])
    rng = random.Random(int(spec["seed"]))
    cases: list[PilotCorpusCase] = []

    for group in spec["groups"]:
        category = str(group["category"])
        statement = str(group["positive_statement"])
        expected_facts = frozenset(
            (str(slot), str(value))
            for slot, value in group.get("expected_facts", {}).items()
        )
        group_cases = [
            PilotCorpusCase(
                case_id=f"korean-{category}-positive-{index:03d}",
                category=category,
                lang=Lang.ko,
                events=(
                    openers[index % len(openers)],
                    statement,
                    closers[(index // len(openers)) % len(closers)],
                ),
                expected_pain=True,
                expected_facts=expected_facts,
                expected_ask_slot=str(group["expected_ask_slot"]),
                hard_negative=False,
            )
            for index in range(positive_count)
        ]
        negatives = (
            *(frame.format(statement=statement) for frame in negative_frames),
            *(str(value) for value in group["hard_negatives"]),
        )
        if len(negatives) != negative_count:
            raise ValueError(
                f"{category} must define exactly {negative_count} hard negatives"
            )
        group_cases.extend(
            PilotCorpusCase(
                case_id=f"korean-{category}-negative-{index:03d}",
                category=category,
                lang=Lang.ko,
                events=(openers[index % len(openers)], negative),
                expected_pain=False,
                expected_facts=frozenset(),
                expected_ask_slot=None,
                hard_negative=True,
            )
            for index, negative in enumerate(negatives)
        )
        rng.shuffle(group_cases)
        cases.extend(group_cases)

    return tuple(cases)


def evaluate_korean_pilot_corpus() -> PilotQualityReport:
    return evaluate_pilot_corpus(generate_korean_pilot_corpus())
