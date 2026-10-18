"""Local orchestration, configuration scopes, resumable runs and watch loop."""
from __future__ import annotations
import copy
import json
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .budgets import Budget, BudgetExceeded
from .engine import run_cycle, run_pending, _error
from .queue import Queue
from .registry import build_adapters
from .universe import resolve_universe


def _effective_security_dates(config, args):
    """Normalize copies before any adapter can read security-level date defaults."""
    forced = args.command in {"update", "watch"}
    rows = []
    for original in config.get("securities", []):
        security = dict(original)
        for key in ("start", "end"):
            if forced or getattr(args, key, None):
                security[key] = config[key]
            else:
                security.setdefault(key, config[key])
            datetime.strptime(security[key], "%Y-%m-%d")
        if security["start"] > security["end"]:
            raise ValueError("Security effective start must not exceed end: " + str(security.get("company_id", security.get("symbol"))))
        rows.append(security)
    config["securities"] = rows


def _pool_signature(config):
    import hashlib
    from .store import _json
    rows = [{key:value for key,value in row.items() if key not in {"start","end"}}
            for row in config.get("securities", [])]
    return hashlib.sha256(_json({"market":config.get("market"),"universe":config.get("universe",{}),"securities":rows}).encode()).hexdigest()


def _cached_universe(store, config, queue, signature, reason):
    cached = store.checkpoint("machine:resolved_universe") or {}
    # Never apply another configured pool, or claim an unresolved symbol has an ID.
    rows = cached.get("securities", []) if cached.get("scope_signature") == signature else []
    if rows:
        queue.gap("UNIVERSE_CACHED_STALE", "Directory unavailable; continuing only previously evidenced identities from the same configured pool.",
                  source_id="sec_directory", status="stale", details={"resolved_at":cached.get("resolved_at"),"reason":reason})
    return [dict(row, identity_cache_resolved_at=cached.get("resolved_at"), identity_cache_stale=True) for row in rows]


