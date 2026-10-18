[CmdletBinding()]
param(
  [string]$SourceRoot = 'C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat\outputs\personal-messenger-ai-v5',
  [string]$OutputDirectory = 'C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat\outputs\qq-vm\media'
)
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $SourceRoot -ErrorAction Stop).Path
$out = [IO.Path]::GetFullPath($OutputDirectory)
$zipPath = Join-Path $out 'personal-messenger-ai-v5-deploy.zip'
$manifestPath = Join-Path $out 'personal-messenger-ai-v5-deploy.manifest.json'
$tempRoot = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path.TrimEnd('\')
$stage = Join-Path $tempRoot ('pmai-package-' + [guid]::NewGuid().ToString('N'))
$tempStageZip = Join-Path $out ('pmai-package-' + [guid]::NewGuid().ToString('N') + '.tmp.zip')
$rootCursor = $source
while ($true) {
  $rootItem = Get-Item -LiteralPath $rootCursor -Force
  if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Rejected reparse point in source ancestor: $rootCursor" }
  $rootParent = Split-Path -Parent $rootCursor
  if ([string]::IsNullOrEmpty($rootParent) -or $rootParent -ieq $rootCursor) { break }
  $rootCursor = $rootParent
}
$files = @('pyproject.toml','scripts/run_vm_runtime.py','scripts/inspect_qq_guest_bindings.py','scripts/deployment/guest_bootstrap.ps1','scripts/deployment/guest_diagnostics.ps1','scripts/deployment/configure_deepseek_key.py','scripts/deployment/runtime.example.json','scripts/deployment/README.md','README.md')
$files += @(Get-ChildItem (Join-Path $source 'src') -Recurse -File | ForEach-Object { $_.FullName.Substring($source.Length + 1) } | Where-Object { $_ -notmatch '(^|[\\/])__pycache__([\\/]|$)' -and $_ -notmatch '\.pyc$' })
$files += @(Get-ChildItem (Join-Path $source 'rulepacks') -Filter '*.yaml' -File | ForEach-Object { $_.FullName.Substring($source.Length + 1) })
$files += 'rulepacks/personal-default-v1-guide.md'
function Resolve-Whitelisted([string]$relative) {
  if ([IO.Path]::IsPathRooted($relative) -or $relative -match '(^|[\\/])\.\.([\\/]|$)') { throw "Rejected non-relative path: $relative" }
  $full = [IO.Path]::GetFullPath((Join-Path $source $relative)); $prefix = $source.TrimEnd('\') + '\'
  if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Rejected path outside source root: $relative" }
  if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw "Missing whitelist file: $relative" }
  $cursor = $full
  while ($true) {
    $item = Get-Item -LiteralPath $cursor -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Rejected reparse point in source path: $relative" }
    if ($cursor -ieq $source) { break }
    $parent = Split-Path -Parent $cursor
    if ([string]::IsNullOrEmpty($parent) -or $parent.Length -lt $source.Length) { throw "Source path escaped root: $relative" }
    $cursor = $parent
  }
  if ($full -match '\\(state|credential-state|\.venv|\.git|\.pytest_cache|\.hypothesis|__pycache__|bin|obj|dist|build|fixtures|tests|third_party)(\\|$)' -or $full -match '\.(sqlite3?|db|key|pem|env|pyc)$') { throw "Rejected sensitive/generated path: $relative" }
  return $full
}
New-Item -ItemType Directory -Force -Path $out,$stage | Out-Null
try {
  foreach ($relative in ($files | Sort-Object -Unique)) { $full=Resolve-Whitelisted $relative; $destination=Join-Path $stage $relative; New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null; Copy-Item -LiteralPath $full -Destination $destination }
  if (Test-Path -LiteralPath $tempStageZip) { Remove-Item -LiteralPath $tempStageZip -Force }
  Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $tempStageZip -CompressionLevel Optimal
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $archive = [IO.Compression.ZipFile]::OpenRead($tempStageZip)
  try {
    foreach ($entry in $archive.Entries) {
      if ([IO.Path]::IsPathRooted($entry.FullName) -or $entry.FullName -match '(^|[\\/])\.\.([\\/]|$)') { throw "Rejected unsafe ZIP entry: $($entry.FullName)" }
    }
  } finally { $archive.Dispose() }
  Move-Item -LiteralPath $tempStageZip -Destination $zipPath -Force
  $gitHead=(& git -C $source rev-parse HEAD 2>$null).Trim(); $dirty=[bool]((& git -C $source status --porcelain 2>$null) -join "`n")
  $entries=@(Get-ChildItem $stage -Recurse -File | ForEach-Object { [ordered]@{path=$_.FullName.Substring($stage.Length+1).Replace('\','/');length=$_.Length;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()} })
  [ordered]@{schema='pmai-v5-deployment-package-1';generated_at_utc=[DateTime]::UtcNow.ToString('o');source_root=$source;source_git_head=$gitHead;source_worktree_dirty=$dirty;archive='personal-messenger-ai-v5-deploy.zip';entries=$entries;archive_sha256=(Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant();archive_length=(Get-Item $zipPath).Length} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
  Write-Output "Created $zipPath"; Write-Output "Created $manifestPath"
} finally {
  if (Test-Path -LiteralPath $stage) {
    $resolvedStage = (Resolve-Path -LiteralPath $stage).Path
    $stageLeaf = Split-Path -Leaf $resolvedStage
    if ($resolvedStage.StartsWith($tempRoot + '\', [StringComparison]::OrdinalIgnoreCase) -and $stageLeaf -match '^pmai-package-[0-9a-f]{32}$' -and ((Get-Item -LiteralPath $resolvedStage -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
      Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    } else { throw "Refusing to remove unexpected staging path: $resolvedStage" }
  }
  if (Test-Path -LiteralPath $tempStageZip) { Remove-Item -LiteralPath $tempStageZip -Force }
}
