[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Wheelhouse
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$api = Join-Path $repo "apps\api"
$requirements = Join-Path $api "requirements-dev.lock"
$resolvedWheelhouse = (Resolve-Path -LiteralPath $Wheelhouse).Path
$scratchRoot = Join-Path $repo "tmp"
$environment = Join-Path $scratchRoot ("python-offline-bootstrap-" + [Guid]::NewGuid().ToString("N"))
$python = Join-Path $environment "Scripts\python.exe"

$scratchFull = [System.IO.Path]::GetFullPath($scratchRoot).TrimEnd('\') + '\'
$environmentFull = [System.IO.Path]::GetFullPath($environment)
if (-not $environmentFull.StartsWith($scratchFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to create a bootstrap environment outside the repository scratch directory."
}

try {
    py -3.12 -m venv $environment
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the temporary Python 3.12 environment."
    }

    & $python -m pip install --no-index --require-hashes `
        --find-links $resolvedWheelhouse -r $requirements
    if ($LASTEXITCODE -ne 0) {
        throw "Offline hash-locked dependency installation failed."
    }

    & $python -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "The fresh environment has inconsistent dependencies."
    }

    Push-Location $api
    try {
        & $python -m ruff check .
        if ($LASTEXITCODE -ne 0) { throw "Fresh-environment ruff failed." }
        & $python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "Fresh-environment API tests failed." }
        $env:PYTHONPATH = "."
        & $python scripts\export_schema.py --check
        if ($LASTEXITCODE -ne 0) { throw "Fresh-environment schema check failed." }
        & $python scripts\eval_golden.py
        if ($LASTEXITCODE -ne 0) { throw "Fresh-environment golden evaluation failed." }
    }
    finally {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        Pop-Location
    }

    Write-Host "Fresh Python 3.12 offline bootstrap passed." -ForegroundColor Green
}
finally {
    if (Test-Path -LiteralPath $environmentFull) {
        Remove-Item -LiteralPath $environmentFull -Recurse -Force
    }
}
