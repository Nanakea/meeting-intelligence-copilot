[CmdletBinding()]
param(
    [string]$ArtifactPath,
    [ValidateRange(1, 65535)]
    [int]$Port = 8772,
    [ValidateRange(1, 120)]
    [int]$StartupTimeoutSeconds = 20,
    [ValidateRange(1024, 10485760)]
    [int]$MaxLogBytes = 1048576,
    [switch]$KeepLogs
)

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo "apps\api\.venv\Scripts\python.exe"
$runner = Join-Path $repo "apps\api\scripts\packaged_bilingual_acceptance.py"
if ([string]::IsNullOrWhiteSpace($ArtifactPath)) {
    $ArtifactPath = Join-Path $repo "dist\backend-sidecar\meeting-intelligence-backend-x86_64-pc-windows-msvc.exe"
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Product API virtual environment is missing: $python"
}
if (-not (Test-Path -LiteralPath $ArtifactPath -PathType Leaf)) {
    throw "Packaged backend artifact is missing: $ArtifactPath"
}

$arguments = @(
    "-X", "utf8",
    $runner,
    "--artifact", (Resolve-Path -LiteralPath $ArtifactPath).Path,
    "--port", $Port,
    "--startup-timeout", $StartupTimeoutSeconds,
    "--max-log-bytes", $MaxLogBytes
)
if ($KeepLogs) {
    $arguments += "--keep-logs"
}

& $python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Packaged JA/EN/KO acceptance failed with exit code $LASTEXITCODE"
}