def execute(store, config, args):
    from .cli import RunHttpClient
    conf = copy.deepcopy(config)
    command = args.command
    if getattr(args, "sources", None):
        conf["sources"] = [s.strip() for s in args.sources.split(",") if s.strip()]
    if getattr(args, "symbols", None):
        conf["universe"] = {"mode": "configured", "symbols": [s.strip().upper() for s in args.symbols.split(",") if s.strip()]}
        conf["securities"] = []
    if getattr(args,"data_types",None):
        conf["data_types"]=[v.strip() for v in args.data_types.split(",") if v.strip()] if isinstance(args.data_types,str) else list(args.data_types)
    conf["_refresh_archives"]=bool(getattr(args,"full_history",False) or conf.get("revision_refresh",False))
    conf["_resume_scope_ids"]=getattr(args,"resume_scope_ids",None) or conf.get("resume_scope_ids",[])
    if command in {"update", "watch"}:
        end = datetime.now(ZoneInfo("America/New_York" if conf["market"] == "US" else "Asia/Shanghai")).date()
        start = conf["start"] if getattr(args, "full_history", False) else (end - timedelta(days=conf.get("update_lookback_days",14))).isoformat()
        conf.update(start=start, end=end.isoformat())
    for key in ("start", "end"):
        if getattr(args, key, None):
            conf[key] = getattr(args,key)
    for key in ("start", "end"):
        datetime.strptime(conf[key], "%Y-%m-%d")
    if conf["start"] > conf["end"]:
        raise ValueError("start must not exceed end")
    conf["_requested_scope"]={k:copy.deepcopy(conf[k]) for k in ("market","start","end","universe","securities","data_types") if k in conf}
    client = RunHttpClient(store,user_agent=conf.get("sec",{}).get("user_agent") or "EquityDataResearch/0.2",offline=getattr(args,"offline",False),**conf.get("http",{}))
    client.budget = Budget(conf.get("budget",{}))
    queue = Queue(store)
    adapters = build_adapters(conf,store)
    need_issuers = any(getattr(a,"scope_kind",None)=="company" for a in adapters.values())
    universe = None
    universe_budget_stop_reason = None
    signature = _pool_signature(conf)
    try:
        if command in {"run","reprocess"}:
            cached = store.checkpoint("machine:resolved_universe") or {}
            if cached.get("scope_signature")==signature:
                conf["securities"] = cached.get("securities", conf.get("securities", []))
                conf["_universe_incomplete"]=any(g["code"]!="HISTORICAL_UNIVERSE_UNAVAILABLE" for g in cached.get("gaps",[]))
        elif need_issuers:
            try:
                universe = resolve_universe(store,client,conf)
                conf["_universe_incomplete"]=any(g["code"]!="HISTORICAL_UNIVERSE_UNAVAILABLE" for g in universe.get("gaps",[]))
                conf["securities"] = universe["securities"]
                from .store import now_utc
                if any(g["code"]=="SEC_DIRECTORY_UNAVAILABLE" for g in universe["gaps"]):
                    cached = _cached_universe(store,conf,queue,signature,"directory unavailable")
                    known = {row.get("company_id") for row in conf["securities"]}
                    conf["securities"] += [row for row in cached if row.get("company_id") not in known]
                else:
                    store.checkpoint("machine:resolved_universe",{**universe,"scope_signature":signature,"resolved_at":now_utc()})
                    queue.resolve_gaps(source_id="sec_directory",code="UNIVERSE_CACHED_STALE")
                for gap in universe["gaps"]:
                    queue.gap(gap["code"],gap["message"],source_id=gap["source_id"],data_type="securities",details=gap.get("details"))
            except BudgetExceeded as exc:
                conf["_universe_incomplete"]=True
                universe_budget_stop_reason = _error(exc)
                queue.gap("UNIVERSE_BUDGET_LIMIT",str(exc),source_id="sec_directory",status="not_discovered",details=client.budget.snapshot())
                conf["securities"] = []
            except (TimeoutError, ConnectionError) as exc:
                conf["_universe_incomplete"]=True
                queue.gap("UNIVERSE_TEMPORARILY_UNAVAILABLE",_error(exc),source_id="sec_directory",status="not_discovered")
                conf["securities"] = _cached_universe(store,conf,queue,signature,_error(exc))
            except Exception as exc:
                import requests
                if not isinstance(exc, requests.RequestException):
                    raise
                conf["_universe_incomplete"]=True
                queue.gap("UNIVERSE_REQUEST_FAILED",_error(exc),source_id="sec_directory",status="not_discovered")
                conf["securities"] = _cached_universe(store,conf,queue,signature,_error(exc))
        _effective_security_dates(conf,args)
        requested=copy.deepcopy(conf["_requested_scope"])
        _effective_security_dates(requested,args)
        conf["_requested_scope"]=requested
        if command == "run":
            result = run_pending(store,client,conf,adapters,max_tasks=getattr(args,"max_tasks",None))
        else:
            result = run_cycle(store,client,conf,adapters,mode="update" if command in {"update","watch"} else "backfill",
                               discover_only=command=="discover",reprocess=command=="reprocess",max_tasks=getattr(args,"max_tasks",None),
                               reprocess_versions=getattr(args,"reprocess_versions","latest"),
                               raw_ids=[value.strip() for value in getattr(args,"raw_ids","").split(",") if value.strip()] if getattr(args,"raw_ids",None) else None)
        result["universe"] = universe
        result["network_budget"] = client.budget.snapshot()
        if universe_budget_stop_reason:
            # Directory discovery precedes run_cycle. An empty task frontier
            # after its budget failure is not successful execution, and the
            # bootstrap must retain a resumable budget interruption.
            result["universe_discovery_stop_reason"] = universe_budget_stop_reason
            result["stop_reason"] = result.get("stop_reason") or universe_budget_stop_reason
            result["execution_complete"] = result["task_execution_complete"] = False
            queue.finish_run(result["run_id"], "budget_limited", result)
        if command=="discover":
            result["note"] = "Discovery roots queued only. Use run to traverse listings and build the complete discoverable frontier; pending lists are not full inventory."
        (store.root/"latest_machine_run.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        return result
    finally:
        client.session.close()


def watch(store,config,args):
    interval = max(1,float(getattr(args,"interval",None) or config.get("scheduler",{}).get("interval_seconds",3600)))
    cycles = getattr(args,"cycles",None)
    if cycles is not None and cycles < 1:
        raise ValueError("cycles must be positive")
    refresh = max(1,int(config.get("scheduler",{}).get("revision_refresh_every_cycles",24)))
    n = 0
    last = None
    failures = 0
    failed_cycles = 0
    scheduler = config.get("scheduler",{})
    max_failures = max(1,int(scheduler.get("max_consecutive_cycle_failures",5)))
    retry_base = max(1,float(scheduler.get("cycle_retry_seconds",5)))
    retry_cap = max(retry_base,float(scheduler.get("cycle_retry_max_seconds",300)))
    queue = Queue(store)
    try:
        while cycles is None or n < cycles:
            local = copy.copy(args)
            local.full_history = bool(getattr(args,"full_history",False) or n % refresh == 0)
            delay = interval
            try:
                last = execute(store,config,local)
                failures = 0
                queue.resolve_gaps(source_id="scheduler",code="WATCH_CYCLE_FAILED")
            except (ValueError,TypeError,KeyError,PermissionError,FileNotFoundError):
                # Invalid configuration or local permissions need intervention.
                # Retrying them indefinitely hides the real fault.
                raise
            except Exception as exc:
                failures += 1
                failed_cycles += 1
                last={"status":"cycle_failed","error":_error(exc),"execution_complete":False,"coverage_complete":False}
                queue.gap("WATCH_CYCLE_FAILED",_error(exc),source_id="scheduler",status="runtime_failure",
                          details={"consecutive_failures":failures,"cycle":n+1})
                delay=min(retry_cap,retry_base*2**(failures-1))
            n += 1
            from .store import now_utc
            event={"observed_at":now_utc(),"cycle":n,"full_history_revision_scan":local.full_history,"result":last,
                   "consecutive_failures":failures,"next_delay_seconds":delay}
            with (store.root/"watch_events.jsonl").open("a",encoding="utf-8") as stream:
                stream.write(json.dumps(event,ensure_ascii=False,default=str)+"\n")
            print(json.dumps(event,ensure_ascii=False,default=str),file=sys.stderr,flush=True)
            if failures>=max_failures:
                return {"status":"failed","reason":"consecutive_cycle_failure_limit","cycles":n,"failed_cycles":failed_cycles,"last_run":last}
            if cycles is None or n < cycles:
                time.sleep(delay)
    except KeyboardInterrupt:
        return {"status":"stopped","cycles":n,"resume":"run or watch resumes durable pending tasks","last_run":last}
    return {"status":"completed" if not failures else "incomplete","cycles":n,"failed_cycles":failed_cycles,"last_run":last}
