param(
    [ValidateSet('status','plan','refresh','process','query','trace')][string]$Action = 'status',
    [string]$Target = '',
    [string]$Config = 'configs/live.toml',
    [string]$Out = ''
)
$ErrorActionPreference = 'Stop'
$Action = $Action.ToLowerInvariant()
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$arguments = @('-X','utf8','-m','equity_data.competition','--config',$Config)
if ($Out) { $arguments += @('--out',$Out) }
$arguments += $Action
if ($Action -eq 'refresh' -and $Target) { $arguments += @('--targets',$Target) }
elseif ($Action -in @('query','trace')) {
    if (-not $Target) { throw 'query/trace require -Target (entity ID or record ID).' }
    $arguments += $Target
}
Push-Location -LiteralPath $projectRoot
try { & $pythonPath @arguments; if ($LASTEXITCODE -ne 0) { throw "Competition command failed: $LASTEXITCODE" } }
finally { Pop-Location }
