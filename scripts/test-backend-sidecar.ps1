[CmdletBinding()]
param(
    [string]$ArtifactPath,
    [ValidateRange(1, 65535)]
    [int]$Port = 8770,
    [ValidateRange(1, 120)]
    [int]$StartupTimeoutSeconds = 20,
    [ValidateRange(1024, 10485760)]
    [int]$MaxLogBytes = 1048576,
    [switch]$KeepLogs
)

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "release-metadata.ps1")
$release = Get-ReleaseMetadata -Root $repo
if ([string]::IsNullOrWhiteSpace($ArtifactPath)) {
    $ArtifactPath = Join-Path $repo "dist\backend-sidecar\meeting-intelligence-backend-x86_64-pc-windows-msvc.exe"
}
$artifact = (Resolve-Path -LiteralPath $ArtifactPath).Path
if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
    throw "Packaged backend artifact is not a real file: $artifact"
}

$healthUrl = "http://127.0.0.1:$Port/health"
$compatibilityUrl = "http://127.0.0.1:$Port/health/compatibility"
$sessionId = "meeting-intel-packaged-smoke-$([Guid]::NewGuid().ToString('N'))"
$transcriptMarker = "PACKAGED_SMOKE_TRANSCRIPT_MARKER_$([Guid]::NewGuid().ToString('N'))"
$tokenBytes = New-Object byte[] 32
$tokenGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $tokenGenerator.GetBytes($tokenBytes)
}
finally {
    $tokenGenerator.Dispose()
}
$capabilityToken = -join ($tokenBytes | ForEach-Object { $_.ToString("x2") })
$tokenHeaderName = "X-Meeting-Intelligence-Token"
$authHeaders = @{ $tokenHeaderName = $capabilityToken }
$logRoot = Join-Path ([System.IO.Path]::GetTempPath()) $sessionId
$cacheRoot = Join-Path ([System.IO.Path]::GetTempPath()) "$sessionId-cache"
$stdoutLog = Join-Path $logRoot "backend.stdout.log"
$stderrLog = Join-Path $logRoot "backend.stderr.log"
$backendProcess = $null
$webSocket = $null
$previousPort = [Environment]::GetEnvironmentVariable("MEETING_INTELLIGENCE_BACKEND_PORT", "Process")
$previousToken = [Environment]::GetEnvironmentVariable("MEETING_INTELLIGENCE_TOKEN", "Process")
$previousCacheDir = [Environment]::GetEnvironmentVariable(
    "MEETING_INTELLIGENCE_CACHE_DIR",
    "Process"
)
$startupTimer = [System.Diagnostics.Stopwatch]::new()
$coldStartMilliseconds = $null

function Assert-Condition {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Assert-PortAvailable {
    param([int]$CandidatePort)

    $probe = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        $CandidatePort
    )
    try {
        $probe.Start()
    }
    catch {
        throw "Acceptance port $CandidatePort is already in use; choose an unused loopback port."
    }
    finally {
        $probe.Stop()
    }
}

function Remove-AcceptanceCache {
    if (-not (Test-Path -LiteralPath $cacheRoot)) {
        return
    }
    $tempRoot = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::GetTempPath()
    ).TrimEnd('\') + '\'
    $resolvedCacheRoot = [System.IO.Path]::GetFullPath($cacheRoot)
    if (
        -not $resolvedCacheRoot.StartsWith(
            $tempRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        (Split-Path -Leaf $resolvedCacheRoot) -ne "$sessionId-cache"
    ) {
        throw "Refusing to remove an unexpected acceptance-cache path."
    }
    Remove-Item -LiteralPath $resolvedCacheRoot -Recurse -Force
}

function Receive-WebSocketJson {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket,
        [int]$TimeoutSeconds = 5
    )

    $buffer = New-Object byte[] 65536
    $segment = [System.ArraySegment[byte]]::new($buffer)
    $stream = [System.IO.MemoryStream]::new()
    $timeout = [System.Threading.CancellationTokenSource]::new(
        [TimeSpan]::FromSeconds($TimeoutSeconds)
    )
    try {
        do {
            $result = $Socket.ReceiveAsync($segment, $timeout.Token).GetAwaiter().GetResult()
            if ($result.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) {
                throw "Backend closed the WebSocket before the expected snapshot arrived."
            }
            $stream.Write($buffer, 0, $result.Count)
        } while (-not $result.EndOfMessage)

        $json = [System.Text.Encoding]::UTF8.GetString($stream.ToArray())
        return $json | ConvertFrom-Json
    }
    finally {
        $timeout.Dispose()
        $stream.Dispose()
    }
}

Assert-PortAvailable -CandidatePort $Port
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
New-Item -ItemType Directory -Force -Path $cacheRoot | Out-Null

