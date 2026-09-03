[CmdletBinding()]
param(
    [string]$Wheelhouse,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$api = Join-Path $repo "apps\api"
$web = Join-Path $repo "apps\web"
$py = Join-Path $api ".venv\Scripts\python.exe"
$requirements = Join-Path $api "requirements-dev.lock"

# This is the only product setup entrypoint that runs `pip install`; the
# normal verification gate never installs packages.

Write-Warning @"
This explicit setup command may contact configured Python and pnpm package registries.
It is separate from scripts\check.ps1, which is a network-disabled verification gate.
"@

if ($Offline -and [string]::IsNullOrWhiteSpace($Wheelhouse)) {
    throw "-Offline requires -Wheelhouse so Python installation cannot contact a registry."
}

if (-not (Test-Path $py)) {
    py -3.12 -m venv (Join-Path $api ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python 3.12 virtual environment."
    }
}

if ($Wheelhouse) {
    $resolvedWheelhouse = (Resolve-Path -LiteralPath $Wheelhouse).Path
    $pipArguments = @(
        "-m", "pip", "install", "--no-index", "--require-hashes",
        "--find-links", $resolvedWheelhouse, "-r", $requirements
    )
}
else {
    $pipArguments = @("-m", "pip", "install", "--require-hashes", "-r", $requirements)
}

& $py @pipArguments
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the hash-locked API dependencies."
}

Push-Location $web
try {
    if ($Offline) {
        pnpm install --offline --frozen-lockfile
    }
    else {
        pnpm install --frozen-lockfile
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install web dependencies."
    }
}
finally {
    Pop-Location
}

Write-Host "Development dependencies are ready." -ForegroundColor Green
Write-Host "Run the network-disabled gate next:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\check.ps1"
