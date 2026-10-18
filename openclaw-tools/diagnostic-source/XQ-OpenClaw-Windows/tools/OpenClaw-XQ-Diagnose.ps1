[CmdletBinding()]
param(
    [string] $OutputDirectory,
    [ValidateRange(1,300)][int] $TimeoutSeconds = 20,
    [string] $PolicyPath,
    [switch] $NoRun
)

# XQ v0.1.0 diagnostic: read-only probes only. Keep this file inspectable and offline.
$scriptDirectory = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($scriptDirectory)) { $scriptDirectory = Split-Path -Parent $PSCommandPath }
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) { $OutputDirectory = Join-Path -Path $scriptDirectory -ChildPath 'reports' }
if ([string]::IsNullOrWhiteSpace($PolicyPath)) { $PolicyPath = Join-Path -Path $scriptDirectory -ChildPath '..\policy\diagnostic-command-policy.json' }
$redactionModulePath = Join-Path -Path $scriptDirectory -ChildPath 'XQ.Redaction.psm1'
if (-not (Test-Path -LiteralPath $redactionModulePath -PathType Leaf)) { throw '无法加载统一脱敏模块。' }
Import-Module -Name $redactionModulePath -Force -DisableNameChecking -ErrorAction Stop
$script:XqDiagnosticVersion = '1'
$script:XqDiagnosticPolicy = $null
$script:XqRedactionContext = $null

function Initialize-XqDiagnosticPolicy {
    param([Parameter(Mandatory)][string]$Path)
    try { $policy = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop } catch { throw '无法读取只读诊断命令策略。' }
    if ($policy.mode -ne 'read-only' -or $policy.defaultCommandTimeoutSeconds -lt 1 -or @($policy.commands).Count -eq 0) { throw '只读诊断命令策略无效。' }
    # The policy is an allowlist, not configuration. Every executable, argument and context is pinned here.
    $expected = @(
        @{id='wsl-list'; executable='wsl.exe'; arguments=@('--list','--verbose'); context='windows-host'},
        @{id='wsl-running-list'; executable='wsl.exe'; arguments=@('--list','--running','--quiet'); context='windows-host'},
        @{id='managed-wsl-openclaw-version'; executable='wsl.exe'; arguments=@('--distribution','OpenClawGateway','--user','openclaw','--','openclaw','--version'); context='managed-wsl-openclaw-user'},
        @{id='managed-wsl-openclaw-doctor'; executable='wsl.exe'; arguments=@('--distribution','OpenClawGateway','--user','openclaw','--','openclaw','doctor','--json'); context='managed-wsl-openclaw-user'},
        @{id='managed-wsl-openclaw-gateway-status'; executable='wsl.exe'; arguments=@('--distribution','OpenClawGateway','--user','openclaw','--','openclaw','gateway','status','--json'); context='managed-wsl-openclaw-user'},
        @{id='native-openclaw-version'; executable='openclaw'; arguments=@('--version'); context='native-cli-if-present'},
        @{id='native-openclaw-doctor'; executable='openclaw'; arguments=@('doctor','--json'); context='native-cli-if-present'},
        @{id='native-openclaw-gateway-status'; executable='openclaw'; arguments=@('gateway','status','--json'); context='native-cli-if-present'},
        @{id='node-version'; executable='node'; arguments=@('--version'); context='windows-host'},
        @{id='npm-version'; executable='npm'; arguments=@('--version'); context='windows-host'}
    )
    $actualIds = @($policy.commands | ForEach-Object { [string]$_.id })
    if (($actualIds | Sort-Object -Unique).Count -ne $actualIds.Count) { throw '只读诊断命令策略包含重复 ID。' }
    foreach ($item in $expected) {
        $actual = @($policy.commands | Where-Object { $_.id -eq $item.id })
        if ($actual.Count -ne 1) { throw ('只读诊断命令策略缺少或重复必需命令：' + $item.id) }
        if ([string]$actual[0].executable -cne $item.executable -or [string]$actual[0].executionContext -cne $item.context) { throw ('只读诊断命令策略被改写：' + $item.id) }
        $actualArgs = @($actual[0].arguments)
        if ($actualArgs.Count -ne $item.arguments.Count) { throw ('只读诊断命令参数数量不符：' + $item.id) }
        for ($i=0; $i -lt $item.arguments.Count; $i++) { if ([string]$actualArgs[$i] -cne [string]$item.arguments[$i]) { throw ('只读诊断命令参数被改写：' + $item.id) } }
    }
    if ($actualIds.Count -ne $expected.Count) { throw '只读诊断命令策略包含未批准的额外命令。' }
    foreach ($command in @($policy.commands)) {
        $joined = (([string]$command.executable) + ' ' + (@($command.arguments) -join ' ')).ToLowerInvariant()
        foreach ($forbidden in @($policy.forbiddenTokens)) {
            if ($joined -match ('(?i)(^|[^a-z])' + [regex]::Escape([string]$forbidden) + '([^a-z]|$)')) { throw '只读诊断命令策略包含禁止词。' }
        }
    }
    $script:XqDiagnosticPolicy = $policy
    return $policy
}

