[CmdletBinding()]
param(
    [Alias("MeetilyRoot")]
    [string]$DesktopRoot = $env:MEETING_INTELLIGENCE_DESKTOP_ROOT,
    [string]$CargoTargetRoot = "$env:CARGO_TARGET_DIR",
    [string]$EvidenceRoot = $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT,
    [string]$SandboxPackage,
    [string]$OutputPath,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$assistantRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($DesktopRoot)) {
    $DesktopRoot = [IO.Path]::GetFullPath(
        (Join-Path $assistantRoot "apps\desktop")
    )
}
if ([string]::IsNullOrWhiteSpace($CargoTargetRoot)) {
    $CargoTargetRoot = Join-Path $env:LOCALAPPDATA "meeting-intelligence\cargo-target"
}
$checks = [System.Collections.Generic.List[object]]::new()

function Add-ReadinessCheck {
    param(
        [string]$Id,
        [ValidateSet("pass", "pending_company", "action_required")]
        [string]$State,
        [string]$Detail
    )
    $checks.Add([pscustomobject][ordered]@{ id = $Id; state = $State; detail = $Detail })
}

function Get-SourceCommit {
    param([string]$Repository)
    $commit = (& git -C $Repository rev-parse HEAD 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') {
        throw "Source revision is unavailable."
    }
    $commit
}

function Test-TrackedSourceClean {
    param([string]$Repository)
    $status = (& git -C $Repository status --porcelain=v1 --untracked-files=no 2>$null | Out-String).Trim()
    $LASTEXITCODE -eq 0 -and [string]::IsNullOrWhiteSpace($status)
}

function Read-JsonFile {
    param([string]$Path)
    Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}

function Get-FileRecord {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    $item = Get-Item -LiteralPath $Path
    [ordered]@{
        name = $item.Name
        bytes = $item.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
        authenticode = (Get-AuthenticodeSignature -LiteralPath $Path).Status.ToString()
    }
}

if ($env:OS -eq "Windows_NT" -and [Environment]::Is64BitOperatingSystem -and [Environment]::OSVersion.Version.Build -ge 22000) {
    Add-ReadinessCheck "windows_11_x64" "pass" "Windows 11 x64 is available."
} else {
    Add-ReadinessCheck "windows_11_x64" "action_required" "Use Windows 11 x64 for this pilot."
}

try {
    $driveRoot = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($CargoTargetRoot))
    $drive = Get-PSDrive -Name $driveRoot.TrimEnd('\').TrimEnd(':')
    if ($drive.Free -ge 20GB) {
        Add-ReadinessCheck "build_disk" "pass" "At least 20 GB is free on the build-output drive."
    } else {
        Add-ReadinessCheck "build_disk" "action_required" "Free at least 20 GB on the build-output drive."
    }
} catch {
    Add-ReadinessCheck "build_disk" "action_required" "Build-output disk capacity could not be verified."
}

try {
    $defender = Get-MpComputerStatus
    if ($defender.AntivirusEnabled -and $defender.RealTimeProtectionEnabled) {
        Add-ReadinessCheck "windows_defender" "pass" "Microsoft Defender antivirus and real-time protection are enabled."
    } else {
        Add-ReadinessCheck "windows_defender" "action_required" "Enable Microsoft Defender or obtain an approved equivalent before company data is used."
    }
} catch {
    Add-ReadinessCheck "windows_defender" "pending_company" "Endpoint-protection status must be confirmed on the company device."
}

try {
    $bitLocker = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop
    if ($bitLocker.ProtectionStatus -eq "On") {
        Add-ReadinessCheck "device_encryption" "pass" "System-drive BitLocker protection is on."
    } else {
        Add-ReadinessCheck "device_encryption" "pending_company" "Enable the company-approved device encryption policy before company data is used."
    }
} catch {
    Add-ReadinessCheck "device_encryption" "pending_company" "Device encryption must be confirmed with company IT."
}

$sourceCommit = $null
try {
    $sourceCommit = Get-SourceCommit $assistantRoot
    if (Test-TrackedSourceClean $assistantRoot) {
        Add-ReadinessCheck "clean_sources" "pass" "The release monorepo has no tracked modifications."
    } else {
        Add-ReadinessCheck "clean_sources" "action_required" "Commit or remove tracked release-source changes before rebuilding."
    }
} catch {
    Add-ReadinessCheck "clean_sources" "action_required" "The release monorepo and source commit must be available."
}

