"""Release thresholds for the fixed-seed synthetic internal-pilot corpus."""

import json

from app.domain.contracts import Lang
from app.evals.pilot_corpus import (
    DEFAULT_SPEC,
    PilotCorpusCase,
    evaluate_pilot_corpus,
    generate_pilot_corpus,
)


def test_pilot_corpus_shape_and_independent_annotations() -> None:
    corpus = generate_pilot_corpus()
    assert len(corpus) == 1_200
    assert sum(case.hard_negative for case in corpus) == 360
    assert len({(case.category, case.lang, case.events) for case in corpus}) == 1_200
    assert all(case.checkpoints for case in corpus if not case.hard_negative)
    assert {
        (case.category, case.lang.value) for case in corpus
    } == {
        (category, lang)
        for category in (
            "data_mismatch",
            "integration_failure",
            "manual_work",
            "process_delay",
            "vague_requirement",
            "unclear_ownership",
        )
        for lang in ("ja", "en")
    }


def test_pilot_corpus_has_semantic_variation_not_only_neutral_wrappers() -> None:
    spec = json.loads(DEFAULT_SPEC.read_text(encoding="utf-8"))
    assert all(
        len(set(group["positive_pain_variants"])) >= 5
        for group in spec["groups"]
    )
    assert all(
        len(set(group["hard_negatives"]))
        + len(set(spec["negative_frames"][group["lang"]]))
        >= 10
        for group in spec["groups"]
    )

    corpus = generate_pilot_corpus()
    for category in {
        "data_mismatch",
        "integration_failure",
        "manual_work",
        "process_delay",
        "vague_requirement",
        "unclear_ownership",
    }:
        for lang in (Lang.ja, Lang.en):
            cases = [
                case
                for case in corpus
                if case.category == category and case.lang == lang
            ]
            positive_bodies = {
                case.events[1] for case in cases if not case.hard_negative
            }
            negative_bodies = {
                case.events[1] for case in cases if case.hard_negative
            }
            assert len(positive_bodies) >= 5
            assert len(negative_bodies) >= 10


def test_pilot_corpus_meets_internal_candidate_thresholds() -> None:
    report = evaluate_pilot_corpus()
    assert report.invariant_failures == 0
    assert report.trajectory_failures == 0
    assert report.pain_precision >= 0.95
    assert report.pain_recall >= 0.90
    assert report.fact_precision >= 0.95
    assert report.fact_recall >= 0.85
    assert report.ask_now_agreement >= 0.95
    assert report.no_question_false_positive_rate < 0.02


def test_pilot_metrics_penalize_wrong_topic_even_when_ask_slot_name_matches() -> None:
    report = evaluate_pilot_corpus(
        (
            PilotCorpusCase(
                case_id="wrong-topic",
                category="data_mismatch",
                lang=Lang.en,
                events=(
                    "Every morning one person manually copies rows into a spreadsheet.",
                ),
                expected_pain=True,
                expected_facts=frozenset(),
                expected_ask_slot="business_impact",
                hard_negative=False,
            ),
        )
    )

    assert report.pain_precision == 0
    assert report.pain_recall == 0
    assert report.fact_precision == 0
    assert report.ask_now_agreement == 0


def test_pilot_fact_metrics_require_the_annotated_value() -> None:
    report = evaluate_pilot_corpus(
        (
            PilotCorpusCase(
                case_id="wrong-value",
                category="manual_work",
                lang=Lang.en,
                events=(
                    "Every morning one person manually copies rows into a spreadsheet.",
                ),
                expected_pain=True,
                expected_facts=frozenset({("frequency", "every business day")}),
                expected_ask_slot="business_impact",
                hard_negative=False,
            ),
        )
    )

    assert report.fact_precision == 0
    assert report.fact_recall == 0
