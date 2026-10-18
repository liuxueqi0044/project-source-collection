"""Read-only comparison against already frozen raw-first audit targets."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import hashlib
import json
import re
import sqlite3

BASE=Path(__file__).resolve().parents[1]
OUT=BASE/'reports/v3'
path=OUT/'narrative-reference.json'
payload=path.read_bytes()
manifest=json.loads((OUT/'narrative-reference-freeze.json').read_text(encoding='utf-8'))
assert hashlib.sha256(payload).hexdigest()==manifest['sha256']
reference=json.loads(payload)
c=sqlite3.connect(f'file:{(BASE/"data/v3_holdout/data.sqlite3").as_posix()}?mode=ro',uri=True)
c.row_factory=sqlite3.Row
snapshot_started=datetime.now(timezone.utc).isoformat()
c.execute('BEGIN')
records=[dict(row) for row in c.execute("SELECT record_id,dataset,record_key,raw_id,data_json,source_locator,unit,currency,period_start,period_end,parser_version FROM records WHERE raw_id IN (?,?,?) AND dataset IN ('guidance','operating_metrics','relations','debt_maturities','commitments')",tuple(dict.fromkeys(t['raw_id'] for t in reference['targets'])))]
gaps=[dict(row) for row in c.execute("SELECT gap_id,code,message,details_json FROM machine_gaps WHERE resolved_at IS NULL")]
for row in records:row['data']=json.loads(row.pop('data_json'))
for row in gaps:row['details']=json.loads(row.pop('details_json'))

def compact(text):return re.sub(r'\s+','',text or '').lower()
def matches_excerpt(expected,actual):
    a,b=compact(expected),compact(actual)
    return bool(a and b and (a in b or b in a and len(b)>=100))

results=[]
for target in reference['targets']:
    rows=[row for row in records if row['raw_id']==target['raw_id'] and row['dataset']==target['family'] and matches_excerpt(target['evidence_excerpt'],row['data'].get('evidence_excerpt',row['data'].get('text','')))]
    matched_gaps=[row for row in gaps if row['details'].get('raw_id')==target['raw_id'] and matches_excerpt(target['evidence_excerpt'],row['details'].get('details',{}).get('evidence_excerpt',''))]
    structured=[row for row in rows if row['data'].get('fact_stage')=='extracted_fact']
    candidates=[row for row in rows if row['data'].get('fact_stage')=='candidate']
    if structured:
        classification='unverified'
        rationale='Structured result requires manual field-by-field comparison; excerpt match alone never proves correctness'
    elif candidates:
        classification='candidate_only'
        rationale='Exact source-bound evidence found, but the stored row explicitly remains a narrative candidate, not the required structured contract'
    elif matched_gaps:
        classification='explicit_reject'
        rationale='Source-bound parser issue explicitly reports refusal or interrupted scope'
    else:
        inventory=[dict(row) for row in c.execute('SELECT resource_id,state,raw_id FROM machine_inventory WHERE raw_id=?',(target['raw_id'],))]
        if not any(row['state'] in {'parsed','partial_parse'} for row in inventory):
            classification='unverified';rationale='Source resource not yet fully attempted for parsing at audit snapshot'
        else:
            classification='silent_omission';rationale='No source-bound structured contract, same-family candidate or explicit source-span rejection after parse attempt; isolated financial tags do not fulfill this semantic target'
    results.append({'target_id':target['target_id'],'family':target['family'],'classification':classification,'rationale':rationale,
                    'matched_records':rows,'matched_issues':matched_gaps})
c.rollback();c.close()
report={'reference_sha256':manifest['sha256'],'reference_frozen_at':manifest['frozen_at_before_record_comparison'],
        'comparison_snapshot_started_at':snapshot_started,'database_read_only':True,'production_parser_called':False,
        'match_rule':'same raw hash AND same family AND overlapping long evidence span; no cross-document or financial-tag-only success',
        'denominator':len(results),'summary':dict(Counter(row['classification'] for row in results)),
        'results':results}
out=OUT/'narrative-reference-comparison.json'
assert not out.exists(),'Do not overwrite audit snapshot'
out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'summary':report['summary'],'targets':[(r['target_id'],r['classification'],len(r['matched_records']),[i['code'] for i in r['matched_issues']]) for r in results]},indent=2))
