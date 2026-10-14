# FRIDAY.exe clean-machine journeys inside Windows Sandbox (PKG-001).
#
# Run by the .wsb LogonCommand that scripts/validate_exe_sandbox.py writes.
# Inputs are read-only under C:\In (the zip with its .sha256, and this
# script); the only way out is the writable C:\Out. Records are kept in
# memory and mirrored to C:\Out\result.partial.json after every journey;
# result.json appears only once the last journey has run, so a half-run
# sandbox never reads as finished. Each record is {id, ok, detail}; the host
# re-judges SBX-03 and the SBX-00 digest itself.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$out = 'C:\Out'
$partial = Join-Path $out 'result.partial.json'
$records = New-Object System.Collections.ArrayList
$started = Get-Date
$ui = 'http://127.0.0.1:8770'
$app = Join-Path $env:LOCALAPPDATA 'Programs\FRIDAY'
$exe = Join-Path $app 'FRIDAY.exe'
$logStash = Join-Path $env:TEMP 'friday-logs'
# A synthetic PIN made here and never written out: it only has to survive a restart.
$pin = -join (1..12 | ForEach-Object { [char](Get-Random -Minimum 97 -Maximum 123) })
# A fresh fact per sandbox, so nothing but this run's own task can satisfy the recall after the restart.
$canary = 'sandbox canary ' + [guid]::NewGuid().ToString('N')
$state = $null
$runId = $null
$stack = $null

# Under --no-agent nothing ticks the objective driver (only the voice worker
# does), so the installed FRIDAY.exe itself drives the run through the same
# dispatch and executor friday.objective_cli uses: FRIDAY.exe <script.py>
# runs bundled code (friday/launcher.py maybe_run_as_python). Prints one JSON line.
$driverPath = Join-Path $env:TEMP 'friday_sandbox_drive.py'
$driver = @'
import asyncio
import json
import sys
import time

from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.objective_cli import _db, build_dispatch


async def drive(run_id):
    store = _db()
    executor = ContinuousTaskExecutor(store, build_dispatch(), executor_id="sandbox-guest")
    started = await executor.start(run_id)
    deadline = time.monotonic() + 240
    run = store.objective_run(run_id)
    while started and run is not None and run["status"] not in O.RUN_TERMINAL and time.monotonic() < deadline:
        await asyncio.sleep(2)
        run = store.objective_run(run_id)
    executor.stop()
    return started, run


started, run = asyncio.run(drive(sys.argv[1]))
print(json.dumps({"started": started, "status": run and run["status"],
                  "summary": run and run.get("summary")}, default=str))
'@
[IO.File]::WriteAllText($driverPath, $driver, (New-Object Text.UTF8Encoding $false))

function Save-Partial {
    $json = ConvertTo-Json -InputObject $records.ToArray() -Depth 8
    [IO.File]::WriteAllText($partial, $json, (New-Object Text.UTF8Encoding $false))
}

function Add-Record([string]$id, [bool]$ok, $detail) {
    [void]$records.Add([ordered]@{ id = $id; ok = $ok; detail = $detail })
    Save-Partial
}

# A journey's body returns @{ ok; detail }. A throw is recorded as a failed
# journey with its message, and the next journey still runs.
function Invoke-Journey([string]$id, [scriptblock]$body) {
    try {
        $result = & $body | Select-Object -Last 1
        Add-Record $id ([bool]$result.ok) $result.detail
    } catch {
        Add-Record $id $false @{ error = "$($_.Exception.Message)" }
    }
}

function Invoke-Friday([string[]]$arguments, [int]$seconds = 300) {
    $stdout = Join-Path $env:TEMP ('friday-' + [guid]::NewGuid() + '.out')
    $process = Start-Process -FilePath $exe -ArgumentList $arguments -NoNewWindow -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError "$stdout.err" -WorkingDirectory 'C:\'
    $null = $process.Handle  # keeps ExitCode readable after a redirected Start-Process
    if (-not $process.WaitForExit($seconds * 1000)) {
        $process.Kill()
        return @{ code = 124; out = ''; err = "timed out after $seconds s" }
    }
    return @{ code = $process.ExitCode; out = [IO.File]::ReadAllText($stdout); err = [IO.File]::ReadAllText("$stdout.err") }
}

