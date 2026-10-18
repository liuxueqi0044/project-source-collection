"""Bounded Guest Control operations for the OpenClaw native Windows pilot.

No plaintext credential is printed or persisted.  Every operation is bound to
the dedicated clone and the existing pmai_setup DPAPI credential.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import time
from pathlib import Path


SOURCE_HELPER = Path(
    r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat\outputs\qq-vm"
    r"\install\control_qq_runtime_host.py"
)
ADMIN_CREDENTIAL = Path(
    r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat\outputs\qq-vm"
    r"\install\credential-state\pmai_setup.dpapi"
)
CLONE_ID = "5d948da3-2078-45bf-92b4-151de5412753"
CLONE_NAME = "XQ-OpenClaw-Test-01"
HOST_ROOT = Path(
    r"C:\Users\xiayu\Documents\Codex\2026-09-15\agent-a-c-users-xiayu-documents"
    r"\work\agent-kits-tech\native-pilot"
)
GUEST_ROOT = r"C:\Users\pmai_setup\OpenClawNativePilot"
GUEST_PROCESS_STARTED = 100
GUEST_PROCESS_TERMINATED = 500
NODE_VERSION = "26.8.2"
OPENCLAW_VERSION = "2026.9.4"
DEEPSEEK_PLUGIN_VERSION = "2026.9.4"
DEEPSEEK_PLUGIN_SPEC = f"@openclaw/deepseek-provider@{DEEPSEEK_PLUGIN_VERSION}"
DEEPSEEK_DEFAULT_MODEL = "deepseek/deepseek-v4-pro"
NODE_ZIP_SHA256 = "CF02F5D0C06C794B84F277177D5CF3743D0924CA49F6641CD435DD7CB6EE9085"
OPENCLAW_TGZ_SHA256 = "4F1F656770461D4677DEA755B1899CBA12B912B06798C89A59E2F0C18688B761"
INSTALLER_SHA256 = "A7B586F471FB02774AE6DF47F1AC640ADDC58FF6C099DA209310D3E2156523CC"
PRODUCTION_DIAGNOSTIC_ROOT = (
    HOST_ROOT.parent
    / "openclaw-fix"
    / "vm-diagnostic-package"
    / "XQ-OpenClaw-Windows"
)
PRODUCTION_DIAGNOSTIC_HASHES = {
    "OpenClaw-XQ-Diagnose.ps1": "3D3156C517026022CB712C33811EBAEA79689AD4F11D61BAC99200CE8BB0585C",
    "XQ.Redaction.psm1": "221B8E6E63759A2F37597EB037CE02A379113D52CFD68BCFA1D0F6C3310BED91",
    "diagnostic-command-policy.json": "DC325439CBAEFC7FA79C0C15E20E0AD5D70880E57294CBF315BB48CC0432C52D",
}


PREFLIGHT_PS = r"""
$ErrorActionPreference = 'Stop'
$result = [ordered]@{
  schema = 'xq-openclaw-native-preflight-v1'
  computer = $env:COMPUTERNAME
  user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
  powershell = $PSVersionTable.PSVersion.ToString()
  commands = [ordered]@{}
  default_state_exists = (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.openclaw'))
  pilot_state_exists = (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.openclaw-xq-native-pilot'))
  openclaw_tasks = @()
  openclaw_startup_files = @()
  openclaw_process_count = 0
  port_18789 = $null
  free_bytes = $null
}

foreach ($name in @('node','npm','git','winget','choco','scoop','openclaw')) {
  $cmd = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
  $entry = [ordered]@{ found = ($null -ne $cmd); version = $null; source = $null }
  if ($cmd) {
    $source = [string]$cmd.Source
    if ($source.StartsWith($env:USERPROFILE, [StringComparison]::OrdinalIgnoreCase)) {
      $entry.source = 'user-profile'
    } elseif ($source.StartsWith($env:LOCALAPPDATA, [StringComparison]::OrdinalIgnoreCase)) {
      $entry.source = 'local-appdata'
    } elseif ($source.StartsWith($env:SystemRoot, [StringComparison]::OrdinalIgnoreCase)) {
      $entry.source = 'windows'
    } else {
      $entry.source = 'other'
    }
    try {
      if ($name -eq 'npm') { $entry.version = [string](& $cmd.Source --version 2>$null | Select-Object -First 1) }
      elseif ($name -eq 'git') { $entry.version = [string](& $cmd.Source --version 2>$null | Select-Object -First 1) }
      elseif ($name -eq 'winget') { $entry.version = [string](& $cmd.Source --version 2>$null | Select-Object -First 1) }
      elseif ($name -eq 'node') { $entry.version = [string](& $cmd.Source --version 2>$null | Select-Object -First 1) }
      elseif ($name -eq 'openclaw') { $entry.version = [string](& $cmd.Source --version 2>$null | Select-Object -First 1) }
    } catch { $entry.version = 'unavailable' }
  }
  $result.commands[$name] = $entry
}

try {
  $result.openclaw_tasks = @(Get-ScheduledTask | Where-Object TaskName -like 'OpenClaw Gateway*' | ForEach-Object {
    [ordered]@{ name = [string]$_.TaskName; state = [string]$_.State; enabled = [bool]$_.Settings.Enabled }
  })
} catch {}
try {
  $startupPaths = @([Environment]::GetFolderPath('Startup'), [Environment]::GetFolderPath('CommonStartup')) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
  $result.openclaw_startup_files = @($startupPaths | ForEach-Object { Get-ChildItem -LiteralPath $_ -File -ErrorAction SilentlyContinue } | Where-Object Name -like '*OpenClaw*' | ForEach-Object Name)
} catch {}
try {
  $result.openclaw_process_count = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '(?i)^openclaw|^node\.exe$' -and ([string]$_.CommandLine) -match '(?i)openclaw' }).Count
} catch {}
try {
  $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
  $result.port_18789 = [ordered]@{
    count = $listeners.Count
    loopback_only = ($listeners.Count -eq 0 -or @($listeners | Where-Object { $_.LocalAddress -notin @('127.0.0.1','::1') }).Count -eq 0)
  }
} catch { $result.port_18789 = [ordered]@{ count = $null; loopback_only = $null } }
try {
  $drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
  $result.free_bytes = [uint64]$drive.FreeSpace
} catch {}

$json = $result | ConvertTo-Json -Depth 8 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""


def load_control():
    spec = importlib.util.spec_from_file_location("pmai_control", SOURCE_HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load existing credential helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.VM_ID = CLONE_ID
    module.VM_NAME = CLONE_NAME
    module.GUEST_USER = "pmai_setup"
    module.CREDENTIAL = ADMIN_CREDENTIAL
    return module


def run_process(guest_session, executable: str, arguments: list[str], timeout_seconds: int = 120) -> tuple[int, bytes, bytes]:
    process = guest_session.ProcessCreate(
        executable,
        arguments,
        r"C:\Windows\System32",
        [],
        [48],
        max(30_000, timeout_seconds * 1000),
    )
    process.WaitForArray([1], 30_000)
    stdout: list[bytes] = []
    stderr: list[bytes] = []
    deadline = time.monotonic() + timeout_seconds
    while True:
        for handle, sink in ((1, stdout), (2, stderr)):
            try:
                chunk = bytes(process.Read(handle, 65536, 500))
            except Exception:
                chunk = b""
            if chunk:
                sink.append(chunk)
        if int(process.Status) >= GUEST_PROCESS_TERMINATED:
            break
        if time.monotonic() >= deadline:
            try:
                process.Terminate()
            except Exception:
                pass
            raise TimeoutError(f"guest process exceeded {timeout_seconds} seconds")
    return int(process.ExitCode), b"".join(stdout), b"".join(stderr)


def run_ps_json(guest_session, script: str, timeout_seconds: int = 120) -> dict[str, object]:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    code, stdout, stderr = run_process(
        guest_session,
        exe,
        [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        timeout_seconds,
    )
    if code != 0:
        detail = stderr.decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"guest PowerShell failed with exit code {code}: {detail}")
    raw = base64.b64decode(stdout.decode("ascii").strip(), validate=True).decode("utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("guest result must be a JSON object")
    return value


def copy_to_guest(guest_session, host_path: Path, guest_path: str, timeout_ms: int = 300_000) -> None:
    progress = guest_session.FileCopyToGuest(str(host_path), guest_path, [])
    progress.WaitForCompletion(timeout_ms)
    if not bool(progress.Completed):
        raise RuntimeError(f"guest copy timed out: {host_path.name}")
    if int(progress.ResultCode) != 0:
        raise RuntimeError(f"guest copy failed for {host_path.name}: {int(progress.ResultCode)}")


def copy_from_guest_file(guest_session, guest_path: str, host_path: Path, timeout_ms: int = 300_000) -> None:
    host_path.parent.mkdir(parents=True, exist_ok=True)
    if host_path.exists():
        host_path.unlink()
    progress = guest_session.FileCopyFromGuest(guest_path, str(host_path), [])
    progress.WaitForCompletion(timeout_ms)
    if not bool(progress.Completed):
        raise RuntimeError(f"guest copy timed out: {guest_path}")
    if int(progress.ResultCode) != 0:
        raise RuntimeError(f"guest copy failed for {guest_path}: {int(progress.ResultCode)}")


def open_guest(control):
    return control._open_guest()


def close_guest(host_session, guest_session, clear: bytearray) -> None:
    for index in range(len(clear)):
        clear[index] = 0
    if guest_session is not None:
        try:
            guest_session.Close()
        except Exception:
            pass
    if host_session is not None:
        try:
            host_session.UnlockMachine()
        except Exception:
            pass


def preflight(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        encoded = base64.b64encode(PREFLIGHT_PS.encode("utf-16-le")).decode("ascii")
        exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        code, stdout, _ = run_process(
            guest_session,
            exe,
            [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            120,
        )
        if code != 0:
            raise RuntimeError(f"native preflight failed with exit code {code}")
        raw = base64.b64decode(stdout.decode("ascii").strip(), validate=True).decode("utf-8")
        value = json.loads(raw)
        if value.get("schema") != "xq-openclaw-native-preflight-v1":
            raise RuntimeError("native preflight schema mismatch")
        if value.get("computer") != "PMAI-QQVM" or not str(value.get("user", "")).lower().endswith(r"\pmai_setup"):
            raise RuntimeError("native preflight identity mismatch")
        output = HOST_ROOT / "guest-native-preflight.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def stage(control) -> int:
    files = {
        "node-v26.8.2-win-x64.zip": NODE_ZIP_SHA256,
        "openclaw-2026.9.4.tgz": OPENCLAW_TGZ_SHA256,
        "install-v2026.9.4.ps1": INSTALLER_SHA256,
        "node-v26.8.2-SHASUMS256.txt": None,
    }
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        create_script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
if (-not (Test-Path -LiteralPath $root)) {{ New-Item -ItemType Directory -Path $root | Out-Null }}
$value = [ordered]@{{ schema = 'xq-openclaw-native-stage-v1'; root = $root; ready = (Test-Path -LiteralPath $root) }}
$json = $value | ConvertTo-Json -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        result = run_ps_json(guest_session, create_script)
        if not result.get("ready"):
            raise RuntimeError("guest staging directory was not created")
        for name in files:
            host_path = HOST_ROOT / name
            if not host_path.is_file():
                raise FileNotFoundError(host_path)
            guest_path = GUEST_ROOT + "\\" + name
            copy_to_guest(guest_session, host_path, guest_path)
        expected_entries = "\n".join(
            f"  '{name}' = '{expected or ''}'" for name, expected in files.items()
        )
        verify_script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$expected = @{{
{expected_entries}
}}
$items = @()
foreach ($name in $expected.Keys | Sort-Object) {{
  $path = Join-Path $root $name
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {{ throw "missing staged file: $name" }}
  $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToUpperInvariant()
  if ($expected[$name] -and $hash -ne $expected[$name]) {{ throw "staged hash mismatch: $name" }}
  $items += [ordered]@{{ name = $name; bytes = [uint64](Get-Item -LiteralPath $path).Length; sha256 = $hash }}
}}
$value = [ordered]@{{ schema = 'xq-openclaw-native-stage-v1'; root = $root; files = $items }}
$json = $value | ConvertTo-Json -Depth 5 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, verify_script, 300)
        output = HOST_ROOT / "guest-native-stage.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def install_node(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$zip = Join-Path $root 'node-v{NODE_VERSION}-win-x64.zip'
$nodeRoot = Join-Path $root 'node'
$expectedHash = '{NODE_ZIP_SHA256}'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToUpperInvariant() -ne $expectedHash) {{ throw 'Node archive hash mismatch' }}
if (Test-Path -LiteralPath $nodeRoot) {{
  $existing = Join-Path $nodeRoot 'node.exe'
  if (-not (Test-Path -LiteralPath $existing -PathType Leaf)) {{ throw 'Node destination exists but is incomplete' }}
  $existingVersion = (& $existing --version).Trim()
  if ($existingVersion -ne 'v{NODE_VERSION}') {{ throw 'Node destination has an unexpected version' }}
}} else {{
  $extract = Join-Path $root 'node-extract-v{NODE_VERSION}'
  if (Test-Path -LiteralPath $extract) {{ throw 'Node extraction staging path already exists' }}
  New-Item -ItemType Directory -Path $extract | Out-Null
  Expand-Archive -LiteralPath $zip -DestinationPath $extract
  $children = @(Get-ChildItem -LiteralPath $extract -Directory)
  if ($children.Count -ne 1 -or -not (Test-Path -LiteralPath (Join-Path $children[0].FullName 'node.exe'))) {{ throw 'Unexpected Node archive layout' }}
  Move-Item -LiteralPath $children[0].FullName -Destination $nodeRoot
  Remove-Item -LiteralPath $extract
}}
$node = Join-Path $nodeRoot 'node.exe'
$npm = Join-Path $nodeRoot 'npm.cmd'
$value = [ordered]@{{
  schema = 'xq-openclaw-native-node-v1'
  node_version = [string](& $node --version)
  npm_version = [string](& $npm --version)
  node_path = $node
  user_path_changed = $false
  machine_path_changed = $false
}}
$json = $value | ConvertTo-Json -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 600)
        if value.get("node_version") != f"v{NODE_VERSION}":
            raise RuntimeError("guest Node version mismatch")
        output = HOST_ROOT / "guest-native-node.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def install_package(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$npm = Join-Path $nodeRoot 'npm.cmd'
$tgz = Join-Path $root 'openclaw-{OPENCLAW_VERSION}.tgz'
$prefix = Join-Path $root 'npm'
$cache = Join-Path $root 'npm-cache'
$userConfig = Join-Path $root 'npmrc'
$log = Join-Path $root 'npm-install.log'
$expectedHash = '{OPENCLAW_TGZ_SHA256}'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $tgz).Hash.ToUpperInvariant() -ne $expectedHash) {{ throw 'OpenClaw archive hash mismatch' }}
if ((& $node --version).Trim() -ne 'v{NODE_VERSION}') {{ throw 'Unexpected private Node version' }}
if (Test-Path -LiteralPath $prefix) {{ throw 'OpenClaw npm prefix already exists; refusing an in-place overwrite' }}
New-Item -ItemType Directory -Path $prefix | Out-Null
$env:Path = $nodeRoot + ';' + $env:Path
$env:npm_config_cache = $cache
$env:npm_config_userconfig = $userConfig
$env:npm_config_update_notifier = 'false'
$env:npm_config_audit = 'false'
$env:npm_config_fund = 'false'
& $npm install --global --prefix $prefix --allow-scripts=openclaw --no-audit --no-fund $tgz *> $log
$installExit = $LASTEXITCODE
$packageJson = Join-Path $prefix 'node_modules\openclaw\package.json'
$cli = Join-Path $prefix 'node_modules\openclaw\openclaw.mjs'
$installedVersion = $null
$cliVersion = $null
$cliExit = $null
if ($installExit -eq 0 -and (Test-Path -LiteralPath $packageJson) -and (Test-Path -LiteralPath $cli)) {{
  $installedVersion = [string](Get-Content -LiteralPath $packageJson -Raw | ConvertFrom-Json).version
  $cliVersion = [string](& $node $cli --version)
  $cliExit = $LASTEXITCODE
  & $npm ls --global --prefix $prefix --depth=0 --json | Out-File -LiteralPath (Join-Path $root 'npm-ls-depth0.json') -Encoding utf8
}}
$logTail = @()
if (Test-Path -LiteralPath $log) {{ $logTail = @(Get-Content -LiteralPath $log -Tail 12) }}
$value = [ordered]@{{
  schema = 'xq-openclaw-native-package-v1'
  install_exit = $installExit
  installed_version = $installedVersion
  cli_version = $cliVersion
  cli_exit = $cliExit
  package_path = $packageJson
  prefix = $prefix
  user_path_changed = $false
  machine_path_changed = $false
  log_tail = $logTail
}}
$json = $value | ConvertTo-Json -Depth 5 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 1_800)
        try:
            copy_from_guest_file(
                guest_session,
                GUEST_ROOT + r"\npm-install.log",
                HOST_ROOT / "guest-npm-install.log",
            )
        except Exception:
            if int(value.get("install_exit", -1)) == 0:
                raise
        if int(value.get("install_exit", -1)) == 0:
            copy_from_guest_file(
                guest_session,
                GUEST_ROOT + r"\npm-ls-depth0.json",
                HOST_ROOT / "guest-npm-ls-depth0.json",
            )
        output = HOST_ROOT / "guest-native-package.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        if int(value.get("install_exit", -1)) != 0:
            return 2
        if value.get("installed_version") != OPENCLAW_VERSION or value.get("cli_exit") != 0:
            return 3
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def install_diagnostic(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$prefix = Join-Path $root 'npm'
$log = Join-Path $root 'npm-install.log'
$packageJson = Join-Path $prefix 'node_modules\openclaw\package.json'
$cli = Join-Path $prefix 'node_modules\openclaw\openclaw.mjs'
$matching = @(Get-CimInstance Win32_Process | Where-Object {{
  $_.Name -eq 'node.exe' -and ([string]$_.CommandLine) -match '(?i)npm-cli\.js' -and ([string]$_.CommandLine) -match '(?i)OpenClawNativePilot'
}})
$before = @($matching | ForEach-Object {{ [ordered]@{{ pid = [uint32]$_.ProcessId; name = [string]$_.Name; command = [string]$_.CommandLine }} }})
$stopped = @()
foreach ($proc in $matching) {{
  try {{ Stop-Process -Id $proc.ProcessId -Force; $stopped += [uint32]$proc.ProcessId }} catch {{}}
}}
$debugLogs = @()
$debugRoot = Join-Path $root 'npm-cache\_logs'
if (Test-Path -LiteralPath $debugRoot) {{
  $debugLogs = @(Get-ChildItem -LiteralPath $debugRoot -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 5 | ForEach-Object {{
    [ordered]@{{ name = $_.Name; bytes = [uint64]$_.Length; modified_utc = $_.LastWriteTimeUtc.ToString('o'); tail = @(Get-Content -LiteralPath $_.FullName -Tail 20) }}
  }})
}}
$version = $null
if (Test-Path -LiteralPath $packageJson) {{
  try {{ $version = [string](Get-Content -LiteralPath $packageJson -Raw | ConvertFrom-Json).version }} catch {{}}
}}
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
$tasks = @()
try {{ $tasks = @(Get-ScheduledTask | Where-Object TaskName -like 'OpenClaw Gateway*' | ForEach-Object TaskName) }} catch {{}}
$value = [ordered]@{{
  schema = 'xq-openclaw-native-install-diagnostic-v1'
  captured_utc = [DateTime]::UtcNow.ToString('o')
  npm_processes_before_stop = $before
  npm_processes_stopped = $stopped
  install_log_exists = (Test-Path -LiteralPath $log)
  install_log_bytes = $(if (Test-Path -LiteralPath $log) {{ [uint64](Get-Item -LiteralPath $log).Length }} else {{ 0 }})
  install_log_tail = $(if (Test-Path -LiteralPath $log) {{ @(Get-Content -LiteralPath $log -Tail 80) }} else {{ @() }})
  debug_logs = $debugLogs
  prefix_exists = (Test-Path -LiteralPath $prefix)
  package_json_exists = (Test-Path -LiteralPath $packageJson)
  installed_version_if_present = $version
  cli_exists = (Test-Path -LiteralPath $cli)
  gateway_task_names = $tasks
  port_18789_listener_count = $listeners.Count
}}
$json = $value | ConvertTo-Json -Depth 8 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 300)
        if value.get("install_log_exists"):
            copy_from_guest_file(
                guest_session,
                GUEST_ROOT + r"\npm-install.log",
                HOST_ROOT / "guest-npm-install-timeout.log",
            )
        output = HOST_ROOT / "guest-native-install-diagnostic.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def copy_latest_debug(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = Join-Path '{GUEST_ROOT}' 'npm-cache\_logs'
