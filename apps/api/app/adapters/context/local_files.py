"""Read-only local knowledge provider backed by the encrypted context index."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader
from pypdf import filters as pypdf_filters
from pypdf.errors import PyPdfError

from app.adapters.context.encrypted_index import EncryptedContextIndex
from app.adapters.evidence.artifacts import build_artifact, build_artifact_from_segments
from app.adapters.evidence.repository import EncryptedEvidenceRepository
from app.domain.context import (
    ConnectorHealth,
    ConnectorKind,
    ConnectorPhase,
    ContextDocument,
    ContextSearchQuery,
)
from app.domain.evidence import ArtifactLocator
from app.domain.ports import OcrProvider

_SUPPORTED_EXTENSIONS = {".csv", ".docx", ".json", ".md", ".pdf", ".txt", ".xlsx"}
_OCR_IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}
_TEXT_EXTENSIONS = {".csv", ".json", ".md", ".txt"}
_MAXIMUM_EXTRACTED_CHARACTERS = 100_000
_MAXIMUM_ARCHIVE_MEMBERS = 1_000
_MAXIMUM_ARCHIVE_EXPANDED_BYTES = 32 * 1024 * 1024
_MAXIMUM_PDF_PAGES = 200
_MAXIMUM_WORKSHEETS = 20
_MAXIMUM_TOTAL_EXTRACTED_CHARACTERS = 32 * 1024 * 1024

# Keep PDF stream expansion within the same ceiling as Office archives.
pypdf_filters.ZLIB_MAX_OUTPUT_LENGTH = min(
    pypdf_filters.ZLIB_MAX_OUTPUT_LENGTH,
    _MAXIMUM_ARCHIVE_EXPANDED_BYTES,
)


def _bounded_archive(path: Path) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(path)
    members = archive.infolist()
    if (
        len(members) > _MAXIMUM_ARCHIVE_MEMBERS
        or sum(member.file_size for member in members) > _MAXIMUM_ARCHIVE_EXPANDED_BYTES
        or any(member.flag_bits & 0x1 for member in members)
    ):
        archive.close()
        raise ValueError("Office document exceeds safe extraction limits")
    return archive


def _xml_text(payload: bytes, tag_suffix: str) -> list[str]:
    root = ElementTree.fromstring(payload)
    return [
        element.text
        for element in root.iter()
        if element.tag.endswith(tag_suffix) and element.text
    ]


def _read_docx(path: Path) -> str:
    with _bounded_archive(path) as archive:
        payload = archive.read("word/document.xml")
    return "\n".join(_xml_text(payload, "}t"))[:_MAXIMUM_EXTRACTED_CHARACTERS]


def _read_xlsx(path: Path) -> str:
    with _bounded_archive(path) as archive:
        names = set(archive.namelist())
        shared = (
            _xml_text(archive.read("xl/sharedStrings.xml"), "}t")
            if "xl/sharedStrings.xml" in names
            else []
        )
        lines: list[str] = []
        sheets = sorted(
            name
            for name in names
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )[:_MAXIMUM_WORKSHEETS]
        extracted_length = 0
        for sheet in sheets:
            root = ElementTree.fromstring(archive.read(sheet))
            row_values: list[str] = []
            for cell in (element for element in root.iter() if element.tag.endswith("}c")):
                value = next(
                    (child.text for child in cell if child.tag.endswith("}v")), None
                )
                if value is None:
                    continue
                if cell.attrib.get("t") == "s" and value.isdigit():
                    index = int(value)
                    value = shared[index] if index < len(shared) else ""
                remaining = _MAXIMUM_EXTRACTED_CHARACTERS - extracted_length
                row_values.append(value[:remaining])
                extracted_length += len(row_values[-1])
                if extracted_length >= _MAXIMUM_EXTRACTED_CHARACTERS:
                    break
            lines.append("\t".join(row_values))
            if extracted_length >= _MAXIMUM_EXTRACTED_CHARACTERS:
                break
    return "\n".join(lines)[:_MAXIMUM_EXTRACTED_CHARACTERS]


def _read_pdf(path: Path) -> str:
    reader = PdfReader(path, strict=False)
    if reader.is_encrypted:
        raise ValueError("encrypted PDFs are not indexed")
    parts: list[str] = []
    length = 0
    for page in reader.pages[:_MAXIMUM_PDF_PAGES]:
        text = page.extract_text() or ""
        remaining = _MAXIMUM_EXTRACTED_CHARACTERS - length
        if remaining <= 0:
            break
        parts.append(text[:remaining])
        length += len(parts[-1])
    return "\n".join(parts)


def _read_document(path: Path, ocr_provider: OcrProvider | None = None) -> str:
    suffix = path.suffix.casefold()
    if suffix in _TEXT_EXTENSIONS:
        return path.read_text(encoding="utf-8-sig", errors="replace")[
            :_MAXIMUM_EXTRACTED_CHARACTERS
        ]
    if suffix == ".docx":
        return _read_docx(path)
    if suffix == ".xlsx":
        return _read_xlsx(path)
    if suffix == ".pdf":
        content = _read_pdf(path)
        if content.strip() or ocr_provider is None:
            return content
        return ocr_provider.extract(path, _MAXIMUM_EXTRACTED_CHARACTERS)
    if suffix in _OCR_IMAGE_EXTENSIONS and ocr_provider is not None:
        return ocr_provider.extract(path, _MAXIMUM_EXTRACTED_CHARACTERS)
    raise ValueError("unsupported local knowledge format")


def _located_pdf(path: Path) -> list[tuple[ArtifactLocator, str]]:
    reader = PdfReader(path, strict=False)
    if reader.is_encrypted:
        raise ValueError("encrypted PDFs are not indexed")
    segments: list[tuple[ArtifactLocator, str]] = []
    length = 0
    for page_number, page in enumerate(reader.pages[:_MAXIMUM_PDF_PAGES], start=1):
        remaining = _MAXIMUM_EXTRACTED_CHARACTERS - length
        if remaining <= 0:
            break
        content = (page.extract_text() or "")[:remaining].strip()
        if content:
            segments.append((ArtifactLocator(page=page_number), content))
            length += len(content)
    return segments


def _attribute(element: ElementTree.Element, suffix: str) -> str | None:
    return next(
        (value for key, value in element.attrib.items() if key.endswith(suffix)), None
    )


def _located_docx(path: Path) -> list[tuple[ArtifactLocator, str]]:
    with _bounded_archive(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    segments: list[tuple[ArtifactLocator, str]] = []
    heading: str | None = None
    buffer: list[str] = []
    paragraph = 0
    start_paragraph = 1
    for element in root.iter():
        if not element.tag.endswith("}p"):
            continue
        paragraph += 1
        text = "".join(
            child.text or "" for child in element.iter() if child.tag.endswith("}t")
        ).strip()
        if not text:
            continue
        style = next(
            (
                _attribute(child, "}val") or ""
                for child in element.iter()
                if child.tag.endswith("}pStyle")
            ),
            "",
        )
        if "heading" in style.casefold() or "見出し" in style:
            if buffer:
                segments.append(
                    (
                        ArtifactLocator(heading=heading, paragraph=start_paragraph),
                        "\n".join(buffer),
                    )
                )
            heading = text
            buffer = []
            start_paragraph = paragraph + 1
        else:
            buffer.append(text)
    if buffer:
        segments.append(
            (
                ArtifactLocator(heading=heading, paragraph=start_paragraph),
                "\n".join(buffer),
            )
        )
    return segments


def _located_xlsx(path: Path) -> list[tuple[ArtifactLocator, str]]:
    with _bounded_archive(path) as archive:
        names = set(archive.namelist())
        shared = (
            _xml_text(archive.read("xl/sharedStrings.xml"), "}t")
            if "xl/sharedStrings.xml" in names
            else []
        )
        sheets = sorted(
            name
            for name in names
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )[:_MAXIMUM_WORKSHEETS]
        segments: list[tuple[ArtifactLocator, str]] = []
        total = 0
        for sheet in sheets:
            root = ElementTree.fromstring(archive.read(sheet))
            values: list[str] = []
            references: list[str] = []
            for cell in (element for element in root.iter() if element.tag.endswith("}c")):
                value = next(
                    (child.text for child in cell if child.tag.endswith("}v")), None
                )
                if value is None:
                    continue
                if cell.attrib.get("t") == "s" and value.isdigit():
                    index = int(value)
                    value = shared[index] if index < len(shared) else ""
                remaining = _MAXIMUM_EXTRACTED_CHARACTERS - total
                if remaining <= 0:
                    break
                values.append(value[:remaining])
                references.append(cell.attrib.get("r", ""))
                total += len(values[-1])
            if values:
                cell_range = ""
                if references:
                    cell_range = (
                        references[0]
                        if references[0] == references[-1]
                        else f"{references[0]}:{references[-1]}"
                    )
                segments.append(
                    (
                        ArtifactLocator(
                            sheet=Path(sheet).stem,
                            cell_range=cell_range or None,
                        ),
                        "\t".join(values),
                    )
                )
        return segments


def _located_document(path: Path, content: str) -> list[tuple[ArtifactLocator, str]]:
    suffix = path.suffix.casefold()
    if suffix == ".pdf":
        return _located_pdf(path)
    if suffix == ".docx":
        return _located_docx(path)
    if suffix == ".xlsx":
        return _located_xlsx(path)
    lines = content.splitlines()
    return [
        (ArtifactLocator(paragraph=index), "\n".join(lines[index - 1 : index + 49]))
        for index in range(1, len(lines) + 1, 50)
        if any(value.strip() for value in lines[index - 1 : index + 49])
    ] or [(ArtifactLocator(), content)]


class LocalFilesContextProvider:
    def __init__(
        self,
        *,
        connector_id: str,
        display_name: str,
        root: Path,
        principal_id: str,
        index: EncryptedContextIndex,
        evidence_repository: EncryptedEvidenceRepository | None = None,
        maximum_file_bytes: int = 10 * 1024 * 1024,
        maximum_documents: int = 5_000,
        maximum_total_characters: int = _MAXIMUM_TOTAL_EXTRACTED_CHARACTERS,
        ocr_provider: OcrProvider | None = None,
    ) -> None:
        self._connector_id = connector_id
        self._display_name = display_name
        self._root = root.resolve(strict=False)
        self._principal_id = principal_id
        self._index = index
        self._evidence_repository = evidence_repository
        self._maximum_file_bytes = maximum_file_bytes
        self._maximum_documents = maximum_documents
        self._maximum_total_characters = maximum_total_characters
        self._ocr_provider = ocr_provider
        self._last_success_at: datetime | None = None

    @property
    def connector_id(self) -> str:
        return self._connector_id

    @property
    def kind(self) -> ConnectorKind:
        return ConnectorKind.local_files

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def auth_enabled(self) -> bool:
        return True

    async def health(self) -> ConnectorHealth:
        phase = ConnectorPhase.ready if self._root.is_dir() else ConnectorPhase.unavailable
        return ConnectorHealth(
            connector_id=self.connector_id,
            kind=self.kind,
            display_name=self.display_name,
            phase=phase,
            auth_enabled=True,
            last_success_at=self._last_success_at,
            detail_code=None if phase is ConnectorPhase.ready else "root_unavailable",
            scope_summary=self._root.name or str(self._root),
            indexed_documents=self._index.document_count(self.connector_id),
        )

    def _read_documents_with_paths(self) -> list[tuple[ContextDocument, Path]]:
        documents: list[tuple[ContextDocument, Path]] = []
        extracted_characters = 0
        for directory, directory_names, file_names in os.walk(
            self._root, topdown=True, followlinks=False
        ):
            directory_names[:] = sorted(
                name
                for name in directory_names
                if not (Path(directory, name).is_symlink())
                and not (
                    hasattr(Path(directory, name), "is_junction")
                    and Path(directory, name).is_junction()
                )
            )
            for name in sorted(file_names):
                if len(documents) >= self._maximum_documents:
                    return documents
                path = Path(directory, name)
                supported = _SUPPORTED_EXTENSIONS | (
                    _OCR_IMAGE_EXTENSIONS if self._ocr_provider is not None else set()
                )
                if path.is_symlink() or path.suffix.casefold() not in supported:
                    continue
                try:
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(self._root):
                        continue
                    stat = resolved.stat()
                    if stat.st_size > self._maximum_file_bytes:
                        continue
                    content = _read_document(resolved, self._ocr_provider)
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    ElementTree.ParseError,
                    PyPdfError,
                    subprocess.SubprocessError,
                    zipfile.BadZipFile,
                ):
                    continue
                remaining = self._maximum_total_characters - extracted_characters
                if remaining <= 0:
                    return documents
                content = content[:remaining]
                if not content.strip():
                    continue
                relative = resolved.relative_to(self._root).as_posix()
                document_id = hashlib.sha256(relative.encode()).hexdigest()
                documents.append(
                    (
                        ContextDocument(
                        document_id=document_id,
                        connector_id=self.connector_id,
                        source_kind=self.kind,
                        record_id=document_id,
                        title=resolved.name,
                        content=content,
                        entity_type="knowledge_document",
                        source_reference=resolved.name,
                        allowed_principals=[self._principal_id],
                        updated_at=datetime.fromtimestamp(stat.st_mtime, UTC),
                        ),
                        resolved,
                    )
                )
                extracted_characters += len(content)
        return documents

    def _read_documents(self) -> list[ContextDocument]:
        return [document for document, _ in self._read_documents_with_paths()]

    def resolve_artifact_source(self, artifact_id: str) -> Path | None:
        """Resolve an encrypted evidence ID without exposing a local path to the UI."""

        for document, path in self._read_documents_with_paths():
            candidate = hashlib.sha256(
                f"{document.connector_id}\0{document.document_id}".encode()
            ).hexdigest()
            if secrets.compare_digest(candidate, artifact_id):
                return path
        return None

    async def sync(self) -> int:
        documents_with_paths = await asyncio.to_thread(self._read_documents_with_paths)
        documents = [document for document, _ in documents_with_paths]
        count = await asyncio.to_thread(self._index.replace, self.connector_id, documents)
        if self._evidence_repository is not None:
            retained_artifact_ids: set[str] = set()
            for document, path in documents_with_paths:
                located = _located_document(path, document.content)
                artifact, revision, sections = (
                    build_artifact_from_segments(document, located)
                    if located
                    else build_artifact(document)
                )
                await asyncio.to_thread(
                    self._evidence_repository.replace_artifact,
                    artifact,
                    revision,
                    sections,
                )
                retained_artifact_ids.add(artifact.artifact_id)
            await asyncio.to_thread(
                self._evidence_repository.reconcile_connector_artifacts,
                self.connector_id,
                retained_artifact_ids,
            )
        self._last_success_at = datetime.now(UTC)
        return count

    async def search(self, query: ContextSearchQuery) -> list[ContextDocument]:
        if query.principal_id != self._principal_id:
            return []
        documents = await asyncio.to_thread(self._index.documents, self.connector_id)
        terms = [term.casefold() for term in query.text.split() if len(term) > 1]
        if not terms:
            return []
        return [
            document
            for document in documents
            if any(
                term in f"{document.title}\n{document.content}".casefold()
                for term in terms
            )
        ][:100]

    async def close(self) -> None:
        return None
