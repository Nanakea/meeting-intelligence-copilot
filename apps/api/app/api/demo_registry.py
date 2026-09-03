"""Server-side demo meeting registry (transport concern).

Maps a fixed set of known demo meeting IDs to their fixture file and language.
The WebSocket accepts only these IDs — never a client-supplied filesystem path —
so there is no path-traversal surface. Unknown IDs resolve to ``None`` and the
caller fails cleanly."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# app/api/demo_registry.py -> api -> app -> apps/api -> apps -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
_FIXTURES_DIR = _REPO_ROOT / "evals" / "fixtures"

# demo_id -> (fixture filename, language). Filenames are constants, not input.
_DEMOS: dict[str, tuple[str, str]] = {
    "inventory-mismatch-ja": ("inventory-mismatch-ja.jsonl", "ja"),
    "data-mismatch-en": ("data-mismatch-en.jsonl", "en"),
    "data-mismatch-ko": ("data-mismatch-ko.jsonl", "ko"),
    "vague-requirement-ja": ("vague-requirement-ja.jsonl", "ja"),
    "vague-requirement-en": ("vague-requirement-en.jsonl", "en"),
    "vendor-integration-failure-ja": ("vendor-integration-failure-ja.jsonl", "ja"),
    "manual-work-en": ("manual-work-en.jsonl", "en"),
    "manual-work-ja": ("manual-work-ja.jsonl", "ja"),
    "manual-work-volume-en": ("manual-work-volume-en.jsonl", "en"),
    "integration-transfer-details-en": ("integration-transfer-details-en.jsonl", "en"),
    "process-delay-en": ("process-delay-en.jsonl", "en"),
    "process-delay-ja": ("process-delay-ja.jsonl", "ja"),
    "unclear-ownership-en": ("unclear-ownership-en.jsonl", "en"),
    "unclear-ownership-ja": ("unclear-ownership-ja.jsonl", "ja"),
    "small-talk-no-pain-ja": ("small-talk-no-pain-ja.jsonl", "ja"),
    "small-talk-no-pain-en": ("small-talk-no-pain-en.jsonl", "en"),
    "integration-failure-mixed-terms-ja": ("integration-failure-mixed-terms-ja.jsonl", "ja"),
    "integration-transfer-details-ja": ("integration-transfer-details-ja.jsonl", "ja"),
    "owner-answer-ja": ("owner-answer-ja.jsonl", "ja"),
}

# Convenience aliases (resolved but not part of the canonical enumerated set).
_ALIASES: dict[str, str] = {"demo": "inventory-mismatch-ja"}


@dataclass(frozen=True)
class DemoSource:
    demo_id: str
    fixture_path: Path
    lang: str


def resolve_demo(meeting_id: str) -> DemoSource | None:
    canonical = _ALIASES.get(meeting_id, meeting_id)
    entry = _DEMOS.get(canonical)
    if entry is None:
        return None
    filename, lang = entry
    return DemoSource(canonical, _FIXTURES_DIR / filename, lang)


def demo_ids() -> tuple[str, ...]:
    return tuple(_DEMOS.keys())
