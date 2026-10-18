"""Generic, resumable discovery -> inventory -> acquire -> parse execution."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .queue import Queue, _decode, exclusive_worker
from .store import _json, now_utc


PARSE_STATES = {"parsed": "parsed", "partial": "partial_parse", "partial_parse": "partial_parse",
                "not_disclosed": "not_disclosed", "not_applicable": "not_applicable",
                "source_conflict": "source_conflict"}


def _single_worker(function):
    from functools import wraps
    @wraps(function)
    def guarded(store,*args,**kwargs):
        with exclusive_worker(store):
            return function(store,*args,**kwargs)
    return guarded


def _historical_parse(queue, client, config, adapter, resource, content, raw, run_id):
    """Reconstruct old parsing in an isolated DB; never promote an old snapshot.

    A clone is deliberate: adapters may use direct SQL, validation tombstones,
    or availability constraints. Merely proxying put_record would miss these.
    Only audit evidence leaves the sandbox; no network or new raw writes occur.
    """
    import copy
    import sqlite3
    from .adapters.base import Context
    cloned=copy.copy(queue.store)
    cloned.db=sqlite3.connect(":memory:")
    cloned.db.row_factory=sqlite3.Row
    queue.db.backup(cloned.db)
    class NoAcquisition:
        def fetch(self,*args,**kwargs):
            raise ValueError("Historical reparse cannot acquire new evidence")
    def no_raw(*args,**kwargs):
        raise ValueError("Historical reparse cannot write new raw evidence")
    cloned.put_raw=no_raw
    sandbox=Queue(cloned)
    sandbox.reprocess_only=True
    sandbox.run_id=run_id
    sandbox.generation=config.get("_generation","historical-audit")
    maximum=cloned.db.execute("SELECT COALESCE(MAX(sequence),0) FROM records").fetchone()[0]
    validation_row=cloned.db.execute("SELECT COALESCE(MAX(rowid),0) FROM record_validations").fetchone()[0]
    try:
        result=adapter.parse(Context(cloned,NoAcquisition(),dict(config,_historical_reparse_audit=True),sandbox,run_id),resource,content,raw)
        if not isinstance(result,dict) or result.get("status") not in PARSE_STATES:
            raise ValueError("Historical parser must report an explicit supported coverage status")
        ids=set(result.get("record_ids",[]))
        rows=[dict(row) for row in cloned.db.execute("SELECT * FROM records WHERE sequence>?",(maximum,))]
        ids.difference_update(row["record_id"] for row in rows)
        for record_id in sorted(ids):
            row=cloned.db.execute("SELECT * FROM records WHERE record_id=?",(record_id,)).fetchone()
            if row is not None:rows.append(dict(row))
        records=[{key:value for key,value in row.items() if key not in {"sequence","record_id","semantic_hash","created_at","supersedes"}}
                 for row in rows]
        for record in records:
            if record.get("raw_id")==raw["raw_id"]:
                record.update(retrieval_id=raw["retrieval_id"],retrieved_at=raw["retrieved_at"])
        records=sorted(records,key=lambda row:(row["dataset"],row["record_key"]))
        validations=[{key:value for key,value in dict(row).items() if key not in {"validation_id","validated_at"}}
                     for row in cloned.db.execute("SELECT * FROM record_validations WHERE rowid>?",(validation_row,))]
        effects={"isolated_validations":validations,"network_disabled":True,"production_records_modified":False,
                 "resource_metadata_saved_with_version":not resource.get("metadata",{}).get("historical_metadata_unknown",False)}
        versions=sorted({row["parser_version"] for row in records})
        canonical={key:value for key,value in result.items() if key not in {"record_ids"}}
        audit_id="reparse:"+hashlib.sha256(_json([resource["resource_id"],raw["raw_id"],raw["retrieval_id"],canonical,records,effects]).encode()).hexdigest()
        queue.db.execute("INSERT OR IGNORE INTO machine_reprocess_audits VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (audit_id,resource["resource_id"],raw["raw_id"],raw["retrieval_id"],run_id,now_utc(),_json(versions),_json(canonical),_json(records),_json(effects),
                          "reconstructed_historical_parsing_audit; not promoted to current research facts or past system knowledge"))
        queue.db.commit()
        return {**canonical,"record_ids":[],"historical_reparse_audit_id":audit_id,"historical_reparse_only":True,
                "research_records_promoted":False,"source_retrieved_at":raw["retrieved_at"]}
    finally:
        cloned.db.close()


def _safe_config(value, key=""):
    """Run metadata must never persist API credentials or authorization headers."""
    if any(term in key.lower() for term in ("secret", "password", "token", "api_key", "authorization")) and not key.endswith("_env"):
        return "REDACTED"
    if isinstance(value, dict):
        return {k: _safe_config(v, str(k)) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe_config(v) for v in value]
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        from .http import redact_url
        return redact_url(value)
    return value


def _error(exc):
    from .http import redact_url
    message = str(exc)
    message = re.sub(r"https?://[^\s<>\"']+", lambda match: redact_url(match.group()), message)
    return (type(exc).__name__ + ": " + message)[:2000]


def _scope_config(config):
    """Source-specific indicators, feeds and import paths are part of scope.

    Resource budgets are not: increasing a cap must resume the existing graph.
    Runtime-only fields and credentials never become persistent scope hashes.
    """
    excluded={"budget","budgets","scheduler","freshness","output_dir","data_root","data_dir"}
    return _safe_config({key:value for key,value in config.items() if key not in excluded and not key.startswith("_")})


def _sources(config, adapters):
    requested = config.get("sources")
    if requested is None:
        return list(adapters)
    if isinstance(requested, dict):
        return [key for key, value in requested.items() if value is not False and (not isinstance(value, dict) or value.get("enabled", True))]
    return list(dict.fromkeys(requested))


def source_scope(config,source_id):
    """Stable identity for one source and an exact requested pool/date/type scope."""
    aliases={"sec_edgar":"sec","official_ir":"ir","exchange_calendars":"calendar","public_news":"news","public_rss":"news","fred_public":"fred","federal_register":"policy","bls":"industry"}
    requested=config.get("_requested_scope",config)
    fields=("market","start","end","start_date","end_date","dates","universe","securities","data_types")
    scope={key:requested[key] for key in fields if key in requested}
    for key in ("start","end","data_types"):
        if key in config:scope[key]=config[key]
    scope["source_id"]=source_id
    option=aliases.get(source_id,source_id)
    scope["source_options"]={option:_safe_config(config.get(option,{}))}
    scope["source_options"][option].pop("user_agent",None)
    scope["source_options"][option].pop("max_depth",None)  # resumable traversal budget
    # Input order is not a different stock pool.
    if scope.get("securities"):scope["securities"]=sorted(scope["securities"],key=_json)
    if scope.get("data_types"):scope["data_types"]=sorted(scope["data_types"])
    if scope.get("universe",{}).get("symbols"):
        scope["universe"]={**scope["universe"],"symbols":sorted(scope["universe"]["symbols"])}
    return _safe_config(scope)


def source_scope_ids(config,adapters):
    return {source:"scope:"+hashlib.sha256(_json(source_scope(config,source)).encode()).hexdigest() for source in _sources(config,adapters)}


def scope_status(store,config=None,adapters=None):
    queue=Queue(store)
    if config is None:return queue.scope_checks()
    ids=source_scope_ids(config,adapters or {})
    return [row for row in queue.scope_checks() if row["scope_id"] in ids.values()]


@_single_worker
def reset_retry_budget(store,*,source_ids=None,resource_ids=None,scope_ids=None,reason="explicit_operator_retry"):
    """Explicit intervention opens a new retry epoch; attempt evidence is retained."""
    resets=Queue(store).reset_retry_budget(source_ids=source_ids,resource_ids=resource_ids,scope_ids=scope_ids,reason=reason)
    return {"reset_count":len(resets),"resets":resets,"reason":reason,"next_action":"run/update within the matching scope"}


def _configure_scopes(queue,config,adapters):
    queue.scope_ids=source_scope_ids(config,adapters)
    queue.scheduler=config.get("scheduler",{})
    queue.universe_incomplete_sources={s for s,a in adapters.items() if getattr(a,"scope_kind",None)=="company" and config.get("_universe_incomplete")}
    resume=config.get("_resume_scope_ids",config.get("resume_scope_ids",[]))
    if isinstance(resume,str):resume=[v.strip() for v in resume.split(",") if v.strip()]
    selected=set(queue.scope_ids)
    for scope_id in resume:
        row=queue.db.execute("SELECT source_id FROM machine_scope_checks WHERE scope_id=?",(scope_id,)).fetchone()
        if row is None:raise ValueError("Unknown resume scope: "+scope_id)
        if row[0] not in selected:raise ValueError("Resume scope source is not selected: "+scope_id)
    queue.resume_scope_ids=list(dict.fromkeys(resume))


def _start_scope_checks(queue,config,run_id):
    now=now_utc()
    for source,scope_id in queue.scope_ids.items():
        queue.db.execute("""INSERT INTO machine_scope_checks(scope_id,source_id,scope_json,last_attempt_at,last_run_id,current_status,progress_json)
            VALUES(?,?,?,?,?,'checking','{}') ON CONFLICT(scope_id) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,last_run_id=excluded.last_run_id,current_status='checking'""",
            (scope_id,source,_json(source_scope(config,source)),now,run_id))
    for scope_id in queue.selected_scope_ids():
        queue.db.execute("UPDATE machine_scope_checks SET last_attempt_at=?,last_run_id=?,current_status='checking' WHERE scope_id=?",(now,run_id,scope_id))
        queue.db.execute("INSERT INTO machine_scope_runs VALUES(?,?,?,NULL,'running','{}')",(run_id,scope_id,now))
    queue.db.commit()


def _finish_scope_checks(queue,run_id,summary):
    now=now_utc();out=[]
    for scope_id in queue.selected_scope_ids():
        check=_decode(queue.db.execute("SELECT * FROM machine_scope_checks WHERE scope_id=?",(scope_id,)).fetchone())
        tasks=[_decode(row) for row in queue.db.execute("SELECT DISTINCT t.* FROM machine_tasks t JOIN machine_task_demands d USING(task_id) WHERE d.scope_id=?",(scope_id,))]
        # Reparse audits are separate work and cannot prove an upstream check.
        collection=[t for t in tasks if t["task_kind"]!="reprocess"]
        roots=[t for t in collection if t["task_kind"]=="discover"]
        assets=[t for t in collection if t["resource_id"]]
        resources=[queue.resource(t["resource_id"]) for t in assets]
        gaps=[g for g in queue.gaps() if g["source_id"]==check["source_id"] and g.get("last_run_id")==run_id]
        enumeration_gaps=[g["code"] for g in gaps if g["code"] in {"ENUMERATION_INCOMPLETE","UNIVERSE_EMPTY","DATA_TYPE_NOT_SUPPORTED","SOURCE_ADAPTER_UNAVAILABLE","CAPABILITY_UNAVAILABLE","COVERAGE_DROP"}]
        if check["source_id"] in getattr(queue,"universe_incomplete_sources",set()):enumeration_gaps.append("UNIVERSE_INCOMPLETE")
        discovery=bool(roots) and all(t["state"]=="succeeded" and (t.get("result") or {}).get("enumeration_complete") for t in roots) and not enumeration_gaps
        listing_kinds={"submissions","submissions_archive","filing_index","ir_listing","policy_search","policy_detail","news_feed","metadata","observations"}
        discovery=discovery and all(t["state"]=="succeeded" and (t.get("result") or {}).get("status")!="partial" for t,r in zip(assets,resources) if r and r["kind"] in listing_kinds)
        # Listing pages may emit child listings; unclosed work blocks completeness.
        acquisition=discovery and all(r and r.get("raw_id") and r.get("refresh_state") in {"succeeded","cached"} for r in resources)
        parsing=acquisition and all(r.get("parse_state") in {"parsed","not_disclosed","not_applicable"} for r in resources) and not any(t["task_kind"]=="reprocess" and t["state"]!="succeeded" for t in tasks)
        refresh_unknown=[r["resource_id"] for r in resources if r and r.get("refresh_state")!="succeeded" and not r.get("metadata",{}).get("immutable_archive")]
        attempted=queue.db.execute("SELECT COUNT(*) FROM machine_attempts a JOIN machine_task_demands d USING(task_id) WHERE a.run_id=? AND d.scope_id=?",(run_id,scope_id)).fetchone()[0]>0
        complete=bool(assets) and parsing and not refresh_unknown and not summary.get("stop_reason") and attempted and not queue.reprocess_only
        status="complete_check" if complete else ("budget_limited" if summary.get("stop_reason") else "incomplete")
        progress={"task_states":dict(Counter(t["state"] for t in collection)),"expected_discovered_resources":len(assets),
            "saved_raw_resources":sum(bool(r and r.get("raw_id")) for r in resources),"discovery_complete":bool(discovery),
            "acquisition_complete":bool(acquisition),"parsing_complete":bool(parsing),"upstream_refresh_unconfirmed":refresh_unknown,
            "complete_check":bool(complete),"attempted_this_run":attempted,"enumeration_gaps":enumeration_gaps,
            "parse_states":dict(Counter(r.get("parse_state","unknown") for r in resources if r)),
            "data_types":sorted({r["data_type"] for r in resources if r}),"stop_reason":summary.get("stop_reason"),
            "basis":"Configured adapter inventory only; no independent recall or semantic accuracy assertion. Cached upstream responses do not establish a new check."}
        progress["by_data_type"]={}
        for dtype in progress["data_types"]:
            typed=[(t,r) for t,r in zip(assets,resources) if r and r["data_type"]==dtype]
            progress["by_data_type"][dtype]={"discovered":len(typed),"raw_saved":sum(bool(r.get("raw_id")) for t,r in typed),
                "acquisition_complete":discovery and all(bool(r.get("raw_id")) and r.get("refresh_state") in {"succeeded","cached"} for t,r in typed),
                "parse_complete":discovery and all(r.get("parse_state") in {"parsed","not_disclosed","not_applicable"} for t,r in typed),
                "parse_states":dict(Counter(r.get("parse_state","unknown") for t,r in typed)),
                "last_upstream_check_at":min((r["last_refresh_success_at"] for t,r in typed if r.get("last_refresh_success_at")),default=None)}
        if not attempted:status="not_rechecked"
        content_check_at=min((r["last_refresh_success_at"] for r in resources if r and r.get("last_refresh_success_at") and not r.get("metadata",{}).get("immutable_archive")),default=None)
        for field,passed in (("last_discovery_success_at",discovery),("last_acquisition_success_at",acquisition),("last_parse_success_at",parsing),("last_complete_check_at",complete)):
            if passed and attempted and not summary.get("stop_reason") and not queue.reprocess_only:
                # Cached discovery/current pages cannot advance freshness clocks.
                if field in {"last_discovery_success_at","last_acquisition_success_at"} and refresh_unknown:continue
                clock=now if field=="last_parse_success_at" else content_check_at
                if clock:queue.db.execute("UPDATE machine_scope_checks SET "+field+"=CASE WHEN "+field+" IS NULL OR "+field+"<? THEN ? ELSE "+field+" END WHERE scope_id=?",(clock,clock,scope_id))
        queue.db.execute("UPDATE machine_scope_checks SET current_status=?,progress_json=? WHERE scope_id=?",(status,_json(progress),scope_id))
        queue.db.execute("UPDATE machine_scope_runs SET finished_at=?,status=?,progress_json=? WHERE run_id=? AND scope_id=?",(now,status,_json(progress),run_id,scope_id))
        out.append(_decode(queue.db.execute("SELECT * FROM machine_scope_checks WHERE scope_id=?",(scope_id,)).fetchone()))
    queue.db.commit();return out


def _scopes(config, adapter):
    dates = config.get("dates", {})
    scope = {"market": config.get("market", "US"),
             "start": config.get("start", config.get("start_date", dates.get("start"))),
             "end": config.get("end", config.get("end_date", dates.get("end"))),
             "data_types": config.get("data_types", list(getattr(adapter, "data_types", ())))}
    if getattr(adapter, "scope_kind", "global") != "company":
        return [scope]
    securities = config.get("securities", [])
    # Multiple securities may have the same issuer. Issuer disclosures should be
    # enumerated once, while the original security mapping remains in scope.
    grouped = {}
    for security in securities:
        company_id = security.get("company_id") or ("sec:" + str(security["cik"]).zfill(10) if security.get("cik") else None)
        if company_id is None:
            continue
        if company_id not in grouped:
            grouped[company_id] = {**scope, **security, "company_id": company_id, "company": dict(security), "security": dict(security), "securities": []}
        grouped[company_id]["securities"].append(dict(security))
    return list(grouped.values())


def _prepare(queue, config, adapters, generation, mode):
    from .adapters.base import Context
    source_ids = _sources(config, adapters)
    for source_id in source_ids:
        adapter = adapters.get(source_id)
        if adapter is None:
            queue.gap("SOURCE_ADAPTER_UNAVAILABLE", "No installed adapter for configured source.", source_id=source_id, status="capability_gap")
            continue
        requested = set(config.get("data_types", ()))
        supported = set(getattr(adapter, "data_types", ()))
        if requested and supported and not requested.intersection(supported):
            queue.gap("DATA_TYPE_NOT_SUPPORTED", "Configured source does not implement any requested data types.", source_id=source_id,
                      details={"requested": sorted(requested), "supported": sorted(supported)})
            continue
        scopes = _scopes(config, adapter)
        if not scopes:
            queue.gap("UNIVERSE_EMPTY", "No resolved companies available for issuer discovery.", source_id=source_id, status="not_discovered")
        for scope in scopes:
            queue.enqueue_discovery(source_id, scope, generation)
    return source_ids


def _refresh_discovery_dependencies(queue, adapters, selected, *, generation=None, upstream_source_id=None, company_id=None):
    """Wake discovery when a declared upstream channel has new stored evidence.

    Dependencies are subscriptions, not global barriers: direct configured IR
    links can be discovered immediately, and an unavailable SEC source cannot
    block the other adapter. Fingerprints omit acquisition timestamps so an
    identical re-download does not create a discovery loop.
    """
    for source_id in selected:
        adapter=adapters.get(source_id)
        dependencies=set(getattr(adapter,"discovery_dependencies",())).intersection(selected).intersection(adapters)
        if upstream_source_id is not None:
            dependencies.intersection_update({upstream_source_id})
        if not dependencies:
            continue
        roots=queue.db.execute("SELECT * FROM machine_tasks WHERE task_kind='discover' AND source_id=?"+(" AND generation=?" if generation else ""),
                               (source_id,generation) if generation else (source_id,)).fetchall()
        if generation is None:
            # A resume should refresh the newest request for each scope; old
            # completed update cycles are historical execution evidence.
            roots=list({row["scope_json"]:row for row in sorted(roots,key=lambda row:row["created_at"])}.values())
        for root in roots:
            scope=json.loads(root["scope_json"])
            target_company=scope.get("company_id")
            if company_id is not None and target_company not in {None,company_id}:
                continue
            for dependency in dependencies:
                parameters=[dependency]
                where="source=?"
                inventory_where="i.source_id=?"
                if target_company is not None:
                    where+=" AND company_id=?"
                    inventory_where+=" AND i.company_id=?"
                    parameters.append(target_company)
                record_evidence=queue.db.execute("SELECT COUNT(*),MAX(sequence) FROM records WHERE "+where,parameters).fetchone()
                raw_evidence=[tuple(row) for row in queue.db.execute("""SELECT DISTINCT v.resource_id,v.raw_id FROM machine_resource_versions v
                    JOIN machine_inventory i USING(resource_id) WHERE """+inventory_where+" AND v.parse_state IN ('parsed','partial_parse','not_disclosed','not_applicable','source_conflict') ORDER BY v.resource_id,v.raw_id",parameters)]
                if not record_evidence[0] and not raw_evidence:
                    continue
                fingerprint=hashlib.sha256(_json({"records":tuple(record_evidence),"originals":raw_evidence}).encode()).hexdigest()
                queue.mark_discovery_dependency(root["task_id"],dependency,fingerprint)


def _execute(queue, client, config, adapters, run_id, *, discover_only, max_tasks):
    from .adapters.base import Context, DependencyMissing, UnsupportedCapability
    from .budgets import BudgetExceeded
    started = time.monotonic()
    limits = config.get("budget", config.get("budgets", {}))
    scheduler = config.get("scheduler", {})
    configured_max = limits.get("max_tasks", scheduler.get("max_tasks"))
    limit = max_tasks if max_tasks is not None else configured_max
    if limit is not None and int(limit) < 0:
        raise ValueError("max_tasks cannot be negative")
    limit = int(limit) if limit is not None else None
    max_runtime = float(limits.get("max_runtime_seconds", scheduler.get("max_runtime_seconds", 900)))
    max_attempts = max(1, int(scheduler.get("max_task_attempts_per_run", 2)))
    retry_seconds = max(0, float(scheduler.get("retry_seconds", 30)))
    lease_seconds = max(30, float(scheduler.get("lease_seconds", 300)))
    selected = _sources(config, adapters)
    budget = getattr(client, "budget", None)
    counters = Counter(executed=0, succeeded=0, failed=0, blocked=0, deferred=0, partial=0)
    stop_reason = None
    dependency_batch=max(1,int(scheduler.get("dependency_batch_tasks",8)))
    if not queue.reprocess_only:
        _refresh_discovery_dependencies(queue,adapters,selected,generation=config.get("_generation"))
        queue.activate_dirty_discoveries(selected)
    for source_id in selected:
        adapter = adapters.get(source_id)
        if adapter is None:
            continue
        try:
            queue.health(source_id, adapter.health(Context(queue.store, client, config, queue, run_id)))
        except Exception as exc:
            queue.health(source_id, {"status": "health_check_failed", "error": _error(exc)})
    while True:
        if not queue.reprocess_only and (counters["executed"]%dependency_batch==0 or (limit is not None and counters["executed"]>=limit)):
            queue.activate_dirty_discoveries(selected)
        if limit is not None and counters["executed"] >= limit:
            unfinished = [t for t in queue.scoped_tasks() if t["source_id"] in selected and t["state"] in {"pending","retry_wait"}
                          and (not discover_only or t["task_kind"]=="discover")
                          and (not queue.reprocess_only or (t["task_kind"]=="reprocess" and t["generation"]==queue.generation))]
            stop_reason = "max_tasks" if unfinished else None
            break
        if time.monotonic() - started >= max_runtime:
            stop_reason = "max_runtime_seconds"
            break
        try:
            if budget is not None:
                budget.check_time()
        except BudgetExceeded as exc:
            stop_reason = _error(exc)
            break
        queue.heartbeat(run_id, lease_seconds)
        task = queue.claim(run_id, discovery_only=discover_only, source_ids=selected, lease_seconds=lease_seconds, reprocess_only=queue.reprocess_only)
        if task is None:
            if not queue.reprocess_only and queue.activate_dirty_discoveries(selected):
                continue
            break
        counters["executed"] += 1
        runtime_config = dict(config, _generation=task["generation"])
        active_scope=queue.scope_ids.get(task["source_id"]) if queue.scope_ids else None
        queue.current_scope_id=active_scope
        if queue.resume_scope_ids and not queue.db.execute("SELECT 1 FROM machine_task_demands WHERE task_id=? AND scope_id=?",(task["task_id"],active_scope)).fetchone():
            older=queue.db.execute("SELECT c.scope_json,c.scope_id FROM machine_scope_checks c JOIN machine_task_demands d USING(scope_id) WHERE d.task_id=? AND c.scope_id IN ("+",".join("?" for _ in queue.resume_scope_ids)+") ORDER BY d.requested_at DESC LIMIT 1",[task["task_id"],*queue.resume_scope_ids]).fetchone()
            if older:
                queue.current_scope_id=older["scope_id"]
                saved=json.loads(older[0]);runtime_config.update({k:v for k,v in saved.items() if k in {"market","start","end","data_types","securities","universe"}})
                runtime_config.update(saved.get("source_options",{}))
        runtime_config["_mode"] = "incremental" if task["generation"].startswith("update:") else config.get("_mode", "backfill")
        ctx = Context(queue.store, client, runtime_config, queue, run_id)
        adapter = adapters.get(task["source_id"])
        dependency_snapshot=queue.dependency_snapshot(task["task_id"]) if task["task_kind"]=="discover" else {}
        try:
            if adapter is None:
                raise UnsupportedCapability("No adapter installed for " + task["source_id"])
            if task["task_kind"] == "discover":
                method = adapter.incremental if runtime_config["_mode"] in {"update", "incremental"} else adapter.backfill
                result = method(ctx, task["scope"])
                if not isinstance(result, dict) or "enumeration_complete" not in result:
                    raise ValueError("Discovery must explicitly report enumeration_complete")
                if not result["enumeration_complete"]:
                    queue.gap("ENUMERATION_INCOMPLETE", "Discovery is incomplete; the emitted inventory is not complete coverage.",
                              source_id=task["source_id"], company_id=task["scope"].get("company_id"), status="not_discovered",
                              details={"task_id": task["task_id"], "result": result})
                else:
                    queue.resolve_gaps(source_id=task["source_id"], code="ENUMERATION_INCOMPLETE", company_id=task["scope"].get("company_id"))
                if dependency_snapshot:
                    result={**result,"dependency_evidence":dependency_snapshot}
                queue.finish(task, result)
            else:
                resource = task["resource"]
                if task["raw_id"]:
                    content, raw = queue.raw_for_task(task)
                elif task["task_kind"] == "reprocess":
                    raise ValueError("Cannot reprocess: no saved raw retrieval")
                else:
                    content, raw = adapter.acquire(ctx, resource)
                    if hashlib.sha256(content).hexdigest() != raw.get("sha256"):
                        raise ValueError("Acquired bytes do not match persisted raw SHA-256")
                    queue.acquisition_checkpoint(task, raw)
                queue.heartbeat(run_id, lease_seconds)
                latest=queue.resource(task["resource_id"])
                historical=(task["task_kind"]=="reprocess" and
                            (raw["raw_id"],raw["retrieval_id"])!=(latest.get("raw_id"),latest.get("retrieval_id")))
                fingerprint=None
                if not historical and not queue.reprocess_only and hasattr(adapter,"parse_input_fingerprint"):
                    fingerprint=adapter.parse_input_fingerprint(ctx,resource,raw)
                # Only the latest applicable parse may be reused; A→B→A needs
                # a real parse to restore/revalidate claims rejected by B.
                latest_parse=queue.db.execute("SELECT raw_id,parsed_at,parse_result_json FROM machine_resource_versions WHERE resource_id=? AND parsed_at IS NOT NULL ORDER BY parsed_at DESC,rowid DESC LIMIT 1",(task["resource_id"],)).fetchone()
                previous=json.loads(latest_parse["parse_result_json"]) if latest_parse and latest_parse["parse_result_json"] else {}
                if fingerprint and latest_parse and latest_parse["raw_id"]==raw["raw_id"] and previous.get("input_fingerprint")==fingerprint:
                    result={**previous,"cache_reused":True,"cached_parsed_at":latest_parse["parsed_at"]}
                    counters["parse_cache_hits"]+=1
                else:
                    result = _historical_parse(queue,client,runtime_config,adapter,resource,content,raw,run_id) if historical else adapter.parse(ctx, resource, content, raw)
                    if fingerprint:result={**result,"input_fingerprint":fingerprint,"cache_reused":False}
                if not isinstance(result, dict) or result.get("status") not in PARSE_STATES:
                    raise ValueError("Parser must return an explicit supported coverage status")
                state = PARSE_STATES[result["status"]]
                if state == "partial_parse":
                    counters["partial"] += 1
                    queue.gap("PARTIAL_PARSE", "Original acquired; some requested fields remain unparsed.",
                              source_id=task["source_id"], company_id=resource.get("company_id"), data_type=resource["data_type"],
                              status=state, details={"resource_id": task["resource_id"], "raw_id": raw["raw_id"], "errors": result.get("errors", [])})
                elif state == "source_conflict":
                    queue.gap("SOURCE_CONFLICT", "Parsed source conflicts with another supplied source.", source_id=task["source_id"],
                              company_id=resource.get("company_id"),data_type=resource["data_type"],status=state,
                              details={"resource_id":task["resource_id"],"raw_id":raw["raw_id"],"errors":result.get("errors",[])})
                queue.finish(task, result, resource_state=state)
                if not historical and hasattr(adapter,"after_resource_parsed"):
                    try:
                        adapter.after_resource_parsed(ctx,resource,raw,result)
                    except Exception as exc:
                        # The parse is durably complete. Dependency bookkeeping
                        # failure cannot overwrite that completed attempt.
                        queue.gap("DEPENDENCY_WAKEUP_FAILED",_error(exc),source_id=task["source_id"],
                                  company_id=resource.get("company_id"),status="partial_parse",
                                  details={"task_id":task["task_id"],"resource_id":task["resource_id"]})
                if not queue.reprocess_only:
                    _refresh_discovery_dependencies(queue,adapters,selected,generation=task["generation"],
                                                    upstream_source_id=task["source_id"],company_id=resource.get("company_id"))
            counters["succeeded"] += 1
        except BudgetExceeded as exc:
            queue.fail(task, _error(exc), state="deferred", resource_state="downloaded_unparsed" if task["raw_id"] else None)
            queue.gap("BUDGET_EXHAUSTED", _error(exc), source_id=task["source_id"], status="resource_limited",
                      details={"run_id":run_id, "task_id":task["task_id"]})
            counters["deferred"] += 1
            stop_reason = _error(exc)
            break
        except (DependencyMissing, UnsupportedCapability) as exc:
            state = "missing_authorization" if isinstance(exc, DependencyMissing) else None
            queue.fail(task, _error(exc), state="blocked", resource_state=state)
            queue.gap("DEPENDENCY_MISSING" if isinstance(exc, DependencyMissing) else "CAPABILITY_UNAVAILABLE", _error(exc),
                      source_id=task["source_id"], company_id=(task.get("resource") or task["scope"]).get("company_id"),
                      status=state or "capability_gap", details={"task_id":task["task_id"]})
            counters["blocked"] += 1
        except Exception as exc:
            attempts = queue.db.execute("SELECT COUNT(*) FROM machine_attempts WHERE task_id=? AND run_id=?", (task["task_id"],run_id)).fetchone()[0]
            retry = attempts < max_attempts
            state = "retry_wait" if retry else "failed"
            resource_state = "downloaded_unparsed" if task["raw_id"] else "download_failed"
            queue.fail(task, _error(exc), state=state, resource_state=resource_state, retry_seconds=retry_seconds)
            queue.gap("PARSE_FAILED" if task["raw_id"] else "TASK_FAILED", _error(exc), source_id=task["source_id"],
                      company_id=(task.get("resource") or task["scope"]).get("company_id"), status=resource_state,
                      details={"task_id":task["task_id"], "raw_id":task["raw_id"]})
            counters["failed"] += 1
        finally:
            queue.current_scope_id=None
            if task["task_kind"]=="discover":
                # A failed dependent discovery retries through normal bounded
                # task policy. The same unchanged evidence must not loop forever.
                queue.consume_dependencies(task["task_id"],dependency_snapshot)
    if not queue.reprocess_only:
        queue.activate_dirty_discoveries(selected)
    if stop_reason:
        queue.gap("RUN_RESOURCE_LIMIT", "Execution stopped at its configured budget; unexecuted tasks remain resumable.",
                  source_id="scheduler",status="resource_limited",details={"run_id":run_id,"reason":stop_reason})
    current_tasks=[t for t in queue.scoped_tasks() if t["source_id"] in selected and (not queue.reprocess_only or t["generation"]==queue.generation)]
    remaining = list(Counter(t["state"] for t in current_tasks if t["state"]!="succeeded").items())
    summary = {"run_id":run_id, **dict(counters), "stop_reason":stop_reason,
               "remaining_tasks":dict(remaining), "elapsed_seconds":round(time.monotonic()-started,3),
               "budget":budget.snapshot() if budget is not None else None,
               "coverage_complete":False,
               "coverage_note":"Task execution and source/field completeness are separate; inspect inventory and unresolved gaps."}
    observed = queue.db.execute("SELECT DISTINCT t.source_id,t.resource_id FROM machine_tasks t JOIN machine_task_demands d USING(task_id) WHERE d.generation=? AND t.resource_id IS NOT NULL",
                                (config.get("_generation",queue.generation),)).fetchall()
    summary["generation_inventory_by_source"] = dict(Counter(row["source_id"] for row in observed))
    # Compare only runs with the same requested scope. Historic inventory remains
    # retained, so a disappearing source response must be compared by generation.
    summary["scope_hash"] = hashlib.sha256(_json(_scope_config(config)).encode()).hexdigest()
    summary["coverage_drop"] = []
    if config.get("_mode") in {"update","incremental"} and counters["executed"] and not stop_reason:
        for prior_row in queue.db.execute("SELECT summary_json FROM machine_runs WHERE run_id!=? AND summary_json IS NOT NULL ORDER BY started_at DESC",(run_id,)):
            prior=json.loads(prior_row[0])
            if prior.get("scope_hash")!=summary["scope_hash"] or not prior.get("generation_inventory_by_source"):
                continue
            for source_id,previous_count in prior["generation_inventory_by_source"].items():
                current_count=summary["generation_inventory_by_source"].get(source_id,0)
                if current_count<previous_count:
                    change={"source_id":source_id,"previous":previous_count,"current":current_count,"previous_run_id":prior.get("run_id")}
                    summary["coverage_drop"].append(change)
                    queue.gap("COVERAGE_DROP","Current discovery generation returned fewer resources than the previous matching scope; inspect source or enumeration changes.",
                              source_id=source_id,status="coverage_drop",details={**change,"run_id":run_id})
                else:
                    queue.resolve_gaps(source_id=source_id,code="COVERAGE_DROP")
            break
    summary["execution_complete"] = not any(count for state,count in remaining)
    summary["task_execution_complete"] = summary["execution_complete"]
    generation_tasks=[task for task in current_tasks if task["source_id"] in selected and
                      (task["generation"]==config.get("_generation") if config.get("_generation") else True)]
    parse_tasks=[task for task in generation_tasks if task["task_kind"] in {"acquire","reprocess"}]
    summary["parsing_complete"] = bool(parse_tasks) and all(task["state"]=="succeeded" and
        (task.get("result") or {}).get("status") in {"parsed","not_applicable","not_disclosed"} for task in parse_tasks)
    summary["parsing_completion_basis"] = "Every acquired/reprocessed task in this generation reports complete supported parsing; no assertion of target-field recall or undiscovered-resource coverage. False when no parse task exists."
    if summary["execution_complete"]:
        queue.resolve_gaps(source_id="scheduler",code="RUN_RESOURCE_LIMIT")
    run_state = "budget_limited" if stop_reason else ("incomplete" if remaining else "completed")
    summary["source_scope_checks"]=_finish_scope_checks(queue,run_id,summary)
    summary["effective_scope"]={"market":config.get("market"),"start":config.get("start"),"end":config.get("end"),
        "requested_data_types":config.get("data_types"),"source_scope_ids":queue.scope_ids,"resumed_scope_ids":queue.resume_scope_ids}
    queue.finish_run(run_id, run_state, summary)
    return summary


def _run_guarded(queue, client, config, adapters, run_id, *, discover_only, max_tasks):
    try:
        return _execute(queue,client,config,adapters,run_id,discover_only=discover_only,max_tasks=max_tasks)
    except BaseException as exc:
        # Ctrl-C after acquiring a document must remain immediately resumable.
        # Persistent workers with an expired lease are handled separately.
        reason=_error(exc)
        queue.release_run_tasks(run_id,reason)
        _finish_scope_checks(queue,run_id,{"stop_reason":reason})
        queue.finish_run(run_id,"interrupted" if isinstance(exc,KeyboardInterrupt) else "failed",{"error":reason,"execution_complete":False,"coverage_complete":False})
        raise


@_single_worker
def run_cycle(store, client, config, adapters, *, mode="backfill", discover_only=False, reprocess=False, max_tasks=None,
              reprocess_versions="latest", raw_ids=None):
    if mode not in {"backfill", "update", "incremental"}:
        raise ValueError("mode must be backfill, update or incremental")
    if reprocess_versions not in {"latest","all"}:
        raise ValueError("reprocess_versions must be latest or all")
    queue = Queue(store)
    _configure_scopes(queue,config,adapters)
    queue.recover_expired()
    stable_scope = _scope_config(config)
    generation = ("reprocess:" if reprocess else "update:") + str(uuid.uuid4()) if reprocess or mode!="backfill" else "backfill:" + hashlib.sha256(_json(stable_scope).encode()).hexdigest()
    runtime_config = dict(config, _generation=generation, _mode=mode,
                          _reprocess_versions=reprocess_versions,_reprocess_raw_ids=raw_ids)
    run_id = queue.start_run("reprocess" if reprocess else mode, generation, _safe_config(runtime_config))
    _start_scope_checks(queue,runtime_config,run_id)
    if not reprocess:
        queue.retry_tasks(source_ids=_sources(config,adapters))
    if reprocess:
        queue.reprocess_only = True
        selected = set(_sources(config,adapters))
        companies = {s.get("company_id") for s in config.get("securities",[]) if s.get("company_id")}
        matched_raw_ids=set()
        for resource in queue.resources():
            if resource["source_id"] not in selected or (companies and resource["company_id"] and resource["company_id"] not in companies):
                continue
            if config.get("data_types") and resource["data_type"] not in config["data_types"]:
                continue
            if resource["raw_id"]:
                versions=[dict(row) for row in queue.db.execute("SELECT * FROM machine_resource_versions WHERE resource_id=? ORDER BY acquired_at,rowid",(resource["resource_id"],))]
                if reprocess_versions=="latest":
                    versions=[row for row in versions if row["retrieval_id"]==resource["retrieval_id"]]
                for version in versions:
                    if raw_ids and version["raw_id"] not in raw_ids:
                        continue
                    matched_raw_ids.add(version["raw_id"])
                    snapshot=(dict(resource) if version["retrieval_id"]==resource["retrieval_id"] else
                              json.loads(version["resource_json"]) if version.get("resource_json") else dict(resource))
                    if not version.get("resource_json") and version["retrieval_id"]!=resource["retrieval_id"]:
                        snapshot["metadata"]={**snapshot.get("metadata",{}),"historical_metadata_unknown":True}
                    queue.enqueue_reprocess(snapshot,generation,raw_id=version["raw_id"],retrieval_id=version["retrieval_id"])
            else:
                queue.gap("RAW_NOT_SAVED", "Requested resource has no acquired raw evidence to reprocess.",source_id=resource["source_id"],
                          company_id=resource["company_id"],data_type=resource["data_type"],status="discovered_not_downloaded",details={"resource_id":resource["resource_id"]})
        for raw_id in set(raw_ids or [])-matched_raw_ids:
            queue.gap("REPROCESS_RAW_NOT_SELECTED","Requested raw ID is unavailable in the selected source/company/type/version scope.",source_id="scheduler",status="not_selected",details={"raw_id":raw_id})
    else:
        _prepare(queue,runtime_config,adapters,generation,mode)
    return _run_guarded(queue,client,runtime_config,adapters,run_id,discover_only=discover_only,max_tasks=max_tasks)


@_single_worker
def run_pending(store, client, config, adapters, *, discover_only=False, max_tasks=None):
    queue = Queue(store)
    _configure_scopes(queue,config,adapters)
    queue.recover_expired()
    run_id = queue.start_run("resume", "resume:"+str(uuid.uuid4()), _safe_config(config))
    _start_scope_checks(queue,config,run_id)
    queue.retry_tasks(source_ids=_sources(config,adapters))
    return _run_guarded(queue,client,config,adapters,run_id,discover_only=discover_only,max_tasks=max_tasks)


def status_report(store):
    queue = Queue(store)
    resources, tasks, gaps = queue.resources(), queue.tasks(), queue.gaps()
    grouped = defaultdict(lambda:{"resources":0,"states":Counter(),"companies":set(),"data_types":set()})
    for resource in resources:
        item = grouped[resource["source_id"]]
        item["resources"] += 1
        item["states"][resource["state"]] += 1
        if resource["company_id"]:
            item["companies"].add(resource["company_id"])
        item["data_types"].add(resource["data_type"])
    sources = {}
    now = datetime.now(timezone.utc)
    for source_id,item in grouped.items():
        sources[source_id] = {"resources":item["resources"],"states":dict(item["states"]),"companies":sorted(item["companies"]),"data_types":sorted(item["data_types"])}
    health = [_decode(row) for row in store.db.execute("SELECT * FROM machine_source_health ORDER BY source_id")]
    for source in health:
        latest = store.db.execute("SELECT MAX(r.retrieved_at) FROM machine_inventory i JOIN raw_retrievals r ON r.retrieval_id=i.retrieval_id WHERE i.source_id=?", (source["source_id"],)).fetchone()[0]
        source["latest_acquired_at"] = latest
        source["acquisition_age_seconds"] = round((now-datetime.fromisoformat(latest.replace("Z","+00:00"))).total_seconds(),1) if latest else None
        source["open_task_states"] = dict(Counter(t["state"] for t in tasks if t["source_id"]==source["source_id"] and t["state"]!="succeeded"))
        if source["open_task_states"].get("failed") or source["open_task_states"].get("retry_wait"):
            source["status"] = "degraded"
    latest_run = _decode(store.db.execute("SELECT * FROM machine_runs ORDER BY started_at DESC,rowid DESC LIMIT 1").fetchone())
    freshness = ((latest_run or {}).get("config") or {}).get("freshness", {})
    thresholds = freshness.get("max_age_hours_by_source", {})
    for source in health:
        threshold = thresholds.get(source["source_id"],freshness.get("max_age_hours",48))
        source["freshness_threshold_hours"] = threshold
        source["freshness_status"] = "never_acquired" if source["acquisition_age_seconds"] is None else (
            "stale" if source["acquisition_age_seconds"]>float(threshold)*3600 else "recently_acquired")
        source["freshness_basis"] = "Configured acquisition-age threshold; does not prove provider publication timeliness."
    return {"generated_at":now_utc(),"summary":{"resource_count":len(resources),"resource_states":dict(Counter(r["state"] for r in resources)),
             "task_count":len(tasks),"task_states":dict(Counter(t["state"] for t in tasks)),"unresolved_gaps":len(gaps),
             "coverage_complete":False,"completeness_basis":"Inventory covers discovered source resources only; unsupported sources, enumeration and field gaps are explicit."},
            "sources":sources,"source_health":health,"latest_run":latest_run,"resources":resources,"tasks":tasks,"gaps":gaps,
            "source_scope_checks":queue.scope_checks(),"work_items":[_decode(row) for row in store.db.execute("SELECT * FROM machine_work_items")],
            "task_scope_demands":[dict(row) for row in store.db.execute("SELECT * FROM machine_task_demands")],
            "retry_resets":[dict(row) for row in store.db.execute("SELECT * FROM machine_retry_resets ORDER BY reset_at")],
            "request_failure_gates":[_decode(row) for row in store.db.execute("SELECT * FROM machine_request_failures")],
            "discovery_dependencies":[dict(row) for row in store.db.execute("SELECT * FROM machine_discovery_dependencies ORDER BY observed_at,task_id")],
            "temporal_policy":"Raw retrieval time is system acquisition evidence. Public availability and source revision time require separate source evidence; inventory discovery is not historical availability."}
