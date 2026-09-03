"""Run Meetily's local STT corpus checks and emit aggregate-only evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.evals.routed_audio_stt import (  # noqa: E402
    build_stt_report,
    parse_stt_observations,
    validate_cargo_test_run,
    validate_routed_audio_corpus,
)

TEST_MODULE = "meeting_intelligence_pilot::tests"
TESTS = (
    f"{TEST_MODULE}::routed_audio_corpus_decodes_all_declared_scenarios",
    f"{TEST_MODULE}::real_routed_audio_corpus_transcribes_all_english_scenarios",
    f"{TEST_MODULE}::real_routed_audio_corpus_transcribes_all_japanese_scenarios",
    f"{TEST_MODULE}::real_routed_audio_corpus_transcribes_all_korean_scenarios",
)


def git_commit(repository: Path) -> str:
    for arguments, description in (
        (("diff", "--quiet", "--"), "unstaged"),
        (("diff", "--cached", "--quiet", "--"), "staged"),
    ):
        status = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=False,
            capture_output=True,
        )
        if status.returncode != 0:
            raise RuntimeError(
                f"repository has {description} tracked changes or is unreadable"
            )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    commit = result.stdout.strip()
    if len(commit) != 40:
        raise RuntimeError("repository did not return a full Git commit")
    return commit


def run_cargo_tests(
    source_directory: Path,
    environment: dict[str, str],
) -> str:
    result = subprocess.run(
        [
            "cargo",
            "test",
            "--locked",
            "routed_audio_corpus",
            "--",
            "--ignored",
            "--nocapture",
            "--test-threads=1",
        ],
        cwd=source_directory,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Meetily routed-audio tests failed with exit code "
            f"{result.returncode}"
        )
    output = f"{result.stdout}\n{result.stderr}"
    validate_cargo_test_run(output, TESTS)
    return output


def write_report(path: Path, report: dict[str, object], *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing report: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--meetily-root", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cargo-target-dir", type=Path)
    parser.add_argument("--libclang-path", type=Path)
    parser.add_argument("--cmake-generator", default="Visual Studio 17 2022")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if os.name != "nt":
        raise RuntimeError("routed-audio STT acceptance is Windows-only")

    meetily_root = args.meetily_root.resolve(strict=True)
    source_directory = meetily_root / "frontend" / "src-tauri"
    if not (source_directory / "Cargo.toml").is_file():
        raise FileNotFoundError("Meetily Tauri source directory is invalid")

    corpus = validate_routed_audio_corpus(args.corpus)
    environment = os.environ.copy()
    environment["MEETILY_ROUTED_AUDIO_CORPUS"] = str(args.corpus.resolve())
    environment["CMAKE_GENERATOR"] = args.cmake_generator
    if args.cargo_target_dir is not None:
        environment["CARGO_TARGET_DIR"] = str(args.cargo_target_dir.resolve())
    if args.libclang_path is not None:
        environment["LIBCLANG_PATH"] = str(args.libclang_path.resolve(strict=True))

    output = run_cargo_tests(source_directory, environment)
    observations = parse_stt_observations(output)
    report = build_stt_report(
        corpus,
        observations,
        assistant_commit=git_commit(REPO_ROOT),
        meetily_commit=git_commit(meetily_root),
        generated_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    write_report(args.output.resolve(), report, force=args.force)
    print(
        "Routed-audio local STT evidence written: "
        f"decoded={report['decoder_scenario_count']} "
        f"transcribed={report['transcribed_scenario_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
