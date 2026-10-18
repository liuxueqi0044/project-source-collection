"""Bounded, read-only preflight for the isolated OpenClaw test clone.

The script reuses the existing DPAPI-protected guest credential without
printing or persisting its plaintext.  It executes one fixed PowerShell probe
through VirtualBox Guest Control and writes only a minimized JSON result.
"""

from __future__ import annotations

import base64
import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path


SOURCE_HELPER = Path(
    r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat\outputs\qq-vm"
    r"\install\control_qq_runtime_host.py"
)
CLONE_ID = "5d948da3-2078-45bf-92b4-151de5412753"
CLONE_NAME = "XQ-OpenClaw-Test-01"
OUTPUT = Path(
    r"C:\Users\xiayu\Documents\Codex\2026-09-15\agent-a-c-users-xiayu-documents"
    r"\work\agent-kits-tech\evidence\guest-preflight.json"
)
ADMIN_CREDENTIAL = Path(
    r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat\outputs\qq-vm"
    r"\install\credential-state\pmai_setup.dpapi"
)
GUEST_PROCESS_STARTED = 100
GUEST_PROCESS_TERMINATED = 500


POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
$result = [ordered]@{
  schema = 'xq-openclaw-guest-preflight-v1'
  computer = $env:COMPUTERNAME
  user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
  os = $null
  hardware = $null
  storage = $null
  network = $null
  project_processes = @()
  qq_process_count = 0
  project_services = @()
  project_tasks = @()
  project_run_entries = @()
  project_startup_files = @()
  windows_features = $null
  openclaw_gateway_present = $null
  section_status = [ordered]@{}
}

try {
  $os = Get-CimInstance Win32_OperatingSystem
  $result.os = [ordered]@{
    caption = [string]$os.Caption
    version = [string]$os.Version
    build = [string]$os.BuildNumber
    architecture = [string]$os.OSArchitecture
  }
  $result.section_status.os = 'ok'
} catch { $result.section_status.os = 'unavailable' }

try {
  $cpus = @(Get-CimInstance Win32_Processor)
  $computer = Get-CimInstance Win32_ComputerSystem
  $result.hardware = [ordered]@{
    logical_processors = [int]($cpus | Measure-Object NumberOfLogicalProcessors -Sum).Sum
    memory_bytes = [uint64]$computer.TotalPhysicalMemory
    virtualization_firmware_enabled = @($cpus | Where-Object { $_.VirtualizationFirmwareEnabled }).Count -gt 0
    slat_available = @($cpus | Where-Object { $_.SecondLevelAddressTranslationExtensions }).Count -gt 0
    vm_monitor_mode_available = @($cpus | Where-Object { $_.VMMonitorModeExtensions }).Count -gt 0
  }
  $result.section_status.hardware = 'ok'
} catch { $result.section_status.hardware = 'unavailable' }

try {
  $drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
  $result.storage = [ordered]@{ size_bytes = [uint64]$drive.Size; free_bytes = [uint64]$drive.FreeSpace }
  $result.section_status.storage = 'ok'
} catch { $result.section_status.storage = 'unavailable' }

try {
  $result.network = [ordered]@{
    adapter_up_count = @(Get-NetAdapter | Where-Object Status -eq 'Up').Count
    ipv4_default_route_count = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue).Count
  }
  $result.section_status.network = 'ok'
} catch { $result.section_status.network = 'unavailable' }

