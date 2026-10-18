"""Freeze production code BEFORE selecting and fetching held-out issuers."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
POOL={'consumer_off_calendar':['NKE','SBUX'], 'industrial':['CAT','HON'], 'foreign_issuer':['SAP','TSM']}
SEED='v3-oct09-raw-first-generalization-1'


def hashes():
    paths=sorted((ROOT/'equity_data').rglob('*.py'))+[ROOT/'configs/source_registry.json',ROOT/'pyproject.toml']
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def freeze(path):
    target=Path(path)
    if target.exists():raise ValueError('Freeze already exists; use a new named freeze, never overwrite')
    digest=hashes()
    selected={group:min(symbols,key=lambda s:hashlib.sha256((SEED+'|'+group+'|'+s).encode()).hexdigest()) for group,symbols in POOL.items()}
    result={'created_at':datetime.now(timezone.utc).isoformat(),'production_hashes':digest,
            'sampling_seed':SEED,'eligible_pool':POOL,'selected':selected,
            'selection_method':'One issuer per declared structural stratum; smallest SHA256(seed|stratum|symbol), selected only after code hashes frozen',
            'limitations':['Small purposive structural pool, not a random US equity universe or unbiased backtest pool.',
                           'Issuer strata are sampling intentions; actual document structures are recorded after acquisition.',
                           'If production changes after inspection, this sample loses final holdout status.']}
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--verify',action='store_true');a=p.parse_args()
    if a.verify:
        before=json.loads(Path(a.out).read_text(encoding='utf-8'))['production_hashes'];after=hashes()
        changed=[k for k in set(before)|set(after) if before.get(k)!=after.get(k)]
        print(json.dumps({'unchanged':not changed,'changed':changed}));raise SystemExit(bool(changed))
    result=freeze(a.out);print(json.dumps({'selected':result['selected'],'files':len(result['production_hashes'])}))
