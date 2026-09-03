[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$MeetilyRepository,

    [Parameter(Mandatory = $true)]
    [string]$AssistantRepository,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-fA-F]{40}$")]
    [string]$MeetilyCommit,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-fA-F]{40}$")]
    [string]$AssistantCommit,

    [Parameter(Mandatory = $true)]
    [string]$MeetilyExecutable,

    [Parameter(Mandatory = $true)]
    [string]$BackendSidecar,

    [Parameter(Mandatory = $true)]
    [string]$NsisInstaller,

    [Parameter(Mandatory = $true)]
    [string]$MsiInstaller,

    [Parameter(Mandatory = $true)]
    [string]$RoutedAudioCorpusManifest,

    [Parameter(Mandatory = $true)]
    [string]$AcceptanceLedger,

    [Parameter(Mandatory = $true)]
    [string]$WhisperModelManifest,

    [Parameter(Mandatory = $true)]
    [string]$WhisperModelManifestSignature
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "Internal pilot evidence must be produced on Windows."
}

function Resolve-ExistingFile {
    param([string]$Path, [string]$Role)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Role is missing."
    }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if ((Get-Item -LiteralPath $resolved).Length -le 0) {
        throw "$Role is empty."
    }
    return $resolved
}

function Assert-ExactCleanCommit {
    param([string]$Repository, [string]$ExpectedCommit, [string]$Role)

    $resolved = (Resolve-Path -LiteralPath $Repository).Path
    $actual = (& git -C $resolved rev-parse HEAD 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $actual -notmatch "^[0-9a-fA-F]{40}$") {
        throw "$Role is not a readable Git repository."
    }
    if (-not $actual.Equals($ExpectedCommit, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Role HEAD does not match the reviewed commit."
    }
    & git -C $resolved diff --quiet --
    if ($LASTEXITCODE -ne 0) {
        throw "$Role has unstaged tracked changes."
    }
    & git -C $resolved diff --cached --quiet --
    if ($LASTEXITCODE -ne 0) {
        throw "$Role has staged tracked changes."
    }
}

function Find-DefenderCommand {
    $legacy = Join-Path $env:ProgramFiles "Windows Defender\MpCmdRun.exe"
    if (Test-Path -LiteralPath $legacy -PathType Leaf) {
        return (Resolve-Path -LiteralPath $legacy).Path
    }
    $platformRoot = Join-Path $env:ProgramData "Microsoft\Windows Defender\Platform"
    $candidate = Get-ChildItem -LiteralPath $platformRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName "MpCmdRun.exe" } |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    if (-not $candidate) {
        throw "Microsoft Defender command-line scanner is unavailable."
    }
    return $candidate
}

Assert-ExactCleanCommit $MeetilyRepository $MeetilyCommit "Meetily repository"
Assert-ExactCleanCommit $AssistantRepository $AssistantCommit "Assistant repository"
$corpusManifest = Resolve-ExistingFile $RoutedAudioCorpusManifest "Routed-audio corpus manifest"
$corpusDefinition = Get-Content -LiteralPath $corpusManifest -Raw | ConvertFrom-Json
if ($corpusDefinition.scenario_count -ne 108 -or $corpusDefinition.scenarios.Count -ne 108) {
    throw "Routed-audio corpus manifest must contain exactly 108 scenarios."
}
$corpusManifestSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $corpusManifest).Hash
$ledger = Resolve-ExistingFile $AcceptanceLedger "Pilot acceptance ledger"
$ledgerDefinition = Get-Content -LiteralPath $ledger -Raw | ConvertFrom-Json
$failedAcceptance = @($ledgerDefinition.scenarios | Where-Object { $_.status -ne "pass" })
if ($ledgerDefinition.scenarios.Count -ne 132 -or $failedAcceptance.Count -ne 0) {
    throw "Pilot acceptance ledger must contain exactly 132 passing scenarios."
}
$acceptanceLedgerSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ledger).Hash
$modelManifest = Resolve-ExistingFile $WhisperModelManifest "Whisper model manifest"
$modelDefinition = Get-Content -LiteralPath $modelManifest -Raw | ConvertFrom-Json
$pilotModel = @($modelDefinition.artifacts | Where-Object { $_.modelId -eq "large-v3-turbo-q5_0" })
if ($modelDefinition.schemaVersion -ne 1 -or $pilotModel.Count -ne 1 -or
    "ko" -notin @($pilotModel[0].supportedLanguages)) {
    throw "Whisper model manifest does not contain the reviewed Korean-capable pilot model."
}
$modelManifestSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $modelManifest).Hash
$modelSignature = Resolve-ExistingFile $WhisperModelManifestSignature "Whisper model manifest signature"
$signatureText = (Get-Content -LiteralPath $modelSignature -Raw).Trim()
if ($signatureText -notmatch "^[0-9a-fA-F]{128}$") {
    throw "Whisper model manifest signature is malformed."
}
$modelSignatureSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $modelSignature).Hash