$latest = Get-ChildItem -LiteralPath $root -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $latest) {{ throw 'No npm debug log was found' }}
$value = [ordered]@{{
  schema = 'xq-openclaw-native-npm-debug-v1'
  name = [string]$latest.Name
  bytes = [uint64]$latest.Length
  sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $latest.FullName).Hash
}}
$json = $value | ConvertTo-Json -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 120)
        name = value.get("name")
        if not isinstance(name, str) or "\\" in name or "/" in name or not name.endswith("-debug-0.log"):
            raise RuntimeError("unexpected npm debug-log name")
        copy_from_guest_file(
            guest_session,
            GUEST_ROOT + "\\npm-cache\\_logs\\" + name,
            HOST_ROOT / "guest-npm-debug-timeout.log",
        )
        output = HOST_ROOT / "guest-native-npm-debug.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def install_package_retry(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$npmCli = Join-Path $nodeRoot 'node_modules\npm\bin\npm-cli.js'
$tgz = Join-Path $root 'openclaw-{OPENCLAW_VERSION}.tgz'
$prefix = Join-Path $root 'npm'
$cache = Join-Path $root 'npm-cache'
$userConfig = Join-Path $root 'npmrc'
$stdoutLog = Join-Path $root 'npm-official-spec.stdout.log'
$stderrLog = Join-Path $root 'npm-official-spec.stderr.log'
$packageJson = Join-Path $prefix 'node_modules\openclaw\package.json'
$entry = Join-Path $prefix 'node_modules\openclaw\dist\entry.js'
$cli = Join-Path $prefix 'node_modules\openclaw\openclaw.mjs'
$pending = Join-Path $prefix 'node_modules\openclaw\.openclaw-lifecycle-pending'
$legacyGuard = Join-Path $prefix 'node_modules\openclaw\dist\openclaw-install-guard'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $tgz).Hash.ToUpperInvariant() -ne '{OPENCLAW_TGZ_SHA256}') {{ throw 'OpenClaw archive hash mismatch' }}
if ((& $node --version).Trim() -ne 'v{NODE_VERSION}') {{ throw 'Unexpected private Node version' }}
$matching = @(Get-CimInstance Win32_Process | Where-Object {{
  $_.Name -eq 'node.exe' -and ([string]$_.CommandLine) -match '(?i)npm-cli\.js' -and ([string]$_.CommandLine) -match '(?i)OpenClawNativePilot'
}})
if ($matching.Count -ne 0) {{ throw 'A prior pilot npm process is still running' }}
$replacingVersion = $null
if (Test-Path -LiteralPath $packageJson) {{
  $replacingVersion = [string](Get-Content -LiteralPath $packageJson -Raw | ConvertFrom-Json).version
  if ($replacingVersion -ne '{OPENCLAW_VERSION}') {{ throw 'An unexpected package version exists; refusing to overwrite it' }}
}}
if (Test-Path -LiteralPath $prefix) {{ Remove-Item -LiteralPath $prefix -Recurse -Force }}
New-Item -ItemType Directory -Path $prefix | Out-Null
foreach ($logPath in @($stdoutLog, $stderrLog)) {{ if (Test-Path -LiteralPath $logPath) {{ Remove-Item -LiteralPath $logPath -Force }} }}
$env:Path = $nodeRoot + ';' + $env:Path
$env:npm_config_cache = $cache
$env:npm_config_userconfig = $userConfig
$env:npm_config_update_notifier = 'false'
$env:npm_config_audit = 'false'
$env:npm_config_fund = 'false'
$allow = '--allow-scripts=openclaw'
$installSpec = 'openclaw@{OPENCLAW_VERSION}'
$arguments = @(
  $npmCli,
  'install',
  '--global',
  '--prefix', $prefix,
  '--min-release-age=0',
  $allow,
  '--no-audit',
  '--no-fund',
  $installSpec
)
$process = Start-Process -FilePath $node -ArgumentList $arguments -WorkingDirectory $env:USERPROFILE -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -Wait -PassThru -NoNewWindow
$installExit = [int]$process.ExitCode
$installedVersion = $null
$cliVersion = $null
$cliExit = $null
if (Test-Path -LiteralPath $packageJson) {{
  $installedVersion = [string](Get-Content -LiteralPath $packageJson -Raw | ConvertFrom-Json).version
}}
if ($installExit -eq 0 -and (Test-Path -LiteralPath $cli)) {{
  $cliVersion = [string](& $node $cli --version)
  $cliExit = $LASTEXITCODE
}}
$lifecycleComplete = (Test-Path -LiteralPath $entry -PathType Leaf) -and -not (Test-Path -LiteralPath $pending) -and -not (Test-Path -LiteralPath $legacyGuard)
$value = [ordered]@{{
  schema = 'xq-openclaw-native-package-official-spec-v1'
  install_spec = $installSpec
  allow_scripts = 'openclaw'
  replacing_version = $replacingVersion
  install_exit = $installExit
  installed_version = $installedVersion
  cli_version = $cliVersion
  cli_exit = $cliExit
  lifecycle_complete = $lifecycleComplete
  entry_exists = (Test-Path -LiteralPath $entry -PathType Leaf)
  pending_marker_exists = (Test-Path -LiteralPath $pending)
  legacy_guard_exists = (Test-Path -LiteralPath $legacyGuard)
  prefix = $prefix
  user_path_changed = $false
  machine_path_changed = $false
  stdout_bytes = $(if (Test-Path -LiteralPath $stdoutLog) {{ [uint64](Get-Item -LiteralPath $stdoutLog).Length }} else {{ 0 }})
  stderr_bytes = $(if (Test-Path -LiteralPath $stderrLog) {{ [uint64](Get-Item -LiteralPath $stderrLog).Length }} else {{ 0 }})
  stdout_tail = $(if (Test-Path -LiteralPath $stdoutLog) {{ @((Get-Content -LiteralPath $stdoutLog -Tail 25) | ForEach-Object {{ [string]$_ }}) }} else {{ @() }})
  stderr_tail = $(if (Test-Path -LiteralPath $stderrLog) {{ @((Get-Content -LiteralPath $stderrLog -Tail 25) | ForEach-Object {{ [string]$_ }}) }} else {{ @() }})
}}
$json = $value | ConvertTo-Json -Depth 5 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 1_800)
        for guest_name, host_name in (
            ("npm-official-spec.stdout.log", "guest-npm-official-spec.stdout.log"),
            ("npm-official-spec.stderr.log", "guest-npm-official-spec.stderr.log"),
        ):
            copy_from_guest_file(
                guest_session,
                GUEST_ROOT + "\\" + guest_name,
                HOST_ROOT / host_name,
            )
        output = HOST_ROOT / "guest-native-package-official-spec.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        if int(value.get("install_exit", -1)) != 0:
            return 2
        if (
            value.get("installed_version") != OPENCLAW_VERSION
            or value.get("cli_exit") != 0
            or not value.get("lifecycle_complete")
        ):
            return 3
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def assess_install(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$prefix = Join-Path $root 'npm'
$node = Join-Path $root 'node\node.exe'
$packageJson = Join-Path $prefix 'node_modules\openclaw\package.json'
$entry = Join-Path $prefix 'node_modules\openclaw\dist\entry.js'
$cli = Join-Path $prefix 'node_modules\openclaw\openclaw.mjs'
$pending = Join-Path $prefix 'node_modules\openclaw\.openclaw-lifecycle-pending'
$legacyGuard = Join-Path $prefix 'node_modules\openclaw\dist\openclaw-install-guard'
$stdoutLog = Join-Path $root 'npm-retry.stdout.log'
$stderrLog = Join-Path $root 'npm-retry.stderr.log'
$version = $null
$cliVersion = $null
$cliExit = $null
if (Test-Path -LiteralPath $packageJson) {{ $version = [string](Get-Content -LiteralPath $packageJson -Raw | ConvertFrom-Json).version }}
if (Test-Path -LiteralPath $cli) {{ $cliVersion = [string](& $node $cli --version); $cliExit = $LASTEXITCODE }}
$matching = @(Get-CimInstance Win32_Process | Where-Object {{
  $_.Name -eq 'node.exe' -and ([string]$_.CommandLine) -match '(?i)OpenClawNativePilot'
}} | ForEach-Object {{ [ordered]@{{ pid = [uint32]$_.ProcessId; command = [string]$_.CommandLine }} }})
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
$tasks = @()
try {{ $tasks = @(Get-ScheduledTask | Where-Object TaskName -like 'OpenClaw Gateway*' | ForEach-Object TaskName) }} catch {{}}
function LogSummary([string]$path) {{
  if (-not (Test-Path -LiteralPath $path)) {{ return [ordered]@{{ exists = $false; bytes = 0; sha256 = $null; tail = @() }} }}
  return [ordered]@{{
    exists = $true
    bytes = [uint64](Get-Item -LiteralPath $path).Length
    sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
    tail = @((Get-Content -LiteralPath $path -Tail 30) | ForEach-Object {{ [string]$_ }})
  }}
}}
$value = [ordered]@{{
  schema = 'xq-openclaw-native-assess-v1'
  captured_utc = [DateTime]::UtcNow.ToString('o')
  package_json_exists = (Test-Path -LiteralPath $packageJson)
  installed_version_if_present = $version
  entry_exists = (Test-Path -LiteralPath $entry -PathType Leaf)
  cli_exists = (Test-Path -LiteralPath $cli)
  cli_version = $cliVersion
  cli_exit = $cliExit
  pending_marker_exists = (Test-Path -LiteralPath $pending)
  legacy_guard_exists = (Test-Path -LiteralPath $legacyGuard)
  matching_node_processes = $matching
  gateway_task_names = $tasks
  port_18789_listener_count = $listeners.Count
  stdout_log = (LogSummary $stdoutLog)
  stderr_log = (LogSummary $stderrLog)
}}
$json = $value | ConvertTo-Json -Depth 7 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 180)
        for key, guest_name, host_name in (
            ("stdout_log", "npm-retry.stdout.log", "guest-npm-retry.stdout.log"),
            ("stderr_log", "npm-retry.stderr.log", "guest-npm-retry.stderr.log"),
        ):
            summary = value.get(key)
            if isinstance(summary, dict) and summary.get("exists"):
                copy_from_guest_file(
                    guest_session,
                    GUEST_ROOT + "\\" + guest_name,
                    HOST_ROOT / host_name,
                )
        output = HOST_ROOT / "guest-native-assess.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def cli_probe(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {{ throw 'OpenClaw CLI is missing' }}
