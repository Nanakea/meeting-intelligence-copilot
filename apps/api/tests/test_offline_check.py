"""The normal verification gate must never install from the network."""

from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]
CHECK = REPO_ROOT / "scripts" / "check.ps1"
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap-dev.ps1"


def test_check_is_explicitly_offline_and_does_not_run_pip_install() -> None:
    source = CHECK.read_text(encoding="utf-8")
    assert "pnpm install --offline --frozen-lockfile" in source
    assert "pip install" not in source
    assert "bootstrap-dev.ps1" in source


def test_network_capable_setup_is_separate_and_explicit() -> None:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    assert "may contact configured Python and pnpm package registries" in source
    assert "pip install" in source
    assert "pnpm install --frozen-lockfile" in source
