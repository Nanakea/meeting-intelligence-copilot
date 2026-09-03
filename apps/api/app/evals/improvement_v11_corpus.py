"""Immutable synthetic safety corpus for v11 declarative improvement profiles.

The corpus is intentionally independent from the proposal builder. It contains no
meeting, connector, or company data and is used only to prove that a profile keeps
language/source cohorts bounded and rejects control-shaped values.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.domain.improvements import ImprovementProposalKind


@dataclass(frozen=True)
class ImprovementCorpusCase:
    case_id: str
    kind: ImprovementProposalKind
    language: str
    source: str
    process: str
    document_kind: str
    safe: bool

    @property
    def cohort_keys(self) -> tuple[str, ...]:
        return (
            f"language:{self.language}",
            f"source:{self.source}",
            f"process:{self.process}",
            f"document:{self.document_kind}",
        )


_PROCESSES = (
    "order_to_cash",
    "procure_to_pay",
    "inventory",
    "project_to_cash",
    "financial_close",
)
_DOCUMENT_KINDS = ("requirement", "design", "mapping", "test", "operations")
_SOURCES = ("local_files", "netsuite", "microsoft_graph")


def _build() -> tuple[ImprovementCorpusCase, ...]:
    values: list[ImprovementCorpusCase] = []
    for kind in ImprovementProposalKind:
        for index in range(36):
            language = "ja" if index % 2 else "en"
            source = _SOURCES[index % len(_SOURCES)]
            process = _PROCESSES[index % len(_PROCESSES)]
            document_kind = _DOCUMENT_KINDS[index % len(_DOCUMENT_KINDS)]
            values.append(
                ImprovementCorpusCase(
                    case_id=f"{kind.value}-{index:02d}",
                    kind=kind,
                    language=language,
                    source=source,
                    process=process,
                    document_kind=document_kind,
                    safe=index % 12 != 0,
                )
            )
    return tuple(values)


CASES = _build()
CORPUS_HASH = hashlib.sha256(
    json.dumps(
        [value.__dict__ for value in CASES],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


def cases_for(kind: ImprovementProposalKind) -> tuple[ImprovementCorpusCase, ...]:
    return tuple(value for value in CASES if value.kind is kind)
