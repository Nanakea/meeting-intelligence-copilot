"""Behavioral release checks: execute the same PowerShell guards used by packaging."""

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from app.release import API_VERSION, VERSION

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "scripts/release-metadata.ps1"
INPUTS = (
    "apps/api/app/release.json",
    "apps/api/requirements-dev.lock",
    "apps/api/requirements-packaging.txt",
    "apps/web/pnpm-lock.yaml",
    "apps/desktop/frontend/pnpm-lock.yaml",
    "apps/desktop/Cargo.lock",
    "apps/api/pyproject.toml",
    "apps/desktop/frontend/package.json",
    "apps/desktop/frontend/src-tauri/tauri.conf.json",
    "apps/desktop/frontend/src-tauri/Cargo.toml",
)


def test_release_versions_and_cargo_lock_match() -> None:
    assert VERSION == "0.6.2"
    assert API_VERSION == 13
    for path in ("apps/api/pyproject.toml", "apps/desktop/frontend/src-tauri/Cargo.toml"):
        data = tomllib.loads((ROOT / path).read_text(encoding="utf-8"))
        assert data.get("project", data.get("package"))["version"] == VERSION
    for path in (
        "apps/desktop/frontend/package.json",
        "apps/desktop/frontend/src-tauri/tauri.conf.json",
    ):
        assert json.loads((ROOT / path).read_text(encoding="utf-8"))["version"] == VERSION
    lock = tomllib.loads((ROOT / "apps/desktop/Cargo.lock").read_text(encoding="utf-8"))
    desktop = next(p for p in lock["package"] if p["name"] == "meeting-intelligence-desktop")
    assert desktop["version"] == VERSION


@pytest.mark.parametrize(
    "fault", ["none", "version", "commit", "api", "hash", "lock", "missing_inputs"]
)
def test_packaging_provenance_fails_closed(tmp_path: Path, fault: str) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("Windows packaging requires PowerShell")
    for relative in INPUTS:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    # Derive a valid fixture first, then alter exactly one trust input.
    mutation = {
        "none": "",
        "version": "$p.backend_version = '0.6.0'",
        "commit": "$p.source_commit = ('b' * 40)",
        "api": "$p.api_version = 12",
        "hash": "$p.artifact.sha256 = ('d' * 64)",
        "lock": "$p.release_inputs.'apps/desktop/Cargo.lock' = ('d' * 64)",
        "missing_inputs": "$p.PSObject.Properties.Remove('release_inputs')",
    }[fault]
    command = f"""
$ErrorActionPreference = 'Stop'
. '{str(HELPER).replace("'", "''")}'
$root = '{str(tmp_path).replace("'", "''")}'
$r = Get-ReleaseMetadata -Root $root
Assert-ReleaseVersions -Root $root -Release $r
$p = [ordered]@{{
    schema_version = 1; product = $r.product; api_version = $r.api_version
    backend_version = $r.version; source_commit = ('a' * 40)
    release_inputs = Get-ReleaseInputHashes -Root $root
    artifact = @{{ name = $r.backend_artifact; sha256 = ('c' * 64) }}
}} | ConvertTo-Json -Depth 5 | ConvertFrom-Json
{mutation}
Assert-BackendProvenance -Root $root -Release $r -Provenance $p `
    -SourceCommit ('a' * 40) -ArtifactHash ('c' * 64)
"""
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        timeout=30,
    )
    assert (result.returncode == 0) == (fault == "none"), result.stderr.decode(errors="replace")


def test_readiness_rejects_misaligned_component(tmp_path: Path) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("Windows readiness requires PowerShell")
    for relative in INPUTS:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    package = tmp_path / "apps/desktop/frontend/package.json"
    package.write_text(json.dumps({"version": "0.6.0"}), encoding="utf-8")
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"$ErrorActionPreference='Stop'; . '{HELPER}'; "
            f"$r=Get-ReleaseMetadata -Root '{tmp_path}'; "
            f"Assert-ReleaseVersions -Root '{tmp_path}' -Release $r",
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0


@pytest.mark.parametrize(
    "change", ["none", "tracked", "staged", "untracked", "revision", "line_endings"]
)
def test_end_of_build_rejects_changed_checkout(tmp_path: Path, change: str) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("Windows packaging requires PowerShell")

    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "--quiet")
    git("config", "user.name", "Release guard test")
    git("config", "user.email", "test@example.invalid")
    source = tmp_path / "source.txt"
    source.write_text("initial\n", encoding="utf-8")
    (tmp_path / ".gitattributes").write_text("*.txt text eol=lf\n", encoding="utf-8")
    git("add", "source.txt", ".gitattributes")
    git("commit", "--quiet", "-m", "fixture")
    commit = git("rev-parse", "HEAD")
    if change in {"tracked", "staged"}:
        source.write_text("changed\n", encoding="utf-8")
        if change == "staged":
            git("add", "source.txt")
    elif change == "line_endings":
        source.write_bytes(b"initial\r\n")
    elif change == "untracked":
        (tmp_path / "new.txt").write_text("unreviewed\n", encoding="utf-8")
    elif change == "revision":
        git("commit", "--quiet", "--allow-empty", "-m", "another revision")
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"$ErrorActionPreference='Stop'; . '{HELPER}'; "
            f"Assert-ReleaseCheckout -Root '{tmp_path}' -SourceCommit '{commit}'",
        ],
        capture_output=True,
        timeout=30,
    )
    assert (result.returncode == 0) == (change in {"none", "line_endings"})