$inputs = @(
    [pscustomobject]@{
        role = "meetily_executable"
        source = Resolve-ExistingFile $MeetilyExecutable "Meetily executable"
        name = "meeting-intelligence-desktop.exe"
    },
    [pscustomobject]@{
        role = "backend_sidecar"
        source = Resolve-ExistingFile $BackendSidecar "Meeting Intelligence backend"
        name = "meeting-intelligence-backend.exe"
    },
    [pscustomobject]@{
        role = "nsis_installer"
        source = Resolve-ExistingFile $NsisInstaller "NSIS installer"
        name = "Meetily-internal-pilot-setup.exe"
    },
    [pscustomobject]@{
        role = "msi_installer"
        source = Resolve-ExistingFile $MsiInstaller "MSI installer"
        name = "Meetily-internal-pilot.msi"
    }
)

if (($inputs.source | Select-Object -Unique).Count -ne $inputs.Count) {
    throw "Every pilot artifact input must be a distinct file."
}

$output = [System.IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) {
    throw "Refusing to overwrite an existing pilot evidence directory."
}
$parent = Split-Path -Parent $output
[System.IO.Directory]::CreateDirectory($parent) | Out-Null
$staging = Join-Path $parent (".{0}.{1}.tmp" -f (Split-Path -Leaf $output), [guid]::NewGuid().ToString("N"))
$artifactsDirectory = Join-Path $staging "artifacts"
$defenderDirectory = Join-Path $staging "defender"
[System.IO.Directory]::CreateDirectory($artifactsDirectory) | Out-Null
[System.IO.Directory]::CreateDirectory($defenderDirectory) | Out-Null
$published = $false

