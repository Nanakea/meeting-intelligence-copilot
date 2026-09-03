"""Create, record, and validate privacy-safe external pilot acceptance evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.evals.pilot_acceptance import (  # noqa: E402
    FAILURE_STAGES,
    REQUIRED_CHECKS,
    acceptance_summary_dict,
    new_acceptance_ledger,
    record_acceptance_result,
    validate_acceptance_ledger,
    validate_release_binding,
)
from app.evals.routed_audio_stt import validate_routed_audio_corpus  # noqa: E402


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def read_ledger(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("acceptance ledger could not be read as JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("acceptance ledger must be a JSON object")
    return value


def write_atomic(path: Path, ledger: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description=(
            "Manage aggregate-only routed-audio and platform acceptance evidence. "
            "The ledger never accepts transcript text, paths, titles, tokens, or free-form notes."
        )
    )
    commands = root.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="create a 132-scenario pending ledger")
    initialize.add_argument("ledger", type=Path)
    initialize.add_argument("--candidate-sha256", required=True)
    initialize.add_argument("--assistant-commit", required=True)
    initialize.add_argument("--meetily-commit", required=True)
    initialize.add_argument(
        "--corpus",
        required=True,
        type=Path,
        help="validated 108-scenario routed-audio corpus directory",
    )

    passed = commands.add_parser("pass", help="attest all required checks for one scenario")
    passed.add_argument("ledger", type=Path)
    passed.add_argument("scenario_id")

    failed = commands.add_parser("fail", help="record one scenario failure using a bounded code")
    failed.add_argument("ledger", type=Path)
    failed.add_argument("scenario_id")
    failed.add_argument("--stage", choices=sorted(FAILURE_STAGES), required=True)

    validate = commands.add_parser("validate", help="validate ledger integrity and status")
    validate.add_argument("ledger", type=Path)
    validate.add_argument("--require-complete", action="store_true")
    validate.add_argument("--require-all-passed", action="store_true")

    release = commands.add_parser(
        "validate-release",
        help="bind an all-pass ledger to an immutable pilot manifest",
    )
    release.add_argument("ledger", type=Path)
    release.add_argument("manifest", type=Path)

    describe = commands.add_parser(
        "describe",
        help="show the immutable, privacy-safe input contract for one scenario",
    )
    describe.add_argument("ledger", type=Path)
    describe.add_argument("scenario_id")
    describe.add_argument("--corpus", required=True, type=Path)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "init":
            if args.ledger.exists():
                raise ValueError("refusing to overwrite an existing acceptance ledger")
            corpus = validate_routed_audio_corpus(args.corpus)
            ledger = new_acceptance_ledger(
                candidate_sha256=args.candidate_sha256,
                assistant_commit=args.assistant_commit,
                meetily_commit=args.meetily_commit,
                corpus_manifest_sha256=corpus.manifest_sha256,
                created_at_utc=utc_now(),
            )
            write_atomic(args.ledger, ledger)
        elif args.command == "describe":
            ledger = read_ledger(args.ledger)
            validate_acceptance_ledger(ledger)
            corpus = validate_routed_audio_corpus(args.corpus)
            if corpus.manifest_sha256 != ledger["corpus_manifest_sha256"]:
                raise ValueError("corpus manifest SHA-256 differs from acceptance ledger")
            scenario = next(
                (
                    item
                    for item in ledger["scenarios"]
                    if item["scenario_id"] == args.scenario_id
                ),
                None,
            )
            if scenario is None:
                raise ValueError(f"unknown acceptance scenario: {args.scenario_id}")
            source = next(
                (
                    item
                    for item in (*corpus.scenarios, *corpus.platform_sources)
                    if item.scenario_id == scenario["source_audio_scenario_id"]
                ),
                None,
            )
            if source is None:
                raise ValueError("scenario source audio is absent from the bound corpus")
            print(
                json.dumps(
                    {
                        "scenario_id": scenario["scenario_id"],
                        "kind": scenario["kind"],
                        "platform": scenario["platform"],
                        "lang": scenario["lang"],
                        "run_seed": scenario["seed"],
                        "source_audio_scenario_id": source.scenario_id,
                        "source_audio_file": source.audio_file,
                        "source_audio_sha256": source.sha256,
                        "category": source.category,
                        "acoustic_profile": source.acoustic_profile,
                        "expected_ask_slot": source.expected_ask_slot,
                        "required_checks": list(REQUIRED_CHECKS),
                    },
                    sort_keys=True,
                )
            )
            return 0
        elif args.command != "validate-release":
            ledger = read_ledger(args.ledger)
            if args.command in {"pass", "fail"}:
                record_acceptance_result(
                    ledger,
                    scenario_id=args.scenario_id,
                    passed=args.command == "pass",
                    completed_at_utc=utc_now(),
                    failure_stage=getattr(args, "stage", None),
                )
                write_atomic(args.ledger, ledger)

        if args.command == "validate-release":
            ledger = read_ledger(args.ledger)
            manifest = read_ledger(args.manifest)
            summary = validate_release_binding(ledger, manifest)
        else:
            summary = validate_acceptance_ledger(
                ledger,
                require_complete=getattr(args, "require_complete", False),
                require_all_passed=getattr(args, "require_all_passed", False),
            )
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(acceptance_summary_dict(summary), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
