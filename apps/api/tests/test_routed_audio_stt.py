import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.evals.pilot_matrix import routed_audio_matrix
from app.evals.routed_audio_assets import build_routed_audio_asset_plan
from app.evals.routed_audio_stt import (
    RoutedAudioScenarioRef,
    RoutedAudioSttObservation,
    ValidatedRoutedAudioCorpus,
    build_stt_report,
    parse_stt_observations,
    validate_cargo_test_run,
    validate_routed_audio_corpus,
)


def _corpus() -> ValidatedRoutedAudioCorpus:
    scenarios = tuple(
        RoutedAudioScenarioRef(
            scenario_id=f"audio-category-{lang}-{profile}-{seed}",
            category="category",
            lang=lang,
            acoustic_profile=profile,
            seed=1 if seed % 2 else 2,
            audio_file=f"audio/audio-category-{lang}-{profile}-{seed}.wav",
            sha256="A" * 64,
            expected_ask_slot=None,
        )
        for lang in ("en", "ja", "ko")
        for profile in ("clean", "compressed", "office_noise")
        for seed in range(1, 13)
    )
    return ValidatedRoutedAudioCorpus(
        manifest_sha256="A" * 64,
        scenarios=scenarios,
    )


def _write_valid_corpus(root: Path) -> Path:
    rows = []
    assets = {
        asset.scenario_id: asset for asset in build_routed_audio_asset_plan()
    }
    audio_directory = root / "audio"
    audio_directory.mkdir(parents=True)
    for scenario in routed_audio_matrix():
        payload = f"synthetic:{scenario.scenario_id}\n".encode()
        relative = f"audio/{scenario.scenario_id}.wav"
        (root / relative).write_bytes(payload)
        rows.append(
            {
                "scenario_id": scenario.scenario_id,
                "category": scenario.category,
                "lang": scenario.lang,
                "acoustic_profile": scenario.acoustic_profile,
                "seed": scenario.seed,
                "audio_file": relative,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest().upper(),
                "expected_ask_slot": assets[scenario.scenario_id].expected_ask_slot,
            }
        )
    no_pain_payload = b"synthetic:platform-source-no-pain-ko\n"
    no_pain_relative = "audio/platform-source-no-pain-ko.wav"
    (root / no_pain_relative).write_bytes(no_pain_payload)
    platform_sources = [
        {
            "scenario_id": "platform-source-no-pain-ko",
            "category": "no_pain",
            "lang": "ko",
            "acoustic_profile": "clean",
            "seed": 1,
            "audio_file": no_pain_relative,
            "bytes": len(no_pain_payload),
            "sha256": hashlib.sha256(no_pain_payload).hexdigest().upper(),
            "expected_ask_slot": None,
        }
    ]
    (root / "MANIFEST.json").write_text(
        json.dumps(
            {
                "scenario_count": len(rows),
                "scenarios": rows,
                "platform_sources": platform_sources,
            }
        ),
        encoding="utf-8",
    )
    return root