function Get-XqPolicyCommand {
    param([Parameter(Mandatory)][string]$Id)
    if ($null -eq $script:XqDiagnosticPolicy) { throw '诊断命令策略尚未初始化。' }
    $command = @($script:XqDiagnosticPolicy.commands | Where-Object { $_.id -eq $Id })
    if ($command.Count -ne 1) { throw ('诊断命令不在唯一 allowlist 中：' + $Id) }
    return $command[0]
}

function New-XqFinding {
    param([string]$Component,[string]$Code,[string]$Message,[ValidateSet('Info','Warning','Error')][string]$Severity='Warning')
    [pscustomobject]@{ Component=$Component; Code=$Code; Severity=$Severity; Message=$Message }
}

function Get-XqCommandLine {
    param([string]$FileName,[string[]]$Arguments)
    if ($Arguments.Count -eq 0) { return $FileName }
    return ($FileName + ' ' + (($Arguments | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '(?<!^)"','\\"') + '"' } else { $_ }
    }) -join ' '))
}

function Invoke-XqExternalCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FileName,
        [string[]]$Arguments = @(),
        [ValidateRange(1,300)][int]$TimeoutSeconds = 20
    )
    $result = [ordered]@{ FileName=$FileName; Arguments=@($Arguments); ExitCode=$null; TimedOut=$false; StdOut=''; StdErr=''; Error=$null }
    $process = $null
    try {
        $start = New-Object System.Diagnostics.ProcessStartInfo
        $start.FileName = $FileName
        $start.Arguments = (($Arguments | ForEach-Object {
            if ($_ -match '[\s"]') { '"' + ($_ -replace '(?<!^)"','\\"') + '"' } else { $_ }
        }) -join ' ')
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $start
        if (-not $process.Start()) { throw '进程无法启动' }
        # Begin both reads before waiting. Waiting first can deadlock when a child fills a pipe.
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $result.TimedOut = $true
            try { $process.Kill() } catch { Write-Verbose 'Timed-out child process could not be killed.' }
            try { [void]$process.WaitForExit(2000) } catch { Write-Verbose 'Timed-out child process did not report an exit state.' }
            $result.Error = '外部命令超过时间限制，已停止等待。'
        }
        # Keep output only in this controlled in-memory object; Invoke-XqSafeCommand immediately
        # performs structured or textual redaction before the value can reach a report.
        try { $result.StdOut = [string]$stdoutTask.GetAwaiter().GetResult() } catch { $result.StdOut = '' }
        try { $result.StdErr = [string]$stderrTask.GetAwaiter().GetResult() } catch { $result.StdErr = '' }
        if (-not $result.TimedOut) { $result.ExitCode = $process.ExitCode }
    } catch {
        $result.Error = '外部命令不可用或启动失败。'
    } finally {
        if ($null -ne $process) { $process.Dispose() }
    }
    return [pscustomobject]$result
}

function Invoke-XqSafeCommand {
    param([string]$Component,[string]$DisplayCommand,[string]$FileName,[string[]]$Arguments,[int]$TimeoutSeconds)
    $raw = Invoke-XqExternalCommand -FileName $FileName -Arguments $Arguments -TimeoutSeconds $TimeoutSeconds
    if ($null -eq $script:XqRedactionContext) { $script:XqRedactionContext = New-XqRedactionContext }
    [pscustomobject]@{
        Component=$Component; Command=$DisplayCommand; ExitCode=$raw.ExitCode; TimedOut=$raw.TimedOut
        StdOut=(Redact-XqCommandOutput -Text $raw.StdOut -Command $DisplayCommand -Context $script:XqRedactionContext); StdErr=(Redact-XqCommandOutput -Text $raw.StdErr -Command $DisplayCommand -Context $script:XqRedactionContext); Error=$raw.Error
        Status=if ($raw.TimedOut) { 'Timeout' } elseif ($null -eq $raw.ExitCode) { 'Unavailable' } elseif ($raw.ExitCode -eq 0) { 'Succeeded' } else { 'Failed' }
    }
}

