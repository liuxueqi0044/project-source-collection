"""Durable single-host acquisition inventory, leases, tasks and run evidence.

The inventory describes expected resources. Task success means a task executed,
not that a source disclosed every requested field. Those are reported separately.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .store import _json, now_utc


SCHEMA = """
CREATE TABLE IF NOT EXISTS machine_inventory (
 resource_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, resource_key TEXT NOT NULL,
 kind TEXT NOT NULL, data_type TEXT NOT NULL, url TEXT, company_id TEXT, security_id TEXT,
 metadata_json TEXT NOT NULL, state TEXT NOT NULL, first_discovered_at TEXT NOT NULL,
 last_discovered_at TEXT NOT NULL, last_attempt_at TEXT, last_success_at TEXT,
 raw_id TEXT, retrieval_id TEXT, last_error TEXT, UNIQUE(source_id,resource_key)
);
CREATE TABLE IF NOT EXISTS machine_tasks (
 task_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, task_kind TEXT NOT NULL,
 resource_id TEXT REFERENCES machine_inventory(resource_id), generation TEXT NOT NULL,
 scope_json TEXT NOT NULL, resource_json TEXT, state TEXT NOT NULL,
 attempt_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 next_attempt_at TEXT, owner_run_id TEXT, lease_until TEXT,
 raw_id TEXT, retrieval_id TEXT, result_json TEXT, last_error TEXT,
 UNIQUE(source_id,task_kind,resource_id,generation,scope_json)
);
CREATE INDEX IF NOT EXISTS machine_tasks_ready ON machine_tasks(state,next_attempt_at,created_at);
CREATE TABLE IF NOT EXISTS machine_attempts (
 attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES machine_tasks(task_id),
 run_id TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
 state TEXT NOT NULL, stage TEXT NOT NULL, error TEXT, raw_id TEXT, result_json TEXT
);
CREATE TABLE IF NOT EXISTS machine_runs (
 run_id TEXT PRIMARY KEY, mode TEXT NOT NULL, generation TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT, heartbeat_at TEXT NOT NULL, lease_until TEXT NOT NULL,
 state TEXT NOT NULL, config_json TEXT NOT NULL, summary_json TEXT
);
CREATE TABLE IF NOT EXISTS machine_gaps (
 gap_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, company_id TEXT, data_type TEXT,
 code TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, details_json TEXT NOT NULL,
 first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, occurrences INTEGER NOT NULL,
 last_run_id TEXT, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS machine_source_health (
 source_id TEXT PRIMARY KEY, last_attempt_at TEXT, last_success_at TEXT,
 last_error_at TEXT, last_error TEXT, status TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0,
 failures INTEGER NOT NULL DEFAULT 0, capabilities_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS machine_resource_versions (
 version_id TEXT PRIMARY KEY, resource_id TEXT NOT NULL REFERENCES machine_inventory(resource_id),
 raw_id TEXT NOT NULL REFERENCES raw_blobs(raw_id),
 retrieval_id TEXT NOT NULL REFERENCES raw_retrievals(retrieval_id),
 acquired_at TEXT NOT NULL, parse_state TEXT NOT NULL, parsed_at TEXT,
 parse_result_json TEXT, UNIQUE(resource_id,retrieval_id)
);
CREATE TABLE IF NOT EXISTS machine_discovery_dependencies (
 task_id TEXT NOT NULL REFERENCES machine_tasks(task_id), upstream_source_id TEXT NOT NULL,
 requested_fingerprint TEXT NOT NULL, consumed_fingerprint TEXT,
 observed_at TEXT NOT NULL, consumed_at TEXT,
 PRIMARY KEY(task_id,upstream_source_id)
);
CREATE TABLE IF NOT EXISTS machine_reprocess_audits (
 audit_id TEXT PRIMARY KEY, resource_id TEXT NOT NULL, raw_id TEXT NOT NULL,
 retrieval_id TEXT NOT NULL, run_id TEXT NOT NULL, parsed_at TEXT NOT NULL,
 parser_versions_json TEXT NOT NULL, result_json TEXT NOT NULL,
 records_json TEXT NOT NULL, effects_json TEXT NOT NULL, interpretation TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS machine_work_items (
 work_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE, source_id TEXT NOT NULL,
 resource_id TEXT, request_json TEXT NOT NULL, failure_class TEXT,
 consecutive_failures INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT,
 last_success_at TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS machine_task_demands (
 task_id TEXT NOT NULL, generation TEXT NOT NULL, scope_id TEXT NOT NULL,
 requested_at TEXT NOT NULL, PRIMARY KEY(task_id,generation,scope_id)
);
CREATE INDEX IF NOT EXISTS demand_scope ON machine_task_demands(scope_id,task_id);
CREATE TABLE IF NOT EXISTS machine_scope_checks (
 scope_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,scope_json TEXT NOT NULL,
 last_attempt_at TEXT,last_discovery_success_at TEXT,last_acquisition_success_at TEXT,
 last_parse_success_at TEXT,last_complete_check_at TEXT,last_run_id TEXT,
 current_status TEXT NOT NULL,progress_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS machine_scope_runs (
 run_id TEXT NOT NULL,scope_id TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,
 status TEXT NOT NULL,progress_json TEXT NOT NULL,PRIMARY KEY(run_id,scope_id)
);
CREATE TABLE IF NOT EXISTS machine_parse_cache (
 resource_id TEXT NOT NULL,raw_id TEXT NOT NULL,input_fingerprint TEXT NOT NULL,
 parsed_at TEXT NOT NULL,result_json TEXT NOT NULL,PRIMARY KEY(resource_id,raw_id,input_fingerprint)
);
CREATE TABLE IF NOT EXISTS machine_retry_resets (
 reset_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,source_id TEXT NOT NULL,reset_at TEXT NOT NULL,
 previous_epoch INTEGER NOT NULL,new_epoch INTEGER NOT NULL,previous_failures INTEGER NOT NULL,reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS machine_request_failures (
 request_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,request_json TEXT NOT NULL,
 failure_class TEXT,consecutive_failures INTEGER NOT NULL DEFAULT 0,retry_epoch INTEGER NOT NULL DEFAULT 1,
 next_retry_at TEXT,exhausted_at TEXT,last_failure_at TEXT,last_success_at TEXT
);
"""


class StaleTaskOwner(RuntimeError):
    """A recovered/stopped worker cannot mutate a newly owned task."""


@contextmanager
def exclusive_worker(store):
    """OS-released single-worker lock, held across long downloads and parses."""
    path = store.root / ".machine-worker.lock"
    stream = path.open("a+b")
    try:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            import os
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another acquisition worker owns this data directory; only one worker is supported") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    finally:
        stream.close()


def ensure_schema(store):
    store.db.executescript(SCHEMA)
    columns={row[1] for row in store.db.execute("PRAGMA table_info(machine_resource_versions)")}
    if "resource_json" not in columns:
        store.db.execute("ALTER TABLE machine_resource_versions ADD COLUMN resource_json TEXT")
    additions={"machine_inventory":{"raw_state":"TEXT NOT NULL DEFAULT 'missing'","refresh_state":"TEXT NOT NULL DEFAULT 'not_checked'",
        "parse_state":"TEXT NOT NULL DEFAULT 'unknown'","upstream_state":"TEXT NOT NULL DEFAULT 'unknown'","last_refresh_attempt_at":"TEXT",
        "last_refresh_success_at":"TEXT","last_parsed_at":"TEXT","failure_class":"TEXT","refresh_generation":"TEXT"},
        "machine_attempts":{"failure_class":"TEXT","retry_at":"TEXT","retry_epoch":"INTEGER NOT NULL DEFAULT 1"},
        "machine_work_items":{"retry_epoch":"INTEGER NOT NULL DEFAULT 1","exhausted_at":"TEXT","request_id":"TEXT"}}
    for table,fields in additions.items():
        known={row[1] for row in store.db.execute("PRAGMA table_info("+table+")")}
        for name,definition in fields.items():
            if name not in known:store.db.execute("ALTER TABLE "+table+" ADD COLUMN "+name+" "+definition)
    store.db.execute("UPDATE machine_inventory SET raw_state='saved',parse_state=CASE WHEN state IN ('parsed','partial_parse','not_disclosed','not_applicable','source_conflict','downloaded_unparsed') THEN state ELSE parse_state END WHERE raw_id IS NOT NULL AND raw_state='missing'")
    store.db.commit()


def _id(prefix, value):
    return prefix + hashlib.sha256(_json(value).encode()).hexdigest()


def _after(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def classify_failure(error, *, has_raw=False, state="failed"):
    import re
    value=str(error).lower()
    if state=="deferred":return "budget"
    if "ir_traversal_limit" in value:return "resource_limit"
    if re.search(r"\b401\b",value):return "authentication"
    if re.search(r"\b403\b",value):return "permission"
    if re.search(r"\b429\b",value):return "rate_limit"
    if "dependencymissing" in value:return "dependency"
    if "unsupportedcapability" in value:return "unsupported"
    if has_raw:return "parse"
    if re.search(r"\b5\d\d\b",value) or any(t in value for t in ("timeout","connection","network","temporar","reset","dns")):return "transient_network"
    return "unknown"


def _decode(row):
    if row is None:
        return None
    result = dict(row)
    for key in list(result):
        if key.endswith("_json"):
            value = result.pop(key)
            result[key[:-5]] = json.loads(value) if value is not None else None
    return result


class Queue:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        ensure_schema(store)
        self.generation = "backfill"
        self.run_id = None
        self.reprocess_only = False
        self.scope_ids = None
        self.scheduler = {}
        self.current_task = None
        self.resume_scope_ids = []
        self.current_scope_id = None

    def selected_scope_ids(self):
        return list(dict.fromkeys(list((self.scope_ids or {}).values())+self.resume_scope_ids))

    def scope_clause(self):
        if self.scope_ids is None:return "1=1",[]
        ids=self.selected_scope_ids()
        if not ids:return "0=1",[]
        return "task_id IN (SELECT task_id FROM machine_task_demands WHERE scope_id IN ("+",".join("?" for _ in ids)+"))",ids

    def scope_checks(self,source_ids=None):
        rows=[_decode(row) for row in self.db.execute("SELECT * FROM machine_scope_checks ORDER BY source_id,scope_id")]
        return [row for row in rows if source_ids is None or row["source_id"] in source_ids]

    def reset_retry_budget(self,*,source_ids=None,resource_ids=None,scope_ids=None,reason="explicit_operator_retry"):
        clauses=["state IN ('failed','blocked','retry_wait','exhausted')"]
        params=[]
        for column,values in (("source_id",source_ids),("resource_id",resource_ids)):
            if values is not None:
                if not values:return []
                clauses.append(column+" IN ("+",".join("?" for _ in values)+")");params.extend(values)
        if scope_ids is not None:
            if not scope_ids:return []
            clauses.append("task_id IN (SELECT task_id FROM machine_task_demands WHERE scope_id IN ("+",".join("?" for _ in scope_ids)+"))");params.extend(scope_ids)
        rows=self.db.execute("SELECT * FROM machine_tasks WHERE "+" AND ".join(clauses),params).fetchall()
        resets=[];now=now_utc();reset_requests={}
        for task in rows:
            work=self.db.execute("SELECT * FROM machine_work_items WHERE task_id=?",(task["task_id"],)).fetchone()
            if work is None:continue  # legacy unscoped tasks are evidence, not adopted work
            epoch=work["retry_epoch"]+1
            if work["request_id"]:
                if work["request_id"] not in reset_requests:
                    gate=self.db.execute("SELECT retry_epoch FROM machine_request_failures WHERE request_id=?",(work["request_id"],)).fetchone()
                    reset_requests[work["request_id"]]=gate[0]+1
                    self.db.execute("UPDATE machine_request_failures SET retry_epoch=?,consecutive_failures=0,next_retry_at=NULL,exhausted_at=NULL WHERE request_id=?",(gate[0]+1,work["request_id"]))
                epoch=max(epoch,reset_requests[work["request_id"]])
            self.db.execute("INSERT INTO machine_retry_resets VALUES(?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),task["task_id"],task["source_id"],now,work["retry_epoch"],epoch,work["consecutive_failures"],reason))
            self.db.execute("UPDATE machine_work_items SET retry_epoch=?,consecutive_failures=0,next_retry_at=NULL,exhausted_at=NULL,updated_at=? WHERE task_id=?",(epoch,now,task["task_id"]))
            self.db.execute("UPDATE machine_tasks SET state='pending',next_attempt_at=NULL,raw_id=CASE WHEN task_kind='acquire' THEN NULL ELSE raw_id END,retrieval_id=CASE WHEN task_kind='acquire' THEN NULL ELSE retrieval_id END,updated_at=? WHERE task_id=?",(now,task["task_id"]))
            resets.append({"task_id":task["task_id"],"source_id":task["source_id"],"retry_epoch":epoch,"previous_failures":work["consecutive_failures"]})
        self.db.commit();return resets

    def scoped_tasks(self):
        if self.scope_ids is None:return self.tasks()
        values=self.selected_scope_ids()
        if not values:return []
        return [_decode(row) for row in self.db.execute("SELECT DISTINCT t.* FROM machine_tasks t JOIN machine_task_demands d USING(task_id) WHERE d.scope_id IN ("+",".join("?" for _ in values)+") ORDER BY t.created_at,t.rowid",values)]

    def _demand(self,task_id,source_id,generation):
        scope_id=self.current_scope_id or (self.scope_ids or {}).get(source_id,"unscoped:"+source_id)
        prior=self.db.execute("SELECT 1 FROM machine_task_demands WHERE task_id=? AND generation=? AND scope_id=?",(task_id,generation,scope_id)).fetchone()
        self.db.execute("INSERT OR IGNORE INTO machine_task_demands VALUES(?,?,?,?)",(task_id,generation,scope_id,now_utc()))
        return prior is None

    def _schedule_work(self,source_id,kind,generation,scope,resource=None,*,reprocess=False):
        """One mutable work item, append-only demand/attempt history across cycles."""
        resource_id=resource.get("resource_id") if resource else None
        request=({"source":source_id,"url":resource.get("url"),"params":resource.get("metadata",{}).get("params"),
                  "kind":resource["kind"],"resource_id":resource_id}
                 if resource else {"source":source_id,"kind":"discover","scope":scope})
        work_id=_id("work:",request)
        work=self.db.execute("SELECT * FROM machine_work_items WHERE work_id=?",(work_id,)).fetchone()
        now=now_utc()
        task_id=work["task_id"] if work else _id("task:",[source_id,"work",work_id])
        self.db.execute("""INSERT OR IGNORE INTO machine_tasks
            (task_id,source_id,task_kind,resource_id,generation,scope_json,resource_json,state,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,'pending',?,?)""",(task_id,source_id,kind,resource_id,generation,_json(scope),_json(resource) if resource else None,now,now))
        self.db.execute("INSERT OR IGNORE INTO machine_work_items(work_id,task_id,source_id,resource_id,request_json,updated_at) VALUES(?,?,?,?,?,?)",(work_id,task_id,source_id,resource_id,_json(request),now))
        gate=None
        if resource and str(resource.get("url","")).startswith(("http://","https://")):
            transport={"source":source_id,"url":resource["url"],"params":resource.get("metadata",{}).get("params")}
            request_id=_id("request:",transport)
            self.db.execute("INSERT OR IGNORE INTO machine_request_failures(request_id,source_id,request_json) VALUES(?,?,?)",(request_id,source_id,_json(transport)))
            self.db.execute("UPDATE machine_work_items SET request_id=? WHERE task_id=?",(request_id,task_id))
            gate=self.db.execute("SELECT * FROM machine_request_failures WHERE request_id=?",(request_id,)).fetchone()
        new_demand=self._demand(task_id,source_id,generation)
        task=self.db.execute("SELECT * FROM machine_tasks WHERE task_id=?",(task_id,)).fetchone()
        changed=resource is not None and task["resource_json"]!=_json(resource)
        if task["state"]!="running":
            if task["state"]!="exhausted" and new_demand and kind=="acquire" and work and work["failure_class"]=="parse" and (not work["next_retry_at"] or work["next_retry_at"]<=now):
                self.db.execute("UPDATE machine_tasks SET raw_id=NULL,retrieval_id=NULL WHERE task_id=?",(task_id,))
            if task["state"]=="succeeded" and (new_demand or changed):
                # New update checks upstream; same-generation metadata changes
                # reparse saved bytes. Failed work retains its deadline/raw checkpoint.
                reset_raw=new_demand and kind=="acquire"
                self.db.execute("UPDATE machine_tasks SET state='pending',generation=?,resource_json=COALESCE(?,resource_json),raw_id=CASE WHEN ? THEN NULL ELSE raw_id END,retrieval_id=CASE WHEN ? THEN NULL ELSE retrieval_id END,next_attempt_at=NULL,updated_at=? WHERE task_id=?",(generation,_json(resource) if resource else None,reset_raw,reset_raw,now,task_id))
            else:
                self.db.execute("UPDATE machine_tasks SET generation=?,resource_json=COALESCE(?,resource_json),updated_at=? WHERE task_id=?",(generation,_json(resource) if resource else None,now,task_id))
                if task["state"] in {"failed","blocked","deferred"} and (not task["next_attempt_at"] or task["next_attempt_at"]<=now):
                    this_run=self.db.execute("SELECT 1 FROM machine_attempts WHERE task_id=? AND run_id=?",(task_id,self.run_id)).fetchone()
                    if not this_run:
                        reset_raw=bool(new_demand and kind=="acquire" and work and work["failure_class"]=="parse")
                        self.db.execute("UPDATE machine_tasks SET state='pending',next_attempt_at=NULL,raw_id=CASE WHEN ? THEN NULL ELSE raw_id END,retrieval_id=CASE WHEN ? THEN NULL ELSE retrieval_id END WHERE task_id=?",(reset_raw,reset_raw,task_id))
            if gate and (gate["exhausted_at"] or (gate["next_retry_at"] and gate["next_retry_at"]>now)):
                self.db.execute("UPDATE machine_tasks SET state=?,next_attempt_at=? WHERE task_id=? AND state!='succeeded'",("exhausted" if gate["exhausted_at"] else "retry_wait",gate["next_retry_at"],task_id))
        self.db.commit()
        return task_id

    def start_run(self, mode, generation, config=None, lease_seconds=300):
        run_id, now = str(uuid.uuid4()), now_utc()
        self.db.execute("INSERT INTO machine_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (run_id, mode, generation, now, None, now, _after(lease_seconds), "running", _json(config or {}), None))
        self.db.commit()
        self.generation, self.run_id = generation, run_id
        return run_id

    def heartbeat(self, run_id, lease_seconds=300):
        until, now = _after(lease_seconds), now_utc()
        self.db.execute("UPDATE machine_runs SET heartbeat_at=?,lease_until=? WHERE run_id=? AND state='running'", (now, until, run_id))
        self.db.execute("UPDATE machine_tasks SET lease_until=? WHERE owner_run_id=? AND state='running'", (until, run_id))
        self.db.commit()

    def finish_run(self, run_id, state, summary):
        self.db.execute("UPDATE machine_runs SET finished_at=?,state=?,summary_json=? WHERE run_id=?", (now_utc(), state, _json(summary), run_id))
        self.db.commit()

    def release_run_tasks(self, run_id, reason):
        """Release only this caller's known-stopped work, preserving checkpoints."""
        now = now_utc()
        self.db.execute("UPDATE machine_tasks SET state='pending',owner_run_id=NULL,lease_until=NULL,updated_at=?,last_error=? WHERE owner_run_id=? AND state='running'",(now,reason,run_id))
        self.db.execute("UPDATE machine_attempts SET state='interrupted',finished_at=?,error=? WHERE run_id=? AND finished_at IS NULL",(now,reason,run_id))
        self.db.commit()

    def enqueue_discovery(self, source_id, scope, generation="backfill"):
        return self._schedule_work(source_id,"discover",generation,scope)

    def mark_discovery_dependency(self, task_id, upstream_source_id, fingerprint):
        """Persist dirty discovery evidence without creating one job per document."""
        self.db.execute("""INSERT INTO machine_discovery_dependencies
          VALUES(?,?,?,NULL,?,NULL) ON CONFLICT(task_id,upstream_source_id)
          DO UPDATE SET requested_fingerprint=excluded.requested_fingerprint,
          observed_at=CASE WHEN requested_fingerprint!=excluded.requested_fingerprint
          THEN excluded.observed_at ELSE observed_at END""",(task_id,upstream_source_id,fingerprint,now_utc()))
        self.db.commit()

    def dependency_snapshot(self, task_id):
        return {row["upstream_source_id"]:row["requested_fingerprint"] for row in self.db.execute("SELECT * FROM machine_discovery_dependencies WHERE task_id=?",(task_id,))}

    def consume_dependencies(self, task_id, snapshot):
        for source_id,fingerprint in snapshot.items():
            self.db.execute("UPDATE machine_discovery_dependencies SET consumed_fingerprint=?,consumed_at=? WHERE task_id=? AND upstream_source_id=?",(fingerprint,now_utc(),task_id,source_id))
        self.db.commit()

    def activate_dirty_discoveries(self, source_ids=None):
        rows=self.db.execute("""SELECT DISTINCT t.task_id,t.source_id FROM machine_tasks t
          JOIN machine_discovery_dependencies d USING(task_id)
          WHERE t.task_kind='discover' AND t.state IN ('succeeded','failed','blocked','retry_wait')
          AND (d.consumed_fingerprint IS NULL OR d.consumed_fingerprint!=d.requested_fingerprint)""").fetchall()
        count=0
        for row in rows:
            if source_ids is not None and row["source_id"] not in source_ids:
                continue
            clause,params=self.scope_clause()
            if not self.db.execute("SELECT 1 FROM machine_tasks WHERE task_id=? AND "+clause,[row["task_id"],*params]).fetchone():continue
            item=self.db.execute("SELECT state,next_attempt_at FROM machine_tasks WHERE task_id=?",(row["task_id"],)).fetchone()
            if item["state"]!="succeeded" and item["next_attempt_at"] and item["next_attempt_at"]>now_utc():continue
            self.db.execute("UPDATE machine_tasks SET state='pending',next_attempt_at=NULL,updated_at=?,last_error=NULL WHERE task_id=?",(now_utc(),row["task_id"]))
            count+=1
        self.db.commit()
        return count

    def emit(self, resource, generation=None, *, reprocess=False):
        resource = dict(resource)
        required = ("source_id", "resource_key", "kind", "data_type")
        if any(not resource.get(key) for key in required):
            raise ValueError("Resource requires source_id, resource_key, kind and data_type")
        resource.setdefault("metadata", {})
        resource_id = _id("resource:", [resource["source_id"], resource["resource_key"]])
        resource["resource_id"] = resource_id
        now, generation = now_utc(), generation or self.generation
        existing = self.db.execute("SELECT * FROM machine_inventory WHERE resource_id=?", (resource_id,)).fetchone()
        if existing:
            resource["metadata"] = {**json.loads(existing["metadata_json"]), **resource["metadata"]}
        self.db.execute("""INSERT INTO machine_inventory
          (resource_id,source_id,resource_key,kind,data_type,url,company_id,security_id,
           metadata_json,state,first_discovered_at,last_discovered_at)
          VALUES(?,?,?,?,?,?,?,?,?,'discovered_not_downloaded',?,?)
          ON CONFLICT(resource_id) DO UPDATE SET metadata_json=excluded.metadata_json,
           url=excluded.url,last_discovered_at=excluded.last_discovered_at""",
          (resource_id, resource["source_id"], resource["resource_key"], resource["kind"], resource["data_type"],
           resource.get("url"), resource.get("company_id"), resource.get("security_id"), _json(resource["metadata"]), now, now))
        reprocess = reprocess or self.reprocess_only
        if reprocess and (existing is None or not existing["raw_id"]):
            self.db.commit()
            self.gap("RAW_NOT_SAVED", "Resource discovered while reprocessing has no saved raw evidence; network work was not scheduled.",
                     source_id=resource["source_id"],company_id=resource.get("company_id"),data_type=resource["data_type"],
                     status="discovered_not_downloaded",details={"resource_id":resource_id})
            return resource_id
        if not reprocess:
            task_id=self._schedule_work(resource["source_id"],"acquire",generation,{},resource)
            task=self.db.execute("SELECT state FROM machine_tasks WHERE task_id=?",(task_id,)).fetchone()
            if task["state"]!="succeeded":
                self.db.execute("UPDATE machine_inventory SET refresh_generation=?,refresh_state=CASE WHEN ? IN ('blocked','failed','retry_wait','exhausted') THEN refresh_state ELSE 'pending' END WHERE resource_id=?",(generation,task["state"],resource_id))
            self.db.commit()
            return resource_id
        task_kind = "reprocess" if reprocess else "acquire"
        task_id = _id("task:", [resource["source_id"], task_kind, resource_id, generation])
        # A stable backfill generation never appends duplicate tasks. A new cycle
        # explicitly refreshes resources and receives its own raw checkpoint.
        raw_id = existing["raw_id"] if existing and reprocess else None
        retrieval_id = existing["retrieval_id"] if existing and reprocess else None
        cursor = self.db.execute("""INSERT OR IGNORE INTO machine_tasks
          (task_id,source_id,task_kind,resource_id,generation,scope_json,resource_json,state,
           created_at,updated_at,raw_id,retrieval_id)
          VALUES(?,?,?,?,?,'{}',?,'pending',?,?,?,?)""",
          (task_id,resource["source_id"],task_kind,resource_id,generation,_json(resource),now,now,raw_id,retrieval_id))
        if not cursor.rowcount:
            previous_task = self.db.execute("SELECT state,resource_json,raw_id FROM machine_tasks WHERE task_id=?", (task_id,)).fetchone()
            if previous_task and previous_task["state"] != "running" and previous_task["resource_json"] != _json(resource):
                # A filing header may strengthen public-time evidence after a
                # listing emitted the primary document. Reparse acquired bytes
                # with the new metadata instead of keeping stale time claims.
                self.db.execute("UPDATE machine_tasks SET resource_json=?,state=CASE WHEN state='succeeded' THEN 'pending' ELSE state END,updated_at=? WHERE task_id=?", (_json(resource),now,task_id))
                if previous_task["state"]=="succeeded" and previous_task["raw_id"]:
                    self.db.execute("UPDATE machine_inventory SET state='downloaded_unparsed' WHERE resource_id=?", (resource_id,))
        if cursor.rowcount and not reprocess:
            self.db.execute("UPDATE machine_inventory SET state='discovered_not_downloaded',last_error=NULL WHERE resource_id=?", (resource_id,))
        self._demand(task_id,resource["source_id"],generation)
        self.db.commit()
        return resource_id

    def enqueue_reprocess(self, resource, generation, *, raw_id=None, retrieval_id=None):
        """Queue exact saved evidence without changing latest inventory metadata."""
        resource=dict(resource)
        resource_id=resource.get("resource_id") or _id("resource:",[resource["source_id"],resource["resource_key"]])
        existing=self.resource(resource_id)
        if existing is None:
            raise ValueError("Reprocess resource must already exist in inventory")
        raw_id=raw_id or existing.get("raw_id")
        retrieval_id=retrieval_id or existing.get("retrieval_id")
        if not raw_id or not retrieval_id:
            raise ValueError("Reprocess requires exact saved raw and retrieval identifiers")
        valid=self.db.execute("SELECT 1 FROM machine_resource_versions WHERE resource_id=? AND raw_id=? AND retrieval_id=?",(resource_id,raw_id,retrieval_id)).fetchone()
        if not valid:
            raise ValueError("Reprocess evidence is not a saved version of this resource")
        resource["resource_id"]=resource_id
        task_id=_id("task:",[resource["source_id"],"reprocess",resource_id,generation,raw_id,retrieval_id])
        scope=_json({"raw_id":raw_id,"retrieval_id":retrieval_id})
        now=now_utc()
        self.db.execute("""INSERT OR IGNORE INTO machine_tasks
            (task_id,source_id,task_kind,resource_id,generation,scope_json,resource_json,state,created_at,updated_at,raw_id,retrieval_id)
            VALUES(?,?,'reprocess',?,?,?,?,'pending',?,?,?,?)""",
            (task_id,resource["source_id"],resource_id,generation,scope,_json(resource),now,now,raw_id,retrieval_id))
        self._demand(task_id,resource["source_id"],generation)
        self.db.commit()
        return task_id

    def _assert_owner(self, task):
        row=self.db.execute("SELECT state,owner_run_id FROM machine_tasks WHERE task_id=?",(task["task_id"],)).fetchone()
        if row is None or row["state"]!="running" or row["owner_run_id"]!=task.get("owner_run_id"):
            raise StaleTaskOwner("Task lease belongs to another or finished execution: "+task["task_id"])

    def gap(self, code, message, *, source_id, company_id=None, data_type=None,
            status="capability_gap", details=None, run_id=None):
        details = details or {}
        gap_id = _id("gap:", [source_id, company_id, data_type, code, details])
        now = now_utc()
        self.db.execute("""INSERT INTO machine_gaps VALUES(?,?,?,?,?,?,?,?,?,?,1,?,NULL)
          ON CONFLICT(gap_id) DO UPDATE SET last_seen_at=excluded.last_seen_at,
          occurrences=machine_gaps.occurrences+1,last_run_id=excluded.last_run_id,
          message=excluded.message,status=excluded.status,resolved_at=NULL""",
          (gap_id,source_id,company_id,data_type,code,status,message,_json(details),now,now,run_id or self.run_id))
        self.db.commit()
        return gap_id

    def resolve_gaps(self, *, source_id, code, company_id=None, details_match=None):
        if details_match is None:
            self.db.execute("UPDATE machine_gaps SET resolved_at=? WHERE source_id=? AND code=? AND company_id IS ? AND resolved_at IS NULL", (now_utc(), source_id, code, company_id))
        else:
            rows=self.db.execute("SELECT gap_id,details_json FROM machine_gaps WHERE source_id=? AND code=? AND company_id IS ? AND resolved_at IS NULL",(source_id,code,company_id)).fetchall()
            for row in rows:
                details=json.loads(row["details_json"])
                if all(details.get(key)==value for key,value in details_match.items()):
                    self.db.execute("UPDATE machine_gaps SET resolved_at=? WHERE gap_id=?",(now_utc(),row["gap_id"]))
        self.db.commit()

    def recover_expired(self):
        now = now_utc()
        rows = self.db.execute("""SELECT t.task_id FROM machine_tasks t LEFT JOIN machine_runs r
          ON r.run_id=t.owner_run_id WHERE t.state='running' AND t.lease_until<?
          AND (r.run_id IS NULL OR r.state!='running' OR r.lease_until<?)""", (now,now)).fetchall()
        for row in rows:
            self.db.execute("UPDATE machine_tasks SET state='pending',owner_run_id=NULL,lease_until=NULL,updated_at=?,last_error='Expired worker lease recovered' WHERE task_id=?", (now,row[0]))
            self.db.execute("UPDATE machine_attempts SET state='interrupted',finished_at=?,error='Expired worker lease recovered' WHERE task_id=? AND finished_at IS NULL", (now,row[0]))
        self.db.execute("UPDATE machine_runs SET state='interrupted',finished_at=? WHERE state='running' AND lease_until<?", (now,now))
        self.db.commit()
        return len(rows)

    def retry_tasks(self, statuses=("failed", "blocked", "deferred"), *, source_ids=None):
        if not statuses:
            return 0
        clauses = ["state IN (" + ",".join("?" for _ in statuses) + ")", "(next_attempt_at IS NULL OR next_attempt_at<=?)"]
        args = list(statuses)
        args.append(now_utc())
        clause,params=self.scope_clause();clauses.append(clause);args.extend(params)
        clauses.append("task_id NOT IN (SELECT w.task_id FROM machine_work_items w JOIN machine_request_failures f USING(request_id) WHERE f.exhausted_at IS NOT NULL OR f.next_retry_at>?)");args.append(now_utc())
        if source_ids is not None:
            if not source_ids:
                return 0
            clauses.append("source_id IN (" + ",".join("?" for _ in source_ids) + ")")
            args.extend(source_ids)
        cursor = self.db.execute("UPDATE machine_tasks SET state='pending',next_attempt_at=NULL,owner_run_id=NULL,lease_until=NULL,updated_at=? WHERE " + " AND ".join(clauses), [now_utc(), *args])
        self.db.commit()
        return cursor.rowcount

    def claim(self, run_id, *, discovery_only=False, source_ids=None, lease_seconds=300, reprocess_only=False):
        now = now_utc()
        clauses, args = ["(state='pending' OR (state='retry_wait' AND next_attempt_at<=?))"], [now]
        if discovery_only:
            clauses.append("task_kind='discover'")
        if reprocess_only:
            clauses.append("task_kind='reprocess'")
            clauses.append("generation=?");args.append(self.generation)
        clause,params=self.scope_clause();clauses.append(clause);args.extend(params)
        clauses.append("task_id NOT IN (SELECT w.task_id FROM machine_work_items w JOIN machine_request_failures f USING(request_id) WHERE f.exhausted_at IS NOT NULL OR f.next_retry_at>?)");args.append(now)
        if source_ids is not None:
            if not source_ids:
                return None
            clauses.append("source_id IN (" + ",".join("?" for _ in source_ids) + ")")
            args.extend(source_ids)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM machine_tasks WHERE " + " AND ".join(clauses) + " ORDER BY created_at,rowid LIMIT 1", args).fetchone()
            if row is None:
                return None
            attempt_id = str(uuid.uuid4())
            self.db.execute("UPDATE machine_tasks SET state='running',attempt_count=attempt_count+1,owner_run_id=?,lease_until=?,updated_at=? WHERE task_id=?", (run_id,_after(lease_seconds),now,row["task_id"]))
            epoch=self.db.execute("SELECT retry_epoch FROM machine_work_items WHERE task_id=?",(row["task_id"],)).fetchone()
            self.db.execute("INSERT INTO machine_attempts(attempt_id,task_id,run_id,started_at,state,stage,retry_epoch) VALUES(?,?,?,?,'running','start',?)", (attempt_id,row["task_id"],run_id,now,epoch[0] if epoch else 1))
            self.db.execute("INSERT INTO machine_source_health(source_id,last_attempt_at,status,attempts,capabilities_json) VALUES(?,?,'running',1,'{}') ON CONFLICT(source_id) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,attempts=machine_source_health.attempts+1", (row["source_id"],now))
            if row["resource_id"]:
                self.db.execute("UPDATE machine_inventory SET last_attempt_at=? WHERE resource_id=?", (now,row["resource_id"]))
                if row["task_kind"]=="acquire":
                    self.db.execute("UPDATE machine_inventory SET refresh_state='running',last_refresh_attempt_at=? WHERE resource_id=?",(now,row["resource_id"]))
        result = _decode(row)
        result.update(attempt_id=attempt_id, owner_run_id=run_id, attempt_count=result["attempt_count"]+1,started_at=now)
        return result

    def acquisition_checkpoint(self, task, raw):
        """Commit the raw retrieval before parsing so parser retries are offline."""
        self._assert_owner(task)
        if not raw.get("raw_id") or not raw.get("retrieval_id"):
            raise ValueError("Adapter acquisition must return a persisted raw retrieval")
        valid = self.db.execute("SELECT 1 FROM raw_retrievals WHERE retrieval_id=? AND raw_id=?", (raw["retrieval_id"],raw["raw_id"])).fetchone()
        if not valid:
            raise ValueError("Acquisition checkpoint refers to unknown raw evidence")
        now = now_utc()
        self.db.execute("UPDATE machine_tasks SET raw_id=?,retrieval_id=?,updated_at=? WHERE task_id=?", (raw["raw_id"],raw["retrieval_id"],now,task["task_id"]))
        fresh=raw["retrieved_at"]>=task.get("started_at",now)
        if fresh:self.db.execute("UPDATE machine_request_failures SET failure_class=NULL,consecutive_failures=0,next_retry_at=NULL,exhausted_at=NULL,last_success_at=? WHERE request_id=(SELECT request_id FROM machine_work_items WHERE task_id=?)",(raw["retrieved_at"],task["task_id"]))
        self.db.execute("""UPDATE machine_inventory SET state=CASE WHEN raw_id=? THEN state ELSE 'downloaded_unparsed' END,
            parse_state=CASE WHEN raw_id=? THEN parse_state ELSE 'downloaded_unparsed' END,
            raw_id=?,retrieval_id=?,raw_state='saved',refresh_state=?,upstream_state=?,
            last_refresh_success_at=CASE WHEN ? THEN ? ELSE last_refresh_success_at END,last_error=NULL,failure_class=NULL WHERE resource_id=?""",
            (raw["raw_id"],raw["raw_id"],raw["raw_id"],raw["retrieval_id"],"succeeded" if fresh else "cached",
             "confirmed_at_response" if fresh else "cached_not_rechecked",fresh,raw["retrieved_at"],task["resource_id"]))
        self.db.execute("UPDATE machine_attempts SET stage='parse',raw_id=? WHERE attempt_id=?", (raw["raw_id"],task["attempt_id"]))
        self.db.execute("""INSERT OR IGNORE INTO machine_resource_versions
            (version_id,resource_id,raw_id,retrieval_id,acquired_at,parse_state,parsed_at,parse_result_json,resource_json)
            VALUES(?,?,?,?,?,'downloaded_unparsed',NULL,NULL,?)""", (_id("version:",[task["resource_id"],raw["retrieval_id"]]),task["resource_id"],raw["raw_id"],raw["retrieval_id"],raw["retrieved_at"],_json(task["resource"])))
        self.db.commit()
        task.update(raw_id=raw["raw_id"],retrieval_id=raw["retrieval_id"])

    def finish(self, task, result, *, resource_state=None):
        self._assert_owner(task)
        now = now_utc()
        latest=self.resource(task["resource_id"]) if task["resource_id"] else None
        current_version=bool(latest and (latest.get("raw_id"),latest.get("retrieval_id"))==(task.get("raw_id"),task.get("retrieval_id")))
        self.db.execute("UPDATE machine_tasks SET state='succeeded',result_json=?,updated_at=?,lease_until=NULL,last_error=NULL WHERE task_id=?", (_json(result),now,task["task_id"]))
        self.db.execute("UPDATE machine_work_items SET failure_class=NULL,consecutive_failures=0,next_retry_at=NULL,exhausted_at=NULL,last_success_at=?,updated_at=? WHERE task_id=?",(now,now,task["task_id"]))
        self.db.execute("UPDATE machine_attempts SET state='succeeded',stage='complete',finished_at=?,result_json=? WHERE attempt_id=?", (now,_json(result),task["attempt_id"]))
        self.db.execute("UPDATE machine_source_health SET last_success_at=?,status=?,successes=successes+1 WHERE source_id=?", (now,"partial" if resource_state=="partial_parse" else "reachable",task["source_id"]))
        if task["resource_id"] and resource_state:
            parsed_at=result.get("cached_parsed_at",now)
            self.db.execute("UPDATE machine_inventory SET state=?,parse_state=?,last_parsed_at=?,last_success_at=?,last_error=NULL WHERE resource_id=? AND raw_id=? AND retrieval_id=?", (resource_state,resource_state,parsed_at,now,task["resource_id"],task["raw_id"],task["retrieval_id"]))
            self.db.execute("UPDATE machine_resource_versions SET parse_state=?,parsed_at=?,parse_result_json=? WHERE resource_id=? AND retrieval_id=?", (resource_state,parsed_at,_json(result),task["resource_id"],task["retrieval_id"]))
        for row in self.db.execute("SELECT gap_id,code,details_json FROM machine_gaps WHERE source_id=? AND resolved_at IS NULL", (task["source_id"],)).fetchall():
            details = json.loads(row["details_json"])
            task_gap = details.get("task_id")==task["task_id"] and row["code"] in {"TASK_FAILED","PARSE_FAILED","DEPENDENCY_MISSING","CAPABILITY_UNAVAILABLE","BUDGET_EXHAUSTED"}
            resource_gap = resource_state=="parsed" and task["resource_id"] and details.get("resource_id")==task["resource_id"] and row["code"] in {"PARTIAL_PARSE","SOURCE_CONFLICT","RAW_NOT_SAVED"} and (details.get("raw_id")==task.get("raw_id") or (not details.get("raw_id") and current_version))
            if task_gap or resource_gap:
                self.db.execute("UPDATE machine_gaps SET resolved_at=? WHERE gap_id=?", (now,row["gap_id"]))
        self.db.commit()

    def fail(self, task, error, *, state="failed", resource_state=None, retry_seconds=30):
        self._assert_owner(task)
        now = now_utc()
        category=classify_failure(error,has_raw=bool(task.get("raw_id")),state=state)
        prior=self.db.execute("SELECT consecutive_failures,request_id FROM machine_work_items WHERE task_id=?",(task["task_id"],)).fetchone()
        gate=self.db.execute("SELECT * FROM machine_request_failures WHERE request_id=?",(prior["request_id"],)).fetchone() if prior and prior["request_id"] else None
        transport_failure=bool(gate and not task.get("raw_id") and category not in {"budget","resource_limit","dependency","unsupported"})
        failures=(prior[0] if prior else 0)+int(category not in {"budget","resource_limit"})
        if transport_failure:failures=gate["consecutive_failures"]+1
        base=float(self.scheduler.get("retry_seconds",retry_seconds))
        cap=float(self.scheduler.get("retry_max_seconds",3600))
        if category in {"authentication","permission","dependency","unsupported"}:
            base=float(self.scheduler.get("authorization_retry_seconds",3600));cap=max(base,cap)
        elif category=="rate_limit":base=float(self.scheduler.get("rate_limit_retry_seconds",60))
        delay=0 if category in {"budget","resource_limit"} else min(cap,max(0,base)*2**min(max(0,failures-1),16))
        retry_at=_after(delay)
        exhausted=prior is not None and failures>=max(1,int(self.scheduler.get("max_resource_attempts",5)))
        if exhausted:
            state="exhausted";retry_at=None
        self.db.execute("UPDATE machine_tasks SET state=?,last_error=?,updated_at=?,next_attempt_at=?,lease_until=NULL WHERE task_id=?", (state,error,now,retry_at,task["task_id"]))
        self.db.execute("UPDATE machine_attempts SET state=?,error=?,finished_at=?,failure_class=?,retry_at=? WHERE attempt_id=?", (state,error,now,category,retry_at,task["attempt_id"]))
        self.db.execute("UPDATE machine_work_items SET failure_class=?,consecutive_failures=?,next_retry_at=?,exhausted_at=?,updated_at=? WHERE task_id=?",(category,failures,retry_at,now if exhausted else None,now,task["task_id"]))
        if transport_failure:
            self.db.execute("UPDATE machine_request_failures SET failure_class=?,consecutive_failures=?,next_retry_at=?,exhausted_at=?,last_failure_at=? WHERE request_id=?",(category,failures,retry_at,now if exhausted else None,now,gate["request_id"]))
            self.db.execute("UPDATE machine_tasks SET state=?,next_attempt_at=? WHERE task_id IN (SELECT task_id FROM machine_work_items WHERE request_id=?) AND task_id!=? AND state IN ('pending','retry_wait','failed','blocked')",("exhausted" if exhausted else "retry_wait",retry_at,gate["request_id"],task["task_id"]))
        health_status = "budget_deferred" if state=="deferred" else ("missing_dependency" if state=="blocked" else "degraded")
        self.db.execute("UPDATE machine_source_health SET last_error_at=?,last_error=?,status=?,failures=failures+? WHERE source_id=?", (now,error,health_status,int(state!="deferred"),task["source_id"]))
        if task["resource_id"]:
            if task["task_kind"]=="reprocess":
                self.db.execute("UPDATE machine_inventory SET state=COALESCE(?,state),parse_state=COALESCE(?,parse_state),last_error=?,failure_class=? WHERE resource_id=? AND raw_id=? AND retrieval_id=?",(resource_state,resource_state,error,category,task["resource_id"],task["raw_id"],task["retrieval_id"]))
            else:
                # A failed refresh does not erase an older acquired/parsed raw.
                self.db.execute("UPDATE machine_inventory SET state=CASE WHEN raw_id IS NOT NULL AND ? IS NULL THEN state ELSE COALESCE(?,state) END,last_error=?,failure_class=?,refresh_state=?,upstream_state='unknown_after_failure',parse_state=CASE WHEN ? IS NOT NULL THEN COALESCE(?,parse_state) ELSE parse_state END WHERE resource_id=?", (task.get("raw_id"),resource_state,error,category,"deferred" if state=="deferred" else "failed",task.get("raw_id"),resource_state,task["resource_id"]))
        self.db.commit()

    def health(self, source_id, capabilities):
        self.db.execute("INSERT INTO machine_source_health(source_id,status,capabilities_json) VALUES(?,'unchecked',?) ON CONFLICT(source_id) DO UPDATE SET capabilities_json=excluded.capabilities_json", (source_id,_json(capabilities)))
        self.db.commit()

    def resources(self):
        return [_decode(row) for row in self.db.execute("SELECT * FROM machine_inventory ORDER BY first_discovered_at,resource_id")]

    def tasks(self):
        return [_decode(row) for row in self.db.execute("SELECT * FROM machine_tasks ORDER BY created_at,rowid")]

    def gaps(self, *, unresolved=True):
        return [_decode(row) for row in self.db.execute("SELECT * FROM machine_gaps" + (" WHERE resolved_at IS NULL" if unresolved else "") + " ORDER BY last_seen_at DESC")]

    def resource(self, resource_id):
        return _decode(self.db.execute("SELECT * FROM machine_inventory WHERE resource_id=?", (resource_id,)).fetchone())

    def raw_for_task(self, task):
        row = self.db.execute("SELECT b.*,r.retrieval_id,r.source,r.url,r.retrieved_at,r.metadata_json FROM raw_retrievals r JOIN raw_blobs b USING(raw_id) WHERE r.retrieval_id=? AND r.raw_id=?", (task["retrieval_id"],task["raw_id"])).fetchone()
        raw = _decode(row)
        if raw is None:
            raise ValueError("Task checkpoint raw retrieval is missing")
        path = self.store.root / raw["path"]
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != raw["sha256"]:
            raise ValueError("Task checkpoint raw SHA-256 mismatch")
        raw["path"] = str(path)
        return content, raw
