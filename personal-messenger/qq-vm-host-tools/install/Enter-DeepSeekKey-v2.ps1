[CmdletBinding()]
param(
    [switch]$ConfirmedGuestGetpassFocus,
    [switch]$ExpectedUsLayout
)

# Review-only until the guest getpass prompt is visibly focused and no other
# agent is sending VM input. The key is never accepted through arguments,
# files, logs, the clipboard API, or a guest shared medium.
$ErrorActionPreference = 'Stop'
$VmId = '7592dfa4-c60c-4818-921e-ced5c626254b'
$VmName = 'PersonalMessengerQQ'
$VBoxManage = 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe'
$IntakeDirectory = 'C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat\outputs\qq-vm\install\diagnostics\key-intake'

function Get-UsSet1Map {
    $map = @{}
    $pairs = @(
        @('a',0x1e), @('b',0x30), @('c',0x2e), @('d',0x20), @('e',0x12), @('f',0x21), @('g',0x22), @('h',0x23), @('i',0x17), @('j',0x24), @('k',0x25), @('l',0x26), @('m',0x32), @('n',0x31), @('o',0x18), @('p',0x19), @('q',0x10), @('r',0x13), @('s',0x1f), @('t',0x14), @('u',0x16), @('v',0x2f), @('w',0x11), @('x',0x2d), @('y',0x15), @('z',0x2c),
        @('0',0x0b), @('1',0x02), @('2',0x03), @('3',0x04), @('4',0x05), @('5',0x06), @('6',0x07), @('7',0x08), @('8',0x09), @('9',0x0a),
        @(' ',0x39), @('-',0x0c), @('=',0x0d), @('[',0x1a), @(']',0x1b), @(';',0x27), @("'",0x28), @('`',0x29), @('\',0x2b), @(',',0x33), @('.',0x34), @('/',0x35)
    )
    foreach ($pair in $pairs) { $map[$pair[0]] = [int]$pair[1] }
    return $map
}

function Get-ShiftedUsSet1Map {
    @{
        '!'=0x02; '@'=0x03; '#'=0x04; '$'=0x05; '%'=0x06; '^'=0x07; '&'=0x08; '*'=0x09; '('=0x0a; ')'=0x0b
        '_'=0x0c; '+'=0x0d; '{'=0x1a; '}'=0x1b; ':'=0x27; '"'=0x28; '~'=0x29; '|'=0x2b; '<'=0x33; '>'=0x34; '?'=0x35
    }
}

function Send-OneScancode($Keyboard, [int]$Code) {
    $Keyboard.PutScancode([int]$Code)
    Start-Sleep -Milliseconds 20
}

function Write-KeyIntakeState(
    [string]$Directory,
    [guid]$Nonce,
    [ValidateSet('form_open','validating','awaiting_focus','sending','typed','failed','cancelled')][string]$State,
    [string]$ErrorCode = 'none',
    [string]$ExceptionType = 'none'
) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $path = Join-Path $Directory ("ready-{0}.json" -f $Nonce.Guid)
    [ordered]@{
        state = $State
        nonce = $Nonce.Guid
        pid = $PID
        errorCode = $ErrorCode
        exceptionType = $ExceptionType
    } | ConvertTo-Json -Compress | Set-Content -LiteralPath $path -Encoding utf8
    return $path
}

function Test-KeyIntakeGo([string]$Path, [guid]$Nonce) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try { $signal = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -ErrorAction Stop }
    catch { return $false }
    return ($signal.state -ceq 'go' -and $signal.nonce -ceq $Nonce.Guid)
}