try {
  $processes = @(Get-CimInstance Win32_Process)
  $projectProcesses = @($processes | Where-Object {
    $path = [string]$_.ExecutablePath
    $_.Name -eq 'OpenClaw.Tray.WinUI.exe' -or $path.StartsWith('C:\PMAI\', [System.StringComparison]::OrdinalIgnoreCase)
  } | ForEach-Object {
    [ordered]@{ name = [string]$_.Name; pid = [int]$_.ProcessId; under_pmai = ([string]$_.ExecutablePath).StartsWith('C:\PMAI\', [System.StringComparison]::OrdinalIgnoreCase) }
  })
  $result.project_processes = $projectProcesses
  $result.qq_process_count = @($processes | Where-Object Name -eq 'QQ.exe').Count
  $result.section_status.processes = 'ok'
} catch { $result.section_status.processes = 'unavailable' }

try {
  $result.project_services = @(Get-CimInstance Win32_Service | Where-Object {
    $_.Name -match '(?i)PMAI|PersonalMessenger|OpenClaw' -or
    $_.DisplayName -match '(?i)PMAI|PersonalMessenger|OpenClaw' -or
    ([string]$_.PathName) -match '(?i)^\"?C:\\PMAI\\'
  } | ForEach-Object {
    [ordered]@{ name = [string]$_.Name; display_name = [string]$_.DisplayName; state = [string]$_.State; start_mode = [string]$_.StartMode }
  })
  $result.section_status.services = 'ok'
} catch { $result.section_status.services = 'unavailable' }

try {
  $taskItems = @()
  foreach ($task in @(Get-ScheduledTask)) {
    $actionMatch = @($task.Actions | Where-Object { ([string]$_.Execute) -match '(?i)^\"?C:\\PMAI\\' }).Count -gt 0
    if ($task.TaskName -match '(?i)PMAI|PersonalMessenger|OpenClaw' -or $task.TaskPath -match '(?i)PMAI|PersonalMessenger|OpenClaw' -or $actionMatch) {
      $triggers = @()
      $triggerCount = 0
      $actionUnderPmai = $actionMatch
      try {
        [xml]$taskXml = Export-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath
        $triggerNodes = @($taskXml.SelectNodes("/*[local-name()='Task']/*[local-name()='Triggers']/*"))
        $triggerCount = $triggerNodes.Count
        $triggers = @($triggerNodes | ForEach-Object { [string]$_.LocalName } | Where-Object { $_ })
        $actionUnderPmai = @($taskXml.Task.Actions.Exec | Where-Object { ([string]$_.Command) -match '(?i)^\"?C:\\PMAI\\' }).Count -gt 0
      } catch {
        $triggers = @($task.Triggers | ForEach-Object {
          $kind = [string]$_.CimClass.CimClassName
          if (-not $kind) { $kind = [string]$_.PSObject.TypeNames[0] }
          $kind
        } | Where-Object { $_ })
        $triggerCount = $triggers.Count
      }
      $taskItems += [ordered]@{
        name = [string]$task.TaskName
        path = [string]$task.TaskPath
        state = [string]$task.State
        enabled = [bool]$task.Settings.Enabled
        trigger_kinds = $triggers
        trigger_count = [int]$triggerCount
        action_under_pmai = [bool]$actionUnderPmai
      }
    }
  }
  $result.project_tasks = $taskItems
  $result.section_status.tasks = 'ok'
} catch { $result.section_status.tasks = 'unavailable' }

try {
  $runPaths = [ordered]@{
    HKCU_RUN = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    HKCU_RUNONCE = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce'
    HKLM_RUN = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run'
    HKLM_RUNONCE = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\RunOnce'
    HKLM32_RUN = 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run'
  }
  $runItems = @()
  foreach ($label in $runPaths.Keys) {
    $path = $runPaths[$label]
    if (-not (Test-Path -LiteralPath $path)) { continue }
    foreach ($property in (Get-ItemProperty -LiteralPath $path).PSObject.Properties) {
      if ($property.Name -like 'PS*') { continue }
      $value = [string]$property.Value
      if ($property.Name -match '(?i)PMAI|PersonalMessenger|OpenClaw' -or $value -match '(?i)C:\\PMAI\\|PersonalMessenger|OpenClaw') {
        $runItems += [ordered]@{ hive_key = [string]$label; value_name = [string]$property.Name }
      }
    }
  }
  $result.project_run_entries = $runItems
  $result.section_status.run_entries = 'ok'
} catch { $result.section_status.run_entries = 'unavailable' }

try {
  $startupFiles = @()
  $startupPaths = @(
    [Environment]::GetFolderPath('Startup'),
    [Environment]::GetFolderPath('CommonStartup')
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
  foreach ($path in $startupPaths) {
    $startupFiles += @(Get-ChildItem -LiteralPath $path -File | Where-Object Name -match '(?i)PMAI|PersonalMessenger|OpenClaw' | ForEach-Object { [string]$_.Name })
  }
  $result.project_startup_files = $startupFiles
  $result.section_status.startup = 'ok'
} catch { $result.section_status.startup = 'unavailable' }

try {
  $features = [ordered]@{}
  foreach ($featureName in @('Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform')) {
    try {
      $feature = Get-WindowsOptionalFeature -Online -FeatureName $featureName
      $features[$featureName] = [string]$feature.State
    } catch { $features[$featureName] = 'unavailable' }
  }
  $result.windows_features = $features
  $result.section_status.windows_features = 'ok'
} catch { $result.section_status.windows_features = 'unavailable' }

try {
  $lxssPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
  $present = $false
  if (Test-Path -LiteralPath $lxssPath) {
    foreach ($distroKey in @(Get-ChildItem -LiteralPath $lxssPath)) {
      $distroName = [string](Get-ItemProperty -LiteralPath $distroKey.PSPath -Name DistributionName -ErrorAction SilentlyContinue).DistributionName
      if ($distroName -eq 'OpenClawGateway') { $present = $true }
    }
  }
  $result.openclaw_gateway_present = $present
  $result.section_status.wsl_inventory = 'ok'
} catch { $result.section_status.wsl_inventory = 'unavailable' }

$json = $result | ConvertTo-Json -Depth 8 -Compress
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""


def _load_control_helper():
    spec = importlib.util.spec_from_file_location("pmai_control", SOURCE_HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load existing credential helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.VM_ID = CLONE_ID
    module.VM_NAME = CLONE_NAME
    return module


def _read_all(process) -> str:
    chunks: list[bytes] = []
    deadline = time.monotonic() + 90
    while True:
        try:
            chunk = bytes(process.Read(1, 65536, 2_000))
        except Exception:
            chunk = b""
        if chunk:
            chunks.append(chunk)
        status = int(process.Status)
        if status == GUEST_PROCESS_TERMINATED:
            break
        if time.monotonic() >= deadline:
            try:
                process.Terminate()
            except Exception:
                pass
            raise TimeoutError("guest preflight exceeded 90 seconds")
        if status != GUEST_PROCESS_STARTED and not chunk:
            process.WaitForArray([2], 5_000)
    return b"".join(chunks).decode("utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", choices=("qqbot", "pmai_setup"), default="qqbot")
    args = parser.parse_args()
    control = _load_control_helper()
    output = OUTPUT
    expected_user = r"\qqbot"
    if args.account == "pmai_setup":
        control.GUEST_USER = "pmai_setup"
        control.CREDENTIAL = ADMIN_CREDENTIAL
        output = OUTPUT.with_name("guest-admin-preflight.json")
        expected_user = r"\pmai_setup"
    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = control._open_guest()
        encoded = base64.b64encode(POWERSHELL.encode("utf-16-le")).decode("ascii")
        executable = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        process = guest_session.ProcessCreate(
            executable,
            [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            r"C:\Windows\System32",
            [],
            [48],
            60_000,
        )
        process.WaitForArray([1], 30_000)
        if int(process.Status) not in (GUEST_PROCESS_STARTED, GUEST_PROCESS_TERMINATED):
            raise RuntimeError("guest preflight process did not start")
        envelope = _read_all(process).strip()
        if int(process.ExitCode) != 0:
            raise RuntimeError(f"guest preflight exited with code {int(process.ExitCode)}")
        raw = base64.b64decode(envelope, validate=True).decode("utf-8")
        value = json.loads(raw)
        if value.get("schema") != "xq-openclaw-guest-preflight-v1":
            raise RuntimeError("guest preflight schema mismatch")
        if value.get("computer") != "PMAI-QQVM":
            raise RuntimeError("unexpected guest computer identity")
        if not str(value.get("user", "")).lower().endswith(expected_user):
            raise RuntimeError("unexpected guest user identity")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    finally:
        password = ""
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


if __name__ == "__main__":
    sys.exit(main())