try {
    [Environment]::SetEnvironmentVariable(
        "MEETING_INTELLIGENCE_BACKEND_PORT",
        $Port.ToString(),
        "Process"
    )
    [Environment]::SetEnvironmentVariable(
        "MEETING_INTELLIGENCE_TOKEN",
        $capabilityToken,
        "Process"
    )
    [Environment]::SetEnvironmentVariable(
        "MEETING_INTELLIGENCE_CACHE_DIR",
        $cacheRoot,
        "Process"
    )
    $startupTimer.Start()
    $backendProcess = Start-Process `
        -FilePath $artifact `
        -WorkingDirectory (Split-Path -Parent $artifact) `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru
    [Environment]::SetEnvironmentVariable(
        "MEETING_INTELLIGENCE_BACKEND_PORT",
        $previousPort,
        "Process"
    )
    [Environment]::SetEnvironmentVariable(
        "MEETING_INTELLIGENCE_TOKEN",
        $previousToken,
        "Process"
    )
    [Environment]::SetEnvironmentVariable(
        "MEETING_INTELLIGENCE_CACHE_DIR",
        $previousCacheDir,
        "Process"
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    $compatibility = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($backendProcess.HasExited) {
            throw "Packaged backend exited before readiness with code $($backendProcess.ExitCode)."
        }
        try {
            $compatibility = Invoke-RestMethod `
                -Uri $compatibilityUrl `
                -Method Get `
                -Headers $authHeaders `
                -TimeoutSec 2
            $startupTimer.Stop()
            $coldStartMilliseconds = $startupTimer.ElapsedMilliseconds
            break
        }
        catch {
            Start-Sleep -Milliseconds 200
        }
    }
    Assert-Condition ($null -ne $compatibility) "Compatibility health did not become ready at $compatibilityUrl."

    $health = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
    $healthProperties = @($health.PSObject.Properties.Name)
    Assert-Condition (
        $healthProperties.Count -eq 1 -and $health.status -eq "ok"
    ) "Minimal /health contract changed."
    Assert-Condition (
        $compatibility.status -eq "ok" -and
        $compatibility.product -eq "meeting-intelligence-copilot" -and
        [int]$compatibility.api_version -eq 13 -and
        $compatibility.backend_version -eq $release.version -and
        $compatibility.capability_auth -eq $true
    ) "Compatibility contract is not the expected product/API/backend version."

    $missingTokenRejected = $false
    try {
        $null = Invoke-WebRequest -Uri $compatibilityUrl -Method Get -TimeoutSec 2
    }
    catch {
        $missingTokenRejected = [int]$_.Exception.Response.StatusCode -eq 401
    }
    Assert-Condition $missingTokenRejected "Compatibility endpoint did not fail closed without a token."

    $invalidTokenRejected = $false
    try {
        $null = Invoke-WebRequest `
            -Uri $compatibilityUrl `
            -Method Get `
            -Headers @{ $tokenHeaderName = ("0" * 64) } `
            -TimeoutSec 2
    }
    catch {
        $invalidTokenRejected = [int]$_.Exception.Response.StatusCode -eq 401
    }
    Assert-Condition $invalidTokenRejected "Compatibility endpoint did not fail closed with an invalid token."

    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop)
    Assert-Condition ($listeners.Count -gt 0) "No listener was found for packaged backend port $Port."
    $nonLoopback = @($listeners | Where-Object { $_.LocalAddress -ne "127.0.0.1" })
    Assert-Condition ($nonLoopback.Count -eq 0) "Packaged backend exposed a non-loopback listener."

    $webSocket = [System.Net.WebSockets.ClientWebSocket]::new()
    # Exercise the same allow-listed Origin used by the packaged Tauri webview.
    # Some Windows .NET versions otherwise synthesize the loopback WS URL as
    # Origin, which correctly fails the backend's browser-origin policy.
    $webSocket.Options.SetRequestHeader("Origin", "http://tauri.localhost")
    $webSocket.Options.AddSubProtocol("meeting-intelligence-v1")
    $webSocket.Options.AddSubProtocol("token.$capabilityToken")
    $connectTimeout = [System.Threading.CancellationTokenSource]::new(
        [TimeSpan]::FromSeconds(5)
    )
    try {
        $null = $webSocket.ConnectAsync(
            [Uri]::new("ws://127.0.0.1:$Port/ws/meeting/$sessionId"),
            $connectTimeout.Token
        ).GetAwaiter().GetResult()
    }
    finally {
        $connectTimeout.Dispose()
    }

    $initial = Receive-WebSocketJson -Socket $webSocket
    Assert-Condition (
        [int]$initial.version -eq 0 -and $initial.meeting_id -eq $sessionId
    ) "Initial packaged WebSocket snapshot was not the expected meeting v0."

    $body = @{
        lang = "en"
        payload = @{
            text = "Every morning one person copies rows into a spreadsheet. $transcriptMarker"
            source = "Audio"
            sequence_id = 0
            is_partial = $false
            confidence = 0.9
            audio_start_time = 0.0
            audio_end_time = 3.0
        }
    } | ConvertTo-Json -Depth 4
    $ingest = Invoke-RestMethod `
        -Uri "http://127.0.0.1:$Port/ingest/meetily/$sessionId" `
        -Method Post `
        -Headers $authHeaders `
        -ContentType "application/json" `
        -Body $body `
        -TimeoutSec 5
    Assert-Condition (
        $ingest.status -eq "applied" -and
        [int]$ingest.received_sequence_id -eq 0 -and
        [int]$ingest.next_expected_sequence_id -eq 1 -and
        [int]$ingest.state_version -eq 1
    ) "Packaged synthetic transcript ingest did not return the expected API v13 acknowledgement."

    $updated = Receive-WebSocketJson -Socket $webSocket
    Assert-Condition (
        [int]$updated.version -eq 1 -and
        $updated.meeting_id -eq $sessionId -and
        @($updated.pain_points).Count -ge 1 -and
        -not (@($updated.PSObject.Properties.Name) -contains "transcript")
    ) "Packaged WebSocket did not publish the expected v1 intelligence snapshot."

    $cleanup = Invoke-RestMethod `
        -Uri "http://127.0.0.1:$Port/meeting/$sessionId" `
        -Method Delete `
        -Headers $authHeaders `
        -TimeoutSec 5
    Assert-Condition (
        $cleanup.reset -eq $true
    ) "Packaged session cleanup did not purge transient recovery state."
    $late = Invoke-RestMethod `
        -Uri "http://127.0.0.1:$Port/ingest/meetily/$sessionId" `
        -Method Post `
        -Headers $authHeaders `
        -ContentType "application/json" `
        -Body $body `
        -TimeoutSec 5
    Assert-Condition (
        $late.status -eq "rejected" -and
        [int]$late.state_version -eq 0
    ) "Packaged backend accepted a late event after session cleanup."

    Write-Host "PASS: minimal and compatibility health contracts"
    Write-Host "PASS: loopback-only listener on 127.0.0.1:$Port"
    Write-Host "PASS: synthetic ingest, compact WebSocket snapshot, cleanup, and late-event rejection"
}
finally {
    [Environment]::SetEnvironmentVariable(
        "MEETING_INTELLIGENCE_BACKEND_PORT",
        $previousPort,
        "Process"
    )
    [Environment]::SetEnvironmentVariable(
        "MEETING_INTELLIGENCE_TOKEN",
        $previousToken,
        "Process"
    )
    [Environment]::SetEnvironmentVariable(
        "MEETING_INTELLIGENCE_CACHE_DIR",
        $previousCacheDir,
        "Process"
    )
    if ($null -ne $webSocket) {
        $webSocket.Dispose()
    }
    if ($null -ne $backendProcess -and -not $backendProcess.HasExited) {
        & taskkill /PID $backendProcess.Id /T /F *> $null
    }
    if ($null -ne $backendProcess) {
        try {
            $null = $backendProcess.WaitForExit(5000)
        }
        catch {
            # Listener verification below is authoritative for cleanup.
        }
        $backendProcess.Dispose()
    }
    Remove-AcceptanceCache
}

$listenerGone = $false
for ($attempt = 0; $attempt -lt 25; $attempt++) {
    $remaining = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($remaining.Count -eq 0) {
        $listenerGone = $true
        break
    }
    Start-Sleep -Milliseconds 200
}
Assert-Condition $listenerGone "Packaged backend listener remained after process-tree shutdown."

$operationalLogs = ""
foreach ($path in @($stdoutLog, $stderrLog)) {
    if (Test-Path -LiteralPath $path) {
        $logLength = (Get-Item -LiteralPath $path).Length
        Assert-Condition (
            $logLength -le $MaxLogBytes
        ) "Operational log exceeded the $MaxLogBytes-byte acceptance limit: $path"
        $operationalLogs += Get-Content -Raw -LiteralPath $path
    }
}
Assert-Condition (
    -not $operationalLogs.Contains($transcriptMarker)
) "Operational logs exposed synthetic transcript content."
Assert-Condition (
    -not $operationalLogs.Contains($capabilityToken)
) "Operational logs exposed the local capability token."

$artifactInfo = Get-Item -LiteralPath $artifact
$artifactHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $artifact).Hash
$authenticodeStatus = (Get-AuthenticodeSignature -LiteralPath $artifact).Status
Write-Host "PASS: owned process tree stopped and listener disappeared"
Write-Host "PASS: operational stdout/stderr contain no synthetic transcript marker"
Write-Host "PASS: missing/invalid capability tokens rejected and token absent from operational logs"
Write-Host "Artifact: $artifact"
Write-Host "Bytes: $($artifactInfo.Length)"
Write-Host "SHA-256: $artifactHash"
Write-Host "Cold start ms: $coldStartMilliseconds"
Write-Host "Authenticode: $authenticodeStatus"

if ($KeepLogs) {
    Write-Host "Logs retained for inspection: $logRoot"
}
else {
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    $resolvedLogRoot = [System.IO.Path]::GetFullPath($logRoot)
    if (
        -not $resolvedLogRoot.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not (Split-Path -Leaf $resolvedLogRoot).StartsWith("meeting-intel-packaged-smoke-")
    ) {
        throw "Refusing to remove an unexpected acceptance-log path: $resolvedLogRoot"
    }
    Remove-Item -LiteralPath $resolvedLogRoot -Recurse -Force
    Write-Host "PASS: bounded temporary acceptance logs removed after inspection"
}
