param(
    [ValidateSet('menu', 'start', 'stop', 'restart', 'status')]
    [string]$Action = 'menu'
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ClientExe = if ($env:SHORTS_TUNNEL_CLIENT_EXE) { $env:SHORTS_TUNNEL_CLIENT_EXE } else { Join-Path $env:USERPROFILE 'tunnel-client\updates\v0.0.19\extracted\tunnel-client.exe' }
$ProfileName = 'shorts-prototype'
$ProfilePath = Join-Path $env:APPDATA 'tunnel-client\shorts-prototype.yaml'
$KeyFile = Join-Path $env:LOCALAPPDATA 'shorts-prototype\secrets\runtime-key.txt'
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'shorts-prototype\runtime'
$LogRoot = Join-Path $env:LOCALAPPDATA 'shorts-prototype\logs'
$PidFile = Join-Path $RuntimeRoot 'tunnel-client.pid'
$StartedFile = Join-Path $RuntimeRoot 'started-at.txt'
$HealthBase = 'http://127.0.0.1:8765'
New-Item -ItemType Directory -Force -Path $RuntimeRoot, $LogRoot | Out-Null

function Get-ManagedTunnelProcesses {
    $expectedExe = [System.IO.Path]::GetFullPath($ClientExe)
    @(Get-CimInstance -ClassName Win32_Process | Where-Object {
        $_.Name -ieq 'tunnel-client.exe' -and
        $_.ExecutablePath -and
        ([System.IO.Path]::GetFullPath($_.ExecutablePath) -ieq $expectedExe) -and
        $_.CommandLine -match '(?i)\brun\s+--profile\s+shorts-prototype(?:\s|$)'
    })
}
function Get-Endpoint {
    param([string]$Path)
    try {
        $response = Invoke-WebRequest -Uri ($HealthBase + $Path) -UseBasicParsing -TimeoutSec 3
        [pscustomobject]@{ Ok = ($response.StatusCode -eq 200); StatusCode = [int]$response.StatusCode; Body = [string]$response.Content }
    }
    catch {
        [pscustomobject]@{ Ok = $false; StatusCode = 0; Body = $_.Exception.Message }
    }
}
function Show-TunnelStatus {
    $processes = @(Get-ManagedTunnelProcesses)
    Write-Host ''
    Write-Host 'SHORTS PROTOTYPE TUNNEL' -ForegroundColor Cyan
    Write-Host '-----------------------'
    if ($processes.Count -eq 0) {
        Write-Host 'State: STOPPED' -ForegroundColor Yellow
        $health = Get-Endpoint '/readyz'
        if ($health.Ok) { Write-Host 'Warning: port 8765 is ready, but the expected tunnel-client process was not found.' -ForegroundColor Yellow }
        return
    }
    if ($processes.Count -gt 1) {
        Write-Host "State: RUNNING ($($processes.Count) matching tunnel-client processes)" -ForegroundColor Yellow
    } else {
        Write-Host 'State: RUNNING' -ForegroundColor Green
    }
    foreach ($process in $processes) { Write-Host "Tunnel-client PID: $($process.ProcessId)" }
    $healthz = Get-Endpoint '/healthz'
    $readyz = Get-Endpoint '/readyz'
    $mcp = Get-Endpoint '/health/mcp'
    Write-Host ("Health: " + $(if ($healthz.Ok) { 'LIVE' } else { 'UNAVAILABLE' }))
    Write-Host ("Ready:  " + $(if ($readyz.Ok -and $readyz.Body.Trim() -eq 'ready') { 'READY' } else { 'NOT READY' }))
    if ($mcp.Ok) {
        try {
            $mcpData = $mcp.Body | ConvertFrom-Json
            $mcpStatus = if ($mcpData.status) { $mcpData.status } else { 'unknown' }
            $mcpState = if ($mcpData.state) { $mcpData.state } else { 'unknown' }
            $toolCount = 0
            if ($null -ne $mcpData.details.tools_list.retained_count) { $toolCount = [int]$mcpData.details.tools_list.retained_count }
            Write-Host "MCP:    $mcpStatus / $mcpState (discovered tools: $toolCount)"
        } catch { Write-Host 'MCP:    health endpoint responded, but its status could not be parsed.' }
    } else { Write-Host 'MCP:    status endpoint unavailable.' }
    if (Test-Path $StartedFile) { Write-Host "Started: $(Get-Content -LiteralPath $StartedFile -Raw)" }
    Write-Host "Logs:   $LogRoot"
}
function Start-Tunnel {
    $running = @(Get-ManagedTunnelProcesses)
    if ($running.Count -gt 0) {
        Write-Host "Tunnel is already running (PID $($running[0].ProcessId)); not starting a duplicate." -ForegroundColor Yellow
        Set-Content -LiteralPath $PidFile -Value ([string]$running[0].ProcessId) -Encoding ASCII
        Show-TunnelStatus
        return
    }
    foreach ($requiredPath in @($ClientExe, $ProfilePath, $KeyFile)) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) { throw "Required file not found: $requiredPath" }
    }
    $runtimeKey = (Get-Content -LiteralPath $KeyFile -Raw).Trim()
    if ([string]::IsNullOrWhiteSpace($runtimeKey)) { throw 'Runtime API key file is empty.' }
    $stdoutLog = Join-Path $LogRoot 'tunnel.stdout.log'
    $stderrLog = Join-Path $LogRoot 'tunnel.stderr.log'
    Set-Content -LiteralPath $stdoutLog -Value '' -Encoding UTF8
    Set-Content -LiteralPath $stderrLog -Value '' -Encoding UTF8
    $env:CONTROL_PLANE_API_KEY = $runtimeKey
    try {
        $startArgs = @{
            FilePath = $ClientExe
            ArgumentList = @('run', '--profile', $ProfileName)
            WorkingDirectory = $ProjectRoot
            WindowStyle = 'Hidden'
            RedirectStandardOutput = $stdoutLog
            RedirectStandardError = $stderrLog
            PassThru = $true
        }
        $process = Start-Process @startArgs
    }
    finally {
        Remove-Item Env:\CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
        Remove-Variable runtimeKey -ErrorAction SilentlyContinue
    }
    Set-Content -LiteralPath $PidFile -Value ([string]$process.Id) -Encoding ASCII
    Set-Content -LiteralPath $StartedFile -Value (Get-Date -Format 'yyyy-MM-dd HH:mm:ss K') -Encoding UTF8
    Write-Host "Started tunnel-client (PID $($process.Id)). Checking readiness..." -ForegroundColor Cyan
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        $process.Refresh()
        if ($process.HasExited) { break }
        $response = Get-Endpoint '/readyz'
        if ($response.Ok -and $response.Body.Trim() -eq 'ready') { $ready = $true; break }
    }
    if (-not $ready) {
        $process.Refresh()
        if ($process.HasExited) {
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
            $tail = if (Test-Path $stderrLog) { (Get-Content $stderrLog -Tail 20) -join [Environment]::NewLine } else { '' }
            throw ("Tunnel-client exited during startup. Check logs at " + $LogRoot + [Environment]::NewLine + $tail)
        }
        Write-Host "Process is running, but /readyz did not become ready within 30 seconds." -ForegroundColor Yellow
        Write-Host "Inspect logs: $stderrLog"
        return
    }
    Write-Host 'Tunnel is READY. It is running in the background.' -ForegroundColor Green
    Show-TunnelStatus
}
function Stop-Tunnel {
    $processes = @(Get-ManagedTunnelProcesses)
    if ($processes.Count -eq 0) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        Write-Host 'Tunnel is already stopped.' -ForegroundColor Yellow
        return
    }
    foreach ($process in $processes) {
        Write-Host "Stopping tunnel-client PID $($process.ProcessId) and its child processes..."
        & "$env:SystemRoot\System32\taskkill.exe" /PID $process.ProcessId /T /F | Out-Null
    }
    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 500
        $remaining = @(Get-ManagedTunnelProcesses)
    } while ($remaining.Count -gt 0 -and (Get-Date) -lt $deadline)
    if ($remaining.Count -gt 0) { throw "Tunnel process did not stop. Remaining PID(s): $($remaining.ProcessId -join ', ')" }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Remove-Item $StartedFile -Force -ErrorAction SilentlyContinue
    Write-Host 'Tunnel stopped. Its local MCP server child process tree was terminated.' -ForegroundColor Green
}
function Invoke-Action {
    param([string]$SelectedAction)
    switch ($SelectedAction) {
        'start' { Start-Tunnel }
        'stop' { Stop-Tunnel }
        'restart' { Stop-Tunnel; Start-Sleep -Seconds 1; Start-Tunnel }
        'status' { Show-TunnelStatus }
        default { throw "Unknown action: $SelectedAction" }
    }
}
if ($Action -ne 'menu') {
    Invoke-Action -SelectedAction $Action
    exit 0
}
while ($true) {
    Write-Host ''
    Write-Host '=============================='
    Write-Host '     SHORTS PROTOTYPE TUNNEL'
    Write-Host '=============================='
    Write-Host '[1] Start tunnel'
    Write-Host '[2] Stop tunnel'
    Write-Host '[3] Restart tunnel'
    Write-Host '[4] Check status'
    Write-Host '[Q] Quit'
    $choice = Read-Host 'Choose an action'
    try {
        switch ($choice.ToLowerInvariant()) {
            '1' { Invoke-Action 'start' }
            '2' { Invoke-Action 'stop' }
            '3' { Invoke-Action 'restart' }
            '4' { Invoke-Action 'status' }
            'q' { return }
            default { Write-Host 'Please choose 1, 2, 3, 4, or Q.' }
        }
    } catch { Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red }
    if ($choice -ne 'q') { [void](Read-Host 'Press Enter to return to the menu') }
}
