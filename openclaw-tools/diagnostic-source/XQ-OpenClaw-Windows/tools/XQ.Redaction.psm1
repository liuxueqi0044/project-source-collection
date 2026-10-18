Set-StrictMode -Version Latest

$script:ReplacementToken = '[REDACTED]'
$script:SensitiveFieldNamePattern = '(key|token|secret|password|authorization|cookie|credential)'

function New-XqRedactionContext {
    [CmdletBinding()]
    param()

    [pscustomobject]@{
        Categories = [ordered]@{
            'structured-field' = 0
            'authorization-header' = 0
            'bearer-or-basic' = 0
            'sensitive-assignment' = 0
            'sensitive-query' = 0
            'credential-url' = 0
            'jwt' = 0
            'provider-token' = 0
            'windows-user-path' = 0
        }
    }
}

function Add-XqRedactionCount {
    param([Parameter(Mandatory)][object]$Context,[Parameter(Mandatory)][string]$Category,[int]$Count = 1)
    if ($Count -le 0) { return }
    if (-not $Context.Categories.Contains($Category)) { $Context.Categories[$Category] = 0 }
    $Context.Categories[$Category] = [int]$Context.Categories[$Category] + $Count
}

function Get-XqRedactionSummary {
    [CmdletBinding()]
    param([Parameter(Mandatory)][object]$Context)

    $categories = New-Object System.Collections.Generic.List[object]
    $total = 0
    foreach ($name in $Context.Categories.Keys) {
        $count = [int]$Context.Categories[$name]
        if ($count -gt 0) {
            [void]$categories.Add([pscustomobject]@{ Category = [string]$name; Count = $count })
            $total += $count
        }
    }
    return [pscustomobject]@{ Categories = @($categories.ToArray()); TotalReplacements = $total }
}

function Invoke-XqTextReplacement {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Text,
        [Parameter(Mandatory)][string]$Pattern,
        [Parameter(Mandatory)][string]$Replacement,
        [Parameter(Mandatory)][string]$Category,
        [Parameter(Mandatory)][object]$Context
    )

    $foundMatches = [regex]::Matches($Text, $Pattern)
    if ($foundMatches.Count -gt 0) {
        Add-XqRedactionCount -Context $Context -Category $Category -Count $foundMatches.Count
        return [regex]::Replace($Text, $Pattern, $Replacement)
    }
    return $Text
}

function Test-XqSensitiveFieldName {
    param([AllowNull()][object]$Name)
    if ($null -eq $Name) { return $false }
    $spaced = [string]$Name
    $spaced = $spaced -replace '(?-i)([a-z0-9])([A-Z])', '$1 $2'
    $spaced = $spaced -replace '(?-i)([A-Z])([A-Z][a-z])', '$1 $2'
    $spaced = $spaced -replace '[^A-Za-z0-9]+', ' '
    $segments = @($spaced.Split([char[]]' ', [System.StringSplitOptions]::RemoveEmptyEntries))
    foreach ($segment in $segments) {
        if ($segment -match ('(?i)^' + $script:SensitiveFieldNamePattern + '$')) { return $true }
    }
    # Lowercase compact names such as apikey are accepted only when the whole name
    # is a conventional sensitive-name compound; arbitrary substrings (monkey,
    # keyboard, hockey) are deliberately not sensitive fields.
    $compact = (($segments -join '')).ToLowerInvariant()
    return $compact -match '^(?:api|access|refresh|client|private|public|session|service|auth)?(?:key|token|secret|password|authorization|cookie|credential)$'
}

function Test-XqNonSecretLiteral {
    param([AllowNull()][string]$Value)
    if ($null -eq $Value) { return $false }
    return $Value.Trim() -match '^(?:-?[0-9]+(?:\.[0-9]+)?|\$(?:true|false|null))$'
}