$env:Path = $nodeRoot + ';' + $env:Path
function Probe([string]$name, [string[]]$arguments) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $lines = @(& $node $cli @arguments 2>&1 | ForEach-Object {{ [string]$_ }})
    $exit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
  return [ordered]@{{ name = $name; arguments = $arguments; exit = $exit; output = $lines }}
}}
$probes = @()
$probes += Probe 'version' @('--version')
$probes += Probe 'root-help' @('--help')
$probes += Probe 'gateway-help' @('--profile','xq-native-pilot','gateway','--help')
$probes += Probe 'gateway-run-help' @('--profile','xq-native-pilot','gateway','run','--help')
$probes += Probe 'gateway-install-help' @('--profile','xq-native-pilot','gateway','install','--help')
$probes += Probe 'gateway-status-help' @('--profile','xq-native-pilot','gateway','status','--help')
$probes += Probe 'gateway-diagnostics-help' @('--profile','xq-native-pilot','gateway','diagnostics','--help')
$probes += Probe 'gateway-diagnostics-export-help' @('--profile','xq-native-pilot','gateway','diagnostics','export','--help')
$probes += Probe 'gateway-stability-help' @('--profile','xq-native-pilot','gateway','stability','--help')
$probes += Probe 'gateway-stop-help' @('--profile','xq-native-pilot','gateway','stop','--help')
$probes += Probe 'gateway-start-help' @('--profile','xq-native-pilot','gateway','start','--help')
$probes += Probe 'gateway-uninstall-help' @('--profile','xq-native-pilot','gateway','uninstall','--help')
$probes += Probe 'config-help' @('--profile','xq-native-pilot','config','--help')
$probes += Probe 'config-set-help' @('--profile','xq-native-pilot','config','set','--help')
$probes += Probe 'doctor-help' @('--profile','xq-native-pilot','doctor','--help')
$probes += Probe 'onboard-help' @('--profile','xq-native-pilot','onboard','--help')
$probes += Probe 'health-help' @('--profile','xq-native-pilot','health','--help')
$probes += Probe 'status-help' @('--profile','xq-native-pilot','status','--help')
$probes += Probe 'tui-help' @('--profile','xq-native-pilot','tui','--help')
$probes += Probe 'models-help' @('--profile','xq-native-pilot','models','--help')
$probes += Probe 'models-auth-help' @('--profile','xq-native-pilot','models','auth','--help')
$probes += Probe 'models-auth-login-help' @('--profile','xq-native-pilot','models','auth','login','--help')
$probes += Probe 'models-status-help' @('--profile','xq-native-pilot','models','status','--help')
$probes += Probe 'agent-help' @('--profile','xq-native-pilot','agent','--help')
$probes += Probe 'uninstall-help' @('--profile','xq-native-pilot','uninstall','--help')
$value = [ordered]@{{ schema = 'xq-openclaw-native-cli-probe-v1'; probes = $probes }}
$json = $value | ConvertTo-Json -Depth 8 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 600)
        output = HOST_ROOT / "guest-native-cli-probe.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(
            json.dumps(
                {
                    "schema": value.get("schema"),
                    "probes": [
                        {
                            "name": item.get("name"),
                            "exit": item.get("exit"),
                            "lines": len(item.get("output", [])),
                        }
                        for item in value.get("probes", [])
                        if isinstance(item, dict)
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def configure_minimal(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$profile = 'xq-native-pilot'
$state = Join-Path $env:USERPROFILE '.openclaw-xq-native-pilot'
$configPath = Join-Path $state 'openclaw.json'
$workspace = Join-Path $root 'workspace'
$secretStdout = Join-Path $root '.gateway-token-generation.stdout.tmp'
$secretStderr = Join-Path $root '.gateway-token-generation.stderr.tmp'
if (Test-Path -LiteralPath $configPath) {{ throw 'Pilot config already exists; refusing to overwrite it' }}
if (-not (Test-Path -LiteralPath $workspace)) {{ New-Item -ItemType Directory -Path $workspace | Out-Null }}
$env:Path = $nodeRoot + ';' + $env:Path
function SetValue([string]$path, [string]$value, [bool]$strict) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $arguments = @('--profile',$profile,'config','set',$path,$value,'--expect-current-absent')
    if ($strict) {{ $arguments += '--strict-json' }}
    $lines = @(& $node $cli @arguments 2>&1 | ForEach-Object {{ [string]$_ }})
    $exit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
  if ($exit -ne 0) {{ throw "config set failed for $path (exit $exit): $($lines -join ' | ')" }}
  return [ordered]@{{ path = $path; exit = $exit }}
}}
$operations = @()
$operations += SetValue 'gateway.mode' 'local' $false
$operations += SetValue 'gateway.bind' 'loopback' $false
$operations += SetValue 'gateway.port' '18789' $true
$operations += SetValue 'gateway.auth.mode' 'token' $false
$operations += SetValue 'gateway.tailscale.mode' 'off' $false
$operations += SetValue 'gateway.terminal.enabled' 'false' $true
$operations += SetValue 'gateway.cliAgents.enabled' 'false' $true
$operations += SetValue 'gateway.controlUi.enabled' 'true' $true
$operations += SetValue 'gateway.controlUi.experimental.customPlugins' 'false' $true
$operations += SetValue 'tools.profile' 'minimal' $false
$operations += SetValue 'plugins.enabled' 'false' $true
$operations += SetValue 'agents.defaults.workspace' $workspace $false
foreach ($path in @($secretStdout, $secretStderr)) {{ if (Test-Path -LiteralPath $path) {{ Remove-Item -LiteralPath $path -Force }} }}
$tokenArgs = @($cli,'--profile',$profile,'doctor','--generate-gateway-token','--non-interactive','--yes')
$tokenProcess = Start-Process -FilePath $node -ArgumentList $tokenArgs -WorkingDirectory $env:USERPROFILE -RedirectStandardOutput $secretStdout -RedirectStandardError $secretStderr -Wait -PassThru -NoNewWindow
$tokenExit = [int]$tokenProcess.ExitCode
if ($tokenExit -ne 0) {{ throw "gateway token generation failed with exit $tokenExit" }}
$validateLines = @(& $node $cli --profile $profile config validate 2>&1 | ForEach-Object {{ [string]$_ }})
$validateExit = $LASTEXITCODE
if ($validateExit -ne 0) {{ throw "config validation failed: $($validateLines -join ' | ')" }}
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$tokenValue = $config.gateway.auth.token
$tokenPresent = ($null -ne $tokenValue) -and -not [string]::IsNullOrWhiteSpace([string]$tokenValue)
$tokenString = [string]$tokenValue
$result = [ordered]@{{
  schema = 'xq-openclaw-native-minimal-config-v1'
  profile = $profile
  state_path = $state
  config_path = $configPath
  config_sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $configPath).Hash
  workspace_path = $workspace
  operations = $operations
  validate_exit = $validateExit
  validate_output = $validateLines
  gateway_mode = [string]$config.gateway.mode
  gateway_bind = [string]$config.gateway.bind
  gateway_port = [int]$config.gateway.port
  gateway_auth_mode = [string]$config.gateway.auth.mode
  gateway_token_present = $tokenPresent
  gateway_token_length_sane = ($tokenString.Length -ge 32)
  tailscale_mode = [string]$config.gateway.tailscale.mode
  terminal_enabled = [bool]$config.gateway.terminal.enabled
  cli_agents_enabled = [bool]$config.gateway.cliAgents.enabled
  control_ui_enabled = [bool]$config.gateway.controlUi.enabled
  custom_plugin_ui_enabled = [bool]$config.gateway.controlUi.experimental.customPlugins
  tools_profile = [string]$config.tools.profile
  plugins_enabled = [bool]$config.plugins.enabled
  default_state_exists = (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.openclaw'))
  token_output_deleted = $false
}}
$tokenValue = $null
$tokenString = ''
foreach ($path in @($secretStdout, $secretStderr)) {{ if (Test-Path -LiteralPath $path) {{ Remove-Item -LiteralPath $path -Force }} }}
$result.token_output_deleted = (-not (Test-Path -LiteralPath $secretStdout)) -and (-not (Test-Path -LiteralPath $secretStderr))
$json = $result | ConvertTo-Json -Depth 6 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 600)
        if not value.get("gateway_token_present") or not value.get("gateway_token_length_sane"):
            raise RuntimeError("gateway token was not configured safely")
        output = HOST_ROOT / "guest-native-minimal-config.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def gateway_foreground_test(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$stdoutLog = Join-Path $root 'gateway-foreground.stdout.log'
$stderrLog = Join-Path $root 'gateway-foreground.stderr.log'
$profile = 'xq-native-pilot'
$env:Path = $nodeRoot + ';' + $env:Path
$before = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
if ($before.Count -ne 0) {{ throw 'Port 18789 already has a listener' }}
foreach ($path in @($stdoutLog, $stderrLog)) {{ if (Test-Path -LiteralPath $path) {{ Remove-Item -LiteralPath $path -Force }} }}
$arguments = @($cli,'--profile',$profile,'gateway','run','--bind','loopback','--port','18789','--auth','token','--tailscale','off','--ws-log','compact')
$gateway = Start-Process -FilePath $node -ArgumentList $arguments -WorkingDirectory $env:USERPROFILE -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru -NoNewWindow
$started = $false
$listeners = @()
$deadline = [DateTime]::UtcNow.AddSeconds(90)
while ([DateTime]::UtcNow -lt $deadline) {{
  if ($gateway.HasExited) {{ break }}
  $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
  if ($listeners.Count -gt 0) {{ $started = $true; break }}
  Start-Sleep -Milliseconds 500
}}
$healthLines = @()
$healthExit = $null
$readyHttpStatus = $null
$healthHttpStatus = $null
if ($started) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $healthLines = @(& $node $cli --profile $profile health --json --timeout 15000 2>&1 | ForEach-Object {{ [string]$_ }})
    $healthExit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
  try {{ $readyHttpStatus = [int](Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:18789/readyz' -TimeoutSec 15).StatusCode }} catch {{ $readyHttpStatus = -1 }}
  try {{ $healthHttpStatus = [int](Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:18789/healthz' -TimeoutSec 15).StatusCode }} catch {{ $healthHttpStatus = -1 }}
}}
$listenerInfo = @($listeners | ForEach-Object {{ [ordered]@{{ address = [string]$_.LocalAddress; port = [int]$_.LocalPort; pid = [uint32]$_.OwningProcess }} }})
$gatewayExitBeforeStop = $(if ($gateway.HasExited) {{ [int]$gateway.ExitCode }} else {{ $null }})
if (-not $gateway.HasExited) {{ Stop-Process -Id $gateway.Id -Force }}
try {{ Wait-Process -Id $gateway.Id -Timeout 30 -ErrorAction SilentlyContinue }} catch {{}}
$closed = $false
$closeDeadline = [DateTime]::UtcNow.AddSeconds(30)
while ([DateTime]::UtcNow -lt $closeDeadline) {{
  if (@(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue).Count -eq 0) {{ $closed = $true; break }}
  Start-Sleep -Milliseconds 500
}}
$allowedAddresses = @('127.0.0.1','::1')
$loopbackOnly = ($listenerInfo.Count -gt 0) -and (@($listenerInfo | Where-Object {{ $_.address -notin $allowedAddresses }}).Count -eq 0)
$result = [ordered]@{{
  schema = 'xq-openclaw-native-gateway-foreground-v1'
  started = $started
  gateway_pid = [uint32]$gateway.Id
  gateway_exit_before_stop = $gatewayExitBeforeStop
  listeners = $listenerInfo
  loopback_only = $loopbackOnly
  health_exit = $healthExit
  health_output = $healthLines
  readyz_http_status = $readyHttpStatus
  healthz_http_status = $healthHttpStatus
  port_closed_after_stop = $closed
  stdout_log = [ordered]@{{ bytes = [uint64](Get-Item -LiteralPath $stdoutLog).Length; sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $stdoutLog).Hash }}
  stderr_log = [ordered]@{{ bytes = [uint64](Get-Item -LiteralPath $stderrLog).Length; sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $stderrLog).Hash }}
}}
$json = $result | ConvertTo-Json -Depth 7 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 240)
        output = HOST_ROOT / "guest-native-gateway-foreground.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        if not value.get("started") or not value.get("loopback_only"):
            return 2
        if value.get("health_exit") != 0 or value.get("readyz_http_status") != 200:
            return 3
        if not value.get("port_closed_after_stop"):
            return 4
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def disable_automation(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$profile = 'xq-native-pilot'
$env:Path = $nodeRoot + ';' + $env:Path
function SetValue([string]$path, [string]$value, [bool]$strict) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $arguments = @('--profile',$profile,'config','set',$path,$value,'--expect-current-absent')
    if ($strict) {{ $arguments += '--strict-json' }}
    $output = @(& $node $cli @arguments 2>&1 | ForEach-Object {{ [string]$_ }})
    $exit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
  if ($exit -ne 0) {{ throw "config set failed for $path (exit $exit): $($output -join ' | ')" }}
}}
SetValue 'agents.defaults.heartbeat.every' '0m' $false
SetValue 'cron.enabled' 'false' $true
$validation = @(& $node $cli --profile $profile config validate 2>&1 | ForEach-Object {{ [string]$_ }})
$validationExit = $LASTEXITCODE
if ($validationExit -ne 0) {{ throw "config validation failed: $($validation -join ' | ')" }}
$configPath = Join-Path $env:USERPROFILE '.openclaw-xq-native-pilot\openclaw.json'
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$value = [ordered]@{{
  schema = 'xq-openclaw-native-automation-disabled-v1'
  heartbeat_every = [string]$config.agents.defaults.heartbeat.every
  cron_enabled = [bool]$config.cron.enabled
  config_sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $configPath).Hash
  validate_exit = $validationExit
  validate_output = $validation
  port_18789_listener_count = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue).Count
}}
$json = $value | ConvertTo-Json -Depth 5 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 300)
        output = HOST_ROOT / "guest-native-automation-disabled.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        if value.get("heartbeat_every") != "0m" or value.get("cron_enabled"):
            return 2
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def gateway_service_install(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$profile = 'xq-native-pilot'
$installOut = Join-Path $root 'gateway-service-install.stdout.log'
$installErr = Join-Path $root 'gateway-service-install.stderr.log'
$env:Path = $nodeRoot + ';' + $env:Path
if (@(Get-ScheduledTask | Where-Object TaskName -like 'OpenClaw Gateway*').Count -ne 0) {{ throw 'An OpenClaw Gateway task already exists' }}
if (@(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue).Count -ne 0) {{ throw 'Port 18789 already has a listener' }}
foreach ($path in @($installOut, $installErr)) {{ if (Test-Path -LiteralPath $path) {{ Remove-Item -LiteralPath $path -Force }} }}
$arguments = @($cli,'--profile',$profile,'gateway','install','--json','--runtime','node','--port','18789')
$install = Start-Process -FilePath $node -ArgumentList $arguments -WorkingDirectory $env:USERPROFILE -RedirectStandardOutput $installOut -RedirectStandardError $installErr -Wait -PassThru -NoNewWindow
$installExit = [int]$install.ExitCode
$started = $false
$listeners = @()
$deadline = [DateTime]::UtcNow.AddSeconds(120)
while ([DateTime]::UtcNow -lt $deadline) {{
  $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
  if ($listeners.Count -gt 0) {{ $started = $true; break }}
  Start-Sleep -Milliseconds 500
}}
$healthLines = @()
$healthExit = $null
$statusLines = @()
$statusExit = $null
if ($started) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $healthLines = @(& $node $cli --profile $profile health --json --timeout 15000 2>&1 | ForEach-Object {{ [string]$_ }})
    $healthExit = $LASTEXITCODE
    $statusLines = @(& $node $cli --profile $profile gateway status --json --require-rpc --timeout 15000 2>&1 | ForEach-Object {{ [string]$_ }})
    $statusExit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
}}
$tasks = @(Get-ScheduledTask | Where-Object TaskName -like 'OpenClaw Gateway*')
$taskSummaries = @()
foreach ($task in $tasks) {{
  $taskInfo = Get-ScheduledTaskInfo -TaskName $task.TaskName -TaskPath $task.TaskPath
  $actions = @()
  foreach ($action in @($task.Actions)) {{
    $execute = [string]$action.Execute
    $argumentsText = [string]$action.Arguments
    $wrapperExists = Test-Path -LiteralPath $execute -PathType Leaf
    $wrapperText = $(if ($wrapperExists) {{ [string](Get-Content -LiteralPath $execute -Raw) }} else {{ '' }})
    $actions += [ordered]@{{
      execute = $execute
      arguments = $argumentsText
      arguments_contain_token_flag = ($argumentsText -match '(?i)(--token|OPENCLAW_GATEWAY_TOKEN)')
      wrapper_exists = $wrapperExists
      wrapper_sha256 = $(if ($wrapperExists) {{ [string](Get-FileHash -Algorithm SHA256 -LiteralPath $execute).Hash }} else {{ $null }})
      wrapper_contains_private_node = $wrapperText.Contains($node)
      wrapper_contains_profile = ($wrapperText -match '(?i)xq-native-pilot')
      wrapper_contains_token_material = ($wrapperText -match '(?i)(--token|OPENCLAW_GATEWAY_TOKEN\s*=)')
    }}
    $wrapperText = ''
  }}
  $taskSummaries += [ordered]@{{
    name = [string]$task.TaskName
    state = [string]$task.State
    enabled = [bool]$task.Settings.Enabled
    last_task_result = [int64]$taskInfo.LastTaskResult
    last_run_time_utc = $taskInfo.LastRunTime.ToUniversalTime().ToString('o')
    next_run_time_utc = $(if ($taskInfo.NextRunTime -gt [DateTime]::MinValue) {{ $taskInfo.NextRunTime.ToUniversalTime().ToString('o') }} else {{ $null }})
    principal_user = [string]$task.Principal.UserId
    actions = $actions
  }}
}}
$listenerInfo = @($listeners | ForEach-Object {{ [ordered]@{{ address = [string]$_.LocalAddress; port = [int]$_.LocalPort; pid = [uint32]$_.OwningProcess }} }})
$runtimeProcesses = @(Get-CimInstance Win32_Process | Where-Object {{
  $_.Name -eq 'node.exe' -and ([string]$_.CommandLine) -match '(?i)openclaw' -and ([string]$_.CommandLine) -match '(?i)gateway'
}} | ForEach-Object {{
  $command = [string]$_.CommandLine
  [ordered]@{{
    pid = [uint32]$_.ProcessId
    executable_path = [string]$_.ExecutablePath
    contains_profile = ($command -match '(?i)xq-native-pilot')
    contains_token_flag = ($command -match '(?i)(--token|OPENCLAW_GATEWAY_TOKEN)')
    contains_private_root = $command.Contains($root)
  }}
}})
$loopbackOnly = ($listenerInfo.Count -gt 0) -and (@($listenerInfo | Where-Object {{ $_.address -notin @('127.0.0.1','::1') }}).Count -eq 0)
$value = [ordered]@{{
  schema = 'xq-openclaw-native-gateway-service-v1'
  install_exit = $installExit
  install_stdout_bytes = [uint64](Get-Item -LiteralPath $installOut).Length
  install_stderr_bytes = [uint64](Get-Item -LiteralPath $installErr).Length
  install_stdout_sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $installOut).Hash
  install_stderr_sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $installErr).Hash
  started = $started
  listeners = $listenerInfo
  loopback_only = $loopbackOnly
  health_exit = $healthExit
  health_output = $healthLines
  status_exit = $statusExit
  status_output = $statusLines
  tasks = $taskSummaries
  runtime_processes = $runtimeProcesses
}}
$json = $value | ConvertTo-Json -Depth 9 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 360)
        output = HOST_ROOT / "guest-native-gateway-service.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        if value.get("install_exit") != 0 or not value.get("started"):
            return 2
        if not value.get("loopback_only") or value.get("health_exit") != 0 or value.get("status_exit") != 0:
            return 3
        tasks = value.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != 1:
            return 4
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def gateway_service_diagnostic(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$state = Join-Path $env:USERPROFILE '.openclaw-xq-native-pilot'
$out = Join-Path $root 'gateway-service-install.stdout.log'
$err = Join-Path $root 'gateway-service-install.stderr.log'
$tasks = @(Get-ScheduledTask | Where-Object TaskName -like 'OpenClaw Gateway*')
$taskSummaries = @()
foreach ($task in $tasks) {{
  $info = Get-ScheduledTaskInfo -TaskName $task.TaskName -TaskPath $task.TaskPath
  $taskSummaries += [ordered]@{{
    name = [string]$task.TaskName
    state = [string]$task.State
    enabled = [bool]$task.Settings.Enabled
    principal_user = [string]$task.Principal.UserId
    principal_logon_type = [string]$task.Principal.LogonType
    principal_run_level = [string]$task.Principal.RunLevel
    last_task_result = [int64]$info.LastTaskResult
    triggers = @($task.Triggers | ForEach-Object {{ [ordered]@{{ type = $_.CimClass.CimClassName; enabled = [bool]$_.Enabled; user_id = [string]$_.UserId }} }})
    actions = @($task.Actions | ForEach-Object {{ [ordered]@{{ execute = [string]$_.Execute; arguments = [string]$_.Arguments }} }})
  }}
}}
$stateFiles = @()
if (Test-Path -LiteralPath $state) {{
  $stateFiles = @(Get-ChildItem -LiteralPath $state -File | ForEach-Object {{
    $text = $(if ($_.Extension -in @('.cmd','.vbs','.ps1')) {{ [string](Get-Content -LiteralPath $_.FullName -Raw) }} else {{ '' }})
    [ordered]@{{
      name = [string]$_.Name
      bytes = [uint64]$_.Length
      sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
      script_contains_profile = ($text -match '(?i)xq-native-pilot')
      script_contains_private_root = $text.Contains($root)
      script_contains_token_material = ($text -match '(?i)(--token|OPENCLAW_GATEWAY_TOKEN\s*=)')
    }}
    $text = ''
  }})
}}
$value = [ordered]@{{
  schema = 'xq-openclaw-native-gateway-service-diagnostic-v1'
  tasks = $taskSummaries
  state_files = $stateFiles
  stdout_exists = (Test-Path -LiteralPath $out)
  stderr_exists = (Test-Path -LiteralPath $err)
  port_18789_listener_count = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue).Count
}}
$json = $value | ConvertTo-Json -Depth 8 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 180)
        for key, guest_name, host_name in (
            ("stdout_exists", "gateway-service-install.stdout.log", "guest-gateway-service-install.stdout.log"),
            ("stderr_exists", "gateway-service-install.stderr.log", "guest-gateway-service-install.stderr.log"),
        ):
            if value.get(key):
                copy_from_guest_file(
                    guest_session,
                    GUEST_ROOT + "\\" + guest_name,
                    HOST_ROOT / host_name,
                )
        output = HOST_ROOT / "guest-native-gateway-service-diagnostic.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def gateway_service_check(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$profile = 'xq-native-pilot'
