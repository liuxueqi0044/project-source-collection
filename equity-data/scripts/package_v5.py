"""V5 delivery using the verified V4/V4.1 locking, backup and privacy engine."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path

MANIFEST='DELIVERY_MANIFEST_V5.json'

class ManifestJson:
    def dumps(self,value,*args,**kwargs):
        if isinstance(value,dict) and value.get('source_manifest_written')==MANIFEST:
            value={**value,'manifest_version':'delivery-v5-sha256-v1',
                'historical_manifests':'Earlier manifests remain historical; DELIVERY_MANIFEST_V5.json describes this delivery.',
                'packaging_engine':'Verified V4 engine plus V4.1 and V5 operational-table privacy policy.',
                'new_operational_table_policy':'Competition run/job/cycle diagnostic JSON may be scrubbed in delivery copies only; immutable records/raw/evidence are unchanged.'}
        return json.dumps(value,*args,**kwargs)
    def __getattr__(self,name):return getattr(json,name)

def package(root,output):
    spec=importlib.util.spec_from_file_location('_v5_parent_package',Path(__file__).with_name('package_v41.py'))
    parent=importlib.util.module_from_spec(spec);spec.loader.exec_module(parent)
    engine=parent._load_engine();engine.MANIFEST=MANIFEST;engine.json=ManifestJson()
    prior_columns=engine.operational_columns
    def operational_columns(connection):
        yield from prior_columns(connection)
        allowed={'competition_jobs':{'input_json','result_json','last_error'},
                 'competition_job_history':{'details_json'},'competition_runs':{'plan_json','result_json'},
                 'competition_cycles':{'plan_json'},'competition_endpoint_state':{'last_error'}}
        for table,columns in allowed.items():
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():continue
            for row in connection.execute('PRAGMA table_info('+engine._quote(table)+')'):
                if row[1] in columns:yield table,row[1]
    engine.operational_columns=operational_columns
    previous=engine.exclusion
    def exclusion(path,project):
        if path.relative_to(project).as_posix()==MANIFEST:return 'v5_manifest_regenerated'
        return previous(path,project)
    engine.exclusion=exclusion
    return engine.package(Path(root).resolve(),Path(output).resolve())

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',default=Path(__file__).resolve().parents[1],type=Path)
    parser.add_argument('--output',default=Path(__file__).resolve().parents[2]/'equity-data-system-v5.zip',type=Path)
    args=parser.parse_args();print(json.dumps(package(args.project_root,args.output),ensure_ascii=False,indent=2))
