"""Bind aggregate gate receipts to one candidate; absent evidence always stays pending."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATES = (
    "source_checks",
    "desktop_checks",
    "rust_tests",
    "packaged_backend",
    "fault_10000",
    "soak_four_hour",
    "routed_audio_108",
    "platform_paths_24",
    "sandbox_lifecycle",
    "accessibility",
    "dependency_audit",
    "secret_scan",
    "defender",
)
INPUTS = (
    "apps/api/app/release.json",
    "apps/api/requirements-dev.lock",
    "apps/api/requirements-packaging.txt",
    "apps/desktop/Cargo.lock",
    "apps/desktop/frontend/pnpm-lock.yaml",
    "apps/web/pnpm-lock.yaml",
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt_status(value: object, gate: str, commit: str, artifacts: dict) -> str:
    if not isinstance(value, dict) or set(value) != {
        "gate",
        "source_commit",
        "artifacts",
        "status",
    }:
        raise ValueError("Gate receipt has unsupported fields")
    if (
        value["gate"] != gate
        or value["source_commit"] != commit
        or value["artifacts"] != artifacts
    ):
        raise ValueError("Gate receipt does not match the candidate")
    if value["status"] not in {"passed", "failed", "pending"}:
        raise ValueError("Unsupported gate status")
    return value["status"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path)
    for kind in (
        "backend",
        "desktop",
        "nsis",
        "msi",
        "sbom",
        "corpus",
        "model_manifest",
        "ledger",
    ):
        parser.add_argument(f"--{kind.replace('_', '-')}", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite an immutable candidate manifest")
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        text=True,
    )
    if status.strip():
        raise ValueError("Candidate manifest requires a clean source checkout")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    release = json.loads(
        (ROOT / "apps/api/app/release.json").read_text(encoding="utf-8")
    )
    artifacts = {
        kind: digest(getattr(args, kind))
        for kind in (
            "backend",
            "desktop",
            "nsis",
            "msi",
            "sbom",
            "corpus",
            "model_manifest",
            "ledger",
        )
    }
    gates = {}
    for gate in GATES:
        path = args.evidence_dir / f"{gate}.json" if args.evidence_dir else None
        if path is None or not path.is_file():
            gates[gate] = {"status": "pending"}
        else:
            value = json.loads(path.read_text(encoding="utf-8"))
            gates[gate] = {
                "status": receipt_status(value, gate, commit, artifacts),
                "receipt_sha256": digest(path),
            }
    result = {
        "schema_version": 1,
        "product_version": release["version"],
        "compatibility_api_version": release["api_version"],
        "source_commit": commit,
        "candidate_kind": "unsigned_internal_candidate",
        "public_distribution_approved": False,
        "human_validated": False,
        "engineering_accepted": all(g["status"] == "passed" for g in gates.values()),
        "inputs": {name: digest(ROOT / name) for name in INPUTS},
        "artifacts": artifacts,
        "gates": gates,
        "external_gates": {
            key: "pending"
            for key in ("company_approval", "tenants", "signing", "human_pilot")
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as destination:
        json.dump(result, destination, indent=2, sort_keys=True)
        destination.write("\n")
    print(
        json.dumps(
            {
                "manifest_sha256": digest(args.output),
                "engineering_accepted": result["engineering_accepted"],
            }
        )
    )


if __name__ == "__main__":
    main()
