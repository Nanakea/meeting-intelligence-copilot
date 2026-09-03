"""Run the optional local semantic provider in aggregate-only shadow mode."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.adapters.analysis.ollama_semantic import semantic_candidate_analyzer_from_env
from app.domain.contracts import Lang, Speaker, TranscriptEvent
from app.services.semantic_shadow_observer import SemanticShadowObserver


def load_events(path: Path, lang: Lang) -> list[TranscriptEvent]:
    events: list[TranscriptEvent] = []
    for seq, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        payload = json.loads(line)
        events.append(
            TranscriptEvent(
                event_id=f"shadow:event-{seq}",
                meeting_id="semantic-shadow-eval",
                seq=seq,
                speaker=Speaker(id="synthetic"),
                text=str(payload["text"]),
                lang=lang,
                ts_start=float(seq),
                ts_end=float(seq + 1),
                is_final=True,
                source="semantic-shadow-eval",
            )
        )
    return events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--lang", choices=("ja", "en"), required=True)
    args = parser.parse_args()

    provider = semantic_candidate_analyzer_from_env()
    counters = SemanticShadowObserver(provider).evaluate(
        load_events(args.fixture, Lang(args.lang))
    )
    print(json.dumps(asdict(counters), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
