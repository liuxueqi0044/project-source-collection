[CmdletBinding()]
param([switch]$Rotate)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSCommandPath
$aux = Join-Path $root 'aux'
$state = Join-Path $root 'credential-state'
$userFile = Join-Path $aux 'pmai_setup.password'
$adminFile = Join-Path $aux 'administrator.password'
if ((Test-Path -LiteralPath $userFile) -or (Test-Path -LiteralPath $adminFile)) {
    if (-not $Rotate) { throw 'Credential files already exist; refusing overwrite. Use -Rotate only before formal rendering.' }
    $vbox = 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe'
    $stateLine = & $vbox showvminfo PersonalMessengerQQ --machinereadable | Where-Object { $_ -match '^VMState=' }
    if ($stateLine -notmatch 'poweroff') { throw 'Refusing credential rotation: VM is not powered off.' }
    $rendered = Get-ChildItem -LiteralPath $aux -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notin @('pmai_setup.password','administrator.password') }
    if ($rendered) { throw 'Refusing credential rotation: unattended auxiliary files already exist.' }
}
New-Item -ItemType Directory -Force -Path $aux,$state | Out-Null
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
foreach ($path in @($aux,$state)) {
    & icacls.exe $path /inheritance:r /grant:r "${identity}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE) { throw "Failed to set ACL: $path" }
}
function New-Password {
    $bytes = [byte[]]::new(30)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return ([Convert]::ToBase64String($bytes).Replace('+','A').Replace('/','B').Replace('=','C') + '!a7')
}
$userPassword = New-Password
$adminPassword = New-Password
[System.IO.File]::WriteAllText($userFile, $userPassword, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText($adminFile, $adminPassword, [System.Text.UTF8Encoding]::new($false))
foreach ($path in @($userFile,$adminFile)) { & icacls.exe $path /inheritance:r /grant:r "${identity}:F" 'SYSTEM:F' | Out-Null }
ConvertTo-SecureString -String $userPassword -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath (Join-Path $state 'pmai_setup.dpapi') -NoNewline -Encoding ascii
ConvertTo-SecureString -String $adminPassword -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath (Join-Path $state 'administrator.dpapi') -NoNewline -Encoding ascii
[pscustomobject]@{user='pmai_setup';auxiliary_path=$aux;dpapi_state_path=$state;plaintext_files_created=$true;acl_principal=$identity} | ConvertTo-Json