function Invoke-XqHostProbe {
    $findings = New-Object System.Collections.Generic.List[object]
    $hostData = [ordered]@{ Status='Unknown'; OperatingSystem=$null; Architecture=$null; IsAdministrator=$null; FreeSpaceBytes=$null }
    try {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
        $hostData.OperatingSystem = [string]$os.Caption
        $hostData.Architecture = [string]$os.OSArchitecture
        $hostData.Status = 'Detected'
    } catch { [void]$findings.Add((New-XqFinding 'Host' 'HostProbeUnavailable' '无法读取 Windows 主机的有限信息。')) }
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        $hostData.IsAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { $hostData.IsAdministrator = $null }
    try {
        $drive = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='C:'" -ErrorAction Stop
        if ($drive) { $hostData.FreeSpaceBytes = [int64]$drive.FreeSpace }
    } catch { Write-Verbose 'Free-space probe is unavailable.' }
    return [pscustomobject]@{ Data=[pscustomobject]$hostData; Findings=@($findings.ToArray()) }
}

function Invoke-XqHubProbe {
    $findings = New-Object System.Collections.Generic.List[object]
    $data = [ordered]@{ Status='NotDetected'; DisplayName=$null; Version=$null; Path=$null; SignatureStatus=$null; SignerSubject=$null }
    $entries = @()
    foreach ($root in @('HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
        try { $entries += @(Get-ItemProperty -Path $root -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match '(?i)OpenClaw' }) } catch { Write-Verbose 'One uninstall registry view is unavailable.' }
    }
    $entry = $entries | Select-Object -First 1
    if ($entry) {
        $data.Status = 'Detected'; $data.DisplayName = [string]$entry.DisplayName; $data.Version = [string]$entry.DisplayVersion
        if ($entry.DisplayIcon) {
            $icon = ([string]$entry.DisplayIcon -replace '^"([^"]+)".*$','$1')
            if (Test-Path -LiteralPath $icon -PathType Leaf) { $data.Path = $icon }
        }
        if (-not $data.Path -and $entry.InstallLocation) {
            foreach ($name in @('OpenClaw.Tray.WinUI.exe','OpenClaw Hub.exe')) {
                $candidate = Join-Path ([string]$entry.InstallLocation) $name
                if (Test-Path -LiteralPath $candidate -PathType Leaf) { $data.Path=$candidate; break }
            }
        }
    }
    $candidates = @()
    foreach ($base in @($env:LOCALAPPDATA,$env:ProgramFiles)) {
        if ($base) { $candidates += (Join-Path $base 'OpenClawTray\OpenClaw.Tray.WinUI.exe'); $candidates += (Join-Path $base 'OpenClaw\OpenClaw.Tray.WinUI.exe'); $candidates += (Join-Path $base 'OpenClaw\OpenClaw Hub.exe') }
    }
    $candidate = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if ($candidate) { $data.Status='Detected'; $data.Path=$candidate }
    if ($data.Path -and (Test-Path -LiteralPath $data.Path -PathType Leaf)) {
        try {
            $signature = Get-AuthenticodeSignature -LiteralPath $data.Path -ErrorAction Stop
            $data.SignatureStatus = [string]$signature.Status
            if ($signature.SignerCertificate) { $data.SignerSubject = [string]$signature.SignerCertificate.Subject }
        } catch { [void]$findings.Add((New-XqFinding 'Hub' 'SignatureProbeUnavailable' '无法读取 Hub 文件签名状态。')) }
    }
    if ($data.Status -eq 'NotDetected') { [void]$findings.Add((New-XqFinding 'Hub' 'HubNotDetected' '未在有限安装位置发现 OpenClaw Hub；这不单独表示安装失败。' 'Info')) }
    return [pscustomobject]@{ Data=[pscustomobject]$data; Findings=@($findings.ToArray()) }
}

