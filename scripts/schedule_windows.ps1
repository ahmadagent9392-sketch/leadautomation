<#
Opportunity Desk - Windows Task Scheduler job for the morning run (Stage 8).

Usage (PowerShell, in the project folder):
    powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -On       # turn on (daily 07:30 Karachi time)
    powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Off      # turn off (job stays, disabled)
    powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Status   # is it on? last / next run
    powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -RunNow   # start it now (test)
    powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Remove   # delete the job

What the job does: `python scripts\daily.py run` in this folder (output added to logs\scheduler.log).
daily.py checks data\STOP, the lock and "already ran today" first, then runs claude -p "/daily-run".
- 07:30 Asia/Karachi is changed to this PC's time zone.
- "Run as soon as possible after a missed start": PC off at 07:30 -> it runs when the PC is on again.
- Runs only when you are logged in (Claude Code uses your login). No admin rights needed.
- Quick pause without this script: create the file data\STOP. Delete it to start again.
#>
param(
    [switch]$On,
    [switch]$Off,
    [switch]$Status,
    [switch]$RunNow,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "OpportunityDesk-DailyRun"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Get-KarachiTimeHere {
    # 07:30 in Asia/Karachi, shown in this PC's time zone
    $pk = [TimeZoneInfo]::FindSystemTimeZoneById("Pakistan Standard Time")
    $at = [DateTime]::SpecifyKind((Get-Date).Date.AddHours(7.5), [DateTimeKind]::Unspecified)
    return [TimeZoneInfo]::ConvertTime($at, $pk, [TimeZoneInfo]::Local)
}

function Get-DeskTask {
    return Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

if (-not ($On -or $Off -or $Status -or $RunNow -or $Remove)) {
    Write-Host "Use one of: -On  -Off  -Status  -RunNow  -Remove"
    exit 1
}

if ($On) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $python) { Write-Host "ERROR: python was not found on the PATH."; exit 1 }
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: claude was not found on the PATH. Install Claude Code first."; exit 1
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null
    $start = Get-KarachiTimeHere
    $cmdLine = "/c `"`"$python`" scripts\daily.py run >> logs\scheduler.log 2>&1`""
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmdLine -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -Daily -At $start
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3)
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
        -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description "Opportunity Desk morning run (never sends anything)" -Force | Out-Null
    Enable-ScheduledTask -TaskName $TaskName | Out-Null
    Write-Host ("ON: '{0}' runs every day at {1:HH:mm} on this PC (= 07:30 Asia/Karachi)." -f $TaskName, $start)
    Write-Host "Missed start (PC off) -> it runs when the PC is on again. Log: logs\scheduler.log"
}

if ($Off) {
    if (-not (Get-DeskTask)) { Write-Host "No job yet. Nothing to turn off."; exit 0 }
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
    Write-Host "OFF: the morning run will not start. Turn on again with -On."
}

if ($Remove) {
    if (-not (Get-DeskTask)) { Write-Host "No job. Nothing to remove."; exit 0 }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed the job '$TaskName'."
}

if ($RunNow) {
    if (-not (Get-DeskTask)) { Write-Host "No job yet. Run with -On first."; exit 1 }
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started. Watch logs\scheduler.log and logs\runs.jsonl (or: python scripts\daily.py runs)."
}

if ($Status) {
    $task = Get-DeskTask
    if (-not $task) { Write-Host "No job. Turn on with -On."; exit 0 }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host ("Job      : {0}" -f $TaskName)
    Write-Host ("State    : {0}" -f $task.State)
    Write-Host ("Last run : {0}  (result {1}; 0 = OK, 2 = not started: STOP / already ran)" -f $info.LastRunTime, $info.LastTaskResult)
    Write-Host ("Next run : {0}" -f $info.NextRunTime)
    if (Test-Path (Join-Path $Root "data\STOP")) { Write-Host "STOP     : data\STOP exists - runs do nothing." }
}
