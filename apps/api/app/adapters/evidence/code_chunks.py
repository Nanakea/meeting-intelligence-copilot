"""Bounded secret-aware repository chunking with optional Tree-sitter symbols."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.domain.evidence import ArtifactLocator, ArtifactSection

_EXCLUDED_PARTS = {
    ".git",
    ".next",
    "bin",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "obj",
    "target",
    "vendor",
}
_BINARY_SUFFIXES = {
    ".7z",
    ".bin",
    ".dll",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".so",
    ".tar",
    ".webp",
    ".zip",
}
_SECRET = re.compile(
    r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:ghp_|github_pat_|glpat-|xox[baprs]-)[A-Za-z0-9_-]{12,}|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"(?i:\b(?:password|client_secret|api_key|access_token)\s*[:=]\s*['\"]?[^\s'\"]{12,}))"
)
_DECLARATION = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:class|interface|trait|enum|def|fn|function|"
    r"public\s+class|private\s+class|protected\s+class)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_LANGUAGE_BY_SUFFIX = {
    ".cs": "c_sharp",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".py": "python",
    ".rs": "rust",
    ".sql": "sql",
    ".ts": "typescript",
    ".tsx": "tsx",
}


@dataclass(frozen=True)
class CodeChunk:
    title: str
    content: str
    start_line: int
    end_line: int


def repository_path_is_indexable(path: str, size_bytes: int) -> bool:
    candidate = PurePosixPath(path.replace("\\", "/"))
    return (
        0 < size_bytes <= 1_000_000
        and not any(part.casefold() in _EXCLUDED_PARTS for part in candidate.parts)
        and candidate.suffix.casefold() not in _BINARY_SUFFIXES
    )


def contains_secret_material(value: str) -> bool:
    return _SECRET.search(value[:1_000_000]) is not None


def _tree_sitter_chunks(path: str, text: str) -> list[CodeChunk]:
    language = _LANGUAGE_BY_SUFFIX.get(PurePosixPath(path).suffix.casefold())
    if language is None:
        return []
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(language)
    except (ImportError, LookupError, RuntimeError):
        return []
    source = text.encode("utf-8")
    tree = parser.parse(source)
    interesting = {
        "class_declaration",
        "class_definition",
        "enum_declaration",
        "function_declaration",
        "function_definition",
        "interface_declaration",
        "method_declaration",
        "method_definition",
        "trait_item",
    }
    chunks: list[CodeChunk] = []
    stack = [tree.root_node]
    while stack and len(chunks) < 500:
        node = stack.pop()
        stack.extend(reversed(node.children))
        if node.type not in interesting or node.end_byte - node.start_byte > 20_000:
            continue
        content = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        if not content.strip() or contains_secret_material(content):
            continue
        match = _DECLARATION.search(content)
        name = match.group(1) if match else node.type.replace("_", " ")
        chunks.append(
            CodeChunk(
                title=f"{path} · {name}",
                content=content,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
            )
        )
    return chunks


def chunk_repository_text(path: str, text: str) -> list[CodeChunk]:
    if not repository_path_is_indexable(path, len(text.encode("utf-8"))):
        return []
    if contains_secret_material(text):
        return []
    parsed = _tree_sitter_chunks(path, text)
    if parsed:
        return parsed
    lines = text.splitlines()
    chunks: list[CodeChunk] = []
    for start in range(0, len(lines), 100):
        selected = lines[start : start + 120]
        content = "\n".join(selected).strip()
        if not content or contains_secret_material(content):
            continue
        match = _DECLARATION.search(content)
        label = match.group(1) if match else f"lines {start + 1}-{start + len(selected)}"
        chunks.append(
            CodeChunk(
                title=f"{path} · {label}",
                content=content,
                start_line=start + 1,
                end_line=start + len(selected),
            )
        )
    return chunks[:500]


def artifact_sections_from_code(
    *,
    artifact_id: str,
    revision_id: str,
    path: str,
    text: str,
    principal_id: str,
) -> list[ArtifactSection]:
    values: list[ArtifactSection] = []
    for ordinal, chunk in enumerate(chunk_repository_text(path, text)):
        content_hash = hashlib.sha256(chunk.content.encode()).hexdigest()
        values.append(
            ArtifactSection(
                section_id=hashlib.sha256(
                    f"{revision_id}\0{ordinal}\0{content_hash}".encode()
                ).hexdigest(),
                artifact_id=artifact_id,
                revision_id=revision_id,
                ordinal=ordinal,
                title=chunk.title,
                content=chunk.content,
                locator=ArtifactLocator(
                    heading=chunk.title,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                ),
                content_hash=content_hash,
                allowed_principals=[principal_id],
            )
        )
    return values
