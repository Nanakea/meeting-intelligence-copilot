"""Test-session isolation for the production live-recovery cache."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_CACHE_ENV = "MEETING_INTELLIGENCE_CACHE_DIR"
_TEST_CACHE_DIR = Path(tempfile.mkdtemp(prefix="meeting-intelligence-pytest-"))
os.environ[_CACHE_ENV] = str(_TEST_CACHE_DIR)
os.environ.pop("MEETING_INTELLIGENCE_TOKEN", None)


def pytest_sessionfinish() -> None:
    resolved = _TEST_CACHE_DIR.resolve()
    if (
        resolved.parent == Path(tempfile.gettempdir()).resolve()
        and resolved.name.startswith("meeting-intelligence-pytest-")
    ):
        shutil.rmtree(resolved, ignore_errors=True)
