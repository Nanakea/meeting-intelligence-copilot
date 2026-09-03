"""Deterministic conversion of connector documents into revisioned artifacts."""

from __future__ import annotations

import hashlib
import re

from app.domain.context import ContextDocument
from app.domain.evidence import (
    ArtifactLocator,
    ArtifactSection,
    DocumentArtifact,
    DocumentKind,
    DocumentRevision,
)

_HEADING_RE = re.compile(r"^(?:#{1,6}\s+|(?:\d+(?:\.\d+)*[.)]?\s+))(.{1,200})$")
_CHUNK_CHARACTERS = 800
_CHUNK_OVERLAP = 120

_KIND_TERMS: list[tuple[DocumentKind, tuple[str, ...]]] = [
    (DocumentKind.adr, ("adr", "decision record", "意思決定記録")),
    (DocumentKind.data_mapping, ("data mapping", "field mapping", "項目マッピング")),
    (DocumentKind.interface, ("interface", "integration", "api", "インターフェース")),
    (DocumentKind.nfr_security, ("nfr", "non-functional", "security", "非機能", "セキュリティ")),
    (DocumentKind.test, ("test plan", "test strategy", "test case", "テスト")),
    (DocumentKind.cutover, ("cutover", "rollback", "移行計画", "切り戻し")),
    (DocumentKind.operations, ("runbook", "operations", "support model", "運用", "保守")),
    (
        DocumentKind.architecture,
        ("hld", "lld", "architecture", "solution design", "アーキテクチャ"),
    ),
    (DocumentKind.requirement, ("requirement", "scope", "要件", "要求仕様")),
]


def classify_document(title: str, content: str) -> DocumentKind:
    haystack = f"{title}\n{content[:4_000]}".casefold()
    for kind, terms in _KIND_TERMS:
        if any(term.casefold() in haystack for term in terms):
            return kind
    return DocumentKind.general


def _logical_segments(content: str) -> list[tuple[str | None, str]]:
    segments: list[tuple[str | None, str]] = []
    heading: str | None = None
    buffer: list[str] = []
    for line in content.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match:
            if any(value.strip() for value in buffer):
                segments.append((heading, "\n".join(buffer).strip()))
            heading = match.group(1).strip()
            buffer = []
        else:
            buffer.append(line)
    if any(value.strip() for value in buffer):
        segments.append((heading, "\n".join(buffer).strip()))
    return segments or [(None, content.strip())]


def _chunks(content: str) -> list[str]:
    if len(content) <= _CHUNK_CHARACTERS:
        return [content]
    chunks: list[str] = []
    start = 0
    while start < len(content):
        end = min(len(content), start + _CHUNK_CHARACTERS)
        if end < len(content):
            boundary = max(
                content.rfind("\n", start + _CHUNK_CHARACTERS // 2, end),
                content.rfind("。", start + _CHUNK_CHARACTERS // 2, end),
                content.rfind(". ", start + _CHUNK_CHARACTERS // 2, end),
            )
            if boundary > start:
                end = boundary + 1
        value = content[start:end].strip()
        if value:
            chunks.append(value)
        if end >= len(content):
            break
        start = max(start + 1, end - _CHUNK_OVERLAP)
    return chunks


def build_artifact(
    document: ContextDocument,
) -> tuple[DocumentArtifact, DocumentRevision, list[ArtifactSection]]:
    located = [
        (ArtifactLocator(heading=heading), segment)
        for heading, segment in _logical_segments(document.content)
    ]
    return build_artifact_from_segments(document, located)


def build_artifact_from_segments(
    document: ContextDocument,
    located_segments: list[tuple[ArtifactLocator, str]],
) -> tuple[DocumentArtifact, DocumentRevision, list[ArtifactSection]]:
    source_reference = document.source_reference or document.title
    artifact_id = hashlib.sha256(
        f"{document.connector_id}\0{document.document_id}".encode()
    ).hexdigest()
    normalized_content = document.content.replace("\r\n", "\n").replace("\r", "\n")
    content_hash = hashlib.sha256(normalized_content.encode()).hexdigest()
    revision_id = hashlib.sha256(f"{artifact_id}\0{content_hash}".encode()).hexdigest()
    sections: list[ArtifactSection] = []
    ordinal = 0
    for locator, segment in located_segments:
        for chunk in _chunks(segment):
            chunk_hash = hashlib.sha256(chunk.encode()).hexdigest()
            section_id = hashlib.sha256(
                f"{revision_id}\0{ordinal}\0{locator.model_dump_json()}\0{chunk_hash}".encode()
            ).hexdigest()
            sections.append(
                ArtifactSection(
                    section_id=section_id,
                    artifact_id=artifact_id,
                    revision_id=revision_id,
                    ordinal=ordinal,
                    title=document.title,
                    content=chunk,
                    locator=locator,
                    content_hash=hashlib.sha256(chunk.encode()).hexdigest(),
                    allowed_principals=document.allowed_principals,
                )
            )
            ordinal += 1
    revision = DocumentRevision(
        revision_id=revision_id,
        artifact_id=artifact_id,
        content_hash=content_hash,
        section_count=len(sections),
    )
    artifact = DocumentArtifact(
        artifact_id=artifact_id,
        connector_id=document.connector_id,
        source_kind=document.source_kind,
        title=document.title,
        source_reference=source_reference,
        document_kind=classify_document(document.title, document.content),
        current_revision_id=revision_id,
        updated_at=document.updated_at,
    )
    return artifact, revision, sections
