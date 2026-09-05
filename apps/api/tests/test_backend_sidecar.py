"""Backend sidecar entry point and internal-candidate build safety tests."""

from __future__ import annotations

import asyncio
import pathlib
import subprocess

import pytest
from scripts.packaged_bilingual_acceptance import receive_snapshot_at_least

from app import sidecar_main

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-backend-sidecar.ps1"
ACCEPTANCE_SCRIPT = REPO_ROOT / "scripts" / "test-backend-sidecar.ps1"
BILINGUAL_WRAPPER = REPO_ROOT / "scripts" / "run-packaged-acceptance.ps1"
BILINGUAL_RUNNER = REPO_ROOT / "apps" / "api" / "scripts" / "packaged_bilingual_acceptance.py"
ACCEPTANCE_CHECKLIST = REPO_ROOT / "docs" / "PACKAGED_ACCEPTANCE_CHECKLIST.md"
PACKAGING_REQUIREMENTS = REPO_ROOT / "apps" / "api" / "requirements-packaging.txt"
RUNTIME_REQUIREMENTS = REPO_ROOT / "apps" / "api" / "requirements-dev.lock"
ARTIFACT = (
    REPO_ROOT
    / "dist"
    / "backend-sidecar"
    / "meeting-intelligence-backend-x86_64-pc-windows-msvc.exe"
)


def test_sidecar_entrypoint_always_binds_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_run(app, **kwargs) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(sidecar_main.uvicorn, "run", fake_run)
    assert sidecar_main.main({sidecar_main.BACKEND_PORT_ENV: "8123"}) == 0
    assert calls == [
        {
            "app": sidecar_main.app,
            "host": "127.0.0.1",
            "port": 8123,
            "access_log": False,
        }
    ]


@pytest.mark.parametrize("value", ["0", "65536", "not-a-port"])
def test_sidecar_port_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        sidecar_main.backend_port({sidecar_main.BACKEND_PORT_ENV: value})


def test_internal_candidate_build_dry_run_creates_no_binary() -> None:
    before = ARTIFACT.exists()
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BUILD_SCRIPT),
            "-DryRun",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "no dependency installation, build, or desktop copy" in result.stdout
    assert ARTIFACT.exists() is before


def test_sidecar_build_outputs_are_gitignored() -> None:
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "dist/" in ignore
    assert "tmp/" in ignore


def test_packaging_toolchain_is_pinned_and_hashed() -> None:
    requirements = PACKAGING_REQUIREMENTS.read_text(encoding="utf-8")
    package_lines = [line for line in requirements.splitlines() if "==" in line]

    assert "pyinstaller==6.21.0" in requirements
    assert len(package_lines) == 7
    assert requirements.count("--hash=sha256:") == len(package_lines)

    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert '$expectedPyInstallerVersion = "6.21.0"' in build_script
    assert str(RUNTIME_REQUIREMENTS.name) in build_script
    assert "--no-index --require-hashes" in build_script
    assert "importlib.metadata" in build_script
    assert "PYTHONHASHSEED" in build_script
    assert "SOURCE_DATE_EPOCH" in build_script
    assert "build-provenance.json" in build_script
    assert "api_version = $release.api_version" in build_script
    assert "backend_version = $release.version" in build_script
    assert "$releaseInputHashes = Get-ReleaseInputHashes" in build_script
    assert "Assert-ReleaseCheckout -Root $repo -SourceCommit $sourceCommit" in build_script
    assert "runtime_lock_sha256" in build_script
    assert "packaging_lock_sha256" in build_script
    assert "Get-AuthenticodeSignature" in build_script
    assert "Get-ReleaseSourceStatus -Root $repo" in build_script
    assert "Refusing to build a backend sidecar from a dirty product worktree" in build_script
    assert "Refusing to replace an artifact outside" in build_script
    assert "pip install" not in build_script.split("if ($DryRun)", maxsplit=1)[0]


