"""Deterministic acceptance matrices for routed audio and platform paths."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

CATEGORIES = (
    "data_mismatch",
    "integration_failure",
    "manual_work",
    "process_delay",
    "vague_requirement",
    "unclear_ownership",
)
LANGUAGES = ("ja", "en", "ko")
ACOUSTIC_PROFILES = ("clean", "office_noise", "compressed")
AUDIO_SEEDS = (1, 2)
PLATFORMS = ("zoom_desktop", "google_meet_browser", "teams_desktop")
PLATFORM_SEEDS = (1, 2, 3)

# Platform acceptance is about the real application audio route, but each run
# still needs a reproducible input and panel expectation. The three runs cover
# distinct categories, acoustic profiles, and synthetic voices.
PLATFORM_SOURCE_RUNS = {
    1: ("data_mismatch", "clean", 1),
    2: ("manual_work", "office_noise", 2),
    3: ("integration_failure", "compressed", 1),
}


@dataclass(frozen=True)
class RoutedAudioScenario:
    scenario_id: str
    category: str
    lang: str
    acoustic_profile: str
    seed: int


@dataclass(frozen=True)
class PlatformPathScenario:
    scenario_id: str
    platform: str
    lang: str
    seed: int
    category: str
    acoustic_profile: str
    source_audio_scenario_id: str


def routed_audio_matrix() -> tuple[RoutedAudioScenario, ...]:
    return tuple(
        RoutedAudioScenario(
            scenario_id=f"audio-{category}-{lang}-{profile}-{seed}",
            category=category,
            lang=lang,
            acoustic_profile=profile,
            seed=seed,
        )
        for category, lang, profile, seed in product(
            CATEGORIES, LANGUAGES, ACOUSTIC_PROFILES, AUDIO_SEEDS
        )
    )


def platform_path_matrix() -> tuple[PlatformPathScenario, ...]:
    scenarios = []
    for platform, lang, seed in product(PLATFORMS, ("ja", "en"), PLATFORM_SEEDS):
        category, acoustic_profile, audio_seed = PLATFORM_SOURCE_RUNS[seed]
        scenarios.append(
            PlatformPathScenario(
                scenario_id=f"platform-{platform}-{lang}-{seed}",
                platform=platform,
                lang=lang,
                seed=seed,
                category=category,
                acoustic_profile=acoustic_profile,
                source_audio_scenario_id=(
                    f"audio-{category}-{lang}-{acoustic_profile}-{audio_seed}"
                ),
            )
        )
    for platform, seed in product(PLATFORMS, (1, 2)):
        positive = seed == 1
        scenarios.append(
            PlatformPathScenario(
                scenario_id=f"platform-{platform}-ko-{seed}",
                platform=platform,
                lang="ko",
                seed=seed,
                category="data_mismatch" if positive else "no_pain",
                acoustic_profile="clean",
                source_audio_scenario_id=(
                    "audio-data_mismatch-ko-clean-1"
                    if positive
                    else "platform-source-no-pain-ko"
                ),
            )
        )
    return tuple(scenarios)
