[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = 'C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat'
$python = 'C:\Users\xiayu\AppData\Local\Programs\Python\Python312\python.exe'
$helper = Join-Path $root 'outputs\qq-vm\install\temporary_wake_hold.py'
$sentinel = Join-Path $root 'outputs\qq-vm\install\diagnostics\pmai-temporary-wake-hold-stop.json'
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw 'wake hold helper missing' }
if (Test-Path -LiteralPath $sentinel) { Remove-Item -LiteralPath $sentinel -Force }
$process = Start-Process -FilePath $python -ArgumentList @($helper) -WindowStyle Hidden -PassThru
[ordered]@{started=$true;pid=$process.Id;ttl_seconds=1800;mode='system_required_only'} | ConvertTo-Json -Compress
