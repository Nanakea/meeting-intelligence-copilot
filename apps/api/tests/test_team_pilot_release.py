from copy import deepcopy
from pathlib import Path

import pytest

from app.evals.team_pilot_release import (
    validate_connector_acceptance,
    validate_human_pilot_summary,
)

HASH = "a" * 64
REPO_ROOT = Path(__file__).resolve().parents[3]


def connector_evidence() -> dict[str, object]:
    source = {
        "queries": 100,
        "hard_negative_queries": 30,
        "true_positive_citations": 70,
        "false_positive_citations": 0,
        "false_negative_citations": 0,
        "unauthorized_citations": 0,
        "acl_revocation_passed": True,
        "expired_credentials_passed": True,
        "throttling_passed": True,
        "meeting_only_fallback_passed": True,
    }
    return {
        "schema_version": 1,
        "candidate_sha256": HASH,
        "completed_at_utc": "2026-09-20T00:00:00Z",
        "sources": [
            {"kind": "microsoft365", **source},
            {"kind": "dynamics365", **source},
        ],
    }


def human_evidence() -> dict[str, object]:
    return {
        "schema_version": 1,
        "candidate_sha256": HASH,
        "started_at_utc": "2026-09-01T00:00:00Z",
        "completed_at_utc": "2026-09-15T00:00:00Z",
        "participants": 7,
        "usable_meetings": 50,
        "ja_meetings": 25,
        "en_meetings": 25,
        "recording_sessions_started": 100,
        "recording_sessions_completed": 99,
        "ask_now_presented": 100,
        "ask_now_rated": 80,
        "ask_now_useful": 56,
        "disruptive_false_prompts": 9,
        "data_loss_incidents": 0,
        "security_incidents": 0,
        "connector_outage_fallback_checks": 4,
        "connector_outage_fallback_failures": 0,
    }


def test_connector_acceptance_requires_both_sources_and_precision() -> None:
    result = validate_connector_acceptance(connector_evidence())
    assert result["queries"] == 200
    assert result["precision"] == 1.0

    unauthorized = connector_evidence()
    unauthorized["sources"][0]["unauthorized_citations"] = 1
    with pytest.raises(ValueError, match="unauthorized"):
        validate_connector_acceptance(unauthorized)

    low_precision = connector_evidence()
    low_precision["sources"][0]["false_positive_citations"] = 8
    with pytest.raises(ValueError, match="precision"):
        validate_connector_acceptance(low_precision)


def test_connector_acceptance_rejects_content_shaped_extra_fields() -> None:
    evidence = connector_evidence()
    evidence["sources"][0]["excerpt"] = "must not be accepted"
    with pytest.raises(ValueError, match="missing or unsupported"):
        validate_connector_acceptance(evidence)


def test_human_pilot_summary_meets_all_promotion_thresholds() -> None:
    result = validate_human_pilot_summary(human_evidence())
    assert result["completion_rate"] == 0.99
    assert result["useful_rate"] == 0.70
    assert result["false_prompt_rate"] == 0.09


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("participants", 4, "5-10"),
        ("usable_meetings", 49, "50 usable"),
        ("recording_sessions_completed", 98, "99%"),
        ("ask_now_useful", 55, "70%"),
        ("disruptive_false_prompts", 10, "below 10%"),
        ("data_loss_incidents", 1, "incident"),
        ("connector_outage_fallback_failures", 1, "fallback"),
    ],
)
def test_human_pilot_summary_fails_closed(field: str, value: int, message: str) -> None:
    evidence = human_evidence()
    evidence[field] = value
    with pytest.raises(ValueError, match=message):
        validate_human_pilot_summary(evidence)


def test_human_pilot_summary_rejects_sensitive_extra_fields() -> None:
    evidence = deepcopy(human_evidence())
    evidence["meeting_titles"] = ["private"]
    with pytest.raises(ValueError, match="missing or unsupported"):
        validate_human_pilot_summary(evidence)


def test_final_manifest_generator_is_signed_and_fail_closed() -> None:
    source = (REPO_ROOT / "scripts" / "create-team-pilot-release-manifest.py").read_text(
        encoding="utf-8"
    )
    assert '"signed_ja_en_team_pilot"' in source
    assert '"product_version": "0.6.1"' in source
    assert 'value.get("Status") != "Valid"' in source
    assert "require_complete=True, require_all_passed=True" in source
    assert "validate_connector_acceptance" in source
    assert "validate_human_pilot_summary" in source
    assert "public_distribution_approved" in source
    assert "transcript" not in source.lower()
