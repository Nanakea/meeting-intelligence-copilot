"""Deterministic local hybrid retrieval over encrypted evidence sections."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import sqlite3
import time
import unicodedata
from datetime import UTC, datetime

from app.domain.evidence import (
    ArtifactSection,
    EmbeddingProfile,
    EvidenceBundle,
    EvidenceCitation,
    RetrievalRequest,
    RetrievalScore,
)
from app.domain.ports import EmbeddingProvider, EvidenceRepository

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]{2,}|[\u3040-\u30ff\u3400-\u9fff]{2,}")
_RRF_K = 60
_LEXICAL_CANDIDATE_LIMIT = 200


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _search_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(_normalize(value)):
        token = match.group(0)
        if any("\u3040" <= character <= "\u9fff" for character in token):
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
        else:
            tokens.append(token)
    return list(dict.fromkeys(token for token in tokens if token.strip()))


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


class LocalHybridRetrievalEngine:
    def __init__(
        self,
        repository: EvidenceRepository,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_store: object | None = None,
        source_labels: dict[str, str] | None = None,
        source_authority: dict[str, float] | None = None,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._embedding_store = embedding_store
        self._source_labels = source_labels or {}
        self._source_authority = source_authority or {}

    async def retrieve(self, request: RetrievalRequest) -> EvidenceBundle:
        started = time.perf_counter()
        artifacts, sections = await asyncio.gather(
            asyncio.to_thread(self._repository.artifacts, request.connector_ids or None),
            asyncio.to_thread(self._repository.sections, request.connector_ids or None),
        )
        artifact_by_id = {artifact.artifact_id: artifact for artifact in artifacts}
        sections = [
            section
            for section in sections
            if request.principal_id in section.allowed_principals
            and (
                not request.document_kinds
                or artifact_by_id[section.artifact_id].document_kind in request.document_kinds
            )
        ]
        lexical = self._lexical_ranking(request, sections)
        semantic: list[tuple[str, float]] = []
        lexical_only = True
        if self._embedding_provider is not None and lexical:
            try:
                semantic = await self._semantic_ranking(request, sections, lexical)
                lexical_only = False
            except (OSError, RuntimeError, TimeoutError, ValueError):
                semantic = []

        lexical_rank = {section_id: index + 1 for index, (section_id, _) in enumerate(lexical)}
        semantic_rank = {
            section_id: index + 1 for index, (section_id, _) in enumerate(semantic)
        }
        section_by_id = {section.section_id: section for section in sections}
        candidates = set(lexical_rank) | set(semantic_rank)
        query_normalized = _normalize(request.text)
        entity_hints = [_normalize(value) for value in request.entity_hints]
        business_keys = [_normalize(value) for value in request.business_keys]
        ranked: list[tuple[float, str, RetrievalScore]] = []
        for section_id in candidates:
            section = section_by_id[section_id]
            artifact = artifact_by_id[section.artifact_id]
            haystack = _normalize(f"{section.title}\n{section.content}")
            entity_matches = sum(value in haystack for value in entity_hints if value)
            key_matches = sum(value in haystack for value in business_keys if value)
            title_match = bool(query_normalized and query_normalized in _normalize(artifact.title))
            freshness = self._freshness(artifact.updated_at)
            authority = self._source_authority.get(artifact.connector_id, 50) / 100
            score = (
                (1 / (_RRF_K + lexical_rank[section_id]) if section_id in lexical_rank else 0)
                + (1 / (_RRF_K + semantic_rank[section_id]) if section_id in semantic_rank else 0)
                + entity_matches * 0.01
                + key_matches * 0.03
                + (0.01 if title_match else 0)
                + freshness * 0.002
                + authority * 0.005
            )
            ranked.append(
                (
                    score,
                    section_id,
                    RetrievalScore(
                        lexical_rank=lexical_rank.get(section_id),
                        semantic_rank=semantic_rank.get(section_id),
                        entity_matches=entity_matches,
                        business_key_matches=key_matches,
                        title_match=title_match,
                        freshness_boost=freshness,
                        source_authority_boost=authority,
                        reciprocal_rank_score=score,
                    ),
                )
            )
        ranked.sort(key=lambda value: (-value[0], value[1]))
        citations: list[EvidenceCitation] = []
        for rank, (_, section_id, score) in enumerate(ranked[: request.limit], start=1):
            section = section_by_id[section_id]
            artifact = artifact_by_id[section.artifact_id]
            citations.append(
                EvidenceCitation(
                    citation_id=hashlib.sha256(
                        f"{section.section_id}\0{request.text}".encode()
                    ).hexdigest(),
                    connector_id=artifact.connector_id,
                    source_kind=artifact.source_kind,
                    source_label=self._source_labels.get(
                        artifact.connector_id, artifact.source_kind.value
                    ),
                    source_reference=artifact.source_reference,
                    document_kind=artifact.document_kind,
                    excerpt=" ".join(section.content.split())[:600],
                    locator=section.locator,
                    updated_at=artifact.updated_at,
                    relation="reference",
                    rank=rank,
                    score=score,
                )
            )
        return EvidenceBundle(
            citations=citations,
            lexical_only=lexical_only,
            elapsed_ms=int((time.perf_counter() - started) * 1_000),
        )

    @staticmethod
    def _freshness(updated_at: datetime | None) -> float:
        if updated_at is None:
            return 0.0
        value = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=UTC)
        days = max(0.0, (datetime.now(UTC) - value).total_seconds() / 86_400)
        return max(0.0, 1.0 - min(days, 365.0) / 365.0)

    @staticmethod
    def _lexical_ranking(
        request: RetrievalRequest, sections: list[ArtifactSection]
    ) -> list[tuple[str, float]]:
        query_tokens = _search_tokens(
            " ".join([request.text, *request.entity_hints, *request.business_keys])
        )
        if not query_tokens or not sections:
            return []
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE evidence_fts USING fts5(section_id UNINDEXED, body, "
                "tokenize='unicode61 remove_diacritics 2')"
            )
            connection.executemany(
                "INSERT INTO evidence_fts(section_id, body) VALUES (?, ?)",
                [
                    (
                        section.section_id,
                        " ".join(_search_tokens(f"{section.title}\n{section.content}")),
                    )
                    for section in sections
                ],
            )
            match = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in query_tokens)
            rows = connection.execute(
                "SELECT section_id, bm25(evidence_fts) FROM evidence_fts "
                "WHERE evidence_fts MATCH ? ORDER BY bm25(evidence_fts), section_id LIMIT ?",
                (match, _LEXICAL_CANDIDATE_LIMIT),
            ).fetchall()
            return [(str(section_id), float(score)) for section_id, score in rows]
        finally:
            connection.close()

    async def _semantic_ranking(
        self,
        request: RetrievalRequest,
        sections: list[ArtifactSection],
        lexical: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        assert self._embedding_provider is not None
        candidates = {section_id for section_id, _ in lexical[:_LEXICAL_CANDIDATE_LIMIT]}
        selected = [section for section in sections if section.section_id in candidates]
        vectors: dict[str, list[float]] = {}
        store = self._embedding_store
        profile: EmbeddingProfile = self._embedding_provider.profile
        if store is not None and hasattr(store, "embeddings"):
            vectors = await asyncio.to_thread(
                store.embeddings,
                profile.model_sha256,
                [section.section_id for section in selected],
            )
        missing = [section for section in selected if section.section_id not in vectors]
        if missing:
            values = await asyncio.wait_for(
                self._embedding_provider.embed_documents(
                    [section.content for section in missing]
                ),
                timeout=5.0,
            )
            vectors.update(
                {
                    section.section_id: vector
                    for section, vector in zip(missing, values, strict=True)
                }
            )
            if store is not None and hasattr(store, "save_embeddings"):
                await asyncio.to_thread(
                    store.save_embeddings, profile.model_sha256, vectors
                )
        query_vector = await asyncio.wait_for(
            self._embedding_provider.embed_query(request.text), timeout=5.0
        )
        ranked = [
            (section_id, _cosine(query_vector, vector))
            for section_id, vector in vectors.items()
        ]
        ranked.sort(key=lambda value: (-value[1], value[0]))
        return ranked