function Get-XqWslDistribution {
    param([string]$Output)
    # wsl.exe may emit UTF-16/NUL-padded or localized state text; only the fixed name and numeric version are trusted.
    $normalized = ([string]$Output).Replace(([string][char]0),'')
    foreach ($line in ($normalized -split "`r?`n")) {
        if ($line -match '^\s*\*?\s*(OpenClawGateway)\s+(\S+)\s+(\d+)\s*$') {
            $name = [string]$matches[1]; $rawState = [string]$matches[2]; $version = [string]$matches[3]
            $state = $rawState
            if ($state -match '(?i)^running$') { $state='Running' } elseif ($state -match '(?i)^stopped$') { $state='Stopped' } else { $state='Unknown' }
            return [pscustomobject]@{ Name=$name; State=$state; Version=$version }
        }
    }
    return $null
}

function Test-XqWslRunningDistribution {
    param([string]$Output)
    $normalized = ([string]$Output).Replace(([string][char]0),'')
    foreach ($line in ($normalized -split "`r?`n")) {
        if ($line.Trim() -match '^(?:\*\s*)?OpenClawGateway\s*$') { return $true }
    }
    return $false
}

function Invoke-XqManagedGatewayProbe {
    param([int]$TimeoutSeconds)
    $findings = New-Object System.Collections.Generic.List[object]
    $data = [ordered]@{ Status='Unknown'; Distribution='OpenClawGateway'; WslState=$null; WslVersion=$null; CliStatus='NotRun' }
    $commands = New-Object System.Collections.Generic.List[object]
    $listSpec = Get-XqPolicyCommand 'wsl-list'
    $wsl = Invoke-XqSafeCommand 'WslGateway' (($listSpec.executable + ' ' + (@($listSpec.arguments) -join ' '))) $listSpec.executable @($listSpec.arguments) $TimeoutSeconds
    [void]$commands.Add($wsl)
    if ($wsl.Status -ne 'Succeeded') {
        $data.Status='Unavailable'; [void]$findings.Add((New-XqFinding 'WslGateway' 'WslProbeFailed' '无法读取 WSL 发行版列表。'))
        return [pscustomobject]@{ Data=[pscustomobject]$data; Commands=@($commands.ToArray()); Findings=@($findings.ToArray()) }
    }
    $distro = Get-XqWslDistribution $wsl.StdOut
    if (-not $distro) {
        $data.Status='NotDetected'; [void]$findings.Add((New-XqFinding 'WslGateway' 'ManagedGatewayMissing' '未发现默认 OpenClawGateway WSL 发行版。' 'Info'))
        return [pscustomobject]@{ Data=[pscustomobject]$data; Commands=@($commands.ToArray()); Findings=@($findings.ToArray()) }
    }
    $data.WslState=$distro.State; $data.WslVersion=$distro.Version
    $runningSpec = Get-XqPolicyCommand 'wsl-running-list'
    $running = Invoke-XqSafeCommand 'WslGateway' (($runningSpec.executable + ' ' + (@($runningSpec.arguments) -join ' '))) $runningSpec.executable @($runningSpec.arguments) $TimeoutSeconds
    [void]$commands.Add($running)
    if ($running.Status -ne 'Succeeded' -or -not (Test-XqWslRunningDistribution $running.StdOut)) {
        $data.Status=if ($distro.State -eq 'Stopped') { 'Stopped' } else { 'Unknown' }
        [void]$findings.Add((New-XqFinding 'WslGateway' 'ManagedGatewayNotRunning' 'OpenClawGateway 已存在但当前未运行；为保持只读姿态，未启动发行版或执行其中命令。' 'Info'))
        return [pscustomobject]@{ Data=[pscustomobject]$data; Commands=@($commands.ToArray()); Findings=@($findings.ToArray()) }
    }
    $data.Status='Detected'
    foreach ($id in @('managed-wsl-openclaw-version','managed-wsl-openclaw-doctor','managed-wsl-openclaw-gateway-status')) {
        $spec = Get-XqPolicyCommand $id
        $display = $spec.executable + ' ' + (@($spec.arguments) -join ' ')
        $item = Invoke-XqSafeCommand 'ManagedGatewayAdapter' $display $spec.executable @($spec.arguments) $TimeoutSeconds
        [void]$commands.Add($item)
        if ($item.Status -ne 'Succeeded') { [void]$findings.Add((New-XqFinding 'ManagedGatewayAdapter' 'ReadOnlyCommandFailed' ('Managed WSL 只读检查未成功：' + $display))) }
    }
    $data.CliStatus = if (($commands | Where-Object Component -eq 'ManagedGatewayAdapter' | Where-Object Status -eq 'Succeeded').Count -eq 3) { 'Available' } else { 'Partial' }
    return [pscustomobject]@{ Data=[pscustomobject]$data; Commands=@($commands.ToArray()); Findings=@($findings.ToArray()) }
}

