[CmdletBinding()]
param(
    [string]$DesktopPath,
    [string]$MeetilyPath = $env:MEETILY_ROOT,
    [string]$LibClangPath = $env:LIBCLANG_PATH,
    [ValidateRange(1, 65535)]
    [int]$BackendPort = 8000,
    [switch]$LaunchDesktop,
    [switch]$LaunchMeetily,
    [switch]$AllowUnauthenticatedDev,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($DesktopPath)) {
    $DesktopPath = if ([string]::IsNullOrWhiteSpace($MeetilyPath)) {
        [IO.Path]::GetFullPath(
        (Join-Path $PSScriptRoot "..\apps\desktop")
        )
    } else { $MeetilyPath }
}
$MeetilyPath = $DesktopPath # Deprecated compatibility alias.
$shouldLaunchDesktop = $LaunchDesktop -or $LaunchMeetily

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$api = Join-Path $repo "apps\api"
$python = Join-Path $api ".venv\Scripts\python.exe"
$healthUrl = "http://127.0.0.1:$BackendPort/health"
$compatibilityUrl = "http://127.0.0.1:$BackendPort/health/compatibility"
$tokenEnvironmentName = "MEETING_INTELLIGENCE_TOKEN"
$tokenHeaderName = "X-Meeting-Intelligence-Token"
$logRoot = Join-Path ([System.IO.Path]::GetTempPath()) "meeting-intelligence-copilot"
$backendOutputLog = Join-Path $logRoot "backend.stdout.log"
$backendErrorLog = Join-Path $logRoot "backend.stderr.log"

function Test-BackendHealth {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
        return $response.status -eq "ok"
    }
    catch {
        return $false
    }
}

function New-CapabilityToken {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Test-BackendCompatibility {
    param(
        [string]$Token,
        [bool]$RequireAuthentication
    )

    try {
        $headers = @{}
        if (-not [string]::IsNullOrWhiteSpace($Token)) {
            $headers[$tokenHeaderName] = $Token
        }
        $response = Invoke-RestMethod `
            -Uri $compatibilityUrl `
            -Method Get `
            -Headers $headers `
            -TimeoutSec 2
        return (
            $response.status -eq "ok" -and
            $response.product -eq "meeting-intelligence-copilot" -and
            [int]$response.api_version -eq 13 -and
            ((-not $RequireAuthentication) -or $response.capability_auth -eq $true)
        )
    }
    catch {
        return $false
    }
}

function Restore-ProcessEnvironment {
    param([hashtable]$Previous)

    foreach ($name in $Previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $Previous[$name], "Process")
    }
}

if (-not (Test-Path $python)) {
    throw @"
Product API virtual environment is missing:
  $python

Run the exact Windows setup/check command, then retry:
  cd $repo
  powershell -ExecutionPolicy Bypass -File scripts\bootstrap-dev.ps1
  powershell -ExecutionPolicy Bypass -File scripts\check.ps1
"@
}

$capabilityToken = [Environment]::GetEnvironmentVariable($tokenEnvironmentName, "Process")
if (-not [string]::IsNullOrWhiteSpace($capabilityToken) -and
    $capabilityToken -notmatch '^[A-Za-z0-9._~-]{32,256}$') {
    throw "$tokenEnvironmentName must contain 32-256 URL-safe ASCII characters."
}
if ([string]::IsNullOrWhiteSpace($capabilityToken)) {
    $capabilityToken = $null
    if ($shouldLaunchDesktop) {
        $capabilityToken = New-CapabilityToken
    }
    elseif (-not $DryRun -and -not $AllowUnauthenticatedDev) {
        throw @"
No local capability token is configured. For the authenticated two-process launch, use:
  powershell -ExecutionPolicy Bypass -File scripts\start-local-v1.ps1 -LaunchDesktop

For separate manual processes, set the same $tokenEnvironmentName value in both process environments.
Use -AllowUnauthenticatedDev only for an explicit loopback development session.
"@
    }
}

$backendArguments = @(
    "-m",
    "uvicorn",
    "app.api.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    $BackendPort.ToString()
)

Write-Host "Product repository: $repo"
Write-Host "Backend health URL: $healthUrl"

