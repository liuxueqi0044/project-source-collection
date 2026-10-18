param([string]$Config = "configs/machine.toml")
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runner = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runner)) { throw 'Create .venv and install requirements.lock first; see README.' }
& $runner -m equity_data --config $Config update
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $runner -m equity_data --config $Config report
exit $LASTEXITCODE
