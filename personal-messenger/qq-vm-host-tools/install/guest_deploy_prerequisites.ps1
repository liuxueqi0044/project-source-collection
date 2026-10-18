[CmdletBinding()]
param(
    [string]$MediaRoot = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$computerSystem = Get-CimInstance -ClassName Win32_ComputerSystem
$isVirtualBox = ($computerSystem.Model -match '^VirtualBox$') -and ($computerSystem.Manufacturer -match '(Oracle|innotek)')
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isVirtualBox -or $env:COMPUTERNAME -cne 'PMAI-QQVM' -or -not $isAdministrator) {
    throw 'This script requires an elevated session inside the PMAI-QQVM VirtualBox guest.'
}

$secretPath = 'C:\Users\pmai_setup\AppData\Local\Temp\pmai-qqbot.password.tmp'
if ([string]::IsNullOrWhiteSpace($MediaRoot)) { throw 'MediaRoot is required.' }
$pythonInstaller = Join-Path $MediaRoot 'python-3.13.15-amd64.exe'
$qqInstaller = Join-Path $MediaRoot 'QQ_9.9.33_260730_x64_01.exe'
$packageZip = Join-Path $MediaRoot 'personal-messenger-ai-v5-deploy.zip'
$statusRoot = 'C:\ProgramData\PMAI'
$statusPath = Join-Path $statusRoot 'prerequisites-status.json'
$appRoot = 'C:\PMAI'
$provisionState = @{ accountProvisioned = $false }
$expected = @{
    $pythonInstaller = 'EDEC09C4853AEAE9AC36EFB8C9F95B6B8E2FEE65EEE56D9767A8B7C69C574403'
    $qqInstaller = '7BCC1AD50C19D14C9AAD17778A0359AACD84B4D6C54EF1C71D938E0A6E2F2F5D'
    $packageZip = '82DA0CB15CADD3E7635C6DC26DB0DE368261F852CFCE480770AF480B4FBDBFF8'
}
$results = [Collections.Generic.List[object]]::new()

function Write-Status([string]$Overall) {
    New-Item -ItemType Directory -Force -Path $statusRoot | Out-Null
    [ordered]@{
        overall = $Overall
        updated_utc = [DateTime]::UtcNow.ToString('o')
        stages = $results.ToArray()
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statusPath -Encoding utf8
}

function Invoke-Stage([string]$Name, [scriptblock]$Action) {
    Write-Host "Stage: $Name"
    try {
        & $Action
        $results.Add([ordered]@{ name = $Name; succeeded = $true })
        Write-Status 'running'
    }
    catch {
        $results.Add([ordered]@{ name = $Name; succeeded = $false; error = $_.Exception.Message })
        Write-Status 'failed'
        throw
    }
}

function Invoke-OptionalStage([string]$Name, [scriptblock]$Action) {
    Write-Host "Stage: $Name (optional)"
    try {
        & $Action
        $results.Add([ordered]@{ name = $Name; succeeded = $true; blocking = $false })
    }
    catch {
        $results.Add([ordered]@{ name = $Name; succeeded = $false; blocking = $false; error = $_.Exception.Message })
    }
    Write-Status 'running'
}

function Assert-ValidSignature([string]$Path, [string]$SubjectPattern) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch $SubjectPattern) {
        throw "Authenticode verification failed: $Path"
    }
}

