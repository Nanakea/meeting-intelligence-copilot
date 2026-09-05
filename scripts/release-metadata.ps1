Set-StrictMode -Version Latest

function Get-ReleaseMetadata {
    param([string]$Root = (Split-Path -Parent $PSScriptRoot))
    $metadata = Get-Content -LiteralPath (Join-Path $Root "apps/api/app/release.json") -Raw | ConvertFrom-Json
    if ($metadata.schema_version -ne 1 -or $metadata.version -notmatch '^\d+\.\d+\.\d+$' -or $metadata.api_version -ne 13) {
        throw "Unsupported release metadata."
    }
    return $metadata
}

function Assert-ReleaseVersions {
    param([string]$Root, $Release)
    $jsonFiles = @("apps/desktop/frontend/package.json", "apps/desktop/frontend/src-tauri/tauri.conf.json")
    foreach ($relative in $jsonFiles) {
        $value = Get-Content -LiteralPath (Join-Path $Root $relative) -Raw | ConvertFrom-Json
        if ($value.version -ne $Release.version) { throw "Release version mismatch: $relative" }
    }
    foreach ($relative in @("apps/api/pyproject.toml", "apps/desktop/frontend/src-tauri/Cargo.toml")) {
        $value = Get-Content -LiteralPath (Join-Path $Root $relative) -Raw
        if ($value -notmatch '(?m)^version = "([^"\r\n]+)"\r?$' -or $Matches[1] -ne $Release.version) {
            throw "Release version mismatch: $relative"
        }
    }
}

function Get-ReleaseInputHashes {
    param([string]$Root)
    $hashes = [ordered]@{}
    foreach ($relative in @(
        "apps/api/app/release.json", "apps/api/requirements-dev.lock",
        "apps/api/requirements-packaging.txt", "apps/web/pnpm-lock.yaml",
        "apps/desktop/frontend/pnpm-lock.yaml", "apps/desktop/Cargo.lock"
    )) {
        $hashes[$relative] = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Root $relative)).Hash
    }
    return $hashes
}

function Assert-BackendProvenance {
    param([string]$Root, $Release, $Provenance, [string]$SourceCommit, [string]$ArtifactHash)
    if ($SourceCommit -notmatch '^[0-9a-f]{40}$' -or
        $Provenance.schema_version -ne 1 -or
        $Provenance.product -ne $Release.product -or
        $Provenance.api_version -ne $Release.api_version -or
        $Provenance.backend_version -ne $Release.version -or
        $Provenance.source_commit -ne $SourceCommit -or
        $Provenance.artifact.name -ne $Release.backend_artifact -or
        $Provenance.artifact.sha256 -ne $ArtifactHash) {
        throw "Backend artifact does not match the release source."
    }
    $expected = Get-ReleaseInputHashes -Root $Root
    foreach ($key in $expected.Keys) {
        $actual = $Provenance.release_inputs.PSObject.Properties[$key]
        if ($null -eq $actual -or $actual.Value -ne $expected[$key]) {
            throw "Backend release-input hashes do not match the current checkout."
        }
    }
}
