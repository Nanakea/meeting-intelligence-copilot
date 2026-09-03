# Synthetic internal-pilot verification entrypoint (Windows-first, per AGENTS rule 17).
#
#   powershell -ExecutionPolicy Bypass -File scripts/check.ps1
#
# Runs, and fails on the first red:
#   api  : ruff lint, pytest (including JA/EN and Korean pilot corpora), schema sync, goldens
#   web  : pnpm vitest, tsc typecheck, production build
# Dependencies must already be present. This verification gate is network-disabled;
# use scripts/bootstrap-dev.ps1 explicitly for first-time dependency setup.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$api = Join-Path $repo "apps\api"
$web = Join-Path $repo "apps\web"
$py = Join-Path $api ".venv\Scripts\python.exe"

function Invoke-Step {
    param([string]$Name, [scriptblock]$Body)
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }
}

# --- api: require explicit setup (never install from the verification gate) ---
if (-not (Test-Path $py)) {
    Write-Host "FAILED: API virtual environment is missing: $py" -ForegroundColor Red
    Write-Host "Run scripts\bootstrap-dev.ps1 explicitly, then rerun this offline gate."
    exit 1
}

# --- api checks ---
Push-Location $api
try {
    Invoke-Step "api: ruff lint (incl. import-boundary)" { & $py -m ruff check . }
    Invoke-Step "api: pytest (contracts + WS + purity + schema)" { & $py -m pytest -q }
    Invoke-Step "api: Korean synthetic pilot quality report" {
        & $py "$repo\scripts\eval-korean-pilot-corpus.py"
    }
    $env:PYTHONPATH = "."
    Invoke-Step "api: contract schema in sync" { & $py scripts\export_schema.py --check }
    Invoke-Step "api: golden evaluation (deterministic Slice 1 trajectory)" {
        & $py scripts\eval_golden.py
    }
    Remove-Item Env:PYTHONPATH
}
finally {
    Pop-Location
}

# --- web checks (CI=true keeps pnpm non-interactive on Windows) ---
$env:CI = "true"
Push-Location $web
try {
    Invoke-Step "web: pnpm install (offline, frozen lockfile)" {
        pnpm install --offline --frozen-lockfile
    }
    Invoke-Step "web: vitest (render + version guard + contract sync)" { pnpm test }
    Invoke-Step "web: typecheck (tsc --noEmit)" { pnpm run typecheck }
    Invoke-Step "web: production build (vite)" { pnpm run build }
}
finally {
    Pop-Location
}

Write-Host "`nAll checks passed." -ForegroundColor Green
exit 0