$env:Path = $nodeRoot + ';' + $env:Path
$started = $false
$listeners = @()
$deadline = [DateTime]::UtcNow.AddSeconds(120)
while ([DateTime]::UtcNow -lt $deadline) {{
  $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
  if ($listeners.Count -gt 0) {{ $started = $true; break }}
  Start-Sleep -Milliseconds 500
}}
$healthLines = @()
$healthExit = $null
$statusLines = @()
$statusExit = $null
if ($started) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $healthLines = @(& $node $cli --profile $profile health --json --timeout 15000 2>&1 | ForEach-Object {{ [string]$_ }})
    $healthExit = $LASTEXITCODE
    $statusLines = @(& $node $cli --profile $profile gateway status --json --require-rpc --timeout 15000 2>&1 | ForEach-Object {{ [string]$_ }})
    $statusExit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
}}
$listenerInfo = @($listeners | ForEach-Object {{ [ordered]@{{ address = [string]$_.LocalAddress; port = [int]$_.LocalPort; pid = [uint32]$_.OwningProcess }} }})
$loopbackOnly = ($listenerInfo.Count -gt 0) -and (@($listenerInfo | Where-Object {{ $_.address -notin @('127.0.0.1','::1') }}).Count -eq 0)
$tasks = @(Get-ScheduledTask | Where-Object TaskName -like 'OpenClaw Gateway*' | ForEach-Object {{
  $info = Get-ScheduledTaskInfo -TaskName $_.TaskName -TaskPath $_.TaskPath
  [ordered]@{{ name = [string]$_.TaskName; state = [string]$_.State; last_task_result = [int64]$info.LastTaskResult }}
}})
$processes = @(Get-CimInstance Win32_Process | Where-Object {{
  $_.Name -in @('node.exe','wscript.exe','cscript.exe','cmd.exe') -and ([string]$_.CommandLine) -match '(?i)(openclaw|gateway\.vbs|gateway\.cmd)'
}} | ForEach-Object {{
  $command = [string]$_.CommandLine
  [ordered]@{{
    pid = [uint32]$_.ProcessId
    name = [string]$_.Name
    executable_path = [string]$_.ExecutablePath
    contains_private_root = $command.Contains($root)
    contains_profile = ($command -match '(?i)xq-native-pilot')
    contains_token_material = ($command -match '(?i)(--token|OPENCLAW_GATEWAY_TOKEN)')
  }}
}})
$logFiles = @()
$logRoot = Join-Path $env:USERPROFILE '.openclaw-xq-native-pilot\logs'
if (Test-Path -LiteralPath $logRoot) {{
  $logFiles = @(Get-ChildItem -LiteralPath $logRoot -File -Recurse -ErrorAction SilentlyContinue | ForEach-Object {{
    [ordered]@{{ relative_path = $_.FullName.Substring($logRoot.Length).TrimStart('\'); bytes = [uint64]$_.Length; sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash; modified_utc = $_.LastWriteTimeUtc.ToString('o') }}
  }})
}}
$value = [ordered]@{{
  schema = 'xq-openclaw-native-gateway-service-check-v1'
  started = $started
  listeners = $listenerInfo
  loopback_only = $loopbackOnly
  health_exit = $healthExit
  health_output = $healthLines
  status_exit = $statusExit
  status_output = $statusLines
  tasks = $tasks
  processes = $processes
  log_files = $logFiles
}}
$json = $value | ConvertTo-Json -Depth 8 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 300)
        output = HOST_ROOT / "guest-native-gateway-service-check.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        if not value.get("started"):
            return 2
        if not value.get("loopback_only") or value.get("health_exit") != 0 or value.get("status_exit") != 0:
            return 3
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def gateway_service_restart(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$profile = 'xq-native-pilot'
$env:Path = $nodeRoot + ';' + $env:Path
$beforeListeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
if ($beforeListeners.Count -ne 1) {{ throw 'Expected exactly one Gateway listener before restart' }}
$beforePid = [uint32]$beforeListeners[0].OwningProcess
$previous = $ErrorActionPreference
try {{
  $ErrorActionPreference = 'Continue'
  $restartLines = @(& $node $cli --profile $profile gateway restart --json 2>&1 | ForEach-Object {{ [string]$_ }})
  $restartExit = $LASTEXITCODE
}} finally {{ $ErrorActionPreference = $previous }}
$started = $false
$listeners = @()
$deadline = [DateTime]::UtcNow.AddSeconds(120)
while ([DateTime]::UtcNow -lt $deadline) {{
  $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
  if ($listeners.Count -eq 1 -and [uint32]$listeners[0].OwningProcess -ne $beforePid) {{ $started = $true; break }}
  Start-Sleep -Milliseconds 500
}}
$healthLines = @()
$healthExit = $null
if ($started) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $healthLines = @(& $node $cli --profile $profile health --json --timeout 15000 2>&1 | ForEach-Object {{ [string]$_ }})
    $healthExit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
}}
$afterPid = $(if ($listeners.Count -eq 1) {{ [uint32]$listeners[0].OwningProcess }} else {{ $null }})
$listenerInfo = @($listeners | ForEach-Object {{ [ordered]@{{ address = [string]$_.LocalAddress; port = [int]$_.LocalPort; pid = [uint32]$_.OwningProcess }} }})
$loopbackOnly = ($listenerInfo.Count -eq 1) -and (@($listenerInfo | Where-Object {{ $_.address -notin @('127.0.0.1','::1') }}).Count -eq 0)
$task = Get-ScheduledTask | Where-Object TaskName -eq 'OpenClaw Gateway (xq-native-pilot)'
$value = [ordered]@{{
  schema = 'xq-openclaw-native-gateway-restart-v1'
  restart_exit = $restartExit
  restart_output = $restartLines
  before_pid = $beforePid
  after_pid = $afterPid
  pid_changed = ($null -ne $afterPid -and $afterPid -ne $beforePid)
  started = $started
  listeners = $listenerInfo
  loopback_only = $loopbackOnly
  health_exit = $healthExit
  health_output = $healthLines
  task_state = [string]$task.State
}}
$json = $value | ConvertTo-Json -Depth 7 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 300)
        output = HOST_ROOT / "guest-native-gateway-restart.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        if value.get("restart_exit") != 0 or not value.get("pid_changed"):
            return 2
        if not value.get("loopback_only") or value.get("health_exit") != 0:
            return 3
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def gateway_fault_recovery(control) -> int:
    """Stop the installed service, capture read-only diagnostics, and recover it."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$profile = 'xq-native-pilot'
$diagPath = Join-Path $root 'diagnostics-stopped.zip'
$env:Path = $nodeRoot + ';' + $env:Path
function Probe([string]$name, [string[]]$arguments) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $lines = @(& $node $cli @arguments 2>&1 | ForEach-Object {{ [string]$_ }})
    $exit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
  return [ordered]@{{ name = $name; arguments = $arguments; exit = $exit; output = $lines }}
}}
function TaskSnapshot {{
  return @(Get-ScheduledTask | Where-Object TaskName -like 'OpenClaw Gateway*' | ForEach-Object {{
    $info = Get-ScheduledTaskInfo -TaskName $_.TaskName -TaskPath $_.TaskPath
    [ordered]@{{ name = [string]$_.TaskName; state = [string]$_.State; last_task_result = [int64]$info.LastTaskResult }}
  }})
}}
$before = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
if ($before.Count -ne 1) {{ throw 'Expected exactly one Gateway listener before fault injection' }}
$beforePid = [uint32]$before[0].OwningProcess
if (Test-Path -LiteralPath $diagPath) {{ Remove-Item -LiteralPath $diagPath -Force }}
$stop = Probe 'gateway-stop' @('--profile',$profile,'gateway','stop','--force','--json')
$stopped = $false
$deadline = [DateTime]::UtcNow.AddSeconds(120)
while ([DateTime]::UtcNow -lt $deadline) {{
  if (@(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue).Count -eq 0) {{ $stopped = $true; break }}
  Start-Sleep -Milliseconds 500
}}
$taskStopped = TaskSnapshot
$stoppedListenerCount = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue).Count
$stoppedProbes = @()
$stoppedProbes += Probe 'health-stopped' @('--profile',$profile,'health','--json','--timeout','5000')
$stoppedProbes += Probe 'gateway-status-stopped' @('--profile',$profile,'gateway','status','--json','--require-rpc','--timeout','5000')
$stoppedProbes += Probe 'doctor-stopped' @('--profile',$profile,'doctor','--json')
$stoppedProbes += Probe 'doctor-lint-stopped' @('--profile',$profile,'doctor','--lint','--json')
$stoppedProbes += Probe 'status-all-stopped' @('--profile',$profile,'status','--all','--json','--timeout','5000')
$stoppedProbes += Probe 'gateway-stability-stopped' @('--profile',$profile,'gateway','stability','--json','--timeout','5000')
$stoppedProbes += Probe 'diagnostics-export-stopped' @('--profile',$profile,'gateway','diagnostics','export','--json','--timeout','3000','--output',$diagPath)
$diag = [ordered]@{{ exists = (Test-Path -LiteralPath $diagPath -PathType Leaf); bytes = $null; sha256 = $null }}
if ($diag.exists) {{
  $item = Get-Item -LiteralPath $diagPath
  $diag.bytes = [uint64]$item.Length
  $diag.sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $diagPath).Hash
}}
$start = Probe 'gateway-start' @('--profile',$profile,'gateway','start','--json')
$started = $false
$after = @()
$deadline = [DateTime]::UtcNow.AddSeconds(120)
while ([DateTime]::UtcNow -lt $deadline) {{
  $after = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
  if ($after.Count -eq 1) {{ $started = $true; break }}
  Start-Sleep -Milliseconds 500
}}
$afterInfo = @($after | ForEach-Object {{ [ordered]@{{ address = [string]$_.LocalAddress; port = [int]$_.LocalPort; pid = [uint32]$_.OwningProcess }} }})
$loopbackOnly = ($afterInfo.Count -eq 1) -and (@($afterInfo | Where-Object {{ $_.address -notin @('127.0.0.1','::1') }}).Count -eq 0)
$recoveredProbes = @()
if ($started) {{
  $recoveredProbes += Probe 'health-recovered' @('--profile',$profile,'health','--json','--timeout','15000')
  $recoveredProbes += Probe 'gateway-status-recovered' @('--profile',$profile,'gateway','status','--json','--require-rpc','--timeout','15000')
  $recoveredProbes += Probe 'gateway-stability-recovered' @('--profile',$profile,'gateway','stability','--json','--timeout','15000')
}}
$value = [ordered]@{{
  schema = 'xq-openclaw-native-fault-recovery-v1'
  before_pid = $beforePid
  stop = $stop
  stopped = $stopped
  stopped_listener_count = $stoppedListenerCount
  task_while_stopped = $taskStopped
  stopped_probes = $stoppedProbes
  diagnostics_export = $diag
  start = $start
  started = $started
  recovered_listeners = $afterInfo
  recovered_loopback_only = $loopbackOnly
  task_after_recovery = TaskSnapshot
  recovered_probes = $recoveredProbes
}}
$json = $value | ConvertTo-Json -Depth 10 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 600)
        if isinstance(value.get("diagnostics_export"), dict) and value["diagnostics_export"].get("exists"):
            copy_from_guest_file(
                guest_session,
                GUEST_ROOT + r"\diagnostics-stopped.zip",
                HOST_ROOT / "guest-native-diagnostics-stopped.zip",
            )
        output = HOST_ROOT / "guest-native-fault-recovery.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        stop = value.get("stop") if isinstance(value.get("stop"), dict) else {}
        start = value.get("start") if isinstance(value.get("start"), dict) else {}
        stopped = value.get("stopped_probes") if isinstance(value.get("stopped_probes"), list) else []
        recovered = value.get("recovered_probes") if isinstance(value.get("recovered_probes"), list) else []
        stopped_by_name = {item.get("name"): item for item in stopped if isinstance(item, dict)}
        recovered_by_name = {item.get("name"): item for item in recovered if isinstance(item, dict)}
        if stop.get("exit") != 0 or not value.get("stopped"):
            return 2
        if stopped_by_name.get("health-stopped", {}).get("exit") == 0:
            return 3
        if stopped_by_name.get("gateway-status-stopped", {}).get("exit") == 0:
            return 4
        if not isinstance(value.get("diagnostics_export"), dict) or not value["diagnostics_export"].get("exists"):
            return 5
        if start.get("exit") != 0 or not value.get("started") or not value.get("recovered_loopback_only"):
            return 6
        if recovered_by_name.get("health-recovered", {}).get("exit") != 0:
            return 7
        if recovered_by_name.get("gateway-status-recovered", {}).get("exit") != 0:
            return 8
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def production_diagnostic_regression(control) -> int:
    """Run the packaged production diagnostic in the authenticated native profile."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    run_id = time.strftime("%Y%m%d-%H%M%S")
    guest_package_root = GUEST_ROOT + rf"\support-regression\{run_id}\XQ-OpenClaw-Windows"
    guest_missing_root = GUEST_ROOT + rf"\support-regression\{run_id}\missing-policy\XQ-OpenClaw-Windows"
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        prep = rf"""
$ErrorActionPreference = 'Stop'
$paths = @(
  '{guest_package_root}\tools',
  '{guest_package_root}\policy',
  '{guest_missing_root}\tools'
)
foreach ($path in $paths) {{ [void](New-Item -ItemType Directory -Path $path -Force) }}
$value = [ordered]@{{ schema = 'xq-production-diagnostic-stage-v1'; paths = $paths }}
$json = $value | ConvertTo-Json -Depth 4 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        run_ps_json(guest_session, prep, 120)

        diagnostic_script = PRODUCTION_DIAGNOSTIC_ROOT / "tools" / "OpenClaw-XQ-Diagnose.ps1"
        redaction_module = PRODUCTION_DIAGNOSTIC_ROOT / "tools" / "XQ.Redaction.psm1"
        policy_file = PRODUCTION_DIAGNOSTIC_ROOT / "policy" / "diagnostic-command-policy.json"
        for source in (diagnostic_script, redaction_module, policy_file):
            if not source.is_file():
                raise FileNotFoundError(source)

        copy_to_guest(
            guest_session,
            diagnostic_script,
            guest_package_root + r"\tools\OpenClaw-XQ-Diagnose.ps1",
        )
        copy_to_guest(
            guest_session,
            redaction_module,
            guest_package_root + r"\tools\XQ.Redaction.psm1",
        )
        copy_to_guest(
            guest_session,
            policy_file,
            guest_package_root + r"\policy\diagnostic-command-policy.json",
        )
        copy_to_guest(
            guest_session,
            diagnostic_script,
            guest_missing_root + r"\tools\OpenClaw-XQ-Diagnose.ps1",
        )
        copy_to_guest(
            guest_session,
            redaction_module,
            guest_missing_root + r"\tools\XQ.Redaction.psm1",
        )

        expected_hashes_json = json.dumps(PRODUCTION_DIAGNOSTIC_HASHES, separators=(",", ":"))
        script = rf"""
