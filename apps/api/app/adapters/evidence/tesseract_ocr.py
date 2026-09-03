"""Bounded local Tesseract OCR adapter with optional Poppler PDF rendering."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

_IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}
_MAXIMUM_PDF_PAGES = 50
_MAXIMUM_RENDERED_BYTES = 128 * 1024 * 1024
_PROCESS_TIMEOUT_SECONDS = 45
_PREFLIGHT_TIMEOUT_SECONDS = 10


class TesseractOcrProvider:
    def __init__(
        self,
        executable: Path,
        *,
        pdf_renderer: Path | None = None,
        languages: str = "jpn+eng",
    ) -> None:
        self._executable = self._local_executable(executable)
        self._pdf_renderer = self._local_executable(pdf_renderer) if pdf_renderer else None
        if not self._executable.is_file() or (
            self._pdf_renderer is not None and not self._pdf_renderer.is_file()
        ):
            raise ValueError("OCR executables must be regular files")
        if not languages or any(value.isspace() for value in languages):
            raise ValueError("OCR languages must be a compact Tesseract language list")
        self._languages = languages

    def preflight(self) -> bool:
        completed = subprocess.run(
            [str(self._executable), "--list-langs"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PREFLIGHT_TIMEOUT_SECONDS,
            creationflags=self._creation_flags(),
        )
        if completed.returncode != 0:
            return False
        available = {value.strip() for value in completed.stdout.splitlines() if value.strip()}
        return all(value in available for value in self._languages.split("+"))

    @staticmethod
    def _local_executable(path: Path) -> Path:
        if not path.is_absolute() or (os.name == "nt" and str(path).startswith("\\\\")):
            raise ValueError("OCR executable paths must be absolute local paths")
        return path.resolve(strict=True)

    @staticmethod
    def _creation_flags() -> int:
        return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    def _run_tesseract(self, path: Path, remaining: int) -> str:
        completed = subprocess.run(
            [
                str(self._executable),
                str(path),
                "stdout",
                "--dpi",
                "150",
                "-l",
                self._languages,
            ],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROCESS_TIMEOUT_SECONDS,
            creationflags=self._creation_flags(),
        )
        if completed.returncode != 0:
            raise OSError("local OCR extraction failed")
        return completed.stdout[:remaining]

    def extract(self, path: Path, maximum_characters: int) -> str:
        suffix = path.suffix.casefold()
        if suffix in _IMAGE_EXTENSIONS:
            return self._run_tesseract(path, maximum_characters)
        if suffix != ".pdf" or self._pdf_renderer is None:
            raise ValueError("OCR format or PDF renderer is unavailable")
        parts: list[str] = []
        length = 0
        with tempfile.TemporaryDirectory(prefix="meeting-intelligence-ocr-") as directory:
            prefix = Path(directory) / "page"
            completed = subprocess.run(
                [
                    str(self._pdf_renderer),
                    "-f",
                    "1",
                    "-l",
                    str(_MAXIMUM_PDF_PAGES),
                    "-r",
                    "150",
                    "-scale-to",
                    "2500",
                    "-png",
                    str(path),
                    str(prefix),
                ],
                check=False,
                capture_output=True,
                timeout=_PROCESS_TIMEOUT_SECONDS,
                creationflags=self._creation_flags(),
            )
            if completed.returncode != 0:
                raise OSError("local PDF rendering for OCR failed")
            images = sorted(Path(directory).glob("page-*.png"))
            if (
                len(images) > _MAXIMUM_PDF_PAGES
                or sum(image.stat().st_size for image in images)
                > _MAXIMUM_RENDERED_BYTES
            ):
                raise ValueError("rendered OCR pages exceed local limits")
            for image in images:
                remaining = maximum_characters - length
                if remaining <= 0:
                    break
                text = self._run_tesseract(image, remaining)
                parts.append(text)
                length += len(text)
        return "\n".join(parts)[:maximum_characters]
