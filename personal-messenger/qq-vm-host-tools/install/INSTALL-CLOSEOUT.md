# Windows installation closeout

`PersonalMessengerQQ` is installed as Windows 11 Home (`EditionID=Core`) and is normally powered off. The installation and auxiliary VISO are detached. Host-side `install\aux` was deleted after verification; the DPAPI-protected recovery state remains under `install\credential-state`.

Start the VM through the project's memory-protected launcher:

```powershell
& 'C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat\outputs\personal-messenger-ai-v5\scripts\deployment\start_qq_vm.ps1' -Start
```

This installation was observed to make GuestControl available after `pmai_setup` signs in. Login recovery must be performed only through the restricted workflow while the visible PMAI Setup password field is confirmed to have focus. No general password-sending helper is supplied. `install\diagnostics\verify_dpapi_recovery.ps1` can verify the preserved DPAPI management credential through a temporary restricted password file once GuestControl is available; it removes that temporary plaintext in a `finally` block.

The DPAPI state is tied to the Windows identity that created it. Keep that profile and the existing restrictive ACL. Do not delete or rotate the only working administrator credential until a second tested administrative recovery path exists.

One guest file remains at `C:\Windows\Panther\unattend.xml`. Deletion through non-elevated GuestControl was denied. Its sensitive values were not inspected because the VM had already shut down before the requested boolean-only check. Remove this exact file later from an elevated guest session, then verify only that the path no longer exists.

WinRE has a BCD ID and points to `\Recovery\WindowsRE` on partition 4. Its `Enabled` state remains unconfirmed because `reagentc /info` requires elevation and the bounded UAC attempts did not produce a result file.