function Redact-XqEncodedQueryValues {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Text,[Parameter(Mandatory)][object]$Context)

    $pattern = '(?i)([?&])([^=&\s]+)(=|%3d)([^&#\s]*)'
    [System.Text.RegularExpressions.MatchCollection]$allMatches = [regex]::Matches($Text, $pattern)
    for ($index = $allMatches.Count - 1; $index -ge 0; $index--) {
        $match = $allMatches[$index]
        try { $decodedName = [uri]::UnescapeDataString($match.Groups[2].Value.Replace('+', ' ')) } catch { $decodedName = $match.Groups[2].Value }
        if ((Test-XqSensitiveFieldName $decodedName) -and $match.Groups[4].Value -notmatch '(?i)^(?:%5b|\[)?REDACTED(?:%5d|\])?$') {
            $replacement = $match.Groups[1].Value + $match.Groups[2].Value + $match.Groups[3].Value + $script:ReplacementToken
            $Text = $Text.Substring(0, $match.Index) + $replacement + $Text.Substring($match.Index + $match.Length)
            Add-XqRedactionCount -Context $Context -Category 'sensitive-query'
        }
    }
    return $Text
}

function Get-XqEncodedQueryFindings {
    param([AllowNull()][string]$Text)

    if ($null -eq $Text) { return @() }
    $findings = New-Object System.Collections.Generic.List[object]
    foreach ($match in [regex]::Matches($Text, '(?i)([?&])([^=&\s]+)(=|%3d)([^&#\s]*)')) {
        try { $decodedName = [uri]::UnescapeDataString($match.Groups[2].Value.Replace('+', ' ')) } catch { $decodedName = $match.Groups[2].Value }
        if ((Test-XqSensitiveFieldName $decodedName) -and $match.Groups[4].Value -notmatch '(?i)^(?:%5b|\[)?REDACTED(?:%5d|\])?$') {
            [void]$findings.Add([pscustomobject]@{ Category = 'sensitive-query' })
        }
    }
    return @($findings.ToArray())
}

function Redact-XqSensitiveAssignments {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Text,[Parameter(Mandatory)][object]$Context)

    $pattern = '(?im)(["'']?)([A-Za-z][A-Za-z0-9_-]*)\1(\s*[:=]\s*)((?:"(?:\\.|[^"])*")|(?:''(?:\\.|[^''])*'')|(?:[^\s,;}\r\n]+))'
    [System.Text.RegularExpressions.MatchCollection]$allMatches = [regex]::Matches($Text, $pattern)
    for ($index = $allMatches.Count - 1; $index -ge 0; $index--) {
        $match = $allMatches[$index]
        $value = $match.Groups[4].Value
        if ((Test-XqSensitiveFieldName $match.Groups[2].Value) -and -not (Test-XqNonSecretLiteral $value) -and $value -notmatch '^(?:["'']?\[REDACTED\]["'']?)$') {
            $valueQuote = ''
            if ($value.StartsWith('"')) { $valueQuote = '"' } elseif ($value.StartsWith("'")) { $valueQuote = "'" }
            $replacement = $match.Groups[1].Value + $match.Groups[2].Value + $match.Groups[1].Value + $match.Groups[3].Value + $valueQuote + $script:ReplacementToken + $valueQuote
            $Text = $Text.Substring(0, $match.Index) + $replacement + $Text.Substring($match.Index + $match.Length)
            Add-XqRedactionCount -Context $Context -Category 'sensitive-assignment'
        }
    }
    return $Text
}

function Get-XqSensitiveAssignmentFindings {
    param([AllowNull()][string]$Text)

    if ($null -eq $Text) { return @() }
    $findings = New-Object System.Collections.Generic.List[object]
    $pattern = '(?im)(["'']?)([A-Za-z][A-Za-z0-9_-]*)\1(\s*[:=]\s*)((?:"(?:\\.|[^"])*")|(?:''(?:\\.|[^''])*'')|(?:[^\s,;}\r\n]+))'
    foreach ($match in [regex]::Matches($Text, $pattern)) {
        if ((Test-XqSensitiveFieldName $match.Groups[2].Value) -and -not (Test-XqNonSecretLiteral $match.Groups[4].Value) -and $match.Groups[4].Value -notmatch '^(?:["'']?\[REDACTED\]["'']?)$') {
            [void]$findings.Add([pscustomobject]@{ Category = 'sensitive-assignment' })
        }
    }
    return @($findings.ToArray())
}

