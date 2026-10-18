"""Execute live no-op initialization and two ordinary refreshes; preserve all evidence."""
from pathlib import Path
import json
import subprocess
import sys
import argparse
from datetime import datetime, timezone
from snapshot_library_v41 import snapshot

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/v4_1/live-repeat-and-refresh'

def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='reports/v4_1/live-repeat-and-refresh')
    parser.add_argument('--sources')
    parser.add_argument('--skip-initialization', action='store_true')
    args = parser.parse_args()
    OUT = ROOT / args.out
    OUT.mkdir(exist_ok=False)
    results = []
    refresh_args = ['refresh'] + (['--sources', args.sources] if args.sources else [])
    steps = ([] if args.skip_initialization else [('repeat-initialization', ['initialize', '--max-batches', '1'])]) + [
             ('increment-first', refresh_args), ('increment-repeat', refresh_args)]
    for label, arguments in steps:
        before = snapshot(ROOT / 'data/live')
        save(label + '-before.json', before)
        command = [sys.executable, '-m', 'equity_data.local', '--config', 'configs/live.toml', *arguments]
        with (OUT / (label + '-stdout.json')).open('w', encoding='utf-8') as stdout, \
             (OUT / (label + '-stderr.txt')).open('w', encoding='utf-8') as stderr:
            process = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                     env={**__import__('os').environ, 'PYTHONUTF8': '1'})
        after = snapshot(ROOT / 'data/live')
        save(label + '-after.json', after)
        response = json.loads((OUT / (label + '-stdout.json')).read_text(encoding='utf-8')) if process.returncode == 0 else {}
        reports = []
        for request in response.get('execution', {}).get('completed', []):
            path = request.get('result_path')
            if path and Path(path).exists():
                report = json.loads(Path(path).read_text(encoding='utf-8'))
                reports.append({k: report.get(k) for k in ('request_id', 'started_at', 'finished_at',
                    'trigger', 'metrics', 'storage_growth', 'task_execution_complete', 'source_checks_complete',
                    'parsing_complete', 'required_freshness_satisfied', 'current_scope_checks', 'uncompleted', 'gaps_count')})
        result = {'step': label, 'command': command, 'exit_code': process.returncode,
                  'business_rows_identical': before['business_rows_sha256'] == after['business_rows_sha256'],
                  'count_changes': {k: after['counts'].get(k, 0) - v for k, v in before['counts'].items()},
                  'raw_byte_change': after['raw_bytes'] - before['raw_bytes'], 'reports': reports,
                  'execution_status': response.get('execution', {}).get('status')}
        results.append(result)
        save('result.json', {'observed_at': datetime.now(timezone.utc).isoformat(), 'steps': results,
                            'note': 'Record hashes cover every stored business column. Retrieval, task and audit history may grow without duplicating business records. Exit code alone does not prove refresh or coverage.'})
        print(json.dumps({k: result[k] for k in ('step','exit_code','business_rows_identical','count_changes')}, ensure_ascii=False), flush=True)
        if process.returncode != 0:
            return process.returncode
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
