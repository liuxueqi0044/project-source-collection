"""Read retained local-run evidence, without changing databases or old reports."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--since',default='2026-10-10T09:24:00+00:00')
    p.add_argument('--out',default='reports/v4_1/live-rounds-final')
    a=p.parse_args(); since=datetime.fromisoformat(a.since)
    rows=[]; totals=Counter(); business=Counter()
    for path in sorted((ROOT/'data/live/local-runs').glob('*/decision-report.json')):
        value=json.loads(path.read_text(encoding='utf-8'))
        if datetime.fromisoformat(value['started_at']) < since:continue
        metrics=value.get('metrics') or {}; growth=value.get('storage_growth') or {}
        row={k:value.get(k) for k in ('request_id','started_at','finished_at','trigger','task_execution_complete','parsing_complete','source_checks_complete','required_freshness_satisfied')}
        row.update(evidence=str(path.relative_to(ROOT)),report_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            requested=value.get('requested'),effective_scope=value.get('effective_scope'),
            metrics=metrics,storage_growth=growth,new_records_by_dataset=value.get('new_records_by_dataset'),
            initialization_progress={k:value.get('initialization',{}).get(k) for k in ('state','batch_count','acquired_batches','parsed_batches','attempted_batches')},
            remaining=value.get('uncompleted'),gaps_count=value.get('gaps_count'))
        rows.append(row)
        for key in ('requests','bytes','elapsed_seconds'):totals[key]+=metrics.get(key,0)
        business.update(value.get('new_records_by_dataset') or {})
    rows.sort(key=lambda r:r['started_at'])
    result={'since':a.since,'completed_report_count':len(rows),'totals':dict(totals),
        'new_stored_versions_by_dataset':dict(business),'rounds':rows,
        'basis':'Actual persisted local run reports; unfinished current processes and raw-cache import excluded. Counts are versions, not independent coverage. Database physical growth includes WAL changes and is not a business-duplication measure.'}
    out=ROOT/a.out;out.parent.mkdir(parents=True,exist_ok=True)
    out.with_suffix('.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 本轮日常库真实运行流水','',f"起点 {a.since}；已落完整报告 {len(rows)} 轮；HTTP {totals['requests']}，下载 {totals['bytes']:,} 字节。",'',
        '|开始（原时区）|请求|触发|HTTP|下载字节|耗时秒|新增版本|原文增量|初始化取得窗口|',
        '|---|---|---|---:|---:|---:|---:|---:|---|']
    for r in rows:
        m=r['metrics'];g=r['storage_growth'];i=r['initialization_progress']
        lines.append(f"|{r['started_at']}|{r['request_id']}|{','.join((r['trigger'] or {}).get('categories',[]))}|{m.get('requests',0)}|{m.get('bytes',0)}|{m.get('elapsed_seconds',0)}|{g.get('records',0)}|{g.get('raw_blobs',0)}|{i.get('acquired_batches')}/{i.get('batch_count')}|")
    lines+=['','精确范围、各源请求、原文/数据库字节增长、未完成项和原报告哈希见同名 JSON。原文导入审计另计；本表不能当作完整性或数值准确率。']
    out.with_suffix('.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='rounds'},ensure_ascii=False))

if __name__=='__main__':main()
