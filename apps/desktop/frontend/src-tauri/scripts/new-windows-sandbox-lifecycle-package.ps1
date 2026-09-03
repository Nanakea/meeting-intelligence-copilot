[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BaselineInstaller,

    [Parameter(Mandatory = $true)]
    [string]$CandidateInstaller,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-fA-F]{7,40}$")]
    [string]$BaselineCommit,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-fA-F]{7,40}$")]
    [string]$CandidateCommit,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "Windows Sandbox lifecycle packaging requires Windows."
}

$baseline = (Resolve-Path -LiteralPath $BaselineInstaller).Path
$candidate = (Resolve-Path -LiteralPath $CandidateInstaller).Path
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
$tauriRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

if ($baseline -eq $candidate) {
    throw "Baseline and candidate installers must be different files."
}
foreach ($installer in @($baseline, $candidate)) {
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "Lifecycle installer is not a file."
    }
    if ((Get-AuthenticodeSignature -LiteralPath $installer).Status -ne "NotSigned") {
        throw "The internal lifecycle package accepts unsigned review installers only."
    }
}
if (
    (Get-FileHash -Algorithm SHA256 -LiteralPath $baseline).Hash -eq
    (Get-FileHash -Algorithm SHA256 -LiteralPath $candidate).Hash
) {
    throw "Baseline and candidate installers must have different hashes."
}
if (Test-Path -LiteralPath $output) {
    throw "Refusing to overwrite an existing lifecycle package directory."
}

$payloadDirectory = Join-Path $output "payload"
$resultsDirectory = Join-Path $output "results"
$scriptsDirectory = Join-Path $payloadDirectory "scripts"
$binariesDirectory = Join-Path $payloadDirectory "binaries"
$inputsDirectory = Join-Path $payloadDirectory "inputs"
foreach ($directory in @(
    $output,
    $payloadDirectory,
    $resultsDirectory,
    $scriptsDirectory,
    $binariesDirectory,
    $inputsDirectory
)) {
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
}

$baselineName = "baseline-setup.exe"
$candidateName = "candidate-setup.exe"
Copy-Item -LiteralPath $baseline -Destination (Join-Path $inputsDirectory $baselineName)
Copy-Item -LiteralPath $candidate -Destination (Join-Path $inputsDirectory $candidateName)

foreach ($scriptName in @(
    "test-windows-installer-lifecycle.ps1",
    "test-installed-meeting-intelligence-backend.ps1"
)) {
    Copy-Item `
        -LiteralPath (Join-Path $PSScriptRoot $scriptName) `
        -Destination (Join-Path $scriptsDirectory $scriptName)
}

foreach ($binaryName in @(
    "ffmpeg-x86_64-pc-windows-msvc.exe",
    "llama-helper-x86_64-pc-windows-msvc.exe",
    "meeting-intelligence-backend-x86_64-pc-windows-msvc.exe"
)) {
    $source = Join-Path (Join-Path $tauriRoot "binaries") $binaryName
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required reviewed sidecar is missing: $binaryName"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $binariesDirectory $binaryName)
}

$runner = @'
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = "C:\Lifecycle"
$resultRoot = "C:\LifecycleResults"
$resultPath = Join-Path $resultRoot "RESULT.json"
$outputPath = Join-Path $resultRoot "lifecycle-output.txt"
$startedAt = [DateTime]::UtcNow.ToString("o")
$exitCode = 1
foreach ($stalePath in @($resultPath, $outputPath)) {
    if (Test-Path -LiteralPath $stalePath -PathType Leaf) {
        [System.IO.File]::Delete($stalePath)
    }
}
try {
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $root "scripts\test-windows-installer-lifecycle.ps1") `
        -BaselineInstaller (Join-Path $root "inputs\baseline-setup.exe") `
        -CandidateInstaller (Join-Path $root "inputs\candidate-setup.exe") `
        -BackendAcceptanceScript (
            Join-Path $root "scripts\test-installed-meeting-intelligence-backend.ps1"
        ) *> $outputPath
    $exitCode = $LASTEXITCODE
}
catch {
    $exitCode = 1
}
finally {
    [ordered]@{
        schema_version = 1
        status = if ($exitCode -eq 0) { "pass" } else { "fail" }
        exit_code = $exitCode
        started_at_utc = $startedAt
        completed_at_utc = [DateTime]::UtcNow.ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
}

if ($exitCode -eq 0) {
    Write-Host "Meeting Intelligence Copilot installer lifecycle passed. You may close Windows Sandbox."
}
else {
    Write-Host "Meeting Intelligence Copilot installer lifecycle failed. Review lifecycle-output.txt."
}
'@
$runnerPath = Join-Path $payloadDirectory "run-in-sandbox.ps1"
[System.IO.File]::WriteAllText($runnerPath, $runner, [System.Text.UTF8Encoding]::new($false))

$escapedPayloadPath = [System.Security.SecurityElement]::Escape($payloadDirectory)
$escapedResultsPath = [System.Security.SecurityElement]::Escape($resultsDirectory)
$sandbox = @"
<Configuration>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>$escapedPayloadPath</HostFolder>
      <SandboxFolder>C:\Lifecycle</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
    <MappedFolder>
      <HostFolder>$escapedResultsPath</HostFolder>
      <SandboxFolder>C:\LifecycleResults</SandboxFolder>
      <ReadOnly>false</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <Networking>Disable</Networking>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <PrinterRedirection>Disable</PrinterRedirection>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <LogonCommand>
    <Command>powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File C:\Lifecycle\run-in-sandbox.ps1</Command>
  </LogonCommand>
</Configuration>
"@
$sandboxPath = Join-Path $output "run-lifecycle.wsb"
[System.IO.File]::WriteAllText($sandboxPath, $sandbox, [System.Text.UTF8Encoding]::new($false))

$manifest = [ordered]@{
    schema_version = 1
    baseline_commit = $BaselineCommit.ToLowerInvariant()
    candidate_commit = $CandidateCommit.ToLowerInvariant()
    baseline_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $baseline).Hash
    candidate_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $candidate).Hash
    sidecars = [ordered]@{}
    acceptance_files = [ordered]@{}
}
foreach ($binary in Get-ChildItem -LiteralPath $binariesDirectory -File | Sort-Object Name) {
    $manifest.sidecars[$binary.Name] = (Get-FileHash -Algorithm SHA256 -LiteralPath $binary.FullName).Hash
}
foreach ($acceptanceFile in @(
    (Join-Path $scriptsDirectory "test-windows-installer-lifecycle.ps1"),
    (Join-Path $scriptsDirectory "test-installed-meeting-intelligence-backend.ps1"),
    $runnerPath,
    $sandboxPath
)) {
    $outputPrefix = $output.TrimEnd("\") + "\"
    if (-not $acceptanceFile.StartsWith(
        $outputPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Acceptance file resolved outside the lifecycle package."
    }
    $relativeName = $acceptanceFile.Substring($outputPrefix.Length)
    $manifest.acceptance_files[$relativeName] = (
        Get-FileHash -Algorithm SHA256 -LiteralPath $acceptanceFile
    ).Hash
}
$manifest |
    ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $payloadDirectory "MANIFEST.json") -Encoding UTF8

Write-Host "Windows Sandbox lifecycle package created: $output" -ForegroundColor Green
Write-Host "Open run-lifecycle.wsb on a host with Windows Sandbox enabled."
