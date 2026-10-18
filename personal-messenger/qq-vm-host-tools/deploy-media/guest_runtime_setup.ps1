[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$computer = Get-CimInstance Win32_ComputerSystem
if ($env:COMPUTERNAME -ne 'PMAI-QQVM' -or
    $computer.Model -notlike '*VirtualBox*' -or
    $computer.Manufacturer -notmatch '(?i)(Oracle|innotek)') {
    throw 'This script is restricted to the PMAI-QQVM VirtualBox guest.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($identity.Name -notlike '*\qqbot') { throw 'Run this script while signed in as qqbot.' }
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'qqbot must remain a standard user.'
}

$project = 'C:\PMAI\app'
$dataRoot = 'C:\PMAI\data'
$pythonSystem = 'C:\PMAI\tools\Python313\python.exe'
$bootstrap = Join-Path $project 'scripts\deployment\guest_bootstrap.ps1'
$venvPython = Join-Path $project '.venv\Scripts\python.exe'
$statusPath = Join-Path $dataRoot 'runtime-setup-status.json'
$prereqPath = 'C:\ProgramData\PMAI\prerequisites-status.json'
$results = [System.Collections.Generic.List[object]]::new()

function Add-Result([string]$Stage, [bool]$Ok, [object]$Details) {
    $results.Add([ordered]@{ stage = $Stage; ok = $Ok; details = $Details })
}
function Write-Status([string]$State) {
    New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
    [ordered]@{
        schema = 1
        state = $State
        observed_at_utc = [DateTime]::UtcNow.ToString('o')
        computer = $env:COMPUTERNAME
        user = $identity.Name
        runtime_started = $false
        qq_started = $false
        model_api_called = $false
        send_attempted = $false
        rulepack_activated = $false
        results = $results.ToArray()
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $statusPath -Encoding UTF8
}
function Invoke-Stage([string]$Name, [scriptblock]$Action) {
    Write-Host "== $Name =="
    try {
        $details = & $Action
        Add-Result $Name $true $details
        Write-Status 'running'
    } catch {
        Add-Result $Name $false ([ordered]@{ error = $_.Exception.Message })
        Write-Status 'failed'
        throw
    }
}

Write-Status 'running'
try {
    Invoke-Stage 'prerequisites-summary' {
        if (-not (Test-Path -LiteralPath $prereqPath -PathType Leaf)) {
            throw 'Prerequisites status is missing.'
        }
        $p = Get-Content -LiteralPath $prereqPath -Raw | ConvertFrom-Json
        if ($p.overall -ne 'succeeded') { throw "Prerequisites are not complete: $($p.overall)" }
        $observationsPath = 'C:\ProgramData\PMAI\windows-observations.json'
        $observations = $null
        if (Test-Path -LiteralPath $observationsPath -PathType Leaf) {
            $o = Get-Content -LiteralPath $observationsPath -Raw | ConvertFrom-Json
            $observations = [ordered]@{
                winre_info_captured = $o.winre_info_captured
                panther_unattend_present_after = $o.panther_unattend_present_after
                panther_sensitive_fields_nonempty = $o.panther_sensitive_fields_nonempty
            }
        }
        return [ordered]@{
            present = $true
            overall = $p.overall
            updated_utc = $p.updated_utc
            stages = @($p.stages | ForEach-Object {
                [ordered]@{ name = $_.name; succeeded = $_.succeeded; blocking = $_.blocking }
            })
            windows_observations = $observations
        }
    }

    Invoke-Stage 'bootstrap-venv' {
        foreach ($required in @($project, $bootstrap, $pythonSystem)) {
            if (-not (Test-Path -LiteralPath $required)) { throw "Required deployment path is missing: $required" }
        }
        $bootstrapBlock = [scriptblock]::Create([IO.File]::ReadAllText($bootstrap))
        & $bootstrapBlock -ProjectPath $project -PythonExecutable $pythonSystem | Out-Host
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) { throw 'Bootstrap did not create .venv Python.' }
        return [ordered]@{ venv = 'C:\PMAI\app\.venv'; extras = @('web','llm','qq-vm') }
    }

    Invoke-Stage 'guest-marker' {
        [Environment]::SetEnvironmentVariable('PERSONAL_MESSENGER_VM_GUEST', '1', 'User')
        $env:PERSONAL_MESSENGER_VM_GUEST = '1'
        if ([Environment]::GetEnvironmentVariable('PERSONAL_MESSENGER_VM_GUEST', 'User') -ne '1') {
            throw 'Guest marker did not persist for qqbot.'
        }
        return [ordered]@{ scope = 'User'; name = 'PERSONAL_MESSENGER_VM_GUEST'; value = '1' }
    }

    Invoke-Stage 'python-smoke' {
        $selfTest = Join-Path $dataRoot ('.runtime-selftest-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $selfTest | Out-Null
        $probePath = Join-Path $selfTest 'probe.py'
        $probe = @'
import json, secrets, sys
from pathlib import Path
import openai, pydantic, starlette, uiautomation, uvicorn, win32api, win32crypt, yaml
from messenger_ai.observability.secrets import WindowsDPAPISecretStore

root = Path(sys.argv[1])
vault = root / "vault"
store = WindowsDPAPISecretStore(vault)
sample = secrets.token_bytes(32)
store.set_secret("runtime.setup.selftest", sample)
roundtrip = store.get_secret("runtime.setup.selftest") == sample
deleted = store.delete_secret("runtime.setup.selftest")
if not (roundtrip and deleted):
    raise RuntimeError("DPAPI self-test failed")
print(json.dumps({
    "python": sys.version.split()[0],
    "imports": ["openai", "pydantic", "starlette", "uiautomation", "uvicorn", "win32api", "win32crypt", "yaml", "messenger_ai"],
    "dpapi_roundtrip": True,
}, sort_keys=True))
vault.rmdir()
'@
        try {
            [IO.File]::WriteAllText($probePath, $probe, [Text.UTF8Encoding]::new($false))
            $json = (& $venvPython $probePath $selfTest 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) { throw "Python smoke failed: $json" }
            return ($json | ConvertFrom-Json)
        } finally {
            if (Test-Path -LiteralPath $selfTest) {
                $resolvedSelfTest = (Resolve-Path -LiteralPath $selfTest -ErrorAction Stop).Path.TrimEnd('\')
                $resolvedDataRoot = (Resolve-Path -LiteralPath $dataRoot -ErrorAction Stop).Path.TrimEnd('\') + '\'
                if (-not $resolvedSelfTest.StartsWith($resolvedDataRoot, [StringComparison]::OrdinalIgnoreCase)) {
                    throw 'Refusing to clean a self-test path outside C:\PMAI\data.'
                }
                Remove-Item -LiteralPath $resolvedSelfTest -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }

    Invoke-Stage 'cli-help' {
        $commands = @(
            @('scripts\run_vm_runtime.py', '--help'),
            @('scripts\inspect_qq_guest_bindings.py', '--help')
        )
        $checked = @()
        foreach ($command in $commands) {
            $scriptPath = Join-Path $project $command[0]
            & $venvPython $scriptPath $command[1] *> $null
            if ($LASTEXITCODE -ne 0) { throw "CLI help failed: $($command[0])" }
            $checked += $command[0]
        }
        return [ordered]@{ checked = $checked; configuration_check_run = $false }
    }

    Invoke-Stage 'qq-metadata' {
        $candidates = @(
            'C:\Program Files\Tencent\QQNT\QQ.exe',
            'C:\Program Files (x86)\Tencent\QQNT\QQ.exe'
        )
        $qq = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        if (-not $qq) { throw 'Official QQ executable was not found in the expected machine paths.' }
        $item = Get-Item -LiteralPath $qq
        $signature = Get-AuthenticodeSignature -LiteralPath $qq
        if ($signature.Status -ne 'Valid') { throw "QQ executable signature is not valid: $($signature.Status)" }
        if ($signature.SignerCertificate.Subject -notmatch '(?i)Tencent') { throw 'QQ executable signer is not Tencent.' }
        if ([string]::IsNullOrWhiteSpace($item.VersionInfo.FileVersion) -or
            [string]::IsNullOrWhiteSpace($item.VersionInfo.ProductVersion)) {
            throw 'QQ executable version metadata is missing.'
        }
        return [ordered]@{
            path = $qq
            file_version = $item.VersionInfo.FileVersion
            product_version = $item.VersionInfo.ProductVersion
            signer_subject = $signature.SignerCertificate.Subject
            signature_status = [string]$signature.Status
            process_started = $false
        }
    }

    Write-Status 'complete'
    Write-Host "Runtime dependencies are ready. Status: $statusPath"
} catch {
    Write-Error $_
    exit 1
}
