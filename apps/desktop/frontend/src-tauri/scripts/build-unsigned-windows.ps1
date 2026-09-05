[CmdletBinding()]
param(
    [ValidateSet("nsis", "msi", "both")]
    [string]$Bundle = "nsis"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "Unsigned Windows installer builds must run on Windows."
}

$tauriRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$frontendRoot = (Resolve-Path -LiteralPath (Join-Path $tauriRoot "..")).Path
$workspaceRoot = (Resolve-Path -LiteralPath (Join-Path $frontendRoot "..")).Path
$expectedWorkspaceRoot = (Resolve-Path -LiteralPath (Join-Path $tauriRoot "..\..")).Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $workspaceRoot "../..")).Path
. (Join-Path $repoRoot "scripts/release-metadata.ps1")
$release = Get-ReleaseMetadata -Root $repoRoot
Assert-ReleaseVersions -Root $repoRoot -Release $release
# Set this before invoking Cargo, not merely when locating the resulting artifacts.
if ([string]::IsNullOrWhiteSpace($env:CARGO_TARGET_DIR)) {
    $env:CARGO_TARGET_DIR = $release.default_cargo_target
}
if ($workspaceRoot -ne $expectedWorkspaceRoot) {
    throw "Could not resolve the Meeting Intelligence Copilot workspace safely."
}

$requiredSidecars = @(
    "ffmpeg-x86_64-pc-windows-msvc.exe",
    "llama-helper-x86_64-pc-windows-msvc.exe",
    "meeting-intelligence-backend-x86_64-pc-windows-msvc.exe"
)
$binaryRoot = Join-Path $tauriRoot "binaries"
foreach ($name in $requiredSidecars) {
    $path = Join-Path $binaryRoot $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required real sidecar is missing: $name"
    }
    if ((Get-Item -LiteralPath $path).Length -le 0) {
        throw "Required sidecar is empty: $name"
    }
}

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm is required."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required to bind the installer to reviewed source."
}
$worktreeStatus = (& git -C $workspaceRoot status --porcelain=v1 --untracked-files=all | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Product source status could not be inspected."
}
if (-not [string]::IsNullOrWhiteSpace($worktreeStatus)) {
    throw "Refusing to build Windows installers from a dirty product worktree."
}
$sourceCommit = (& git -C $workspaceRoot rev-parse HEAD).Trim()
$backendProvenancePath = Join-Path $binaryRoot "meeting-intelligence-backend-provenance.json"
if (-not (Test-Path -LiteralPath $backendProvenancePath -PathType Leaf)) {
    throw "Reviewed Meeting Intelligence backend provenance is missing."
}
$backendProvenance = Get-Content -LiteralPath $backendProvenancePath -Raw | ConvertFrom-Json
$packagedBackendPath = Join-Path $binaryRoot "meeting-intelligence-backend-x86_64-pc-windows-msvc.exe"
$packagedBackendHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $packagedBackendPath).Hash
Assert-BackendProvenance -Root $repoRoot -Release $release -Provenance $backendProvenance -SourceCommit $sourceCommit -ArtifactHash $packagedBackendHash

$bundles = if ($Bundle -eq "both") { "nsis,msi" } else { $Bundle }
$configPath = Join-Path $tauriRoot "tauri.unsigned.conf.json"
$buildStarted = [DateTime]::UtcNow

Write-Host "Building unsigned Windows bundle(s): $bundles"
Write-Host "Updater artifacts are disabled for this build flavor."

Push-Location $frontendRoot
try {
    & pnpm exec tauri build --ci --no-sign --bundles $bundles --config $configPath
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri build failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

$targetRoot = [System.IO.Path]::GetFullPath($env:CARGO_TARGET_DIR)
$bundleRoot = Join-Path $targetRoot "release\bundle"
$extensions = if ($Bundle -eq "nsis") { @(".exe") } elseif ($Bundle -eq "msi") { @(".msi") } else { @(".exe", ".msi") }
$artifacts = Get-ChildItem -LiteralPath $bundleRoot -Recurse -File | Where-Object {
    $_.Extension -in $extensions -and $_.LastWriteTimeUtc -ge $buildStarted
}
if (-not $artifacts) {
    throw "Tauri reported success but produced no new installer artifact."
}

$newSignatures = Get-ChildItem -LiteralPath $bundleRoot -Recurse -File -Filter "*.sig" | Where-Object {
    $_.LastWriteTimeUtc -ge $buildStarted
}
if ($newSignatures) {
    throw "Unsigned build unexpectedly produced updater signature artifacts."
}

$records = foreach ($artifact in $artifacts) {
    $signature = Get-AuthenticodeSignature -LiteralPath $artifact.FullName
    if ($signature.Status -ne "NotSigned") {
        throw "Unsigned build produced an artifact with unexpected Authenticode status '$($signature.Status)': $($artifact.FullName)"
    }
    [pscustomobject]@{
        Path = $artifact.FullName
        Bytes = $artifact.Length
        SHA256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $artifact.FullName).Hash
        Authenticode = $signature.Status
    }
}

$records | Format-List

$sidecarRecords = [ordered]@{}
foreach ($name in $requiredSidecars) {
    $path = Join-Path $binaryRoot $name
    $sidecarRecords[$name] = [ordered]@{
        bytes = (Get-Item -LiteralPath $path).Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
        authenticode = (Get-AuthenticodeSignature -LiteralPath $path).Status.ToString()
    }
}
$artifactRecords = @($records | ForEach-Object {
    [ordered]@{
        name = [System.IO.Path]::GetFileName($_.Path)
        bytes = $_.Bytes
        sha256 = $_.SHA256
        authenticode = $_.Authenticode.ToString()
    }
})
$applicationPath = Join-Path $targetRoot "release\meeting-intelligence-desktop.exe"
if (-not (Test-Path -LiteralPath $applicationPath -PathType Leaf)) {
    throw "Tauri reported success but the release application is missing."
}
$applicationRecord = [ordered]@{
    name = "meeting-intelligence-desktop.exe"
    bytes = (Get-Item -LiteralPath $applicationPath).Length
    sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $applicationPath).Hash
    authenticode = (Get-AuthenticodeSignature -LiteralPath $applicationPath).Status.ToString()
}
$provenance = [ordered]@{
    schema_version = 1
    product = "meeting-intelligence-copilot"
    version = $release.version
    compatibility_api_version = $release.api_version
    release_inputs = Get-ReleaseInputHashes -Root $repoRoot
    source_commit = $sourceCommit
    backend_source_commit = $backendProvenance.source_commit
    backend_sha256 = $backendProvenance.artifact.sha256
    updater_artifacts_enabled = $false
    application = $applicationRecord
    artifacts = $artifactRecords
    sidecars = $sidecarRecords
}
$provenancePath = Join-Path $bundleRoot "precompany-build-provenance.json"
$provenanceTemporary = Join-Path $bundleRoot "precompany-build-provenance.$([Guid]::NewGuid().ToString('N')).tmp"
try {
    [System.IO.File]::WriteAllText(
        $provenanceTemporary,
        ($provenance | ConvertTo-Json -Depth 6),
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $provenanceTemporary -Destination $provenancePath -Force
}
finally {
    if (Test-Path -LiteralPath $provenanceTemporary) {
        Remove-Item -LiteralPath $provenanceTemporary -Force
    }
}
Write-Host "Pre-company build provenance: $provenancePath"
