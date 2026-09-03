"""Failure-isolated broker for read-only context providers."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from app.domain.context import (
    ConnectorHealth,
    ConnectorPhase,
    ContextCitation,
    ContextDocument,
    ContextRelation,
    ContextSearchQuery,
    ContextSearchResult,
)
from app.domain.ports import ContextProvider

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TOKEN = re.compile(r"[\w.-]+", re.UNICODE)


def sanitize_external_text(value: str, *, limit: int) -> str:
    """Bound untrusted connector content without interpreting instructions in it."""

    return _CONTROL_CHARACTERS.sub(" ", value).replace("\r\n", "\n").strip()[:limit]


def _terms(text: str) -> list[str]:
    return list(dict.fromkeys(token.casefold() for token in _TOKEN.findall(text) if len(token) > 1))


def _score(document: ContextDocument, terms: list[str], phrase: str) -> int:
    title = document.title.casefold()
    content = document.content.casefold()
    reference = (document.source_reference or "").casefold()
    exact_reference = any(term == reference for term in terms)
    matched_terms = sum(
        term in title or term in content or term in reference for term in terms
    )
    phrase_match = bool(phrase and (phrase in title or phrase in content))
    if not exact_reference and matched_terms < 2 and not phrase_match:
        return 0
    score = sum(
        (12 if term == reference else 0)
        + (6 if term in reference else 0)
        + (4 if term in title else 0)
        + min(content.count(term), 4)
        for term in terms
    )
    if phrase and phrase in title:
        score += 10
    if phrase and phrase in content:
        score += 6
    return score


def _excerpt(document: ContextDocument, terms: list[str]) -> str:
    content = sanitize_external_text(document.content, limit=100_000)
    folded = content.casefold()
    positions = [folded.find(term) for term in terms if folded.find(term) >= 0]
    start = max(0, (min(positions) if positions else 0) - 120)
    excerpt = content[start : start + 560]
    if start:
        excerpt = f"...{excerpt}"
    if start + 560 < len(content):
        excerpt = f"{excerpt}..."
    return excerpt


def _public_uri(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    return value


def _relation(document: ContextDocument, query: ContextSearchQuery) -> ContextRelation:
    comparisons: list[bool] = []
    for slot, values in query.known_values.items():
        actual = document.structured_values.get(slot)
        expected = {
            value.casefold().strip() for value in values if value.strip()
        }
        if actual is not None and actual.strip() and expected:
            comparisons.append(actual.casefold().strip() in expected)
    if not comparisons:
        return ContextRelation.reference
    return ContextRelation.supporting if all(comparisons) else ContextRelation.conflicting


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    opened_until: float = 0.0
    half_open_in_flight: bool = False

    def permit(self, now: float) -> bool:
        if self.opened_until <= 0:
            return True
        if now < self.opened_until or self.half_open_in_flight:
            return False
        self.half_open_in_flight = True
        return True

    def succeeded(self) -> None:
        self.consecutive_failures = 0
        self.opened_until = 0.0
        self.half_open_in_flight = False

    def failed(self, now: float, cooldown_seconds: float) -> None:
        self.half_open_in_flight = False
        self.consecutive_failures += 1
        if self.consecutive_failures >= 3:
            self.opened_until = now + cooldown_seconds


class ContextBroker:
    """Queries providers concurrently while containing every provider failure."""

    def __init__(
        self,
        providers: list[ContextProvider] | None = None,
        *,
        timeout_seconds: float = 3.0,
        stale_after: timedelta = timedelta(hours=24),
        circuit_cooldown_seconds: float = 30.0,
    ) -> None:
        self._providers: dict[str, ContextProvider] = {}
        self._timeout_seconds = timeout_seconds
        self._stale_after = stale_after
        self._circuit_cooldown_seconds = circuit_cooldown_seconds
        self._circuits: dict[str, _CircuitState] = {}
        self._search_requests = 0
        self._citation_results = 0
        self._unavailable_provider_results = 0
        self._circuit_opened = 0
        self._max_search_latency_ms = 0
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: ContextProvider) -> None:
        if provider.connector_id in self._providers:
            raise ValueError("connector id is already registered")
        self._providers[provider.connector_id] = provider
        self._circuits[provider.connector_id] = _CircuitState()

    async def remove(self, connector_id: str) -> bool:
        provider = self._providers.pop(connector_id, None)
        self._circuits.pop(connector_id, None)
        if provider is None:
            return False
        try:
            await asyncio.wait_for(provider.close(), timeout=self._timeout_seconds)
        except Exception:
            # Disconnect remains idempotent and cannot affect meeting processing.
            pass
        return True

    async def close(self) -> None:
        await asyncio.gather(
            *(self.remove(connector_id) for connector_id in list(self._providers)),
            return_exceptions=True,
        )

    async def health(self) -> list[ConnectorHealth]:
        async def one(provider: ContextProvider) -> ConnectorHealth:
            try:
                health = await asyncio.wait_for(
                    provider.health(), timeout=self._timeout_seconds
                )
                circuit = self._circuits[provider.connector_id]
                if circuit.opened_until > time.monotonic():
                    retry_seconds = circuit.opened_until - time.monotonic()
                    return health.model_copy(
                        update={
                            "phase": ConnectorPhase.degraded,
                            "detail_code": "circuit_open",
                            "retry_at": datetime.now(UTC)
                            + timedelta(seconds=retry_seconds),
                        }
                    )
                return health
            except Exception:
                return ConnectorHealth(
                    connector_id=provider.connector_id,
                    kind=provider.kind,
                    display_name=provider.display_name,
                    phase=ConnectorPhase.unavailable,
                    auth_enabled=provider.auth_enabled,
                    detail_code="provider_unavailable",
                )

        return await asyncio.gather(*(one(provider) for provider in self._providers.values()))

    async def sync(self, connector_id: str) -> int:
        provider = self._providers.get(connector_id)
        if provider is None:
            raise KeyError(connector_id)
        return await asyncio.wait_for(provider.sync(), timeout=max(30.0, self._timeout_seconds))

    async def search(self, query: ContextSearchQuery) -> ContextSearchResult:
        started = time.monotonic()
        self._search_requests += 1
        selected = [
            provider
            for connector_id, provider in self._providers.items()
            if not query.connector_ids or connector_id in query.connector_ids
        ]

        async def one(
            provider: ContextProvider,
        ) -> tuple[str, list[ContextDocument] | None]:
            circuit = self._circuits[provider.connector_id]
            now = time.monotonic()
            if not circuit.permit(now):
                return provider.connector_id, None
            try:
                documents = await asyncio.wait_for(
                    provider.search(query), timeout=self._timeout_seconds
                )
                circuit.succeeded()
                return provider.connector_id, documents
            except Exception:
                was_open = circuit.opened_until > time.monotonic()
                circuit.failed(time.monotonic(), self._circuit_cooldown_seconds)
                if not was_open and circuit.opened_until > time.monotonic():
                    self._circuit_opened += 1
                return provider.connector_id, None

        responses = await asyncio.gather(*(one(provider) for provider in selected))
        unavailable: list[str] = []
        candidates: list[tuple[int, ContextDocument]] = []
        terms = _terms(query.text)
        for connector_id, documents in responses:
            if documents is None:
                unavailable.append(connector_id)
                continue
            for document in documents:
                # Access checks are repeated at the broker boundary even when a
                # provider already applies source-system ACLs.
                if query.principal_id not in document.allowed_principals:
                    continue
                if query.entity_types and document.entity_type not in query.entity_types:
                    continue
                score = _score(document, terms, query.text.casefold())
                if score > 0 and document.entity_type in query.preferred_entity_types:
                    score += 8
                candidates.append((score, document))

        candidates.sort(key=lambda item: (-item[0], item[1].document_id))
        now = datetime.now(UTC)
        selected_documents = [
            document for score, document in candidates if score > 0
        ][: query.limit]
        citations = [
            ContextCitation(
                citation_id=hashlib.sha256(
                    f"{document.connector_id}\0{document.document_id}".encode()
                ).hexdigest()[:24],
                connector_id=document.connector_id,
                source_kind=document.source_kind,
                source_label=sanitize_external_text(document.title, limit=500),
                source_reference=sanitize_external_text(
                    document.source_reference or document.title, limit=256
                ),
                entity_type=document.entity_type,
                excerpt=_excerpt(document, terms),
                uri=_public_uri(document.uri),
                retrieved_at=document.retrieved_at,
                updated_at=document.updated_at,
                stale=now - document.retrieved_at > self._stale_after,
                relation=_relation(document, query),
                rank=rank,
            )
            for rank, document in enumerate(selected_documents, start=1)
        ]
        result = ContextSearchResult(
            citations=citations,
            unavailable_connector_ids=unavailable,
        )
        self._citation_results += len(citations)
        self._unavailable_provider_results += len(unavailable)
        self._max_search_latency_ms = max(
            self._max_search_latency_ms,
            round((time.monotonic() - started) * 1_000),
        )
        return result

    def diagnostics(self) -> dict[str, int]:
        return {
            "search_requests": self._search_requests,
            "citation_results": self._citation_results,
            "unavailable_provider_results": self._unavailable_provider_results,
            "circuit_opened": self._circuit_opened,
            "max_search_latency_ms": self._max_search_latency_ms,
        }