$ErrorActionPreference = 'Stop'
$packageRoot = '{guest_package_root}'
$missingRoot = '{guest_missing_root}'
$successReports = Join-Path $packageRoot 'reports'
$missingReports = Join-Path $missingRoot 'reports'
$nodeRoot = Join-Path '{GUEST_ROOT}' 'node'
$npmRoot = Join-Path '{GUEST_ROOT}' 'npm'
$env:Path = $nodeRoot + ';' + $npmRoot + ';' + $env:Path
$env:OPENCLAW_PROFILE = 'xq-native-pilot'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

function Invoke-Diagnostic([string]$ScriptPath,[string]$OutputDirectory) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $lines = @(& $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $ScriptPath -OutputDirectory $OutputDirectory -TimeoutSeconds 20 2>&1 | ForEach-Object {{ [string]$_ }})
    $exit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
  return [ordered]@{{ exit = $exit; output = $lines }}
}}

$success = Invoke-Diagnostic (Join-Path $packageRoot 'tools\OpenClaw-XQ-Diagnose.ps1') $successReports
$missing = Invoke-Diagnostic (Join-Path $missingRoot 'tools\OpenClaw-XQ-Diagnose.ps1') $missingReports
$reportFiles = @()
if (Test-Path -LiteralPath $successReports) {{
  $reportFiles = @(Get-ChildItem -LiteralPath $successReports -File | Sort-Object Name)
}}
$secretPattern = '(?i)\bsk-(?:proj-)?[A-Za-z0-9_-]{{12,}}\b'
$marker = 'OC-NATIVE-DEEPSEEK-7391'
$secretMatches = @()
$markerMatches = @()
$reportMetadata = @()
$nativeStatus = $null
$nativeVersion = $null
$nativeCommandSummary = @()
foreach ($file in $reportFiles) {{
  $text = [IO.File]::ReadAllText($file.FullName)
  if ([regex]::IsMatch($text,$secretPattern)) {{ $secretMatches += $file.Name }}
  if ($text.Contains($marker)) {{ $markerMatches += $file.Name }}
  $reportMetadata += [ordered]@{{
    name = $file.Name
    path = $file.FullName
    bytes = [uint64]$file.Length
    sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash
  }}
  if ($file.Extension -eq '.json') {{
    try {{
      $parsed = $text | ConvertFrom-Json
      $nativeStatus = [string]$parsed.NativeCli.Status
      $nativeVersion = [string]$parsed.NativeCli.Version
      $nativeCommandSummary = @($parsed.CommandResults | Where-Object Component -eq 'NativeCliAdapter' | ForEach-Object {{
        [ordered]@{{ command = [string]$_.Command; status = [string]$_.Status; exit_code = $_.ExitCode }}
      }})
    }} catch {{}}
  }}
  if ($file.Extension -eq '.txt') {{
    $nativeMatch = [regex]::Match($text, '(?ms)\[Windows 原生 CLI（附加路径）\]\s*\r?\n\s*Status：([^\r\n]+).*?\r?\n\s*Version：([^\r\n]+)')
    if ($nativeMatch.Success) {{
      $nativeStatus = $nativeMatch.Groups[1].Value.Trim()
      $nativeVersion = $nativeMatch.Groups[2].Value.Trim()
    }}
  }}
}}
$missingReportCount = $(if (Test-Path -LiteralPath $missingReports) {{ @(Get-ChildItem -LiteralPath $missingReports -File -ErrorAction SilentlyContinue).Count }} else {{ 0 }})
$expectedHashes = '{expected_hashes_json}' | ConvertFrom-Json
$actualHashes = [ordered]@{{
  'OpenClaw-XQ-Diagnose.ps1' = [string](Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $packageRoot 'tools\OpenClaw-XQ-Diagnose.ps1')).Hash
  'XQ.Redaction.psm1' = [string](Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $packageRoot 'tools\XQ.Redaction.psm1')).Hash
  'diagnostic-command-policy.json' = [string](Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $packageRoot 'policy\diagnostic-command-policy.json')).Hash
}}
$hashesMatch = $true
foreach ($property in $expectedHashes.PSObject.Properties) {{
  if ([string]$actualHashes[$property.Name] -ne [string]$property.Value) {{ $hashesMatch = $false }}
}}
$value = [ordered]@{{
  schema = 'xq-production-diagnostic-vm-regression-v1'
  package_root = $packageRoot
  source_hashes = $actualHashes
  source_hashes_match = $hashesMatch
  success_exit = $success.exit
  success_output = $success.output
  report_count = $reportFiles.Count
  reports = $reportMetadata
  native_status = $nativeStatus
  native_version = $nativeVersion
  native_commands = $nativeCommandSummary
  secret_match_count = $secretMatches.Count
  secret_match_files = $secretMatches
  conversation_marker_match_count = $markerMatches.Count
  conversation_marker_match_files = $markerMatches
  missing_policy_exit = $missing.exit
  missing_policy_output = $missing.output
  missing_policy_report_count = $missingReportCount
}}
$json = $value | ConvertTo-Json -Depth 10 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 300)
        clean = value.get("secret_match_count") == 0 and value.get("conversation_marker_match_count") == 0
        if clean:
            reports = value.get("reports") if isinstance(value.get("reports"), list) else []
            for item in reports:
                if not isinstance(item, dict):
                    continue
                guest_path = item.get("path")
                name = item.get("name")
                if not isinstance(guest_path, str) or not isinstance(name, str):
                    continue
                suffix = Path(name).suffix.lower()
                if suffix in (".json", ".txt"):
                    copy_from_guest_file(
                        guest_session,
                        guest_path,
                        HOST_ROOT / f"guest-production-diagnostic-report{suffix}",
                    )
        output = HOST_ROOT / "guest-production-diagnostic-regression.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        if not value.get("source_hashes_match"):
            return 2
        if value.get("success_exit") != 0 or int(value.get("report_count", 0)) < 1:
            return 3
        native_version = value.get("native_version")
        if value.get("native_status") != "Detected" or not isinstance(native_version, str) or OPENCLAW_VERSION not in native_version:
            return 4
        if not clean:
            return 5
        if value.get("missing_policy_exit") == 0 or value.get("missing_policy_report_count") != 0:
            return 6
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def uninstall_validation(control) -> int:
    """Exercise official profile cleanup, then remove the dedicated npm CLI install."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$npmCli = Join-Path $nodeRoot 'node_modules\npm\bin\npm-cli.js'
$prefix = Join-Path $root 'npm'
$cli = Join-Path $prefix 'node_modules\openclaw\openclaw.mjs'
$packageJson = Join-Path $prefix 'node_modules\openclaw\package.json'
$profile = 'xq-native-pilot'
$state = Join-Path $env:USERPROFILE '.openclaw-xq-native-pilot'
$workspace = Join-Path $root 'workspace'
$taskName = 'OpenClaw Gateway (xq-native-pilot)'
$env:Path = $nodeRoot + ';' + $prefix + ';' + $env:Path
$env:npm_config_cache = Join-Path $root 'npm-cache'
$env:npm_config_userconfig = Join-Path $root 'npmrc'
$env:npm_config_update_notifier = 'false'
$env:npm_config_audit = 'false'
$env:npm_config_fund = 'false'

function Run-Process([string]$Name,[string]$Executable,[string[]]$Arguments) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $lines = @(& $Executable @Arguments 2>&1 | ForEach-Object {{ [string]$_ }})
    $exit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
  return [ordered]@{{ name = $Name; arguments = $Arguments; exit = $exit; output = $lines }}
}}

if (-not (Test-Path -LiteralPath $node -PathType Leaf)) {{ throw 'Dedicated Node runtime is missing before uninstall validation' }}
if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {{ throw 'OpenClaw CLI is missing before uninstall validation' }}
$beforeListeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
$beforeTasks = @(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)
$beforeVersion = $(if (Test-Path -LiteralPath $packageJson) {{ [string](Get-Content -LiteralPath $packageJson -Raw | ConvertFrom-Json).version }} else {{ $null }})
$before = [ordered]@{{
  version = $beforeVersion
  listener_count = $beforeListeners.Count
  task_count = $beforeTasks.Count
  state_exists = (Test-Path -LiteralPath $state)
  workspace_exists = (Test-Path -LiteralPath $workspace)
  default_state_exists = (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.openclaw'))
  cli_exists = (Test-Path -LiteralPath $cli -PathType Leaf)
}}
$scopes = @('--profile',$profile,'uninstall','--service','--state','--workspace','--yes','--non-interactive')
$dryRun = Run-Process 'openclaw-uninstall-dry-run' $node (@($cli) + $scopes + @('--dry-run'))
$actual = $null
if ($dryRun.exit -eq 0) {{ $actual = Run-Process 'openclaw-uninstall' $node (@($cli) + $scopes) }}
$stopped = $false
$deadline = [DateTime]::UtcNow.AddSeconds(120)
while ([DateTime]::UtcNow -lt $deadline) {{
  if (@(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue).Count -eq 0) {{ $stopped = $true; break }}
  Start-Sleep -Milliseconds 500
}}
$npmRemove = $null
if ($null -ne $actual -and $actual.exit -eq 0) {{
  $npmRemove = Run-Process 'npm-remove-global-openclaw' $node @($npmCli,'remove','--global','--prefix',$prefix,'openclaw','--no-audit','--no-fund')
}}
$afterListeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
$afterTasks = @(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)
$matchingProcesses = @(Get-CimInstance Win32_Process | Where-Object {{
  $_.Name -in @('node.exe','wscript.exe','cscript.exe','cmd.exe') -and ([string]$_.CommandLine) -match '(?i)(openclaw|gateway\.vbs|gateway\.cmd)' -and ([string]$_.CommandLine).Contains($root)
}} | ForEach-Object {{ [ordered]@{{ pid = [uint32]$_.ProcessId; name = [string]$_.Name }} }})
$shimPaths = @(
  (Join-Path $prefix 'openclaw'),
  (Join-Path $prefix 'openclaw.cmd'),
  (Join-Path $prefix 'openclaw.ps1')
)
$after = [ordered]@{{
  listener_count = $afterListeners.Count
  task_count = $afterTasks.Count
  state_exists = (Test-Path -LiteralPath $state)
  workspace_exists = (Test-Path -LiteralPath $workspace)
  default_state_exists = (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.openclaw'))
  cli_exists = (Test-Path -LiteralPath $cli -PathType Leaf)
  package_exists = (Test-Path -LiteralPath $packageJson -PathType Leaf)
  shim_exists = @($shimPaths | Where-Object {{ Test-Path -LiteralPath $_ }}).Count -gt 0
  dedicated_node_exists = (Test-Path -LiteralPath $node -PathType Leaf)
  matching_processes = $matchingProcesses
}}
$value = [ordered]@{{
  schema = 'xq-openclaw-native-uninstall-validation-v1'
  before = $before
  dry_run = $dryRun
  actual = $actual
  listener_stopped = $stopped
  npm_remove = $npmRemove
  after = $after
}}
$json = $value | ConvertTo-Json -Depth 10 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 600)
        output = HOST_ROOT / "guest-native-uninstall-validation.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        dry_run = value.get("dry_run") if isinstance(value.get("dry_run"), dict) else {}
        actual = value.get("actual") if isinstance(value.get("actual"), dict) else {}
        npm_remove = value.get("npm_remove") if isinstance(value.get("npm_remove"), dict) else {}
        after = value.get("after") if isinstance(value.get("after"), dict) else {}
        if dry_run.get("exit") != 0:
            return 2
        if actual.get("exit") != 0 or not value.get("listener_stopped"):
            return 3
        if npm_remove.get("exit") != 0:
            return 4
        if after.get("listener_count") != 0 or after.get("task_count") != 0:
            return 5
        if after.get("state_exists") or after.get("workspace_exists"):
            return 6
        if after.get("cli_exists") or after.get("package_exists") or after.get("shim_exists"):
            return 7
        if after.get("matching_processes"):
            return 8
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def model_readiness(control) -> int:
    """Capture model/plugin readiness without starting auth or a billed request."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$profile = 'xq-native-pilot'