def test_packaged_acceptance_harness_guards_runtime_contract() -> None:
    script = ACCEPTANCE_SCRIPT.read_text(encoding="utf-8")

    for required in [
        "Assert-PortAvailable",
        "MEETING_INTELLIGENCE_BACKEND_PORT",
        "MEETING_INTELLIGENCE_TOKEN",
        "MEETING_INTELLIGENCE_CACHE_DIR",
        "X-Meeting-Intelligence-Token",
        "RandomNumberGenerator",
        ".GetBytes($tokenBytes)",
        "/health/compatibility",
        "meeting-intelligence-copilot",
        "Get-NetTCPConnection",
        "ClientWebSocket",
        'SetRequestHeader("Origin", "http://tauri.localhost")',
        'AddSubProtocol("token.$capabilityToken")',
        "/ingest/meetily/",
        "/meeting/",
        "state_version",
        'contains "transcript"',
        "taskkill /PID $backendProcess.Id /T /F",
        "Operational logs exposed synthetic transcript content",
        "Operational logs exposed the local capability token",
        "did not fail closed with an invalid token",
        "Operational log exceeded the",
        "Cold start ms:",
        "Authenticode:",
        "bounded temporary acceptance logs removed",
        "Refusing to remove an unexpected acceptance-log path",
        "Refusing to remove an unexpected acceptance-cache path",
    ]:
        assert required in script

    assert "127.0.0.1" in script
    assert "0.0.0.0" not in script
    assert "::Fill" not in script
    assert "ToHexString" not in script
    assert "pip install" not in script


def test_packaged_acceptance_checklist_preserves_release_boundary() -> None:
    current = ACCEPTANCE_CHECKLIST.read_text(encoding="utf-8")
    assert "# 0.6.2 Packaged Acceptance" in current
    assert "**pending**" in current
    assert "Preparing a Sandbox package does not mean executing it passed" in current
    checklist = (
        ACCEPTANCE_CHECKLIST.parent / "history" / "PACKAGED_ACCEPTANCE_THROUGH_0.6.1.md"
    ).read_text(encoding="utf-8")

    assert "API v13 / 0.6.1 JA/EN/KO pilot source" in checklist
    assert "23DEEEF91EB7BF53C1D747242AF1A2037384D265F6EC46D696A8F0211470B31F" in checklist
    assert "76436b04e5a1b023173ea3f800d1d0eef6313e89" in checklist
    assert "E1727BAEFCE29AF66C4ADA6234C1E85051A9F8B20125C0579B8F3B525D7462EB" in checklist
    assert "065A77CFCC6CDF1DED7555D14AD9B76740988C5290F72BF2A3ABB08EEAEE59D7" in checklist
    assert "meetily-v0.5.0-sandbox-20260823" in checklist
    assert "DPAPI-encrypted local context indexing" in checklist
    assert "GUI/audio gates remain open" in checklist
    assert "public/end-user distribution: **not ready**" in checklist
    assert "Superseded historical API v2 candidate" in checklist
    assert "API version `13`" in checklist
    assert "bounded restart/backoff" in checklist
    assert "signed installer" in checklist
    assert "human-speech" in checklist


def test_packaged_bilingual_harness_is_local_authenticated_and_bounded() -> None:
    wrapper = BILINGUAL_WRAPPER.read_text(encoding="utf-8")
    runner = BILINGUAL_RUNNER.read_text(encoding="utf-8")

    assert "packaged_bilingual_acceptance.py" in wrapper
    assert "--max-log-bytes" in wrapper
    assert "127.0.0.1" in runner
    assert "0.0.0.0" not in runner
    assert "MEETING_INTELLIGENCE_TOKEN" in runner
    assert "MEETING_INTELLIGENCE_CACHE_DIR" in runner
    assert "MEETING_INTELLIGENCE_CONTEXT_DB" in runner
    assert "X-Meeting-Intelligence-Token" in runner
    assert "token.{token}" in runner
    assert '"state_version": sequence_id + 1' in runner
    assert '"backend_version": RELEASE["version"]' in runner
    assert '"transcript" not in snapshot' in runner
    assert "remove_acceptance_tree" in runner
    assert "assert_context_cache_encrypted" in runner
    assert '"/context/connectors"' in runner
    assert '"/context/search"' in runner
    assert '"/issues/drafts/submit"' in runner
    assert '"https://erp.invalid/odata"' in runner
    assert "Credential Manager" in runner
    assert "manual-work-en.jsonl" in runner
    assert "owner-answer-ja.jsonl" in runner
    assert '== "倉庫側"' in runner
    assert '== "出荷作業の遅延"' in runner
    assert '== "affected_scope"' in runner
    assert "max_bytes" in runner
    assert "taskkill" in runner
    assert "?lang=" not in runner
    assert "ollama" not in runner.lower()
    assert "example.com" not in runner.lower()


def test_packaged_bilingual_harness_skips_initial_reset_snapshot() -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.messages = iter(
                [
                    '{"meeting_id":"meeting-intel-test","version":0}',
                    '{"meeting_id":"meeting-intel-test","version":1}',
                ]
            )

        async def recv(self) -> str:
            return next(self.messages)

    snapshot = asyncio.run(receive_snapshot_at_least(FakeWebSocket(), expected_version=1))

    assert snapshot["version"] == 1
