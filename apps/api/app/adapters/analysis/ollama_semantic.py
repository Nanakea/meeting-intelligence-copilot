"""Opt-in, loopback-only Ollama semantic candidate adapter.

The adapter is composed only when the local semantic beta is explicitly enabled. It returns
validated candidate observations only and fails closed to an empty batch on
transport, parse, schema, or structural-validation failure. Transcript content
is never logged by this module.
"""

from __future__ import annotations

import ipaddress
import json
import os
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.domain.contracts import TranscriptEvent
from app.domain.semantic_candidates import (
    SemanticCandidateValidationError,
    SemanticObservationBatch,
    validate_semantic_observations,
)
from app.domain.templates import TEMPLATES
from app.services.semantic_candidate_analyzer import (
    NullSemanticCandidateAnalyzer,
    SemanticCandidateAnalyzer,
)

FEATURE_FLAG = "MEETING_INTELLIGENCE_SEMANTIC_ANALYZER"
OLLAMA_URL_ENV = "MEETING_INTELLIGENCE_OLLAMA_URL"
OLLAMA_MODEL_ENV = "MEETING_INTELLIGENCE_OLLAMA_MODEL"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:8b"
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_CONTEXT_EVENTS = 20
MAX_RESPONSE_BYTES = 1_000_000


class JsonTransport(Protocol):
    def post_json(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]: ...


class UrllibJsonTransport:
    """Small stdlib transport so Ollama adds no runtime dependency."""

    def post_json(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is validated below
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Ollama response exceeds size limit")
        decoded = json.loads(body.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Ollama response envelope must be an object")
        return decoded


class OllamaSemanticCandidateAnalyzer:
    """Best-effort local provider that can only return validated observations."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_OLLAMA_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: JsonTransport | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("Ollama model must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("Ollama timeout must be positive")
        self._base_url = validate_loopback_ollama_url(base_url)
        self._model = model.strip()
        self._timeout_seconds = timeout_seconds
        self._transport = transport or UrllibJsonTransport()

    def observe(self, events: list[TranscriptEvent]) -> SemanticObservationBatch:
        window = _bounded_final_window(events)
        if not window:
            return SemanticObservationBatch()

        payload = {
            "model": self._model,
            "stream": False,
            "format": "json",
            "prompt": _build_prompt(window),
        }
        try:
            envelope = self._transport.post_json(
                f"{self._base_url}/api/generate",
                payload,
                self._timeout_seconds,
            )
            raw_response = envelope.get("response")
            if not isinstance(raw_response, str):
                return SemanticObservationBatch()
            raw_batch = json.loads(raw_response)
            batch = SemanticObservationBatch.model_validate(raw_batch)
            return validate_semantic_observations(batch, window)
        except (
            OSError,
            TimeoutError,
            json.JSONDecodeError,
            ValidationError,
            SemanticCandidateValidationError,
            ValueError,
        ):
            return SemanticObservationBatch()


def semantic_candidate_analyzer_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    transport: JsonTransport | None = None,
) -> SemanticCandidateAnalyzer:
    """Select Ollama only for an explicit flag; every other value is safe-off."""

    values = os.environ if environ is None else environ
    if values.get(FEATURE_FLAG, "off").strip().lower() != "ollama":
        return NullSemanticCandidateAnalyzer()
    return OllamaSemanticCandidateAnalyzer(
        base_url=values.get(OLLAMA_URL_ENV, DEFAULT_OLLAMA_URL),
        model=values.get(OLLAMA_MODEL_ENV, DEFAULT_OLLAMA_MODEL),
        transport=transport,
    )


def validate_loopback_ollama_url(value: str) -> str:
    """Return a normalized loopback base URL or reject it."""

    try:
        parsed = urlsplit(value.strip())
        port = parsed.port or 11434
    except ValueError as exc:
        raise ValueError("invalid Ollama URL") from exc
    if parsed.scheme != "http":
        raise ValueError("Ollama URL must use http")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Ollama URL must not contain credentials, query, or fragment")
    if parsed.path not in ("", "/"):
        raise ValueError("Ollama URL must be a base URL without a path")
    host = parsed.hostname
    if host is None or not _is_loopback_host(host):
        raise ValueError("Ollama URL must resolve to an explicit loopback host")
    rendered_host = f"[{host}]" if ":" in host else host.lower()
    return f"http://{rendered_host}:{port}"


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _bounded_final_window(events: list[TranscriptEvent]) -> list[TranscriptEvent]:
    finals = [event for event in events if event.is_final]
    if not finals or len({event.meeting_id for event in finals}) != 1:
        return []
    return finals[-MAX_CONTEXT_EVENTS:]


def _build_prompt(events: list[TranscriptEvent]) -> str:
    templates = {category: list(template.slots) for category, template in TEMPLATES.items()}
    transcript = [
        {
            "event_id": event.event_id,
            "lang": event.lang.value,
            "speaker_id": event.speaker.id,
            "text": event.text,
        }
        for event in events
    ]
    instructions = {
        "task": "Propose semantic pain and fact candidates grounded only in supplied events.",
        "rules": [
            "Return one JSON object with pains and facts arrays only.",
            "Use only registered categories and slots.",
            "Every evidence_event_id must be copied from the supplied events.",
            "Every fact value must be copied exactly from its cited event text.",
            "Use kind=evidence only for explicit statements; otherwise use kind=inference.",
            "Do not generate questions, gaps, lifecycle status, or MeetingState.",
            "Return empty arrays rather than guessing.",
        ],
        "candidate_shape": {
            "pains": [
                {
                    "category": "registered category",
                    "title": "short title",
                    "kind": "evidence|inference",
                    "evidence_event_ids": ["event id"],
                    "confidence": 0.0,
                    "reason": "short rationale",
                }
            ],
            "facts": [
                {
                    "slot": "registered slot",
                    "value": "normalized nonblank value",
                    "kind": "evidence|inference",
                    "evidence_event_ids": ["event id"],
                    "pain_category_hint": "registered category",
                    "confidence": 0.0,
                    "reason": "short rationale",
                }
            ],
        },
        "templates": templates,
        "events": transcript,
    }
    return json.dumps(instructions, ensure_ascii=False, separators=(",", ":"))