$env:Path = $nodeRoot + ';' + $env:Path
function Probe([string]$name, [string[]]$arguments) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $lines = @(& $node $cli @arguments 2>&1 | ForEach-Object {{ [string]$_ }})
    $exit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
  return [ordered]@{{ name = $name; arguments = $arguments; exit = $exit; output = $lines }}
}}
$probes = @()
$probes += Probe 'models-status-check' @('--profile',$profile,'models','status','--json','--check')
$probes += Probe 'openai-auth-list' @('--profile',$profile,'models','auth','list','--provider','openai','--json')
$probes += Probe 'openai-model-list' @('--profile',$profile,'models','list','--provider','openai','--json')
$probes += Probe 'plugins-list' @('--profile',$profile,'plugins','list','--json')
$probes += Probe 'default-model' @('--profile',$profile,'config','get','agents.defaults.model','--json')
$value = [ordered]@{{ schema = 'xq-openclaw-native-model-readiness-v1'; probes = $probes }}
$json = $value | ConvertTo-Json -Depth 10 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 300)
        output = HOST_ROOT / "guest-native-model-readiness.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def deepseek_readiness(control) -> int:
    """Verify DeepSeek readiness while emitting only redacted CLI/config summaries."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$profile = 'xq-native-pilot'
$env:Path = $nodeRoot + ';' + $env:Path
function Probe([string]$name, [string[]]$arguments) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $lines = @(& $node $cli @arguments 2>&1 | ForEach-Object {{
      ([string]$_) -replace 'sk-[^\s"]+','<redacted>'
    }})
    $exit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
  return [ordered]@{{ name = $name; arguments = $arguments; exit = $exit; output = $lines }}
}}
$probes = @()
$probes += Probe 'models-status-check' @('--profile',$profile,'models','status','--json','--check')
$probes += Probe 'deepseek-auth-list' @('--profile',$profile,'models','auth','list','--provider','deepseek','--json')
$probes += Probe 'deepseek-model-list' @('--profile',$profile,'models','list','--provider','deepseek','--json')
$probes += Probe 'deepseek-plugin-inspect' @('--profile',$profile,'plugins','inspect','deepseek','--runtime','--json')
$probes += Probe 'config-validate' @('--profile',$profile,'config','validate')
$probes += Probe 'default-model' @('--profile',$profile,'config','get','agents.defaults.model','--json')
$probes += Probe 'plugin-allowlist' @('--profile',$profile,'config','get','plugins.allow','--json')
$probes += Probe 'deepseek-provider-redacted' @('--profile',$profile,'config','get','models.providers.deepseek','--json')
$value = [ordered]@{{ schema = 'xq-openclaw-native-deepseek-readiness-v1'; probes = $probes }}
$json = $value | ConvertTo-Json -Depth 14 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 600)
        output = HOST_ROOT / "guest-native-deepseek-readiness.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        probes = value.get("probes") if isinstance(value.get("probes"), list) else []
        by_name = {item.get("name"): item for item in probes if isinstance(item, dict)}
        required = (
            "models-status-check",
            "deepseek-auth-list",
            "deepseek-model-list",
            "deepseek-plugin-inspect",
            "config-validate",
            "default-model",
            "plugin-allowlist",
            "deepseek-provider-redacted",
        )
        if any(by_name.get(name, {}).get("exit") != 0 for name in required):
            return 2
        rendered = json.dumps(value, ensure_ascii=False)
        if "deepseek/deepseek-v4-pro" not in rendered:
            return 3
        if '"deepseek"' not in rendered:
            return 4
        if "sk-" in rendered:
            return 5
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def deepseek_live_conversation(control) -> int:
    """Run one Gateway-backed DeepSeek turn without any channel delivery."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    marker = "OC-NATIVE-DEEPSEEK-7391"
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$profile = 'xq-native-pilot'
$env:Path = $nodeRoot + ';' + $env:Path
$prompt = 'This is an automated local connectivity test. Reply with exactly {marker}. Do not use tools or add other text.'
$previous = $ErrorActionPreference
try {{
  $ErrorActionPreference = 'Continue'
  $lines = @(& $node $cli '--profile' $profile 'agent' '--agent' 'main' '--session-key' 'pilot-deepseek-7391' '--model' '{DEEPSEEK_DEFAULT_MODEL}' '--thinking' 'off' '--message' $prompt '--timeout' '180' '--json' 2>&1 | ForEach-Object {{
    ([string]$_) -replace 'sk-[^\s"]+','<redacted>'
  }})
  $exit = $LASTEXITCODE
}} finally {{ $ErrorActionPreference = $previous }}
$joined = $lines -join "`n"
$value = [ordered]@{{
  schema = 'xq-openclaw-native-deepseek-conversation-v1'
  exit = $exit
  marker = '{marker}'
  expected_model = '{DEEPSEEK_DEFAULT_MODEL}'
  delivered = $false
  output = $lines
  marker_present = $joined.Contains('{marker}')
  provider_present = $joined.Contains('deepseek')
  contains_secret_pattern = [bool]($joined -match 'sk-')
}}
$json = $value | ConvertTo-Json -Depth 14 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 300)
        output = HOST_ROOT / "guest-native-deepseek-conversation.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        if value.get("exit") != 0:
            return 2
        if not value.get("marker_present") or not value.get("provider_present"):
            return 3
        if value.get("delivered") or value.get("contains_secret_pattern"):
            return 4
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def enable_codex_only(control) -> int:
    """Allow only the bundled Codex plugin needed for OpenAI subscription auth."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$profile = 'xq-native-pilot'
$env:Path = $nodeRoot + ';' + $env:Path
function Probe([string]$name, [string[]]$arguments) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $lines = @(& $node $cli @arguments 2>&1 | ForEach-Object {{ [string]$_ }})
    $exit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
  return [ordered]@{{ name = $name; arguments = $arguments; exit = $exit; output = $lines }}
}}
$patchPath = Join-Path $root '.codex-only-config-patch.json'
$operations = @()
$operations += Probe 'enable-plugin-system' @('--profile',$profile,'config','set','plugins.enabled','true','--strict-json')
$patchJson = '{{"plugins":{{"allow":["codex"]}}}}'
[IO.File]::WriteAllText($patchPath, $patchJson, [Text.UTF8Encoding]::new($false))
$operations += Probe 'allow-codex-only' @('--profile',$profile,'config','patch','--file',$patchPath)
if (Test-Path -LiteralPath $patchPath) {{ Remove-Item -LiteralPath $patchPath -Force }}
$operations += Probe 'enable-codex-entry' @('--profile',$profile,'config','set','plugins.entries.codex.enabled','true','--strict-json')
$operations += Probe 'set-default-model' @('--profile',$profile,'config','set','agents.defaults.model.primary','openai/gpt-5.6-sol')
$validate = Probe 'config-validate' @('--profile',$profile,'config','validate')
$restart = Probe 'gateway-restart' @('--profile',$profile,'gateway','restart','--json')
$ready = $false
$listeners = @()
$deadline = [DateTime]::UtcNow.AddSeconds(120)
while ([DateTime]::UtcNow -lt $deadline) {{
  $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
  if ($listeners.Count -eq 1) {{ $ready = $true; break }}
  Start-Sleep -Milliseconds 500
}}
$health = $(if ($ready) {{ Probe 'health' @('--profile',$profile,'health','--json','--timeout','15000') }} else {{ $null }})
$config = Get-Content -LiteralPath (Join-Path $env:USERPROFILE '.openclaw-xq-native-pilot\openclaw.json') -Raw | ConvertFrom-Json
$pluginSummary = Probe 'plugins-list' @('--profile',$profile,'plugins','list','--json')
$value = [ordered]@{{
  schema = 'xq-openclaw-native-codex-only-v1'
  operations = $operations
  validate = $validate
  restart = $restart
  ready = $ready
  health = $health
  listener_addresses = @($listeners | ForEach-Object {{ [string]$_.LocalAddress }})
  plugins_enabled = [bool]$config.plugins.enabled
  plugins_allow = @($config.plugins.allow)
  codex_entry_enabled = [bool]$config.plugins.entries.codex.enabled
  default_model = [string]$config.agents.defaults.model.primary
  plugin_list = $pluginSummary
}}
$json = $value | ConvertTo-Json -Depth 10 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 600)
        output = HOST_ROOT / "guest-native-codex-only.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        operations = value.get("operations") if isinstance(value.get("operations"), list) else []
        if any(item.get("exit") != 0 for item in operations if isinstance(item, dict)):
            return 2
        validate = value.get("validate") if isinstance(value.get("validate"), dict) else {}
        restart = value.get("restart") if isinstance(value.get("restart"), dict) else {}
        health = value.get("health") if isinstance(value.get("health"), dict) else {}
        if validate.get("exit") != 0 or restart.get("exit") != 0:
            return 3
        if not value.get("ready") or health.get("exit") != 0:
            return 4
        if value.get("plugins_allow") != ["codex"] or not value.get("codex_entry_enabled"):
            return 5
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def enable_openai_codex_only(control) -> int:
    """Allow only the bundled OpenAI provider plus the required Codex harness."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$profile = 'xq-native-pilot'
