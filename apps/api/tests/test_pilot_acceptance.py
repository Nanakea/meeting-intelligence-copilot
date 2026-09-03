from copy import deepcopy

import pytest

from app.evals.pilot_acceptance import (
    REQUIRED_CHECKS,
    new_acceptance_ledger,
    record_acceptance_result,
    validate_acceptance_ledger,
    validate_release_binding,
)

HASH = "a" * 64
CREATED_AT = "2026-07-27T12:00:00Z"
ASSISTANT_COMMIT = "b" * 40
MEETILY_COMMIT = "c" * 40
CORPUS_HASH = "d" * 64


def ledger() -> dict[str, object]:
    return new_acceptance_ledger(
        candidate_sha256=HASH,
        assistant_commit=ASSISTANT_COMMIT,
        meetily_commit=MEETILY_COMMIT,
        corpus_manifest_sha256=CORPUS_HASH,
        created_at_utc=CREATED_AT,
    )


def test_new_ledger_contains_all_scenarios_as_pending_not_passed() -> None:
    value = ledger()

    summary = validate_acceptance_ledger(value)

    assert summary.total == 132
    assert summary.pending == 132
    assert summary.passed == 0
    assert not summary.complete
    assert not summary.all_passed
    platform = next(
        scenario
        for scenario in value["scenarios"]
        if scenario["scenario_id"] == "platform-zoom_desktop-en-2"
    )
    assert platform["source_audio_scenario_id"] == (
        "audio-manual_work-en-office_noise-2"
    )
    assert platform["category"] == "manual_work"
    assert platform["acoustic_profile"] == "office_noise"


def test_all_scenarios_require_explicit_passing_results() -> None:
    value = ledger()
    for scenario in value["scenarios"]:
        record_acceptance_result(
            value,
            scenario_id=scenario["scenario_id"],
            passed=True,
            completed_at_utc=CREATED_AT,
        )

    summary = validate_acceptance_ledger(
        value,
        require_complete=True,
        require_all_passed=True,
    )

    assert summary.passed == 132
    assert summary.all_passed


def test_failed_result_uses_bounded_stage_without_free_form_content() -> None:
    value = ledger()

    record_acceptance_result(
        value,
        scenario_id="platform-zoom_desktop-en-1",
        passed=False,
        failure_stage="audio_route",
        completed_at_utc=CREATED_AT,
    )

    summary = validate_acceptance_ledger(value)
    result = next(
        scenario
        for scenario in value["scenarios"]
        if scenario["scenario_id"] == "platform-zoom_desktop-en-1"
    )
    assert summary.failed == 1
    assert result["failure_stage"] == "audio_route"
    assert not any(result["checks"].values())


def test_validator_rejects_matrix_tampering_and_duplicate_scenarios() -> None:
    modified = ledger()
    modified["scenarios"][0]["lang"] = "en"
    with pytest.raises(ValueError, match="descriptor was modified"):
        validate_acceptance_ledger(modified)

    duplicated = ledger()
    duplicated["scenarios"][-1] = deepcopy(duplicated["scenarios"][0])
    with pytest.raises(ValueError, match="duplicate acceptance scenario"):
        validate_acceptance_ledger(duplicated)


def test_validator_rejects_unknown_fields_that_could_hold_sensitive_data() -> None:
    value = ledger()
    value["scenarios"][0]["transcript"] = "must never be accepted"

    with pytest.raises(ValueError, match="missing or unsupported fields"):
        validate_acceptance_ledger(value)


def test_passing_status_cannot_skip_a_required_attestation() -> None:
    value = ledger()
    scenario = value["scenarios"][0]
    scenario["status"] = "pass"
    scenario["completed_at_utc"] = CREATED_AT
    scenario["checks"] = {name: True for name in REQUIRED_CHECKS}
    scenario["checks"]["session_cleanup_confirmed"] = False

    with pytest.raises(ValueError, match="missing a required check"):
        validate_acceptance_ledger(value)


def test_release_gate_rejects_pending_or_failed_results() -> None:
    value = ledger()
    with pytest.raises(ValueError, match="pending"):
        validate_acceptance_ledger(value, require_complete=True)
    with pytest.raises(ValueError, match="not all-pass"):
        validate_acceptance_ledger(value, require_all_passed=True)


def test_acceptance_ledger_rejects_abbreviated_commit_ids() -> None:
    with pytest.raises(ValueError, match="exact 40-character"):
        new_acceptance_ledger(
            candidate_sha256=HASH,
            assistant_commit="b" * 7,
            meetily_commit=MEETILY_COMMIT,
            corpus_manifest_sha256=CORPUS_HASH,
            created_at_utc=CREATED_AT,
        )


def test_release_binding_requires_exact_candidate_and_source_provenance() -> None:
    value = ledger()
    for scenario in value["scenarios"]:
        record_acceptance_result(
            value,
            scenario_id=scenario["scenario_id"],
            passed=True,
            completed_at_utc=CREATED_AT,
        )
    manifest = {
        "schema_version": 2,
        "candidate_kind": "unsigned_synthetic_tested_internal_pilot",
        "product_version": "0.6.1",
        "compatibility_api_version": 13,
        "supported_languages": ["ja", "en", "ko"],
        "korean_acceptance": "synthetic_only",
        "human_validated": False,
        "public_distribution_approved": False,
        "source": {
            "assistant_commit": ASSISTANT_COMMIT,
            "meetily_commit": MEETILY_COMMIT,
        },
        "acceptance_inputs": {
            "routed_audio_corpus_manifest_sha256": CORPUS_HASH,
            "acceptance_ledger_sha256": "e" * 64,
            "whisper_model_manifest_sha256": "f" * 64,
            "whisper_model_manifest_signature_sha256": "1" * 64,
        },
        "artifacts": [{"role": "meetily_executable", "sha256": HASH}],
    }

    assert validate_release_binding(value, manifest).all_passed

    modified = deepcopy(manifest)
    modified["artifacts"][0]["sha256"] = "d" * 64
    with pytest.raises(ValueError, match="candidate SHA-256 differs"):
        validate_release_binding(value, modified)

    modified = deepcopy(manifest)
    modified["source"]["assistant_commit"] = "d" * 40
    with pytest.raises(ValueError, match="assistant commit differs"):
        validate_release_binding(value, modified)

    modified = deepcopy(manifest)
    modified["acceptance_inputs"]["routed_audio_corpus_manifest_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="corpus manifest SHA-256 differs"):
        validate_release_binding(value, modified)


def test_release_binding_rejects_incomplete_acceptance() -> None:
    manifest = {
        "schema_version": 2,
        "candidate_kind": "unsigned_synthetic_tested_internal_pilot",
        "product_version": "0.6.1",
        "compatibility_api_version": 13,
        "supported_languages": ["ja", "en", "ko"],
        "korean_acceptance": "synthetic_only",
        "human_validated": False,
        "public_distribution_approved": False,
        "source": {
            "assistant_commit": ASSISTANT_COMMIT,
            "meetily_commit": MEETILY_COMMIT,
        },
        "acceptance_inputs": {
            "routed_audio_corpus_manifest_sha256": CORPUS_HASH,
            "acceptance_ledger_sha256": "e" * 64,
            "whisper_model_manifest_sha256": "f" * 64,
            "whisper_model_manifest_signature_sha256": "1" * 64,
        },
        "artifacts": [{"role": "meetily_executable", "sha256": HASH}],
    }

    with pytest.raises(ValueError, match="pending"):
        validate_release_binding(ledger(), manifest)
