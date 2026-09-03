"""Tests for the local routed-audio corpus build tool."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


def _builder() -> ModuleType:
    script = Path(__file__).resolve().parents[3] / "scripts" / "build-routed-audio-corpus.py"
    spec = importlib.util.spec_from_file_location("build_routed_audio_corpus", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sapi_metadata_uses_distinct_seed_profiles() -> None:
    builder = _builder()

    english = [
        builder.synthesizer_metadata(lang="en", seed=seed, use_voicevox=False)
        for seed in (1, 2)
    ]
    japanese = [
        builder.synthesizer_metadata(lang="ja", seed=seed, use_voicevox=False)
        for seed in (1, 2)
    ]
    korean = [
        builder.synthesizer_metadata(lang="ko", seed=seed, use_voicevox=False)
        for seed in (1, 2)
    ]

    assert {item["engine"] for item in english + japanese + korean} == {"windows_sapi"}
    assert len({item["voice"] for item in english}) == 2
    assert len({(item["voice"], item["rate"]) for item in japanese}) == 2
    assert len({(item["voice"], item["rate"]) for item in korean}) == 2
    assert all(
        str(item["voice"]).startswith("Microsoft ")
        for item in english + japanese + korean
    )


def test_build_supports_sapi_only_corpus_without_starting_voicevox(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builder = _builder()
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"reviewed-ffmpeg")
    output = tmp_path / "corpus"
    synthesis_calls: list[tuple[str, int]] = []

    def fake_sapi(text: str, destination: Path, voice: str, rate: int) -> None:
        assert text.strip()
        synthesis_calls.append((voice, rate))
        destination.write_bytes(b"RIFF-synthetic-base")

    def fake_profile(
        _ffmpeg: Path,
        source: Path,
        destination: Path,
        profile: str,
    ) -> None:
        assert source.is_file()
        destination.write_bytes(f"RIFF-{profile}".encode())

    monkeypatch.setattr(builder, "synthesize_sapi", fake_sapi)
    monkeypatch.setattr(builder, "ffmpeg_profile", fake_profile)
    monkeypatch.setattr(
        builder,
        "start_voicevox",
        lambda _engine: (_ for _ in ()).throw(AssertionError("VOICEVOX must not start")),
    )

    report = builder.build(
        output=output,
        ffmpeg=ffmpeg,
        voicevox_engine=None,
        spec=builder.DEFAULT_SPEC,
    )

    assert report["scenario_count"] == 108
    assert report["voicevox_version"] == ""
    assert len(synthesis_calls) == 37
    manifest = json.loads((output / "MANIFEST.json").read_text(encoding="utf-8"))
    japanese = [item for item in manifest["scenarios"] if item["lang"] == "ja"]
    assert len(japanese) == 36
    korean = [item for item in manifest["scenarios"] if item["lang"] == "ko"]
    assert len(korean) == 36
    assert {item["synthesizer"]["engine"] for item in japanese} == {"windows_sapi"}
    assert len({
        (item["synthesizer"]["voice"], item["synthesizer"]["rate"])
        for item in japanese
    }) == 2
    assert {item["synthesizer"]["engine"] for item in korean} == {"windows_sapi"}
    assert manifest["platform_sources"][0]["category"] == "no_pain"
    assert (output / manifest["platform_sources"][0]["audio_file"]).is_file()
    assert all((output / item["audio_file"]).is_file() for item in manifest["scenarios"])