$patchPath = Join-Path $root '.openai-codex-only-config-patch.json'
$env:Path = $nodeRoot + ';' + $env:Path
function Probe([string]$name, [string[]]$arguments) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $lines = @(& $node $cli @arguments 2>&1 | ForEach-Object {{ [string]$_ }})
    $exit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
  return [ordered]@{{ name = $name; exit = $exit; output = $lines }}
}}
$patchJson = '{{"plugins":{{"allow":["codex","openai"]}}}}'
[IO.File]::WriteAllText($patchPath, $patchJson, [Text.UTF8Encoding]::new($false))
$patch = Probe 'allow-openai-codex-only' @('--profile',$profile,'config','patch','--file',$patchPath)
if (Test-Path -LiteralPath $patchPath) {{ Remove-Item -LiteralPath $patchPath -Force }}
$validate = Probe 'config-validate' @('--profile',$profile,'config','validate')
$restart = Probe 'gateway-restart' @('--profile',$profile,'gateway','restart','--json')
$ready = $false
$listeners = @()
$deadline = [DateTime]::UtcNow.AddSeconds(120)
while ([DateTime]::UtcNow -lt $deadline) {{
  $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
  if ($listeners.Count -eq 1) {{ $ready = $true; break }}
  Start-Sleep -Milliseconds 500
}}
$health = $(if ($ready) {{ Probe 'health' @('--profile',$profile,'health','--json','--timeout','15000') }} else {{ $null }})
$config = Get-Content -LiteralPath (Join-Path $env:USERPROFILE '.openclaw-xq-native-pilot\openclaw.json') -Raw | ConvertFrom-Json
$value = [ordered]@{{
  schema = 'xq-openclaw-native-openai-codex-only-v1'
  patch = $patch
  validate = $validate
  restart = $restart
  ready = $ready
  health = $health
  listener_addresses = @($listeners | ForEach-Object {{ [string]$_.LocalAddress }})
  plugins_allow = @($config.plugins.allow)
}}
$json = $value | ConvertTo-Json -Depth 8 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 600)
        output = HOST_ROOT / "guest-native-openai-codex-only.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        patch_result = value.get("patch") if isinstance(value.get("patch"), dict) else {}
        validate = value.get("validate") if isinstance(value.get("validate"), dict) else {}
        restart = value.get("restart") if isinstance(value.get("restart"), dict) else {}
        health = value.get("health") if isinstance(value.get("health"), dict) else {}
        if patch_result.get("exit") != 0 or validate.get("exit") != 0 or restart.get("exit") != 0:
            return 2
        if not value.get("ready") or health.get("exit") != 0:
            return 3
        if value.get("plugins_allow") != ["codex", "openai"]:
            return 4
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def enable_deepseek_only(control) -> int:
    """Install the exact official DeepSeek provider and make it the only allowed provider."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$nodeRoot = Join-Path $root 'node'
$node = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$profile = 'xq-native-pilot'
$patchPath = Join-Path $root '.deepseek-only-config-patch.json'
$env:Path = $nodeRoot + ';' + $env:Path
function Probe([string]$name, [string[]]$arguments) {{
  $previous = $ErrorActionPreference
  try {{
    $ErrorActionPreference = 'Continue'
    $lines = @(& $node $cli @arguments 2>&1 | ForEach-Object {{ [string]$_ }})
    $exit = $LASTEXITCODE
  }} finally {{ $ErrorActionPreference = $previous }}
  return [ordered]@{{ name = $name; arguments = $arguments; exit = $exit; output = $lines }}
}}
$operations = @()
$installed = Probe 'deepseek-install-precheck' @('--profile',$profile,'plugins','inspect','deepseek','--json')
if ($installed.exit -eq 0) {{
  $operations += $installed
}} else {{
  $operations += Probe 'install-deepseek-exact' @('--profile',$profile,'plugins','install','{DEEPSEEK_PLUGIN_SPEC}','--pin')
}}
$patchJson = '{{"plugins":{{"enabled":true,"allow":["deepseek"],"entries":{{"codex":{{"enabled":false}},"openai":{{"enabled":false}},"deepseek":{{"enabled":true}}}}}}}}'
[IO.File]::WriteAllText($patchPath, $patchJson, [Text.UTF8Encoding]::new($false))
$operations += Probe 'allow-deepseek-only' @('--profile',$profile,'config','patch','--file',$patchPath)
if (Test-Path -LiteralPath $patchPath) {{ Remove-Item -LiteralPath $patchPath -Force }}
$operations += Probe 'set-default-model' @('--profile',$profile,'models','set','{DEEPSEEK_DEFAULT_MODEL}')
$validate = Probe 'config-validate' @('--profile',$profile,'config','validate')
$restart = Probe 'gateway-restart' @('--profile',$profile,'gateway','restart','--json')
$ready = $false
$listeners = @()
$deadline = [DateTime]::UtcNow.AddSeconds(120)
while ([DateTime]::UtcNow -lt $deadline) {{
  $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
  if ($listeners.Count -eq 1) {{ $ready = $true; break }}
  Start-Sleep -Milliseconds 500
}}
$health = $(if ($ready) {{ Probe 'health' @('--profile',$profile,'health','--json','--timeout','15000') }} else {{ $null }})
$inspect = Probe 'deepseek-inspect' @('--profile',$profile,'plugins','inspect','deepseek','--runtime','--json')
$models = Probe 'deepseek-model-list' @('--profile',$profile,'models','list','--all','--provider','deepseek','--json')
$auth = Probe 'deepseek-auth-list' @('--profile',$profile,'models','auth','list','--provider','deepseek','--json')
$config = Get-Content -LiteralPath (Join-Path $env:USERPROFILE '.openclaw-xq-native-pilot\openclaw.json') -Raw | ConvertFrom-Json
$value = [ordered]@{{
  schema = 'xq-openclaw-native-deepseek-only-v1'
  operations = $operations
  validate = $validate
  restart = $restart
  ready = $ready
  health = $health
  listener_addresses = @($listeners | ForEach-Object {{ [string]$_.LocalAddress }})
  plugins_allow = @($config.plugins.allow)
  codex_entry_enabled = [bool]$config.plugins.entries.codex.enabled
  openai_entry_enabled = [bool]$config.plugins.entries.openai.enabled
  deepseek_entry_enabled = [bool]$config.plugins.entries.deepseek.enabled
  default_model = [string]$config.agents.defaults.model.primary
  inspect = $inspect
  models = $models
  auth = $auth
}}
$json = $value | ConvertTo-Json -Depth 12 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 1_200)
        output = HOST_ROOT / "guest-native-deepseek-only.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        operations = value.get("operations") if isinstance(value.get("operations"), list) else []
        if any(item.get("exit") != 0 for item in operations if isinstance(item, dict)):
            return 2
        validate = value.get("validate") if isinstance(value.get("validate"), dict) else {}
        restart = value.get("restart") if isinstance(value.get("restart"), dict) else {}
        health = value.get("health") if isinstance(value.get("health"), dict) else {}
        inspect = value.get("inspect") if isinstance(value.get("inspect"), dict) else {}
        models = value.get("models") if isinstance(value.get("models"), dict) else {}
        if validate.get("exit") != 0 or restart.get("exit") != 0:
            return 3
        if not value.get("ready") or health.get("exit") != 0:
            return 4
        if value.get("plugins_allow") != ["deepseek"]:
            return 5
        if value.get("codex_entry_enabled") or value.get("openai_entry_enabled"):
            return 6
        if not value.get("deepseek_entry_enabled") or value.get("default_model") != DEEPSEEK_DEFAULT_MODEL:
            return 7
        if inspect.get("exit") != 0 or models.get("exit") != 0:
            return 8
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def prepare_auth_ui(control) -> int:
    """Place a credential-free launcher on the guest desktop for user OAuth."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('Desktop')
