"""Privacy-safe acceptance ledger for external routed-audio pilot gates."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from app.evals.pilot_matrix import platform_path_matrix, routed_audio_matrix

SCHEMA_VERSION = 2
REQUIRED_CHECKS = (
    "audio_reached_meetily",
    "transcript_reached_backend",
    "panel_projection_matched",
    "recording_remained_healthy",
    "session_cleanup_confirmed",
)
FAILURE_STAGES = frozenset(
    {
        "audio_route",
        "local_stt",
        "backend_transport",
        "panel_projection",
        "recording_health",
        "session_cleanup",
    }
)
STATUS_VALUES = frozenset({"pending", "pass", "fail"})
LEDGER_FIELDS = frozenset(
    {
        "schema_version",
        "candidate_sha256",
        "assistant_commit",
        "meetily_commit",
        "corpus_manifest_sha256",
        "created_at_utc",
        "scenarios",
    }
)
SCENARIO_RESULT_FIELDS = frozenset(
    {
        "scenario_id",
        "kind",
        "category",
        "platform",
        "lang",
        "acoustic_profile",
        "seed",
        "source_audio_scenario_id",
        "status",
        "checks",
        "failure_stage",
        "completed_at_utc",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class AcceptanceSummary:
    total: int
    passed: int
    failed: int
    pending: int
    complete: bool
    all_passed: bool


def _expected_scenarios() -> dict[str, dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    for scenario in routed_audio_matrix():
        expected[scenario.scenario_id] = {
            "scenario_id": scenario.scenario_id,
            "kind": "routed_audio",
            "category": scenario.category,
            "platform": None,
            "lang": scenario.lang,
            "acoustic_profile": scenario.acoustic_profile,
            "seed": scenario.seed,
            "source_audio_scenario_id": scenario.scenario_id,
        }
    for scenario in platform_path_matrix():
        expected[scenario.scenario_id] = {
            "scenario_id": scenario.scenario_id,
            "kind": "platform_path",
            "category": scenario.category,
            "platform": scenario.platform,
            "lang": scenario.lang,
            "acoustic_profile": scenario.acoustic_profile,
            "seed": scenario.seed,
            "source_audio_scenario_id": scenario.source_audio_scenario_id,
        }
    return expected


def _validate_timestamp(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")


def new_acceptance_ledger(
    *,
    candidate_sha256: str,
    assistant_commit: str,
    meetily_commit: str,
    corpus_manifest_sha256: str,
    created_at_utc: str,
) -> dict[str, Any]:
    """Create a pending ledger; generated scenarios are evidence requirements, not passes."""
    if not _SHA256_RE.fullmatch(candidate_sha256):
        raise ValueError("candidate_sha256 must be exactly 64 hexadecimal characters")
    if not _COMMIT_RE.fullmatch(assistant_commit):
        raise ValueError("assistant_commit must be an exact 40-character hexadecimal commit id")
    if not _COMMIT_RE.fullmatch(meetily_commit):
        raise ValueError("meetily_commit must be an exact 40-character hexadecimal commit id")
    if not _SHA256_RE.fullmatch(corpus_manifest_sha256):
        raise ValueError(
            "corpus_manifest_sha256 must be exactly 64 hexadecimal characters"
        )
    _validate_timestamp(created_at_utc, "created_at_utc")

    scenarios = []
    for descriptor in _expected_scenarios().values():
        scenarios.append(
            {
                **descriptor,
                "status": "pending",
                "checks": {name: False for name in REQUIRED_CHECKS},
                "failure_stage": None,
                "completed_at_utc": None,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_sha256": candidate_sha256.upper(),
        "assistant_commit": assistant_commit.lower(),
        "meetily_commit": meetily_commit.lower(),
        "corpus_manifest_sha256": corpus_manifest_sha256.upper(),
        "created_at_utc": created_at_utc,
        "scenarios": scenarios,
    }


def record_acceptance_result(
    ledger: dict[str, Any],
    *,
    scenario_id: str,
    passed: bool,
    completed_at_utc: str,
    failure_stage: str | None = None,
) -> None:
    """Record an operator result without accepting transcript text or free-form notes."""
    validate_acceptance_ledger(ledger)
    _validate_timestamp(completed_at_utc, "completed_at_utc")
    if passed and failure_stage is not None:
        raise ValueError("a passing scenario cannot include a failure_stage")
    if not passed and failure_stage not in FAILURE_STAGES:
        raise ValueError(f"failure_stage must be one of: {', '.join(sorted(FAILURE_STAGES))}")

    scenarios = ledger["scenarios"]
    scenario = next(
        (item for item in scenarios if item["scenario_id"] == scenario_id),
        None,
    )
    if scenario is None:
        raise ValueError(f"unknown acceptance scenario: {scenario_id}")
    scenario["status"] = "pass" if passed else "fail"
    scenario["checks"] = {name: passed for name in REQUIRED_CHECKS}
    scenario["failure_stage"] = failure_stage
    scenario["completed_at_utc"] = completed_at_utc


def validate_acceptance_ledger(
    ledger: object,
    *,
    require_complete: bool = False,
    require_all_passed: bool = False,
) -> AcceptanceSummary:
    """Validate shape, matrix completeness, immutable descriptors, and result semantics."""
    if not isinstance(ledger, dict):
        raise ValueError("acceptance ledger must be a JSON object")
    unknown_ledger_fields = set(ledger) - LEDGER_FIELDS
    if unknown_ledger_fields:
        raise ValueError(
            f"acceptance ledger contains unsupported fields: {sorted(unknown_ledger_fields)}"
        )
    if set(ledger) != LEDGER_FIELDS:
        raise ValueError("acceptance ledger is missing required fields")
    if ledger["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    if not isinstance(ledger["candidate_sha256"], str) or not _SHA256_RE.fullmatch(
        ledger["candidate_sha256"]
    ):
        raise ValueError("candidate_sha256 must be exactly 64 hexadecimal characters")
    for field in ("assistant_commit", "meetily_commit"):
        value = ledger[field]
        if not isinstance(value, str) or not _COMMIT_RE.fullmatch(value):
            raise ValueError(
                f"{field} must be an exact 40-character hexadecimal commit id"
            )
    corpus_manifest_sha256 = ledger["corpus_manifest_sha256"]
    if not isinstance(corpus_manifest_sha256, str) or not _SHA256_RE.fullmatch(
        corpus_manifest_sha256
    ):
        raise ValueError(
            "corpus_manifest_sha256 must be exactly 64 hexadecimal characters"
        )
    _validate_timestamp(ledger["created_at_utc"], "created_at_utc")

    scenarios = ledger["scenarios"]
    if not isinstance(scenarios, list):
        raise ValueError("scenarios must be a JSON array")
    expected = _expected_scenarios()
    seen: set[str] = set()
    counts = {"pass": 0, "fail": 0, "pending": 0}

    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError("each scenario must be a JSON object")
        if set(scenario) != SCENARIO_RESULT_FIELDS:
            raise ValueError("scenario contains missing or unsupported fields")
        scenario_id = scenario["scenario_id"]
        if not isinstance(scenario_id, str) or scenario_id not in expected:
            raise ValueError(f"unknown acceptance scenario: {scenario_id!r}")
        if scenario_id in seen:
            raise ValueError(f"duplicate acceptance scenario: {scenario_id}")
        seen.add(scenario_id)

        descriptor = {
            field: scenario[field]
            for field in (
                "scenario_id",
                "kind",
                "category",
                "platform",
                "lang",
                "acoustic_profile",
                "seed",
                "source_audio_scenario_id",
            )
        }
        if descriptor != expected[scenario_id]:
            raise ValueError(f"scenario descriptor was modified: {scenario_id}")

        status = scenario["status"]
        if status not in STATUS_VALUES:
            raise ValueError(f"invalid status for {scenario_id}")
        checks = scenario["checks"]
        if not isinstance(checks, dict) or set(checks) != set(REQUIRED_CHECKS):
            raise ValueError(f"invalid checks for {scenario_id}")
        if any(not isinstance(value, bool) for value in checks.values()):
            raise ValueError(f"checks must be booleans for {scenario_id}")

        failure_stage = scenario["failure_stage"]
        completed_at = scenario["completed_at_utc"]
        if status == "pending":
            if any(checks.values()) or failure_stage is not None or completed_at is not None:
                raise ValueError(f"pending scenario contains result data: {scenario_id}")
        elif status == "pass":
            if not all(checks.values()) or failure_stage is not None:
                raise ValueError(f"passing scenario is missing a required check: {scenario_id}")
            _validate_timestamp(completed_at, f"{scenario_id}.completed_at_utc")
        else:
            if failure_stage not in FAILURE_STAGES:
                raise ValueError(f"failed scenario has invalid failure_stage: {scenario_id}")
            _validate_timestamp(completed_at, f"{scenario_id}.completed_at_utc")
        counts[status] += 1

    missing = set(expected) - seen
    if missing:
        raise ValueError(f"acceptance ledger is missing {len(missing)} scenario(s)")
    summary = AcceptanceSummary(
        total=len(scenarios),
        passed=counts["pass"],
        failed=counts["fail"],
        pending=counts["pending"],
        complete=counts["pending"] == 0,
        all_passed=counts["pass"] == len(scenarios),
    )
    if require_complete and not summary.complete:
        raise ValueError(f"acceptance ledger has {summary.pending} pending scenario(s)")
    if require_all_passed and not summary.all_passed:
        raise ValueError(
            f"acceptance ledger is not all-pass: {summary.failed} failed, "
            f"{summary.pending} pending"
        )
    return summary


def acceptance_summary_dict(summary: AcceptanceSummary) -> dict[str, Any]:
    return asdict(summary)


def validate_release_binding(
    ledger: object,
    manifest: object,
) -> AcceptanceSummary:
    """Bind completed external acceptance to one immutable pilot executable."""

    summary = validate_acceptance_ledger(
        ledger,
        require_complete=True,
        require_all_passed=True,
    )
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 2:
        raise ValueError("pilot manifest schema is invalid")
    if (
        manifest.get("candidate_kind")
        != "unsigned_synthetic_tested_internal_pilot"
        or manifest.get("human_validated") is not False
        or manifest.get("public_distribution_approved") is not False
    ):
        raise ValueError("pilot manifest release classification is invalid")
    if (
        manifest.get("product_version") != "0.6.1"
        or manifest.get("compatibility_api_version") != 13
        or manifest.get("supported_languages") != ["ja", "en", "ko"]
        or manifest.get("korean_acceptance") != "synthetic_only"
    ):
        raise ValueError("pilot manifest product compatibility is invalid")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise ValueError("pilot manifest source provenance is missing")
    if source.get("assistant_commit") != ledger["assistant_commit"]:
        raise ValueError("assistant commit differs between ledger and manifest")
    if source.get("meetily_commit") != ledger["meetily_commit"]:
        raise ValueError("Meetily commit differs between ledger and manifest")
    acceptance_inputs = manifest.get("acceptance_inputs")
    if not isinstance(acceptance_inputs, dict):
        raise ValueError("pilot manifest acceptance inputs are missing")
    manifest_corpus_hash = acceptance_inputs.get("routed_audio_corpus_manifest_sha256")
    if not isinstance(manifest_corpus_hash, str) or not _SHA256_RE.fullmatch(
        manifest_corpus_hash
    ):
        raise ValueError("pilot manifest corpus SHA-256 is invalid")
    if manifest_corpus_hash.upper() != ledger["corpus_manifest_sha256"]:
        raise ValueError("corpus manifest SHA-256 differs between ledger and manifest")
    for field in (
        "acceptance_ledger_sha256",
        "whisper_model_manifest_sha256",
        "whisper_model_manifest_signature_sha256",
    ):
        value = acceptance_inputs.get(field)
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise ValueError(f"pilot manifest {field} is invalid")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("pilot manifest artifacts are missing")
    executables = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get("role") == "meetily_executable"
    ]
    if len(executables) != 1:
        raise ValueError("pilot manifest requires one Meetily executable")
    executable_hash = executables[0].get("sha256")
    if not isinstance(executable_hash, str) or not _SHA256_RE.fullmatch(executable_hash):
        raise ValueError("pilot manifest Meetily executable SHA-256 is invalid")
    if executable_hash.upper() != ledger["candidate_sha256"]:
        raise ValueError("candidate SHA-256 differs between ledger and manifest")
    return summary
