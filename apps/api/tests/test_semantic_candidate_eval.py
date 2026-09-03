"""Provider-free semantic annotation harness tests."""

from __future__ import annotations

import json
import pathlib

import pytest

from app.api.demo_registry import resolve_demo
from app.evals.semantic_candidate_eval import (
    CoverageStatus,
    SemanticEvalInputError,
    evaluate_semantic_annotations,
    load_semantic_annotations,
    render_semantic_eval_markdown,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ANNOTATIONS = REPO_ROOT / "evals" / "semantic" / "local-semantic-paraphrases.json"
REPORT = REPO_ROOT / "docs" / "SEMANTIC_EVAL_REPORT.md"


def test_annotation_cases_load_and_remain_annotation_only() -> None:
    suite = load_semantic_annotations(ANNOTATIONS)
    assert suite.status == "annotation-only"
    assert len(suite.cases) == 10
    assert all(case.related_demo_id for case in suite.cases)
    assert all(resolve_demo(case.related_demo_id or "") is not None for case in suite.cases)


def test_expected_candidates_validate_and_baseline_coverage_is_measured() -> None:
    report = evaluate_semantic_annotations(ANNOTATIONS)
    assert report.expected_candidates == 18
    assert report.matched_candidates == 5
    statuses = {result.case_id: result.status for result in report.results}
    assert statuses == {
        "ja-sem-01": CoverageStatus.semantic_required,
        "ja-sem-02": CoverageStatus.partial,
        "ja-sem-03": CoverageStatus.semantic_required,
        "ja-sem-04": CoverageStatus.partial,
        "en-sem-01": CoverageStatus.semantic_required,
        "en-sem-02": CoverageStatus.semantic_required,
        "en-sem-03": CoverageStatus.semantic_required,
        "en-sem-04": CoverageStatus.partial,
        "ja-sem-neg-01": CoverageStatus.negative_guard,
        "en-sem-neg-01": CoverageStatus.negative_guard,
    }


def test_invalid_annotation_fails_with_case_identity(tmp_path: pathlib.Path) -> None:
    payload = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    payload["cases"][0]["expected_facts"][0]["slot"] = "invented_slot"
    invalid = tmp_path / "invalid-semantic-annotations.json"
    invalid.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SemanticEvalInputError, match="ja-sem-01.*unknown slot"):
        evaluate_semantic_annotations(invalid)


def test_committed_report_matches_executable_harness() -> None:
    rendered = render_semantic_eval_markdown(evaluate_semantic_annotations(ANNOTATIONS))
    assert "## Failure taxonomy" in rendered
    assert "## Recommendation: do not enable yet" in rendered
    assert "Zero false positives remains a promotion gate" in rendered
    assert REPORT.read_text(encoding="utf-8") == rendered
