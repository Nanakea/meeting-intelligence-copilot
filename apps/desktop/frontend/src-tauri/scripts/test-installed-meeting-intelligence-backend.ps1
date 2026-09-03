[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArtifactPath,

    [ValidateRange(1, 65535)]
    [int]$Port = 8772,

    [ValidateRange(1, 120)]
    [int]$StartupTimeoutSeconds = 20,

    [ValidateRange(1024, 10485760)]
    [int]$MaxLogBytes = 1048576
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$artifact = (Resolve-Path -LiteralPath $ArtifactPath).Path
if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
    throw "Installed backend artifact is unavailable."
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

function New-CapabilityToken {
    $bytes = [byte[]]::new(32)
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Invoke-BackendJson {
    param(
        [string]$BaseUrl,
        [string]$Path,
        [string]$Token,
        [ValidateSet("GET", "POST", "DELETE")]
        [string]$Method = "GET",
        [object]$Body
    )

    $parameters = @{
        Uri = "$BaseUrl$Path"
        Method = $Method
        Headers = @{ "X-Meeting-Intelligence-Token" = $Token }
        TimeoutSec = 5
        UseBasicParsing = $true
    }
    if ($null -ne $Body) {
        $parameters.ContentType = "application/json; charset=utf-8"
        $parameters.Body = $Body | ConvertTo-Json -Depth 8 -Compress
    }
    return Invoke-RestMethod @parameters
}

if ($Port -eq 8772) {
    $Port = Get-AvailableLoopbackPort
}
else {
    $probe = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        $Port
    )
    try {
        $probe.Start()
    }
    finally {
        $probe.Stop()
    }
}

$token = New-CapabilityToken
$sessionId = "meeting-intel-lifecycle-$([guid]::NewGuid().ToString('N'))"
$marker = "LIFECYCLE_MARKER_$([guid]::NewGuid().ToString('N'))"
$root = Join-Path $env:TEMP "meeting-intelligence-lifecycle-$([guid]::NewGuid().ToString('N'))"
$cache = Join-Path $root "cache"
$stdout = Join-Path $root "backend.stdout.log"
$stderr = Join-Path $root "backend.stderr.log"
$process = $null
$baseUrl = "http://127.0.0.1:$Port"

[System.IO.Directory]::CreateDirectory($cache) | Out-Null

$previousPort = $env:MEETING_INTELLIGENCE_BACKEND_PORT
$previousToken = $env:MEETING_INTELLIGENCE_TOKEN
$previousCache = $env:MEETING_INTELLIGENCE_CACHE_DIR
try {
    try {
        $env:MEETING_INTELLIGENCE_BACKEND_PORT = [string]$Port
        $env:MEETING_INTELLIGENCE_TOKEN = $token
        $env:MEETING_INTELLIGENCE_CACHE_DIR = $cache
        $process = Start-Process `
            -FilePath $artifact `
            -PassThru `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr
    }
    finally {
        $env:MEETING_INTELLIGENCE_BACKEND_PORT = $previousPort
        $env:MEETING_INTELLIGENCE_TOKEN = $previousToken
        $env:MEETING_INTELLIGENCE_CACHE_DIR = $previousCache
    }

    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    $compatibility = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($process.HasExited) {
            throw "Installed backend exited before readiness."
        }
        try {
            $compatibility = Invoke-BackendJson `
                -BaseUrl $baseUrl `
                -Path "/health/compatibility" `
                -Token $token
            break
        }
        catch {
            Start-Sleep -Milliseconds 200
        }
    }
    if ($null -eq $compatibility) {
        throw "Installed backend compatibility readiness timed out."
    }
    if (
        $compatibility.status -ne "ok" -or
        $compatibility.product -ne "meeting-intelligence-copilot" -or
        $compatibility.api_version -ne 13 -or
        $compatibility.capability_auth -ne $true
    ) {
        throw "Installed backend compatibility contract is invalid."
    }

    $payload = @{
        adapter = "meetily"
        lang = "en"
        payload = @{
            text = "Every morning one person downloads the order file and copies it manually. $marker"
            source = "Audio"
            sequence_id = 0
            is_partial = $false
            confidence = 0.9
            audio_start_time = 0.0
            audio_end_time = 3.0
        }
    }
    $acknowledgement = Invoke-BackendJson `
        -BaseUrl $baseUrl `
        -Path "/ingest/live/$sessionId" `
        -Token $token `
        -Method "POST" `
        -Body $payload
    if (
        $acknowledgement.status -ne "applied" -or
        $acknowledgement.received_sequence_id -ne 0 -or
        $acknowledgement.next_expected_sequence_id -ne 1 -or
        $acknowledgement.state_version -ne 1
    ) {
        throw "Installed backend ingest acknowledgement is invalid."
    }

    $duplicate = Invoke-BackendJson `
        -BaseUrl $baseUrl `
        -Path "/ingest/live/$sessionId" `
        -Token $token `
        -Method "POST" `
        -Body $payload
    if (
        $duplicate.status -ne "duplicate" -or
        $duplicate.next_expected_sequence_id -ne 1 -or
        $duplicate.state_version -ne 1
    ) {
        throw "Installed backend duplicate acknowledgement is invalid."
    }

    $cleanup = Invoke-BackendJson `
        -BaseUrl $baseUrl `
        -Path "/meeting/$sessionId" `
        -Token $token `
        -Method "DELETE"
    if ($cleanup.reset -ne $true) {
        throw "Installed backend session cleanup is incomplete."
    }
}
finally {
    if ($null -ne $process -and -not $process.HasExited) {
        & taskkill.exe /PID $process.Id /T /F | Out-Null
        $null = $process.WaitForExit(5000)
    }

    $logFailure = $null
    foreach ($path in @($stdout, $stderr)) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $item = Get-Item -LiteralPath $path
            if ($item.Length -gt $MaxLogBytes) {
                $logFailure = "Installed backend operational log exceeded its size limit."
            }
            $content = Get-Content -Raw -LiteralPath $path
            if ($null -eq $content) {
                $content = ""
            }
            if ($content.Contains($token) -or $content.Contains($marker)) {
                $logFailure = "Installed backend operational log exposed protected content."
            }
        }
    }

    $resolvedRoot = [System.IO.Path]::GetFullPath($root)
    $expectedRoot = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd("\") + "\"
    if (
        -not $resolvedRoot.StartsWith($expectedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not ([System.IO.Path]::GetFileName($resolvedRoot)).StartsWith(
            "meeting-intelligence-lifecycle-",
            [System.StringComparison]::Ordinal
        )
    ) {
        throw "Refusing to remove an unexpected lifecycle acceptance path."
    }
    if (Test-Path -LiteralPath $resolvedRoot) {
        [System.IO.Directory]::Delete($resolvedRoot, $true)
    }
    if ($null -ne $logFailure) {
        throw $logFailure
    }
}

Write-Host "PASS installed backend compatibility, ingest, dedupe, cleanup, and log safety" `
    -ForegroundColor Green
