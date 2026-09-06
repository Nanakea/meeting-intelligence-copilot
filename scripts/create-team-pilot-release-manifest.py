"""Create the immutable signed-team-pilot manifest after every gate passes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.evals.pilot_acceptance import validate_acceptance_ledger  # noqa: E402
from app.release import API_VERSION, VERSION  # noqa: E402
from app.evals.team_pilot_release import (  # noqa: E402
    validate_connector_acceptance,
    validate_human_pilot_summary,
)

ARTIFACT_ARGUMENTS = {
    "backend_sidecar": "backend",
    "meetily_executable": "meetily_exe",
    "nsis_installer": "nsis",
    "msi_installer": "msi",
}
LOCK_FILES = {
    "assistant_api_runtime": Path("apps/api/requirements-dev.lock"),
    "assistant_api_packaging": Path("apps/api/requirements-packaging.txt"),
    "assistant_web": Path("apps/web/pnpm-lock.yaml"),
    "meetily_rust": Path("Cargo.lock"),
    "meetily_frontend": Path("frontend/pnpm-lock.yaml"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} could not be read as JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def git_value(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def require_clean_tracked_source(root: Path, label: str) -> str:
    if git_value(root, "status", "--porcelain=v1", "--untracked-files=no"):
        raise ValueError(
            f"{label} has tracked changes; freeze and commit before manifesting"
        )
    commit = git_value(root, "rev-parse", "HEAD")
    if len(commit) != 40:
        raise ValueError(f"{label} source commit is not exact")
    return commit


def authenticode(path: Path) -> dict[str, str]:
    command = (
        "$signature = Get-AuthenticodeSignature -LiteralPath $args[0]; "
        "[pscustomobject]@{Status=$signature.Status.ToString(); "
        "Subject=$signature.SignerCertificate.Subject; "
        "Thumbprint=$signature.SignerCertificate.Thumbprint} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(result.stdout)
    if value.get("Status") != "Valid":
        raise ValueError(f"{path.name} does not have a valid Authenticode signature")
    if not value.get("Subject") or not value.get("Thumbprint"):
        raise ValueError(f"{path.name} signature identity is unavailable")
    return {
        "status": value["Status"],
        "signer_subject": value["Subject"],
        "certificate_thumbprint": value["Thumbprint"],
    }


def validate_defender_evidence(
    evidence: dict[str, Any], artifact_hashes: set[str]
) -> dict[str, Any]:
    expected = {
        "schema_version",
        "scanned_at_utc",
        "signature_version",
        "detections",
        "artifact_sha256",
    }
    if set(evidence) != expected or evidence["schema_version"] != 1:
        raise ValueError("Defender evidence schema is invalid")
    if evidence["detections"] != 0:
        raise ValueError("Defender evidence contains detections")
    if (
        not isinstance(evidence["signature_version"], str)
        or not evidence["signature_version"]
    ):
        raise ValueError("Defender signature version is missing")
    if not isinstance(evidence["scanned_at_utc"], str) or not evidence[
        "scanned_at_utc"
    ].endswith("Z"):
        raise ValueError("Defender scan timestamp is invalid")
    scanned_hashes = evidence["artifact_sha256"]
    if (
        not isinstance(scanned_hashes, list)
        or {str(value).upper() for value in scanned_hashes} != artifact_hashes
    ):
        raise ValueError("Defender evidence is not bound to every release artifact")
    return evidence


def write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as output:
            output.write(encoded)
        Path(temporary_name).replace(path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--meetily-root", type=Path, required=True)
    value.add_argument("--backend", type=Path, required=True)
    value.add_argument("--meetily-exe", type=Path, required=True)
    value.add_argument("--nsis", type=Path, required=True)
    value.add_argument("--msi", type=Path, required=True)
    value.add_argument("--corpus-manifest", type=Path, required=True)
    value.add_argument("--acceptance-ledger", type=Path, required=True)
    value.add_argument("--connector-acceptance", type=Path, required=True)
    value.add_argument("--human-pilot-summary", type=Path, required=True)
    value.add_argument("--defender-evidence", type=Path, required=True)
    value.add_argument("--output", type=Path, required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    assistant_commit = require_clean_tracked_source(REPO_ROOT, "assistant")
    meetily_commit = require_clean_tracked_source(args.meetily_root, "Meetily")

    artifacts = []
    artifact_hashes: set[str] = set()
    for role, argument in ARTIFACT_ARGUMENTS.items():
        path = Path(getattr(args, argument)).resolve(strict=True)
        digest = sha256_file(path)
        artifact_hashes.add(digest)
        artifacts.append(
            {
                "role": role,
                "filename": path.name,
                "bytes": path.stat().st_size,
                "sha256": digest,
                "authenticode": authenticode(path),
            }
        )
    candidate_hash = next(
        artifact["sha256"]
        for artifact in artifacts
        if artifact["role"] == "meetily_executable"
    )

    corpus_hash = sha256_file(args.corpus_manifest)
    ledger = read_json(args.acceptance_ledger, "platform acceptance ledger")
    summary = validate_acceptance_ledger(
        ledger, require_complete=True, require_all_passed=True
    )
    if ledger["candidate_sha256"].upper() != candidate_hash:
        raise ValueError("platform acceptance ledger targets a different executable")
    if ledger["corpus_manifest_sha256"].upper() != corpus_hash:
        raise ValueError("platform acceptance ledger targets a different corpus")

    connector = read_json(args.connector_acceptance, "connector acceptance")
    connector_summary = validate_connector_acceptance(connector)
    human = read_json(args.human_pilot_summary, "human pilot summary")
    human_summary = validate_human_pilot_summary(human)
    for label, evidence in (("connector", connector), ("human pilot", human)):
        if evidence["candidate_sha256"].upper() != candidate_hash:
            raise ValueError(f"{label} evidence targets a different executable")
    defender = validate_defender_evidence(
        read_json(args.defender_evidence, "Defender evidence"), artifact_hashes
    )

    lock_hashes = {}
    for label, relative in LOCK_FILES.items():
        root = args.meetily_root if label.startswith("meetily_") else REPO_ROOT
        lock_hashes[label] = sha256_file(root / relative)

    manifest = {
        "schema_version": 2,
        "candidate_kind": "signed_ja_en_team_pilot",
        "product_version": VERSION,
        "compatibility_api_version": API_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "public_distribution_approved": False,
        "source": {
            "assistant_commit": assistant_commit,
            "meetily_commit": meetily_commit,
            "dependency_locks": lock_hashes,
        },
        "artifacts": artifacts,
        "acceptance": {
            "routed_audio_corpus_manifest_sha256": corpus_hash,
            "platform_ledger_sha256": sha256_file(args.acceptance_ledger),
            "platform_scenarios_passed": summary.passed,
            "connector_evidence_sha256": sha256_file(args.connector_acceptance),
            "connector_summary": connector_summary,
            "human_pilot_evidence_sha256": sha256_file(args.human_pilot_summary),
            "human_pilot_summary": human_summary,
            "defender": defender,
        },
        "supported_configurations": {
            "operating_system": "Windows 11 x64",
            "languages": ["ja", "en"],
            "english_stt": "parakeet-tdt-0.6b-v3-int8",
            "japanese_stt": "Whisper large-v3-turbo-q5_0 (verified optional artifact)",
            "capture_model": "locally captured meeting audio",
            "connected_context": ["Microsoft 365", "Dynamics 365"],
        },
    }
    write_atomic(args.output, manifest)
    print(f"Created signed team-pilot manifest: {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
