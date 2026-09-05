from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check-precompany-readiness.ps1"
GUIDE = REPO_ROOT / "docs" / "FIRST_WORKDAY_SETUP.md"


def test_precompany_readiness_is_read_only_fail_closed_and_source_bound() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "--untracked-files=all" in source
    assert "backendProvenance.source_commit -eq $sourceCommit" in source
    assert "bundleProvenance.source_commit -eq $sourceCommit" in source
    assert "bundleProvenance.backend_source_commit -eq $sourceCommit" in source
    assert 'source_commit = $sourceCommit' in source
    assert "assistant_commit =" not in source
    assert "meetily_commit =" not in source
    assert "sandboxManifest.candidate_sha256 -eq $nsisRecord.sha256" in source
    assert "Get-AuthenticodeSignature" in source
    assert "pending_company" in source
    assert '"netsuite_acceptance"' in source
    assert '"teams_rsc_acceptance"' in source
    assert '"slack_acceptance"' in source
    assert '"dynamics_365_acceptance"' not in source
    assert "ready_for_company_data" in source
    assert "Refusing to overwrite" in source
    assert "Start-MpScan" not in source
    assert "Remove-Item -Recurse" not in source
    assert "access_token" not in source.lower()
    assert "transcript" not in source.lower()


def test_first_workday_guide_preserves_company_only_gates() -> None:
    guide = GUIDE.read_text(encoding="utf-8")

    for required in [
        "Before joining",
        "First day with company IT",
        "Before every real meeting",
        "API v13",
        "organization-signed",
        "OneDrive",
        "SharePoint",
        "NetSuite",
        "meeting-only",
        "Do not use company data",
    ]:
        assert required in guide
