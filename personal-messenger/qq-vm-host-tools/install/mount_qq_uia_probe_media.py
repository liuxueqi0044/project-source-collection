"""Hot-swap one of two fixed QQ UIA probe media on the fixed VM DVD slot."""

from __future__ import annotations

import argparse
from pathlib import Path

import win32com.client


VM_ID = "7592dfa4-c60c-4818-921e-ced5c626254b"
VM_NAME = "PersonalMessengerQQ"
VM_RUNNING = 6
CONTROLLER = "IDE"
PORT = 0
DEVICE = 0
DEVICE_TYPE_DVD = 2
ACCESS_MODE_READ_ONLY = 1
LOCK_SHARED = 1

WORKSPACE = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
MEDIA = {
    "foreground": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_UIA_FOREGROUND_62c5109e.viso",
    "warmup": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_UIA_WARMUP_bd292557.viso",
    "private": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_UIA_PRIVATE_7c5b9a3e.viso",
    "rulepack": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_RULEPACK_ACTIVATION_4e3b7c91.viso",
    "private_structural": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_UIA_PRIVATE_STRUCTURAL_91d4e6b2.viso",
    "guest_local_r02": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_GUEST_LOCAL_R20260911_02.viso",
    "guest_local_r03": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_GUEST_LOCAL_R20260911_03.viso",
    "guest_local_r04": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_GUEST_LOCAL_R20260911_04.viso",
    "guest_local_r05": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_GUEST_LOCAL_R20260911_05.viso",
    "guest_local_r06": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_GUEST_LOCAL_R20260911_06.viso",
    "guest_local_r07": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_GUEST_LOCAL_R20260911_07.viso",
    "guest_local_r08": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_GUEST_LOCAL_R20260911_08.viso",
    "guest_local_r09": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_GUEST_LOCAL_R20260911_09.viso",
    "guest_local_r10": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_GUEST_LOCAL_R20260911_10.viso",
    "guest_local_r11": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_GUEST_LOCAL_R20260911_11.viso",    "guest_local_r12": WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_GUEST_LOCAL_R20260911_12.viso",
}
ALLOWED_CURRENT = {
    WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_UIA_PROBE_94371bb1-48ce-4c5a-948a-e06cc79bd56d.viso",
    WORKSPACE / r"outputs\qq-vm\deploy-media\PMAI_UPDATE_DEEPSEEK_V4_c6e92d41.viso",
    WORKSPACE / r"outputs\qq-vm\deploy-media\qq-runtime-session-final-cc8a7201.viso",
    *MEDIA.values(),
}


def normalized(path: str | Path) -> Path:
    return Path(path).resolve(strict=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--medium", required=True, choices=tuple(MEDIA))
    args = parser.parse_args()
    target_path = normalized(MEDIA[args.medium])
    allowed_current = {normalized(path) for path in ALLOWED_CURRENT}

    virtualbox = win32com.client.Dispatch("VirtualBox.VirtualBox")
    machine = virtualbox.FindMachine(VM_ID)
    if (
        str(machine.Id).lower() != VM_ID
        or machine.Name != VM_NAME
        or int(machine.State) != VM_RUNNING
    ):
        raise RuntimeError("fixed VM identity or running-state guard failed")

    session = win32com.client.Dispatch("VirtualBox.Session")
    try:
        machine.LockMachine(session, LOCK_SHARED)
        mutable = session.Machine
        if str(mutable.Id).lower() != VM_ID or mutable.Name != VM_NAME:
            raise RuntimeError("locked VM identity guard failed")
        attachment = mutable.GetMediumAttachment(CONTROLLER, PORT, DEVICE)
        if int(attachment.Type) != DEVICE_TYPE_DVD:
            raise RuntimeError("fixed IDE attachment is not a DVD drive")
        current = attachment.Medium
        if current is None:
            raise RuntimeError("fixed IDE DVD has no current probe medium")
        current_path = normalized(str(current.Location))
        if current_path not in allowed_current:
            raise RuntimeError("current IDE DVD medium is outside the fixed allowlist")
        if current_path == target_path:
            print("mounted=true")
            return 0

        target = virtualbox.OpenMedium(
            str(target_path), DEVICE_TYPE_DVD, ACCESS_MODE_READ_ONLY, False
        )
        if target is None or normalized(str(target.Location)) != target_path:
            raise RuntimeError("target medium identity guard failed")
        mutable.MountMedium(CONTROLLER, PORT, DEVICE, target, True)
        mutable.SaveSettings()
        attached = mutable.GetMediumAttachment(CONTROLLER, PORT, DEVICE).Medium
        if attached is None or normalized(str(attached.Location)) != target_path:
            raise RuntimeError("post-mount medium verification failed")
        print("mounted=true")
        return 0
    finally:
        try:
            session.UnlockMachine()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
