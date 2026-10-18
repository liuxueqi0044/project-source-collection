"""Bounded, offline idempotence experiments on isolated evidence copies.

Source databases are opened read-only and backed up; raw files are copied, never
linked or modified. Public backfill excludes the persistently blocked BLS source.
SEC primary-document selection reads source submissions bytes, not parsed facts.
This does NOT certify SEC full-inventory backfill or whole-market completeness.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from equity_data.cli import load_config
from equity_data.machine import execute
from equity_data.store import Store


def now():return datetime.now(timezone.utc).isoformat()
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")


def snapshot_evidence(source,destination):
    """SQLite online backup is consistent; only immutable referenced blobs follow."""
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if destination.exists():
        raise ValueError("Refusing to overwrite experiment directory: "+str(destination))
    destination.mkdir(parents=True)
    src=sqlite3.connect((source/"data.sqlite3").as_uri()+"?mode=ro",uri=True)
    src.row_factory=sqlite3.Row
    dst=sqlite3.connect(destination/"data.sqlite3")
    try:
        started=now()
        src.backup(dst)
        dst.row_factory=sqlite3.Row
        pending=[dict(row) for row in dst.execute("SELECT task_kind,source_id,state,COUNT(*) AS n FROM machine_tasks WHERE state!='succeeded' GROUP BY task_kind,source_id,state")]
        running=[dict(row) for row in dst.execute("SELECT run_id,mode,generation,state,lease_until FROM machine_runs WHERE state='running'")]
        originals=[dict(row) for row in dst.execute("SELECT raw_id,path,sha256,byte_size FROM raw_blobs")]
        for original in originals:
            relative=Path(original["path"])
            if relative.is_absolute():relative=relative.relative_to(source)
            source_file=(source/relative).resolve()
            copied_file=(destination/relative).resolve()
            if not source_file.is_relative_to(source) or not copied_file.is_relative_to(destination):
                raise ValueError("Raw evidence path escapes source or destination")
            copied_file.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source_file,copied_file)
            if hashlib.sha256(copied_file.read_bytes()).hexdigest()!=original["sha256"]:
                raise ValueError("Raw SHA-256 failed while copying: "+original["raw_id"])
        return {"source_storage":str(source),"experiment_storage":str(destination),"started_at":started,"completed_at":now(),
                "source_access":"read-only SQLite backup and file reads; original tasks were not deleted or edited",
                "raw_blobs_copied_and_verified":len(originals),"raw_bytes":sum(r["byte_size"] for r in originals),
                "source_unfinished_task_snapshot":pending,"source_running_run_snapshot":running}
    finally:
        dst.close();src.close()


def select_primary_documents(storage,start,end,per_company):
    db=sqlite3.connect((Path(storage).resolve()/"data.sqlite3").as_uri()+"?mode=ro",uri=True)
    db.row_factory=sqlite3.Row
    try:
        latest={}
        for row in db.execute("SELECT r.*,b.path,b.sha256 FROM raw_retrievals r JOIN raw_blobs b USING(raw_id) WHERE source='sec_edgar' ORDER BY retrieved_at,r.rowid"):
            latest[row["url"]]=dict(row)
        by_company={}
        for row in latest.values():
            if "/submissions/CIK" not in row["url"]:continue
            import re
            match=re.search(r"CIK(\d{10})",row["url"])
            if not match:continue
            cik=match[1]
            path=Path(row["path"])
            body=(path if path.is_absolute() else Path(storage)/path).read_bytes()
            payload=json.loads(body)
            columns=payload.get("filings",{}).get("recent",payload)
            if not isinstance(columns.get("accessionNumber"),list):continue
            for index,accession in enumerate(columns["accessionNumber"]):
                form=columns["form"][index]
                day=columns["filingDate"][index]
                primary=columns["primaryDocument"][index]
                if not start<=day<=end or form not in {"10-K","10-Q","20-F","40-F"} or not primary:continue
                url=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-','')}/{primary}"
                candidate={"company_id":"sec:"+cik,"accession":accession,"form":form,"filing_date":day,"url":url,
                           "listing_raw_id":row["raw_id"],"listing_sha256":row["sha256"],"listing_locator":f"/primaryDocument/{index}"}
                candidate["selection_score"]=hashlib.sha256(("v3-bounded-idempotency-primary-v1|"+cik+"|"+accession+"|"+primary).encode()).hexdigest()
                by_company.setdefault("sec:"+cik,{})[url]=candidate
        selections=[]
        populations=[]
        for company,candidates in sorted(by_company.items()):
            ordered=sorted(candidates.values(),key=lambda row:row["selection_score"])
            chosen=ordered[:per_company]
            populations.append({"company_id":company,"eligible_source_listed_primaries":len(ordered),"selected":len(chosen)})
            for item in chosen:
                raw=latest.get(item["url"])
                item.update(raw_id=raw["raw_id"] if raw else None,sha256=raw["sha256"] if raw else None,
                            available_in_saved_raw=raw is not None)
                selections.append(item)
        return {"method":"Fixed hash ordering of source-listed annual/quarterly primary documents per company, selected BEFORE checking saved primary availability; no parsed record reads",
                "seed":"v3-bounded-idempotency-primary-v1","scope":{"start":start,"end":end,"per_company":per_company},
                "populations":populations,"selected":selections,
                "limitations":["Only saved official submissions listings define this bounded sampling frame.",
                               "No assertion of all SEC forms, all primary documents or full backfill idempotence.",
                               "Unavailable selected originals remain missing; not replaced with conveniently parsed alternatives."]}
    finally:db.close()


def counts(store):
    return {table:store.db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
            for table in ("records","raw_blobs","raw_retrievals","record_validations","machine_reprocess_audits")}


def phase(store,config,args,out,name):
    before=counts(store)
    started=now()
    result=execute(store,config,args)
    after=counts(store)
    tasks=[dict(row) for row in store.db.execute("""SELECT a.attempt_id,a.task_id,a.state,a.error,t.source_id,t.task_kind,t.resource_id,t.raw_id,t.generation
        FROM machine_attempts a JOIN machine_tasks t USING(task_id) WHERE a.run_id=? ORDER BY a.started_at,a.rowid""",(result["run_id"],))]
    selected=set(config.get("sources",[]))
    unfinished=[dict(row) for row in store.db.execute("SELECT source_id,task_kind,state,COUNT(*) AS n FROM machine_tasks WHERE state!='succeeded' GROUP BY source_id,task_kind,state") if row["source_id"] in selected]
    evidence={"started_at":started,"ended_at":now(),"before":before,"after":after,"delta":{key:after[key]-before[key] for key in before},
              "run":result,"actual_executed_tasks":tasks,"zero_network_requests":result["network_budget"]["requests"]==0,
              "selected_source_unfinished_task_counts":unfinished,
              "note":"Global execution_complete can remain false due preserved excluded/unfinished source tasks. This phase is a bounded data-idempotence check, not complete data coverage."}
    save(out/(name+".json"),evidence)
    return evidence


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--public-config",default="configs/v3_public_channels.toml")
    p.add_argument("--sec-config",default="configs/v3_holdout.toml")
    p.add_argument("--out",default="reports/v3/bounded-idempotency")
    p.add_argument("--work-root",default="data/v3_bounded_idempotency")
    p.add_argument("--per-company",type=int,default=1)
    p.add_argument("--sec-max-tasks",type=int,default=12)
    p.add_argument("--runtime-seconds",type=int,default=180)
    options=p.parse_args()
    if options.per_company<1 or options.sec_max_tasks<1 or options.runtime_seconds<1:raise ValueError("Budgets and sample size must be positive")
    out=Path(options.out).resolve();out.mkdir(parents=True,exist_ok=True)
    if (out/"experiment.json").exists():raise ValueError("Refusing to overwrite prior experiment evidence")
    work=Path(options.work_root).resolve()
    report={"started_at":now(),"status":"running","scope":"Offline independent SQLite/raw copies; public selected sources backfill and SEC sampled primary-document reparse ONLY",
            "sec_full_inventory_backfill_idempotence":"not_completed; previous interrupted warm-up/pending work remains in original database and snapshot evidence",
            "public_excluded_sources":["bls"],"software_or_live":"Original bytes were obtained live; this experiment is offline replay, not live source recovery"}
    save(out/"experiment.json",report)
    try:
        configs={"public":load_config(options.public_config),"sec":load_config(options.sec_config)}
        for name,config in configs.items():
            copy_evidence=snapshot_evidence(config["storage"],work/name)
            save(out/(name+"-source-snapshot.json"),copy_evidence)
            config["storage"]=str(work/name)
            config.setdefault("budget",{}).update(max_requests=0,max_tasks=120,max_runtime_seconds=options.runtime_seconds)
        public=configs["public"]
        public["sources"]=[source for source in public["sources"] if source!="bls"]
        report["public_selected_sources"]=public["sources"]
        common=dict(sources=None,symbols=None,start=None,end=None,offline=True,full_history=False)
        with Store(public["storage"]) as store:
            arguments=SimpleNamespace(command="backfill",max_tasks=120,**common)
            report["public_warmup"]=phase(store,public,arguments,out,"public-warmup")
            report["public_repeats"]=[phase(store,public,arguments,out,f"public-repeat-{i}") for i in (1,2)]
        sec=configs["sec"]
        sample=select_primary_documents(sec["storage"],sec["start"],sec["end"],options.per_company)
        save(out/"sec-primary-sample.json",sample)
        raw_ids=[row["raw_id"] for row in sample["selected"] if row["raw_id"]]
        if not sample["selected"] or len(raw_ids)!=len(sample["selected"]):
            raise ValueError("One or more independently selected primary originals unavailable; no replacement sampling")
        sec["sources"]=["sec_edgar"]
        with Store(sec["storage"]) as store:
            older=[dict(row) for row in store.db.execute("SELECT task_id,state,raw_id FROM machine_tasks WHERE source_id='sec_edgar' AND task_kind='reprocess' AND state IN ('pending','retry_wait','running')")]
            if older:
                save(out/"sec-preexisting-reprocess-frontier.json",older)
                raise ValueError("Existing SEC reprocess frontier would broaden raw-id execution; preserved and not erased")
            arguments=SimpleNamespace(command="reprocess",max_tasks=options.sec_max_tasks,reprocess_versions="latest",raw_ids=",".join(raw_ids),**common)
            report["sec_selected_raw_ids"]=raw_ids
            report["sec_warmup"]=phase(store,sec,arguments,out,"sec-warmup")
            report["sec_repeats"]=[phase(store,sec,arguments,out,f"sec-repeat-{i}") for i in (1,2)]
        checks=report["public_repeats"]+report["sec_repeats"]
        data_stable=all(all(delta==0 for delta in item["delta"].values()) and item["zero_network_requests"] for item in checks)
        public_complete=all(not item["selected_source_unfinished_task_counts"] for item in report["public_repeats"])
        successful_targets=all({t["raw_id"] for t in item["actual_executed_tasks"] if t["task_kind"]=="reprocess" and t["state"]=="succeeded"}>=set(raw_ids) for item in report["sec_repeats"])
        unexpected={t["raw_id"] for item in report["sec_repeats"] for t in item["actual_executed_tasks"]}-set(raw_ids)
        report.update(status="completed",bounded_passed=data_stable and public_complete and successful_targets and not unexpected,
                      public_selected_sources_task_complete=public_complete,
                      successful_sampled_targets_each_repeat=successful_targets,unexpected_executed_raw_ids=sorted(str(value) for value in unexpected),
                      interpretation="No result here establishes full SEC backfill idempotence, field accuracy, whole-source completeness or long-running live recovery.")
    except BaseException as exc:
        report.update(status="interrupted" if isinstance(exc,KeyboardInterrupt) else "failed",error=type(exc).__name__+": "+str(exc),bounded_passed=False)
        raise
    finally:
        report["ended_at"]=now()
        save(out/"experiment.json",report)
    print(json.dumps({key:report[key] for key in ("status","bounded_passed","scope","sec_full_inventory_backfill_idempotence")},ensure_ascii=False))


if __name__=="__main__":main()
