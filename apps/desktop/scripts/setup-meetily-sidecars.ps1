[CmdletBinding()]
param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",
    [string]$TargetTriple,
    [string]$MeetingIntelligenceBackendPath,
    [string]$CargoTargetDirectory = $env:CARGO_TARGET_DIR,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$helperManifest = Join-Path $repoRoot "llama-helper\Cargo.toml"

if (-not (Test-Path -LiteralPath $helperManifest -PathType Leaf)) {
    throw "llama-helper source was not found at $helperManifest"
}

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "cargo is required to build the real llama-helper sidecar."
}

if ([string]::IsNullOrWhiteSpace($TargetTriple)) {
    $hostLine = (& rustc -vV | Select-String -Pattern '^host:\s+(.+)$').Matches
    if ($LASTEXITCODE -ne 0 -or $hostLine.Count -ne 1) {
        throw "Unable to determine the Rust host target from 'rustc -vV'."
    }
    $TargetTriple = $hostLine[0].Groups[1].Value.Trim()
}

if ($TargetTriple -notmatch 'windows') {
    throw "This PowerShell setup currently supports Windows targets only; got '$TargetTriple'."
}
if ([string]::IsNullOrWhiteSpace($MeetingIntelligenceBackendPath)) {
    throw @"
An explicitly accepted Meeting Intelligence API v13 artifact is required.
Build and test it in the product repository, then rerun with:
  -MeetingIntelligenceBackendPath <path-to-meeting-intelligence-backend.exe>
"@
}

$profile = $Configuration.ToLowerInvariant()
$cargoArgs = @("build", "-p", "llama-helper")
if ($Configuration -eq "Release") {
    $cargoArgs += "--release"
}

if ([string]::IsNullOrWhiteSpace($CargoTargetDirectory)) {
    $cargoTargetRoot = Join-Path $repoRoot "target"
} elseif ([System.IO.Path]::IsPathRooted($CargoTargetDirectory)) {
    $cargoTargetRoot = [System.IO.Path]::GetFullPath($CargoTargetDirectory)
} else {
    $cargoTargetRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $repoRoot $CargoTargetDirectory)
    )
}
$source = Join-Path $cargoTargetRoot "$profile\llama-helper.exe"
$destinationDirectory = Join-Path $repoRoot "frontend\src-tauri\binaries"
$destination = Join-Path $destinationDirectory "llama-helper-$TargetTriple.exe"
$backendDestination = Join-Path $destinationDirectory "meeting-intelligence-backend-$TargetTriple.exe"
$backendProvenanceDestination = Join-Path $destinationDirectory "meeting-intelligence-backend-provenance.json"

Write-Host "Building the real llama-helper sidecar from local source:"
Write-Host "  cargo $($cargoArgs -join ' ')"
Write-Host "Cargo target root: $cargoTargetRoot"
Write-Host "Target path: $destination"
Write-Host "Meeting Intelligence source: $MeetingIntelligenceBackendPath"
Write-Host "Meeting Intelligence target: $backendDestination"

if ($DryRun) {
    Write-Host "Dry run only; no build or copy was performed."
    exit 0
}

$previousCargoTargetDirectory = [Environment]::GetEnvironmentVariable(
    "CARGO_TARGET_DIR",
    "Process"
)
[Environment]::SetEnvironmentVariable("CARGO_TARGET_DIR", $cargoTargetRoot, "Process")
Push-Location $repoRoot
try {
    & cargo @cargoArgs
    if ($LASTEXITCODE -ne 0) {
        throw "llama-helper build failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
    [Environment]::SetEnvironmentVariable(
        "CARGO_TARGET_DIR",
        $previousCargoTargetDirectory,
        "Process"
    )
}

if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Cargo reported success, but the real helper was not found at $source"
}

# Exercise the helper's real stdin/stdout protocol before copying it into Tauri's externalBin path.
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $source
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardInput = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true

$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $startInfo
if (-not $process.Start()) {
    throw "Failed to start the built llama-helper for its protocol smoke test."
}

$process.StandardInput.WriteLine('{"type":"ping"}')
$process.StandardInput.WriteLine('{"type":"shutdown"}')
$process.StandardInput.Close()
$stdout = $process.StandardOutput.ReadToEnd()
$stderr = $process.StandardError.ReadToEnd()

if (-not $process.WaitForExit(10000)) {
    $process.Kill($true)
    throw "The built llama-helper did not stop after the shutdown smoke request."
}

if ($process.ExitCode -ne 0) {
    throw "The built llama-helper smoke test failed with exit code $($process.ExitCode): $stderr"
}

$responseTypes = @(
    $stdout -split "`r?`n" |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        ForEach-Object { (ConvertFrom-Json -InputObject $_).type }
)
if ($responseTypes.Count -ne 2 -or $responseTypes[0] -ne "pong" -or $responseTypes[1] -ne "goodbye") {
    throw "Unexpected llama-helper smoke responses: $stdout"
}

New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
Copy-Item -LiteralPath $source -Destination $destination -Force

$artifact = Get-Item -LiteralPath $destination
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $destination
Write-Host "Real llama-helper sidecar is ready (ignored build output)."
Write-Host "  Path: $($artifact.FullName)"
Write-Host "  Bytes: $($artifact.Length)"
Write-Host "  SHA-256: $($hash.Hash)"

$backendSource = (Resolve-Path -LiteralPath $MeetingIntelligenceBackendPath).Path
if (-not (Test-Path -LiteralPath $backendSource -PathType Leaf)) {
    throw "Meeting Intelligence backend artifact is not a real file: $backendSource"
}
$backendProvenanceSource = Join-Path (Split-Path -Parent $backendSource) "build-provenance.json"
if (-not (Test-Path -LiteralPath $backendProvenanceSource -PathType Leaf)) {
    throw "Meeting Intelligence backend build provenance is missing: $backendProvenanceSource"
}
$backendProvenance = Get-Content -LiteralPath $backendProvenanceSource -Raw | ConvertFrom-Json
$backendSourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $backendSource).Hash
if (
    $backendProvenance.schema_version -ne 1 -or
    $backendProvenance.product -ne "meeting-intelligence-copilot" -or
    $backendProvenance.api_version -ne 13 -or
    $backendProvenance.backend_version -ne "0.6.1" -or
    $backendProvenance.artifact.sha256 -ne $backendSourceHash
) {
    throw "Meeting Intelligence backend provenance does not match the accepted API v13 artifact."
}
Copy-Item -LiteralPath $backendSource -Destination $backendDestination -Force
Copy-Item -LiteralPath $backendProvenanceSource -Destination $backendProvenanceDestination -Force

$backendArtifact = Get-Item -LiteralPath $backendDestination
$backendHash = Get-FileHash -Algorithm SHA256 -LiteralPath $backendDestination
Write-Host "Real Meeting Intelligence backend sidecar is ready (ignored build output)."
Write-Host "  Path: $($backendArtifact.FullName)"
Write-Host "  Bytes: $($backendArtifact.Length)"
Write-Host "  SHA-256: $($backendHash.Hash)"
Write-Host "  Source commit: $($backendProvenance.source_commit)"
Write-Host "FFmpeg remains managed and verified by frontend/src-tauri/build/ffmpeg.rs during Cargo builds."
