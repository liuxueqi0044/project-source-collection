"""Host-only audited cancellation runner; accepts only fixed contact 1/2 targets."""
from __future__ import annotations
import base64, hashlib, importlib.util, json, os, re, time, uuid
from pathlib import Path
from typing import Any
ROOT=Path(r'C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat'); CONTROL=ROOT/r'outputs\qq-vm\install\control_qq_runtime_host.py'; START=ROOT/r'outputs\qq-vm\install\start_qq_runtime_host.py'; SCRIPT=ROOT/r'outputs\personal-messenger-ai-v5\scripts\deployment\cancel_stale_hub_trigger_guest.py'; DIAG=ROOT/r'outputs\qq-vm\install\diagnostics'
PY=r'C:\PMAI\app\.venv\Scripts\python.exe'; DATA=r'C:\PMAI\data'; DATA_DIR=DATA+r'\runtime\qq-default-account'; HUB=DATA_DIR+r'\hub.sqlite3'; SCHEMA='pmai-stale-hub-trigger-cancel-host-v2'
TARGETS={1:('qq-session-conversation-1','4b10624a-c907-402f-bb52-122a16bc2b67','message_anchor_gap'),2:('qq-session-conversation-2','148c7b0b-7e40-4984-949d-22f93d712c0d','driver_quarantine:read_only_observe_timeout')}; SAFE=re.compile(r'^[A-Z][A-Z0-9_]{2,127}$')
def load(p,n):
 s=importlib.util.spec_from_file_location(n,p)
 if not s or not s.loader:raise RuntimeError('CONTROL_UNAVAILABLE')
 m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def atomic(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);t=p.with_name('.'+p.name+'.tmp-'+uuid.uuid4().hex)
 try:t.write_text(json.dumps(v,sort_keys=True,separators=(',',':')),encoding='utf-8');os.replace(t,p)
 finally:t.unlink(missing_ok=True)
def stopped(c,start,g,n):
 s=c._runtime_status(g,'cancel-v2-'+n);pid=s.get('runtime_process_id')
 if s.get('runtime_process_alive') is not False or not isinstance(pid,int) or isinstance(pid,bool) or pid<=0 or start._guest_pid_alive(g,pid):raise RuntimeError('RUNTIME_NOT_STOPPED')
def valid_meta(v,conv,outbox):
 keys={'outbox_id','event_type','status','attempt_count','created_at','available_at'}
 if not isinstance(v,dict) or set(v)!=keys or v['outbox_id']!=outbox or v['event_type']!='conversation.stable_window' or v['status']!='pending' or v['attempt_count']!=0 or not all(isinstance(v[k],str) and len(v[k])<80 for k in ('created_at','available_at')):raise RuntimeError('OUTBOX_METADATA_INVALID')
 return v
def valid_result(v,outbox):
 if not isinstance(v,dict) or v.get('schema')!='pmai-stale-hub-trigger-cancel-v2' or not isinstance(v.get('succeeded'),bool):raise RuntimeError('GUEST_REPORT_INVALID')
 if v['succeeded']:
  if set(v)!={'schema','succeeded','status','idempotent','outbox_id'} or v['status']!='succeeded' or not isinstance(v['idempotent'],bool) or v['outbox_id']!=outbox:raise RuntimeError('GUEST_REPORT_INVALID')
 else:
  if set(v)!={'schema','succeeded','error_code'} or not isinstance(v['error_code'],str) or not SAFE.fullmatch(v['error_code']):raise RuntimeError('GUEST_REPORT_INVALID')
 return v
