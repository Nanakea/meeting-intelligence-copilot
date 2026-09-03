"""Static guardrails for the Windows local V1 launcher."""

from pathlib import Path

LAUNCHER = Path(__file__).parents[3] / "scripts" / "start-local-v1.ps1"


def test_launcher_stays_loopback_and_checks_health() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert '"127.0.0.1"' in source
    assert "0.0.0.0" not in source
    assert "/health" in source
    assert "app.api.main:app" in source
    assert "MEETING_INTELLIGENCE_TOKEN" in source
    assert "X-Meeting-Intelligence-Token" in source
    assert "RandomNumberGenerator" in source
    assert ".GetBytes($bytes)" in source
    assert "::Fill" not in source
    assert "ToHexString" not in source
    assert "api_version -eq 13" in source
    assert "capability_auth -eq $true" in source
    assert "value not displayed" in source
    assert "Write-Host $capabilityToken" not in source
    assert "-ExecutionPolicy Bypass -File scripts\\bootstrap-dev.ps1" in source
    assert "-ExecutionPolicy Bypass -File scripts\\check.ps1" in source


def test_launcher_uses_pinned_meetily_build_environment_without_persisting_it() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "$env:MEETILY_ROOT" in source
    assert '"..\\apps\\desktop"' in source
    assert "$env:LIBCLANG_PATH" in source
    assert '"E:\\work\\LLVM\\bin"' not in source
    assert '"RUSTUP_TOOLCHAIN", "stable"' in source
    assert '"LIBCLANG_PATH", $LibClangPath' in source
    assert '"CMAKE_GENERATOR", "Visual Studio 17 2022"' in source
    assert '"Process")' in source
    assert "llama-helper-x86_64-pc-windows-msvc.exe" in source
    assert "scripts\\setup-meetily-sidecars.ps1" in source
