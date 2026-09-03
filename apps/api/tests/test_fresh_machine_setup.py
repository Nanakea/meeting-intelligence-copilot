from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]
CHECKER = REPO_ROOT / "scripts" / "check-local-prereqs.ps1"
GUIDE = REPO_ROOT / "docs" / "FRESH_MACHINE_SETUP.md"
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap-dev.ps1"
OFFLINE_BOOTSTRAP_TEST = REPO_ROOT / "scripts" / "test-python-offline-bootstrap.ps1"
RUNTIME_LOCK = REPO_ROOT / "apps" / "api" / "requirements-dev.lock"


def test_prerequisite_checker_is_read_only_and_offline() -> None:
    source = CHECKER.read_text(encoding="utf-8")
    lowered = source.lower()

    assert "invoke-webrequest" not in lowered
    assert "invoke-restmethod" not in lowered
    assert "start-bitstransfer" not in lowered
    assert "winget install" not in lowered
    assert "pip install" not in lowered
    assert "pnpm install" not in lowered
    assert "cargo install" not in lowered
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "meeting-intelligence-backend-x86_64-pc-windows-msvc.exe" in source
    assert "llama-helper-x86_64-pc-windows-msvc.exe" in source
    assert "ffmpeg-x86_64-pc-windows-msvc.exe" in source
    assert (
        "FA8C7E4BE262CEF897DD747CB28E1C84D5B2F7BB8F2A234286CDB0D3C36E0C48"
        in source
    )
    assert "Get-FileHash -Algorithm SHA256" in source
    assert "Superseded API v2 backend" in source


def test_fresh_machine_guide_keeps_setup_and_offline_verification_separate() -> None:
    source = GUIDE.read_text(encoding="utf-8")

    assert "Network-capable setup" in source
    assert "Offline verification" in source
    assert "scripts\\check-local-prereqs.ps1" in source
    assert "--offline --frozen-lockfile" in source
    assert "requirements-dev.lock" in source
    assert "test-python-offline-bootstrap.ps1" in source
    assert "No cloud AI or Ollama is required" in source
    assert "apps\\desktop" in source
    assert "no second repository or linked worktree is required" in source
    assert "MEETILY_ROOT" in source  # Compatibility input only.
    assert "rejects the known superseded API v2 backend" in source
    assert "superseded local API v3 backend candidate" in source
    assert "71E66B6AE8D1B57B0C34B521B8396C555CBEEB3E9DB0E213889E83531332DFB8" in source
    assert "F8506D3049FDA89B07CEA6F09D455394132F88F349CD4E42920277BF88ACECFF" in source
    assert "6BB29896D3E6B036014259D5983A8EF6273116016CC0530C198E50E1EE1A80AF" in source
    assert "$env:CARGO_TARGET_DIR" in source
    assert "refuses a dirty product worktree" in source
    assert "never" in source.lower()


def test_python_runtime_and_dev_dependencies_are_exact_and_hashed() -> None:
    requirements = RUNTIME_LOCK.read_text(encoding="utf-8")
    package_lines = [
        line for line in requirements.splitlines() if line and not line.startswith(("#", " "))
    ]

    assert "fastapi==0.139.0" in requirements
    assert "pydantic==2.13.4" in requirements
    assert "websockets==16.1" in requirements
    assert "pytest==9.1.1" in requirements
    assert "ruff==0.15.20" in requirements
    assert "httpx2==2.12.0" in requirements
    assert "pypdf==6.16.1" in requirements
    assert len(package_lines) == 28
    assert requirements.count("--hash=sha256:") == len(package_lines)


def test_bootstrap_supports_a_network_disabled_fresh_environment() -> None:
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    offline_test = OFFLINE_BOOTSTRAP_TEST.read_text(encoding="utf-8")

    assert "--require-hashes" in bootstrap
    assert "--no-index" in bootstrap
    assert "--offline --frozen-lockfile" in bootstrap
    assert "pip install --upgrade" not in bootstrap
    assert "--no-index --require-hashes" in offline_test
    assert "python-offline-bootstrap-" in offline_test
    assert "Refusing to create a bootstrap environment outside" in offline_test
    assert "Remove-Item -LiteralPath $environmentFull -Recurse -Force" in offline_test
