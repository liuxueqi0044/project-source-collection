"""Controlled real-process recovery, with synthetic requests and no network.

Creates only its explicit isolated storage/evidence directories. A child exits
via os._exit(73) while holding the actual collector lock. A separate process
recovers the preserved earliest window. Never stops an external process.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from equity_data.local_control import Control, collector_lock

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def synthetic_payload(window, scopes):
    return {"symbols":["SYNTHETIC_ONLY"], "universe":{"mode":"configured","symbols":["SYNTHETIC_ONLY"]},
        "sources":["synthetic_source"], "data_types":["documents"], "full_history":False,
        "window_start_hint":window, "resume_scope_ids":scopes}


def worker(args):
    storage, output = args.storage.resolve(), args.output.resolve()
    iteration = args.iteration
    with collector_lock(storage) as owned:
        if not owned:
            raise RuntimeError("Controlled worker did not acquire its own isolated lock")
        control = Control(storage)
        if args.worker == "crash":
            if iteration == 1:
                control.enqueue(synthetic_payload("2020-01-01",["scope:old-synthetic"]), "missed_schedule_catchup")
            running = control.claim()
            if running is None:
                raise RuntimeError("No synthetic request to claim")
            successor = control.enqueue(synthetic_payload("2024-01-01" if iteration == 1 else "2025-01-01",
                ["scope:later-synthetic"] if iteration == 1 else []), "manual_decision_refresh")
            snapshot = {"worker_pid":os.getpid(),"kernel_lock_acquired":owned,"iteration":iteration,
                "old_running":control.get(running["request_id"]),"pending_successor":control.get(successor),
                "source_scope_database_exists":(storage/"data.sqlite3").exists(),"exit_method":"os._exit(73)"}
            save(output/f"before-crash-{iteration}.json",snapshot)
            # Parent proves that another process cannot take our held lock, then
            # permits this process to terminate without any lock/DB cleanup.
            deadline = time.monotonic()+20
            while not (output/f"allow-crash-{iteration}").exists():
                if time.monotonic()>deadline:
                    raise TimeoutError("Parent never completed the overlap lock probe")
                time.sleep(.02)
            os._exit(73)
        recovered = control.recover_under_lock()
        rows = [dict(r) for r in control.db.execute("SELECT * FROM local_requests ORDER BY created_at")]
        events = [dict(r) for r in control.db.execute("SELECT * FROM local_events ORDER BY observed_at,rowid")]
        save(output/f"after-recovery-{iteration}.json",{"worker_pid":os.getpid(),"kernel_lock_acquired":owned,
            "recovered_count":recovered,"requests":rows,"events":events,
            "source_scope_database_exists":(storage/"data.sqlite3").exists()})
        control.close()
    return 0


def verify(storage, output):
    storage=storage.resolve();output=output.resolve()
    if storage.exists() or output.exists():
        raise FileExistsError("Use new isolated directories; previous evidence is never overwritten")
    if storage==ROOT/"data/live":
        raise ValueError("Live storage is not permitted")
    storage.mkdir(parents=True);output.mkdir(parents=True)
    env={**os.environ,"PYTHONPATH":str(ROOT)}
    rounds=[]
    for iteration in (1,2):
        command=[sys.executable,str(Path(__file__).resolve()),"--storage",str(storage),"--output",str(output),"--iteration",str(iteration)]
        with (output/f"crash-{iteration}.stdout.log").open("w",encoding="utf-8") as out, (output/f"crash-{iteration}.stderr.log").open("w",encoding="utf-8") as error:
            child=subprocess.Popen(command+["--worker","crash"],cwd=ROOT,env=env,stdout=out,stderr=error)
            before=output/f"before-crash-{iteration}.json"
            deadline=time.monotonic()+20
            while not before.exists():
                if child.poll() is not None:
                    raise RuntimeError(f"Crash worker exited before evidence: {child.returncode}")
                if time.monotonic()>deadline:
                    # No terminate/kill of any process. The child has its own
                    # bounded handshake timeout if a parent operation fails.
                    raise TimeoutError("No child evidence within deadline")
                time.sleep(.02)
            with collector_lock(storage) as acquired_while_owner_alive:
                if acquired_while_owner_alive:
                    raise AssertionError("A second process acquired the active worker lock")
            (output/f"allow-crash-{iteration}").write_text("Parent confirmed kernel lock exclusion",encoding="utf-8")
            exitcode=child.wait(timeout=25)
        if exitcode!=73:
            raise AssertionError(f"Expected explicit abnormal exit 73; got {exitcode}")
        recovered=subprocess.run(command+["--worker","recover"],cwd=ROOT,env=env,capture_output=True,text=True,timeout=20)
        (output/f"recover-{iteration}.stdout.log").write_text(recovered.stdout,encoding="utf-8")
        (output/f"recover-{iteration}.stderr.log").write_text(recovered.stderr,encoding="utf-8")
        if recovered.returncode:
            raise AssertionError(f"Recovery worker exited {recovered.returncode}")
        before_data=json.loads(before.read_text(encoding="utf-8"))
        after=json.loads((output/f"after-recovery-{iteration}.json").read_text(encoding="utf-8"))
        rows={row["request_id"]:row for row in after["requests"]}
        successor=rows[before_data["pending_successor"]["request_id"]]
        body=json.loads(successor["payload_json"])
        expected_lineage={row["request_id"] for row in after["requests"] if row["state"]=="interrupted"}
        checks={"different_process_reacquired_lock":after["kernel_lock_acquired"] and after["worker_pid"]!=before_data["worker_pid"],
            "other_process_was_denied_during_owner_lifetime":not acquired_while_owner_alive,
            "old_request_interrupted":rows[before_data["old_running"]["request_id"]]["state"]=="interrupted",
            "successor_pending":successor["state"]=="pending",
            "earliest_window_retained":body.get("window_start_hint")=="2020-01-01",
            "resume_ids_retained":set(body.get("resume_scope_ids",[]))=={"scope:old-synthetic","scope:later-synthetic"},
            "lineage_retained":set(body.get("_local",{}).get("recovered_request_ids",[]))==expected_lineage,
            "source_scope_not_yet_persisted":not before_data["source_scope_database_exists"] and not after["source_scope_database_exists"],
            "exactly_one_request_recovered":after["recovered_count"]==1}
        rounds.append({"iteration":iteration,"crash_exit_code":exitcode,"recovery_exit_code":recovered.returncode,
            "checks":checks,"before_file":before.name,"after_file":f"after-recovery-{iteration}.json"})
        save(output/"progress.json",{"rounds":rounds})
        if not all(checks.values()):
            raise AssertionError("Controlled recovery check failed; evidence preserved")
    result={"evidence_kind":"controlled_real_process_exits_with_synthetic_requests",
        "completed_at":datetime.now(timezone.utc).isoformat(),"storage":str(storage),"output":str(output),
        "rounds":rounds,"all_passed":all(all(row["checks"].values()) for row in rounds),
        "production_local_control_sha256":hashlib.sha256((ROOT/"equity_data/local_control.py").read_bytes()).hexdigest(),
        "network_requests":0,"external_processes_terminated":0,"live_storage_touched":False,
        "windows_tasks_changed":False,"limitations":["Not a natural reboot, login or sleep/resume observation.",
            "Synthetic request and scope identifiers; no upstream source scope existed before either exit."]}
    save(output/"result.json",result)
    (output/"README.md").write_text("Controlled real process exits with synthetic requests; see result.json for lock, exit-code, window, scope and lineage evidence. This does not establish natural reboot, login, or sleep recovery.",encoding="utf-8")
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage",type=Path,default=ROOT/"data/v41_recovery_process")
    parser.add_argument("--output",type=Path,default=ROOT/"reports/v4_1/recovery-process")
    parser.add_argument("--worker",choices=["crash","recover"])
    parser.add_argument("--iteration",type=int,default=1)
    args=parser.parse_args()
    if args.worker:return worker(args)
    print(json.dumps(verify(args.storage,args.output),ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":raise SystemExit(main())