function Test-Port([int]$port) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        # A refused connection throws; for a port probe that simply means closed.
        return ($client.ConnectAsync('127.0.0.1', $port).Wait(500) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Get-Health {
    try {
        return (Invoke-WebRequest -Uri "$ui/health" -UseBasicParsing -TimeoutSec 3).StatusCode
    } catch {
        return 0  # not answering yet
    }
}

function Wait-Until([scriptblock]$probe, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (& $probe) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Invoke-Api([string]$method, [string]$path, $body, $session) {
    if ($null -eq $body) {
        $response = Invoke-WebRequest -Uri "$ui$path" -Method $method -WebSession $session -UseBasicParsing -TimeoutSec 30
    } else {
        $json = ConvertTo-Json -InputObject $body -Compress -Depth 6
        $response = Invoke-WebRequest -Uri "$ui$path" -Method $method -WebSession $session -UseBasicParsing `
            -TimeoutSec 30 -ContentType 'application/json' -Body $json
    }
    return ($response.Content | ConvertFrom-Json)
}

function Start-Stack([string]$name) {
    $log = Join-Path $env:TEMP "friday-stack-$name.log"
    $script:stack = Start-Process -FilePath $exe -ArgumentList @('--no-agent', '--no-browser') -PassThru `
        -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err" -WorkingDirectory 'C:\'
    $mcp = Wait-Until { Test-Port 8000 } 150
    $health = Wait-Until { (Get-Health) -eq 200 } 90
    return [ordered]@{ mcp_listening = $mcp; ui_health_200 = $health; launcher_alive = (-not $script:stack.HasExited) }
}

function Stop-Stack {
    $stop = Invoke-Friday @('--stop') 90
    $free = Wait-Until { -not (Test-Port 8000) -and -not (Test-Port 8770) } 20
    $exited = ($null -eq $script:stack) -or $script:stack.WaitForExit(10000)
    return [ordered]@{ ports_free = $free; launcher_exited = $exited; stop = $stop.out.Trim() }
}

function Get-DefenderEvents {
    try {
        return @(Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-Windows Defender/Operational'; StartTime = $started } -ErrorAction Stop |
            Select-Object -First 50 |
            ForEach-Object { "$($_.TimeCreated.ToString('s')) $($_.Id) $(($_.Message -split "`n")[0].Trim())" })
    } catch {
        if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { return @() }
        return @("unreadable: $($_.Exception.Message)")
    }
}

function Get-RunStatus($session, [string]$id) {
    return Invoke-Api 'GET' ('/api/objective/status?run_id=' + [uri]::EscapeDataString($id)) $null $session
}

# One real task: accepted through the gated control room, executed by the
# installed EXE, then read back through the control room. ok only when the
# run COMPLETED with every task SUCCEEDED.
function Invoke-Task($session, [string]$objective, [string]$capability, $arguments) {
    $tasks = ConvertTo-Json -Compress -Depth 4 -InputObject @(@{ capability = $capability; arguments = $arguments; dependencies = @() })
    $start = Invoke-Api 'POST' '/api/objective' @{ objective = $objective; tasks = $tasks } $session
    $id = "$($start.run_id)"
    if (-not $start.ok -or -not $id) { return [ordered]@{ ok = $false; accepted = $start } }
    $drive = Invoke-Friday @($driverPath, $id) 300
    $status = Get-RunStatus $session $id
    $taskRows = @($status.output.tasks)
    $done = ($status.status -eq 'succeeded' -and "$($status.output.status)" -eq 'COMPLETED' -and $taskRows.Count -eq 1 -and
             "$($taskRows[0].status)" -eq 'SUCCEEDED')
    return [ordered]@{ ok = ($drive.code -eq 0 -and $done); run_id = $id; run_status = "$($status.output.status)";
                       tasks = $taskRows; driver = $drive.out.Trim(); driver_err = $drive.err.Trim() }
}

Invoke-Journey 'SBX-00' {
    $zip = @(Get-ChildItem -LiteralPath 'C:\In\dist' -Filter '*.zip')
    if ($zip.Count -ne 1) { throw "expected one zip under C:\In\dist, found $($zip.Count)" }
    $script:zipPath = $zip[0].FullName
    $script:zipSha = (Get-FileHash -LiteralPath $script:zipPath -Algorithm SHA256).Hash.ToLower()
    $os = Get-CimInstance Win32_OperatingSystem
    try {
        $mp = Get-MpComputerStatus
        $defender = [ordered]@{ antivirus = $mp.AntivirusEnabled; realtime = $mp.RealTimeProtectionEnabled;
                                signatures = "$($mp.AntivirusSignatureVersion)" }
    } catch {
        $defender = @{ error = "$($_.Exception.Message)" }
    }
    # A \WindowsApps\ App Execution Alias stub is not an install, so it does not count.
    $python = [bool](Get-Command python -All -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*\WindowsApps\*' })
    $git = [bool](Get-Command git -All -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*\WindowsApps\*' })
    @{ ok = (-not $python -and -not $git); detail = [ordered]@{
        zip = $zip[0].Name; zip_sha256 = $script:zipSha; os_build = "$($os.Version)";
        ram_mb = [int]($os.TotalVisibleMemorySize / 1024); user = $env:USERNAME;
        python_on_path = $python; git_on_path = $git; defender = $defender } }
}

Invoke-Journey 'SBX-01' {
    $sidecar = ((Get-Content -LiteralPath "$script:zipPath.sha256" -Raw).Trim() -split '\s+')[0].ToLower()
    if ($sidecar -ne $script:zipSha) {
        return @{ ok = $false; detail = [ordered]@{ sidecar = $sidecar; computed = $script:zipSha } }
    }
    Expand-Archive -LiteralPath $script:zipPath -DestinationPath (Join-Path $env:LOCALAPPDATA 'Programs') -Force
    @{ ok = (Test-Path -LiteralPath $exe); detail = [ordered]@{
        exe = $exe; installed = (Test-Path -LiteralPath $exe); defender_events = @(Get-DefenderEvents) } }
}

Invoke-Journey 'SBX-02' {
    $version = Invoke-Friday @('--version') 120
    $paths = (Invoke-Friday @('--paths') 120).out | ConvertFrom-Json
    $script:state = "$($paths.state_root)"
    $under = $script:state.StartsWith($env:LOCALAPPDATA + '\', [StringComparison]::OrdinalIgnoreCase)
    @{ ok = ($version.code -eq 0 -and $version.out.StartsWith('FRIDAY') -and $under); detail = [ordered]@{
        version = $version.out.Trim(); state_root = $script:state; local_app_data = $env:LOCALAPPDATA } }
}

Invoke-Journey 'SBX-03' {
    $doctor = Invoke-Friday @('--doctor') 300
    $stdout = $doctor.out
    # shortcut: keeps the doctor's last 200k chars under the host's 1 MB cap; a longer report fails closed on the host.
    if ($stdout.Length -gt 200000) { $stdout = $stdout.Substring($stdout.Length - 200000) }
    # ok only says the doctor answered; the host decides with validate_exe_clean.doctor_verdict.
    @{ ok = ($doctor.code -ne 124); detail = [ordered]@{ returncode = $doctor.code; stdout = $stdout } }
}

Invoke-Journey 'SBX-04' {
    $up = Start-Stack 'first'
    @{ ok = ($up.mcp_listening -and $up.ui_health_200 -and $up.launcher_alive); detail = $up }
}

Invoke-Journey 'SBX-05' {
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $set = Invoke-Api 'POST' '/api/auth/pin/set' @{ pin = $script:pin } $session
    $verify = Invoke-Api 'POST' '/api/auth/pin/verify' @{ pin = $script:pin } $session
    $task = Invoke-Task $session 'remember the sandbox canary' 'memory_remember' @{ subject = $script:canary; value = 'installed exe' }
    $script:runId = "$($task.run_id)"
    $task['pin_set'] = $set.ok
    $task['pin_verified'] = $verify.ok
    @{ ok = ($set.ok -and $verify.ok -and $task.ok); detail = $task }
}

Invoke-Journey 'SBX-06' {
    $down = Stop-Stack
    $status = Invoke-Friday @('--status') 60
    $down['status'] = $status.out.Trim()
    @{ ok = ($down.ports_free -and $status.out.Contains('no Friday processes')); detail = $down }
}

Invoke-Journey 'SBX-07' {
    $up = Start-Stack 'restart'
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    # Sessions live in memory, so a fresh verify proves the PIN itself persisted.
    $verify = Invoke-Api 'POST' '/api/auth/pin/verify' @{ pin = $script:pin } $session
    $status = Get-RunStatus $session $script:runId
    $same = [bool]$script:runId -and ("$($status.output.run_id)" -eq $script:runId) -and "$($status.output.status)" -eq 'COMPLETED'
    # memory_recall fails on "nothing recorded", so a SUCCEEDED recall proves the remembered fact persisted.
    $recall = Invoke-Task $session 'recall the sandbox canary' 'memory_recall' @{ subject = $script:canary }
    $up['pin_verified'] = $verify.ok
    $up['run_id'] = "$($status.output.run_id)"
    $up['run_status'] = "$($status.output.status)"
    $up['recall'] = $recall
    @{ ok = ($up.mcp_listening -and $up.ui_health_200 -and $up.launcher_alive -and $verify.ok -and $same -and $recall.ok)
       detail = $up }
}

Invoke-Journey 'SBX-08' {
    $down = Stop-Stack
    if (-not $script:state -or -not $script:state.StartsWith($env:LOCALAPPDATA + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "no usable state_root from SBX-02 ($script:state); nothing deleted"
    }
    $logs = Join-Path $script:state 'logs'
    if (Test-Path -LiteralPath $logs) { Copy-Item -LiteralPath $logs -Destination $logStash -Recurse -Force }
    Remove-Item -LiteralPath $app -Recurse -Force
    $database = Join-Path $script:state 'data\ada.sqlite3'
    $kept = (Test-Path -LiteralPath $script:state) -and (Test-Path -LiteralPath $database)
    Remove-Item -LiteralPath $script:state -Recurse -Force
    $leftover = @(Get-Process -Name 'FRIDAY' -ErrorAction SilentlyContinue).Count
    $portsFree = -not (Test-Port 8000) -and -not (Test-Port 8770)
    $down['app_removed'] = -not (Test-Path -LiteralPath $app)
    $down['state_and_db_kept_after_app_removal'] = $kept
    $down['state_removed'] = -not (Test-Path -LiteralPath $script:state)
    $down['friday_processes_left'] = $leftover
    $down['ports_free_at_end'] = $portsFree
    @{ ok = ($down.ports_free -and $down.app_removed -and $kept -and $down.state_removed -and $leftover -eq 0 -and $portsFree)
       detail = $down }
}

Save-Partial
Move-Item -LiteralPath $partial -Destination (Join-Path $out 'result.json')

# Evidence beside the verdict: launcher logs, the stack's own output, Defender events.
if (Test-Path -LiteralPath $logStash) { Copy-Item -LiteralPath $logStash -Destination (Join-Path $out 'logs') -Recurse -Force }
Get-ChildItem -Path $env:TEMP -Filter 'friday-stack-*' | Copy-Item -Destination $out -Force
@(Get-DefenderEvents) | Set-Content -LiteralPath (Join-Path $out 'defender_events.txt') -Encoding UTF8
shutdown /s /t 5
