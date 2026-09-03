"""Bounded in-memory parsing for live-only remote knowledge documents."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader

SUPPORTED_REMOTE_EXTENSIONS = {".csv", ".docx", ".json", ".md", ".pdf", ".txt", ".xlsx"}
MAXIMUM_REMOTE_FILE_BYTES = 10 * 1024 * 1024
MAXIMUM_REMOTE_EXTRACTED_CHARACTERS = 100_000
_MAXIMUM_ARCHIVE_MEMBERS = 1_000
_MAXIMUM_ARCHIVE_EXPANDED_BYTES = 32 * 1024 * 1024
_MAXIMUM_PDF_PAGES = 200
_MAXIMUM_WORKSHEETS = 20


def _archive(payload: bytes) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(io.BytesIO(payload))
    members = archive.infolist()
    if (
        len(members) > _MAXIMUM_ARCHIVE_MEMBERS
        or sum(member.file_size for member in members) > _MAXIMUM_ARCHIVE_EXPANDED_BYTES
        or any(member.flag_bits & 0x1 for member in members)
    ):
        archive.close()
        raise ValueError("remote Office document exceeds safe extraction limits")
    return archive


def _xml_text(payload: bytes, suffix: str) -> list[str]:
    root = ElementTree.fromstring(payload)
    return [
        element.text
        for element in root.iter()
        if element.tag.endswith(suffix) and element.text
    ]


def _docx(payload: bytes) -> str:
    with _archive(payload) as archive:
        return "\n".join(_xml_text(archive.read("word/document.xml"), "}t"))


def _xlsx(payload: bytes) -> str:
    with _archive(payload) as archive:
        names = set(archive.namelist())
        shared = (
            _xml_text(archive.read("xl/sharedStrings.xml"), "}t")
            if "xl/sharedStrings.xml" in names
            else []
        )
        lines: list[str] = []
        for sheet in sorted(
            name
            for name in names
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )[:_MAXIMUM_WORKSHEETS]:
            root = ElementTree.fromstring(archive.read(sheet))
            values: list[str] = []
            for cell in (element for element in root.iter() if element.tag.endswith("}c")):
                value = next(
                    (child.text for child in cell if child.tag.endswith("}v")), None
                )
                if value is None:
                    continue
                if cell.attrib.get("t") == "s" and value.isdigit():
                    index = int(value)
                    value = shared[index] if index < len(shared) else ""
                values.append(value)
            lines.append("\t".join(values))
        return "\n".join(lines)


def _pdf(payload: bytes) -> str:
    reader = PdfReader(io.BytesIO(payload), strict=False)
    if reader.is_encrypted:
        raise ValueError("encrypted remote PDFs are not processed")
    return "\n".join(
        (page.extract_text() or "") for page in reader.pages[:_MAXIMUM_PDF_PAGES]
    )


def parse_remote_document(filename: str, payload: bytes) -> str:
    if len(payload) > MAXIMUM_REMOTE_FILE_BYTES:
        raise ValueError("remote document exceeds the file size limit")
    suffix = Path(filename).suffix.casefold()
    if suffix not in SUPPORTED_REMOTE_EXTENSIONS:
        raise ValueError("unsupported remote document format")
    if suffix == ".docx":
        text = _docx(payload)
    elif suffix == ".xlsx":
        text = _xlsx(payload)
    elif suffix == ".pdf":
        text = _pdf(payload)
    else:
        text = payload.decode("utf-8-sig", errors="replace")
        if suffix == ".json":
            decoded = json.loads(text)
            text = json.dumps(decoded, ensure_ascii=False, indent=2)
        elif suffix == ".csv":
            text = "\n".join("\t".join(row) for row in csv.reader(io.StringIO(text)))
    return text[:MAXIMUM_REMOTE_EXTRACTED_CHARACTERS]