$path = Join-Path $desktop 'OpenClaw-Login.cmd'
$lines = @(
  '@echo off',
  'title OpenClaw OpenAI Secure Login',
  '"{GUEST_ROOT}\node\node.exe" "{GUEST_ROOT}\npm\node_modules\openclaw\openclaw.mjs" --profile xq-native-pilot models auth login --provider openai --device-code --set-default',
  'echo.',
  'echo Login flow finished. Keep this window open for verification.',
  'pause'
)
[IO.File]::WriteAllLines($path, $lines, [Text.Encoding]::ASCII)
$value = [ordered]@{{
  schema = 'xq-openclaw-native-auth-ui-v1'
  desktop = $desktop
  launcher_path = $path
  exists = (Test-Path -LiteralPath $path -PathType Leaf)
  sha256 = [string](Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
  contains_secret_material = ([IO.File]::ReadAllText($path) -match '(?i)(sk-[a-z0-9]|access_token|refresh_token|client_secret)')
}}
$json = $value | ConvertTo-Json -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 120)
        output = HOST_ROOT / "guest-native-auth-ui.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if value.get("exists") and not value.get("contains_secret_material") else 2
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def open_dashboard(control) -> int:
    """Open the tokenized local dashboard without exporting its URL or token."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = '{GUEST_ROOT}'
$node = Join-Path $root 'node\node.exe'
$cli = Join-Path $root 'npm\node_modules\openclaw\openclaw.mjs'
$stdout = Join-Path $root '.dashboard-open.stdout.tmp'
$stderr = Join-Path $root '.dashboard-open.stderr.tmp'
foreach ($path in @($stdout,$stderr)) {{ if (Test-Path -LiteralPath $path) {{ Remove-Item -LiteralPath $path -Force }} }}
$process = Start-Process -FilePath $node -ArgumentList @($cli,'--profile','xq-native-pilot','dashboard') -WorkingDirectory $env:USERPROFILE -RedirectStandardOutput $stdout -RedirectStandardError $stderr -Wait -PassThru -NoNewWindow
$exit = [int]$process.ExitCode
foreach ($path in @($stdout,$stderr)) {{ if (Test-Path -LiteralPath $path) {{ Remove-Item -LiteralPath $path -Force }} }}
Start-Sleep -Seconds 2
$browsers = @(Get-CimInstance Win32_Process | Where-Object {{ $_.Name -in @('msedge.exe','chrome.exe','firefox.exe') }} | ForEach-Object {{ [ordered]@{{ name = [string]$_.Name; pid = [uint32]$_.ProcessId }} }})
$value = [ordered]@{{
  schema = 'xq-openclaw-native-dashboard-open-v1'
  exit = $exit
  temp_output_deleted = (-not (Test-Path -LiteralPath $stdout)) -and (-not (Test-Path -LiteralPath $stderr))
  browser_process_count = $browsers.Count
  browsers = $browsers
}}
$json = $value | ConvertTo-Json -Depth 4 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 120)
        output = HOST_ROOT / "guest-native-dashboard-open.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if value.get("exit") == 0 and value.get("temp_output_deleted") else 2
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def restart_clipboard_integration(control) -> int:
    """Restart only the Guest Additions desktop-integration process in the test clone."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = r"""
$ErrorActionPreference = 'Stop'
$path = 'C:\Program Files\Oracle\VirtualBox Guest Additions\VBoxTray.exe'
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'VBoxTray.exe was not found' }
$before = @(Get-Process -Name VBoxTray -ErrorAction SilentlyContinue | ForEach-Object { [uint32]$_.Id })
Get-Process -Name VBoxTray -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 750
$process = Start-Process -FilePath $path -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
$after = @(Get-Process -Name VBoxTray -ErrorAction SilentlyContinue | ForEach-Object { [uint32]$_.Id })
$value = [ordered]@{
  schema = 'xq-openclaw-native-clipboard-integration-restart-v1'
  executable = $path
  before_pids = $before
  started_pid = [uint32]$process.Id
  after_pids = $after
  running = ($after.Count -ge 1)
}
$json = $value | ConvertTo-Json -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 60)
        output = HOST_ROOT / "guest-native-clipboard-integration-restart.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if value.get("running") else 2
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def open_settings_page(control) -> int:
    """Open the local dashboard settings route in the guest's existing Edge session."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = r"""
$ErrorActionPreference = 'Stop'
$candidates = @(
  'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
  'C:\Program Files\Microsoft\Edge\Application\msedge.exe'
)
$edge = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $edge) { throw 'Microsoft Edge executable was not found' }
$process = Start-Process -FilePath $edge -ArgumentList @('http://127.0.0.1:18789/settings') -PassThru
Start-Sleep -Seconds 2
$value = [ordered]@{ schema = 'xq-openclaw-native-settings-open-v1'; started = ($null -ne $process); process_id = [uint32]$process.Id; executable = $edge }
$json = $value | ConvertTo-Json -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 60)
        output = HOST_ROOT / "guest-native-settings-open.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if value.get("started") else 2
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def open_dashboard_root(control) -> int:
    """Open the local dashboard root in the guest's existing Edge session."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = r"""
$ErrorActionPreference = 'Stop'
$candidates = @(
  'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
  'C:\Program Files\Microsoft\Edge\Application\msedge.exe'
)
$edge = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $edge) { throw 'Microsoft Edge executable was not found' }
$process = Start-Process -FilePath $edge -ArgumentList @('http://127.0.0.1:18789/') -PassThru
Start-Sleep -Seconds 2
$value = [ordered]@{ schema = 'xq-openclaw-native-dashboard-root-open-v1'; started = ($null -ne $process); process_id = [uint32]$process.Id; executable = $edge }
$json = $value | ConvertTo-Json -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 60)
        output = HOST_ROOT / "guest-native-dashboard-root-open.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if value.get("started") else 2
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def open_deepseek_api_keys(control) -> int:
    """Open the official DeepSeek API-key page; the user performs all authentication."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        script = r"""
$ErrorActionPreference = 'Stop'
$candidates = @(
  'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
  'C:\Program Files\Microsoft\Edge\Application\msedge.exe'
)
$edge = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $edge) { throw 'Microsoft Edge executable was not found' }
$url = 'https://platform.deepseek.com/api_keys'
$process = Start-Process -FilePath $edge -ArgumentList @($url) -PassThru
Start-Sleep -Seconds 2
$value = [ordered]@{ schema = 'xq-openclaw-native-deepseek-api-keys-open-v1'; started = ($null -ne $process); process_id = [uint32]$process.Id; url = $url }
$json = $value | ConvertTo-Json -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
        value = run_ps_json(guest_session, script, 60)
        output = HOST_ROOT / "guest-native-deepseek-api-keys-open.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if value.get("started") else 2
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def click_owner_menu(control) -> int:
    """Click only the visible Owner menu in the fixed 1600x1200 pilot display."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        mouse = host_session.Console.Mouse
        x, y = 94, 1121
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 0)
        time.sleep(0.25)
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 1)
        time.sleep(0.12)
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 0)
        value = {
            "schema": "xq-openclaw-native-owner-menu-click-v1",
            "x": x,
            "y": y,
            "absolute_supported": bool(mouse.AbsoluteSupported),
        }
        output = HOST_ROOT / "guest-native-owner-menu-click.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def click_settings_menu(control) -> int:
    """Click Settings in the already-open Owner menu on the fixed display."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        mouse = host_session.Console.Mouse
        x, y = 72, 855
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 0)
        time.sleep(0.25)
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 1)
        time.sleep(0.12)
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 0)
        value = {"schema": "xq-openclaw-native-settings-menu-click-v1", "x": x, "y": y}
        output = HOST_ROOT / "guest-native-settings-menu-click.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def click_model_settings(control) -> int:
    """Click Model in the local settings sidebar on the fixed display."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        mouse = host_session.Console.Mouse
        x, y = 68, 729
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 0)
        time.sleep(0.25)
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 1)
        time.sleep(0.12)
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 0)
        value = {"schema": "xq-openclaw-native-model-settings-click-v1", "x": x, "y": y}
        output = HOST_ROOT / "guest-native-model-settings-click.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def click_connect_model(control) -> int:
    """Open the verified-model connection chooser without authorizing an account."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        mouse = host_session.Console.Mouse
        x, y = 1208, 291
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 0)
        time.sleep(0.25)
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 1)
        time.sleep(0.12)
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 0)
        value = {"schema": "xq-openclaw-native-connect-model-click-v1", "x": x, "y": y}
        output = HOST_ROOT / "guest-native-connect-model-click.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def click_device_pairing_view(control) -> int:
    """Open the ChatGPT device-pairing review; do not install or authorize it."""
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        mouse = host_session.Console.Mouse
        x, y = 1434, 618
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 0)
        time.sleep(0.25)
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 1)
        time.sleep(0.12)
        mouse.PutMouseEventAbsolute(x, y, 0, 0, 0)
        value = {"schema": "xq-openclaw-native-device-pairing-view-v1", "x": x, "y": y}
        output = HOST_ROOT / "guest-native-device-pairing-view.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def request_reboot(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        listeners = run_ps_json(
            guest_session,
            r"""
$ErrorActionPreference = 'Stop'
$items = @(Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue)
$value = [ordered]@{ schema = 'xq-openclaw-native-reboot-ready-v1'; listener_count = $items.Count; loopback_only = ($items.Count -eq 1 -and @($items | Where-Object { $_.LocalAddress -notin @('127.0.0.1','::1') }).Count -eq 0) }
$json = $value | ConvertTo-Json -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
""",
            120,
        )
        if listeners.get("listener_count") != 1 or not listeners.get("loopback_only"):
            raise RuntimeError("Gateway was not ready for the reboot test")
        exe = r"C:\Windows\System32\shutdown.exe"
        code, _, _ = run_process(
            guest_session,
            exe,
            [exe, "/r", "/t", "0", "/d", "p:0:0", "/c", "OpenClaw native pilot reboot test"],
            60,
        )
        value = {
            "schema": "xq-openclaw-native-reboot-request-v1",
            "listener_count_before": listeners.get("listener_count"),
            "loopback_only_before": listeners.get("loopback_only"),
            "shutdown_exit": code,
        }
        output = HOST_ROOT / "guest-native-reboot-request.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if code == 0 else 2
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def request_shutdown(control) -> int:
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = open_guest(control)
        exe = r"C:\Windows\System32\shutdown.exe"
        code, _, _ = run_process(
            guest_session,
            exe,
            [exe, "/s", "/f", "/t", "0", "/d", "p:0:0", "/c", "OpenClaw native pilot rollback"],
            60,
        )
        value = {
            "schema": "xq-openclaw-native-shutdown-request-v1",
            "shutdown_exit": code,
        }
        output = HOST_ROOT / "guest-native-shutdown-request.json"
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if code == 0 else 2
    finally:
        password = ""
        close_guest(host_session, guest_session, clear)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "preflight",
            "stage",
            "install-node",
            "install-package",
            "install-diagnostic",
            "copy-latest-debug",
            "install-package-retry",
            "assess-install",
            "cli-probe",
            "configure-minimal",
            "gateway-foreground-test",
            "disable-automation",
            "gateway-service-install",
            "gateway-service-diagnostic",
            "gateway-service-check",
            "gateway-service-restart",
            "gateway-fault-recovery",
            "production-diagnostic-regression",
            "uninstall-validation",
            "model-readiness",
            "deepseek-readiness",
            "deepseek-live-conversation",
            "enable-codex-only",
            "enable-openai-codex-only",
            "enable-deepseek-only",
            "prepare-auth-ui",
            "restart-clipboard-integration",
            "open-dashboard",
            "open-settings-page",
            "open-dashboard-root",
            "open-deepseek-api-keys",
            "click-owner-menu",
            "click-settings-menu",
            "click-model-settings",
            "click-connect-model",
            "click-device-pairing-view",
            "request-reboot",
            "request-shutdown",
        ),
    )
    args = parser.parse_args()
    control = load_control()
    if args.action == "preflight":
        return preflight(control)
    if args.action == "stage":
        return stage(control)
    if args.action == "install-node":
        return install_node(control)
    if args.action == "install-package":
        return install_package(control)
    if args.action == "install-diagnostic":
        return install_diagnostic(control)
    if args.action == "copy-latest-debug":
        return copy_latest_debug(control)
    if args.action == "install-package-retry":
        return install_package_retry(control)
    if args.action == "assess-install":
        return assess_install(control)
    if args.action == "cli-probe":
        return cli_probe(control)
    if args.action == "configure-minimal":
        return configure_minimal(control)
    if args.action == "gateway-foreground-test":
        return gateway_foreground_test(control)
    if args.action == "disable-automation":
        return disable_automation(control)
    if args.action == "gateway-service-install":
        return gateway_service_install(control)
    if args.action == "gateway-service-diagnostic":
        return gateway_service_diagnostic(control)
    if args.action == "gateway-service-check":
        return gateway_service_check(control)
    if args.action == "gateway-service-restart":
        return gateway_service_restart(control)
    if args.action == "gateway-fault-recovery":
        return gateway_fault_recovery(control)
    if args.action == "production-diagnostic-regression":
        return production_diagnostic_regression(control)
    if args.action == "uninstall-validation":
        return uninstall_validation(control)
    if args.action == "model-readiness":
        return model_readiness(control)
    if args.action == "deepseek-readiness":
        return deepseek_readiness(control)
    if args.action == "deepseek-live-conversation":
        return deepseek_live_conversation(control)
    if args.action == "enable-codex-only":
        return enable_codex_only(control)
    if args.action == "enable-openai-codex-only":
        return enable_openai_codex_only(control)
    if args.action == "enable-deepseek-only":
        return enable_deepseek_only(control)
    if args.action == "prepare-auth-ui":
        return prepare_auth_ui(control)
    if args.action == "restart-clipboard-integration":
        return restart_clipboard_integration(control)
    if args.action == "open-dashboard":
        return open_dashboard(control)
    if args.action == "open-settings-page":
        return open_settings_page(control)
    if args.action == "open-dashboard-root":
        return open_dashboard_root(control)
    if args.action == "open-deepseek-api-keys":
        return open_deepseek_api_keys(control)
    if args.action == "click-owner-menu":
        return click_owner_menu(control)
    if args.action == "click-settings-menu":
        return click_settings_menu(control)
    if args.action == "click-model-settings":
        return click_model_settings(control)
    if args.action == "click-connect-model":
        return click_connect_model(control)
    if args.action == "click-device-pairing-view":
        return click_device_pairing_view(control)
    if args.action == "request-reboot":
        return request_reboot(control)
    if args.action == "request-shutdown":
        return request_shutdown(control)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
