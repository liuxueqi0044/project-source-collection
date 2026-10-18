"""Explicit manual historical bootstrap using the production queue and shared lock.

Rate override applies to this process only. It does not change Windows tasks or
the daily configuration. Only SEC initialization is supported by this operator
wrapper; source policy must be checked before choosing its request rate.
"""
from pathlib import Path
import argparse
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from equity_data.cli import load_config
from equity_data.initialization import build_initialization_payload
from equity_data.local import drain,status
from equity_data.local_control import Control,base_payload

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='configs/live.toml')
    parser.add_argument('--max-batches',type=int,default=1)
    parser.add_argument('--requests-per-second',type=float,default=2)
    args=parser.parse_args()
    if not 0<args.requests_per_second<=5 or args.max_batches<1:
        parser.error('This manual wrapper permits 0 < requests-per-second <= 5 and positive max-batches')
    config=load_config(ROOT/args.config)
    payload={**base_payload(config),**build_initialization_payload(config)}
    if payload['sources']!=['sec_edgar']:
        parser.error('This operator rate profile is limited to SEC initialization')
    payload['_local']={'operator_execution_profile':{'requests_per_second':args.requests_per_second,
        'max_batches':args.max_batches,'daily_config_unchanged':True}}
    config.setdefault('http',{})['requests_per_second']=args.requests_per_second
    control=Control(config['storage'])
    try:
        if control.meta('paused',False):
            raise RuntimeError('Collector is paused; explicitly resume before running')
        request=control.enqueue(payload,'manual_history_initialization')
    finally:control.close()
    result={'request_id':request,'execution':drain(config,max_jobs=args.max_batches),
        'status':status(config),'operator_execution_profile':payload['_local']['operator_execution_profile']}
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))

if __name__=='__main__':main()
