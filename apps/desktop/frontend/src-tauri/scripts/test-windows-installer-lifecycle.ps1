[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BaselineInstaller,

    [Parameter(Mandatory = $true)]
    [string]$CandidateInstaller,

    [string]$BackendAcceptanceScript
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "The NSIS lifecycle test requires Windows."
}

$tauriRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$baseline = (Resolve-Path -LiteralPath $BaselineInstaller).Path
$candidate = (Resolve-Path -LiteralPath $CandidateInstaller).Path
$installDirectory = Join-Path $env:LOCALAPPDATA "Meeting Intelligence Copilot"
$installedExecutable = Join-Path $installDirectory "meeting-intelligence-desktop.exe"
$installedBackend = Join-Path $installDirectory "meeting-intelligence-backend.exe"
$uninstaller = Join-Path $installDirectory "uninstall.exe"
$roamingData = Join-Path $env:APPDATA "com.nanakea.meetingintelligence"
$localData = Join-Path $env:LOCALAPPDATA "com.nanakea.meetingintelligence"
$dataStateBefore = @(
    (Test-Path -LiteralPath $roamingData),
    (Test-Path -LiteralPath $localData)
)
$installedByTest = $false

if (Test-Path -LiteralPath $installDirectory) {
    throw "Refusing to overwrite a pre-existing Meeting Intelligence Copilot installation: $installDirectory"
}

foreach ($installer in @($baseline, $candidate)) {
    if ((Get-AuthenticodeSignature -LiteralPath $installer).Status -ne "NotSigned") {
        throw "This controlled lifecycle test expects an unsigned local-review installer: $installer"
    }
}

if (-not [string]::IsNullOrWhiteSpace($BackendAcceptanceScript)) {
    $BackendAcceptanceScript = (Resolve-Path -LiteralPath $BackendAcceptanceScript).Path
    if (-not (Test-Path -LiteralPath $BackendAcceptanceScript -PathType Leaf)) {
        throw "Backend acceptance script is not a file: $BackendAcceptanceScript"
    }
}

function Get-AvailableLoopbackPort {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    try {
        $listener.Start()
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

function Assert-InstalledBackendAcceptance {
    if ([string]::IsNullOrWhiteSpace($BackendAcceptanceScript)) {
        return
    }

    $port = Get-AvailableLoopbackPort
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $BackendAcceptanceScript `
        -ArtifactPath $installedBackend `
        -Port $port
    if ($LASTEXITCODE -ne 0) {
        throw "Installed backend acceptance failed with exit code $LASTEXITCODE."
    }
    Write-Host "PASS installed backend authenticated acceptance"
}

function Invoke-SilentInstall {
    param([string]$Installer)

    $process = Start-Process -FilePath $Installer -ArgumentList "/S" -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "NSIS silent install failed with exit code $($process.ExitCode)."
    }
    $script:installedByTest = $true
}

function Assert-InstalledPayload {
    param([switch]$VerifyCurrentSidecars)

    $sourceBinaries = @{
        "ffmpeg.exe" = Join-Path $tauriRoot "binaries\ffmpeg-x86_64-pc-windows-msvc.exe"
        "llama-helper.exe" = Join-Path $tauriRoot "binaries\llama-helper-x86_64-pc-windows-msvc.exe"
        "meeting-intelligence-backend.exe" = Join-Path $tauriRoot "binaries\meeting-intelligence-backend-x86_64-pc-windows-msvc.exe"
    }

    foreach ($name in @("meeting-intelligence-desktop.exe", "uninstall.exe") + $sourceBinaries.Keys) {
        $installed = Join-Path $installDirectory $name
        if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) {
            throw "Installed payload is missing: $name"
        }
        if ((Get-Item -LiteralPath $installed).Length -le 0) {
            throw "Installed payload is empty: $name"
        }
    }

    if ($VerifyCurrentSidecars) {
        foreach ($entry in $sourceBinaries.GetEnumerator()) {
            $installedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $installDirectory $entry.Key)).Hash
            $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $entry.Value).Hash
            if ($installedHash -ne $sourceHash) {
                throw "Installed sidecar does not match the reviewed build input: $($entry.Key)"
            }
        }
    }
}

function Assert-InstalledExecutableHash {
    param([string]$ExpectedHash, [string]$Phase)

    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $installedExecutable).Hash
    if ($actual -ne $ExpectedHash) {
        throw "$Phase installed executable hash mismatch."
    }
}

try {
    Invoke-SilentInstall -Installer $baseline
    Assert-InstalledPayload
    $baselineExecutableHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installedExecutable).Hash
    Write-Host "PASS first install"

    Invoke-SilentInstall -Installer $candidate
    Assert-InstalledPayload -VerifyCurrentSidecars
    $candidateExecutableHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installedExecutable).Hash
    if ($candidateExecutableHash -eq $baselineExecutableHash) {
        throw "Upgrade did not replace the baseline executable."
    }
    Write-Host "PASS same-format upgrade"
    Assert-InstalledBackendAcceptance

    Invoke-SilentInstall -Installer $baseline
    Assert-InstalledPayload
    Assert-InstalledExecutableHash -ExpectedHash $baselineExecutableHash -Phase "Rollback"
    Write-Host "PASS rollback"

    Invoke-SilentInstall -Installer $candidate
    Assert-InstalledPayload -VerifyCurrentSidecars
    Assert-InstalledExecutableHash -ExpectedHash $candidateExecutableHash -Phase "Re-upgrade"
    Write-Host "PASS re-upgrade"

    $uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList "/S" -Wait -PassThru -WindowStyle Hidden
    if ($uninstallProcess.ExitCode -ne 0) {
        throw "NSIS silent uninstall failed with exit code $($uninstallProcess.ExitCode)."
    }
    $installedByTest = $false
    $uninstallDeadline = [DateTime]::UtcNow.AddSeconds(10)
    while (
        (Test-Path -LiteralPath $installDirectory) -and
        [DateTime]::UtcNow -lt $uninstallDeadline
    ) {
        Start-Sleep -Milliseconds 250
    }
    if (Test-Path -LiteralPath $installDirectory) {
        throw "NSIS uninstall left the program directory behind: $installDirectory"
    }

    $dataStateAfter = @(
        (Test-Path -LiteralPath $roamingData),
        (Test-Path -LiteralPath $localData)
    )
    if ($dataStateAfter[0] -ne $dataStateBefore[0] -or $dataStateAfter[1] -ne $dataStateBefore[1]) {
        throw "Installer lifecycle changed existing user-data directory retention."
    }

    Write-Host "PASS uninstall and user-data retention" -ForegroundColor Green
    [pscustomobject]@{
        BaselineInstallerSHA256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $baseline).Hash
        CandidateInstallerSHA256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $candidate).Hash
        CandidateExecutableSHA256 = $candidateExecutableHash
        InstallDirectoryRemoved = $true
        ExistingUserDataPreserved = $true
    } | Format-List
}
finally {
    if ($installedByTest -and (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        $cleanup = Start-Process -FilePath $uninstaller -ArgumentList "/S" -Wait -PassThru -WindowStyle Hidden
        if ($cleanup.ExitCode -ne 0) {
            Write-Warning "Emergency lifecycle-test uninstall failed with exit code $($cleanup.ExitCode)."
        }
    }
}
