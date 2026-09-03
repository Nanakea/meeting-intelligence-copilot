"""Opt-in loopback-only semantic document-claim suggestions."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.analysis.ollama_semantic import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    JsonTransport,
    UrllibJsonTransport,
    validate_loopback_ollama_url,
)
from app.domain.consistency import (
    ClaimOrigin,
    ClaimStatus,
    ConsistencyCitation,
    DocumentClaim,
)
from app.domain.evidence import ArtifactSection, DocumentArtifact

FEATURE_FLAG = "MEETING_INTELLIGENCE_CONSISTENCY_SEMANTIC_ANALYZER"
MAX_SECTIONS = 20
MAX_SECTION_CHARS = 2_000
TIMEOUT_SECONDS = 5.0


class _Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    canonical_subject: str = Field(min_length=1, max_length=300)
    canonical_property: str = Field(min_length=1, max_length=160)
    exact_value: str = Field(min_length=1, max_length=1_000)
    value_type: str | None = Field(default=None, max_length=120)
    environment: str | None = Field(default=None, max_length=120)
    version: str | None = Field(default=None, max_length=120)
    confidence: float = Field(ge=0.0, le=1.0)


class _CandidateBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[_Candidate] = Field(default_factory=list, max_length=100)


class NullConsistencyClaimSuggester:
    def suggest(
        self,
        artifacts: list[DocumentArtifact],
        sections: list[ArtifactSection],
        connector_labels: dict[str, str],
        language: str,
    ) -> list[DocumentClaim]:
        del artifacts, sections, connector_labels, language
        return []


class OllamaConsistencyClaimSuggester:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_OLLAMA_MODEL,
        transport: JsonTransport | None = None,
    ) -> None:
        self._base_url = validate_loopback_ollama_url(base_url)
        self._model = model.strip()
        if not self._model:
            raise ValueError("Ollama model must not be blank")
        self._transport = transport or UrllibJsonTransport()

    def suggest(
        self,
        artifacts: list[DocumentArtifact],
        sections: list[ArtifactSection],
        connector_labels: dict[str, str],
        language: str,
    ) -> list[DocumentClaim]:
        artifacts_by_id = {value.artifact_id: value for value in artifacts}
        bounded = [
            value
            for value in sections
            if value.artifact_id in artifacts_by_id
        ][:MAX_SECTIONS]
        if not bounded:
            return []
        prompt = json.dumps(
            {
                "task": "Propose explicit system claims from supplied document sections.",
                "rules": [
                    "Return JSON with a claims array only.",
                    "Use only supplied section_id values.",
                    "exact_value must be copied literally from that section.",
                    "Do not infer absent values, authority, severity, or mismatches.",
                    "Return an empty array rather than guessing.",
                ],
                "language": language,
                "sections": [
                    {
                        "section_id": value.section_id,
                        "title": value.title,
                        "content": value.content[:MAX_SECTION_CHARS],
                    }
                    for value in bounded
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            envelope = self._transport.post_json(
                f"{self._base_url}/api/generate",
                {"model": self._model, "stream": False, "format": "json", "prompt": prompt},
                TIMEOUT_SECONDS,
            )
            response = envelope.get("response")
            if not isinstance(response, str):
                return []
            batch = _CandidateBatch.model_validate(json.loads(response))
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError, ValidationError):
            return []
        sections_by_id = {value.section_id: value for value in bounded}
        proposals: list[DocumentClaim] = []
        for candidate in batch.claims:
            section = sections_by_id.get(candidate.section_id)
            if section is None or candidate.exact_value not in section.content:
                continue
            artifact = artifacts_by_id[section.artifact_id]
            subject = " ".join(candidate.canonical_subject.casefold().split())
            property_name = " ".join(candidate.canonical_property.casefold().split())
            value = " ".join(candidate.exact_value.split())
            fingerprint = hashlib.sha256(
                f"semantic\0{subject}\0{property_name}\0{value}\0{section.section_id}".encode()
            ).hexdigest()
            proposals.append(
                DocumentClaim(
                    claim_id=hashlib.sha256(
                        f"{fingerprint}\0{artifact.current_revision_id}".encode()
                    ).hexdigest(),
                    fingerprint=fingerprint,
                    canonical_subject=subject,
                    canonical_property=property_name,
                    expected_value=value,
                    expected_value_sha256=hashlib.sha256(value.encode()).hexdigest(),
                    value_type=candidate.value_type,
                    environment=candidate.environment,
                    version=candidate.version,
                    language=language,
                    document_kind=artifact.document_kind,
                    origin=ClaimOrigin.semantic_suggested,
                    status=ClaimStatus.proposed,
                    confidence=candidate.confidence,
                    citation=ConsistencyCitation(
                        connector_id=artifact.connector_id,
                        source_kind=artifact.source_kind,
                        source_label=connector_labels.get(
                            artifact.connector_id, artifact.source_kind.value
                        ),
                        source_reference=artifact.source_reference,
                        source_revision=artifact.current_revision_id,
                        locator=section.locator,
                        retrieved_at=artifact.indexed_at,
                        freshness="current",
                    ),
                )
            )
        return proposals


def consistency_claim_suggester_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    transport: JsonTransport | None = None,
):
    values = os.environ if environ is None else environ
    if values.get(FEATURE_FLAG, "off").strip().lower() != "ollama":
        return NullConsistencyClaimSuggester()
    return OllamaConsistencyClaimSuggester(
        base_url=values.get("MEETING_INTELLIGENCE_OLLAMA_URL", DEFAULT_OLLAMA_URL),
        model=values.get("MEETING_INTELLIGENCE_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        transport=transport,
    )
