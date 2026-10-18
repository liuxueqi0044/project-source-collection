[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$computer = Get-CimInstance Win32_ComputerSystem
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if ($env:COMPUTERNAME -ne 'PMAI-QQVM' -or $computer.Model -notlike '*VirtualBox*' -or
    $computer.Manufacturer -notmatch '(?i)(Oracle|innotek)' -or
    -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This repair requires an elevated session inside PMAI-QQVM.'
}

$appRoot = 'C:\PMAI\app'
$toolsRoot = 'C:\PMAI\tools'
$pythonRoot = 'C:\PMAI\tools\Python313'
$statusPath = 'C:\ProgramData\PMAI\file-acl-repair-status.json'
foreach ($expectedRoot in @($appRoot, $toolsRoot, $pythonRoot)) {
    $resolved = (Resolve-Path -LiteralPath $expectedRoot -ErrorAction Stop).Path.TrimEnd('\')
    if ($resolved -cne $expectedRoot) { throw "Unexpected repair root: $resolved" }
}
$qqbot = Get-LocalUser -Name 'qqbot' -ErrorAction Stop
$qqbotSid = $qqbot.SID.Value
$admins = @(Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object { $_.SID.Value })
if (-not $qqbot.Enabled -or $admins -contains $qqbotSid) { throw 'qqbot is not an enabled standard user.' }

$previousAttempt = $null
if (Test-Path -LiteralPath $statusPath -PathType Leaf) {
    try {
        $previous = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
        $previousAttempt = [ordered]@{
            state = $previous.state
            updated_at_utc = $previous.updated_at_utc
            app_files_repaired = $previous.app_files_repaired
            tools_files_repaired = $previous.tools_files_repaired
            tools_file_owners_changed = $previous.tools_file_owners_changed
        }
    } catch { $previousAttempt = [ordered]@{ state = 'unreadable' } }
}

$result = [ordered]@{
    schema = 1
    started_at_utc = [DateTime]::UtcNow.ToString('o')
    state = 'running'
    owner_changed = $false
    directory_acls_changed = $false
    app_files_repaired = 0
    tools_files_repaired = 0
    tools_file_owners_changed = 0
    failed_path = $null
    failure = $null
    previous_attempt = $previousAttempt
}
function Save-Status {
    $parent = Split-Path -Parent $statusPath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $result['updated_at_utc'] = [DateTime]::UtcNow.ToString('o')
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statusPath -Encoding UTF8
}
function Get-ValidatedFiles([string]$Root) {
    $prefix = $Root.TrimEnd('\') + '\'
    $stack = [Collections.Generic.Stack[string]]::new()
    $files = [Collections.Generic.List[string]]::new()
    $stack.Push($Root)
    while ($stack.Count -gt 0) {
        $directoryPath = $stack.Pop()
        $directory = Get-Item -LiteralPath $directoryPath -Force
        if (-not $directory.PSIsContainer -or ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing non-directory or reparse-point directory: $directoryPath"
        }
        foreach ($item in Get-ChildItem -LiteralPath $directory.FullName -Force) {
            $full = [IO.Path]::GetFullPath($item.FullName)
            if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Refusing path outside repair root: $full"
            }
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing reparse point: $full"
            }
            if ($item.PSIsContainer) { $stack.Push($full) } else { $files.Add($full) }
        }
    }
    return $files.ToArray()
}
function Invoke-IcaclsGrant([string]$Path, [string]$QqbotRight) {
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $nativeOutput = (& icacls.exe $Path /inheritance:r /grant:r `
            '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' "*$qqbotSid`:($QqbotRight)" 2>&1 | ForEach-Object ToString | Out-String).Trim()
        $nativeExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $savedPreference }
    return [ordered]@{ exit_code = $nativeExit; output = $nativeOutput }
}
function Repair-Files([string[]]$Files, [string]$QqbotRight, [string]$Counter, [bool]$AllowTakeOwnership) {
    foreach ($full in $Files) {
        $result.failed_path = $full
        $grant = Invoke-IcaclsGrant $full $QqbotRight
        if ($grant.exit_code -ne 0 -and $AllowTakeOwnership) {
            $savedPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'Continue'
                $takeownOutput = (& takeown.exe /F $full /A 2>&1 | ForEach-Object ToString | Out-String).Trim()
                $takeownExit = $LASTEXITCODE
            } finally { $ErrorActionPreference = $savedPreference }
            if ($takeownExit -ne 0) {
                throw "takeown failed for dedicated Python file (exit $takeownExit): $takeownOutput"
            }
            $result.tools_file_owners_changed = [int]$result.tools_file_owners_changed + 1
            $result.owner_changed = $true
            $grant = Invoke-IcaclsGrant $full $QqbotRight
        }
        if ($grant.exit_code -ne 0) {
            throw "icacls failed for $full (exit $($grant.exit_code)): $($grant.output)"
        }
        $result[$Counter] = [int]$result[$Counter] + 1
    }
}

Save-Status
try {
    Write-Host 'Validating all app and dedicated Python paths before changing file ACLs...'
    $appFiles = @(Get-ValidatedFiles $appRoot)
    $pythonFiles = @(Get-ValidatedFiles $pythonRoot)
    if ($previousAttempt -and $previousAttempt.state -eq 'failed' -and
        [int]$previousAttempt.app_files_repaired -eq $appFiles.Count) {
        $result['app_files_reused_from_previous_attempt'] = $appFiles.Count
        Write-Host "App file ACL repair already completed in the previous attempt ($($appFiles.Count) files); skipping."
    } else {
        Write-Host 'Repairing file ACLs under C:\PMAI\app (directories and owners remain unchanged)...'
        Repair-Files $appFiles 'M' 'app_files_repaired' $false
    }
    Write-Host 'Repairing file ACLs under C:\PMAI\tools\Python313 (only dedicated Python file owners may change on access failure)...'
    Repair-Files $pythonFiles 'RX' 'tools_files_repaired' $true

    $bootstrap = Join-Path $appRoot 'scripts\deployment\guest_bootstrap.ps1'
    $pyproject = Join-Path $appRoot 'pyproject.toml'
    $python = Join-Path $toolsRoot 'Python313\python.exe'
    $null = [IO.File]::ReadAllText($bootstrap)
    $null = Get-FileHash -LiteralPath $pyproject -Algorithm SHA256
    $version = (& $python --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $version -ne 'Python 3.13.15') { throw 'Python execution verification failed.' }
    $result.verification = [ordered]@{
        bootstrap_read = $true
        pyproject_hash = $true
        python_version = $version
    }
    $result.state = 'succeeded'
    $result.failed_path = $null
    Save-Status
    Write-Host "App files repaired: $($result.app_files_repaired)"
    Write-Host "Python files repaired: $($result.tools_files_repaired)"
    Write-Host "Python file owners changed: $($result.tools_file_owners_changed)"
    Write-Host "Verification: bootstrap_read=$($result.verification.bootstrap_read), pyproject_hash=$($result.verification.pyproject_hash), python=$($result.verification.python_version)"
    Write-Host "File ACL repair succeeded. Status: $statusPath"
} catch {
    $result.state = 'failed'
    $result.failure = [ordered]@{
        exception_type = $_.Exception.GetType().FullName
        hresult = ('0x{0:X8}' -f ($_.Exception.HResult -band 0xffffffffL))
        message = $_.Exception.Message
    }
    Save-Status
    throw
}
