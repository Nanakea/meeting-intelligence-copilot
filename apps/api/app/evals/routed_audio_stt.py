"""Privacy-safe evidence helpers for Meetily routed-audio STT acceptance."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.evals.pilot_matrix import routed_audio_matrix
from app.evals.routed_audio_assets import build_routed_audio_asset_plan

RESULT_PATTERN = re.compile(
    r"ROUTED_AUDIO_RESULT "
    r"(?P<scenario_id>audio-[a-z0-9_]+-(?:ja|en|ko)-[a-z_]+-[12]) "
    r"characters=(?P<character_count>[1-9][0-9]*)(?=$|\s)"
)


@dataclass(frozen=True)
class RoutedAudioScenarioRef:
    scenario_id: str
    category: str
    lang: str
    acoustic_profile: str
    seed: int
    audio_file: str
    sha256: str
    expected_ask_slot: str | None


@dataclass(frozen=True)
class RoutedAudioSttObservation:
    scenario_id: str
    character_count: int


@dataclass(frozen=True)
class ValidatedRoutedAudioCorpus:
    manifest_sha256: str
    scenarios: tuple[RoutedAudioScenarioRef, ...]
    platform_sources: tuple[RoutedAudioScenarioRef, ...] = ()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _validate_korean_no_pain_source(
    root: Path, raw: object
) -> RoutedAudioScenarioRef:
    if not isinstance(raw, dict):
        raise ValueError("platform audio source is invalid")
    expected = {
        "scenario_id": "platform-source-no-pain-ko",
        "category": "no_pain",
        "lang": "ko",
        "acoustic_profile": "clean",
        "seed": 1,
        "audio_file": "audio/platform-source-no-pain-ko.wav",
        "expected_ask_slot": None,
    }
    if any(raw.get(key) != value for key, value in expected.items()):
        raise ValueError("platform audio source descriptor mismatch")
    try:
        path = (root / expected["audio_file"]).resolve(strict=True)
    except OSError as exc:
        raise ValueError("platform audio source is unavailable") from exc
    if root not in path.parents:
        raise ValueError("platform audio source escapes corpus")
    byte_count = raw.get("bytes")
    sha256 = raw.get("sha256")
    if type(byte_count) is not int or byte_count <= 0:
        raise ValueError("platform audio source byte count is invalid")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9A-Fa-f]{64}", sha256):
        raise ValueError("platform audio source SHA-256 is invalid")
    if path.stat().st_size != byte_count or _sha256(path) != sha256.upper():
        raise ValueError("platform audio source integrity mismatch")
    return RoutedAudioScenarioRef(
        scenario_id=expected["scenario_id"],
        category=expected["category"],
        lang=expected["lang"],
        acoustic_profile=expected["acoustic_profile"],
        seed=expected["seed"],
        audio_file=expected["audio_file"],
        sha256=sha256.upper(),
        expected_ask_slot=None,
    )


def validate_routed_audio_corpus(root: Path) -> ValidatedRoutedAudioCorpus:
    """Validate the fixed matrix, paths, sizes, and hashes before model execution."""
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("routed-audio corpus directory is unavailable") from exc
    manifest_path = resolved_root / "MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("routed-audio corpus manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise ValueError("routed-audio corpus manifest must be an object")
    raw_scenarios = manifest.get("scenarios")
    if manifest.get("scenario_count") != 108 or not isinstance(raw_scenarios, list):
        raise ValueError("routed-audio corpus must declare exactly 108 scenarios")

    expected_matrix = {
        scenario.scenario_id: scenario for scenario in routed_audio_matrix()
    }
    expected_assets = {
        asset.scenario_id: asset for asset in build_routed_audio_asset_plan()
    }

    seen: set[str] = set()
    scenarios: list[RoutedAudioScenarioRef] = []
    for raw in raw_scenarios:
        if not isinstance(raw, dict):
            raise ValueError("routed-audio scenario must be an object")
        scenario_id = raw.get("scenario_id")
        if not isinstance(scenario_id, str):
            raise ValueError("routed-audio scenario ID is invalid")
        if scenario_id in seen:
            raise ValueError(f"duplicate routed-audio scenario: {scenario_id}")
        expected = expected_matrix.get(scenario_id)
        if expected is None:
            raise ValueError(f"unexpected routed-audio scenario: {scenario_id}")
        seen.add(scenario_id)

        category = raw.get("category")
        lang = raw.get("lang")
        acoustic_profile = raw.get("acoustic_profile")
        seed = raw.get("seed")
        if (
            not isinstance(category, str)
            or not isinstance(lang, str)
            or not isinstance(acoustic_profile, str)
            or type(seed) is not int
        ):
            raise ValueError(f"routed-audio descriptor is invalid: {scenario_id}")
        descriptor = (category, lang, acoustic_profile, seed)
        expected_descriptor = (
            expected.category,
            expected.lang,
            expected.acoustic_profile,
            expected.seed,
        )
        if descriptor != expected_descriptor:
            raise ValueError(f"routed-audio descriptor mismatch: {scenario_id}")

        expected_audio_file = f"audio/{scenario_id}.wav"
        if str(raw.get("audio_file")) != expected_audio_file:
            raise ValueError(f"routed-audio file mapping mismatch: {scenario_id}")
        expected_ask_slot = expected_assets[scenario_id].expected_ask_slot
        if raw.get("expected_ask_slot") != expected_ask_slot:
            raise ValueError(f"routed-audio ASK NOW expectation mismatch: {scenario_id}")

        relative_audio = Path(expected_audio_file)
        try:
            audio_path = (resolved_root / relative_audio).resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"routed-audio file is unavailable: {scenario_id}") from exc
        if resolved_root not in audio_path.parents:
            raise ValueError(f"routed-audio path escapes corpus: {scenario_id}")
        byte_count = raw.get("bytes")
        if type(byte_count) is not int or byte_count <= 0:
            raise ValueError(f"routed-audio byte count is invalid: {scenario_id}")
        raw_sha256 = raw.get("sha256")
        if not isinstance(raw_sha256, str):
            raise ValueError(f"routed-audio SHA-256 is invalid: {scenario_id}")
        audio_sha256 = raw_sha256.upper()
        if not re.fullmatch(r"[0-9A-F]{64}", audio_sha256):
            raise ValueError(f"routed-audio SHA-256 is invalid: {scenario_id}")
        try:
            actual_bytes = audio_path.stat().st_size
            actual_sha256 = _sha256(audio_path)
        except OSError as exc:
            raise ValueError(f"routed-audio file is unavailable: {scenario_id}") from exc
        if actual_bytes != byte_count:
            raise ValueError(f"routed-audio byte count mismatch: {scenario_id}")
        if actual_sha256 != audio_sha256:
            raise ValueError(f"routed-audio hash mismatch: {scenario_id}")

        scenarios.append(
            RoutedAudioScenarioRef(
                scenario_id=scenario_id,
                category=expected.category,
                lang=expected.lang,
                acoustic_profile=expected.acoustic_profile,
                seed=expected.seed,
                audio_file=expected_audio_file,
                sha256=audio_sha256,
                expected_ask_slot=expected_ask_slot,
            )
        )

    missing = set(expected_matrix) - seen
    if missing:
        raise ValueError(f"routed-audio corpus is missing {len(missing)} scenarios")

    language_counts: dict[str, int] = defaultdict(int)
    for scenario in scenarios:
        language_counts[scenario.lang] += 1
    if language_counts != {"en": 36, "ja": 36, "ko": 36}:
        raise ValueError(
            "routed-audio corpus must contain 36 EN, 36 JA, and 36 KO scenarios"
        )

    raw_platform_sources = manifest.get("platform_sources")
    if not isinstance(raw_platform_sources, list) or len(raw_platform_sources) != 1:
        raise ValueError("routed-audio corpus must contain one Korean no-pain source")
    platform_sources = (
        _validate_korean_no_pain_source(resolved_root, raw_platform_sources[0]),
    )

    return ValidatedRoutedAudioCorpus(
        manifest_sha256=_sha256(manifest_path),
        scenarios=tuple(scenarios),
        platform_sources=platform_sources,
    )


def parse_stt_observations(output: str) -> tuple[RoutedAudioSttObservation, ...]:
    """Extract only explicit privacy-safe result markers from a Cargo test run."""
    observations: list[RoutedAudioSttObservation] = []
    seen: set[str] = set()
    for line in output.splitlines():
        for match in RESULT_PATTERN.finditer(line):
            scenario_id = match.group("scenario_id")
            if scenario_id in seen:
                raise ValueError(f"duplicate STT result: {scenario_id}")
            seen.add(scenario_id)
            observations.append(
                RoutedAudioSttObservation(
                    scenario_id=scenario_id,
                    character_count=int(match.group("character_count")),
                )
            )
    return tuple(observations)


def validate_cargo_test_run(output: str, required_tests: tuple[str, ...]) -> None:
    """Require every named test and one successful exact-count test summary."""
    missing_tests = [
        test_name for test_name in required_tests if test_name not in output
    ]
    expected_summary = (
        f"test result: ok. {len(required_tests)} passed; 0 failed;"
    )
    if missing_tests or expected_summary not in output:
        raise ValueError(
            "Meetily routed-audio run did not prove every required test passed "
            f"(missing={len(missing_tests)})"
        )


def build_stt_report(
    corpus: ValidatedRoutedAudioCorpus,
    observations: tuple[RoutedAudioSttObservation, ...],
    *,
    assistant_commit: str,
    meetily_commit: str,
    generated_at_utc: str,
) -> dict[str, Any]:
    """Build aggregate-only evidence after requiring exact scenario coverage."""
    expected = {scenario.scenario_id: scenario for scenario in corpus.scenarios}
    observed = {observation.scenario_id: observation for observation in observations}
    missing = sorted(expected.keys() - observed.keys())
    unexpected = sorted(observed.keys() - expected.keys())
    if missing or unexpected or len(observations) != len(expected):
        raise ValueError(
            "STT observations do not exactly cover the routed-audio corpus "
            f"(missing={len(missing)}, unexpected={len(unexpected)})"
        )

    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for scenario_id, observation in observed.items():
        scenario = expected[scenario_id]
        buckets[(scenario.lang, scenario.acoustic_profile)].append(
            observation.character_count
        )

    summaries = []
    for (lang, acoustic_profile), counts in sorted(buckets.items()):
        summaries.append(
            {
                "lang": lang,
                "acoustic_profile": acoustic_profile,
                "scenario_count": len(counts),
                "minimum_character_count": min(counts),
                "maximum_character_count": max(counts),
                "mean_character_count": round(sum(counts) / len(counts), 2),
            }
        )

    return {
        "schema_version": 1,
        "scope": "local_stt_nonempty_only",
        "generated_at_utc": generated_at_utc,
        "assistant_commit": assistant_commit,
        "meetily_commit": meetily_commit,
        "corpus_manifest_sha256": corpus.manifest_sha256,
        "decoder_scenario_count": len(corpus.scenarios),
        "transcribed_scenario_count": len(observations),
        "all_transcripts_nonempty": all(
            observation.character_count > 0 for observation in observations
        ),
        "summaries": summaries,
        "does_not_prove": [
            "analyzer_accuracy",
            "ask_now_quality",
            "vb_cable_routing",
            "zoom_google_meet_teams_capture",
        ],
    }
