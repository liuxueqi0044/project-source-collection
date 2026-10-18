import argparse
from unittest.mock import patch
from equity_data.cli import load_config, main
from equity_data.machine import execute
from equity_data.store import Store
from equity_data.budgets import Budget
import pytest

def args(**kwargs):
    return argparse.Namespace(command='backfill',sources='sec_edgar',symbols='UNSEEN',start=None,end=None,offline=True,max_tasks=0,full_history=False,**kwargs)

def test_new_symbol_pool_is_resolved_by_configuration_not_answers(tmp_path):
    config={'market':'US','storage':str(tmp_path),'start':'2024-01-01','end':'2024-12-31','sources':['sec_edgar'],'sec':{},'budget':{},'data_types':['filings']}
    resolved={'securities':[{'company_id':'sec:0000003333','cik':'0000003333','symbol':'UNSEEN'}],'gaps':[]}
    with Store(tmp_path) as store,patch('equity_data.machine.resolve_universe',return_value=resolved) as resolver:
        result=execute(store,config,args())
        assert resolver.call_args.args[2]['universe']['symbols']==['UNSEEN']
        assert result['execution_complete'] is False
        assert store.db.execute("select count(*) from machine_tasks where state='pending'").fetchone()[0]==1
        assert config.get('securities') is None

def test_budget_rejects_negative_or_nonfinite_cost():
    for values in ({'request_cost_usd':{'paid':-1}},{'max_cost_usd':float('nan')},{'max_requests':-1}):
        with pytest.raises(ValueError):Budget(values)

def test_sources_cli_is_readable_without_credentials(tmp_path,capsys):
    conf=tmp_path/'config.toml'
    conf.write_text('market="US"\nstart="2024-01-01"\nend="2024-01-31"\nstorage="data"\n',encoding='utf-8')
    assert main(['--config',str(conf),'sources'])==0
    assert 'sec_edgar' in capsys.readouterr().out
