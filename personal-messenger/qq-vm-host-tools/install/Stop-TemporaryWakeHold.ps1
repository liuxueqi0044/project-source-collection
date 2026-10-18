[CmdletBinding()]
param([ValidateRange(1,30)][int]$TimeoutSeconds = 10)

$ErrorActionPreference = 'Stop'
$root = 'C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat'
$diag = Join-Path $root 'outputs\qq-vm\install\diagnostics'
$sentinel = Join-Path $diag 'pmai-temporary-wake-hold-stop.json'
$state = Join-Path $diag 'pmai-temporary-wake-hold-state.json'
[IO.File]::WriteAllText($sentinel, '{"schema":"pmai-temporary-wake-hold-stop-v1","action":"stop"}', [Text.UTF8Encoding]::new($false))
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
do {
    if (Test-Path -LiteralPath $state) {
        $current = Get-Content -LiteralPath $state -Raw | ConvertFrom-Json
        if ($current.active -eq $false) { $current | ConvertTo-Json -Compress; exit 0 }
    }
    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $deadline)
throw 'wake hold stop acknowledgement timed out; no termination was attempted'
