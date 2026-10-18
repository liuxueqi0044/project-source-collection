param(
    [Parameter(Mandatory = $true)]
    [string] $MediaRoot
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$expectedComputer = 'PMAI-QQVM'
$expectedWheelHash = '284EFF831DCBBFC337C4128FB45C34B44F767289FC61E2D3EF71A88B861C6124'
$expectedProviderHash = 'D3B976055996107F13987600DE4E3331C5F5DF2526A5593B1BEA4518BA6CCAF3'
$expectedDeepSeekHash = '55628728F32857B20F33940F40A59372367757CA22B04CD58674CC774EA6CFC8'
$wheelName = 'personal_messenger_ai-0.1.0-py3-none-any.whl'
$python = 'C:\PMAI\app\.venv\Scripts\python.exe'
$statusPath = 'C:\PMAI\data\pmai-wheel-update-status.json'
$status = [ordered]@{
    schema = 'pmai-wheel-update-v1'
    state = 'running'
    package_version = '0.1.0'
    wheel_sha256 = $expectedWheelHash
    provider_sha256 = $null
    deepseek_sha256 = $null
    pip_exit_code = $null
    error_code = $null
    updated_utc = [DateTime]::UtcNow.ToString('o')
}

function Write-Status {
    $status.updated_utc = [DateTime]::UtcNow.ToString('o')
    $json = $status | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText($statusPath, $json, [Text.UTF8Encoding]::new($false))
}

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $computer = Get-CimInstance Win32_ComputerSystem
    if ($env:COMPUTERNAME -cne $expectedComputer -or
        $computer.Name -cne $expectedComputer -or
        $computer.Model -notmatch 'VirtualBox' -or
        $computer.Manufacturer -notmatch '(Oracle|innotek)' -or
        $identity.Name -notlike '*\qqbot' -or
        $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw [InvalidOperationException]::new('GUEST_GUARD_FAILED')
    }

    $media = [IO.Path]::GetFullPath($MediaRoot)
    $wheel = [IO.Path]::GetFullPath((Join-Path $media $wheelName))
    if (-not $wheel.StartsWith($media, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $wheel -PathType Leaf) -or
        -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw [InvalidOperationException]::new('DEPLOYMENT_PATH_FAILED')
    }
    if ((Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash -cne $expectedWheelHash) {
        throw [InvalidOperationException]::new('WHEEL_HASH_FAILED')
    }

    & $python -m pip install --no-index --disable-pip-version-check --no-deps --force-reinstall $wheel 2>&1 | Out-Host
    $status.pip_exit_code = $LASTEXITCODE
    if ($LASTEXITCODE -ne 0) {
        throw [InvalidOperationException]::new('PIP_INSTALL_FAILED')
    }

    $sitePackages = & $python -c "import sysconfig; print(sysconfig.get_path('purelib'))"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sitePackages)) {
        throw [InvalidOperationException]::new('SITE_PACKAGES_FAILED')
    }
    $venvRoot = [IO.Path]::GetFullPath('C:\PMAI\app\.venv')
    $sitePackages = [IO.Path]::GetFullPath($sitePackages.Trim())
    if (-not $sitePackages.StartsWith($venvRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) {
        throw [InvalidOperationException]::new('SITE_PACKAGES_GUARD_FAILED')
    }
    $providerPath = Join-Path $sitePackages 'messenger_ai\llm\providers.py'
    $deepSeekPath = Join-Path $sitePackages 'messenger_ai\llm\deepseek.py'
    $status.provider_sha256 = (Get-FileHash -LiteralPath $providerPath -Algorithm SHA256).Hash
    $status.deepseek_sha256 = (Get-FileHash -LiteralPath $deepSeekPath -Algorithm SHA256).Hash
    if ($status.provider_sha256 -cne $expectedProviderHash -or
        $status.deepseek_sha256 -cne $expectedDeepSeekHash) {
        throw [InvalidOperationException]::new('INSTALLED_FILE_HASH_FAILED')
    }

    $status.state = 'succeeded'
    Write-Status
    exit 0
}
catch {
    $status.state = 'failed'
    $status.error_code = if ($_.Exception.Message -match '^[A-Z_]+$') {
        $_.Exception.Message
    } else {
        $_.Exception.GetType().Name
    }
    Write-Status
    exit 1
}
