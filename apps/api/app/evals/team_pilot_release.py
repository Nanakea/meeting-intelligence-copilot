"""Fail-closed aggregate evidence contracts for the signed team pilot."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
_TIMESTAMP_SUFFIX = "Z"
_CONNECTOR_KINDS = {"microsoft365", "dynamics365"}
_CONNECTOR_FIELDS = {
    "kind",
    "queries",
    "hard_negative_queries",
    "true_positive_citations",
    "false_positive_citations",
    "false_negative_citations",
    "unauthorized_citations",
    "acl_revocation_passed",
    "expired_credentials_passed",
    "throttling_passed",
    "meeting_only_fallback_passed",
}


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith(_TIMESTAMP_SUFFIX):
        raise ValueError(f"{field} must be a UTC timestamp ending in Z")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{field} is invalid") from error


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be an exact SHA-256")
    return value.upper()


def _exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has missing or unsupported fields")


def validate_connector_acceptance(evidence: object) -> dict[str, float | int]:
    """Validate aggregate real-tenant retrieval evidence without accepting content."""

    if not isinstance(evidence, dict):
        raise ValueError("connector acceptance must be an object")
    _exact_fields(
        evidence,
        {"schema_version", "candidate_sha256", "completed_at_utc", "sources"},
        "connector acceptance",
    )
    if evidence["schema_version"] != 1:
        raise ValueError("connector acceptance schema_version must be 1")
    _sha256(evidence["candidate_sha256"], "candidate_sha256")
    _timestamp(evidence["completed_at_utc"], "completed_at_utc")
    sources = evidence["sources"]
    if not isinstance(sources, list) or len(sources) != 2:
        raise ValueError("connector acceptance requires Microsoft 365 and Dynamics 365")

    seen: set[str] = set()
    totals = {
        "queries": 0,
        "hard_negatives": 0,
        "true_positives": 0,
        "false_positives": 0,
        "false_negatives": 0,
    }
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("connector source evidence must be an object")
        _exact_fields(source, _CONNECTOR_FIELDS, "connector source evidence")
        kind = source["kind"]
        if kind not in _CONNECTOR_KINDS or kind in seen:
            raise ValueError("connector source kinds must be unique Microsoft 365 and Dynamics 365")
        seen.add(kind)
        for field in (
            "queries",
            "hard_negative_queries",
            "true_positive_citations",
            "false_positive_citations",
            "false_negative_citations",
            "unauthorized_citations",
        ):
            if not isinstance(source[field], int) or source[field] < 0:
                raise ValueError(f"{kind}.{field} must be a non-negative integer")
        if source["queries"] < 100:
            raise ValueError(f"{kind} requires at least 100 queries")
        if source["hard_negative_queries"] * 10 < source["queries"] * 3:
            raise ValueError(f"{kind} requires at least 30% hard negatives")
        if source["unauthorized_citations"] != 0:
            raise ValueError(f"{kind} returned unauthorized citations")
        for field in (
            "acl_revocation_passed",
            "expired_credentials_passed",
            "throttling_passed",
            "meeting_only_fallback_passed",
        ):
            if source[field] is not True:
                raise ValueError(f"{kind}.{field} must pass")
        totals["queries"] += source["queries"]
        totals["hard_negatives"] += source["hard_negative_queries"]
        totals["true_positives"] += source["true_positive_citations"]
        totals["false_positives"] += source["false_positive_citations"]
        totals["false_negatives"] += source["false_negative_citations"]

    precision = totals["true_positives"] / max(
        1, totals["true_positives"] + totals["false_positives"]
    )
    if precision < 0.95:
        raise ValueError("connector citation precision is below 95%")
    return {**totals, "precision": precision}


def validate_human_pilot_summary(evidence: object) -> dict[str, float | int]:
    """Validate transcript-free aggregate pilot results against promotion thresholds."""

    if not isinstance(evidence, dict):
        raise ValueError("human pilot summary must be an object")
    expected = {
        "schema_version",
        "candidate_sha256",
        "started_at_utc",
        "completed_at_utc",
        "participants",
        "usable_meetings",
        "ja_meetings",
        "en_meetings",
        "recording_sessions_started",
        "recording_sessions_completed",
        "ask_now_presented",
        "ask_now_rated",
        "ask_now_useful",
        "disruptive_false_prompts",
        "data_loss_incidents",
        "security_incidents",
        "connector_outage_fallback_checks",
        "connector_outage_fallback_failures",
    }
    _exact_fields(evidence, expected, "human pilot summary")
    if evidence["schema_version"] != 1:
        raise ValueError("human pilot schema_version must be 1")
    _sha256(evidence["candidate_sha256"], "candidate_sha256")
    started = _timestamp(evidence["started_at_utc"], "started_at_utc")
    completed = _timestamp(evidence["completed_at_utc"], "completed_at_utc")
    if (completed - started).total_seconds() < 14 * 24 * 60 * 60:
        raise ValueError("human pilot must run for at least two weeks")
    integer_fields = expected - {
        "schema_version",
        "candidate_sha256",
        "started_at_utc",
        "completed_at_utc",
    }
    for field in integer_fields:
        if not isinstance(evidence[field], int) or evidence[field] < 0:
            raise ValueError(f"{field} must be a non-negative integer")
    if not 5 <= evidence["participants"] <= 10:
        raise ValueError("human pilot requires 5-10 participants")
    if evidence["usable_meetings"] < 50:
        raise ValueError("human pilot requires at least 50 usable meetings")
    if evidence["ja_meetings"] < 1 or evidence["en_meetings"] < 1:
        raise ValueError("human pilot requires both Japanese and English meetings")
    if evidence["ja_meetings"] + evidence["en_meetings"] < evidence["usable_meetings"]:
        raise ValueError("language meeting counts do not cover usable meetings")
    if evidence["recording_sessions_started"] < evidence["usable_meetings"]:
        raise ValueError("recording session count is inconsistent")
    if evidence["recording_sessions_completed"] > evidence["recording_sessions_started"]:
        raise ValueError("completed recording sessions exceed starts")
    completion_rate = evidence["recording_sessions_completed"] / max(
        1, evidence["recording_sessions_started"]
    )
    if completion_rate < 0.99:
        raise ValueError("recording completion rate is below 99%")
    if evidence["ask_now_rated"] > evidence["ask_now_presented"]:
        raise ValueError("rated ASK NOW count exceeds presented count")
    useful_rate = evidence["ask_now_useful"] / max(1, evidence["ask_now_rated"])
    if useful_rate < 0.70:
        raise ValueError("ASK NOW useful rate is below 70%")
    false_prompt_rate = evidence["disruptive_false_prompts"] / max(1, evidence["ask_now_presented"])
    if false_prompt_rate >= 0.10:
        raise ValueError("disruptive false-prompt rate must be below 10%")
    if evidence["data_loss_incidents"] or evidence["security_incidents"]:
        raise ValueError("human pilot contains a data-loss or security incident")
    if (
        evidence["connector_outage_fallback_checks"] < 1
        or evidence["connector_outage_fallback_failures"] != 0
    ):
        raise ValueError("meeting-only connector outage fallback was not proven")
    return {
        "completion_rate": completion_rate,
        "useful_rate": useful_rate,
        "false_prompt_rate": false_prompt_rate,
        "usable_meetings": evidence["usable_meetings"],
    }