def test_corpus_validation_requires_the_authoritative_matrix(tmp_path: Path) -> None:
    root = _write_valid_corpus(tmp_path)

    corpus = validate_routed_audio_corpus(root)

    assert len(corpus.scenarios) == 108
    assert len(corpus.platform_sources) == 1
    assert corpus.scenarios[0].audio_file.startswith("audio/")
    assert len(corpus.manifest_sha256) == 64


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("lang", "en", "descriptor mismatch"),
        ("seed", True, "descriptor is invalid"),
        ("audio_file", "audio/substitute.wav", "file mapping mismatch"),
        ("expected_ask_slot", "substitute", "ASK NOW expectation mismatch"),
    ],
)
def test_corpus_validation_rejects_descriptor_substitution(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    root = _write_valid_corpus(tmp_path)
    manifest_path = root / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scenarios"][0][field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        validate_routed_audio_corpus(root)


def test_corpus_validation_errors_do_not_expose_local_paths(tmp_path: Path) -> None:
    root = _write_valid_corpus(tmp_path)
    missing = root / "audio" / "audio-data_mismatch-ja-clean-1.wav"
    missing.unlink()

    with pytest.raises(ValueError, match="file is unavailable") as failure:
        validate_routed_audio_corpus(root)

    assert str(tmp_path) not in str(failure.value)


def test_acceptance_cli_describes_bound_platform_input_without_local_path(
    tmp_path: Path,
) -> None:
    corpus_root = _write_valid_corpus(tmp_path / "corpus")
    ledger = tmp_path / "acceptance.json"
    script = Path(__file__).resolve().parents[3] / "scripts" / "pilot-acceptance-ledger.py"
    initialized = subprocess.run(
        [
            sys.executable,
            str(script),
            "init",
            str(ledger),
            "--candidate-sha256",
            "a" * 64,
            "--assistant-commit",
            "b" * 40,
            "--meetily-commit",
            "c" * 40,
            "--corpus",
            str(corpus_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert initialized.returncode == 0, initialized.stderr
    # Windows PowerShell 5.1 commonly emits UTF-8 JSON with a BOM. Release
    # validation accepts that existing format while new evidence is canonical.
    ledger.write_text(ledger.read_text(encoding="utf-8"), encoding="utf-8-sig")
    validated = subprocess.run(
        [sys.executable, str(script), "validate", str(ledger)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert validated.returncode == 0, validated.stderr

    described = subprocess.run(
        [
            sys.executable,
            str(script),
            "describe",
            str(ledger),
            "platform-zoom_desktop-en-2",
            "--corpus",
            str(corpus_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert described.returncode == 0, described.stderr
    result = json.loads(described.stdout)
    assert result["source_audio_scenario_id"] == (
        "audio-manual_work-en-office_noise-2"
    )
    assert result["source_audio_file"] == (
        "audio/audio-manual_work-en-office_noise-2.wav"
    )
    assert result["expected_ask_slot"] == "reason_manual"
    assert str(tmp_path) not in described.stdout

    described_no_pain = subprocess.run(
        [
            sys.executable,
            str(script),
            "describe",
            str(ledger),
            "platform-zoom_desktop-ko-2",
            "--corpus",
            str(corpus_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert described_no_pain.returncode == 0, described_no_pain.stderr
    no_pain = json.loads(described_no_pain.stdout)
    assert no_pain["source_audio_scenario_id"] == "platform-source-no-pain-ko"
    assert no_pain["source_audio_file"] == "audio/platform-source-no-pain-ko.wav"
    assert no_pain["expected_ask_slot"] is None
    assert str(tmp_path) not in described_no_pain.stdout


def test_parser_keeps_only_explicit_aggregate_result_markers() -> None:
    output = """
compiler warning containing a local path
ROUTED_AUDIO_RESULT audio-data_mismatch-en-clean-1 characters=248
transcript-shaped unrelated output
test module::slow ... ROUTED_AUDIO_RESULT audio-manual_work-ja-office_noise-2 characters=115
"""

    assert parse_stt_observations(output) == (
        RoutedAudioSttObservation(
            scenario_id="audio-data_mismatch-en-clean-1",
            character_count=248,
        ),
        RoutedAudioSttObservation(
            scenario_id="audio-manual_work-ja-office_noise-2",
            character_count=115,
        ),
    )


def test_parser_rejects_duplicate_result_markers() -> None:
    marker = "ROUTED_AUDIO_RESULT audio-data_mismatch-en-clean-1 characters=248"

    try:
        parse_stt_observations(f"{marker}\n{marker}")
    except ValueError as error:
        assert "duplicate STT result" in str(error)
    else:
        raise AssertionError("duplicate marker was accepted")


def test_cargo_output_accepts_long_running_test_display_format() -> None:
    required = ("module::fast", "module::slow_en", "module::slow_ja")
    output = """
test module::fast ... ok
test module::slow_en has been running for over 60 seconds
test module::slow_ja has been running for over 60 seconds
test result: ok. 3 passed; 0 failed; 0 ignored
"""

    validate_cargo_test_run(output, required)


def test_cargo_output_rejects_missing_named_test_despite_green_summary() -> None:
    try:
        validate_cargo_test_run(
            "module::fast\ntest result: ok. 3 passed; 0 failed;",
            ("module::fast", "module::slow_en", "module::slow_ja"),
        )
    except ValueError as error:
        assert "missing=2" in str(error)
    else:
        raise AssertionError("incomplete named-test evidence was accepted")


def test_report_requires_exact_108_scenario_coverage() -> None:
    corpus = _corpus()
    observations = tuple(
        RoutedAudioSttObservation(
            scenario_id=scenario.scenario_id,
            character_count=100,
        )
        for scenario in corpus.scenarios
    )

    report = build_stt_report(
        corpus,
        observations,
        assistant_commit="1" * 40,
        meetily_commit="2" * 40,
        generated_at_utc="2026-07-27T00:00:00Z",
    )

    assert report["scope"] == "local_stt_nonempty_only"
    assert report["decoder_scenario_count"] == 108
    assert report["transcribed_scenario_count"] == 108
    assert report["all_transcripts_nonempty"] is True
    assert len(report["summaries"]) == 9
    assert "ask_now_quality" in report["does_not_prove"]


def test_report_rejects_partial_sweep() -> None:
    corpus = _corpus()

    try:
        build_stt_report(
            corpus,
            (
                RoutedAudioSttObservation(
                    scenario_id=corpus.scenarios[0].scenario_id,
                    character_count=100,
                ),
            ),
            assistant_commit="1" * 40,
            meetily_commit="2" * 40,
            generated_at_utc="2026-07-27T00:00:00Z",
        )
    except ValueError as error:
        assert "missing=107" in str(error)
    else:
        raise AssertionError("partial STT sweep was accepted")
