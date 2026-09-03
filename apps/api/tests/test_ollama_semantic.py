"""Local-only Ollama adapter tests; every transport is in-memory."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.adapters.analysis.ollama_semantic import (
    FEATURE_FLAG,
    OllamaSemanticCandidateAnalyzer,
    semantic_candidate_analyzer_from_env,
    validate_loopback_ollama_url,
)
from app.domain.contracts import Lang, Speaker, TranscriptEvent
from app.domain.semantic_candidates import CandidateKind, SemanticObservationBatch
from app.services.semantic_candidate_analyzer import NullSemanticCandidateAnalyzer


def _event(event_id: str = "e1") -> TranscriptEvent:
    return TranscriptEvent(
        event_id=event_id,
        meeting_id="m1",
        seq=0,
        speaker=Speaker(id="speaker"),
        text="EC is the source of truth.",
        lang=Lang.en,
        ts_start=0.0,
        ts_end=1.0,
        source="ollama-adapter-test",
    )


class _FakeTransport:
    def __init__(self, response: dict[str, Any] | BaseException) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    def post_json(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.calls.append((url, payload, timeout))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _envelope(batch: dict[str, Any]) -> dict[str, Any]:
    return {"response": json.dumps(batch)}


def _valid_batch() -> dict[str, Any]:
    return {
        "pains": [],
        "facts": [
            {
                "slot": "source_of_truth",
                "value": "EC",
                "kind": "evidence",
                "evidence_event_ids": ["e1"],
                "pain_category_hint": "data_mismatch",
                "confidence": 0.92,
                "reason": "Explicitly stated.",
            }
        ],
    }


def test_valid_response_is_structurally_validated_and_accepted() -> None:
    transport = _FakeTransport(_envelope(_valid_batch()))
    provider = OllamaSemanticCandidateAnalyzer(transport=transport)

    result = provider.observe([_event()])

    assert result.facts[0].kind is CandidateKind.evidence
    assert result.facts[0].value == "EC"
    url, payload, timeout = transport.calls[0]
    assert url == "http://127.0.0.1:11434/api/generate"
    assert payload["stream"] is False
    assert payload["format"] == "json"
    assert timeout > 0


@pytest.mark.parametrize(
    "response",
    [
        {"response": "not-json"},
        {"unexpected": "envelope"},
        {"response": json.dumps({"facts": "not-a-list", "pains": []})},
    ],
)
def test_malformed_output_fails_closed(response: dict[str, Any]) -> None:
    provider = OllamaSemanticCandidateAnalyzer(transport=_FakeTransport(response))
    assert provider.observe([_event()]) == SemanticObservationBatch()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pain_category_hint", "unknown_category"),
        ("slot", "unknown_slot"),
        ("evidence_event_ids", ["invented-event"]),
        ("evidence_event_ids", []),
    ],
)
def test_invalid_candidate_output_fails_closed(field: str, value: Any) -> None:
    batch = _valid_batch()
    batch["facts"][0][field] = value
    provider = OllamaSemanticCandidateAnalyzer(transport=_FakeTransport(_envelope(batch)))
    assert provider.observe([_event()]) == SemanticObservationBatch()


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:11434",
        "http://192.168.1.10:11434",
        "http://example.com:11434",
        "http://127.0.0.1.evil.example:11434",
        "http://user@127.0.0.1:11434",
        "http://127.0.0.1:11434/api/generate",
    ],
)
def test_non_loopback_or_unsafe_url_is_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        OllamaSemanticCandidateAnalyzer(base_url=url, transport=_FakeTransport({}))


def test_loopback_url_forms_are_normalized() -> None:
    assert validate_loopback_ollama_url("http://localhost") == "http://localhost:11434"
    assert validate_loopback_ollama_url("http://[::1]:11434/") == "http://[::1]:11434"


def test_timeout_fails_closed_without_state_or_question_output() -> None:
    provider = OllamaSemanticCandidateAnalyzer(
        timeout_seconds=0.1,
        transport=_FakeTransport(TimeoutError("local model timeout")),
    )
    assert provider.observe([_event()]) == SemanticObservationBatch()


def test_provider_is_disabled_by_default_and_unknown_values_are_safe_off() -> None:
    assert isinstance(semantic_candidate_analyzer_from_env({}), NullSemanticCandidateAnalyzer)
    assert isinstance(
        semantic_candidate_analyzer_from_env({FEATURE_FLAG: "unexpected"}),
        NullSemanticCandidateAnalyzer,
    )


def test_explicit_flag_selects_provider_but_does_not_call_it() -> None:
    transport = _FakeTransport(_envelope(_valid_batch()))
    provider = semantic_candidate_analyzer_from_env(
        {FEATURE_FLAG: "ollama"},
        transport=transport,
    )
    assert isinstance(provider, OllamaSemanticCandidateAnalyzer)
    assert transport.calls == []
