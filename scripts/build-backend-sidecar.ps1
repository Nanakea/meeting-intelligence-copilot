[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "release-metadata.ps1")
$release = Get-ReleaseMetadata -Root $repo
Assert-ReleaseVersions -Root $repo -Release $release
$api = Join-Path $repo "apps\api"
$python = Join-Path $api ".venv\Scripts\python.exe"
$entryPoint = Join-Path $api "app\sidecar_main.py"
$runtimeRequirements = Join-Path $api "requirements-dev.lock"
$packagingRequirements = Join-Path $api "requirements-packaging.txt"
$distRoot = Join-Path $repo "dist\backend-sidecar"
$workRoot = Join-Path $repo "tmp\pyinstaller\work"
$specRoot = Join-Path $repo "tmp\pyinstaller\spec"
$artifactName = [IO.Path]::GetFileNameWithoutExtension($release.backend_artifact)
$artifact = Join-Path $distRoot "$artifactName.exe"

$arguments = @(
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name",
    $artifactName,
    "--paths",
    $api,
    "--add-data",
    "$(Join-Path $api 'app/release.json');app",
    "--distpath",
    $distRoot,
    "--workpath",
    $workRoot,
    "--specpath",
    $specRoot,
    $entryPoint
)

Write-Host "Backend sidecar internal-pilot output: $artifact"
Write-Host "Build command: $python $($arguments -join ' ')"

if ($DryRun) {
    Write-Host "DRY RUN: no dependency installation, build, or desktop copy was performed."
    exit 0
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required to prove the backend sidecar source revision."
}
$worktreeStatus = Get-ReleaseSourceStatus -Root $repo
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the product worktree before packaging."
}
if (-not [string]::IsNullOrWhiteSpace($worktreeStatus)) {
    throw @"
Refusing to build a backend sidecar from a dirty product worktree.
Commit the reviewed release changes first so the reported source commit exactly identifies the
artifact's source.
"@
}

if (-not (Test-Path $python)) {
    throw "Product API virtual environment is missing: $python"
}

$expectedPyInstallerVersion = "6.21.0"
$installedPyInstallerVersion = & $python -c "import PyInstaller; print(PyInstaller.__version__)" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw @"
PyInstaller is not installed in the product API virtual environment.
This script never installs packages or accesses the network automatically.
Install the reviewed, hashed toolchain from an approved wheelhouse, then rerun:
  $python -m pip install --no-index --require-hashes --find-links <wheelhouse> -r $packagingRequirements
"@
}
if ($installedPyInstallerVersion.Trim() -ne $expectedPyInstallerVersion) {
    throw "Expected PyInstaller $expectedPyInstallerVersion, found $installedPyInstallerVersion. Install $packagingRequirements from the approved wheelhouse."
}

foreach ($requirementsFile in @($runtimeRequirements, $packagingRequirements)) {
    foreach ($line in Get-Content -LiteralPath $requirementsFile) {
        if ($line -match '^([A-Za-z0-9_.-]+)==([^\s]+)') {
            $packageName = $Matches[1]
            $expectedVersion = $Matches[2]
            $installedVersion = & $python -c "import importlib.metadata as m; print(m.version('$packageName'))" 2>$null
            if ($LASTEXITCODE -ne 0 -or $installedVersion.Trim() -ne $expectedVersion) {
                throw "Expected locked dependency $packageName $expectedVersion, found $installedVersion. Install $requirementsFile from the approved wheelhouse."
            }
        }
    }
}

$artifactFullPath = [System.IO.Path]::GetFullPath($artifact)
$distFullPath = [System.IO.Path]::GetFullPath($distRoot).TrimEnd('\') + '\'
if (-not $artifactFullPath.StartsWith($distFullPath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to replace an artifact outside the ignored distribution directory: $artifactFullPath"
}
if (Test-Path -LiteralPath $artifactFullPath) {
    Remove-Item -LiteralPath $artifactFullPath -Force
}

$previousPythonHashSeed = [Environment]::GetEnvironmentVariable("PYTHONHASHSEED", "Process")
$previousPythonUtf8 = [Environment]::GetEnvironmentVariable("PYTHONUTF8", "Process")
$previousSourceDateEpoch = [Environment]::GetEnvironmentVariable("SOURCE_DATE_EPOCH", "Process")
$sourceCommit = (& git -C $repo rev-parse HEAD).Trim()
$releaseInputHashes = Get-ReleaseInputHashes -Root $repo
$sourceDateEpoch = (& git -C $repo log -1 --format=%ct).Trim()
try {
    [Environment]::SetEnvironmentVariable("PYTHONHASHSEED", "0", "Process")
    [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Process")
    [Environment]::SetEnvironmentVariable("SOURCE_DATE_EPOCH", $sourceDateEpoch, "Process")
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller backend sidecar build failed with exit code $LASTEXITCODE"
    }
}
finally {
    [Environment]::SetEnvironmentVariable("PYTHONHASHSEED", $previousPythonHashSeed, "Process")
    [Environment]::SetEnvironmentVariable("PYTHONUTF8", $previousPythonUtf8, "Process")
    [Environment]::SetEnvironmentVariable("SOURCE_DATE_EPOCH", $previousSourceDateEpoch, "Process")
}
if (-not (Test-Path $artifact)) {
    throw "PyInstaller completed without the expected artifact: $artifact"
}

Assert-ReleaseCheckout -Root $repo -SourceCommit $sourceCommit
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $artifact).Hash
$size = (Get-Item -LiteralPath $artifact).Length
$provenancePath = Join-Path $distRoot "build-provenance.json"
$provenanceTemporary = Join-Path $distRoot "build-provenance.$([Guid]::NewGuid().ToString('N')).tmp"
$provenance = [ordered]@{
    schema_version = 1
    product = "meeting-intelligence-copilot"
    api_version = $release.api_version
    backend_version = $release.version
    release_inputs = $releaseInputHashes
    source_commit = $sourceCommit
    source_date_epoch = [long]$sourceDateEpoch
    python_version = (& $python -c "import platform; print(platform.python_version())").Trim()
    pyinstaller_version = $installedPyInstallerVersion.Trim()
    runtime_lock_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $runtimeRequirements).Hash
    packaging_lock_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $packagingRequirements).Hash
    artifact = [ordered]@{
        name = [System.IO.Path]::GetFileName($artifact)
        bytes = $size
        sha256 = $hash
        authenticode = (Get-AuthenticodeSignature -LiteralPath $artifact).Status.ToString()
    }
}
try {
    [System.IO.File]::WriteAllText(
        $provenanceTemporary,
        ($provenance | ConvertTo-Json -Depth 4),
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $provenanceTemporary -Destination $provenancePath -Force
}
finally {
    if (Test-Path -LiteralPath $provenanceTemporary) {
        Remove-Item -LiteralPath $provenanceTemporary -Force
    }
}
Write-Host "Built backend sidecar: $artifact"
Write-Host "Size bytes: $size"
Write-Host "SHA256: $hash"
Write-Host "Source commit: $sourceCommit"
Write-Host "SOURCE_DATE_EPOCH: $sourceDateEpoch"
Write-Host "Provenance: $provenancePath"
Write-Host "The artifact remains under ignored dist\ and was not copied into the desktop bundle."