function Expand-SafeZip([string]$ZipPath, [string]$Destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $destinationFull = [IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
    New-Item -ItemType Directory -Force -Path $destinationFull | Out-Null
    $archive = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        foreach ($entry in $archive.Entries) {
            if ([string]::IsNullOrEmpty($entry.FullName)) { continue }
            $target = [IO.Path]::GetFullPath((Join-Path $destinationFull $entry.FullName))
            if (-not $target.StartsWith($destinationFull, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Unsafe ZIP entry rejected: $($entry.FullName)"
            }
            if ([string]::IsNullOrEmpty($entry.Name)) {
                New-Item -ItemType Directory -Force -Path $target | Out-Null
                continue
            }
            New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($target)) | Out-Null
            $zipInput = $entry.Open()
            try {
                $output = [IO.File]::Open($target, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
                try { $zipInput.CopyTo($output) } finally { $output.Dispose() }
            }
            finally { $zipInput.Dispose() }
        }
    }
    finally { $archive.Dispose() }
}

Write-Status 'running'
try {
    Invoke-Stage 'media-validation' {
        foreach ($path in $expected.Keys) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required staged file missing: $path" }
            if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $expected[$path]) {
                throw "SHA-256 mismatch: $path"
            }
        }
        Assert-ValidSignature $pythonInstaller 'Python Software Foundation'
        Assert-ValidSignature $qqInstaller 'Tencent'
    }

    Invoke-Stage 'qqbot-account' {
        if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) { throw 'Staged qqbot password is missing.' }
        $plain = [IO.File]::ReadAllText($secretPath).Trim()
        try {
            $secure = ConvertTo-SecureString -String $plain -AsPlainText -Force
            $account = Get-LocalUser -Name 'qqbot' -ErrorAction SilentlyContinue
            if ($account) { $account | Set-LocalUser -Password $secure }
            else { New-LocalUser -Name 'qqbot' -Password $secure -Description 'PMAI standard interactive runtime account' | Out-Null }
        }
        finally { $plain = $null }
        Add-LocalGroupMember -SID 'S-1-5-32-545' -Member 'qqbot' -ErrorAction SilentlyContinue
        Remove-LocalGroupMember -SID 'S-1-5-32-544' -Member 'qqbot' -ErrorAction SilentlyContinue
        $users = @(Get-LocalGroupMember -SID 'S-1-5-32-545' | ForEach-Object Name)
        $admins = @(Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object Name)
        if (-not (Get-LocalUser qqbot).Enabled -or -not [bool]($users -match '\\qqbot$') -or [bool]($admins -match '\\qqbot$')) {
            throw 'qqbot account or group validation failed.'
        }
        $provisionState.accountProvisioned = $true
    }

    Invoke-Stage 'python-machine-install' {
        New-Item -ItemType Directory -Force -Path 'C:\PMAI\tools\Python313' | Out-Null
        $process = Start-Process -FilePath $pythonInstaller -ArgumentList @(
            '/quiet', 'InstallAllUsers=1', 'TargetDir=C:\PMAI\tools\Python313',
            'PrependPath=0', 'Include_launcher=1', 'InstallLauncherAllUsers=1', 'Include_test=0'
        ) -Wait -PassThru
        if ($process.ExitCode -notin 0,3010) { throw "Python installer exit code: $($process.ExitCode)" }
        $pythonExe = 'C:\PMAI\tools\Python313\python.exe'
        if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) { throw 'Installed Python executable not found.' }
        Assert-ValidSignature $pythonExe 'Python Software Foundation'
        $version = & $pythonExe --version 2>&1
        if ($LASTEXITCODE -ne 0 -or ([string]$version).Trim() -ne 'Python 3.13.15') { throw 'Installed Python version validation failed.' }
    }

    Invoke-Stage 'qq-machine-install' {
        $process = Start-Process -FilePath $qqInstaller -ArgumentList '/s' -Wait -PassThru
        if ($process.ExitCode -notin 0,3010) { throw "QQ installer exit code: $($process.ExitCode)" }
        $candidates = @(
            'C:\Program Files\Tencent\QQNT\QQ.exe',
            'C:\Program Files\Tencent\QQ\QQ.exe',
            'C:\Program Files (x86)\Tencent\QQNT\QQ.exe',
            'C:\Program Files (x86)\Tencent\QQ\QQ.exe'
        )
        $qqExe = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        if (-not $qqExe) { throw 'Installed QQ executable not found in an expected machine path.' }
        Assert-ValidSignature $qqExe 'Tencent'
        $qqVersion = (Get-Item -LiteralPath $qqExe).VersionInfo.ProductVersion
        if ([string]::IsNullOrWhiteSpace($qqVersion)) { throw 'Installed QQ version is unavailable.' }
    }

    Invoke-Stage 'application-layout' {
        $app = Join-Path $appRoot 'app'
        $data = Join-Path $appRoot 'data'
        $secrets = Join-Path $appRoot 'secrets'
        $venv = Join-Path $app '.venv'
        foreach ($path in $app,$data,$secrets,$venv) { New-Item -ItemType Directory -Force -Path $path | Out-Null }

        & icacls.exe $appRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(RX)' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to secure PMAI root ACL.' }
        & icacls.exe $app /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' 'qqbot:(OI)(CI)M' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to secure application ACL.' }
        foreach ($path in $data,$secrets,$venv) {
            & icacls.exe $path /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' 'qqbot:(OI)(CI)M' | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Failed to secure writable directory ACL: $path" }
        }
        Expand-SafeZip $packageZip $app
        & icacls.exe $app /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' 'qqbot:(OI)(CI)M' /t /c | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to apply application tree ACL.' }
        & icacls.exe (Join-Path $appRoot 'tools') /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' /t /c | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to apply tools tree ACL.' }
    }

    Invoke-OptionalStage 'optional-windows-observations' {
        $observationError = $null
        try {
            $winre = & reagentc.exe /info 2>&1
            $winreExit = $LASTEXITCODE
            $winre | Set-Content -LiteralPath (Join-Path $statusRoot 'reagentc-info.txt') -Encoding utf8
        }
        catch { $winre = @(); $winreExit = $null; $observationError = $_.Exception.Message }
        $panther = 'C:\Windows\Panther\unattend.xml'
        $pantherPresentBefore = Test-Path -LiteralPath $panther -PathType Leaf
        $pantherSensitiveNonEmpty = $null
        if ($pantherPresentBefore) {
            try {
                [xml]$xml = Get-Content -LiteralPath $panther -Raw
                $nodes = $xml.SelectNodes("//*[local-name()='Password' or local-name()='AdministratorPassword']/*[local-name()='Value'] | //*[local-name()='AutoLogon']//*[local-name()='Password']/*[local-name()='Value']")
                $pantherSensitiveNonEmpty = [bool](@($nodes | Where-Object { -not [string]::IsNullOrWhiteSpace($_.'#text') }).Count)
                if ($pantherSensitiveNonEmpty) { Remove-Item -LiteralPath $panther -Force }
            }
            catch { $pantherSensitiveNonEmpty = $null }
        }
        [ordered]@{
            winre_info_captured = [bool]$winre
            winre_exit_code = $winreExit
            panther_unattend_present_before = $pantherPresentBefore
            panther_unattend_present_after = Test-Path -LiteralPath $panther -PathType Leaf
            panther_sensitive_fields_nonempty = $pantherSensitiveNonEmpty
            observation_error = $observationError
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $statusRoot 'windows-observations.json') -Encoding utf8
    }

    Write-Status 'succeeded'
}
catch {
    throw
}
finally {
    if ($provisionState.accountProvisioned) { Remove-Item -LiteralPath $secretPath -Force -ErrorAction SilentlyContinue }
}
