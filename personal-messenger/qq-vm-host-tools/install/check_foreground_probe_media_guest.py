"""Check one fixed public launcher on the guest DVD without starting a process."""

from pathlib import Path

import win32com.client
import win32crypt


VM_ID = "7592dfa4-c60c-4818-921e-ced5c626254b"
CREDENTIAL = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat\outputs\qq-vm\install\credential-state\qqbot.dpapi")
GUEST_FILE = r"D:\StartStructuralProbeForeground.cmd"


def main() -> None:
    clear = bytearray(win32crypt.CryptUnprotectData(bytes.fromhex(CREDENTIAL.read_text("ascii").strip()), None, None, None, 0)[1])
    password = clear.decode("utf-16-le").rstrip("\x00")
    host_session = guest_session = None
    try:
        vbox = win32com.client.Dispatch("VirtualBox.VirtualBox")
        machine = vbox.FindMachine(VM_ID)
        if str(machine.Id).lower() != VM_ID or machine.Name != "PersonalMessengerQQ" or int(machine.State) != 6:
            raise RuntimeError("fixed VM guard failed")
        host_session = win32com.client.Dispatch("VirtualBox.Session")
        machine.LockMachine(host_session, 1)
        guest_session = host_session.Console.Guest.CreateSession("qqbot", password, "", "pmai-fixed-media-check")
        if guest_session.WaitForArray([1], 30_000) != 1 or int(guest_session.Status) != 100:
            raise RuntimeError("qqbot guest session did not start")
        exists = bool(guest_session.FileExists(GUEST_FILE, True))
        size = int(guest_session.FsQueryInfo(GUEST_FILE, True).ObjectSize) if exists else 0
        print(f"exists={str(exists).lower()} size={size}")
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
    main()
