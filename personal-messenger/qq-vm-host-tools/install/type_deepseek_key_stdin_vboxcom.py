"""Type one DeepSeek key from stdin into the fixed VM's focused getpass prompt."""

import argparse
import sys
import time

import win32com.client


VM_ID = "7592dfa4-c60c-4818-921e-ced5c626254b"
VM_NAME = "PersonalMessengerQQ"
VM_RUNNING = 6

PLAIN = {
    **{
        ord("a"): 0x1E, ord("b"): 0x30, ord("c"): 0x2E, ord("d"): 0x20,
        ord("e"): 0x12, ord("f"): 0x21, ord("g"): 0x22, ord("h"): 0x23,
        ord("i"): 0x17, ord("j"): 0x24, ord("k"): 0x25, ord("l"): 0x26,
        ord("m"): 0x32, ord("n"): 0x31, ord("o"): 0x18, ord("p"): 0x19,
        ord("q"): 0x10, ord("r"): 0x13, ord("s"): 0x1F, ord("t"): 0x14,
        ord("u"): 0x16, ord("v"): 0x2F, ord("w"): 0x11, ord("x"): 0x2D,
        ord("y"): 0x15, ord("z"): 0x2C,
        ord("0"): 0x0B, ord("1"): 0x02, ord("2"): 0x03, ord("3"): 0x04,
        ord("4"): 0x05, ord("5"): 0x06, ord("6"): 0x07, ord("7"): 0x08,
        ord("8"): 0x09, ord("9"): 0x0A, ord(" "): 0x39, ord("-"): 0x0C,
        ord("="): 0x0D, ord("["): 0x1A, ord("]"): 0x1B, ord(";"): 0x27,
        ord("'"): 0x28, ord("`"): 0x29, ord("\\"): 0x2B, ord(","): 0x33,
        ord("."): 0x34, ord("/"): 0x35,
    },
}
SHIFTED = dict(zip(b"!@#$%^&*()_+{}:\"~|<>?", (
    0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B,
    0x0C, 0x0D, 0x1A, 0x1B, 0x27, 0x28, 0x29, 0x2B, 0x33, 0x34, 0x35,
)))


def put(keyboard, code: int) -> None:
    keyboard.PutScancode(int(code))
    time.sleep(0.020)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirmed-getpass-focus", action="store_true", required=True)
    parser.add_argument("--confirmed-us-layout", action="store_true", required=True)
    args = parser.parse_args()
    if not (args.confirmed_getpass_focus and args.confirmed_us_layout):
        raise RuntimeError("public focus or keyboard-layout confirmation missing")

    secret = bytearray(sys.stdin.buffer.readline(514))
    session = None
    keyboard = None
    try:
        while secret and secret[-1] in (10, 13):
            secret.pop()
        if not secret or len(secret) > 512:
            raise RuntimeError("stdin key is empty or too long")
        for value in secret:
            lower = value + 32 if 65 <= value <= 90 else value
            if lower not in PLAIN and value not in SHIFTED:
                raise RuntimeError("stdin key contains an unsupported character")

        virtualbox = win32com.client.Dispatch("VirtualBox.VirtualBox")
        machine = virtualbox.FindMachine(VM_ID)
        if str(machine.Id).lower() != VM_ID or machine.Name != VM_NAME or int(machine.State) != VM_RUNNING:
            raise RuntimeError("fixed VM identity or running-state guard failed")
        session = win32com.client.Dispatch("VirtualBox.Session")
        machine.LockMachine(session, 1)
        keyboard = session.Console.Keyboard
        keyboard.ReleaseKeys()
        leds = keyboard.KeyboardLEDs
        caps_on = bool((int(leds) & 2) if isinstance(leds, int) else 2 in [int(x) for x in leds])
        if caps_on:
            put(keyboard, 0x3A)
            put(keyboard, 0xBA)

        for value in secret:
            upper = 65 <= value <= 90
            lower = value + 32 if upper else value
            shifted = upper or value in SHIFTED
            code = PLAIN[lower] if upper or value in PLAIN else SHIFTED[value]
            if shifted:
                put(keyboard, 0x2A)
            put(keyboard, code)
            put(keyboard, code | 0x80)
            if shifted:
                put(keyboard, 0xAA)
        put(keyboard, 0x1C)
        put(keyboard, 0x9C)
        print("typed=true")
        return 0
    finally:
        if keyboard is not None:
            try:
                keyboard.ReleaseKeys()
            except Exception:
                pass
        if session is not None:
            try:
                session.UnlockMachine()
            except Exception:
                pass
        for index in range(len(secret)):
            secret[index] = 0


if __name__ == "__main__":
    raise SystemExit(main())
