"""Independent deterministic retrieval acceptance for connected context."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.adapters.context.broker import ContextBroker
from app.domain.context import (
    ConnectorHealth,
    ConnectorKind,
    ConnectorPhase,
    ContextDocument,
    ContextSearchQuery,
)


@dataclass(frozen=True)
class RetrievalCase:
    query: str
    expected_reference: str | None


class CorpusProvider:
    connector_id = "corpus"
    kind = ConnectorKind.odata
    display_name = "Independent corpus"
    auth_enabled = True

    def __init__(self, documents: list[ContextDocument]) -> None:
        self._documents = documents

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(
            connector_id=self.connector_id,
            kind=self.kind,
            display_name=self.display_name,
            phase=ConnectorPhase.ready,
            auth_enabled=True,
        )

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        return self._documents

    async def sync(self) -> int:
        return 0

    async def close(self) -> None:
        return None


def _corpus() -> tuple[list[ContextDocument], list[RetrievalCase]]:
    topics = [
        ("inventory", "在庫"),
        ("integration", "連携"),
        ("manual", "手作業"),
        ("delay", "遅延"),
        ("requirement", "要件"),
        ("ownership", "担当"),
    ]
    documents: list[ContextDocument] = []
    cases: list[RetrievalCase] = []
    for language in ("en", "ja"):
        for index in range(60):
            english, japanese = topics[index % len(topics)]
            topic = english if language == "en" else japanese
            reference = f"REC-{language.upper()}-{index:03d}"
            documents.append(
                ContextDocument(
                    document_id=f"document-{language}-{index}",
                    connector_id="corpus",
                    source_kind=ConnectorKind.odata,
                    record_id=f"private-{language}-{index}",
                    title=f"{topic} policy {reference}",
                    content=f"{topic} operating rule for {reference}",
                    entity_type="policy",
                    source_reference=reference,
                    allowed_principals=["alice"],
                )
            )
            cases.append(
                RetrievalCase(
                    query=f"{reference} {topic}", expected_reference=reference
                )
            )
        for index in range(40):
            english, japanese = topics[index % len(topics)]
            topic = english if language == "en" else japanese
            cases.append(
                RetrievalCase(
                    query=f"UNKNOWN-{language.upper()}-{index:03d} {topic}",
                    expected_reference=None,
                )
            )
    return documents, cases


def test_connected_context_retrieval_corpus_meets_precision_gate() -> None:
    documents, cases = _corpus()
    assert len(cases) == 200
    assert sum(case.expected_reference is None for case in cases) / len(cases) >= 0.30
    broker = ContextBroker([CorpusProvider(documents)])
    true_positive = 0
    false_positive = 0
    false_negative = 0
    for case in cases:
        result = asyncio.run(
            broker.search(
                ContextSearchQuery(text=case.query, principal_id="alice", limit=1)
            )
        )
        actual = result.citations[0].source_reference if result.citations else None
        if case.expected_reference is None:
            false_positive += actual is not None
        elif actual == case.expected_reference:
            true_positive += 1
        else:
            false_negative += 1

    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    assert precision >= 0.95
    assert recall >= 0.90
    assert false_positive == 0