function Invoke-XqNativeCliProbe {
    param([int]$TimeoutSeconds)
    $findings = New-Object System.Collections.Generic.List[object]
    $data = [ordered]@{ Status='NotDetected'; Path=$null; Version=$null }
    $commands = New-Object System.Collections.Generic.List[object]
    $command = $null
    try { $command = Get-Command -Name 'openclaw' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1 } catch { Write-Verbose 'Native OpenClaw CLI discovery is unavailable.' }
    if (-not $command) {
        [void]$findings.Add((New-XqFinding 'NativeCli' 'NativeCliNotDetected' '未在 Windows PATH 中发现原生 openclaw CLI；这不是标准 Hub 路径的必要条件。' 'Info'))
        return [pscustomobject]@{ Data=[pscustomobject]$data; Commands=@($commands.ToArray()); Findings=@($findings.ToArray()) }
    }
    $data.Status='Detected'; $data.Path=[string]$command.Source
    if ([string]::IsNullOrWhiteSpace($data.Path)) { $data.Path=[string]$command.Path }
    foreach ($id in @('native-openclaw-version','native-openclaw-doctor','native-openclaw-gateway-status')) {
        $spec = Get-XqPolicyCommand $id
        $display = $spec.executable + ' ' + (@($spec.arguments) -join ' ')
        $item = Invoke-XqSafeCommand 'NativeCliAdapter' $display $data.Path @($spec.arguments) $TimeoutSeconds
        [void]$commands.Add($item)
        if ($item.Status -ne 'Succeeded') { [void]$findings.Add((New-XqFinding 'NativeCliAdapter' 'ReadOnlyCommandFailed' ('Windows 原生 CLI 只读检查未成功：' + $display))) }
    }
    $versionResult = $commands | Where-Object Command -eq 'openclaw --version' | Select-Object -First 1
    if ($versionResult -and $versionResult.Status -eq 'Succeeded') { $data.Version = $versionResult.StdOut.Trim() }
    return [pscustomobject]@{ Data=[pscustomobject]$data; Commands=@($commands.ToArray()); Findings=@($findings.ToArray()) }
}

function Invoke-XqOptionalRuntimeProbe {
    param([int]$TimeoutSeconds)
    $commands = New-Object System.Collections.Generic.List[object]
    $findings = New-Object System.Collections.Generic.List[object]
    foreach ($id in @('node-version','npm-version')) {
        $spec = Get-XqPolicyCommand $id
        $present = $null
        try { $present = Get-Command -Name ([string]$spec.executable) -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1 } catch { Write-Verbose 'Optional runtime discovery is unavailable.' }
        if ($present) {
            $runtimePath = [string]$present.Source
            if ([string]::IsNullOrWhiteSpace($runtimePath)) { $runtimePath = [string]$present.Path }
            if (-not [string]::IsNullOrWhiteSpace($runtimePath)) {
                $item = Invoke-XqSafeCommand 'HostRuntime' ($spec.executable + ' ' + (@($spec.arguments) -join ' ')) $runtimePath @($spec.arguments) $TimeoutSeconds
                [void]$commands.Add($item)
                if ($item.Status -ne 'Succeeded') { [void]$findings.Add((New-XqFinding 'HostRuntime' 'RuntimeVersionFailed' ('附加运行时版本检查未成功：' + $spec.executable))) }
            }
        }
    }
    return [pscustomobject]@{ Commands=@($commands.ToArray()); Findings=@($findings.ToArray()) }
}