$backendProcess = $null
if ($DryRun) {
    Write-Host "DRY RUN: would start or reuse: $python $($backendArguments -join ' ')"
    Write-Host "DRY RUN: backend logs would be written under $logRoot"
    Write-Host "DRY RUN: authenticated launch would use an in-memory capability token (value not displayed)"
}
elseif (Test-BackendHealth) {
    $requireAuthentication = -not $AllowUnauthenticatedDev
    if (-not (Test-BackendCompatibility -Token $capabilityToken -RequireAuthentication $requireAuthentication)) {
        throw "A backend is listening at $healthUrl but did not accept the configured capability/compatibility contract."
    }
    Write-Host "Backend is already healthy and compatible; reusing the existing loopback process." -ForegroundColor Green
}
else {
    New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
    $previousToken = [Environment]::GetEnvironmentVariable($tokenEnvironmentName, "Process")
    try {
        [Environment]::SetEnvironmentVariable($tokenEnvironmentName, $capabilityToken, "Process")
        $backendProcess = Start-Process `
            -FilePath $python `
            -ArgumentList $backendArguments `
            -WorkingDirectory $api `
            -RedirectStandardOutput $backendOutputLog `
            -RedirectStandardError $backendErrorLog `
            -WindowStyle Hidden `
            -PassThru
    }
    finally {
        [Environment]::SetEnvironmentVariable($tokenEnvironmentName, $previousToken, "Process")
    }

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($backendProcess.HasExited) {
            break
        }
        if (Test-BackendHealth -and (
            Test-BackendCompatibility `
                -Token $capabilityToken `
                -RequireAuthentication (-not $AllowUnauthenticatedDev)
        )) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }

    if (-not $ready) {
        if (-not $backendProcess.HasExited) {
            Stop-Process -Id $backendProcess.Id
        }
        throw "Backend did not become healthy at $healthUrl. See $backendOutputLog and $backendErrorLog."
    }

    Write-Host "Backend started (PID $($backendProcess.Id)); health check passed." -ForegroundColor Green
    Write-Host "Backend logs: $logRoot"
}

$desktopFrontend = Join-Path $DesktopPath "frontend"
if ($shouldLaunchDesktop) {
    if (-not (Test-Path (Join-Path $desktopFrontend "package.json"))) {
        throw "Desktop package.json was not found under $desktopFrontend. Use -DesktopPath with the monorepo desktop directory."
    }

    $pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if ($null -eq $pnpm) {
        $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
    }
    if ($null -eq $pnpm) {
        throw "pnpm is required to launch the desktop app. Install pnpm, then retry from $desktopFrontend."
    }

    $llamaHelper = Join-Path $desktopFrontend "src-tauri\binaries\llama-helper-x86_64-pc-windows-msvc.exe"
    if (-not (Test-Path $llamaHelper)) {
        throw @"
Desktop launch was not attempted because the required Tauri externalBin is missing:
  $llamaHelper

This is a real packaging blocker, not a placeholder condition. Build llama-helper from the
desktop source and place the target-suffixed executable at that path before using -LaunchDesktop.
From the current clean distribution worktree, run:
  powershell -ExecutionPolicy Bypass -File scripts\setup-meetily-sidecars.ps1
"@
    }

    if ($DryRun) {
        Write-Host "DRY RUN: would launch desktop from $desktopFrontend"
        Write-Host "DRY RUN: pnpm run tauri:dev:cpu"
    }
    else {
        $previousEnvironment = @{}
        foreach ($name in @("RUSTUP_TOOLCHAIN", "LIBCLANG_PATH", "CMAKE_GENERATOR", $tokenEnvironmentName)) {
            $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        }

        [Environment]::SetEnvironmentVariable("RUSTUP_TOOLCHAIN", "stable", "Process")
        if (-not [string]::IsNullOrWhiteSpace($LibClangPath)) {
            [Environment]::SetEnvironmentVariable("LIBCLANG_PATH", $LibClangPath, "Process")
        }
        [Environment]::SetEnvironmentVariable("CMAKE_GENERATOR", "Visual Studio 17 2022", "Process")
        [Environment]::SetEnvironmentVariable($tokenEnvironmentName, $capabilityToken, "Process")
        try {
            Start-Process `
                -FilePath $pnpm.Source `
                -ArgumentList @("run", "tauri:dev:cpu") `
                -WorkingDirectory $desktopFrontend `
                -WindowStyle Normal | Out-Null
            Write-Host "Desktop launch requested from $desktopFrontend." -ForegroundColor Green
        }
        finally {
            Restore-ProcessEnvironment -Previous $previousEnvironment
        }
    }
}
elseif (-not $DryRun) {
    Write-Host "Backend-only startup complete. Launch the desktop app manually when ready:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File $repo\scripts\start-local-v1.ps1 -DesktopPath $DesktopPath -LaunchDesktop"
}

Write-Host "Local-only backend binding is fixed to 127.0.0.1; no LAN listener is created."
