"""Export one fixed QQ observation-quarantine row without copying guest DBs.

The host asks the stopped guest's bundled Python to run a fixed, read-only
SQLite query.  Only CAS metadata is returned; payloads, message text,
credentials, and arbitrary exception text never cross the guest boundary.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import uuid
from pathlib import Path

import win32com.client
import win32crypt


VM_ID = "7592dfa4-c60c-4818-921e-ced5c626254b"
VM_NAME = "PersonalMessengerQQ"
VM_RUNNING = 6
GUEST_USER = "qqbot"
WORKSPACE = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CREDENTIAL = WORKSPACE / r"outputs\qq-vm\install\credential-state\qqbot.dpapi"
DIAGNOSTICS = WORKSPACE / r"outputs\qq-vm\install\diagnostics"
DEFAULT_REPORT = DIAGNOSTICS / "qq-quarantine-metadata-host.json"
GUEST_PYTHON = r"C:\PMAI\app\.venv\Scripts\python.exe"
GUEST_DB = r"C:\PMAI\data\runtime\qq-default-account\qq-vm-bridge.sqlite3"
GUEST_STATUS = r"C:\PMAI\data\qq-session-runtime-status.json"
GUEST_DATA = r"C:\PMAI\data"
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

QUERY = r'''
import json, re, sqlite3, sys
conversation_id = sys.argv[1]
result_path = sys.argv[2]
id_re = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')
if not id_re.fullmatch(conversation_id):
    raise SystemExit(11)
db = sqlite3.connect(r'C:\PMAI\data\runtime\qq-default-account\qq-vm-bridge.sqlite3')
db.row_factory = sqlite3.Row
try:
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'qq_vm_observation_quarantines' not in tables:
        payload = {'schema':'pmai-qq-quarantine-metadata-guest-v1','succeeded':False,'error_code':'SCHEMA_INVALID'}
        temporary = result_path + '.tmp'
        open(temporary, 'w', encoding='utf-8').write(json.dumps(payload, separators=(',',':')))
        import os; os.replace(temporary, result_path)
        raise SystemExit(12)
    if 'qq_vm_observation_quarantine_releases' in tables:
        sql = """
      SELECT q.failed_run_id,q.conversation_id,q.binding_id,q.binding_revision,
             q.request_id,q.failed_generation,q.error_code,
             q.parent_terminate_reason,q.worker_exit_code,q.quarantined_at,
             r.release_id,r.released_at
        FROM qq_vm_observation_quarantines q
        LEFT JOIN qq_vm_observation_quarantine_releases r
          ON r.conversation_id=q.conversation_id AND r.request_id=q.request_id
       WHERE q.conversation_id=?
       ORDER BY q.quarantined_at DESC LIMIT 1
    """
    else:
        sql = """
      SELECT q.failed_run_id,q.conversation_id,q.binding_id,q.binding_revision,
             q.request_id,q.failed_generation,q.error_code,
             q.parent_terminate_reason,q.worker_exit_code,q.quarantined_at,
             NULL AS release_id,NULL AS released_at
        FROM qq_vm_observation_quarantines q
       WHERE q.conversation_id=?
       ORDER BY q.quarantined_at DESC LIMIT 1
    """
    row = db.execute(sql, (conversation_id,)).fetchone()
    if row is None:
        payload = {'schema':'pmai-qq-quarantine-metadata-guest-v1','succeeded':True,'quarantine_present':False,'conversation_id':conversation_id}
        temporary = result_path + '.tmp'
        open(temporary, 'w', encoding='utf-8').write(json.dumps(payload, separators=(',',':')))
        import os; os.replace(temporary, result_path)
    else:
        fields = ('failed_run_id','conversation_id','binding_id','binding_revision',
                  'request_id','failed_generation','error_code','parent_terminate_reason',
                  'worker_exit_code','quarantined_at','release_id','released_at')
        value = {k: row[k] for k in fields}
        value.update({'schema':'pmai-qq-quarantine-metadata-guest-v1','succeeded':True,'quarantine_present':True})
        temporary = result_path + '.tmp'
        open(temporary, 'w', encoding='utf-8').write(json.dumps(value, separators=(',',':')))
        import os; os.replace(temporary, result_path)
finally:
    db.close()
'''.strip()


def _open_guest():
    protected = CREDENTIAL.read_text(encoding="ascii").strip()
    clear = bytearray(win32crypt.CryptUnprotectData(bytes.fromhex(protected), None, None, None, 0)[1])
    password = clear.decode("utf-16-le").rstrip("\x00")
    host = guest = None
    try:
        vbox = win32com.client.Dispatch("VirtualBox.VirtualBox")
        machine = vbox.FindMachine(VM_ID)
        if str(machine.Id).lower() != VM_ID or machine.Name != VM_NAME or int(machine.State) != VM_RUNNING:
            raise RuntimeError("VM_GUARD_FAILED")
        host = win32com.client.Dispatch("VirtualBox.Session")
        machine.LockMachine(host, 1)
        guest = host.Console.Guest.CreateSession(GUEST_USER, password, "", "pmai-quarantine-metadata")
        if guest.WaitForArray([1], 30_000) != 1 or int(guest.Status) != 100:
            raise RuntimeError("GUEST_SESSION_FAILED")
        return guest, host, clear
    except BaseException:
        if guest is not None:
            try: guest.Close()
            except Exception: pass
        if host is not None:
            try: host.UnlockMachine()
            except Exception: pass
        for i in range(len(clear)): clear[i] = 0
        raise


def _read_guest_json(guest, path: str) -> dict[str, object]:
    temporary = DIAGNOSTICS / ".qq-quarantine-status.tmp.json"
    try:
        if temporary.exists(): temporary.unlink()
        progress = guest.FileCopyFromGuest(path, str(temporary), [])
        progress.WaitForCompletion(30_000)
        if not bool(progress.Completed) or int(progress.ResultCode) != 0:
            raise RuntimeError("STATUS_COPY_FAILED")
        value = json.loads(temporary.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict): raise RuntimeError("STATUS_SHAPE_INVALID")
        return value
    finally:
        temporary.unlink(missing_ok=True)


def _run_query(guest, conversation_id: str) -> dict[str, object]:
    encoded = base64.b64encode(QUERY.encode("utf-8")).decode("ascii")
    nonce = uuid.uuid4().hex
    result_guest = rf"C:\PMAI\data\pmai-quarantine-metadata-{nonce}.json"
    result_host = DIAGNOSTICS / f".qq-quarantine-query-{nonce}.json"
    process = guest.ProcessCreate(
        GUEST_PYTHON,
        [GUEST_PYTHON, "-c", "import base64;exec(base64.b64decode(%r))" % encoded, conversation_id, result_guest],
        GUEST_DATA, [], [], 60_000,
    )
    if process.WaitForArray([1], 30_000) != 1:
        raise RuntimeError("QUERY_PROCESS_DID_NOT_START")
    if process.WaitForArray([2], 30_000) != 2 or int(process.ExitCode) != 0:
        exit_code = int(process.ExitCode)
        raise RuntimeError(f"QUERY_PROCESS_FAILED_{exit_code}")
    try:
        progress = guest.FileCopyFromGuest(result_guest, str(result_host), [])
        progress.WaitForCompletion(30_000)
        if not bool(progress.Completed) or int(progress.ResultCode) != 0:
            raise RuntimeError("QUERY_RESULT_COPY_FAILED")
        if result_host.stat().st_size > 16_384:
            raise RuntimeError("QUERY_RESULT_SIZE_INVALID")
        value = json.loads(result_host.read_text(encoding="utf-8"))
    finally:
        result_host.unlink(missing_ok=True)
        try:
            guest.FileDelete(result_guest)
        except Exception:
            pass
    if not isinstance(value, dict) or value.get("schema") != "pmai-qq-quarantine-metadata-guest-v1":
        raise RuntimeError("QUERY_SCHEMA_INVALID")
    return value


def _validate(value: dict[str, object], conversation_id: str) -> dict[str, object]:
    if value.get("succeeded") is not True or value.get("conversation_id") != conversation_id:
        raise RuntimeError("QUERY_RESULT_INVALID")
    if value.get("quarantine_present") is False:
        return {"schema":"pmai-qq-quarantine-metadata-host-v1", "succeeded":True,
                "quarantine_present":False, "conversation_id":conversation_id}
    for key in ("failed_run_id", "request_id"):
        if not isinstance(value.get(key), str) or not UUID_RE.fullmatch(value[key]): raise RuntimeError("QUERY_UUID_INVALID")
    for key in ("binding_id", "error_code", "parent_terminate_reason"):
        if not isinstance(value.get(key), str) or not CODE.fullmatch(value[key]): raise RuntimeError("QUERY_CODE_INVALID")
    for key in ("binding_revision", "failed_generation"):
        if not isinstance(value.get(key), int) or isinstance(value[key], bool) or value[key] < 1: raise RuntimeError("QUERY_INTEGER_INVALID")
    for key in ("quarantined_at", "released_at"):
        if value.get(key) is not None and (not isinstance(value[key], str) or len(value[key]) > 64): raise RuntimeError("QUERY_TIME_INVALID")
    if value.get("worker_exit_code") is not None and (not isinstance(value["worker_exit_code"], int) or isinstance(value["worker_exit_code"], bool)):
        raise RuntimeError("QUERY_EXIT_INVALID")
    return {"schema":"pmai-qq-quarantine-metadata-host-v1", "succeeded":True,
            "quarantine_present":True, **{k:value.get(k) for k in (
                "failed_run_id","conversation_id","binding_id","binding_revision","request_id",
                "failed_generation","error_code","parent_terminate_reason","worker_exit_code",
                "quarantined_at","release_id","released_at")}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conversation-id", required=True)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report: dict[str, object] = {"schema":"pmai-qq-quarantine-metadata-host-v1", "succeeded":False}
    guest = host = None; clear = bytearray()
    try:
        if ID.fullmatch(args.conversation_id) is None: raise RuntimeError("CONVERSATION_ID_INVALID")
        guest, host, clear = _open_guest()
        status = _read_guest_json(guest, GUEST_STATUS)
        if status.get("runtime_process_alive") is not False: raise RuntimeError("RUNTIME_MUST_BE_STOPPED")
        report = _validate(_run_query(guest, args.conversation_id), args.conversation_id)
        code = 0
    except Exception as exc:
        report = {"schema":"pmai-qq-quarantine-metadata-host-v1", "succeeded":False,
                  "error_code": str(exc) if CODE.fullmatch(str(exc)) else type(exc).__name__}
        code = 2
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_name("." + args.report.name + ".tmp")
        temporary.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
        temporary.replace(args.report)
        for i in range(len(clear)): clear[i] = 0
        if guest is not None:
            try: guest.Close()
            except Exception: pass
        if host is not None:
            try: host.UnlockMachine()
            except Exception: pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