function Write-XqPrivacyBlockedNotice {
    param([Parameter(Mandatory)][string]$OutputDirectory)
    $notice = '诊断报告未写出：隐私检查发现可能的敏感内容。请勿发送 API Key、密码、Cookie、Token 或完整配置；请在删除敏感内容后重新运行诊断。'
    try { [void](New-Item -ItemType Directory -Path $OutputDirectory -Force -ErrorAction Stop) } catch { throw '无法创建安全错误提示目录。' }
    for ($attempt=0; $attempt -lt 5; $attempt++) {
        $name = 'OpenClaw-Diagnostic-Privacy-Blocked-{0}-{1}.txt' -f (Get-Date -Format 'yyyyMMdd-HHmmss'),([Guid]::NewGuid().ToString('N').Substring(0,6))
        $path = Join-Path $OutputDirectory $name
        try {
            $stream = New-Object System.IO.FileStream($path,[System.IO.FileMode]::CreateNew,[System.IO.FileAccess]::Write,[System.IO.FileShare]::None)
            try { $writer = New-Object System.IO.StreamWriter($stream,(New-Object System.Text.UTF8Encoding($false))); $writer.Write($notice); $writer.Flush() } finally { if ($writer) { $writer.Dispose() } else { $stream.Dispose() } }
            return $path
        } catch [System.IO.IOException] { continue }
    }
    throw '无法以不覆盖旧文件的方式创建安全错误提示。'
}

function Write-XqDiagnosticReport {
    [CmdletBinding()]
    param([Parameter(Mandatory)][object]$Report,[Parameter(Mandatory)][string]$OutputDirectory)
    if ($null -eq $script:XqRedactionContext) { $script:XqRedactionContext = New-XqRedactionContext }
    $safe = ConvertTo-XqRedactedObject -InputObject $Report -Context $script:XqRedactionContext
    $json = $safe | ConvertTo-Json -Depth 12
    $json = Redact-XqText -InputObject $json -Context $script:XqRedactionContext
    try { Assert-XqTextIsSafe -Text $json } catch { [void](Write-XqPrivacyBlockedNotice -OutputDirectory $OutputDirectory); throw 'Sensitive-content scan failed; only a safe privacy notice was written.' }
    $safe.RedactionSummary = Get-XqRedactionSummary -Context $script:XqRedactionContext
    $lines = New-Object System.Collections.Generic.List[string]
    [void]$lines.Add('XQ 只读诊断报告')
    [void]$lines.Add('================')
    [void]$lines.Add(('报告版本：' + $safe.Metadata.ReportVersion + '；XQ 版本：' + $safe.Metadata.ProductVersion))
    [void]$lines.Add(('生成时间（UTC）：' + $safe.Metadata.GeneratedAtUtc))
    [void]$lines.Add('运行姿态：只读；不提权、不上传、不读取完整配置/会话/原始日志。')
    foreach ($section in @(@{Name='Windows 主机';Value=$safe.WindowsHost},@{Name='官方 Hub';Value=$safe.HubInstallation},@{Name='Managed WSL Gateway';Value=$safe.WslGateway},@{Name='Windows 原生 CLI（附加路径）';Value=$safe.NativeCli})) {
        [void]$lines.Add(''); [void]$lines.Add(('[' + $section.Name + ']'))
        foreach ($property in $section.Value.PSObject.Properties) { [void]$lines.Add((' {0}：{1}' -f $property.Name,$property.Value)) }
    }
    [void]$lines.Add(''); [void]$lines.Add('[健康 finding]')
    if (@($safe.HealthFindings).Count -eq 0) { [void]$lines.Add(' 无') } else { foreach ($finding in @($safe.HealthFindings)) { [void]$lines.Add((' {0} / {1} / {2}：{3}' -f $finding.Severity,$finding.Component,$finding.Code,$finding.Message)) } }
    [void]$lines.Add(''); [void]$lines.Add('[只读命令结果]')
    foreach ($command in @($safe.CommandResults)) {
        [void]$lines.Add((' {0}：{1}（退出码：{2}，超时：{3}）' -f $command.Component,$command.Command,$command.ExitCode,$command.TimedOut))
        if (-not [string]::IsNullOrWhiteSpace([string]$command.StdOut)) { [void]$lines.Add(('  输出：' + $command.StdOut.Trim())) }
        if (-not [string]::IsNullOrWhiteSpace([string]$command.StdErr)) { [void]$lines.Add(('  错误输出：' + $command.StdErr.Trim())) }
        if ($command.Error) { [void]$lines.Add(('  说明：' + $command.Error)) }
    }
    [void]$lines.Add(''); [void]$lines.Add('[脱敏与限制]')
    $summaryText = @($safe.RedactionSummary.Categories | ForEach-Object { $_.Category + ' (' + $_.Count + ')' }) -join '，'
    if ([string]::IsNullOrWhiteSpace($summaryText)) { $summaryText = '无' }
    [void]$lines.Add(' 替换类别与数量：' + $summaryText)
    foreach ($limitation in @($safe.Limitations)) { [void]$lines.Add((' ' + $limitation)) }
    $text = $lines -join [Environment]::NewLine
    $text = Redact-XqText -InputObject $text -Context $script:XqRedactionContext
    try { Assert-XqTextIsSafe -Text $text } catch { [void](Write-XqPrivacyBlockedNotice -OutputDirectory $OutputDirectory); throw 'Sensitive-content scan failed; only a safe privacy notice was written.' }
    try { [void](New-Item -ItemType Directory -Path $OutputDirectory -Force -ErrorAction Stop) } catch { throw '无法创建诊断报告目录。' }
    for ($attempt=0; $attempt -lt 5; $attempt++) {
        $name = 'OpenClaw-Diagnostic-{0}-{1}.txt' -f (Get-Date -Format 'yyyyMMdd-HHmmss'),([Guid]::NewGuid().ToString('N').Substring(0,6))
        $path = Join-Path $OutputDirectory $name
        try {
            $stream = New-Object System.IO.FileStream($path,[System.IO.FileMode]::CreateNew,[System.IO.FileAccess]::Write,[System.IO.FileShare]::None)
            try { $writer = New-Object System.IO.StreamWriter($stream,(New-Object System.Text.UTF8Encoding($false))); $writer.Write($text); $writer.Flush() } finally { if ($writer) { $writer.Dispose() } else { $stream.Dispose() } }
            return $path
        } catch [System.IO.IOException] { continue }
    }
    throw '无法以不覆盖旧文件的方式创建新的诊断报告。'
}

