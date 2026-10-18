$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
Push-Location -LiteralPath $projectRoot
try {
    $configPath = Join-Path $projectRoot 'configs\v4_calendar_trigger.toml'
    if (Test-Path -LiteralPath $configPath) { throw 'Preserved experiment already exists; do not overwrite.' }
    $clockText = & $pythonPath -c 'from datetime import datetime,timedelta; from zoneinfo import ZoneInfo; import json; t=(datetime.now(ZoneInfo("Asia/Shanghai"))+timedelta(minutes=2)).replace(second=0,microsecond=0); print(json.dumps({"time":t.strftime("%H:%M"),"expected":t.isoformat()}))'
    if ($LASTEXITCODE -ne 0) { throw 'Clock calculation failed.' }
    $clock = $clockText | ConvertFrom-Json
    $text = Get-Content configs/v4_local_recovery_final.toml -Raw
    $text = $text.Replace('data/v4_local_recovery_final','data/v4_calendar_trigger').Replace('times = ["09:00", "20:30", "23:30"]',('times = ["' + $clock.time + '"]'))
    $text | Set-Content -LiteralPath $configPath -Encoding UTF8
    $specText = & $pythonPath -m equity_data.local_schedule --config $configPath --user-id ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
    if ($LASTEXITCODE -ne 0) { throw 'XML generation failed.' }
    $spec = $specText | ConvertFrom-Json
    [xml]$xml = Get-Content -LiteralPath $spec.xml_path -Raw
    $ns = New-Object Xml.XmlNamespaceManager($xml.NameTable)
    $ns.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task')
    $pulse = $xml.SelectSingleNode('/t:Task/t:Triggers/t:TimeTrigger',$ns)
    if ($pulse) { $null = $pulse.ParentNode.RemoveChild($pulse) }
    # Isolate a real CalendarTrigger; production task and its pulse are unchanged.
    & $pythonPath -c 'import sys; from equity_data.cli import load_config; from equity_data.local_control import Control,stamp; c=Control(load_config(sys.argv[1])["storage"]); c.set_meta("planning_cursor",stamp()); c.close()' $configPath
    if ($LASTEXITCODE -ne 0) { throw 'Experiment cursor setup failed.' }
    Register-ScheduledTask -TaskName $spec.task_name -TaskPath '\' -Xml $xml.OuterXml | Out-Null
    $out = Join-Path $projectRoot 'reports\v4\calendar-trigger'
    New-Item -Path $out -ItemType Directory -Force | Out-Null
    Export-ScheduledTask -TaskName $spec.task_name | Set-Content -LiteralPath (Join-Path $out 'registered-calendar-only.xml') -Encoding UTF8
    @{registered_at=(Get-Date).ToUniversalTime().ToString('o'); expected_trigger=$clock.expected; task_name=$spec.task_name; config=$configPath; calendar_only=$true; production_task_changed=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $out 'expected.json') -Encoding UTF8
    Get-Content -LiteralPath (Join-Path $out 'expected.json')
}
finally { Pop-Location }
