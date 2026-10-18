"""Durable analysis work. Call mutations only under the shared collector lock."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta, timezone
from ..store import _json, now_utc

SCHEMA = '''
CREATE TABLE IF NOT EXISTS competition_jobs(
 job_id TEXT PRIMARY KEY, input_json TEXT NOT NULL, state TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, started_at TEXT,
 finished_at TEXT, next_attempt_at TEXT, result_json TEXT, last_error TEXT);
CREATE TABLE IF NOT EXISTS competition_job_history(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,at TEXT NOT NULL,
 event TEXT NOT NULL,details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS competition_endpoint_state(
 endpoint_id TEXT PRIMARY KEY,config_hash TEXT,last_attempt_at TEXT,last_success_at TEXT,
 last_processed_at TEXT,next_due_at TEXT,last_raw_id TEXT,last_snapshot_id TEXT,
 last_error TEXT,consecutive_failures INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS competition_runs(
 run_id TEXT PRIMARY KEY,started_at TEXT NOT NULL,finished_at TEXT,trigger TEXT NOT NULL,
 plan_json TEXT NOT NULL,result_json TEXT);
CREATE TABLE IF NOT EXISTS competition_cycles(
 scope_id TEXT PRIMARY KEY,cycle_started_at TEXT NOT NULL,plan_json TEXT NOT NULL,
 state TEXT NOT NULL,last_attempt_at TEXT NOT NULL);
'''

def ensure_schema(store):
    store.db.executescript(SCHEMA)

def history(store,job_id,event,details=None):
    store.db.execute('INSERT INTO competition_job_history(job_id,at,event,details_json) VALUES(?,?,?,?)',
                     (job_id,now_utc(),event,_json(details or {})))

def enqueue(store,payload):
    ensure_schema(store)
    identity='analysis:'+hashlib.sha256(_json(payload).encode()).hexdigest()
    with store.db:
        inserted=store.db.execute('INSERT OR IGNORE INTO competition_jobs(job_id,input_json,state,created_at) VALUES(?,?,?,?)',
                                 (identity,_json(payload),'pending',now_utc())).rowcount
        if inserted:history(store,identity,'queued')
    return identity

def recover(store):
    """OS lock proves the previous owner exited. Never erase an attempt."""
    ensure_schema(store)
    with store.db:
        rows=store.db.execute("SELECT job_id FROM competition_jobs WHERE state='running'").fetchall()
        for row in rows:
            store.db.execute("UPDATE competition_jobs SET state=CASE WHEN attempts>=3 THEN 'exhausted' ELSE 'pending' END,last_error='owner_interrupted',next_attempt_at=NULL WHERE job_id=?",(row[0],))
            history(store,row[0],'owner_interrupted')
    return [row[0] for row in rows]

def claim(store,allowed_ids=None):
    clause="state IN ('pending','retry_wait') AND (next_attempt_at IS NULL OR next_attempt_at<=?)"
    params=[now_utc()]
    if allowed_ids is not None:
        if not allowed_ids:return None
        clause+=' AND job_id IN ('+','.join('?' for _ in allowed_ids)+')';params+=list(allowed_ids)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        row=store.db.execute('SELECT * FROM competition_jobs WHERE '+clause+' ORDER BY created_at,job_id LIMIT 1',params).fetchone()
        if not row:return None
        store.db.execute("UPDATE competition_jobs SET state='running',attempts=attempts+1,started_at=? WHERE job_id=?",(now_utc(),row['job_id']))
        history(store,row['job_id'],'started',{'attempt':row['attempts']+1})
    return dict(row,attempts=row['attempts']+1,payload=json.loads(row['input_json']))

def finish(store,job,result=None,error=None,max_attempts=3):
    when=now_utc();state='completed';next_at=None
    if error:
        state='exhausted' if job['attempts']>=max_attempts else 'retry_wait'
        if state=='retry_wait':next_at=(datetime.now(timezone.utc)+timedelta(minutes=15*2**(job['attempts']-1))).isoformat().replace('+00:00','Z')
    with store.db:
        store.db.execute('UPDATE competition_jobs SET state=?,finished_at=?,next_attempt_at=?,result_json=?,last_error=? WHERE job_id=?',
                         (state,when,next_at,_json(result) if result is not None else None,error,job['job_id']))
        history(store,job['job_id'],state,{'error':error,'next_attempt_at':next_at,'result':result})
    return state

def status(store):
    present=store.db.execute("SELECT 1 FROM sqlite_master WHERE name='competition_jobs'").fetchone()
    if not present:return {'enabled':False,'jobs':{},'endpoints':[],'unfinished':[]}
    counts={r[0]:r[1] for r in store.db.execute('SELECT state,COUNT(*) FROM competition_jobs GROUP BY state')}
    return {'enabled':True,'jobs':counts,
            'endpoints':[dict(r) for r in store.db.execute('SELECT * FROM competition_endpoint_state ORDER BY endpoint_id')],
            'unfinished':[dict(r) for r in store.db.execute("SELECT job_id,state,attempts,next_attempt_at,last_error FROM competition_jobs WHERE state!='completed' ORDER BY created_at")],
            'latest_run':dict(row) if (row:=store.db.execute('SELECT * FROM competition_runs ORDER BY started_at DESC LIMIT 1').fetchone()) else None}