function Invoke-XqDiagnosticMain {
    param([string]$OutputDirectory,[int]$TimeoutSeconds=20,[string]$PolicyPath)
    try { $policy = Initialize-XqDiagnosticPolicy -Path $PolicyPath } catch { Write-Error $_.Exception.Message; return 1 }
    if ($TimeoutSeconds -eq 20 -and $policy.defaultCommandTimeoutSeconds) { $TimeoutSeconds = [int]$policy.defaultCommandTimeoutSeconds }
    $script:XqRedactionContext = New-XqRedactionContext
    $started = Get-Date
    $findings = New-Object System.Collections.Generic.List[object]
    $commands = New-Object System.Collections.Generic.List[object]
    $hostProbe = Invoke-XqHostProbe; foreach ($f in $hostProbe.Findings) { [void]$findings.Add($f) }
    $hub = Invoke-XqHubProbe; foreach ($f in $hub.Findings) { [void]$findings.Add($f) }
    $managed = Invoke-XqManagedGatewayProbe $TimeoutSeconds; foreach ($c in $managed.Commands) { [void]$commands.Add($c) }; foreach ($f in $managed.Findings) { [void]$findings.Add($f) }
    $native = Invoke-XqNativeCliProbe $TimeoutSeconds; foreach ($c in $native.Commands) { [void]$commands.Add($c) }; foreach ($f in $native.Findings) { [void]$findings.Add($f) }
    $runtime = Invoke-XqOptionalRuntimeProbe $TimeoutSeconds; foreach ($c in $runtime.Commands) { [void]$commands.Add($c) }; foreach ($f in $runtime.Findings) { [void]$findings.Add($f) }
    $report = [pscustomobject][ordered]@{
        Metadata=[pscustomobject]@{ ReportVersion=$script:XqDiagnosticVersion; Product='XQ Windows 配置与排错资料包'; ProductVersion='0.1.0'; GeneratedAtUtc=$started.ToUniversalTime().ToString('o'); ReadOnly=$true }
        WindowsHost=$hostProbe.Data; HubInstallation=$hub.Data; WslGateway=$managed.Data; NativeCli=$native.Data
        HealthFindings=@($findings.ToArray()); CommandResults=@($commands.ToArray())
        RedactionSummary=$null
        Limitations=@('报告只反映当前有限探测。','XQ 不代表 OpenClaw 官方，也不保证模型提供商、网络或未来版本行为。')
    }
    try { $path = Write-XqDiagnosticReport -Report $report -OutputDirectory $OutputDirectory; Write-Output ('诊断报告已生成：' + $path); return 0 }
    catch { Write-Error $_.Exception.Message; return 1 }
}

if (-not $NoRun -and $MyInvocation.InvocationName -ne '.') {
    exit (Invoke-XqDiagnosticMain -OutputDirectory $OutputDirectory -TimeoutSeconds $TimeoutSeconds -PolicyPath $PolicyPath)
}
