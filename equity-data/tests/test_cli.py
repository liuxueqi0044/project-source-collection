import argparse
from datetime import datetime
from unittest.mock import patch

from equity_data.cli import run_collect,RunHttpClient
from equity_data.store import Store


def test_incremental_rescans_old_macro_observations(tmp_path):
    config={'market':'US','start':'2020-01-01','end':'2024-12-31','update_lookback_days':14,
            'sec':{},'securities':[{'security_id':'test','start':'2021-01-01','end':'2021-12-31'}]}
    captured={}
    def capture_sec(store,client,cfg):
        captured['sec']=cfg
        return {'status':'ok'}
    def capture_fred(store,client,cfg):
        captured['fred']=cfg
        return {'status':'ok'}
    args=argparse.Namespace(command='update',sources='sec,fred',start=None,end=None,offline=True)
    with Store(tmp_path) as store,patch('equity_data.cli.collect_sec',capture_sec),patch('equity_data.cli.collect_fred',capture_fred):
        run_collect(store,config,args)
    assert captured['fred']['start']=='2020-01-01'
    assert (datetime.fromisoformat(captured['sec']['end'])-datetime.fromisoformat(captured['sec']['start'])).days==14
    assert captured['sec']['securities'][0]['start']==captured['sec']['start']
    assert config['securities'][0]['start']=='2021-01-01'


def test_offline_missing_evidence_never_touches_network(tmp_path):
    with Store(tmp_path) as store:
        client=RunHttpClient(store,offline=True)
        with patch.object(client.session,'get',side_effect=AssertionError('network touched')) as request:
            try:
                client.fetch('https://example.org/missing.json',source='test')
            except RuntimeError as exc:
                assert 'Offline raw evidence missing' in str(exc)
            else:
                raise AssertionError('offline missing evidence must fail')
            request.assert_not_called()