try {
    $defender = Get-MpComputerStatus
    if (-not $defender.AntivirusEnabled -or -not $defender.RealTimeProtectionEnabled) {
        throw "Microsoft Defender antivirus and real-time protection must be enabled."
    }
    $defenderCommand = Find-DefenderCommand
    $artifactRecords = @()

    foreach ($artifactInput in $inputs) {
        $destination = Join-Path $artifactsDirectory $artifactInput.name
        Copy-Item -LiteralPath $artifactInput.source -Destination $destination
        $signature = Get-AuthenticodeSignature -LiteralPath $destination
        if ($signature.Status -ne "NotSigned") {
            throw "The internal pilot accepts only reviewed unsigned artifacts."
        }

        $scanLines = @(
            & $defenderCommand -Scan -ScanType 3 -File $destination -DisableRemediation 2>&1 |
                ForEach-Object { $_.ToString() }
        )
        $scanExitCode = $LASTEXITCODE
        $safeScanLines = $scanLines |
            ForEach-Object { $_.Replace($destination, $artifactInput.name).Replace($staging, ".") }
        [System.IO.File]::WriteAllLines(
            (Join-Path $defenderDirectory ("{0}.txt" -f $artifactInput.role)),
            $safeScanLines,
            [System.Text.UTF8Encoding]::new($false)
        )
        if ($scanExitCode -ne 0) {
            throw "Microsoft Defender did not return a clean result for $($artifactInput.role)."
        }

        $artifact = Get-Item -LiteralPath $destination
        $artifactRecords += [ordered]@{
            role = $artifactInput.role
            file = "artifacts/$($artifactInput.name)"
            bytes = $artifact.Length
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash
            authenticode = $signature.Status.ToString()
            defender_exit_code = $scanExitCode
        }
    }

    if (($artifactRecords.sha256 | Select-Object -Unique).Count -ne $artifactRecords.Count) {
        throw "Pilot artifact roles must not contain identical files."
    }

    $hashLines = $artifactRecords |
        Sort-Object { $_.file } |
        ForEach-Object { "{0} *{1}" -f $_.sha256, $_.file }
    [System.IO.File]::WriteAllLines(
        (Join-Path $staging "INTERNAL_PILOT_SHA256.txt"),
        $hashLines,
        [System.Text.UTF8Encoding]::new($false)
    )

    $manifest = [ordered]@{
        schema_version = 2
        candidate_kind = "unsigned_synthetic_tested_internal_pilot"
        product_version = "0.6.1"
        compatibility_api_version = 13
        supported_languages = @("ja", "en", "ko")
        korean_acceptance = "synthetic_only"
        human_validated = $false
        public_distribution_approved = $false
        created_at_utc = [DateTime]::UtcNow.ToString("o")
        source = [ordered]@{
            assistant_commit = $AssistantCommit.ToLowerInvariant()
            meetily_commit = $MeetilyCommit.ToLowerInvariant()
        }
        acceptance_inputs = [ordered]@{
            routed_audio_corpus_manifest_sha256 = $corpusManifestSha256
            acceptance_ledger_sha256 = $acceptanceLedgerSha256
            whisper_model_manifest_sha256 = $modelManifestSha256
            whisper_model_manifest_signature_sha256 = $modelSignatureSha256
        }
        defender = [ordered]@{
            antivirus_enabled = [bool]$defender.AntivirusEnabled
            realtime_protection_enabled = [bool]$defender.RealTimeProtectionEnabled
            engine_version = [string]$defender.AMEngineVersion
            antivirus_signature_version = [string]$defender.AntivirusSignatureVersion
            antivirus_signature_updated_at = ([DateTime]$defender.AntivirusSignatureLastUpdated).ToUniversalTime().ToString("o")
        }
        artifacts = $artifactRecords
    }
    $manifestJson = $manifest | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText(
        (Join-Path $staging "MANIFEST.json"),
        $manifestJson + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )

    @'
# Meetily Internal Pilot Candidate

This folder contains an unsigned, synthetic-tested internal candidate. It is
not approved for public distribution and is not evidence of human usability.

Verify every artifact against `INTERNAL_PILOT_SHA256.txt`. Review
`MANIFEST.json` for exact assistant/Meetily source commits, the complete
JA/EN/KO acceptance ledger, the verified Whisper manifest hashes, and Defender
provenance. Korean support is synthetic-tested and is not human-validated.
Automatic updating is not enabled for this pilot.
'@ | Set-Content -LiteralPath (Join-Path $staging "README.txt") -Encoding UTF8

    Move-Item -LiteralPath $staging -Destination $output
    $published = $true
    Write-Host "Internal pilot evidence created: $output" -ForegroundColor Green
}
finally {
    if (-not $published -and (Test-Path -LiteralPath $staging)) {
        $resolvedStaging = [System.IO.Path]::GetFullPath($staging)
        $expectedPrefix = [System.IO.Path]::GetFullPath($parent).TrimEnd("\") + "\"
        if ($resolvedStaging.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
        }
    }
}
