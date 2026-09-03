[CmdletBinding()]
param(
    [string]$MeetilyPath = $env:MEETILY_ROOT,
    [string]$LibClangPath = $env:LIBCLANG_PATH,
    [switch]$RequirePreparedSidecars,
    [string]$ParakeetModelPath,
    [string]$WhisperModelPath,
    [switch]$CheckAcceptanceTools
)

$ErrorActionPreference = "Stop"
$supersededBackendSha256 = "FA8C7E4BE262CEF897DD747CB28E1C84D5B2F7BB8F2A234286CDB0D3C36E0C48"
Set-StrictMode -Version Latest

$failures = 0
$warnings = 0

if ([string]::IsNullOrWhiteSpace($MeetilyPath)) {
    $MeetilyPath = [IO.Path]::GetFullPath(
        (Join-Path $PSScriptRoot "..\apps\desktop")
    )
}

function Write-CheckResult {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Detail,
        [bool]$Required = $true
    )

    if ($Passed) {
        Write-Host "[PASS] $Name - $Detail" -ForegroundColor Green
        return
    }

    if ($Required) {
        $script:failures += 1
        Write-Host "[FAIL] $Name - $Detail" -ForegroundColor Red
    }
    else {
        $script:warnings += 1
        Write-Host "[WARN] $Name - $Detail" -ForegroundColor Yellow
    }
}

function Find-Command {
    param([string]$Name)
    return Get-Command $Name -ErrorAction SilentlyContinue
}

Write-CheckResult "Windows" ($env:OS -eq "Windows_NT") "Windows_NT is required for this V1 distribution path"

$productRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Write-CheckResult "Product repository" (Test-Path -LiteralPath (Join-Path $productRoot "scripts\check.ps1") -PathType Leaf) $productRoot

$git = Find-Command "git"
Write-CheckResult "Git" ($null -ne $git) $(if ($git) { (& git --version) } else { "git is not on PATH" })

$pythonLauncher = Find-Command "py"
$pythonVersion = "Python 3.12 launcher is unavailable"
$pythonReady = $false
if ($pythonLauncher) {
    $pythonVersion = (& py -3.12 --version 2>&1 | Out-String).Trim()
    $pythonReady = $LASTEXITCODE -eq 0 -and $pythonVersion -match "Python 3\.12\."
}
Write-CheckResult "Python 3.12" $pythonReady $pythonVersion

$venvPython = Join-Path $productRoot "apps\api\.venv\Scripts\python.exe"
$venvReady = Test-Path -LiteralPath $venvPython -PathType Leaf
$venvDetail = if ($venvReady) { $venvPython } else { "Run scripts\bootstrap-dev.ps1 only during the explicit dependency-setup phase if missing" }
Write-CheckResult "Product API venv" $venvReady $venvDetail $false

$node = Find-Command "node"
$nodeVersion = if ($node) { (& node --version) } else { "node is not on PATH" }
Write-CheckResult "Node.js" ($null -ne $node) $nodeVersion

$pnpm = Find-Command "pnpm"
$pnpmVersion = if ($pnpm) { (& pnpm --version) } else { "pnpm is not on PATH" }
Write-CheckResult "pnpm" ($null -ne $pnpm) $pnpmVersion

$rustup = Find-Command "rustup"
Write-CheckResult "rustup" ($null -ne $rustup) $(if ($rustup) { (& rustup --version 2>&1 | Select-Object -First 1) } else { "rustup is not on PATH" })

$rustVersion = "Rust stable is unavailable"
$rustReady = $false
if ($rustup) {
    $rustVersion = (& rustc +stable --version 2>&1 | Out-String).Trim()
    $rustReady = $LASTEXITCODE -eq 0
}
Write-CheckResult "Rust stable" $rustReady $rustVersion

$cmake = Find-Command "cmake"
$cmakeVersion = if ($cmake) { (& cmake --version | Select-Object -First 1) } else { "cmake is not on PATH" }
Write-CheckResult "CMake" ($null -ne $cmake) $cmakeVersion

$generatorReady = $false
if ($cmake) {
    $generatorReady = ((& cmake --help 2>&1 | Out-String) -match "Visual Studio 17 2022")
}
Write-CheckResult "CMake VS 2022 generator" $generatorReady "Visual Studio 17 2022"

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$visualStudioPath = "Visual Studio C++ tools were not found"
if (Test-Path -LiteralPath $vswhere -PathType Leaf) {
    $visualStudioPath = (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Out-String).Trim()
}
Write-CheckResult "MSVC C++ tools" (-not [string]::IsNullOrWhiteSpace($visualStudioPath) -and $visualStudioPath -ne "Visual Studio C++ tools were not found") $visualStudioPath

$windowsSdkRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
$windowsSdk = Get-ChildItem -LiteralPath $windowsSdkRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+$' } |
    Sort-Object Name -Descending |
    Select-Object -First 1
Write-CheckResult "Windows 10/11 SDK" ($null -ne $windowsSdk) $(if ($windowsSdk) { $windowsSdk.FullName } else { "No versioned Windows SDK bin directory found" })

