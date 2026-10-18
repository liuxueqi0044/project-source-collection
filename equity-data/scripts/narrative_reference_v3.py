"""Audit only. Raw-first evidence annotation, never production extraction rules."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone
from bs4 import BeautifulSoup, Tag

BASE = Path(__file__).resolve().parents[1]
STORAGE = BASE / "data/v3_holdout"
OUT = BASE / "reports/v3"

def independent_path(node):
    parts=[]
    while isinstance(node,Tag) and node.name != '[document]':
        siblings = [c for c in node.parent.children if isinstance(c,Tag) and c.name == node.name] if node.parent else [node]
        index = next((i for i,n in enumerate(siblings,1) if n is node),1)
        parts.append(f'{node.name}[{index}]')
        node = node.parent
    return '/'+'/'.join(reversed(parts))

def raw_documents():
    c=sqlite3.connect(f'file:{STORAGE.as_posix()}/data.sqlite3?mode=ro',uri=True)
    c.row_factory=sqlite3.Row
    rows=c.execute("SELECT r.url,b.raw_id,b.sha256,b.path,b.byte_size FROM raw_retrievals r JOIN raw_blobs b USING(raw_id) WHERE r.url LIKE '%htm%' AND r.url NOT LIKE '%index%' ORDER BY r.url").fetchall()
    c.close()
    wanted=('hon-20231231.htm','sbux-20240331.htm','sap-20231231x20f.htm')
    return [dict(row) for row in rows if row['url'].split('/')[-1] in wanted]

def catalog():
    rules={'guidance':r'\b(?:expect|outlook|guidance).{0,180}(?:million|billion|%|percent)',
           'operating_metrics':r'\b(?:comparable store|stores at|store count|cloud revenue|backlog|organic sales|transactions|ticket)\b',
           'relations':r'\b(?:supplier|suppliers|customer|customers|Nestl).{0,100}(?:agreement|accounted|represented|largest|concentration|single|contract)',
           'debt_maturities':r'\b(?:notes|debt|principal).{0,180}(?:matur|due|202[4-9])',
           'commitments':r'\b(?:purchase commitments|purchase obligations|lease commitments|lease payments|contractual obligations)\b'}
    results=[]
    for doc in raw_documents():
        content=(STORAGE/doc['path']).read_bytes()
        assert hashlib.sha256(content).hexdigest()==doc['sha256']
        soup=BeautifulSoup(content,'html.parser')
        selected=[]
        for node in soup.find_all(['p','div','tr']):
            if node.find(['p','div','tr']):continue
            text=' '.join(node.get_text(' ',strip=True).split())
            if not 30 <= len(text) <= 2400:continue
            categories=[name for name,rule in rules.items() if re.search(rule,text,re.I)]
            if categories:selected.append({'independent_path':independent_path(node),'excerpt':text,'categories':categories})
        results.append({**doc,'candidates':selected})
    (OUT/'narrative-raw-catalog.json').write_text(json.dumps({'raw_only_before_records':True,'created_at':datetime.now(timezone.utc).isoformat(),'documents':results},indent=2,ensure_ascii=False),encoding='utf-8')
    for doc in results:
        print(doc['url'],len(doc['candidates']))
        for family in rules:
            print('\nFAMILY',family)
            for index,item in [(i,item) for i,item in enumerate(doc['candidates']) if family in item['categories']][:5]:
                print(index,item['excerpt'])

if __name__=='__main__':catalog()
