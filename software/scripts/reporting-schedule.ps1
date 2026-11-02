param(
    [ValidateSet('Plan', 'Install', 'Enable', 'Disable', 'Status', 'Run', 'Worker')]
    [string]$Action = 'Status',
    [ValidatePattern('^[a-zA-Z0-9_-]{1,64}$')]
    [string]$ProfileId = 'tefal-kettle',
    [ValidatePattern('^[a-zA-Z0-9_-]{1,64}$')]
    [string]$DeviceId = 'sim-esp32-03b248',
    [string]$CodexExecutable,
    [string]$RuntimeRoot,
    [ValidateSet('local', 'supabase')]
    [string]$Source = 'supabase',
    [string]$Database,
    [string]$StateDirectory,
    [switch]$NoAI
)
$ErrorActionPreference = 'Stop'
$softwareRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
if (-not $RuntimeRoot) { $RuntimeRoot = $softwareRoot }
$RuntimeRoot = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$taskName = 'EnergyMonitor-CycleReporting'
$description = "Energy Monitor guarded laptop reporting | $RuntimeRoot"
$python = Join-Path $RuntimeRoot '.venv\Scripts\python.exe'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not $Database) { $Database = Join-Path $RuntimeRoot 'data\reporting-worker-cache.db' }
if (-not $StateDirectory) { $StateDirectory = Join-Path $RuntimeRoot 'data\reporting-worker' }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Project Python environment missing.' }

function Resolve-ReportingCodex([string]$Explicit, [string]$BinRoot) {
    if ($Explicit) {
        if (-not [IO.Path]::IsPathRooted($Explicit) -or
            -not (Test-Path -LiteralPath $Explicit -PathType Leaf) -or
            [IO.Path]::GetExtension($Explicit) -ne '.exe') {
            throw 'Existing absolute Codex executable required.'
        }
        return (Resolve-Path -LiteralPath $Explicit).Path
    }
    $command = Get-Command codex.exe -ErrorAction SilentlyContinue
    if ($command -and (Test-Path -LiteralPath $command.Source -PathType Leaf)) {
        return $command.Source
    }
    if (-not $BinRoot) { $BinRoot = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin' }
    if (-not (Test-Path -LiteralPath $BinRoot -PathType Container)) { return $null }
    # App updates replace version directories. Never persist one in the task action.
    $candidates = Get-ChildItem -LiteralPath $BinRoot -Directory | ForEach-Object {
        $candidate = Join-Path $_.FullName 'codex.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { Get-Item -LiteralPath $candidate }
    }
    $latest = $candidates | Sort-Object LastWriteTimeUtc, FullName -Descending | Select-Object -First 1
    if ($latest) { return $latest.FullName }
    return $null
}

$resolvedCodex = Resolve-ReportingCodex -Explicit $CodexExecutable
$workerArguments = @('-m', 'energy_monitor.report_worker', '--source', $Source,
    '--database', $Database, '--state-dir', $StateDirectory,
    '--profile-id', $ProfileId, '--device-id', $DeviceId)
if ($NoAI) { $workerArguments += '--no-ai' } else { $workerArguments += '--allow-one-codex-call' }
if ($resolvedCodex) { $workerArguments += @('--codex-executable', $resolvedCodex) }
$envFile = Join-Path $RuntimeRoot '.env'
if (Test-Path -LiteralPath $envFile -PathType Leaf) { $workerArguments += @('--env-file', $envFile) }

if ($Action -eq 'Worker') {
    # Scope imports to this checkout without repointing the shared editable venv.
    # Preserve the installed database/credential/attempt-journal root separately.
    $previousPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = Join-Path $softwareRoot 'src'
        if ($previousPythonPath) { $env:PYTHONPATH += [IO.Path]::PathSeparator + $previousPythonPath }
        & $python @workerArguments
        $workerExit = $LASTEXITCODE
    } finally { $env:PYTHONPATH = $previousPythonPath }
    exit $workerExit
}

$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Action Worker -RuntimeRoot "{1}" -ProfileId {2} -DeviceId {3} -Source {4} -Database "{5}" -StateDirectory "{6}"' -f $PSCommandPath, $RuntimeRoot, $ProfileId, $DeviceId, $Source, $Database, $StateDirectory
if ($NoAI) { $arguments += ' -NoAI' }

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
    $taskAction = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $RuntimeRoot
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
            WorkerArguments = ($workerArguments -join ' '); CodexExecutable = $resolvedCodex
            ExecutionMode = $(if ($NoAI) { 'no_ai' } else { 'guarded_ai' })
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
if ($task.Description -ne $description -or $task.Actions.Count -ne 1 -or $task.Actions[0].Execute -ne $powershell -or $task.Actions[0].WorkingDirectory -ne $RuntimeRoot -or $task.Actions[0].Arguments -ne $arguments) {
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