if ([string]::IsNullOrWhiteSpace($LibClangPath)) {
    $defaultLlvm = Join-Path $env:ProgramFiles "LLVM\bin"
    if (Test-Path -LiteralPath (Join-Path $defaultLlvm "libclang.dll") -PathType Leaf) {
        $LibClangPath = $defaultLlvm
    }
}
$libclang = if ([string]::IsNullOrWhiteSpace($LibClangPath)) { $null } else { Join-Path $LibClangPath "libclang.dll" }
Write-CheckResult "LLVM/libclang" ($null -ne $libclang -and (Test-Path -LiteralPath $libclang -PathType Leaf)) $(if ($libclang) { $libclang } else { "Pass -LibClangPath <LLVM-bin-directory>" })

if (-not [string]::IsNullOrWhiteSpace($MeetilyPath)) {
    $meetilyRoot = if (Test-Path -LiteralPath $MeetilyPath -PathType Container) { (Resolve-Path -LiteralPath $MeetilyPath).Path } else { $MeetilyPath }
    $meetilyReady = Test-Path -LiteralPath (Join-Path $meetilyRoot "frontend\src-tauri\tauri.conf.json") -PathType Leaf
    Write-CheckResult "Desktop workspace" $meetilyReady $meetilyRoot

    if ($meetilyReady) {
        Write-CheckResult "Desktop Cargo lock" (Test-Path -LiteralPath (Join-Path $meetilyRoot "Cargo.lock") -PathType Leaf) "Cargo.lock"
        Write-CheckResult "Desktop pnpm lock" (Test-Path -LiteralPath (Join-Path $meetilyRoot "frontend\pnpm-lock.yaml") -PathType Leaf) "frontend\pnpm-lock.yaml"

        $sidecarRoot = Join-Path $meetilyRoot "frontend\src-tauri\binaries"
        foreach ($sidecar in @(
            "ffmpeg-x86_64-pc-windows-msvc.exe",
            "llama-helper-x86_64-pc-windows-msvc.exe",
            "meeting-intelligence-backend-x86_64-pc-windows-msvc.exe"
        )) {
            $sidecarPath = Join-Path $sidecarRoot $sidecar
            $sidecarReady = Test-Path -LiteralPath $sidecarPath -PathType Leaf
            if ($sidecarReady) {
                $sidecarReady = (Get-Item -LiteralPath $sidecarPath).Length -gt 0
            }
            $sidecarDetail = $sidecarPath
            if (
                $sidecarReady -and
                $sidecar -eq "meeting-intelligence-backend-x86_64-pc-windows-msvc.exe"
            ) {
                $backendHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sidecarPath).Hash
                if ($backendHash -eq $supersededBackendSha256) {
                    $sidecarReady = $false
                    $sidecarDetail = "Superseded API v2 backend; build and install the API v13 candidate"
                }
            }
            if (-not $sidecarReady -and $sidecarDetail -eq $sidecarPath) {
                $sidecarDetail = "Run the reviewed monorepo sidecar setup commands"
            }
            Write-CheckResult "Sidecar $sidecar" $sidecarReady $sidecarDetail ([bool]$RequirePreparedSidecars)
        }
    }
}

foreach ($model in @(
    @{ Name = "Parakeet model"; Path = $ParakeetModelPath },
    @{ Name = "Whisper model"; Path = $WhisperModelPath }
)) {
    if ([string]::IsNullOrWhiteSpace($model.Path)) {
        Write-CheckResult $model.Name $false "Pass the local model path to verify it; models are not fetched by this checker" $false
    }
    else {
        Write-CheckResult $model.Name (Test-Path -LiteralPath $model.Path) $model.Path
    }
}

if ($CheckAcceptanceTools) {
    $voicevox = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*", "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match "VOICEVOX" } |
        Select-Object -First 1
    Write-CheckResult "VOICEVOX" ($null -ne $voicevox) "Required only for the synthetic Japanese acceptance procedure" $false

    $vlc = Find-Command "vlc"
    if ($null -eq $vlc) {
        $vlcCandidates = @(
            (Join-Path $env:ProgramFiles "VideoLAN\VLC\vlc.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "VideoLAN\VLC\vlc.exe")
        )
        $vlc = $vlcCandidates |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
            Select-Object -First 1 |
            ForEach-Object { Get-Item -LiteralPath $_ }
    }
    $vlcDetail = if ($null -eq $vlc) {
        "Required only for routed-audio acceptance"
    }
    elseif ($vlc.PSObject.Properties.Name -contains "Source") {
        $vlc.Source
    }
    else {
        $vlc.FullName
    }
    Write-CheckResult "VLC" ($null -ne $vlc) $vlcDetail $false

    $vbCable = Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "CABLE|VB-Audio" } |
        Select-Object -First 1
    Write-CheckResult "VB-Audio Virtual Cable" ($null -ne $vbCable) $(if ($vbCable) { $vbCable.Name } else { "Required only for routed-audio acceptance" }) $false
}

Write-Host ""
Write-Host "Prerequisite check complete: $failures failure(s), $warnings warning(s)."
Write-Host "This command performed read-only inspection and made no network requests."
if ($failures -gt 0) {
    exit 1
}
