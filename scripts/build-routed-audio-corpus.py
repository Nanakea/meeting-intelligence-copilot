"""Build the fixed 108-scenario local routed-audio acceptance corpus on Windows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.evals.routed_audio_assets import (  # noqa: E402
    DEFAULT_SPEC,
    RoutedAudioAssetPlan,
    build_routed_audio_asset_plan,
)

SAPI_SCRIPT = r"""
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $synth.SelectVoice($env:ROUTED_AUDIO_VOICE)
    $synth.Rate = [int]$env:ROUTED_AUDIO_RATE
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
        16000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono
    )
    $synth.SetOutputToWaveFile($env:ROUTED_AUDIO_OUTPUT, $format)
    $synth.Speak($env:ROUTED_AUDIO_TEXT)
} finally {
    $synth.Dispose()
}
"""

ENGLISH_VOICES = (
    ("Microsoft David Desktop", -1),
    ("Microsoft Zira Desktop", 1),
)
JAPANESE_SAPI_VOICES = (
    ("Microsoft Haruka Desktop", -1),
    ("Microsoft Haruka Desktop", 1),
)
KOREAN_SAPI_VOICES = (
    ("Microsoft Heami Desktop", -1),
    ("Microsoft Heami Desktop", 1),
)
JAPANESE_STYLE_IDS = (2, 3)
VOICEVOX_URL = "http://127.0.0.1:50021"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def http_json(
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: float = 10,
) -> Any:
    headers = {} if content_type is None else {"Content-Type": content_type}
    request = urllib.request.Request(
        f"{VOICEVOX_URL}{path}",
        method=method,
        data=body,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content = response.read()
        if "application/json" in response.headers.get("Content-Type", ""):
            return json.loads(content.decode("utf-8"))
        return content


def wait_for_voicevox(process: subprocess.Popen[bytes] | None, timeout: float = 30) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError("VOICEVOX engine exited before readiness")
        try:
            value = http_json("GET", "/version", timeout=1)
            if isinstance(value, str):
                return value
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(0.2)
    raise TimeoutError("VOICEVOX engine did not become ready")


def start_voicevox(engine: Path) -> tuple[subprocess.Popen[bytes] | None, str]:
    try:
        return None, wait_for_voicevox(None, timeout=1)
    except TimeoutError:
        pass
    process = subprocess.Popen(
        [str(engine), "--host", "127.0.0.1", "--port", "50021"],
        cwd=engine.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return process, wait_for_voicevox(process)


def synthesize_sapi(text: str, output: Path, voice: str, rate: int) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "ROUTED_AUDIO_TEXT": text,
            "ROUTED_AUDIO_OUTPUT": str(output),
            "ROUTED_AUDIO_VOICE": voice,
            "ROUTED_AUDIO_RATE": str(rate),
        }
    )
    subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            SAPI_SCRIPT,
        ],
        env=environment,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def synthesize_voicevox(text: str, output: Path, style_id: int) -> None:
    encoded = urllib.parse.quote(text, safe="")
    query = http_json("POST", f"/audio_query?text={encoded}&speaker={style_id}")
    if not isinstance(query, dict):
        raise RuntimeError("VOICEVOX audio query returned an invalid response")
    query["speedScale"] = 0.96 if style_id == JAPANESE_STYLE_IDS[0] else 1.04
    audio = http_json(
        "POST",
        f"/synthesis?speaker={style_id}",
        body=json.dumps(query, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
        timeout=60,
    )
    if not isinstance(audio, bytes) or not audio:
        raise RuntimeError("VOICEVOX synthesis returned no audio")
    output.write_bytes(audio)


def ffmpeg_profile(ffmpeg: Path, source: Path, output: Path, profile: str) -> None:
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
    if profile == "clean":
        command.extend(["-af", "aresample=16000"])
    elif profile == "office_noise":
        command.extend(
            [
                "-filter_complex",
                (
                    "anoisesrc=color=pink:amplitude=0.018:r=16000[noise];"
                    "[0:a][noise]amix=inputs=2:duration=first:weights='1 0.32',"
                    "alimiter=limit=0.95"
                ),
            ]
        )
    elif profile == "compressed":
        command.extend(
            [
                "-af",
                (
                    "highpass=f=150,lowpass=f=3800,"
                    "acompressor=threshold=-24dB:ratio=3:attack=20:release=200,"
                    "volume=0.85,aresample=16000"
                ),
            ]
        )
    else:
        raise ValueError(f"unsupported acoustic profile: {profile}")
    command.extend(["-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(output)])
    subprocess.run(
        command,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def relative_to_repo(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def synthesizer_metadata(
    *,
    lang: str,
    seed: int,
    use_voicevox: bool,
) -> dict[str, object]:
    if lang in {"en", "ko"} or not use_voicevox:
        voices = (
            ENGLISH_VOICES
            if lang == "en"
            else KOREAN_SAPI_VOICES
            if lang == "ko"
            else JAPANESE_SAPI_VOICES
        )
        voice, rate = voices[seed - 1]
        return {
            "engine": "windows_sapi",
            "voice": voice,
            "rate": rate,
        }
    return {
        "engine": "voicevox",
        "style_id": JAPANESE_STYLE_IDS[seed - 1],
        "speed_scale": 0.96 if seed == 1 else 1.04,
    }


def build(
    *,
    output: Path,
    ffmpeg: Path,
    voicevox_engine: Path | None,
    spec: Path,
) -> dict[str, object]:
    if output.exists():
        raise ValueError("refusing to overwrite an existing routed-audio corpus")
    if not ffmpeg.is_file():
        raise ValueError("FFmpeg executable is unavailable")
    if voicevox_engine is not None and not voicevox_engine.is_file():
        raise ValueError("VOICEVOX engine executable is unavailable")
    use_voicevox = voicevox_engine is not None
    plan = build_routed_audio_asset_plan(spec)
    output.mkdir(parents=True)
    audio_directory = output / "audio"
    audio_directory.mkdir()
    process: subprocess.Popen[bytes] | None = None
    voicevox_version = ""
    platform_sources: list[dict[str, object]] = []

    grouped: dict[tuple[str, str, int], list[RoutedAudioAssetPlan]] = defaultdict(list)
    for item in plan:
        grouped[(item.category, item.lang, item.seed)].append(item)

    try:
        if voicevox_engine is not None:
            process, voicevox_version = start_voicevox(voicevox_engine)
        with tempfile.TemporaryDirectory(prefix="routed-audio-corpus-") as temporary:
            temporary_root = Path(temporary)
            base_files: dict[tuple[str, str, int], Path] = {}
            for key, items in sorted(grouped.items()):
                category, lang, seed = key
                text = " ".join(items[0].utterances)
                base = temporary_root / f"{category}-{lang}-{seed}.wav"
                if lang in {"en", "ko"}:
                    voices = ENGLISH_VOICES if lang == "en" else KOREAN_SAPI_VOICES
                    voice, rate = voices[seed - 1]
                    synthesize_sapi(text, base, voice, rate)
                elif use_voicevox:
                    synthesize_voicevox(text, base, JAPANESE_STYLE_IDS[seed - 1])
                else:
                    voice, rate = JAPANESE_SAPI_VOICES[seed - 1]
                    synthesize_sapi(text, base, voice, rate)
                base_files[key] = base

            scenarios: list[dict[str, object]] = []
            for item in plan:
                destination = audio_directory / f"{item.scenario_id}.wav"
                ffmpeg_profile(
                    ffmpeg,
                    base_files[(item.category, item.lang, item.seed)],
                    destination,
                    item.acoustic_profile,
                )
                scenarios.append(
                    {
                        "scenario_id": item.scenario_id,
                        "category": item.category,
                        "lang": item.lang,
                        "acoustic_profile": item.acoustic_profile,
                        "seed": item.seed,
                        "audio_file": f"audio/{destination.name}",
                        "sha256": sha256(destination),
                        "bytes": destination.stat().st_size,
                        "fixture": relative_to_repo(item.fixture),
                        "golden": relative_to_repo(item.golden),
                        "expected_ask_slot": item.expected_ask_slot,
                        "synthesizer": synthesizer_metadata(
                            lang=item.lang,
                            seed=item.seed,
                            use_voicevox=use_voicevox,
                        ),
                    }
                )
            no_pain_base = temporary_root / "platform-source-no-pain-ko.wav"
            voice, rate = KOREAN_SAPI_VOICES[0]
            synthesize_sapi(
                "오늘 회의 일정을 확인하고 다음 안건으로 이동하겠습니다.",
                no_pain_base,
                voice,
                rate,
            )
            no_pain_destination = audio_directory / "platform-source-no-pain-ko.wav"
            ffmpeg_profile(ffmpeg, no_pain_base, no_pain_destination, "clean")
            platform_sources.append(
                {
                    "scenario_id": "platform-source-no-pain-ko",
                    "category": "no_pain",
                    "lang": "ko",
                    "acoustic_profile": "clean",
                    "seed": 1,
                    "audio_file": "audio/platform-source-no-pain-ko.wav",
                    "sha256": sha256(no_pain_destination),
                    "bytes": no_pain_destination.stat().st_size,
                    "expected_ask_slot": None,
                    "synthesizer": synthesizer_metadata(
                        lang="ko", seed=1, use_voicevox=False
                    ),
                }
            )
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise
    finally:
        if process is not None and process.poll() is None:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

    manifest: dict[str, object] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "scenario_count": len(scenarios),
        "corpus_spec": relative_to_repo(spec),
        "corpus_spec_sha256": sha256(spec),
        "ffmpeg_sha256": sha256(ffmpeg),
        "voicevox_version": voicevox_version,
        "scenarios": scenarios,
        "platform_sources": platform_sources,
    }
    manifest_path = output / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--output", type=Path, required=True)
    root.add_argument("--ffmpeg", type=Path, required=True)
    root.add_argument(
        "--voicevox-engine",
        type=Path,
        help="Optional local VOICEVOX engine. Without it, installed Japanese SAPI voices are used.",
    )
    root.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    return root


if __name__ == "__main__":
    arguments = parser().parse_args()
    result = build(
        output=arguments.output.resolve(),
        ffmpeg=arguments.ffmpeg.resolve(),
        voicevox_engine=(
            arguments.voicevox_engine.resolve()
            if arguments.voicevox_engine is not None
            else None
        ),
        spec=arguments.spec.resolve(),
    )
    print(
        json.dumps(
            {
                "output": str(arguments.output.resolve()),
                "scenario_count": result["scenario_count"],
                "japanese_synthesizer": (
                    "voicevox"
                    if arguments.voicevox_engine is not None
                    else "windows_sapi"
                ),
                "voicevox_version": result["voicevox_version"],
            },
            sort_keys=True,
        )
    )