if (-not $ConfirmedGuestGetpassFocus) { throw 'Refusing without a confirmed visible, focused guest getpass prompt.' }
if (-not $ExpectedUsLayout) { throw 'Refusing without a confirmed US English guest keyboard layout.' }
if (-not (Test-Path -LiteralPath $VBoxManage -PathType Leaf)) { throw 'VBoxManage is missing.' }
$machineReadable = & $VBoxManage showvminfo $VmId --machinereadable 2>$null
if ($LASTEXITCODE -ne 0 -or -not ($machineReadable -match '^VMState="running"$') -or -not ($machineReadable -match ('^name="' + [regex]::Escape($VmName) + '"$'))) {
    throw 'VM identity or running-state guard failed.'
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$textBox = New-Object System.Windows.Forms.TextBox
$secure = $null
$bstr = [IntPtr]::Zero
$characters = $null
$nonce = [guid]::NewGuid()
$readyPath = $null
$intakeState = 'form_open'
$errorCode = 'none'
$exceptionType = 'none'
$formState = @{ accepted = $false; entered = $null }
$virtualBox = $null
$hostSession = $null
$keyboard = $null
try {
    # Establish a public lifecycle record before accepting any secret input.
    # If this fails, execution stops before the form is displayed.
    $readyPath = Write-KeyIntakeState $IntakeDirectory $nonce $intakeState
    $form.Text = 'DeepSeek API Key'
    $form.Size = New-Object System.Drawing.Size(420,195)
    $form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
    $form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.ShowInTaskbar = $false
    $label = New-Object System.Windows.Forms.Label
    $label.Text = '粘贴DeepSeek API Key，仅保存在虚拟机内'
    $label.AutoSize = $true
    $label.Location = New-Object System.Drawing.Point(18,18)
    $textBox.UseSystemPasswordChar = $true
    $textBox.Location = New-Object System.Drawing.Point(18,48)
    $textBox.Size = New-Object System.Drawing.Size(365,24)
    $ok = New-Object System.Windows.Forms.Button
    $ok.Text = '确认'
    $ok.Location = New-Object System.Drawing.Point(214,118)
    $cancel = New-Object System.Windows.Forms.Button
    $cancel.Text = '取消'
    $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $cancel.Location = New-Object System.Drawing.Point(308,118)
    $validationLabel = New-Object System.Windows.Forms.Label
    $validationLabel.AutoSize = $true
    $validationLabel.ForeColor = [System.Drawing.Color]::DarkRed
    $validationLabel.Location = New-Object System.Drawing.Point(18,80)
    $form.Controls.AddRange(@($label,$textBox,$validationLabel,$ok,$cancel))
    $form.CancelButton = $cancel
    $form.TopMost = $true
    $form.Add_Shown({ $textBox.Focus() })
    $ok.Add_Click({
        $script:intakeState = 'validating'
        Write-KeyIntakeState $script:IntakeDirectory $script:nonce $script:intakeState | Out-Null
        $candidate = $textBox.Text.Trim()
        $validationError = 'none'
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            $validationError = 'empty_input'
            $validationLabel.Text = '请输入非空的 API Key。'
        }
        elseif ($candidate.Length -gt 512) {
            $validationError = 'input_too_long'
            $validationLabel.Text = 'API Key 过长，请检查后重试。'
        }
        else {
            $plainForValidation = Get-UsSet1Map
            $shiftedForValidation = Get-ShiftedUsSet1Map
            foreach ($candidateCharacter in $candidate.ToCharArray()) {
                $candidateText = $candidateCharacter.ToString()
                $candidateLower = $candidateText.ToLowerInvariant()
                if (($candidateText -cmatch '^[A-Z]$' -and -not $plainForValidation.ContainsKey($candidateLower)) -or
                    ($candidateText -cnotmatch '^[A-Z]$' -and -not $plainForValidation.ContainsKey($candidateText) -and -not $shiftedForValidation.ContainsKey($candidateText))) {
                    $validationError = 'unsupported_character'
                    $validationLabel.Text = 'API Key 含不支持的字符，请检查后重试。'
                    break
                }
            }
        }
        if ($validationError -ne 'none') {
            $script:intakeState = 'form_open'
            Write-KeyIntakeState $script:IntakeDirectory $script:nonce $script:intakeState $validationError | Out-Null
            $textBox.Focus()
            return
        }
        $textBox.Text = $candidate
        $script:formState.entered = $candidate
        $script:formState.accepted = $true
        $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $form.Close()
    })
    [void]$form.ShowDialog()
    if (-not $formState.accepted) {
        $intakeState = 'cancelled'
        Write-KeyIntakeState $IntakeDirectory $nonce $intakeState | Out-Null
        return
    }
    $entered = $formState.entered
    $formState.entered = $null
    $secure = ConvertTo-SecureString -String $entered -AsPlainText -Force
    $entered = $null
    $textBox.Clear()
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $length = $secure.Length
    $characters = New-Object char[] $length
    for ($index = 0; $index -lt $length; $index++) { $characters[$index] = [char][Runtime.InteropServices.Marshal]::ReadInt16($bstr, $index * 2) }

    $plain = Get-UsSet1Map
    $shifted = Get-ShiftedUsSet1Map
    foreach ($character in $characters) {
        $text = $character.ToString()
        $lower = $text.ToLowerInvariant()
        if (($text -cmatch '^[A-Z]$' -and -not $plain.ContainsKey($lower)) -or ($text -cnotmatch '^[A-Z]$' -and -not $plain.ContainsKey($text) -and -not $shifted.ContainsKey($text))) {
            throw 'Key contains an unsupported character.'
        }
    }

    $intakeState = 'awaiting_focus'
    $readyPath = Write-KeyIntakeState $IntakeDirectory $nonce $intakeState
    $goPath = Join-Path $IntakeDirectory ("go-{0}.json" -f $nonce.Guid)
    $deadline = [datetime]::UtcNow.AddMinutes(5)
    $authorized = $false
    while ([datetime]::UtcNow -lt $deadline) {
        if (Test-KeyIntakeGo $goPath $nonce) { $authorized = $true; break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $authorized) { throw 'Focus authorization did not arrive.' }

    # The matching go signal represents a fresh guest screenshot review and a VM-input pause.
    $intakeState = 'sending'
    Write-KeyIntakeState $IntakeDirectory $nonce $intakeState | Out-Null
    $machineReadable = & $VBoxManage showvminfo $VmId --machinereadable 2>$null
    if ($LASTEXITCODE -ne 0 -or -not ($machineReadable -match '^VMState="running"$') -or -not ($machineReadable -match ('^name="' + [regex]::Escape($VmName) + '"$'))) {
        throw 'VM identity or running-state guard failed.'
    }
    $virtualBox = New-Object -ComObject 'VirtualBox.VirtualBox'
    $machine = $virtualBox.FindMachine($VmId)
    if ($machine.Id -ne $VmId -or $machine.Name -ne $VmName -or [int]$machine.State -ne 6) { throw 'VM identity or running-state guard failed.' }
    $hostSession = New-Object -ComObject 'VirtualBox.Session'
    $machine.LockMachine($hostSession, 1)
    $keyboard = $hostSession.Console.Keyboard
    $keyboard.ReleaseKeys()
    if (@($keyboard.KeyboardLEDs | ForEach-Object { [int]$_ }) -contains 2) { Send-OneScancode $keyboard 0x3a; Send-OneScancode $keyboard 0xba }
    foreach ($character in $characters) {
        $text = $character.ToString(); $lower = $text.ToLowerInvariant(); $upper = $text -cmatch '^[A-Z]$'; $shift = $upper -or $shifted.ContainsKey($text)
        $code = if ($upper) { $plain[$lower] } elseif ($shift) { $shifted[$text] } else { $plain[$text] }
        if ($shift) { Send-OneScancode $keyboard 0x2a }
        Send-OneScancode $keyboard $code
        Send-OneScancode $keyboard ($code -bor 0x80)
        if ($shift) { Send-OneScancode $keyboard 0xaa }
    }
    Send-OneScancode $keyboard 0x1c
    Send-OneScancode $keyboard 0x9c
    $intakeState = 'typed'
    Write-KeyIntakeState $IntakeDirectory $nonce $intakeState | Out-Null
    Write-Output 'Key typing completed; verify guest storage separately.'
}
catch {
    $intakeState = 'failed'
    $exceptionType = $_.Exception.GetType().FullName
    $errorCode = switch -Regex ($_.Exception.Message) {
        '^Focus authorization did not arrive\.$' { 'focus_timeout'; break }
        '^VM identity or running-state guard failed\.$' { 'vm_guard_failed'; break }
        default { 'internal_failure' }
    }
}
finally {
    if ($keyboard) { try { $keyboard.ReleaseKeys() } catch {} }
    if ($hostSession) { try { $hostSession.UnlockMachine() } catch {} }
    foreach ($object in @($hostSession,$virtualBox)) { if ($object -and [Runtime.InteropServices.Marshal]::IsComObject($object)) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($object) } }
    if ($characters) { [Array]::Clear($characters,0,$characters.Length) }
    $entered = $null
    $formState.entered = $null
    if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if ($secure) { $secure.Dispose() }
    if ($textBox) { $textBox.Clear(); $textBox.Dispose() }
    if ($form) { $form.Dispose() }
    if ($readyPath -and $intakeState -in @('typed','failed','cancelled')) {
        try { Write-KeyIntakeState $IntakeDirectory $nonce $intakeState $errorCode $exceptionType | Out-Null } catch {}
    }
}

