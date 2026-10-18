"""Read-only library identity/count snapshot for repeat and merge evidence."""
from pathlib import Path
import argparse
from datetime import datetime,timezone
import hashlib
import json
import sqlite3

def snapshot(root):
    root=Path(root).resolve()
    db=sqlite3.connect((root/'data.sqlite3').as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    try:
        existing={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts={table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in
            ('records','raw_blobs','raw_retrievals','machine_tasks','machine_resource_versions','initialization_batches','initialization_events') if table in existing}
        digest=hashlib.sha256()
        for row in db.execute('SELECT * FROM records ORDER BY sequence'):
            digest.update(json.dumps(dict(row),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8'))
            digest.update(b'\n')
        return {'observed_at':datetime.now(timezone.utc).isoformat(),'storage':str(root),'counts':counts,
            'business_rows_sha256':digest.hexdigest(),
            'datasets':dict(db.execute('SELECT dataset,COUNT(*) FROM records GROUP BY dataset')),
            'raw_bytes':db.execute('SELECT COALESCE(SUM(byte_size),0) FROM raw_blobs').fetchone()[0],
            'database_physical_bytes':sum(p.stat().st_size for p in root.glob('*.sqlite3*')),
            'basis':'One read-only SQLite snapshot; all columns of every append-only business row hashed in sequence order. Physical bytes may vary with WAL/checkpoint and are not business duplicates.'}
    finally:db.rollback();db.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--storage',default='data/live');p.add_argument('--out',required=True)
    args=p.parse_args();result=snapshot(args.storage);out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