$assistantVersionSource = Get-Content -LiteralPath (Join-Path $assistantRoot "apps\api\pyproject.toml") -Raw
$desktopPackagePath = Join-Path $DesktopRoot "frontend\package.json"
$desktopTauriPath = Join-Path $DesktopRoot "frontend\src-tauri\tauri.conf.json"
try {
    $desktopPackage = Read-JsonFile $desktopPackagePath
    $desktopTauri = Read-JsonFile $desktopTauriPath
    if (
        $assistantVersionSource -match '(?m)^version = "0\.6\.0"$' -and
        $desktopPackage.version -eq "0.6.1" -and
        $desktopTauri.version -eq "0.6.1"
    ) {
        Add-ReadinessCheck "version_alignment" "pass" "Monorepo components are aligned at 0.6.1 / compatibility API v13."
    } else {
        Add-ReadinessCheck "version_alignment" "action_required" "Align all release versions at 0.6.1 before rebuilding."
    }
} catch {
    Add-ReadinessCheck "version_alignment" "action_required" "Release version alignment could not be verified."
}

$backendPath = Join-Path $assistantRoot "dist\backend-sidecar\meeting-intelligence-backend-x86_64-pc-windows-msvc.exe"
$backendProvenancePath = Join-Path $assistantRoot "dist\backend-sidecar\build-provenance.json"
$backendRecord = Get-FileRecord $backendPath
try {
    $backendProvenance = Read-JsonFile $backendProvenancePath
    if (
        $null -ne $backendRecord -and
        $backendProvenance.api_version -eq 13 -and
        $backendProvenance.backend_version -eq "0.6.1" -and
        $backendProvenance.source_commit -eq $sourceCommit -and
        $backendProvenance.artifact.sha256 -eq $backendRecord.sha256
    ) {
        Add-ReadinessCheck "backend_provenance" "pass" "The backend artifact matches the current assistant commit and API v13 provenance."
    } else {
        Add-ReadinessCheck "backend_provenance" "action_required" "Rebuild and accept the backend from the current clean assistant commit."
    }
} catch {
    Add-ReadinessCheck "backend_provenance" "action_required" "Backend artifact provenance is missing or invalid."
}

$nsisPath = Join-Path $CargoTargetRoot "release\bundle\nsis\Meeting Intelligence Copilot_0.6.1_x64-setup.exe"
$msiPath = Join-Path $CargoTargetRoot "release\bundle\msi\Meeting Intelligence Copilot_0.6.1_x64_en-US.msi"
$desktopExePath = Join-Path $CargoTargetRoot "release\meeting-intelligence-desktop.exe"
$bundleProvenancePath = Join-Path $CargoTargetRoot "release\bundle\precompany-build-provenance.json"
$nsisRecord = Get-FileRecord $nsisPath
$msiRecord = Get-FileRecord $msiPath
$desktopRecord = Get-FileRecord $desktopExePath
try {
    $bundleProvenance = Read-JsonFile $bundleProvenancePath
    $artifactHashes = @($bundleProvenance.artifacts | ForEach-Object { $_.sha256 })
    if (
        $null -ne $nsisRecord -and $null -ne $msiRecord -and $null -ne $desktopRecord -and
        $bundleProvenance.source_commit -eq $sourceCommit -and
        $bundleProvenance.backend_source_commit -eq $sourceCommit -and
        $bundleProvenance.backend_sha256 -eq $backendRecord.sha256 -and
        $bundleProvenance.application.sha256 -eq $desktopRecord.sha256 -and
        $artifactHashes -contains $nsisRecord.sha256 -and
        $artifactHashes -contains $msiRecord.sha256
    ) {
        Add-ReadinessCheck "installer_provenance" "pass" "NSIS and MSI match the current monorepo and backend provenance."
    } else {
        Add-ReadinessCheck "installer_provenance" "action_required" "Rebuild both installers from the current clean commits."
    }
} catch {
    Add-ReadinessCheck "installer_provenance" "action_required" "Installer provenance is missing or invalid."
}

