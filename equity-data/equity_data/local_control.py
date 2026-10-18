"""Durable local triggers; schedule acknowledgment is NOT a data watermark."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from zoneinfo import ZoneInfo


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or utcnow()).astimezone(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@contextmanager
def collector_lock(root):
    """Kernel owned lock: process death releases it, stale text grants no ownership."""
    path=Path(root)/'.local-collector.lock';path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b');stream.seek(0,2)
    if stream.tell()==0:stream.write(b'0');stream.flush()
    stream.seek(0)
    locked=False
    try:
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True
        except OSError:
            yield False
            return
        yield True
    finally:
        if locked:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        stream.close()


def schedule_slots(times,zone,after,through):
    """Civil-time slots; UTC IDs keep overlaps and DST unambiguous."""
    tz=ZoneInfo(zone);after=after.astimezone(timezone.utc);through=through.astimezone(timezone.utc)
    parsed=sorted({tuple(map(int,t.split(':'))) for t in times})
    if not parsed or any(len(t)!=2 or not 0<=t[0]<=23 or not 0<=t[1]<=59 for t in parsed):
        raise ValueError('schedule.times must contain HH:MM civil times')
    day=after.astimezone(tz).date();last=through.astimezone(tz).date();slots=[]
    while day<=last:
        for hour,minute in parsed:
            local=datetime(day.year,day.month,day.day,hour,minute,tzinfo=tz)
            # Nonexistent DST clocks are omitted; ambiguous clocks use fold=0 once.
            point=local.astimezone(timezone.utc)
            if point.astimezone(tz).replace(tzinfo=None)!=local.replace(tzinfo=None):continue
            if after<point<=through:slots.append({'utc':stamp(point),'local':local.isoformat()})
        day+=timedelta(days=1)
    return slots


class Control:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.root/'local-control.sqlite3',timeout=30)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS local_meta(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS local_requests(
          request_id TEXT PRIMARY KEY,scope_key TEXT NOT NULL,payload_json TEXT NOT NULL,
          state TEXT NOT NULL,created_at TEXT NOT NULL,latest_trigger_at TEXT NOT NULL,
          started_at TEXT,finished_at TEXT,result_path TEXT,error TEXT,trigger_count INTEGER NOT NULL DEFAULT 1);
        CREATE UNIQUE INDEX IF NOT EXISTS local_one_pending ON local_requests(scope_key) WHERE state='pending';
        CREATE TABLE IF NOT EXISTS local_events(
          event_id TEXT PRIMARY KEY,request_id TEXT,observed_at TEXT NOT NULL,kind TEXT NOT NULL,details_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS local_slots(
          slot_utc TEXT PRIMARY KEY,local_time TEXT NOT NULL,request_id TEXT NOT NULL,observed_at TEXT NOT NULL);
        ''')
        columns={row[1] for row in self.db.execute('PRAGMA table_info(local_requests)')}
        if 'continuation_json' not in columns:
            self.db.execute('ALTER TABLE local_requests ADD COLUMN continuation_json TEXT')
        self.db.commit()

    def close(self):self.db.close()
    def meta(self,key,default=None):
        row=self.db.execute('SELECT value_json FROM local_meta WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def set_meta(self,key,value):
        self.db.execute('INSERT INTO local_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',(key,encode(value)));self.db.commit()
    def event(self,kind,details,request_id=None):
        self.db.execute('INSERT INTO local_events VALUES(?,?,?,?,?)',(str(uuid.uuid4()),request_id,stamp(),kind,encode(details)));self.db.commit()
    def get(self,request_id):
        row=self.db.execute('SELECT * FROM local_requests WHERE request_id=?',(request_id,)).fetchone()
        return dict(row) if row else None

    def enqueue(self,payload,reason,now=None,details=None):
        current=stamp(now);key=scope_key(payload)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute("SELECT * FROM local_requests WHERE scope_key=? AND state='pending'",(key,)).fetchone()
            if row:
                merged_payload=merge_recovery_payload(json.loads(row['payload_json']),payload)
                self.db.execute('UPDATE local_requests SET latest_trigger_at=?,trigger_count=trigger_count+1,payload_json=? WHERE request_id=?',(current,encode(merged_payload),row['request_id']))
                identity=row['request_id'];merged=True
            else:
                identity=str(uuid.uuid4());merged=False
                self.db.execute('INSERT INTO local_requests(request_id,scope_key,payload_json,state,created_at,latest_trigger_at) VALUES(?,?,?,?,?,?)',(identity,key,encode(payload),'pending',current,current))
            trigger={'reason':reason,'category':trigger_category(reason),'merged_pending':merged,**(details or {})}
            self.db.execute('INSERT INTO local_events VALUES(?,?,?,?,?)',(str(uuid.uuid4()),identity,current,'trigger',encode(trigger)))
            self.db.commit()
            return identity
        except BaseException:self.db.rollback();raise

    def request_trigger_summary(self,request_id):
        events=[]
        for row in self.db.execute("SELECT observed_at,kind,details_json FROM local_events WHERE request_id=? AND kind IN ('trigger','owner_exit_recovery','recovery_window_transfer') ORDER BY observed_at,rowid",(request_id,)):
            details=json.loads(row['details_json'])
            events.append({'at':row['observed_at'],'kind':row['kind'],**details})
        categories=sorted({e.get('category',trigger_category(e.get('reason',''))) for e in events})
        return {'categories':categories,'events':events,'legacy_reason_is_not_reinterpreted':True}

    def recover_under_lock(self):
        """Transfer windows and lineage atomically before interrupting any old owner.

        Call only after acquiring collector_lock, never based on a PID guess.
        Scope identity includes the pool, sources, data types and initialization
        bounds. Recovery metadata does not change the collection scope.
        """
        current=stamp();self.db.execute('BEGIN IMMEDIATE')
        try:
            rows=self.db.execute("SELECT * FROM local_requests WHERE state='running' ORDER BY created_at").fetchall()
            for row in rows:
                old=json.loads(row['payload_json'])
                successor=self.db.execute("SELECT * FROM local_requests WHERE scope_key=? AND state='pending'",(row['scope_key'],)).fetchone()
                incoming=copy.deepcopy(old)
                info=incoming.setdefault('_local',{})
                info['recovered_request_ids']=sorted(set(info.get('recovered_request_ids',[])+[row['request_id']]))
                event={'category':'unfinished_work_resume','successor':successor['request_id'] if successor else None,
                       'previous_started_at':row['started_at'],'previous_window_start_hint':old.get('window_start_hint'),
                       'previous_resume_scope_ids':old.get('resume_scope_ids',[])}
                if successor:
                    prior=json.loads(successor['payload_json'])
                    merged=merge_recovery_payload(prior,incoming)
                    event.update(successor_window_before=prior.get('window_start_hint'),
                                 effective_window_start_hint=merged.get('window_start_hint'),
                                 effective_resume_scope_ids=merged.get('resume_scope_ids',[]))
                    self.db.execute('UPDATE local_requests SET payload_json=? WHERE request_id=?',(encode(merged),successor['request_id']))
                    self.db.execute('INSERT INTO local_events VALUES(?,?,?,?,?)',(str(uuid.uuid4()),successor['request_id'],current,'recovery_window_transfer',encode({**event,'from_request_id':row['request_id']})))
                    self.db.execute("UPDATE local_requests SET state='interrupted',finished_at=?,error=? WHERE request_id=?",(current,'Owner exited; recovery window and scope evidence transferred atomically to pending successor',row['request_id']))
                else:
                    event.update(effective_window_start_hint=incoming.get('window_start_hint'),effective_resume_scope_ids=incoming.get('resume_scope_ids',[]))
                    self.db.execute("UPDATE local_requests SET state='pending',payload_json=?,error=? WHERE request_id=?",(encode(incoming),'Prior owner exited; resumed under acquired kernel lock',row['request_id']))
                self.db.execute('INSERT INTO local_events VALUES(?,?,?,?,?)',(str(uuid.uuid4()),row['request_id'],current,'owner_exit_recovery',encode(event)))
            self.db.commit()
            return len(rows)
        except BaseException:self.db.rollback();raise

    def claim(self):
        self.db.execute('BEGIN IMMEDIATE')
        row=self.db.execute("SELECT * FROM local_requests WHERE state='pending' ORDER BY created_at LIMIT 1").fetchone()
        if row:
            self.db.execute("UPDATE local_requests SET state='running',started_at=? WHERE request_id=?",(stamp(),row['request_id']))
        self.db.commit()
        return self.get(row['request_id']) if row else None

    def finish(self,request_id,state,result_path,error=None,continuation=None):
        if state not in {'completed','partial','failed'}:raise ValueError('Invalid terminal request state')
        self.db.execute('UPDATE local_requests SET state=?,finished_at=?,result_path=?,error=?,continuation_json=? WHERE request_id=?',(state,stamp(),str(result_path),error,encode(continuation) if continuation is not None else None,request_id));self.db.commit()

    def plan(self,config,now=None):
        now=now or utcnow();settings=config['local_schedule']
        if self.meta('paused',False):return {'paused':True,'requests':[]}
        zone=settings.get('timezone','Asia/Shanghai');times=settings['times'];ZoneInfo(zone)
        cursor=self.meta('planning_cursor')
        after=datetime.fromisoformat(cursor) if cursor else now-timedelta(days=1)
        # Cursor is a trigger-enumeration position; successful source progress is elsewhere.
        slots=schedule_slots(times,zone,after,now)
        existing={r[0] for r in self.db.execute('SELECT slot_utc FROM local_slots')}
        slots=[s for s in slots if s['utc'] not in existing]
        requests=[]
        if slots:
            hint=(datetime.fromisoformat(slots[0]['utc']).astimezone(ZoneInfo('America/New_York' if config['market']=='US' else zone)).date()-timedelta(days=settings.get('catchup_overlap_days',2))).isoformat()
            payload=base_payload(config);payload['window_start_hint']=max(config['start'],hint)
            delay=(now-datetime.fromisoformat(slots[-1]['utc'])).total_seconds()
            grace=settings.get('on_time_grace_seconds',settings.get('pulse_minutes',5)*60+60)
            reason='normal_schedule' if len(slots)==1 and 0<=delay<=grace else 'missed_schedule_catchup'
            identity=self.enqueue(payload,reason,now,details={'scheduled_slots':slots,'latest_slot_delay_seconds':round(delay,3)})
            for slot in slots:self.db.execute('INSERT OR IGNORE INTO local_slots VALUES(?,?,?,?)',(slot['utc'],slot['local'],identity,stamp(now)))
            self.db.commit();requests.append(identity)
            self.set_meta('automatic_retries_since_slot',0)
        elif not self.db.execute("SELECT 1 FROM local_requests WHERE state IN ('running','pending') LIMIT 1").fetchone():
            row=self.db.execute('SELECT * FROM local_requests ORDER BY created_at DESC LIMIT 1').fetchone()
            attempts=self.meta('automatic_retries_since_slot',0)
            if row and row['state'] in {'failed','partial','interrupted'} and attempts<settings.get('max_between_slot_retries',2):
                age=(now-datetime.fromisoformat(row['finished_at'] or row['latest_trigger_at'])).total_seconds()
                if age>=settings.get('retry_minutes',60)*60:
                    payload=json.loads(row['payload_json'])
                    continuation=json.loads(row['continuation_json']) if row['continuation_json'] else legacy_continuation(row)
                    # Initializations use their own durable, bounded batch ledger.
                    eligible=[] if payload.get('initialization') else [item for item in continuation.get('items',[]) if not item.get('eligible_after') or datetime.fromisoformat(item['eligible_after'].replace('Z','+00:00'))<=now]
                    if eligible:
                        payload['sources']=sorted({item['source_id'] for item in eligible})
                        payload['resume_scope_ids']=sorted({scope for item in eligible for scope in item.get('scope_ids',[])})
                        payload.setdefault('_local',{})['retry_parent_id']=row['request_id']
                        reason='unfinished_work_resume' if any(item['kind']=='unfinished_work_resume' for item in eligible) else 'limited_failure_retry'
                        requests.append(self.enqueue(payload,reason,now,details={'parent_request_id':row['request_id'],'attempt_between_slots':attempts+1,'continuation_evidence':eligible}))
                        self.set_meta('automatic_retries_since_slot',attempts+1)
        self.set_meta('planning_cursor',stamp(now))
        return {'paused':False,'coalesced_missed_slots':len(slots),'requests':requests,'cursor_semantics':'enumerated triggers only; NOT a source success watermark'}


# Metadata is not collection identity. Initialization IDs and bounds intentionally
# remain in the scope, preventing independent history jobs from being combined.
NON_SCOPE_KEYS={'window_start_hint','reason','resume_scope_ids','_local'}


def scope_key(payload):
    return hashlib.sha256(encode({k:v for k,v in payload.items() if k not in NON_SCOPE_KEYS}).encode()).hexdigest()


def merge_recovery_payload(destination,incoming):
    """Preserve the earliest required window; never broaden across scope identity."""
    if scope_key(destination)!=scope_key(incoming):
        raise ValueError('Cannot merge different collection scopes')
    result=copy.deepcopy(destination)
    bounds=[v for v in (destination.get('window_start_hint'),incoming.get('window_start_hint')) if v]
    if bounds:result['window_start_hint']=min(bounds)
    if destination.get('resume_scope_ids') or incoming.get('resume_scope_ids'):
        result['resume_scope_ids']=sorted(set(destination.get('resume_scope_ids',[])+incoming.get('resume_scope_ids',[])))
    lineage=sorted(set(destination.get('_local',{}).get('recovered_request_ids',[])+incoming.get('_local',{}).get('recovered_request_ids',[])))
    if lineage:result.setdefault('_local',{})['recovered_request_ids']=lineage
    return result


def trigger_category(reason):
    return {'normal_schedule':'normal_schedule','missed_schedule_catchup':'missed_schedule_catchup',
        'unfinished_work_resume':'unfinished_work_resume','limited_failure_retry':'limited_failure_retry',
        'manual_decision_refresh':'manual_refresh','manual_refresh':'manual_refresh',
        'low_frequency_history_revision_check':'historical_revision_check',
        'historical_revision_check':'historical_revision_check','historical_initialization':'historical_initialization',
        'initialization_resume':'historical_initialization',
        'manual_history_initialization':'historical_initialization',
        'initialization_unfinished_work':'unfinished_work_resume',
        'initialization_continuation':'unfinished_work_resume'}.get(reason,'legacy_or_unspecified')


def legacy_continuation(row):
    """No retrospective network retries from a bare legacy partial status."""
    return {'items':[],'suppressed':[{'reason':'Legacy request lacks task-level continuation evidence; next normal slot or explicit refresh will check it.'}]}


def build_continuation(store,run):
    """A source-specific work receipt, not an assertion of data completeness.

    Only unacquired pending work or retryable transport failures may trigger an
    extra collection. Parse/semantic gaps, unsupported discovery, and exhausted
    gates require another kind of intervention, never blind all-source refresh.
    """
    scope_ids=set(run.get('effective_scope',{}).get('source_scope_ids',{}).values())
    scope_ids.update(run.get('effective_scope',{}).get('resumed_scope_ids',[]))
    if not scope_ids:return {'items':[],'suppressed':[{'reason':'No persisted source scopes; preserve request window through owner recovery.'}]}
    placeholders=','.join('?' for _ in scope_ids)
    rows=store.db.execute("""SELECT DISTINCT t.task_id,t.source_id,t.task_kind,t.state,t.raw_id,t.next_attempt_at,
        w.failure_class,w.next_retry_at,w.exhausted_at,d.scope_id
        FROM machine_tasks t JOIN machine_task_demands d USING(task_id)
        LEFT JOIN machine_work_items w USING(task_id)
        WHERE d.scope_id IN ("""+placeholders+") AND t.state!='succeeded'",sorted(scope_ids)).fetchall()
    items=[];suppressed=[]
    retryable={'transient_network','rate_limit','authentication','permission'}
    for raw in rows:
        row=dict(raw);category=row['failure_class'];kind=None
        if row['exhausted_at'] or row['state']=='exhausted':reason='Retry budget exhausted'
        elif row['task_kind']=='reprocess' or category in {'parse','dependency','unsupported','resource_limit'}:reason='Requires parsing, capability, configuration or dependency intervention'
        elif row['raw_id'] and category not in retryable:reason='Raw already acquired; parse incompleteness is not network retry evidence'
        elif row['state'] in {'pending','deferred','running'} and category in {None,'budget'}:
            kind='unfinished_work_resume';reason=None
        elif category in retryable and row['state'] in {'failed','retry_wait','blocked'}:
            kind='limited_failure_retry';reason=None
        else:reason='No automatic transport-retry evidence'
        if kind:
            gates=[v for v in (row['next_attempt_at'],row['next_retry_at']) if v]
            eligible_after=max(gates,key=lambda v:datetime.fromisoformat(v.replace('Z','+00:00'))) if gates else None
            items.append({'source_id':row['source_id'],'scope_ids':[row['scope_id']],'task_ids':[row['task_id']],
                          'kind':kind,'failure_class':category,'eligible_after':eligible_after})
        else:suppressed.append({'source_id':row['source_id'],'scope_id':row['scope_id'],'task_id':row['task_id'],'reason':reason,'failure_class':category})
    return {'items':items,'suppressed':suppressed,'basis':'Persisted unfinished tasks in this request only; no retry from partial coverage alone.'}


def base_payload(config):
    import copy
    universe=copy.deepcopy(config.get('universe',{}))
    for key in ('symbols','ciks'):
        if key in universe:universe[key]=sorted(universe[key])
    csv_path=universe.get('csv_file')
    csv_hash=None
    if csv_path:
        path=(Path(config['_config_dir'])/csv_path).resolve()
        csv_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    payload={'universe':universe,'csv_sha256':csv_hash,
            'securities':copy.deepcopy(config.get('securities',[])),
            'symbols':sorted(universe.get('symbols',[])),
            'sources':sorted(config.get('sources',[])), 'data_types':sorted(config.get('data_types',[])),
            'full_history':False}
    if config.get('competition',{}).get('enabled',False):
        from .competition.runner import freeze_settings
        payload['competition_frozen']=freeze_settings(config)
    return payload
