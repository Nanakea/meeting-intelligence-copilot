import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_receipt_is_bound_to_exact_source_and_artifacts() -> None:
    script = load_script("create-internal-candidate-manifest")
    hashes = {"backend": "a" * 64}
    value = {
        "gate": "packaged_backend",
        "source_commit": "b" * 40,
        "artifacts": hashes,
        "status": "passed",
    }
    assert script.receipt_status(value, "packaged_backend", "b" * 40, hashes) == "passed"
    for changed in (
        {"source_commit": "c" * 40},
        {"artifacts": {"backend": "d" * 64}},
        {"gate": "source_checks"},
        {"status": "success"},
        {"raw_log": "private"},
    ):
        with pytest.raises(ValueError):
            script.receipt_status(value | changed, "packaged_backend", "b" * 40, hashes)
    value["status"] = "pending"
    assert script.receipt_status(value, "packaged_backend", "b" * 40, hashes) == "pending"


def test_source_scan_reports_credential_shapes_without_matching_placeholders() -> None:
    script = load_script("check-tracked-secrets")
    assert script.contains_credential(("ghp_" + "a" * 36).encode())
    assert script.contains_credential(("xoxb-" + "123-" * 12).encode())
    assert script.contains_credential(("-----BEGIN PRIVATE KEY-----\n" + "A" * 40).encode())
    assert not script.contains_credential(b"token.<token> ghp_placeholder $env:API_TOKEN")


def test_candidate_writer_preserves_missing_gates_and_refuses_overwrite() -> None:
    source = (ROOT / "scripts/create-internal-candidate-manifest.py").read_text(encoding="utf-8")
    assert 'gates[gate] = {"status": "pending"}' in source
    assert 'args.output.open("x"' in source
    assert 'all(g["status"] == "passed"' in source
    assert '"public_distribution_approved": False' in source