$sandboxManifests = if ([string]::IsNullOrWhiteSpace($SandboxPackage)) {
    if ([string]::IsNullOrWhiteSpace($EvidenceRoot)) {
        @()
    } else {
        Get-ChildItem -LiteralPath $EvidenceRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending |
            ForEach-Object { Join-Path $_.FullName "payload\MANIFEST.json" } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
    }
} else {
    @(Join-Path $SandboxPackage "payload\MANIFEST.json")
}
$matchingSandbox = $false
foreach ($manifestPath in $sandboxManifests) {
    try {
        $sandboxManifest = Read-JsonFile $manifestPath
        if (
            $sandboxManifest.candidate_commit -eq $sourceCommit -and
            $sandboxManifest.candidate_sha256 -eq $nsisRecord.sha256
        ) {
            $matchingSandbox = $true
            break
        }
    } catch {
        continue
    }
}
if ($matchingSandbox) {
    Add-ReadinessCheck "sandbox_package" "pass" "The offline lifecycle package matches the current NSIS candidate."
} else {
    Add-ReadinessCheck "sandbox_package" "action_required" "Regenerate the offline lifecycle package for the current candidate."
}

$artifactRecords = @($backendRecord, $desktopRecord, $nsisRecord, $msiRecord) | Where-Object { $null -ne $_ }
if ($artifactRecords.Count -eq 4 -and @($artifactRecords | Where-Object { $_.authenticode -ne "Valid" }).Count -eq 0) {
    Add-ReadinessCheck "organization_signing" "pass" "All executable release artifacts have valid Authenticode signatures."
} elseif (@($artifactRecords | Where-Object { $_.authenticode -notin @("Valid", "NotSigned") }).Count -gt 0) {
    Add-ReadinessCheck "organization_signing" "action_required" "At least one artifact has an invalid or untrusted signature."
} else {
    Add-ReadinessCheck "organization_signing" "pending_company" "Sign the final artifacts with the organization-controlled certificate."
}

Add-ReadinessCheck "recording_policy" "pending_company" "Confirm company recording, consent, retention, and approved-device policy."
Add-ReadinessCheck "microsoft_365_acceptance" "pending_company" "Configure delegated OneDrive and selected SharePoint access in the real tenant."
Add-ReadinessCheck "netsuite_acceptance" "pending_company" "Configure a sandbox NetSuite read-only integration role, certificate, mappings, and custom review-record target."
Add-ReadinessCheck "teams_rsc_acceptance" "pending_company" "Install the approved Teams app and verify resource-specific consent for each selected team, channel, and meeting chat."
Add-ReadinessCheck "slack_acceptance" "pending_company" "Approve the internal Slack app, rotating PKCE user search, selected channels, and separately enrolled bot posting credentials."
Add-ReadinessCheck "human_pilot" "pending_company" "Complete the consented 5-10 user, two-week JA/EN/KO pilot before team promotion."

$actionCount = @($checks | Where-Object { $_.state -eq "action_required" }).Count
$pendingCount = @($checks | Where-Object { $_.state -eq "pending_company" }).Count
$report = [ordered]@{
    schema_version = 1
    product_version = "0.6.1"
    compatibility_api_version = 13
    source_commit = $sourceCommit
    precompany_ready = $actionCount -eq 0
    ready_for_company_data = $actionCount -eq 0 -and $pendingCount -eq 0
    action_required_count = $actionCount
    pending_company_count = $pendingCount
    artifacts = $artifactRecords
    checks = $checks
}

$checks | Format-Table id, state, detail -AutoSize
Write-Host "Pre-company ready: $($report.precompany_ready)"
Write-Host "Ready for company data: $($report.ready_for_company_data)"

if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $destination = [System.IO.Path]::GetFullPath($OutputPath)
    if ((Test-Path -LiteralPath $destination) -and -not $Force) {
        throw "Refusing to overwrite an existing readiness report without -Force."
    }
    $parent = Split-Path -Parent $destination
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null
    $temporary = "$destination.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporary,
            ($report | ConvertTo-Json -Depth 7),
            [System.Text.UTF8Encoding]::new($false)
        )
        Move-Item -LiteralPath $temporary -Destination $destination -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
    Write-Host "Readiness report written."
}

if ($actionCount -gt 0) { exit 1 }
