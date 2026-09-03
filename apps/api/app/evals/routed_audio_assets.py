"""Deterministic routed-audio asset plan derived from independent pilot specs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.evals.pilot_matrix import routed_audio_matrix

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SPEC = REPO_ROOT / "evals" / "pilot" / "corpus-spec.json"
DEFAULT_KOREAN_SOURCES = (
    REPO_ROOT / "evals" / "pilot" / "korean-routed-audio-sources.json"
)


@dataclass(frozen=True)
class RoutedAudioAssetPlan:
    scenario_id: str
    category: str
    lang: str
    acoustic_profile: str
    seed: int
    fixture: Path
    golden: Path
    utterances: tuple[str, ...]
    expected_ask_slot: str | None


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _utterances(path: Path) -> tuple[str, ...]:
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    values = tuple(str(event["text"]).strip() for event in events)
    if not values or any(not value for value in values):
        raise ValueError(f"fixture must contain non-empty transcript text: {path}")
    return values


def _expected_ask_slot(path: Path) -> str | None:
    golden = _json(path)
    checkpoints = golden.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise ValueError(f"golden must contain checkpoints: {path}")
    value = checkpoints[-1].get("ask_now_gap")
    return str(value) if value is not None else None


def build_routed_audio_asset_plan(
    spec_path: Path = DEFAULT_SPEC,
    korean_sources_path: Path = DEFAULT_KOREAN_SOURCES,
) -> tuple[RoutedAudioAssetPlan, ...]:
    """Bind every matrix row to source speech and an independent final expectation."""
    spec = _json(spec_path)
    groups = spec.get("groups")
    if not isinstance(groups, list):
        raise ValueError("pilot corpus spec must contain groups")
    korean_spec = _json(korean_sources_path)
    korean_groups = korean_spec.get("sources")
    if not isinstance(korean_groups, list):
        raise ValueError("Korean routed-audio spec must contain sources")
    groups = [*groups, *korean_groups]

    sources: dict[tuple[str, str], tuple[Path, Path, tuple[str, ...], str | None]] = {}
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("pilot corpus group must be an object")
        category = str(group["category"])
        lang = str(group["lang"])
        key = (category, lang)
        if key in sources:
            raise ValueError(f"duplicate pilot corpus group: {category}/{lang}")
        fixture = REPO_ROOT / str(group["fixture"])
        golden = REPO_ROOT / str(group["golden"])
        if not fixture.is_file() or not golden.is_file():
            raise ValueError(f"pilot corpus source is missing: {category}/{lang}")
        sources[key] = (
            fixture,
            golden,
            _utterances(fixture),
            _expected_ask_slot(golden),
        )

    plan: list[RoutedAudioAssetPlan] = []
    for scenario in routed_audio_matrix():
        key = (scenario.category, scenario.lang)
        if key not in sources:
            raise ValueError(
                f"routed-audio matrix has no corpus source: "
                f"{scenario.category}/{scenario.lang}"
            )
        fixture, golden, utterances, expected_ask_slot = sources[key]
        plan.append(
            RoutedAudioAssetPlan(
                scenario_id=scenario.scenario_id,
                category=scenario.category,
                lang=scenario.lang,
                acoustic_profile=scenario.acoustic_profile,
                seed=scenario.seed,
                fixture=fixture,
                golden=golden,
                utterances=utterances,
                expected_ask_slot=expected_ask_slot,
            )
        )
    return tuple(plan)
