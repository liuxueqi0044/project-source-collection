[CmdletBinding()]
param(
    [ValidateSet('Install','Status','Run','Pause','Resume','Uninstall')][string]$Action = 'Status',
    [string]$Config = 'configs/live.toml'
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$configPath = if ([IO.Path]::IsPathRooted($Config)) { [IO.Path]::GetFullPath($Config) } else { [IO.Path]::GetFullPath((Join-Path $projectRoot $Config)) }
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$identityName = [Security.Principal.WindowsIdentity]::GetCurrent().Name
function Invoke-LocalControl([string]$Command) {
    & $pythonPath -m equity_data.local --config $configPath $Command | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Local control command failed: $Command (exit $LASTEXITCODE)" }
}
Push-Location -LiteralPath $projectRoot
try {
    $specText = & $pythonPath -m equity_data.local_schedule --config $configPath --user-id $identityName
    if ($LASTEXITCODE -ne 0) { throw 'Unable to validate and generate local schedule configuration.' }
    $taskSpec = $specText | ConvertFrom-Json
    $existing = Get-ScheduledTask -TaskName $taskSpec.task_name -TaskPath $taskSpec.task_path -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Description -ne $taskSpec.ownership_marker)) {
        throw 'Task name exists but ownership marker does not match; refusing to modify it.'
    }
    switch ($Action) {
        'Install' {
            $xmlText = Get-Content -LiteralPath $taskSpec.xml_path -Raw -Encoding UTF8
            Register-ScheduledTask -TaskName $taskSpec.task_name -TaskPath $taskSpec.task_path -Xml $xmlText -Force | Out-Null
            Invoke-LocalControl 'resume'
        }
        'Run' {
            if (-not $existing) { throw 'Project task is not installed.' }
            Start-ScheduledTask -TaskName $taskSpec.task_name -TaskPath $taskSpec.task_path
        }
        'Pause' {
            Invoke-LocalControl 'pause'
            if ($existing) { Disable-ScheduledTask -TaskName $taskSpec.task_name -TaskPath $taskSpec.task_path | Out-Null }
        }
        'Resume' {
            if (-not $existing) { throw 'Project task is not installed.' }
            Invoke-LocalControl 'resume'
            Enable-ScheduledTask -TaskName $taskSpec.task_name -TaskPath $taskSpec.task_path | Out-Null
            Start-ScheduledTask -TaskName $taskSpec.task_name -TaskPath $taskSpec.task_path
        }
        'Uninstall' {
            Invoke-LocalControl 'pause'
            if ($existing) {
                if ($existing.Actions.Execute -ne $taskSpec.python -or $existing.Actions.Arguments -ne $taskSpec.arguments) {
                    throw 'Task action has changed; refusing to uninstall a task with a mismatched action.'
                }
                Unregister-ScheduledTask -TaskName $taskSpec.task_name -TaskPath $taskSpec.task_path -Confirm:$false
            }
        }
    }
    $current = Get-ScheduledTask -TaskName $taskSpec.task_name -TaskPath $taskSpec.task_path -ErrorAction SilentlyContinue
    $info = if ($current) { Get-ScheduledTaskInfo -TaskName $taskSpec.task_name -TaskPath $taskSpec.task_path } else { $null }
    $registeredBoundaries = @()
    $registeredAction = $null
    if ($current) {
        [xml]$registeredXml = Export-ScheduledTask -TaskName $taskSpec.task_name -TaskPath $taskSpec.task_path
        $registeredBoundaries = @($registeredXml.Task.Triggers.CalendarTrigger | ForEach-Object { [string]$_.StartBoundary })
        $registeredAction = @{Command=[string]$registeredXml.Task.Actions.Exec.Command; Arguments=[string]$registeredXml.Task.Actions.Exec.Arguments; WorkingDirectory=[string]$registeredXml.Task.Actions.Exec.WorkingDirectory}
    }
    $registeredClocks = @($registeredBoundaries | ForEach-Object { ([DateTimeOffset]::Parse($_)).ToString('HH:mm') })
    $scheduleDrift = [bool]$current -and (($registeredClocks -join ',') -ne ($taskSpec.times -join ','))
    $actionDrift = [bool]$current -and (($registeredAction.Command -ne $taskSpec.python) -or ($registeredAction.Arguments -ne $taskSpec.arguments) -or ($registeredAction.WorkingDirectory -ne $taskSpec.working_directory))
    $answer = [ordered]@{
        ObservedAt = (Get-Date).ToUniversalTime().ToString('o'); Action=$Action; Installed=[bool]$current;
        Name=$taskSpec.task_name; ScheduleTimezone=$taskSpec.timezone; Times=$taskSpec.times;
        WindowsTimezone=(Get-TimeZone).Id; Identity=$identityName; State=if ($current) { [string]$current.State } else { 'Absent' };
        RegisteredDailyStartBoundaries=$registeredBoundaries; RegisteredAction=$registeredAction;
        ScheduleOrActionDrift=($scheduleDrift -or $actionDrift);
        LastRunTime=if ($info) { $info.LastRunTime.ToString('o') } else { $null };
        LastTaskResult=if ($info) { $info.LastTaskResult } else { $null };
        NextRunTime=if ($info) { $info.NextRunTime.ToString('o') } else { $null };
        MissedRuns=if ($info) { $info.NumberOfMissedRuns } else { $null };
        Runtime=$taskSpec.python; WorkingDirectory=$taskSpec.working_directory; Config=$configPath;
        LogonType='InteractiveToken'; RequiresLoggedOnUser=$true; WakeToRun=$false;
        Notice='LastTaskResult=0 means dispatcher ran. Inspect latest-decision-report.json for acquisition/parse/source-check completion.'
    }
    $json = $answer | ConvertTo-Json -Depth 8
    $json | Set-Content -LiteralPath (Join-Path $taskSpec.storage 'deployment\last-management-status.json') -Encoding UTF8
    if ($current) { Export-ScheduledTask -TaskName $taskSpec.task_name -TaskPath $taskSpec.task_path | Set-Content -LiteralPath (Join-Path $taskSpec.storage 'deployment\registered-task.xml') -Encoding UTF8 }
    $json
}
finally { Pop-Location }