function Redact-XqText {
    <#
    The textual fallback deliberately accepts only in-memory text. It never reads
    configuration, environment variables, sessions, browser data, or log files.
    #>
    [CmdletBinding()]
    param([AllowNull()][object]$InputObject,[object]$Context)

    if ($null -eq $InputObject) { return $null }
    if ($null -eq $Context) { $Context = New-XqRedactionContext }

    $text = [string]$InputObject
    $text = $text.Replace(([string][char]0), '').Replace([string][char]0x200B, '').Replace([string][char]0x200C, '').Replace([string][char]0x200D, '')
    $text = $text.Replace([string][char]0x2010, '-').Replace([string][char]0x2011, '-').Replace([string][char]0x2012, '-').Replace([string][char]0x2013, '-').Replace([string][char]0x2014, '-')

    $rules = @(
        @{ Pattern='(?i)([A-Z]:\\Users\\)(?!\[USER\](?:\\|$))[^\\/\r\n]+'; Replacement='$1[USER]'; Category='windows-user-path' },
        @{ Pattern='(?im)(\bauthorization\s*[:=]\s*(?:["'']?\s*)?(?:bearer|basic)\s+)(?!\[REDACTED\])[^\s,;}\r\n]+'; Replacement='$1[REDACTED]'; Category='authorization-header' },
        @{ Pattern='(?im)(\b(?:bearer|basic)\s+)(?!\[REDACTED\])(?:[A-Za-z0-9+/_=\-.]{4,})'; Replacement='$1[REDACTED]'; Category='bearer-or-basic' },
        @{ Pattern='(?i)(https?://[^/\s:@]+:)(?!\[REDACTED\]@)[^@\s/]+(@)'; Replacement='$1[REDACTED]$2'; Category='credential-url' },
        @{ Pattern='\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b'; Replacement='[REDACTED]'; Category='jwt' },
        @{ Pattern='(?i)\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|sk-ant-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_-]{12,}|github_pat_[A-Za-z0-9_-]{12,}|xox[baprs]-[A-Za-z0-9_-]{12,})\b'; Replacement='[REDACTED]'; Category='provider-token' }
    )
    foreach ($rule in $rules) {
        $text = Invoke-XqTextReplacement -Text $text -Pattern $rule.Pattern -Replacement $rule.Replacement -Category $rule.Category -Context $Context
    }
    $text = Redact-XqSensitiveAssignments -Text $text -Context $Context
    $text = Redact-XqEncodedQueryValues -Text $text -Context $Context
    return $text
}

function ConvertTo-XqRedactedObject {
    [CmdletBinding()]
    param([AllowNull()][object]$InputObject,[object]$Context)

    if ($null -eq $InputObject) { return $null }
    if ($null -eq $Context) { $Context = New-XqRedactionContext }
    if ($InputObject -is [string] -or $InputObject -is [char]) { return Redact-XqText -InputObject $InputObject -Context $Context }
    if ($InputObject -is [System.ValueType] -or $InputObject -is [System.DateTimeOffset]) { return $InputObject }

    if ($InputObject -is [System.Collections.IDictionary]) {
        $result = [ordered]@{}
        foreach ($key in $InputObject.Keys) {
            $name = [string]$key
            if (Test-XqSensitiveFieldName $name) {
                Add-XqRedactionCount -Context $Context -Category 'structured-field'
                $result[$name] = $script:ReplacementToken
            } else {
                $result[$name] = ConvertTo-XqRedactedObject -InputObject $InputObject[$key] -Context $Context
            }
        }
        return [pscustomobject]$result
    }

    if ($InputObject -is [pscustomobject]) {
        $result = [ordered]@{}
        foreach ($property in $InputObject.PSObject.Properties) {
            if (Test-XqSensitiveFieldName $property.Name) {
                Add-XqRedactionCount -Context $Context -Category 'structured-field'
                $result[$property.Name] = $script:ReplacementToken
            } else {
                $result[$property.Name] = ConvertTo-XqRedactedObject -InputObject $property.Value -Context $Context
            }
        }
        return [pscustomobject]$result
    }

    if ($InputObject -is [byte[]]) { return ,$InputObject }
    if ($InputObject -is [System.Collections.IEnumerable]) {
        $items = foreach ($item in $InputObject) { ConvertTo-XqRedactedObject -InputObject $item -Context $Context }
        return @($items)
    }
    return $InputObject
}