def query_code():return r'''import json,os,sqlite3,sys
p,oid,out=sys.argv[1:];v={'error_code':'QUERY_FAILED'}
try:
 d=sqlite3.connect('file:'+p.replace('\\','/')+'?mode=ro',uri=True);r=d.execute("SELECT outbox_id,event_type,status,attempt_count,created_at,available_at FROM outbox WHERE outbox_id=?",(oid,)).fetchone();d.close()
 if r is not None:v=dict(zip(('outbox_id','event_type','status','attempt_count','created_at','available_at'),r))
except Exception:pass
t=out+'.tmp';open(t,'w',encoding='utf-8').write(json.dumps(v,separators=(',',':')));os.replace(t,out)'''
def main():
 import argparse;p=argparse.ArgumentParser();p.add_argument('--contact-index',type=int,choices=(1,2),required=True);a=p.parse_args();conv,outbox,pause=TARGETS[a.contact_index];n=uuid.uuid4().hex;report={'schema':SCHEMA,'contact_index':a.contact_index,'conversation_id':conv,'outbox_id':outbox,'succeeded':False};g=h=None;clear=bytearray();meta_g=DATA+rf'\cancel-meta-{n}.json';res_g=DATA+rf'\cancel-result-{n}.json';script_g=DATA+rf'\cancel-stale-{n}.py';script_h=DIAG/f'.cancel-script-{n}.py';meta_h=DIAG/f'.cancel-meta-{n}.json';res_h=DIAG/f'.cancel-result-{n}.json'
 try:
  c=load(CONTROL,'cancel_v2_control');start=load(START,'cancel_v2_start');_,h,g,clear,_=c._open_guest();stopped(c,start,g,n)
  enc=base64.b64encode(query_code().encode()).decode();q=g.ProcessCreate(PY,[PY,'-c',"import base64;exec(base64.b64decode(%r))"%enc,HUB,outbox,meta_g],DATA,[],[],30000)
  if q.WaitForArray([1],30000)!=1 or q.WaitForArray([2],30000)!=2 or int(q.ExitCode)!=0:raise RuntimeError('METADATA_QUERY_FAILED')
  meta=valid_meta(c._copy_from_guest(g,meta_g,meta_h),conv,outbox);report['metadata']=meta
  source_hash=hashlib.sha256(SCRIPT.read_bytes()).hexdigest();c._copy_to_guest(g,SCRIPT,script_g);copy=g.FileCopyFromGuest(script_g,str(script_h),[]);copy.WaitForCompletion(30000)
  if not bool(copy.Completed) or int(copy.ResultCode)!=0 or hashlib.sha256(script_h.read_bytes()).hexdigest()!=source_hash:raise RuntimeError('GUEST_SCRIPT_HASH_MISMATCH')
  args=[PY,script_g,'--data-dir',DATA_DIR,'--conversation-id',conv,'--outbox-id',outbox,'--event-type',meta['event_type'],'--status',meta['status'],'--attempt-count',str(meta['attempt_count']),'--created-at',meta['created_at'],'--available-at',meta['available_at'],'--pause-reason',pause,'--operator-id','codex_operator','--reason-code','STALE_HUB_TRIGGER','--report',res_g]
  x=g.ProcessCreate(PY,args,DATA,[],[],60000)
  if x.WaitForArray([1],30000)!=1 or x.WaitForArray([2],60000)!=2:raise RuntimeError('CANCEL_PROCESS_FAILED')
  report['exit_code']=int(x.ExitCode);report['guest_result']=valid_result(c._copy_from_guest(g,res_g,res_h),outbox)
  if report['exit_code']!=0 or not report['guest_result']['succeeded']:raise RuntimeError('CANCEL_REJECTED')
  report['succeeded']=True;return 0
 except Exception as e:report['error_code']=str(e) if SAFE.fullmatch(str(e)) else type(e).__name__;return 2
 finally:
  for pth in (script_h,meta_h,res_h):pth.unlink(missing_ok=True)
  if g:
   for pth in (script_g,meta_g,res_g):
    try:g.FileDelete(pth)
    except Exception:pass
  for i in range(len(clear)):clear[i]=0
  if g:
   try:g.Close()
   except Exception:pass
  if h:
   try:h.UnlockMachine()
   except Exception:pass
  atomic(DIAG/f'cancel-stale-hub-trigger-v2-{a.contact_index}-{n}.json',report)
if __name__=='__main__':raise SystemExit(main())
