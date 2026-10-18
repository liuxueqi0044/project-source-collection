"""Capture the same date/watch probes before and after V3; no real securities."""
import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from equity_data import machine
from equity_data.engine import _scopes
from equity_data.store import Store, now_utc


def probe():
    adapter = SimpleNamespace(scope_kind="company", data_types=("documents",))
    original = {"market":"US", "start":"2024-01-01", "end":"2024-12-31", "sources":["fixture"],
                "securities":[{"company_id":"sec:0000009999", "cik":"9999", "symbol":"SYNTHETIC",
                               "start":"2020-01-01", "end":"2020-01-31"}], "sec":{}, "http":{}, "budget":{}}
    cases=[]
    with tempfile.TemporaryDirectory() as directory, Store(directory) as store:
        for name,command,start,end in [("backfill_security_default","backfill",None,None),
                                      ("backfill_cli_overrides","backfill","2025-02-01","2025-02-28"),
                                      ("update_effective_dates","update",None,None),
                                      ("update_cli_overrides","update","2025-03-01","2025-03-31")]:
            args=SimpleNamespace(command=command,sources=None,symbols=None,start=start,end=end,
                                 offline=True,max_tasks=0,full_history=False)
            def capture(store,client,conf,adapters,**kwargs):
                scope=_scopes(conf,adapter)[0]
                return {"global_dates":[conf["start"],conf["end"]],"scope_dates":[scope["start"],scope["end"]],
                        "security_dates":[conf["securities"][0].get("start"),conf["securities"][0].get("end")]}
            with patch.object(machine,"build_adapters",return_value={"fixture":adapter}), \
                 patch.object(machine,"resolve_universe",return_value={"securities":original["securities"],"gaps":[]}), \
                 patch.object(machine,"run_cycle",side_effect=capture):
                result=machine.execute(store,original,args)
            expected=result["global_dates"] if start or command=="update" else ["2020-01-01","2020-01-31"]
            cases.append({"case":name,"expected_scope_dates":expected,"observed":result,
                          "passed":result["scope_dates"]==expected and result["security_dates"]==expected})
        watch_args=SimpleNamespace(command="watch",interval=1,cycles=2,full_history=False)
        with patch.object(machine,"execute",side_effect=[TimeoutError("synthetic transient outage"),{"execution_complete":True}]) as execute, \
             patch.object(machine.time,"sleep"):
            try:
                observed=machine.watch(store,original,watch_args)
                watch={"returned":observed,"execute_calls":execute.call_count,"passed":execute.call_count==2}
            except Exception as exc:
                watch={"error":type(exc).__name__+": "+str(exc),"execute_calls":execute.call_count,"passed":False}
    return {"evidence_kind":"synthetic regression reproduction; no live-channel claim", "captured_at":now_utc(),
            "production_sha256":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in [ROOT/"equity_data/machine.py",ROOT/"equity_data/engine.py"]},
            "date_precedence":cases,"watch_outage":watch}


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    destination=Path(args.output)
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        raise SystemExit("Evidence exists; choose a new output path instead of overwriting.")
    destination.write_text(json.dumps(probe(),ensure_ascii=False,indent=2),encoding="utf-8")
    print(destination)