function Redact-XqCommandOutput {
    [CmdletBinding()]
    param([AllowNull()][string]$Text,[string]$Command,[object]$Context)

    if ($null -eq $Text) { return $null }
    if ($null -eq $Context) { $Context = New-XqRedactionContext }
    if ($Command -match '(?i)--json') {
        try {
            $parsed = $Text | ConvertFrom-Json -ErrorAction Stop
            return ((ConvertTo-XqRedactedObject -InputObject $parsed -Context $Context) | ConvertTo-Json -Depth 16 -Compress)
        } catch { Write-Verbose 'Command output was not valid JSON; applying the text redaction path.' }
    }
    return Redact-XqText -InputObject $Text -Context $Context
}

function Get-XqSensitiveFindings {
    [CmdletBinding()]
    param([AllowNull()][string]$Text)

    if ($null -eq $Text) { return @() }
    $candidate = [string]$Text
    $rules = @(
        @{ Pattern='(?im)\bauthorization\s*[:=]\s*(?:["'']?\s*)?(?:bearer|basic)\s+(?!\[REDACTED\])[^\s,;}\r\n]+'; Category='authorization-header' },
        @{ Pattern='(?im)\b(?:bearer|basic)\s+(?!\[REDACTED\])[A-Za-z0-9+/_=\-.]{4,}'; Category='bearer-or-basic' },
        @{ Pattern='(?i)https?://[^/\s:@]+:(?!\[REDACTED\])[^@\s/]+@'; Category='credential-url' },
        @{ Pattern='\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b'; Category='jwt' },
        @{ Pattern='(?i)\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|sk-ant-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_-]{12,}|github_pat_[A-Za-z0-9_-]{12,}|xox[baprs]-[A-Za-z0-9_-]{12,})\b'; Category='provider-token' },
        @{ Pattern='(?i)\b[A-Z]:\\Users\\(?!\[USER\](?:\\|$))[^\\/\r\n]+'; Category='windows-user-path' }
    )
    $findings = New-Object System.Collections.Generic.List[object]
    foreach ($rule in $rules) {
        foreach ($match in [regex]::Matches($candidate, $rule.Pattern)) {
            [void]$findings.Add([pscustomobject]@{ Category = $rule.Category })
        }
    }
    foreach ($finding in @(Get-XqSensitiveAssignmentFindings -Text $candidate)) { [void]$findings.Add($finding) }
    foreach ($finding in @(Get-XqEncodedQueryFindings -Text $candidate)) { [void]$findings.Add($finding) }
    return @($findings.ToArray())
}

function Assert-XqTextIsSafe {
    [CmdletBinding()]
    param([AllowNull()][string]$Text)

    if (@(Get-XqSensitiveFindings -Text $Text).Count -gt 0) {
        throw 'Sensitive-content scan failed; the report was not written.'
    }
}

Export-ModuleMember -Function @('New-XqRedactionContext','Get-XqRedactionSummary','Redact-XqText','ConvertTo-XqRedactedObject','Redact-XqCommandOutput','Get-XqSensitiveFindings','Assert-XqTextIsSafe')
