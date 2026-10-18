[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$computer = Get-CimInstance Win32_ComputerSystem
if ($env:COMPUTERNAME -ne 'PMAI-QQVM' -or
    $computer.Model -notlike '*VirtualBox*' -or
    $computer.Manufacturer -notmatch '(?i)(Oracle|innotek)') {
    throw 'This diagnostic is restricted to the PMAI-QQVM VirtualBox guest.'
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($identity.Name -notlike '*\qqbot') { throw 'Run this diagnostic as qqbot.' }
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This diagnostic must run with the standard qqbot token.'
}

$statusRoot = 'C:\PMAI\data'
$statusPath = Join-Path $statusRoot 'acl-diagnostic.json'
$bootstrap = 'C:\PMAI\app\scripts\deployment\guest_bootstrap.ps1'
$python = 'C:\PMAI\tools\Python313\python.exe'
$targets = @(
    'C:\PMAI',
    'C:\PMAI\app',
    'C:\PMAI\app\scripts',
    'C:\PMAI\app\scripts\deployment',
    $bootstrap,
    'C:\PMAI\tools',
    'C:\PMAI\tools\Python313',
    $python
)

function Get-SafeError([Management.Automation.ErrorRecord]$Record) {
    [ordered]@{
        exception_type = $Record.Exception.GetType().FullName
        category = [string]$Record.CategoryInfo.Category
        fully_qualified_error_id = $Record.FullyQualifiedErrorId
        hresult = ('0x{0:X8}' -f ($Record.Exception.HResult -band 0xffffffffL))
    }
}

function Inspect-Path([string]$Path) {
    $result = [ordered]@{ path = $Path; exists = $false }
    try {
        $item = Get-Item -LiteralPath $Path -Force
        $result.exists = $true
        $result.is_container = [bool]$item.PSIsContainer
        $result.attributes = [string]$item.Attributes
        $result.length = if ($item.PSIsContainer) { $null } else { $item.Length }
        $result.encrypted = [bool]($item.Attributes -band [IO.FileAttributes]::Encrypted)
    } catch {
        $result.item_error = Get-SafeError $_
        return $result
    }
    try {
        $acl = Get-Acl -LiteralPath $Path
        $result.owner = $acl.Owner
        $result.access_rules_protected = $acl.AreAccessRulesProtected
        $result.sddl = $acl.Sddl
        $result.aces = @($acl.Access | ForEach-Object {
            [ordered]@{
                identity = $_.IdentityReference.Value
                type = [string]$_.AccessControlType
                rights = [string]$_.FileSystemRights
                inherited = $_.IsInherited
                inheritance = [string]$_.InheritanceFlags
                propagation = [string]$_.PropagationFlags
            }
        })
    } catch { $result.acl_error = Get-SafeError $_ }
    return $result
}

$report = [ordered]@{
    schema = 1
    observed_at_utc = [DateTime]::UtcNow.ToString('o')
    computer = $env:COMPUTERNAME
    current_identity = $identity.Name
    current_sid = $identity.User.Value
    current_is_administrator = $false
    modified_acl = $false
    targets = @($targets | ForEach-Object { Inspect-Path $_ })
}

try {
    $text = [IO.File]::ReadAllText($bootstrap)
    $report.bootstrap_read = [ordered]@{ succeeded = $true; character_count = $text.Length }
} catch { $report.bootstrap_read = [ordered]@{ succeeded = $false; error = Get-SafeError $_ } }

try {
    $hash = (Get-FileHash -LiteralPath $bootstrap -Algorithm SHA256).Hash
    $report.bootstrap_hash = [ordered]@{ succeeded = $true; sha256 = $hash }
} catch { $report.bootstrap_hash = [ordered]@{ succeeded = $false; error = Get-SafeError $_ } }

try {
    $version = (& $python --version 2>&1 | Out-String).Trim()
    $report.python_execute = [ordered]@{ succeeded = ($LASTEXITCODE -eq 0); exit_code = $LASTEXITCODE; version = $version }
} catch { $report.python_execute = [ordered]@{ succeeded = $false; error = Get-SafeError $_ } }

New-Item -ItemType Directory -Force -Path $statusRoot | Out-Null
$report | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath $statusPath -Encoding UTF8
Write-Host "ACL diagnostic saved to $statusPath"
if (-not $report.bootstrap_read.succeeded -or -not $report.bootstrap_hash.succeeded -or -not $report.python_execute.succeeded) { exit 2 }
exit 0
