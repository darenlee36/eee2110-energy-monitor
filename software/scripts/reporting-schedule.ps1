param(
    [ValidateSet('Plan', 'Install', 'Enable', 'Disable', 'Status', 'Run')]
    [string]$Action = 'Status',
    [ValidatePattern('^[a-zA-Z0-9_-]{1,64}$')]
    [string]$ProfileId = 'tefal-kettle',
    [ValidatePattern('^[a-zA-Z0-9_-]{1,64}$')]
    [string]$DeviceId = 'sim-esp32-03b248',
    [string]$CodexExecutable
)
$ErrorActionPreference = 'Stop'
$softwareRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$taskName = 'EnergyMonitor-CycleReporting'
$description = "Energy Monitor guarded laptop reporting | $softwareRoot"
$python = Join-Path $softwareRoot '.venv\Scripts\pythonw.exe'
$database = Join-Path $softwareRoot 'data\reporting-worker-cache.db'
$state = Join-Path $softwareRoot 'data\reporting-worker'
$arguments = '-m energy_monitor.report_worker --source supabase --database "{0}" --state-dir "{1}" --profile-id {2} --device-id {3} --allow-one-codex-call' -f $database, $state, $ProfileId, $DeviceId
if (-not $CodexExecutable) {
    $CodexExecutable = (Get-Command codex.exe -ErrorAction SilentlyContinue).Source
    if (-not $CodexExecutable -and $Action -ne 'Plan') {
        $installed = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
        if ($installed -and $installed.Actions[0].Arguments -match ' --codex-executable "([^"]+)"$') {
            $CodexExecutable = $Matches[1]
        }
    }
}
if (-not $CodexExecutable -or -not (Test-Path -LiteralPath $CodexExecutable -PathType Leaf)) {
    throw 'Codex executable missing. Pass -CodexExecutable with its existing absolute path.'
}
$arguments += ' --codex-executable "{0}"' -f $CodexExecutable
if (-not (Test-Path -LiteralPath $python)) { throw 'Project Python environment missing.' }

function Assert-ReportingTaskSafety($Definition) {
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $taskUser = $Definition.Principal.UserId
        $taskSid = if ($taskUser -match '^S-1-') {
            [System.Security.Principal.SecurityIdentifier]::new($taskUser).Value
        } else {
            [System.Security.Principal.NTAccount]::new($taskUser).Translate([System.Security.Principal.SecurityIdentifier]).Value
        }
    } catch { throw 'Reporting task user cannot be verified. No change made.' }
    if ($taskSid -ne $currentUser.User.Value -or
        [string]$Definition.Principal.LogonType -ne 'Interactive' -or
        [string]$Definition.Principal.RunLevel -ne 'Limited' -or
        $Definition.Settings.WakeToRun -or -not $Definition.Settings.StartWhenAvailable -or
        [string]$Definition.Settings.MultipleInstances -ne 'IgnoreNew' -or
        $Definition.Settings.ExecutionTimeLimit -ne 'PT10M' -or
        $Definition.Triggers.Count -ne 1 -or
        $Definition.Triggers[0].Repetition.Interval -ne 'PT30M' -or
        $Definition.Triggers[0].EndBoundary -ne '2026-12-07T00:00:00+08:00') {
        throw 'Reporting task safety settings differ. No change made.'
    }
}

if ($Action -in 'Plan', 'Install') {
    if ([datetime]::Now -ge [datetime]'2026-12-07T00:00:00') { throw 'Reporting period expired.' }
    $taskAction = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $softwareRoot
    $trigger = New-ScheduledTaskTrigger -Once -At ([datetime]::Now.AddMinutes(2)) -RepetitionInterval (New-TimeSpan -Minutes 30)
    $trigger.EndBoundary = '2026-12-07T00:00:00+08:00'
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $settings.Enabled = $false
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    if ($Action -eq 'Plan') {
        [pscustomobject]@{
            TaskName = $taskName; Executable = $taskAction.Execute
            Arguments = $taskAction.Arguments; WorkingDirectory = $taskAction.WorkingDirectory
            Interval = $trigger.Repetition.Interval; EndBoundary = $trigger.EndBoundary
            StartWhenAvailable = $settings.StartWhenAvailable; WakeToRun = $settings.WakeToRun
            MultipleInstances = [string]$settings.MultipleInstances; Enabled = $settings.Enabled
            LogonType = [string]$principal.LogonType; RunLevel = [string]$principal.RunLevel
        }
        return
    }
    $existing = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
    if ($existing) { throw 'Task already exists. Inspect it before replacing; installation will not overwrite.' }
    Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $taskAction -Trigger $trigger -Settings $settings -Principal $principal -Description $description | Out-Null
}

$task = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
if (-not $task) {
    if ($Action -eq 'Status') { Write-Output 'Reporting task not installed.'; return }
    throw 'Reporting task not installed.'
}
if ($task.Description -ne $description -or $task.Actions.Count -ne 1 -or $task.Actions[0].Execute -ne $python -or $task.Actions[0].WorkingDirectory -ne $softwareRoot -or $task.Actions[0].Arguments -ne $arguments) {
    throw 'Task ownership/configuration differs. No change made.'
}
switch ($Action) {
    'Enable' {
        Assert-ReportingTaskSafety $task
        Enable-ScheduledTask -InputObject $task | Out-Null
    }
    'Disable' { Disable-ScheduledTask -InputObject $task | Out-Null }
    'Run' {
        Assert-ReportingTaskSafety $task
        Start-ScheduledTask -InputObject $task
    }
}
Get-ScheduledTask -TaskName $taskName -TaskPath '\' | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $taskName -TaskPath '\' | Select-Object LastRunTime, LastTaskResult, NextRunTime
